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


def get_media_image_b64(media: OzonSourceMedia):
    """加载图片base64。优先local_path → 相对URL → 公网URL。返回(base64, W, H)或(None,0,0)"""
    from PIL import Image
    import requests as req
    path = (media.local_path or '').replace('\\', '/')
    # 1. local_path
    if path:
        for prefix in ['', 'uploads/']:
            p = Path(current_app.root_path) / prefix / path
            if p.exists():
                return _img_to_b64(Image.open(p).convert('RGB'))
    # 2. 站内相对URL
    url = media.source_url or ''
    if url.startswith('/'):
        # 尝试作为本地路径
        clean = url.lstrip('/')
        p = Path(current_app.root_path) / clean
        if p.exists():
            return _img_to_b64(Image.open(p).convert('RGB'))
    # 3. 公网URL
    if url and url.startswith('http') and 'example.com' not in url:
        try:
            resp = req.get(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://detail.1688.com/'}, timeout=20)
            resp.raise_for_status()
            return _img_to_b64(Image.open(io.BytesIO(resp.content)).convert('RGB'))
        except Exception:
            pass
    return None, 0, 0


def _img_to_b64(img):
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    return base64.b64encode(buf.getvalue()).decode(), img.size[0], img.size[1]


def analyze_product_image(
    user, media: OzonSourceMedia, task_type: str = 'fact_extraction',
    source_skus: Optional[List] = None, fact: Optional[Any] = None,
    force: bool = False,
) -> dict:
    """统一入口：分析商品图片。返回 {'ok':True/False, 'facts':N, 'error':''} """
    config = get_active_config(user)
    if not config:
        return {'ok': False, 'error': '未配置启用的视觉模型', 'facts': 0}

    # 任务指纹去重
    img_b64, W, H = get_media_image_b64(media)
    if not img_b64:
        return {'ok': False, 'error': '无法加载图片', 'facts': 0}

    task_hash = hashlib.sha256(
        f'{media.id}|{task_type}|{config.provider}|{config.model_name}'.encode()
    ).hexdigest()[:16]
    if not force:
        existing = ImageAnalysisJob.select().where(
            (ImageAnalysisJob.user == user) & (ImageAnalysisJob.media == media) &
            (ImageAnalysisJob.task_type == task_type) & (ImageAnalysisJob.status == 'success')
        ).first()
        if existing:
            return {'ok': True, 'facts': 0, 'skipped': True, 'message': '已识别，跳过'}

    # 调用视觉模型
    api_key = _decrypt_key(config)
    try:
        response_text, raw_json = _call_vision_api(config, api_key, img_b64, PRODUCT_IMAGE_PROMPT)
        parsed = _parse_vision_response(response_text, W, H)
    except Exception as e:
        _create_job(user, media, task_type, config, status='failed', error=str(e)[:500])
        return {'ok': False, 'error': str(e)[:200], 'facts': 0}

    job = _create_job(user, media, task_type, config, status='success', response=raw_json, parsed=parsed)
    count = _save_image_facts(user, media, job, parsed, source_skus, fact)
    return {'ok': True, 'facts': count, 'media_id': media.id}


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


def _save_image_facts(user, media, job, parsed, source_skus, fact=None):
    """写入 ImageFact + ProductFactEvidence。返回写入数量。"""
    facts = parsed.get('facts', [])
    count = 0
    for f in facts:
        field_path = f.get('field_path', '')
        value = str(f.get('value', ''))[:2000]
        confidence = f.get('confidence', 0)
        status = f.get('status', 'extracted')
        if confidence < 0.7 and status != 'inferred':
            status = 'inferred'

        # ImageFact
        ImageFact.create(user=user, image_analysis_job=job, media=media,
            field_path=field_path, value=value,
            evidence_text=f.get('evidence_text', '')[:1000],
            confidence=confidence, requires_manual_confirmation=(confidence < 0.85))
        count += 1

        # ProductFactEvidence（通过已有 fact 参数传入）
        if fact:
            evidence_hash = _hash_evidence(fact.id, field_path, value, 'image', str(media.id))
            if not ProductFactEvidence.select().where(
                (ProductFactEvidence.user == user) & (ProductFactEvidence.fact == fact) &
                (ProductFactEvidence.evidence_hash == evidence_hash)
            ).exists():
                ProductFactEvidence.create(
                    user=user, fact=fact, field_path=field_path, evidence_type='image',
                    fact_status=status, confidence=confidence, source_type='image',
                    media=media,
                    value_json=value, evidence_hash=evidence_hash)
                count += 1
    return count


def _hash_evidence(fact_id, field_path, value, etype, identifier):
    raw = f'{fact_id}|{field_path}|{value}|{etype}|{identifier}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
