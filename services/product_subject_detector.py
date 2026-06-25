"""
产品主体自动识别服务

使用已配置的视觉模型识别：
- 真正出售的主商品位置
- 真实附属配件
- 商品外部广告文字/Logo/人物/装饰

复用 VisionModelConfig 体系，不创建独立 API Key 配置。
"""
from __future__ import annotations

import json
import base64
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from flask import current_app

from models import (
    OzonSourceMedia, OzonSource, OzonSourceSku,
    OzonProductSubjectDetection, VisionModelConfig,
)
from crypto_utils import decrypt_api_key


DETECTION_PROMPT = """You are an ecommerce product subject detector.

Task: Identify the MAIN PRODUCT actually for sale in this image. Also identify real accessories, and regions that should be EXCLUDED from a transparent product cutout.

Context:
- Product title: {title}
- Category: {category}
- SKU count: {sku_count}

You MUST distinguish:
1. MAIN PRODUCT: The actual item being sold
2. REAL ACCESSORIES: Items included in the package (only if clearly visible and confirmed)
3. OTHER PRODUCTS: Different items not being sold in this listing
4. PERSON/HAND: Any human body parts
5. ADVERTISING TEXT: Large promotional text OUTSIDE the product
6. EXTERNAL LOGO/WATERMARK: Brand logos, platform watermarks OUTSIDE the product
7. BACKGROUND DECORATION: Color blocks, decorative lines, background graphics

CRITICAL RULES:
- Text ON the product screen, buttons, or body IS product structure - DO NOT exclude.
- Large promotional text OUTSIDE the product must be excluded.
- Do NOT guess accessories. Only mark items confirmed to be in the package.
- If unsure about the main product, set requires_confirmation=true and explain in warnings.
- All bbox coordinates must be in ORIGINAL IMAGE pixels.
- Output ONLY valid JSON, no markdown, no explanations.

Return JSON:
{{
  "image_width": {width},
  "image_height": {height},
  "main_product": {{
    "label": "product name",
    "bbox": [x1, y1, x2, y2],
    "confidence": 0.0,
    "description": "short description"
  }},
  "accessories": [],
  "exclude_regions": [
    {{
      "type": "advertising_text|external_logo|person|background_decoration|other_product",
      "bbox": [x1, y1, x2, y2],
      "confidence": 0.0,
      "description": "what this region contains"
    }}
  ],
  "uncertain": [],
  "warnings": [],
  "requires_confirmation": false
}}"""


def detect_product_subject(user, media: OzonSourceMedia) -> Dict[str, Any]:
    """调用视觉模型识别产品主体。

    返回识别 JSON，失败时返回 error。
    """
    # 获取视觉模型配置
    config = _get_vision_config(user)
    if not config:
        return {'error': '未配置启用的视觉模型，请在 /ozon/models 配置并启用视觉工具模型'}

    # 获取商品信息
    source = OzonSource.get_or_none(OzonSource.id == media.source_id)
    title = getattr(source, 'title_cn', '') or getattr(source, 'title', '') or 'product'
    category = getattr(source, 'category_cn', '') or ''
    sku_count = OzonSourceSku.select().where(OzonSourceSku.source == source).count() if source else 0

    # 加载图片
    img = _load_image(media)
    if not img:
        return {'error': '无法加载原图'}

    W, H = img.size

    # 编码图片
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    img_b64 = base64.b64encode(buf.getvalue()).decode()

    # 解密 API Key
    try:
        api_key = decrypt_api_key(config.api_key_encrypted)
    except Exception:
        api_key = config.api_key_encrypted

    prompt = DETECTION_PROMPT.format(
        title=title, category=category, sku_count=sku_count,
        width=W, height=H
    )

    # 调用视觉模型
    try:
        response_text, raw_json = _call_vision_api(config, api_key, img_b64, prompt)
    except Exception as e:
        return {'error': f'视觉模型调用失败: {str(e)[:200]}'}

    # 解析 JSON
    try:
        detection = _parse_detection_response(response_text, W, H)
    except Exception as e:
        return {'error': f'识别结果解析失败: {str(e)[:200]}', 'raw': response_text[:500]}

    # 保存检测记录
    confidence = None
    if detection.get('main_product'):
        confidence = detection['main_product'].get('confidence')

    OzonProductSubjectDetection.create(
        user=user, source=media.source_id, source_media=media,
        provider=config.provider, model_name=config.model_name,
        image_width=W, image_height=H,
        detection_json=json.dumps(detection, ensure_ascii=False),
        raw_response_json=raw_json,
        main_product_confidence=confidence,
        status='detected'
    )

    detection['source_media_id'] = media.id
    detection['provider'] = config.provider
    return detection


