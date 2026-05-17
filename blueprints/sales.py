from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import *
from peewee import fn
from helpers import check_stock_before_ship
import datetime

sales_bp = Blueprint('sales', __name__)

@sales_bp.route('/sales/add', methods=['GET', 'POST'])
def add_sales():
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        order_date = request.form.get('order_date')
        remark = request.form.get('remark', '')
        ship_method = request.form.get('ship_method', '')
        tracking_number = request.form.get('tracking_number', '')

        product_ids = request.form.getlist('product_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')

        items = []
        for pid, qty, price in zip(product_ids, quantities, unit_prices):
            if not pid or not qty or not price:
                continue
            qty = float(qty)
            price = float(price)
            items.append({'product_id': int(pid), 'quantity': qty,
                          'unit_price': price, 'subtotal': qty * price})

        if not items:
            flash('请至少填写一条明细', 'danger')
            return render_template('sales.html',
                                   customers=Customer.select(),
                                   products=Product.select())

        stock_ok, stock_errors = check_stock_before_ship(items)
        if not stock_ok:
            for err in stock_errors:
                flash(f'库存不足：{err}', 'danger')
            return render_template('sales.html',
                                   customers=Customer.select(),
                                   products=Product.select())

        total_amount = sum(item['subtotal'] for item in items)
        order = SalesOrder.create(
            customer=customer_id,
            order_date=order_date or datetime.date.today(),
            total_amount=total_amount,
            remark=remark or None,
            ship_method=ship_method or None,
            tracking_number=tracking_number or None
        )
        for item in items:
            SalesOrderItem.create(
                order=order,
                product=item['product_id'],
                quantity=item['quantity'],
                unit_price=item['unit_price'],
                subtotal=item['subtotal']
            )
        flash(f'出库单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('sales.add_sales'))

    products_data = [{'id': p.id, 'name': p.name} for p in Product.select()]
    customers_data = [{'id': c.id, 'name': c.name} for c in Customer.select()]
    return render_template('sales.html',
                           customers=customers_data,
                           products=products_data,
                           customers_json=customers_data,
                           products_json=products_data)


@sales_bp.route('/shipments')
def list_shipments():
    customer_id = request.args.get('customer_id')
    query = SalesOrder.select().order_by(SalesOrder.order_date.desc())
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
def edit_shipment(shipment_id):
    shipment = SalesOrder.get_or_none(SalesOrder.id == shipment_id)
    if not shipment:
        flash('出库单不存在', 'danger')
        return redirect(url_for('sales.list_shipments'))

    if request.method == 'POST':
        shipment.customer = request.form.get('customer_id')
        shipment.order_date = request.form.get('order_date')
        shipment.remark = request.form.get('remark', '') or None
        shipment.ship_method = request.form.get('ship_method', '') or None
        shipment.tracking_number = request.form.get('tracking_number', '') or None

        SalesOrderItem.delete().where(SalesOrderItem.order == shipment).execute()
        product_ids = request.form.getlist('product_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')
        items = []
        for pid, qty, price in zip(product_ids, quantities, unit_prices):
            if not pid or not qty or not price:
                continue
            qty = float(qty)
            price = float(price)
            items.append({'product_id': int(pid), 'quantity': qty, 'unit_price': price, 'subtotal': qty * price})
        shipment.total_amount = sum(item['subtotal'] for item in items)
        shipment.save()
        for item in items:
            SalesOrderItem.create(order=shipment, product=item['product_id'], quantity=item['quantity'],
                                 unit_price=item['unit_price'], subtotal=item['subtotal'])
        flash('出库单修改成功', 'success')
        return redirect(url_for('sales.list_shipments'))

    items = list(SalesOrderItem.select().where(SalesOrderItem.order == shipment))
    customers = Customer.select()
    products = Product.select()
    return render_template('shipment_edit.html', shipment=shipment, items=items, customers=customers, products=products)


@sales_bp.route('/shipments/delete/<int:shipment_id>', methods=['POST'])
def delete_shipment(shipment_id):
    shipment = SalesOrder.get_or_none(SalesOrder.id == shipment_id)
    if shipment:
        if shipment.customer_order_id:
            if not CustomerOrder.select().where(CustomerOrder.id == shipment.customer_order_id).exists():
                flash('关联的订单已不存在，出库单将被删除', 'warning')
            else:
                flash('提醒：该出库单关联订单，删除后订单状态需手动调整', 'warning')
        SalesOrderItem.delete().where(SalesOrderItem.order == shipment).execute()
        shipment.delete_instance()
        flash('出库单已删除', 'success')
    return redirect(url_for('sales.list_shipments'))