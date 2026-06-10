# blueprints/sales.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Customer, Product, SalesOrder, SalesOrderItem, CustomerOrder
from peewee import fn
from helpers import check_stock_before_ship, parse_non_negative_float, parse_positive_float
from log_utils import log_action
import datetime

sales_bp = Blueprint('sales', __name__)


@sales_bp.route('/sales/add', methods=['GET', 'POST'])
@login_required
def add_sales():
    customers = Customer.select().where(Customer.user == current_user)
    customers_data = [{'id': c.id, 'name': c.name} for c in customers]
    products = Product.select().where(Product.user == current_user)
    products_data = [{'id': p.id, 'name': p.name} for p in products]

    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        order_date = request.form.get('order_date')
        remark = request.form.get('remark', '')
        ship_method = request.form.get('ship_method', '')
        tracking_number = request.form.get('tracking_number', '')

        customer = Customer.get_or_none((Customer.id == customer_id) & (Customer.user == current_user))
        if not customer:
            flash('客户不存在或无权访问', 'danger')
            return redirect(url_for('sales.add_sales'))

        product_ids = request.form.getlist('product_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')

        items = []
        for pid, qty, price in zip(product_ids, quantities, unit_prices):
            if not pid or not qty or not price:
                continue
            product = Product.get_or_none((Product.id == pid) & (Product.user == current_user))
            qty = parse_positive_float(qty)
            price = parse_non_negative_float(price)
            if not product or qty is None or price is None:
                flash('出库明细包含无效的产品、数量或单价', 'danger')
                return render_template('sales.html',
                                       customers=customers_data,
                                       products=products_data,
                                       customers_json=customers_data,
                                       products_json=products_data)
            items.append({'product_id': int(pid), 'quantity': qty,
                          'unit_price': price, 'subtotal': qty * price})

        if not items:
            flash('请至少填写一条明细', 'danger')
            return render_template('sales.html',
                                   customers=customers_data,
                                   products=products_data,
                                   customers_json=customers_data,
                                   products_json=products_data)

        stock_ok, stock_errors = check_stock_before_ship(items, user=current_user)
        if not stock_ok:
            for err in stock_errors:
                flash(f'库存不足：{err}', 'danger')
            return render_template('sales.html',
                                   customers=customers_data,
                                   products=products_data,
                                   customers_json=customers_data,
                                   products_json=products_data)

        with db.atomic():
            total_amount = sum(item['subtotal'] for item in items)
            order = SalesOrder.create(
                customer=customer,
                order_date=order_date or datetime.date.today(),
                total_amount=total_amount,
                remark=remark or None,
                ship_method=ship_method or None,
                tracking_number=tracking_number or None,
                user=current_user
            )
            for item in items:
                SalesOrderItem.create(
                    order=order,
                    product=item['product_id'],
                    quantity=item['quantity'],
                    unit_price=item['unit_price'],
                    subtotal=item['subtotal'],
                    user=current_user
                )
        flash(f'出库单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('sales.add_sales'))

    return render_template('sales.html',
                           customers=customers_data,
                           products=products_data,
                           customers_json=customers_data,
                           products_json=products_data)


@sales_bp.route('/shipments')
@login_required
def list_shipments():
    customer_id = request.args.get('customer_id')
    query = SalesOrder.select().where(SalesOrder.user == current_user).order_by(SalesOrder.order_date.desc())
    if customer_id:
        query = query.where(SalesOrder.customer == int(customer_id))

    rows = []
    for so in query:
        order_id = so.customer_order_id
        items = list(so.items)
        total_lines = len(items) if items else 1
        for idx, item in enumerate(items):
            rows.append({
                'ship_id': so.id,
                'customer_name': so.customer.name,
                'order_id': order_id,
                'order_date': so.order_date,
                'total_amount': so.total_amount,
                'ship_method': so.ship_method or '-',
                'tracking_number': so.tracking_number or '-',
                'product_name': item.product.name,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'subtotal': item.subtotal,
                'is_first_row': idx == 0,
                'rowspan_count': total_lines
            })
        if not items:
            rows.append({
                'ship_id': so.id,
                'customer_name': so.customer.name,
                'order_id': order_id,
                'order_date': so.order_date,
                'total_amount': so.total_amount,
                'ship_method': so.ship_method or '-',
                'tracking_number': so.tracking_number or '-',
                'product_name': '-',
                'quantity': 0,
                'unit_price': 0,
                'subtotal': 0,
                'is_first_row': True,
                'rowspan_count': 1
            })

    return render_template('shipments.html', shipments=rows)


@sales_bp.route('/shipments/edit/<int:shipment_id>', methods=['GET', 'POST'])
@login_required
def edit_shipment(shipment_id):
    shipment = SalesOrder.get_or_none((SalesOrder.id == shipment_id) & (SalesOrder.user == current_user))
    if not shipment:
        flash('出库单不存在或无权访问', 'danger')
        return redirect(url_for('sales.list_shipments'))

    if request.method == 'POST':
        customer = Customer.get_or_none((Customer.id == request.form.get('customer_id')) &
                                        (Customer.user == current_user))
        if not customer:
            flash('客户不存在或无权访问', 'danger')
            return redirect(url_for('sales.list_shipments'))
        shipment.customer = customer
        shipment.order_date = request.form.get('order_date')
        shipment.remark = request.form.get('remark', '') or None
        shipment.ship_method = request.form.get('ship_method', '') or None
        shipment.tracking_number = request.form.get('tracking_number', '') or None

        product_ids = request.form.getlist('product_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')
        items = []
        for pid, qty, price in zip(product_ids, quantities, unit_prices):
            if not pid or not qty or not price:
                continue
            product = Product.get_or_none((Product.id == pid) & (Product.user == current_user))
            qty = parse_positive_float(qty)
            price = parse_non_negative_float(price)
            if not product or qty is None or price is None:
                flash('出库明细包含无效的产品、数量或单价', 'danger')
                return redirect(url_for('sales.edit_shipment', shipment_id=shipment.id))
            items.append({'product_id': int(pid), 'quantity': qty, 'unit_price': price, 'subtotal': qty * price})
        if not items:
            flash('请至少填写一条明细', 'danger')
            return redirect(url_for('sales.edit_shipment', shipment_id=shipment.id))

        # 编辑时也校验库存：计算每个产品增量，只检查新增部分
        old_qty_map = {}
        for old_item in SalesOrderItem.select().where(SalesOrderItem.order == shipment):
            old_qty_map[old_item.product_id] = old_qty_map.get(old_item.product_id, 0) + old_item.quantity

        delta_items = []
        for it in items:
            pid = it['product_id']
            old_qty = old_qty_map.get(pid, 0)
            delta = it['quantity'] - old_qty
            if delta > 0:
                delta_items.append({'product_id': pid, 'quantity': delta})

        if delta_items:
            stock_ok, stock_errors = check_stock_before_ship(delta_items, user=current_user)
            if not stock_ok:
                for err in stock_errors:
                    flash(f'库存不足：{err}', 'danger')
                return redirect(url_for('sales.edit_shipment', shipment_id=shipment.id))

        with db.atomic():
            SalesOrderItem.delete().where(SalesOrderItem.order == shipment).execute()
            shipment.total_amount = sum(item['subtotal'] for item in items)
            shipment.save()
            for item in items:
                SalesOrderItem.create(order=shipment, product=item['product_id'], quantity=item['quantity'],
                                      unit_price=item['unit_price'], subtotal=item['subtotal'],
                                      user=current_user)
        flash('出库单修改成功', 'success')
        log_action(current_user, 'update', 'SalesOrder', shipment.id,
                   f'修改出库单 #{shipment.id}', request.remote_addr)
        return redirect(url_for('sales.list_shipments'))

    items = list(SalesOrderItem.select().where(SalesOrderItem.order == shipment))
    customers = Customer.select().where(Customer.user == current_user)
    products = Product.select().where(Product.user == current_user)
    return render_template('shipment_edit.html', shipment=shipment, items=items,
                           customers=customers, products=products)


@sales_bp.route('/shipments/delete/<int:shipment_id>', methods=['POST'])
@login_required
def delete_shipment(shipment_id):
    shipment = SalesOrder.get_or_none((SalesOrder.id == shipment_id) & (SalesOrder.user == current_user))
    if shipment:
        if shipment.customer_order_id:
            order = CustomerOrder.get_or_none(CustomerOrder.id == shipment.customer_order_id)
            if not order:
                flash('关联的订单已不存在，出库单将被删除', 'warning')
            else:
                flash('提醒：该出库单关联订单，订单状态将重新计算', 'warning')

        # 清理关联的退款记录
        CustomerRefund.delete().where(CustomerRefund.sales_order == shipment).execute()
        SalesOrderItem.delete().where(SalesOrderItem.order == shipment).execute()
        shipment.delete_instance()

        # 回退父订单状态
        if shipment.customer_order_id:
            c_order = CustomerOrder.get_or_none(CustomerOrder.id == shipment.customer_order_id)
            if c_order:
                from peewee import fn as _fn
                shipped_amt = (SalesOrder
                              .select(_fn.SUM(SalesOrder.total_amount))
                              .where((SalesOrder.customer_order == c_order) &
                                     (SalesOrder.user == current_user))
                              .scalar()) or 0
                c_order.status = 'shipped' if shipped_amt >= c_order.total_amount else 'pending'
                c_order.save()

        flash('出库单已删除', 'success')
    return redirect(url_for('sales.list_shipments'))
