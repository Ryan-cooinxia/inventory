# blueprints/reports.py
from flask import Blueprint, render_template, request
from models import (
    Product, PurchaseOrder, PurchaseOrderItem,
    SalesOrder, SalesOrderItem,
    CustomerOrder, CustomerOrderItem,
    SupplierOrder, SupplierOrderItem
)
from peewee import fn
import datetime

reports_bp = Blueprint('reports', __name__)


@reports_bp.route('/report/daily')
def report_daily():
    """每日进货/出货量统计"""
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

    date_range = [start_date + datetime.timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    purchase_dict = {row.order_date: row.total_qty for row in purchase_by_day}
    sales_dict = {row.order_date: row.total_qty for row in sales_by_day}

    chart_dates = [d.strftime('%m-%d') for d in date_range]
    chart_purchase = [purchase_dict.get(d, 0) for d in date_range]
    chart_sales = [sales_dict.get(d, 0) for d in date_range]

    return render_template('report_daily.html',
                           start_date=start_date,
                           end_date=end_date,
                           chart_dates=chart_dates,
                           chart_purchase=chart_purchase,
                           chart_sales=chart_sales)


@reports_bp.route('/report/customer')
def report_customer():
    """客户订货统计：按客户+产品汇总订货量及金额"""
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

    return render_template('report_customer.html',
                           start_date=start_date,
                           end_date=end_date,
                           rows=rows)


@reports_bp.route('/report/supplier')
def report_supplier():
    """产品订购总表：按产品汇总供应商订单的订购总量和总金额"""
    start_date = request.args.get('start_date', '2000-01-01')
    end_date = request.args.get('end_date', datetime.date.today().strftime('%Y-%m-%d'))

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
                           start_date=start_date,
                           end_date=end_date,
                           rows=rows)


@reports_bp.route('/report/inventory')
def report_inventory():
    """当前库存总览"""
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

        if stock < 0:
            alert_products.append({'sku': p.sku or '', 'name': p.name, 'stock': stock})

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


@reports_bp.route('/report/supplier_products')
def report_supplier_products():
    """供应商订单总表（按产品）"""
    start_date = request.args.get('start_date', '2000-01-01')
    end_date = request.args.get('end_date', datetime.date.today().strftime('%Y-%m-%d'))

    query = (SupplierOrderItem
             .select(SupplierOrderItem.product,
                     fn.SUM(SupplierOrderItem.quantity).alias('total_qty'),
                     fn.SUM(SupplierOrderItem.subtotal).alias('total_amount'))
             .join(SupplierOrder)
             .where(SupplierOrder.order_date.between(start_date, end_date))
             .group_by(SupplierOrderItem.product)
             .order_by(fn.SUM(SupplierOrderItem.subtotal).desc()))

    # 分页
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    if per_page not in [10, 20, 50, 100]:
        per_page = 20
    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    items = query.paginate(page, per_page)

    rows = []
    for item in items:
        rows.append({
            'product_name': item.product.name,
            'sku': item.product.sku or '',
            'total_qty': item.total_qty,
            'total_amount': item.total_amount
        })

    return render_template('report_supplier_products.html',
                           rows=rows,
                           start_date=start_date,
                           end_date=end_date,
                           page=page,
                           per_page=per_page,
                           total_pages=total_pages,
                           total=total)

