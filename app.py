"""
仓库记账系统主程序
基于 Flask + Peewee + SQLite
包含产品/客户/供应商管理、入库/出库录单、统计报表
"""
import os
import sys
from flask import Flask, render_template, request, redirect, url_for, flash
from models import *
from peewee import fn, JOIN
from blueprints.products import products_bp
from blueprints.customers import customers_bp
from blueprints.suppliers import suppliers_bp
import datetime
import csv
import io

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'  # 用于 flash 消息
app.register_blueprint(products_bp)
app.register_blueprint(customers_bp)
app.register_blueprint(suppliers_bp)


# ----- 数据库连接管理 -----
@app.before_request
def before_request():
    """每个请求前连接数据库"""
    db.connect()

@app.after_request
def after_request(response):
    """每个请求后关闭数据库（避免连接泄漏）"""
    db.close()
    return response

# ----- 初始化数据库（首次运行时创建表）-----
def init_db():
    db.create_tables([Product, Customer, Supplier,
                      PurchaseOrder, PurchaseOrderItem,
                      SalesOrder, SalesOrderItem,
                      CustomerRefund, CustomerTransaction], safe=True)

# 打包成 exe 后，模板路径可能发生变化，需要修正
if getattr(sys, 'frozen', False):
    template_folder = os.path.join(sys._MEIPASS, 'templates')
    static_folder = os.path.join(sys._MEIPASS, 'static')
    app.template_folder = template_folder
    app.static_folder = static_folder

# ================== 首页看板 ==================
@app.route('/')
def index():
    today = datetime.date.today()

    # 供货商订单总额
    supplier_order_total = (SupplierOrder
                            .select(fn.SUM(SupplierOrder.total_amount))
                            .scalar()) or 0
    # 供货商已收货金额（关联了供应商订单的入库单总额）
    supplier_received_amount = (PurchaseOrder
                                .select(fn.SUM(PurchaseOrder.total_amount))
                                .where(PurchaseOrder.supplier_order.is_null(False))
                                .scalar()) or 0
    supplier_unreceived_total = max(supplier_order_total - supplier_received_amount, 0)

    # 客户订单总额
    customer_order_total = (CustomerOrder
                            .select(fn.SUM(CustomerOrder.total_amount))
                            .scalar()) or 0
    # 客户已发货金额（关联了客户订单的出库单总额）
    customer_shipped_amount = (SalesOrder
                               .select(fn.SUM(SalesOrder.total_amount))
                               .where(SalesOrder.customer_order.is_null(False))
                               .scalar()) or 0
    customer_unshipped_total = max(customer_order_total - customer_shipped_amount, 0)
    # 客户退款总额
    customer_refund_total = (CustomerRefund
                             .select(fn.SUM(CustomerRefund.amount))
                             .scalar()) or 0

    # 供货商退款总额（暂时没有供货商退款表，设为0）
    supplier_refund_total = 0.0

    # 当日入库单列表
    today_purchase_orders = (PurchaseOrder
                             .select()
                             .where(PurchaseOrder.order_date == today)
                             .order_by(PurchaseOrder.id.desc()))
    purchase_list = []
    for po in today_purchase_orders:
        items = list(po.items)
        product_names = ', '.join([item.product.name for item in items])
        purchase_list.append({
            'id': po.id,
            'supplier': po.supplier.name,
            'total_amount': po.total_amount,
            'product_names': product_names,
            'order_date': po.order_date
        })

    # 当日出库单列表
    today_sales_orders = (SalesOrder
                          .select()
                          .where(SalesOrder.order_date == today)
                          .order_by(SalesOrder.id.desc()))
    sales_list = []
    for so in today_sales_orders:
        items = list(so.items)
        product_names = ', '.join([item.product.name for item in items])
        sales_list.append({
            'id': so.id,
            'customer': so.customer.name,
            'total_amount': so.total_amount,
            'product_names': product_names,
            'order_date': so.order_date
        })

    # 当前库存（有库存的产品 + 负数库存收集）
    products = Product.select()
    inventory_list = []
    negative_stock_products = []
    for p in products:
        total_in = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .where(PurchaseOrderItem.product == p)
                    .scalar()) or 0
        total_out = (SalesOrderItem
                     .select(fn.SUM(SalesOrderItem.quantity))
                     .where(SalesOrderItem.product == p)
                     .scalar()) or 0
        stock = total_in - total_out

        if stock < 0:
            negative_stock_products.append({
                'sku': p.sku or '',
                'name': p.name,
                'stock': stock
            })

        if stock > 0:
            purchase_data = (PurchaseOrderItem
                             .select(fn.SUM(PurchaseOrderItem.subtotal).alias('total_cost'),
                                     fn.SUM(PurchaseOrderItem.quantity).alias('total_qty'))
                             .where(PurchaseOrderItem.product == p)
                             .first())
            if purchase_data and purchase_data.total_qty and purchase_data.total_qty > 0:
                avg_cost = purchase_data.total_cost / purchase_data.total_qty
            else:
                avg_cost = 0.0
            inventory_list.append({
                'sku': p.sku or '',
                'name': p.name,
                'stock': stock,
                'stock_value': stock * avg_cost
            })

    return render_template('index.html',
                           today=today,
                           supplier_unreceived_total=supplier_unreceived_total,
                           customer_unshipped_total=customer_unshipped_total,
                           customer_refund_total=customer_refund_total,
                           supplier_refund_total=supplier_refund_total,
                           purchase_list=purchase_list,
                           sales_list=sales_list,
                           inventory_list=inventory_list,
                           negative_stock_products=negative_stock_products)

