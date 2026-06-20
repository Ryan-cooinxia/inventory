"""
套装组合 — Blueprint（零件 → 套装，与拆包方向相反）
"""
import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from peewee import fn
from models import (db, Product,
                    ProductAssemblyRule, ProductAssemblyRuleItem,
                    ProductAssemblyOrder, ProductAssemblyOrderItem)
from helpers import get_product_stock
from models import update_product_stock
from log_utils import log_action

assembly_bp = Blueprint('inventory_assembly', __name__, url_prefix='/inventory')


def _gen_assembly_no(user=None):
    """生成组合单号"""
    if user is None:
        user = current_user
    today = datetime.date.today()
    count = (ProductAssemblyOrder
             .select()
             .where((ProductAssemblyOrder.user == user) &
                    (ProductAssemblyOrder.assembly_date == today))
             .count())
    return f"AS-{today.strftime('%Y%m%d')}-{count + 1:03d}"


# ═══════════════════════════════════════
# 组合规则
# ═══════════════════════════════════════

@assembly_bp.route('/assembly-rules')
@login_required
def assembly_rules():
    rules = (ProductAssemblyRule
             .select()
             .where(ProductAssemblyRule.user == current_user)
             .order_by(ProductAssemblyRule.updated_at.desc()))

    products = (Product
                .select()
                .where(Product.user == current_user)
                .order_by(Product.name))

    return render_template('inventory/assembly_rules.html',
                           rules=rules, products=products)


@assembly_bp.route('/assembly-rules/add', methods=['POST'])
@login_required
def assembly_rule_add():
    name = request.form.get('name', '').strip()
    bundle_id = request.form.get('bundle_product_id', '').strip()
    cost_method = request.form.get('cost_method', 'sum').strip()

    if not name or not bundle_id:
        flash('规则名称和套装产品必填', 'danger')
        return redirect(url_for('inventory_assembly.assembly_rules'))

    bundle = Product.get_or_none((Product.id == bundle_id) & (Product.user == current_user))
    if not bundle:
        flash('产品不存在', 'danger')
        return redirect(url_for('inventory_assembly.assembly_rules'))

    rule = ProductAssemblyRule.create(
        user=current_user,
        name=name,
        bundle_product=bundle,
        cost_method=cost_method,
        remark=request.form.get('remark', '') or None,
    )

    component_ids = request.form.getlist('component_product_id[]')
    quantities = request.form.getlist('component_quantity[]')
    cost_ratios = request.form.getlist('component_cost_ratio[]')

    total_ratio = 0
    for cid, qty, ratio in zip(component_ids, quantities, cost_ratios):
        if not cid or not qty:
            continue
        product = Product.get_or_none((Product.id == cid) & (Product.user == current_user))
        if not product:
            continue
        r = float(ratio) if ratio else 0
        total_ratio += r
        ProductAssemblyRuleItem.create(
            rule=rule,
            component_product=product,
            quantity=float(qty),
            cost_ratio=r if cost_method == 'manual' else None,
            user=current_user,
        )

    if cost_method == 'manual' and abs(total_ratio - 1.0) > 0.001 and total_ratio > 0:
        items = list(ProductAssemblyRuleItem.select().where(ProductAssemblyRuleItem.rule == rule))
        scale = 1.0 / total_ratio
        for item in items:
            item.cost_ratio = (item.cost_ratio or 0) * scale
            item.save()

    flash('组合规则创建成功', 'success')
    return redirect(url_for('inventory_assembly.assembly_rules'))


