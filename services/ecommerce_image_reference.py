"""
Reference image selection for OZON image generation slots.

Selects the best product reference images from OzonSourceMedia
based on the slot's role (main/SKU/detail/scene etc.).

Priority rules per role:
  main/hero  → primary white-background product image
  sku        → SKU-specific images from image_refs
  detail     → detail close-up images
  scene      → scene/usage images (fall back to main)
  size       → main product image (size overlays added later)
  package    → only confirmed package content images
  function   → main image + any UI/control zone images
  selling_point → main image
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from models import OzonDraft, OzonImageSlot, OzonSourceMedia


# Role → preferred reference_role and fallback order
ROLE_REFERENCE_PRIORITY: Dict[str, List[str]] = {
    'main':           ['main', 'primary'],
    'sku':            ['sku', 'main'],
    'scene':          ['scene', 'main'],
    'selling_point':  ['main', 'scene'],
    'function':       ['detail', 'main'],
    'detail':         ['detail', 'main'],
    'size':           ['main'],
    'package':        ['main'],  # packaging images are special — only if confirmed
}


def select_references_for_slot(
    user,
    draft: OzonDraft,
    slot: OzonImageSlot,
    max_references: int = 3,
) -> List[Dict[str, Any]]:
    """Auto-select reference images for one image slot.

    Args:
        user: current_user
        draft: OzonDraft instance
        slot: OzonImageSlot instance
        max_references: max number of reference images to return (default 3)

    Returns:
        List of dicts: [{"media_id": 123, "path": "source_media/xxx.jpg",
                          "role": "main", "source_url": "..."}, ...]
        Empty list if no suitable reference images found.
    """
    # Query all media for this source (not just for_ozon=True —
    # white-background main images may not have for_ozon flag set yet)
    source = getattr(draft, 'source', None)
    if not source:
        return []

    all_media = list(OzonSourceMedia.select().where(
        (OzonSourceMedia.source == source) &
        (OzonSourceMedia.user == user)
    ).order_by(OzonSourceMedia.role, OzonSourceMedia.id))

    if not all_media:
        return []

    role = getattr(slot, 'role', '') or 'main'

    # ── Package slot: only confirmed images, NO main image fallback ──
    if role == 'package':
        # Package slot requires explicit packaging/product images
        # Don't fall back to main — return empty to block reference mode
        result = []
        for m in all_media:
            m_role = (m.role or '').strip()
            if m_role in ('main', '') and (m.source_url or m.local_path):
                result.append({
                    'media_id': m.id,
                    'path': m.local_path or '',
                    'source_url': m.source_url or '',
                    'role': m_role or 'main',
                    'aspect_ratio': m.aspect_ratio or '',
                    'width': m.width,
                    'height': m.height,
                })
                if len(result) >= 1:
                    break
        # Return empty — package content must be confirmed before generating
        return []  # blocks reference mode, forcing user to confirm package contents

    preferred_roles = ROLE_REFERENCE_PRIORITY.get(role, ['main'])

    # ── Build sets: media by their role ──
    media_by_role: Dict[str, List[OzonSourceMedia]] = {}
    for m in all_media:
        r = (m.role or '').strip() or 'main'
        media_by_role.setdefault(r, []).append(m)

    # ── SKU slot: SKU-linked images FIRST, then general SKU/main ──
    selected: List[OzonSourceMedia] = []
    seen_ids = set()

    if role == 'sku' and slot.scope_sku_ref:
        sku_ref = slot.scope_sku_ref
        for m in all_media:
            if m.id in seen_ids:
                continue
            sku_refs = _parse_sku_refs(m.sku_refs)
            if sku_ref in sku_refs and len(selected) < max_references:
                selected.append(m)
                seen_ids.add(m.id)

    # ── Select by role priority (fills remaining slots) ──
    for pref_role in preferred_roles:
        candidates = media_by_role.get(pref_role, [])
        for m in candidates:
            if m.id not in seen_ids and len(selected) < max_references:
                selected.append(m)
                seen_ids.add(m.id)
        if len(selected) >= max_references:
            break

    # ── Build output ──
    result = []
    for m in selected:
        result.append({
            'media_id': m.id,
            'path': m.local_path or '',
            'source_url': m.source_url or '',
            'role': m.role or '',
            'aspect_ratio': m.aspect_ratio or '',
            'width': m.width,
            'height': m.height,
        })

    return result


def has_usable_references(references: List[Dict[str, Any]]) -> bool:
    """Check if any reference has a valid path or URL."""
    for ref in references:
        if ref.get('path') or ref.get('source_url'):
            return True
    return False


def _parse_sku_refs(sku_refs_raw) -> List[str]:
    """Parse sku_refs JSON field to list of SKU identifiers."""
    import json
    if not sku_refs_raw:
        return []
    try:
        data = json.loads(sku_refs_raw)
        if isinstance(data, list):
            return [str(x) for x in data if x]
        return []
    except (json.JSONDecodeError, TypeError, ValueError):
        return [str(sku_refs_raw)] if sku_refs_raw else []
