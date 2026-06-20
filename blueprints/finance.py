# blueprints/finance.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import Customer, CustomerOrder, SalesOrder, CustomerRefund
from peewee import fn
from helpers import parse_non_negative_float

finance_bp = Blueprint('finance', __name__)

@finance_bp.route('/customer/finance', methods=['GET', 'POST'])
@login_required
def customer_finance_overview():
    if request.method == 'POST':
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
        return redirect(url_for('finance.customer_finance_overview'))

    customers = Customer.select().where(Customer.user == current_user)
    rows = []
    for customer in customers:
        # 订单总金额
        order_total = (CustomerOrder
                       .select(fn.SUM(CustomerOrder.total_amount))
                       .where((CustomerOrder.customer == customer) & (CustomerOrder.user == current_user))
                       .scalar()) or 0

        # 已发货金额
        total_shipped = (SalesOrder
                         .select(fn.SUM(SalesOrder.total_amount))
                         .where((SalesOrder.customer == customer) & (SalesOrder.user == current_user))
                         .scalar()) or 0

        # 实际退款总金额（已发生的退款）
        actual_refund = (CustomerRefund
                        .select(fn.SUM(CustomerRefund.amount))
                        .where((CustomerRefund.customer == customer) & (CustomerRefund.user == current_user))
                        .scalar()) or 0

        # 预计退款总金额（手动设定）
        planned_refund = getattr(customer, 'planned_refund', 0.0) or 0.0

        # 剩余未退款 = 预计退款 - 实际退款
        remaining_refund = max(planned_refund - actual_refund, 0)

        # 财务指标拆分
        pending_shipment = max(order_total - total_shipped, 0)   # 待发货金额
        net_receivable = total_shipped - actual_refund            # 已发货净应收
        balance = order_total - total_shipped - actual_refund     # 客户余额 = 订货总额 - 已发货 - 已退款

        rows.append({
            'customer': customer,
            'total_order': order_total,
            'total_shipped': total_shipped,
            'pending_shipment': pending_shipment,
            'actual_refund': actual_refund,
            'planned_refund': planned_refund,
            'remaining_refund': remaining_refund,
            'net_receivable': net_receivable,
            'balance': balance
        })

    return render_template('customer_finance.html', rows=rows)