@assembly_bp.route('/assembly-rules/<int:rule_id>/edit', methods=['POST'])
@login_required
def assembly_rule_edit(rule_id):
    rule = ProductAssemblyRule.get_or_none((ProductAssemblyRule.id == rule_id) &
                                           (ProductAssemblyRule.user == current_user))
    if not rule:
        flash('规则不存在', 'danger')
        return redirect(url_for('inventory_assembly.assembly_rules'))

    name = request.form.get('name', '').strip()
    bundle_id = request.form.get('bundle_product_id', '').strip()
    cost_method = request.form.get('cost_method', 'sum').strip()

    if not name or not bundle_id:
        flash('规则名称和套装产品必填', 'danger')
        return redirect(url_for('inventory_assembly.assembly_rules'))

    bundle = Product.get_or_none((Product.id == bundle_id) & (Product.user == current_user))
    if not bundle:
        flash('产品不存在', 'danger')
        return redirect(url_for('inventory_assembly.assembly_rules'))

    rule.name = name
    rule.bundle_product = bundle
    rule.cost_method = cost_method
    rule.remark = request.form.get('remark', '') or None
    rule.updated_at = datetime.datetime.now()
    rule.save()

    ProductAssemblyRuleItem.delete().where(ProductAssemblyRuleItem.rule == rule).execute()

    component_ids = request.form.getlist('component_product_id[]')
    quantities = request.form.getlist('component_quantity[]')
    cost_ratios = request.form.getlist('component_cost_ratio[]')

    total_ratio = 0
    for cid, qty, ratio in zip(component_ids, quantities, cost_ratios):
        if not cid or not qty:
            continue
        product = Product.get_or_none((Product.id == cid) & (Product.user == current_user))
        if not product:
            continue
        r = float(ratio) if ratio else 0
        total_ratio += r
        ProductAssemblyRuleItem.create(
            rule=rule,
            component_product=product,
            quantity=float(qty),
            cost_ratio=r if cost_method == 'manual' else None,
            user=current_user,
        )

    if cost_method == 'manual' and abs(total_ratio - 1.0) > 0.001 and total_ratio > 0:
        items = list(ProductAssemblyRuleItem.select().where(ProductAssemblyRuleItem.rule == rule))
        scale = 1.0 / total_ratio
        for item in items:
            item.cost_ratio = (item.cost_ratio or 0) * scale
            item.save()

    flash('组合规则更新成功', 'success')
    return redirect(url_for('inventory_assembly.assembly_rules'))


@assembly_bp.route('/assembly-rules/<int:rule_id>/delete', methods=['POST'])
@login_required
def assembly_rule_delete(rule_id):
    rule = ProductAssemblyRule.get_or_none((ProductAssemblyRule.id == rule_id) &
                                           (ProductAssemblyRule.user == current_user))
    if not rule:
        flash('规则不存在', 'danger')
        return redirect(url_for('inventory_assembly.assembly_rules'))

    ProductAssemblyRuleItem.delete().where(ProductAssemblyRuleItem.rule == rule).execute()
    rule.delete_instance()
    flash('规则已删除', 'success')
    return redirect(url_for('inventory_assembly.assembly_rules'))


# ═══════════════════════════════════════
# 组合单
# ═══════════════════════════════════════

@assembly_bp.route('/assembly-orders')
@login_required
def assembly_orders():
    orders = (ProductAssemblyOrder
              .select()
              .where(ProductAssemblyOrder.user == current_user)
              .order_by(ProductAssemblyOrder.created_at.desc()))

    rules = (ProductAssemblyRule
             .select()
             .where(ProductAssemblyRule.user == current_user))

    return render_template('inventory/assembly_orders.html',
                           orders=orders, rules=rules)