# ================== 入库单管理 ==================
@app.route('/purchase/add', methods=['GET', 'POST'])
def add_purchase():
    """新增采购入库单"""
    if request.method == 'POST':
        supplier_id = request.form.get('supplier_id')
        order_date = request.form.get('order_date')
        remark = request.form.get('remark', '')
        ship_method = request.form.get('ship_method', '')
        tracking_number = request.form.get('tracking_number', '')
        tracking_number = tracking_number.replace(' ', '')

        # 获取明细数据（前端传来的是数组）
        product_ids = request.form.getlist('product_id[]')
        quantities = request.form.getlist('quantity[]')
        unit_prices = request.form.getlist('unit_price[]')

        # 过滤掉空行
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
            return render_template('purchase.html',
                                   suppliers=Supplier.select(),
                                   products=Product.select())

        # 计算总金额
        total_amount = sum(item['subtotal'] for item in items)

        # 创建采购单
        order = PurchaseOrder.create(
            supplier=supplier_id,
            order_date=order_date or datetime.date.today(),
            total_amount=total_amount,
            remark=remark or None,
            ship_method=ship_method or None,      # 新增
            tracking_number=tracking_number or None  # 新增
        )
        # 创建明细行
        for item in items:
            PurchaseOrderItem.create(
                order=order,
                product=item['product_id'],
                quantity=item['quantity'],
                unit_price=item['unit_price'],
                subtotal=item['subtotal']
            )
        flash(f'入库单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('add_purchase'))

# 构建产品列表（id + name）
    products_data = [{'id': p.id, 'name': p.name} for p in Product.select()]
    suppliers_data = [{'id': s.id, 'name': s.name} for s in Supplier.select()]

    return render_template('purchase.html',
                           suppliers=suppliers_data,
                           products=products_data,
                           suppliers_json=suppliers_data,
                           products_json=products_data)

# ---------------- 入库单管理 ----------------
@app.route('/receipts')
def list_receipts():
    supplier_id = request.args.get('supplier_id')
    query = PurchaseOrder.select().order_by(PurchaseOrder.order_date.desc())
    if supplier_id:
        query = query.where(PurchaseOrder.supplier == int(supplier_id))

    rows = []
    for po in query:
        order_id = po.supplier_order_id
        items = list(po.items)
        # 计算行数
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
        # 如果没有明细项（理论上不会），仍然显示一行空产品
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

    return render_template('receipts.html', receipts=rows, filter_supplier_id=supplier_id)

@app.route('/receipts/edit/<int:receipt_id>', methods=['GET', 'POST'])
def edit_receipt(receipt_id):
    receipt = PurchaseOrder.get_or_none(PurchaseOrder.id == receipt_id)
    if not receipt:
        flash('入库单不存在', 'danger')
        return redirect(url_for('list_receipts'))

    if request.method == 'POST':
        receipt.supplier = request.form.get('supplier_id')
        receipt.order_date = request.form.get('order_date')
        receipt.remark = request.form.get('remark', '') or None
        receipt.ship_method = request.form.get('ship_method', '') or None
        receipt.tracking_number = request.form.get('tracking_number', '') or None

        # 删除旧明细，重建
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
        return redirect(url_for('list_receipts'))

    # GET：查询明细回填
    suppliers = Supplier.select()
    products = Product.select()
    items = list(PurchaseOrderItem.select().where(PurchaseOrderItem.order == receipt))
    return render_template('receipt_edit.html', receipt=receipt, items=items, suppliers=suppliers, products=products)


@app.route('/receipts/delete/<int:receipt_id>', methods=['POST'])
def delete_receipt(receipt_id):
    receipt = PurchaseOrder.get_or_none(PurchaseOrder.id == receipt_id)
    if receipt:
        if receipt.supplier_order_id:
            flash('提醒：该入库单关联供应商订单，删除后订单状态需手动调整', 'warning')
        PurchaseOrderItem.delete().where(PurchaseOrderItem.order == receipt).execute()
        receipt.delete_instance()
        flash('入库单已删除', 'success')
    return redirect(url_for('list_receipts'))

# ================== 出库单管理 ==================

@app.route('/sales/add', methods=['GET', 'POST'])
def add_sales():
    """新增销售出库单"""
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        order_date = request.form.get('order_date')
        remark = request.form.get('remark', '')
        ship_method = request.form.get('ship_method', '')
        tracking_number = request.form.get('tracking_number', '')
        tracking_number = tracking_number.replace(' ', '')

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
            return render_template('sales.html',
                                   customers=Customer.select(),
                                   products=Product.select())

        total_amount = sum(item['subtotal'] for item in items)

        order = SalesOrder.create(
            customer=customer_id,
            order_date=order_date or datetime.date.today(),
            total_amount=total_amount,
            remark=remark or None,
            ship_method=ship_method or None,      # 新增
            tracking_number=tracking_number or None,  # 新增
        )
        for item in items:
            SalesOrderItem.create(
                order=order,
                product=item['product_id'],
                quantity=item['quantity'],
                unit_price=item['unit_price'],
                subtotal=item['subtotal']
            )
        flash(f'出库单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('add_sales'))

    products_data = [{'id': p.id, 'name': p.name} for p in Product.select()]
    customers_data = [{'id': c.id, 'name': c.name} for c in Customer.select()]

    return render_template('sales.html',
                           customers=customers_data,
                           products=products_data,
                           customers_json=customers_data,
                           products_json=products_data)

# ================== 统计报表 ==================
@app.route('/report/daily')
def report_daily():
    """每日进货/出货量统计"""
    # 默认查询最近7天
    end_date = request.args.get('end_date', datetime.date.today())
    start_date = request.args.get('start_date', datetime.date.today() - datetime.timedelta(days=6))

    if isinstance(start_date, str):
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()
    if isinstance(end_date, str):
        end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()

    # 按天汇总进货量
    purchase_by_day = (PurchaseOrder
                       .select(PurchaseOrder.order_date,
                               fn.SUM(PurchaseOrderItem.quantity).alias('total_qty'))
                       .join(PurchaseOrderItem)
                       .where(PurchaseOrder.order_date.between(start_date, end_date))
                       .group_by(PurchaseOrder.order_date)
                       .order_by(PurchaseOrder.order_date))

    # 按天汇总出货量
    sales_by_day = (SalesOrder
                    .select(SalesOrder.order_date,
                            fn.SUM(SalesOrderItem.quantity).alias('total_qty'))
                    .join(SalesOrderItem)
                    .where(SalesOrder.order_date.between(start_date, end_date))
                    .group_by(SalesOrder.order_date)
                    .order_by(SalesOrder.order_date))

    # 整理成前端图表需要的数据
    date_range = [start_date + datetime.timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    purchase_dict = {row.order_date: row.total_qty for row in purchase_by_day}
    sales_dict = {row.order_date: row.total_qty for row in sales_by_day}

    chart_dates = [d.strftime('%m-%d') for d in date_range]
    chart_purchase = [purchase_dict.get(d, 0) for d in date_range]
    chart_sales = [sales_dict.get(d, 0) for d in date_range]

    return render_template('report_daily.html',
                           start_date=start_date, end_date=end_date,
                           chart_dates=chart_dates,
                           chart_purchase=chart_purchase,
                           chart_sales=chart_sales)

@app.route('/report/customer')
@app.route('/report/customer')
def report_customer():
    start_date = request.args.get('start_date', '2000-01-01')
    end_date = request.args.get('end_date', datetime.date.today())
    query = (CustomerOrderItem
             .select(CustomerOrder.customer, CustomerOrderItem.product,
                     fn.SUM(CustomerOrderItem.quantity).alias('total_qty'),
                     fn.SUM(CustomerOrderItem.subtotal).alias('total_amount'))
             .join(CustomerOrder)
             .where(CustomerOrder.order_date.between(start_date, end_date))
             .group_by(CustomerOrder.customer, CustomerOrderItem.product)
             .order_by(CustomerOrder.customer.name, CustomerOrderItem.product.name))
    rows = []
    for item in query:
        rows.append({
            'customer': item.order.customer.name,
            'product': item.product.name,
            'unit': item.product.unit,
            'total_qty': item.total_qty,
            'total_amount': item.total_amount
        })
    return render_template('report_customer.html', start_date=start_date, end_date=end_date, rows=rows)

@app.route('/report/supplier')
def report_supplier():
    start_date = request.args.get('start_date', '2000-01-01')
    end_date = request.args.get('end_date', datetime.date.today().strftime('%Y-%m-%d'))

    # 按产品汇总供应商订单的订购总量和总金额
    query = (SupplierOrderItem
             .select(SupplierOrderItem.product,
                     fn.SUM(SupplierOrderItem.quantity).alias('total_qty'),
                     fn.SUM(SupplierOrderItem.subtotal).alias('total_amount'))
             .join(SupplierOrder)
             .where(SupplierOrder.order_date.between(start_date, end_date))
             .group_by(SupplierOrderItem.product)
             .order_by(fn.SUM(SupplierOrderItem.subtotal).desc()))

    rows = []
    for item in query:
        rows.append({
            'product': item.product.name,
            'sku': item.product.sku or '',
            'unit': item.product.unit,
            'total_qty': item.total_qty,
            'total_amount': item.total_amount
        })

    return render_template('report_supplier.html',
                           rows=rows,
                           start_date=start_date,
                           end_date=end_date)

@app.route('/report/inventory')
def report_inventory():
    products = Product.select()
    rows = []
    total_value = 0.0
    alert_products = []

    for p in products:
        total_in = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .where(PurchaseOrderItem.product == p)
                    .scalar()) or 0
        total_out = (SalesOrderItem
                     .select(fn.SUM(SalesOrderItem.quantity))
                     .where(SalesOrderItem.product == p)
                     .scalar()) or 0
        stock = total_in - total_out

        # 记录负数库存
        if stock < 0:
            alert_products.append({'sku': p.sku or '', 'name': p.name, 'stock': stock})

        # 展示所有库存不为零的产品（包含负数）
        if stock != 0:
            purchase_data = (PurchaseOrderItem
                             .select(fn.SUM(PurchaseOrderItem.subtotal).alias('total_cost'),
                                     fn.SUM(PurchaseOrderItem.quantity).alias('total_qty'))
                             .where(PurchaseOrderItem.product == p)
                             .first())
            if purchase_data and purchase_data.total_qty and purchase_data.total_qty > 0:
                avg_cost = purchase_data.total_cost / purchase_data.total_qty
            else:
                avg_cost = 0.0

            stock_value = stock * avg_cost
            total_value += stock_value

            rows.append({
                'sku': p.sku or '',
                'name': p.name,
                'spec': p.spec or '',
                'unit': p.unit,
                'stock': stock,
                'avg_cost': avg_cost,
                'stock_value': stock_value,
                'is_negative': stock < 0
            })

    return render_template('report_inventory.html',
                           rows=rows,
                           total_value=total_value,
                           alert_products=alert_products)

# ================== 供应商订单管理 ==================
@app.route('/supplier_orders')
def list_supplier_orders():
    orders = SupplierOrder.select().order_by(SupplierOrder.order_date.desc())
    rows = []
    row_index = 0
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

        # 检查是否存在已收货记录（用于判断是否可删除/编辑）
        has_receipt = PurchaseOrder.select().where(PurchaseOrder.supplier_order == order).exists()

        for item in order.items:
            product = item.product
            qty_ordered = item.quantity
            unit_price = item.unit_price
            subtotal = item.subtotal
            received_qty = received_map.get(product.id, 0)

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
                'order_number': order.order_number or f'MD-{order.id:06d}',
                'supplier_name': order.supplier.name,
                'order_date': order.order_date,
                'product_name': product.name,
                'unit_price': unit_price,
                'qty_ordered': qty_ordered,
                'subtotal': subtotal,
                'received_qty': received_qty,
                'status_text': status_text,
                'status': order.status,
                'estimated_delivery': order.estimated_delivery,
                'has_receipt': has_receipt
            })

    # 汇总全部产品货值和已收货价值
    total_order_value = sum(row['subtotal'] for row in rows)
    total_received_value = sum(row['received_qty'] * row['unit_price'] for row in rows)
    total_unreceived_value = total_order_value - total_received_value   # 新增
    
    return render_template('supplier_orders.html', orders=rows,
                           total_order_value=total_order_value,
                           total_received_value=total_received_value,
                           total_unreceived_value=total_unreceived_value)   # 传递新变量

