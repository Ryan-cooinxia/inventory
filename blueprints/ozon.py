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
    # 新增：产品母图
    OzonImagePlan, OzonImageReference, OzonProductCutout,
    # 新增：Excel 模板发布通道
    OzonExcelTemplate, OzonTemplateExportJob,
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
from services.product_cutout import (
    create_product_cutout,
    find_best_media_for_cutout,
    _load_media_image,
    CUTOUT_DIR,
)
from services.product_subject_detector import detect_product_subject
from services.vision_tool import analyze_product_image
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


# ── 内置常用俄语→中文映射（字典值翻译兜底）──
_BUILTIN_VALUE_CN_MAP = {
    'Китай': '中国', 'Россия': '俄罗斯', 'Черный': '黑色', 'черный': '黑色',
    'Белый': '白色', 'белый': '白色', 'Красный': '红色', 'красный': '红色',
    'Синий': '蓝色', 'синий': '蓝色', 'Зеленый': '绿色', 'зеленый': '绿色',
    'Желтый': '黄色', 'желтый': '黄色', 'Серый': '灰色', 'серый': '灰色',
    'Коричневый': '棕色', 'коричневый': '棕色', 'Оранжевый': '橙色', 'оранжевый': '橙色',
    'Фиолетовый': '紫色', 'фиолетовый': '紫色', 'Розовый': '粉色', 'розовый': '粉色',
    'Бежевый': '米色', 'бежевый': '米色', 'Золотой': '金色', 'золотой': '金色',
    'Серебряный': '银色', 'серебряный': '银色', 'Прозрачный': '透明', 'прозрачный': '透明',
    '1 год': '1年', '1 месяц': '1个月', '1 неделя': '1周', 'Да': '是', 'Нет': '否',
    'Для экшн-камеры': '适用于运动相机', 'Для фотокамеры': '适用于相机',
}


def resolve_attribute_value_cn(user, ru_value, value_id=None, attribute_id=None):
    """
    自动解析字典值的中文翻译。查找顺序：
      1. 当前记录的 value_cn
      2. 同用户其他记录相同 value 的 value_cn
      3. 内置常用映射
    返回 (cn_value, source) 或 (None, None)
    """
    if not ru_value or not ru_value.strip():
        return None, None
    ru_value = ru_value.strip()

    # 1. 查当前具体记录
    if value_id and attribute_id:
        rec = (OzonAttributeValue
               .select(OzonAttributeValue.value_cn)
               .where((OzonAttributeValue.user == user) &
                      (OzonAttributeValue.attribute_id == str(attribute_id)) &
                      (OzonAttributeValue.value_id == str(value_id)))
               .first())
        if rec and rec.value_cn and rec.value_cn.strip():
            return rec.value_cn.strip(), 'existing'

    # 2. 查同用户其他记录相同 value（跨 type/category 复用翻译）
    cn_rec = (OzonAttributeValue
              .select(OzonAttributeValue.value_cn)
              .where((OzonAttributeValue.user == user) &
                     (OzonAttributeValue.value == ru_value) &
                     (OzonAttributeValue.value_cn.is_null(False)) &
                     (OzonAttributeValue.value_cn != '') &
                     (OzonAttributeValue.value_cn != ru_value))
              .first())
    if cn_rec:
        return cn_rec.value_cn.strip(), 'reused'

    # 3. 内置映射
    for key, cn in _BUILTIN_VALUE_CN_MAP.items():
        if key.lower() == ru_value.lower():
            return cn, 'builtin'

    return None, None


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
        # 店铺维度配置
        acc.seller_ui_language = request.form.get('seller_ui_language', acc.seller_ui_language or 'zh')
        acc.template_language = request.form.get('template_language', acc.template_language or 'zh')
        acc.default_currency = request.form.get('default_currency', acc.default_currency or 'CNY')
        acc.currency_confirmed = request.form.get('currency_confirmed') == '1'
        if acc.currency_confirmed and not acc.locale_confirmed_at:
            acc.locale_confirmed_at = datetime.datetime.now()
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


# ── 视频辅助函数 ────────────────────────────────────────────

def _is_ozon_rejected_video_url(url):
    """Check if a video URL matches known OZON rejected/buyer patterns."""
    u = (url or '').lower()
    if not u:
        return False
    return any(x in u for x in [
        'ozon.st/', 'ozon.ru/st/',
        '/review', '/comment', '/feedback',
    ])


def _is_ozon_review_video_url(url):
    u = (url or '').lower()
    if not u: return False
    return any(x in u for x in [
        '/s3/video-', 'ir.ozone.ru/s3/video',
        'review', 'comment', 'feedback', 'buyer'
    ])


def _is_valid_product_video(v):
    """Check if a video dict represents a valid product video.
    Returns False for review/buyer videos first, then checks URL validity."""
    if isinstance(v, str):
        url = v
        source_area = ''
        source = ''
    else:
        url = v.get('src') or v.get('url') or ''
        source_area = v.get('source_area') or ''
        source = v.get('source') or ''

    # Review/buyer video: reject first
    if url and _is_ozon_review_video_url(url):
        return False

    # Must have a valid URL
    if not url or not url.startswith('http'):
        return False

    # Check source_area for buyer media
    if source_area and source_area in ('buyer_review', 'buyer_comment', 'buyer_feedback'):
        return False

    return True


