"""
OZON 运营模块 — Blueprint
路由前缀 /ozon，所有路由需登录
"""
import json
import hashlib
import datetime
import base64
import os
import time
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, jsonify, send_from_directory
from flask_login import login_required, current_user
from peewee import fn
from werkzeug.utils import secure_filename

from models import (
    db,
    OzonAccount, OzonSource, OzonSourceSku, OzonSourceMedia,
    OzonDraft, OzonDraftSku, OzonImageSlot, OzonImageCandidate, OzonPublishJob,
    OzonPrompt, OzonPricingRule, ExchangeRate, UserApiKey,
    # 新增：适配层
    SourceProductGroup, SourceProductGroupItem,
    ProductFact, ProductFactSku, ProductFactEvidence,
    ListingAdaptation,
    # 新增：类目属性
    OzonCategory, OzonCategoryType, OzonCategoryAttribute,
    OzonAttributeValue, OzonAttributeMapping, OzonFieldGap,
    # 新增：视觉模型
    VisionModelConfig, ImageAnalysisJob, ImageFact,
    # 新增：在线商品
    OzonOnlineProduct, OzonOnlineProductAction,
    # 新增：同步任务 + 常用 type
    OzonCategorySyncJob, OzonFavoriteCategoryType,
)
from services.ozon_api import (
    create_client, test_account,
    OzonAPIError, OzonAuthError, OzonValidationError,
)
from services.ozon_collector import fetch_url, extract_product, fetch_url_headless, collect_quality_check
from services.ecommerce_image_skill import build_slot_prompt
from services.image_generation import (
    get_image_generation_configs,
    generate_image_with_config,
    save_generated_image,
)
from services.ecommerce_image_reference import (
    select_references_for_slot,
    has_usable_references,
)
from crypto_utils import encrypt_api_key
from crypto_utils import decrypt_api_key

ozon_bp = Blueprint('ozon', __name__, url_prefix='/ozon')


# ── 辅助函数 ────────────────────────────────────────────

def _parse_batch_ids(ids_str):
    """解析批量操作传来的逗号分隔 ID 字符串，返回 int 列表"""
    if not ids_str:
        return []
    try:
        return [int(x.strip()) for x in ids_str.split(',') if x.strip()]
    except ValueError:
        return []


def _batch_translate(ru_names, user):
    """批量翻译俄语类目/属性名为中文（分批 + 重试）。
    返回 dict: {"ru_name": "zh_name", ..., "_errors": ["err1", ...]}"""
    errors = []

    if not ru_names:
        return {'_errors': ['no input']}

    from crypto_utils import decrypt_api_key
    key_record = UserApiKey.get_or_none(UserApiKey.user == user)
    if not key_record:
        return {'_errors': ['未配置 AI API Key，请先在「平台接口」页配置 DeepSeek API Key']}

    api_key = decrypt_api_key(key_record.api_key)
    if not api_key:
        return {'_errors': ['API Key 解密失败']}

    import openai
    import time

    BATCH_SIZE = 30
    MAX_RETRIES = 2
    all_results = {}
    unique = list(dict.fromkeys(ru_names))
    batches = [unique[i:i + BATCH_SIZE] for i in range(0, len(unique), BATCH_SIZE)]

    for bi, batch in enumerate(batches):
        ok = False
        for attempt in range(MAX_RETRIES + 1):
            try:
                client = openai.OpenAI(
                    api_key=api_key,
                    base_url='https://api.deepseek.com',
                )
                prompt = (
                    '你是电商商品类目翻译助手。请将以下俄语电商类目名称翻译成简洁准确的中文。'
                    '只输出 JSON 字典（不要 markdown），格式：{"俄语名": "中文翻译", ...}\n\n'
                    + json.dumps(batch, ensure_ascii=False)
                )
                response = client.chat.completions.create(
                    model='deepseek-chat',
                    messages=[{'role': 'user', 'content': prompt}],
                    temperature=0.1,
                    max_tokens=2000,
                    timeout=30,
                )
                raw = response.choices[0].message.content.strip()
                if raw.startswith('```'):
                    lines = raw.split('\n')
                    lines = lines[1:] if lines[0].startswith('```') else lines
                    if lines and lines[-1].startswith('```'):
                        lines = lines[:-1]
                    raw = '\n'.join(lines)
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    all_results.update(parsed)
                ok = True
                break
            except json.JSONDecodeError as e:
                errors.append(f'第{bi+1}批第{attempt+1}次JSON解析失败: {str(e)[:100]}')
                if attempt < MAX_RETRIES:
                    time.sleep(2)
            except Exception as e:
                errors.append(f'第{bi+1}批第{attempt+1}次失败: {str(e)[:150]}')
                if attempt < MAX_RETRIES:
                    time.sleep(2)
        if not ok:
            errors.append(f'第{bi+1}批({len(batch)}条)翻译最终失败')

    all_results['_errors'] = errors
    return all_results


def _get_exchange_rate():
    """获取当前 CNY→RUB 汇率"""
    row = (ExchangeRate
           .select()
           .where(ExchangeRate.target_currency == 'RUB')
           .order_by(ExchangeRate.updated_at.desc())
           .first())
    return row.rate if row else 12.5


def _get_or_create_draft(source):
    """获取或创建草稿（不触发 AI 生成）"""
    draft = (OzonDraft
             .select()
             .where((OzonDraft.source == source) & (OzonDraft.user == current_user))
             .first())
    if not draft:
        draft = OzonDraft.create(
            user=current_user,
            source=source,
            status='draft',
        )
        # 从源 SKU 复制到草稿 SKU
        for src_sku in (OzonSourceSku
                        .select()
                        .where(OzonSourceSku.source == source)
                        .order_by(OzonSourceSku.source_order)):
            OzonDraftSku.create(
                user=current_user,
                draft=draft,
                source_sku=src_sku,
                source_order=src_sku.source_order,
                source_sku_name=src_sku.source_sku_name,
                color_ru=src_sku.color_ru,
                style_ru=src_sku.style_ru,
                bundle_quantity=src_sku.bundle_quantity,
                purchase_price_cny=src_sku.purchase_price_cny,
            )
    return draft


# ═══════════════════════════════════════════════════════
# P1 — OZON 工作台
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/dashboard')
@login_required
def dashboard():
    stats = {
        'account_count': OzonAccount.select().where(OzonAccount.user == current_user).count(),
        'source_pending': OzonSource.select().where(
            (OzonSource.user == current_user) &
            (OzonSource.status.in_(['collected', 'parsed']))
        ).count(),
        'draft_pending': OzonDraft.select().where(
            (OzonDraft.user == current_user) &
            (OzonDraft.status == 'needs_review')
        ).count(),
        'draft_approved': OzonDraft.select().where(
            (OzonDraft.user == current_user) &
            (OzonDraft.status == 'approved')
        ).count(),
        'publishing_count': OzonDraft.select().where(
            (OzonDraft.user == current_user) &
            (OzonDraft.status == 'publishing')
        ).count(),
        'failed_count': OzonPublishJob.select().where(
            (OzonPublishJob.user == current_user) &
            (OzonPublishJob.status == 'failed')
        ).count(),
        'published_count': OzonDraft.select().where(
            (OzonDraft.user == current_user) &
            (OzonDraft.status == 'published')
        ).count(),
    }
    recent_failures = (OzonPublishJob
                       .select()
                       .where((OzonPublishJob.user == current_user) &
                              (OzonPublishJob.status == 'failed'))
                       .order_by(OzonPublishJob.created_at.desc())
                       .limit(5))
    return render_template('ozon/dashboard.html',
                           stats=stats,
                           recent_failures=recent_failures,
                           now=datetime.datetime.now())


# ═══════════════════════════════════════════════════════
# P2 — 平台接口
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/accounts')
@login_required
def accounts():
    accounts = (OzonAccount
                .select()
                .where(OzonAccount.user == current_user)
                .order_by(OzonAccount.created_at.desc()))

    # 获取或生成浏览器插件 token
    import secrets
    if not current_user.extension_token:
        current_user.extension_token = secrets.token_urlsafe(32)
        current_user.save()
    extension_token = current_user.extension_token

    return render_template('ozon/accounts.html',
                           accounts=accounts,
                           extension_token=extension_token)


@ozon_bp.route('/accounts/add', methods=['POST'])
@login_required
def account_add():
    name = request.form.get('name', '').strip()
    shop_type = request.form.get('shop_type', 'cross_border')
    environment = request.form.get('environment', 'test')
    client_id = request.form.get('client_id', '').strip()
    api_key = request.form.get('api_key', '').strip()

    if not name or not client_id or not api_key:
        flash('请填写所有必填字段', 'danger')
        return redirect(url_for('ozon.accounts'))

    # 检查同用户下名称唯一
    exists = (OzonAccount
              .select()
              .where((OzonAccount.user == current_user) &
                     (OzonAccount.name == name))
              .first())
    if exists:
        flash(f'店铺名称 "{name}" 已存在', 'danger')
        return redirect(url_for('ozon.accounts'))

    OzonAccount.create(
        user=current_user,
        name=name,
        shop_type=shop_type,
        environment=environment,
        client_id=client_id,
        api_key=api_key,
    )
    flash(f'店铺 "{name}" 已添加', 'success')
    return redirect(url_for('ozon.accounts'))


@ozon_bp.route('/accounts/<int:account_id>/edit', methods=['GET', 'POST'])
@login_required
def account_edit(account_id):
    acc = (OzonAccount
           .select()
           .where((OzonAccount.id == account_id) & (OzonAccount.user == current_user))
           .first())
    if not acc:
        flash('店铺不存在', 'danger')
        return redirect(url_for('ozon.accounts'))

    if request.method == 'POST':
        acc.name = request.form.get('name', acc.name)
        acc.shop_type = request.form.get('shop_type', acc.shop_type)
        acc.environment = request.form.get('environment', acc.environment)
        acc.client_id = request.form.get('client_id', acc.client_id)
        api_key = request.form.get('api_key', '').strip()
        if api_key:
            acc.api_key = api_key
        acc.updated_at = datetime.datetime.now()
        acc.save()
        flash(f'店铺 "{acc.name}" 已更新', 'success')
        return redirect(url_for('ozon.accounts'))

    return render_template('ozon/account_edit.html', acc=acc)


@ozon_bp.route('/accounts/<int:account_id>/delete', methods=['POST'])
@login_required
def account_delete(account_id):
    acc = (OzonAccount
           .select()
           .where((OzonAccount.id == account_id) & (OzonAccount.user == current_user))
           .first())
    if acc:
        name = acc.name
        acc.delete_instance()
        flash(f'店铺 "{name}" 已删除', 'success')
    return redirect(url_for('ozon.accounts'))


@ozon_bp.route('/accounts/<int:account_id>/test', methods=['POST'])
@login_required
def account_test(account_id):
    acc = (OzonAccount
           .select()
           .where((OzonAccount.id == account_id) & (OzonAccount.user == current_user))
           .first())
    if not acc:
        flash('店铺不存在', 'danger')
        return redirect(url_for('ozon.accounts'))

    success, message = test_account(acc)
    flash(message, 'success' if success else 'danger')
    return redirect(url_for('ozon.accounts'))


# ═══════════════════════════════════════════════════════
# P3 — 采集列表
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/sources')
@login_required
def sources():
    # 自动清理过期（30天+）的软删除记录
    cutoff = datetime.datetime.now() - datetime.timedelta(days=30)
    expired = (OzonSource
               .select()
               .where((OzonSource.user == current_user) &
                      (OzonSource.deleted_at.is_null(False)) &
                      (OzonSource.deleted_at < cutoff)))
    for src in expired:
        # 先解除新表的外键引用
        media_subq = OzonSourceMedia.select(OzonSourceMedia.id).where(OzonSourceMedia.source == src)
        ImageFact.delete().where(ImageFact.media_id.in_(media_subq)).execute()
        ImageAnalysisJob.delete().where(ImageAnalysisJob.source == src).execute()
        ProductFactEvidence.delete().where(ProductFactEvidence.source == src).execute()
        ProductFactEvidence.delete().where(ProductFactEvidence.media_id.in_(media_subq)).execute()
        SourceProductGroupItem.delete().where(SourceProductGroupItem.source == src).execute()
        OzonSourceSku.delete().where(OzonSourceSku.source == src).execute()
        OzonSourceMedia.delete().where(OzonSourceMedia.source == src).execute()
        src.delete_instance()

    # 回收站模式
    view = request.args.get('view', '')
    if view == 'trash':
        query = (OzonSource
                 .select()
                 .where((OzonSource.user == current_user) &
                        (OzonSource.deleted_at.is_null(False)))
                 .order_by(OzonSource.deleted_at.desc()))
        sources_list = list(query)
        source_ids = [s.id for s in sources_list]
        preview_media = {}
        if source_ids:
            all_media = (OzonSourceMedia
                         .select()
                         .where(OzonSourceMedia.source_id.in_(source_ids))
                         .order_by(OzonSourceMedia.id))
            for m in all_media:
                preview_media.setdefault(m.source_id, []).append(m)
        return render_template('ozon/sources.html', sources=sources_list,
                               preview_media=preview_media, view='trash',
                               quality_map={})

    # 正常模式：排除已软删除的
    query = (OzonSource
             .select()
             .where((OzonSource.user == current_user) &
                    (OzonSource.deleted_at.is_null()))
             .order_by(OzonSource.captured_at.desc()))

    platform = request.args.get('platform', '').strip()
    status = request.args.get('status', '').strip()
    if platform:
        query = query.where(OzonSource.platform == platform)
    if status:
        query = query.where(OzonSource.status == status)

    sources_list = list(query)

    # 预加载每个采集商品的前4张图片用于缩略图预览（排除 rejected 的图片）
    source_ids = [s.id for s in sources_list]
    preview_media = {}
    if source_ids:
        all_media = (OzonSourceMedia
                     .select()
                     .where((OzonSourceMedia.source_id.in_(source_ids)) &
                            ((OzonSourceMedia.compliance_status != 'rejected') |
                             (OzonSourceMedia.compliance_status.is_null())))
                     .order_by(OzonSourceMedia.id))
        for m in all_media:
            lst = preview_media.setdefault(m.source_id, [])
            if len(lst) < 4:
                lst.append(m)

    # 预加载质量检查结果
    quality_map = {}
    for s in sources_list:
        if s.quality_json:
            try:
                quality_map[s.id] = json.loads(s.quality_json)
            except (json.JSONDecodeError, TypeError):
                pass

    return render_template('ozon/sources.html', sources=sources_list,
                           preview_media=preview_media, view='active',
                           quality_map=quality_map)


# ═══ API 端点（供浏览器插件使用） ═══

@ozon_bp.route('/api/sources/add', methods=['POST'])
def api_source_add():
    """浏览器插件专用 API：直接 POST JSON 入库（无需登录态）

    接收格式（插件 v2）:
    {
      "platform": "1688", "url": "...", "title": "...",
      "category": "...", "description": "...", "shop_name": "...",
      "skus": [{"source_order":1, "source_sku_name":"...", "purchase_price_cny":10.5, ...}],
      "images": [{"role":"main/sku/detail", "src":"https://..."}],
      "specs": [{"name":"品牌","value":"DJI"}],   // 可选，v2.1+
      "capture_url": "..."                         // 可选
    }
    """
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify(ok=False, error='请求体不是合法的 JSON'), 400

    if not data:
        return jsonify(ok=False, error='请求体为空'), 400

    # Token 认证
    token = request.headers.get('X-Auth-Token', '') or data.get('token', '')
    if not token:
        return jsonify(ok=False, error='缺少认证 token，请在插件中配置'), 401

    from models import User as UserModel
    user = (UserModel
            .select()
            .where(UserModel.extension_token == token)
            .first())
    if not user:
        return jsonify(ok=False, error='无效的 token，请在平台接口页面重新获取'), 401

    # 解析数据
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify(ok=False, error='商品标题不能为空'), 400

    platform = data.get('platform', 'manual')
    source_url = data.get('url', '')
    capture_url = data.get('capture_url') or source_url
    skus = data.get('skus', [])
    images = data.get('images', [])
    specs = data.get('specs', [])  # v2.1 新增

    # ── 构建 raw_json（含 specs）────────────────────
    raw_data = {
        "product": {
            "title_cn": title,
            "category_cn": data.get('category', ''),
            "description_cn": data.get('description', ''),
            "shop_name": data.get('shop_name', ''),
        },
        "skus": skus,
        "media": [{"source_url": img.get("src", ""), "role": img.get("role", "sku")} for img in images],
        "specs_json": specs,
        "platform": platform,
        "source_url": source_url,
        "capture_url": capture_url,
        "collection_method": "browser_extension",
    }

    # ── 图片预分类 + 质量检查 ──────────────────────
    from services.ozon_collector import classify_source_image_url, collect_quality_check

    media_records = []  # 暂存分类结果
    saved_img_count = 0
    rejected_img_count = 0
    for img in (images or []):
        src = img.get('src', '')
        role = img.get('role', 'sku')
        if not src:
            continue
        # 构建插件传入的图片元数据
        img_meta = {
            'source_area': img.get('source_area', 'unknown'),
            'dom_path': img.get('dom_path', ''),
            'width': img.get('width', 0),
            'height': img.get('height', 0),
            'alt': img.get('alt', ''),
            'nearby_text': img.get('nearby_text', ''),
            'source_selector': img.get('source_selector', ''),
            'collect_reason': img.get('reason', ''),
            'linked_sku_name': img.get('linked_sku_name'),
        }
        classification = classify_source_image_url(src, role, img_meta)
        comp_status = classification["status"]
        if comp_status == "rejected":
            rejected_img_count += 1
        else:
            saved_img_count += 1
        details = classification.get("details") or {}
        media_records.append({
            "src": src, "role": role,
            "comp_status": comp_status,
            "reason": classification.get("reason") or '',
            "source_area": img_meta.get("source_area"),
            "dom_path": img_meta.get("dom_path"),
            "width": img_meta.get("width"),
            "height": img_meta.get("height"),
            "alt": img_meta.get("alt"),
            "nearby_text": img_meta.get("nearby_text"),
            "rule": details.get("rule", ''),
            "source_selector": img_meta.get("source_selector", ''),
            "collect_reason": img_meta.get("collect_reason", ''),
            "linked_sku_name": img_meta.get("linked_sku_name"),
        })

    # ── 图片统计 ──────────────────────────────────────
    detail_missing_from_payload = data.get('detail_missing', False)
    pc_main_count = sum(1 for mr in media_records if mr['role'] == 'main' and mr['comp_status'] != 'rejected')
    sku_img_count = sum(1 for mr in media_records if mr['role'] == 'sku' and mr['comp_status'] != 'rejected')
    sku_text_count = len(skus)


    quality = collect_quality_check(raw_data, source_url)

    has_confirmed_price = any(sku.get('purchase_price_cny') for sku in skus)
    price_unconfirmed = not has_confirmed_price

    # ── 先建 source（media 需要 FK）─────────────────
    source = OzonSource.create(
        user=user,
        platform=platform,
        source_url=source_url,
        capture_url=capture_url,
        source_item_id=data.get('item_id', ''),
        title_cn=title,
        category_cn=data.get('category', ''),
        description_cn=data.get('description', ''),
        shop_name=data.get('shop_name', ''),
        sku_count=len(skus),
        image_count=saved_img_count,
        raw_json=json.dumps(raw_data, ensure_ascii=False),
        quality_json=json.dumps(quality, ensure_ascii=False),
        detail_missing=(platform == '1688' and detail_missing_from_payload),
        price_manual_confirmed=has_confirmed_price,
        status='collected',
        capture_method='browser_extension',
        captured_at=datetime.datetime.now(),
    )

    # ── 再建 media（关联 source）───────────────────
    for mrec in media_records:
        meta_json = {
            'source_area': mrec.get('source_area', 'unknown'),
            'dom_path': mrec.get('dom_path', ''),
            'alt': mrec.get('alt', ''),
            'nearby_text': mrec.get('nearby_text', ''),
            'rule': mrec.get('rule', ''),
            'evidence': mrec.get('evidence', 'browser_extension_pc'),
            'source_selector': mrec.get('source_selector', ''),
            'collect_reason': mrec.get('collect_reason', ''),
            'linked_sku_name': mrec.get('linked_sku_name'),
        }
        OzonSourceMedia.create(
            user=user,
            source=source,
            media_id=f'ext-{mrec["src"][:50]}',
            media_source='browser_extension',
            role=mrec["role"],
            source_url=mrec["src"],
            width=mrec.get('width') or None,
            height=mrec.get('height') or None,
            compliance_status=mrec["comp_status"],
            reject_reason=mrec["reason"],
            review_status='rejected' if mrec["comp_status"] == 'rejected' else ('pending' if mrec["comp_status"] == 'needs_review' else 'approved'),
            raw_json=json.dumps(meta_json, ensure_ascii=False),
        )

    for i, sku_data in enumerate(skus):
        OzonSourceSku.create(
            user=user,
            source=source,
            source_order=sku_data.get('source_order', i + 1),
            source_sku_id=f'sku-{i + 1:03d}',
            source_sku_name=sku_data.get('source_sku_name', '') or f'SKU {i + 1}',
            color_cn=sku_data.get('color_cn'),
            size_cn=sku_data.get('size_cn'),
            style_cn=sku_data.get('style_cn'),
            bundle_quantity=sku_data.get('bundle_quantity', 1),
            package_contents=json.dumps(sku_data.get('package_contents_cn', []), ensure_ascii=False) if sku_data.get('package_contents_cn') else None,
            purchase_price_cny=sku_data.get('purchase_price_cny'),
        )

    source.status = 'parsed'
    source.sku_count = OzonSourceSku.select().where(OzonSourceSku.source == source).count()
    source.image_count = saved_img_count
    source.save()

    # ── 响应 ────────────────────────────────────────
    resp = {
        "ok": True, "id": source.id, "title": title,
        "sku_count": source.sku_count, "image_count": saved_img_count,
    }
    if rejected_img_count > 0:
        resp["rejected_images"] = rejected_img_count
    if platform == '1688' and source.detail_missing:
        resp["detail_missing_warning"] = "1688 详情图未采集到，建议在详情区完全加载后重试或手动补图"
    if sku_img_count < sku_text_count and sku_text_count > 0:
        resp["sku_gap_warning"] = f"SKU 图片可能未采齐：识别 SKU {sku_text_count} 个，SKU 图 {sku_img_count} 张"
    if quality.get("warnings"):
        resp["warnings"] = quality["warnings"]
    if quality.get("missing_fields"):
        resp["missing_fields"] = quality["missing_fields"]
    if price_unconfirmed:
        resp["price_warning"] = "未识别到采购价，请在系统中手动填写"
    return jsonify(**resp)


@ozon_bp.route('/api/h5-health-check', methods=['GET'])
@login_required
def api_h5_health_check():
    """检测 H5 补采环境：Playwright + Chromium 是否可用"""
    import sys
    result = {
        "python_executable": sys.executable,
        "playwright_import": False,
        "playwright_version": "",
        "chromium_launch": False,
        "chromium_version": "",
        "error": "",
    }

    # 1. 检测 playwright 包
    try:
        from playwright.sync_api import sync_playwright
        import importlib.metadata
        result["playwright_import"] = True
        result["playwright_version"] = importlib.metadata.version("playwright")
    except ImportError:
        result["error"] = "Playwright 未安装。请运行: " + sys.executable + " -m pip install playwright"
        return jsonify(ok=False, **result)

    # 2. 检测 Chromium 可启动
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            result["chromium_version"] = browser.version
            result["chromium_launch"] = True
            browser.close()
    except Exception as e:
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
            result["error"] = (
                "Chromium 内核未安装或版本不匹配。请运行:\n"
                + sys.executable + " -m playwright install chromium"
            )
        else:
            result["error"] = f"Chromium 启动失败: {msg[:300]}"
        return jsonify(ok=False, **result)

    return jsonify(
        ok=True,
        message=f"H5 补采环境正常 — Python {sys.version.split()[0]}, Playwright {result['playwright_version']}, Chromium {result['chromium_version']}",
        **result
    )


# ═══ 页面路由 ═══

@ozon_bp.route('/sources/add', methods=['POST'])
@login_required
def source_add():
    raw_json = request.form.get('raw_json', '').strip()
    if not raw_json:
        flash('请输入采集 JSON', 'danger')
        return redirect(url_for('ozon.sources'))

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        flash('JSON 格式无效，请检查', 'danger')
        return redirect(url_for('ozon.sources'))

    src = data.get('source', {})
    prod = data.get('product', {})
    skus = data.get('skus', [])
    media = data.get('media', [])

    # 解析采集时间（可能是 ISO 字符串）
    captured_at_str = src.get('captured_at', '')
    try:
        captured_at = datetime.datetime.fromisoformat(captured_at_str) if captured_at_str else datetime.datetime.now()
    except (ValueError, TypeError):
        captured_at = datetime.datetime.now()

    source = OzonSource.create(
        user=current_user,
        platform=src.get('platform', 'manual'),
        source_url=src.get('url', ''),
        source_item_id=src.get('item_id'),
        title_cn=prod.get('title_cn', ''),
        category_cn=prod.get('category_cn'),
        description_cn=prod.get('description_cn'),
        shop_name=src.get('shop_name'),
        sku_count=len(skus),
        image_count=len(media),
        raw_json=raw_json,
        status='collected',
        capture_method=src.get('capture_method', 'manual'),
        captured_at=captured_at,
    )

    # 解析 SKU
    for sku_data in skus:
        OzonSourceSku.create(
            user=current_user,
            source=source,
            source_order=sku_data.get('source_order', 1),
            source_sku_id=sku_data.get('source_sku_id', ''),
            source_sku_name=sku_data.get('source_sku_name', ''),
            color_cn=sku_data.get('color_cn'),
            color_ru=sku_data.get('color_ru'),
            size_cn=sku_data.get('size_cn'),
            size_ru=sku_data.get('size_ru'),
            style_cn=sku_data.get('style_cn'),
            style_ru=sku_data.get('style_ru'),
            bundle_quantity=sku_data.get('bundle_quantity', 1),
            package_contents=json.dumps(sku_data.get('package_contents_cn', []), ensure_ascii=False) if sku_data.get('package_contents_cn') else None,
            material_cn=sku_data.get('material_cn'),
            purchase_price_cny=sku_data.get('purchase_price_cny'),
            image_refs=json.dumps(sku_data.get('image_refs', [])) if sku_data.get('image_refs') else None,
        )

    # 解析媒体
    for m in media:
        OzonSourceMedia.create(
            user=current_user,
            source=source,
            media_id=m.get('media_id', ''),
            media_source=m.get('source', 'source_page'),
            role=m.get('role'),
            source_url=m.get('source_url'),
            sku_refs=json.dumps(m.get('sku_refs', [])) if m.get('sku_refs') else None,
            width=m.get('width'),
            height=m.get('height'),
            aspect_ratio=m.get('aspect_ratio'),
            has_text=m.get('has_text', False),
            text_language=m.get('text_language'),
            needs_cleanup=m.get('needs_cleanup', False),
            for_ozon=m.get('for_ozon', False),
            review_status=m.get('review_status', 'pending'),
        )

    source.status = 'parsed'
    source.save()
    flash(f'采集商品 "{source.title_cn}" 已入库 ({len(skus)} SKU, {len(media)} 图片)', 'success')
    return redirect(url_for('ozon.sources'))


# ── 上传 1688 HTML 文件解析（集成 jiyun/1688 解析器） ─────

