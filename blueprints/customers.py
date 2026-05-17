# blueprints/customers.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import Customer, SalesOrder, CustomerOrder, CustomerRefund

customers_bp = Blueprint('customers', __name__)


@customers_bp.route('/customers', methods=['GET', 'POST'])
def manage_customers():
    if request.method == 'POST':
        name = request.form.get('name')
        if not name or not name.strip():
            flash('客户名称不能为空', 'danger')
            return redirect(url_for('customers.manage_customers'))
        contact = request.form.get('contact', '')
        phone = request.form.get('phone', '')
        address = request.form.get('address', '')
        Customer.create(
            name=name.strip(),
            contact=contact.strip() or None,
            phone=phone.strip() or None,
            address=address.strip() or None
        )
        flash('客户添加成功', 'success')
        return redirect(url_for('customers.manage_customers'))

    # 分页与搜索
    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    if per_page not in [10, 20, 50, 100]:
        per_page = 20

    query = Customer.select()
    if search:
        query = query.where(Customer.name.contains(search))
    query = query.order_by(Customer.id.desc())
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    customers = query.paginate(page, per_page)

    return render_template('customers.html',
                           customers=customers,
                           page=page,
                           per_page=per_page,
                           total_pages=total_pages,
                           total=total,
                           search=search)


@customers_bp.route('/customers/edit/<int:customer_id>', methods=['POST'])
def edit_customer(customer_id):
    customer = Customer.get_or_none(Customer.id == customer_id)
    if not customer:
        flash('客户不存在', 'danger')
        return redirect(url_for('customers.manage_customers'))

    name = request.form.get('name')
    if not name or not name.strip():
        flash('客户名称不能为空', 'danger')
        return redirect(url_for('customers.manage_customers'))

    customer.name = name.strip()
    customer.contact = request.form.get('contact', '').strip() or None
    customer.phone = request.form.get('phone', '').strip() or None
    customer.address = request.form.get('address', '').strip() or None
    customer.save()
    flash('客户修改成功', 'success')
    return redirect(url_for('customers.manage_customers'))


@customers_bp.route('/customers/delete/<int:customer_id>', methods=['POST'])
def delete_customer(customer_id):
    customer = Customer.get_or_none(Customer.id == customer_id)
    if not customer:
        flash('客户不存在', 'danger')
        return redirect(url_for('customers.manage_customers'))

    # 删除保护
    has_sales = SalesOrder.select().where(SalesOrder.customer == customer).exists()
    has_orders = CustomerOrder.select().where(CustomerOrder.customer == customer).exists()
    has_refunds = CustomerRefund.select().where(CustomerRefund.customer == customer).exists()
    if has_sales or has_orders or has_refunds:
        flash('该客户已有业务记录，无法删除。', 'danger')
        return redirect(url_for('customers.manage_customers'))

    customer.delete_instance()
    flash('客户已删除', 'success')
    return redirect(url_for('customers.manage_customers'))