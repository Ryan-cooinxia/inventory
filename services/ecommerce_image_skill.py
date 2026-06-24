
"""Deterministic prompt builder for ecommerce image generation.

This module turns the local ecommerce-image-maker Skill rules into concrete
prompts for OzonImageSlot generation. It intentionally does not call an LLM;
it provides a stable prompt baseline for testing image-2 and other providers.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional


SKILL_DIR = Path(__file__).resolve().parents[1] / "ai" / "ecommerce-image-maker"

ROLE_OBJECTIVES: Dict[str, Dict[str, str]] = {
    "main": {
        "title": "Hero main image",
        "objective": "make the product identity and primary value clear at first glance",
        "layout": "large product hero, clean ecommerce composition, strong whitespace for headline and three benefit icons",
    },
    "sku": {
        "title": "Version and variation image",
        "objective": "explain available versions, variants, or system compatibility without implying one version fits all systems",
        "layout": "main product with clean version cards or variation tiles, simple labels, no third-party logos unless provided by the operator",
    },
    "scene": {
        "title": "Usage scene image",
        "objective": "show the product in a realistic buyer-relevant workflow",
        "layout": "product in actual use, scene supports scale and purpose, product remains sharp and dominant",
    },
    "selling_point": {
        "title": "Key selling point image",
        "objective": "visualize one major buyer benefit with clear evidence",
        "layout": "technical benefit module with one headline, two to three proof labels, and focused product view",
    },
    "function": {
        "title": "Function demonstration image",
        "objective": "show how the product works or what it controls",
        "layout": "clean infographic-style module with product as the control center and simple connected function elements",
    },
    "detail": {
        "title": "Detail close-up grid",
        "objective": "prove build quality and usability through close-up details",
        "layout": "five-panel detail grid: screen/control area, buttons, dial, ports or mount, material texture",
    },
    "size": {
        "title": "Size and fit image",
        "objective": "answer fit, installation, or compatibility questions without inventing dimensions",
        "layout": "technical dimension-style image with placeholders for exact measurements if unknown",
    },
    "package": {
        "title": "Package and accessories image",
        "objective": "show what is included and reduce purchase uncertainty",
        "layout": "clean flat lay of confirmed product and accessories only, quantity labels if confirmed",
    },
}

DEVICE_UI_KEYWORDS = (
    "xpro", "x-pro", "godox", "flash trigger", "wireless trigger", "ttl", "hss",
    "camera", "lcd", "screen", "display", "button", "dial", "controller", "remote",
    "??", "??", "??", "??", "??", "??", "???",
)

COMMON_NEGATIVE = (
    "No product deformation, no redesigned body, no missing parts, no extra parts, "
    "no wrong component count, no changed button positions, no fake badges, no fake certifications, "
    "no unverified claims, no cluttered background, no unreadable random text, no Chinese layout text."
)

DEVICE_UI_GUARD = (
    "Preserve all visible control UI details as product structure: screen layout, screen color, "
    "menu rows, button count, button shape, printed button legends, icons, dial labels, switch labels, "
    "and model markings. Do not convert the screen into a smartphone app UI. Do not turn button labels "
    "into random decorative text."
)

XPRO_UI_GUARD = (
    "For Godox XPro / XPro-C style flash trigger products, preserve the pale blue LCD inside the black bezel, "
    "CH1 header, A/B/C/D/E group rows, TTL and M modes, values such as 0.0, +0.3, 1/32, 1/64 or 1/128, "
    "small status icons, and the purple-blue bottom menu strip with short labels such as CH/Zoom, SYNC, ALL, MOD. "
    "Preserve the five left shortcut buttons, four oval buttons below the screen, MODE, RST, MENU, TCM, lock icon, "
    "flash icon, SET dial, side ON/OFF switches, hot shoe mount, and XPro-C / XPro model marking when visible."
)


def load_skill_rule_text() -> str:
    """Return compact text from local Skill files for diagnostics or UI display."""
    parts: List[str] = []
    for rel in (
        "SKILL.md",
        "references/workflow.md",
        "references/modules.md",
        "references/prompt-and-qa.md",
        "references/device-ui-preservation.md",
    ):
        path = SKILL_DIR / rel
        if path.exists():
            parts.append(f"# {rel}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def build_slot_prompt(draft, slot, sku_names: Optional[Iterable[str]] = None) -> Dict[str, str]:
    """Build the final image prompt and negative prompt for one image slot."""
    source = getattr(draft, "source", None)
    title = _first_text(
        getattr(source, "title_cn", None),
        getattr(draft, "title_ru", None),
        getattr(source, "title", None),
        "the product",
    )
    category = _first_text(
        getattr(source, "category_cn", None),
        getattr(source, "category", None),
        "ecommerce product",
    )
    brand = _first_text(getattr(source, "brand", None), getattr(draft, "brand", None), "")
    sku_text = ", ".join([s for s in (sku_names or []) if s][:6])
    custom_note = (getattr(slot, "prompt_cn", None) or "").strip()
    role = getattr(slot, "role", "") or "main"
    role_spec = ROLE_OBJECTIVES.get(role, ROLE_OBJECTIVES["main"])

    signal_text = " ".join([title, category, brand, custom_note, sku_text])
    needs_ui = _needs_device_ui_guard(signal_text)
    is_xpro = _is_xpro_product(signal_text)
    product_profile = _product_profile(title, category, brand, sku_text)

    prompt_parts = [
        f"Create a premium cross-border ecommerce image for {title}.",
        "Language: English only for layout text. Do not use Chinese layout text.",
        (
            "Product preservation: keep the exact product structure from the reference images: same silhouette, "
            "proportions, component count, component positions, material split lines, color blocking, buttons, ports, "
            "handles, mounts, contact points, and visible functional parts. Do not redesign, simplify, or add parts."
        ),
    ]

    if needs_ui:
        prompt_parts.append(f"Device UI preservation: {DEVICE_UI_GUARD}")
    if is_xpro:
        prompt_parts.append(f"Godox XPro UI preservation: {XPRO_UI_GUARD}")

    prompt_parts.extend([
        f"Image type: {role_spec['title']}.",
        f"Objective: {role_spec['objective']}.",
        f"Product context: {product_profile}.",
        f"Composition: {role_spec['layout']}. Use a clean 3:4 ecommerce layout unless the marketplace requires another ratio.",
        "Lighting and material: realistic commercial product photography, accurate materials, crisp edges, clean soft shadows, premium catalog quality.",
        "Text/layout: use short English text only; keep labels minimal, readable, and easy to replace in post-production.",
    ])

    if custom_note:
        prompt_parts.append(f"Slot-specific requirements from the operator: {custom_note}")
    if sku_text and role in {"sku", "package"}:
        prompt_parts.append(f"Known variants/SKUs to respect: {sku_text}.")

    prompt_parts.append(
        "Style: coherent cross-border ecommerce A+ detail page style, clean white/light-gray base, restrained accent colors, professional and trustworthy."
    )

    negative = _negative_prompt(needs_ui=needs_ui, is_xpro=is_xpro)
    prompt_parts.append(f"Negative constraints: {negative}")

    return {
        "prompt": "\n\n".join(prompt_parts),
        "negative_prompt": negative,
        "prompt_version": "ecommerce-image-maker-v0.2-ui-preservation",
    }


def _product_profile(title: str, category: str, brand: str, sku_text: str) -> str:
    parts = [f"product name: {title}", f"category: {category}"]
    if brand:
        parts.append(f"brand: {brand}")
    if sku_text:
        parts.append(f"variants: {sku_text}")
    return "; ".join(parts)


def _negative_prompt(needs_ui: bool, is_xpro: bool) -> str:
    items = [COMMON_NEGATIVE]
    if needs_ui:
        items.append(
            "No smartphone-style app screen, no touchscreen UI, no random letters on displays, no missing printed control legends, no merged buttons, no extra buttons."
        )
    if is_xpro:
        items.append(
            "No missing CH1/group rows, no missing MODE/RST/MENU/TCM labels, no missing SET dial, no missing hot shoe mount, no added antenna, no cables attached to the trigger."
        )
    return " ".join(items)


def _needs_device_ui_guard(text: str) -> bool:
    lowered = text.lower()
    return any(k.lower() in lowered for k in DEVICE_UI_KEYWORDS)


def _is_xpro_product(text: str) -> bool:
    lowered = text.lower()
    return "xpro" in lowered or "x-pro" in lowered or "godox" in lowered or "??" in text


def _first_text(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