@assembly_bp.route('/assembly-orders/add', methods=['POST'])
@login_required
def assembly_order_add():
    rule_id = request.form.get('rule_id', '').strip()
    bundle_qty = float(request.form.get('bundle_quantity', 0))

    if not rule_id or bundle_qty <= 0:
        flash('请选择规则并填写有效数量', 'danger')
        return redirect(url_for('inventory_assembly.assembly_orders'))

    rule = ProductAssemblyRule.get_or_none((ProductAssemblyRule.id == rule_id) &
                                           (ProductAssemblyRule.user == current_user))
    if not rule:
        flash('规则不存在', 'danger')
        return redirect(url_for('inventory_assembly.assembly_orders'))

    rule_items = list(ProductAssemblyRuleItem.select().where(ProductAssemblyRuleItem.rule == rule))
    if not rule_items:
        flash('规则没有明细', 'danger')
        return redirect(url_for('inventory_assembly.assembly_orders'))

    # 校验所有零件库存
    for ri in rule_items:
        need = ri.quantity * bundle_qty
        stock = get_product_stock(ri.component_product)
        if stock < need:
            flash(f'零件库存不足：{ri.component_product.name} 当前 {stock}，需要 {need}', 'danger')
            return redirect(url_for('inventory_assembly.assembly_orders'))

    # 计算总成本（零件加权平均成本累加）
    total_cost = 0
    detail = []
    for ri in rule_items:
        need_qty = ri.quantity * bundle_qty
        unit_cost = ri.component_product.avg_cost or 0
        line_cost = unit_cost * need_qty
        total_cost += line_cost
        detail.append({
            'component': ri.component_product,
            'quantity': need_qty,
            'unit_cost': unit_cost,
            'total_cost': line_cost,
        })

    order = ProductAssemblyOrder.create(
        user=current_user,
        assembly_no=_gen_assembly_no(user=current_user),
        assembly_date=datetime.date.today(),
        rule=rule,
        bundle_product=rule.bundle_product,
        bundle_quantity=bundle_qty,
        total_cost=round(total_cost, 2),
        status='draft',
        remark=request.form.get('remark', '') or None,
    )

    for d in detail:
        ProductAssemblyOrderItem.create(
            order=order,
            component_product=d['component'],
            quantity=d['quantity'],
            unit_cost=d['unit_cost'],
            total_cost=round(d['total_cost'], 2),
            user=current_user,
        )

    flash(f'组合单 {order.assembly_no} 创建成功（草稿）', 'success')
    return redirect(url_for('inventory_assembly.assembly_orders'))


@assembly_bp.route('/assembly-orders/<int:order_id>/confirm', methods=['POST'])
@login_required
def assembly_order_confirm(order_id):
    """确认组合单（扣减零件库存，增加套装库存）"""
    order = ProductAssemblyOrder.get_or_none((ProductAssemblyOrder.id == order_id) &
                                             (ProductAssemblyOrder.user == current_user))
    if not order or order.status != 'draft':
        flash('组合单不存在或状态不允许确认', 'danger')
        return redirect(url_for('inventory_assembly.assembly_orders'))

    # 再次校验所有零件库存
    for item in order.items:
        stock = get_product_stock(item.component_product)
        if stock < item.quantity:
            flash(f'零件库存不足：{item.component_product.name} 当前 {stock}，需要 {item.quantity}', 'danger')
            return redirect(url_for('inventory_assembly.assembly_orders'))

    order.status = 'confirmed'
    order.save()

    # 更新受影响产品的库存缓存
    update_product_stock(order.bundle_product)
    for item in order.items:
        update_product_stock(item.component_product)

    log_action(current_user, 'confirm', 'ProductAssemblyOrder', order.id,
               f'确认组合单 {order.assembly_no}：{order.bundle_product.name} x{order.bundle_quantity}',
               request.remote_addr)

    flash(f'组合单 {order.assembly_no} 已确认，库存已更新', 'success')
    return redirect(url_for('inventory_assembly.assembly_orders'))


@assembly_bp.route('/assembly-orders/<int:order_id>/cancel', methods=['POST'])
@login_required
def assembly_order_cancel(order_id):
    """撤销组合单（归还零件库存，扣减套装库存）"""
    order = ProductAssemblyOrder.get_or_none((ProductAssemblyOrder.id == order_id) &
                                             (ProductAssemblyOrder.user == current_user))
    if not order or order.status != 'confirmed':
        flash('组合单不存在或状态不允许撤销', 'danger')
        return redirect(url_for('inventory_assembly.assembly_orders'))

    order.status = 'cancelled'
    order.cancelled_at = datetime.datetime.now()
    order.save()

    # 恢复库存
    update_product_stock(order.bundle_product)
    for item in order.items:
        update_product_stock(item.component_product)

    log_action(current_user, 'cancel', 'ProductAssemblyOrder', order.id,
               f'撤销组合单 {order.assembly_no}：{order.bundle_product.name} x{order.bundle_quantity}',
               request.remote_addr)

    flash(f'组合单 {order.assembly_no} 已撤销，库存已恢复', 'success')
    return redirect(url_for('inventory_assembly.assembly_orders'))
