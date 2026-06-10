# blueprints/supplier_orders.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import (
    Supplier, SupplierOrder, SupplierOrderItem,
    PurchaseOrder, PurchaseOrderItem, Product
)
from peewee import fn
from helpers import parse_non_negative_float, parse_positive_float
from models import db
from log_utils import log_action
import datetime

supplier_orders_bp = Blueprint('supplier_orders', __name__)


@supplier_orders_bp.route('/supplier_orders')
@login_required
def list_supplier_orders():
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

    supplier_id = request.args.get('supplier_id', '')
    orders = SupplierOrder.select().where(SupplierOrder.user == current_user)
    if supplier_id:
        orders = orders.where(SupplierOrder.supplier == int(supplier_id))
    orders = orders.order_by(SupplierOrder.order_date.desc())

    # 供应商列表（用于下拉筛选）
    suppliers = Supplier.select().where(Supplier.user == current_user)

    rows = []
    row_index = 0
    total_order_value = 0.0
    total_received_value = 0.0
    total_received_in_period_value = 0.0

    for order in orders:
        items = list(order.items)

        # 批量查询收货量，减少数据库查询
        for item in items:
            pid = item.product.id

            # 截止筛选日期的总收货量（含当天）
            received_total = (PurchaseOrderItem
                        .select(fn.SUM(PurchaseOrderItem.quantity))
                        .join(PurchaseOrder)
                        .where((PurchaseOrder.supplier_order == order) &
                               (PurchaseOrderItem.product == pid) &
                               (PurchaseOrder.user == current_user))
                        .scalar()) or 0

            # 时段开始前的收货量
            received_before = (PurchaseOrderItem
                        .select(fn.SUM(PurchaseOrderItem.quantity))
                        .join(PurchaseOrder)
                        .where((PurchaseOrder.supplier_order == order) &
                               (PurchaseOrderItem.product == pid) &
                               (PurchaseOrder.user == current_user) &
                               (PurchaseOrder.order_date < reconcile_start))
                        .scalar()) or 0

            # 时段结束前的收货量（含结束日）
            received_up_to_end = (PurchaseOrderItem
                        .select(fn.SUM(PurchaseOrderItem.quantity))
                        .join(PurchaseOrder)
                        .where((PurchaseOrder.supplier_order == order) &
                               (PurchaseOrderItem.product == pid) &
                               (PurchaseOrder.user == current_user) &
                               (PurchaseOrder.order_date <= reconcile_end))
                        .scalar()) or 0

            # 时段内入库量
            received_in_period = received_up_to_end - received_before

            # 期初剩余 = 订单量 - 时段开始前已收
            pending_before = item.quantity - received_before
            # 期末剩余 = 订单量 - 时段结束前总收货
            pending_after = item.quantity - received_up_to_end

            item.received_total = received_total
            item.received_before = received_before
            item.received_in_period = received_in_period
            item.pending_before = pending_before
            item.pending_after = pending_after

        has_receipt = PurchaseOrder.select().where(
            (PurchaseOrder.supplier_order == order) & (PurchaseOrder.user == current_user)
        ).exists()

        total_lines = len(items) if items else 1

        for idx, item in enumerate(items):
            product = item.product
            qty_ordered = item.quantity
            unit_price = item.unit_price
            subtotal = item.subtotal

            if item.received_total == 0:
                status_text = '未交货'
            elif item.received_total < qty_ordered:
                status_text = '部分交货'
            else:
                status_text = '已完成'

            row_index += 1
            rows.append({
                'row_no': row_index,
                'order_id': order.id,
                'order_number': order.order_number or f'MD-{order.order_date.strftime("%Y%m%d")}-{order.id:04d}',
                'supplier_name': order.supplier.name,
                'order_date': order.order_date,
                'product_name': product.name,
                'unit_price': unit_price,
                'qty_ordered': qty_ordered,
                'subtotal': subtotal,
                'received_before': item.received_before,           # 时段前已收
                'pending_before': item.pending_before,          # 期初剩余
                'received_in_period': item.received_in_period,  # 时段内入库
                'received_total': item.received_total,          # 累计已收
                'pending_after': item.pending_after,            # 期末剩余
                'status_text': status_text,
                'status': order.status,
                'estimated_delivery': order.estimated_delivery,
                'has_receipt': has_receipt,
                'is_first_row': idx == 0,
                'rowspan_count': total_lines
            })
            total_order_value += subtotal
            total_received_value += item.received_total * unit_price
            total_received_in_period_value += item.received_in_period * unit_price

        if not items:
            row_index += 1
            rows.append({
                'row_no': row_index,
                'order_id': order.id,
                'order_number': order.order_number or f'MD-{order.order_date.strftime("%Y%m%d")}-{order.id:04d}',
                'supplier_name': order.supplier.name,
                'order_date': order.order_date,
                'product_name': '-',
                'unit_price': 0,
                'qty_ordered': 0,
                'subtotal': 0,
                'received_before': 0,
                'pending_before': 0,
                'received_in_period': 0,
                'received_total': 0,
                'pending_after': 0,
                'status_text': '-',
                'status': order.status,
                'estimated_delivery': order.estimated_delivery,
                'has_receipt': has_receipt,
                'is_first_row': True,
                'rowspan_count': 1
            })

    return render_template('supplier_orders.html',
                           orders=rows,
                           suppliers=suppliers,
                           reconcile_start=reconcile_start,
                           reconcile_end=reconcile_end,
                           selected_supplier=supplier_id,
                           total_order_value=total_order_value,
                           total_received_value=total_received_value,
                           total_received_in_period_value=total_received_in_period_value)


