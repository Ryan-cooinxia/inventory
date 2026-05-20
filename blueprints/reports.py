# blueprints/reports.py
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
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
@login_required
def report_daily():
    end_date = request.args.get('end_date', datetime.date.today())
    start_date = request.args.get('start_date', datetime.date.today() - datetime.timedelta(days=6))
    if isinstance(start_date, str):
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()
    if isinstance(end_date, str):
        end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()

    purchase_by_day = (PurchaseOrder
                       .select(PurchaseOrder.order_date,
                               fn.SUM(PurchaseOrderItem.quantity).alias('total_qty'))
                       .join(PurchaseOrderItem)
                       .where((PurchaseOrder.order_date.between(start_date, end_date)) &
                              (PurchaseOrder.user == current_user))
                       .group_by(PurchaseOrder.order_date)
                       .order_by(PurchaseOrder.order_date))

    sales_by_day = (SalesOrder
                    .select(SalesOrder.order_date,
                            fn.SUM(SalesOrderItem.quantity).alias('total_qty'))
                    .join(SalesOrderItem)
                    .where((SalesOrder.order_date.between(start_date, end_date)) &
                           (SalesOrder.user == current_user))
                    .group_by(SalesOrder.order_date)
                    .order_by(SalesOrder.order_date))

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


@reports_bp.route('/report/customer')
@login_required
def report_customer():
    start_date = request.args.get('start_date', '2000-01-01')
    end_date = request.args.get('end_date', datetime.date.today())

    query = (CustomerOrderItem
             .select(CustomerOrder.customer, CustomerOrderItem.product,
                     fn.SUM(CustomerOrderItem.quantity).alias('total_qty'),
                     fn.SUM(CustomerOrderItem.subtotal).alias('total_amount'))
             .join(CustomerOrder)
             .where((CustomerOrder.order_date.between(start_date, end_date)) &
                    (CustomerOrder.user == current_user))
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
                           start_date=start_date, end_date=end_date,
                           rows=rows)


@reports_bp.route('/report/supplier')
@login_required
def report_supplier():
    start_date = request.args.get('start_date', '2000-01-01')
    end_date = request.args.get('end_date', datetime.date.today().strftime('%Y-%m-%d'))

    query = (SupplierOrderItem
             .select(SupplierOrderItem.product,
                     fn.SUM(SupplierOrderItem.quantity).alias('total_qty'),
                     fn.SUM(SupplierOrderItem.subtotal).alias('total_amount'))
             .join(SupplierOrder)
             .where((SupplierOrder.order_date.between(start_date, end_date)) &
                    (SupplierOrder.user == current_user))
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
                           start_date=start_date, end_date=end_date,
                           rows=rows)


