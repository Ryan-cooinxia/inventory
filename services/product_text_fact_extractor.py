"""
网页文本事实提取器

从 OzonSource 的网页文本中提取结构化事实候选：
- 标题、描述
- raw_json 中的参数
- SKU 信息
- 包装清单
- 商品属性

所有事实写入 ProductFactEvidence，不直接覆盖 ProductFact。
"""
import json
import hashlib
import datetime
from typing import Any, Dict, List, Optional

from models import (
    db, ProductFact, ProductFactSku, ProductFactEvidence,
    OzonSource, OzonSourceSku,
)


def extract_text_facts(user, source: OzonSource, fact: ProductFact) -> int:
    """从网页文本提取所有事实候选。返回新增数量。"""
    count = 0

    # 1. 标题
    if source.title_cn:
        count += _add_text_evidence(user, fact, 'product_identity.name', source.title_cn,
                                     'text', confidence=0.9, locator={'field': 'title_cn'})

    # 2. 描述
    if source.description_cn:
        count += _add_text_evidence(user, fact, 'description', source.description_cn[:2000],
                                     'text', confidence=0.7, locator={'field': 'description_cn'})

    # 3. 类目
    if source.category_cn:
        count += _add_text_evidence(user, fact, 'product_identity.category', source.category_cn,
                                     'text', confidence=0.8, locator={'field': 'category_cn'})

    # 4. raw_json
    if source.raw_json:
        try:
            raw = json.loads(source.raw_json)
            # 属性
            for attr in raw.get('attributes', [])[:20]:
                name = attr.get('name', '')
                val = attr.get('value', '')
                if name and val:
                    field = _map_attribute_to_field(name)
                    count += _add_text_evidence(user, fact, field, val, 'html',
                                                 confidence=0.8, locator={'field': 'attributes', 'name': name})
            # 规格
            for spec in raw.get('specifications', [])[:10]:
                name = spec.get('name', '')
                val = spec.get('value', '')
                if name and val:
                    field = _map_attribute_to_field(name)
                    count += _add_text_evidence(user, fact, field, val, 'html',
                                                 confidence=0.85, locator={'field': 'specifications', 'name': name})

            # 店铺名 → 品牌线索
            shop = raw.get('shop_name', '') or raw.get('seller', '')
            if shop:
                count += _add_text_evidence(user, fact, 'product_identity.brand', shop,
                                             'html', confidence=0.5, locator={'field': 'shop_name'})
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # 5. SKU 信息
    skus = list(OzonSourceSku.select().where(
        (OzonSourceSku.user == user) & (OzonSourceSku.source == source)
    ))
    for sku in skus:
        if sku.color_cn:
            count += _add_text_evidence(user, fact,
                f'skus[{sku.source_order}].color',
                sku.color_cn, 'text', confidence=0.9, sku_id=sku.id,
                locator={'field': 'color_cn', 'source_order': sku.source_order})
        if sku.size_cn:
            count += _add_text_evidence(user, fact,
                f'skus[{sku.source_order}].size',
                sku.size_cn, 'text', confidence=0.9, sku_id=sku.id,
                locator={'field': 'size_cn', 'source_order': sku.source_order})
        if sku.style_cn:
            count += _add_text_evidence(user, fact,
                f'skus[{sku.source_order}].style',
                sku.style_cn, 'text', confidence=0.9, sku_id=sku.id,
                locator={'field': 'style_cn', 'source_order': sku.source_order})
        if sku.bundle_quantity:
            count += _add_text_evidence(user, fact,
                f'skus[{sku.source_order}].bundle_quantity',
                sku.bundle_quantity, 'text', confidence=0.9, sku_id=sku.id,
                locator={'field': 'bundle_quantity', 'source_order': sku.source_order})

    return count


def _add_text_evidence(
    user, fact: ProductFact, field_path: str, value: Any,
    source_type: str = 'text', confidence: float = 0.8,
    sku_id: Optional[int] = None, locator: Optional[dict] = None,
) -> int:
    """添加一条网页/文本证据。通过 evidence_hash 去重。"""
    evidence_hash = _hash_evidence(fact.id, field_path, value, source_type,
                                    str(sku_id) if sku_id else '')

    existing = ProductFactEvidence.select().where(
        (ProductFactEvidence.user == user) &
        (ProductFactEvidence.fact == fact) &
        (ProductFactEvidence.evidence_hash == evidence_hash)
    ).first()
    if existing:
        return 0

    value_str = str(value)[:2000]
    ProductFactEvidence.create(
        user=user, fact=fact,
        applicable_sku_id=sku_id,
        field_path=field_path,
        evidence_type='text',
        fact_status='extracted',
        confidence=confidence,
        source_type=source_type,
        source_locator_json=json.dumps(locator, ensure_ascii=False) if locator else None,
        source=fact.source if hasattr(fact, 'source') else None,
        source_url=getattr(fact.source, 'source_url', '') if hasattr(fact, 'source') else '',
        value_json=value_str,
        evidence_hash=evidence_hash,
    )
    return 1


def _hash_evidence(fact_id, field_path, value, etype, sku_hint):
    raw = f'{fact_id}|{field_path}|{value}|{etype}|{sku_hint}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _map_attribute_to_field(name: str) -> str:
    """将中文属性名映射到标准 field_path。"""
    import re
    n = name.strip().lower()
    if any(kw in n for kw in ['品牌', 'brand']): return 'product_identity.brand'
    if any(kw in n for kw in ['型号', 'model']): return 'product_identity.model'
    if any(kw in n for kw in ['材质', '材料', 'material']): return 'physical_structure.material'
    if any(kw in n for kw in ['尺寸', 'size', '长', '宽', '高']): return 'dimensions'
    if any(kw in n for kw in ['重量', 'weight']): return 'weight'
    if any(kw in n for kw in ['功率', 'power']): return 'power'
    if any(kw in n for kw in ['容量', 'capacity']): return 'capacity'
    if any(kw in n for kw in ['电压', 'voltage']): return 'voltage'
    if any(kw in n for kw in ['电池', 'battery']): return 'battery'
    if any(kw in n for kw in ['产地', 'origin']): return 'origin'
    if any(kw in n for kw in ['颜色', 'color']): return 'sku_variants.color'
    if any(kw in n for kw in ['包装', 'package', '配件']): return 'package_contents'
    # 默认用属性名作为 field_path（sanitize）
    safe = re.sub(r'[^a-z0-9_.]', '_', n)[:50]
    return f'attributes.{safe}' if safe else 'attributes.unknown'