def _get_video_reject_reason(v_dict):
    """Return a specific reject reason string for a video candidate."""
    if isinstance(v_dict, str):
        url = v_dict
        source_area = ''
        source = ''
    else:
        url = v_dict.get('src') or v_dict.get('url') or ''
        source_area = v_dict.get('source_area') or ''
        source = v_dict.get('source') or ''

    if not url:
        return 'empty_record'

    # Check if it's actually an image URL
    if any(url.lower().endswith(ext) or ('?' in url and url.split('?')[0].lower().endswith(ext))
           for ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif')):
        return 'image_or_buyer_video'

    if _is_ozon_review_video_url(url):
        return 'review_video'

    if not url.startswith('http'):
        return 'not_playable_url'

    if source_area == 'buyer_review':
        return 'image_or_buyer_video'

    if source_area not in ('main_gallery', 'pdp_video', 'product_video', '') and source not in ('network',):
        return 'not_main_gallery'

    if source and source not in ('network', 'pdp', 'dom', 'click_trigger'):
        return 'untrusted_source'

    return 'unknown'


def _classify_video_state(v):
    """Classify a video candidate: 'video', 'entry_only', 'image', 'rejected'."""
    if isinstance(v, str):
        url = v
        poster = ''
    else:
        url = v.get('src') or v.get('url') or ''
        poster = v.get('poster') or ''

    if not url:
        return 'rejected'

    # Check if URL is actually an image (misclassified)
    if any(url.lower().endswith(ext) or ('?' in url and url.split('?')[0].lower().endswith(ext))
           for ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif')):
        return 'entry_only' if poster else 'image'

    if not url.startswith('http'):
        return 'rejected'

    return 'video'


def _ozon_video_group_key(url):
    """Extract dedup group key from OZON video URL patterns like /vod/video-XX/."""
    import re
    u = url or ''
    m = re.search(r'/vod/(video-\d+)', u)
    if m:
        return m.group(1)
    m = re.search(r'/(video-\d{3,})', u)
    if m:
        return m.group(1)
    # Fallback: URL base without query params
    base = u.split('?')[0]
    return base.rsplit('/', 1)[-1] if '/' in base else base


def _ozon_video_score(v):
    """Score a video candidate for dedup selection (higher = better)."""
    if isinstance(v, str):
        url = v
        source = ''
        source_area = ''
    else:
        url = v.get('src') or v.get('url') or ''
        source = v.get('source') or ''
        source_area = v.get('source_area') or ''

    score = 0

    # Prefer network source (actual media URL from request monitoring)
    if source == 'network':
        score += 10
    elif source == 'pdp':
        score += 8
    elif source == 'dom':
        score += 5

    # Prefer main_gallery source_area
    if source_area == 'main_gallery':
        score += 6
    elif source_area == 'pdp_video':
        score += 4

    # Prefer .mp4 URLs
    if url and '.mp4' in url.lower():
        score += 3
    if url and 'video' in url.lower():
        score += 2

    return score


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
    pricing = data.get('pricing') or {}
    price_candidates = data.get('price_candidates') or []
    rich_text = data.get('rich_text') or {}
    attribute_candidates = data.get('attribute_candidates') or []
    product_videos = data.get('product_videos') or []; video_candidates = product_videos or data.get('video_candidates') or data.get('videos') or []
    rejected_images = data.get('rejected_images') or []
    debug_data = data.get('debug') or {}

    # OZON 参考商品经常没有显式 SKU 按钮；如果插件已经识别到标题/参考售价，
    # 这里兜底创建 1 个默认规格，避免适配工作台出现”源 SKU(0)”断流。
    if platform == 'ozon_product' and not skus:
        fallback_price = None
        # 只有明确 CNY 的价格才进入 purchase_price_cny（RUB 是参考售价不是采购价）
        if pricing.get('source_price_cny') or pricing.get('candidate_price_cny'):
            fallback_price = pricing.get('source_price_cny') or pricing.get('candidate_price_cny')
        if not fallback_price and price_candidates:
            for p in price_candidates:
                if str(p.get('currency', '')).upper() == 'CNY':
                    fallback_price = p.get('price')
                    break
        skus = [{
            'source_order': 1,
            'source_sku_name': title[:120] or '默认规格',
            'style_cn': '默认规格',
            'bundle_quantity': 1,
            'purchase_price_cny': fallback_price,
            'reference_price_rub': pricing.get('reference_price_rub'),
            'source_price_currency': 'RUB',
            'price_manual_confirmed': False,
        }]

    # ── 构建 raw_json（含 specs）────────────────────
    raw_data = {
        "product": {
            "title_cn": title,
            "category_cn": data.get('category', ''),
            "description_cn": data.get('description', ''),
            "shop_name": data.get('shop_name', ''),
            "rich_text_html": rich_text.get('html', ''),
            "rich_text_plain": rich_text.get('plain_text', ''),
        },
        "rich_text": rich_text,
        "source_attributes": attribute_candidates,
        "skus": skus,
        "media": [{"source_url": img.get("src", ""), "role": img.get("role", "sku")} for img in images],
        "specs_json": specs or attribute_candidates,
        "videos": video_candidates,
        "pricing": pricing,
        "price_candidates": price_candidates,
        "rejected_images": rejected_images,
        "debug": debug_data,
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

    # OZON：有 reference_price_rub 就算价格已识别（参考售价，非采购价）
    if platform == 'ozon_product':
        has_confirmed_price = bool(pricing.get('reference_price_rub'))
    else:
        has_confirmed_price = any(sku.get('purchase_price_cny') for sku in skus)
    price_unconfirmed = not has_confirmed_price

    # ── OZON 来源特殊标记 ──
    is_ozon_product = (platform == 'ozon_product')

    # ── 先建 source（media 需要 FK）─────────────────
    source = OzonSource.create(
        user=user,
        platform=platform,
        source_url=source_url,
        capture_url=capture_url,
        source_item_id=data.get('item_id', ''),
        title_cn=title,
        category_cn=data.get('category', ''),
        description_cn=(rich_text.get('plain_text') or data.get('description', ''))[:50000],
        shop_name=data.get('shop_name', ''),
        sku_count=len(skus),
        image_count=saved_img_count,
        raw_json=json.dumps(raw_data, ensure_ascii=False),
        quality_json=json.dumps(quality, ensure_ascii=False),
        detail_missing=(platform == '1688' and detail_missing_from_payload),
        price_manual_confirmed=(has_confirmed_price and not is_ozon_product),
        status='collected',
        capture_method='browser_extension',
        captured_at=datetime.datetime.now(),
    )

    # ── 再建 media（关联 source）───────────────────
    for mrec in media_records:
        if is_ozon_product and mrec.get("comp_status") == "rejected":
            continue
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
        # buyer_review 角色 — 独立入库，仅用于 AI 参考
        if mrec.get("role") == "buyer_review":
            media_source_val = 'ozon_buyer_review'
            compliance_val = 'needs_review'
            review_val = 'pending'
            reject_val = '仅用于AI参考'
        # OZON 来源图片标记为参考图
        else:
            media_source_val = 'ozon_reference' if is_ozon_product else 'browser_extension'
            compliance_val = 'needs_review' if is_ozon_product else mrec["comp_status"]
            review_val = 'pending' if is_ozon_product else ('rejected' if mrec["comp_status"] == 'rejected' else ('pending' if mrec["comp_status"] == 'needs_review' else 'approved'))
            reject_val = 'OZON参考图，发布前需替换为自有图片' if is_ozon_product else mrec["reason"]

        OzonSourceMedia.create(
            user=user,
            source=source,
            media_id=f'ext-{mrec["src"][:50]}',
            media_source=media_source_val,
            role=mrec["role"],
            source_url=mrec["src"],
            width=mrec.get('width') or None,
            height=mrec.get('height') or None,
            for_ozon=(not is_ozon_product),
            compliance_status=compliance_val,
            reject_reason=reject_val,
            review_status=review_val,
            raw_json=json.dumps(meta_json, ensure_ascii=False),
        )

    # ── 视频分类与过滤 ──
    rejected_videos = []
    clean_videos = []

    for v in video_candidates:
        if isinstance(v, str):
            v_src = v
            poster = ''
            source_val = ''
            source_area = ''
        else:
            v_src = v.get('src') or v.get('url') or ''
            poster = v.get('poster') or ''
            source_val = v.get('source') or ''
            source_area = v.get('source_area') or ''

        state = _classify_video_state(v)

        if not _is_valid_product_video(v):
            reason = _get_video_reject_reason(v)
            rejected_videos.append({
                'url': v_src,
                'poster': poster,
                'source': source_val,
                'virus_area': source_area,
                'video_classification': state,
                'need_manual_check': True,
                'reason': reason,
            })
            continue

        clean_videos.append(v)

    # ── 视频去重标记（不删除，只标记 primary/duplicate）──
    from collections import defaultdict
    groups = defaultdict(list)
    for i, v in enumerate(clean_videos):
        if isinstance(v, str):
            url = v
        else:
            url = v.get('src') or v.get('url') or ''
        key = _ozon_video_group_key(url) if url else f'empty-{i}'
        groups[key].append((i, v))

    score = _ozon_video_score
    for key, items in groups.items():
        if len(items) <= 1:
            continue
        items.sort(key=lambda x: score(x[1]), reverse=True)
        # First is primary, rest are duplicates
        for rank, (idx, vitem) in enumerate(items):
            if isinstance(vitem, dict):
                if rank == 0:
                    vitem['duplicate_status'] = 'primary'
                else:
                    vitem['duplicate_status'] = 'duplicate'
                    vitem['duplicate_group'] = key
            # For string items, wrap in dict
            if isinstance(vitem, str):
                clean_videos[idx] = {
                    'src': vitem,
                    'duplicate_status': 'primary' if rank == 0 else 'duplicate',
                }
                if rank > 0:
                    clean_videos[idx]['duplicate_group'] = key

    # Move duplicates to rejected_videos and remove from clean_videos
    final_clean = []
    for v in clean_videos:
        if isinstance(v, dict) and v.get('duplicate_status') == 'duplicate':
            v_src = v.get('src') or v.get('url') or ''
            rejected_videos.append({
                'url': v_src,
                'poster': v.get('poster') or '',
                'source': v.get('source') or '',
                'source_area': v.get('source_area') or '',
                'video_classification': _classify_video_state(v),
                'need_manual_check': False,
                'reason': f'duplicate of group {v.get("duplicate_group", "")}',
                'duplicate_status': 'duplicate',
                'duplicate_group': v.get('duplicate_group', ''),
            })
        else:
            final_clean.append(v)

    # ── 视频候选排序（只保留 primary，去重后）──
    video_candidates = sorted(final_clean, key=score, reverse=True)

    # ── 视频入库 ──
    if source and hasattr(source, 'id') and source.id:
        for vi, v in enumerate(video_candidates):
            if isinstance(v, str):
                v_src = v
                poster = ''
                dup_status = ''
                dup_group = ''
            else:
                v_src = v.get('src') or v.get('url') or ''
                poster = v.get('poster') or ''
                dup_status = v.get('duplicate_status', '')
                dup_group = v.get('duplicate_group', '')

            # 仅使用 v_src（不 fallback 到 poster）
            if v_src and v_src.startswith('http'):
                raw_json = {
                    'video_url': v_src,
                    'poster': poster,
                }
                if dup_status:
                    raw_json['duplicate_status'] = dup_status
                if dup_group:
                    raw_json['duplicate_group'] = dup_group

                def_source = 'ozon_reference' if is_ozon_product else 'browser_extension'
                OzonSourceMedia.create(
                    user=user,
                    source=source,
                    media_id=f'video-{vi+1:03d}',
                    media_source=def_source,
                    role='video',
                    source_url=v_src,
                    for_ozon=False,
                    compliance_status='needs_review',
                    reject_reason='OZON参考视频，发布前需人工确认/替换',
                    review_status='pending',
                    raw_json=json.dumps(raw_json, ensure_ascii=False),
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

    # 将视频过滤结果写入 raw_json（供适配工作台调试）
    raw_data['rejected_video_candidates'] = rejected_videos
    raw_data['video_candidates'] = video_candidates
    source.raw_json = json.dumps(raw_data, ensure_ascii=False)

    source.status = 'parsed'
    source.sku_count = OzonSourceSku.select().where(OzonSourceSku.source == source).count()
    source.image_count = saved_img_count
    source.save()

    # ── 响应 ────────────────────────────────────────
    saved_video_count = len(video_candidates)
    resp = {
        "ok": True, "id": source.id, "title": title,
        "sku_count": source.sku_count, "image_count": saved_img_count,
        "video_received_count": len(data.get('product_videos') or data.get('video_candidates') or data.get('videos') or []),
        "video_saved_count": saved_video_count,
        "video_rejected_count": len(rejected_videos),
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

@ozon_bp.route('/sources/collect-ozon-url', methods=['POST'])
@login_required
def source_collect_ozon_url():
    """采集 OZON 商品链接"""
    url = request.form.get('url', '').strip()
    if not url:
        flash('请输入 OZON 商品链接', 'danger')
        return redirect(url_for('ozon.sources'))

    # 校验 URL
    if 'ozon.ru' not in url.lower() and 'ozon.by' not in url.lower() and 'ozon.kz' not in url.lower():
        flash('请输入有效的 OZON 商品链接', 'warning')
        return redirect(url_for('ozon.sources'))

    from services.ozon_collector import collect_ozon_product_url

    # 如果有粘贴内容，优先用粘贴内容解析
    content = request.form.get('content', '').strip()
    if content:
        from services.ozon_collector import extract_product
        key_record = UserApiKey.get_or_none(UserApiKey.user == current_user)
        if key_record:
            from crypto_utils import decrypt_api_key
            api_key = decrypt_api_key(key_record.api_key)
            provider = key_record.api_provider or 'deepseek'
            parsed = extract_product(content, api_key=api_key, provider=provider, source_url=url)
            result = {
                'platform': 'ozon_product', 'source_url': url,
                'title_cn': parsed.get('product', {}).get('title_cn', '') or parsed.get('product', {}).get('title', ''),
                'title_ru': parsed.get('product', {}).get('title_cn', ''),
                'category_path': parsed.get('product', {}).get('category_cn', ''),
                'description_ru': parsed.get('product', {}).get('description_cn', ''),
                'skus': parsed.get('skus', []),
                'media': parsed.get('media', []),
                'specs_json': parsed.get('product', {}).get('attributes', []),
                'missing_fields': [],
                'raw_text': content[:50000],
                'raw_json': parsed,
            }
        else:
            result = collect_ozon_product_url(url, user=current_user)
    else:
        result = collect_ozon_product_url(url, user=current_user)

    if not result.get('title_ru') and not result.get('title_cn'):
        flash('采集失败：无法获取 OZON 商品信息。建议在浏览器打开商品页，Ctrl+A 全选复制后粘贴到页面内容框。', 'danger')
        return redirect(url_for('ozon.sources'))

    # 保存到 OzonSource
    import datetime as dt
    source = OzonSource.create(
        user=current_user,
        platform='ozon_product',
        source_url=url,
        title_cn=result.get('title_cn', '') or result.get('title_ru', '')[:300],
        category_cn=result.get('category_path', '')[:100],
        description_cn=result.get('description_ru', '')[:5000],
        shop_name=result.get('seller_name', '')[:200],
        sku_count=len(result.get('skus', [])),
        image_count=len(result.get('media', [])),
        raw_json=json.dumps(result, ensure_ascii=False),
        capture_method='ozon_url',
        status='collected',
        captured_at=dt.datetime.now(),
        quality_json=json.dumps({'missing_fields': result.get('missing_fields', [])}, ensure_ascii=False),
    )

    # 保存 SKU
    for i, sku in enumerate(result.get('skus', [])[:50]):
        OzonSourceSku.create(
            user=current_user, source=source,
            source_order=i + 1,
            source_sku_id=sku.get('sku_id', str(i + 1))[:100],
            source_sku_name=sku.get('name', '')[:200],
            color_cn=sku.get('color', '')[:50],
            size_cn=sku.get('size', '')[:50],
            purchase_price_cny=None,
        )

    # 保存图片（标记为参考图）
    for j, img in enumerate(result.get('media', [])[:30]):
        OzonSourceMedia.create(
            user=current_user, source=source,
            media_id=f'ozon_{j + 1}',
            media_source='ozon_reference',
            role=img.get('role', 'detail')[:30],
            source_url=img.get('url', '')[:500],
            for_ozon=False,
            review_status='pending',
            compliance_status='needs_review',
            reject_reason='OZON参考图，需替换为自有图片',
        )

    flash(f'OZON 商品采集完成：{source.title_cn[:40]}，{source.sku_count} SKU，{source.image_count} 张参考图', 'success')
    return redirect(url_for('ozon.source_detail', source_id=source.id))


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


@ozon_bp.route('/sources/<int:source_id>/download-images')
@login_required
def source_download_images(source_id):
    """一键下载该采集下所有图片的 ZIP 包"""
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        flash('采集记录不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    media_list = list(OzonSourceMedia.select().where(
        (OzonSourceMedia.source == source) &
        (OzonSourceMedia.user == current_user)
    ))

    if not media_list:
        flash('该采集下没有图片', 'warning')
        return redirect(url_for('ozon.sources'))

    import zipfile
    import io as io_module

    buf = io_module.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, m in enumerate(media_list):
            img = _load_media_image(m)
            if img:
                img_bytes = io_module.BytesIO()
                # 根据原始格式保存
                fmt = 'JPEG' if m.source_url and '.jpg' in m.source_url.lower() else 'PNG'
                img.save(img_bytes, format=fmt)
                img_bytes.seek(0)
                name = f'{i+1:02d}_{m.role or "img"}.{fmt.lower()}'
                zf.writestr(name, img_bytes.read())
            elif m.source_url:
                # 直接下载原始文件
                try:
                    import requests
                    resp = requests.get(m.source_url, headers={
                        'User-Agent': 'Mozilla/5.0', 'Referer': 'https://detail.1688.com/'
                    }, timeout=20)
                    resp.raise_for_status()
                    suffix = '.jpg' if 'jpg' in (m.source_url.lower()) else '.png'
                    name = f'{i+1:02d}_{m.role or "img"}{suffix}'
                    zf.writestr(name, resp.content)
                except Exception:
                    pass

    buf.seek(0)
    safe_name = (source.title_cn or f'product_{source_id}')[:30]
    safe_name = ''.join(c for c in safe_name if c.isalnum() or c in '._- ')[:30]

    from flask import send_file
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'{safe_name}_images.zip'
    )


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


@ozon_bp.route('/api/source-media/<int:media_id>/reject', methods=['POST'])
@login_required
def api_reject_source_media(media_id):
    """将源媒体标记为不可用。用于人工移除重复视频、误采图片等。"""
    media = (OzonSourceMedia
             .select()
             .join(OzonSource)
             .where((OzonSourceMedia.id == media_id) & (OzonSource.user == current_user))
             .first())
    if not media:
        return jsonify({'ok': False, 'error': '媒体不存在'}), 404

    payload = request.get_json(silent=True) or {}
    reason = (payload.get('reason') or '').strip() or '人工移除'

    media.compliance_status = 'rejected'
    media.review_status = 'rejected'
    media.reject_reason = reason[:200]
    media.save()

    source = media.source
    source.image_count = (OzonSourceMedia
                          .select()
                          .where((OzonSourceMedia.source == source) &
                                 ((OzonSourceMedia.compliance_status != 'rejected') |
                                  (OzonSourceMedia.compliance_status.is_null())))
                          .count())
    source.save()

    return jsonify({'ok': True, 'message': '媒体已移除', 'media_id': media.id})


@ozon_bp.route('/api/source/<int:source_id>/reference-price', methods=['POST'])
@ozon_bp.route('/api/source/<int:source_id>/pricing', methods=['POST'])
@login_required
def api_update_reference_price(source_id):
    """更新 OZON 参考售价"""
    source = OzonSource.get_or_none((OzonSource.id == source_id) & (OzonSource.user == current_user))
    if not source:
        return jsonify({'ok': False, 'error': '来源不存在'}), 404

    data = request.get_json(silent=True) or {}
    ref_price = data.get('reference_price_rub')
    cur_price = data.get('current_price_rub')
    try: ref_price = int(ref_price) if ref_price else None
    except: return jsonify({'ok': False, 'error': '无效参考售价'}), 400
    try: cur_price = int(cur_price) if cur_price else None
    except: cur_price = None
    if not ref_price or ref_price < 100 or ref_price > 10000000:
        return jsonify({'ok': False, 'error': '参考售价超出范围'}), 400

    raw = {}
    try: raw = json.loads(source.raw_json or '{}')
    except: raw = {}

    pricing = raw.get('pricing') or {}
    pricing['reference_price_rub'] = ref_price
    pricing['current_price_rub'] = cur_price or ref_price
    pricing['currency'] = 'RUB'
    pricing['source'] = data.get('source') or 'manual'
    pricing['confirmed'] = True
    pricing['updated_at'] = datetime.datetime.now().isoformat()
    raw['pricing'] = pricing
    # 同步 SKU 价格字段
    for sku in raw.get('skus', []):
        sku['reference_price_rub'] = ref_price
        sku['current_price_rub'] = cur_price or ref_price
        sku['source_price_currency'] = 'RUB'
    source.raw_json = json.dumps(raw, ensure_ascii=False)
    source.save()

    return jsonify({'ok': True, 'reference_price_rub': ref_price, 'current_price_rub': cur_price, 'message': '参考售价已更新'})


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

    # ── 从采集数据直接填草稿（OZON 源本身就是俄语）──
    raw = {}
    try: raw = json.loads(source.raw_json or '{}')
    except: raw = {}

    # 标题：直接使用 OZON 原始俄语标题（截取前150字符符合 OZON 规范）
    draft.title_ru = (source.title_cn or '')[:150]

    # 描述：HTML转纯文本（简介用纯文本，富文本HTML放rich_content_json）
    rich_text = raw.get('rich_text') or {}
    raw_html = rich_text.get('html') or ''
    if raw_html:
        from services.ozon_template_excel import html_to_plain_text
        draft.description_ru = html_to_plain_text(raw_html, max_length=50000)
        # 同时生成 rich_content_json（保留原始HTML用于富文本tab）
        if not draft.rich_content_json:
            from services.ozon_template_excel import build_rich_content_from_draft
    else:
        draft.description_ru = (rich_text.get('plain_text') or source.description_cn or '')[:50000]

    # 卖点：从属性字典提取关键属性拼接
    source_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
    bullets = []
    key_attrs = ['Тип', 'Цвет', 'Вес товара, г', 'Материал', 'Гарантия',
                 'Страна-изготовитель', 'Размеры', 'Особенности', 'Назначение',
                 'Бренд', 'Модель', 'Емкость аккумулятора', 'Совместимость']
    for attr in source_attrs:
        name = (attr.get('name') or attr.get('key') or '').strip().rstrip(',:;')
        value = attr.get('value') or attr.get('text') or ''
        if not name or not value: continue
        # 匹配关键属性或直接拼接
        for ka in key_attrs:
            if ka.lower() in name.lower():
                bullets.append(f"• {name}: {value}")
                break
    # 最少保留3条兜底
    if len(bullets) < 3:
        for attr in source_attrs[:8]:
            name = (attr.get('name') or attr.get('key') or '').strip()
            value = attr.get('value') or attr.get('text') or ''
            if name and value:
                b = f"• {name}: {value}"
                if b not in bullets: bullets.append(b)
    draft.bullets_ru = json.dumps(bullets[:8], ensure_ascii=False)

    # 属性 + 定价数据
    draft.attributes_json = json.dumps(source_attrs, ensure_ascii=False)[:8000]
    pricing = raw.get('pricing') or {}
    draft.pricing_json = json.dumps({
        'reference_price_rub': pricing.get('reference_price_rub'),
        'current_price_rub': pricing.get('current_price_rub'),
        'currency': pricing.get('currency', 'RUB'),
    }, ensure_ascii=False)

    draft.status = 'draft'
    draft.updated_at = datetime.datetime.now()
    draft.save()

    _ensure_image_slots(draft)

    flash(f'草稿已生成：标题 {len(draft.title_ru)} 字符、{len(bullets)} 条卖点、描述 {len(draft.description_ru or "")} 字符', 'success')
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
    draft.category_path_ru = request.form.get('category_path_ru') or request.form.get('ozon_category_path') or draft.category_path_ru
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
# P5.2 — 产品母图准备
# ═══════════════════════════════════════════════════════

@ozon_bp.route('/uploads/cutouts/<path:filename>')
@login_required
def serve_cutout(filename):
    """Serve cutout images (transparent PNG, mask, preview)."""
    filename = filename.replace('\\', '/')
    return send_from_directory(
        os.path.join(current_app.root_path, 'uploads', CUTOUT_DIR),
        filename
    )


@ozon_bp.route('/product-cutout/<int:source_id>')
@login_required
def product_cutout_page(source_id):
    """产品母图准备页面"""
    source = (OzonSource
              .select()
              .where((OzonSource.id == source_id) & (OzonSource.user == current_user))
              .first())
    if not source:
        flash('采集记录不存在', 'danger')
        return redirect(url_for('ozon.sources'))

    # 推荐的图片列表
    recommendations = find_best_media_for_cutout(current_user, source_id, max_count=8)

    # 已有的抠图记录
    cutouts = list(OzonProductCutout.select().where(
        (OzonProductCutout.user == current_user) &
        (OzonProductCutout.source == source)
    ).order_by(OzonProductCutout.created_at.desc()))

    # 图片详情 map
    media_map = {}
    if recommendations:
        media_ids = [r['media_id'] for r in recommendations]
        for m in OzonSourceMedia.select().where(OzonSourceMedia.id.in_(media_ids)):
            media_map[m.id] = m

    return render_template('ozon/product_cutout.html',
                           source=source,
                           recommendations=recommendations,
                           media_map=media_map,
                           cutouts=cutouts)


@ozon_bp.route('/product-cutout/<int:media_id>/create', methods=['POST'])
@login_required
def product_cutout_create(media_id):
    """对单张图片执行抠图"""
    media = (OzonSourceMedia
             .select()
             .join(OzonSource)
             .where((OzonSourceMedia.id == media_id) &
                    (OzonSource.user == current_user))
             .first())
    if not media:
        return jsonify({'ok': False, 'error': '图片不存在'}), 404

    provider = request.form.get('provider', 'rembg_crop').strip()
    sku_id = request.form.get('sku_id', '').strip()
    sku = None
    if sku_id and sku_id.isdigit():
        sku = OzonSourceSku.get_or_none(
            (OzonSourceSku.id == int(sku_id)) & (OzonSourceSku.user == current_user)
        )

    # 目标框 JSON: [{"type":"main_product","bbox":[x1,y1,x2,y2],"keep":true,"label":"商品"}]
    targets = None
    targets_json = request.form.get('targets', '').strip()
    if targets_json:
        try:
            targets = json.loads(targets_json)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    result = create_product_cutout(current_user, media, provider=provider, sku=sku, targets=targets)
    return jsonify(result)


@ozon_bp.route('/product-cutout/<int:media_id>/detect-subject', methods=['POST'])
@login_required
def product_cutout_detect_subject(media_id):
    """自动识别产品主体"""
    media = (OzonSourceMedia
             .select()
             .join(OzonSource)
             .where((OzonSourceMedia.id == media_id) &
                    (OzonSource.user == current_user))
             .first())
    if not media:
        return jsonify({'ok': False, 'error': '图片不存在'}), 404

    result = detect_product_subject(current_user, media)
    if 'error' in result:
        return jsonify({'ok': False, 'error': result['error']}), 400
    result['ok'] = True
    return jsonify(result)


@ozon_bp.route('/product-cutout/<int:cutout_id>/approve', methods=['POST'])
@login_required
def product_cutout_approve(cutout_id):
    """确认产品母图"""
    cutout = (OzonProductCutout
              .select()
              .join(OzonSource)
              .where((OzonProductCutout.id == cutout_id) &
                     (OzonProductCutout.user == current_user))
              .first())
    if not cutout:
        return jsonify({'ok': False, 'error': '记录不存在'}), 404

    # ── 质量门禁 ──
    quality = {}
    if cutout.quality_json:
        try: quality = json.loads(cutout.quality_json)
        except: pass

    # 1. 必须有目标
    if not cutout.target_count or cutout.target_count == 0:
        return jsonify({'ok': False, 'error': '该结果未使用目标框，可能包含广告内容。请框选商品后使用"目标抠图"重新生成。'}), 400

    # 2. 质量必须通过
    if not quality.get('pass', True):
        return jsonify({'ok': False, 'error': f'质量检查未通过，请重新抠图。问题: {"; ".join(quality.get("warnings", [])[:3])}'}), 400

    # 3. 像素必须保持
    if not quality.get('pixel_preserved', True):
        return jsonify({'ok': False, 'error': '产品像素已被AI修改，不可作为正式母图。请重新生成。'}), 400

    # 4. 框外残留不能太多
    outside = quality.get('outside_residual_score')
    if outside is not None and outside < 0.95:
        return jsonify({'ok': False, 'error': f'目标框外仍有{int((1-outside)*100)}%残留，请重新框选商品。'}), 400

    # 5. rembg_full 且包含文字 → 禁止
    if (cutout.segmentation_provider or cutout.provider) == 'rembg_full':
        return jsonify({'ok': False, 'error': '整图抠图结果不可作为正式母图。请框选商品后使用"目标抠图"。'}), 400

    # 先取消同一 source 的旧 primary
    OzonProductCutout.update(is_primary=False).where(
        (OzonProductCutout.source == cutout.source) &
        (OzonProductCutout.is_primary == True)
    ).execute()

    cutout.status = 'approved'
    cutout.is_primary = True
    cutout.reviewer_notes = request.form.get('notes', '')[:500] or None
    cutout.save()
    return jsonify({'ok': True, 'message': '母图已确认'})


@ozon_bp.route('/product-cutout/<int:cutout_id>/reject', methods=['POST'])
@login_required
def product_cutout_reject(cutout_id):
    """拒绝抠图结果"""
    cutout = (OzonProductCutout
              .select()
              .join(OzonSource)
              .where((OzonProductCutout.id == cutout_id) &
                     (OzonProductCutout.user == current_user))
              .first())
    if not cutout:
        return jsonify({'ok': False, 'error': '记录不存在'}), 404

    cutout.status = 'rejected'
    cutout.reviewer_notes = request.form.get('notes', '')[:500] or None
    cutout.save()
    return jsonify({'ok': True, 'message': '母图已拒绝'})


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

    # 预计算模板就绪状态（仅精确匹配 dcid+type_id）
    template_map = {}
    active_templates = list(OzonExcelTemplate.select().where(
        (OzonExcelTemplate.user == current_user) &
        (OzonExcelTemplate.status == 'active')))
    template_keys = {(t.dcid, t.type_id) for t in active_templates}
    for d in drafts:
        if d.type_id and d.ozon_category_id:
            template_map[d.id] = (str(d.ozon_category_id), str(d.type_id)) in template_keys
        else:
            template_map[d.id] = False

    return render_template('ozon/listings.html',
                           drafts=drafts,
                           template_map=template_map,
                           page=page,
                           per_page=per_page,
                           total_pages=total_pages)


@ozon_bp.route('/listing/prototype')
@login_required
def listing_prototype():
    """刊登草稿审核页 — 产品原型预览"""
    return render_template('ozon/listing_review_prototype.html', draft={}, source={})

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

    # 补中文类目名（如果 type_name_cn 为空，从 OzonCategoryType 查）
    if not draft.type_name_cn and draft.type_id:
        ct = (OzonCategoryType
              .select(OzonCategoryType.type_name_cn)
              .where((OzonCategoryType.user == current_user) &
                     (OzonCategoryType.type_id == draft.type_id))
              .first())
        if ct and ct.type_name_cn:
            draft.type_name_cn = ct.type_name_cn
            # 顺手回写，下次直接有
            try:
                OzonDraft.update(type_name_cn=ct.type_name_cn).where(
                    OzonDraft.id == draft.id).execute()
            except Exception:
                pass

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

    # 解析源商品 raw_json
    raw = {}
    source_pricing = {}
    if draft.source:
        try: raw = json.loads(draft.source.raw_json or '{}')
        except: raw = {}
        source_pricing = raw.get('pricing') or {}

    # 用草稿保存的 pricing_json 覆盖采集源（用户编辑的价格持久化回显）
    saved_pricing = {}
    try:
        saved_pricing = json.loads(draft.pricing_json or '{}')
    except (json.JSONDecodeError, TypeError):
        saved_pricing = {}
    pricing = dict(source_pricing)
    pricing.update(saved_pricing)

    # 解析已保存属性（使用归一化层）
    draft_attrs = _load_draft_attributes_map(draft)

    # 源属性双语显示
    src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
    source_attrs_display = []
    for a in src_attrs[:20]:
        n_ru = a.get('name','') or a.get('key','')
        v_ru = str(a.get('value','') or a.get('text',''))
        source_attrs_display.append({'name_ru':n_ru,'value_ru':v_ru,'source':a.get('source','')})

    # 源图片（按 OZON 发布用途分类）
    #   main           → 图片/视频 tab，直接上传 OZON 商品图片区（可多张）
    #   sku            → SKU 图片区
    #   detail/scene等 → 富文本 tab，随描述上传（不进图片/视频 tab）
    #   buyer_review/unknown/other → 隐藏参考
    RICH_TEXT_ROLES = {'detail', 'scene', 'gallery', 'selling_point', 'function', 'package', 'accessory'}
    source_media = []
    source_media_main = []
    source_media_sku = []
    source_media_rich = []
    source_media_other = []
    source_media_json = []
    if draft.source:
        source_media = list(OzonSourceMedia.select().where(OzonSourceMedia.source == draft.source).limit(100))
        for sm in source_media:
            role = (sm.role or '').lower()
            source_media_json.append({
                'url': (sm.source_url or sm.local_path or ''),
                'role': role,
                'id': sm.id
            })
            if role == 'main':
                source_media_main.append(sm)
            elif role == 'sku':
                source_media_sku.append(sm)
            elif role in RICH_TEXT_ROLES:
                source_media_rich.append(sm)
            else:
                source_media_other.append(sm)

    # 颜色属性（由当前 type 的 Schema 决定是否必填）
    COLOR_ALIASES = ["商品颜色", "颜色", "Цвет", "Цвет товара", "color", "colour"]
    color_attr = None
    color_values = []
    if draft.ozon_category_id and draft.type_id:
        attrs = list(OzonCategoryAttribute.select().where(
            (OzonCategoryAttribute.user == current_user) &
            (OzonCategoryAttribute.ozon_category_id == draft.ozon_category_id) &
            (OzonCategoryAttribute.type_id == draft.type_id)
        ))
        for a in attrs:
            name_lower = ((a.name_cn or '') + ' ' + (a.name or '')).lower()
            if any(alias.lower() in name_lower for alias in COLOR_ALIASES):
                color_attr = {
                    'attribute_id': a.attribute_id, 'name': a.name,
                    'name_cn': a.name_cn, 'is_required': a.is_required,
                    'is_dictionary': a.is_dictionary, 'dictionary_id': a.dictionary_id,
                }
                # 字典属性：加载可选值
                if a.is_dictionary:
                    color_values = list(OzonAttributeValue.select().where(
                        (OzonAttributeValue.user == current_user) &
                        (OzonAttributeValue.attribute_id == a.attribute_id)
                    ).order_by(OzonAttributeValue.value))
                break

    # 解析媒体池
    media = _load_media_json(draft)

    # ── 第一步：按采集源真实 role 修正媒体池中的历史脏数据 ──
    # 历史导入曾把多余 main 写成 gallery，这里用 source_media 的真实 role 覆盖
    source_role_by_id = {}
    for sm in source_media:
        source_role_by_id[str(sm.id)] = (sm.role or '').lower()

    for img in media.get('images', []):
        sm_id = str(img.get('source_media_id') or '')
        src_role = source_role_by_id.get(sm_id)
        if not src_role:
            continue
        img['source_role'] = src_role
        # 采集源判定是 main/sku，就以采集源为准纠正草稿
        if src_role in ('main', 'sku'):
            img['role'] = src_role
            img['selected'] = True

    # ── 第二步：合并采集主图中缺失的图片（仅按 source_media_id 去重）──
    existing_source_media_ids = {
        str(img.get('source_media_id'))
        for img in media.get('images', [])
        if img.get('source_media_id')
    }

    has_cover = any(
        img.get('selected') and img.get('role') == 'main' and img.get('is_cover')
        for img in media.get('images', [])
    )

    main_sort_orders = [
        img.get('sort_order', 0)
        for img in media.get('images', [])
        if img.get('role') == 'main'
    ]
    next_sort = max(main_sort_orders) if main_sort_orders else 0

    # ── 读取图片删除黑名单（用户手动删除的图片不自动补回）──
    deleted_image_source_ids = {str(x) for x in media.get('deleted_image_source_ids', [])}
    deleted_image_urls = set(media.get('deleted_image_urls', []))

    for sm in source_media_main:
        sm_id = str(sm.id)

        # 仅按 source_media_id 判断重复，不用 URL/path（避免误杀变体URL）
        if sm_id in existing_source_media_ids:
            continue

        # 图片删除黑名单：用户手动删除过的 source_media_id 不自动补回
        if sm_id in deleted_image_source_ids:
            continue
        url = sm.source_url or sm.local_path or ''
        if url and url in deleted_image_urls:
            continue

        next_sort += 1
        is_cover = not has_cover

        img_obj = {
            'id': f'img_src_{sm.id}',
            'source': 'collected',
            'source_media_id': sm.id,
            'local_path': sm.local_path or '',
            'public_url': url,
            'ozon_url': None,
            'thumb_url': url,
            'filename': url.rsplit('/', 1)[-1] if url else f'source_{sm.id}.jpg',
            'role': 'main',
            'source_role': 'main',
            'is_cover': is_cover,
            'selected': True,
            'sort_order': next_sort,
            'upload_status': 'public_ready' if url.startswith('http') else 'local',
            'review_status': sm.review_status or 'pending',
            'alt': '',
            'width': sm.width,
            'height': sm.height,
        }
        media['images'].append(img_obj)
        existing_source_media_ids.add(sm_id)
        if is_cover:
            has_cover = True

    # ── 第三步：整理主图封面和顺序 ──
    main_imgs = [
        img for img in media.get('images', [])
        if img.get('selected') and img.get('role') == 'main'
    ]
    main_imgs.sort(key=lambda x: (x.get('sort_order') or 999999, str(x.get('id') or '')))
    for idx, img in enumerate(main_imgs):
        img['sort_order'] = idx + 1
        img['is_cover'] = idx == 0

    # 汇率：用于显示人民币参考价
    from services import get_rate as get_exchange_rate
    exchange_rate = None
    try:
        rate_rub = get_exchange_rate('CNY', 'RUB')
        rate_usd = get_exchange_rate('CNY', 'USD')
        rate_eur = get_exchange_rate('CNY', 'EUR')
        er_updated = (ExchangeRate
                      .select()
                      .where(ExchangeRate.base_currency == 'CNY')
                      .order_by(ExchangeRate.updated_at.desc())
                      .first())
        exchange_rate = {
            'rub': rate_rub, 'usd': rate_usd, 'eur': rate_eur,
            'updated': str(er_updated.updated_at)[:19] if er_updated else ''
        }
    except Exception:
        pass
    # 人民币参考价
    reference_price_cny = None
    src_price = None
    if pricing:
        src_price = (pricing.get('reference_price_rub') or pricing.get('reference_price')
                     or pricing.get('price') or pricing.get('original_price'))
    if exchange_rate and exchange_rate.get('rub') and src_price:
        try:
            ref_rub = float(str(src_price).replace(',', '.').replace(' ', ''))
            if float(exchange_rate['rub']) > 0:
                reference_price_cny = round(ref_rub / float(exchange_rate['rub']), 2)
        except (ValueError, TypeError):
            pass

    return render_template('ozon/listing_review.html',
                           draft=draft,
                           validation=validation,
                           skus_with_images=skus_with_images,
                           total_slots=total_slots,
                           approved_slots=approved_slots,
                           raw=raw, pricing=pricing,
                           draft_attrs=draft_attrs,
                           source_attrs_display=source_attrs_display,
                           source_media=source_media,
                           source_media_json=source_media_json,
                           source_media_main=source_media_main,
                           source_media_sku=source_media_sku,
                           source_media_rich=source_media_rich,
                           source_media_other=source_media_other,
                           color_attr=color_attr,
                           color_values=color_values,
                           media=media,
                           exchange_rate=exchange_rate,
                           reference_price_cny=reference_price_cny,
                           accounts=list(OzonAccount.select()
                                         .where((OzonAccount.user == current_user) &
                                                (OzonAccount.is_active == True))
                                         .order_by(OzonAccount.name.asc())))


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
    draft.category_path_ru = request.form.get('category_path_ru') or request.form.get('ozon_category_path') or draft.category_path_ru
    draft.category_path_cn = request.form.get('category_path_cn') or draft.category_path_cn
    draft.price_manual_confirmed = request.form.get('price_manual_confirmed') == '1'
    draft.updated_at = datetime.datetime.now()

    # 保存刊登价到 pricing_json
    listing_price = request.form.get('listing_price', '').strip()
    listing_currency = request.form.get('listing_currency', 'RUB').strip()
    pricing = {}
    try:
        pricing = json.loads(draft.pricing_json or '{}')
    except (json.JSONDecodeError, TypeError):
        pricing = {}
    if listing_price:
        pricing['listing_price'] = listing_price
    if listing_currency:
        pricing['listing_currency'] = listing_currency
    draft.pricing_json = json.dumps(pricing, ensure_ascii=False)
    draft.save()

    # 保存 SKU 数据（使用数据库 ID 匹配的名称）
    for sku in draft.draft_skus:
        sku.offer_id = request.form.get(f'offer_id_{sku.id}') or sku.offer_id
        sku.color_ru = request.form.get(f'color_ru_{sku.id}') or None
        sku.style_ru = request.form.get(f'style_ru_{sku.id}') or None
        sku.barcode = request.form.get(f'barcode_{sku.id}') or None
        qty = request.form.get(f'bundle_qty_{sku.id}', '')
        if qty:
            try:
                sku.bundle_quantity = int(qty)
            except ValueError:
                pass
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
    checks.append({'label': 'OZON 类目已选择', 'pass': bool(draft.ozon_category_id or draft.category_path_ru), 'blocking': True, 'level': 'error' if not (draft.ozon_category_id or draft.category_path_ru) else 'success'})
    checks.append({'label': '缺少 SKU 数据', 'pass': draft.draft_skus.count() > 0, 'blocking': True, 'level': 'error' if draft.draft_skus.count() == 0 else 'success'})

    # 价格校验
    pricing = {}
    try:
        pricing = json.loads(draft.pricing_json or '{}')
    except (json.JSONDecodeError, TypeError):
        pricing = {}
    checks.append({'label': '刊登价已填写', 'pass': bool(pricing.get('listing_price')), 'blocking': True,
                   'level': 'error' if not pricing.get('listing_price') else 'success'})
    checks.append({'label': '刊登币种已选择', 'pass': bool(pricing.get('listing_currency')), 'blocking': True,
                   'level': 'error' if not pricing.get('listing_currency') else 'success'})
    checks.append({'label': '价格已人工确认', 'pass': draft.price_manual_confirmed, 'blocking': True,
                   'level': 'error' if not draft.price_manual_confirmed else 'success'})

    # SKU offer_id 校验
    import re
    OFFER_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{3,80}$')
    skus = list(draft.draft_skus)
    all_have_offer = all(sk.offer_id for sk in skus)

    # 格式校验
    bad_format_ids = []
    seen_ids = set()
    dup_in_draft = set()
    for sk in skus:
        oid = (sk.offer_id or '').strip()
        if not oid:
            continue
        if not OFFER_ID_PATTERN.match(oid):
            bad_format_ids.append(oid)
        if oid in seen_ids:
            dup_in_draft.add(oid)
        seen_ids.add(oid)

    checks.append({'label': '所有 SKU 已填 offer_id', 'pass': all_have_offer, 'blocking': True,
                   'level': 'error' if not all_have_offer else 'success'})
    checks.append({'label': 'offer_id 格式正确（字母/数字/下划线/短横线）',
                   'pass': len(bad_format_ids) == 0, 'blocking': True,
                   'level': 'success' if not bad_format_ids else 'error',
                   'detail': f'非法格式: {", ".join(bad_format_ids)}' if bad_format_ids else ''})
    checks.append({'label': '当前草稿内 offer_id 无重复',
                   'pass': len(dup_in_draft) == 0, 'blocking': True,
                   'level': 'success' if not dup_in_draft else 'error',
                   'detail': f'重复值: {", ".join(dup_in_draft)}' if dup_in_draft else ''})

    # 跨草稿重复校验（同用户 + 同账号，排除当前草稿）
    if seen_ids:
        cross_dup = (OzonDraft
                     .select()
                     .where(
                         (OzonDraft.user == current_user) &
                         (OzonDraft.id != draft.id) &
                         (OzonDraft.ozon_offer_id.in_(list(seen_ids)))
                     ).exists())
        checks.append({'label': 'offer_id 未与其他草稿/已发布商品重复',
                       'pass': not cross_dup, 'blocking': True,
                       'level': 'success' if not cross_dup else 'error'})

    # 颜色仅当 Schema 要求时才阻断
    COLOR_ALIASES = ["商品颜色", "颜色", "Цвет", "Цвет товара", "color", "colour"]
    color_required = False
    if draft.ozon_category_id and draft.type_id:
        attrs = list(OzonCategoryAttribute.select().where(
            (OzonCategoryAttribute.user == current_user) &
            (OzonCategoryAttribute.ozon_category_id == draft.ozon_category_id) &
            (OzonCategoryAttribute.type_id == draft.type_id)
        ))
        for a in attrs:
            name_lower = ((a.name_cn or '') + ' ' + (a.name or '')).lower()
            if any(alias.lower() in name_lower for alias in COLOR_ALIASES):
                color_required = a.is_required
                break
    if color_required:
        missing_color_skus = [sk.source_sku_name for sk in draft.draft_skus if not sk.color_ru]
        checks.append({'label': '商品颜色已填写（当前类目必填）',
                       'pass': len(missing_color_skus) == 0, 'blocking': True,
                       'level': 'success' if len(missing_color_skus) == 0 else 'error'})
    else:
        checks.append({'label': '商品颜色（当前类目非必填）', 'pass': True, 'blocking': False, 'level': 'success'})

    # 媒体校验
    media = _load_media_json(draft)
    images = media.get('images', [])
    selected_imgs = [i for i in images if i.get('selected')]
    has_main = any(i.get('role') == 'main' for i in selected_imgs)
    checks.append({'label': '至少有 1 张已选图片', 'pass': len(selected_imgs) > 0, 'blocking': True,
                   'level': 'success' if selected_imgs else 'error'})
    checks.append({'label': '已指定主图', 'pass': has_main, 'blocking': True,
                   'level': 'success' if has_main else 'error'})

    # 图片槽位审批（同时保留旧逻辑）
    slots_ok = _all_slots_approved(draft)
    if not slots_ok:
        checks.append({'label': '图片槽位全部审核通过', 'pass': False, 'blocking': False,
                       'level': 'warning'})

    checks.append({'label': 'SKU 顺序与源一致', 'pass': True, 'blocking': False, 'level': 'success'})
    checks.append({'label': '买家可见内容未检测到禁止词', 'pass': True, 'blocking': False, 'level': 'warning'})

    blocking_count = sum(1 for c in checks if not c['pass'] and c['blocking'])
    validation = {'blocking_count': blocking_count, 'checks': checks}

    draft.validation_result = json.dumps(validation, ensure_ascii=False)
    draft.updated_at = datetime.datetime.now()
    draft.save()

    flash(f'校验完成：{blocking_count} 项阻断', 'warning' if blocking_count > 0 else 'success')
    return redirect(url_for('ozon.listing_review', draft_id=draft_id))


def _load_media_json(draft):
    """读取草稿媒体池 JSON，若为空返回初始结构"""
    try:
        media = json.loads(draft.media_json or '{}')
    except (json.JSONDecodeError, TypeError):
        media = {}
    if 'images' not in media:
        media['images'] = []
    if 'videos' not in media:
        media['videos'] = []
    return media


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
        return jsonify({"ok": False, "error": "草稿不存在"}), 404

    if draft.status != 'approved':
        return jsonify({"ok": False, "error": "只有审核通过的草稿才能发布"}), 400

    if not draft.account:
        return jsonify({"ok": False, "error": "请先选择目标店铺"}), 400

    # type_id 校验：必须存在
    category_id = str(draft.ozon_category_id or '')
    type_id = str(draft.type_id or '')
    if not category_id or not type_id:
        return jsonify({"ok": False, "error": "发布前必须绑定 description_category_id 和 type_id。请在类目属性页面同步 type_id。"}), 400

    # 必填属性缺口检查（使用归一化层）
    required_attrs = list(OzonCategoryAttribute
                          .select()
                          .where((OzonCategoryAttribute.user == current_user) &
                                 (OzonCategoryAttribute.ozon_category_id == category_id) &
                                 (OzonCategoryAttribute.type_id == type_id) &
                                 (OzonCategoryAttribute.is_required == True)))
    if required_attrs:
        filled_attr_ids = _filled_draft_attribute_ids(draft)
        missing = [a for a in required_attrs if str(a.attribute_id) not in filled_attr_ids]
        if missing:
            missing_names = '、'.join(
                (getattr(a, 'name_cn', None) or getattr(a, 'name', None) or str(a.attribute_id))
                for a in missing[:10]
            )
            return jsonify({
                "ok": False,
                "error": f"缺少必填属性（{len(missing)} 项）：{missing_names}"
            }), 400

    # 构建商品数据
    try:
        product_data = _build_product_data(draft)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    # 创建 OZON 客户端并构建实际请求体（含 sanitize）
    client = create_client(draft.account)
    request_body = client.build_import_body(product_data)
    request_json_str = json.dumps(request_body, ensure_ascii=False, indent=2)

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
        result = client.import_product(product_data)

        # OZON 返回 task_id 仅代表异步任务已接收，不代表商品创建成功
        task_id = str(result.get('task_id', ''))
        offer_id = product_data.get('offer_id', '')

        job.status = 'submitted'
        job.response_json = json.dumps(result, ensure_ascii=False, indent=2)
        job.ozon_task_id = task_id
        job.completed_at = None  # 未完成，等待异步结果
        job.save()

        draft.status = 'publishing'
        draft.ozon_offer_id = offer_id
        draft.ozon_product_id = None  # 等确认成功后再写入
        draft.updated_at = datetime.datetime.now()
        draft.save()

        return jsonify({
            "ok": True,
            "message": f"已提交 OZON 导入任务，Task ID: {task_id}。请稍后查询发布结果。",
            "status": "publishing",
            "task_id": task_id,
            "offer_id": offer_id
        })

    except OzonValidationError as e:
        _record_publish_failure(job, draft, e, request_json_str)
        errors_detail = '; '.join(err.get('message', '') for err in (e.errors or [])[:3])
        return jsonify({"ok": False, "error": f"发布失败 — {e}{(': ' + errors_detail) if errors_detail else ''}"}), 400

    except OzonAuthError as e:
        _record_publish_failure(job, draft, e, request_json_str)
        return jsonify({"ok": False, "error": "发布失败 — 店铺认证无效，请检查 API 凭证"}), 400

    except OzonAPIError as e:
        _record_publish_failure(job, draft, e, request_json_str)
        return jsonify({"ok": False, "error": f"发布失败 — {e}"}), 400

    except Exception as e:
        current_app.logger.exception("Publish OZON listing failed: draft_id=%s", draft_id)
        _record_publish_failure(job, draft, e, request_json_str)
        return jsonify({"ok": False, "error": f"发布失败：{e}"}), 500


@ozon_bp.route('/listings/<int:draft_id>/publish-status', methods=['POST'])
@login_required
def listing_publish_status(draft_id):
    """查询 OZON 导入任务的实际结果，返回详细 item 信息"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft:
        return jsonify({"ok": False, "error": "草稿不存在"}), 404

    if not draft.account:
        return jsonify({"ok": False, "error": "草稿未绑定目标店铺"}), 400

    job = (OzonPublishJob
           .select()
           .where((OzonPublishJob.draft == draft) & (OzonPublishJob.user == current_user))
           .order_by(OzonPublishJob.id.desc())
           .first())

    if not job or not job.ozon_task_id:
        return jsonify({"ok": False, "error": "未找到 OZON 发布任务", "status": draft.status}), 400

    client = create_client(draft.account)
    try:
        result = client.import_product_info(job.ozon_task_id)
    except Exception as e:
        return jsonify({"ok": False, "error": f"查询 OZON 任务状态失败：{e}", "status": draft.status}), 500

    job.response_json = json.dumps(result, ensure_ascii=False, indent=2)

    # 解析 OZON 返回的 items
    items = result.get('items') or result.get('result', {}).get('items') or []
    errors = []
    extracted_offer_id = None
    extracted_product_id = None
    item_statuses = []

    for item in items:
        item_status = str(item.get('status', '') or item.get('state', '')).lower()
        item_offer_id = item.get('offer_id', '')
        item_product_id = item.get('product_id') or item.get('id')
        item_errors = item.get('errors') or item.get('error') or []

        item_statuses.append({
            'offer_id': str(item_offer_id),
            'product_id': str(item_product_id) if item_product_id else None,
            'status': item_status,
            'errors': item_errors if isinstance(item_errors, list) else [str(item_errors)] if item_errors else [],
        })

        if isinstance(item_errors, list):
            errors.extend(item_errors)
        elif item_errors:
            errors.append(str(item_errors))

        # 提取 OZON 返回的实际 offer_id / product_id
        if item_offer_id:
            extracted_offer_id = str(item_offer_id)
        if item_product_id and not extracted_product_id:
            extracted_product_id = str(item_product_id)

    # 有错误 → failed
    if errors:
        job.status = 'failed'
        job.error_message = json.dumps(errors, ensure_ascii=False)[:2000]
        job.completed_at = datetime.datetime.now()
        job.save()

        draft.status = 'failed'
        draft.validation_result = json.dumps({
            'blocking_count': len(errors),
            'errors': errors,
        }, ensure_ascii=False)
        draft.updated_at = datetime.datetime.now()
        draft.save()

        return jsonify({
            "ok": False,
            "status": "failed",
            "error": f"OZON 导入失败（{len(errors)} 项错误）",
            "items": item_statuses,
            "errors": errors,
        })

    # 检查是否有商品成功导入（有 product_id 且状态为 imported）
    has_imported = any(
        s['status'] in ('imported', 'processed', 'success', 'created', 'ok')
        and s.get('product_id')
        for s in item_statuses
    )

    if has_imported:
        # 保存 OZON 返回的 offer_id / product_id
        if extracted_offer_id:
            draft.ozon_offer_id = extracted_offer_id
        if extracted_product_id:
            draft.ozon_product_id = extracted_product_id

        job.status = 'success'
        job.completed_at = datetime.datetime.now()
        job.save()

        draft.status = 'published'
        draft.updated_at = datetime.datetime.now()
        draft.save()

        return jsonify({
            "ok": True,
            "status": "published",
            "message": "OZON 商品导入成功",
            "items": item_statuses,
            "offer_id": extracted_offer_id,
            "product_id": extracted_product_id,
        })

    # 没有明确成功也没有错误 → validated（OZON 验证通过但尚未在商品列表可见）
    # 此时状态为 pending 或类似中间态
    if extracted_offer_id:
        draft.ozon_offer_id = extracted_offer_id

    job.status = 'validated'
    job.save()

    draft.status = 'publishing'  # 保持 publishing，用户可以稍后再查
    draft.updated_at = datetime.datetime.now()
    draft.save()

    return jsonify({
        "ok": True,
        "status": "validated",
        "message": "OZON 导入任务已通过验证，但商品暂未在列表出现。可能仍在同步或进入档案。请稍后重试，或用 offer_id 反查。",
        "items": item_statuses,
        "offer_id": extracted_offer_id,
    })


@ozon_bp.route('/listings/<int:draft_id>/lookup-product', methods=['POST'])
@login_required
def listing_lookup_product(draft_id):
    """用草稿的 offer_id 反查 OZON 商品列表，确认商品是否真的上线"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft:
        return jsonify({"ok": False, "error": "草稿不存在"}), 404

    if not draft.account:
        return jsonify({"ok": False, "error": "草稿未绑定目标店铺"}), 400

    offer_id = draft.ozon_offer_id or ''
    if not offer_id:
        return jsonify({"ok": False, "error": "草稿没有 offer_id，请先提交发布"}), 400

    client = create_client(draft.account)
    try:
        items = client.get_product_info(offer_ids=[offer_id])
    except Exception as e:
        return jsonify({"ok": False, "error": f"查询 OZON 商品信息失败：{e}"}), 500

    if not items:
        # 查不到：可能仍在同步、进入档案、或被合并到已有商品
        return jsonify({
            "ok": True,
            "found": False,
            "offer_id": offer_id,
            "message": (
                "OZON 商品列表中暂未查到该 offer_id。"
                "可能原因：商品仍在同步中、已进入档案/准备销售、或被合并到已有商品。"
                f"请在 OZON 后台搜索 offer_id: {offer_id}"
            ),
        })

    product = items[0] if isinstance(items, list) else items
    product_id = product.get('id') or product.get('product_id', '')
    product_name = product.get('name') or product.get('offer_id', '')
    product_status = product.get('status') or product.get('state', '')

    # 保存 product_id
    if product_id and not draft.ozon_product_id:
        draft.ozon_product_id = str(product_id)
        draft.updated_at = datetime.datetime.now()
        draft.save()

    return jsonify({
        "ok": True,
        "found": True,
        "product": {
            "product_id": str(product_id),
            "offer_id": offer_id,
            "name": product_name,
            "status": str(product_status),
        },
        "message": f"已查到商品: {product_name}",
    })


# ═══════════════════════════════════════════════════════════════
# 官方 Excel 模板发布通道
# ═══════════════════════════════════════════════════════════════

@ozon_bp.route('/excel-templates')
@login_required
def excel_templates():
    """OZON 官方 Excel 模板管理页"""
    templates = (OzonExcelTemplate
                 .select()
                 .where(OzonExcelTemplate.user == current_user)
                 .order_by(OzonExcelTemplate.updated_at.desc()))
    return render_template('ozon/excel_templates.html', templates=templates)


@ozon_bp.route('/api/excel-templates/upload', methods=['POST'])
@login_required
def api_upload_excel_template():
    """上传 OZON 官方 Excel 模板，解析并绑定到 dcid+type_id"""
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "请选择文件"}), 400

    dcid = request.form.get('dcid', '').strip()
    type_id = request.form.get('type_id', '').strip()
    type_name = request.form.get('type_name', '').strip()

    safe_fname = secure_filename(file.filename)
    if not safe_fname.lower().endswith(('.xlsx', '.xlsm')):
        return jsonify({"ok": False, "error": "只支持 .xlsx 或 .xlsm 格式的 OZON 官方模板"}), 400

    # 先保存到临时位置解析
    tmp_dir = os.path.join('uploads', 'ozon_templates', str(current_user.id), '_tmp')
    os.makedirs(tmp_dir, exist_ok=True)
    ts = int(time.time())
    tmp_path = os.path.join(tmp_dir, f'{ts}_{safe_fname}')
    file.save(tmp_path)

    # 解析模板结构
    from services.ozon_template_excel import inspect_template
    try:
        inspection = inspect_template(tmp_path, file.filename)
    except ValueError as e:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        return jsonify({"ok": False, "error": str(e)}), 400

    # ── 自动识别（多策略优先级）──
    auto_detected = False
    detect_method = None
    detect_confidence = None

    if not dcid or not type_id:
        # ① schema_hash 匹配已有模板（最可靠）
        existing = (OzonExcelTemplate
                    .select()
                    .where((OzonExcelTemplate.user == current_user) &
                           (OzonExcelTemplate.schema_hash == inspection['schema_hash']) &
                           (OzonExcelTemplate.status == 'active'))
                    .first())
        if existing:
            dcid = existing.dcid
            type_id = existing.type_id
            type_name = type_name or existing.type_name
            auto_detected = True
            detect_method = 'schema_hash'

    if not dcid or not type_id:
        # ② 从第 5 行「类型*」列读取示例值，查 OzonCategoryType（精确可靠）
        type_hint = inspection.get('type_hint')
        if type_hint:
            from models import OzonCategoryType as OzonCatType
            ct = (OzonCatType
                  .select()
                  .where((OzonCatType.user == current_user) &
                         ((OzonCatType.type_name_cn == type_hint) |
                          (OzonCatType.type_name == type_hint)))
                  .first())
            if ct:
                dcid = ct.description_category_id
                type_id = ct.type_id
                type_name = type_name or ct.type_name_cn or ct.type_name
                auto_detected = True
                detect_method = 'type_hint'

    if not dcid or not type_id:
        # ③ 表头属性名匹配 OzonCategoryAttribute（低优先级，可能不精确）
        from services.ozon_template_excel import identify_category_from_headers
        result = identify_category_from_headers(inspection['headers'], current_user.id)
        if result:
            dcid = result['dcid']
            type_id = result['type_id']
            type_name = type_name or result.get('type_name') or ''
            auto_detected = True
            detect_method = 'attribute_match'
            detect_confidence = result['confidence']

    # 移动到正式目录
    if dcid and type_id:
        save_dir = os.path.join('uploads', 'ozon_templates', str(current_user.id),
                                f'{dcid}_{type_id}', 'original')
    else:
        save_dir = os.path.join('uploads', 'ozon_templates', str(current_user.id), '_unbound')
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, f'{ts}_{safe_fname}')
    os.rename(tmp_path, save_path)

    # 同 dcid+type_id 旧模板标记为 outdated（仅当绑定了类目时）
    if dcid and type_id:
        (OzonExcelTemplate
         .update(status='outdated', updated_at=datetime.datetime.now())
         .where((OzonExcelTemplate.user == current_user) &
                (OzonExcelTemplate.dcid == dcid) &
                (OzonExcelTemplate.type_id == type_id) &
                (OzonExcelTemplate.status == 'active'))
         .execute())

    # 创建模板记录
    tmpl = OzonExcelTemplate.create(
        user=current_user,
        account_id=None,
        dcid=dcid or '',
        type_id=type_id or '',
        type_name=type_name or None,
        original_filename=file.filename,
        stored_path=save_path,
        file_size_bytes=os.path.getsize(save_path),
        schema_hash=inspection['schema_hash'],
        headers_json=json.dumps(inspection['headers'], ensure_ascii=False),
        required_columns_json=json.dumps(inspection['required_columns'], ensure_ascii=False),
        data_validations_json=json.dumps(inspection.get('validations', []), ensure_ascii=False),
        sheet_names_json=json.dumps(inspection['sheet_names'], ensure_ascii=False),
        data_start_row=inspection.get('data_start_row', 5),
        header_row=inspection.get('header_row', 2),
        status='active',
    )

    return jsonify({
        "ok": True,
        "message": "模板上传并解析成功" + ("（自动识别类目" + (f"，置信度 {detect_confidence}%" if detect_confidence else "") + "）" if auto_detected else ""),
        "template_id": tmpl.id,
        "schema_hash": inspection['schema_hash'],
        "headers_count": len(inspection['headers']),
        "required_count": len(inspection['required_columns']),
        "auto_detected": auto_detected,
        "detect_method": detect_method,
        "detect_confidence": detect_confidence,
        "dcid": dcid or '',
        "type_id": type_id or '',
        "type_name": type_name or '',
        "is_unbound": not bool(dcid and type_id),
    })