@supplier_orders_bp.route('/supplier_orders/add', methods=['GET', 'POST'])
@login_required
def add_supplier_order():
    suppliers = Supplier.select().where(Supplier.user == current_user)
    suppliers_data = [{'id': s.id, 'name': s.name} for s in suppliers]
    products = Product.select().where(Product.user == current_user)
    products_data = [{'id': p.id, 'name': p.name} for p in products]

    if request.method == 'POST':
        supplier_id = request.form.get('supplier_id')
        order_date = request.form.get('order_date')
        estimated_delivery = request.form.get('estimated_delivery') or None
        remark = request.form.get('remark', '')

        supplier = Supplier.get_or_none((Supplier.id == supplier_id) & (Supplier.user == current_user))
        if not supplier:
            flash('供应商不存在或无权访问', 'danger')
            return redirect(url_for('supplier_orders.add_supplier_order'))

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
                flash('供应商订单明细包含无效的产品、数量或单价', 'danger')
                return render_template('supplier_order_form.html',
                                       suppliers=suppliers,
                                       products=products,
                                       suppliers_json=suppliers_data,
                                       products_json=products_data,
                                       order=None,
                                       order_items=[],
                                       today=datetime.date.today())
            items.append({'product_id': int(pid), 'quantity': qty, 'unit_price': price, 'subtotal': qty * price})

        if not items:
            flash('请至少填写一条明细', 'danger')
            return render_template('supplier_order_form.html',
                                   suppliers=suppliers,
                                   products=products,
                                   suppliers_json=suppliers_data,
                                   products_json=products_data,
                                   order=None,
                                   order_items=[],
                                   today=datetime.date.today())

        total_amount = sum(item['subtotal'] for item in items)

        with db.atomic():
            # 订单号在事务内生成，避免并发冲突
            today_str = datetime.date.today().strftime('%Y%m%d')
            last_order = (SupplierOrder
                          .select()
                          .where((SupplierOrder.order_date == datetime.date.today()) &
                                 (SupplierOrder.user == current_user))
                          .order_by(SupplierOrder.id.desc())
                          .first())
            if last_order and last_order.order_number:
                try:
                    last_num = int(last_order.order_number.split('-')[-1])
                    new_num = last_num + 1
                except (IndexError, ValueError):
                    new_num = 1
            else:
                new_num = 1
            order_number = f"MD-{today_str}-{new_num:04d}"

            order = SupplierOrder.create(
                supplier=supplier,
                order_number=order_number,
                order_date=order_date or datetime.date.today(),
                total_amount=total_amount,
                estimated_delivery=estimated_delivery,
                remark=remark or None,
                user=current_user
            )
            for item in items:
                SupplierOrderItem.create(order=order,
                                         product=item['product_id'],
                                         quantity=item['quantity'],
                                         unit_price=item['unit_price'],
                                         subtotal=item['subtotal'],
                                         user=current_user)
        flash(f'供应商订单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    return render_template('supplier_order_form.html',
                           suppliers=suppliers,
                           products=products,
                           suppliers_json=suppliers_data,
                           products_json=products_data,
                           order=None,
                           order_items=[],
                           today=datetime.date.today())