@app.route('/supplier_orders/add', methods=['GET', 'POST'])
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
            products_data = [{'id': p.id, 'name': p.name} for p in Product.select()]
            return render_template('supplier_order_form.html',
                                   suppliers=suppliers,
                                   products=products,
                                   suppliers_json=suppliers_data,
                                   products_json=products_data,
                                   order=None,
                                   order_items=[],
                                   today=datetime.date.today())

        total_amount = sum(item['subtotal'] for item in items)

        # ---------- 自动生成订单单号 ----------
        today_str = datetime.date.today().strftime('%Y%m%d')
        # 查询当天已有的最大序号
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
            order_number=order_number,           # 新加的单号字段
            order_date=order_date or datetime.date.today(),
            total_amount=total_amount,
            estimated_delivery=estimated_delivery,
            remark=remark or None
        )
        for item in items:
            SupplierOrderItem.create(order=order, product=item['product_id'], quantity=item['quantity'], unit_price=item['unit_price'], subtotal=item['subtotal'])
        flash(f'供应商订单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('list_supplier_orders'))

    # GET 请求
    suppliers = Supplier.select()
    suppliers_data = [{'id': s.id, 'name': s.name} for s in suppliers]
    products = Product.select()
    products_data = [{'id': p.id, 'name': p.name} for p in products]

    order = None
    order_items = []

    return render_template('supplier_order_form.html',
                           suppliers=suppliers,
                           products=products,
                           suppliers_json=suppliers_data,
                           products_json=products_data,
                           order=order,
                           order_items=order_items,
                           today=datetime.date.today())

@app.route('/supplier_orders/edit/<int:order_id>', methods=['GET', 'POST'])
def edit_supplier_order(order_id):
    order = SupplierOrder.get_or_none(SupplierOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('list_supplier_orders'))
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
            SupplierOrderItem.create(order=order, product=item['product_id'], quantity=item['quantity'], unit_price=item['unit_price'], subtotal=item['subtotal'])
        flash('订单修改成功', 'success')
        return redirect(url_for('list_supplier_orders'))
    suppliers = Supplier.select()
    suppliers_data = [{'id': s.id, 'name': s.name} for s in suppliers]
    products = Product.select()
    products_data = [{'id': p.id, 'name': p.name} for p in Product.select()]
    # 获取明细，附带上产品对象（Peewee 懒加载已包含）
    order_items = list(SupplierOrderItem.select().where(SupplierOrderItem.order == order))
    # 当前供应商的初始值（供搜索组件使用）
    current_supplier = order.supplier
    # 构建显示用的单号（若数据库无值则生成临时单号）
    display_number = order.order_number or f"MD-{order.order_date.strftime('%Y%m%d')}-{order.id:04d}"
    return render_template('supplier_order_form.html',
                           suppliers=suppliers,
                           products=products,
                           suppliers_json=suppliers_data,
                           products_json=products_data,
                           order=order,
                           order_items=order_items,
                           current_supplier=current_supplier,
                           display_number=display_number,
                           today=datetime.date.today())

@app.route('/supplier_orders/delete/<int:order_id>', methods=['POST'])
def delete_supplier_order(order_id):
    order = SupplierOrder.get_or_none(SupplierOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('list_supplier_orders'))

    # 检查是否已有入库单关联
    if PurchaseOrder.select().where(PurchaseOrder.supplier_order == order).exists():
        flash('该订单已有收货记录，无法删除。', 'danger')
        return redirect(url_for('list_supplier_orders'))

    SupplierOrderItem.delete().where(SupplierOrderItem.order == order).execute()
    order.delete_instance()
    flash('供应商订单已删除', 'success')
    return redirect(url_for('list_supplier_orders'))

# 从供应商订单生成入库单（收货）
@app.route('/supplier_orders/receive/<int:order_id>', methods=['GET'])
def receive_order_form(order_id):
    order = SupplierOrder.get_or_none(SupplierOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('list_supplier_orders'))

    # 计算每个产品已收货数量
    order_items = list(SupplierOrderItem.select().where(SupplierOrderItem.order == order))
    received_qty_map = {}
    for item in order_items:
        received = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .join(PurchaseOrder)
                    .where((PurchaseOrder.supplier_order == order) & (PurchaseOrderItem.product == item.product))
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
    return render_template('receive_order.html', order=order, items_data=items_data, today=datetime.date.today())

@app.route('/supplier_orders/receive/<int:order_id>', methods=['POST'])
def create_receipt(order_id):
    order = SupplierOrder.get_or_none(SupplierOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('list_supplier_orders'))

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
        # 计算已收货量，确保不超过待收货量
        received = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .join(PurchaseOrder)
                    .where((PurchaseOrder.supplier_order == order) & (PurchaseOrderItem.product == item.product))
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
        return redirect(url_for('receive_order_form', order_id=order.id))

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
                    .where((PurchaseOrder.supplier_order == order) & (PurchaseOrderItem.product == item.product))
                    .scalar()) or 0
        if received < item.quantity:
            all_received = False
            break
    order.status = 'received' if all_received else 'pending'
    order.save()

    flash(f'入库单生成成功，本次收货金额 ¥{total_amount:.2f}', 'success')
    return redirect(url_for('list_supplier_orders'))

