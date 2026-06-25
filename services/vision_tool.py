"""
视觉工具模型服务 V2 — 真实 API 调用

负责调用视觉模型 API 对商品图片执行：
- analyze_product_image: 商品多模态理解(主商品/配件/SKU候选/事实提取)
- 图片合规检查
- 事实补充

统一入口复用 VisionModelConfig，不创建独立 API 配置。
"""
import json
import io
import base64
import datetime
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from flask import current_app

from models import (
    db, OzonSourceMedia, OzonSource, OzonSourceSku,
    VisionModelConfig, ImageAnalysisJob, ImageFact,
    ProductFactEvidence, ProductFact,
)
from crypto_utils import decrypt_api_key

PRODUCT_IMAGE_PROMPT = """You are an ecommerce product image analyst.

Analyze this product image and return structured JSON. Focus on EXTRACTABLE FACTS only — do not guess.

Task:
1. Identify the main product and any visible accessories.
2. Identify SKU-relevant attributes (color, size, style, bundle quantity).
3. Extract visible facts (material, dimensions, text on product, ports, buttons, package contents).
4. Flag anything you are UNCERTAIN about.

Rules:
- Only report what you SEE in the image.
- Do NOT infer brand, model, or specifications from memory.
- If text is visible, transcribe it exactly (OCR-like).
- Low confidence items must go in "uncertain", not "facts".
- bbox coordinates must be in ORIGINAL IMAGE pixels [x1, y1, x2, y2].
- Output ONLY valid JSON, no markdown.

Return JSON:
{
  "schema_version": "1.0",
  "image_role": "main|sku|detail|scene",
  "main_product": {
    "visible": true,
    "description": "short description",
    "bbox": [x1, y1, x2, y2],
    "condition": "完整|部分遮挡"
  },
  "accessories": [],
  "sku_candidates": [
    {
      "color_cn": "",
      "color_ru": "",
      "size_cn": "",
      "style_cn": "",
      "bundle_quantity": 1,
      "confidence": 0.0
    }
  ],
  "facts": [
    {
      "field_path": "material|weight|dimensions|functions|package_contents|...",
      "value": "fact value",
      "status": "extracted|inferred",
      "confidence": 0.0,
      "evidence_text": "what in the image supports this",
      "bbox": [x1, y1, x2, y2],
      "applicable_sku_orders": []
    }
  ],
  "uncertain": [],
  "warnings": []
}"""


def get_active_config(user) -> Optional[VisionModelConfig]:
    return (VisionModelConfig
            .select()
            .where((VisionModelConfig.user == user) &
                   (VisionModelConfig.enabled == True) &
                   ~(VisionModelConfig.provider.startswith('img_gen_')))
            .first())


def analyze_product_image(
    user,
    media: OzonSourceMedia,
    task_type: str = 'fact_extraction',
    source_skus: Optional[List[OzonSourceSku]] = None,
) -> Optional[dict]:
    """统一入口：分析商品图片，返回结构化事实。

    Args:
        user: current_user
        media: 图片记录
        task_type: fact_extraction / sku_image / compliance_check
        source_skus: 关联的源 SKU 列表（用于 SKU 归属判断）

    Returns:
        vision_result dict 或 None(失败)
    """
    config = get_active_config(user)
    if not config:
        return None

    # 加载图片
    img_b64, W, H = _load_image_base64(media)
    if not img_b64:
        return None

    # 调用视觉模型
    api_key = _decrypt_key(config)
    try:
        response_text, raw_json = _call_vision_api(config, api_key, img_b64, PRODUCT_IMAGE_PROMPT)
    except Exception as e:
        _create_job(user, media, task_type, config, status='failed', error=str(e)[:500])
        return None

    # 解析
    try:
        parsed = _parse_vision_response(response_text, W, H)
    except Exception as e:
        _create_job(user, media, task_type, config, status='failed', error=f'Parse error: {str(e)[:500]}')
        return None

    # 保存 ImageAnalysisJob
    job = _create_job(user, media, task_type, config,
                      status='success', response=raw_json, parsed=parsed)

    # 将事实写入 ImageFact 和 ProductFactEvidence
    _save_image_facts(user, media, job, parsed, source_skus)

    return parsed


def analyze_image(media: OzonSourceMedia, task_type: str, user) -> Optional[dict]:
    """兼容旧接口"""
    return analyze_product_image(user, media, task_type)


def analyze_batch(media_list: list, task_type: str, user) -> list:
    """批量分析。单张失败不影响其他图片。"""
    config = get_active_config(user)
    if not config:
        return [None] * len(media_list)

    skus = list(OzonSourceSku.select().where(
        (OzonSourceSku.user == user) &
        (OzonSourceSku.source == media_list[0].source if media_list else 0)
    ))

    results = []
    for media in media_list:
        try:
            result = analyze_product_image(user, media, task_type, skus)
            results.append(result)
        except Exception as e:
            print(f'[vision_tool] batch error media={media.id}: {str(e)[:100]}')
            results.append(None)
    return results


