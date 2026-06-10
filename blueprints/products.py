# blueprints/products.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user         # 新增
from models import db, Product, ProductBundle, ProductBundleItem, PurchaseOrderItem, SalesOrderItem
from peewee import fn
from helpers import generate_sku, parse_positive_float
from log_utils import log_action
from .crud_utils import paginate

products_bp = Blueprint('products', __name__)

@products_bp.route('/products', methods=['GET', 'POST'])
@login_required                                              # 新增
def manage_products():
    if request.method == 'POST':
        sku = request.form.get('sku', '')
        brand = request.form.get('brand', '')
        category1 = request.form.get('category1', '')
        category2 = request.form.get('category2', '')
        name = request.form.get('name')
        spec = request.form.get('spec', '')
        unit = request.form.get('unit')

        product = Product.create(
            sku=sku if sku else None,
            brand=brand if brand else None,
            category1=category1 if category1 else None,
            category2=category2 if category2 else None,
            name=name,
            spec=spec if spec else None,
            unit=unit,
            user=current_user                                 # 新增
        )
        if not sku:
            product.sku = generate_sku(product)
        product.save()
        log_action(current_user, 'update', 'Product', product.id, f'修改产品：{product.name}', request.remote_addr)
        flash('产品修改成功', 'success')

    search = request.args.get('search', '').strip()
    query = Product.select().where(Product.user == current_user)
    if search:
        query = query.where(Product.name.contains(search))
    query = query.order_by(Product.id.desc())
    products, page, per_page, total_pages, total = paginate(query, request)
    all_products = list(Product.select().where(Product.user == current_user).order_by(Product.name))
    all_products_data = [{'id': product.id, 'name': product.name} for product in all_products]
    bundles = list(ProductBundle.select().where(ProductBundle.user == current_user).order_by(ProductBundle.name))

    bundle_rows = (ProductBundleItem
                   .select(ProductBundleItem, Product, ProductBundle)
                   .join(ProductBundle)
                   .switch(ProductBundleItem)
                   .join(Product, on=(ProductBundleItem.component_product == Product.id))
                   .where(ProductBundleItem.user == current_user)
                   .order_by(ProductBundle.name, Product.name))
    bundle_map = {}
    for item in bundle_rows:
        bundle_map.setdefault(item.bundle_id, []).append({
            'name': item.component_product.name,
            'quantity': item.quantity,
            'unit': item.component_product.unit
        })

    # 历史列表也需要过滤
    brands = (Product.select(Product.brand)
              .where(Product.brand.is_null(False) & (Product.brand != '') &
                     (Product.user == current_user))
              .distinct().order_by(Product.brand))
    brand_list = [b.brand for b in brands]

    cat1s = (Product.select(Product.category1)
             .where(Product.category1.is_null(False) & (Product.category1 != '') &
                    (Product.user == current_user))
             .distinct().order_by(Product.category1))
    category1_list = [c.category1 for c in cat1s]

    cat2s = (Product.select(Product.category2)
             .where(Product.category2.is_null(False) & (Product.category2 != '') &
                    (Product.user == current_user))
             .distinct().order_by(Product.category2))
    category2_list = [c.category2 for c in cat2s]

    names = (Product.select(Product.name)
             .where(Product.user == current_user)
             .distinct().order_by(Product.name))
    name_list = [n.name for n in names]

    specs = (Product.select(Product.spec)
             .where(Product.spec.is_null(False) & (Product.spec != '') &
                    (Product.user == current_user))
             .distinct().order_by(Product.spec))
    spec_list = [s.spec for s in specs]

    units = (Product.select(Product.unit)
             .where(Product.user == current_user)
             .distinct().order_by(Product.unit))
    unit_list = [u.unit for u in units]

    return render_template('products.html',
                           products=products,
                           all_products=all_products,
                           all_products_data=all_products_data,
                           bundles=bundles,
                           bundle_map=bundle_map,
                           brand_list=brand_list,
                           category1_list=category1_list,
                           category2_list=category2_list,
                           name_list=name_list,
                           spec_list=spec_list,
                           unit_list=unit_list,
                           page=page,
                           per_page=per_page,
                           total_pages=total_pages,
                           total=total,
                           search=search)

@products_bp.route('/products/edit/<int:product_id>', methods=['POST'])
@login_required
def edit_product(product_id):
    product = Product.get_or_none((Product.id == product_id) &
                                  (Product.user == current_user))   # 校验所属
    if not product:
        flash('产品不存在或无权访问', 'danger')
        return redirect(url_for('products.manage_products'))

    product.sku = request.form.get('sku', '') or None
    product.brand = request.form.get('brand', '') or None
    product.category1 = request.form.get('category1', '') or None
    product.category2 = request.form.get('category2', '') or None
    product.name = request.form.get('name')
    product.spec = request.form.get('spec', '') or None
    product.unit = request.form.get('unit')
    product.save()
    flash('产品修改成功', 'success')
    return redirect(url_for('products.manage_products'))