# -------------------- 客户订单管理 --------------------
@app.route('/orders')
def list_orders():
    orders = CustomerOrder.select().order_by(CustomerOrder.order_date.desc())
    rows = []
    row_index = 0
    total_order_value = 0.0
    total_shipped_value = 0.0

    for order in orders:
        items = list(order.items)
        order_product_ids = {item.product.id for item in items}

        # 已发货总金额
        shipped_amount = (SalesOrder
                          .select(fn.SUM(SalesOrder.total_amount))
                          .where(SalesOrder.customer_order == order)
                          .scalar()) or 0.0
        total_shipped_value += shipped_amount
        total_order_value += order.total_amount

        # 已发货总数量（按产品汇总）
        shipped_qty_total = 0.0
        shipped_qty_map = {}
        for item in items:
            shipped = (SalesOrderItem
                       .select(fn.SUM(SalesOrderItem.quantity))
                       .join(SalesOrder)
                       .where((SalesOrder.customer_order == order) & (SalesOrderItem.product == item.product))
                       .scalar()) or 0
            shipped_qty_map[item.product.id] = shipped
            shipped_qty_total += shipped

        # 订单总数量
        order_qty_total = sum(item.quantity for item in items)
        remaining_qty_total = order_qty_total - shipped_qty_total
        if remaining_qty_total < 0:
            remaining_qty_total = 0.0

        # 剩余金额
        remaining_amount = order.total_amount - shipped_amount
        if remaining_amount < 0:
            remaining_amount = 0.0

        # 判断换货
        has_swap = False
        if shipped_amount > 0:
            extra_items = (SalesOrderItem
                           .select()
                           .join(SalesOrder)
                           .where(SalesOrder.customer_order == order)
                           .where(SalesOrderItem.product.not_in(order_product_ids))
                           .exists())
            has_swap = extra_items

        # 订单状态文本
        if shipped_amount >= order.total_amount:
            status_text = '已完成'
        elif shipped_amount > 0:
            status_text = '部分发货-换货' if has_swap else '部分发货'
        else:
            status_text = '未发货'

        has_shipment = shipped_amount > 0
        total_lines = len(items) if items else 1

        for idx, item in enumerate(items):
            product = item.product
            row_index += 1
            rows.append({
                'row_no': row_index,
                'order_id': order.id,
                'customer_name': order.customer.name,
                'order_date': order.order_date,
                'product_name': product.name,
                'unit_price': item.unit_price,
                'qty_ordered': item.quantity,
                'subtotal': item.subtotal,
                'shipped_qty_total': shipped_qty_total,      # 订单总已发货数量
                'remaining_qty_total': remaining_qty_total,  # 订单总剩余数量
                'shipped_amount': shipped_amount,            # 订单已发货金额
                'remaining_amount': remaining_amount,        # 订单剩余金额
                'status_text': status_text,
                'invoice_required': '是' if order.invoice_required else '否',
                'is_first_row': idx == 0,
                'rowspan_count': total_lines,
                'has_shipment': has_shipment,
                'status': order.status
            })

        if not items:   # 空订单兜底
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
                'shipped_qty_total': shipped_qty_total,
                'remaining_qty_total': remaining_qty_total,
                'shipped_amount': shipped_amount,
                'remaining_amount': remaining_amount,
                'status_text': status_text,
                'invoice_required': '是' if order.invoice_required else '否',
                'is_first_row': True,
                'rowspan_count': 1,
                'has_shipment': has_shipment,
                'status': order.status
            })

    total_unshipped_value = total_order_value - total_shipped_value
    return render_template('orders.html',
                           orders=rows,
                           total_order_value=total_order_value,
                           total_shipped_value=total_shipped_value,
                           total_unshipped_value=total_unshipped_value)