@supplier_orders_bp.route('/supplier_orders/edit/<int:order_id>', methods=['GET', 'POST'])
@login_required
def edit_supplier_order(order_id):
    order = SupplierOrder.get_or_none((SupplierOrder.id == order_id) & (SupplierOrder.user == current_user))
    if not order:
        flash('订单不存在或无权访问', 'danger')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    if request.method == 'POST':
        # 检查是否已有收货记录
        has_receipts = PurchaseOrder.select().where(
            (PurchaseOrder.supplier_order == order) & (PurchaseOrder.user == current_user)
        ).exists()

        supplier = Supplier.get_or_none((Supplier.id == request.form.get('supplier_id')) &
                                        (Supplier.user == current_user))
        if not supplier:
            flash('供应商不存在或无权访问', 'danger')
            return redirect(url_for('supplier_orders.list_supplier_orders'))
        order.supplier = supplier
        order.order_date = request.form.get('order_date')
        order.estimated_delivery = request.form.get('estimated_delivery') or None
        order.remark = request.form.get('remark', '') or None

        if has_receipts:
            order.save()
            flash('订单已更新（已有收货记录，明细行不可修改）', 'warning')
            return redirect(url_for('supplier_orders.list_supplier_orders'))

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
                flash('供应商订单明细包含无效的产品、数量或单价', 'danger')
                return redirect(url_for('supplier_orders.edit_supplier_order', order_id=order.id))
            items.append({'product_id': int(pid), 'quantity': qty, 'unit_price': price, 'subtotal': qty * price})
        if not items:
            flash('请至少填写一条明细', 'danger')
            return redirect(url_for('supplier_orders.edit_supplier_order', order_id=order.id))
        with db.atomic():
            SupplierOrderItem.delete().where(SupplierOrderItem.order == order).execute()
            order.total_amount = sum(item['subtotal'] for item in items)
            order.save()
            for item in items:
                SupplierOrderItem.create(order=order,
                                         product=item['product_id'],
                                         quantity=item['quantity'],
                                         unit_price=item['unit_price'],
                                         subtotal=item['subtotal'],
                                         user=current_user)
        flash('订单修改成功', 'success')
        log_action(current_user, 'update', 'SupplierOrder', order.id,
                   f'修改供应商订单 #{order.id}', request.remote_addr)
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    suppliers = Supplier.select().where(Supplier.user == current_user)
    suppliers_data = [{'id': s.id, 'name': s.name} for s in suppliers]
    products = Product.select().where(Product.user == current_user)
    products_data = [{'id': p.id, 'name': p.name} for p in products]
    order_items = list(SupplierOrderItem.select().where(SupplierOrderItem.order == order))

    display_number = order.order_number or f"MD-{order.order_date.strftime('%Y%m%d')}-{order.id:04d}"

    return render_template('supplier_order_form.html',
                           suppliers=suppliers,
                           products=products,
                           suppliers_json=suppliers_data,
                           products_json=products_data,
                           order=order,
                           order_items=order_items,
                           display_number=display_number,
                           current_supplier=order.supplier,
                           today=datetime.date.today())


@supplier_orders_bp.route('/supplier_orders/delete/<int:order_id>', methods=['POST'])
@login_required
def delete_supplier_order(order_id):
    order = SupplierOrder.get_or_none((SupplierOrder.id == order_id) & (SupplierOrder.user == current_user))
    if not order:
        flash('订单不存在或无权访问', 'danger')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    if PurchaseOrder.select().where((PurchaseOrder.supplier_order == order) & (PurchaseOrder.user == current_user)).exists():
        flash('该订单已有收货记录，无法删除。', 'danger')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    SupplierOrderItem.delete().where(SupplierOrderItem.order == order).execute()
    order.delete_instance()
    flash('供应商订单已删除', 'success')
    return redirect(url_for('supplier_orders.list_supplier_orders'))