@products_bp.route('/products/delete/<int:product_id>', methods=['POST'])
@login_required
def delete_product(product_id):
    product = Product.get_or_none((Product.id == product_id) &
                                  (Product.user == current_user))   # 校验所属
    if not product:
        flash('产品不存在或无权访问', 'danger')
        return redirect(url_for('products.manage_products'))

    used_in_purchase = PurchaseOrderItem.select().where(PurchaseOrderItem.product == product).exists()
    used_in_sales = SalesOrderItem.select().where(SalesOrderItem.product == product).exists()
    used_in_bundle = ProductBundleItem.select().where(ProductBundleItem.component_product == product).exists()
    used_in_c_order = CustomerOrderItem.select().where(CustomerOrderItem.product == product).exists()
    used_in_s_order = SupplierOrderItem.select().where(SupplierOrderItem.product == product).exists()
    if used_in_purchase or used_in_sales or used_in_bundle or used_in_c_order or used_in_s_order:
        flash('该产品已存在于单据或套装组成中，无法删除。', 'danger')
        return redirect(url_for('products.manage_products'))

    product.delete_instance()
    log_action(current_user, 'delete', 'Product', product_id, f'删除产品：{product.name}', request.remote_addr)
    flash('产品删除成功', 'success')
    return redirect(url_for('products.manage_products'))

@products_bp.route('/products/batch-delete', methods=['POST'])
@login_required
def batch_delete_products():
    data = request.get_json()
    ids = data.get('ids', [])
    if not ids:
        return jsonify({'error': '未选择产品'}), 400

    deleted = 0
    for pid in ids:
        product = Product.get_or_none((Product.id == pid) & (Product.user == current_user))
        if product:
            # 删除保护：检查是否被引用
            used_in_purchase = PurchaseOrderItem.select().where(PurchaseOrderItem.product == product).exists()
            used_in_sales = SalesOrderItem.select().where(SalesOrderItem.product == product).exists()
            used_in_bundle = ProductBundleItem.select().where(ProductBundleItem.component_product == product).exists()
            if used_in_purchase or used_in_sales or used_in_bundle:
                continue  # 跳过硬删除，可提示
            product.delete_instance()
            deleted += 1

    return jsonify({'deleted': deleted})

@products_bp.route('/products/bundles', methods=['POST'])
@login_required
def save_product_bundle():
    bundle_name = request.form.get('bundle_name', '').strip()
    if not bundle_name:
        flash('套装名称不能为空', 'danger')
        return redirect(url_for('products.manage_products'))

    component_ids = request.form.getlist('component_product_id[]')
    quantities = request.form.getlist('component_quantity[]')
    items = []
    seen_components = set()
    for component_id, quantity in zip(component_ids, quantities):
        if not component_id or not quantity:
            continue
        try:
            component_id = int(component_id)
        except (TypeError, ValueError):
            flash('套装组成包含无效产品', 'danger')
            return redirect(url_for('products.manage_products'))
        quantity = parse_positive_float(quantity)
        component = Product.get_or_none((Product.id == component_id) & (Product.user == current_user))
        if not component or quantity is None:
            flash('套装组成包含无效产品或数量', 'danger')
            return redirect(url_for('products.manage_products'))
        if component.id in seen_components:
            flash('同一个单品不能重复添加到同一个套装', 'danger')
            return redirect(url_for('products.manage_products'))
        seen_components.add(component.id)
        items.append({'component': component, 'quantity': quantity})

    if not items:
        flash('请至少添加一个单品组成套装', 'danger')
        return redirect(url_for('products.manage_products'))

    with db.atomic():
        bundle, _ = ProductBundle.get_or_create(
            user=current_user,
            name=bundle_name
        )
        ProductBundleItem.delete().where(
            (ProductBundleItem.bundle == bundle) &
            (ProductBundleItem.user == current_user)
        ).execute()
        for item in items:
            ProductBundleItem.create(
                bundle=bundle,
                component_product=item['component'],
                quantity=item['quantity'],
                user=current_user
            )

    log_action(current_user, 'update', 'ProductBundle', bundle.id,
               f'设置套装组成：{bundle.name}', request.remote_addr)
    flash('套装组成已保存', 'success')
    return redirect(url_for('products.manage_products'))

@products_bp.route('/products/bundles/<int:bundle_id>/delete', methods=['POST'])
@login_required
def delete_product_bundle(bundle_id):
    bundle = ProductBundle.get_or_none((ProductBundle.id == bundle_id) & (ProductBundle.user == current_user))
    if not bundle:
        flash('套装方案不存在或无权访问', 'danger')
        return redirect(url_for('products.manage_products'))

    with db.atomic():
        ProductBundleItem.delete().where(
            (ProductBundleItem.bundle == bundle) &
            (ProductBundleItem.user == current_user)
        ).execute()
        bundle.delete_instance()

    flash('套装方案已删除', 'success')
    return redirect(url_for('products.manage_products'))
