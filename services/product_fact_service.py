"""
Product Brief 服务 — 统一商品事实管理

职责:
- 构建/合并/验证 Product Brief
- 证据管理与去重
- 冲突检测与解决
- 版本快照
- 为生图提供只读已确认事实
"""
from __future__ import annotations

import json
import hashlib
import datetime
from typing import Any, Dict, List, Optional, Tuple

from models import (
    db, ProductFact, ProductFactSku, ProductFactEvidence,
    ProductFactRevision, OzonSourceMedia, OzonSource,
)

# 事实状态枚举
FACT_STATUS = [
    'extracted',   # 已提取，未审核
    'inferred',    # AI推断，不可直接使用
    'verified',    # 证据充分，未人工确认
    'confirmed',   # 人工确认，可用于生图
    'conflict',    # 存在冲突
    'unknown',     # 没有可靠信息
    'rejected',    # 已拒绝
]

GENERATION_ALLOWED_STATUS = {'confirmed'}
BLOCKING_STATUS = {'conflict'}
# 高置信度阈值（但仍需人工确认）
HIGH_CONFIDENCE = 0.85

# ═══════════════════════════════════════════════════════════════
# Product Brief Schema
# ═══════════════════════════════════════════════════════════════

BRIEF_SCHEMA_VERSION = '1.0'


def build_product_brief(fact: ProductFact) -> Dict[str, Any]:
    """从 ProductFact 构建标准化 Product Brief。"""
    skus = list(ProductFactSku.select().where(
        (ProductFactSku.fact == fact) &
        (ProductFactSku.user == fact.user)
    ).order_by(ProductFactSku.source_order))

    evidences = list(ProductFactEvidence.select().where(
        (ProductFactEvidence.fact == fact) &
        (ProductFactEvidence.user == fact.user)
    ))

    brief = {
        'schema_version': BRIEF_SCHEMA_VERSION,
        'fact_id': fact.id,
        'product_identity': {
            'name': fact.standard_name_cn or '',
            'brand': fact.brand_name or '',
            'model': fact.model or '',
            'category': fact.category_hint_cn or '',
            'product_type': fact.product_type or '',
        },
        'physical_structure': {
            'shape': '',
            'colors': [],
            'materials': [fact.material] if fact.material else [],
            'components': _parse_list(fact.functions_json),
            'buttons_ports': [],
            'immutable_features': _parse_list(fact.locked_fields_json),
        },
        'sku_variants': [
            {
                'fact_sku_id': s.id,
                'source_order': s.source_order,
                'name': s.standard_sku_name_cn or s.standard_sku_name_ru or '',
                'color': s.color_cn or s.color_ru or '',
                'size': s.size_cn or s.size_ru or '',
                'style': s.style_cn or s.style_ru or '',
                'bundle_quantity': s.bundle_quantity or 1,
                'package_contents': _parse_list(s.package_contents_json),
                'reference_media_ids': _parse_list(s.image_refs_json),
            }
            for s in skus
        ],
        'verified_parameters': _parse_list(fact.facts_json),
        'selling_points': [],
        'usage_scenarios': _parse_list(fact.usage_scenarios_json),
        'target_customers': [],
        'buyer_questions': [],
        'package_contents': _parse_list(fact.package_contents_json),
        'prohibited_claims': [],
        'unknown_fields': _parse_list(fact.unknown_fields_json),
        'conflicts': [],
        'evidence_summary': [
            {
                'field_path': e.field_path,
                'evidence_type': e.evidence_type,
                'fact_status': e.fact_status,
                'confidence': e.confidence,
            }
            for e in evidences[:50]
        ],
        'brief_status': fact.review_status or 'draft',
    }
    return brief


# ═══════════════════════════════════════════════════════════════
# 证据创建
# ═══════════════════════════════════════════════════════════════

