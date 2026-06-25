"""
产品母图服务 V3 — 只分割、不重绘

rembg 只用于生成 mask，产品像素从原图直接取，禁止 AI 重绘。

工作流:
原图 → 用户框选 → rembg only_mask → 蒙版清理 → 原图+mask合成 → 像素验证 → 人工确认

后端:
- rembg_crop (默认): bbox裁剪+rembg mask+框外透明
- rembg_full: 简单白底图全图rembg mask
- manual: 人工蒙版
"""
from __future__ import annotations

import json
import io
import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw
import numpy as np
from flask import current_app

from models import OzonSourceMedia, OzonProductCutout, OzonSourceSku, db


CUTOUT_DIR = 'cutouts'
TARGET_TYPES = ['main_product', 'accessory', 'unwanted_text', 'unwanted_logo',
                'person', 'background_decoration', 'other_product']


def _cutout_root() -> Path:
    return Path(current_app.root_path) / 'uploads' / CUTOUT_DIR


def _cutout_dir(media_id: int) -> Path:
    d = _cutout_root() / str(media_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ═══════════════════════════════════════════════════════════════
# 评分
# ═══════════════════════════════════════════════════════════════

def score_media_for_cutout(user, media: OzonSourceMedia) -> Dict[str, Any]:
    scores = {
        'product_completeness': 80, 'occlusion': 0,
        'background_complexity': 50, 'resolution': 80,
        'has_person': False,
        'has_text_overlay': getattr(media, 'has_text', False) or False,
        'has_multiple_products': False, 'suitable_for_cutout': True,
    }
    w, h = getattr(media, 'width', None) or 0, getattr(media, 'height', None) or 0
    if w and h:
        p = w * h
        if p > 2000000: scores['resolution'] = 95
        elif p > 1000000: scores['resolution'] = 85
        elif p > 500000: scores['resolution'] = 70
        elif p > 100000: scores['resolution'] = 50
        else: scores['resolution'] = 30
    compliance = getattr(media, 'compliance_status', '') or ''
    if compliance == 'rejected': scores['suitable_for_cutout'] = False
    elif compliance == 'needs_review': scores['product_completeness'] -= 20
    role = getattr(media, 'role', '') or ''
    if role == 'main': scores['product_completeness'] += 10
    elif role == 'sku': scores['product_completeness'] += 5
    elif role in ('detail', 'scene'): scores['product_completeness'] -= 10
    base = scores['product_completeness']*0.4 + scores['resolution']*0.3 + (100-scores['background_complexity'])*0.2 + (100-scores['occlusion'])*0.1
    scores['suitability_score'] = max(0, min(100, int(base)))
    scores['suitable_for_cutout'] = scores['suitable_for_cutout'] and scores['suitability_score'] >= 50
    return scores


def find_best_media_for_cutout(user, source_id: int, max_count: int = 5) -> List[Dict[str, Any]]:
    media_list = list(OzonSourceMedia.select().where(
        (OzonSourceMedia.user == user) &
        (OzonSourceMedia.source_id == source_id) &
        (OzonSourceMedia.compliance_status != 'rejected')
    ).order_by(OzonSourceMedia.id))
    results = []
    for m in media_list:
        score = score_media_for_cutout(user, m)
        if score['suitable_for_cutout']:
            r = {'media_id': m.id, 'role': m.role or '', 'local_path': m.local_path or '',
                 'source_url': m.source_url or '', 'width': m.width, 'height': m.height, **score}
            results.append(r)
    results.sort(key=lambda x: x['suitability_score'], reverse=True)
    return results[:max_count]


# ═══════════════════════════════════════════════════════════════
# 核心抠图 V2
# ═══════════════════════════════════════════════════════════════

def create_product_cutout(
    user, media: OzonSourceMedia, provider: str = 'rembg_crop',
    sku: Optional[OzonSourceSku] = None,
    targets: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """对图片执行目标级抠图。

    Args:
        targets: 用户指定的目标列表，每个目标有 type/bbox/keep。
                 如 [{'type':'main_product','bbox':[x1,y1,x2,y2],'keep':True,'label':'商品'}]
                 如果为 None 且 provider='rembg_crop'，则不启动广告图抠图。
    """
    img = _load_media_image(media)
    if not img:
        return {'ok': False, 'error': 'Cannot load source image'}

    # 检查是否已有同 provider 的成功记录
    existing = (OzonProductCutout
                .select().where((OzonProductCutout.user == user) &
                                (OzonProductCutout.source_media == media) &
                                (OzonProductCutout.provider == provider) &
                                (OzonProductCutout.status == 'generated')).first())
    if existing:
        return _cutout_to_dict(existing)

    if provider == 'rembg_crop' and targets:
        transparent, raw_mask, cleaned_mask, seg_info = _cutout_rembg_crop(img, targets)
        segmentation_provider = 'rembg_crop'
    elif provider == 'rembg_full':
        transparent, mask = _cutout_rembg_full(img)
        raw_mask, cleaned_mask, seg_info = mask, mask, {'method': 'rembg_full'}
        segmentation_provider = 'rembg_full'
    else:
        return {'ok': False, 'error': f'请先指定目标框 (provider={provider}, targets={bool(targets)})'}

    if not transparent:
        return {'ok': False, 'error': 'Cutout produced empty result'}

    # 质量检查
    quality = _check_cutout_quality_v2(img, transparent, cleaned_mask, targets, seg_info)

    # 保存文件
    out_dir = _cutout_dir(media.id)
    revision = _next_revision(user, media, provider)
    transparent_path = out_dir / f'cutout_{provider}_v{revision}.png'
    raw_mask_path = out_dir / f'mask_raw_{provider}_v{revision}.png'
    cleaned_mask_path = out_dir / f'mask_clean_{provider}_v{revision}.png'
    preview_path = out_dir / f'preview_{provider}_v{revision}.jpg'

    transparent.save(transparent_path, 'PNG')
    if raw_mask: raw_mask.save(raw_mask_path, 'PNG')
    if cleaned_mask: cleaned_mask.save(cleaned_mask_path, 'PNG')

    preview = _make_checkerboard_preview(transparent, (400, 400))
    preview.save(preview_path, 'JPEG', quality=85)

    rel = lambda p: str(p.relative_to(_cutout_root())).replace('\\', '/') if p else None

    keep_targets = [t for t in (targets or []) if t.get('keep')]
    cutout = OzonProductCutout.create(
        user=user, source=media.source, source_media=media, source_sku=sku,
        transparent_path=rel(transparent_path), mask_path=rel(cleaned_mask_path),
        preview_path=rel(preview_path), provider=provider,
        quality_score=quality.get('score'),
        quality_json=json.dumps(quality, ensure_ascii=False),
        target_spec_json=json.dumps(targets, ensure_ascii=False) if targets else None,
        raw_mask_path=rel(raw_mask_path), cleaned_mask_path=rel(cleaned_mask_path),
        segmentation_provider=segmentation_provider,
        target_count=len(keep_targets), has_accessories=any(t.get('type')=='accessory' for t in keep_targets),
        outside_residual_score=quality.get('outside_residual_score'),
        completeness_score=quality.get('completeness_score'),
        edge_quality_score=quality.get('edge_quality_score'),
        revision=revision, status='generated' if quality.get('pass') else 'pending',
    )
    return _cutout_to_dict(cutout)


def _cutout_to_dict(cutout: OzonProductCutout) -> Dict[str, Any]:
    return {'ok': True, 'cutout_id': cutout.id, 'source_media_id': cutout.source_media_id,
            'transparent_path': cutout.transparent_path, 'mask_path': cutout.mask_path,
            'preview_path': cutout.preview_path, 'quality_score': cutout.quality_score,
            'warnings': _parse_warnings(cutout.quality_json), 'target_count': cutout.target_count}


def _parse_warnings(qj: Optional[str]) -> list:
    if not qj: return []
    try: return json.loads(qj).get('warnings', [])
    except: return []


def _next_revision(user, media, provider: str) -> int:
    last = (OzonProductCutout.select().where(
        (OzonProductCutout.user == user) & (OzonProductCutout.source_media == media) &
        (OzonProductCutout.provider == provider)
    ).order_by(OzonProductCutout.revision.desc()).first())
    return (last.revision + 1) if last else 1


# ═══════════════════════════════════════════════════════════════
# rembg_crop: bbox 裁剪 + rembg + 框外强制透明
# ═══════════════════════════════════════════════════════════════

def _cutout_rembg_crop(img: Image.Image, targets: List[Dict]) -> Tuple[
    Optional[Image.Image], Optional[Image.Image], Optional[Image.Image], Dict
]:
    """对每个 keep 目标裁剪后 rembg only_mask，mask 贴回原图，产品像素不变。"""
    from rembg import remove

    W, H = img.size
    keep_targets = [t for t in targets if t.get('keep')]
    if not keep_targets:
        return None, None, None, {'error': 'No keep targets'}

    merged_mask = Image.new('L', (W, H), 0)
    seg_info = {'method': 'rembg_crop', 'mask_only': True, 'targets': []}

    for i, t in enumerate(keep_targets):
        bbox = t.get('bbox', [0, 0, W, H])
        x1, y1, x2, y2 = _clamp_bbox(bbox, W, H)

        # 扩展 5% 给 rembg
        pad_x = int((x2 - x1) * 0.05)
        pad_y = int((y2 - y1) * 0.05)
        cx1, cy1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        cx2, cy2 = min(W, x2 + pad_x), min(H, y2 + pad_y)

        crop = img.crop((cx1, cy1, cx2, cy2))
        try:
            # V3: only_mask=True → 只出mask，不重绘产品像素
            local_mask = remove(crop.convert('RGB'), only_mask=True, post_process_mask=True)
        except Exception as e:
            print(f'[rembg_crop] target {i} error: {e}')
            continue

        # 贴回全局坐标
        global_mask = Image.new('L', (W, H), 0)
        global_mask.paste(local_mask, (cx1, cy1))
        merged_mask = np.maximum(np.array(merged_mask), np.array(global_mask)).astype(np.uint8)
        merged_mask = Image.fromarray(merged_mask)

        seg_info['targets'].append({'index': i, 'bbox': [x1, y1, x2, y2]})

    # 框外强制透明
    cleaned_mask = _clean_target_mask(merged_mask, keep_targets, W, H)

    # ── 核心：原图 RGBA + mask = 透明母图，产品像素零修改 ──
    transparent = img.convert('RGBA')
    transparent.putalpha(cleaned_mask)

    # 半透明像素设为完全透明（二值mask）
    mask_arr = np.array(cleaned_mask)
    rgba_arr = np.array(transparent)
    rgba_arr[mask_arr < 20] = [0, 0, 0, 0]
    transparent = Image.fromarray(rgba_arr)

    return transparent, merged_mask, cleaned_mask, seg_info


# ═══════════════════════════════════════════════════════════════
# rembg_full (旧版兼容, V3: 也改用 only_mask)
# ═══════════════════════════════════════════════════════════════

def _cutout_rembg_full(img: Image.Image) -> Tuple[Optional[Image.Image], Optional[Image.Image]]:
    from rembg import remove
    if img.mode not in ('RGB', 'RGBA'): img = img.convert('RGB')
    try:
        mask = remove(img, only_mask=True, post_process_mask=True)
        transparent = img.convert('RGBA')
        transparent.putalpha(mask)
        return transparent, mask
    except Exception as e:
        print(f'[rembg_full] Error: {e}')
        return None, None


# ═══════════════════════════════════════════════════════════════
# 蒙版清理
# ═══════════════════════════════════════════════════════════════

def _clean_target_mask(
    mask: Image.Image, keep_targets: List[Dict], W: int, H: int,
    preserve_components: Optional[List[int]] = None,
) -> Image.Image:
    """清理蒙版: 框外透明 + 去噪 + 补孔 + 轻微闭运算 + 1px羽化"""
    from scipy import ndimage

    mask_arr = np.array(mask)

    # 1. 框外强制透明
    outside = np.ones((H, W), dtype=np.uint8) * 255
    for t in keep_targets:
        bbox = t.get('bbox', [0, 0, W, H])
        x1, y1, x2, y2 = _clamp_bbox(bbox, W, H)
        outside[y1:y2, x1:x2] = 0
    mask_arr[outside == 255] = 0

    # 2. 二值化
    binary = (mask_arr > 30).astype(np.uint8) * 255

    # 3. 去小噪点
    labeled, n = ndimage.label(binary)
    if n > 0:
        sizes = ndimage.sum(binary, labeled, range(1, n+1))
        min_size = (W * H) * 0.0002  # 最小0.02%面积
        for i in range(1, n+1):
            if sizes[i-1] < min_size:
                binary[labeled == i] = 0

    # 4. 闭运算补孔洞
    from scipy.ndimage import binary_closing, binary_dilation
    struct = np.ones((3, 3), dtype=np.uint8)
    binary = binary_closing(binary, structure=struct, iterations=1).astype(np.uint8) * 255

    # 5. 1px羽化
    eroded = ndimage.binary_erosion(binary, iterations=1).astype(np.uint8) * 255
    edge = binary.astype(int) - eroded.astype(int)
    binary = np.clip(binary.astype(int) + (edge * 0.5).astype(int), 0, 255).astype(np.uint8)

    return Image.fromarray(binary)


# ═══════════════════════════════════════════════════════════════
# 质量检查 V2
# ═══════════════════════════════════════════════════════════════

def _check_cutout_quality_v2(
    img: Image.Image, transparent: Image.Image,
    mask: Optional[Image.Image], targets: Optional[List[Dict]], seg_info: Dict,
) -> Dict[str, Any]:
    """升级版质量检查。检测框外残留、完整性、边缘质量。"""
    warnings = []
    W, H = img.size
    mask_arr = np.array(mask) if mask else np.array(transparent.split()[-1])
    binary = (mask_arr > 30).astype(np.uint8)

    keep_targets = [t for t in (targets or []) if t.get('keep')]

    # 框外残留
    outside_residual = 0.0
    if keep_targets:
        outside_mask = np.ones((H, W), dtype=np.uint8)
        for t in keep_targets:
            bbox = t.get('bbox', [0, 0, W, H])
            x1, y1, x2, y2 = _clamp_bbox(bbox, W, H)
            outside_mask[y1:y2, x1:x2] = 0
        outside_pixels = binary[outside_mask == 1]
        outside_residual = outside_pixels.mean() / 255 if outside_pixels.size > 0 else 0
        if outside_residual > 0.05:
            warnings.append(f'目标框外仍有{int(outside_residual*100)}%残留，广告文字/Logo可能未被清除')
        elif outside_residual > 0.01:
            warnings.append(f'框外少量残留({int(outside_residual*100)}%)')

    # 完整性
    completeness = 1.0
    for i, t in enumerate(keep_targets):
        bbox = t.get('bbox', [0, 0, W, H])
        x1, y1, x2, y2 = _clamp_bbox(bbox, W, H)
        region = binary[y1:y2, x1:x2]
        if region.size > 0:
            fill = region.mean() / 255
            if fill < 0.3:
                warnings.append(f'目标 {t.get("label", i+1)} 填充率仅{int(fill*100)}%，商品可能被截断')
                completeness = min(completeness, fill)

    # 边缘质量
    edge_score = 1.0
    if binary.shape[0] > 2 and binary.shape[1] > 2:
        grad = np.abs(np.diff(binary.astype(int), axis=1))
        halo = (grad > 200).mean()
        if halo > 0.08:
            warnings.append('边缘存在明显白边/黑边')
            edge_score = 0.7

    # ── V3: 像素真实性检查 ──
    # 产品内部不透明区域必须与原图一致
    pixel_preserved = True
    opaque_diff = 0.0
    if mask is not None:
        original_rgb = np.array(img.convert('RGB'))
        result_rgba = np.array(transparent)
        result_rgb = result_rgba[:, :, :3]
        result_alpha = result_rgba[:, :, 3]

        opaque = result_alpha > 250
        if opaque.any() and original_rgb.shape == result_rgb.shape:
            diff = np.abs(original_rgb[opaque].astype(int) - result_rgb[opaque].astype(int))
            opaque_diff = float(diff.mean())
            if opaque_diff > 0:
                pixel_preserved = False
                warnings.append(f'产品内部像素被修改 (平均差异{opaque_diff:.1f})，母图不可作为正式产品图')

    # 评分
    score = 90
    score -= int(outside_residual * 80)
    score -= int((1 - completeness) * 40)
    score -= int((1 - edge_score) * 30)
    if not pixel_preserved: score -= 50
    for w in warnings: score -= 3
    score = max(0, min(100, score))

    return {
        'score': score,
        'pass': score >= 70 and outside_residual < 0.05 and completeness > 0.5 and pixel_preserved,
        'warnings': warnings,
        'pixel_preserved': pixel_preserved,
        'opaque_pixel_difference': round(opaque_diff, 2),
        'outside_residual_score': round(1 - outside_residual, 3),
        'completeness_score': round(completeness, 3),
        'edge_quality_score': round(edge_score, 3),
        'target_count': len(keep_targets),
    }


def _detect_text_regions(img: Image.Image, mask: Optional[Image.Image] = None) -> float:
    gray = img.convert('L'); gray_arr = np.array(gray, dtype=np.float32)
    if mask:
        m = np.array(mask.convert('L'))
        if m.shape == gray_arr.shape: gray_arr[m < 128] = 128
    if gray_arr.shape[0] < 3 or gray_arr.shape[1] < 3: return 0.0
    gx = np.abs(np.diff(gray_arr, axis=1)[:, :-1]); gy = np.abs(np.diff(gray_arr, axis=0)[:-1, :])
    h, w = min(gx.shape[0], gy.shape[0]), min(gx.shape[1], gy.shape[1])
    gradient = np.sqrt(gx[:h, :w]**2 + gy[:h, :w]**2)
    high_grad = (gradient > 30).mean()
    score = float(min(1.0, max(0.0, high_grad * 0.4)))
    return score


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _clamp_bbox(bbox: List[int], W: int, H: int) -> Tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox[:4]
    return max(0, min(x1, x2)), max(0, min(y1, y2)), min(W, max(x1, x2)), min(H, max(y1, y2))


def _load_media_image(media: OzonSourceMedia) -> Optional[Image.Image]:
    import requests
    path = (media.local_path or '').replace('\\', '/')
    if path:
        for prefix in ['', 'uploads/']:
            p = Path(current_app.root_path) / prefix / path
            if p.exists(): return Image.open(p).convert('RGB')
    if media.source_url and 'example.com' not in media.source_url:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                   'Referer': 'https://detail.1688.com/',
                   'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8'}
        try:
            resp = requests.get(media.source_url, headers=headers, timeout=20)
            resp.raise_for_status()
            return Image.open(io.BytesIO(resp.content)).convert('RGB')
        except Exception as e:
            print(f'[cutout] Cannot load URL for media {media.id}: {str(e)[:150]}')
    return None


def _make_checkerboard_preview(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    thumb = img.copy(); thumb.thumbnail(size, Image.LANCZOS)
    bg = Image.new('RGBA', thumb.size, (255,255,255,255))
    draw = ImageDraw.Draw(bg); tile = 16
    for y in range(0, thumb.size[1], tile):
        for x in range(0, thumb.size[0], tile):
            if (x//tile + y//tile) % 2 == 0:
                draw.rectangle([x, y, x+tile-1, y+tile-1], fill=(200,200,200,255))
    bg.paste(thumb, (0,0), thumb)
    return bg.convert('RGB')
