# blueprints/orders.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import (
    Customer, CustomerOrder, CustomerOrderItem,
    SalesOrder, SalesOrderItem, Product
)
from peewee import fn
from helpers import check_stock_before_ship
from log_utils import log_action
import datetime

orders_bp = Blueprint('orders', __name__)


@orders_bp.route('/orders')
@login_required
def list_orders():
    orders = CustomerOrder.select().where(CustomerOrder.user == current_user).order_by(CustomerOrder.order_date.desc())
    rows = []
    row_index = 0
    total_order_value = 0.0
    total_shipped_value = 0.0

    for order in orders:
        items = list(order.items)
        order_product_ids = {item.product.id for item in items}

        shipped_amount = (SalesOrder
                          .select(fn.SUM(SalesOrder.total_amount))
                          .where((SalesOrder.customer_order == order) & (SalesOrder.user == current_user))
                          .scalar()) or 0.0
        total_shipped_value += shipped_amount
        total_order_value += order.total_amount

        shipped_qty_total = 0.0
        shipped_qty_map = {}
        for item in items:
            shipped = (SalesOrderItem
                       .select(fn.SUM(SalesOrderItem.quantity))
                       .join(SalesOrder)
                       .where((SalesOrder.customer_order == order) &
                              (SalesOrderItem.product == item.product) &
                              (SalesOrder.user == current_user))
                       .scalar()) or 0
            shipped_qty_map[item.product.id] = shipped
            shipped_qty_total += shipped

        order_qty_total = sum(item.quantity for item in items)
        remaining_qty_total = max(order_qty_total - shipped_qty_total, 0)

        remaining_amount = max(order.total_amount - shipped_amount, 0)

        has_swap = False
        if shipped_amount > 0:
            extra_items = (SalesOrderItem
                           .select()
                           .join(SalesOrder)
                           .where((SalesOrder.customer_order == order) &
                                  (SalesOrder.user == current_user) &
                                  SalesOrderItem.product.not_in(order_product_ids))
                           .exists())
            has_swap = extra_items

        if shipped_amount >= order.total_amount:
            status_text = '已完成'
        elif shipped_amount > 0:
            status_text = '部分发货-换货' if has_swap else '部分发货'
        else:
            status_text = '未发货'

        has_shipment = shipped_amount > 0
        total_lines = len(items) if items else 1

        for idx, item in enumerate(items):
            product = item.product
            row_index += 1
            rows.append({
                'row_no': row_index,
                'order_id': order.id,
                'customer_name': order.customer.name,
                'order_date': order.order_date,
                'product_name': product.name,
                'unit_price': item.unit_price,
                'qty_ordered': item.quantity,
                'subtotal': item.subtotal,
                'shipped_qty_total': shipped_qty_total,
                'remaining_qty_total': remaining_qty_total,
                'shipped_amount': shipped_amount,
                'remaining_amount': remaining_amount,
                'status_text': status_text,
                'invoice_required': '是' if order.invoice_required else '否',
                'is_first_row': idx == 0,
                'rowspan_count': total_lines,
                'has_shipment': has_shipment,
                'status': order.status
            })

        if not items:
            row_index += 1
            rows.append({
                'row_no': row_index,
                'order_id': order.id,
                'customer_name': order.customer.name,
                'order_date': order.order_date,
                'product_name': '-',
                'unit_price': 0,
                'qty_ordered': 0,
                'subtotal': 0,
                'shipped_qty_total': shipped_qty_total,
                'remaining_qty_total': remaining_qty_total,
                'shipped_amount': shipped_amount,
                'remaining_amount': remaining_amount,
                'status_text': status_text,
                'invoice_required': '是' if order.invoice_required else '否',
                'is_first_row': True,
                'rowspan_count': 1,
                'has_shipment': has_shipment,
                'status': order.status
            })

    total_unshipped_value = total_order_value - total_shipped_value
    return render_template('orders.html',
                           orders=rows,
                           total_order_value=total_order_value,
                           total_shipped_value=total_shipped_value,
                           total_unshipped_value=total_unshipped_value)