def create_fact_candidate(
    user,
    fact: ProductFact,
    field_path: str,
    value: Any,
    evidence_type: str = 'text',
    fact_status: str = 'extracted',
    confidence: Optional[float] = None,
    source_type: Optional[str] = None,
    source_locator_json: Optional[str] = None,
    applicable_sku_id: Optional[int] = None,
    source_url: Optional[str] = None,
    content: Optional[str] = None,
    media: Optional[OzonSourceMedia] = None,
    source: Optional[OzonSource] = None,
) -> ProductFactEvidence:
    """创建事实证据候选。通过 evidence_hash 去重。"""
    evidence_hash = _hash_evidence(
        fact.id, field_path, value, evidence_type, source_url, content
    )

    # 去重检查
    existing = (ProductFactEvidence.select().where(
        (ProductFactEvidence.user == user) &
        (ProductFactEvidence.fact == fact) &
        (ProductFactEvidence.field_path == field_path) &
        (ProductFactEvidence.evidence_hash == evidence_hash)
    ).first())
    if existing:
        return existing

    return ProductFactEvidence.create(
        user=user,
        fact=fact,
        field_path=field_path,
        evidence_type=evidence_type,
        fact_status=fact_status,
        confidence=confidence,
        source_type=source_type,
        source_locator_json=source_locator_json,
        applicable_sku_id=applicable_sku_id,
        source_url=source_url,
        content=str(content)[:5000] if content else None,
        media=media,
        source=source,
        value_json=json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value,
        evidence_hash=evidence_hash,
    )


# ═══════════════════════════════════════════════════════════════
# 合并
# ═══════════════════════════════════════════════════════════════

def merge_fact_candidates(fact: ProductFact) -> int:
    """合并同一 field_path 的证据候选。标记冲突。返回合并数量。"""
    user = fact.user
    evidences = list(ProductFactEvidence.select().where(
        (ProductFactEvidence.user == user) &
        (ProductFactEvidence.fact == fact) &
        (ProductFactEvidence.fact_status.not_in(['confirmed', 'rejected']))
    ).order_by(ProductFactEvidence.field_path))

    by_field: Dict[str, List[ProductFactEvidence]] = {}
    for e in evidences:
        by_field.setdefault(e.field_path, []).append(e)

    merged = 0
    for field_path, ev_list in by_field.items():
        if len(ev_list) <= 1:
            continue
        # 排序：置信度高的在前
        ev_list.sort(key=lambda x: x.confidence or 0, reverse=True)
        # 检查冲突
        values = [_normalize_value(_parse_evidence_value(e)) for e in ev_list]
        unique_values = set(values)
        if len(unique_values) > 1 and values[0] != values[1]:
            # 有冲突 → 标记
            conflict_group = _next_conflict_group(user, fact)
            for e in ev_list:
                e.fact_status = 'conflict'
                e.conflict_group = conflict_group
                e.save()
            merged += len(ev_list)
    return merged


# ═══════════════════════════════════════════════════════════════
# 冲突检测
# ═══════════════════════════════════════════════════════════════

def detect_fact_conflicts(fact: ProductFact) -> List[Dict[str, Any]]:
    """检测 ProductFact 中的所有冲突。"""
    user = fact.user
    conflicts_raw = list(ProductFactEvidence.select().where(
        (ProductFactEvidence.user == user) &
        (ProductFactEvidence.fact == fact) &
        (ProductFactEvidence.fact_status == 'conflict')
    ))

    conflict_groups: Dict[int, List[ProductFactEvidence]] = {}
    for e in conflicts_raw:
        cg = e.conflict_group or 0
        conflict_groups.setdefault(cg, []).append(e)

    results = []
    for cg, ev_list in conflict_groups.items():
        values = [_parse_evidence_value(e) for e in ev_list]
        results.append({
            'conflict_group': cg,
            'field_path': ev_list[0].field_path if ev_list else '',
            'values': values,
            'evidence_ids': [e.id for e in ev_list],
            'evidence_types': [e.evidence_type for e in ev_list],
        })
    return results


def resolve_conflict(fact: ProductFact, conflict_group: int, winning_evidence_id: int, user) -> bool:
    """解决冲突：选择胜出证据，其余标记为 rejected。"""
    evidences = list(ProductFactEvidence.select().where(
        (ProductFactEvidence.user == user) &
        (ProductFactEvidence.fact == fact) &
        (ProductFactEvidence.conflict_group == conflict_group)
    ))
    for e in evidences:
        if e.id == winning_evidence_id:
            e.fact_status = 'verified'
        else:
            e.fact_status = 'rejected'
            e.rejected_reason = f'冲突解决: 证据 #{winning_evidence_id} 被人工选择'
        e.save()
    return True


