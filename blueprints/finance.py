# blueprints/finance.py
from flask import Blueprint, render_template, request, redirect, url_for
from models import Customer, CustomerOrder, SalesOrder, CustomerRefund
from peewee import fn

finance_bp = Blueprint('finance', __name__)


@finance_bp.route('/customer/finance')
def customer_finance_overview():
    customers = Customer.select()
    rows = []
    for customer in customers:
        # 订单总金额
        order_total = (CustomerOrder
                       .select(fn.SUM(CustomerOrder.total_amount))
                       .where(CustomerOrder.customer == customer)
                       .scalar()) or 0

        # 已发货金额（关联了客户订单的出库单总额）
        total_shipped = (SalesOrder
                         .select(fn.SUM(SalesOrder.total_amount))
                         .where(SalesOrder.customer == customer)
                         .scalar()) or 0

        # 退款金额
        total_refund = (CustomerRefund
                        .select(fn.SUM(CustomerRefund.amount))
                        .where(CustomerRefund.customer == customer)
                        .scalar()) or 0

        # 余额 = 订单总额 - 已发货 - 退款
        balance = order_total - total_shipped - total_refund

        rows.append({
            'customer': customer,
            'total_order': order_total,
            'total_shipped': total_shipped,
            'total_refund': total_refund,
            'balance': balance
        })

    return render_template('customer_finance.html', rows=rows)