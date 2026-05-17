from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import *
from peewee import fn
import datetime

purchases_bp = Blueprint('purchases', __name__)

@purchases_bp.route('/purchase/add', methods=['GET', 'POST'])
def add_purchase():
    # 提前查询供应商和产品，并构造 JSON 数据（搜索组件必需）
    suppliers = Supplier.select()
    suppliers_data = [{'id': s.id, 'name': s.name} for s in suppliers]
    products = Product.select()
    products_data = [{'id': p.id, 'name': p.name} for p in products]

    if request.method == 'POST':
        supplier_id = request.form.get('supplier_id')
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
            # 传入 JSON 数据，否则模板中的 tojson 过滤器会报错
            return render_template('purchase.html',
                                   suppliers=suppliers_data,
                                   products=products_data,
                                   suppliers_json=suppliers_data,
                                   products_json=products_data)

        total_amount = sum(item['subtotal'] for item in items)
        order = PurchaseOrder.create(
            supplier=supplier_id,
            order_date=order_date or datetime.date.today(),
            total_amount=total_amount,
            remark=remark or None,
            ship_method=ship_method or None,
            tracking_number=tracking_number or None
        )
        for item in items:
            PurchaseOrderItem.create(
                order=order,
                product=item['product_id'],
                quantity=item['quantity'],
                unit_price=item['unit_price'],
                subtotal=item['subtotal']
            )
        flash(f'入库单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('purchases.add_purchase'))

    # GET 请求：同样传入 JSON 数据
    return render_template('purchase.html',
                           suppliers=suppliers_data,
                           products=products_data,
                           suppliers_json=suppliers_data,
                           products_json=products_data)


@purchases_bp.route('/receipts')
def list_receipts():
    supplier_id = request.args.get('supplier_id')
    query = PurchaseOrder.select().order_by(PurchaseOrder.order_date.desc())
    if supplier_id:
        query = query.where(PurchaseOrder.supplier == int(supplier_id))

    rows = []
    for po in query:
        order_id = po.supplier_order_id
        items = list(po.items)
        total_lines = len(items) if items else 1
        for idx, item in enumerate(items):
            rows.append({
                'receipt_id': po.id,
                'supplier_name': po.supplier.name,
                'order_id': order_id,
                'order_date': po.order_date,
                'total_amount': po.total_amount,
                'ship_method': po.ship_method or '-',
                'tracking_number': po.tracking_number or '-',
                'product_name': item.product.name,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'subtotal': item.subtotal,
                'is_first_row': idx == 0,
                'rowspan_count': total_lines
            })
        if not items:
            rows.append({
                'receipt_id': po.id,
                'supplier_name': po.supplier.name,
                'order_id': order_id,
                'order_date': po.order_date,
                'total_amount': po.total_amount,
                'ship_method': po.ship_method or '-',
                'tracking_number': po.tracking_number or '-',
                'product_name': '-',
                'quantity': 0,
                'unit_price': 0,
                'subtotal': 0,
                'is_first_row': True,
                'rowspan_count': 1
            })

    return render_template('receipts.html', receipts=rows)


@purchases_bp.route('/receipts/edit/<int:receipt_id>', methods=['GET', 'POST'])
def edit_receipt(receipt_id):
    receipt = PurchaseOrder.get_or_none(PurchaseOrder.id == receipt_id)
    if not receipt:
        flash('入库单不存在', 'danger')
        return redirect(url_for('purchases.list_receipts'))

    if request.method == 'POST':
        receipt.supplier = request.form.get('supplier_id')
        receipt.order_date = request.form.get('order_date')
        receipt.remark = request.form.get('remark', '') or None
        receipt.ship_method = request.form.get('ship_method', '') or None
        receipt.tracking_number = request.form.get('tracking_number', '') or None

        PurchaseOrderItem.delete().where(PurchaseOrderItem.order == receipt).execute()
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
        receipt.total_amount = sum(item['subtotal'] for item in items)
        receipt.save()
        for item in items:
            PurchaseOrderItem.create(order=receipt, product=item['product_id'], quantity=item['quantity'],
                                     unit_price=item['unit_price'], subtotal=item['subtotal'])
        flash('入库单修改成功', 'success')
        return redirect(url_for('purchases.list_receipts'))

    suppliers = Supplier.select()
    products = Product.select()
    items = list(PurchaseOrderItem.select().where(PurchaseOrderItem.order == receipt))
    return render_template('receipt_edit.html', receipt=receipt, items=items, suppliers=suppliers, products=products)


@purchases_bp.route('/receipts/delete/<int:receipt_id>', methods=['POST'])
def delete_receipt(receipt_id):
    receipt = PurchaseOrder.get_or_none(PurchaseOrder.id == receipt_id)
    if receipt:
        if receipt.supplier_order_id:
            flash('提醒：该入库单关联供应商订单，删除后订单状态需手动调整', 'warning')
        PurchaseOrderItem.delete().where(PurchaseOrderItem.order == receipt).execute()
        receipt.delete_instance()
        flash('入库单已删除', 'success')
    return redirect(url_for('purchases.list_receipts'))