# ═══════════════════════════════════════════════════════════════
# 确认 / 拒绝
# ═══════════════════════════════════════════════════════════════

def confirm_fact_evidence(evidence_id: int, user, confirmed_by) -> bool:
    """确认单条证据。状态改为 confirmed。"""
    ev = ProductFactEvidence.get_or_none(
        (ProductFactEvidence.id == evidence_id) &
        (ProductFactEvidence.user == user)
    )
    if not ev:
        return False
    if ev.fact_status == 'conflict':
        return False  # 冲突状态需先解决
    ev.fact_status = 'confirmed'
    ev.confirmed_by = confirmed_by
    ev.confirmed_at = datetime.datetime.now()
    ev.save()
    return True


def reject_fact_evidence(evidence_id: int, user, reason: str = '') -> bool:
    """拒绝证据。"""
    ev = ProductFactEvidence.get_or_none(
        (ProductFactEvidence.id == evidence_id) &
        (ProductFactEvidence.user == user)
    )
    if not ev:
        return False
    ev.fact_status = 'rejected'
    ev.rejected_reason = reason[:500]
    ev.save()
    return True


# ═══════════════════════════════════════════════════════════════
# 验证
# ═══════════════════════════════════════════════════════════════

def validate_product_brief(fact: ProductFact) -> Dict[str, Any]:
    """验证 Product Brief 是否可以审核通过。返回 blocking_errors 列表。"""
    errors = []
    user = fact.user
    brief = build_product_brief(fact)

    # 1. 商品名称
    if not brief['product_identity'].get('name'):
        errors.append({'field': 'product_identity.name', 'reason': '商品名称未确认'})

    # 2. 商品类型
    if not brief['product_identity'].get('product_type'):
        errors.append({'field': 'product_identity.product_type', 'reason': '商品类型未确认'})

    # 3. 品牌
    brand = brief['product_identity'].get('brand', '')
    if not brand:
        errors.append({'field': 'product_identity.brand', 'reason': '品牌未确认（至少需设为 unknown）'})

    # 4. SKU
    skus = list(ProductFactSku.select().where(
        (ProductFactSku.fact == fact) & (ProductFactSku.user == user)
    ))
    source_skus = list(OzonSource.select().join(
        ProductFact, on=(ProductFact.source_id == OzonSource.id)  # approximate
    ).where(ProductFact.id == fact.id))
    # Check SKU evidence
    for s in skus:
        confirmed_count = (ProductFactEvidence.select().where(
            (ProductFactEvidence.user == user) &
            (ProductFactEvidence.fact_sku == s) &
            (ProductFactEvidence.fact_status == 'confirmed')
        ).count())
        if confirmed_count == 0:
            errors.append({'field': f'skus[{s.source_order}]', 'reason': f'SKU #{s.source_order} 未人工确认'})

    # 5. 冲突
    conflicts = detect_fact_conflicts(fact)
    unresolved = [c for c in conflicts if c['evidence_ids']]
    if unresolved:
        errors.append({'field': 'conflicts', 'reason': f'存在 {len(unresolved)} 个未解决的冲突'})

    # 6. 高置信度推断事实
    inferred = list(ProductFactEvidence.select().where(
        (ProductFactEvidence.user == user) &
        (ProductFactEvidence.fact == fact) &
        (ProductFactEvidence.fact_status == 'inferred') &
        (ProductFactEvidence.confidence >= HIGH_CONFIDENCE)
    ))
    if inferred:
        errors.append({'field': 'inferred', 'reason': f'存在 {len(inferred)} 条高置信度推断事实未确认'})

    passed = len(errors) == 0
    return {
        'passed': passed,
        'blocking_errors': errors,
        'total_evidences': ProductFactEvidence.select().where(
            (ProductFactEvidence.user == user) &
            (ProductFactEvidence.fact == fact)
        ).count(),
        'confirmed_evidences': ProductFactEvidence.select().where(
            (ProductFactEvidence.user == user) &
            (ProductFactEvidence.fact == fact) &
            (ProductFactEvidence.fact_status == 'confirmed')
        ).count(),
        'conflict_count': len(conflicts),
    }


