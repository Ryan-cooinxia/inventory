# blueprints/supplier_orders.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import (
    Supplier, SupplierOrder, SupplierOrderItem,
    PurchaseOrder, PurchaseOrderItem, Product
)
from peewee import fn
import datetime

supplier_orders_bp = Blueprint('supplier_orders', __name__)


@supplier_orders_bp.route('/supplier_orders')
def list_supplier_orders():
    orders = SupplierOrder.select().order_by(SupplierOrder.order_date.desc())
    rows = []
    row_index = 0
    total_order_value = 0.0
    total_received_value = 0.0

    for order in orders:
        # 计算已收货数量映射
        received_map = {}
        for item in order.items:
            received = (PurchaseOrderItem
                        .select(fn.SUM(PurchaseOrderItem.quantity))
                        .join(PurchaseOrder)
                        .where(
                            (PurchaseOrder.supplier_order == order) &
                            (PurchaseOrderItem.product == item.product)
                        ).scalar()) or 0
            received_map[item.product.id] = received

        # 检查是否存在已收货记录（用于控制编辑/删除按钮）
        has_receipt = PurchaseOrder.select().where(PurchaseOrder.supplier_order == order).exists()

        items = list(order.items)
        total_lines = len(items) if items else 1

        for idx, item in enumerate(items):
            product = item.product
            qty_ordered = item.quantity
            unit_price = item.unit_price
            subtotal = item.subtotal
            received_qty = received_map.get(product.id, 0)
            pending_qty = qty_ordered - received_qty

            # 中文状态
            if received_qty == 0:
                status_text = '未交货'
            elif received_qty < qty_ordered:
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
                'received_qty': received_qty,
                'pending_qty': pending_qty,
                'status_text': status_text,
                'status': order.status,
                'estimated_delivery': order.estimated_delivery,
                'has_receipt': has_receipt,
                'is_first_row': idx == 0,
                'rowspan_count': total_lines
            })
            total_order_value += subtotal
            total_received_value += received_qty * unit_price

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
                'received_qty': 0,
                'pending_qty': 0,
                'status_text': '-',
                'status': order.status,
                'estimated_delivery': order.estimated_delivery,
                'has_receipt': has_receipt,
                'is_first_row': True,
                'rowspan_count': 1
            })

    total_unreceived_value = total_order_value - total_received_value

    return render_template('supplier_orders.html',
                           orders=rows,
                           total_order_value=total_order_value,
                           total_received_value=total_received_value,
                           total_unreceived_value=total_unreceived_value)