@ozon_bp.route('/sources/import-1688-html', methods=['POST'])
@login_required
def source_import_1688_html():
    """上传 SingleFile 保存的 1688 HTML 文件，自动解析并导入"""
    import sys
    import requests as req_lib

    file = request.files.get('html_file')
    if not file or not file.filename:
        flash('请选择 HTML 文件', 'danger')
        return redirect(url_for('ozon.sources'))

    user_title = request.form.get('title', '').strip()

    # ── 1. 读取 HTML 内容 ──
    try:
        html_content = file.read().decode('utf-8', errors='ignore')
    except Exception as e:
        flash(f'读取文件失败: {e}', 'danger')
        return redirect(url_for('ozon.sources'))

    if len(html_content) < 1000:
        flash('文件内容太短，请确认是 SingleFile 保存的完整 HTML', 'warning')
        return redirect(url_for('ozon.sources'))

    # ── 2. 加载 jiyun/1688 解析器 ──
    try:
        scraper_path = os.path.join('G:', os.sep, 'tools', '1688-scraper')
        if scraper_path not in sys.path:
            sys.path.insert(0, scraper_path)
        from utils.parsers.alibaba_parser import AlibabaParser
    except ImportError as e:
        flash(f'jiyun/1688 解析器未安装，请先安装到 G:\\tools\\1688-scraper: {e}', 'danger')
        return redirect(url_for('ozon.sources'))

    # ── 3. 解析 HTML ──
    try:
        parser = AlibabaParser(html_content)
    except Exception as e:
        flash(f'HTML 解析失败: {e}', 'danger')
        return redirect(url_for('ozon.sources'))

    title = user_title or parser.get_title() or '未识别标题'
    main_image_urls = parser.get_main_images() or []
    color_options = parser.get_color_options() or []  # [(name, image_url), ...]
    detail_image_urls = parser.get_detail_images() or []
    attributes = parser.get_attributes() or []  # [(name, value), ...]
    price_info = parser.get_price()

    # 提取商品 URL
    source_url = ''
    import re as _re
    url_match = _re.search(r'https?://detail\.1688\.com/offer/\d+\.html', html_content)
    if url_match:
        source_url = url_match.group()
    item_id = ''
    id_match = _re.search(r'/offer/(\d+)', source_url)
    if id_match:
        item_id = id_match.group(1)

    total_images = len(main_image_urls) + len(color_options) + len(detail_image_urls)
    if total_images == 0:
        flash('解析完成但未找到任何图片，请确认是 1688 商品详情页', 'warning')
        return redirect(url_for('ozon.sources'))

    # ── 4. 构建 SKU 列表 ──
    attrs_dict = {name: value for name, value in attributes}
    sku_list = []
    color_names = [name for name, img in color_options if name]
    spec_names = []
    if attrs_dict.get('规格'):
        spec_names = [s.strip() for s in attrs_dict['规格'].split(',') if s.strip()]

    if color_names and spec_names:
        order = 0
        for color in color_names:
            for spec in spec_names:
                order += 1
                sku_list.append({'source_order': order, 'source_sku_name': f'{color} / {spec}',
                                 'color_cn': color, 'style_cn': spec})
    elif color_names:
        for i, cn in enumerate(color_names):
            sku_list.append({'source_order': i + 1, 'source_sku_name': cn, 'color_cn': cn})
    elif spec_names:
        for i, sn in enumerate(spec_names):
            sku_list.append({'source_order': i + 1, 'source_sku_name': sn, 'color_cn': sn})
    else:
        sku_list.append({'source_order': 1, 'source_sku_name': '默认规格', 'color_cn': None, 'style_cn': '标配'})

    # ── 5. 创建 OzonSource ──
    specs_json = [{'name': n, 'value': v} for n, v in attributes]
    raw_data = {
        'product': {'title_cn': title},
        'specs_json': specs_json,
        'platform': '1688',
        'source_url': source_url,
        'collection_method': 'import_1688_html',
        'price_info': price_info,
    }
    source = OzonSource.create(
        user=current_user, platform='1688',
        source_url=source_url, source_item_id=item_id,
        title_cn=title, shop_name=attrs_dict.get('品牌', ''),
        sku_count=len(sku_list),
        image_count=total_images,
        raw_json=json.dumps(raw_data, ensure_ascii=False),
        status='parsed', capture_method='import_1688_html',
        captured_at=datetime.datetime.now(),
    )

    # ── 6. 下载图片并创建 OzonSourceMedia ──
    save_dir = os.path.join('uploads', 'source_media', str(source.id))
    os.makedirs(save_dir, exist_ok=True)
    headers = {'Referer': 'https://detail.1688.com/', 'User-Agent': 'Mozilla/5.0'}
    download_ok = 0
    download_fail = 0

    def download_and_save(img_url, fname, role, linked_sku=None):
        nonlocal download_ok, download_fail
        if not img_url:
            return
        url = img_url if img_url.startswith('http') else ('https:' + img_url if img_url.startswith('//') else img_url)
        dst_path = os.path.join(save_dir, fname)
        try:
            resp = req_lib.get(url, headers=headers, timeout=15)
            if resp.status_code == 200 and len(resp.content) > 500:
                with open(dst_path, 'wb') as f:
                    f.write(resp.content)
                download_ok += 1
            else:
                download_fail += 1
                dst_path = None
        except Exception:
            download_fail += 1
            dst_path = None

        serve_url = f'/ozon/uploads/source_media/{source.id}/{fname}' if dst_path else url
        OzonSourceMedia.create(
            user=current_user, source=source,
            media_id=f'html-{role}-{fname[:30]}',
            media_source='manual_upload', role=role,
            source_url=serve_url,
            local_path=(dst_path or '').replace('\\', '/') if dst_path else None,
            compliance_status='usable', review_status='approved',
            raw_json=json.dumps({
                'source_area': {'main': 'main_gallery', 'sku': 'sku_panel', 'detail': 'detail_content'}.get(role, 'unknown'),
                'evidence': 'import_1688_html', 'linked_sku_name': linked_sku,
                'original_url': url,
            }, ensure_ascii=False),
        )

    # 主图
    for i, url in enumerate(main_image_urls):
        download_and_save(url, f'T_{i+1}.jpg', 'main')
    # SKU 图
    for name, img_url in color_options:
        if img_url:
            safe_name = _re.sub(r'[\\/:*?"<>|]', '-', name or f'sku_{len(sku_list)}')
            download_and_save(img_url, f'color_{safe_name}.jpg', 'sku', linked_sku=name)
    # 详情图
    for i, url in enumerate(detail_image_urls):
        download_and_save(url, f'C_{i+1}.jpg', 'detail')

    # ── 7. 创建 OzonSourceSku ──
    for sku_data in sku_list:
        OzonSourceSku.create(
            user=current_user, source=source,
            source_order=sku_data.get('source_order', 1),
            source_sku_id=f'sku-{sku_data["source_order"]:03d}',
            source_sku_name=sku_data['source_sku_name'],
            color_cn=sku_data.get('color_cn'), style_cn=sku_data.get('style_cn'),
        )

    flash(f'✅ 导入成功：{title} — 主图 {len(main_image_urls)} / SKU图 {len([c for c in color_options if c[1]])} / 详情图 {len(detail_image_urls)} / SKU {len(sku_list)} 个（下载成功 {download_ok}，失败 {download_fail}）', 'success')
    return redirect(url_for('ozon.source_detail', source_id=source.id))


# ── 导入 jiyun/1688 采集结果 ──────────────────────────────