# ═══════════════════════════════════════════════════════════════
# 版本快照
# ═══════════════════════════════════════════════════════════════

def create_fact_revision(fact: ProductFact, user) -> ProductFactRevision:
    """创建不可覆盖的 Product Brief 版本快照。"""
    brief = build_product_brief(fact)
    last_rev = (ProductFactRevision.select().where(
        (ProductFactRevision.fact == fact) & (ProductFactRevision.user == fact.user)
    ).order_by(ProductFactRevision.revision.desc()).first())
    rev_num = (last_rev.revision + 1) if last_rev else 1

    return ProductFactRevision.create(
        user=fact.user,
        fact=fact,
        revision=rev_num,
        brief_json=json.dumps(brief, ensure_ascii=False),
        status='confirmed',
        created_by=user,
    )


# ═══════════════════════════════════════════════════════════════
# 为生图提供只读接口
# ═══════════════════════════════════════════════════════════════

def get_confirmed_facts_for_generation(fact: ProductFact) -> Dict[str, Any]:
    """只返回已确认的事实，用于生图 Prompt 构建。"""
    user = fact.user
    confirmed = list(ProductFactEvidence.select().where(
        (ProductFactEvidence.user == user) &
        (ProductFactEvidence.fact == fact) &
        (ProductFactEvidence.fact_status == 'confirmed')
    ))

    brief = build_product_brief(fact)

    # 过滤：仅保留 confirmed 的证据字段
    confirmed_fields = set(e.field_path for e in confirmed)

    result = {
        'fact_id': fact.id,
        'product_identity': _filter_confirmed(brief['product_identity'], confirmed_fields, ''),
        'sku_variants': [
            sku for sku in brief['sku_variants']
            if any(f'fact_sku_id:{sku["fact_sku_id"]}' in cf or cf.startswith(f'skus[{sku["source_order"]}]') for cf in confirmed_fields)
        ],
        'verified_parameters': [p for p in brief['verified_parameters'] if p.get('status') == 'confirmed'],
        'usage_scenarios': brief['usage_scenarios'],
        'immutable_features': brief['physical_structure'].get('immutable_features', []),
        'prohibited_claims': brief.get('prohibited_claims', []),
        'total_confirmed': len(confirmed),
        'total_fields': len(confirmed_fields),
    }
    return result


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _hash_evidence(fact_id: int, field_path: str, value: Any, evidence_type: str,
                   source_url: Optional[str], content: Optional[str]) -> str:
    raw = f'{fact_id}|{field_path}|{value}|{evidence_type}|{source_url or ""}|{content or ""}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _parse_evidence_value(ev: ProductFactEvidence) -> Any:
    if ev.value_json:
        try:
            return json.loads(ev.value_json) if ev.value_json.startswith('{') or ev.value_json.startswith('[') else ev.value_json
        except (json.JSONDecodeError, TypeError):
            return ev.value_json
    return ev.content


def _normalize_value(v: Any) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    return str(v).strip().lower()


def _parse_list(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else [data]
    except (json.JSONDecodeError, TypeError):
        return [raw] if raw else []


def _next_conflict_group(user, fact: ProductFact) -> int:
    last = (ProductFactEvidence.select().where(
        (ProductFactEvidence.user == user) &
        (ProductFactEvidence.fact == fact)
    ).order_by(ProductFactEvidence.conflict_group.desc()).first())
    return (last.conflict_group + 1) if last and last.conflict_group else 1


def _filter_confirmed(identity: dict, confirmed_fields: set, prefix: str) -> dict:
    """只返回已确认的字段子集。"""
    result = {}
    for k, v in identity.items():
        full_path = f'{prefix}.{k}' if prefix else k
        if full_path in confirmed_fields or any(cf.startswith(full_path) for cf in confirmed_fields):
            result[k] = v
    return result if result else identity  # fallback: return full identity