@app.route('/orders/add', methods=['GET', 'POST'])
def add_order():
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        order_date = request.form.get('order_date')
        remark = request.form.get('remark', '')
        invoice_required = request.form.get('invoice_required') == '1'

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
            customers = Customer.select()
            products = Product.select()
            customers_data = [{'id': c.id, 'name': c.name} for c in customers]
            products_data = [{'id': p.id, 'name': p.name} for p in products]
            return render_template('order_form.html',
                                   customers=customers,
                                   products=products,
                                   customers_json=customers_data,
                                   products_json=products_data,
                                   order=None,
                                   order_items=[],
                                   today=datetime.date.today())

        total_amount = sum(item['subtotal'] for item in items)
        order = CustomerOrder.create(
            customer=customer_id,
            order_date=order_date or datetime.date.today(),
            total_amount=total_amount,
            invoice_required=invoice_required,
            remark=remark or None
        )
        for item in items:
            CustomerOrderItem.create(order=order, product=item['product_id'],
                                     quantity=item['quantity'], unit_price=item['unit_price'],
                                     subtotal=item['subtotal'])
        flash(f'订单创建成功，总金额：{total_amount:.2f}', 'success')
        return redirect(url_for('list_orders'))

    # GET 请求
    customers = Customer.select()
    customers_data = [{'id': c.id, 'name': c.name} for c in customers]
    products = Product.select()
    products_data = [{'id': p.id, 'name': p.name} for p in products]
    return render_template('order_form.html',
                           customers=customers,
                           products=products,
                           customers_json=customers_data,
                           products_json=products_data,
                           order=None,
                           order_items=[],
                           today=datetime.date.today())