@supplier_orders_bp.route('/supplier_orders/receive/<int:order_id>', methods=['GET'])
@login_required
def receive_order_form(order_id):
    order = SupplierOrder.get_or_none((SupplierOrder.id == order_id) & (SupplierOrder.user == current_user))
    if not order:
        flash('订单不存在或无权访问', 'danger')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    order_items = list(SupplierOrderItem.select().where(SupplierOrderItem.order == order))
    received_qty_map = {}
    for item in order_items:
        received = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .join(PurchaseOrder)
                    .where((PurchaseOrder.supplier_order == order) &
                           (PurchaseOrderItem.product == item.product) &
                           (PurchaseOrder.user == current_user))
                    .scalar()) or 0
        received_qty_map[item.product.id] = received

    items_data = []
    for item in order_items:
        pid = item.product.id
        qty_ordered = item.quantity
        received = received_qty_map.get(pid, 0)
        pending = qty_ordered - received
        items_data.append({
            'product': item.product,
            'qty_ordered': qty_ordered,
            'received': received,
            'pending': pending,
            'unit_price': item.unit_price
        })

    return render_template('receive_order.html',
                           order=order,
                           items_data=items_data,
                           today=datetime.date.today())


@supplier_orders_bp.route('/supplier_orders/receive/<int:order_id>', methods=['POST'])
@login_required
def create_receipt(order_id):
    order = SupplierOrder.get_or_none((SupplierOrder.id == order_id) & (SupplierOrder.user == current_user))
    if not order:
        flash('订单不存在或无权访问', 'danger')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    receive_quantities = {}
    for item in order.items:
        field_name = f'receive_qty_{item.product.id}'
        qty_str = request.form.get(field_name, '0')
        try:
            qty = float(qty_str)
        except ValueError:
            qty = 0
        if qty < 0:
            qty = 0

        received = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .join(PurchaseOrder)
                    .where((PurchaseOrder.supplier_order == order) &
                           (PurchaseOrderItem.product == item.product) &
                           (PurchaseOrder.user == current_user))
                    .scalar()) or 0
        max_qty = item.quantity - received
        if qty > max_qty:
            if max_qty <= 0:
                flash(f'{item.product.name} 已全部收货，无需再收', 'warning')
            else:
                flash(f'{item.product.name} 收货量超出订单剩余({max_qty})，已自动调整为最大值', 'warning')
            qty = max_qty
        if qty > 0:
            receive_quantities[item.product.id] = {
                'quantity': qty,
                'unit_price': item.unit_price,
                'subtotal': qty * item.unit_price
            }

    if not receive_quantities:
        flash('请至少填入一种产品的大于0的收货数量', 'danger')
        return redirect(url_for('supplier_orders.receive_order_form', order_id=order.id))

    with db.atomic():
        total_amount = sum(v['subtotal'] for v in receive_quantities.values())
        receipt = PurchaseOrder.create(
            supplier=order.supplier,
            supplier_order=order,
            order_date=request.form.get('order_date', datetime.date.today()),
            total_amount=total_amount,
            remark=request.form.get('remark', '') or None,
            ship_method=request.form.get('ship_method', '') or None,
            tracking_number=request.form.get('tracking_number', '') or None,
            user=current_user
        )
        for product_id, data in receive_quantities.items():
            PurchaseOrderItem.create(
                order=receipt,
                product=product_id,
                quantity=data['quantity'],
                unit_price=data['unit_price'],
                subtotal=data['subtotal'],
                user=current_user
            )

        all_received = True
        for item in order.items:
            received = (PurchaseOrderItem
                        .select(fn.SUM(PurchaseOrderItem.quantity))
                        .join(PurchaseOrder)
                        .where((PurchaseOrder.supplier_order == order) &
                               (PurchaseOrderItem.product == item.product) &
                               (PurchaseOrder.user == current_user))
                        .scalar()) or 0
            if received < item.quantity:
                all_received = False
                break
        order.status = 'received' if all_received else 'pending'
        order.save()

    flash(f'入库单生成成功，本次收货金额 ¥{total_amount:.2f}', 'success')
    return redirect(url_for('supplier_orders.list_supplier_orders'))