@supplier_orders_bp.route('/supplier_orders/add', methods=['GET', 'POST'])
def add_supplier_order():
    if request.method == 'POST':
        supplier_id = request.form.get('supplier_id')
        order_date = request.form.get('order_date')
        estimated_delivery = request.form.get('estimated_delivery') or None
        remark = request.form.get('remark', '')

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

        if not items:
            flash('请至少填写一条明细', 'danger')
            suppliers = Supplier.select()
            products = Product.select()
            suppliers_data = [{'id': s.id, 'name': s.name} for s in suppliers]
            products_data = [{'id': p.id, 'name': p.name} for p in products]
            return render_template('supplier_order_form.html',
                                   suppliers=suppliers,
                                   products=products,
                                   suppliers_json=suppliers_data,
                                   products_json=products_data,
                                   order=None,
                                   order_items=[],
                                   today=datetime.date.today())

        total_amount = sum(item['subtotal'] for item in items)

        # 自动生成订单单号：MD-YYYYMMDD-序号
        today_str = datetime.date.today().strftime('%Y%m%d')
        last_order = (SupplierOrder
                      .select()
                      .where(SupplierOrder.order_date == datetime.date.today())
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
            supplier=supplier_id,
            order_number=order_number,
            order_date=order_date or datetime.date.today(),
            total_amount=total_amount,
            estimated_delivery=estimated_delivery,
            remark=remark or None
        )
        for item in items:
            SupplierOrderItem.create(order=order,
                                     product=item['product_id'],
                                     quantity=item['quantity'],
                                     unit_price=item['unit_price'],
                                     subtotal=item['subtotal'])
        flash(f'供应商订单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    # GET
    suppliers = Supplier.select()
    suppliers_data = [{'id': s.id, 'name': s.name} for s in suppliers]
    products = Product.select()
    products_data = [{'id': p.id, 'name': p.name} for p in products]
    return render_template('supplier_order_form.html',
                           suppliers=suppliers,
                           products=products,
                           suppliers_json=suppliers_data,
                           products_json=products_data,
                           order=None,
                           order_items=[],
                           today=datetime.date.today())


@supplier_orders_bp.route('/supplier_orders/edit/<int:order_id>', methods=['GET', 'POST'])
def edit_supplier_order(order_id):
    order = SupplierOrder.get_or_none(SupplierOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    if request.method == 'POST':
        order.supplier = request.form.get('supplier_id')
        order.order_date = request.form.get('order_date')
        order.estimated_delivery = request.form.get('estimated_delivery') or None
        order.remark = request.form.get('remark', '') or None

        # 删除旧明细，重建
        SupplierOrderItem.delete().where(SupplierOrderItem.order == order).execute()
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
        order.total_amount = sum(item['subtotal'] for item in items)
        order.save()
        for item in items:
            SupplierOrderItem.create(order=order,
                                     product=item['product_id'],
                                     quantity=item['quantity'],
                                     unit_price=item['unit_price'],
                                     subtotal=item['subtotal'])
        flash('订单修改成功', 'success')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    # GET
    suppliers = Supplier.select()
    suppliers_data = [{'id': s.id, 'name': s.name} for s in suppliers]
    products = Product.select()
    products_data = [{'id': p.id, 'name': p.name} for p in products]
    order_items = list(SupplierOrderItem.select().where(SupplierOrderItem.order == order))

    # 构造显示用单号
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
def delete_supplier_order(order_id):
    order = SupplierOrder.get_or_none(SupplierOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    # 删除保护
    if PurchaseOrder.select().where(PurchaseOrder.supplier_order == order).exists():
        flash('该订单已有收货记录，无法删除。', 'danger')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    SupplierOrderItem.delete().where(SupplierOrderItem.order == order).execute()
    order.delete_instance()
    flash('供应商订单已删除', 'success')
    return redirect(url_for('supplier_orders.list_supplier_orders'))


@supplier_orders_bp.route('/supplier_orders/receive/<int:order_id>', methods=['GET'])
def receive_order_form(order_id):
    order = SupplierOrder.get_or_none(SupplierOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    order_items = list(SupplierOrderItem.select().where(SupplierOrderItem.order == order))
    received_qty_map = {}
    for item in order_items:
        received = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .join(PurchaseOrder)
                    .where((PurchaseOrder.supplier_order == order) &
                           (PurchaseOrderItem.product == item.product))
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
def create_receipt(order_id):
    order = SupplierOrder.get_or_none(SupplierOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('supplier_orders.list_supplier_orders'))

    # 收集本次收货数量
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
                           (PurchaseOrderItem.product == item.product))
                    .scalar()) or 0
        max_qty = item.quantity - received
        if qty > max_qty:
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

    # 创建入库单，自动关联供应商订单
    total_amount = sum(v['subtotal'] for v in receive_quantities.values())
    receipt = PurchaseOrder.create(
        supplier=order.supplier,
        supplier_order=order,
        order_date=request.form.get('order_date', datetime.date.today()),
        total_amount=total_amount,
        remark=request.form.get('remark', '') or None,
        ship_method=request.form.get('ship_method', '') or None,
        tracking_number=request.form.get('tracking_number', '') or None
    )
    for product_id, data in receive_quantities.items():
        PurchaseOrderItem.create(
            order=receipt,
            product=product_id,
            quantity=data['quantity'],
            unit_price=data['unit_price'],
            subtotal=data['subtotal']
        )

    # 更新订单状态：检查是否全部收完
    all_received = True
    for item in order.items:
        received = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .join(PurchaseOrder)
                    .where((PurchaseOrder.supplier_order == order) &
                           (PurchaseOrderItem.product == item.product))
                    .scalar()) or 0
        if received < item.quantity:
            all_received = False
            break
    order.status = 'received' if all_received else 'pending'
    order.save()

    flash(f'入库单生成成功，本次收货金额 ¥{total_amount:.2f}', 'success')
    return redirect(url_for('supplier_orders.list_supplier_orders'))