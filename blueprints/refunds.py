# blueprints/refunds.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from models import Customer, CustomerRefund, SalesOrder, CustomerOrder
from peewee import fn, JOIN
from helpers import parse_non_negative_float, parse_positive_float
import datetime

refunds_bp = Blueprint('refunds', __name__)

@refunds_bp.route('/refunds', methods=['GET', 'POST'])
@login_required
def manage_refunds():
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        sales_order_id = request.form.get('sales_order_id') or None
        customer_order_id = request.form.get('customer_order_id') or None
        refund_date = request.form.get('refund_date')
        amount = request.form.get('amount')
        remark = request.form.get('remark', '')

        customer = Customer.get_or_none((Customer.id == customer_id) & (Customer.user == current_user))
        amount = parse_positive_float(amount)
        sales_order = None
        customer_order = None
        if sales_order_id:
            sales_order = SalesOrder.get_or_none((SalesOrder.id == sales_order_id) &
                                                 (SalesOrder.user == current_user))
        if customer_order_id:
            customer_order = CustomerOrder.get_or_none((CustomerOrder.id == customer_order_id) &
                                                       (CustomerOrder.user == current_user))
        if not customer or amount is None or (sales_order_id and not sales_order) or (customer_order_id and not customer_order):
            flash('退款记录包含无效的客户、订单或金额', 'danger')
            return redirect(url_for('refunds.manage_refunds'))

        # 校验退款总额不超过订单金额（仅对关联了出库单的退款）
        if sales_order:
            existing_refunds = (CustomerRefund
                               .select(fn.SUM(CustomerRefund.amount))
                               .where(CustomerRefund.sales_order == sales_order)
                               .scalar()) or 0
            if existing_refunds + amount > sales_order.total_amount:
                flash(f'退款总额({existing_refunds + amount:.2f})不能超过订单金额({sales_order.total_amount:.2f})', 'danger')
                return redirect(url_for('refunds.manage_refunds'))

        CustomerRefund.create(
            customer=customer,
            sales_order=sales_order,
            customer_order=customer_order,
            refund_date=refund_date or datetime.date.today(),
            amount=amount,
            remark=remark or None,
            user=current_user
        )
        flash('退款记录添加成功', 'success')
        return redirect(url_for('refunds.manage_refunds'))

    refunds = (CustomerRefund
               .select(CustomerRefund, Customer, SalesOrder, CustomerOrder)
               .join(Customer)
               .switch(CustomerRefund)
               .join(SalesOrder, JOIN.LEFT_OUTER, on=(CustomerRefund.sales_order == SalesOrder.id))
               .join(CustomerOrder, JOIN.LEFT_OUTER, on=(CustomerRefund.customer_order == CustomerOrder.id))
               .where(CustomerRefund.user == current_user)
               .order_by(CustomerRefund.refund_date.desc()))

    customers = Customer.select().where(Customer.user == current_user)
    sales_orders = SalesOrder.select().where(SalesOrder.user == current_user)
    customer_orders = CustomerOrder.select().where(CustomerOrder.user == current_user)

    return render_template('refunds.html',
                           refunds=refunds,
                           customers=customers,
                           sales_orders=sales_orders,
                           customer_orders=customer_orders)


@refunds_bp.route('/refunds/edit/<int:refund_id>', methods=['POST'])
@login_required
def edit_refund(refund_id):
    refund = CustomerRefund.get_or_none((CustomerRefund.id == refund_id) & (CustomerRefund.user == current_user))
    if not refund:
        flash('退款记录不存在或无权访问', 'danger')
        return redirect(url_for('refunds.manage_refunds'))

    customer_id = request.form.get('customer_id')
    sales_order_id = request.form.get('sales_order_id') or None
    customer_order_id = request.form.get('customer_order_id') or None
    customer = Customer.get_or_none((Customer.id == customer_id) & (Customer.user == current_user))
    sales_order = None
    customer_order = None
    if sales_order_id:
        sales_order = SalesOrder.get_or_none((SalesOrder.id == sales_order_id) &
                                             (SalesOrder.user == current_user))
    if customer_order_id:
        customer_order = CustomerOrder.get_or_none((CustomerOrder.id == customer_order_id) &
                                                   (CustomerOrder.user == current_user))
    amount = parse_positive_float(request.form.get('amount', 0))
    if not customer or amount is None or (sales_order_id and not sales_order) or (customer_order_id and not customer_order):
        flash('退款记录包含无效的客户、订单或金额', 'danger')
        return redirect(url_for('refunds.manage_refunds'))

    # 校验退款总额不超过订单金额
    if sales_order:
        existing_refunds = (CustomerRefund
                           .select(fn.SUM(CustomerRefund.amount))
                           .where((CustomerRefund.sales_order == sales_order) &
                                  (CustomerRefund.id != refund.id))
                           .scalar()) or 0
        if existing_refunds + amount > sales_order.total_amount:
            flash(f'退款总额({existing_refunds + amount:.2f})不能超过订单金额({sales_order.total_amount:.2f})', 'danger')
            return redirect(url_for('refunds.manage_refunds'))

    refund.customer = customer
    refund.sales_order = sales_order
    refund.customer_order = customer_order
    refund.refund_date = request.form.get('refund_date') or datetime.date.today()
    refund.amount = amount
    refund.remark = request.form.get('remark', '') or None
    refund.save()

    flash('退款记录修改成功', 'success')
    return redirect(url_for('refunds.manage_refunds'))


@refunds_bp.route('/refunds/delete/<int:refund_id>', methods=['POST'])
@login_required
def delete_refund(refund_id):
    refund = CustomerRefund.get_or_none((CustomerRefund.id == refund_id) & (CustomerRefund.user == current_user))
    if refund:
        refund.delete_instance()
        flash('退款记录已删除', 'success')
    else:
        flash('退款记录不存在或无权访问', 'danger')
    return redirect(url_for('refunds.manage_refunds'))

@refunds_bp.route('/refunds/set_planned_refund', methods=['POST'])
@login_required
def set_planned_refund():
    customer_id = request.form.get('customer_id')
    planned_refund = request.form.get('planned_refund', '0')
    planned_refund = parse_non_negative_float(planned_refund)
    if planned_refund is None:
        planned_refund = 0.0
    customer = Customer.get_or_none((Customer.id == customer_id) & (Customer.user == current_user))
    if customer:
        customer.planned_refund = planned_refund
        customer.save()
        flash('预计退款金额已更新', 'success')
    return redirect(url_for('refunds.manage_refunds'))