@app.route('/orders/edit/<int:order_id>', methods=['GET', 'POST'])
def edit_order(order_id):
    order = CustomerOrder.get_or_none(CustomerOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('list_orders'))

    if request.method == 'POST':
        order.customer = request.form.get('customer_id')
        order.order_date = request.form.get('order_date')
        order.remark = request.form.get('remark', '') or None
        order.invoice_required = (request.form.get('invoice_required') == '1')

        # 删除旧明细，重建
        CustomerOrderItem.delete().where(CustomerOrderItem.order == order).execute()
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
            CustomerOrderItem.create(order=order, product=item['product_id'],
                                     quantity=item['quantity'], unit_price=item['unit_price'],
                                     subtotal=item['subtotal'])
        flash('订单修改成功', 'success')
        return redirect(url_for('list_orders'))

    # GET 请求
    customers = Customer.select()
    customers_data = [{'id': c.id, 'name': c.name} for c in customers]
    products = Product.select()
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

@app.route('/orders/delete/<int:order_id>', methods=['POST'])
def delete_order(order_id):
    order = CustomerOrder.get_or_none(CustomerOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('list_orders'))

    # 检查是否已有出库单关联
    if SalesOrder.select().where(SalesOrder.customer_order == order).exists():
        flash('该订单已有发货记录，无法删除。如需关闭，请使用“强制完成”功能。', 'danger')
        return redirect(url_for('list_orders'))

    CustomerOrderItem.delete().where(CustomerOrderItem.order == order).execute()
    order.delete_instance()
    flash('订单已删除', 'success')
    return redirect(url_for('list_orders'))

@app.route('/orders/ship/<int:order_id>', methods=['GET'])
def ship_order_form(order_id):
    order = CustomerOrder.get_or_none(CustomerOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('list_orders'))
    
    # 计算每个产品的已发货数量（汇总该订单关联的所有出库单明细）
    order_items = list(CustomerOrderItem.select().where(CustomerOrderItem.order == order))
    shipped_qty_map = {}
    for item in order_items:
        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) & (SalesOrderItem.product == item.product))
                   .scalar()) or 0
        shipped_qty_map[item.product.id] = shipped

    items_data = []
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
    products = Product.select()
    products_data = [{'id': p.id, 'name': p.name} for p in products]

    return render_template('ship_order.html',
                           order=order,
                           items_data=items_data,
                           today=datetime.date.today(),
                           products=products,                  # 保留原对象（可删除）
                           products_json=products_data) 


@app.route('/orders/ship/<int:order_id>', methods=['POST'])
def create_shipment(order_id):
    order = CustomerOrder.get_or_none(CustomerOrder.id == order_id)
    if not order:
        flash('订单不存在', 'danger')
        return redirect(url_for('list_orders'))

    # 收集本次发货数量（原始订单明细）
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

        # 计算已发货量，确保不超待发货量
        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) & (SalesOrderItem.product == item.product))
                   .scalar()) or 0
        max_qty = item.quantity - shipped
        if qty > max_qty:
            qty = max_qty
        if qty > 0:
            ship_quantities[item.product.id] = {
                'quantity': qty,
                'unit_price': item.unit_price,
                'subtotal': qty * item.unit_price
            }

    # 处理额外添加的产品行（换货部分）
    extra_product_ids = request.form.getlist('extra_product_id[]')
    extra_quantities = request.form.getlist('extra_quantity[]')
    extra_unit_prices = request.form.getlist('extra_unit_price[]')
    extra_items = []
    for pid, qty, price in zip(extra_product_ids, extra_quantities, extra_unit_prices):
        if not pid or not qty or not price:
            continue
        qty = float(qty)
        price = float(price)
        extra_items.append({'product_id': int(pid), 'quantity': qty, 'unit_price': price, 'subtotal': qty * price})

    if not ship_quantities and not extra_items:
        flash('请至少填写发货数量或添加产品', 'danger')
        return redirect(url_for('ship_order_form', order_id=order.id))
    # 构建完整的发货清单（包含 product_id）
    all_ship_items = []
    for pid, data in ship_quantities.items():
        all_ship_items.append({'product_id': pid, 'quantity': data['quantity']})
    all_ship_items.extend(extra_items)

    # 库存检查（合并原始明细和额外产品）
    stock_ok, stock_errors = check_stock_before_ship(all_ship_items)
    if not stock_ok:
        for err in stock_errors:
            flash(f'库存不足：{err}', 'danger')
        return redirect(url_for('ship_order_form', order_id=order.id))    
        
    # 创建出库单（包含原明细发货量 + 额外产品）
    total_amount = sum(v['subtotal'] for v in ship_quantities.values()) + sum(e['subtotal'] for e in extra_items)
    ship = SalesOrder.create(
        customer=order.customer,
        customer_order=order,
        order_date=request.form.get('order_date', datetime.date.today()),
        total_amount=total_amount,
        remark=request.form.get('remark', '') or None,
        ship_method=request.form.get('ship_method', '') or None,
        tracking_number=request.form.get('tracking_number', '') or None
    )
    for product_id, data in ship_quantities.items():
        SalesOrderItem.create(
            order=ship,
            product=product_id,
            quantity=data['quantity'],
            unit_price=data['unit_price'],
            subtotal=data['subtotal']
        )
    for extra in extra_items:
        SalesOrderItem.create(
            order=ship,
            product=extra['product_id'],
            quantity=extra['quantity'],
            unit_price=extra['unit_price'],
            subtotal=extra['subtotal']
        )

    # 更新订单状态（自动判断）
    # 计算订单所有产品已发货总数量（包括额外发货的不算在订单明细内，这里只判断原明细是否发完）
    all_shipped = True
    for item in order.items:
        shipped = (SalesOrderItem
                   .select(fn.SUM(SalesOrderItem.quantity))
                   .join(SalesOrder)
                   .where((SalesOrder.customer_order == order) & (SalesOrderItem.product == item.product))
                   .scalar()) or 0
        if shipped < item.quantity:
            all_shipped = False
            break
    # 如果原明细都发完了，或者发货总额 >= 订单总额，则自动标记为已完成
    if all_shipped or total_amount >= order.total_amount:
        order.status = 'shipped'
    else:
        order.status = 'pending'
    order.save()

    flash(f'出库单生成成功，本次发货金额 ¥{total_amount:.2f}', 'success')
    return redirect(url_for('list_orders'))