@orders_bp.route('/orders/add', methods=['GET', 'POST'])
@login_required
def add_order():
    customers = Customer.select().where(Customer.user == current_user)
    customers_data = [{'id': c.id, 'name': c.name} for c in customers]
    products = Product.select().where(Product.user == current_user)
    products_data = [{'id': p.id, 'name': p.name} for p in products]

    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        order_date = request.form.get('order_date')
        remark = request.form.get('remark', '')
        invoice_required = request.form.get('invoice_required') == '1'

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
            return render_template('order_form.html',
                                   customers=customers,
                                   products=products,
                                   customers_json=customers_data,
                                   products_json=products_data,
                                   order=None,
                                   order_items=[],
                                   today=datetime.date.today())

        total_amount = sum(item['subtotal'] for item in items)
        order = CustomerOrder.create(
            customer=customer_id,
            order_date=order_date or datetime.date.today(),
            total_amount=total_amount,
            invoice_required=invoice_required,
            remark=remark or None,
            user=current_user
        )
        for item in items:
            CustomerOrderItem.create(order=order,
                                     product=item['product_id'],
                                     quantity=item['quantity'],
                                     unit_price=item['unit_price'],
                                     subtotal=item['subtotal'])

        # 记录操作日志
        log_action(current_user, 'create', 'CustomerOrder', order.id,
                   f'创建客户订单，金额：{total_amount:.2f}', request.remote_addr)

        flash(f'订单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('orders.list_orders'))

    return render_template('order_form.html',
                           customers=customers,
                           products=products,
                           customers_json=customers_data,
                           products_json=products_data,
                           order=None,
                           order_items=[],
                           today=datetime.date.today())


@orders_bp.route('/orders/edit/<int:order_id>', methods=['GET', 'POST'])
@login_required
def edit_order(order_id):
    order = CustomerOrder.get_or_none((CustomerOrder.id == order_id) & (CustomerOrder.user == current_user))
    if not order:
        flash('订单不存在或无权访问', 'danger')
        return redirect(url_for('orders.list_orders'))

    if request.method == 'POST':
        order.customer = request.form.get('customer_id')
        order.order_date = request.form.get('order_date')
        order.remark = request.form.get('remark', '') or None
        order.invoice_required = (request.form.get('invoice_required') == '1')

        CustomerOrderItem.delete().where(CustomerOrderItem.order == order).execute()
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
        order.total_amount = sum(item['subtotal'] for item in items)
        order.save()
        for item in items:
            CustomerOrderItem.create(order=order,
                                     product=item['product_id'],
                                     quantity=item['quantity'],
                                     unit_price=item['unit_price'],
                                     subtotal=item['subtotal'])

        # 记录操作日志
        log_action(current_user, 'update', 'CustomerOrder', order.id,
                   f'修改客户订单 #{order.id}', request.remote_addr)

        flash('订单修改成功', 'success')
        return redirect(url_for('orders.list_orders'))

    customers = Customer.select().where(Customer.user == current_user)
    customers_data = [{'id': c.id, 'name': c.name} for c in customers]
    products = Product.select().where(Product.user == current_user)
    products_data = [{'id': p.id, 'name': p.name} for p in products]
    order_items = list(CustomerOrderItem.select().where(CustomerOrderItem.order == order))
    return render_template('order_form.html',
                           customers=customers,
                           products=products,
                           customers_json=customers_data,
                           products_json=products_data,
                           order=order,
                           order_items=order_items,
                           today=datetime.date.today())


@orders_bp.route('/orders/delete/<int:order_id>', methods=['POST'])
@login_required
def delete_order(order_id):
    order = CustomerOrder.get_or_none((CustomerOrder.id == order_id) & (CustomerOrder.user == current_user))
    if not order:
        flash('订单不存在或无权访问', 'danger')
        return redirect(url_for('orders.list_orders'))

    if SalesOrder.select().where((SalesOrder.customer_order == order) & (SalesOrder.user == current_user)).exists():
        flash('该订单已有发货记录，无法删除。如需关闭，请使用“强制完成”功能。', 'danger')
        return redirect(url_for('orders.list_orders'))

    CustomerOrderItem.delete().where(CustomerOrderItem.order == order).execute()
    order.delete_instance()

    # 记录操作日志
    log_action(current_user, 'delete', 'CustomerOrder', order_id,
               f'删除客户订单 #{order_id}', request.remote_addr)

    flash('订单已删除', 'success')
    return redirect(url_for('orders.list_orders'))


@orders_bp.route('/orders/ship/<int:order_id>', methods=['GET'])
@login_required
def ship_order_form(order_id):
    order = CustomerOrder.get_or_none((CustomerOrder.id == order_id) & (CustomerOrder.user == current_user))
    if not order:
        flash('订单不存在或无权访问', 'danger')
        return redirect(url_for('orders.list_orders'))

    order_items = list(CustomerOrderItem.select().where(CustomerOrderItem.order == order))
    shipped_qty_map = {}
    for item in order_items:
        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) &
                          (SalesOrderItem.product == item.product) &
                          (SalesOrder.user == current_user))
                   .scalar()) or 0
        shipped_qty_map[item.product.id] = shipped

    items_data = []
    for item in order_items:
        pid = item.product.id
        qty_ordered = item.quantity
        shipped = shipped_qty_map.get(pid, 0)
        pending = qty_ordered - shipped
        items_data.append({
            'product': item.product,
            'qty_ordered': qty_ordered,
            'shipped': shipped,
            'pending': pending,
            'unit_price': item.unit_price
        })

    products = Product.select().where(Product.user == current_user)
    products_data = [{'id': p.id, 'name': p.name} for p in products]

    return render_template('ship_order.html',
                           order=order,
                           items_data=items_data,
                           today=datetime.date.today(),
                           products=products,
                           products_json=products_data)


