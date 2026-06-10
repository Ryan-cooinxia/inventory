# blueprints/purchases.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, Supplier, Product, PurchaseOrder, PurchaseOrderItem
from helpers import parse_non_negative_float, parse_positive_float
from .crud_utils import paginate, get_or_none_user, parse_order_items_from_form
from log_utils import log_action
import datetime

purchases_bp = Blueprint('purchases', __name__)


def _supplier_choices():
    """当前用户的供应商列表（下拉选项）"""
    suppliers = Supplier.select().where(Supplier.user == current_user)
    return [{'id': s.id, 'name': s.name} for s in suppliers]


def _product_choices():
    """当前用户的产品列表（下拉选项）"""
    products = Product.select().where(Product.user == current_user)
    return [{'id': p.id, 'name': p.name} for p in products]


def _render_purchase_form(suppliers_data=None, products_data=None):
    """统一的表单渲染"""
    if suppliers_data is None:
        suppliers_data = _supplier_choices()
    if products_data is None:
        products_data = _product_choices()
    return render_template('purchase.html',
                           suppliers=suppliers_data,
                           products=products_data,
                           suppliers_json=suppliers_data,
                           products_json=products_data)


@purchases_bp.route('/purchase/add', methods=['GET', 'POST'])
@login_required
def add_purchase():
    if request.method == 'POST':
        supplier = get_or_none_user(Supplier, request.form.get('supplier_id'), current_user)
        if not supplier:
            flash('供应商不存在或无权访问', 'danger')
            return redirect(url_for('purchases.add_purchase'))

        items, errors = parse_order_items_from_form(request, current_user)
        if errors:
            for err in errors:
                flash(err, 'danger')
            return _render_purchase_form()
        if not items:
            flash('请至少填写一条明细', 'danger')
            return _render_purchase_form()

        with db.atomic():
            total_amount = sum(it['subtotal'] for it in items)
            order = PurchaseOrder.create(
                supplier=supplier,
                order_date=request.form.get('order_date') or datetime.date.today(),
                total_amount=total_amount,
                remark=request.form.get('remark', '') or None,
                ship_method=request.form.get('ship_method', '') or None,
                tracking_number=request.form.get('tracking_number', '') or None,
                user=current_user
            )
            for it in items:
                PurchaseOrderItem.create(
                    order=order,
                    product=it['product_id'],
                    quantity=it['quantity'],
                    unit_price=it['unit_price'],
                    subtotal=it['subtotal'],
                    user=current_user
                )
        flash(f'入库单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('purchases.add_purchase'))

    return _render_purchase_form()


@purchases_bp.route('/receipts')
@login_required
def list_receipts():
    supplier_id = request.args.get('supplier_id')
    query = PurchaseOrder.select().where(PurchaseOrder.user == current_user)
    if supplier_id:
        query = query.where(PurchaseOrder.supplier == int(supplier_id))
    query = query.order_by(PurchaseOrder.order_date.desc())

    rows = []
    for po in query:
        order_id = po.supplier_order_id
        po_items = list(po.items)
        total_lines = len(po_items) if po_items else 1
        for idx, item in enumerate(po_items):
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
        if not po_items:
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
@login_required
def edit_receipt(receipt_id):
    receipt = get_or_none_user(PurchaseOrder, receipt_id, current_user)
    if not receipt:
        flash('入库单不存在或无权访问', 'danger')
        return redirect(url_for('purchases.list_receipts'))

    if request.method == 'POST':
        supplier = get_or_none_user(Supplier, request.form.get('supplier_id'), current_user)
        if not supplier:
            flash('供应商不存在或无权访问', 'danger')
            return redirect(url_for('purchases.list_receipts'))

        receipt.supplier = supplier
        receipt.order_date = request.form.get('order_date')
        receipt.remark = request.form.get('remark', '') or None
        receipt.ship_method = request.form.get('ship_method', '') or None
        receipt.tracking_number = request.form.get('tracking_number', '') or None

        items, errors = parse_order_items_from_form(request, current_user)
        if errors or not items:
            for err in errors:
                flash(err, 'danger')
            if not items:
                flash('请至少填写一条明细', 'danger')
            return redirect(url_for('purchases.edit_receipt', receipt_id=receipt.id))

        with db.atomic():
            PurchaseOrderItem.delete().where(PurchaseOrderItem.order == receipt).execute()
            receipt.total_amount = sum(it['subtotal'] for it in items)
            receipt.save()
            for it in items:
                PurchaseOrderItem.create(
                    order=receipt, product=it['product_id'],
                    quantity=it['quantity'], unit_price=it['unit_price'],
                    subtotal=it['subtotal'], user=current_user
                )
        flash('入库单修改成功', 'success')
        log_action(current_user, 'update', 'PurchaseOrder', receipt.id,
                   f'修改入库单 #{receipt.id}', request.remote_addr)
        return redirect(url_for('purchases.list_receipts'))

    suppliers = Supplier.select().where(Supplier.user == current_user)
    products = Product.select().where(Product.user == current_user)
    items = list(PurchaseOrderItem.select().where(PurchaseOrderItem.order == receipt))
    return render_template('receipt_edit.html', receipt=receipt, items=items,
                           suppliers=suppliers, products=products)


@purchases_bp.route('/receipts/delete/<int:receipt_id>', methods=['POST'])
@login_required
def delete_receipt(receipt_id):
    receipt = get_or_none_user(PurchaseOrder, receipt_id, current_user)
    if receipt:
        if receipt.supplier_order_id:
            flash('提醒：该入库单关联供应商订单，订单状态将重新计算', 'warning')
        PurchaseOrderItem.delete().where(PurchaseOrderItem.order == receipt).execute()
        receipt.delete_instance()

        # 回退供应商订单状态
        if receipt.supplier_order_id:
            s_order = SupplierOrder.get_or_none(SupplierOrder.id == receipt.supplier_order_id)
            if s_order:
                from peewee import fn as _fn
                received_amt = (PurchaseOrder
                               .select(_fn.SUM(PurchaseOrder.total_amount))
                               .where((PurchaseOrder.supplier_order == s_order) &
                                      (PurchaseOrder.user == current_user))
                               .scalar()) or 0
                s_order.status = 'received' if received_amt >= s_order.total_amount else 'pending'
                s_order.save()

        flash('入库单已删除', 'success')
    return redirect(url_for('purchases.list_receipts'))