# ---------------- 出库单管理 ----------------
@app.route('/shipments')
def list_shipments():
    customer_id = request.args.get('customer_id')
    query = SalesOrder.select().order_by(SalesOrder.order_date.desc())
    if customer_id:
        query = query.where(SalesOrder.customer == int(customer_id))

    rows = []
    for so in query:
        order_id = so.customer_order_id
        items = list(so.items)
        total_lines = len(items) if items else 1
        for idx, item in enumerate(items):
            rows.append({
                'ship_id': so.id,
                'customer_name': so.customer.name,
                'order_id': order_id,
                'order_date': so.order_date,
                'total_amount': so.total_amount,
                'ship_method': so.ship_method or '-',
                'tracking_number': so.tracking_number or '-',
                'product_name': item.product.name,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'subtotal': item.subtotal,
                'is_first_row': idx == 0,
                'rowspan_count': total_lines
            })
        if not items:
            rows.append({
                'ship_id': so.id,
                'customer_name': so.customer.name,
                'order_id': order_id,
                'order_date': so.order_date,
                'total_amount': so.total_amount,
                'ship_method': so.ship_method or '-',
                'tracking_number': so.tracking_number or '-',
                'product_name': '-',
                'quantity': 0,
                'unit_price': 0,
                'subtotal': 0,
                'is_first_row': True,
                'rowspan_count': 1
            })

    return render_template('shipments.html', shipments=rows, filter_customer_id=customer_id)
@app.route('/shipments/edit/<int:shipment_id>', methods=['GET', 'POST'])
def edit_shipment(shipment_id):
    shipment = SalesOrder.get_or_none(SalesOrder.id == shipment_id)
    if not shipment:
        flash('出库单不存在', 'danger')
        return redirect(url_for('list_shipments'))
    if request.method == 'POST':
        shipment.customer = request.form.get('customer_id')
        shipment.order_date = request.form.get('order_date')
        shipment.remark = request.form.get('remark', '') or None
        shipment.ship_method = request.form.get('ship_method', '') or None
        shipment.tracking_number = request.form.get('tracking_number', '') or None
        # 处理明细：删除旧明细，新建
        SalesOrderItem.delete().where(SalesOrderItem.order == shipment).execute()
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
        shipment.total_amount = sum(item['subtotal'] for item in items)
        shipment.save()
        for item in items:
            SalesOrderItem.create(order=shipment, product=item['product_id'], quantity=item['quantity'], unit_price=item['unit_price'], subtotal=item['subtotal'])
        flash('出库单修改成功', 'success')
        return redirect(url_for('list_shipments'))
    # GET：查询明细回填
    items = list(SalesOrderItem.select().where(SalesOrderItem.order == shipment))
    customers = Customer.select()
    products = Product.select()
    return render_template('shipment_edit.html', shipment=shipment, items=items, customers=customers, products=products)

@app.route('/shipments/delete/<int:shipment_id>', methods=['POST'])
def delete_shipment(shipment_id):
    shipment = SalesOrder.get_or_none(SalesOrder.id == shipment_id)
    if shipment:
        # 直接检查外键值，避免懒加载出错
        if shipment.customer_order_id is not None:
            # 可选：检查订单是否真实存在（避免指向已删除订单）
            if not CustomerOrder.select().where(CustomerOrder.id == shipment.customer_order_id).exists():
                flash('关联的订单已不存在，出库单将被删除', 'warning')
            else:
                flash('提醒：该出库单关联订单，删除后订单状态需手动调整', 'warning')
        SalesOrderItem.delete().where(SalesOrderItem.order == shipment).execute()
        shipment.delete_instance()
        flash('出库单已删除', 'success')
    return redirect(url_for('list_shipments'))

