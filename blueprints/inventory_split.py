"""
库存转换 / 套装拆分 — Blueprint
"""
import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from peewee import fn
from models import (db, Product,
                    ProductSplitRule, ProductSplitRuleItem,
                    ProductSplitOrder, ProductSplitOrderItem)
from helpers import get_product_stock
from models import update_product_stock
from log_utils import log_action

split_bp = Blueprint('inventory_split', __name__, url_prefix='/inventory')


def _gen_split_no(user=None):
    """生成拆包单号"""
    if user is None:
        user = current_user
    today = datetime.date.today()
    count = (ProductSplitOrder
             .select()
             .where((ProductSplitOrder.user == user) &
                    (ProductSplitOrder.split_date == today))
             .count())
    return f"SP-{today.strftime('%Y%m%d')}-{count + 1:03d}"


def auto_create_split_order(product, quantity, unit_cost, user):
    """检查产品是否有拆包规则，有则自动创建草稿拆包单。
    返回 ProductSplitOrder 或 None"""
    rule = (ProductSplitRule
            .select()
            .where((ProductSplitRule.source_product == product) &
                   (ProductSplitRule.user == user))
            .first())
    if not rule:
        return None

    rule_items = list(ProductSplitRuleItem.select().where(ProductSplitRuleItem.rule == rule))
    if not rule_items:
        return None

    src_total_cost = unit_cost * quantity

    order = ProductSplitOrder.create(
        user=user,
        split_no=_gen_split_no(user=user),
        split_date=datetime.date.today(),
        rule=rule,
        source_product=product,
        source_quantity=quantity,
        source_unit_cost=unit_cost,
        source_total_cost=src_total_cost,
        status='draft',
        remark=f'入库自动生成',
    )

    total_ratio = sum(it.cost_ratio or 0 for it in rule_items)
    for ri in rule_items:
        out_qty = ri.quantity * quantity
        if ri.cost_ratio and total_ratio > 0:
            item_unit_cost = (src_total_cost * ri.cost_ratio) / out_qty if out_qty > 0 else 0
        else:
            item_unit_cost = unit_cost / len(rule_items) if len(rule_items) > 0 else 0

        ProductSplitOrderItem.create(
            order=order,
            target_product=ri.target_product,
            quantity=out_qty,
            unit_cost=round(item_unit_cost, 2),
            total_cost=round(item_unit_cost * out_qty, 2),
            user=user,
        )

    log_action(user, 'create', 'ProductSplitOrder', order.id,
               f'入库自动生成拆包单 {order.split_no}：{product.name} x{quantity}',
               'auto')
    return order


# ═══════════════════════════════════════
# 拆包规则
# ═══════════════════════════════════════

@split_bp.route('/split-rules')
@login_required
def split_rules():
    rules = (ProductSplitRule
             .select()
             .where(ProductSplitRule.user == current_user)
             .order_by(ProductSplitRule.updated_at.desc()))

    products = (Product
                .select()
                .where(Product.user == current_user)
                .order_by(Product.name))

    return render_template('inventory/split_rules.html',
                           rules=rules, products=products)


@split_bp.route('/split-rules/add', methods=['POST'])
@login_required
def split_rule_add():
    name = request.form.get('name', '').strip()
    source_id = request.form.get('source_product_id', '').strip()
    cost_method = request.form.get('cost_method', 'ratio').strip()

    if not name or not source_id:
        flash('规则名称和源产品必填', 'danger')
        return redirect(url_for('inventory_split.split_rules'))

    source = Product.get_or_none((Product.id == source_id) & (Product.user == current_user))
    if not source:
        flash('产品不存在', 'danger')
        return redirect(url_for('inventory_split.split_rules'))

    rule = ProductSplitRule.create(
        user=current_user,
        name=name,
        source_product=source,
        cost_method=cost_method,
        remark=request.form.get('remark', '') or None,
    )

    # 保存明细
    target_ids = request.form.getlist('target_product_id[]')
    quantities = request.form.getlist('target_quantity[]')
    cost_ratios = request.form.getlist('target_cost_ratio[]')
    manual_costs = request.form.getlist('target_manual_cost[]')

    total_ratio = 0
    for tid, qty, ratio, manual in zip(target_ids, quantities, cost_ratios, manual_costs):
        if not tid or not qty:
            continue
        product = Product.get_or_none((Product.id == tid) & (Product.user == current_user))
        if not product:
            continue
        r = float(ratio) if ratio else 0
        m = float(manual) if manual else None
        total_ratio += r
        ProductSplitRuleItem.create(
            rule=rule,
            target_product=product,
            quantity=float(qty),
            cost_ratio=r if cost_method == 'ratio' else None,
            manual_unit_cost=m if cost_method == 'manual' else None,
            user=current_user,
        )

    # 比例自动补齐
    if cost_method == 'ratio' and abs(total_ratio - 1.0) > 0.001:
        items = list(ProductSplitRuleItem.select().where(ProductSplitRuleItem.rule == rule))
        if items and total_ratio > 0:
            scale = 1.0 / total_ratio
            for item in items:
                item.cost_ratio = (item.cost_ratio or 0) * scale
                item.save()

    flash('拆包规则创建成功', 'success')
    return redirect(url_for('inventory_split.split_rules'))


