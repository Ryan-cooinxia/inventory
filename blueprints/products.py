# blueprints/products.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from models import Product, PurchaseOrderItem, SalesOrderItem
from peewee import fn
from helpers import generate_sku

products_bp = Blueprint('products', __name__)

@products_bp.route('/products', methods=['GET', 'POST'])
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
            unit=unit
        )
        if not sku:
            product.sku = generate_sku(product)
            product.save()
        flash('产品添加成功', 'success')
        return redirect(url_for('products.manage_products'))

    search = request.args.get('search', '').strip()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    if per_page not in [10, 20, 50, 100]:
        per_page = 20

    query = Product.select()
    if search:
        query = query.where(Product.name.contains(search))
    query = query.order_by(Product.id.desc())
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    products = query.paginate(page, per_page)

    # 历史列表查询
    brands = (Product.select(Product.brand)
              .where(Product.brand.is_null(False) & (Product.brand != ''))
              .distinct().order_by(Product.brand))
    brand_list = [b.brand for b in brands]

    cat1s = (Product.select(Product.category1)
             .where(Product.category1.is_null(False) & (Product.category1 != ''))
             .distinct().order_by(Product.category1))
    category1_list = [c.category1 for c in cat1s]

    cat2s = (Product.select(Product.category2)
             .where(Product.category2.is_null(False) & (Product.category2 != ''))
             .distinct().order_by(Product.category2))
    category2_list = [c.category2 for c in cat2s]

    names = (Product.select(Product.name)
             .distinct().order_by(Product.name))
    name_list = [n.name for n in names]

    specs = (Product.select(Product.spec)
             .where(Product.spec.is_null(False) & (Product.spec != ''))
             .distinct().order_by(Product.spec))
    spec_list = [s.spec for s in specs]

    units = (Product.select(Product.unit)
             .distinct().order_by(Product.unit))
    unit_list = [u.unit for u in units]

    return render_template('products.html',
                           products=products,
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
def edit_product(product_id):
    product = Product.get_or_none(Product.id == product_id)
    if not product:
        flash('产品不存在', 'danger')
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
def delete_product(product_id):
    product = Product.get_or_none(Product.id == product_id)
    if not product:
        flash('产品不存在', 'danger')
        return redirect(url_for('products.manage_products'))

    # 删除保护
    used_in_purchase = PurchaseOrderItem.select().where(PurchaseOrderItem.product == product).exists()
    used_in_sales = SalesOrderItem.select().where(SalesOrderItem.product == product).exists()
    if used_in_purchase or used_in_sales:
        flash('该产品已存在于入库/出库单中，无法删除。', 'danger')
        return redirect(url_for('products.manage_products'))

    product.delete_instance()
    flash('产品已删除', 'success')
    return redirect(url_for('products.manage_products'))