@ozon_bp.route('/sources/import-1688-folder', methods=['POST'])
@login_required
def source_import_1688_folder():
    """从 jiyun/1688 工具的输出文件夹导入商品数据"""
    import glob
    import shutil
    from bs4 import BeautifulSoup as BS4

    folder_path = request.form.get('folder_path', '').strip()
    title = request.form.get('title', '').strip()

    if not folder_path or not os.path.isdir(folder_path):
        flash('文件夹路径无效或不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    # ── 1. 解析 #URL.url 获取商品链接 ──
    source_url = ''
    item_id = ''
    url_file = os.path.join(folder_path, '#URL.url')
    if os.path.exists(url_file):
        try:
            with open(url_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('URL='):
                        source_url = line.strip().split('=', 1)[1]
                    elif line.startswith('BASEURL='):
                        source_url = source_url or line.strip().split('=', 1)[1]
            import re as _re
            m = _re.search(r'/offer/(\d+)', source_url)
            if m:
                item_id = m.group(1)
        except Exception:
            pass

    # ── 2. 解析 attribute.html 获取商品属性 ──
    attrs = {}
    attr_file = os.path.join(folder_path, 'attribute.html')
    if os.path.exists(attr_file):
        try:
            with open(attr_file, 'r', encoding='utf-8') as f:
                soup = BS4(f.read(), 'html.parser')
            for tr in soup.find_all('tr'):
                cells = tr.find_all('td')
                if len(cells) >= 2:
                    name = cells[0].get_text(strip=True)
                    value = cells[1].get_text(strip=True)
                    if name and value:
                        attrs[name] = value
        except Exception:
            pass

    # ── 3. 标题（用户输入 > 属性货号 > 文件夹名） ──
    if not title:
        title = attrs.get('货号', '') or os.path.basename(folder_path)

    # ── 4. 扫描图片文件 ──
    main_files = sorted(glob.glob(os.path.join(folder_path, 'T_*.jpg')) +
                        glob.glob(os.path.join(folder_path, 'T_*.png')))
    color_files = sorted(glob.glob(os.path.join(folder_path, 'color_*.*')))
    detail_files = sorted(glob.glob(os.path.join(folder_path, 'C_*.jpg')) +
                          glob.glob(os.path.join(folder_path, 'C_*.png')))

    if not main_files and not color_files and not detail_files:
        flash('文件夹中未找到图片文件（T_*.jpg / color_*.* / C_*.jpg）', 'warning')
        return redirect(url_for('ozon.sources'))

    # ── 5. 提取 SKU 信息 ──
    sku_list = []
    # 从 color_*.jpg 文件名提取
    color_names = []
    for cf in color_files:
        fname = os.path.splitext(os.path.basename(cf))[0]
        if fname.startswith('color_'):
            color_names.append(fname[6:])  # 去掉 "color_" 前缀

    # 从 attribute.html 获取规格维度
    spec_names = []
    if attrs.get('规格'):
        spec_names = [s.strip() for s in attrs['规格'].split(',') if s.strip()]

    # 构建 SKU 列表
    if color_names and spec_names:
        # 双维度：笛卡尔积
        order = 0
        for color in color_names:
            for spec in spec_names:
                order += 1
                sku_list.append({
                    'source_order': order,
                    'source_sku_name': f'{color} / {spec}',
                    'color_cn': color, 'style_cn': spec,
                })
    elif color_names:
        for i, cn in enumerate(color_names):
            sku_list.append({'source_order': i + 1, 'source_sku_name': cn, 'color_cn': cn})
    elif spec_names:
        for i, sn in enumerate(spec_names):
            sku_list.append({'source_order': i + 1, 'source_sku_name': sn, 'color_cn': sn})
    else:
        sku_list.append({'source_order': 1, 'source_sku_name': '默认规格', 'color_cn': None, 'style_cn': '标配'})

    # ── 6. 构建 raw_data ──
    specs_json = [{'name': k, 'value': v} for k, v in attrs.items()]
    raw_data = {
        'product': {'title_cn': title},
        'media': [],
        'specs_json': specs_json,
        'platform': '1688',
        'source_url': source_url,
        'collection_method': 'import_1688_folder',
        'import_folder': folder_path,
    }

    # ── 7. 创建 OzonSource ──
    source = OzonSource.create(
        user=current_user,
        platform='1688',
        source_url=source_url,
        source_item_id=item_id,
        title_cn=title,
        shop_name=attrs.get('品牌', ''),
        sku_count=len(sku_list),
        image_count=len(main_files) + len(color_files) + len(detail_files),
        raw_json=json.dumps(raw_data, ensure_ascii=False),
        status='parsed',
        capture_method='import_1688_folder',
        captured_at=datetime.datetime.now(),
    )

    # ── 8. 复制图片并创建 OzonSourceMedia ──
    save_dir = os.path.join('uploads', 'source_media', str(source.id))
    os.makedirs(save_dir, exist_ok=True)

    def import_image(src_path, role, linked_sku=None, order=0):
        fname = os.path.basename(src_path)
        dst_path = os.path.join(save_dir, fname)
        try:
            shutil.copy2(src_path, dst_path)
        except Exception:
            dst_path = src_path  # 复制失败则引用原路径

        # 生成可访问的 URL（通过 serve_source_media 路由提供）
        serve_url = f'/ozon/uploads/source_media/{source.id}/{fname}'

        OzonSourceMedia.create(
            user=current_user, source=source,
            media_id=f'import-{role}-{order}',
            media_source='manual_upload',
            role=role,
            source_url=serve_url,
            local_path=dst_path.replace('\\', '/'),
            compliance_status='usable',
            review_status='approved',
            raw_json=json.dumps({
                'source_area': {'main': 'main_gallery', 'sku': 'sku_panel', 'detail': 'detail_content'}.get(role, 'unknown'),
                'evidence': 'import_1688_folder',
                'collect_reason': f'jiyun/1688 导入: {fname}',
                'linked_sku_name': linked_sku,
            }, ensure_ascii=False),
        )

    for i, f in enumerate(main_files):
        import_image(f, 'main', order=i)
    for i, f in enumerate(color_files):
        sku_name = os.path.splitext(os.path.basename(f))[0]
        if sku_name.startswith('color_'):
            sku_name = sku_name[6:]
        import_image(f, 'sku', linked_sku=sku_name, order=i)
    for i, f in enumerate(detail_files):
        import_image(f, 'detail', order=i)

    # ── 9. 创建 OzonSourceSku ──
    for sku_data in sku_list:
        OzonSourceSku.create(
            user=current_user, source=source,
            source_order=sku_data.get('source_order', 1),
            source_sku_id=f'sku-{sku_data["source_order"]:03d}',
            source_sku_name=sku_data['source_sku_name'],
            color_cn=sku_data.get('color_cn'),
            style_cn=sku_data.get('style_cn'),
        )

    flash(f'✅ 导入成功：{title} — 主图 {len(main_files)} 张 / SKU图 {len(color_files)} 张 / 详情图 {len(detail_files)} 张 / SKU {len(sku_list)} 个', 'success')
    return redirect(url_for('ozon.source_detail', source_id=source.id))


# ── 网页采集（URL） ─────────────────────────────────────

@ozon_bp.route('/sources/collect-url', methods=['POST'])
@login_required
def source_collect_url():
    """粘贴 URL → 自动抓取 + AI 解析 → 入库"""
    url = request.form.get('url', '').strip()
    if not url:
        flash('请输入商品 URL', 'danger')
        return redirect(url_for('ozon.sources'))

    # 获取 AI API Key
    key_record = UserApiKey.get_or_none(UserApiKey.user == current_user)
    if not key_record:
        flash('请先在 工具箱 → AI 设置 中配置 API Key', 'danger')
        return redirect(url_for('ozon.sources'))

    api_key = decrypt_api_key(key_record.api_key)
    provider = key_record.api_provider

    # 第一步：抓取网页
    result = fetch_url(url)
    if not result["ok"]:
        error_msg = result["error"]
        # 如果是反爬拦截，自动引导到粘贴内容模式
        if result.get("hint") == "use_paste_mode":
            from flask import url_for as _url_for
            from urllib.parse import urlencode
            params = urlencode({"collect": "paste", "url": url})
            flash(f'⚠️ {error_msg}', 'warning')
            return redirect(f'{_url_for("ozon.sources")}?{params}')
        flash(f'网页抓取失败：{error_msg}', 'warning')
        return redirect(url_for('ozon.sources'))

    # 第二步：AI 提取
    html_content = result["html"]
    actual_url = result.get("capture_url", url)
    extracted = extract_product(html_content, api_key, provider, source_url=actual_url)
    if not extracted["ok"]:
        flash(f'AI 解析失败：{extracted["error"]}', 'danger')
        return redirect(url_for('ozon.sources'))

    # ── 第 2.5 步：质量检查 → headless 兜底 ────────────
    # 只在非 headless 路径时才尝试兜底，避免重复
    if result.get("collected_via") != "playwright_headless":
        from urllib.parse import urlparse as _urlparse
        _domain = _urlparse(url).netloc.lower()
        _is_tb = any(d in _domain for d in ['taobao.com', 'tmall.com'])
        _text_len = len(result.get("text", "").strip())

        # 快速质量预检
        _pre_quality = collect_quality_check(extracted["data"], url)
        _usable_imgs = _pre_quality.get("usable_image_count", 0)
        _detail_miss = _pre_quality.get("detail_missing", False)

        _needs_headless = (
            _is_tb
            or _text_len < 500
            or _usable_imgs < 3
        )

        if _needs_headless:
            _headless_url = result.get("capture_url") or url
            _hresult = fetch_url_headless(_headless_url)

            if _hresult.get("ok"):
                _h_text_len = len(_hresult.get("text", "").strip())
                # headless 内容更丰富时，用 AI 重新提取
                if _h_text_len > _text_len or _h_text_len > 1000:
                    _h_extracted = extract_product(
                        _hresult["html"], api_key, provider,
                        source_url=_headless_url
                    )
                    if _h_extracted.get("ok"):
                        _h_quality = collect_quality_check(_h_extracted["data"], url)
                        _h_usable = _h_quality.get("usable_image_count", 0)
                        # 图片更多 或 文本量显著提升 → 替换
                        if _h_usable > _usable_imgs or (
                            _h_usable >= _usable_imgs and _h_text_len > _text_len * 1.5
                        ):
                            extracted = _h_extracted
                            result = _hresult
                            actual_url = result.get("capture_url", url)
                            result["collected_via"] = "playwright_headless"
                            flash('🤖 自动使用 Headless 浏览器补充采集，获得更完整的商品详情', 'info')

    # 第三步：入库
    is_mobile = result.get("converted_to_mobile", False)
    _save_collected_data(extracted["data"], url,
                         captured_at=datetime.datetime.now(),
                         capture_url=actual_url,
                         is_mobile_h5=is_mobile)

    # 如果抓取时有质量警告，追加 flash
    if result.get("quality_warning"):
        flash(f'⚠️ {result["quality_warning"]}', 'warning')
    if result.get("collected_via") == "playwright_headless":
        flash('🤖 已使用 Headless 浏览器渲染页面，提取内容更完整', 'info')
    elif result.get("converted_to_mobile"):
        flash(f'📱 已自动使用手机版页面抓取 (H5)，图片和详情更完整', 'info')
    if result.get("detail_missing") and '1688' in url:
        flash('⚠️ 1688 详情图未采集到，建议使用浏览器插件补充采集', 'warning')

    return redirect(url_for('ozon.sources'))


# ── 网页采集（粘贴内容） ─────────────────────────────────

@ozon_bp.route('/sources/collect-text', methods=['POST'])
@login_required
def source_collect_text():
    """粘贴页面内容 → AI 解析 → 入库"""
    raw_text = request.form.get('content', '').strip()
    source_url = request.form.get('url', '').strip()
    if not raw_text:
        flash('请粘贴商品页面的内容', 'danger')
        return redirect(url_for('ozon.sources'))

    # 获取 AI API Key
    key_record = UserApiKey.get_or_none(UserApiKey.user == current_user)
    if not key_record:
        flash('请先在 工具箱 → AI 设置 中配置 API Key', 'danger')
        return redirect(url_for('ozon.sources'))

    api_key = decrypt_api_key(key_record.api_key)
    provider = key_record.api_provider

    # AI 提取
    extracted = extract_product(raw_text, api_key, provider, source_url=source_url)
    if not extracted["ok"]:
        flash(f'AI 解析失败：{extracted["error"]}', 'danger')
        return redirect(url_for('ozon.sources'))

    # 入库
    _save_collected_data(extracted["data"], source_url or "手动粘贴", captured_at=datetime.datetime.now())
    return redirect(url_for('ozon.sources'))


def _save_collected_data(data: dict, source_url: str, captured_at=None,
                         capture_url=None, is_mobile_h5=False):
    """将 AI 提取的数据保存为 OzonSource 记录（含图片过滤 + 质量检查）"""
    if captured_at is None:
        captured_at = datetime.datetime.now()

    from services.ozon_collector import (
        classify_source_image_url, extract_candidate_price_from_url,
        collect_quality_check
    )

    prod = data.get("product", {})
    skus = data.get("skus", [])
    media = data.get("media", [])
    pricing = data.get("pricing", {})

    # ── URL 候选价格 ──────────────────────────────
    url_price = extract_candidate_price_from_url(source_url)
    if url_price.get("price") and not pricing.get("source_price_cny") and not pricing.get("candidate_price_cny"):
        pricing["candidate_price_cny"] = url_price["price"]
        pricing["price_note_cn"] = url_price.get("note", "")
        pricing["price_source"] = "url_param"
        pricing["price_confidence"] = "low"
        pricing["price_manual_confirmed"] = False

    # ── 质量检查 ──────────────────────────────────
    quality = collect_quality_check(data, source_url)

    # 推断平台
    from urllib.parse import urlparse
    domain = urlparse(source_url).netloc
    if "1688" in domain:
        platform = "1688"
    elif "taobao" in domain:
        platform = "taobao"
    elif "tmall" in domain:
        platform = "tmall"
    elif "pinduoduo" in domain or "yangkeduo" in domain:
        platform = "pinduoduo"
    else:
        platform = "manual"

    source = OzonSource.create(
        user=current_user,
        platform=platform,
        source_url=source_url,
        capture_url=capture_url or None,
        source_item_id="",
        title_cn=prod.get("title_cn", "") or "未命名商品",
        category_cn=prod.get("category_cn"),
        description_cn=prod.get("description_cn"),
        shop_name=prod.get("shop_name", "") or "",
        sku_count=len(skus),
        image_count=len(media),
        raw_json=json.dumps(data, ensure_ascii=False),
        status="collected",
        capture_method="ai_extract",
        captured_at=captured_at,
        # 质量检查结果
        quality_json=json.dumps(quality, ensure_ascii=False),
        detail_missing=quality.get("detail_missing", False),
        price_manual_confirmed=False,
    )

    for i, sku_data in enumerate(skus):
        # 优先用确认价，否则用候选价
        price = sku_data.get("purchase_price_cny")
        if not price:
            price = sku_data.get("candidate_price_cny") or (pricing.get("source_price_cny") if i == 0 else None)
            # 如果是候选价，也保存
            if not price:
                price = pricing.get("candidate_price_cny") if i == 0 else None

        OzonSourceSku.create(
            user=current_user,
            source=source,
            source_order=sku_data.get("source_order", i + 1),
            source_sku_id=f"sku-{i + 1:03d}",
            source_sku_name=sku_data.get("source_sku_name", "") or f"SKU {i + 1}",
            color_cn=sku_data.get("color_cn"),
            size_cn=sku_data.get("size_cn"),
            style_cn=sku_data.get("style_cn"),
            bundle_quantity=sku_data.get("bundle_quantity", 1),
            package_contents=json.dumps(sku_data.get("package_contents_cn", []), ensure_ascii=False) if sku_data.get("package_contents_cn") else None,
            purchase_price_cny=price,
        )

    # ── 图片保存（带过滤） ─────────────────────────
    saved_count = 0
    rejected_count = 0
    for m in media:
        url = m.get("source_url", "")
        role = m.get("role")
        if not url:
            continue

        classification = classify_source_image_url(url, role)
        comp_status = classification["status"]

        if comp_status == "rejected":
            rejected_count += 1
            # 仍然保存 rejected 的图片，但标记状态 → 默认不展示
            OzonSourceMedia.create(
                user=current_user,
                source=source,
                media_id=f"img-{url[:50]}",
                media_source="mobile_h5" if is_mobile_h5 else "source_page",
                role=role,
                source_url=url,
                sku_refs=json.dumps(m.get("sku_refs", [])) if m.get("sku_refs") else None,
                compliance_status="rejected",
                reject_reason=classification.get("reason", ""),
                review_status="rejected",
            )
        else:
            OzonSourceMedia.create(
                user=current_user,
                source=source,
                media_id=f"img-{url[:50]}",
                media_source="mobile_h5" if is_mobile_h5 else "source_page",
                role=role,
                source_url=url,
                sku_refs=json.dumps(m.get("sku_refs", [])) if m.get("sku_refs") else None,
                compliance_status=comp_status,
                reject_reason=classification.get("reason") or "",
                review_status="pending" if comp_status == "needs_review" else "pending",
            )
            saved_count += 1

    source.status = "parsed"
    source.sku_count = OzonSourceSku.select().where(OzonSourceSku.source == source).count()
    source.image_count = saved_count  # 只计可用+待审查的
    source.save()

    # ── Flash 消息 ──────────────────────────────────
    msg = f'采集成功！"{source.title_cn[:40]}" ({len(skus)} SKU, {saved_count} 图片'
    if rejected_count > 0:
        msg += f', {rejected_count} 已过滤'
    if quality.get("price_unconfirmed"):
        msg += ', ⚠️ 采购价待确认'
    if quality.get("detail_missing"):
        msg += ', ⚠️ 详情缺失'
    if quality.get("bad_source_page"):
        msg += ', ⚠️ 非商品页'
    msg += ')'
    flash(msg, 'warning' if quality.get("needs_manual_capture") else 'success')
    if quality.get("bad_source_page") or (platform == '1688' and quality.get("needs_manual_capture")):
        flash('💡 建议使用浏览器插件采集：在商品页点击右侧绿色「采集」按钮，识别更准确', 'info')


@ozon_bp.route('/sources/<int:source_id>')
@login_required
def source_detail(source_id):
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        flash('采集商品不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    # 预过滤图片：可用（usable/needs_review/未分类）vs 已拒绝
    usable_media = list(OzonSourceMedia
        .select()
        .where((OzonSourceMedia.source == source) &
               ((OzonSourceMedia.compliance_status != 'rejected') |
                (OzonSourceMedia.compliance_status.is_null())))
        .order_by(OzonSourceMedia.id))
    rejected_media = list(OzonSourceMedia
        .select()
        .where((OzonSourceMedia.source == source) &
               (OzonSourceMedia.compliance_status == 'rejected'))
        .order_by(OzonSourceMedia.id))

    # 解析质量检查结果
    quality = None
    if source.quality_json:
        try:
            quality = json.loads(source.quality_json)
        except (json.JSONDecodeError, TypeError):
            pass

    return render_template('ozon/source_detail.html', source=source,
                           usable_media=usable_media, rejected_media=rejected_media,
                           quality=quality)


@ozon_bp.route('/sources/<int:source_id>/delete', methods=['POST'])
@login_required
def source_delete(source_id):
    """软删除 — 移入回收站"""
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        flash('采集记录不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    source.deleted_at = datetime.datetime.now()
    source.save()
    flash(f'「{source.title_cn}」已移入回收站（30天后自动清理）', 'success')
    return redirect(url_for('ozon.sources'))


@ozon_bp.route('/sources/batch-delete', methods=['POST'])
@login_required
def source_batch_delete():
    """批量软删除采集记录"""
    ids_str = request.form.get('ids', '')
    if not ids_str:
        flash('未选择任何记录', 'warning')
        return redirect(url_for('ozon.sources'))

    try:
        ids = [int(x.strip()) for x in ids_str.split(',') if x.strip()]
    except ValueError:
        flash('无效的记录 ID', 'danger')
        return redirect(url_for('ozon.sources'))

    if not ids:
        flash('未选择任何记录', 'warning')
        return redirect(url_for('ozon.sources'))

    now = datetime.datetime.now()
    count = (OzonSource
             .update(deleted_at=now, updated_at=now)
             .where((OzonSource.id.in_(ids)) &
                    (OzonSource.user == current_user) &
                    (OzonSource.deleted_at.is_null()))
             .execute())

    flash(f'已将 {count} 条记录移入回收站', 'success')
    return redirect(url_for('ozon.sources'))


@ozon_bp.route('/sources/<int:source_id>/restore', methods=['POST'])
@login_required
def source_restore(source_id):
    """从回收站还原"""
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        flash('采集记录不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    source.deleted_at = None
    source.save()
    flash(f'「{source.title_cn}」已还原', 'success')
    return redirect(url_for('ozon.sources', view='trash'))


@ozon_bp.route('/sources/<int:source_id>/destroy', methods=['POST'])
@login_required
def source_destroy(source_id):
    """永久删除"""
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        flash('采集记录不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    title = source.title_cn

    # 1. 清理关联的草稿（OzonDraft.source → OzonSource）
    drafts = OzonDraft.select().where(OzonDraft.source == source)
    for draft in drafts:
        OzonFieldGap.delete().where(OzonFieldGap.draft == draft).execute()
        ListingAdaptation.update(draft=None).where(ListingAdaptation.draft == draft).execute()
        ImageAnalysisJob.update(draft=None).where(ImageAnalysisJob.draft == draft).execute()
        OzonPublishJob.delete().where(OzonPublishJob.draft == draft).execute()
        OzonDraftSku.delete().where(OzonDraftSku.draft == draft).execute()
        OzonImageSlot.delete().where(OzonImageSlot.draft == draft).execute()
        draft.delete_instance()

    # 2. 清理视觉识别相关
    ImageFact.delete().where(ImageFact.media_id.in_(
        OzonSourceMedia.select(OzonSourceMedia.id).where(OzonSourceMedia.source == source)
    )).execute()
    ImageAnalysisJob.delete().where(ImageAnalysisJob.source == source).execute()

    # 3. 清理商品事实证据
    ProductFactEvidence.delete().where(ProductFactEvidence.source == source).execute()
    ProductFactEvidence.delete().where(ProductFactEvidence.media_id.in_(
        OzonSourceMedia.select(OzonSourceMedia.id).where(OzonSourceMedia.source == source)
    )).execute()

    # 4. 清理适配任务关联
    SourceProductGroupItem.delete().where(SourceProductGroupItem.source == source).execute()

    # 5. 级联删除 SKU 和图片
    OzonSourceSku.delete().where(OzonSourceSku.source == source).execute()
    OzonSourceMedia.delete().where(OzonSourceMedia.source == source).execute()

    source.delete_instance()
    flash(f'「{title}」已彻底删除', 'success')
    return redirect(url_for('ozon.sources', view='trash'))


@ozon_bp.route('/api/source-media/<int:media_id>/restore', methods=['POST'])
@login_required
def api_restore_source_media(media_id):
    """恢复被过滤的图片为可用"""
    media = (OzonSourceMedia
             .select()
             .join(OzonSource)
             .where((OzonSourceMedia.id == media_id) & (OzonSource.user == current_user))
             .first())
    if not media:
        return jsonify({'ok': False, 'error': '图片不存在'}), 404

    media.compliance_status = 'usable'
    media.review_status = 'approved'
    media.reject_reason = '人工恢复'
    media.save()

    # 更新 source 的 image_count
    source = media.source
    source.image_count = (OzonSourceMedia
                          .select()
                          .where((OzonSourceMedia.source == source) &
                                 (OzonSourceMedia.compliance_status != 'rejected'))
                          .count())
    source.save()

    return jsonify({'ok': True, 'message': '图片已恢复'})


@ozon_bp.route('/api/source-media/<int:source_id>/upload', methods=['POST'])
@login_required
def api_upload_source_media(source_id):
    """人工补图：上传文件或粘贴 URL"""
    source = OzonSource.get_or_none(
        (OzonSource.id == source_id) & (OzonSource.user == current_user))
    if not source:
        return jsonify(ok=False, error='来源不存在'), 404

    role = request.form.get('role', 'detail')
    if role not in ('main', 'sku', 'detail'):
        return jsonify(ok=False, error='无效的图片角色'), 400

    results = []
    ts = int(time.time())

    # 方式 1: 文件上传
    files = request.files.getlist('images')
    for idx, f in enumerate(files):
        if not f or not f.filename:
            continue
        save_dir = os.path.join('uploads', 'source_media', str(source_id))
        os.makedirs(save_dir, exist_ok=True)
        safe_name = secure_filename(f.filename) or f'image_{idx}.jpg'
        save_path = os.path.join(save_dir, f'{ts}_{idx}_{safe_name}')
        f.save(save_path)

        media = OzonSourceMedia.create(
            user=current_user, source=source,
            media_id=f'manual-{ts}-{idx}',
            media_source='manual_upload',
            role=role, local_path=save_path.replace('\\', '/'),
            compliance_status='usable',
            review_status='approved',
            raw_json=json.dumps({'source_area': 'manual_upload', 'evidence': 'manual_upload'}, ensure_ascii=False),
        )
        results.append({'id': media.id, 'local_path': save_path.replace('\\', '/')})

    # 方式 2: URL 粘贴
    urls = request.form.getlist('urls')
    for idx2, img_url in enumerate(urls):
        img_url = img_url.strip()
        if not img_url or not img_url.startswith('http'):
            continue
        media = OzonSourceMedia.create(
            user=current_user, source=source,
            media_id=f'manual-url-{ts}-{idx2}',
            media_source='manual_upload',
            role=role, source_url=img_url,
            compliance_status='usable',
            review_status='approved',
            raw_json=json.dumps({'source_area': 'manual_upload', 'evidence': 'manual_upload'}, ensure_ascii=False),
        )
        results.append({'id': media.id, 'url': img_url})

    # 更新 source 的 image_count
    if results:
        source.image_count = (OzonSourceMedia
                              .select()
                              .where((OzonSourceMedia.source == source) &
                                     (OzonSourceMedia.compliance_status != 'rejected'))
                              .count())
        source.save()

    return jsonify(ok=True, count=len(results), items=results)


@ozon_bp.route('/uploads/source_media/<int:source_id>/<path:filename>')
@login_required
def serve_source_media(source_id, filename):
    """提供上传图片的访问"""
    source = OzonSource.get_or_none(
        (OzonSource.id == source_id) & (OzonSource.user == current_user))
    if not source:
        return 'Not found', 404
    upload_dir = os.path.join(os.getcwd(), 'uploads', 'source_media', str(source_id))
    return send_from_directory(upload_dir, filename)


@ozon_bp.route('/api/source/<int:source_id>/confirm-price', methods=['POST'])
@login_required
def api_confirm_source_price(source_id):
    """确认采购价"""
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        return jsonify({'ok': False, 'error': '采集记录不存在'}), 404

    source.price_manual_confirmed = True
    source.save()
    return jsonify({'ok': True, 'message': '采购价已确认'})


# ═══════════════════════════════════════════════════════
# P4 — 商品加工
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/processing/<int:source_id>')
@login_required
def processing(source_id):
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        flash('采集商品不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    draft = _get_or_create_draft(source)
    return render_template('ozon/processing.html', source=source, draft=draft)


@ozon_bp.route('/processing/<int:source_id>/generate', methods=['POST'])
@login_required
def processing_generate(source_id):
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        flash('采集商品不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    draft = _get_or_create_draft(source)

    # TODO: 阶段 6 接入实际 AI 生成
    # 当前模拟 AI 生成结果
    draft.title_ru = f"Mock Russian Title for {source.title_cn}"
    draft.bullets_ru = json.dumps([
        "• Подходит для ежедневного использования",
        "• Компактный и удобный дизайн",
        "• Высокое качество материалов",
    ], ensure_ascii=False)
    draft.description_ru = f"Mock Russian description for {source.title_cn}."
    draft.ai_title_confidence = 0.82
    draft.ai_description_confidence = 0.75
    draft.ai_bullets_confidence = 0.78
    draft.status = 'draft'
    draft.updated_at = datetime.datetime.now()
    draft.save()

    # 确保有 8 个图片槽位
    _ensure_image_slots(draft)

    flash('AI 内容已生成（模拟），请审核后保存', 'success')
    return redirect(url_for('ozon.processing', source_id=source_id))


@ozon_bp.route('/processing/<int:source_id>/save', methods=['POST'])
@login_required
def processing_save(source_id):
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        flash('采集商品不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    draft_id = request.form.get('draft_id')
    draft = None
    if draft_id:
        draft = (OzonDraft
                 .select()
                 .where((OzonDraft.id == int(draft_id)) & (OzonDraft.user == current_user))
                 .first())
    if not draft:
        draft = _get_or_create_draft(source)

    draft.title_ru = request.form.get('title_ru', draft.title_ru)
    draft.bullets_ru = request.form.get('bullets_ru', draft.bullets_ru)
    draft.description_ru = request.form.get('description_ru', draft.description_ru)
    draft.ozon_category_path = request.form.get('ozon_category_path') or draft.ozon_category_path
    draft.status = 'needs_review'
    draft.updated_at = datetime.datetime.now()
    draft.save()

    _ensure_image_slots(draft)
    source.status = 'drafted'
    source.save()
    flash('草稿已保存', 'success')
    return redirect(url_for('ozon.listings'))


def _ensure_image_slots(draft):
    """确保草稿有 8 个默认图片槽位"""
    existing = OzonImageSlot.select().where(OzonImageSlot.draft == draft).count()
    if existing > 0:
        return

    default_slots = [
        (1, 'main', 'all', None, '3:4 竖版，白底，商品主体居中。负面：禁止中文、logo、价格、二维码'),
        (2, 'sku', 'all', None, '3:4 竖版，白底，各 SKU 单独展示'),
        (3, 'scene', 'all', None, '使用场景图，体现商品实际用途'),
        (4, 'selling_point', 'all', None, '卖点展示图，俄语简短标注核心卖点'),
        (5, 'function', 'all', None, '功能展示图，图标+简短俄语标注'),
        (6, 'detail', 'all', None, '细节特写图'),
        (7, 'size', 'all', None, '尺寸标注图，俄语标注长宽高和重量'),
        (8, 'package', 'all', None, '包装清单图，配件平铺展示'),
    ]
    for slot_order, role, scope, scope_sku_ref, prompt in default_slots:
        OzonImageSlot.create(
            user=current_user,
            draft=draft,
            slot_order=slot_order,
            role=role,
            scope=scope,
            scope_sku_ref=scope_sku_ref,
            prompt_cn=prompt,
            status='planned',
        )


# ═══════════════════════════════════════════════════════
# P5 — 图片方案
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/image-plan/<int:draft_id>')
@login_required
def image_plan(draft_id):
    draft = (OzonDraft
             .select()
             .where((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
             .first())
    if not draft:
        flash('草稿不存在', 'danger')
        return redirect(url_for('ozon.listings'))

    _ensure_image_slots(draft)

    query = (OzonImageSlot
             .select()
             .where(OzonImageSlot.draft == draft)
             .order_by(OzonImageSlot.slot_order))

    # 筛选
    status_filter = request.args.get('status', '')
    role_filter = request.args.get('role', '')
    if status_filter:
        query = query.where(OzonImageSlot.status == status_filter)
    if role_filter:
        query = query.where(OzonImageSlot.role == role_filter)

    slots = list(query)

    # 统计
    total = draft.image_slots.count()
    approved_count = draft.image_slots.where(OzonImageSlot.status == 'approved').count()
    generated_count = draft.image_slots.where(OzonImageSlot.status != 'planned').count()

    # 图片生成模型配置
    image_model_configs = get_image_generation_configs(current_user)

    # 候选图映射: {slot_id: [candidates]}
    candidate_map = {}
    if slots:
        candidates = list(OzonImageCandidate
                         .select()
                         .where(OzonImageCandidate.slot.in_([s.id for s in slots]))
                         .order_by(OzonImageCandidate.created_at.desc()))
        for c in candidates:
            candidate_map.setdefault(c.slot_id, []).append(c)

    # 参考图映射: {slot_id: [reference dicts]}
    reference_map = {}
    for s in slots:
        refs = []
        if s.reference_media_ids_json:
            try:
                ref_ids = json.loads(s.reference_media_ids_json)
                if ref_ids:
                    media_list = list(OzonSourceMedia.select().where(
                        OzonSourceMedia.id.in_(ref_ids) &
                        (OzonSourceMedia.user == current_user)
                    ))
                    refs = [
                        {'media_id': m.id, 'role': m.role or '', 'local_path': m.local_path or '',
                         'source_url': m.source_url or ''}
                        for m in media_list
                    ]
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        reference_map[s.id] = refs

    return render_template('ozon/image_plan.html',
                           draft=draft,
                           slots=slots,
                           total=total,
                           approved_count=approved_count,
                           generated_count=generated_count,
                           status_filter=status_filter,
                           role_filter=role_filter,
                           image_model_configs=image_model_configs,
                           candidate_map=candidate_map,
                           reference_map=reference_map)


@ozon_bp.route('/image-plan/<int:draft_id>/generate', methods=['POST'])
@login_required
def image_plan_generate(draft_id):
    draft = (OzonDraft
             .select()
             .where((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
             .first())
    if not draft:
        flash('Draft not found', 'danger')
        return redirect(url_for('ozon.listings'))

    _ensure_image_slots(draft)

    slot_ids = request.form.get('slot_ids', '').strip()
    if slot_ids:
        target_ids = [int(x) for x in slot_ids.split(',') if x.strip().isdigit()]
        slots = list(OzonImageSlot
                     .select()
                     .where((OzonImageSlot.draft == draft) &
                            (OzonImageSlot.id.in_(target_ids)))
                     .order_by(OzonImageSlot.slot_order))
    if not slot_ids:
        # 默认：生成所有 planned + generated 的 slot（允许随时重新生成）
        slots = list(OzonImageSlot
                     .select()
                     .where((OzonImageSlot.draft == draft) &
                            (OzonImageSlot.status.in_(['planned', 'generated'])))
                     .order_by(OzonImageSlot.slot_order))

    if not slots:
        flash('No image slots need generation', 'info')
        return redirect(url_for('ozon.image_plan', draft_id=draft_id))

    # ── 生成前清理旧的失败候选图 ──
    slot_id_list = [s.id for s in slots]
    deleted = (OzonImageCandidate
               .delete()
               .where((OzonImageCandidate.slot.in_(slot_id_list)) &
                      (OzonImageCandidate.status == 'failed'))
               .execute())
    if deleted:
        print(f'[IMG-GEN] Cleaned up {deleted} old failed candidates before regeneration')

    # Determine configs: single model or all models
    generate_all = request.form.get('generate_all_models', '').strip() == '1'
    requested_config_id = request.form.get('model_config_id', '').strip()

    if generate_all:
        configs = get_image_generation_configs(current_user)
    elif requested_config_id.isdigit():
        configs = get_image_generation_configs(current_user, selected_config_id=requested_config_id)
    else:
        configs = get_image_generation_configs(current_user)

    if not configs:
        flash(
            'Please configure at least one image generation model with provider prefix img_gen_ in Model APIs.',
            'danger'
        )
        return redirect(url_for('ozon.image_plan', draft_id=draft_id))

    sku_names = [s.source_sku_name for s in draft.draft_skus.order_by(OzonDraftSku.source_order)]

    total_candidates = 0
    total_failed = 0
    errors = []

    # ── 确定目标市场和语言 ──
    marketplace = 'ozon'  # 默认 OZON
    target_language = 'ru'

    for slot in slots:
        # ── 选择参考图 ──
        reference_images = select_references_for_slot(current_user, draft, slot, max_references=2)
        has_refs = has_usable_references(reference_images)
        generation_mode = getattr(slot, 'generation_mode', 'reference') or 'reference'
        if not has_refs:
            generation_mode = 'text_only'

        # ── Build prompt with marketplace/language ──
        prompt_payload = build_slot_prompt(
            draft, slot, sku_names=sku_names,
            product_analysis=None,          # P1 接入
            selling_point_group=None,       # P1 接入
            reference_media=reference_images,
            marketplace=marketplace,
            language=target_language,
        )
        prompt = prompt_payload['prompt']
        negative_prompt = prompt_payload.get('negative_prompt')
        prompt_version = prompt_payload.get('prompt_version')

        # ── Save reference IDs to slot ──
        ref_ids = [r.get('media_id') for r in reference_images if r.get('media_id')]
        slot.reference_media_ids_json = json.dumps(ref_ids) if ref_ids else None
        slot.generation_mode = generation_mode
        slot.prompt_ru = prompt
        slot.negative_prompt = negative_prompt
        slot.status = 'generated'
        slot.save()

        for config in configs:
            # Create candidate record
            candidate = OzonImageCandidate.create(
                user=current_user,
                draft=draft,
                slot=slot,
                provider=config.provider,
                model_name=config.model_name,
                prompt_version=prompt_version,
                prompt=prompt,
                negative_prompt=negative_prompt,
                generation_mode=generation_mode,
                reference_snapshot_json=json.dumps(reference_images, ensure_ascii=False) if reference_images else None,
                status='generated',
            )

            try:
                result = generate_image_with_config(
                    config, prompt, negative_prompt,
                    reference_images=reference_images,
                    generation_mode=generation_mode,
                )
                candidate.image_url = result.get('image_url')
                candidate.generation_mode = result.get('generation_mode', generation_mode)
                candidate.request_json = json.dumps(
                    result.get('request_snapshot', {}), ensure_ascii=False
                )
                candidate.response_json = json.dumps(result.get('raw_response'), ensure_ascii=False)
                candidate.save()

                save_generated_image(
                    candidate,
                    image_url=result.get('image_url'),
                    image_base64=result.get('image_base64'),
                )

                total_candidates += 1
            except Exception as e:
                total_failed += 1
                err_msg = str(e)[:500]
                candidate.status = 'failed'
                candidate.error_message = err_msg
                candidate.save()
                errors.append(f'Slot#{slot.slot_order}/{config.provider}/{config.model_name}: {err_msg[:120]}')
                print(f'[IMG-GEN-ERROR] slot={slot.id} provider={config.provider} model={config.model_name}: {err_msg[:200]}')

    if total_candidates:
        flash(
            f'Generated {total_candidates} candidate image(s)'
            + (f', {total_failed} failed' if total_failed else ''),
            'success'
        )
    if not total_candidates and total_failed:
        flash(
            'All image generation attempts failed. Please check model configuration and API response format.',
            'danger'
        )
    for err in errors[:3]:
        flash(f'Generation failed: {err}', 'warning')

    # 异步模式（AJAX 调用）：返回 JSON
    if request.form.get('async') == '1':
        return jsonify({
            'ok': total_candidates > 0,
            'generated': total_candidates,
            'failed': total_failed,
            'errors': errors[:5],
        })

    return redirect(url_for('ozon.image_plan', draft_id=draft_id))


@ozon_bp.route('/image-plan/slot/<int:slot_id>/approve', methods=['POST'])
@login_required
def image_plan_approve(slot_id):
    slot = (OzonImageSlot
            .select()
            .join(OzonDraft)
            .where((OzonImageSlot.id == slot_id) & (OzonDraft.user == current_user))
            .first())
    if slot:
        slot.status = 'approved'
        slot.save()
    return redirect(url_for('ozon.image_plan', draft_id=slot.draft_id))


@ozon_bp.route('/image-plan/slot/<int:slot_id>/reject', methods=['POST'])
@login_required
def image_plan_reject(slot_id):
    slot = (OzonImageSlot
            .select()
            .join(OzonDraft)
            .where((OzonImageSlot.id == slot_id) & (OzonDraft.user == current_user))
            .first())
    if slot:
        slot.status = 'rejected'
        slot.save()
    return redirect(url_for('ozon.image_plan', draft_id=slot.draft_id))


# ═══════════════════════════════════════════════════════
# P5.1 — 候选图选择
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/image-candidate/<int:candidate_id>/select', methods=['POST'])
@login_required
def image_candidate_select(candidate_id):
    candidate = (OzonImageCandidate
                 .select()
                 .join(OzonDraft)
                 .where((OzonImageCandidate.id == candidate_id) &
                        (OzonImageCandidate.user == current_user))
                 .first())
    if not candidate:
        flash('Candidate not found', 'danger')
        return redirect(request.referrer or url_for('ozon.listings'))

    slot = candidate.slot

    if candidate.status == 'failed':
        flash('Failed candidates cannot be selected', 'warning')
        return redirect(url_for('ozon.image_plan', draft_id=slot.draft_id))

    if not candidate.image_url and not candidate.local_path:
        flash('This candidate has no generated image to select', 'warning')
        return redirect(url_for('ozon.image_plan', draft_id=slot.draft_id))

    # Only a user-selected candidate becomes the final approved slot image.
    slot.generated_url = candidate.image_url
    slot.local_path = candidate.local_path
    slot.prompt_ru = candidate.prompt
    slot.negative_prompt = candidate.negative_prompt
    slot.status = 'approved'
    slot.save()

    # Reset other candidates in same slot from 'selected' to 'generated'
    OzonImageCandidate.update(status='generated').where(
        (OzonImageCandidate.slot == slot) &
        (OzonImageCandidate.status == 'selected')
    ).execute()

    # Mark this candidate as selected
    candidate.status = 'selected'
    candidate.save()

    flash(f'Slot #{slot.slot_order} image set to candidate #{candidate.id} ({candidate.provider}/{candidate.model_name})', 'success')
    return redirect(url_for('ozon.image_plan', draft_id=slot.draft_id))


@ozon_bp.route('/image-candidate/<int:candidate_id>/score', methods=['POST'])
@login_required
def image_candidate_score(candidate_id):
    candidate = (OzonImageCandidate
                 .select()
                 .join(OzonDraft)
                 .where((OzonImageCandidate.id == candidate_id) &
                        (OzonImageCandidate.user == current_user))
                 .first())
    if not candidate:
        flash('Candidate not found', 'danger')
        return redirect(request.referrer or url_for('ozon.listings'))

    # Parse scores and clamp them to the rubric limits.
    def _parse_score(val, max_value, default=None):
        try:
            v = int(val)
        except (ValueError, TypeError):
            return default
        if v < 0:
            return default
        return min(v, max_value)

    candidate.structure_score = _parse_score(request.form.get('structure_score'), 30)
    candidate.detail_score = _parse_score(request.form.get('detail_score'), 25)
    candidate.text_score = _parse_score(request.form.get('text_score'), 15)
    candidate.commercial_score = _parse_score(request.form.get('commercial_score'), 20)
    candidate.postprocess_score = _parse_score(request.form.get('postprocess_score'), 10)
    candidate.review_notes = (request.form.get('review_notes', '') or '').strip()[:2000] or None

    # Calculate total from non-null scores
    scores = [
        candidate.structure_score,
        candidate.detail_score,
        candidate.text_score,
        candidate.commercial_score,
        candidate.postprocess_score,
    ]
    candidate.total_score = sum(s for s in scores if s is not None)

    candidate.save()
    flash(f'Candidate #{candidate.id} scored: {candidate.total_score}', 'success')
    return redirect(url_for('ozon.image_plan', draft_id=candidate.draft_id))


@ozon_bp.route('/uploads/ai_generated/<path:filename>')
@login_required
def serve_ai_generated(filename):
    """Serve locally saved AI-generated images."""
    # Normalize backslashes from Windows paths
    filename = filename.replace('\\', '/')
    return send_from_directory(
        os.path.join(current_app.root_path, 'uploads', 'ai_generated'),
        filename
    )


# ═══════════════════════════════════════════════════════
# P6+P7 — 刊登草稿列表 + 草稿审核
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/listings')
@login_required
def listings():
    query = OzonDraft.select().where(OzonDraft.user == current_user)

    status = request.args.get('status', '').strip()
    platform = request.args.get('platform', '').strip()
    q = request.args.get('q', '').strip()

    if status:
        query = query.where(OzonDraft.status == status)
    if platform:
        query = query.join(OzonSource).where(OzonSource.platform == platform)
    if q:
        query = query.where(OzonDraft.title_ru.contains(q))

    page = int(request.args.get('page', 1))
    per_page = 20
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    drafts = query.order_by(OzonDraft.updated_at.desc()).paginate(page, per_page)

    return render_template('ozon/listings.html',
                           drafts=drafts,
                           page=page,
                           per_page=per_page,
                           total_pages=total_pages)


@ozon_bp.route('/listings/<int:draft_id>')
@login_required
def listing_review(draft_id):
    draft = (OzonDraft
             .select()
             .where((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
             .first())
    if not draft:
        flash('草稿不存在', 'danger')
        return redirect(url_for('ozon.listings'))

    # 解析校验结果
    validation = None
    if draft.validation_result:
        try:
            validation = json.loads(draft.validation_result)
        except (json.JSONDecodeError, TypeError):
            pass

    # 预计算 SKU 级别图片数量（避免模板中写 ORM 查询）
    skus_with_images = []
    for sku in draft.draft_skus.order_by(OzonDraftSku.source_order):
        approved_count = (OzonImageSlot
                          .select()
                          .where(
                              (OzonImageSlot.draft == draft) &
                              (OzonImageSlot.status == 'approved') &
                              ((OzonImageSlot.scope_sku_ref == sku.source_sku_name) |
                               (OzonImageSlot.scope == 'all'))
                          ).count())
        total_count = (OzonImageSlot
                       .select()
                       .where(
                           (OzonImageSlot.draft == draft) &
                           ((OzonImageSlot.scope_sku_ref == sku.source_sku_name) |
                            (OzonImageSlot.scope == 'all'))
                       ).count())
        skus_with_images.append({
            'sku': sku,
            'approved_img': approved_count,
            'total_img': total_count,
        })

    # 预计算图片槽位统计
    total_slots = draft.image_slots.count()
    approved_slots = draft.image_slots.where(OzonImageSlot.status == 'approved').count()

    return render_template('ozon/listing_review.html',
                           draft=draft,
                           validation=validation,
                           skus_with_images=skus_with_images,
                           total_slots=total_slots,
                           approved_slots=approved_slots)


@ozon_bp.route('/listings/<int:draft_id>/save', methods=['POST'])
@login_required
def listing_save(draft_id):
    draft = (OzonDraft
             .select()
             .where((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
             .first())
    if not draft:
        flash('草稿不存在', 'danger')
        return redirect(url_for('ozon.listings'))

    draft.title_ru = request.form.get('title_ru', draft.title_ru)
    draft.bullets_ru = request.form.get('bullets_ru', draft.bullets_ru)
    draft.description_ru = request.form.get('description_ru', draft.description_ru)
    draft.ozon_category_path = request.form.get('ozon_category_path') or draft.ozon_category_path
    draft.price_manual_confirmed = request.form.get('price_manual_confirmed') == '1'
    draft.updated_at = datetime.datetime.now()
    draft.save()

    # 保存 SKU 颜色/款式
    for sku in draft.draft_skus:
        color_key = f'color_ru_{sku.id}'
        style_key = f'style_ru_{sku.id}'
        if color_key in request.form:
            sku.color_ru = request.form[color_key] or None
        if style_key in request.form:
            sku.style_ru = request.form[style_key] or None
        sku.save()

    flash('草稿已保存', 'success')
    return redirect(url_for('ozon.listing_review', draft_id=draft_id))


@ozon_bp.route('/listings/<int:draft_id>/validate')
@login_required
def listing_validate(draft_id):
    draft = (OzonDraft
             .select()
             .where((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
             .first())
    if not draft:
        flash('草稿不存在', 'danger')
        return redirect(url_for('ozon.listings'))

    checks = []
    checks.append({'label': '俄语标题已填写', 'pass': bool(draft.title_ru), 'blocking': True, 'level': 'error' if not draft.title_ru else 'success'})
    checks.append({'label': 'OZON 类目已选择', 'pass': bool(draft.ozon_category_id or draft.ozon_category_path), 'blocking': True, 'level': 'error' if not (draft.ozon_category_id or draft.ozon_category_path) else 'success'})
    checks.append({'label': '缺少 SKU 数据', 'pass': draft.draft_skus.count() > 0, 'blocking': True, 'level': 'error' if draft.draft_skus.count() == 0 else 'success'})
    checks.append({'label': '价格已人工确认', 'pass': draft.price_manual_confirmed, 'blocking': True, 'level': 'error' if not draft.price_manual_confirmed else 'success'})
    checks.append({'label': '图片全部审核通过', 'pass': _all_slots_approved(draft), 'blocking': True, 'level': 'error' if not _all_slots_approved(draft) else 'success'})
    checks.append({'label': 'SKU 顺序与源一致', 'pass': True, 'blocking': False, 'level': 'success'})
    checks.append({'label': '买家可见内容未检测到禁止词', 'pass': True, 'blocking': False, 'level': 'warning'})

    blocking_count = sum(1 for c in checks if not c['pass'] and c['blocking'])
    validation = {'blocking_count': blocking_count, 'checks': checks}

    draft.validation_result = json.dumps(validation, ensure_ascii=False)
    draft.updated_at = datetime.datetime.now()
    draft.save()

    flash(f'校验完成：{blocking_count} 项阻断', 'warning' if blocking_count > 0 else 'success')
    return redirect(url_for('ozon.listing_review', draft_id=draft_id))


def _all_slots_approved(draft):
    total = OzonImageSlot.select().where(OzonImageSlot.draft == draft).count()
    if total == 0:
        return False  # 没有图片槽位 = 未通过
    approved = OzonImageSlot.select().where(
        (OzonImageSlot.draft == draft) &
        (OzonImageSlot.status == 'approved')
    ).count()
    return approved >= total


@ozon_bp.route('/listings/<int:draft_id>/approve')
@login_required
def listing_approve(draft_id):
    draft = (OzonDraft
             .select()
             .where((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
             .first())
    if not draft:
        flash('草稿不存在', 'danger')
        return redirect(url_for('ozon.listings'))

    # 先校验
    if not draft.validation_result:
        return redirect(url_for('ozon.listing_validate', draft_id=draft_id))

    try:
        validation = json.loads(draft.validation_result)
    except (json.JSONDecodeError, TypeError):
        return redirect(url_for('ozon.listing_validate', draft_id=draft_id))

    if validation.get('blocking_count', 0) > 0:
        flash(f'存在 {validation["blocking_count"]} 项阻断错误，无法审核通过', 'danger')
        return redirect(url_for('ozon.listing_review', draft_id=draft_id))

    draft.status = 'approved'
    draft.updated_at = datetime.datetime.now()
    draft.save()
    flash('草稿审核通过，可以提交发布', 'success')
    return redirect(url_for('ozon.listing_review', draft_id=draft_id))


@ozon_bp.route('/listings/<int:draft_id>/publish', methods=['POST'])
@login_required
def listing_publish(draft_id):
    draft = (OzonDraft
             .select()
             .where((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
             .first())
    if not draft:
        flash('草稿不存在', 'danger')
        return redirect(url_for('ozon.listings'))

    if draft.status != 'approved':
        flash('只有审核通过的草稿才能发布', 'danger')
        return redirect(url_for('ozon.listing_review', draft_id=draft_id))

    if not draft.account:
        flash('请先选择目标店铺', 'danger')
        return redirect(url_for('ozon.listing_review', draft_id=draft_id))

    # type_id 校验：必须存在
    category_id = str(draft.category_id or '')
    type_id = str(draft.type_id or '')
    if not category_id or not type_id:
        flash('发布前必须绑定 description_category_id 和 type_id。请在类目属性页面同步 type_id。', 'danger')
        return redirect(url_for('ozon.listing_review', draft_id=draft_id))

    # 必填属性缺口检查
    required_attrs = (OzonCategoryAttribute
                      .select()
                      .where((OzonCategoryAttribute.user == current_user) &
                             (OzonCategoryAttribute.ozon_category_id == category_id) &
                             (OzonCategoryAttribute.type_id == type_id) &
                             (OzonCategoryAttribute.is_required == True)))
    if required_attrs.exists():
        draft_attrs = json.loads(draft.attributes_json or '[]')
        draft_attr_ids = {str(a.get('id', '')) for a in draft_attrs}
        missing = [a for a in required_attrs if str(a.attribute_id) not in draft_attr_ids]
        if missing:
            names = ', '.join(f'{a.name} (id:{a.attribute_id})' for a in missing[:5])
            flash(f'必填属性缺失（{len(missing)} 项）：{names}', 'danger')
            return redirect(url_for('ozon.listing_review', draft_id=draft_id))

    # 构建商品数据
    product_data = _build_product_data(draft)
    request_json_str = json.dumps(product_data, ensure_ascii=False, indent=2)

    # 创建发布任务
    job = OzonPublishJob.create(
        user=current_user,
        account=draft.account,
        draft=draft,
        action='create_product',
        status='pending',
        request_json=request_json_str,
    )

    draft.status = 'publishing'
    draft.updated_at = datetime.datetime.now()
    draft.save()

    # 调用 OZON API
    try:
        client = create_client(draft.account)
        result = client.import_product(product_data)

        # 成功
        job.status = 'success'
        job.response_json = json.dumps(result, ensure_ascii=False, indent=2)
        job.ozon_task_id = str(result.get('task_id', ''))
        job.completed_at = datetime.datetime.now()
        job.save()

        draft.status = 'published'
        draft.ozon_product_id = job.ozon_task_id
        draft.ozon_offer_id = product_data.get('offer_id', '')
        draft.updated_at = datetime.datetime.now()
        draft.save()

        flash(f'发布成功！OZON Task ID: {job.ozon_task_id}', 'success')

    except OzonValidationError as e:
        _record_publish_failure(job, draft, e, request_json_str)
        errors_detail = '; '.join(err.get('message', '') for err in (e.errors or [])[:3])
        flash(f'发布失败 — {e}{(": " + errors_detail) if errors_detail else ""}', 'danger')

    except OzonAuthError as e:
        _record_publish_failure(job, draft, e, request_json_str)
        flash(f'发布失败 — 店铺认证无效，请检查 API 凭证', 'danger')

    except OzonAPIError as e:
        _record_publish_failure(job, draft, e, request_json_str)
        flash(f'发布失败 — {e}', 'danger')

    except Exception as e:
        _record_publish_failure(job, draft, e, request_json_str)
        flash(f'发布失败 — 未知错误: {e}', 'danger')

    return redirect(url_for('ozon.publish_jobs'))


def _build_product_data(draft):
    """从草稿构建 OZON import_product 请求体"""
    offer_id = f"draft_{draft.id}"

    # 收集已审核通过的图片 URL
    images = []
    for slot in (OzonImageSlot
                 .select()
                 .where((OzonImageSlot.draft == draft) &
                        (OzonImageSlot.status == 'approved'))
                 .order_by(OzonImageSlot.slot_order)):
        if slot.generated_url:
            images.append(slot.generated_url)

    data = {
        "offer_id": offer_id,
        "name": draft.title_ru or "Untitled",
        "category_id": int(draft.ozon_category_id) if draft.ozon_category_id else None,
        "price": None,  # 将从 pricing_json 或手动售价取
        "vat": "0",
        "description": draft.description_ru or "",
    }

    # 处理多 SKU
    skus_list = []
    for sku in draft.draft_skus.order_by(OzonDraftSku.source_order):
        skus_list.append({
            "offer_id": f"{offer_id}_{sku.source_order}",
            "sku_name": sku.source_sku_name,
            "price": str(sku.purchase_price_cny or 0),
            "quantity": sku.bundle_quantity,
        })
    if skus_list:
        data["skus"] = skus_list

    # 图片（OZON 期望对象数组）
    if images:
        data["images"] = images

    # 属性
    if draft.attributes_json:
        try:
            attrs = json.loads(draft.attributes_json)
            if isinstance(attrs, list):
                data["attributes"] = attrs
        except (json.JSONDecodeError, TypeError):
            pass

    return data


def _record_publish_failure(job, draft, error, request_json_str):
    """记录发布失败信息"""
    now = datetime.datetime.now()
    job.status = 'failed'
    job.error_message = str(error)[:1000]
    if not job.response_json:
        # 尝试从异常中提取响应体
        error_body = getattr(error, 'response_body', None)
        if error_body:
            job.response_json = str(error_body)[:5000]
    job.completed_at = now
    job.save()

    draft.status = 'failed'
    draft.validation_result = json.dumps({
        'error': str(error)[:500],
        'blocking_count': 1,
    }, ensure_ascii=False)
    draft.updated_at = now
    draft.save()


@ozon_bp.route('/listings/batch-delete', methods=['POST'])
@login_required
def listing_batch_delete():
    """批量删除草稿"""
    ids = _parse_batch_ids(request.form.get('ids', ''))
    if not ids:
        flash('未选择任何记录', 'warning')
        return redirect(url_for('ozon.listings'))

    count = 0
    for draft_id in ids:
        draft = (OzonDraft
                 .select()
                 .where((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
                 .first())
        if draft:
            OzonFieldGap.delete().where(OzonFieldGap.draft == draft).execute()
            ListingAdaptation.update(draft=None).where(ListingAdaptation.draft == draft).execute()
            ImageAnalysisJob.update(draft=None).where(ImageAnalysisJob.draft == draft).execute()
            draft.delete_instance(recursive=True)
            count += 1

    flash(f'已删除 {count} 条草稿', 'success')
    return redirect(url_for('ozon.listings'))


@ozon_bp.route('/listings/<int:draft_id>/delete', methods=['POST'])
@login_required
def listing_delete(draft_id):
    draft = (OzonDraft
             .select()
             .where((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
             .first())
    if draft:
        # 先清理新表的外键引用
        OzonFieldGap.delete().where(OzonFieldGap.draft == draft).execute()
        ListingAdaptation.update(draft=None).where(ListingAdaptation.draft == draft).execute()
        ImageAnalysisJob.update(draft=None).where(ImageAnalysisJob.draft == draft).execute()
        draft.delete_instance()
        flash('草稿已删除', 'success')
    return redirect(url_for('ozon.listings'))


# ═══════════════════════════════════════════════════════
# P8 — 发布任务
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/publish-jobs')
@login_required
def publish_jobs():
    query = OzonPublishJob.select().where(OzonPublishJob.user == current_user)

    status = request.args.get('status', '').strip()
    account_id = request.args.get('account_id', '').strip()
    if status:
        query = query.where(OzonPublishJob.status == status)
    if account_id:
        try:
            query = query.where(OzonPublishJob.account_id == int(account_id))
        except ValueError:
            pass

    jobs = query.order_by(OzonPublishJob.created_at.desc())
    accounts = OzonAccount.select().where(OzonAccount.user == current_user)
    return render_template('ozon/publish_jobs.html', jobs=jobs, accounts=accounts)


@ozon_bp.route('/publish-jobs/batch-delete', methods=['POST'])
@login_required
def publish_job_batch_delete():
    """批量删除发布任务"""
    ids = _parse_batch_ids(request.form.get('ids', ''))
    if not ids:
        flash('未选择任何记录', 'warning')
        return redirect(url_for('ozon.publish_jobs'))

    count = (OzonPublishJob
             .delete()
             .where((OzonPublishJob.id.in_(ids)) &
                    (OzonPublishJob.user == current_user))
             .execute())
    flash(f'已删除 {count} 条发布任务', 'success')
    return redirect(url_for('ozon.publish_jobs'))


@ozon_bp.route('/publish-jobs/<int:job_id>/retry', methods=['POST'])
@login_required
def publish_job_retry(job_id):
    job = (OzonPublishJob
           .select()
           .where((OzonPublishJob.id == job_id) & (OzonPublishJob.user == current_user))
           .first())
    if not job:
        flash('任务不存在', 'danger')
        return redirect(url_for('ozon.publish_jobs'))

    if job.status != 'failed':
        flash('只能重试失败的任务', 'warning')
        return redirect(url_for('ozon.publish_jobs'))

    draft = job.draft
    job.status = 'pending'
    job.retry_count += 1
    job.save()

    # 重新调用 OZON API
    product_data = _build_product_data(draft)
    request_json_str = json.dumps(product_data, ensure_ascii=False, indent=2)
    job.request_json = request_json_str
    job.save()

    try:
        client = create_client(job.account)
        result = client.import_product(product_data)

        job.status = 'success'
        job.response_json = json.dumps(result, ensure_ascii=False, indent=2)
        job.ozon_task_id = str(result.get('task_id', ''))
        job.error_message = None
        job.completed_at = datetime.datetime.now()
        job.save()

        draft.status = 'published'
        draft.ozon_product_id = job.ozon_task_id
        draft.ozon_offer_id = product_data.get('offer_id', '')
        draft.updated_at = datetime.datetime.now()
        draft.save()

        flash(f'重试成功！OZON Task ID: {job.ozon_task_id}', 'success')

    except OzonValidationError as e:
        _record_publish_failure(job, draft, e, request_json_str)
        flash(f'重试失败 — {e}', 'danger')

    except OzonAPIError as e:
        _record_publish_failure(job, draft, e, request_json_str)
        flash(f'重试失败 — {e}', 'danger')

    except Exception as e:
        _record_publish_failure(job, draft, e, request_json_str)
        flash(f'重试失败 — {e}', 'danger')

    return redirect(url_for('ozon.publish_jobs'))


# ═══════════════════════════════════════════════════════
# P9 — 提示词库
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/prompts', methods=['GET', 'POST'])
@login_required
def prompts():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        prompt_type = request.form.get('prompt_type', 'title')
        category = request.form.get('category', 'common')
        content = request.form.get('content', '').strip()

        if name and content:
            OzonPrompt.create(
                user=current_user,
                name=name,
                prompt_type=prompt_type,
                category=category,
                content=content,
            )
            flash(f'提示词模板 "{name}" 已创建', 'success')
        return redirect(url_for('ozon.prompts'))

    category = request.args.get('category', '').strip()
    query = OzonPrompt.select().where(
        (OzonPrompt.user == current_user) | (OzonPrompt.is_default == True)
    )
    if category:
        query = query.where(OzonPrompt.category == category)
    prompts_list = query.order_by(OzonPrompt.prompt_type, OzonPrompt.created_at.desc())
    return render_template('ozon/prompts.html', prompts=prompts_list)


@ozon_bp.route('/prompts/batch-delete', methods=['POST'])
@login_required
def prompt_batch_delete():
    """批量删除提示词模板"""
    ids = _parse_batch_ids(request.form.get('ids', ''))
    if not ids:
        flash('未选择任何记录', 'warning')
        return redirect(url_for('ozon.prompts'))

    count = (OzonPrompt
             .delete()
             .where((OzonPrompt.id.in_(ids)) &
                    (OzonPrompt.user == current_user))
             .execute())
    flash(f'已删除 {count} 条提示词模板', 'success')
    return redirect(url_for('ozon.prompts'))


@ozon_bp.route('/prompts/<int:prompt_id>/delete', methods=['POST'])
@login_required
def prompt_delete(prompt_id):
    prompt = (OzonPrompt
              .select()
              .where((OzonPrompt.id == prompt_id) & (OzonPrompt.user == current_user))
              .first())
    if prompt:
        prompt.delete_instance()
        flash('模板已删除', 'success')
    return redirect(url_for('ozon.prompts'))


# ═══════════════════════════════════════════════════════
# P10 — 定价规则
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/pricing', methods=['GET', 'POST'])
@login_required
def pricing():
    rule = (OzonPricingRule
            .select()
            .where((OzonPricingRule.user == current_user) &
                   (OzonPricingRule.is_default == True))
            .first())

    if request.method == 'POST':
        exchange_rate_source = request.form.get('exchange_rate_source', 'auto')
        manual_exchange_rate = request.form.get('manual_exchange_rate', '')
        target_margin_rate = float(request.form.get('target_margin_rate', 35)) / 100.0
        ad_reserve_rate = float(request.form.get('ad_reserve_rate', 5)) / 100.0
        commission_rate = float(request.form.get('commission_rate', 10)) / 100.0
        risk_buffer_type = request.form.get('risk_buffer_type', 'fixed')
        risk_buffer_value = float(request.form.get('risk_buffer_value', 3.0))

        if not rule:
            rule = OzonPricingRule(user=current_user, is_default=True, name='默认定价规则')

        rule.exchange_rate_source = exchange_rate_source
        rule.manual_exchange_rate = float(manual_exchange_rate) if manual_exchange_rate and exchange_rate_source == 'manual' else None
        rule.target_margin_rate = target_margin_rate
        rule.ad_reserve_rate = ad_reserve_rate
        rule.commission_rate = commission_rate
        rule.risk_buffer_type = risk_buffer_type
        rule.risk_buffer_value = risk_buffer_value
        rule.updated_at = datetime.datetime.now()
        rule.save()
        flash('定价规则已保存', 'success')
        return redirect(url_for('ozon.pricing'))

    current_rate = _get_exchange_rate()
    return render_template('ozon/pricing.html', rule=rule, current_rate=current_rate)


# ═══════════════════════════════════════════════════════
# P4 — 商品适配工作台
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/adaptation')
@login_required
def adaptation_list():
    """适配任务列表页"""
    groups = (SourceProductGroup
              .select()
              .where(SourceProductGroup.user == current_user)
              .order_by(SourceProductGroup.updated_at.desc()))
    return render_template('ozon/adaptation_list.html', groups=groups)


@ozon_bp.route('/adaptation/batch-delete', methods=['POST'])
@login_required
def adaptation_batch_delete():
    """批量删除适配任务（级联清理）"""
    ids = _parse_batch_ids(request.form.get('ids', ''))
    if not ids:
        flash('未选择任何记录', 'warning')
        return redirect(url_for('ozon.adaptation_list'))

    count = 0
    for gid in ids:
        group = (SourceProductGroup
                 .select()
                 .where((SourceProductGroup.id == gid) & (SourceProductGroup.user == current_user))
                 .first())
        if group:
            SourceProductGroupItem.delete().where(SourceProductGroupItem.group == group).execute()
            # 清理关联的 ProductFact
            facts = ProductFact.select().where(ProductFact.group == group)
            for f in facts:
                ProductFactEvidence.delete().where(ProductFactEvidence.fact == f).execute()
                ProductFactSku.delete().where(ProductFactSku.fact == f).execute()
                ListingAdaptation.delete().where(ListingAdaptation.fact == f).execute()
                f.delete_instance()
            group.delete_instance()
            count += 1

    flash(f'已删除 {count} 条适配任务', 'success')
    return redirect(url_for('ozon.adaptation_list'))


@ozon_bp.route('/adaptation/<int:source_id>')
@login_required
def adaptation_workspace(source_id):
    """商品适配工作台 — 从采集商品进入"""
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        flash('采集商品不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    source_skus = (OzonSourceSku
                   .select()
                   .where(OzonSourceSku.source == source)
                   .order_by(OzonSourceSku.source_order))
    source_media = (OzonSourceMedia
                    .select()
                    .where(OzonSourceMedia.source == source))

    # 查找或创建适配任务组
    group = (SourceProductGroup
             .select()
             .where((SourceProductGroup.user == current_user) &
                    (SourceProductGroup.status.in_(['draft', 'adapting'])))
             .join(SourceProductGroupItem)
             .where(SourceProductGroupItem.source == source)
             .first())

    if not group:
        group = SourceProductGroup.create(
            user=current_user,
            name=source.title_cn or '未命名适配任务',
            relation_type='one_to_one',
            status='draft',
        )
        SourceProductGroupItem.create(
            user=current_user,
            group=group,
            source=source,
            role='primary',
        )

    fact = (ProductFact
            .select()
            .where((ProductFact.user == current_user) &
                   (ProductFact.group == group))
            .first())

    if not fact:
        fact = ProductFact.create(
            user=current_user,
            group=group,
            standard_name_cn=source.title_cn or '',
            review_status='pending',
        )
        for sku in source_skus:
            ProductFactSku.create(
                user=current_user,
                fact=fact,
                source_sku=sku,
                source_order=sku.source_order,
                standard_sku_name_cn=sku.source_sku_name,
                color_cn=sku.color_cn,
                size_cn=sku.size_cn,
                style_cn=sku.style_cn,
                bundle_quantity=sku.bundle_quantity,
                purchase_price_cny=sku.purchase_price_cny,
            )

    fact_skus = (ProductFactSku
                 .select()
                 .where(ProductFactSku.fact == fact)
                 .order_by(ProductFactSku.source_order))

    adaptation = (ListingAdaptation
                  .select()
                  .where((ListingAdaptation.user == current_user) &
                         (ListingAdaptation.fact == fact))
                  .first())

    gaps = []
    if adaptation and adaptation.ozon_category_id:
        gaps = (OzonFieldGap
                .select()
                .where((OzonFieldGap.user == current_user) &
                       (OzonFieldGap.adaptation == adaptation)))

    # ═══ 新增：加载适配层类目/Type/属性/字典值 ═══
    adaptation_types = []
    adaptation_attr_schema = []
    adaptation_attr_values = {}
    adaptation_saved_attrs = {}

    if adaptation and adaptation.ozon_category_id:
        # 加载该类目的 Type 列表
        adaptation_types = list(OzonCategoryType
            .select()
            .where((OzonCategoryType.user == current_user) &
                   (OzonCategoryType.description_category_id == adaptation.ozon_category_id))
            .order_by(OzonCategoryType.type_name))

        # 如果已绑定 type，加载属性 Schema
        if adaptation.type_id:
            adaptation_attr_schema = list(OzonCategoryAttribute
                .select()
                .where((OzonCategoryAttribute.user == current_user) &
                       (OzonCategoryAttribute.ozon_category_id == adaptation.ozon_category_id) &
                       (OzonCategoryAttribute.type_id == adaptation.type_id))
                .order_by(OzonCategoryAttribute.is_required.desc(),
                          OzonCategoryAttribute.attribute_id))

            # 加载字典属性值
            dict_attr_ids = [a.attribute_id for a in adaptation_attr_schema if a.is_dictionary]
            if dict_attr_ids:
                val_records = list(OzonAttributeValue
                    .select()
                    .where((OzonAttributeValue.user == current_user) &
                           (OzonAttributeValue.attribute_id.in_(dict_attr_ids)))
                    .order_by(OzonAttributeValue.attribute_id, OzonAttributeValue.value_id))
                for v in val_records:
                    adaptation_attr_values.setdefault(v.attribute_id, []).append({
                        'value_id': v.value_id, 'value': v.value, 'info': v.info
                    })

        # 解析已保存的属性值
        if adaptation.attribute_mapping_json:
            try:
                attr_data = json.loads(adaptation.attribute_mapping_json)
                adaptation_saved_attrs = attr_data.get('attributes', {}) if isinstance(attr_data, dict) else {}
            except (json.JSONDecodeError, TypeError):
                pass

    # 加载已有的视觉识别结果
    media_ids = [m.id for m in source_media]
    image_facts = []
    if media_ids:
        image_facts = (ImageFact
                       .select()
                       .where(ImageFact.user == current_user,
                              ImageFact.media_id.in_(media_ids))
                       .order_by(ImageFact.created_at.desc()))

    # 检查是否有已启用的视觉模型配置
    has_vision_config = (VisionModelConfig
                         .select()
                         .where((VisionModelConfig.user == current_user) &
                                (VisionModelConfig.enabled == True))
                         .exists())

    # 构建 source_media JSON 列表（供前端图库弹窗使用）
    source_media_list_json = []
    for m in source_media:
        # 解析 raw_json 中的扩展元数据
        extra = {}
        if m.raw_json:
            try:
                extra = json.loads(m.raw_json) if isinstance(m.raw_json, str) else m.raw_json
            except (json.JSONDecodeError, TypeError):
                pass
        source_media_list_json.append({
            'id': m.id,
            'source_url': m.source_url or '',
            'role': m.role or 'sku',
            'compliance_status': m.compliance_status or 'usable',
            'reject_reason': m.reject_reason or '',
            'review_status': m.review_status or 'pending',
            'width': m.width or 0,
            'height': m.height or 0,
            'source_area': extra.get('source_area', 'unknown'),
            'dom_path': extra.get('dom_path', ''),
            'alt': extra.get('alt', ''),
            'nearby_text': (extra.get('nearby_text') or '')[:100],
            'rule': extra.get('rule', ''),
            'evidence': extra.get('evidence', ''),
            'source_selector': extra.get('source_selector', ''),
            'collect_reason': extra.get('collect_reason', ''),
            'linked_sku_name': extra.get('linked_sku_name'),
            'media_source': m.media_source or 'browser_extension',
        })

    return render_template('ozon/adaptation_workspace.html',
                           source=source, source_skus=source_skus,
                           source_media=source_media,
                           source_media_list_json=source_media_list_json,
                           group=group, fact=fact, fact_skus=fact_skus,
                           adaptation=adaptation, gaps=gaps,
                           image_facts=image_facts,
                           has_vision_config=has_vision_config,
                           # 新增：类目/Type/属性/字典
                           adaptation_types=adaptation_types,
                           adaptation_attr_schema=adaptation_attr_schema,
                           adaptation_attr_values=adaptation_attr_values,
                           adaptation_saved_attrs=adaptation_saved_attrs)


@ozon_bp.route('/api/adaptation/<int:group_id>/save-fact', methods=['POST'])
@login_required
def api_save_fact(group_id):
    """保存商品事实"""
    group = (SourceProductGroup
             .select()
             .where((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
             .first())
    if not group:
        return jsonify({'ok': False, 'error': '任务组不存在'}), 404

    data = request.get_json() or {}
    fact = (ProductFact
            .select()
            .where((ProductFact.user == current_user) & (ProductFact.group == group))
            .first())
    if not fact:
        return jsonify({'ok': False, 'error': '商品事实不存在'}), 404

    for field in ['standard_name_cn', 'product_type', 'brand_name', 'model',
                  'material', 'origin', 'warranty', 'battery_capacity',
                  'power', 'wireless_range']:
        if field in data:
            setattr(fact, field, data[field])

    for json_field in ['functions_json', 'package_contents_json',
                       'usage_scenarios_json', 'compatibility_json',
                       'dimensions_json', 'weight_json', 'certifications_json',
                       'facts_json', 'unknown_fields_json', 'locked_fields_json']:
        if json_field in data and data[json_field]:
            setattr(fact, json_field, json.dumps(data[json_field], ensure_ascii=False))

    if 'confidence' in data:
        fact.confidence = data['confidence']
    if 'review_status' in data:
        fact.review_status = data['review_status']
    if 'reviewer_notes' in data:
        fact.reviewer_notes = data['reviewer_notes']
    fact.updated_at = datetime.datetime.now()
    fact.save()

    if 'skus' in data:
        for sku_data in data['skus']:
            sku_id = sku_data.get('id')
            if sku_id:
                sku = (ProductFactSku
                       .select()
                       .where((ProductFactSku.id == sku_id) & (ProductFactSku.fact == fact))
                       .first())
                if sku:
                    for f in ['color_cn', 'color_ru', 'size_cn', 'size_ru',
                              'style_cn', 'style_ru', 'standard_sku_name_cn',
                              'bundle_quantity', 'purchase_price_cny',
                              'package_contents_json']:
                        if f in sku_data:
                            setattr(sku, f, sku_data[f])
                    if 'confidence' in sku_data:
                        sku.confidence = sku_data['confidence']
                    if 'manual_status' in sku_data:
                        sku.manual_status = sku_data['manual_status']
                    sku.updated_at = datetime.datetime.now()
                    sku.save()

    group.status = 'adapting'
    group.updated_at = datetime.datetime.now()
    group.save()
    return jsonify({'ok': True, 'message': '商品事实已保存'})


@ozon_bp.route('/api/adaptation/<int:group_id>/relation', methods=['POST'])
@login_required
def api_set_relation(group_id):
    """设置适配关系"""
    group = (SourceProductGroup
             .select()
             .where((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
             .first())
    if not group:
        return jsonify({'ok': False, 'error': '任务组不存在'}), 404

    data = request.get_json() or {}
    relation_type = data.get('relation_type', 'one_to_one')
    if relation_type not in ('one_to_one', 'one_to_many', 'many_to_one'):
        return jsonify({'ok': False, 'error': '无效的适配关系'}), 400

    group.relation_type = relation_type
    group.updated_at = datetime.datetime.now()
    group.save()
    return jsonify({'ok': True, 'relation_type': relation_type})


@ozon_bp.route('/api/adaptation/<int:group_id>/save-category-type', methods=['POST'])
@login_required
def api_adaptation_save_category_type(group_id):
    """保存适配任务的 OZON 类目和 Type 选择"""
    group = (SourceProductGroup
             .select()
             .where((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
             .first())
    if not group:
        return jsonify({'ok': False, 'error': '任务组不存在'}), 404

    fact = (ProductFact
            .select()
            .where((ProductFact.user == current_user) & (ProductFact.group == group))
            .first())
    if not fact:
        return jsonify({'ok': False, 'error': '请先保存商品事实'}), 400

    data = request.get_json() or {}
    dcid = data.get('description_category_id', '').strip()
    type_id = data.get('type_id', '').strip()
    type_name = data.get('type_name', '')
    path = data.get('path', '')

    if not dcid:
        return jsonify({'ok': False, 'error': '缺少 description_category_id'}), 400

    # 查找或创建 adaptation
    adaptation = (ListingAdaptation
                  .select()
                  .where((ListingAdaptation.user == current_user) &
                         (ListingAdaptation.fact == fact))
                  .first())
    if not adaptation:
        adaptation = ListingAdaptation.create(
            user=current_user,
            fact=fact,
            relation_type=group.relation_type,
            status='draft',
        )

    # 更新类目信息
    adaptation.ozon_category_id = dcid
    adaptation.category_path = path

    # 尝试从已翻译的类目数据获取中文类目名
    cat_records = list(OzonCategory
                       .select()
                       .where((OzonCategory.user == current_user) &
                              (OzonCategory.ozon_category_id == dcid)))
    if cat_records:
        adaptation.ozon_category_name = cat_records[0].path or cat_records[0].name_cn or cat_records[0].name

    if type_id:
        adaptation.type_id = type_id
        adaptation.type_name_ru = type_name
        # 尝试获取已翻译的中文 type 名
        type_record = (OzonCategoryType
                       .select()
                       .where((OzonCategoryType.user == current_user) &
                              (OzonCategoryType.description_category_id == dcid) &
                              (OzonCategoryType.type_id == type_id))
                       .first())
        if type_record and type_record.type_name_cn:
            adaptation.type_name_cn = type_record.type_name_cn
        if type_record and not type_record.type_name_cn:
            adaptation.type_name_cn = type_name  # fallback

    adaptation.updated_at = datetime.datetime.now()
    adaptation.save()

    # 更新 group 状态
    if group.status == 'draft':
        group.status = 'adapting'
        group.updated_at = datetime.datetime.now()
        group.save()

    return jsonify({
        'ok': True,
        'message': '已绑定类目/Type',
        'ozon_category_id': dcid,
        'type_id': type_id or None,
        'type_name_cn': adaptation.type_name_cn or type_name,
        'category_path': path,
    })


@ozon_bp.route('/api/adaptation/<int:group_id>/save-attributes', methods=['POST'])
@login_required
def api_adaptation_save_attributes(group_id):
    """保存适配任务的属性填写值并更新缺口"""
    group = (SourceProductGroup
             .select()
             .where((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
             .first())
    if not group:
        return jsonify({'ok': False, 'error': '任务组不存在'}), 404

    fact = (ProductFact
            .select()
            .where((ProductFact.user == current_user) & (ProductFact.group == group))
            .first())
    if not fact:
        return jsonify({'ok': False, 'error': '请先保存商品事实'}), 400

    adaptation = (ListingAdaptation
                  .select()
                  .where((ListingAdaptation.user == current_user) &
                         (ListingAdaptation.fact == fact))
                  .first())
    if not adaptation:
        return jsonify({'ok': False, 'error': '请先选择类目'}), 400

    data = request.get_json() or {}
    category_id = data.get('category_id', adaptation.ozon_category_id)
    type_id = data.get('type_id', adaptation.type_id)
    attributes = data.get('attributes', {})

    # 构建 attribute_mapping_json
    mapping = {
        'category_id': category_id,
        'type_id': type_id,
        'type_name_ru': adaptation.type_name_ru,
        'category_path': adaptation.category_path,
        'attributes': attributes,
        'filled_at': datetime.datetime.now().isoformat(),
    }
    adaptation.attribute_mapping_json = json.dumps(mapping, ensure_ascii=False)
    adaptation.updated_at = datetime.datetime.now()
    adaptation.save()

    # 重新生成缺口记录
    # 1) 删除旧的未解决缺口
    OzonFieldGap.delete().where(
        (OzonFieldGap.user == current_user) &
        (OzonFieldGap.adaptation == adaptation) &
        (OzonFieldGap.resolved == False)
    ).execute()

    # 2) 查询当前 type 的必填属性
    blocking_count = 0
    gaps = []
    if type_id and category_id:
        required_attrs = (OzonCategoryAttribute
                          .select()
                          .where((OzonCategoryAttribute.user == current_user) &
                                 (OzonCategoryAttribute.ozon_category_id == category_id) &
                                 (OzonCategoryAttribute.type_id == type_id) &
                                 (OzonCategoryAttribute.is_required == True))
                          .order_by(OzonCategoryAttribute.attribute_id))

        for attr in required_attrs:
            filled_val = attributes.get(attr.attribute_id)
            is_filled = filled_val is not None and filled_val != '' and filled_val != False
            if not is_filled:
                gap = OzonFieldGap.create(
                    user=current_user,
                    adaptation=adaptation,
                    ozon_category_id=category_id,
                    attribute_id=attr.attribute_id,
                    field_name=attr.name_cn or attr.name,
                    gap_type='missing_required',
                    severity='error',
                    suggested_action=f'请填写必填属性: {attr.name_cn or attr.name}',
                    resolved=False,
                )
                gaps.append({
                    'attribute_id': attr.attribute_id,
                    'field_name': attr.name_cn or attr.name,
                    'severity': 'error',
                })
                blocking_count += 1

    return jsonify({
        'ok': True,
        'message': f'属性已保存{"，" + str(blocking_count) + " 个必填字段未填写" if blocking_count else ""}',
        'blocking_count': blocking_count,
        'gaps': gaps,
    })


@ozon_bp.route('/api/adaptation/<int:group_id>/sync-type-attributes', methods=['POST'])
@login_required
def api_adaptation_sync_type_attributes(group_id):
    """触发当前绑定 type 的属性+字典同步，委托已有同步逻辑"""
    group = (SourceProductGroup
             .select()
             .where((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
             .first())
    if not group:
        return jsonify({'ok': False, 'error': '任务组不存在'}), 404

    fact = (ProductFact
            .select()
            .where((ProductFact.user == current_user) & (ProductFact.group == group))
            .first())
    if not fact:
        return jsonify({'ok': False, 'error': '请先保存商品事实'}), 400

    adaptation = (ListingAdaptation
                  .select()
                  .where((ListingAdaptation.user == current_user) &
                         (ListingAdaptation.fact == fact))
                  .first())
    if not adaptation or not adaptation.ozon_category_id:
        return jsonify({'ok': False, 'error': '请先选择类目'}), 400

    dcid = adaptation.ozon_category_id
    type_id = adaptation.type_id
    if not type_id:
        return jsonify({'ok': False, 'error': '请先选择商品类型 (type)'}), 400

    # 委托给同步逻辑：前端可以直接调用 sync-current-type-full
    # 这里返回 redirect 信息让前端两步完成
    return jsonify({
        'ok': True,
        'message': '请通过同步端点获取属性',
        'dcid': dcid,
        'type_id': type_id,
        'sync_url': url_for('ozon.api_sync_current_type_full', dcid=dcid, type_id=type_id),
        'attrs_url': url_for('ozon.api_get_category_attributes', cat_id=dcid, type_id=type_id),
    })


@ozon_bp.route('/api/adaptation/<int:group_id>/recommend-category', methods=['GET'])
@login_required
def api_adaptation_recommend_category(group_id):
    """轻量级 OZON 类目/Type 推荐（不依赖 AI）"""
    group = (SourceProductGroup
             .select()
             .where((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
             .first())
    if not group:
        return jsonify({'ok': False, 'error': '任务组不存在'}), 404

    fact = (ProductFact
            .select()
            .where((ProductFact.user == current_user) & (ProductFact.group == group))
            .first())
    if not fact:
        return jsonify({'ok': False, 'error': '请先保存商品事实'}), 400

    # 构建搜索关键词：从品类提示、商品类型、标题中提取
    keywords = []
    if fact.category_hint_cn:
        keywords.extend(fact.category_hint_cn.replace('>', ' ').replace('>', ' ').split())
    if fact.product_type:
        keywords.append(fact.product_type)
    if fact.standard_name_cn:
        # 从标题提取关键品类词
        for word in fact.standard_name_cn.split():
            if len(word) >= 2:
                keywords.append(word)

    # 去重
    keywords = list(dict.fromkeys(kw.lower() for kw in keywords if len(kw) >= 2))[:10]

    recommendations = []

    if keywords:
        # 1) 先查用户收藏的常用 type
        favorites = list(OzonFavoriteCategoryType
                         .select()
                         .where(OzonFavoriteCategoryType.user == current_user)
                         .order_by(OzonFavoriteCategoryType.created_at.desc())
                         .limit(20))

        for fav in favorites:
            score = 0
            fav_text = (fav.type_name_cn or fav.type_name or '').lower() + ' ' + (fav.path or '').lower()
            for kw in keywords:
                if kw in fav_text:
                    score += 2  # 收藏加权
            if score > 0:
                recommendations.append({
                    'description_category_id': fav.description_category_id,
                    'type_id': fav.type_id,
                    'type_name': fav.type_name_cn or fav.type_name,
                    'type_name_ru': fav.type_name,
                    'path': fav.path or '',
                    'score': score,
                    'reason': f'收藏匹配: {fav.type_name_cn or fav.type_name}',
                    'source': 'favorite',
                })

        # 2) 从 OzonCategoryType 匹配（type_name_cn / type_name）
        types = list(OzonCategoryType
                     .select()
                     .where((OzonCategoryType.user == current_user) &
                            (OzonCategoryType.type_name_cn.is_null(False)))
                     .order_by(OzonCategoryType.type_name_cn))

        for t in types:
            type_text = (t.type_name_cn or t.type_name or '').lower()
            score = 0
            for kw in keywords:
                if kw in type_text:
                    score += 1
            if score >= 2:  # 至少匹配 2 个关键词
                recommendations.append({
                    'description_category_id': t.description_category_id,
                    'type_id': t.type_id,
                    'type_name': t.type_name_cn or t.type_name,
                    'type_name_ru': t.type_name,
                    'path': t.path or '',
                    'score': score,
                    'reason': f'关键词匹配 ({score}): {t.type_name_cn or t.type_name}',
                    'source': 'keyword',
                })

    # 3) 如果没有匹配结果，回退到类目路径模糊搜索
    if not recommendations:
        cats = list(OzonCategory
                    .select()
                    .where((OzonCategory.user == current_user) &
                           (OzonCategory.name_cn.is_null(False)))
                    .order_by(OzonCategory.name_cn))
        for kw in keywords:
            for cat in cats:
                if cat.name_cn and kw in cat.name_cn.lower():
                    # 查该类目下是否有 type
                    child_types = list(OzonCategoryType
                                       .select()
                                       .where((OzonCategoryType.user == current_user) &
                                              (OzonCategoryType.description_category_id == cat.ozon_category_id))
                                       .limit(3))
                    for ct in child_types:
                        recommendations.append({
                            'description_category_id': cat.ozon_category_id,
                            'type_id': ct.type_id,
                            'type_name': ct.type_name_cn or ct.type_name,
                            'type_name_ru': ct.type_name,
                            'path': cat.name_cn or cat.name,
                            'score': 1,
                            'reason': f'类目匹配: {cat.name_cn or cat.name} > {ct.type_name_cn or ct.type_name}',
                            'source': 'category_fallback',
                        })

    # 去重 + 排序 + Top 5
    seen = set()
    unique_recs = []
    for r in sorted(recommendations, key=lambda x: x['score'], reverse=True):
        key = (r['description_category_id'], r['type_id'])
        if key not in seen:
            seen.add(key)
            unique_recs.append(r)
            if len(unique_recs) >= 5:
                break

    return jsonify({
        'ok': True,
        'keywords_used': keywords,
        'recommendations': unique_recs,
        'total': len(unique_recs),
    })


@ozon_bp.route('/api/adaptation/<int:group_id>/generate-draft', methods=['POST'])
@login_required
def api_generate_draft(group_id):
    """从商品事实生成 OZON 草稿"""
    group = (SourceProductGroup
             .select()
             .where((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
             .first())
    if not group:
        return jsonify({'ok': False, 'error': '任务组不存在'}), 404

    fact = (ProductFact
            .select()
            .where((ProductFact.user == current_user) & (ProductFact.group == group))
            .first())
    if not fact:
        return jsonify({'ok': False, 'error': '请先保存商品事实'}), 400

    if fact.review_status != 'approved':
        return jsonify({'ok': False, 'error': '商品事实尚未审核通过'}), 400

    adaptation = (ListingAdaptation
                  .select()
                  .where((ListingAdaptation.user == current_user) &
                         (ListingAdaptation.fact == fact))
                  .first())
    if not adaptation:
        adaptation = ListingAdaptation.create(
            user=current_user,
            fact=fact,
            relation_type=group.relation_type,
            status='draft',
        )

    item = (SourceProductGroupItem
            .select()
            .where(SourceProductGroupItem.group == group)
            .first())
    source = item.source if item else None

    draft = _get_or_create_draft(source) if source else None
    if draft:
        if fact.standard_name_ru:
            draft.title_ru = fact.standard_name_ru
        if adaptation.title_ru:
            draft.title_ru = adaptation.title_ru
        if adaptation.description_ru:
            draft.description_ru = adaptation.description_ru
        if adaptation.bullets_ru_json:
            draft.bullets_ru = adaptation.bullets_ru_json
        if adaptation.ozon_category_id:
            draft.ozon_category_id = adaptation.ozon_category_id
        if adaptation.ozon_category_name:
            draft.ozon_category_path = adaptation.ozon_category_name
        if adaptation.category_path:
            draft.category_path_cn = adaptation.category_path
        if adaptation.type_id:
            draft.type_id = adaptation.type_id
        if adaptation.type_name_ru:
            draft.type_name_ru = adaptation.type_name_ru
        if adaptation.type_name_cn:
            draft.type_name_cn = adaptation.type_name_cn
        if adaptation.attribute_mapping_json:
            draft.attributes_json = adaptation.attribute_mapping_json
        draft.updated_at = datetime.datetime.now()
        draft.save()

        adaptation.draft = draft
        adaptation.status = 'converted'
        adaptation.updated_at = datetime.datetime.now()
        adaptation.save()

        group.status = 'converted'
        group.updated_at = datetime.datetime.now()
        group.save()

        return jsonify({'ok': True, 'message': '草稿已生成',
                        'draft_id': draft.id,
                        'redirect': url_for('ozon.listing_review', draft_id=draft.id)})

    return jsonify({'ok': False, 'error': '需要先有采集商品'}), 400


@ozon_bp.route('/api/adaptation/<int:group_id>/ai-suggest', methods=['POST'])
@login_required
def api_ai_suggest(group_id):
    """AI 智能填充商品事实 — 综合源数据+视觉识别结果"""
    group = (SourceProductGroup
             .select()
             .where((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
             .first())
    if not group:
        return jsonify({'ok': False, 'error': '任务组不存在'}), 404

    fact = (ProductFact
            .select()
            .where((ProductFact.user == current_user) & (ProductFact.group == group))
            .first())
    if not fact:
        return jsonify({'ok': False, 'error': '请先进入适配工作台'}), 400

    # 获取源商品数据
    item = (SourceProductGroupItem
            .select()
            .where(SourceProductGroupItem.group == group)
            .first())
    if not item:
        return jsonify({'ok': False, 'error': '适配任务未关联源商品'}), 400

    source = item.source
    source_skus = list(OzonSourceSku
                       .select()
                       .where(OzonSourceSku.source == source)
                       .order_by(OzonSourceSku.source_order))

    # 获取已接受的视觉识别事实
    media_ids = [m.id for m in OzonSourceMedia
                 .select().where(OzonSourceMedia.source == source)]
    image_facts = []
    if media_ids:
        image_facts = list(ImageFact
                           .select()
                           .where(ImageFact.user == current_user,
                                  ImageFact.media_id.in_(media_ids),
                                  ImageFact.accepted == True))

    # 获取 AI API Key
    from crypto_utils import decrypt_api_key
    key_record = UserApiKey.get_or_none(UserApiKey.user == current_user)
    if not key_record:
        return jsonify({'ok': False, 'error': '未配置 AI API Key，请先在平台接口页配置'}), 400

    api_key = decrypt_api_key(key_record.api_key)
    provider = key_record.api_provider or 'deepseek'

    # 构建输入数据
    source_data = {
        'title': source.title_cn or '',
        'category': source.category_cn or '',
        'shop_name': source.shop_name or '',
        'description': (source.description_cn or '')[:1000],
        'skus': [{
            'order': s.source_order,
            'name': s.source_sku_name,
            'color': s.color_cn,
            'size': s.size_cn,
            'style': s.style_cn,
            'price': s.purchase_price_cny,
        } for s in source_skus],
    }

    vision_facts = [{
        'field_path': f.field_path,
        'value': f.value,
        'evidence': f.evidence_text,
        'confidence': f.confidence,
    } for f in image_facts]

    prompt = (
        '你是电商商品数据分析助手。请根据提供的源商品数据和图片识别结果，分析并提取标准化的商品事实。\n\n'
        '源商品数据：\n' + json.dumps(source_data, ensure_ascii=False, indent=2) + '\n\n'
        '图片识别事实：\n' + json.dumps(vision_facts, ensure_ascii=False, indent=2) + '\n\n'
        '请输出以下 JSON（不要 markdown 代码块），所有中文内容：\n'
        '{\n'
        '  "standard_name_cn": "去除营销词的标准商品名（如：DJI Mic Mini 2 一拖二无线麦克风 含充电盒）",\n'
        '  "product_type": "商品类型（如：无线麦克风 / 车载工具 / 摄影配件）",\n'
        '  "category_hint_cn": "建议的本地品类路径（如：3C数码 > 音频配件）",\n'
        '  "brand_name": "品牌名，无法确认则为 null",\n'
        '  "model": "型号，无法确认则为 null",\n'
        '  "material": "材质，无法确认则为 null",\n'
        '  "functions_json": ["功能点列表，如 [\\"无线收音\\",\\"降噪\\",\\"长续航\\"]"],\n'
        '  "package_contents_json": ["包装内容列表"],\n'
        '  "unknown_fields_json": ["无法确认的字段名列表，如 [\\"材质\\",\\"重量\\",\\"电池容量\\"]"],\n'
        '  "confidence": 0.85,\n'
        '  "skus": [\n'
        '    {\n'
        '      "source_order": 1,\n'
        '      "standard_sku_name_cn": "标准化SKU名",\n'
        '      "color_cn": "颜色",\n'
        '      "style_cn": "款式/套餐名",\n'
        '      "bundle_quantity": 1,\n'
        '      "package_contents_json": ["此SKU的包装内容"]\n'
        '    }\n'
        '  ]\n'
        '}\n\n'
        '规则：\n'
        '1. 标题去掉「厂家直销」「爆款」「促销」「包邮」等营销词\n'
        '2. SKU 顺序必须与源数据一致\n'
        '3. 颜色/款式/套餐从 SKU 名称中拆解，不要编造\n'
        '4. 不确定的字段放入 unknown_fields_json，值设为 null\n'
        '5. 优先采纳图片识别事实中的信息\n'
        '6. confidence 按 (已确认来源数 / 总字段数) 估算'
    )

    try:
        import openai
        client = openai.OpenAI(
            api_key=api_key,
            base_url='https://api.deepseek.com' if provider == 'deepseek' else None,
        )
        response = client.chat.completions.create(
            model='deepseek-chat',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.2,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'AI 调用失败: {str(e)[:200]}'}), 500

    # 清洗 JSON
    if raw.startswith('```'):
        lines = raw.split('\n')
        if lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        raw = '\n'.join(lines)

    try:
        ai_data = json.loads(raw)
    except json.JSONDecodeError:
        return jsonify({'ok': False, 'error': 'AI 返回格式异常', 'raw': raw[:500]}), 500

    # 写入 ProductFact
    ai_to_fact = {
        'standard_name_cn': 'standard_name_cn', 'product_type': 'product_type',
        'category_hint_cn': 'category_hint_cn', 'brand_name': 'brand_name',
        'model': 'model', 'material': 'material',
    }
    for ai_key, fact_key in ai_to_fact.items():
        if ai_key in ai_data and ai_data[ai_key] is not None:
            setattr(fact, fact_key, ai_data[ai_key])

    for json_key in ['functions_json', 'package_contents_json', 'unknown_fields_json']:
        if json_key in ai_data and ai_data[json_key]:
            setattr(fact, json_key, json.dumps(ai_data[json_key], ensure_ascii=False))

    if 'confidence' in ai_data:
        fact.confidence = ai_data['confidence']
    fact.review_status = 'pending'
    fact.updated_at = datetime.datetime.now()
    fact.save()

    # 写入 SKU 事实
    skus_updated = 0
    if 'skus' in ai_data:
        fact_skus = list(ProductFactSku
                         .select()
                         .where(ProductFactSku.fact == fact)
                         .order_by(ProductFactSku.source_order))
        for ai_sku in ai_data['skus']:
            order = ai_sku.get('source_order', 0)
            target = None
            for fs in fact_skus:
                if fs.source_order == order:
                    target = fs
                    break
            if not target:
                continue
            sku_fields = {
                'standard_sku_name_cn': 'standard_sku_name_cn',
                'color_cn': 'color_cn', 'style_cn': 'style_cn',
                'bundle_quantity': 'bundle_quantity',
            }
            for ai_k, sku_k in sku_fields.items():
                if ai_k in ai_sku and ai_sku[ai_k] is not None:
                    setattr(target, sku_k, ai_sku[ai_k])
            if 'package_contents_json' in ai_sku and ai_sku['package_contents_json']:
                target.package_contents_json = json.dumps(ai_sku['package_contents_json'], ensure_ascii=False)
            target.updated_at = datetime.datetime.now()
            target.save()
            skus_updated += 1

    return jsonify({
        'ok': True,
        'message': f'AI 已完成：商品事实 + {skus_updated} 个 SKU 已填充',
        'confidence': ai_data.get('confidence'),
        'unknown_fields': ai_data.get('unknown_fields_json', []),
    })


@ozon_bp.route('/api/adaptation/<int:source_id>/analyze-images', methods=['POST'])
@login_required
def api_analyze_images(source_id):
    """批量识别源商品的所有图片，结果写入 ImageFact"""
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        return jsonify({'ok': False, 'error': '采集商品不存在'}), 404

    config = (VisionModelConfig
              .select()
              .where((VisionModelConfig.user == current_user) &
                     (VisionModelConfig.enabled == True))
              .first())
    if not config:
        return jsonify({'ok': False, 'error': '未配置已启用的视觉模型'}), 400

    media_list = list(OzonSourceMedia
                      .select()
                      .where(OzonSourceMedia.source == source))

    if not media_list:
        return jsonify({'ok': False, 'error': '没有可识别的图片'}), 400

    # 确定每张图的任务类型
    def task_for_media(m):
        role = (m.role or '').lower()
        if role in ('detail', 'scene', 'selling_point', 'function', 'size', 'package'):
            return 'detail_ocr'
        return 'sku_image'

    # 获取图片内容（从 URL 下载）
    import requests as req
    results = []
    for m in media_list:
        try:
            image_bytes = None
            if m.source_url:
                try:
                    r = req.get(m.source_url, timeout=15, headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                        'Referer': 'https://detail.1688.com/',
                    })
                    if r.status_code == 200 and len(r.content) < 10 * 1024 * 1024:
                        image_bytes = r.content
                except Exception:
                    pass

            if not image_bytes:
                results.append({'media_id': m.id, 'role': m.role, 'ok': False, 'error': '无法下载图片'})
                continue

            task_type = task_for_media(m)
            vision_result = call_vision_api(config, image_bytes, m.source_url or f'img_{m.id}.jpg', task_type)

            # 创建 ImageAnalysisJob
            job = ImageAnalysisJob.create(
                user=current_user,
                media=m,
                source=source,
                task_type=task_type,
                provider=config.provider,
                model_name=config.model_name,
                status='success',
                parsed_json=json.dumps(vision_result, ensure_ascii=False),
            )

            # 提取 facts 写入 ImageFact
            facts_created = 0
            for fact_item in vision_result.get('facts', []):
                ImageFact.create(
                    user=current_user,
                    image_analysis_job=job,
                    media=m,
                    field_path=fact_item.get('field_path', ''),
                    value=str(fact_item.get('value', '')),
                    evidence_text=fact_item.get('evidence', ''),
                    confidence=float(fact_item.get('confidence', 0.8)),
                    requires_manual_confirmation=fact_item.get('confidence', 0) < 0.85,
                )
                facts_created += 1

            results.append({
                'media_id': m.id, 'role': m.role, 'ok': True,
                'task_type': task_type,
                'facts_created': facts_created,
                'summary': vision_result.get('summary_cn', ''),
            })

        except Exception as e:
            results.append({'media_id': m.id, 'role': m.role, 'ok': False, 'error': str(e)[:200]})

    ok_count = sum(1 for r in results if r.get('ok'))
    total_facts = sum(r.get('facts_created', 0) for r in results)
    return jsonify({
        'ok': True,
        'message': f'识别完成：{ok_count}/{len(media_list)} 张图片，提取 {total_facts} 条事实',
        'total': len(media_list),
        'ok_count': ok_count,
        'total_facts': total_facts,
        'results': results,
    })


@ozon_bp.route('/api/adaptation/accept-image-fact/<int:image_fact_id>', methods=['POST'])
@login_required
def api_accept_image_fact(image_fact_id):
    """接受一条视觉识别事实，将其值写入对应的 ProductFact / ProductFactSku"""
    img_fact = (ImageFact
                .select()
                .where((ImageFact.id == image_fact_id) & (ImageFact.user == current_user))
                .first())
    if not img_fact:
        return jsonify({'ok': False, 'error': '事实不存在'}), 404

    # 找到关联的 source 和 group
    source = img_fact.media.source
    group = (SourceProductGroup
             .select()
             .join(SourceProductGroupItem)
             .where((SourceProductGroup.user == current_user) &
                    (SourceProductGroupItem.source == source))
             .first())

    if not group:
        return jsonify({'ok': False, 'error': '找不到关联的适配任务'}), 404

    fact = (ProductFact
            .select()
            .where((ProductFact.user == current_user) & (ProductFact.group == group))
            .first())
    if not fact:
        return jsonify({'ok': False, 'error': '请先进入适配工作台创建商品事实'}), 400

    field_path = img_fact.field_path
    value = img_fact.value

    # 根据 field_path 写入对应字段
    # 商品级字段映射
    fact_fields = {
        'brand_name': 'brand_name', 'model': 'model', 'material': 'material',
        'origin': 'origin', 'warranty': 'warranty', 'product_type': 'product_type',
        'battery_capacity': 'battery_capacity', 'power': 'power', 'wireless_range': 'wireless_range',
        'standard_name_cn': 'standard_name_cn',
        'package_contents': 'package_contents_json',
    }

    # SKU 级字段 (格式: skus[0].color_cn 等)
    if field_path.startswith('skus['):
        import re
        match = re.match(r'skus\[(\d+)\]\.(.+)', field_path)
        if match:
            sku_idx = int(match.group(1))
            sku_field = match.group(2)
            sku_fields_map = {
                'color_cn': 'color_cn', 'color_ru': 'color_ru',
                'size_cn': 'size_cn', 'size_ru': 'size_ru',
                'style_cn': 'style_cn', 'style_ru': 'style_ru',
                'bundle_quantity': 'bundle_quantity',
                'purchase_price_cny': 'purchase_price_cny',
                'standard_sku_name_cn': 'standard_sku_name_cn',
                'package_contents': 'package_contents_json',
            }
            if sku_field in sku_fields_map:
                fact_skus = list(ProductFactSku
                                 .select()
                                 .where(ProductFactSku.fact == fact)
                                 .order_by(ProductFactSku.source_order))
                if sku_idx < len(fact_skus):
                    target_sku = fact_skus[sku_idx]
                    db_field = sku_fields_map[sku_field]
                    val = value
                    if db_field == 'bundle_quantity':
                        val = int(value) if str(value).isdigit() else value
                    elif db_field == 'purchase_price_cny':
                        val = float(value) if value else None
                    setattr(target_sku, db_field, val)
                    target_sku.updated_at = datetime.datetime.now()
                    target_sku.save()

                    # 创建证据记录
                    ProductFactEvidence.create(
                        user=current_user,
                        fact=fact,
                        fact_sku=target_sku,
                        field_path=field_path,
                        evidence_type='ocr' if 'ocr' in img_fact.evidence_text.lower() else 'ai',
                        media=img_fact.media,
                        content=img_fact.evidence_text,
                        confidence=img_fact.confidence,
                    )

                    img_fact.accepted = True
                    img_fact.accepted_at = datetime.datetime.now()
                    img_fact.save()
                    return jsonify({'ok': True, 'message': f'SKU 事实已写入 {field_path}'})

            return jsonify({'ok': False, 'error': f'无法映射 SKU 字段: {sku_field}'}), 400

    elif field_path in fact_fields:
        db_field = fact_fields[field_path]
        val = value
        if db_field == 'package_contents_json' and not value.startswith('['):
            val = json.dumps([value], ensure_ascii=False)
        setattr(fact, db_field, val)
        fact.updated_at = datetime.datetime.now()
        fact.save()

        ProductFactEvidence.create(
            user=current_user,
            fact=fact,
            field_path=field_path,
            evidence_type='ocr' if 'ocr' in img_fact.evidence_text.lower() else 'ai',
            media=img_fact.media,
            content=img_fact.evidence_text,
            confidence=img_fact.confidence,
        )

        img_fact.accepted = True
        img_fact.accepted_at = datetime.datetime.now()
        img_fact.save()
        return jsonify({'ok': True, 'message': f'商品事实已写入 {field_path}'})

    return jsonify({'ok': False, 'error': f'未知字段路径: {field_path}'}), 400


@ozon_bp.route('/api/adaptation/reject-image-fact/<int:image_fact_id>', methods=['POST'])
@login_required
def api_reject_image_fact(image_fact_id):
    """拒绝一条视觉识别事实"""
    img_fact = (ImageFact
                .select()
                .where((ImageFact.id == image_fact_id) & (ImageFact.user == current_user))
                .first())
    if not img_fact:
        return jsonify({'ok': False, 'error': '事实不存在'}), 404

    img_fact.requires_manual_confirmation = True
    img_fact.accepted = False
    img_fact.save()
    return jsonify({'ok': True, 'message': '已标记为不需要'})


# ═══════════════════════════════════════════════════════
# P5 — 商品事实库
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/fact-library')
@login_required
def fact_library():
    """商品事实库列表页"""
    product_type = request.args.get('product_type', '').strip()
    review_status = request.args.get('review_status', '').strip()

    query = ProductFact.select().where(ProductFact.user == current_user)
    if product_type:
        query = query.where(ProductFact.product_type == product_type)
    if review_status:
        query = query.where(ProductFact.review_status == review_status)

    facts = query.order_by(ProductFact.updated_at.desc())
    return render_template('ozon/fact_library.html', facts=facts)


@ozon_bp.route('/api/fact/<int:fact_id>/approve', methods=['POST'])
@login_required
def api_approve_fact(fact_id):
    """审核通过商品事实"""
    fact = (ProductFact
            .select()
            .where((ProductFact.id == fact_id) & (ProductFact.user == current_user))
            .first())
    if not fact:
        return jsonify({'ok': False, 'error': '事实不存在'}), 404

    fact.review_status = 'approved'
    fact.updated_at = datetime.datetime.now()
    fact.save()
    return jsonify({'ok': True, 'message': '事实已审核通过'})


@ozon_bp.route('/fact-library/batch-delete', methods=['POST'])
@login_required
def fact_batch_delete():
    """批量删除商品事实（级联清理关联数据）"""
    ids = _parse_batch_ids(request.form.get('ids', ''))
    if not ids:
        flash('未选择任何记录', 'warning')
        return redirect(url_for('ozon.fact_library'))

    count = 0
    for fact_id in ids:
        fact = (ProductFact
                .select()
                .where((ProductFact.id == fact_id) & (ProductFact.user == current_user))
                .first())
        if fact:
            ProductFactEvidence.delete().where(ProductFactEvidence.fact == fact).execute()
            ProductFactSku.delete().where(ProductFactSku.fact == fact).execute()
            ListingAdaptation.delete().where(ListingAdaptation.fact == fact).execute()
            fact.delete_instance()
            count += 1

    flash(f'已删除 {count} 条商品事实', 'success')
    return redirect(url_for('ozon.fact_library'))


# ═══════════════════════════════════════════════════════
# P6 — 类目属性字典
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/api/category/sync-tree', methods=['POST'])
@login_required
def api_sync_category_tree():
    """从 OZON API 拉取类目树并保存到本地"""
    # 获取第一个已启用的 OZON 店铺
    account = (OzonAccount
               .select()
               .where((OzonAccount.user == current_user) &
                      (OzonAccount.is_active == True))
               .first())
    if not account:
        return jsonify({'ok': False, 'error': '未找到已启用的 OZON 店铺，请先在平台接口页配置'}), 400

    try:
        from services.ozon_api import create_client
        client = create_client(account)
        tree = client.get_category_tree()

        def save_categories(cats, parent_id=None, path=''):
            count = 0
            for cat in cats:
                # 规则：含 type_id 的节点禁止写入 OzonCategory
                if cat.get('type_id'):
                    # 仍然递归处理其 children（可能含真实子类目）
                    if cat.get('children'):
                        count += save_categories(cat['children'], parent_id, path)
                    continue

                cat_id = str(cat.get('category_id', ''))
                title = cat.get('title', '')
                current_path = (path + ' > ' + title).strip(' > ')
                record, created = OzonCategory.get_or_create(
                    user=current_user,
                    ozon_category_id=cat_id,
                    defaults={
                        'name': title,
                        'path': current_path,
                        'parent_id': parent_id,
                        'is_leaf': not cat.get('children'),
                        'source': 'api',
                        'raw_json': json.dumps(cat, ensure_ascii=False),
                        'last_synced_at': datetime.datetime.now(),
                    },
                )
                if not created:
                    record.name = title
                    record.path = current_path
                    record.parent_id = parent_id
                    record.is_leaf = not cat.get('children')
                    record.raw_json = json.dumps(cat, ensure_ascii=False)
                    record.last_synced_at = datetime.datetime.now()
                    record.save()
                count += 1
                if cat.get('children'):
                    count += save_categories(cat['children'], cat_id, current_path)
            return count

        total = save_categories(tree)
        return jsonify({'ok': True, 'message': f'已同步 {total} 个类目，点击"🌐 翻译全部"翻译', 'total': total})

    except Exception as e:
        return jsonify({'ok': False, 'error': f'API 调用失败: {str(e)[:300]}'}), 500


@ozon_bp.route('/api/category/translate', methods=['POST'])
@login_required
def api_translate_categories():
    """对已有类目、类型和属性进行翻译（每次最多处理限定数量，自动循环直到完成）"""
    import re as _re
    _has_cyrillic = _re.compile('[а-яА-ЯёЁ]')

    def needs_translation(cn_val, ru_val):
        """判断是否需要翻译：无中文名/中文名等于俄文名/中文名仍含俄语字符"""
        if not cn_val:
            return True
        if cn_val == ru_val:
            return True
        if _has_cyrillic.search(cn_val):
            return True
        return False

    errors = []
    limit = int(request.args.get('limit', 200))

    # ── 收集所有待翻译项 ──
    cats = list(OzonCategory.select().where(OzonCategory.user == current_user))
    total_cats = len(cats)
    need_trans_cats = [c for c in cats if needs_translation(c.name_cn, c.name)]

    # ── 翻译商品类型名 ──
    types = list(OzonCategoryType
                 .select()
                 .where(OzonCategoryType.user == current_user))
    total_types = len(types)
    need_trans_types = [t for t in types if needs_translation(t.type_name_cn, t.type_name)]

    # ── 翻译属性名 ──
    attrs = list(OzonCategoryAttribute
                 .select()
                 .where(OzonCategoryAttribute.user == current_user))
    need_trans_attrs = [a for a in attrs if needs_translation(a.name_cn, a.name)]

    # 合并所有待翻译名称（去重，限制数量）
    all_names = []
    name_sources = {}  # name -> ['cat', 'type', 'attr']
    for c in need_trans_cats:
        if c.name and c.name not in name_sources:
            all_names.append(c.name)
            name_sources[c.name] = 'cat'
    for t in need_trans_types:
        if t.type_name and t.type_name not in name_sources:
            all_names.append(t.type_name)
            name_sources[t.type_name] = 'type'
    for a in need_trans_attrs:
        if a.name and a.name not in name_sources:
            all_names.append(a.name)
            name_sources[a.name] = 'attr'

    # 限制每次翻译数量
    batch_names = all_names[:limit]
    remaining = len(all_names) - len(batch_names)

    cat_translated = 0
    type_translated = 0
    attr_translated = 0

    if batch_names:
        result = _batch_translate(batch_names, current_user)
        errors.extend(result.pop('_errors', []))

        # 应用到类目
        for cat in need_trans_cats:
            if cat.name in result and result[cat.name] != cat.name:
                cat.name_cn = result[cat.name]
                cat.save()
                cat_translated += 1

        # 应用到类型
        for type_name_ru, cn in result.items():
            if cn and cn != type_name_ru:
                count = (OzonCategoryType
                         .update(type_name_cn=cn)
                         .where((OzonCategoryType.user == current_user) &
                                (OzonCategoryType.type_name == type_name_ru))
                         .execute())
                type_translated += count

        # 应用到属性
        for attr_name, cn in result.items():
            if cn and cn != attr_name and not attr_name.startswith('_'):
                count = (OzonCategoryAttribute
                         .update(name_cn=cn)
                         .where((OzonCategoryAttribute.user == current_user) &
                                (OzonCategoryAttribute.name == attr_name))
                         .execute())
                attr_translated += count

    msg = f'本次翻译：类目 {cat_translated} 个，类型 {type_translated} 个，属性 {attr_translated} 个'
    if remaining > 0:
        msg += f'。还剩 {remaining} 个未翻译，请再次点击继续。'

    return jsonify({
        'ok': True,
        'message': msg,
        'cats': cat_translated,
        'types': type_translated,
        'attrs': attr_translated,
        'remaining': remaining,
        'need_trans': {'cats': len(need_trans_cats), 'types': len(need_trans_types), 'attrs': len(need_trans_attrs)},
        'errors': errors[:5] if errors else [],
    })


@ozon_bp.route('/api/category/<cat_id>/get-attributes')
@login_required
def api_get_category_attributes(cat_id):
    """获取已缓存的类目属性（JSON）"""
    type_id = request.args.get('type_id', '').strip()
    # 查 type 的真实 cat_id
    actual_cat_id = cat_id
    if type_id:
        type_record = (OzonCategoryType
                       .select()
                       .where((OzonCategoryType.user == current_user) &
                              (OzonCategoryType.type_id == type_id))
                       .first())
        if type_record:
            actual_cat_id = type_record.description_category_id
    query = (OzonCategoryAttribute
             .select()
             .where((OzonCategoryAttribute.user == current_user) &
                    (OzonCategoryAttribute.ozon_category_id == actual_cat_id)))
    if type_id:
        query = query.where(OzonCategoryAttribute.type_id == type_id)

    attr_list = list(query.order_by(OzonCategoryAttribute.is_required.desc(),
                                     OzonCategoryAttribute.attribute_id))

    # 批量加载字典值
    dict_attr_ids = [a.attribute_id for a in attr_list if a.is_dictionary]
    values_map = {}
    if dict_attr_ids:
        val_records = (OzonAttributeValue
                       .select()
                       .where((OzonAttributeValue.user == current_user) &
                              (OzonAttributeValue.attribute_id.in_(dict_attr_ids)))
                       .order_by(OzonAttributeValue.attribute_id, OzonAttributeValue.value_id))
        for v in val_records:
            values_map.setdefault(v.attribute_id, []).append({
                'value_id': v.value_id, 'value': v.value, 'info': v.info
            })

    attrs = [{
        'attribute_id': a.attribute_id,
        'name': a.name, 'name_cn': a.name_cn,
        'is_required': a.is_required, 'is_collection': a.is_collection,
        'is_dictionary': a.is_dictionary, 'data_type': a.data_type,
        'dictionary_id': a.dictionary_id,
        'description': a.description, 'group_name': a.group_name,
        'values': values_map.get(a.attribute_id, []),
    } for a in attr_list]

    return jsonify({'ok': True, 'attrs': attrs, 'total': len(attrs)})


@ozon_bp.route('/api/category/<cat_id>/sync-attributes', methods=['POST'])
@login_required
def api_sync_category_attributes(cat_id):
    """从 OZON API 拉取该类目+type_id 的属性字典"""
    account = (OzonAccount
               .select()
               .where((OzonAccount.user == current_user) &
                      (OzonAccount.is_active == True))
               .first())
    if not account:
        return jsonify({'ok': False, 'error': '未找到已启用的 OZON 店铺'}), 400

    type_id = request.args.get('type_id', '').strip()
    if not type_id:
        return jsonify({'ok': False, 'error': '缺少 type_id 参数。请先展开子类目选择 type_id。'}), 400

    # 从数据库查 type_id 的真实 description_category_id
    type_record = (OzonCategoryType
                   .select()
                   .where((OzonCategoryType.user == current_user) &
                          (OzonCategoryType.type_id == type_id))
                   .first())
    actual_cat_id = type_record.description_category_id if type_record else cat_id

    try:
        from services.ozon_api import create_client
        client = create_client(account)
        attrs = client.get_category_attributes(actual_cat_id, type_id=type_id, attribute_type='ALL')

        saved = 0
        required_count = 0
        dict_count = 0

        for attr in attrs:
            attr_id = str(attr.get('attribute_id', ''))
            if not attr_id:
                continue

            is_req = attr.get('is_required', False)
            dict_id = attr.get('dictionary_id')

            record, created = OzonCategoryAttribute.get_or_create(
                user=current_user,
                account=account,
                ozon_category_id=actual_cat_id,
                type_id=type_id,
                attribute_id=attr_id,
                defaults={
                    'name': attr.get('name', ''),
                    'description': attr.get('description', ''),
                    'is_required': is_req,
                    'is_collection': attr.get('is_collection', False),
                    'is_dictionary': bool(dict_id),
                    'dictionary_id': dict_id,
                    'data_type': attr.get('data_type', 'string'),
                    'group_name': attr.get('group_name', ''),
                    'max_value_count': attr.get('max_value_count', 1),
                    'raw_json': json.dumps(attr, ensure_ascii=False),
                    'last_synced_at': datetime.datetime.now(),
                    'source': 'api',
                    'schema_hash': hashlib.sha256(json.dumps(attr, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16],
                },
            )
            if not created:
                record.name = attr.get('name', record.name)
                record.description = attr.get('description', '')
                record.is_required = is_req
                record.is_collection = attr.get('is_collection', False)
                record.is_dictionary = bool(dict_id)
                record.dictionary_id = dict_id
                record.data_type = attr.get('data_type', 'string')
                record.group_name = attr.get('group_name', '')
                record.max_value_count = attr.get('max_value_count', 1)
                record.raw_json = json.dumps(attr, ensure_ascii=False)
                record.last_synced_at = datetime.datetime.now()
                record.source = 'api'
                record.save()
            saved += 1
            if is_req:
                required_count += 1
            if dict_id:
                dict_count += 1

        return jsonify({
            'ok': True,
            'message': f'已拉取 {saved} 个属性（{required_count} 必填，{dict_count} 有字典值）',
            'total': saved,
            'required_count': required_count,
            'dict_count': dict_count,
        })

    except Exception as e:
        return jsonify({'ok': False, 'error': f'拉取失败: {str(e)[:300]}'}), 500


# ── 类目树浏览 API ──

@ozon_bp.route('/api/category/root')
@login_required
def api_category_root():
    """获取一级类目（仅返回 OzonCategory，过滤历史误写入的 type 节点）"""
    cats = (OzonCategory
            .select()
            .where((OzonCategory.user == current_user) &
                   (OzonCategory.parent_id.is_null()))
            .order_by(OzonCategory.name))
    items = []
    for c in cats:
        if not c.ozon_category_id:
            continue
        # 过滤：raw_json 包含 type_id → 跳过（历史 type 节点误存为 category）
        if c.raw_json and '"type_id"' in c.raw_json:
            continue
        type_cnt = (OzonCategoryType
                    .select()
                    .where((OzonCategoryType.user == current_user) &
                           (OzonCategoryType.description_category_id == c.ozon_category_id))
                    .count())
        # 动态计算 has_children：查是否有真实子类目（排除 type 节点）
        real_child = (OzonCategory
                      .select()
                      .where((OzonCategory.user == current_user) &
                             (OzonCategory.parent_id == c.ozon_category_id) &
                             ~(OzonCategory.raw_json.contains('"type_id"')) &
                             (OzonCategory.ozon_category_id != ''))
                      .exists())
        items.append({
            'id': c.ozon_category_id, 'name': c.name, 'name_cn': c.name_cn,
            'has_children': real_child,
            'type_count': type_cnt,
        })

        # 若无子类目但有 type，在 items 中追加 type 节点供模态框选择
        if not real_child and type_cnt > 0:
            type_records = (OzonCategoryType
                           .select()
                           .where((OzonCategoryType.user == current_user) &
                                  (OzonCategoryType.description_category_id == c.ozon_category_id))
                           .order_by(OzonCategoryType.type_name))
            for t in type_records:
                items.append({
                    'id': t.type_id,
                    'name': t.type_name_cn or t.type_name,
                    'name_cn': t.type_name_cn,
                    'is_type': True,
                    'description_category_id': c.ozon_category_id,
                    'has_children': False,
                    'type_count': 0,
                })
    return jsonify({'ok': True, 'items': items})


def _collect_descendant_category_ids(parent_id, user):
    """递归收集 parent_id 下所有子孙类目的 ozon_category_id"""
    ids = {parent_id}
    children = (OzonCategory
                .select(OzonCategory.ozon_category_id)
                .where((OzonCategory.user == user) &
                       (OzonCategory.parent_id == parent_id)))
    for c in children:
        if c.ozon_category_id:
            ids.update(_collect_descendant_category_ids(c.ozon_category_id, user))
    return ids


@ozon_bp.route('/api/category/children')
@login_required
def api_category_children():
    """获取子类目（仅返回 OzonCategory，不返回 type 节点）"""
    parent_id = request.args.get('parent_id', '').strip()
    if not parent_id:
        return jsonify({'ok': False, 'error': '缺少 parent_id'}), 400

    children = (OzonCategory
                .select()
                .where((OzonCategory.user == current_user) &
                       (OzonCategory.parent_id == parent_id))
                .order_by(OzonCategory.name))

    items = []
    for c in children:
        if not c.ozon_category_id:
            continue
        # 过滤：raw_json 包含 type_id 的节点 → 跳过（历史 type 节点误存为 category）
        if c.raw_json and '"type_id"' in c.raw_json:
            continue

        type_cnt = (OzonCategoryType
                    .select()
                    .where((OzonCategoryType.user == current_user) &
                           (OzonCategoryType.description_category_id == c.ozon_category_id))
                    .count())
        # 动态计算 has_children：查是否有真实子类目（排除 type 节点）
        real_child = (OzonCategory
                      .select()
                      .where((OzonCategory.user == current_user) &
                             (OzonCategory.parent_id == c.ozon_category_id) &
                             ~(OzonCategory.raw_json.contains('"type_id"')) &
                             (OzonCategory.ozon_category_id != ''))
                      .exists())
        items.append({
            'id': c.ozon_category_id, 'name': c.name, 'name_cn': c.name_cn,
            'has_children': real_child or type_cnt > 0,
            'type_count': type_cnt,
        })

        # type 作为子节点始终追加到类目树中
        if type_cnt > 0:
            type_records = (OzonCategoryType
                           .select()
                           .where((OzonCategoryType.user == current_user) &
                                  (OzonCategoryType.description_category_id == c.ozon_category_id))
                           .order_by(OzonCategoryType.type_name))
            for t in type_records:
                items.append({
                    'id': t.type_id,
                    'name': t.type_name_cn or t.type_name,
                    'name_cn': t.type_name_cn,
                    'is_type': True,
                    'description_category_id': c.ozon_category_id,
                    'has_children': False,
                    'type_count': 0,
                })

    return jsonify({'ok': True, 'items': items})


@ozon_bp.route('/api/category/<cat_id>/types')
@login_required
def api_category_types(cat_id):
    """获取当前类目直接关联的 type 列表（供右侧面板使用）"""
    types = (OzonCategoryType
             .select()
             .where((OzonCategoryType.user == current_user) &
                    (OzonCategoryType.description_category_id == cat_id))
             .order_by(OzonCategoryType.type_name))

    items = [{
        'id': t.type_id,
        'name': t.type_name or f'type_{t.type_id}',
        'name_cn': t.type_name_cn or None,
        'description_category_id': t.description_category_id,
    } for t in types]

    return jsonify({'ok': True, 'items': items})


@ozon_bp.route('/api/category/cleanup-type-nodes', methods=['POST'])
@login_required
def api_cleanup_type_nodes():
    """清理历史误写入 OzonCategory 的 type 节点"""
    mode = request.args.get('mode', 'dry_run')   # dry_run / confirm

    # 统计
    type_polluted = (OzonCategory
                     .select()
                     .where((OzonCategory.user == current_user) &
                            (OzonCategory.raw_json.contains('"type_id"'))))

    polluted_count = type_polluted.count()
    samples = list(type_polluted.limit(5))

    # 额外诊断：ozon_category_id 命中了 type_id（不删除，仅统计）
    all_type_ids = set(
        t.type_id for t in
        OzonCategoryType.select(OzonCategoryType.type_id)
        .where(OzonCategoryType.user == current_user)
    )
    suspicious = (OzonCategory
                  .select()
                  .where((OzonCategory.user == current_user) &
                         (OzonCategory.ozon_category_id.in_(all_type_ids)) &
                         ~(OzonCategory.raw_json.contains('"type_id"'))))
    suspicious_count = suspicious.count()
    suspicious_samples = list(suspicious.limit(5))

    result = {
        'ok': True,
        'mode': mode,
        'polluted_count': polluted_count,
        'polluted_samples': [{
            'id': s.id, 'ozon_category_id': s.ozon_category_id,
            'name': s.name, 'parent_id': s.parent_id,
        } for s in samples],
        'suspicious_count': suspicious_count,
        'suspicious_samples': [{
            'id': s.id, 'ozon_category_id': s.ozon_category_id,
            'name': s.name, 'reason': 'ozon_category_id matches a type_id but raw_json clean',
        } for s in suspicious_samples],
    }

    if mode == 'confirm':
        deleted = type_polluted.delete().execute()
        result['deleted'] = deleted
        result['message'] = f'已删除 {deleted} 条 type 节点'
    else:
        result['message'] = f'发现 {polluted_count} 条 type 节点（raw_json 含 type_id）。使用 mode=confirm 确认删除。'

    return jsonify(result)


@ozon_bp.route('/api/category/search')
@login_required
def api_category_search():
    """搜索类目"""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'ok': False, 'error': '至少输入2个字符'}), 400

    cats = (OzonCategory
            .select()
            .where((OzonCategory.user == current_user) &
                   ((OzonCategory.name.contains(q)) |
                    (OzonCategory.name_cn.contains(q)) |
                    (OzonCategory.ozon_category_id.contains(q))))
            .limit(50))

    items = [{'id': c.ozon_category_id, 'name': c.name, 'name_cn': c.name_cn,
              'has_children': not c.is_leaf, 'path': c.path}
             for c in cats if c.ozon_category_id]

    return jsonify({'ok': True, 'items': items})


@ozon_bp.route('/api/draft/<int:draft_id>/set-category-type', methods=['POST'])
@login_required
def api_draft_set_category_type(draft_id):
    """为草稿绑定类目+type"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft:
        return jsonify({'ok': False, 'error': '草稿不存在'}), 404

    data = request.get_json(silent=True) or {}
    dcid = data.get('description_category_id', '').strip()
    tid = data.get('type_id', '').strip()
    if not dcid or not tid:
        return jsonify({'ok': False, 'error': '必须提供 description_category_id 和 type_id'}), 400

    # 查类目路径
    cat = OzonCategory.get_or_none((OzonCategory.user == current_user) &
                                   (OzonCategory.ozon_category_id == dcid))
    path_ru = cat.path if cat else dcid
    path_cn = cat.name_cn or cat.name if cat else dcid

    # 查 type 名
    t = OzonCategoryType.get_or_none((OzonCategoryType.user == current_user) &
                                      (OzonCategoryType.description_category_id == dcid) &
                                      (OzonCategoryType.type_id == tid))
    type_name_ru = t.type_name if t else f'type_{tid}'

    draft.ozon_category_id = dcid
    draft.type_id = tid
    draft.category_path_ru = path_ru
    draft.category_path_cn = data.get('category_path_cn', '') or path_cn
    draft.type_name_ru = type_name_ru
    draft.type_name_cn = data.get('type_name_cn', '') or ''
    draft.updated_at = datetime.datetime.now()
    draft.save()

    return jsonify({
        'ok': True,
        'message': f'已绑定类目 {dcid} / type {tid}',
        'description_category_id': dcid,
        'type_id': tid,
        'category_path_ru': path_ru,
        'type_name_ru': type_name_ru,
    })


@ozon_bp.route('/api/category/<cat_id>/sync-types', methods=['POST'])
@login_required
def api_sync_category_types(cat_id):
    """拉取该类目下的所有 type_id"""
    account = (OzonAccount
               .select()
               .where((OzonAccount.user == current_user) &
                      (OzonAccount.is_active == True))
               .first())
    if not account:
        return jsonify({'ok': False, 'error': '未找到店铺'}), 400

    try:
        from services.ozon_api import create_client
        client = create_client(account)

        result = client.get_category_types_for_node(cat_id)

        # 1. 有子类目但无直接 type → 提示继续展开
        if result['direct_count'] == 0 and result['has_children']:
            return jsonify({
                'ok': True,
                'status': 'has_children',
                'message': '当前类目还有子类目，请继续展开到更细类目。'
                           f'子树中共有 {result["total_in_tree"]} 个 type，但不会在当前节点同步。',
                'total_in_tree': result['total_in_tree'],
            })

        # 2. 无直接 type 且无子类目 → 空
        if result['direct_count'] == 0:
            return jsonify({
                'ok': True,
                'status': 'empty',
                'message': '当前类目没有可同步的 type，请换一个类目。',
                'total': 0,
            })

        # 3. 直接 type 过多 → 提示缩小范围
        if result['category_too_broad']:
            return jsonify({
                'ok': True,
                'status': 'too_broad',
                'message': f'当前类目直接 type 过多（{result["direct_count"]} 个），请继续缩小范围。',
                'type_count': result['direct_count'],
            })

        # 4. 可以同步
        direct_types = result['types']
        saved = 0
        for t in direct_types:
            tid = t['type_id']
            if not tid:
                continue
            actual_cat_id = t.get('description_category_id') or cat_id
            record, created = OzonCategoryType.get_or_create(
                user=current_user,
                account=account,
                description_category_id=actual_cat_id,
                type_id=tid,
                defaults={
                    'type_name': t.get('type_name', ''),
                    'raw_json': json.dumps(t, ensure_ascii=False),
                    'last_synced_at': datetime.datetime.now(),
                },
            )
            if not created:
                record.type_name = t.get('type_name', record.type_name)
                record.description_category_id = actual_cat_id
                record.last_synced_at = datetime.datetime.now()
                record.save()
            saved += 1

        # 翻译 type 名称
        need_trans = list(OzonCategoryType.select().where(
            (OzonCategoryType.user == current_user) &
            (OzonCategoryType.description_category_id == cat_id)
        ))
        need_trans = [t for t in need_trans if not t.type_name_cn or t.type_name_cn == t.type_name]
        if need_trans:
            try:
                names_to_trans = [t.type_name for t in need_trans]
                translated = _batch_translate(names_to_trans, current_user)
                for t in need_trans:
                    cn = translated.get(t.type_name)
                    if cn and cn != t.type_name:
                        t.type_name_cn = cn
                        t.save()
            except Exception:
                pass

        return jsonify({
            'ok': True,
            'status': 'done',
            'message': f'已同步 {saved} 个 type，选择 type 后提取属性',
            'total': saved,
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@ozon_bp.route('/api/category/sync-all', methods=['POST'])
@login_required
def api_sync_all():
    """全局同步：仅支持类目树。type/属性/字典值必须按选中类目操作。"""
    import time as time_mod

    phase = request.args.get('phase', 'tree')
    if phase in ('types', 'attrs', 'all'):
        return jsonify({
            'ok': False,
            'error': '不支持全平台全量同步 type/属性/字典值。请选择具体类目后使用对应按钮。',
            'suggestion': '展开左侧类目树 → 选择叶子类目 → 同步当前类目 type → 选择 type → 同步属性和字典值',
        }), 400
    # phase=tree: 只同步类目树
    account = (OzonAccount
               .select()
               .where((OzonAccount.user == current_user) &
                      (OzonAccount.is_active == True))
               .first())
    if not account:
        return jsonify({'ok': False, 'error': '未找到已启用的 OZON 店铺'}), 400

    try:
        from services.ozon_api import create_client
        client = create_client(account)
        tree = client.get_category_tree()

        def save_categories(cats, parent_id=None, path=''):
            count = 0
            for cat in cats:
                # 规则：含 type_id 的节点禁止写入 OzonCategory
                if cat.get('type_id'):
                    if cat.get('children'):
                        count += save_categories(cat['children'], parent_id, path)
                    continue

                cat_id = str(cat.get('category_id', ''))
                title = cat.get('title', '')
                current_path = (path + ' > ' + title).strip(' > ')
                record, created = OzonCategory.get_or_create(
                    user=current_user,
                    ozon_category_id=cat_id,
                    defaults={
                        'name': title,
                        'path': current_path,
                        'parent_id': parent_id,
                        'is_leaf': not cat.get('children'),
                        'source': 'api',
                        'raw_json': json.dumps(cat, ensure_ascii=False),
                        'last_synced_at': datetime.datetime.now(),
                    },
                )
                if not created:
                    record.name = title
                    record.path = current_path
                    record.parent_id = parent_id
                    record.is_leaf = not cat.get('children')
                    record.last_synced_at = datetime.datetime.now()
                    record.save()
                count += 1
                if cat.get('children'):
                    count += save_categories(cat['children'], cat_id, current_path)
            return count

        total = save_categories(tree)

        # 记录同步任务
        OzonCategorySyncJob.create(
            user=current_user, account=account,
            job_type='tree', status='done',
            success_count=total, total_count=total,
            message=f'已同步 {total} 个类目',
            started_at=datetime.datetime.now(),
            finished_at=datetime.datetime.now(),
        )

        return jsonify({
            'ok': True,
            'message': f'已同步 {total} 个类目。请展开左侧树选择具体类目后同步 type。',
            'tree_synced': total,
        })

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@ozon_bp.route('/api/category/<cat_id>/batch-sync-current-category', methods=['POST'])
@login_required
def api_batch_sync_current_category(cat_id):
    """当前类目受控批量同步：只处理当前类目直接关联的 type，每批最多 20 个"""
    import time

    account = (OzonAccount
               .select()
               .where((OzonAccount.user == current_user) &
                      (OzonAccount.is_active == True))
               .first())
    if not account:
        return jsonify({'ok': False, 'error': '未找到店铺'}), 400

    from services.ozon_api import create_client
    client = create_client(account)

    # 先估算 type 数量
    est = client.estimate_type_count(cat_id)
    if est['too_broad'] or est['direct_count'] > 100:
        return jsonify({
            'ok': True,
            'status': 'too_broad',
            'message': f"该类目包含 {est['direct_count']} 个直接 type（合计 {est['total_count']}），范围过大，请继续选择子类目",
            'type_count': est['direct_count'],
            'total_count': est['total_count'],
        })

    # 获取直接 type
    result = client.get_category_types_for_node(cat_id)
    direct_types = result['types'][:20]  # 最多 20 个

    if not direct_types:
        return jsonify({'ok': False, 'error': '该类目下没有直接 type，请展开子类目'}), 400

    # 创建 Job
    job = OzonCategorySyncJob.create(
        user=current_user, account=account,
        job_type='category_batch',
        target_category_id=cat_id,
        status='running',
        total_count=len(direct_types),
        message=f'开始批量同步 {len(direct_types)} 个 type',
        started_at=datetime.datetime.now(),
    )

    try:
        attrs_total = 0
        values_total = 0
        warnings = []
        errors_list = []

        for idx, t in enumerate(direct_types):
            tid = t['type_id']
            actual_cat_id = t.get('description_category_id') or cat_id

            try:
                # 提取属性
                attrs = client.get_category_attributes(actual_cat_id, type_id=tid, attribute_type='ALL')
                dict_attr_ids = []

                for attr in attrs:
                    attr_id = str(attr.get('attribute_id', ''))
                    if not attr_id:
                        continue
                    dict_id = attr.get('dictionary_id')
                    record, created = OzonCategoryAttribute.get_or_create(
                        user=current_user, account=account,
                        ozon_category_id=actual_cat_id, type_id=tid,
                        attribute_id=attr_id,
                        defaults={
                            'name': attr.get('name', ''),
                            'description': attr.get('description', ''),
                            'is_required': bool(attr.get('is_required')),
                            'is_collection': bool(attr.get('is_collection')),
                            'is_dictionary': bool(dict_id),
                            'dictionary_id': dict_id,
                            'data_type': str(attr.get('data_type', 'string')),
                            'group_name': str(attr.get('group_name', '')),
                            'max_value_count': attr.get('max_value_count', 1),
                            'raw_json': json.dumps(attr, ensure_ascii=False),
                            'last_synced_at': datetime.datetime.now(),
                            'source': 'api',
                        },
                    )
                    if not created:
                        record.name = attr.get('name', record.name)
                        record.is_dictionary = bool(dict_id)
                        record.dictionary_id = dict_id
                        record.last_synced_at = datetime.datetime.now()
                        record.save()
                    if dict_id:
                        dict_attr_ids.append((attr_id, attr.get('name', '')))
                    attrs_total += 1

                # 拉取字典值
                for da_id, da_name in dict_attr_ids:
                    try:
                        values = client.get_attribute_values(actual_cat_id, type_id=tid, attribute_id=da_id)
                        if len(values) > 200:
                            values = values[:200]
                            warnings.append(f'{tid}/{da_name}: 字典值 {len(values)} 条（已限制前200）')
                        for v in values:
                            vid = str(v.get('id', ''))
                            if not vid:
                                continue
                            OzonAttributeValue.get_or_create(
                                user=current_user, account=account,
                                type_id=tid, attribute_id=da_id, value_id=vid,
                                defaults={
                                    'value': v.get('value', ''),
                                    'info': v.get('info', '') or None,
                                    'last_synced_at': datetime.datetime.now(),
                                },
                            )
                            values_total += 1
                    except Exception as e:
                        warnings.append(f'{tid}/{da_name}: {str(e)[:60]}')

                job.processed_count = idx + 1
                job.success_count = idx + 1
                job.save()

                if idx < len(direct_types) - 1:
                    time.sleep(0.6)

            except Exception as e:
                errors_list.append(f'type={tid}: {str(e)[:100]}')
                job.error_count += 1
                job.save()

        # 更新 Job
        job.status = 'partial' if errors_list else 'done'
        job.skipped_count = len(warnings)
        job.warnings_json = json.dumps(warnings, ensure_ascii=False)
        job.errors_json = json.dumps(errors_list, ensure_ascii=False)
        job.message = f'完成: {job.success_count}/{job.total_count} type, {attrs_total} 属性, {values_total} 字典值'
        job.finished_at = datetime.datetime.now()
        job.save()

        return jsonify({
            'ok': True,
            'job_id': job.id,
            'message': job.message,
            'types_processed': job.success_count,
            'attrs_total': attrs_total,
            'values_total': values_total,
            'warnings': warnings[:10],
            'errors': errors_list[:10],
        })

    except Exception as e:
        job.status = 'failed'
        job.message = str(e)[:500]
        job.finished_at = datetime.datetime.now()
        job.save()
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


# ═══════════════════════════════════════════════════════
# 新增接口：当前 type 一键同步 / 常用 type / 收藏 / 同步记录
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/api/category/<dcid>/sync-current-type-full', methods=['POST'])
@login_required
def api_sync_current_type_full(dcid):
    """当前 type 一键同步：属性 + 字典值"""
    import time
    type_id = request.args.get('type_id', '').strip()
    if not type_id:
        return jsonify({'ok': False, 'error': '必须提供 type_id'}), 400

    account = (OzonAccount
               .select()
               .where((OzonAccount.user == current_user) &
                      (OzonAccount.is_active == True))
               .first())
    if not account:
        return jsonify({'ok': False, 'error': '未找到店铺'}), 400

    start_t = time.time()

    try:
        from services.ozon_api import create_client
        client = create_client(account)

        job = OzonCategorySyncJob.create(
            user=current_user, account=account,
            job_type='current_type',
            target_category_id=dcid, target_type_id=type_id,
            status='running',
            message=f'同步 type {type_id}',
            started_at=datetime.datetime.now(),
        )

        # 拉取属性
        attrs = client.get_category_attributes(dcid, type_id=type_id, attribute_type='ALL')
        if not attrs:
            job.status = 'done'
            job.message = '该 type 无属性'
            job.finished_at = datetime.datetime.now()
            job.save()
            return jsonify({'ok': True, 'message': '该 type 无属性', 'attributes_synced': 0})

        required_count = 0
        dict_attrs = []
        skipped = []
        warnings = []

        for attr in attrs:
            attr_id = str(attr.get('attribute_id', ''))
            if not attr_id:
                continue
            is_req = bool(attr.get('is_required'))
            dict_id = attr.get('dictionary_id')

            record, created = OzonCategoryAttribute.get_or_create(
                user=current_user, account=account,
                ozon_category_id=dcid, type_id=type_id,
                attribute_id=attr_id,
                defaults={
                    'name': attr.get('name', ''),
                    'is_required': is_req,
                    'is_dictionary': bool(dict_id),
                    'is_collection': bool(attr.get('is_collection')),
                    'dictionary_id': dict_id,
                    'data_type': str(attr.get('data_type', 'string')),
                    'group_name': str(attr.get('group_name', '')),
                    'max_value_count': attr.get('max_value_count', 1),
                    'raw_json': json.dumps(attr, ensure_ascii=False),
                    'last_synced_at': datetime.datetime.now(),
                    'source': 'api',
                },
            )
            if not created:
                record.is_required = is_req
                record.is_dictionary = bool(dict_id)
                record.last_synced_at = datetime.datetime.now()
                record.save()

            if is_req:
                required_count += 1
            if dict_id:
                dict_attrs.append((attr_id, attr.get('name', '')))

        # 翻译属性名
        try:
            saved_attrs = list(OzonCategoryAttribute.select().where(
                (OzonCategoryAttribute.user == current_user) &
                (OzonCategoryAttribute.ozon_category_id == dcid) &
                (OzonCategoryAttribute.type_id == type_id)
            ))
            need_trans_attrs = [a for a in saved_attrs if not a.name_cn or a.name_cn == a.name]
            if need_trans_attrs:
                names_to_trans = [a.name for a in need_trans_attrs]
                trans_result = _batch_translate(names_to_trans, current_user)
                for a in need_trans_attrs:
                    cn = trans_result.get(a.name)
                    if cn and cn != a.name:
                        a.name_cn = cn
                        a.save()
        except Exception:
            pass

        # 拉取字典值
        values_synced = 0
        for da_id, da_name in dict_attrs:
            try:
                vals = client.get_attribute_values(dcid, type_id=type_id, attribute_id=da_id)
                if len(vals) > 200:
                    vals = vals[:200]  # 软限制：取前 200 条
                    warnings.append(f'{da_name}: 字典值共 {len(vals) if len(vals)==200 else 0} 条（已限制前200）')
                for v in vals:
                    vid = str(v.get('id', ''))
                    if not vid:
                        continue
                    OzonAttributeValue.get_or_create(
                        user=current_user, account=account,
                        type_id=type_id, attribute_id=da_id, value_id=vid,
                        defaults={
                            'value': v.get('value', ''),
                            'info': v.get('info', '') or None,
                            'last_synced_at': datetime.datetime.now(),
                        },
                    )
                    values_synced += 1
            except Exception as e:
                warnings.append(f'{da_name}: {str(e)[:60]}')

        elapsed = round(time.time() - start_t, 1)

        job.status = 'partial' if (warnings or skipped) else 'done'
        job.success_count = len(attrs)
        job.skipped_count = len(skipped)
        job.warnings_json = json.dumps(warnings, ensure_ascii=False)
        job.message = f'属性 {len(attrs)} 个, 字典值 {values_synced} 条'
        job.finished_at = datetime.datetime.now()
        job.save()

        return jsonify({
            'ok': True,
            'attributes_synced': len(attrs),
            'required_count': required_count,
            'dictionary_attrs': len(dict_attrs),
            'dictionary_values_synced': values_synced,
            'skipped_dictionary_attrs': skipped,
            'warnings': warnings,
            'elapsed': elapsed,
            'job_id': job.id,
        })

    except Exception as e:
        import traceback
        print(f'[SYNC-TYPE-ERROR] type_id={type_id} dcid={dcid}: {e}')
        traceback.print_exc()
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@ozon_bp.route('/api/category/sync-used-types', methods=['POST'])
@login_required
def api_sync_used_types():
    """同步我的常用 type（草稿用过 + 在线商品 + 收藏的）"""
    import time

    account = (OzonAccount
               .select()
               .where((OzonAccount.user == current_user) &
                      (OzonAccount.is_active == True))
               .first())
    if not account:
        return jsonify({'ok': False, 'error': '未找到店铺'}), 400

    # 收集常用 type
    used_types = set()

    # 来源1：草稿使用过的
    drafts = (OzonDraft
              .select(OzonDraft.type_id, OzonDraft.ozon_category_id)
              .where((OzonDraft.user == current_user) &
                     (OzonDraft.type_id.is_null(False))))
    for d in drafts:
        if d.type_id and d.ozon_category_id:
            used_types.add((d.ozon_category_id, d.type_id))

    # 来源2：收藏的
    favs = (OzonFavoriteCategoryType
            .select()
            .where(OzonFavoriteCategoryType.user == current_user))
    for f in favs:
        if f.type_id and f.description_category_id:
            used_types.add((f.description_category_id, f.type_id))

    # 来源3：已同步的在线商品
    try:
        online_products = (OzonOnlineProduct
                  .select(OzonOnlineProduct.type_id, OzonOnlineProduct.ozon_category_id)
                  .where((OzonOnlineProduct.user == current_user) &
                         (OzonOnlineProduct.type_id.is_null(False))))
        for o in online_products:
            if o.type_id and o.ozon_category_id:
                used_types.add((o.ozon_category_id, o.type_id))
    except Exception:
        pass  # 如果在线商品表不存在则跳过

    if not used_types:
        return jsonify({'ok': True, 'message': '无常用 type（请先创建草稿或收藏 type）', 'types_processed': 0})

    from services.ozon_api import create_client
    client = create_client(account)

    job = OzonCategorySyncJob.create(
        user=current_user, account=account,
        job_type='used_types',
        status='running',
        total_count=len(used_types),
        message=f'同步 {len(used_types)} 个常用 type',
        started_at=datetime.datetime.now(),
    )

    try:
        attrs_total = 0
        values_total = 0
        processed = 0
        errors_list = []

        for dcid, tid in list(used_types)[:20]:
            try:
                attrs = client.get_category_attributes(dcid, type_id=tid, attribute_type='ALL')
                dict_attr_ids = [str(a.get('attribute_id', '')) for a in attrs
                                 if a.get('dictionary_id') and str(a.get('attribute_id', ''))]

                for attr in attrs:
                    attr_id = str(attr.get('attribute_id', ''))
                    if not attr_id:
                        continue
                    OzonCategoryAttribute.get_or_create(
                        user=current_user, account=account,
                        ozon_category_id=dcid, type_id=tid,
                        attribute_id=attr_id,
                        defaults={
                            'name': attr.get('name', ''),
                            'is_required': bool(attr.get('is_required')),
                            'is_dictionary': bool(attr.get('dictionary_id')),
                            'dictionary_id': attr.get('dictionary_id'),
                            'last_synced_at': datetime.datetime.now(),
                            'source': 'api',
                        },
                    )
                    attrs_total += 1

                for da_id in dict_attr_ids:
                    try:
                        vals = client.get_attribute_values(dcid, type_id=tid, attribute_id=da_id)
                        if len(vals) > 200:
                            vals = vals[:200]
                        for v in vals:
                            vid = str(v.get('id', ''))
                            if not vid:
                                continue
                            OzonAttributeValue.get_or_create(
                                user=current_user, account=account,
                                type_id=tid, attribute_id=da_id, value_id=vid,
                                defaults={
                                    'value': v.get('value', ''),
                                    'info': v.get('info', '') or None,
                                    'last_synced_at': datetime.datetime.now(),
                                },
                            )
                            values_total += 1
                    except Exception:
                        pass

                processed += 1
                if processed < len(list(used_types)[:20]):
                    time.sleep(0.5)

            except Exception as e:
                errors_list.append(f'{dcid}/{tid}: {str(e)[:60]}')

        job.status = 'partial' if errors_list else 'done'
        job.success_count = processed
        job.errors_json = json.dumps(errors_list, ensure_ascii=False)
        job.message = f'已处理 {processed} 个 type, {attrs_total} 属性, {values_total} 字典值'
        job.finished_at = datetime.datetime.now()
        job.save()

        return jsonify({
            'ok': True,
            'types_processed': processed,
            'attrs_total': attrs_total,
            'values_total': values_total,
            'errors': errors_list[:10],
            'job_id': job.id,
        })

    except Exception as e:
        job.status = 'failed'
        job.message = str(e)[:500]
        job.finished_at = datetime.datetime.now()
        job.save()
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@ozon_bp.route('/api/category/sync-all-type-attributes', methods=['POST'])
@login_required
def api_sync_all_type_attributes():
    """批量同步全部 type 的属性 Schema（偏移量分页，每次 5 个）"""
    import time as time_mod
    batch_size = int(request.args.get('batch', 20))
    offset = int(request.args.get('offset', 0))

    account = (OzonAccount
               .select()
               .where((OzonAccount.user == current_user) &
                      (OzonAccount.is_active == True))
               .first())
    if not account:
        return jsonify({'ok': False, 'error': '未找到已启用的 OZON 店铺'}), 400

    # 按 type_id 排序，用 offset 分页（不依赖去重检测）
    all_types = list(OzonCategoryType
                     .select()
                     .where(OzonCategoryType.user == current_user)
                     .order_by(OzonCategoryType.type_id))

    total = len(all_types)
    batch = all_types[offset:offset + batch_size]
    next_offset = offset + len(batch)
    remaining = total - next_offset

    if not batch:
        return jsonify({'ok': True, 'message': '全部 type 属性已同步',
                        'synced': 0, 'remaining': 0, 'next_offset': next_offset, 'total': total})

    try:
        from services.ozon_api import create_client
        client = create_client(account)
        synced_count = 0
        attr_total = 0
        skipped = 0
        errors = []

        for t in batch:
            try:
                # 跳过已有属性的 type
                existing = (OzonCategoryAttribute
                            .select()
                            .where((OzonCategoryAttribute.user == current_user) &
                                   (OzonCategoryAttribute.type_id == t.type_id))
                            .count())
                if existing > 0:
                    skipped += 1
                    continue

                attrs = client.get_category_attributes(
                    t.description_category_id, type_id=t.type_id, attribute_type='ALL')

                if not attrs:
                    # 无属性也标记，避免下次重复调 API
                    OzonCategoryAttribute.get_or_create(
                        user=current_user, account=account,
                        ozon_category_id=t.description_category_id,
                        type_id=t.type_id, attribute_id='_no_attrs',
                        defaults={'name': '(无属性)', 'is_required': False,
                                  'is_dictionary': False, 'is_collection': False,
                                  'data_type': '', 'description': ''})
                    synced_count += 1
                    time_mod.sleep(0.1)
                    continue

                for attr in attrs:
                    attr_id = str(attr.get('attribute_id', ''))
                    if not attr_id:
                        continue
                    dict_id = attr.get('dictionary_id')
                    OzonCategoryAttribute.get_or_create(
                        user=current_user, account=account,
                        ozon_category_id=t.description_category_id,
                        type_id=t.type_id, attribute_id=attr_id,
                        defaults={
                            'name': attr.get('name', ''),
                            'is_required': bool(attr.get('is_required')),
                            'is_dictionary': bool(dict_id),
                            'is_collection': bool(attr.get('is_collection')),
                            'data_type': attr.get('data_type', ''),
                            'description': attr.get('description', ''),
                            'dictionary_id': int(dict_id) if dict_id else None,
                            'group_name': attr.get('group_name', ''),
                            'max_value_count': attr.get('max_value_count', 0),
                        })
                    attr_total += 1
                synced_count += 1
                time_mod.sleep(0.1)
            except Exception as e:
                err_str = str(e)
                if 'UNIQUE constraint' in err_str:
                    skipped += 1  # 已存在，静默跳过
                else:
                    errors.append(f'{t.type_name_cn or t.type_name}: {err_str[:100]}')

        msg = f'第{offset//batch_size+1}批：同步 {synced_count} 个，跳过 {skipped} 个，属性 {attr_total} 个'
        if remaining > 0:
            msg += f'。剩余 {remaining} 个。'

        return jsonify({
            'ok': True, 'message': msg,
            'synced': synced_count, 'attrs': attr_total, 'skipped': skipped,
            'remaining': remaining, 'next_offset': next_offset, 'total': total,
            'errors': errors[:5],
        })

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@ozon_bp.route('/api/category/type/<type_id>/favorite', methods=['POST'])
@login_required
def api_favorite_type(type_id):
    """收藏/取消收藏常用 type"""
    data = request.get_json(silent=True) or {}
    dcid = data.get('description_category_id', '').strip()
    action = data.get('action', 'toggle')

    if not dcid:
        return jsonify({'ok': False, 'error': '必须提供 description_category_id'}), 400

    existing = OzonFavoriteCategoryType.get_or_none(
        (OzonFavoriteCategoryType.user == current_user) &
        (OzonFavoriteCategoryType.description_category_id == dcid) &
        (OzonFavoriteCategoryType.type_id == type_id)
    )

    if action == 'remove' or existing:
        if existing:
            existing.delete_instance()
        return jsonify({'ok': True, 'favorited': False, 'message': '已取消收藏'})

    OzonFavoriteCategoryType.create(
        user=current_user,
        description_category_id=dcid,
        type_id=type_id,
        type_name=data.get('type_name', ''),
        path=data.get('path', ''),
    )
    return jsonify({'ok': True, 'favorited': True, 'message': '已收藏'})


@ozon_bp.route('/api/category/sync-jobs', methods=['GET'])
@login_required
def api_sync_jobs():
    """获取最近同步记录"""
    jobs = (OzonCategorySyncJob
            .select()
            .where(OzonCategorySyncJob.user == current_user)
            .order_by(OzonCategorySyncJob.created_at.desc())
            .limit(10))
    return jsonify({
        'ok': True,
        'jobs': [{
            'id': j.id,
            'job_type': j.job_type,
            'status': j.status,
            'message': j.message,
            'success_count': j.success_count,
            'error_count': j.error_count,
            'started_at': str(j.started_at) if j.started_at else None,
            'finished_at': str(j.finished_at) if j.finished_at else None,
        } for j in jobs],
    })


@ozon_bp.route('/api/category/<cat_id>/sync-attribute-values', methods=['POST'])
@login_required
def api_sync_attribute_values(cat_id):
    """拉取类目+type_id 下所有字典类属性的字典值"""
    account = (OzonAccount
               .select()
               .where((OzonAccount.user == current_user) &
                      (OzonAccount.is_active == True))
               .first())
    if not account:
        return jsonify({'ok': False, 'error': '未找到已启用的 OZON 店铺'}), 400

    type_id = request.args.get('type_id', '').strip()
    if not type_id:
        return jsonify({'ok': False, 'error': '缺少 type_id 参数'}), 400

    # 查 type 的真实 cat_id
    type_record = (OzonCategoryType
                   .select()
                   .where((OzonCategoryType.user == current_user) &
                          (OzonCategoryType.type_id == type_id))
                   .first())
    actual_cat_id = type_record.description_category_id if type_record else cat_id

    # 找到该类目下所有有 dictionary_id 的属性
    dict_attrs = list(OzonCategoryAttribute
                     .select()
                     .where((OzonCategoryAttribute.user == current_user) &
                            (OzonCategoryAttribute.ozon_category_id == actual_cat_id) &
                            (OzonCategoryAttribute.type_id == type_id) &
                            (OzonCategoryAttribute.is_dictionary == True)))

    if not dict_attrs:
        return jsonify({'ok': True, 'message': '该类目没有字典类型属性', 'count': 0})

    try:
        from services.ozon_api import create_client
        client = create_client(account)
        total_values = 0
        attr_results = []

        for attr in dict_attrs:
            if not attr.dictionary_id:
                continue
            try:
                values = client.get_attribute_values(cat_id, type_id, attr.attribute_id)
                saved = 0
                for v in values:
                    vid = str(v.get('id', ''))
                    if not vid:
                        continue
                    _, created = OzonAttributeValue.get_or_create(
                        user=current_user,
                        account=account,
                        attribute_id=attr.attribute_id,
                        value_id=vid,
                        defaults={
                            'value': v.get('value', ''),
                            'info': v.get('info', '') or None,
                            'last_synced_at': datetime.datetime.now(),
                        },
                    )
                    if not created:
                        rec = OzonAttributeValue.get(
                            (OzonAttributeValue.user == current_user) &
                            (OzonAttributeValue.attribute_id == attr.attribute_id) &
                            (OzonAttributeValue.value_id == vid))
                        rec.value = v.get('value', '')
                        rec.info = v.get('info', '') or None
                        rec.last_synced_at = datetime.datetime.now()
                        rec.save()
                    saved += 1
                attr_results.append(f'{attr.name_cn or attr.name}: {saved} 个值')
                total_values += saved
            except Exception as e:
                attr_results.append(f'{attr.name_cn or attr.name}: 拉取失败 ({str(e)[:50]})')

        return jsonify({
            'ok': True,
            'message': f'已拉取 {len(dict_attrs)} 个属性的字典值，共 {total_values} 条',
            'details': attr_results,
            'total': total_values,
        })

    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)[:300]}), 500


@ozon_bp.route('/api/category/<cat_id>/expand-tree', methods=['POST'])
@login_required
def api_expand_category_tree(cat_id):
    """展开指定类目的子类目树，只写 OzonCategory + OzonCategoryType，不混写"""
    account = (OzonAccount
               .select()
               .where((OzonAccount.user == current_user) &
                      (OzonAccount.is_active == True))
               .first())
    if not account:
        return jsonify({'ok': False, 'error': '未找到已启用的 OZON 店铺'}), 400

    try:
        from services.ozon_api import create_client
        client = create_client(account)
        tree = client.get_category_tree_with_subtree(cat_id)

        cats_saved = 0
        types_saved = 0

        def walk_tree(nodes, parent_id=None, path=''):
            nonlocal cats_saved, types_saved
            total = 0
            for cat in nodes:
                cat_id_val = str(cat.get('category_id', '') or cat.get('description_category_id', ''))
                title = cat.get('title', '')
                type_id = cat.get('type_id')
                type_name = cat.get('type_name', '')
                current_path = (path + ' > ' + title).strip(' > ')

                if type_id:
                    # type 节点 → 只写 OzonCategoryType，禁止写 OzonCategory
                    tid = str(type_id)
                    actual_dcid = cat_id_val or parent_id or cat_id
                    record, _ = OzonCategoryType.get_or_create(
                        user=current_user,
                        account=account,
                        description_category_id=actual_dcid,
                        type_id=tid,
                        defaults={
                            'type_name': type_name,
                            'raw_json': json.dumps(cat, ensure_ascii=False),
                            'last_synced_at': datetime.datetime.now(),
                        },
                    )
                    if record:
                        record.type_name = type_name
                        record.description_category_id = actual_dcid
                        record.last_synced_at = datetime.datetime.now()
                        record.save()
                    types_saved += 1
                    # 继续递归 type 的 children（可能还有更深层）
                    if cat.get('children'):
                        total += walk_tree(cat['children'], parent_id or cat_id, current_path)
                elif cat_id_val:
                    # 纯类目节点 → 写 OzonCategory
                    record, _ = OzonCategory.get_or_create(
                        user=current_user,
                        ozon_category_id=cat_id_val,
                        defaults={
                            'name': title,
                            'path': current_path,
                            'parent_id': parent_id,
                            'is_leaf': not cat.get('children'),
                            'source': 'api',
                            'raw_json': json.dumps(cat, ensure_ascii=False),
                            'last_synced_at': datetime.datetime.now(),
                        },
                    )
                    if record:
                        record.name = title
                        record.path = current_path
                        record.parent_id = parent_id
                        record.is_leaf = not cat.get('children')
                        record.raw_json = json.dumps(cat, ensure_ascii=False)
                        record.last_synced_at = datetime.datetime.now()
                        record.save()
                    cats_saved += 1
                    total += 1
                    if cat.get('children'):
                        total += walk_tree(cat['children'], cat_id_val, current_path)
                else:
                    # 没有 ID → 继续递归 children
                    if cat.get('children'):
                        total += walk_tree(cat['children'], parent_id, current_path)
            return total

        walk_tree(tree, parent_id=cat_id)

        return jsonify({
            'ok': True,
            'message': f'已展开 {cats_saved} 个子类目，{types_saved} 个 type',
            'total': cats_saved,
            'types_saved': types_saved,
        })

    except Exception as e:
        return jsonify({'ok': False, 'error': f'展开失败: {str(e)[:300]}'}), 500


@ozon_bp.route('/api/category/<cat_id>/add-attribute', methods=['POST'])
@login_required
def api_add_category_attribute(cat_id):
    """手工添加类目属性"""
    data = request.get_json(silent=True) or {}
    attr_id = str(data.get('attribute_id', '')).strip()
    name = data.get('name', '').strip()
    if not attr_id or not name:
        return jsonify({'ok': False, 'error': '属性ID和属性名必填'}), 400

    # 检查是否已存在
    existing = OzonCategoryAttribute.get_or_none(
        (OzonCategoryAttribute.user == current_user) &
        (OzonCategoryAttribute.ozon_category_id == cat_id) &
        (OzonCategoryAttribute.attribute_id == attr_id)
    )
    if existing:
        # 更新
        existing.name = name
        existing.name_cn = data.get('name_cn', '').strip() or existing.name_cn
        existing.data_type = data.get('data_type', 'string')
        existing.is_required = bool(data.get('is_required', False))
        existing.is_dictionary = bool(data.get('is_dictionary', False))
        existing.save()
        return jsonify({'ok': True, 'message': '属性已更新', 'updated': True})

    OzonCategoryAttribute.create(
        user=current_user,
        ozon_category_id=cat_id,
        attribute_id=attr_id,
        name=name,
        name_cn=data.get('name_cn', '').strip() or None,
        data_type=data.get('data_type', 'string'),
        is_required=bool(data.get('is_required', False)),
        is_dictionary=bool(data.get('is_dictionary', False)),
        source='manual',
    )
    return jsonify({'ok': True, 'message': '属性已添加', 'created': True})


@ozon_bp.route('/api/category/<cat_id>/delete-attribute', methods=['POST'])
@login_required
def api_delete_category_attribute(cat_id):
    """删除手工添加的类目属性"""
    data = request.get_json(silent=True) or {}
    attr_id = str(data.get('attribute_id', '')).strip()
    if not attr_id:
        return jsonify({'ok': False, 'error': '缺少 attribute_id'}), 400

    deleted = (OzonCategoryAttribute
               .delete()
               .where((OzonCategoryAttribute.user == current_user) &
                      (OzonCategoryAttribute.ozon_category_id == cat_id) &
                      (OzonCategoryAttribute.attribute_id == attr_id))
               .execute())
    return jsonify({'ok': True, 'message': f'已删除 {deleted} 条', 'deleted': deleted})


@ozon_bp.route('/api/category/<cat_id>/update-attribute', methods=['POST'])
@login_required
def api_update_category_attribute(cat_id):
    """更新类目属性（名称、必填、字典等）+ 可选全局同步"""
    data = request.get_json(silent=True) or {}
    attr_id = str(data.get('attribute_id', '')).strip()
    propagate = data.get('propagate', False)  # 是否同步到所有类目

    if not attr_id:
        return jsonify({'ok': False, 'error': '缺少 attribute_id'}), 400

    # 更新当前类目的属性
    updates = {}
    if 'name' in data:
        updates['name'] = data['name'].strip()
    if 'name_cn' in data:
        updates['name_cn'] = data['name_cn'].strip()
    if 'is_required' in data:
        updates['is_required'] = bool(data['is_required'])
    if 'is_dictionary' in data:
        updates['is_dictionary'] = bool(data['is_dictionary'])

    if not updates:
        return jsonify({'ok': False, 'error': '没有要更新的字段'}), 400

    where = ((OzonCategoryAttribute.user == current_user) &
             (OzonCategoryAttribute.ozon_category_id == cat_id) &
             (OzonCategoryAttribute.attribute_id == attr_id))
    count = OzonCategoryAttribute.update(**updates).where(where).execute()

    # 全局同步：把 name / name_cn 同步到同名 attribute_id 的所有记录
    sync_count = 0
    if propagate and ('name' in updates or 'name_cn' in updates):
        sync_updates = {}
        if 'name' in updates:
            sync_updates['name'] = updates['name']
        if 'name_cn' in updates:
            sync_updates['name_cn'] = updates['name_cn']
        sync_count = (OzonCategoryAttribute
                      .update(**sync_updates)
                      .where((OzonCategoryAttribute.user == current_user) &
                             (OzonCategoryAttribute.attribute_id == attr_id))
                      .execute())

    return jsonify({
        'ok': True,
        'message': f'已更新 {count} 条'
                   + (f'，全局同步 {sync_count} 条' if sync_count else ''),
        'updated': count,
        'synced': sync_count,
    })


# ═══════════════════════════════════════════════════════

@ozon_bp.route('/category-attributes')
@login_required
def category_attributes():
    """类目属性字典页面"""
    categories = (OzonCategory
                  .select()
                  .where(OzonCategory.user == current_user)
                  .order_by(OzonCategory.name))

    cat_id = request.args.get('category_id', '').strip()
    type_id = request.args.get('type_id', '').strip()
    attributes = []
    mappings = []
    types = []
    if cat_id:
        types = list(OzonCategoryType
                     .select()
                     .where((OzonCategoryType.user == current_user) &
                            (OzonCategoryType.description_category_id == cat_id))
                     .order_by(OzonCategoryType.type_name))

        attr_query = OzonCategoryAttribute.select().where(
            (OzonCategoryAttribute.user == current_user) &
            (OzonCategoryAttribute.ozon_category_id == cat_id)
        )
        if type_id:
            attr_query = attr_query.where(OzonCategoryAttribute.type_id == type_id)
        attributes = list(attr_query.order_by(
            OzonCategoryAttribute.is_required.desc(),
            OzonCategoryAttribute.attribute_id
        ))

        mappings = (OzonAttributeMapping
                    .select()
                    .where((OzonAttributeMapping.user == current_user) &
                           (OzonAttributeMapping.ozon_category_id == cat_id)))

    return render_template('ozon/category_attributes.html',
                           categories=categories, attributes=attributes,
                           mappings=mappings, selected_category=cat_id,
                           selected_type_id=type_id, types=types)


@ozon_bp.route('/api/category/<cat_id>/gaps/<int:draft_id>')
@login_required
def api_field_gaps(cat_id, draft_id):
    """检查字段缺口"""
    draft = (OzonDraft
             .select()
             .where((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
             .first())
    if not draft:
        return jsonify({'ok': False, 'error': '草稿不存在'}), 404

    required_attrs = (OzonCategoryAttribute
                      .select()
                      .where((OzonCategoryAttribute.user == current_user) &
                             (OzonCategoryAttribute.ozon_category_id == cat_id) &
                             (OzonCategoryAttribute.is_required == True)))

    OzonFieldGap.delete().where(
        (OzonFieldGap.user == current_user) & (OzonFieldGap.draft == draft)
    ).execute()

    gaps = []
    for attr in required_attrs:
        gap = OzonFieldGap.create(
            user=current_user,
            draft=draft,
            ozon_category_id=cat_id,
            attribute_id=attr.attribute_id,
            field_name=attr.name,
            gap_type='missing_required',
            severity='error',
            suggested_action=f'请填写 {attr.name_cn or attr.name}',
        )
        gaps.append(gap)

    blocking_count = sum(1 for g in gaps if g.severity == 'error')
    return jsonify({
        'ok': True,
        'required_total': required_attrs.count(),
        'blocking_count': blocking_count,
        'warning_count': sum(1 for g in gaps if g.severity == 'warning'),
        'gaps': [{'id': g.id, 'field_name': g.field_name,
                   'gap_type': g.gap_type, 'severity': g.severity,
                   'suggested_action': g.suggested_action} for g in gaps],
    })


@ozon_bp.route('/api/category/mapping', methods=['POST'])
@login_required
def api_save_mapping():
    """保存属性映射规则"""
    data = request.get_json() or {}
    ozon_category_id = data.get('ozon_category_id', '').strip()
    attribute_id = data.get('attribute_id', '').strip()
    local_field_path = data.get('local_field_path', '').strip()
    fill_policy = data.get('fill_policy', 'manual_required').strip()

    if not ozon_category_id or not attribute_id:
        return jsonify({'ok': False, 'error': '缺少类目ID或属性ID'}), 400

    mapping = (OzonAttributeMapping
               .select()
               .where((OzonAttributeMapping.user == current_user) &
                      (OzonAttributeMapping.ozon_category_id == ozon_category_id) &
                      (OzonAttributeMapping.attribute_id == attribute_id))
               .first())

    if not mapping:
        mapping = OzonAttributeMapping(
            user=current_user,
            ozon_category_id=ozon_category_id,
            attribute_id=attribute_id,
        )

    mapping.local_field_path = local_field_path or None
    mapping.fill_policy = fill_policy
    mapping.manual_required = data.get('manual_required', False)
    mapping.default_value = data.get('default_value', '').strip() or None
    mapping.confidence = data.get('confidence')
    mapping.notes = data.get('notes', '').strip() or None
    mapping.updated_at = datetime.datetime.now()
    mapping.save()

    return jsonify({'ok': True, 'message': '映射规则已保存', 'id': mapping.id})


# ═══════════════════════════════════════════════════════
# P7 — 模型接口配置
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/models', methods=['GET', 'POST'])
@login_required
def model_config():
    """模型接口配置页面"""
    if request.method == 'POST':
        action = request.form.get('action', '').strip()

        # ── 图片生成模型保存 ──
        if action == 'save_image_gen':
            provider = request.form.get('img_gen_provider', '').strip()
            model_name = request.form.get('img_gen_model_name', '').strip()
            api_base = request.form.get('img_gen_api_base', '').strip()
            api_key = request.form.get('img_gen_api_key', '').strip()
            enabled = request.form.get('img_gen_enabled') == 'on'
            notes = request.form.get('img_gen_notes', '').strip() or None

            # 自动加 img_gen_ 前缀
            if provider and not provider.startswith('img_gen_'):
                provider = f'img_gen_{provider}'

            if provider and model_name and api_base:
                # 按 provider 查找已有配置
                config = (VisionModelConfig
                          .select()
                          .where((VisionModelConfig.user == current_user) &
                                 (VisionModelConfig.provider == provider))
                          .first())
                if not config:
                    config = VisionModelConfig(user=current_user, provider=provider)
                config.model_name = model_name
                config.api_base = api_base
                if api_key and api_key != '••••••••••••••••':
                    config.api_key_encrypted = encrypt_api_key(api_key)
                config.enabled = enabled
                config.notes = notes
                config.timeout_seconds = int(request.form.get('img_gen_timeout', 60))
                config.max_images_per_batch = int(request.form.get('img_gen_batch', 5))
                config.updated_at = datetime.datetime.now()
                config.save()
                flash(f'图片生成模型 "{model_name}" 配置已保存 (provider: {provider})', 'success')
            elif provider and not api_base:
                flash('请填写 API Base URL', 'warning')
            elif provider and not model_name:
                flash('请填写 Model Name', 'warning')

            return redirect(url_for('ozon.model_config'))

        # ── 视觉模型保存（原有逻辑）──
        provider = request.form.get('vision_provider', '').strip()
        model_name = request.form.get('vision_model_name', '').strip()
        api_base = request.form.get('vision_api_base', '').strip()
        api_key = request.form.get('vision_api_key', '').strip()
        enabled = request.form.get('vision_enabled') == 'on'
        config_id = request.form.get('config_id', '').strip()

        if provider and model_name and api_base:
            # 编辑已有配置
            if config_id:
                try:
                    config = (VisionModelConfig
                              .select()
                              .where((VisionModelConfig.id == int(config_id)) &
                                     (VisionModelConfig.user == current_user))
                              .first())
                except ValueError:
                    config = None
            else:
                # 新建：按 provider 查找
                config = (VisionModelConfig
                          .select()
                          .where((VisionModelConfig.user == current_user) &
                                 (VisionModelConfig.provider == provider))
                          .first())

            if not config:
                config = VisionModelConfig(user=current_user, provider=provider)
            config.model_name = model_name
            config.api_base = api_base
            if api_key and api_key != '••••••••••••••••':
                config.api_key_encrypted = api_key
            config.enabled = enabled
            config.timeout_seconds = int(request.form.get('vision_timeout', 60))
            config.max_images_per_batch = int(request.form.get('vision_batch', 5))
            config.updated_at = datetime.datetime.now()
            config.save()
            flash(f'视觉模型 "{model_name}" 配置已保存', 'success')

        return redirect(url_for('ozon.model_config'))

    # ── GET：分拆 img_gen 和 vision 配置 ──
    all_configs = (VisionModelConfig
                   .select()
                   .where(VisionModelConfig.user == current_user))
    api_keys = (UserApiKey
                .select()
                .where(UserApiKey.user == current_user))

    # 拆分为图片生成配置和视觉配置
    img_gen_configs = []
    vision_configs = []
    for cfg in all_configs:
        if (cfg.provider or '').startswith('img_gen_'):
            img_gen_configs.append(cfg)
        else:
            vision_configs.append(cfg)

    return render_template('ozon/models.html',
                           vision_configs=vision_configs,
                           img_gen_configs=img_gen_configs,
                           api_keys=api_keys)


@ozon_bp.route('/api/models/config/<int:config_id>', methods=['DELETE'])
@login_required
def api_delete_vision_config(config_id):
    """删除视觉模型配置"""
    config = (VisionModelConfig
              .select()
              .where((VisionModelConfig.id == config_id) & (VisionModelConfig.user == current_user))
              .first())
    if not config:
        return jsonify({'ok': False, 'error': '配置不存在'}), 404
    config.delete_instance()
    return jsonify({'ok': True, 'message': '配置已删除'})


@ozon_bp.route('/api/models/test-vision', methods=['POST'])
@login_required
def api_test_vision():
    """测试视觉模型识别 — 调用真实 API"""
    if 'image' not in request.files:
        return jsonify({'ok': False, 'error': '未收到图片文件'}), 400

    file = request.files['image']
    if not file.filename:
        return jsonify({'ok': False, 'error': '文件名为空'}), 400

    task_type = request.form.get('task_type', 'sku_image')

    file_content = file.read()
    file_size = len(file_content)
    if file_size > 10 * 1024 * 1024:
        return jsonify({'ok': False, 'error': '图片不能超过 10MB'}), 400

    import io, base64, time
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(file_content))
        width, height = img.size
        aspect_ratio = f'{width}:{height}'
        is_34 = abs(width / height - 3 / 4) < 0.05 if width and height else False
    except Exception:
        width = height = None
        aspect_ratio = 'unknown'
        is_34 = False

    img_info = {
        'filename': file.filename,
        'size_kb': round(file_size / 1024, 1),
        'width': width,
        'height': height,
        'aspect_ratio': aspect_ratio,
        'is_3_4': is_34,
    }

    # 获取用户配置的视觉模型
    config = (VisionModelConfig
              .select()
              .where((VisionModelConfig.user == current_user) &
                     (VisionModelConfig.enabled == True))
              .first())

    if not config:
        return jsonify({
            'ok': False,
            'error': '未找到已启用的视觉模型配置，请先在左侧"视觉工具模型"中配置并启用',
            'result': build_basic_vision_result(task_type, img_info),
        }), 400

    # 真实 API 调用
    start_time = time.time()
    try:
        vision_result = call_vision_api(config, file_content, file.filename, task_type)
        elapsed = round((time.time() - start_time) * 1000)
        return jsonify({
            'ok': True,
            'meta': f"任务: {task_type} | 模型: {config.provider}/{config.model_name} | 耗时: {elapsed}ms",
            'result': {**vision_result, 'image': img_info},
        })
    except Exception as e:
        elapsed = round((time.time() - start_time) * 1000)
        # API 调用失败时返回基础图片信息 + 错误提示
        return jsonify({
            'ok': False,
            'error': f'视觉模型调用失败: {str(e)[:300]}',
            'meta': f"模型: {config.provider}/{config.model_name} | 耗时: {elapsed}ms",
            'result': {**build_basic_vision_result(task_type, img_info), 'api_error': str(e)[:500]},
        }), 500


def call_vision_api(config, image_bytes, filename, task_type):
    """
    调用视觉模型 API（OpenAI 兼容格式）。
    支持 openai_vision / qwen_vl / gemini_vision / custom_http，
    只要 API 兼容 OpenAI Chat Completions 接口即可。
    """
    import base64

    mime_types = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'png': 'image/png', 'webp': 'image/webp', 'gif': 'image/gif',
    }
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'png'
    mime = mime_types.get(ext, 'image/png')
    data_url = f'data:{mime};base64,{base64.b64encode(image_bytes).decode()}'

    # 按任务类型构建不同的提示词
    prompts = {
        'sku_image': (
            '你是电商商品图片识别助手。请分析这张SKU/商品图片，只输出 JSON（不要 markdown 代码块）。\n'
            '{\n'
            '  "detected": {"objects": ["识别到的物体"], "colors": ["颜色描述"], "materials": ["疑似材质"]},\n'
            '  "compliance": {"has_chinese": true/false, "has_watermark": true/false, "has_qr_code": true/false, "has_price_or_discount": true/false, "has_platform_logo": true/false, "ozon_ready": true/false, "issues": ["问题描述"]},\n'
            '  "facts": [{"field_path": "字段路径", "value": "值", "evidence": "图片中的证据", "confidence": 0.0-1.0}],\n'
            '  "uncertain": [{"field_path": "字段名", "guess": "猜测值", "reason": "原因", "confidence": 0.0-1.0}],\n'
            '  "summary_cn": "一句中文描述图片内容"\n'
            '}\n'
            '规则：只描述图片中能看到的内容，不要猜测品牌/认证/保修。不确定就放进 uncertain。'
        ),
        'detail_ocr': (
            '你是电商详情图 OCR 识别助手。请提取这张详情图中的文字和参数，只输出 JSON（不要 markdown 代码块）。\n'
            '{\n'
            '  "detected": {"visible_text": ["提取到的文字行"], "dimensions": ["尺寸参数"], "specs": ["规格参数"]},\n'
            '  "compliance": {"has_chinese": true/false, "has_watermark": true/false, "has_qr_code": true/false, "has_platform_logo": true/false, "ozon_ready": true/false, "issues": []},\n'
            '  "facts": [{"field_path": "字段路径", "value": "OCR值", "evidence": "OCR原文", "confidence": 0.0-1.0}],\n'
            '  "summary_cn": "图片中的文字内容概要"\n'
            '}\n'
            '规则：OCR 文字原样提取，不要改写。提取尺寸、重量、材质、型号等参数。'
        ),
        'compliance_check': (
            '你是 OZON 电商图片合规检查助手。请检查这张图片是否符合 OZON 商品卡要求，只输出 JSON（不要 markdown 代码块）。\n'
            '{\n'
            '  "compliance": {\n'
            '    "has_chinese": true/false,\n'
            '    "has_non_russian_text": true/false,\n'
            '    "has_watermark": true/false,\n'
            '    "has_qr_code": true/false,\n'
            '    "has_price_or_discount": true/false,\n'
            '    "has_contact_info": true/false,\n'
            '    "has_platform_logo": true/false,\n'
            '    "ozon_ready": true/false,\n'
            '    "issues": ["具体问题描述，如没有则为空数组"]\n'
            '  },\n'
            '  "summary_cn": "合规检查结论"\n'
            '}\n'
            'OZON 要求：白底或浅色背景、3:4竖版、主体居中、无中文/水印/二维码/价格/Logo。'
        ),
    }

    prompt = prompts.get(task_type, prompts['sku_image'])

    # 构建正确的 API Base URL
    base_url = _resolve_api_base(config)

    try:
        import openai
        client = openai.OpenAI(
            api_key=config.api_key_encrypted or '',
            base_url=base_url,
        )
        response = client.chat.completions.create(
            model=config.model_name,
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': data_url}},
                ],
            }],
            temperature=0.2,
            max_tokens=1500,
            timeout=config.timeout_seconds or 60,
        )
        raw_text = response.choices[0].message.content.strip()
    except Exception:
        # 如果 openai 库不可用或 API 不通，尝试 requests 直接调用
        import requests
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={
                'Authorization': f'Bearer {config.api_key_encrypted}',
                'Content-Type': 'application/json',
            },
            json={
                'model': config.model_name,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt},
                        {'type': 'image_url', 'image_url': {'url': data_url}},
                    ],
                }],
                'temperature': 0.2,
                'max_tokens': 1500,
            },
            timeout=(config.timeout_seconds or 60),
        )
        resp.raise_for_status()
        raw_text = resp.json()['choices'][0]['message']['content'].strip()

    # 清洗 JSON：去掉可能的 markdown 代码块包裹
    if raw_text.startswith('```'):
        lines = raw_text.split('\n')
        lines = lines[1:] if lines[0].startswith('```') else lines
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        raw_text = '\n'.join(lines)

    result = json.loads(raw_text)
    return result


# ═══════════════════════════════════════════════════════════════
# 图片生成模型测试端点
# ═══════════════════════════════════════════════════════════════

@ozon_bp.route('/api/models/test-image-gen', methods=['POST'])
@login_required
def api_test_image_gen():
    """测试图片生成模型配置 — 不实际调用付费 API，仅展示脱敏请求体"""
    config_id = request.form.get('config_id', '').strip()
    if not config_id:
        return jsonify({'ok': False, 'error': '请指定模型配置 ID'}), 400

    try:
        config_id_int = int(config_id)
    except ValueError:
        return jsonify({'ok': False, 'error': '无效的配置 ID'}), 400

    config = (VisionModelConfig
              .select()
              .where((VisionModelConfig.id == config_id_int) &
                     (VisionModelConfig.user == current_user))
              .first())

    if not config:
        return jsonify({'ok': False, 'error': '配置不存在'}), 404

    if not (config.provider or '').startswith('img_gen_'):
        return jsonify({'ok': False, 'error': '该配置不是图片生成模型（provider 不以 img_gen_ 开头）'}), 400

    from services.image_generation import (
        _resolve_api_base,
        _match_model_preset,
        _parse_user_size_override,
    )

    base_url = _resolve_api_base(config)
    model_name = config.model_name or '(未设置)'
    preset = _match_model_preset(model_name)

    # 尺寸
    user_size = _parse_user_size_override(config.notes)
    if user_size:
        image_size = user_size
    elif preset:
        image_size = preset.get('portrait_size', preset.get('default_size', '2K'))
    else:
        image_size = '2K'

    extra_body = preset.get('extra_body') if preset else None
    api_style = preset.get('api_style', 'openai') if preset else 'openai'

    # 构建脱敏请求体
    request_preview = {
        'endpoint': f'{base_url}/images/generations',
        'model': model_name,
        'size': image_size,
        'prompt_length': 0,  # 测试不发真实 prompt
        'api_style': api_style,
        'extra_body': {k: v for k, v in (extra_body or {}).items() if k not in ('api_key',)} if extra_body else None,
        'provider': config.provider,
        'enabled': config.enabled,
    }

    # API Key 脱敏
    key_preview = ''
    if config.api_key_encrypted:
        try:
            decrypted = decrypt_api_key(config.api_key_encrypted)
            key_preview = decrypted[:6] + '...' + decrypted[-4:] if len(decrypted) > 10 else '****'
        except Exception:
            key_preview = (config.api_key_encrypted[:6] + '...') if len(config.api_key_encrypted) > 10 else '****'
    request_preview['api_key_preview'] = key_preview

    # 尝试获取第一张参考图信息
    draft_id = request.form.get('draft_id', '').strip()
    ref_info = None
    if draft_id:
        try:
            draft = (OzonDraft
                     .select()
                     .where((OzonDraft.id == int(draft_id)) & (OzonDraft.user == current_user))
                     .first())
            if draft:
                from services.ecommerce_image_reference import select_references_for_slot
                slots = list(OzonImageSlot.select().where(
                    (OzonImageSlot.draft == draft) &
                    (OzonImageSlot.status == 'planned')
                ).limit(1))
                if slots:
                    refs = select_references_for_slot(current_user, draft, slots[0], max_references=1)
                    if refs:
                        r = refs[0]
                        ref_info = {
                            'media_id': r.get('media_id'),
                            'has_url': bool(r.get('source_url')),
                            'has_local_path': bool(r.get('path')),
                        }
        except Exception as e:
            ref_info = {'error': str(e)[:100]}
    if ref_info:
        request_preview['reference_test'] = ref_info

    return jsonify({
        'ok': True,
        'message': '配置有效（未实际调用付费 API）',
        'request_preview': request_preview,
    })


def _resolve_api_base(config):
    """根据 provider 解析正确的 OpenAI 兼容 API Base URL"""
    raw = (config.api_base or '').strip().rstrip('/')

    # 如果用户已经填了完整的 /v1 路径，直接用
    if raw.endswith('/v1'):
        return raw

    # 阿里云百炼 / DashScope：需要 /compatible-mode/v1
    if 'dashscope.aliyuncs.com' in raw or 'dashscope' in raw.lower():
        return f'{raw}/compatible-mode/v1'

    # 其他 provider（openai / gemini / custom）：默认追加 /v1
    return f'{raw}/v1'


def build_basic_vision_result(task_type, img_info):
    """返回基本的图片信息（API 未配置或调用失败时的回退）"""
    base = {
        'image': img_info,
        'detected': {},
        'compliance': {
            'has_chinese': False, 'has_non_russian_text': False,
            'has_watermark': False, 'has_qr_code': False,
            'has_price_or_discount': False, 'has_contact_info': False,
            'has_platform_logo': False, 'has_unverified_certification': False,
            'ozon_ready': True, 'issues': [],
        },
        'facts': [],
        'uncertain': [],
        'summary_cn': '',
    }
    if img_info.get('width') and img_info.get('height') and not img_info.get('is_3_4'):
        base['compliance']['issues'].append(
            f"图片比例 {img_info['aspect_ratio']}，建议 OZON 使用 3:4 竖版（当前非 3:4）"
        )
        base['compliance']['ozon_ready'] = False
    base['summary_cn'] = (
        '合规检查完成（仅本地图片尺寸分析）。'
        if not base['compliance']['issues']
        else f"发现 {len(base['compliance']['issues'])} 个合规问题。"
    )
    return base


# ═══════════════════════════════════════════════════════
# P9 — 在线商品管理
# ═══════════════════════════════════════════════════════

def _normalize_online_product(item, account):
    """将 OZON API 返回的商品数据归一化为本地字段（兼容 list 和 info 两种响应）"""
    # ID 字段：可能有 product_id / id / offer_id
    ozon_id = str(item.get('product_id') or item.get('id', ''))
    offer_id = item.get('offer_id', '')
    name = item.get('name', '') or offer_id  # list API 可能不含 name

    # 价格：可能是字符串或数字
    price = item.get('price')
    if price is not None:
        try: price = float(str(price))
        except: price = None

    old_price = item.get('old_price')
    if old_price is not None:
        try: old_price = float(str(old_price))
        except: old_price = None

    min_price = item.get('min_price')
    if min_price is not None:
        try: min_price = float(str(min_price))
        except: min_price = None

    # 库存：stocks.present 或 stock.present 或 sources 数组
    stock_present = 0
    stock_reserved = 0
    stocks = item.get('stocks') or item.get('stock')
    if isinstance(stocks, dict):
        stock_present = stocks.get('present', 0)
        stock_reserved = stocks.get('reserved', 0)
    # 也可能在 sources 里
    sources = item.get('sources', [])
    if sources and not stock_present:
        for s in sources:
            if s.get('source') == 'fbs':
                stock_present += s.get('quantity', 0)

    # 图片
    images = item.get('images', [])
    primary_image = ''
    if images:
        if isinstance(images[0], dict):
            primary_image = images[0].get('link', images[0].get('url', ''))
        elif isinstance(images[0], str):
            primary_image = images[0]

    return {
        'ozon_product_id': ozon_id,
        'offer_id': offer_id,
        'name': name,
        'sku': item.get('sku', 1) or 1,
        'status': item.get('status', item.get('state', 'active')),
        'visibility': str(item.get('visibility', item.get('visible', ''))),
        'is_archived': bool(item.get('is_archived', item.get('archived', False))),
        'price': price,
        'old_price': old_price,
        'min_price': min_price,
        'currency': str(item.get('currency', item.get('currency_code', 'RUB'))),
        'stock_present': stock_present,
        'stock_reserved': stock_reserved,
        'category_id': str(item.get('category_id', item.get('description_category_id', ''))),
        'category_name': item.get('category_name', ''),
        'type_id': str(item.get('type_id', '')),
        'primary_image': primary_image,
        'images_json': json.dumps(images, ensure_ascii=False),
        'attributes_json': json.dumps(item.get('attributes', []), ensure_ascii=False),
        'errors_json': json.dumps(item.get('errors', []), ensure_ascii=False) if item.get('errors') else None,
        'raw_json': json.dumps(item, ensure_ascii=False),
    }


@ozon_bp.route('/online-products')
@login_required
def online_products():
    """在线商品列表"""
    account_id = request.args.get('account_id', '').strip()
    status = request.args.get('status', '').strip()
    archived = request.args.get('archived', '').strip()
    search = request.args.get('search', '').strip()

    accounts = (OzonAccount
                .select()
                .where((OzonAccount.user == current_user) &
                       (OzonAccount.is_active == True)))

    query = OzonOnlineProduct.select().where(OzonOnlineProduct.user == current_user)
    if account_id:
        query = query.where(OzonOnlineProduct.account == account_id)
    if status:
        query = query.where(OzonOnlineProduct.status == status)
    if archived == '1':
        query = query.where(OzonOnlineProduct.local_is_archived == True)
    elif archived == '0':
        query = query.where(OzonOnlineProduct.local_is_archived == False)
    if search:
        query = query.where(
            (OzonOnlineProduct.name.contains(search)) |
            (OzonOnlineProduct.offer_id.contains(search)) |
            (OzonOnlineProduct.ozon_product_id.contains(search))
        )

    products = list(query.order_by(OzonOnlineProduct.updated_at.desc()).limit(200))

    # 最近同步时间
    last_sync = (OzonOnlineProduct
                 .select(fn.MAX(OzonOnlineProduct.last_synced_at))
                 .where(OzonOnlineProduct.user == current_user)
                 .scalar())

    return render_template('ozon/online_products.html',
                           products=products,
                           accounts=accounts,
                           last_sync=last_sync,
                           selected_account=account_id,
                           selected_status=status,
                           selected_archived=archived,
                           search=search)


@ozon_bp.route('/online-products/sync', methods=['POST'])
@login_required
def online_products_sync():
    """从 OZON API 同步在线商品到本地缓存"""
    account_id = request.form.get('account_id', '').strip()
    account = (OzonAccount
               .get_or_none((OzonAccount.id == account_id) &
                            (OzonAccount.user == current_user)))
    if not account:
        return jsonify({'ok': False, 'error': '未找到店铺'}), 400

    try:
        from services.ozon_api import create_client
        client = create_client(account)

        # Step 1: 分页拉取全部在线商品 ID 列表
        all_ids = []
        last_id = ""
        print(f"[同步] 开始拉取商品列表...")
        while True:
            items, total, last_id = client.list_products(last_id=last_id, limit=100)
            for it in items:
                pid = str(it.get('product_id') or it.get('id', ''))
                if pid:
                    all_ids.append(pid)
            if not last_id or len(all_ids) >= total:
                break

        print(f"[同步] 获取到 {len(all_ids)} 个商品 ID，开始拉取详情...")

        # Step 2: 分批获取完整商品信息
        BATCH_SIZE = 50
        all_details = []
        for i in range(0, len(all_ids), BATCH_SIZE):
            batch_ids = all_ids[i:i + BATCH_SIZE]
            try:
                details = client.get_product_info(product_ids=batch_ids)
                all_details.extend(details)
                print(f"[同步] 详情 {i+1}-{min(i+BATCH_SIZE, len(all_ids))}/{len(all_ids)} 完成")
            except Exception as e:
                print(f"[同步] 详情批次 {i} 失败: {e}")

        print(f"[同步] 获取到 {len(all_details)} 件详细数据，开始保存...")

        # Step 3: 保存到本地
        saved = 0
        for item in all_details:
            data = _normalize_online_product(item, account)
            offer_id = data['offer_id']
            if not offer_id:
                continue

            record, created = OzonOnlineProduct.get_or_create(
                user=current_user,
                account=account,
                offer_id=offer_id,
                defaults=data,
            )
            if not created:
                for k, v in data.items():
                    setattr(record, k, v)
                record.last_synced_at = datetime.datetime.now()
                record.save()
            saved += 1

        # 记录操作日志
        OzonOnlineProductAction.create(
            user=current_user,
            account=account,
            action_type='sync',
            status='success',
            request_json=json.dumps({'account_id': account_id, 'total': len(all_details)}, ensure_ascii=False),
            response_json=json.dumps({'saved': saved}, ensure_ascii=False),
        )

        return jsonify({'ok': True, 'message': f'同步完成：{len(all_details)} 件商品，保存 {saved} 件', 'total': saved})

    except Exception as e:
        OzonOnlineProductAction.create(
            user=current_user,
            account=account,
            action_type='sync',
            status='failed',
            error_message=str(e)[:500],
        )
        return jsonify({'ok': False, 'error': f'同步失败: {str(e)[:300]}'}), 500


@ozon_bp.route('/online-products/<int:product_id>')
@login_required
def online_product_detail(product_id):
    """在线商品详情"""
    product = OzonOnlineProduct.get_or_none(
        (OzonOnlineProduct.id == product_id) &
        (OzonOnlineProduct.user == current_user)
    )
    if not product:
        flash('商品不存在或无权访问', 'danger')
        return redirect(url_for('ozon.online_products'))

    # 解析 JSON 字段
    images = json.loads(product.images_json) if product.images_json else []
    attributes = json.loads(product.attributes_json) if product.attributes_json else []
    errors = json.loads(product.errors_json) if product.errors_json else []

    # 获取操作日志
    actions = (OzonOnlineProductAction
               .select()
               .where(OzonOnlineProductAction.online_product == product)
               .order_by(OzonOnlineProductAction.created_at.desc())
               .limit(20))

    # 关联草稿
    draft = None
    if product.draft_id:
        draft = OzonDraft.get_or_none(OzonDraft.id == product.draft_id)

    return render_template('ozon/online_product_detail.html',
                           product=product,
                           images=images,
                           attributes=attributes,
                           errors=errors,
                           actions=actions,
                           draft=draft)


@ozon_bp.route('/online-products/<int:product_id>/sync-detail', methods=['POST'])
@login_required
def online_product_sync_detail(product_id):
    """从 OZON API 同步单个商品详情"""
    product = OzonOnlineProduct.get_or_none(
        (OzonOnlineProduct.id == product_id) &
        (OzonOnlineProduct.user == current_user)
    )
    if not product:
        return jsonify({'ok': False, 'error': '商品不存在'}), 404

    try:
        from services.ozon_api import create_client
        client = create_client(product.account)
        items = client.get_product_info(product_ids=[product.ozon_product_id])
        if not items:
            return jsonify({'ok': False, 'error': 'OZON 未返回该商品信息'}), 404

        data = _normalize_online_product(items[0], product.account)
        for k, v in data.items():
            setattr(product, k, v)
        product.last_synced_at = datetime.datetime.now()
        product.save()

        OzonOnlineProductAction.create(
            user=current_user,
            account=product.account,
            online_product=product,
            action_type='sync',
            status='success',
        )

        return jsonify({'ok': True, 'message': '已同步最新数据'})

    except Exception as e:
        return jsonify({'ok': False, 'error': f'同步失败: {str(e)[:200]}'}), 500


@ozon_bp.route('/online-products/<int:product_id>/archive', methods=['POST'])
@login_required
def online_product_archive(product_id):
    """归档在线商品（本地 + OZON）"""
    product = OzonOnlineProduct.get_or_none(
        (OzonOnlineProduct.id == product_id) &
        (OzonOnlineProduct.user == current_user)
    )
    if not product:
        return jsonify({'ok': False, 'error': '商品不存在'}), 404

    try:
        from services.ozon_api import create_client
        client = create_client(product.account)
        client.archive_products([product.ozon_product_id])

        product.local_is_archived = True
        product.save()

        OzonOnlineProductAction.create(
            user=current_user,
            account=product.account,
            online_product=product,
            action_type='archive',
            status='success',
        )

        return jsonify({'ok': True, 'message': '已归档'})

    except Exception as e:
        OzonOnlineProductAction.create(
            user=current_user,
            account=product.account,
            online_product=product,
            action_type='archive',
            status='failed',
            error_message=str(e)[:500],
        )
        return jsonify({'ok': False, 'error': f'归档失败: {str(e)[:200]}'}), 500


@ozon_bp.route('/online-products/<int:product_id>/unarchive', methods=['POST'])
@login_required
def online_product_unarchive(product_id):
    """取消归档"""
    product = OzonOnlineProduct.get_or_none(
        (OzonOnlineProduct.id == product_id) &
        (OzonOnlineProduct.user == current_user)
    )
    if not product:
        return jsonify({'ok': False, 'error': '商品不存在'}), 404

    try:
        from services.ozon_api import create_client
        client = create_client(product.account)
        client.unarchive_products([product.ozon_product_id])

        product.local_is_archived = False
        product.save()

        OzonOnlineProductAction.create(
            user=current_user,
            account=product.account,
            online_product=product,
            action_type='unarchive',
            status='success',
        )

        return jsonify({'ok': True, 'message': '已恢复'})

    except Exception as e:
        return jsonify({'ok': False, 'error': f'恢复失败: {str(e)[:200]}'}), 500


@ozon_bp.route('/online-products/<int:product_id>/update', methods=['POST'])
@login_required
def online_product_update(product_id):
    """更新在线商品并推送至 OZON（按字段类型分发到不同 API）"""
    product = OzonOnlineProduct.get_or_none(
        (OzonOnlineProduct.id == product_id) &
        (OzonOnlineProduct.user == current_user)
    )
    if not product:
        return jsonify({'ok': False, 'error': '商品不存在'}), 404

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({'ok': False, 'error': '没有要更新的数据'}), 400

    try:
        from services.ozon_api import create_client
        client = create_client(product.account)
        results = []

        # ── 价格更新（专用端点） ──
        price_fields = {k: data[k] for k in ('price', 'old_price', 'min_price') if k in data}
        if price_fields:
            price_item = {
                "offer_id": product.offer_id,
                "currency_code": "RUB",
            }
            if 'price' in price_fields:
                price_item['price'] = str(price_fields['price'])
                product.price = price_fields['price']
            if 'old_price' in price_fields:
                price_item['old_price'] = str(price_fields['old_price'])
                product.old_price = price_fields['old_price']
            if 'min_price' in price_fields:
                price_item['min_price'] = str(price_fields['min_price'])
                product.min_price = price_fields['min_price']

            r = client.update_product_prices([price_item])
            results.append(('price', r))

        # ── 库存更新（专用端点） ──
        if 'stock' in data:
            stock_item = {
                "offer_id": product.offer_id,
                "product_id": int(product.ozon_product_id) if product.ozon_product_id else 0,
                "stock": int(data['stock']),
            }
            product.stock_present = int(data['stock'])
            r = client.update_product_stocks([stock_item])
            results.append(('stock', r))

        # ── 内容更新（标题/描述/属性 — 用 import 接口，带完整数据） ──
        content_fields = {k: data[k] for k in ('name', 'description', 'barcode', 'weight', 'depth', 'width', 'height', 'attributes', 'images') if k in data}
        if content_fields:
            # 构建完整的商品数据（当前值 + 修改值），避免 OZON 判定为非正常更新
            body_item = {
                "offer_id": product.offer_id,
                "name": product.name or '',
                "category_id": int(product.category_id) if product.category_id else 0,
            }
            if product.type_id:
                body_item['type_id'] = int(product.type_id)

            # 保留现有图片
            if product.images_json:
                try:
                    existing_imgs = json.loads(product.images_json) if isinstance(product.images_json, str) else product.images_json
                    if isinstance(existing_imgs, list):
                        body_item['images'] = existing_imgs
                except (json.JSONDecodeError, TypeError):
                    pass

            # 保留现有属性
            if product.attributes_json:
                try:
                    existing_attrs = json.loads(product.attributes_json) if isinstance(product.attributes_json, str) else product.attributes_json
                    if isinstance(existing_attrs, list):
                        body_item['attributes'] = existing_attrs
                except (json.JSONDecodeError, TypeError):
                    pass

            # 覆盖要修改的字段
            if 'name' in content_fields:
                body_item['name'] = content_fields['name']
                product.name = content_fields['name']
            if 'description' in content_fields:
                body_item['description'] = content_fields['description']
            if 'barcode' in content_fields:
                body_item['barcode'] = str(content_fields['barcode'])
            if 'weight' in content_fields:
                body_item['weight'] = int(content_fields['weight'])
            if 'depth' in content_fields:
                body_item['depth'] = int(content_fields['depth'])
            if 'width' in content_fields:
                body_item['width'] = int(content_fields['width'])
            if 'height' in content_fields:
                body_item['height'] = int(content_fields['height'])
            if 'attributes' in content_fields:
                body_item['attributes'] = content_fields['attributes']
                product.attributes_json = json.dumps(content_fields['attributes'], ensure_ascii=False)
            if 'images' in content_fields:
                body_item['images'] = content_fields['images']
                product.images_json = json.dumps(content_fields['images'], ensure_ascii=False)

            r = client.import_product(body_item)
            results.append(('content', r))

        product.updated_at = datetime.datetime.now()
        product.save()

        OzonOnlineProductAction.create(
            user=current_user,
            account=product.account,
            online_product=product,
            action_type='update_content',
            status='success',
            request_json=json.dumps(data, ensure_ascii=False)[:2000],
            response_json=json.dumps(results, ensure_ascii=False, default=str)[:2000],
        )

        parts = [t for t, _ in results]
        return jsonify({'ok': True, 'message': '已推送: ' + ', '.join(parts)})

    except Exception as e:
        OzonOnlineProductAction.create(
            user=current_user,
            account=product.account,
            online_product=product,
            action_type='update_content',
            status='failed',
            error_message=str(e)[:500],
        )
        return jsonify({'ok': False, 'error': f'更新失败: {str(e)[:200]}'}), 500
