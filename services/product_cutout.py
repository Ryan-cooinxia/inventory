"""
产品母图服务 — 自动抠图 + 质量检查

支持后端:
- rembg (本地, 免费, 默认)
- dashscope (通义视觉分割, API)
- manual (人工蒙版)

流程:
原始图片 → 评分筛选 → rembg 抠图 → 质量检查 → 保存透明PNG → 人工确认
"""
from __future__ import annotations

import json
import io
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw
from flask import current_app

from models import OzonSourceMedia, OzonProductCutout, OzonSourceSku, db


CUTOUT_DIR = 'cutouts'


def _cutout_root() -> Path:
    return Path(current_app.root_path) / 'uploads' / CUTOUT_DIR


def _cutout_dir(media_id: int) -> Path:
    d = _cutout_root() / str(media_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ═══════════════════════════════════════════════════════════════
# 评分 — 判断图片是否适合抠图
# ═══════════════════════════════════════════════════════════════

def score_media_for_cutout(user, media: OzonSourceMedia) -> Dict[str, Any]:
    """评估单张 OzonSourceMedia 是否适合抠图。

    不调用外部 API，基于元数据和图片属性本地评分。
    返回 0-100 的 suitability_score 和详细维度。
    """
    scores = {
        'product_completeness': 80,   # 默认（无法自动判断）
        'occlusion': 0,                # 0=无遮挡, 100=完全遮挡
        'background_complexity': 50,   # 默认中等
        'resolution': 80,
        'has_person': False,
        'has_text_overlay': getattr(media, 'has_text', False) or False,
        'has_multiple_products': False,
        'suitable_for_cutout': True,
    }

    # 分辨率评分
    w = getattr(media, 'width', None) or 0
    h = getattr(media, 'height', None) or 0
    if w and h:
        pixels = w * h
        if pixels > 2000000:      scores['resolution'] = 95
        elif pixels > 1000000:    scores['resolution'] = 85
        elif pixels > 500000:     scores['resolution'] = 70
        elif pixels > 100000:     scores['resolution'] = 50
        else:                     scores['resolution'] = 30

    # 合规状态扣分
    compliance = getattr(media, 'compliance_status', '') or ''
    if compliance == 'rejected':
        scores['suitable_for_cutout'] = False
    elif compliance == 'needs_review':
        scores['product_completeness'] -= 20

    # 图片角色
    role = getattr(media, 'role', '') or ''
    if role == 'main':
        scores['product_completeness'] += 10
    elif role == 'sku':
        scores['product_completeness'] += 5
    elif role in ('detail', 'scene'):
        scores['product_completeness'] -= 10

    # 综合评分
    base = (
        scores['product_completeness'] * 0.4
        + scores['resolution'] * 0.3
        + (100 - scores['background_complexity']) * 0.2
        + (100 - scores['occlusion']) * 0.1
    )
    scores['suitability_score'] = max(0, min(100, int(base)))
    scores['suitable_for_cutout'] = scores['suitable_for_cutout'] and scores['suitability_score'] >= 50

    return scores


def find_best_media_for_cutout(user, source_id: int, max_count: int = 5) -> List[Dict[str, Any]]:
    """为指定 source 推荐最适合抠图的图片列表。"""
    media_list = list(OzonSourceMedia.select().where(
        (OzonSourceMedia.user == user) &
        (OzonSourceMedia.source_id == source_id) &
        (OzonSourceMedia.compliance_status != 'rejected')
    ).order_by(OzonSourceMedia.id))

    results = []
    for m in media_list:
        score = score_media_for_cutout(user, m)
        if score['suitable_for_cutout']:
            results.append({
                'media_id': m.id,
                'role': m.role or '',
                'local_path': m.local_path or '',
                'source_url': m.source_url or '',
                'width': m.width,
                'height': m.height,
                **score,
            })

    # 按评分降序
    results.sort(key=lambda x: x['suitability_score'], reverse=True)
    return results[:max_count]


# ═══════════════════════════════════════════════════════════════
# 抠图核心
# ═══════════════════════════════════════════════════════════════

def create_product_cutout(
    user,
    media: OzonSourceMedia,
    provider: str = 'rembg',
    sku: Optional[OzonSourceSku] = None,
) -> Dict[str, Any]:
    """对一张图片执行抠图，生成透明 PNG。

    Args:
        user: current_user
        media: OzonSourceMedia 记录
        provider: rembg / dashscope / manual
        sku: 关联的 SKU（可选）

    Returns:
        dict with ok, source_media_id, transparent_path, mask_path, preview_path,
             quality_score, warnings
    """
    # 检查是否已有成功的抠图
    existing = (OzonProductCutout
                .select()
                .where((OzonProductCutout.user == user) &
                       (OzonProductCutout.source_media == media) &
                       (OzonProductCutout.provider == provider) &
                       (OzonProductCutout.status == 'generated'))
                .first())
    if existing:
        return {
            'ok': True,
            'cutout_id': existing.id,
            'source_media_id': media.id,
            'transparent_path': existing.transparent_path,
            'mask_path': existing.mask_path,
            'preview_path': existing.preview_path,
            'quality_score': existing.quality_score,
            'warnings': [],
        }

    # 加载原图
    img = _load_media_image(media)
    if not img:
        return {'ok': False, 'error': 'Cannot load source image'}

    # 抠图
    if provider == 'rembg':
        transparent, mask = _cutout_rembg(img)
    elif provider == 'dashscope':
        return {'ok': False, 'error': 'DashScope cutout not yet implemented'}
    else:
        return {'ok': False, 'error': f'Unknown provider: {provider}'}

    if not transparent:
        return {'ok': False, 'error': 'Cutout produced empty result'}

    # 质量检查
    quality = _check_cutout_quality(transparent, mask, img)
    warnings = quality.get('warnings', [])

    # 保存文件
    out_dir = _cutout_dir(media.id)
    transparent_path = out_dir / f'cutout_{provider}.png'
    mask_path = out_dir / f'mask_{provider}.png'
    preview_path = out_dir / f'preview_{provider}.jpg'

    transparent.save(transparent_path, 'PNG')
    if mask:
        mask.save(mask_path, 'PNG')

    # 预览图：棋盘格背景
    preview = _make_checkerboard_preview(transparent, (400, 400))
    preview.save(preview_path, 'JPEG', quality=85)

    # 相对路径
    rel_transparent = str(transparent_path.relative_to(_cutout_root())).replace('\\', '/')
    rel_mask = str(mask_path.relative_to(_cutout_root())).replace('\\', '/') if mask else None
    rel_preview = str(preview_path.relative_to(_cutout_root())).replace('\\', '/')

    # 保存数据库
    cutout = OzonProductCutout.create(
        user=user,
        source=media.source,
        source_media=media,
        source_sku=sku,
        transparent_path=rel_transparent,
        mask_path=rel_mask,
        preview_path=rel_preview,
        provider=provider,
        quality_score=quality.get('score'),
        quality_json=json.dumps(quality, ensure_ascii=False),
        status='generated' if quality.get('pass', True) else 'pending',
    )

    return {
        'ok': True,
        'cutout_id': cutout.id,
        'source_media_id': media.id,
        'transparent_path': rel_transparent,
        'mask_path': rel_mask,
        'preview_path': rel_preview,
        'quality_score': quality.get('score'),
        'warnings': warnings,
    }


# ═══════════════════════════════════════════════════════════════
# 抠图后端实现
# ═══════════════════════════════════════════════════════════════

def _cutout_rembg(img: Image.Image) -> Tuple[Optional[Image.Image], Optional[Image.Image]]:
    """rembg 抠图，返回 (透明图, mask图)"""
    from rembg import remove, new_session
    import numpy as np

    # 转 RGB 保证兼容
    if img.mode not in ('RGB', 'RGBA'):
        img = img.convert('RGB')

    try:
        # rembg remove 返回 RGBA
        output = remove(img, post_process_mask=True)
        transparent = output.convert('RGBA')

        # 提取 mask (alpha channel)
        alpha = transparent.split()[-1]
        mask = Image.new('L', transparent.size, 0)
        mask.paste(alpha)

        return transparent, mask
    except Exception as e:
        print(f'[rembg] Error: {e}')
        return None, None


# ═══════════════════════════════════════════════════════════════
# 质量检查
# ═══════════════════════════════════════════════════════════════

def _check_cutout_quality(
    transparent: Image.Image,
    mask: Optional[Image.Image],
    original: Image.Image,
) -> Dict[str, Any]:
    """检查抠图质量。返回 score/pass/warnings。"""
    warnings = []
    check_items = {
        'edge_residual': True,      # 边缘无残留
        'completeness': True,       # 商品完整
        'white_clipping': False,    # 白色商品未被吃掉
        'person_hand': False,       # 无残留人手
    }

    if mask:
        import numpy as np
        mask_arr = np.array(mask)

        # 检查主体占比（太低 = 可能抠坏了）
        total = mask_arr.size
        foreground = np.count_nonzero(mask_arr)
        fg_ratio = foreground / total if total > 0 else 0

        if fg_ratio < 0.05:
            warnings.append('主体占比过低 (<5%)，可能抠图失败')
            check_items['completeness'] = False
        elif fg_ratio > 0.95:
            warnings.append('主体占比过高 (>95%)，可能保留了背景')

        # 边缘密度检查（过于复杂的边缘可能有问题）
        if mask_arr.shape[0] > 0 and mask_arr.shape[1] > 0:
            edge_pixels = np.sum(np.abs(np.diff(mask_arr.astype(int), axis=1)))
            edge_density = edge_pixels / (mask_arr.shape[0] * mask_arr.shape[1])
            if edge_density > 0.3:
                warnings.append('边缘复杂度过高，可能有噪点残留')

    # 评分
    score = 90
    if not check_items['completeness']:
        score -= 30
    if not check_items['edge_residual']:
        score -= 20
    for w in warnings:
        score -= 5
    score = max(0, min(100, score))

    return {
        'score': score,
        'pass': score >= 70,
        'warnings': warnings,
        'checks': check_items,
    }


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _load_media_image(media: OzonSourceMedia) -> Optional[Image.Image]:
    """从 OzonSourceMedia 加载图片。优先本地路径，其次远程下载。"""
    import requests

    # 1. 本地路径
    path = (media.local_path or '').replace('\\', '/')
    if path:
        # 路径可能已包含 uploads 前缀
        abs_path = Path(current_app.root_path) / path
        if not abs_path.exists():
            abs_path = Path(current_app.root_path) / 'uploads' / path
        if abs_path.exists():
            return Image.open(abs_path).convert('RGB')

    # 2. 远程 URL（带防盗链绕过 headers）
    if media.source_url:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://detail.1688.com/',
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        }
        try:
            resp = requests.get(media.source_url, headers=headers, timeout=20)
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content)).convert('RGB')
        except Exception as e:
            print(f'[cutout] Cannot load URL for media {media.id}: {str(e)[:150]}')

    return None


def _make_checkerboard_preview(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """生成棋盘格背景的预览图"""
    thumb = img.copy()
    thumb.thumbnail(size, Image.LANCZOS)

    # 棋盘格背景
    bg = Image.new('RGBA', thumb.size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(bg)
    tile = 16
    for y in range(0, thumb.size[1], tile):
        for x in range(0, thumb.size[0], tile):
            if (x // tile + y // tile) % 2 == 0:
                draw.rectangle([x, y, x + tile - 1, y + tile - 1], fill=(200, 200, 200, 255))

    # 合成
    bg.paste(thumb, (0, 0), thumb)
    return bg.convert('RGB')
