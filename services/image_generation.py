"""
Multi-model image generation service for OZON ecommerce images.

Provides:
- Config discovery (img_gen_* providers from VisionModelConfig)
- Multi-model image generation via OpenAI-compatible Images API
- Local image saving from URL or base64

Supported models (all via OpenAI-compatible endpoints):
- Seedream 4.5 → 火山引擎 ARK (https://ark.cn-beijing.volces.com/api/v3)
- IMAGE NANO BANANA → standard OpenAI-compatible endpoint
- 通义万相 → DashScope (https://dashscope.aliyuncs.com/compatible-mode/v1)
- DALL-E 3 → OpenAI native
- 其他任何 OpenAI-compatible 图片模型
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from flask import current_app

from models import VisionModelConfig, OzonImageCandidate
from crypto_utils import decrypt_api_key


# ═══════════════════════════════════════════════════════════════
# 已知模型预设（size / extra_body / 特殊参数）
# ═══════════════════════════════════════════════════════════════

# 模型名匹配 → 默认参数。匹配时对 model_name 做小写包含检查。
KNOWN_MODEL_PRESETS: Dict[str, Dict[str, Any]] = {
    # ── OpenAI GPT Image 系列（最新，2025）──
    # gpt-image-1 / gpt-image-1.5 / gpt-image-1-mini
    # 走 /v1/images/generations，和 DALL-E 3 同接口但参数不同
    'gpt-image': {
        'default_size': '1024x1024',
        'portrait_size': '1024x1536',        # GPT Image 竖版尺寸（不含 1792）
        'max_prompt_chars': 4000,
        'extra_body': {
            'quality': 'high',               # low / medium / high（不是 standard/hd）
            'output_format': 'png',          # png / jpeg / webp
        },
    },
    # ── DALL-E 3（旧，向后兼容）──
    'dall-e': {
        'default_size': '1024x1024',
        'portrait_size': '1024x1792',
        'max_prompt_chars': 4000,
        'extra_body': {'quality': 'standard'},
    },
    # ── Seedream 4.5 → 火山引擎 ARK ──
    'seedream': {
        'default_size': '2K',
        'portrait_size': '2K',
        'max_prompt_chars': 4000,
        'extra_body': {
            'watermark': False,
            'response_format': 'url',
            'sequential_image_generation': 'disabled',
        },
        'api_style': 'seedream',   # 独立适配器
    },
    # ── 通义万相 → DashScope ──
    'wanx': {
        'default_size': '1024*1024',         # 通义万相用 * 分隔
        'portrait_size': '768*1344',
        'max_prompt_chars': 4000,
        'extra_body': None,
    },
    # ── IMAGE NANO BANANA ──
    'banana': {
        'default_size': '1024x1024',
        'portrait_size': '1024x1536',
        'max_prompt_chars': 4000,
        'extra_body': None,
    },
    # ── 其他可灵 / 即梦等 ──
    'image-2': {
        'default_size': '1024x1024',
        'portrait_size': '1024x1024',
        'max_prompt_chars': 4000,
        'extra_body': None,
    },
    'kling': {
        'default_size': '1024x1024',
        'portrait_size': '1024x1024',
        'max_prompt_chars': 4000,
        'extra_body': None,
    },
    'jimeng': {
        'default_size': '1024x1024',
        'portrait_size': '1024x1024',
        'max_prompt_chars': 4000,
        'extra_body': None,
    },
}


def _match_model_preset(model_name: str) -> Optional[Dict[str, Any]]:
    """Match a model name against known presets. Returns preset dict or None."""
    lowered = (model_name or '').lower()
    for key, preset in KNOWN_MODEL_PRESETS.items():
        if key in lowered:
            return preset
    return None


# ═══════════════════════════════════════════════════════════════
# API Base URL 解析
# ═══════════════════════════════════════════════════════════════

def _resolve_api_base(config: VisionModelConfig) -> str:
    """Resolve OpenAI-compatible API base URL from a VisionModelConfig.

    Handles:
    - 火山引擎 ARK:  https://ark.cn-beijing.volces.com/api/v3  (already correct)
    - DashScope:      https://dashscope.aliyuncs.com → /compatible-mode/v1
    - 标准 OpenAI:    https://api.openai.com → /v1
    - 自定义:         https://your-proxy.com → /v1 (unless already versioned)
    """
    raw = (config.api_base or '').strip().rstrip('/')
    if not raw:
        return ''

    # Already ends with a version path like /v1, /v2, /v3, /v4
    if re.search(r'/v\d+$', raw):
        return raw

    # DashScope needs /compatible-mode/v1
    if 'dashscope.aliyuncs.com' in raw or 'dashscope' in raw.lower():
        return f'{raw}/compatible-mode/v1'

    # Default: append /v1 (works for OpenAI, most proxies, and ARK if misconfigured)
    return f'{raw}/v1'


# ═══════════════════════════════════════════════════════════════
# 配置发现
# ═══════════════════════════════════════════════════════════════

def get_image_generation_configs(user, selected_config_id=None):
    """Return available image generation model configs.

    Filters VisionModelConfig where provider starts with 'img_gen_'.

    Args:
        user: current_user
        selected_config_id: if set, return only that config (or empty list if not valid)

    Returns:
        list of VisionModelConfig, each with decrypted api_key attached as ._api_key
    """
    query = VisionModelConfig.select().where(
        (VisionModelConfig.user == user) &
        (VisionModelConfig.enabled == True) &
        (VisionModelConfig.provider.startswith('img_gen_'))
    )

    if selected_config_id is not None:
        try:
            query = query.where(VisionModelConfig.id == int(selected_config_id))
        except (ValueError, TypeError):
            pass

    configs = list(query)

    # Attach decrypted API key for convenience
    for cfg in configs:
        cfg._api_key = None
        cfg._api_base_url = ''
        if cfg.api_key_encrypted:
            try:
                cfg._api_key = decrypt_api_key(cfg.api_key_encrypted)
            except Exception:
                # 密钥可能是明文存储的（旧数据），直接使用
                cfg._api_key = cfg.api_key_encrypted
        cfg._api_base_url = _resolve_api_base(cfg)

    return configs


# ═══════════════════════════════════════════════════════════════
# 图片生成
# ═══════════════════════════════════════════════════════════════

def _parse_user_size_override(notes: Optional[str]) -> Optional[str]:
    """Extract user-specified image size from notes JSON field.

    Example notes: {"size": "1024x1536"}
    Returns None if no valid size found.
    """
    if not notes:
        return None
    try:
        data = json.loads(notes)
        if isinstance(data, dict) and 'size' in data:
            return str(data['size'])
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


def generate_image_with_config(
    config,
    prompt,
    negative_prompt=None,
    reference_images=None,
    generation_mode='reference',
):
    """Call a single image generation model via OpenAI-compatible Images API.

    Automatically detects model capabilities from KNOWN_MODEL_PRESETS
    and falls back to sensible defaults for unknown models.

    Args:
        config: VisionModelConfig with ._api_key and ._api_base_url attached
        prompt: the image prompt text
        negative_prompt: optional negative prompt (not used by all models)
        reference_images: list of dicts with 'path'/'source_url' for reference images
        generation_mode: 'reference' / 'text_only' / 'composite'

    Returns:
        dict with keys: image_url, image_base64, raw_response, provider, model_name,
                        generation_mode, reference_count, request_snapshot
    """
    import openai

    api_key = getattr(config, '_api_key', None)
    if not api_key and config.api_key_encrypted:
        try:
            api_key = decrypt_api_key(config.api_key_encrypted)
        except Exception:
            api_key = config.api_key_encrypted
    base_url = getattr(config, '_api_base_url', '') or _resolve_api_base(config)
    model_name = config.model_name or ''
    provider = config.provider or ''

    if not api_key:
        raise RuntimeError(f'No API key configured for model config id={config.id}')

    if not model_name:
        raise RuntimeError(f'No model name configured for model config id={config.id}')

    # ── 参考图处理 ──
    refs = _normalize_references(reference_images or [])
    has_refs = len(refs) > 0
    effective_mode = generation_mode if has_refs else 'text_only'

    # ── 匹配已知预设 ──
    preset = _match_model_preset(model_name)

    max_chars = preset['max_prompt_chars'] if preset else 4000
    extra_body = preset.get('extra_body') if preset else None

    # 尺寸：用户 notes 覆盖 > 预设竖版 > 预设默认 > 通用默认
    user_size = _parse_user_size_override(config.notes)
    if user_size:
        image_size = user_size
    elif preset:
        image_size = preset.get('portrait_size', preset.get('default_size', '1024x1024'))
    else:
        image_size = '1024x1024'

    # ── 构建请求快照 ──
    request_snapshot = {
        'model': model_name,
        'size': image_size,
        'prompt': prompt[:500],   # 截断保存
        'prompt_length': len(prompt),
        'negative_prompt': negative_prompt[:200] if negative_prompt else None,
        'generation_mode': effective_mode,
        'reference_count': len(refs),
        'reference_ids': [r.get('media_id') for r in refs],
        'provider': provider,
        'timestamp': str(datetime.datetime.now()),
    }

    # ── 构建 API 调用 ──
    is_seedream = preset and preset.get('api_style') == 'seedream' if preset else False

    if is_seedream:
        # ── Seedream 4.5 独立适配器：用 requests 直调 ARK API ──
        response, request_snapshot = _call_seedream_api(
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            prompt=prompt[:max_chars],
            image_size=image_size,
            refs=refs,
            extra_body=extra_body,
            request_snapshot=request_snapshot,
        )
    else:
        # ── 标准 OpenAI 兼容路径 ──
        client = openai.OpenAI(api_key=api_key, base_url=base_url)

        kwargs = dict(
            model=model_name,
            prompt=prompt[:max_chars],
            n=1,
            size=image_size,
        )

        if extra_body:
            kwargs['extra_body'] = dict(extra_body)

        use_edit_api = False
        if has_refs and _is_gpt_image_model(model_name, provider):
            primary_ref = refs[0]
            ref_path = primary_ref.get('path') or primary_ref.get('source_url')
            if ref_path:
                try:
                    kwargs['image'] = _open_reference_image(ref_path)
                    use_edit_api = True
                    request_snapshot['api_method'] = 'images.edit'
                except Exception as e:
                    request_snapshot['reference_error'] = str(e)[:200]
                    effective_mode = 'text_only'

        if use_edit_api:
            response = client.images.edit(**kwargs)
        else:
            if has_refs and not use_edit_api:
                kwargs.setdefault('extra_body', {})
                kwargs['extra_body']['reference_images'] = [
                    {'url': r.get('source_url', ''), 'role': r.get('role', '')}
                    for r in refs[:3]
                ]
            response = client.images.generate(**kwargs)

        request_snapshot['api_method'] = request_snapshot.get('api_method', 'images.generate')

    # ── 提取图片数据 ──
    image_data = response.data[0]

    img_url = getattr(image_data, 'url', None)
    b64_json = getattr(image_data, 'b64_json', None)

    if isinstance(image_data, dict):
        img_url = image_data.get('url')
        b64_json = image_data.get('b64_json')

    if not img_url and not b64_json:
        raw = response.model_dump() if hasattr(response, 'model_dump') else {}
        if isinstance(raw, dict):
            data_list = raw.get('data', [])
            if data_list:
                img_url = data_list[0].get('url') if isinstance(data_list[0], dict) else getattr(data_list[0], 'url', None)
                b64_json = data_list[0].get('b64_json') if isinstance(data_list[0], dict) else getattr(data_list[0], 'b64_json', None)

    if not img_url and not b64_json:
        raise RuntimeError(
            'Image provider returned neither url nor b64_json. '
            'Check API response format.'
        )

    return {
        'image_url': img_url,
        'image_base64': b64_json,
        'raw_response': response.model_dump() if hasattr(response, 'model_dump') else str(response),
        'provider': provider,
        'model_name': model_name,
        'generation_mode': effective_mode,
        'reference_count': len(refs),
        'request_snapshot': request_snapshot,
    }


def _call_seedream_api(
    api_key: str,
    base_url: str,
    model_name: str,
    prompt: str,
    image_size: str,
    refs: list,
    extra_body: dict,
    request_snapshot: dict,
):
    """Call Seedream 4.5 via Volcano ARK API directly (not via OpenAI SDK).

    ARK image generation endpoint: POST {base_url}/images/generations

    Required parameters:
      - model: doubao-seedream-4-5-251128
      - prompt: text prompt
      - size: "2K"
      - image: [url_or_base64, ...]   (reference images as array)
      - watermark: false
      - response_format: "url"
      - sequential_image_generation: "disabled"
    """
    # Build the full endpoint
    endpoint = base_url.rstrip('/') + '/images/generations'

    # ── Build request body ──
    body: Dict[str, Any] = {
        'model': model_name,
        'prompt': prompt,
        'size': image_size,
    }

    # Extra body params (watermark, response_format, etc.)
    if extra_body:
        body.update(extra_body)

    # Reference images → image array
    if refs:
        image_urls = []
        for r in refs:
            src = r.get('source_url') or r.get('path') or ''
            if src:
                image_urls.append(src)
        if image_urls:
            body['image'] = image_urls
            request_snapshot['api_method'] = 'seedream/images/generations'
            request_snapshot['reference_image_count'] = len(image_urls)
            request_snapshot['reference_image_urls'] = [
                u[:120] for u in image_urls[:3]
            ]
        else:
            request_snapshot['reference_error'] = 'Reference images found but no valid URLs'
    else:
        request_snapshot['reference_image_count'] = 0

    # Don't send n unless confirmed
    # body['n'] = 1

    request_snapshot['api_method'] = request_snapshot.get('api_method', 'seedream/images/generations')
    request_snapshot['endpoint'] = endpoint
    request_snapshot['body_keys'] = list(body.keys())

    # ── Make the HTTP request ──
    resp = requests.post(
        endpoint,
        json=body,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        timeout=90,
    )

    # ── Parse response ──
    if not resp.ok:
        error_text = resp.text[:500]
        raise RuntimeError(
            f'Seedream API error {resp.status_code}: {error_text}'
        )

    data = resp.json()

    # Extract image from response
    # ARK response format: {"data": [{"url": "..."}]} or similar
    img_url = None
    b64_json = None

    if isinstance(data, dict):
        data_list = data.get('data', [])
        if data_list and isinstance(data_list[0], dict):
            img_url = data_list[0].get('url')
            b64_json = data_list[0].get('b64_json')
        # Some models return url at top level
        img_url = img_url or data.get('url')

    if not img_url and not b64_json:
        raise RuntimeError(
            f'Seedream returned no image. Response keys: {list(data.keys()) if isinstance(data, dict) else type(data)}'
        )

    # Wrap in a fake response object compatible with the rest of the code
    _img_url = img_url
    _b64_json = b64_json

    class _FakeData:
        url = _img_url
        b64_json = _b64_json

    class _FakeResponse:
        data = [_FakeData()]

        @staticmethod
        def model_dump():
            return data

    return _FakeResponse(), request_snapshot


def _is_gpt_image_model(model_name: str, provider: str) -> bool:
    """Check if model supports images.edit() API."""
    lowered = (model_name or '').lower()
    return 'gpt-image' in lowered or 'dall-e' in lowered


def _normalize_references(references: list) -> list:
    """Clean and validate reference image list."""
    result = []
    for ref in (references or []):
        if isinstance(ref, dict):
            path = ref.get('path', '') or ''
            url = ref.get('source_url', '') or ''
            if path or url:
                result.append({
                    'media_id': ref.get('media_id'),
                    'path': path,
                    'source_url': url,
                    'role': ref.get('role', ''),
                })
    return result


def _open_reference_image(ref_path: str):
    """Open a reference image from local path or URL, return file-like object or URL string."""
    from urllib.parse import urlparse
    parsed = urlparse(ref_path)
    if parsed.scheme in ('http', 'https'):
        # Return URL directly — OpenAI SDK supports URL strings
        return ref_path
    # Local path
    p = Path(ref_path)
    if p.is_absolute() and p.exists():
        return open(str(p), 'rb')
    # Try relative to app root
    from flask import current_app
    abs_path = Path(current_app.root_path) / ref_path
    if abs_path.exists():
        return open(str(abs_path), 'rb')
    # Try uploads directory
    uploads_path = Path(current_app.root_path) / 'uploads' / ref_path
    if uploads_path.exists():
        return open(str(uploads_path), 'rb')
    raise FileNotFoundError(f'Reference image not found: {ref_path}')


# ═══════════════════════════════════════════════════════════════
# 本地保存
# ═══════════════════════════════════════════════════════════════

def _sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames."""
    return re.sub(r'[\\/:*?"<>|]', '_', name)


