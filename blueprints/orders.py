# blueprints/orders.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import (
    Customer, CustomerOrder, CustomerOrderItem,
    SalesOrder, SalesOrderItem, Product,
    db
)
from peewee import fn
from helpers import check_stock_before_ship, parse_non_negative_float, parse_positive_float
from log_utils import log_action
import datetime

orders_bp = Blueprint('orders', __name__)


@orders_bp.route('/orders')
@login_required
def list_orders():
    # 对账时段筛选参数（默认今天）
    start_str = request.args.get('reconcile_start', '')
    end_str = request.args.get('reconcile_end', '')
    today = datetime.date.today()
    if start_str:
        try:
            reconcile_start = datetime.datetime.strptime(start_str, '%Y-%m-%d').date()
        except ValueError:
            reconcile_start = today
    else:
        reconcile_start = today
    if end_str:
        try:
            reconcile_end = datetime.datetime.strptime(end_str, '%Y-%m-%d').date()
        except ValueError:
            reconcile_end = today
    else:
        reconcile_end = today

    # 客户筛选
    customer_id = request.args.get('customer_id', '')
    orders = CustomerOrder.select().where(CustomerOrder.user == current_user)
    if customer_id:
        orders = orders.where(CustomerOrder.customer == int(customer_id))
    orders = orders.order_by(CustomerOrder.order_date.desc())

    # 客户列表（用于下拉筛选）
    customers = Customer.select().where(Customer.user == current_user)

    # ── 自动截单：发货金额 >= 订货金额但明细数量不匹配的，自动修正 ──
    auto_fixed = []
    for order in orders:
        items = list(order.items)
        if not items:
            continue
        total_shipped_amt = (SalesOrderItem
                            .select(fn.SUM(SalesOrderItem.subtotal))
                            .join(SalesOrder)
                            .where((SalesOrder.customer_order == order) &
                                   (SalesOrder.user == current_user) &
                                   (SalesOrder.is_settlement == False))
                            .scalar()) or 0
        if total_shipped_amt < order.total_amount - 0.01:
            continue  # 金额不够，跳过

        # 检查是否有明细不匹配
        has_mismatch = False
        for item in items:
            shipped_qty = (SalesOrderItem
                          .select(fn.SUM(SalesOrderItem.quantity))
                          .join(SalesOrder)
                          .where((SalesOrder.customer_order == order) &
                                 (SalesOrderItem.product == item.product) &
                                 (SalesOrder.user == current_user))
                          .scalar()) or 0
            if shipped_qty < item.quantity:
                has_mismatch = True
                break

        if not has_mismatch:
            continue

        # 执行自动修正：缩减订货量到实发量
        fixed_items = []
        for item in items:
            shipped_qty = (SalesOrderItem
                          .select(fn.SUM(SalesOrderItem.quantity))
                          .join(SalesOrder)
                          .where((SalesOrder.customer_order == order) &
                                 (SalesOrderItem.product == item.product) &
                                 (SalesOrder.user == current_user))
                          .scalar()) or 0
            if shipped_qty < item.quantity:
                fixed_items.append(f'{item.product.name}: {item.quantity}->{shipped_qty}')
                item.quantity = shipped_qty
                item.subtotal = shipped_qty * item.unit_price
                item.save()
        new_total = sum(it.quantity * it.unit_price for it in items)
        order.total_amount = new_total
        order.status = 'shipped'
        order.save()
        auto_fixed.append(f'#{order.id}({"; ".join(fixed_items)})')

    if auto_fixed:
        flash(f'已自动截单 {len(auto_fixed)} 笔：{" | ".join(auto_fixed)}', 'success')

    rows = []
    row_index = 0
    total_order_value = 0.0
    total_shipped_value = 0.0
    total_shipped_in_period_value = 0.0

    for order in orders:
        items = list(order.items)
        order_product_ids = {item.product.id for item in items}

        # 批量查询发货量（对账三段式），减少数据库查询
        for item in items:
            pid = item.product.id

            # 截止当前总发货量
            shipped_total = (SalesOrderItem
                        .select(fn.SUM(SalesOrderItem.quantity))
                        .join(SalesOrder)
                        .where((SalesOrder.customer_order == order) &
                               (SalesOrderItem.product == pid) &
                               (SalesOrder.user == current_user))
                        .scalar()) or 0

            # 时段开始前的发货量
            shipped_before = (SalesOrderItem
                        .select(fn.SUM(SalesOrderItem.quantity))
                        .join(SalesOrder)
                        .where((SalesOrder.customer_order == order) &
                               (SalesOrderItem.product == pid) &
                               (SalesOrder.user == current_user) &
                               (SalesOrder.order_date < reconcile_start))
                        .scalar()) or 0

            # 时段结束前的发货量（含结束日）
            shipped_up_to_end = (SalesOrderItem
                        .select(fn.SUM(SalesOrderItem.quantity))
                        .join(SalesOrder)
                        .where((SalesOrder.customer_order == order) &
                               (SalesOrderItem.product == pid) &
                               (SalesOrder.user == current_user) &
                               (SalesOrder.order_date <= reconcile_end))
                        .scalar()) or 0

            # 时段内发货量
            shipped_in_period = shipped_up_to_end - shipped_before

            # 期初剩余 = 订单量 - 时段开始前已发
            pending_before = item.quantity - shipped_before
            # 期末剩余 = 订单量 - 时段结束前总发货
            pending_after = item.quantity - shipped_up_to_end

            item.shipped_total = shipped_total
            item.shipped_before = shipped_before
            item.shipped_in_period = shipped_in_period
            item.pending_before = pending_before
            item.pending_after = pending_after

        # 订单级汇总（用于状态判断）
        shipped_amount = sum(getattr(it, 'shipped_total', 0) * it.unit_price for it in items)
        total_shipped_value += shipped_amount
        total_order_value += order.total_amount

        if shipped_amount > 0:
            extra_items = (SalesOrderItem
                           .select()
                           .join(SalesOrder)
                           .where((SalesOrder.customer_order == order) &
                                  (SalesOrder.user == current_user) &
                                  SalesOrderItem.product.not_in(order_product_ids))
                           .exists())
            has_swap = extra_items
        else:
            has_swap = False

        if shipped_amount >= order.total_amount:
            status_text = '已完成'
        elif shipped_amount > 0:
            status_text = '部分发货-换货' if has_swap else '部分发货'
        else:
            status_text = '未发货'

        has_shipment = shipped_amount > 0
        # 检查是否有明细的发货量 ≠ 订货量（替代品发货或历史遗留订单）
        # 条件：有发货记录 + 存在某行产品发货量 < 订货量
        needs_fix = (has_shipment and
                     any(getattr(it, 'shipped_total', 0) < it.quantity for it in items))
        total_lines = len(items) if items else 1

        for idx, item in enumerate(items):
            product = item.product
            qty_ordered = item.quantity
            unit_price = item.unit_price
            subtotal = item.subtotal

            shipped_total = getattr(item, 'shipped_total', 0)
            shipped_before = getattr(item, 'shipped_before', 0)
            shipped_in_period = getattr(item, 'shipped_in_period', 0)
            pending_before = getattr(item, 'pending_before', 0)
            pending_after = getattr(item, 'pending_after', 0)

            if shipped_total == 0:
                item_status = '未发货'
            elif shipped_total < qty_ordered:
                item_status = '部分发货'
            else:
                item_status = '已完成'

            row_index += 1
            rows.append({
                'row_no': row_index,
                'order_id': order.id,
                'customer_name': order.customer.name,
                'order_date': order.order_date,
                'product_name': product.name,
                'unit_price': unit_price,
                'qty_ordered': qty_ordered,
                'subtotal': subtotal,
                'shipped_before': shipped_before,
                'pending_before': pending_before,
                'shipped_in_period': shipped_in_period,
                'shipped_total': shipped_total,
                'pending_after': pending_after,
                'status_text': item_status,
                'invoice_required': '是' if order.invoice_required else '否',
                'is_first_row': idx == 0,
                'rowspan_count': total_lines,
                'has_shipment': has_shipment,
                'needs_fix': needs_fix
            })
            total_shipped_in_period_value += shipped_in_period * unit_price

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
                'shipped_before': 0,
                'pending_before': 0,
                'shipped_in_period': 0,
                'shipped_total': 0,
                'pending_after': 0,
                'status_text': '-',
                'invoice_required': '是' if order.invoice_required else '否',
                'is_first_row': True,
                'rowspan_count': 1,
                'has_shipment': has_shipment,
                'needs_fix': False
            })

    total_unshipped_value = total_order_value - total_shipped_value
    return render_template('orders.html',
                           orders=rows,
                           customers=customers,
                           reconcile_start=reconcile_start,
                           reconcile_end=reconcile_end,
                           selected_customer=customer_id,
                           total_order_value=total_order_value,
                           total_shipped_value=total_shipped_value,
                           total_shipped_in_period_value=total_shipped_in_period_value,
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

        customer = Customer.get_or_none((Customer.id == customer_id) & (Customer.user == current_user))
        if not customer:
            flash('客户不存在或无权访问', 'danger')
            return redirect(url_for('orders.add_order'))

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
                flash('订单明细包含无效的产品、数量或单价', 'danger')
                return render_template('order_form.html',
                                       customers=customers,
                                       products=products,
                                       customers_json=customers_data,
                                       products_json=products_data,
                                       order=None,
                                       order_items=[],
                                       today=datetime.date.today())
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

        with db.atomic():
            total_amount = sum(item['subtotal'] for item in items)
            order = CustomerOrder.create(
                customer=customer,
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
                                         subtotal=item['subtotal'],
                                         user=current_user)

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
        # 检查是否已有发货记录
        has_shipments = SalesOrder.select().where(
            (SalesOrder.customer_order == order) & (SalesOrder.user == current_user)
        ).exists()

        customer = Customer.get_or_none((Customer.id == request.form.get('customer_id')) &
                                        (Customer.user == current_user))
        if not customer:
            flash('客户不存在或无权访问', 'danger')
            return redirect(url_for('orders.list_orders'))
        order.customer = customer
        order.order_date = request.form.get('order_date')
        order.remark = request.form.get('remark', '') or None
        order.invoice_required = (request.form.get('invoice_required') == '1')

        if has_shipments:
            # 已有发货记录：只允许修改备注、发票标记等，不允许修改明细行
            order.save()
            log_action(current_user, 'update', 'CustomerOrder', order.id,
                       f'修改客户订单 #{order.id}（仅更新基本信息）', request.remote_addr)
            flash('订单已更新（已有发货记录，明细行不可修改）', 'warning')
            return redirect(url_for('orders.list_orders'))

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
                flash('订单明细包含无效的产品、数量或单价', 'danger')
                return redirect(url_for('orders.edit_order', order_id=order.id))
            items.append({'product_id': int(pid), 'quantity': qty,
                          'unit_price': price, 'subtotal': qty * price})
        if not items:
            flash('请至少填写一条明细', 'danger')
            return redirect(url_for('orders.edit_order', order_id=order.id))
        with db.atomic():
            CustomerOrderItem.delete().where(CustomerOrderItem.order == order).execute()
            order.total_amount = sum(item['subtotal'] for item in items)
            order.save()
            for item in items:
                CustomerOrderItem.create(order=order,
                                         product=item['product_id'],
                                         quantity=item['quantity'],
                                         unit_price=item['unit_price'],
                                         subtotal=item['subtotal'],
                                         user=current_user)

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
    shipped_amount_total = 0.0
    for item in order_items:
        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) &
                          (SalesOrderItem.product == item.product) &
                          (SalesOrder.user == current_user))
                   .scalar()) or 0
        shipped_qty_map[item.product.id] = shipped
        shipped_amount_total += shipped * item.unit_price

    items_data = []
    remaining_order_value = order.total_amount - shipped_amount_total
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
                           remaining_order_value=remaining_order_value,
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
            if max_qty <= 0:
                flash(f'{item.product.name} 已全部发货，无需再发', 'warning')
            else:
                flash(f'{item.product.name} 发货量超出订单剩余({max_qty})，已自动调整为最大值', 'warning')
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
        product = Product.get_or_none((Product.id == pid) & (Product.user == current_user))
        qty = parse_positive_float(qty)
        price = parse_non_negative_float(price)
        if not product or qty is None or price is None:
            flash('额外发货明细包含无效的产品、数量或单价', 'danger')
            return redirect(url_for('orders.ship_order_form', order_id=order.id))
        extra_items.append({
            'product_id': int(pid),
            'product': product,
            'quantity': qty,
            'unit_price': price,
            'subtotal': qty * price
        })

    if not ship_quantities and not extra_items:
        flash('请至少填写发货数量或添加产品', 'danger')
        return redirect(url_for('orders.ship_order_form', order_id=order.id))

    # 新增选项
    cancel_remaining = request.form.get('cancel_remaining') == 'on'
    extra_as_new = request.form.get('extra_as_new_order') == 'on'

    all_ship_items = []
    for pid, data in ship_quantities.items():
        all_ship_items.append({'product_id': pid, 'quantity': data['quantity']})
    all_ship_items.extend(extra_items)

    stock_ok, stock_errors = check_stock_before_ship(all_ship_items, user=current_user)
    if not stock_ok:
        for err in stock_errors:
            flash(f'库存不足：{err}', 'danger')
        return redirect(url_for('orders.ship_order_form', order_id=order.id))

    with db.atomic():
        ship_total = sum(v['subtotal'] for v in ship_quantities.values())
        extra_total = sum(e['subtotal'] for e in extra_items)

        # ── 1. 正常发货 ──
        ship = SalesOrder.create(
            customer=order.customer,
            customer_order=order,
            order_date=request.form.get('order_date', datetime.date.today()),
            total_amount=ship_total + (0 if extra_as_new else extra_total),
            remark=request.form.get('remark', '') or None,
            ship_method=request.form.get('ship_method', '') or None,
            tracking_number=request.form.get('tracking_number', '') or None,
            user=current_user
        )
        for product_id, data in ship_quantities.items():
            SalesOrderItem.create(
                order=ship, product=product_id,
                quantity=data['quantity'], unit_price=data['unit_price'],
                subtotal=data['subtotal'], user=current_user
            )
        if not extra_as_new:
            for extra in extra_items:
                SalesOrderItem.create(
                    order=ship, product=extra['product_id'],
                    quantity=extra['quantity'], unit_price=extra['unit_price'],
                    subtotal=extra['subtotal'], user=current_user
                )

        # ── 2. 取消剩余未发：减少订货量 ──
        if cancel_remaining:
            items_cancelled = []
            for item in order.items:
                shipped_now = ship_quantities.get(item.product.id, {}).get('quantity', 0)
                shipped_before = (SalesOrderItem
                                  .select(fn.SUM(SalesOrderItem.quantity))
                                  .join(SalesOrder)
                                  .where((SalesOrder.customer_order == order) &
                                         (SalesOrderItem.product == item.product) &
                                         (SalesOrder.user == current_user) &
                                         (SalesOrder.id != ship.id))
                                  .scalar()) or 0
                total_shipped = shipped_before + shipped_now
                if total_shipped < item.quantity:
                    cancelled = item.quantity - total_shipped
                    items_cancelled.append(f'{item.product.name} x{cancelled}')
                    item.quantity = total_shipped
                    item.subtotal = total_shipped * item.unit_price
                    item.save()
            if items_cancelled:
                # 重算订单总额
                new_total = sum(it.quantity * it.unit_price for it in order.items)
                order.total_amount = new_total
                log_action(current_user, 'cancel_remaining', 'CustomerOrder', order.id,
                           f'取消未发：{"; ".join(items_cancelled)}', request.remote_addr)

        # ── 3. 替代品生成独立订单 ──
        new_order = None
        if extra_as_new and extra_items:
            new_total = extra_total
            new_order = CustomerOrder.create(
                user=current_user,
                customer=order.customer,
                order_date=datetime.date.today(),
                total_amount=new_total,
                status='shipped',
                invoice_required=False,
                remark=f'替代发货（原订单 #{order.id}）'
            )
            for extra in extra_items:
                CustomerOrderItem.create(
                    order=new_order,
                    product=extra['product_id'],
                    quantity=extra['quantity'],
                    unit_price=extra['unit_price'],
                    subtotal=extra['subtotal'],
                    user=current_user
                )
            # 自动发货新订单
            new_ship = SalesOrder.create(
                customer=order.customer,
                customer_order=new_order,
                order_date=datetime.date.today(),
                total_amount=new_total,
                remark=f'替代发货（原订单 #{order.id} 换货）',
                user=current_user
            )
            for extra in extra_items:
                SalesOrderItem.create(
                    order=new_ship, product=extra['product_id'],
                    quantity=extra['quantity'], unit_price=extra['unit_price'],
                    subtotal=extra['subtotal'], user=current_user
                )

        # ── 4. 更新订单状态 ──
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
        order.status = 'shipped' if all_shipped else 'pending'
        order.save()

    log_action(current_user, 'ship', 'CustomerOrder', order.id,
               f'订单 #{order.id} 发货 ¥{ship_total:.2f}', request.remote_addr)

    msg = f'出库单生成成功，发货金额 ¥{ship_total:.2f}'
    if cancel_remaining and items_cancelled:
        msg += f'，已取消剩余未发'
    if new_order:
        msg += f'，已自动创建替代订单 #{new_order.id}（¥{extra_total:.2f}）并完成发货'
    flash(msg, 'success')
    return redirect(url_for('orders.list_orders'))


