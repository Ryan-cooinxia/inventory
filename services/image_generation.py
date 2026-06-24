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
        'default_size': '1024x1024',
        'portrait_size': '1024x1536',
        'max_prompt_chars': 4000,
        'extra_body': None,
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
            cfg._api_key = decrypt_api_key(cfg.api_key_encrypted)
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


def generate_image_with_config(config, prompt, negative_prompt=None):
    """Call a single image generation model via OpenAI-compatible Images API.

    Automatically detects model capabilities from KNOWN_MODEL_PRESETS
    and falls back to sensible defaults for unknown models.

    Args:
        config: VisionModelConfig with ._api_key and ._api_base_url attached
        prompt: the image prompt text
        negative_prompt: optional negative prompt (not used by all models)

    Returns:
        dict with keys: image_url, image_base64, raw_response, provider, model_name
    """
    import openai

    api_key = getattr(config, '_api_key', None) or decrypt_api_key(config.api_key_encrypted or '')
    base_url = getattr(config, '_api_base_url', '') or _resolve_api_base(config)
    model_name = config.model_name or ''
    provider = config.provider or ''

    if not api_key:
        raise RuntimeError(f'No API key configured for model config id={config.id}')

    if not model_name:
        raise RuntimeError(f'No model name configured for model config id={config.id}')

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

    # ── 构建 API 调用参数 ──
    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    kwargs = dict(
        model=model_name,
        prompt=prompt[:max_chars],
        n=1,
        size=image_size,
    )

    # 部分模型支持 extra_body（如 DALL-E 的 quality 参数）
    if extra_body:
        kwargs['extra_body'] = extra_body

    # 部分模型支持 negative_prompt（通过 extra_body 传入）
    # 如果 preset 明确标记 supports_negative，或用户配置了，则传入
    if negative_prompt and preset and preset.get('supports_negative'):
        kwargs.setdefault('extra_body', {})
        kwargs['extra_body']['negative_prompt'] = negative_prompt

    # ── 调用 API ──
    response = client.images.generate(**kwargs)

    # ── 提取图片数据 ──
    image_data = response.data[0]

    img_url = getattr(image_data, 'url', None)
    b64_json = getattr(image_data, 'b64_json', None)

    if isinstance(image_data, dict):
        img_url = image_data.get('url')
        b64_json = image_data.get('b64_json')

    if not img_url and not b64_json:
        # 有些模型返回不同结构，尝试从 response 直接提取
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
    }


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

    filename = f'candidate_{candidate_id}_{provider}_{model_name}.png'
    filepath = slot_dir / filename

    try:
        if image_url:
            _download_image(image_url, filepath)
        elif image_base64:
            _decode_base64_image(image_base64, filepath)
        else:
            return None

        # Store relative path: ai_generated/draft_12/slot_1/candidate_3_xxx.png
        rel_path = str(filepath.relative_to(uploads_root))
        candidate.local_path = rel_path
        candidate.save()
        return str(filepath)

    except Exception as e:
        print(f'[IMG-SAVE-ERROR] candidate={candidate.id}: {e}')
        candidate.error_message = (candidate.error_message or '') + f' | save_failed: {str(e)[:200]}'
        candidate.save()
        return None


def _download_image(url: str, filepath: Path):
    """Download image from URL to local path."""
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    filepath.write_bytes(resp.content)


def _decode_base64_image(b64_data: str, filepath: Path):
    """Decode base64 string and write to file."""
    filepath.write_bytes(base64.b64decode(b64_data))