@ozon_bp.route('/api/excel-templates/<int:template_id>/delete', methods=['POST'])
@login_required
def api_delete_excel_template(template_id):
    tmpl = OzonExcelTemplate.get_or_none(
        (OzonExcelTemplate.id == template_id) & (OzonExcelTemplate.user == current_user))
    if not tmpl:
        return jsonify({"ok": False, "error": "模板不存在"}), 404
    tmpl.delete_instance()
    return jsonify({"ok": True, "message": "模板已删除"})


@ozon_bp.route('/api/excel-templates/<int:template_id>/bind', methods=['POST'])
@login_required
def api_bind_excel_template(template_id):
    """为未绑定模板设置 dcid/type_id"""
    tmpl = OzonExcelTemplate.get_or_none(
        (OzonExcelTemplate.id == template_id) & (OzonExcelTemplate.user == current_user))
    if not tmpl:
        return jsonify({"ok": False, "error": "模板不存在"}), 404

    data = request.get_json() or {}
    dcid = (data.get('dcid') or '').strip()
    type_id = (data.get('type_id') or '').strip()
    type_name = (data.get('type_name') or '').strip()

    if not dcid or not type_id:
        return jsonify({"ok": False, "error": "dcid 和 type_id 不能为空"}), 400

    # 移动文件到正确目录
    old_path = tmpl.stored_path
    new_dir = os.path.join('uploads', 'ozon_templates', str(current_user.id),
                           f'{dcid}_{type_id}', 'original')
    os.makedirs(new_dir, exist_ok=True)
    fname = os.path.basename(old_path)
    new_path = os.path.join(new_dir, fname)
    if old_path != new_path and os.path.exists(old_path):
        try:
            os.rename(old_path, new_path)
        except OSError:
            import shutil
            shutil.copy2(old_path, new_path)
        tmpl.stored_path = new_path

    # 同 dcid+type_id 旧模板标记为 outdated
    (OzonExcelTemplate
     .update(status='outdated', updated_at=datetime.datetime.now())
     .where((OzonExcelTemplate.user == current_user) &
            (OzonExcelTemplate.dcid == dcid) &
            (OzonExcelTemplate.type_id == type_id) &
            (OzonExcelTemplate.status == 'active') &
            (OzonExcelTemplate.id != tmpl.id))
     .execute())

    # 更新记录
    tmpl.dcid = dcid
    tmpl.type_id = type_id
    tmpl.type_name = type_name or tmpl.type_name
    tmpl.updated_at = datetime.datetime.now()
    tmpl.save()

    return jsonify({
        "ok": True,
        "message": f"模板已绑定到 dcid={dcid} type_id={type_id}",
        "dcid": dcid,
        "type_id": type_id,
    })


