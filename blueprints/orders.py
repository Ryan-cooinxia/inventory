# blueprints/orders.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import (
    Customer, CustomerOrder, CustomerOrderItem,
    SalesOrder, SalesOrderItem, Product
)
from peewee import fn
from helpers import check_stock_before_ship
import datetime

orders_bp = Blueprint('orders', __name__)


@orders_bp.route('/orders')
def list_orders():
    orders = CustomerOrder.select().order_by(CustomerOrder.order_date.desc())
    rows = []
    row_index = 0
    total_order_value = 0.0
    total_shipped_value = 0.0

    for order in orders:
        items = list(order.items)
        order_product_ids = {item.product.id for item in items}

        # 已发货总金额
        shipped_amount = (SalesOrder
                          .select(fn.SUM(SalesOrder.total_amount))
                          .where(SalesOrder.customer_order == order)
                          .scalar()) or 0.0
        total_shipped_value += shipped_amount
        total_order_value += order.total_amount

        # 已发货总数量（按产品汇总）
        shipped_qty_total = 0.0
        shipped_qty_map = {}
        for item in items:
            shipped = (SalesOrderItem
                       .select(fn.SUM(SalesOrderItem.quantity))
                       .join(SalesOrder)
                       .where((SalesOrder.customer_order == order) &
                              (SalesOrderItem.product == item.product))
                       .scalar()) or 0
            shipped_qty_map[item.product.id] = shipped
            shipped_qty_total += shipped

        # 订单总数量
        order_qty_total = sum(item.quantity for item in items)
        remaining_qty_total = order_qty_total - shipped_qty_total
        if remaining_qty_total < 0:
            remaining_qty_total = 0.0

        # 剩余金额
        remaining_amount = order.total_amount - shipped_amount
        if remaining_amount < 0:
            remaining_amount = 0.0

        # 判断换货
        has_swap = False
        if shipped_amount > 0:
            extra_items = (SalesOrderItem
                           .select()
                           .join(SalesOrder)
                           .where(SalesOrder.customer_order == order)
                           .where(SalesOrderItem.product.not_in(order_product_ids))
                           .exists())
            has_swap = extra_items

        # 订单状态文本
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
def add_order():
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
            customers = Customer.select()
            products = Product.select()
            customers_data = [{'id': c.id, 'name': c.name} for c in customers]
            products_data = [{'id': p.id, 'name': p.name} for p in products]
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
            remark=remark or None
        )
        for item in items:
            CustomerOrderItem.create(order=order,
                                     product=item['product_id'],
                                     quantity=item['quantity'],
                                     unit_price=item['unit_price'],
                                     subtotal=item['subtotal'])
        flash(f'订单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('orders.list_orders'))

    # GET
    customers = Customer.select()
    customers_data = [{'id': c.id, 'name': c.name} for c in customers]
    products = Product.select()
    products_data = [{'id': p.id, 'name': p.name} for p in products]
    return render_template('order_form.html',
                           customers=customers,
                           products=products,
                           customers_json=customers_data,
                           products_json=products_data,
                           order=None,
                           order_items=[],
                           today=datetime.date.today())


@orders_bp.route('/orders/edit/<int:order_id>', methods=['GET', 'POST'])
def edit_order(order_id):
    order = CustomerOrder.get_or_none(CustomerOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('orders.list_orders'))

    if request.method == 'POST':
        order.customer = request.form.get('customer_id')
        order.order_date = request.form.get('order_date')
        order.remark = request.form.get('remark', '') or None
        order.invoice_required = (request.form.get('invoice_required') == '1')

        # 删除旧明细，重建
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
        flash('订单修改成功', 'success')
        return redirect(url_for('orders.list_orders'))

    # GET
    customers = Customer.select()
    customers_data = [{'id': c.id, 'name': c.name} for c in customers]
    products = Product.select()
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
def delete_order(order_id):
    order = CustomerOrder.get_or_none(CustomerOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('orders.list_orders'))

    if SalesOrder.select().where(SalesOrder.customer_order == order).exists():
        flash('该订单已有发货记录，无法删除。如需关闭，请使用“强制完成”功能。', 'danger')
        return redirect(url_for('orders.list_orders'))

    CustomerOrderItem.delete().where(CustomerOrderItem.order == order).execute()
    order.delete_instance()
    flash('订单已删除', 'success')
    return redirect(url_for('orders.list_orders'))


@orders_bp.route('/orders/ship/<int:order_id>', methods=['GET'])
def ship_order_form(order_id):
    order = CustomerOrder.get_or_none(CustomerOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('orders.list_orders'))

    order_items = list(CustomerOrderItem.select().where(CustomerOrderItem.order == order))

    shipped_qty_map = {}
    for item in order_items:
        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) &
                          (SalesOrderItem.product == item.product))
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

    products = Product.select()
    products_data = [{'id': p.id, 'name': p.name} for p in products]

    return render_template('ship_order.html',
                           order=order,
                           items_data=items_data,
                           today=datetime.date.today(),
                           products=products,
                           products_json=products_data)


@orders_bp.route('/orders/ship/<int:order_id>', methods=['POST'])
def create_shipment(order_id):
    order = CustomerOrder.get_or_none(CustomerOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('orders.list_orders'))

    # 收集本次发货数量（原始订单明细）
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
                          (SalesOrderItem.product == item.product))
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

    # 处理额外添加的产品行（换货部分）
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

    # 库存检查
    all_ship_items = []
    for pid, data in ship_quantities.items():
        all_ship_items.append({'product_id': pid, 'quantity': data['quantity']})
    all_ship_items.extend(extra_items)

    stock_ok, stock_errors = check_stock_before_ship(all_ship_items)
    if not stock_ok:
        for err in stock_errors:
            flash(f'库存不足：{err}', 'danger')
        return redirect(url_for('orders.ship_order_form', order_id=order.id))

    # 创建出库单
    total_amount = sum(v['subtotal'] for v in ship_quantities.values()) + \
                   sum(e['subtotal'] for e in extra_items)
    ship = SalesOrder.create(
        customer=order.customer,
        customer_order=order,
        order_date=request.form.get('order_date', datetime.date.today()),
        total_amount=total_amount,
        remark=request.form.get('remark', '') or None,
        ship_method=request.form.get('ship_method', '') or None,
        tracking_number=request.form.get('tracking_number', '') or None
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

    # 更新订单状态
    all_shipped = True
    for item in order.items:
        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) &
                          (SalesOrderItem.product == item.product))
                   .scalar()) or 0
        if shipped < item.quantity:
            all_shipped = False
            break
    if all_shipped or total_amount >= order.total_amount:
        order.status = 'shipped'
    else:
        order.status = 'pending'
    order.save()

    flash(f'出库单生成成功，本次发货金额 ¥{total_amount:.2f}', 'success')
    return redirect(url_for('orders.list_orders'))


@orders_bp.route('/orders/force_complete/<int:order_id>', methods=['POST'])
def force_complete_order(order_id):
    order = CustomerOrder.get_or_none(CustomerOrder.id == order_id)
    if order:
        order.status = 'shipped'
        order.save()
        flash('订单已标记为完成', 'success')
    return redirect(url_for('orders.list_orders'))