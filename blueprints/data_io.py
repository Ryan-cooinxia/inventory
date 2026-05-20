# blueprints/data_io.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response
from flask_login import login_required, current_user
from models import Product, Customer, Supplier
from helpers import generate_sku
import csv
import io

data_io_bp = Blueprint('data_io', __name__)

@data_io_bp.route('/data')
@login_required
def data_manage():
    return render_template('data_manage.html')

@data_io_bp.route('/export/<table_type>')
@login_required
def export_csv(table_type):
    if table_type == 'products':
        records = Product.select().where(Product.user == current_user)
        headers = ['SKU编码', '品牌', '一级分类', '二级分类', '产品名称', '规格', '单位']
        rows = [[p.sku or '', p.brand or '', p.category1 or '', p.category2 or '', p.name, p.spec or '', p.unit] for p in records]
    elif table_type == 'customers':
        records = Customer.select().where(Customer.user == current_user)
        headers = ['ID', '客户名称', '联系人', '电话', '常用地址']
        rows = [[c.id, c.name, c.contact or '', c.phone or '', c.address or ''] for c in records]
    elif table_type == 'suppliers':
        records = Supplier.select().where(Supplier.user == current_user)
        headers = ['ID', '供应商名称', '联系人', '电话']
        rows = [[s.id, s.name, s.contact or '', s.phone or ''] for s in records]
    else:
        flash('无效的表类型', 'danger')
        return redirect(url_for('data_io.data_manage'))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)

    response = Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={table_type}.csv'}
    )
    return response

@data_io_bp.route('/import/<table_type>', methods=['POST'])
@login_required
def import_csv(table_type):
    if 'file' not in request.files:
        flash('未选择文件', 'danger')
        return redirect(url_for('data_io.data_manage'))

    file = request.files['file']
    if file.filename == '':
        flash('文件名为空', 'danger')
        return redirect(url_for('data_io.data_manage'))

    try:
        raw_data = file.stream.read()
        try:
            text = raw_data.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = raw_data.decode('gbk')
            except UnicodeDecodeError:
                text = raw_data.decode('latin-1')
        stream = io.StringIO(text)
        reader = csv.reader(stream)
        next(reader)  # 跳过表头
    except Exception as e:
        flash(f'文件读取失败：{e}', 'danger')
        return redirect(url_for('data_io.data_manage'))

    success_count = 0
    error_count = 0

    if table_type == 'products':
        for row in reader:
            try:
                product = Product.create(
                    sku=row[0] if row[0] else None,
                    brand=row[1] if row[1] else None,
                    category1=row[2] if row[2] else None,
                    category2=row[3] if row[3] else None,
                    name=row[4],
                    spec=row[5] if row[5] else None,
                    unit=row[6],
                    user=current_user
                )
                if not product.sku:
                    product.sku = generate_sku(product)
                    product.save()
                success_count += 1
            except Exception:
                error_count += 1

    elif table_type == 'customers':
        for row in reader:
            try:
                Customer.create(
                    name=row[0],
                    contact=row[1] if row[1] else None,
                    phone=row[2] if row[2] else None,
                    address=row[3] if len(row) > 3 and row[3] else None,
                    user=current_user
                )
                success_count += 1
            except Exception:
                error_count += 1

    elif table_type == 'suppliers':
        for row in reader:
            try:
                Supplier.create(
                    name=row[0],
                    contact=row[1] if row[1] else None,
                    phone=row[2] if row[2] else None,
                    user=current_user
                )
                success_count += 1
            except Exception:
                error_count += 1
    else:
        flash('无效的表类型', 'danger')
        return redirect(url_for('data_io.data_manage'))

    flash(f'导入完成：成功 {success_count} 条，失败 {error_count} 条', 'success')
    return redirect(url_for('data_io.data_manage'))