@reports_bp.route('/report/inventory')
@login_required
def report_inventory():
    products = Product.select().where(Product.user == current_user)
    rows = []
    total_value = 0.0
    alert_products = []

    for p in products:
        total_in = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .join(PurchaseOrder)
                    .where((PurchaseOrderItem.product == p) &
                           (PurchaseOrder.user == current_user))
                    .scalar()) or 0
        total_out = (SalesOrderItem
                     .select(fn.SUM(SalesOrderItem.quantity))
                     .join(SalesOrder)
                     .where((SalesOrderItem.product == p) &
                            (SalesOrder.user == current_user))
                     .scalar()) or 0
        stock = total_in - total_out

        if stock < 0:
            alert_products.append({'sku': p.sku or '', 'name': p.name, 'stock': stock})

        if stock != 0:
            purchase_data = (PurchaseOrderItem
                             .select(fn.SUM(PurchaseOrderItem.subtotal).alias('total_cost'),
                                     fn.SUM(PurchaseOrderItem.quantity).alias('total_qty'))
                             .join(PurchaseOrder)
                             .where((PurchaseOrderItem.product == p) &
                                    (PurchaseOrder.user == current_user))
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


@reports_bp.route('/report/sales_profit')
@login_required
def report_sales_profit():
    start_date = request.args.get('start_date', '2000-01-01')
    end_date = request.args.get('end_date', datetime.date.today().strftime('%Y-%m-%d'))

    # 加权平均成本（基于当前用户的所有采购）
    product_cost = {}
    cost_query = (PurchaseOrderItem
                  .select(PurchaseOrderItem.product,
                          fn.SUM(PurchaseOrderItem.subtotal).alias('total_cost'),
                          fn.SUM(PurchaseOrderItem.quantity).alias('total_qty'))
                  .join(PurchaseOrder)
                  .where(PurchaseOrder.user == current_user)
                  .group_by(PurchaseOrderItem.product))
    for row in cost_query:
        if row.total_qty and row.total_qty > 0:
            product_cost[row.product_id] = row.total_cost / row.total_qty
        else:
            product_cost[row.product_id] = 0.0

    # 选定时间段的销售
    sales_query = (SalesOrderItem
                   .select(SalesOrderItem.product,
                           fn.SUM(SalesOrderItem.quantity).alias('sold_qty'),
                           fn.SUM(SalesOrderItem.subtotal).alias('revenue'))
                   .join(SalesOrder)
                   .where((SalesOrder.order_date.between(start_date, end_date)) &
                          (SalesOrder.user == current_user))
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
@login_required
def report_inventory_trend():
    end_date = request.args.get('end_date', datetime.date.today())
    start_date = request.args.get('start_date',
                                  datetime.date.today() - datetime.timedelta(days=29))
    if isinstance(start_date, str):
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()
    if isinstance(end_date, str):
        end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()

    # 初始库存（start_date之前）
    initial_stock = 0.0
    products = Product.select().where(Product.user == current_user)
    for p in products:
        total_in = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .join(PurchaseOrder)
                    .where((PurchaseOrderItem.product == p) &
                           (PurchaseOrder.order_date < start_date) &
                           (PurchaseOrder.user == current_user))
                    .scalar()) or 0
        total_out = (SalesOrderItem
                     .select(fn.SUM(SalesOrderItem.quantity))
                     .join(SalesOrder)
                     .where((SalesOrderItem.product == p) &
                            (SalesOrder.order_date < start_date) &
                            (SalesOrder.user == current_user))
                     .scalar()) or 0
        initial_stock += (total_in - total_out)

    # 日期范围内的每日净变化
    daily_in = (PurchaseOrder
                .select(PurchaseOrder.order_date,
                        fn.SUM(PurchaseOrderItem.quantity).alias('qty'))
                .join(PurchaseOrderItem)
                .where((PurchaseOrder.order_date.between(start_date, end_date)) &
                       (PurchaseOrder.user == current_user))
                .group_by(PurchaseOrder.order_date)
                .order_by(PurchaseOrder.order_date))

    daily_out = (SalesOrder
                 .select(SalesOrder.order_date,
                         fn.SUM(SalesOrderItem.quantity).alias('qty'))
                 .join(SalesOrderItem)
                 .where((SalesOrder.order_date.between(start_date, end_date)) &
                        (SalesOrder.user == current_user))
                 .group_by(SalesOrder.order_date)
                 .order_by(SalesOrder.order_date))

    in_dict = {row.order_date: row.qty for row in daily_in}
    out_dict = {row.order_date: row.qty for row in daily_out}

    date_range = [start_date + datetime.timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    chart_labels = []
    chart_data = []
    current_stock = initial_stock
    for d in date_range:
        net_change = in_dict.get(d, 0) - out_dict.get(d, 0)
        current_stock += net_change
        chart_labels.append(d.strftime('%m-%d'))
        chart_data.append(round(current_stock, 2))

    return render_template('report_inventory_trend.html',
                           start_date=start_date, end_date=end_date,
                           chart_labels=chart_labels,
                           chart_data=chart_data,
                           initial_stock=initial_stock,
                           current_stock=current_stock)