@split_bp.route('/split-rules/<int:rule_id>/edit', methods=['POST'])
@login_required
def split_rule_edit(rule_id):
    rule = ProductSplitRule.get_or_none((ProductSplitRule.id == rule_id) &
                                        (ProductSplitRule.user == current_user))
    if not rule:
        flash('规则不存在', 'danger')
        return redirect(url_for('inventory_split.split_rules'))

    name = request.form.get('name', '').strip()
    source_id = request.form.get('source_product_id', '').strip()
    cost_method = request.form.get('cost_method', 'ratio').strip()

    if not name or not source_id:
        flash('规则名称和源产品必填', 'danger')
        return redirect(url_for('inventory_split.split_rules'))

    source = Product.get_or_none((Product.id == source_id) & (Product.user == current_user))
    if not source:
        flash('产品不存在', 'danger')
        return redirect(url_for('inventory_split.split_rules'))

    rule.name = name
    rule.source_product = source
    rule.cost_method = cost_method
    rule.remark = request.form.get('remark', '') or None
    rule.updated_at = datetime.datetime.now()
    rule.save()

    # 删除旧明细，重建
    ProductSplitRuleItem.delete().where(ProductSplitRuleItem.rule == rule).execute()

    target_ids = request.form.getlist('target_product_id[]')
    quantities = request.form.getlist('target_quantity[]')
    cost_ratios = request.form.getlist('target_cost_ratio[]')
    manual_costs = request.form.getlist('target_manual_cost[]')

    total_ratio = 0
    for tid, qty, ratio, manual in zip(target_ids, quantities, cost_ratios, manual_costs):
        if not tid or not qty:
            continue
        product = Product.get_or_none((Product.id == tid) & (Product.user == current_user))
        if not product:
            continue
        r = float(ratio) if ratio else 0
        m = float(manual) if manual else None
        total_ratio += r
        ProductSplitRuleItem.create(
            rule=rule,
            target_product=product,
            quantity=float(qty),
            cost_ratio=r if cost_method == 'ratio' else None,
            manual_unit_cost=m if cost_method == 'manual' else None,
            user=current_user,
        )

    if cost_method == 'ratio' and abs(total_ratio - 1.0) > 0.001:
        items = list(ProductSplitRuleItem.select().where(ProductSplitRuleItem.rule == rule))
        if items and total_ratio > 0:
            scale = 1.0 / total_ratio
            for item in items:
                item.cost_ratio = (item.cost_ratio or 0) * scale
                item.save()

    flash('拆包规则更新成功', 'success')
    return redirect(url_for('inventory_split.split_rules'))


@split_bp.route('/split-rules/<int:rule_id>/delete', methods=['POST'])
@login_required
def split_rule_delete(rule_id):
    rule = ProductSplitRule.get_or_none((ProductSplitRule.id == rule_id) &
                                         (ProductSplitRule.user == current_user))
    if not rule:
        flash('规则不存在', 'danger')
        return redirect(url_for('inventory_split.split_rules'))

    # 删除明细
    ProductSplitRuleItem.delete().where(ProductSplitRuleItem.rule == rule).execute()
    rule.delete_instance()
    flash('规则已删除', 'success')
    return redirect(url_for('inventory_split.split_rules'))


# ═══════════════════════════════════════
# 拆包单
# ═══════════════════════════════════════

@split_bp.route('/split-orders')
@login_required
def split_orders():
    orders = (ProductSplitOrder
              .select()
              .where(ProductSplitOrder.user == current_user)
              .order_by(ProductSplitOrder.created_at.desc()))

    rules = (ProductSplitRule
             .select()
             .where(ProductSplitRule.user == current_user))

    return render_template('inventory/split_orders.html',
                           orders=orders, rules=rules)


