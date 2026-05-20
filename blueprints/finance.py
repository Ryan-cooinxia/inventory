# blueprints/finance.py
from flask import Blueprint, render_template
from flask_login import login_required, current_user
from models import Customer, CustomerOrder, SalesOrder, CustomerRefund
from peewee import fn

finance_bp = Blueprint('finance', __name__)

@finance_bp.route('/customer/finance')
@login_required
def customer_finance_overview():
    customers = Customer.select().where(Customer.user == current_user)
    rows = []
    for customer in customers:
        # 订单总金额
        order_total = (CustomerOrder
                       .select(fn.SUM(CustomerOrder.total_amount))
                       .where((CustomerOrder.customer == customer) &
                              (CustomerOrder.user == current_user))
                       .scalar()) or 0

        # 已发货金额
        total_shipped = (SalesOrder
                         .select(fn.SUM(SalesOrder.total_amount))
                         .where((SalesOrder.customer == customer) &
                                (SalesOrder.user == current_user))
                         .scalar()) or 0

        # 退款金额
        total_refund = (CustomerRefund
                        .select(fn.SUM(CustomerRefund.amount))
                        .where((CustomerRefund.customer == customer) &
                               (CustomerRefund.user == current_user))
                        .scalar()) or 0

        balance = order_total - total_shipped - total_refund

        rows.append({
            'customer': customer,
            'total_order': order_total,
            'total_shipped': total_shipped,
            'total_refund': total_refund,
            'balance': balance
        })

    return render_template('customer_finance.html', rows=rows)