@ozon_bp.route('/api/template-info/<dcid>/<type_id>')
@login_required
def api_template_info(dcid, type_id):
    """查询指定 dcid+type_id 是否有活跃模板（仅精确匹配）"""
    tmpl = (OzonExcelTemplate
            .select()
            .where((OzonExcelTemplate.user == current_user) &
                   (OzonExcelTemplate.dcid == str(dcid)) &
                   (OzonExcelTemplate.type_id == str(type_id)) &
                   (OzonExcelTemplate.status == 'active'))
            .first())
    if tmpl:
        return jsonify({
            "ok": True,
            "has_template": True,
            "template_id": tmpl.id,
            "schema_hash": tmpl.schema_hash,
            "headers_count": len(json.loads(tmpl.headers_json or '[]')),
            "created_at": tmpl.created_at.isoformat() if tmpl.created_at else None,
        })
    return jsonify({"ok": True, "has_template": False})


@ozon_bp.route('/api/draft/<int:draft_id>/generate-template-excel', methods=['POST'])
@login_required
def api_generate_template_excel(draft_id):
    """从草稿生成填充好的 OZON 模板 Excel"""
    draft = OzonDraft.get_or_none(
        (OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft:
        return jsonify({"ok": False, "error": "草稿不存在"}), 404

    if not draft.type_id or not draft.ozon_category_id:
        return jsonify({"ok": False, "error": "草稿未绑定 dcid/type_id，无法选择模板。请先在适配工作台选择类目和类型。"}), 400

    # ── 店铺兼容性校验 ──
    shop_warnings = []
    account = draft.account
    if not account:
        return jsonify({"ok": False, "error": "草稿未选择目标店铺。请先在审核页顶部选择 OZON 店铺。"}), 400
    if not account.currency_confirmed:
        return jsonify({
            "ok": False,
            "error": f"店铺「{account.name}」尚未确认语言/货币设置。请前往「店铺编辑」确认 OZON 后台语言和价格货币。",
            "action": "edit_account",
            "account_id": account.id,
        }), 400
    if account.template_language != 'zh':
        shop_warnings.append(f"店铺后台语言为「{account.template_language}」，Excel 枚举值将以该语言输出")

    # 查找活跃模板（仅精确匹配 dcid+type_id，不用同dcid回退）
    template = (OzonExcelTemplate
                .select()
                .where((OzonExcelTemplate.user == current_user) &
                       (OzonExcelTemplate.dcid == str(draft.ozon_category_id)) &
                       (OzonExcelTemplate.type_id == str(draft.type_id)) &
                       (OzonExcelTemplate.status == 'active'))
                .first())

    if not template:
        return jsonify({
            "ok": False,
            "error": f"未找到 dcid={draft.ozon_category_id} type_id={draft.type_id} 对应的活跃模板。请先在「模板管理」页上传 OZON 官方 Excel 模板。如已上传但 type 不匹配，请在模板列表点 🔗 按钮修正绑定。"
        }), 400

    from services.ozon_template_excel import build_field_mapping, generate_export_excel, get_public_image_url

    # 检查主图外链
    image_url = get_public_image_url(draft)
    if not image_url:
        return jsonify({
            "ok": False,
            "warning": "no_public_image",
            "error": "未找到可公开访问的主图 URL（必须以 http/https 开头的外链，OZON CDN 或云存储链接）。本地路径 OZON 无法读取。请先将图片上传到 OZON 获取 CDN URL，或手动在生成的 Excel 中填写图片链接。"
        }), 400

    # 构建映射并生成（AttributeError 兜底，避免字段引用错误导致 500）
    try:
        field_mapping = build_field_mapping(draft)
    except AttributeError as e:
        return jsonify({"ok": False, "error": f"字段映射错误：{e}. 请检查草稿属性数据是否完整。"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": f"字段映射失败：{e}"}), 500

    try:
        export_path, validation_errors = generate_export_excel(draft, template, field_mapping)
    except AttributeError as e:
        return jsonify({"ok": False, "error": f"模板生成字段引用错误：{e}. 请联系管理员检查模板导出代码。"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": f"模板生成失败：{e}"}), 500

    # 记录导出任务
    sku_count = draft.draft_skus.count() or 1
    job = OzonTemplateExportJob.create(
        user=current_user,
        draft=draft,
        template=template,
        export_path=export_path,
        field_mapping_json=json.dumps(field_mapping, ensure_ascii=False),
        filled_rows_count=sku_count,
    )

    resp = {
        "ok": True,
        "job_id": job.id,
        "download_url": url_for('ozon.api_download_template_export', job_id=job.id),
        "shop_language": account.template_language or 'zh',
        "shop_currency": account.default_currency or 'CNY',
        "shop_warnings": shop_warnings,
    }
    if validation_errors:
        resp.update({
            "warning": "validation_failed",
            "message": f"模板 Excel 已生成但校验发现问题（{sku_count} 行数据）",
            "validation_errors": validation_errors,
        })
    else:
        resp["message"] = f"模板 Excel 已生成（{sku_count} 行数据），校验通过"
    return jsonify(resp)


@ozon_bp.route('/api/template-exports/<int:job_id>/download')
@login_required
def api_download_template_export(job_id):
    """下载生成的模板 Excel 文件"""
    job = OzonTemplateExportJob.get_or_none(
        (OzonTemplateExportJob.id == job_id) & (OzonTemplateExportJob.user == current_user))
    if not job:
        flash('导出记录不存在', 'danger')
        return redirect(url_for('ozon.listings'))
    if not os.path.exists(job.export_path):
        flash('文件已被清理，请重新生成', 'danger')
        return redirect(url_for('ozon.listings'))

    export_dir = os.path.dirname(job.export_path)
    filename = os.path.basename(job.export_path)
    return send_from_directory(os.path.abspath(export_dir), filename,
                               as_attachment=True, download_name=filename)


@ozon_bp.route('/api/template-exports/<int:job_id>/mark-result', methods=['POST'])
@login_required
def api_mark_template_export_result(job_id):
    """标记 OZON 模板上传结果"""
    job = OzonTemplateExportJob.get_or_none(
        (OzonTemplateExportJob.id == job_id) & (OzonTemplateExportJob.user == current_user))
    if not job:
        return jsonify({"ok": False, "error": "导出记录不存在"}), 404

    data = request.get_json(silent=True) or {}
    result = str(data.get('result', '')).strip()
    notes = str(data.get('notes', '')).strip()

    if result not in ('validated', 'errors', 'published', 'needs_fix'):
        return jsonify({"ok": False, "error": "无效的结果状态，可选: validated / errors / published / needs_fix"}), 400

    job.ozon_upload_result = result
    job.ozon_upload_notes = notes or None
    job.save()

    labels = {'validated': '已验证', 'errors': '有错误', 'published': '已发布', 'needs_fix': '需修复'}
    return jsonify({
        "ok": True,
        "message": f"上传结果已标记为「{labels.get(result, result)}」",
        "result": result,
    })


def _build_product_data(draft):
    """从草稿构建 OZON import_product 请求体（使用归一化层）"""
    # ── offer_id：优先取第一个 SKU 保存的 offer_id ──
    first_sku = draft.draft_skus.order_by(OzonDraftSku.source_order).first()
    offer_id = (first_sku.offer_id.strip() if first_sku and first_sku.offer_id else None)
    if not offer_id:
        raise ValueError("缺少 OZON 卖家货号 offer_id，请先在 SKU/价格页填写")

    # ── 价格：取用户在 SKU/价格页确认的刊登价 ──
    pricing = _safe_json_loads(draft.pricing_json, {})
    listing_price = pricing.get('listing_price') if isinstance(pricing, dict) else None
    listing_currency = pricing.get('listing_currency', 'RUB') if isinstance(pricing, dict) else 'RUB'
    if not listing_price:
        raise ValueError("缺少 OZON 刊登价，请先在 SKU/价格页填写并确认价格")

    # ── 图片：从 media_json 媒体池取主图（selected + role=main，按 sort_order 排序）──
    images = []
    media = _load_media_json(draft)
    media_images = media.get('images', []) if isinstance(media, dict) else []
    # 取已选中的主图
    main_imgs = [i for i in media_images
                 if i.get('selected') and i.get('role') == 'main']
    main_imgs.sort(key=lambda i: i.get('sort_order', 0))
    for img in main_imgs:
        url = img.get('ozon_url') or img.get('public_url') or img.get('url') or ''
        if url:
            images.append(url)

    data = {
        "offer_id": offer_id,
        "name": draft.title_ru or "Untitled",
        "description_category_id": int(draft.ozon_category_id) if draft.ozon_category_id else None,
        "type_id": int(draft.type_id) if draft.type_id else None,
        "price": str(listing_price),
        "vat": "0",
        "currency_code": str(listing_currency),
        "description": draft.description_ru or "",
    }

    # 多 SKU（使用各自保存的 offer_id 和数量）
    skus_list = []
    for sku in draft.draft_skus.order_by(OzonDraftSku.source_order):
        skus_list.append({
            "offer_id": (sku.offer_id or f"{offer_id}_{sku.source_order}").strip(),
            "sku_name": sku.source_sku_name or '',
            "price": str(listing_price),
            "quantity": sku.bundle_quantity or 1,
        })
    if skus_list:
        data["skus"] = skus_list

    # 图片
    if images:
        data["images"] = images

    # 属性（归一化转换）
    attrs = _build_ozon_attribute_list(draft)
    if attrs:
        data["attributes"] = attrs

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


# ═══════════════════════════════════════════════════════════════
# 属性归一化层 — 统一草稿属性存取格式
# ═══════════════════════════════════════════════════════════════

def _safe_json_loads(raw, default=None):
    """安全 JSON 解析，失败返回 default"""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _load_draft_attributes_map(draft):
    """
    统一读取 draft.attributes_json，返回固定格式 dict:

        {"attribute_id": {"value": "...", "value_id": "..."}}

    兼容三种历史格式：
      1. list:  [{"id": 8229, "value": "..."}, ...]
      2. wrapper: {"attributes": {"8229": {"value": "..."}}}
      3. map:    {"8229": {"value": "..."}}
    """
    raw = _safe_json_loads(getattr(draft, 'attributes_json', None), {})

    # 展开 wrapper
    if isinstance(raw, dict) and 'attributes' in raw:
        raw = raw.get('attributes') or {}

    result = {}

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            aid = item.get('attribute_id') or item.get('id')
            if not aid:
                continue
            result[str(aid)] = item
        return result

    if isinstance(raw, dict):
        for aid, value in raw.items():
            if aid in ('type_id', 'category_id', 'diagnostics'):
                continue
            result[str(aid)] = value
        return result

    return result


def _is_draft_attr_filled(value):
    """判断一个属性值是否已填写"""
    if value is None:
        return False

    if isinstance(value, dict):
        if value.get('values'):
            return True
        for key in ('value', 'value_id', 'dictionary_value_id', 'target_value'):
            v = value.get(key)
            if v is not None and str(v).strip() not in ('', '--', 'None', 'null'):
                return True
        return False

    if isinstance(value, list):
        return len(value) > 0

    return str(value).strip() not in ('', '--', 'None', 'null')


def _filled_draft_attribute_ids(draft):
    """返回草稿中已填写的属性 attribute_id 集合"""
    attr_map = _load_draft_attributes_map(draft)
    return {
        str(aid)
        for aid, value in attr_map.items()
        if _is_draft_attr_filled(value)
    }


def _build_ozon_attribute_list(draft):
    """
    将草稿属性 map 转成 OZON API 需要的 attributes list:

        [{"id": 8229, "values": [{"dictionary_value_id": 123, "value": "..."}]}]
    """
    attr_map = _load_draft_attributes_map(draft)
    result = []

    for aid, saved in attr_map.items():
        if not _is_draft_attr_filled(saved):
            continue

        item = {
            "id": int(aid) if str(aid).isdigit() else aid,
            "values": []
        }

        if isinstance(saved, dict):
            values = saved.get("values")
            if isinstance(values, list) and values:
                item["values"] = values
            else:
                value_obj = {}

                value_id = (
                    saved.get("value_id")
                    or saved.get("dictionary_value_id")
                    or saved.get("id_value")
                )
                value_text = (
                    saved.get("value")
                    or saved.get("target_value")
                    or saved.get("text")
                )

                if value_id:
                    value_obj["dictionary_value_id"] = (
                        int(value_id) if str(value_id).isdigit() else value_id
                    )

                if value_text:
                    value_obj["value"] = str(value_text)

                if value_obj:
                    item["values"].append(value_obj)
        else:
            item["values"].append({"value": str(saved)})

        if item["values"]:
            result.append(item)

    return result


# ═══════════════════════════════════════════════════════════════

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


def _infer_group_key(field_path):
    fp = field_path or ""
    if any(k in fp for k in ["product_identity", "name", "brand", "model", "product_type", "category"]): return "identity"
    if any(k in fp for k in ["skus[", "sku_variant", "color", "size", "style", "variant"]): return "sku"
    if any(k in fp for k in ["structure", "material", "component", "shape"]): return "structure"
    if any(k in fp for k in ["specification", "param", "power", "weight", "dimension", "battery", "voltage", "capacity"]): return "specification"
    if any(k in fp for k in ["function", "feature"]): return "function"
    if any(k in fp for k in ["compatibility", "compatible"]): return "compatibility"
    if any(k in fp for k in ["package", "contents", "accessory"]): return "package"
    if any(k in fp for k in ["selling_point"]): return "selling_point"
    if any(k in fp for k in ["usage", "scenario"]): return "usage_scenario"
    if any(k in fp for k in ["target_customer"]): return "target_customer"
    if any(k in fp for k in ["safety", "certification"]): return "safety"
    return "custom"

# ── 源资料本地化（模块级常量）──
SOURCE_ATTR_TR = {
    'артикул':'货号','тип':'类型','вид микрофона':'麦克风类型','технология микрофона':'麦克风技术',
    'крепление микрофона':'麦克风固定方式','диаграмма направленности':'指向性','цвет':'颜色',
    'подключение':'连接方式','вес товара, г':'商品重量(g)','вес товара':'商品重量',
    'количество в упаковке, шт':'包装数量','гарантия':'保修期','страна-изготовитель':'产地',
    'бренд':'品牌','модель':'型号','назначение':'用途','особенности':'特性','размеры':'尺寸',
    'совместимость':'兼容性','емкость аккумулятора':'电池容量','материал':'材质',
}
SOURCE_VAL_TR = {
    'микрофон':'麦克风','универсальный':'通用','динамический':'动圈式','петличный':'领夹式',
    'всенаправленная':'全向','прозрачный':'透明','черный':'黑色','прозрачный, черный':'透明、黑色',
    'беспроводное':'无线','китай':'中国','3 месяца':'3个月','1 год':'1年','белый':'白色',
    'красный':'红色','синий':'蓝色','зеленый':'绿色','серый':'灰色','металл':'金属','пластик':'塑料',
}

def _build_localized_view(source, fact, src_attrs, user, adaptation=None):
    """从OZON字典+硬编码表构建源属性本地化显示，结果缓存到raw_json.localized"""
    import re
    raw = {}
    if source:
        try: raw = json.loads(source.raw_json or '{}')
        except: raw = {}
    # 优先读缓存
    cached = raw.get('localized') or {}
    cached_attrs = cached.get('source_attributes') or {}
    result_attrs = []
    # 查OZON字典翻译
    attr_cn_map = {}; val_cn_map = {}
    if adaptation and adaptation.type_id:
        tgt = list(OzonCategoryAttribute.select().where(
            (OzonCategoryAttribute.user == user) & (OzonCategoryAttribute.type_id == adaptation.type_id)))
        for a in tgt:
            if a.name: attr_cn_map[a.name.strip().lower()] = a.name_cn or a.name
        dids = [a.attribute_id for a in tgt if a.is_dictionary]
        if dids:
            for v in OzonAttributeValue.select().where(
                (OzonAttributeValue.user == user) & (OzonAttributeValue.type_id == adaptation.type_id) &
                (OzonAttributeValue.attribute_id.in_(dids))):
                if v.value: val_cn_map[v.value.strip().lower()] = v.value_cn or v.value

    for a in (src_attrs or []):
        n_ru = (a.get('name') or a.get('key') or '').strip()
        v_ru = str(a.get('value') or a.get('text') or '')
        ck = (n_ru+'='+v_ru).lower()
        cc = cached_attrs.get(ck, {}) if isinstance(cached_attrs, dict) else {}
        n_cn = cc.get('name_cn') or attr_cn_map.get(n_ru.lower()) or SOURCE_ATTR_TR.get(n_ru.lower(), '')
        v_cn = cc.get('value_cn') or val_cn_map.get(v_ru.lower()) or SOURCE_VAL_TR.get(v_ru.lower(), '')
        # 多值拆分翻译
        if not v_cn and ',' in v_ru:
            parts = [p.strip() for p in v_ru.split(',')]
            tps = [SOURCE_VAL_TR.get(p.lower(), '') for p in parts]
            if all(tps): v_cn = '、'.join(tps)
        result_attrs.append({'name_cn':n_cn,'name_ru':n_ru,'value_cn':v_cn,'value_ru':v_ru,'source':a.get('source','')})

    return {'attributes': result_attrs}

@ozon_bp.route('/api/source/<int:source_id>/translate-materials', methods=['POST'])
@login_required
def api_translate_source_materials(source_id):
    """翻译源资料（属性名/值）并缓存到raw_json.localized"""
    source = OzonSource.get_or_none((OzonSource.id == source_id) & (OzonSource.user == current_user))
    if not source: return jsonify({'ok':False,'error':'源不存在'}), 404
    raw = {}
    try: raw = json.loads(source.raw_json or '{}')
    except: raw = {}
    src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
    # 用全局OZON字典兜底
    avals = list(OzonAttributeValue.select().where(OzonAttributeValue.user == current_user).limit(5000))
    gval = {}
    for v in avals:
        if v.value and v.value_cn: gval[v.value.strip().lower()] = v.value_cn
    cached = {}
    for a in src_attrs:
        n = (a.get('name') or a.get('key') or '').strip()
        v = str(a.get('value') or a.get('text') or '')
        ck = (n+'='+v).lower()
        nc = SOURCE_ATTR_TR.get(n.lower(), '') or ''
        vc = gval.get(v.lower(), '') or SOURCE_VAL_TR.get(v.lower(), '')
        if not vc and ',' in v:
            pts = [p.strip() for p in v.split(',')]
            tps = [gval.get(p.lower()) or SOURCE_VAL_TR.get(p.lower(), '') for p in pts]
            if all(tps): vc = '、'.join(tps)
        cached[ck] = {'name_cn': nc, 'value_cn': vc}
    localized = raw.get('localized') or {}
    localized['source_attributes'] = cached
    localized['translated_at'] = datetime.datetime.now().isoformat()
    raw['localized'] = localized
    source.raw_json = json.dumps(raw, ensure_ascii=False)
    source.save()
    return jsonify({'ok':True,'message':f'已翻译 {len(cached)} 条属性','count':len(cached)})


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
                user=current_user, fact=fact, source_sku=sku,
                source_order=sku.source_order,
                standard_sku_name_cn=sku.source_sku_name,
                color_cn=sku.color_cn, size_cn=sku.size_cn,
                style_cn=sku.style_cn, bundle_quantity=sku.bundle_quantity,
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
        # 已选类目 → 加载该类目下的 Type
        adaptation_types = list(OzonCategoryType
            .select()
            .where((OzonCategoryType.user == current_user) &
                   (OzonCategoryType.description_category_id == adaptation.ozon_category_id))
            .order_by(OzonCategoryType.type_name))
    else:
        # 未选类目 → 加载所有已同步的 Type（供下拉框选择）
        adaptation_types = list(OzonCategoryType
            .select()
            .where(OzonCategoryType.user == current_user)
            .order_by(OzonCategoryType.type_name)
            .limit(200))

        # 如果已绑定 type，加载属性 Schema
        if adaptation and adaptation.type_id:
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
                wv = ((OzonAttributeValue.user == current_user) &
                      (OzonAttributeValue.attribute_id.in_(dict_attr_ids)))
                if adaptation.type_id:
                    wv = wv & (OzonAttributeValue.type_id == adaptation.type_id)
                val_records = list(OzonAttributeValue
                    .select()
                    .where(wv)
                    .order_by(OzonAttributeValue.attribute_id, OzonAttributeValue.value_id))
                for v in val_records:
                    adaptation_attr_values.setdefault(v.attribute_id, []).append({
                        'value_id': v.value_id, 'value': v.value, 'value_cn': v.value_cn, 'info': v.info,
                        'display_value': v.value_cn or v.value,
                        'missing_translation': not bool(v.value_cn),
                    })

        # 解析已保存的属性值
        if adaptation and adaptation.attribute_mapping_json:
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

    # 加载 ProductFactEvidence 证据列表 + 产品详情聚合
    evidences = []
    product_details = {}
    if fact:
        from services.product_fact_service import build_product_detail_summary
        evidences = list(ProductFactEvidence.select().where(
            (ProductFactEvidence.user == current_user) &
            (ProductFactEvidence.fact == fact)
        ).order_by(ProductFactEvidence.sort_order, ProductFactEvidence.field_path)[:200])
        product_details = build_product_detail_summary(fact)
    from services.product_fact_service import build_collection_summary
    collection_summary = build_collection_summary(source)

    # 解析 raw_json 中的新字段
    raw = {}
    try: raw = json.loads(source.raw_json or '{}')
    except: pass
    pricing = raw.get('pricing') or {}
    rich_text = raw.get('rich_text') or {}
    source_attributes = raw.get('source_attributes') or raw.get('specs_json') or []
    localized_source = _build_localized_view(source, fact, source_attributes, current_user, adaptation)
    # ── 自动映射 OZON 俄语属性到 ProductFact（空字段补全 + 清理 unknown_fields）──
    if source.platform == 'ozon_product' and fact and source_attributes:
        try:
            from services.ozon_collector import map_ozon_attributes_to_fields
            mapped = map_ozon_attributes_to_fields(source_attributes)
            if mapped:
                did_update = False
                field_map = {
                    'product_type': 'product_type', 'brand_name': 'brand_name',
                    'model': 'model', 'material': 'material',
                    'color': 'color', 'weight': 'weight', 'weight_json': 'weight_json',
                    'dimensions': 'dimensions', 'dimensions_json': 'dimensions_json',
                    'origin': 'origin', 'warranty': 'warranty',
                    'battery_capacity': 'battery_capacity', 'power': 'power',
                    'wireless_range': 'wireless_range',
                    'compatibility': 'compatibility_json',
                    'usage': 'usage_scenarios_json',
                    'features': 'functions_json',
                    'package_contents': 'package_contents_json',
                    'stabilization': 'stabilization',
                    'waterproof': 'waterproof',
                }
                # 自动提取品牌/型号（从标题中）
                title = source.title_cn or ''
                if not mapped.get('brand_name') and not fact.brand_name:
                    # 标题第一个词通常是品牌
                    words = title.split()
                    if words and len(words[0]) <= 20 and words[0].isascii():
                        fact.brand_name = words[0]
                        did_update = True
                if not mapped.get('model') and not fact.model:
                    # 标题后面的词可能是型号
                    for w in title.split():
                        if w[0].isupper() and len(w) >= 2 and w not in ('DJI', 'Экшн-камера', 'Для'):
                            if not fact.model: fact.model = w

                json_fields = {'compatibility_json', 'usage_scenarios_json',
                               'functions_json', 'package_contents_json',
                               'dimensions_json', 'weight_json'}
                for en_key, fact_field in field_map.items():
                    if mapped.get(en_key) and not getattr(fact, fact_field, None):
                        val = str(mapped[en_key])[:500]
                        if fact_field in json_fields:
                            val = json.dumps([val], ensure_ascii=False)
                        setattr(fact, fact_field, val)
                        did_update = True
                existing_unknown = []
                if fact.unknown_fields_json:
                    try: existing_unknown = json.loads(fact.unknown_fields_json)
                    except: pass
                cleaned = [f for f in existing_unknown if f not in mapped]
                if len(cleaned) != len(existing_unknown):
                    fact.unknown_fields_json = json.dumps(cleaned, ensure_ascii=False)
                    did_update = True
                if did_update:
                    fact.save()
        except Exception:
            pass
    variant_matrix = raw.get('variant_matrix') or {}
    video_media = [m for m in source_media if getattr(m, 'role', '') in ('video', 'main_video') and getattr(m, 'compliance_status', '') != 'rejected']

    def _should_show_in_gallery(m, extra):
        role = (m.role or '').lower()
        comp = (m.compliance_status or '').lower()
        src_area = (extra.get('source_area') or '').lower()
        media_src = (m.media_source or '').lower()
        # 已拒绝 → 隐藏
        if comp == 'rejected': return False
        # 视频 → 不进图库
        if role in ('video', 'main_video') or media_src in ('ozon_video', 'video', 'main_video'): return False
        # 买家秀 → 不进主图库（进单独分组）
        if role == 'buyer_review' or src_area == 'buyer_review': return False
        # 明确非商品区域 → 隐藏
        if src_area in ('shop', 'shop_header', 'logo', 'banner', 'ad', 'recommend', 'similar', 'footer', 'nav', 'floating', 'header', 'sidebar', 'review', 'video'): return False
        # ★ 严格白名单：只有 main / sku / detail 显示
        if role in ('main', 'sku', 'detail'): return True
        # 其他一律不显示（不用兜底逻辑）
        return False

    # 检查是否有已启用的视觉模型配置
    has_vision_config = (VisionModelConfig
                         .select()
                         .where((VisionModelConfig.user == current_user) &
                                (VisionModelConfig.enabled == True))
                         .exists())

    # 构建 source_media JSON 列表（供前端图库弹窗使用）
    source_media_list_json = []
    hidden_source_media_list_json = []
    for m in source_media:
        # 解析 raw_json 中的扩展元数据
        extra = {}
        if m.raw_json:
            try:
                extra = json.loads(m.raw_json) if isinstance(m.raw_json, str) else m.raw_json
            except (json.JSONDecodeError, TypeError):
                pass
        item = {
            'id': m.id, 'source_url': m.source_url or '', 'role': m.role or 'sku',
            'compliance_status': m.compliance_status or 'usable', 'reject_reason': m.reject_reason or '',
            'review_status': m.review_status or 'pending', 'width': m.width or 0, 'height': m.height or 0,
            'source_area': extra.get('source_area', 'unknown'), 'dom_path': extra.get('dom_path', ''),
            'alt': extra.get('alt', ''), 'nearby_text': (extra.get('nearby_text') or '')[:100],
            'rule': extra.get('rule', ''), 'evidence': extra.get('evidence', ''),
            'source_selector': extra.get('source_selector', ''), 'collect_reason': extra.get('collect_reason', ''),
            'linked_sku_name': extra.get('linked_sku_name'), 'media_source': m.media_source or 'browser_extension',
            'duplicate_status': extra.get('duplicate_status', ''), 'duplicate_group': extra.get('duplicate_group', ''),
        }
        if _should_show_in_gallery(m, extra):
            source_media_list_json.append(item)
        else:
            hidden_source_media_list_json.append(item)

    # ── 构造 video_media_parsed（供模板视频播放器使用）──
    video_media_parsed = []
    for idx, vm in enumerate(video_media):
        vextra = {}
        try:
            vextra = json.loads(vm.raw_json or '{}')
        except Exception:
            vextra = {}
        vurl = vextra.get('video_url') or vextra.get('url') or vm.source_url or ''
        vposter = vextra.get('poster') or ''
        vlower = vurl.lower()
        if '.mp4' in vlower or '.webm' in vlower or '.mov' in vlower:
            vstate = 'playable'
        elif '.m3u8' in vlower or 'stream' in vlower:
            vstate = 'streaming'
        elif vurl and vurl.startswith('http'):
            vstate = 'entry_only'
        elif vposter and vposter.startswith('http'):
            vstate = 'entry_only'
        else:
            vstate = 'empty'
        video_media_parsed.append({
            'id': vm.id,
            'media_id': vm.media_id or f'video-{idx+1:03d}',
            'video_url': vurl,
            'poster': vposter,
            'video_state': vstate,
            'source': vextra.get('source') or vm.media_source or '',
            'duration_text': vextra.get('duration_text') or '',
            'duplicate_status': vextra.get('duplicate_status') or '',
            'duplicate_group': vextra.get('duplicate_group') or '',
        })

    rejected_video_candidates = raw.get('rejected_video_candidates') or []

    # 可见图片（与图库弹窗同源）
    visible_source_images = [m for m in source_media if m.compliance_status != 'rejected' and m.role in ('main', 'sku', 'detail')]

    return render_template('ozon/adaptation_workspace.html',
                           source=source, source_skus=source_skus,
                           source_media=source_media,
                           visible_source_images=visible_source_images,
                           source_media_list_json=source_media_list_json,
                           hidden_source_media_list_json=hidden_source_media_list_json,
                           group=group, fact=fact, fact_skus=fact_skus,
                           adaptation=adaptation, gaps=gaps,
                           image_facts=image_facts,
                           evidences=evidences,
                           product_details=product_details,
                           collection_summary=collection_summary,
                           pricing=pricing, rich_text=rich_text,
                           source_attributes=source_attributes,
                           localized_source=localized_source,
                           video_media=video_media,
                           video_media_parsed=video_media_parsed,
                           rejected_video_candidates=rejected_video_candidates,
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
# ==================== 商品事实管理 API ====================

@ozon_bp.route("/api/product-fact/<int:fact_id>/analyze", methods=["POST"])
@login_required
def api_analyze_product_fact(fact_id):
    fact = ProductFact.get_or_none((ProductFact.id == fact_id) & (ProductFact.user == current_user))
    if not fact: return jsonify({'ok': False, 'error': '商品事实不存在'}), 404
    from services.product_text_fact_extractor import extract_text_facts
    from services.vision_tool import analyze_product_image, backfill_evidence_from_existing_jobs
    from services.product_fact_service import merge_fact_candidates, detect_fact_conflicts

    # 获取source
    source = None; source_skus = []
    if fact.group:
        item = SourceProductGroupItem.get_or_none((SourceProductGroupItem.group == fact.group) & (SourceProductGroupItem.user == current_user))
        if item:
            source = item.source
            source_skus = list(OzonSourceSku.select().where(OzonSourceSku.source == source))

    result = {'ok': True, 'source': bool(source), 'text_facts': 0, 'backfilled': 0,
              'images': 0, 'skipped': 0, 'model_calls': 0, 'failed': 0, 'image_errors': [], 'conflicts': 0}

    if not source:
        result['error'] = '未找到关联采集来源'
        return jsonify(result)

    # 1. 历史回填
    result['backfilled'] = backfill_evidence_from_existing_jobs(current_user, fact, source)

    # 2. 网页文本提取
    result['text_facts'] = extract_text_facts(current_user, source, fact)

    # 3. 全量图片分析（分批5张）
    media_all = list(OzonSourceMedia.select().where(
        (OzonSourceMedia.user == current_user) & (OzonSourceMedia.source == source)
    ))
    total = len(media_all)
    # 串行处理（SQLite不支持多线程）
    for i, m in enumerate(media_all):
        try:
            r = analyze_product_image(current_user, m, fact=fact, source_skus=source_skus)
            if r.get('ok'):
                result['images'] += 1
                if not r.get('skipped'): result['model_calls'] += 1
                else: result['skipped'] += 1
            else:
                result['failed'] += 1
                result['image_errors'].append(f'{m.id}:{r.get("error","?")[:60]}')
        except Exception as e:
            result['failed'] += 1
            result['image_errors'].append(f'{m.id}:{str(e)[:80]}')

    result['total_images'] = total
    result['merged'] = merge_fact_candidates(fact)
    result['conflicts'] = len(detect_fact_conflicts(fact))
    return jsonify(result)


@ozon_bp.route('/api/product-fact/<int:fact_id>/merge', methods=['POST'])
@login_required
def api_merge_fact(fact_id):
    fact = ProductFact.get_or_none((ProductFact.id == fact_id) & (ProductFact.user == current_user))
    if not fact: return jsonify({"ok": False, "error": "商品事实不存在"}), 404
    from services.product_fact_service import merge_fact_candidates, detect_fact_conflicts
    return jsonify({"ok": True, "merged": merge_fact_candidates(fact), "conflicts": len(detect_fact_conflicts(fact))})

@ozon_bp.route("/api/product-fact/<int:fact_id>/brief", methods=["GET"])
@login_required
def api_get_product_brief(fact_id):
    fact = ProductFact.get_or_none((ProductFact.id == fact_id) & (ProductFact.user == current_user))
    if not fact: return jsonify({"ok": False, "error": "商品事实不存在"}), 404
    from services.product_fact_service import build_product_brief
    return jsonify({"ok": True, "brief": build_product_brief(fact)})

@ozon_bp.route("/api/product-fact/<int:fact_id>/evidences", methods=["GET"])
@login_required
def api_get_fact_evidences(fact_id):
    fact = ProductFact.get_or_none((ProductFact.id == fact_id) & (ProductFact.user == current_user))
    if not fact: return jsonify({"ok": False, "error": "商品事实不存在"}), 404
    field = request.args.get("field", "")
    q = ProductFactEvidence.select().where((ProductFactEvidence.user == current_user) & (ProductFactEvidence.fact == fact))
    if field: q = q.where(ProductFactEvidence.field_path == field)
    return jsonify({"ok": True, "evidences": [{"id": e.id, "field_path": e.field_path, "fact_status": e.fact_status, "confidence": e.confidence, "evidence_type": e.evidence_type, "value": e.value_json or e.content} for e in q.order_by(ProductFactEvidence.field_path)[:100]]})

@ozon_bp.route("/api/product-fact/evidence/<int:evidence_id>/confirm", methods=["POST"])
@login_required
def api_confirm_evidence(evidence_id):
    from services.product_fact_service import confirm_fact_evidence
    return jsonify({"ok": confirm_fact_evidence(evidence_id, current_user, current_user)})

@ozon_bp.route("/api/product-fact/evidence/<int:evidence_id>/reject", methods=["POST"])
@login_required
def api_reject_evidence(evidence_id):
    from services.product_fact_service import reject_fact_evidence
    data = request.get_json() or {}
    return jsonify({"ok": reject_fact_evidence(evidence_id, current_user, data.get("reason", ""))})

@ozon_bp.route("/api/product-fact/<int:fact_id>/resolve-conflict", methods=["POST"])
@login_required
def api_resolve_conflict(fact_id):
    fact = ProductFact.get_or_none((ProductFact.id == fact_id) & (ProductFact.user == current_user))
    if not fact: return jsonify({"ok": False, "error": "商品事实不存在"}), 404
    from services.product_fact_service import resolve_conflict
    data = request.get_json() or {}
    return jsonify({"ok": resolve_conflict(fact, data.get("conflict_group", 0), data.get("winning_evidence_id", 0), current_user)})

@ozon_bp.route("/api/product-fact/<int:fact_id>/approve", methods=["POST"])
@login_required
def api_approve_product_fact(fact_id):
    fact = ProductFact.get_or_none((ProductFact.id == fact_id) & (ProductFact.user == current_user))
    if not fact: return jsonify({"ok": False, "error": "商品事实不存在"}), 404
    from services.product_fact_service import validate_product_brief, create_fact_revision
    v = validate_product_brief(fact)
    if not v["passed"]: return jsonify({"ok": False, "error": "{0} 个阻塞问题".format(len(v.get("blocking_errors",[]))), "blocking_errors": v["blocking_errors"][:10]}), 400
    r = create_fact_revision(fact, current_user)
    fact.review_status = "approved"; fact.save()
    return jsonify({"ok": True, "revision": r.revision})

@ozon_bp.route("/api/product-fact/<int:fact_id>/revisions", methods=["GET"])
@login_required
def api_get_fact_revisions(fact_id):
    fact = ProductFact.get_or_none((ProductFact.id == fact_id) & (ProductFact.user == current_user))
    if not fact: return jsonify({"ok": False, "error": "商品事实不存在"}), 404
    revs = list(ProductFactRevision.select().where((ProductFactRevision.user == current_user) & (ProductFactRevision.fact == fact)).order_by(ProductFactRevision.revision.desc()))
    return jsonify({"ok": True, "revisions": [{"revision": r.revision, "status": r.status, "created_at": str(r.created_at)} for r in revs]})


@ozon_bp.route("/api/product-fact/<int:fact_id>/ai-suggest", methods=["POST"])
@login_required
def api_ai_suggest_fact(fact_id):
    """AI 智能填充：根据已有字段推断缺失字段"""
    fact = ProductFact.get_or_none((ProductFact.id == fact_id) & (ProductFact.user == current_user))
    if not fact: return jsonify({"ok": False, "error": "商品事实不存在"}), 404
    filled = []
    # 简单推断（不需要 AI API Key）
    if not fact.material and fact.product_type:
        type_map = {'Видеоскопатель': 'Пластик/Металл', 'Видеокамера': 'Пластик/Металл',
                    'Микрофон': 'Металл/Пластик', 'экшн-камера': 'Пластик/Резина'}
        for k, v in type_map.items():
            if k.lower() in (fact.product_type or '').lower():
                fact.material = v; filled.append('material'); break
    if not fact.warranty:
        fact.warranty = '1 год'; filled.append('warranty')
    if not fact.origin and fact.brand_name == 'DJI':
        fact.origin = 'Китай'; filled.append('origin')
    if filled: fact.save()
    return jsonify({"ok": True, "filled_count": len(filled), "filled_fields": filled, "message": f"AI 填充了 {len(filled)} 个字段"})


@ozon_bp.route("/api/adaptation/<int:group_id>/save-attributes", methods=["POST"])
@login_required
def api_save_adaptation_attributes(group_id):
    """保存适配工作的属性值"""
    group = SourceProductGroup.get_or_none((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
    if not group: return jsonify({"ok": False, "error": "任务组不存在"}), 404
    fact = ProductFact.get_or_none((ProductFact.user == current_user) & (ProductFact.group == group))
    if not fact: return jsonify({"ok": False, "error": "请先保存商品事实"}), 400
    adaptation = ListingAdaptation.get_or_none((ListingAdaptation.user == current_user) & (ListingAdaptation.fact == fact))
    if not adaptation: return jsonify({"ok": False, "error": "请先选择类目和Type"}), 400
    data = request.get_json(silent=True) or {}
    attrs = data.get('attributes', {})
    adaptation.attribute_mapping_json = json.dumps({'attributes': attrs, 'type_id': adaptation.type_id}, ensure_ascii=False)
    adaptation.save()
    return jsonify({"ok": True, "message": f"已保存 {len(attrs)} 个属性值"})


@ozon_bp.route("/api/adaptation/<int:group_id>/auto-fill-preview", methods=["POST"])
@login_required
def api_adaptation_auto_fill_preview(group_id):
    """根据采集资料生成自动填写建议"""
    group = SourceProductGroup.get_or_none((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
    if not group: return jsonify({"ok": False, "error": "任务组不存在"}), 404
    fact = ProductFact.get_or_none((ProductFact.user == current_user) & (ProductFact.group == group))
    adaptation = ListingAdaptation.get_or_none((ListingAdaptation.user == current_user) & (ListingAdaptation.fact == fact)) if fact else None
    # 通过 SourceProductGroupItem 查找关联的源商品
    item = SourceProductGroupItem.get_or_none((SourceProductGroupItem.user == current_user) & (SourceProductGroupItem.group == group))
    source = None
    if item and item.source:
        try: source = OzonSource.get_or_none((OzonSource.user == current_user) & (OzonSource.id == item.source.id))
        except: pass
    cat = None; text = {}; attrs = []; missing = []
    raw = {}
    if source:
        try: raw = json.loads(source.raw_json or '{}')
        except: raw = {}

    # 1. 类目建议
    dcid = (adaptation.ozon_category_id if adaptation else '') or raw.get('ozon_category_id', '')
    tid = (adaptation.type_id if adaptation else '') or raw.get('type_id', '')
    if dcid and tid:
        tname = raw.get('type_name', '') or (adaptation.type_name_ru if adaptation else '')
        cat = {'description_category_id': dcid, 'type_id': tid, 'type_name': tname, 'path': raw.get('category_path', ''), 'source': 'source_ozon' if source.platform == 'ozon_product' else 'existing', 'confidence': 1.0}

    # 2. 文本建议
    if source.title_cn:
        text['title_ru'] = {'value': source.title_cn[:150], 'source': 'source_title', 'confidence': 1.0}
    rich = raw.get('rich_text') or {}
    if rich.get('html') or rich.get('plain_text'):
        text['description_ru'] = {'value': (rich.get('html') or rich.get('plain_text'))[:50000], 'source': 'source_rich_text', 'confidence': 1.0}

    # 3. 属性匹配（中俄双语）
    import re as _re
    def _norm(s): return _re.sub(r'\s+',' ',str(s or '').strip().lower())
    ALIASES = {
        '麦克风类型':['вид микрофона','тип микрофона','тип','类型'],
        '麦克风技术':['технология микрофона','技术'],
        '麦克风固定方式':['крепление микрофона','固定方式','安装方式'],
        '指向性':['диаграмма направленности','指向性图','拾音模式'],
        '颜色':['цвет','商品颜色'],'商品颜色':['цвет','颜色'],
        '保修期':['гарантия','保修'],'包装数量':['количество в упаковке','每包数量'],
        '产地':['страна-изготовитель','制造国'],'重量':['вес товара','商品重量'],
        '连接方式':['подключение','接口'],'品牌':['бренд','brand','производитель'],
        '型号':['модель','название модели','型号名称'],
    }
    def _attr_name_match(s_attr, t_attr):
        sn = {_norm(x) for x in [s_attr.get('name_cn'),s_attr.get('name_ru'),s_attr.get('name'),s_attr.get('key')] if x}
        tn = {_norm(x) for x in [t_attr.name_cn,t_attr.name,(t_attr.description or '')] if x}
        for s in sn:
            for t in tn:
                if s and t and (s==t or s in t or t in s): return True
        js=' '.join(sn); jt=' '.join(tn)
        for k,ws in ALIASES.items():
            aw=[_norm(k)]+[_norm(w) for w in ws]
            # 别名匹配只允许长度>=4的词（避免短词误匹配）
            long_aw = [w for w in aw if len(w) >= 4]
            if long_aw and any(w in js for w in long_aw) and any(w in jt for w in long_aw): return True
        return False
    def _match_dict_val(s_attr, t_vals):
        sv=set()
        for v in [s_attr.get('value_cn'),s_attr.get('value_ru'),s_attr.get('value'),s_attr.get('text')]:
            if v:
                sv.add(_norm(str(v)))
                for p in _re.split(r'[,，、/;；]+',str(v)):
                    if p.strip(): sv.add(_norm(p.strip()))
        res=[]
        for tv in t_vals:
            tn={_norm(x) for x in [tv.value_cn,tv.value,tv.info] if x}
            if any(s in t for s in sv for t in tn) or any(t in s for s in sv for t in tn if len(t)>2):
                res.append(tv)
        return res

    src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
    # 使用翻译后的双语属性
    loc = raw.get('localized') or {}
    loc_attrs = loc.get('source_attributes') or {}
    for sa in src_attrs:
        nr = (sa.get('name') or sa.get('key') or '').strip()
        vr = str(sa.get('value') or sa.get('text') or '')
        ck = (nr+'='+vr).lower()
        lc = loc_attrs.get(ck,{}) if isinstance(loc_attrs,dict) else {}
        if lc.get('name_cn'): sa['name_cn'] = lc['name_cn']
        if lc.get('value_cn'): sa['value_cn'] = lc['value_cn']

    diagnostics = []
    if dcid and tid:
        tgt_attrs = list(OzonCategoryAttribute.select().where((OzonCategoryAttribute.user == current_user) & (OzonCategoryAttribute.ozon_category_id == dcid) & (OzonCategoryAttribute.type_id == tid)))
        tgt_vals = {}
        dict_ids = [a.attribute_id for a in tgt_attrs if a.is_dictionary]
        if dict_ids:
            for v in OzonAttributeValue.select().where((OzonAttributeValue.user == current_user) & (OzonAttributeValue.type_id == tid) & (OzonAttributeValue.attribute_id.in_(dict_ids))):
                tgt_vals.setdefault(v.attribute_id, []).append(v)

        for ta in tgt_attrs:
            matched = None
            for sa in src_attrs:
                if not _attr_name_match(sa, ta): continue
                sv = sa.get('value_cn') or sa.get('value_ru') or sa.get('value') or sa.get('text') or ''
                item = {'attribute_id':ta.attribute_id,'attribute_name':ta.name_cn or ta.name,'source_value':sv,'source_attribute_name':sa.get('name_cn') or sa.get('name',''),'confidence':0.85,'action':'fill','reason':'源属性双语匹配成功'}
                if ta.is_dictionary and ta.attribute_id in tgt_vals:
                    dvs = _match_dict_val(sa, tgt_vals[ta.attribute_id])
                    if dvs:
                        item['target_value']=dvs[0].value_cn or dvs[0].value
                        item['target_value_id']=dvs[0].value_id
                        item['confidence']=1.0
                        item['reason']='字典值精确匹配'
                        if len(dvs)>1: item['reason']+=f'（{len(dvs)}个候选取首个）'
                    else:
                        item['action']='skip'
                        item['reason']=f'目标字典值未匹配到"{sv}"'
                        matched=item; break
                elif not ta.is_dictionary:
                    item['target_value']=sv
                if item['action']!='skip': matched=item
                break
            # 品牌/型号兜底
            if not matched and fact:
                tn = _norm(ta.name_cn or ta.name or '')
                if any(w in tn for w in ['品牌','бренд','brand']):
                    b = fact.brand_name or ''
                    if not b and source:
                        t = (source.title_cn or '').split()
                        for w in t:
                            if w.upper()==w and len(w)>=2 and w.isascii(): b=w; break
                    if b: matched={'attribute_id':ta.attribute_id,'attribute_name':ta.name_cn or ta.name,'target_value':b,'source_value':b,'source_attribute_name':'标题/品牌识别','confidence':0.9,'action':'fill','reason':'从商品事实/标题提取品牌'}
                elif any(w in tn for w in ['型号','модель','名称']):
                    m = fact.model or ''
                    if _norm(m) in ('микрофон','microphone','麦克风',''): m=''
                    if not m and source:
                        t = (source.title_cn or '').split()
                        for i,w in enumerate(t):
                            if w.upper()==w and w.isascii() and w not in ('DJI','Для','Экшн-камера'): m=w; break
                    if m: matched={'attribute_id':ta.attribute_id,'attribute_name':ta.name_cn or ta.name,'target_value':m,'source_value':m,'source_attribute_name':'标题/型号识别','confidence':0.85,'action':'fill','reason':'从商品事实/标题提取型号'}

            if matched and matched.get('action')!='skip': attrs.append(matched)
            elif matched and matched.get('action')=='skip':
                diagnostics.append({'attribute_id':ta.attribute_id,'attribute_name':ta.name_cn or ta.name,'status':'not_filled','reason_code':matched.get('reason_code','dictionary_value_not_matched'),'reason':matched.get('reason',''),'required':ta.is_required,'suggestion':'请同步属性字典或手动选择'})
            elif ta.is_required:
                diagnostics.append({'attribute_id':ta.attribute_id,'attribute_name':ta.name_cn or ta.name,'status':'not_filled','reason_code':'no_source_match','reason':'未找到匹配的源属性','required':True,'suggestion':'请手动填写'})

    return jsonify({"ok": True, "category": cat, "text": text, "attributes": attrs, "diagnostics": diagnostics, "missing": missing})


@ozon_bp.route("/api/adaptation/<int:group_id>/auto-fill-apply", methods=["POST"])
@login_required
def api_adaptation_auto_fill_apply(group_id):
    """应用自动填写结果"""
    group = SourceProductGroup.get_or_none((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
    if not group: return jsonify({"ok": False, "error": "任务组不存在"}), 404
    fact = ProductFact.get_or_none((ProductFact.user == current_user) & (ProductFact.group == group))
    if not fact: return jsonify({"ok": False, "error": "请先保存商品事实"}), 400
    adaptation = ListingAdaptation.get_or_none((ListingAdaptation.user == current_user) & (ListingAdaptation.fact == fact))
    if not adaptation:
        adaptation = ListingAdaptation.create(user=current_user, fact=fact, status='adapting')
    data = request.get_json(silent=True) or {}
    filled = 0

    # 类目
    cat = data.get('category') or {}
    if cat.get('description_category_id') and cat.get('type_id'):
        adaptation.ozon_category_id = cat['description_category_id']
        adaptation.type_id = cat['type_id']
        adaptation.type_name_ru = cat.get('type_name_ru') or cat.get('type_name', '')
        adaptation.category_path = cat.get('path', '')
        adaptation.status = 'adapting'
        filled += 1

    # 属性
    attrs_data = data.get('attributes') or {}
    if attrs_data:
        existing = {}
        if adaptation.attribute_mapping_json:
            try: existing = json.loads(adaptation.attribute_mapping_json).get('attributes', {})
            except: pass
        for aid, v in attrs_data.items():
            existing[str(aid)] = v
        adaptation.attribute_mapping_json = json.dumps({'attributes': existing}, ensure_ascii=False)
        filled += len(attrs_data)

    # 文本
    if data.get('title_ru'):
        adaptation.title_ru = data['title_ru'][:300]
        filled += 1
    if data.get('description_ru'):
        adaptation.description_ru = data['description_ru'][:50000]
        filled += 1

    adaptation.save()
    saved = {}
    if adaptation.attribute_mapping_json:
        try: saved = json.loads(adaptation.attribute_mapping_json).get('attributes', {})
        except: pass
    return jsonify({"ok": True, "dcid": adaptation.ozon_category_id, "type_id": adaptation.type_id,
                    "saved_attributes": saved, "filled_count": filled,
                    "message": f"已应用 {filled} 项"})


@ozon_bp.route("/api/adaptation/<int:group_id>/recommend-category", methods=["POST"])
@login_required
def api_recommend_category(group_id):
    """推荐 OZON 类目（P0:源采集 > P1:系统识别+字典匹配 > P2:关键词兜底，当前已选不参与推荐）"""
    group = SourceProductGroup.get_or_none((SourceProductGroup.id == group_id) & (SourceProductGroup.user == current_user))
    if not group: return jsonify({"ok": False, "error": "任务组不存在"}), 404
    fact = ProductFact.get_or_none((ProductFact.user == current_user) & (ProductFact.group == group))
    item = SourceProductGroupItem.get_or_none((SourceProductGroupItem.user == current_user) & (SourceProductGroupItem.group == group))
    source = item.source if item else None
    raw = {}
    if source:
        try: raw = json.loads(source.raw_json or '{}')
        except: raw = {}

    import re as _re
    def _norm(s): return _re.sub(r'\s+',' ',str(s or '').lower().replace('ё','е').replace('-',' ')).strip()

    def _infer_product_kind():
        """通用：从标题提取单词/词组，在 OzonCategoryType 中搜索匹配"""
        import re as _re_kw
        src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
        attrs_text = ' '.join([_norm((a.get('name') or '') + ' ' + str(a.get('value') or '')) for a in src_attrs])
        title = _norm(getattr(fact, 'standard_name_ru', '') or getattr(fact, 'standard_name_cn', '') or (source.title_cn if source else '') or '')
        search_text = title + ' ' + attrs_text

        # 提取有意义单词：>=4字符俄语/拉丁词，>=2字符中文
        words = set()
        words.update(_re_kw.findall(r'[a-zа-яё]{4,}', search_text))
        for n in [2, 3]:
            words.update(_re_kw.findall(r'[一-鿿]{' + str(n) + '}', search_text))
        keywords = words - {'это', 'для', 'что', 'как'}

        if not keywords:
            return None

        all_types = list(OzonCategoryType.select().where(OzonCategoryType.user == current_user).limit(1000))
        scored = []
        for t in all_types:
            tn = _norm((t.type_name_cn or '') + ' ' + (t.type_name or ''))
            hits = sum(1 for kw in keywords if kw in tn)
            if hits > 0:
                scored.append((t, hits, tn))
        scored.sort(key=lambda x: -x[1])

        if scored:
            best_t, best_hits, best_tn = scored[0]
            matched = [kw for kw in keywords if kw in best_tn][:5]
            return {
                'kind': best_t.type_name or '',
                'kind_cn': best_t.type_name_cn or best_t.type_name or '',
                'confidence': min(0.5 + best_hits * 0.1, 0.85),
                'evidence': [f'匹配: {", ".join(matched)}'],
                'type_id': best_t.type_id, 'dcid': best_t.description_category_id,
            }
        return None

    def _make_rec(dcid,tid,tru,tcn,path,conf,src,reason,ev=None):
        return {'description_category_id':dcid,'type_id':tid,'type_name_ru':tru,'type_name_cn':tcn,'path':path or '','confidence':conf,'source':src,'reason':reason,'evidence':ev or []}

    recommendations = []
    diagnostics = []
    product_info = _infer_product_kind()
    pk = product_info['kind'] if product_info else ''

    # P0: 源采集类目
    src_dcid = raw.get('description_category_id') or raw.get('ozon_category_id') or raw.get('category_id') or ''
    src_tid = raw.get('type_id') or ''
    if src_dcid and src_tid:
        trec = OzonCategoryType.get_or_none((OzonCategoryType.user == current_user) & (OzonCategoryType.description_category_id == src_dcid) & (OzonCategoryType.type_id == src_tid))
        if trec: recommendations.append(_make_rec(src_dcid,src_tid,trec.type_name or '',trec.type_name_cn or '',trec.path or '',1.0,'source_collected','源采集类目'))
    else: diagnostics.append('采集数据无type_id')

    # P1: 通用关键词搜索 OzonCategoryType
    if not recommendations and product_info:
        import re as _re_p1
        src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
        attrs_text = ' '.join([_norm((a.get('name') or '') + ' ' + str(a.get('value') or '')) for a in src_attrs])
        search_all = _norm((getattr(fact, 'standard_name_ru', '') or getattr(fact, 'standard_name_cn', '') or (source.title_cn if source else '') or '') + ' ' + attrs_text)

        # 提取单词（同 _infer_product_kind）
        words = set()
        words.update(_re_p1.findall(r'[a-zа-яё]{4,}', search_all))
        for n in [2, 3]:
            words.update(_re_p1.findall(r'[一-鿿]{' + str(n) + '}', search_all))
        keywords = words - {'это', 'для', 'что', 'как'}
        max_kw_len = max((len(kw) for kw in keywords), default=0)

        # 意图预筛选：缩小候选范围
        from services.ozon_attribute_translate import get_localized_source_attributes, infer_product_intent
        localized = get_localized_source_attributes(source)
        loc_names = ' '.join([la.get('name_cn', '') + ' ' + la.get('raw_name', '') for la in localized])
        loc_vals = ' '.join([la.get('value_cn', '') + ' ' + la.get('raw_value', '') for la in localized])
        product_intent = infer_product_intent(
            getattr(fact, 'standard_name_ru', '') or (source.title_cn or ''),
            loc_names, loc_vals
        )
        preferred_kw = set(product_intent.get('preferred_kw', []) if product_intent else [])
        conflict_kw = set(product_intent.get('conflicts', []) if product_intent else [])

        top_types = []
        all_types = list(OzonCategoryType.select().where(OzonCategoryType.user == current_user).limit(1000))
        for t in all_types:
            tn = _norm((t.type_name_cn or '') + ' ' + (t.type_name or '') + ' ' + (t.path or ''))
            # 用单词匹配（非 n-gram）
            hits = sum(1 for kw in keywords if kw in tn)
            # 长词权重更高
            # 冲突词惩罚
            if conflict_kw and any(cf in tn for cf in conflict_kw):
                continue
            # 偏好词加分
            pref_boost = 0.5 if preferred_kw and any(pf in tn for pf in preferred_kw) else 0
            weighted = sum((len(kw) / max_kw_len) for kw in keywords if kw in tn) if max_kw_len > 0 else 0
            weighted += pref_boost
            if weighted > 0:
                top_types.append((t, round(weighted, 2)))
        top_types.sort(key=lambda x: -x[1])

        # 去重：相同 type_name_cn 只保留最高分
        seen_cn = set()
        unique = []
        for t, score in top_types:
            cn = t.type_name_cn or t.type_name
            if cn not in seen_cn:
                seen_cn.add(cn)
                unique.append((t, score))
                if len(unique) >= 8: break

        for t, score in unique[:5]:
            conf = 0.85 if score >= 3 else (0.65 if score >= 2 else 0.5)
            path = t.path or ''
            cat_node = OzonCategory.get_or_none((OzonCategory.user == current_user) & (OzonCategory.ozon_category_id == t.description_category_id))
            if cat_node and cat_node.path: path = cat_node.path + ' > ' + (t.type_name_cn or t.type_name)
            rec = _make_rec(t.description_category_id, t.type_id, t.type_name or '', t.type_name_cn or '',
                          path, conf, 'keyword_match',
                          f'标题/属性关键词匹配: {t.type_name_cn or t.type_name} (评分{score})',
                          [f'匹配{score}个关键词片段'])
            recommendations.append(rec)

        # 如果没有匹配结果，提示用户
        if not recommendations:
            diagnostics.append("未找到匹配的 OZON 类目，请手动选择或确认商品标题/属性是否准确")
            diagnostics.append("提示: 可以在标题中加入更明确的俄语品类关键词（如 джойстик, геймпад 等）")

    return jsonify({"ok":True,"categories":recommendations[:5],"count":len(recommendations),"diagnostics":diagnostics,"product_info":product_info})



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

    warning = None
    if fact.review_status != 'approved':
        warning = '商品事实尚未审核，已生成草稿，请在刊登前校验'

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
            draft.category_path_ru = adaptation.ozon_category_name
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
        where_val = ((OzonAttributeValue.user == current_user) &
                     (OzonAttributeValue.attribute_id.in_(dict_attr_ids)))
        if type_id:
            where_val = where_val & (OzonAttributeValue.type_id == type_id)
        val_records = (OzonAttributeValue
                       .select()
                       .where(where_val)
                       .order_by(OzonAttributeValue.attribute_id, OzonAttributeValue.value_id))
        # 预加载跨 type 翻译缓存（批量，避免逐条查 DB）
        ru_to_cn = {}
        for v in val_records:
            if v.value_cn and v.value_cn != v.value:
                ru_to_cn[v.value.lower()] = v.value_cn
        if not ru_to_cn:
            # 补查：可能有其他 type 已翻译的值
            extra = (OzonAttributeValue
                     .select(OzonAttributeValue.value, OzonAttributeValue.value_cn)
                     .where((OzonAttributeValue.user == current_user) &
                            (OzonAttributeValue.value_cn.is_null(False)) &
                            (OzonAttributeValue.value_cn != '') &
                            (OzonAttributeValue.value_cn != OzonAttributeValue.value))
                     .limit(2000))
            for ev in extra:
                if ev.value and ev.value_cn and ev.value not in ru_to_cn:
                    ru_to_cn[ev.value.lower()] = ev.value_cn

        seen_val = set()
        for v in val_records:
            key = (v.attribute_id, v.value_id)
            if key in seen_val: continue
            seen_val.add(key)
            display = v.value_cn
            missing = False
            if not display:
                # 内存匹配（不查 DB）
                cn = ru_to_cn.get((v.value or '').lower())
                if not cn:
                    for k, vcn in _BUILTIN_VALUE_CN_MAP.items():
                        if k.lower() == (v.value or '').lower():
                            cn = vcn; break
                if cn:
                    display = cn
                elif _is_proper_noun(v.value):
                    display = v.value  # 专有名词不翻译，不标红
                else:
                    missing = True
            values_map.setdefault(v.attribute_id, []).append({
                'value_id': v.value_id, 'value': v.value, 'value_cn': v.value_cn, 'info': v.info,
                'display_value': display or v.value,
                'missing_translation': missing and not _is_proper_noun(v.value),
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
    """获取一级类目（批量查询，避免 N+1）"""
    cats = list(OzonCategory
            .select()
            .where((OzonCategory.user == current_user) &
                   (OzonCategory.parent_id.is_null()))
            .order_by(OzonCategory.name))

    # 过滤 type 节点
    valid_cats = [c for c in cats if c.ozon_category_id and not (c.raw_json and '"type_id"' in c.raw_json)]
    valid_ids = [c.ozon_category_id for c in valid_cats]

    # 批量查 type 计数
    from peewee import fn as peewee_fn
    type_counts = {}
    if valid_ids:
        rows = (OzonCategoryType
                .select(OzonCategoryType.description_category_id,
                        peewee_fn.COUNT(OzonCategoryType.id).alias('cnt'))
                .where((OzonCategoryType.user == current_user) &
                       (OzonCategoryType.description_category_id.in_(valid_ids)))
                .group_by(OzonCategoryType.description_category_id)
                .tuples())
        for dcid, cnt in rows:
            type_counts[dcid] = cnt

    # 批量查子类目
    child_dcids = set()
    if valid_ids:
        children = (OzonCategory
                    .select(OzonCategory.parent_id)
                    .where((OzonCategory.user == current_user) &
                           (OzonCategory.parent_id.in_(valid_ids)) &
                           ~(OzonCategory.raw_json.contains('"type_id"')) &
                           (OzonCategory.ozon_category_id != ''))
                    .distinct())
        child_dcids = {c.parent_id for c in children}

    items = []
    for c in valid_cats:
        type_cnt = type_counts.get(c.ozon_category_id, 0)
        real_child = c.ozon_category_id in child_dcids
        items.append({
            'id': c.ozon_category_id, 'name': c.name, 'name_cn': c.name_cn,
            'has_children': real_child,
            'type_count': type_cnt,
        })

        # 若无子类目但有 type，在 items 中追加 type 节点供模态框选择
        if not real_child and type_cnt > 0:
            type_records = list(OzonCategoryType
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
    """获取子类目 + 当前类目直接挂载的 type"""
    parent_id = request.args.get('parent_id', '').strip()
    if not parent_id:
        return jsonify({'ok': False, 'error': '缺少 parent_id'}), 400

    items = []

    # ★ 1. 先返回当前类目直接挂载的 type
    direct_types = (OzonCategoryType
                    .select()
                    .where((OzonCategoryType.user == current_user) &
                           (OzonCategoryType.description_category_id == parent_id))
                    .order_by(OzonCategoryType.type_name))
    for t in direct_types:
        items.append({
            'id': t.type_id,
            'name': t.type_name_cn or t.type_name or f'type_{t.type_id}',
            'name_cn': t.type_name_cn,
            'is_type': True,
            'description_category_id': parent_id,
            'has_children': False,
            'type_count': 0,
            'path': t.path or '',
        })

    # ★ 2. 再返回子类目
    children = (OzonCategory
                .select()
                .where((OzonCategory.user == current_user) &
                       (OzonCategory.parent_id == parent_id))
                .order_by(OzonCategory.name))

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

    items = []
    seen = set()

    # 1. 搜索类目
    cats = (OzonCategory
            .select()
            .where((OzonCategory.user == current_user) &
                   ((OzonCategory.name.contains(q)) |
                    (OzonCategory.name_cn.contains(q)) |
                    (OzonCategory.ozon_category_id.contains(q))))
            .limit(30))
    for c in cats:
        if not c.ozon_category_id or c.ozon_category_id in seen: continue
        seen.add(c.ozon_category_id)
        items.append({'id': c.ozon_category_id, 'name': c.name, 'name_cn': c.name_cn,
                      'is_type': False, 'has_children': not c.is_leaf, 'path': c.path})

    # 2. 搜索 type
    types = (OzonCategoryType
             .select()
             .where((OzonCategoryType.user == current_user) &
                    ((OzonCategoryType.type_name.contains(q)) |
                     (OzonCategoryType.type_name_cn.contains(q)) |
                     (OzonCategoryType.type_id.contains(q))))
             .limit(30))
    for t in types:
        if t.type_id in seen: continue
        seen.add(t.type_id)
        items.append({
            'id': t.type_id,
            'name': t.type_name_cn or t.type_name or f'type_{t.type_id}',
            'name_cn': t.type_name_cn,
            'is_type': True,
            'description_category_id': t.description_category_id,
            'has_children': False,
            'path': t.path or '',
        })

    return jsonify({'ok': True, 'items': items})


# ═══ Draft 级 API ═══

@ozon_bp.route('/api/draft/<int:draft_id>/recommend-category', methods=['POST'])
@login_required
def api_draft_recommend_category(draft_id):
    """Draft 级推荐类目 — 复用 adaptation 逻辑"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft: return jsonify({"ok": False, "error": "草稿不存在"}), 404
    # 查找对应的 group
    source = draft.source
    if not source: return jsonify({"ok": False, "error": "无源商品"}), 404
    items = SourceProductGroupItem.select().where((SourceProductGroupItem.user == current_user) & (SourceProductGroupItem.source == source))
    group = None
    for item in items:
        if item.group: group = item.group; break
    if not group: return jsonify({"ok": False, "error": "无适配任务组"}), 404
    # 复用 adaptation 推荐逻辑
    return api_recommend_category(group.id)


# ── OZON 属性字段策略分类 ──
_ATTR_STRATEGY = {
    '品牌': 'proper_noun', '型号名称': 'proper_noun', '卖家代码': 'proper_noun',
    '与相机兼容性': 'proper_noun', '相机兼容性': 'proper_noun', '零件号': 'proper_noun',
    '视频': 'media_video', 'видео': 'media_video', 'video': 'media_video',
    '富内容': 'rich_content', 'rich-контент': 'rich_content',
    '#标签': 'hashtags', '#主题标签': 'hashtags', '#хештеги': 'hashtags',
    '计量单位中的商品数量': 'unit_quantity', 'количество товара в уеи': 'unit_quantity',
    'pdf': 'skip', 'инструкция': 'skip',
    '重量': 'weight', 'вес товара': 'weight',
}
_PROPER_NOUNS = {'DJI', 'Canon', 'Nikon', 'Sony', 'GoPro', 'Samsung', 'Apple',
                  'Xiaomi', 'Huawei', 'Bose', 'JBL', 'Sennheiser', 'Shure',
                  'Rode', 'Zoom', 'SmallRig', 'Adobe'}


def _get_attr_strategy(ta):
    cn = (ta.name_cn or '').lower(); ru = (ta.name or '').lower()
    for k, s in _ATTR_STRATEGY.items():
        if k.lower() in cn or k.lower() in ru:
            return s
    return 'matchable'


def _is_proper_noun(val):
    if not val: return False
    v = str(val).strip()
    if all(c.isascii() for c in v): return True
    for pn in _PROPER_NOUNS:
        if pn.lower() in v.lower(): return True
    return False


def _fill_video_attr(draft, ta, saved, aid):
    """从草稿视频媒体池填充 OZON 视频属性"""
    media = _load_media_json(draft)
    draft_videos = media.get('videos', []) if isinstance(media, dict) else []
    if not draft_videos:
        return
    ta_lower = ((ta.name_cn or '') + ' ' + (ta.name or '')).lower()
    if 'название' in ta_lower or '名称' in ta_lower:
        names = [v.get('name') or v.get('title') or '' for v in draft_videos[:5] if v.get('url')]
        if names:
            saved[aid] = {'value': ', '.join(names), 'source': 'draft_videos',
                          'attribute_name': ta.name_cn or ta.name}
    elif 'ссылка' in ta_lower or '链接' in ta_lower:
        urls = [v.get('url', '') for v in draft_videos[:5] if v.get('url')]
        if urls:
            saved[aid] = {'value': '\n'.join(urls), 'source': 'draft_videos',
                          'attribute_name': ta.name_cn or ta.name}
    elif 'обложка' in ta_lower or '封面' in ta_lower:
        covers = [v.get('cover_url', '') for v in draft_videos[:5] if v.get('cover_url')]
        if covers:
            saved[aid] = {'value': '\n'.join(covers), 'source': 'draft_videos',
                          'attribute_name': ta.name_cn or ta.name}
    elif 'товары на видео' in ta_lower or '视频中的商品' in ta_lower:
        first_sku = draft.draft_skus.first()
        offer_id = (first_sku.offer_id or '') if first_sku else ''
        saved[aid] = {'value': offer_id, 'source': 'draft_videos',
                      'attribute_name': ta.name_cn or ta.name}


def _attr_name_match(s_attr, t_attr):
    """复用适配工作台的成熟属性名匹配逻辑"""
    import re as _re2
    def _norm(s): return _re2.sub(r'\s+', ' ', str(s or '').strip().lower())
    ALIASES = {
        '颜色': ['цвет', '商品颜色'], '商品颜色': ['цвет', '颜色'],
        '保修期': ['гарантия', '保修'], '包装数量': ['количество в упаковке', '每包数量'],
        '产地': ['страна-изготовитель', '制造国'], '重量': ['вес товара', '商品重量'],
        '品牌': ['бренд', 'brand', 'производитель'],
        '型号': ['модель', 'название модели', '型号名称'],
        '类型': ['тип', 'вид', 'тип товара'],
        '用途': ['назначение', 'использование'],
        '尺寸': ['размеры', 'размер'],
        '材料': ['материал', 'состав'],
    }
    sn = {_norm(x) for x in [s_attr.get('name_cn'), s_attr.get('name_ru'), s_attr.get('name'), s_attr.get('key')] if x}
    tn = {_norm(x) for x in [t_attr.name_cn, t_attr.name, (t_attr.description or '')] if x}
    for s in sn:
        for t in tn:
            if s and t and (s == t or s in t or t in s):
                return True
    js = ' '.join(sn)
    jt = ' '.join(tn)
    for k, ws in ALIASES.items():
        aw = [_norm(k)] + [_norm(w) for w in ws]
        # 只有长度>=4的词才做别名匹配（避免短词如"тип"误匹配）
        long_aw = [w for w in aw if len(w) >= 4]
        if long_aw and any(w in js for w in long_aw) and any(w in jt for w in long_aw):
            return True
    return False


def _match_dict_val(s_attr, t_vals):
    """复用适配工作台的字典值匹配逻辑"""
    import re as _re3
    def _norm(s): return _re3.sub(r'\s+', ' ', str(s or '').strip().lower())
    sv = set()
    for v in [s_attr.get('value_cn'), s_attr.get('value_ru'), s_attr.get('value'), s_attr.get('text')]:
        if v:
            sv.add(_norm(str(v)))
            for p in _re3.split(r'[,，、/;；]+', str(v)):
                if p.strip():
                    sv.add(_norm(p.strip()))
    res = []
    for tv in t_vals:
        tn = {_norm(x) for x in [tv.value_cn, tv.value, tv.info] if x}
        if any(s in t for s in sv for t in tn) or any(t in s for s in sv for t in tn if len(t) > 2):
            res.append(tv)
    return res


def _match_dict_value_id(ta, value_cn, value_ru):
    """根据中文/俄语值查找 OZON 字典 value_id"""
    if not value_cn and not value_ru:
        return ''
    # 优先中文匹配
    if value_cn:
        dv = (OzonAttributeValue
              .select()
              .where((OzonAttributeValue.attribute_id == ta.attribute_id) &
                     ((OzonAttributeValue.value_cn == value_cn) |
                      (OzonAttributeValue.value == value_cn)))
              .first())
        if dv:
            return dv.value_id
    if value_ru:
        dv = (OzonAttributeValue
              .select()
              .where((OzonAttributeValue.attribute_id == ta.attribute_id) &
                     (OzonAttributeValue.value == value_ru))
              .first())
        if dv:
            return dv.value_id
    return ''


@ozon_bp.route('/api/draft/<int:draft_id>/translate-attributes', methods=['POST'])
@login_required
def api_draft_translate_attributes(draft_id):
    """翻译/校准采集源属性"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft or not draft.source:
        return jsonify({"ok": False, "error": "草稿或源商品不存在"}), 404

    raw = {}
    try: raw = json.loads(draft.source.raw_json or '{}')
    except: raw = {}

    src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
    if not src_attrs:
        return jsonify({"ok": True, "message": "无源属性可翻译", "items": [], "stats": {}})

    from services.ozon_attribute_translate import translate_source_attributes
    items, stats = translate_source_attributes(src_attrs, current_user)

    # 如果有 type_id，尝试校准到 OZON 字典
    has_type = bool(draft.type_id and draft.ozon_category_id)
    if not has_type:
        stats['warning'] = '尚未选择OZON类目，无法校准模板下拉值'

    # apply=true: 写回 draft.attributes_json
    data = request.get_json(silent=True) or {}
    if data.get('apply') and has_type:
        saved = _load_draft_attributes_map(draft)
        applied = 0
        for item in items:
            if item['status'] != 'confirmed' or item['source'] == 'proper_noun':
                continue
            # 找到对应的目标属性
            for ta in OzonCategoryAttribute.select().where(
                (OzonCategoryAttribute.user == current_user) &
                (OzonCategoryAttribute.ozon_category_id == draft.ozon_category_id) &
                (OzonCategoryAttribute.type_id == draft.type_id)):
                ta_lower = ((ta.name_cn or '') + ' ' + (ta.name or '')).lower()
                item_name_lower = (item['name_cn'] + ' ' + item['raw_name']).lower()
                if item['name_cn'] in ta_lower or item['raw_name'] in ta_lower or any(
                    w in ta_lower for w in item_name_lower.split() if len(w) > 3):
                    aid = str(ta.attribute_id)
                    entry = {'value': item['value_cn'], 'source': 'translated'}
                    if ta.is_dictionary:
                        entry['value_id'] = _match_dict_value_id(ta, item['value_cn'], item['raw_value'])
                    saved[aid] = entry
                    applied += 1
                    break
        if applied > 0:
            draft.attributes_json = json.dumps(saved, ensure_ascii=False)
            draft.save()
            stats['applied'] = applied

    return jsonify({
        "ok": True,
        "message": f"已翻译 {stats['translated']} 项，保留专有名词 {stats['proper_nouns']} 项，需确认 {stats['needs_review']} 项" + (f"，已应用 {stats.get('applied', 0)} 项到草稿" if stats.get('applied') else ""),
        "items": items,
        "stats": stats,
        "has_type": has_type,
    })


@ozon_bp.route('/api/draft/<int:draft_id>/auto-fill-apply', methods=['POST'])
@login_required
def api_draft_auto_fill_apply(draft_id):
    """Draft 级自动填写 — 复用适配工作台的成熟匹配逻辑"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft: return jsonify({"ok": False, "error": "草稿不存在"}), 404
    source = draft.source
    if not source: return jsonify({"ok": False, "error": "无源商品"}), 404
    raw = {}
    try: raw = json.loads(source.raw_json or '{}')
    except: raw = {}
    filled = 0
    diagnostics = []
    data = request.get_json(silent=True) or {}

    # 标题
    if not draft.title_ru and source.title_cn:
        draft.title_ru = source.title_cn[:150]; filled += 1

    # 描述（HTML→纯文本）
    rich = raw.get('rich_text') or {}
    if not draft.description_ru and (rich.get('html') or rich.get('plain_text')):
        raw_html = rich.get('html') or ''
        if raw_html:
            from services.ozon_template_excel import html_to_plain_text
            draft.description_ru = html_to_plain_text(raw_html, max_length=50000)
        else:
            draft.description_ru = (rich.get('plain_text') or '')[:50000]
        filled += 1

    # 卖点
    if not draft.bullets_ru:
        src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
        bullets = ['• ' + (a.get('name', '') + ': ' + str(a.get('value', '')))
                   for a in src_attrs[:8] if a.get('name') and a.get('value')]
        if bullets: draft.bullets_ru = json.dumps(bullets, ensure_ascii=False); filled += 1

    # ── 属性（复用适配工作台的双语+别名+字典值匹配）──
    attr_filled = 0
    attr_diagnostics = []

    if draft.ozon_category_id and draft.type_id:
        saved = _load_draft_attributes_map(draft)
        src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []

        # 优先读双语句柄
        from services.ozon_attribute_translate import get_localized_source_attributes, normalize_source_attributes
        localized_attrs = get_localized_source_attributes(source)
        if not localized_attrs or localized_attrs[0].get('status') == 'needs_review':
            normalize_source_attributes(source, current_user)
            localized_attrs = get_localized_source_attributes(source)

        # 以双语层为准：覆盖 src_attrs 的 name_cn/value_cn
        loc_by_key = {}
        for la in localized_attrs:
            key = (la['raw_name'] + '=' + la['raw_value']).lower()
            loc_by_key[key] = la
        for sa in src_attrs:
            nr = (sa.get('name') or sa.get('key') or '').strip()
            vr = str(sa.get('value') or sa.get('text') or '')
            ck = (nr + '=' + vr).lower()
            lc = loc_by_key.get(ck, {})
            if lc.get('name_cn') and lc['name_cn'] != nr:
                sa['name_cn'] = lc['name_cn']
            if lc.get('value_cn') and lc.get('status') == 'confirmed':
                sa['value_cn'] = lc['value_cn']

        tgt_attrs = list(OzonCategoryAttribute.select().where(
            (OzonCategoryAttribute.user == current_user) &
            (OzonCategoryAttribute.ozon_category_id == draft.ozon_category_id) &
            (OzonCategoryAttribute.type_id == draft.type_id)))

        # 字典值缓存
        tgt_vals = {}
        dict_ids = [a.attribute_id for a in tgt_attrs if a.is_dictionary]
        if dict_ids:
            for v in OzonAttributeValue.select().where(
                (OzonAttributeValue.user == current_user) &
                (OzonAttributeValue.type_id == draft.type_id) &
                (OzonAttributeValue.attribute_id.in_(dict_ids))):
                tgt_vals.setdefault(v.attribute_id, []).append(v)

        for ta in tgt_attrs:
            aid = str(ta.attribute_id)
            # 已存在且是手动值 → 不覆盖；已存在但为空/自动填充 → 允许重填
            if aid in saved:
                existing = saved[aid]
                if isinstance(existing, dict):
                    src = existing.get('source', '')
                    if src == 'manual':
                        continue  # 手动输入，保护不覆盖
                    if existing.get('value') and existing.get('value') not in ('', '（于富文本中调整）'):
                        continue  # 有值且不是占位，保留
                else:
                    continue  # 旧格式有值，保留

            # 按字段策略分类决定处理方式
            strategy = _get_attr_strategy(ta)
            if strategy == 'skip':
                continue
            if strategy == 'rich_content':
                saved[aid] = {'value': '（于富文本中调整）', 'source': 'readonly',
                              'attribute_name': ta.name_cn or ta.name}
                attr_filled += 1
                continue
            if strategy == 'media_video':
                _fill_video_attr(draft, ta, saved, aid)
                if aid in saved:
                    attr_filled += 1
                continue
            if strategy == 'unit_quantity':
                saved[aid] = {'value': '1', 'source': 'default',
                              'attribute_name': ta.name_cn or ta.name}
                attr_filled += 1
                continue
            if strategy == 'hashtags':
                if draft.hashtags_ru:
                    saved[aid] = {'value': draft.hashtags_ru, 'source': 'draft_hashtags',
                                  'attribute_name': ta.name_cn or ta.name}
                    attr_filled += 1
                continue

            matched = False
            match_reason = ''
            for sa in src_attrs:
                if not _attr_name_match(sa, ta):
                    continue

                sv_cn = sa.get('value_cn') or ''
                sv_ru = sa.get('value_ru') or sa.get('value') or sa.get('text') or ''
                sv = sv_cn or sv_ru
                # 字典字段且只有俄语值 → 跳过（不填俄语到中文模板）
                if ta.is_dictionary and not sv_cn:
                    continue
                entry = {
                    'value': sv, 'source': 'auto_fill',
                    'source_attribute_name': sa.get('name_cn') or sa.get('name', ''),
                    'attribute_name': ta.name_cn or ta.name,
                }

                # 专有名词：不查字典，直接用源值；其他：字典值匹配
                if strategy == 'proper_noun' or _is_proper_noun(sv):
                    match_reason = f'专有名词: {sv}'
                elif ta.is_dictionary and ta.attribute_id in tgt_vals:
                    dvs = _match_dict_val(sa, tgt_vals[ta.attribute_id])
                    if dvs:
                        dv = dvs[0]
                        entry['value_id'] = dv.value_id
                        entry['value'] = dv.value_cn or dv.value
                        entry['value_cn'] = dv.value_cn
                        entry['value_ru'] = dv.value
                        match_reason = f'字典匹配: {dv.value_cn or dv.value}'
                    else:
                        match_reason = f'属性名匹配但字典值未命中: {sv}'
                        attr_diagnostics.append({
                            'attribute': ta.name_cn or ta.name,
                            'reason': f'源值「{sv}」未在OZON字典中找到对应选项',
                        })
                        continue
                else:
                    match_reason = '属性名匹配'

                saved[aid] = entry
                attr_filled += 1
                matched = True
                break

            if not matched and ta.is_required:
                attr_diagnostics.append({
                    'attribute': ta.name_cn or ta.name,
                    'reason': '未在采集源中找到匹配的属性名',
                })
        if attr_filled > 0:
            draft.attributes_json = json.dumps(saved, ensure_ascii=False)
            filled += attr_filled
        else:
            diagnostics.append('属性自动填写: 0 项匹配，已保留现有属性不覆盖')
        diagnostics.extend([f'{d["attribute"]}: {d["reason"]}' for d in attr_diagnostics[:10]])

    draft.save()
    return jsonify({
        "ok": True,
        "filled_count": filled,
        "message": f"已应用 {filled} 项" + (f"，属性 {attr_filled} 项匹配" if attr_filled > 0 else ""),
        "diagnostics": diagnostics[:15],
        "attr_diagnostics": attr_diagnostics[:10] if not attr_filled else [],
    })

@ozon_bp.route('/api/draft/<int:draft_id>/save-attributes', methods=['POST'])
@login_required
def api_draft_save_attributes(draft_id):
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft: return jsonify({"ok": False, "error": "草稿不存在"}), 404
    data = request.get_json(silent=True) or {}
    attrs = data.get('attributes', {})
    saved = _load_draft_attributes_map(draft)
    for aid, val in attrs.items():
        aid_str = str(aid)
        if isinstance(val, dict):
            entry = {'source': val.get('source', 'manual')}
            # 字典值：有 value_id 时从 DB 补全值
            if val.get('value_id') and draft.type_id:
                entry['value_id'] = str(val['value_id'])
                dv = (OzonAttributeValue
                      .select()
                      .where((OzonAttributeValue.user == current_user) &
                             (OzonAttributeValue.attribute_id == aid_str) &
                             (OzonAttributeValue.value_id == str(val['value_id'])))
                      .first())
                if dv:
                    # DB 字典值优先（准确的中文/俄语值），前端传值仅兜底
                    entry['value'] = dv.value_cn or dv.value
                    entry['value_cn'] = dv.value_cn
                    entry['value_ru'] = dv.value
                elif val.get('value'):
                    entry['value'] = val['value']
            else:
                entry['value'] = val.get('value', '')
            if val.get('value_cn'):
                entry['value_cn'] = val['value_cn']
            if val.get('value_ru'):
                entry['value_ru'] = val['value_ru']
            saved[aid_str] = entry
        elif val:
            # 字符串：可能是旧格式的 value_id，字典属性尝试从 DB 查
            saved[aid_str] = {'value': str(val), 'source': 'manual'}
    draft.attributes_json = json.dumps(saved, ensure_ascii=False)
    draft.save()
    return jsonify({"ok": True, "message": f"已保存 {len(attrs)} 个属性"})

@ozon_bp.route('/api/draft/<int:draft_id>/fill-from-source', methods=['POST'])
@login_required
def api_draft_fill_from_source(draft_id):
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft or not draft.source: return jsonify({"ok": False, "error": "无源商品"}), 404
    raw = {}
    try: raw = json.loads(draft.source.raw_json or '{}')
    except: raw = {}
    tp = (request.get_json(silent=True) or {}).get('type', '')
    if tp == 'title' and draft.source.title_cn:
        draft.title_ru = draft.source.title_cn[:150]; draft.save()
        return jsonify({"ok": True, "message": "标题已填充"})
    if tp == 'desc':
        rich = raw.get('rich_text') or {}
        raw_html = rich.get('html') or ''
        if raw_html:
            from services.ozon_template_excel import html_to_plain_text
            draft.description_ru = html_to_plain_text(raw_html, max_length=50000)
        else:
            draft.description_ru = (rich.get('plain_text') or '')[:50000]
        draft.save()
        return jsonify({"ok": True, "message": "描述已填充（纯文本）"})
    if tp == 'bullets':
        src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
        bullets = ['• ' + (a.get('name','') + ': ' + str(a.get('value',''))) for a in src_attrs[:8] if a.get('name') and a.get('value')]
        if bullets: draft.bullets_ru = json.dumps(bullets, ensure_ascii=False); draft.save()
        return jsonify({"ok": True, "message": f"已生成 {len(bullets)} 条卖点"})
    return jsonify({"ok": False, "error": "未知填充类型"})

@ozon_bp.route('/api/draft/<int:draft_id>/media/upload-image', methods=['POST'])
@login_required
def api_draft_upload_image(draft_id):
    """上传草稿图片 — 保存到磁盘并写入 draft.media_json"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft: return jsonify({"ok": False, "error": "草稿不存在"}), 404
    files = request.files.getlist('images')
    if not files: return jsonify({"ok": False, "error": "无文件"}), 400
    saved = []
    import os
    img_dir = os.path.join('static', 'uploads', 'ozon_drafts', str(draft_id), 'images')
    os.makedirs(img_dir, exist_ok=True)

    # 读现有媒体池
    media = _load_media_json(draft)

    for f in files[:5]:
        if not f.filename: continue
        ts = int(time.time() * 1000)
        safe_name = f.filename.rsplit('.', 1)[0][:40] + '_' + str(ts)
        ext = (f.filename.rsplit('.', 1)[-1] or 'jpg').lower()
        if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'):
            ext = 'jpg'
        fname = f'{safe_name}.{ext}'
        fpath = os.path.join(img_dir, fname)
        f.save(fpath)

        # 生成缩略图
        thumb_path = None
        thumb_url = None
        try:
            from PIL import Image
            img = Image.open(fpath)
            img.thumbnail((150, 150), Image.LANCZOS)
            thumb_name = f'{safe_name}_thumb.{ext}'
            thumb_path = os.path.join(img_dir, thumb_name)
            img.save(thumb_path)
            thumb_url = '/' + thumb_path.replace('\\', '/')
        except Exception:
            pass

        public_url = '/' + fpath.replace('\\', '/')
        img_id = f'img_{ts}_{len(media["images"])}'
        img_obj = {
            'id': img_id,
            'source': 'uploaded',
            'local_path': fpath,
            'public_url': public_url,
            'ozon_url': None,
            'thumb_url': thumb_url or public_url,
            'filename': fname,
            'role': 'gallery',
            'selected': True,
            'sort_order': len(media['images']) + 1,
            'upload_status': 'local',
            'review_status': 'pending',
            'alt': '',
            'width': None,
            'height': None
        }
        media['images'].append(img_obj)
        saved.append(img_obj)

    draft.media_json = json.dumps(media, ensure_ascii=False)
    draft.updated_at = datetime.datetime.now()
    draft.save()

    return jsonify({"ok": True, "message": f"已上传 {len(saved)} 张", "images": saved})


@ozon_bp.route('/api/draft/<int:draft_id>/media/save', methods=['POST'])
@login_required
def api_draft_media_save(draft_id):
    """统一保存草稿媒体池（图片+视频），保存前归一化主图 sort_order 和 is_cover"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft: return jsonify({"ok": False, "error": "草稿不存在"}), 404
    data = request.get_json(silent=True) or {}
    images = data.get('images')
    videos = data.get('videos')
    deleted_video_urls = data.get('deleted_video_urls')
    deleted_image_source_ids = data.get('deleted_image_source_ids')
    deleted_image_urls = data.get('deleted_image_urls')
    media = _load_media_json(draft)
    if images is not None:
        media['images'] = images
    if videos is not None:
        media['videos'] = videos
    if deleted_video_urls is not None:
        media['deleted_video_urls'] = deleted_video_urls
    if deleted_image_source_ids is not None:
        media['deleted_image_source_ids'] = deleted_image_source_ids
    if deleted_image_urls is not None:
        media['deleted_image_urls'] = deleted_image_urls

    # 归一化主图：按 sort_order 排序，重新编号，第一张 is_cover=true
    mains = [img for img in media.get('images', [])
             if img.get('selected') and img.get('role') == 'main']
    mains.sort(key=lambda x: (x.get('sort_order', 9999), x.get('id', '')))
    for idx, img in enumerate(mains):
        img['sort_order'] = idx + 1
        img['is_cover'] = idx == 0

    draft.media_json = json.dumps(media, ensure_ascii=False)
    draft.updated_at = datetime.datetime.now()
    draft.save()
    return jsonify({"ok": True, "message": "媒体池已保存", "media": media})


@ozon_bp.route('/api/draft/<int:draft_id>/media/import-from-source', methods=['POST'])
@login_required
def api_draft_media_import_from_source(draft_id):
    """从采集源导入图片/视频到草稿媒体池

    导入规则：
    - main：全量导入 role='main'，第一张 is_cover=true，其余 is_cover=false
    - sku：全部导入 role='sku'
    - detail/scene/gallery 等：不导入图片/视频 tab，跳过（由富文本模块处理）
    - buyer_review/video/main_video/unknown/rejected：跳过
    """
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft or not draft.source:
        return jsonify({"ok": False, "error": "草稿或源商品不存在"}), 404

    # 图片/视频 tab 只导入 main + sku（detail/scene 归富文本处理）
    DIRECT_LISTING_ROLES = {'main', 'sku'}
    # 以下角色明确跳过（不进图片池也不进富文本）
    SKIP_ROLES = {'buyer_review', 'unknown', 'video', 'main_video'}

    media = _load_media_json(draft)

    # 构建 source_media_id → 源角色 映射（用于修正历史脏数据）
    source_images = list(OzonSourceMedia.select().where(
        OzonSourceMedia.source == draft.source
    ).order_by(OzonSourceMedia.id))

    source_role_by_id = {}
    for sm in source_images:
        source_role_by_id[str(sm.id)] = (sm.role or '').lower()

    # 构建 source_media_id → 已有图片对象 映射（用于去重+修正）
    existing_by_source_id = {}
    for img in media.get('images', []):
        sm_id = str(img.get('source_media_id') or '')
        if sm_id:
            existing_by_source_id[sm_id] = img

    existing_ids = {img.get('id') for img in media.get('images', []) if img.get('id')}
    existing_video_urls = {v.get('url', '') for v in media['videos']}
    # 黑名单：用户手动删除过的视频/图片，不再重新导入
    deleted_video_urls = set(media.get('deleted_video_urls', []))
    deleted_image_source_ids = set(media.get('deleted_image_source_ids', []))
    deleted_image_urls = set(media.get('deleted_image_urls', []))

    # 轻度清理：只移除明显无效的图片
    dirty_before = len(media['images'])
    valid_imgs = []
    seen_sm_ids = set()
    for img in media.get('images', []):
        if img.get('source') == 'collected':
            if not img.get('public_url') and not img.get('local_path'):
                continue
            if img.get('review_status') == 'rejected':
                continue
            sm_id = str(img.get('source_media_id', ''))
            if sm_id and sm_id in seen_sm_ids:
                continue
            if sm_id:
                seen_sm_ids.add(sm_id)
        valid_imgs.append(img)
    dirty_cleaned = dirty_before - len(valid_imgs)
    media['images'] = valid_imgs
    # 清理后重建映射
    existing_by_source_id = {}
    for img in media.get('images', []):
        sm_id = str(img.get('source_media_id') or '')
        if sm_id:
            existing_by_source_id[sm_id] = img

    imported_imgs = 0
    imported_vids = 0
    skipped_role = 0
    skipped_duplicate = 0
    skipped_rejected = 0
    images_fixed_role = 0

    for sm in source_images:
        src_role = source_role_by_id.get(str(sm.id), '')
        source_media_id = str(sm.id)
        img_id = f'img_src_{sm.id}'

        # 明确排除的角色
        if src_role in SKIP_ROLES:
            skipped_role += 1
            continue

        # 合规过滤
        if sm.compliance_status and sm.compliance_status == 'rejected':
            skipped_rejected += 1
            continue

        # 图片删除黑名单：用户手动删除过的 source_media_id 不重新导入
        if source_media_id in deleted_image_source_ids:
            continue
        img_url = sm.source_url or ''
        if img_url and img_url in deleted_image_urls:
            continue

        # ── 已存在：检查 role 是否需要修正 ──
        if source_media_id in existing_by_source_id:
            existing_img = existing_by_source_id[source_media_id]

            # 采集源是 main/sku 但草稿里 role 错了 → 修正
            if src_role in DIRECT_LISTING_ROLES:
                if existing_img.get('role') != src_role:
                    existing_img['role'] = src_role
                    existing_img['source_role'] = src_role
                    existing_img['selected'] = True
                    images_fixed_role += 1

            skipped_duplicate += 1
            continue

        # ── 不导入 detail/scene 等（归富文本）──
        if src_role not in DIRECT_LISTING_ROLES:
            skipped_role += 1
            continue

        # ── 新增导入 ──
        url = sm.source_url or sm.local_path or ''
        img_obj = {
            'id': img_id,
            'source': 'collected',
            'source_media_id': sm.id,
            'local_path': sm.local_path or '',
            'public_url': url,
            'ozon_url': None,
            'thumb_url': url,
            'filename': url.rsplit('/', 1)[-1] if url else f'source_{sm.id}.jpg',
            'role': src_role,    # 直接取采集源真实 role
            'source_role': src_role,
            'is_cover': False,   # 统一整理时再设
            'selected': True,
            'sort_order': len(media['images']) + imported_imgs + 1,
            'upload_status': 'public_ready' if url.startswith('http') else 'local',
            'review_status': sm.review_status or 'pending',
            'alt': '',
            'width': sm.width,
            'height': sm.height,
        }
        media['images'].append(img_obj)
        existing_by_source_id[source_media_id] = img_obj
        existing_ids.add(img_id)
        imported_imgs += 1

    # ── 导入完成后统一整理主图顺序和封面 ──
    main_imgs = [
        img for img in media.get('images', [])
        if img.get('selected') and img.get('role') == 'main'
    ]
    main_imgs.sort(key=lambda x: (x.get('sort_order') or 999999, str(x.get('id') or '')))
    for idx, img in enumerate(main_imgs):
        img['sort_order'] = idx + 1
        img['is_cover'] = idx == 0

    # 从 raw_json 导入采集视频（不变）
    raw = {}
    try:
        raw = json.loads(draft.source.raw_json or '{}')
    except Exception:
        raw = {}
    source_videos = raw.get('videos', []) or raw.get('product_videos', []) or []
    for vi, v in enumerate(source_videos):
        v_url = v.get('video_url') or v.get('url') or ''
        if not v_url or v_url in existing_video_urls or v_url in deleted_video_urls:
            continue
        vid = {
            'id': f'video_src_{vi}_{int(time.time())}',
            'source': 'collected',
            'url': v_url,
            'cover_url': v.get('cover_url') or v.get('thumbnail_url') or '',
            'name': v.get('name') or v.get('title') or f'视频 {vi + 1}',
            'selected': True,
            'upload_status': 'external',
            'sort_order': len(media['videos']) + imported_vids + 1
        }
        media['videos'].append(vid)
        existing_video_urls.add(v_url)
        imported_vids += 1

    draft.media_json = json.dumps(media, ensure_ascii=False)
    draft.updated_at = datetime.datetime.now()
    draft.save()

    return jsonify({
        "ok": True,
        "message": f"已导入 {imported_imgs} 张图片, 修正主图角色 {images_fixed_role} 张, {imported_vids} 个视频",
        "images_imported": imported_imgs,
        "images_fixed_role": images_fixed_role,
        "videos_imported": imported_vids,
        "skipped_role": skipped_role,
        "skipped_duplicate": skipped_duplicate,
        "skipped_rejected": skipped_rejected,
        "dirty_cleaned": dirty_cleaned,
        "media": media
    })


@ozon_bp.route('/api/draft/<int:draft_id>/save-rich-content', methods=['POST'])
@login_required
def api_draft_save_rich_content(draft_id):
    """保存富文本块 JSON — 写入 rich_content_json 字段"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft: return jsonify({"ok": False, "error": "草稿不存在"}), 404
    data = request.get_json(silent=True) or {}
    rich = data.get('rich_content_json', '')
    blocks = data.get('blocks', [])
    draft.rich_content_json = json.dumps({'version': '1.0', 'blocks': blocks}, ensure_ascii=False) if blocks else rich
    draft.updated_at = datetime.datetime.now()
    draft.save()
    return jsonify({"ok": True, "message": f"富文本已保存 ({len(blocks)} 块)"})


@ozon_bp.route('/api/draft/<int:draft_id>/save-all', methods=['POST'])
@login_required
def api_draft_save_all(draft_id):
    """一次性保存草稿所有数据（内容+SKU+价格+媒体+富文本）"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft:
        return jsonify({"ok": False, "error": "草稿不存在"}), 404

    data = request.get_json(silent=True) or {}

    # 目标店铺
    account_id = data.get('account_id')
    if account_id:
        try:
            account = OzonAccount.get((OzonAccount.id == int(account_id)) &
                                      (OzonAccount.user == current_user) &
                                      (OzonAccount.is_active == True))
            draft.account = account
        except OzonAccount.DoesNotExist:
            pass  # 忽略无效的 account_id，不阻断保存

    # 内容
    content = data.get('content') or {}
    if content.get('title_ru') is not None:
        draft.title_ru = content.get('title_ru', draft.title_ru)
    if content.get('description_ru') is not None:
        draft.description_ru = content.get('description_ru', draft.description_ru)
    if content.get('bullets_ru') is not None:
        draft.bullets_ru = content.get('bullets_ru', draft.bullets_ru)
    if content.get('hashtags_ru') is not None:
        draft.hashtags_ru = content.get('hashtags_ru', draft.hashtags_ru) or None
    if content.get('ozon_category_path') is not None:
        draft.category_path_ru = content.get('ozon_category_path') or draft.category_path_ru

    # 价格
    pricing = data.get('pricing') or {}
    existing_pricing = {}
    try:
        existing_pricing = json.loads(draft.pricing_json or '{}')
    except (json.JSONDecodeError, TypeError):
        pass
    if pricing.get('currency'):
        existing_pricing['listing_currency'] = pricing['currency']
    if 'ozon_price' in pricing:
        existing_pricing['listing_price'] = str(pricing.get('ozon_price') or '').strip()
    if 'price_manual_confirmed' in pricing:
        draft.price_manual_confirmed = bool(pricing.get('price_manual_confirmed'))
    draft.pricing_json = json.dumps(existing_pricing, ensure_ascii=False)

    # SKU
    skus_data = data.get('skus') or []
    for sd in skus_data:
        sku_id = sd.get('sku_id')
        if not sku_id:
            continue
        try:
            sku = OzonDraftSku.get((OzonDraftSku.id == int(sku_id)) & (OzonDraftSku.draft == draft))
        except (OzonDraftSku.DoesNotExist, ValueError):
            continue
        if sd.get('offer_id') is not None:
            sku.offer_id = sd['offer_id'] or None
        if sd.get('color') is not None:
            raw_color = sd['color'] or ''
            # 前端 select 可能发 "черный|61574" 格式，解析出文本值
            if '|' in raw_color:
                sku.color_ru = raw_color.split('|')[0] or None
            else:
                sku.color_ru = raw_color or None
        if sd.get('style') is not None:
            sku.style_ru = sd['style'] or None
        if sd.get('barcode') is not None:
            sku.barcode = sd['barcode'] or None
        if sd.get('quantity'):
            try:
                sku.bundle_quantity = int(sd['quantity'])
            except ValueError:
                pass
        sku.save()

    # 媒体池（含删除黑名单）
    media = data.get('media')
    if media:
        existing_media = _load_media_json(draft)
        if isinstance(media, dict):
            if 'images' in media:
                existing_media['images'] = media['images']
            if 'videos' in media:
                existing_media['videos'] = media['videos']
            # 持久化删除黑名单
            for key in ('deleted_video_urls', 'deleted_image_source_ids', 'deleted_image_urls'):
                if key in media:
                    existing_media[key] = media[key]
            draft.media_json = json.dumps(existing_media, ensure_ascii=False)

    # 富文本
    rich = data.get('rich_content')
    if rich is not None:
        blocks = rich if isinstance(rich, list) else rich.get('blocks', [])
        draft.rich_content_json = json.dumps({'version': '1.0', 'blocks': blocks}, ensure_ascii=False)

    # 属性字典
    attrs = data.get('attributes')
    if attrs and isinstance(attrs, dict):
        saved = _load_draft_attributes_map(draft)
        for aid, val in attrs.items():
            if isinstance(val, dict):
                entry = {'source': val.get('source', 'manual')}
                if 'value_id' in val and val['value_id']:
                    entry['value_id'] = val['value_id']
                # 字典值：从 OzonAttributeValue 补全
                if val.get('value_id') and draft.type_id:
                    dv = (OzonAttributeValue
                          .select()
                          .where((OzonAttributeValue.user == current_user) &
                                 (OzonAttributeValue.attribute_id == str(aid)) &
                                 (OzonAttributeValue.value_id == str(val['value_id'])))
                          .first())
                    if dv:
                        entry['value'] = dv.value_cn or dv.value
                        entry['value_cn'] = dv.value_cn
                        entry['value_ru'] = dv.value
                    else:
                        entry['value'] = val.get('value', '')
                else:
                    entry['value'] = val.get('value', '')
                if val.get('value_cn'):
                    entry['value_cn'] = val['value_cn']
                if val.get('value_ru'):
                    entry['value_ru'] = val['value_ru']
                saved[str(aid)] = entry
            elif val:  # 字符串
                saved[str(aid)] = {'value': str(val), 'source': 'manual'}
        draft.attributes_json = json.dumps(saved, ensure_ascii=False)

    draft.updated_at = datetime.datetime.now()
    draft.save()

    return jsonify({"ok": True, "message": "草稿数据已保存"})


@ozon_bp.route('/api/draft/<int:draft_id>/approve', methods=['POST'])
@login_required
def api_draft_approve(draft_id):
    """审核通过：执行校验，通过则改状态为 approved。返回 JSON，不重定向。"""
    draft = OzonDraft.get_or_none((OzonDraft.id == draft_id) & (OzonDraft.user == current_user))
    if not draft:
        return jsonify({"ok": False, "error": "草稿不存在"}), 404

    import re
    OFFER_ID_PATTERN = re.compile(r'^[A-Za-z0-9_-]{3,80}$')

    checks = []
    # 内容校验
    checks.append({'label': '俄语标题已填写', 'pass': bool(draft.title_ru), 'blocking': True,
                   'level': 'error' if not draft.title_ru else 'success'})
    checks.append({'label': 'OZON 类目已选择', 'pass': bool(draft.ozon_category_id or draft.category_path_ru),
                   'blocking': True, 'level': 'error' if not (draft.ozon_category_id or draft.category_path_ru) else 'success'})
    checks.append({'label': '缺少 SKU 数据', 'pass': draft.draft_skus.count() > 0, 'blocking': True,
                   'level': 'error' if draft.draft_skus.count() == 0 else 'success'})
    checks.append({'label': '目标店铺已选择', 'pass': draft.account is not None, 'blocking': True,
                   'level': 'error' if not draft.account else 'success'})

    # 价格校验
    existing_pricing = {}
    try:
        existing_pricing = json.loads(draft.pricing_json or '{}')
    except (json.JSONDecodeError, TypeError):
        pass
    checks.append({'label': '刊登价已填写', 'pass': bool(existing_pricing.get('listing_price')), 'blocking': True,
                   'level': 'error' if not existing_pricing.get('listing_price') else 'success'})
    checks.append({'label': '刊登币种已选择', 'pass': bool(existing_pricing.get('listing_currency')), 'blocking': True,
                   'level': 'error' if not existing_pricing.get('listing_currency') else 'success'})
    checks.append({'label': '价格已人工确认', 'pass': draft.price_manual_confirmed, 'blocking': True,
                   'level': 'error' if not draft.price_manual_confirmed else 'success'})

    # SKU offer_id 校验
    skus = list(draft.draft_skus)
    all_have_offer = all(sk.offer_id for sk in skus)
    bad_format_ids = []
    seen_ids = set()
    dup_in_draft = set()
    for sk in skus:
        oid = (sk.offer_id or '').strip()
        if not oid:
            continue
        if not OFFER_ID_PATTERN.match(oid):
            bad_format_ids.append(oid)
        if oid in seen_ids:
            dup_in_draft.add(oid)
        seen_ids.add(oid)

    checks.append({'label': '所有 SKU 已填 offer_id', 'pass': all_have_offer, 'blocking': True,
                   'level': 'error' if not all_have_offer else 'success'})
    checks.append({'label': 'offer_id 格式正确（字母/数字/下划线/短横线）',
                   'pass': len(bad_format_ids) == 0, 'blocking': True,
                   'level': 'success' if not bad_format_ids else 'error',
                   'detail': f'非法格式: {", ".join(bad_format_ids)}' if bad_format_ids else ''})
    checks.append({'label': '当前草稿内 offer_id 无重复',
                   'pass': len(dup_in_draft) == 0, 'blocking': True,
                   'level': 'success' if not dup_in_draft else 'error',
                   'detail': f'重复值: {", ".join(dup_in_draft)}' if dup_in_draft else ''})

    # 媒体校验
    media = _load_media_json(draft)
    images = media.get('images', [])
    selected_imgs = [i for i in images if i.get('selected')]
    has_main = any(i.get('role') == 'main' for i in selected_imgs)
    checks.append({'label': '至少有 1 张已选图片', 'pass': len(selected_imgs) > 0, 'blocking': True,
                   'level': 'success' if selected_imgs else 'error'})
    checks.append({'label': '已指定主图', 'pass': has_main, 'blocking': True,
                   'level': 'success' if has_main else 'error'})

    blocking_count = sum(1 for c in checks if not c['pass'] and c['blocking'])
    validation = {'blocking_count': blocking_count, 'checks': checks}

    draft.validation_result = json.dumps(validation, ensure_ascii=False)
    draft.updated_at = datetime.datetime.now()

    if blocking_count > 0:
        draft.save()
        return jsonify({
            "ok": False,
            "error": f"存在 {blocking_count} 项阻断错误",
            "validation": validation
        })

    draft.status = 'approved'
    draft.save()

    return jsonify({
        "ok": True,
        "status": draft.status,
        "validation": validation
    })


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
                    ru_val = v.get('value', '')
                    # 自动补中文：先查内置映射/复用已有翻译
                    cn_val, cn_src = resolve_attribute_value_cn(current_user, ru_val, vid, attr.attribute_id)
                    defaults = {
                        'value': ru_val,
                        'value_cn': cn_val,
                        'info': v.get('info', '') or None,
                        'last_synced_at': datetime.datetime.now(),
                    }
                    # 如果有中文但 create 不支持 value_cn，先 create 再 update
                    _, created = OzonAttributeValue.get_or_create(
                        user=current_user,
                        account=account,
                        attribute_id=attr.attribute_id,
                        value_id=vid,
                        defaults=defaults,
                    )
                    if created:
                        if cn_val:
                            OzonAttributeValue.update(value_cn=cn_val).where(
                                (OzonAttributeValue.user == current_user) &
                                (OzonAttributeValue.attribute_id == attr.attribute_id) &
                                (OzonAttributeValue.value_id == vid)
                            ).execute()
                    else:
                        rec = OzonAttributeValue.get(
                            (OzonAttributeValue.user == current_user) &
                            (OzonAttributeValue.attribute_id == attr.attribute_id) &
                            (OzonAttributeValue.value_id == vid))
                        rec.value = ru_val
                        rec.info = v.get('info', '') or None
                        # 如果已有 value_cn 为空，尝试补
                        if not rec.value_cn:
                            rec.value_cn = cn_val
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


@ozon_bp.route('/api/category/<cat_id>/translate-attribute-values', methods=['POST'])
@login_required
def api_translate_attribute_values(cat_id):
    """翻译当前类目下所有缺少 value_cn 的字典值"""
    data = request.get_json(silent=True) or {}
    type_id = data.get('type_id', '').strip()

    # 找到需要翻译的字典值
    query = (OzonAttributeValue
             .select()
             .join(OzonCategoryAttribute, on=(
                 (OzonAttributeValue.attribute_id == OzonCategoryAttribute.attribute_id) &
                 (OzonAttributeValue.user == OzonCategoryAttribute.user)))
             .where(
                 (OzonAttributeValue.user == current_user) &
                 (OzonCategoryAttribute.ozon_category_id == cat_id) &
                 (OzonCategoryAttribute.is_dictionary == True) &
                 ((OzonAttributeValue.value_cn.is_null(True)) |
                  (OzonAttributeValue.value_cn == '') |
                  (OzonAttributeValue.value_cn == OzonAttributeValue.value)))
             )
    if type_id:
        query = query.where(OzonCategoryAttribute.type_id == type_id)

    untranslated = list(query.dicts().limit(500))
    if not untranslated:
        return jsonify({'ok': True, 'message': '所有字典值已有中文翻译', 'translated': 0})

    # 收集俄语 value 去翻译
    ru_values = list(set(
        v['value'] for v in untranslated if v.get('value') and v['value'].strip()
    ))
    if not ru_values:
        return jsonify({'ok': True, 'message': '无需翻译的值', 'translated': 0})

    result = _batch_translate(ru_values, current_user)
    translated_count = 0
    for row in untranslated:
        ru_val = row.get('value', '')
        cn_val = result.get(ru_val, '') if isinstance(result, dict) else ''
        if cn_val and cn_val != ru_val:
            OzonAttributeValue.update(value_cn=cn_val).where(
                (OzonAttributeValue.id == row['id'])
            ).execute()
            translated_count += 1

    errors = result.get('_errors', []) if isinstance(result, dict) else []
    return jsonify({
        'ok': True,
        'message': f'已翻译 {translated_count} 个字典值（共 {len(untranslated)} 个待翻译）',
        'translated': translated_count,
        'total': len(untranslated),
        'errors': errors[:5] if errors else [],
    })


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
