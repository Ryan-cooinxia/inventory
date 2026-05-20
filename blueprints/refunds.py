# blueprints/refunds.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import Customer, CustomerRefund, SalesOrder, CustomerOrder
from peewee import fn, JOIN
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

        CustomerRefund.create(
            customer=customer_id,
            sales_order=sales_order_id if sales_order_id else None,
            customer_order=customer_order_id if customer_order_id else None,
            refund_date=refund_date or datetime.date.today(),
            amount=float(amount),
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