@split_bp.route('/split-orders/add', methods=['POST'])
@login_required
def split_order_add():
    rule_id = request.form.get('rule_id', '').strip()
    source_qty = float(request.form.get('source_quantity', 0))

    if not rule_id or source_qty <= 0:
        flash('请选择规则并填写有效数量', 'danger')
        return redirect(url_for('inventory_split.split_orders'))

    rule = ProductSplitRule.get_or_none((ProductSplitRule.id == rule_id) &
                                         (ProductSplitRule.user == current_user))
    if not rule:
        flash('规则不存在', 'danger')
        return redirect(url_for('inventory_split.split_orders'))

    rule_items = list(ProductSplitRuleItem.select().where(ProductSplitRuleItem.rule == rule))
    if not rule_items:
        flash('规则没有明细', 'danger')
        return redirect(url_for('inventory_split.split_orders'))

    # 校验库存
    src_stock = get_product_stock(rule.source_product)
    if src_stock < source_qty:
        flash(f'库存不足：{rule.source_product.name} 当前库存 {src_stock}，需要 {source_qty}', 'danger')
        return redirect(url_for('inventory_split.split_orders'))

    # 计算成本（加权平均）
    src_unit_cost = rule.source_product.avg_cost or 0
    src_total_cost = src_unit_cost * source_qty

    order = ProductSplitOrder.create(
        user=current_user,
        split_no=_gen_split_no(),
        split_date=datetime.date.today(),
        rule=rule,
        source_product=rule.source_product,
        source_quantity=source_qty,
        source_unit_cost=src_unit_cost,
        source_total_cost=src_total_cost,
        status='draft',
        remark=request.form.get('remark', '') or None,
    )

    # 创建明细——按规则比例计算产出
    total_ratio = sum(it.cost_ratio or 0 for it in rule_items)
    for ri in rule_items:
        out_qty = ri.quantity * source_qty
        if ri.cost_ratio and total_ratio > 0:
            unit_cost = (src_total_cost * ri.cost_ratio) / out_qty if out_qty > 0 else 0
        else:
            unit_cost = src_unit_cost / len(rule_items) if len(rule_items) > 0 else 0

        ProductSplitOrderItem.create(
            order=order,
            target_product=ri.target_product,
            quantity=out_qty,
            unit_cost=round(unit_cost, 2),
            total_cost=round(unit_cost * out_qty, 2),
            user=current_user,
        )

    flash(f'拆包单 {order.split_no} 创建成功（草稿）', 'success')
    return redirect(url_for('inventory_split.split_orders'))


@split_bp.route('/split-orders/<int:order_id>/confirm', methods=['POST'])
@login_required
def split_order_confirm(order_id):
    """确认拆包单（扣减源库存，增加目标库存）"""
    order = ProductSplitOrder.get_or_none((ProductSplitOrder.id == order_id) &
                                           (ProductSplitOrder.user == current_user))
    if not order or order.status != 'draft':
        flash('拆包单不存在或状态不允许确认', 'danger')
        return redirect(url_for('inventory_split.split_orders'))

    # 再次校验库存
    src_stock = get_product_stock(order.source_product)
    if src_stock < order.source_quantity:
        flash(f'库存不足：当前 {src_stock}，需要 {order.source_quantity}', 'danger')
        return redirect(url_for('inventory_split.split_orders'))

    order.status = 'confirmed'
    order.save()

    # 更新受影响产品的库存缓存
    update_product_stock(order.source_product)
    for item in order.items:
        update_product_stock(item.target_product)

    log_action(current_user, 'confirm', 'ProductSplitOrder', order.id,
               f'确认拆包单 {order.split_no}：{order.source_product.name} x{order.source_quantity}',
               request.remote_addr)

    flash(f'拆包单 {order.split_no} 已确认，库存已更新', 'success')
    return redirect(url_for('inventory_split.split_orders'))


@split_bp.route('/split-orders/<int:order_id>/cancel', methods=['POST'])
@login_required
def split_order_cancel(order_id):
    """撤销拆包单（恢复库存）"""
    order = ProductSplitOrder.get_or_none((ProductSplitOrder.id == order_id) &
                                           (ProductSplitOrder.user == current_user))
    if not order or order.status != 'confirmed':
        flash('拆包单不存在或状态不允许撤销', 'danger')
        return redirect(url_for('inventory_split.split_orders'))

    order.status = 'cancelled'
    order.cancelled_at = datetime.datetime.now()
    order.save()

    # 恢复库存
    update_product_stock(order.source_product)
    for item in order.items:
        update_product_stock(item.target_product)

    log_action(current_user, 'cancel', 'ProductSplitOrder', order.id,
               f'撤销拆包单 {order.split_no}：{order.source_product.name} x{order.source_quantity}',
               request.remote_addr)

    flash(f'拆包单 {order.split_no} 已撤销，库存已恢复', 'success')
    return redirect(url_for('inventory_split.split_orders'))