def _get_vision_config(user) -> Optional[VisionModelConfig]:
    return (VisionModelConfig
            .select()
            .where((VisionModelConfig.user == user) &
                   (VisionModelConfig.enabled == True) &
                   ~(VisionModelConfig.provider.startswith('img_gen_')))
            .first())


def _load_image(media: OzonSourceMedia):
    from PIL import Image
    import requests as req
    path = (media.local_path or '').replace('\\', '/')
    if path:
        for prefix in ['', 'uploads/']:
            p = Path(current_app.root_path) / prefix / path
            if p.exists():
                return Image.open(p).convert('RGB')
    if media.source_url and 'example.com' not in media.source_url:
        hdrs = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://detail.1688.com/'}
        resp = req.get(media.source_url, headers=hdrs, timeout=20)
        resp.raise_for_status()
        return Image.open(io.BytesIO(resp.content)).convert('RGB')
    return None


def _call_vision_api(config, api_key: str, img_b64: str, prompt: str) -> tuple:
    """调用视觉模型API，返回 (response_text, raw_json_string)。"""
    provider = config.provider or ''

    if 'qwen' in provider.lower() or 'dashscope' in (config.api_base or '').lower():
        return _call_qwen_vl(config, api_key, img_b64, prompt)

    # 默认 OpenAI 兼容格式
    base = (config.api_base or '').rstrip('/')
    if not base.endswith('/v1'):
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
            'max_tokens': 2000,
        },
        timeout=90
    )
    resp.raise_for_status()
    data = resp.json()
    text = data['choices'][0]['message']['content']
    return text, json.dumps(data, ensure_ascii=False)


def _call_qwen_vl(config, api_key: str, img_b64: str, prompt: str) -> tuple:
    """千问 VL 专用调用"""
    base = (config.api_base or '').rstrip('/')
    if 'dashscope' in base.lower() and '/compatible-mode' not in base:
        base += '/compatible-mode/v1'

    resp = requests.post(
        f'{base}/chat/completions',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={
            'model': config.model_name,
            'messages': [{'role': 'user', 'content': [
                {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{img_b64}'}},
                {'type': 'text', 'text': prompt}
            ]}],
            'max_tokens': 2000,
        },
        timeout=90
    )
    resp.raise_for_status()
    data = resp.json()
    text = data['choices'][0]['message']['content']
    return text, json.dumps(data, ensure_ascii=False)


def _parse_detection_response(text: str, W: int, H: int) -> Dict[str, Any]:
    """清洗和解析视觉模型返回的 JSON"""
    # 去掉可能的 markdown 代码块
    text = text.strip()
    if text.startswith('```'):
        lines = text.split('\n')
        lines = lines[1:] if lines[0].startswith('```') else lines
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines)

    detection = json.loads(text)

    # 校验 main_product bbox
    mp = detection.get('main_product')
    if mp and mp.get('bbox'):
        bbox = mp['bbox']
        if len(bbox) >= 4:
            mp['bbox'] = [_clamp(v, 0, W if i % 2 == 0 else H) for i, v in enumerate(bbox[:4])]
            if mp['bbox'][2] <= mp['bbox'][0] or mp['bbox'][3] <= mp['bbox'][1]:
                detection['warnings'].append('主商品 bbox 非法 (x2<=x1 或 y2<=y1)')
                mp['bbox'] = [0, 0, W, H]

    # 校验 exclude_regions，过滤无效框
    valid_excludes = []
    for er in detection.get('exclude_regions', []):
        bbox = er.get('bbox', [])
        if len(bbox) >= 4:
            x1, y1, x2, y2 = [_clamp(v, 0, W if i % 2 == 0 else H) for i, v in enumerate(bbox[:4])]
            area = (x2 - x1) * (y2 - y1)
            img_area = W * H
            # 跳过占全图90%以上的排除框（如"整个背景"）
            if area > img_area * 0.9:
                continue
            # 跳过退化的框（宽或高为0）
            if x2 <= x1 or y2 <= y1:
                continue
            er['bbox'] = [x1, y1, x2, y2]
            valid_excludes.append(er)
    detection['exclude_regions'] = valid_excludes

    detection['image_width'] = W
    detection['image_height'] = H
    return detection


def _clamp(v, lo, hi):
    return max(lo, min(hi, int(v)))