@orders_bp.route('/orders/ship/<int:order_id>', methods=['POST'])
@login_required
def create_shipment(order_id):
    order = CustomerOrder.get_or_none((CustomerOrder.id == order_id) & (CustomerOrder.user == current_user))
    if not order:
        flash('订单不存在或无权访问', 'danger')
        return redirect(url_for('orders.list_orders'))

    ship_quantities = {}
    for item in order.items:
        field_name = f'ship_qty_{item.product.id}'
        qty_str = request.form.get(field_name, '0')
        try:
            qty = float(qty_str)
        except ValueError:
            qty = 0
        if qty < 0:
            qty = 0

        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) &
                          (SalesOrderItem.product == item.product) &
                          (SalesOrder.user == current_user))
                   .scalar()) or 0
        max_qty = item.quantity - shipped
        if qty > max_qty:
            qty = max_qty
        if qty > 0:
            ship_quantities[item.product.id] = {
                'quantity': qty,
                'unit_price': item.unit_price,
                'subtotal': qty * item.unit_price
            }

    extra_product_ids = request.form.getlist('extra_product_id[]')
    extra_quantities = request.form.getlist('extra_quantity[]')
    extra_unit_prices = request.form.getlist('extra_unit_price[]')
    extra_items = []
    for pid, qty, price in zip(extra_product_ids, extra_quantities, extra_unit_prices):
        if not pid or not qty or not price:
            continue
        qty = float(qty)
        price = float(price)
        extra_items.append({
            'product_id': int(pid),
            'quantity': qty,
            'unit_price': price,
            'subtotal': qty * price
        })

    if not ship_quantities and not extra_items:
        flash('请至少填写发货数量或添加产品', 'danger')
        return redirect(url_for('orders.ship_order_form', order_id=order.id))

    all_ship_items = []
    for pid, data in ship_quantities.items():
        all_ship_items.append({'product_id': pid, 'quantity': data['quantity']})
    all_ship_items.extend(extra_items)

    stock_ok, stock_errors = check_stock_before_ship(all_ship_items)
    if not stock_ok:
        for err in stock_errors:
            flash(f'库存不足：{err}', 'danger')
        return redirect(url_for('orders.ship_order_form', order_id=order.id))

    total_amount = sum(v['subtotal'] for v in ship_quantities.values()) + sum(e['subtotal'] for e in extra_items)
    ship = SalesOrder.create(
        customer=order.customer,
        customer_order=order,
        order_date=request.form.get('order_date', datetime.date.today()),
        total_amount=total_amount,
        remark=request.form.get('remark', '') or None,
        ship_method=request.form.get('ship_method', '') or None,
        tracking_number=request.form.get('tracking_number', '') or None,
        user=current_user
    )
    for product_id, data in ship_quantities.items():
        SalesOrderItem.create(
            order=ship,
            product=product_id,
            quantity=data['quantity'],
            unit_price=data['unit_price'],
            subtotal=data['subtotal']
        )
    for extra in extra_items:
        SalesOrderItem.create(
            order=ship,
            product=extra['product_id'],
            quantity=extra['quantity'],
            unit_price=extra['unit_price'],
            subtotal=extra['subtotal']
        )

    all_shipped = True
    for item in order.items:
        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) &
                          (SalesOrderItem.product == item.product) &
                          (SalesOrder.user == current_user))
                   .scalar()) or 0
        if shipped < item.quantity:
            all_shipped = False
            break
    if all_shipped or total_amount >= order.total_amount:
        order.status = 'shipped'
    else:
        order.status = 'pending'
    order.save()

    # 记录操作日志
    log_action(current_user, 'ship', 'CustomerOrder', order.id,
               f'订单 #{order.id} 发货，金额：{total_amount:.2f}', request.remote_addr)

    flash(f'出库单生成成功，本次发货金额 ¥{total_amount:.2f}', 'success')
    return redirect(url_for('orders.list_orders'))


@orders_bp.route('/orders/force_complete/<int:order_id>', methods=['POST'])
@login_required
def force_complete_order(order_id):
    order = CustomerOrder.get_or_none((CustomerOrder.id == order_id) & (CustomerOrder.user == current_user))
    if order:
        order.status = 'shipped'
        order.save()

        # 记录操作日志
        log_action(current_user, 'complete', 'CustomerOrder', order.id,
                   f'订单 #{order.id} 强制完成', request.remote_addr)

        flash('订单已标记为完成', 'success')
    return redirect(url_for('orders.list_orders'))