# ---------------- 客户退款管理 ----------------
@app.route('/refunds', methods=['GET', 'POST'])
def manage_refunds():
    """退款列表 + 新增退款"""
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        sales_order_id = request.form.get('sales_order_id')
        refund_date = request.form.get('refund_date')
        amount = request.form.get('amount')
        remark = request.form.get('remark', '')

        customer_order_id = request.form.get('customer_order_id') or None

        CustomerRefund.create(
            customer=customer_id,
            sales_order=sales_order_id if sales_order_id else None,
            customer_order=customer_order_id,   # 新增：关联订单
            refund_date=refund_date or datetime.date.today(),
            amount=float(amount),
            remark=remark or None
        )
        flash('退款记录添加成功', 'success')
        return redirect(url_for('manage_refunds'))

    # 用标准 JOIN 语法，避免 SQL 报错
    refunds = (CustomerRefund
               .select(CustomerRefund, Customer, SalesOrder, CustomerOrder)
               .join(Customer)
               .switch(CustomerRefund)
               .join(SalesOrder, JOIN.LEFT_OUTER, on=(CustomerRefund.sales_order == SalesOrder.id))
               .join(CustomerOrder, JOIN.LEFT_OUTER, on=(CustomerRefund.customer_order == CustomerOrder.id))
               .order_by(CustomerRefund.refund_date.desc()))
    customers = Customer.select()
    sales_orders = SalesOrder.select()
    customer_orders = CustomerOrder.select()   # 新增：所有客户订单

    return render_template('refunds.html',
                           refunds=refunds,
                           customers=customers,
                           sales_orders=sales_orders,
                           customer_orders=customer_orders)   # 新增传参

# ---------------- 客户资金总览 ----------------
@app.route('/customer/finance')
def customer_finance_overview():
    customers = Customer.select()
    rows = []
    for customer in customers:
        total_order = (SalesOrder
                       .select(fn.SUM(SalesOrder.total_amount))
                       .where(SalesOrder.customer == customer)
                       .scalar()) or 0

        # 已发货金额 = 该客户所有出库单的金额总和
        total_shipped = (SalesOrder
                        .select(fn.SUM(SalesOrder.total_amount))
                        .where(SalesOrder.customer == customer)
                        .scalar()) or 0  # 注意这里我们实际就是出库单总额，但为了区分订单与出库，需要确认数据来源
        # 更准确：已发货金额可以取 SalesOrder 的 total_amount 汇总，因为它就是出库金额
        # 但目前 SalesOrder 表既包含手工出的，也包含订单生成的，其总金额就是已发货金额

        total_refund = (CustomerRefund
                        .select(fn.SUM(CustomerRefund.amount))
                        .where(CustomerRefund.customer == customer)
                        .scalar()) or 0

        # 客户余额 = 订单总金额 - 已发货金额 - 退款金额
        # 这里订单总金额我们使用 CustomerOrder 的总金额查询，而非 SalesOrder
        # 先计算订单总金额
        order_total = (CustomerOrder
                       .select(fn.SUM(CustomerOrder.total_amount))
                       .where(CustomerOrder.customer == customer)
                       .scalar()) or 0

        balance = order_total - total_shipped - total_refund

        rows.append({
            'customer': customer,
            'total_order': order_total,
            'total_shipped': total_shipped,
            'total_refund': total_refund,
            'balance': balance
        })
    return render_template('customer_finance.html', rows=rows)
# ---------------- 数据导入导出 ----------------
@app.route('/data')
def data_manage():
    """数据管理页面"""
    return render_template('data_manage.html')

@app.route('/export/<table_type>')
def export_csv(table_type):
    """导出指定表为 CSV 并下载"""
    if table_type == 'products':
        records = Product.select()
        headers = ['SKU编码', '品牌', '一级分类', '二级分类', '产品名称', '规格', '单位']
        rows = [[p.sku or '', p.brand or '', p.category1 or '', p.category2 or '', p.name, p.spec or '', p.unit] for p in records]
    elif table_type == 'customers':
        records = Customer.select()
        headers = ['ID', '客户名称', '联系人', '电话']
        rows = [[c.id, c.name, c.contact or '', c.phone or ''] for c in records]
    elif table_type == 'suppliers':
        records = Supplier.select()
        headers = ['ID', '供应商名称', '联系人', '电话']
        rows = [[s.id, s.name, s.contact or '', s.phone or ''] for s in records]
    else:
        flash('无效的表类型', 'danger')
        return redirect(url_for('data_manage'))

    # 生成 CSV 内容
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)

    # 响应为 CSV 文件下载（UTF-8 BOM 解决 Excel 中文乱码）
    response = app.response_class(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={
            'Content-Disposition': f'attachment; filename={table_type}.csv'
        }
    )
    return response

@app.route('/import/<table_type>', methods=['POST'])
def import_csv(table_type):
    if 'file' not in request.files:
        flash('未选择文件', 'danger')
        return redirect(url_for('data_manage'))

    file = request.files['file']
    if file.filename == '':
        flash('文件名为空', 'danger')
        return redirect(url_for('data_manage'))

    # 读取文件内容，并自动尝试常见编码（UTF-8、GBK）
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
        return redirect(url_for('data_manage'))

    success_count = 0
    error_count = 0

    if table_type == 'products':
        for row in reader:
            try:
                Product.create(
                    sku=row[0] if row[0] else None,
                    brand=row[1] if row[1] else None,
                    category1=row[2] if row[2] else None,
                    category2=row[3] if row[3] else None,
                    name=row[4],
                    spec=row[5] if row[5] else None,
                    unit=row[6]
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
                    address=row[3] if len(row) > 3 and row[3] else None
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
                    address=row[3] if len(row) > 3 and row[3] else None
                )
                success_count += 1
            except Exception:
                error_count += 1
    else:
        flash('无效的表类型', 'danger')
        return redirect(url_for('data_manage'))

    flash(f'导入完成：成功 {success_count} 条，失败 {error_count} 条', 'success')
    return redirect(url_for('data_manage'))


# ================== 启动应用 ==================
if __name__ == '__main__':
    with app.app_context():
        init_db()          # 自动建表
    app.run(debug=True, host='0.0.0.0', port=5000)