@orders_bp.route('/orders/fix/<int:order_id>', methods=['POST'])
@login_required
def fix_order_quantities(order_id):
    """修正历史订单：将每行产品的订货量缩减到实际发货量"""
    order = CustomerOrder.get_or_none((CustomerOrder.id == order_id) & (CustomerOrder.user == current_user))
    if not order:
        flash('订单不存在或无权访问', 'danger')
        return redirect(url_for('orders.list_orders'))

    fixed = []
    for item in order.items:
        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) &
                          (SalesOrderItem.product == item.product) &
                          (SalesOrder.user == current_user))
                   .scalar()) or 0
        if shipped < item.quantity:
            fixed.append(f'{item.product.name}：{item.quantity}→{shipped}')
            item.quantity = shipped
            item.subtotal = shipped * item.unit_price
            item.save()

    if fixed:
        new_total = sum(it.quantity * it.unit_price for it in order.items)
        order.total_amount = new_total
        order.save()
        flash(f'已修正：{"; ".join(fixed)}，订单总额 ¥{new_total:.2f}', 'success')
    else:
        flash('该订单无需修正，订货量已与实际发货一致', 'info')

    return redirect(url_for('orders.list_orders'))


@orders_bp.route('/orders/settle/<int:order_id>', methods=['POST'])
@login_required
def settle_order(order_id):
    """手动平账：对尚未发货的剩余数量生成零价出库单，关闭订单"""
    order = CustomerOrder.get_or_none((CustomerOrder.id == order_id) & (CustomerOrder.user == current_user))
    if not order:
        flash('订单不存在或无权访问', 'danger')
        return redirect(url_for('orders.list_orders'))

    # 统计每条明细的未发数量
    settle_items = []
    for item in order.items:
        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) &
                          (SalesOrderItem.product == item.product) &
                          (SalesOrder.user == current_user))
                   .scalar()) or 0
        remaining = item.quantity - shipped
        if remaining > 0:
            settle_items.append({
                'product_id': item.product.id,
                'product_name': item.product.name,
                'quantity': remaining,
            })

    if not settle_items:
        flash('该订单已全部发货，无需平账', 'info')
        return redirect(url_for('orders.list_orders'))

    with db.atomic():
        settle = SalesOrder.create(
            customer=order.customer,
            customer_order=order,
            order_date=datetime.date.today(),
            total_amount=0,
            remark='手动平账（替代品已通过其他方式发出）',
            is_settlement=True,
            user=current_user
        )
        for it in settle_items:
            SalesOrderItem.create(
                order=settle,
                product=it['product_id'],
                quantity=it['quantity'],
                unit_price=0,
                subtotal=0,
                user=current_user
            )

        order.status = 'shipped'
        order.save()

    names = '、'.join(f"{it['product_name']} x{it['quantity']}" for it in settle_items)
    log_action(current_user, 'settle', 'CustomerOrder', order.id,
               f'手动平账订单 #{order.id}：{names}', request.remote_addr)

    flash(f'订单 #{order.id} 已平账：{names}（金额 ¥0，数量已归零）', 'success')
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