def check_image_compliance(media: OzonSourceMedia, user) -> dict:
    """快速合规检查。"""
    result = analyze_product_image(user, media, 'compliance_check')
    if not result:
        return {'ozon_ready': False, 'error': '视觉模型不可用'}
    return result.get('compliance', {'ozon_ready': True})


# ═══════════════════════════════════════════════════════════════
# 内部实现
# ═══════════════════════════════════════════════════════════════

def _load_image_base64(media: OzonSourceMedia):
    from PIL import Image
    import requests as req
    path = (media.local_path or '').replace('\\', '/')
    if path:
        for prefix in ['', 'uploads/']:
            p = Path(current_app.root_path) / prefix / path
            if p.exists():
                img = Image.open(p).convert('RGB')
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=85)
                return base64.b64encode(buf.getvalue()).decode(), img.size[0], img.size[1]
    if media.source_url and 'example.com' not in media.source_url:
        try:
            resp = req.get(media.source_url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://detail.1688.com/'
            }, timeout=20)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content)).convert('RGB')
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            return base64.b64encode(buf.getvalue()).decode(), img.size[0], img.size[1]
        except Exception:
            pass
    return None, 0, 0


def _decrypt_key(config) -> str:
    try:
        return decrypt_api_key(config.api_key_encrypted)
    except Exception:
        return config.api_key_encrypted or ''


def _call_vision_api(config, api_key: str, img_b64: str, prompt: str) -> tuple:
    provider = config.provider or ''
    base = (config.api_base or '').rstrip('/')
    if 'dashscope' in base.lower() and '/compatible-mode' not in base:
        base += '/compatible-mode/v1'
    elif not base.endswith('/v1'):
        base += '/v1'

    resp = requests.post(
        f'{base}/chat/completions',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={
            'model': config.model_name,
            'messages': [{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{img_b64}'}},
                {'type': 'text', 'text': prompt}
            ]}],
            'max_tokens': 3000,
        },
        timeout=120
    )
    resp.raise_for_status()
    data = resp.json()
    text = data['choices'][0]['message']['content']
    return text, json.dumps(data, ensure_ascii=False)


def _parse_vision_response(text: str, W: int, H: int) -> dict:
    text = text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        lines = lines[1:] if lines[0].startswith('```') else lines
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines)
    return json.loads(text)


def _create_job(user, media, task_type, config, status='success',
                response=None, parsed=None, error=None):
    return ImageAnalysisJob.create(
        user=user,
        media=media,
        source=media.source,
        task_type=task_type,
        provider=config.provider,
        model_name=config.model_name,
        status=status,
        request_json=json.dumps({'task_type': task_type, 'model': config.model_name}, ensure_ascii=False),
        response_json=response,
        parsed_json=json.dumps(parsed, ensure_ascii=False) if parsed else None,
        error_message=error,
    )


def _save_image_facts(user, media, job, parsed, source_skus):
    """将识别结果写入 ImageFact 和 ProductFactEvidence，不直接覆盖 ProductFact。"""
    facts = parsed.get('facts', [])
    if not facts:
        return

    for f in facts:
        field_path = f.get('field_path', '')
        value = f.get('value', '')
        confidence = f.get('confidence', 0)
        status = f.get('status', 'extracted')
        bbox = f.get('bbox')
        sku_orders = f.get('applicable_sku_orders', [])

        # 低于阈值 → inferred
        if confidence < 0.7 and status != 'inferred':
            status = 'inferred'

        # 找匹配的 fact_sku
        fact_sku = None
        if sku_orders and source_skus:
            for sku in source_skus:
                if sku.source_order in sku_orders:
                    fact_sku = sku
                    break

        # 写 ImageFact
        ImageFact.create(
            user=user,
            image_analysis_job=job,
            media=media,
            field_path=field_path,
            value=str(value)[:2000],
            evidence_text=f.get('evidence_text', '')[:1000],
            confidence=confidence,
            requires_manual_confirmation=(confidence < 0.85),
        )

        # 写 ProductFactEvidence（如果有关联的 fact）
        fact = None
        # 尝试找已存在的 ProductFact
        if media.source:
            from models import ProductFact as PF
            fact = PF.select().where(
                (PF.user == user) & (PF.source_id == media.source_id)
            ).first()

        if fact:
            source_locator = json.dumps({'bbox': bbox, 'image_role': parsed.get('image_role', '')}, ensure_ascii=False) if bbox else None
            evidence_hash = _hash_evidence(fact.id, field_path, value, 'image', str(media.id))
            existing = ProductFactEvidence.select().where(
                (ProductFactEvidence.user == user) &
                (ProductFactEvidence.fact == fact) &
                (ProductFactEvidence.evidence_hash == evidence_hash)
            ).first()
            if not existing:
                ProductFactEvidence.create(
                    user=user, fact=fact, fact_sku=fact_sku,
                    field_path=field_path, evidence_type='image',
                    fact_status=status, confidence=confidence,
                    source_type='image', source_locator_json=source_locator,
                    media=media, source=media.source,
                    value_json=str(value)[:2000],
                    evidence_hash=evidence_hash,
                )


def _hash_evidence(fact_id, field_path, value, etype, identifier):
    raw = f'{fact_id}|{field_path}|{value}|{etype}|{identifier}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
