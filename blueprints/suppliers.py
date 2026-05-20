# blueprints/suppliers.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import Supplier, PurchaseOrder, SupplierOrder
from log_utils import log_action

suppliers_bp = Blueprint('suppliers', __name__)


@suppliers_bp.route('/suppliers', methods=['GET', 'POST'])
@login_required
def manage_suppliers():
    if request.method == 'POST':
        name = request.form.get('name')
        if not name or not name.strip():
            flash('供应商名称不能为空', 'danger')
            return redirect(url_for('suppliers.manage_suppliers'))
        contact = request.form.get('contact', '')
        phone = request.form.get('phone', '')

        supplier = Supplier.create(
            name=name.strip(),
            contact=contact.strip() or None,
            phone=phone.strip() or None,
            user=current_user
        )

        # 记录操作日志
        log_action(current_user, 'create', 'Supplier', supplier.id,
                   f'添加供应商：{supplier.name}', request.remote_addr)

        flash('供应商添加成功', 'success')
        return redirect(url_for('suppliers.manage_suppliers'))

    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    if per_page not in [10, 20, 50, 100]:
        per_page = 20

    query = Supplier.select().where(Supplier.user == current_user)
    if search:
        query = query.where(Supplier.name.contains(search))
    query = query.order_by(Supplier.id.desc())
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    suppliers = query.paginate(page, per_page)

    return render_template('suppliers.html',
                           suppliers=suppliers,
                           page=page,
                           per_page=per_page,
                           total_pages=total_pages,
                           total=total,
                           search=search)


@suppliers_bp.route('/suppliers/edit/<int:supplier_id>', methods=['POST'])
@login_required
def edit_supplier(supplier_id):
    supplier = Supplier.get_or_none((Supplier.id == supplier_id) & (Supplier.user == current_user))
    if not supplier:
        flash('供应商不存在或无权访问', 'danger')
        return redirect(url_for('suppliers.manage_suppliers'))

    name = request.form.get('name')
    if not name or not name.strip():
        flash('供应商名称不能为空', 'danger')
        return redirect(url_for('suppliers.manage_suppliers'))

    supplier.name = name.strip()
    supplier.contact = request.form.get('contact', '').strip() or None
    supplier.phone = request.form.get('phone', '').strip() or None
    supplier.save()

    # 记录操作日志
    log_action(current_user, 'update', 'Supplier', supplier.id,
               f'修改供应商：{supplier.name}', request.remote_addr)

    flash('供应商修改成功', 'success')
    return redirect(url_for('suppliers.manage_suppliers'))


@suppliers_bp.route('/suppliers/delete/<int:supplier_id>', methods=['POST'])
@login_required
def delete_supplier(supplier_id):
    supplier = Supplier.get_or_none((Supplier.id == supplier_id) & (Supplier.user == current_user))
    if not supplier:
        flash('供应商不存在或无权访问', 'danger')
        return redirect(url_for('suppliers.manage_suppliers'))

    has_purchase = PurchaseOrder.select().where(PurchaseOrder.supplier == supplier).exists()
    has_orders = SupplierOrder.select().where(SupplierOrder.supplier == supplier).exists()
    if has_purchase or has_orders:
        flash('该供应商已有业务记录，无法删除。', 'danger')
        return redirect(url_for('suppliers.manage_suppliers'))

    name = supplier.name
    supplier.delete_instance()

    # 记录操作日志
    log_action(current_user, 'delete', 'Supplier', supplier_id,
               f'删除供应商：{name}', request.remote_addr)

    flash('供应商已删除', 'success')
    return redirect(url_for('suppliers.manage_suppliers'))