@reports_bp.route('/report/sales_profit')
def report_sales_profit():
    start_date = request.args.get('start_date', '2000-01-01')
    end_date = request.args.get('end_date', datetime.date.today().strftime('%Y-%m-%d'))

    # 1. 计算每个产品的加权平均成本（全局，所有时间）
    product_cost = {}
    cost_query = (PurchaseOrderItem
                  .select(PurchaseOrderItem.product,
                          fn.SUM(PurchaseOrderItem.subtotal).alias('total_cost'),
                          fn.SUM(PurchaseOrderItem.quantity).alias('total_qty'))
                  .group_by(PurchaseOrderItem.product))
    for row in cost_query:
        if row.total_qty and row.total_qty > 0:
            product_cost[row.product_id] = row.total_cost / row.total_qty
        else:
            product_cost[row.product_id] = 0.0

    # 2. 查询选定时间段内的销售明细（按产品汇总）
    sales_query = (SalesOrderItem
                   .select(SalesOrderItem.product,
                           fn.SUM(SalesOrderItem.quantity).alias('sold_qty'),
                           fn.SUM(SalesOrderItem.subtotal).alias('revenue'))
                   .join(SalesOrder)
                   .where(SalesOrder.order_date.between(start_date, end_date))
                   .group_by(SalesOrderItem.product)
                   .order_by(fn.SUM(SalesOrderItem.subtotal).desc()))

    rows = []
    total_profit = 0.0
    for item in sales_query:
        pid = item.product_id
        product = item.product
        sold_qty = item.sold_qty or 0
        revenue = item.revenue or 0.0
        avg_cost = product_cost.get(pid, 0.0)
        cost_total = avg_cost * sold_qty
        profit = revenue - cost_total
        margin = (profit / revenue * 100) if revenue > 0 else 0.0
        total_profit += profit

        rows.append({
            'sku': product.sku or '',
            'name': product.name,
            'unit': product.unit,
            'sold_qty': sold_qty,
            'avg_cost': avg_cost,
            'revenue': revenue,
            'profit': profit,
            'margin': round(margin, 2)
        })

    return render_template('report_sales_profit.html',
                           rows=rows,
                           total_profit=total_profit,
                           start_date=start_date,
                           end_date=end_date)

@reports_bp.route('/report/inventory_trend')
def report_inventory_trend():
    # 默认显示最近30天
    end_date = request.args.get('end_date', datetime.date.today())
    start_date = request.args.get('start_date',
                                  datetime.date.today() - datetime.timedelta(days=29))
    if isinstance(start_date, str):
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()
    if isinstance(end_date, str):
        end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()

    # 1. 计算起始日期之前的总结存（作为初始值）
    initial_stock = 0.0
    products = Product.select()
    for p in products:
        total_in = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .join(PurchaseOrder)
                    .where((PurchaseOrderItem.product == p) & (PurchaseOrder.order_date < start_date))
                    .scalar()) or 0
        total_out = (SalesOrderItem
                     .select(fn.SUM(SalesOrderItem.quantity))
                     .join(SalesOrder)
                     .where((SalesOrderItem.product == p) & (SalesOrder.order_date < start_date))
                     .scalar()) or 0
        initial_stock += (total_in - total_out)

    # 2. 计算日期范围内每天的净变化，然后累计得到每日库存
    # 按天汇总入库数量
    daily_in = (PurchaseOrder
                .select(PurchaseOrder.order_date,
                        fn.SUM(PurchaseOrderItem.quantity).alias('qty'))
                .join(PurchaseOrderItem)
                .where(PurchaseOrder.order_date.between(start_date, end_date))
                .group_by(PurchaseOrder.order_date)
                .order_by(PurchaseOrder.order_date))

    daily_out = (SalesOrder
                 .select(SalesOrder.order_date,
                         fn.SUM(SalesOrderItem.quantity).alias('qty'))
                 .join(SalesOrderItem)
                 .where(SalesOrder.order_date.between(start_date, end_date))
                 .group_by(SalesOrder.order_date)
                 .order_by(SalesOrder.order_date))

    # 转为字典
    in_dict = {row.order_date: row.qty for row in daily_in}
    out_dict = {row.order_date: row.qty for row in daily_out}

    # 构建日期序列
    date_range = [start_date + datetime.timedelta(days=i)
                  for i in range((end_date - start_date).days + 1)]

    chart_labels = []
    chart_data = []
    current_stock = initial_stock
    for d in date_range:
        net_change = in_dict.get(d, 0) - out_dict.get(d, 0)
        current_stock += net_change
        chart_labels.append(d.strftime('%m-%d'))
        chart_data.append(round(current_stock, 2))

    return render_template('report_inventory_trend.html',
                           start_date=start_date,
                           end_date=end_date,
                           chart_labels=chart_labels,
                           chart_data=chart_data,
                           initial_stock=initial_stock,
                           current_stock=current_stock)