def save_generated_image(candidate, image_url=None, image_base64=None):
    """Download or decode an image and save to local filesystem.

    Path pattern:
        uploads/ai_generated/draft_{draft_id}/slot_{slot_order}/candidate_{id}_{provider}_{model}.png

    Sets candidate.local_path (relative to uploads dir) and saves.

    Args:
        candidate: OzonImageCandidate instance
        image_url: remote URL to download
        image_base64: base64-encoded image data

    Returns:
        str: absolute local path, or None on failure
    """
    draft_id = candidate.draft_id
    candidate_id = candidate.id or 0
    provider = _sanitize_filename(candidate.provider or 'unknown')
    model_name = _sanitize_filename(candidate.model_name or 'unknown')

    # Get slot_order safely — query by slot_id if relation not loaded
    try:
        slot_order = candidate.slot.slot_order
    except Exception:
        from models import OzonImageSlot
        slot_obj = OzonImageSlot.get_or_none(OzonImageSlot.id == candidate.slot_id)
        slot_order = slot_obj.slot_order if slot_obj else 0

    # Build directory: uploads/ai_generated/draft_<id>/slot_<order>/
    uploads_root = Path(current_app.root_path) / 'uploads' / 'ai_generated'
    slot_dir = uploads_root / f'draft_{draft_id}' / f'slot_{slot_order}'
    slot_dir.mkdir(parents=True, exist_ok=True)

    # Detect file extension from content
    ext = 'png'  # default
    image_bytes = None

    try:
        if image_url:
            image_bytes, content_type = _download_image_bytes(image_url)
            ext = _ext_from_content_type(content_type) or _ext_from_magic(image_bytes) or ext
        elif image_base64:
            image_bytes = base64.b64decode(image_base64)
            ext = _ext_from_magic(image_bytes) or ext
        else:
            return None

        filename = f'candidate_{candidate_id}_{provider}_{model_name}.{ext}'
        filepath = slot_dir / filename
        filepath.write_bytes(image_bytes)

        # Store relative path
        rel_path = str(filepath.relative_to(uploads_root))
        candidate.local_path = rel_path
        candidate.save()
        return str(filepath)

    except Exception as e:
        print(f'[IMG-SAVE-ERROR] candidate={candidate.id}: {e}')
        candidate.error_message = (candidate.error_message or '') + f' | save_failed: {str(e)[:200]}'
        candidate.save()
        return None


def _download_image_bytes(url: str):
    """Download image from URL, return (bytes, content_type)."""
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    content_type = resp.headers.get('Content-Type', '')
    return resp.content, content_type


def _ext_from_content_type(content_type: str):
    """ Map Content-Type to file extension. """
    ct = content_type.lower()
    if 'jpeg' in ct or 'jpg' in ct:
        return 'jpg'
    if 'png' in ct:
        return 'png'
    if 'webp' in ct:
        return 'webp'
    return None


def _ext_from_magic(data: bytes):
    """ Detect image format from magic bytes. """
    if len(data) < 4:
        return None
    if data[:4] == b'\x89PNG':
        return 'png'
    if data[:2] == b'\xff\xd8':
        return 'jpg'
    if data[:4] == b'RIFF' and len(data) >= 12 and data[8:12] == b'WEBP':
        return 'webp'
    return None
