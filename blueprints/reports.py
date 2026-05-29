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
    """每日进出货统计（含总量、货值、按产品）"""
    # ---------- 1. 确定日期范围 ----------
    # 计算当前用户最早的出入库日期
    earliest_purchase = (PurchaseOrder
                         .select(fn.MIN(PurchaseOrder.order_date))
                         .where(PurchaseOrder.user == current_user)
                         .scalar())
    earliest_sales = (SalesOrder
                      .select(fn.MIN(SalesOrder.order_date))
                      .where(SalesOrder.user == current_user)
                      .scalar())
    # 取两者中最早的日期
    candidates = [d for d in (earliest_purchase, earliest_sales) if d is not None]
    if candidates:
        earliest_data_date = min(candidates)
    else:
        earliest_data_date = datetime.date.today()

    # 默认结束日期为今天
    end_date = request.args.get('end_date', datetime.date.today())
    if isinstance(end_date, str):
        end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()

    # 默认开始日期：最早数据日或30天前，两者中较晚的（保证至少显示最近30天，但不超过最早数据日）
    default_start = max(earliest_data_date, datetime.date.today() - datetime.timedelta(days=29))
    start_date = request.args.get('start_date', default_start.strftime('%Y-%m-%d'))
    if isinstance(start_date, str):
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()

    # 确保开始日期不晚于结束日期
    if start_date > end_date:
        start_date = end_date

    # 生成日期列表（用于图表和表格）
    date_range = [start_date + datetime.timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    chart_dates = [d.strftime('%m-%d') for d in date_range]

    # ---------- 2. 总量图表数据 ----------
    def sum_dict_by_date(query, key='total_qty'):
        d = {}
        for row in query:
            d[row.order_date] = getattr(row, key)
        return d

    purchase_qty_total = (PurchaseOrder
                          .select(PurchaseOrder.order_date,
                                  fn.SUM(PurchaseOrderItem.quantity).alias('total_qty'))
                          .join(PurchaseOrderItem)
                          .where((PurchaseOrder.order_date.between(start_date, end_date)) &
                                 (PurchaseOrder.user == current_user))
                          .group_by(PurchaseOrder.order_date)
                          .order_by(PurchaseOrder.order_date))

    sales_qty_total = (SalesOrder
                       .select(SalesOrder.order_date,
                               fn.SUM(SalesOrderItem.quantity).alias('total_qty'))
                       .join(SalesOrderItem)
                       .where((SalesOrder.order_date.between(start_date, end_date)) &
                              (SalesOrder.user == current_user))
                       .group_by(SalesOrder.order_date)
                       .order_by(SalesOrder.order_date))

    purchase_amount_total = (PurchaseOrder
                             .select(PurchaseOrder.order_date,
                                     fn.SUM(PurchaseOrderItem.subtotal).alias('total_amount'))
                             .join(PurchaseOrderItem)
                             .where((PurchaseOrder.order_date.between(start_date, end_date)) &
                                    (PurchaseOrder.user == current_user))
                             .group_by(PurchaseOrder.order_date)
                             .order_by(PurchaseOrder.order_date))

    sales_amount_total = (SalesOrder
                          .select(SalesOrder.order_date,
                                  fn.SUM(SalesOrderItem.subtotal).alias('total_amount'))
                          .join(SalesOrderItem)
                          .where((SalesOrder.order_date.between(start_date, end_date)) &
                                 (SalesOrder.user == current_user))
                          .group_by(SalesOrder.order_date)
                          .order_by(SalesOrder.order_date))

    purchase_qty_dict = sum_dict_by_date(purchase_qty_total)
    sales_qty_dict = sum_dict_by_date(sales_qty_total)
    purchase_amount_dict = sum_dict_by_date(purchase_amount_total, key='total_amount')
    sales_amount_dict = sum_dict_by_date(sales_amount_total, key='total_amount')

    # ---------- 3. 明细数据（表格用） ----------
    purchase_detail = list((PurchaseOrderItem
                            .select(PurchaseOrder.order_date.alias('order_date'),
                                    PurchaseOrderItem.product.alias('product_id'),
                                    Product.name.alias('product_name'),
                                    fn.SUM(PurchaseOrderItem.quantity).alias('qty'),
                                    fn.SUM(PurchaseOrderItem.subtotal).alias('amount'))
                            .join(PurchaseOrder)
                            .join(Product, on=(PurchaseOrderItem.product == Product.id))
                            .where((PurchaseOrder.order_date.between(start_date, end_date)) &
                                   (PurchaseOrder.user == current_user))
                            .group_by(PurchaseOrder.order_date, PurchaseOrderItem.product, Product.name)
                            .order_by(PurchaseOrder.order_date))
                           .dicts())

    sales_detail = list((SalesOrderItem
                         .select(SalesOrder.order_date.alias('order_date'),
                                 SalesOrderItem.product.alias('product_id'),
                                 Product.name.alias('product_name'),
                                 fn.SUM(SalesOrderItem.quantity).alias('qty'),
                                 fn.SUM(SalesOrderItem.subtotal).alias('amount'))
                         .join(SalesOrder)
                         .join(Product, on=(SalesOrderItem.product == Product.id))
                         .where((SalesOrder.order_date.between(start_date, end_date)) &
                                (SalesOrder.user == current_user))
                         .group_by(SalesOrder.order_date, SalesOrderItem.product, Product.name)
                         .order_by(SalesOrder.order_date))
                        .dicts())

    daily_details = {d: {} for d in date_range}
    for row in purchase_detail:
        d = row['order_date']
        pid = row['product_id']
        name = row['product_name']
        if pid not in daily_details[d]:
            daily_details[d][pid] = {'name': name, 'in_qty': 0, 'in_amount': 0, 'out_qty': 0, 'out_amount': 0}
        daily_details[d][pid]['in_qty'] += row['qty']
        daily_details[d][pid]['in_amount'] += row['amount']

    for row in sales_detail:
        d = row['order_date']
        pid = row['product_id']
        name = row['product_name']
        if pid not in daily_details[d]:
            daily_details[d][pid] = {'name': name, 'in_qty': 0, 'in_amount': 0, 'out_qty': 0, 'out_amount': 0}
        daily_details[d][pid]['out_qty'] += row['qty']
        daily_details[d][pid]['out_amount'] += row['amount']

    detail_rows = []
    for d in date_range:
        for pid, data in daily_details[d].items():
            detail_rows.append({
                'date': d,
                'product_name': data['name'],
                'in_qty': data['in_qty'],
                'in_amount': data['in_amount'],
                'out_qty': data['out_qty'],
                'out_amount': data['out_amount']
            })

    # ---------- 4. 按产品图表数据 ----------
    product_data = {}
    for row in purchase_detail:
        name = row['product_name']
        d = row['order_date']
        if name not in product_data:
            product_data[name] = {date: {'in_qty': 0, 'out_qty': 0, 'in_amount': 0, 'out_amount': 0} for date in date_range}
        product_data[name][d]['in_qty'] += row['qty']
        product_data[name][d]['in_amount'] += row['amount']

    for row in sales_detail:
        name = row['product_name']
        d = row['order_date']
        if name not in product_data:
            product_data[name] = {date: {'in_qty': 0, 'out_qty': 0, 'in_amount': 0, 'out_amount': 0} for date in date_range}
        product_data[name][d]['out_qty'] += row['qty']
        product_data[name][d]['out_amount'] += row['amount']

    product_list = sorted(product_data.keys())
    product_chart_data = {}
    for name, dates_data in product_data.items():
        product_chart_data[name] = {
            'labels': chart_dates,
            'in_qty': [dates_data[d]['in_qty'] for d in date_range],
            'out_qty': [dates_data[d]['out_qty'] for d in date_range],
            'in_amount': [dates_data[d]['in_amount'] for d in date_range],
            'out_amount': [dates_data[d]['out_amount'] for d in date_range]
        }

    purchase_qty_list = [purchase_qty_dict.get(d, 0) for d in date_range]
    sales_qty_list = [sales_qty_dict.get(d, 0) for d in date_range]
    purchase_amount_list = [purchase_amount_dict.get(d, 0) for d in date_range]
    sales_amount_list = [sales_amount_dict.get(d, 0) for d in date_range]

    return render_template('report_daily.html',
                           start_date=start_date, end_date=end_date,
                           chart_dates=chart_dates,
                           purchase_qty_list=purchase_qty_list,
                           sales_qty_list=sales_qty_list,
                           purchase_amount_list=purchase_amount_list,
                           sales_amount_list=sales_amount_list,
                           product_list=product_list,
                           product_chart_data=product_chart_data,
                           detail_rows=detail_rows)

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

@reports_bp.route('/report/inventory_period')
@login_required
def report_inventory_period():
    end_date = request.args.get('end_date', datetime.date.today())
    start_date = request.args.get('start_date', datetime.date.today() - datetime.timedelta(days=30))

    if isinstance(start_date, str):
        start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()
    if isinstance(end_date, str):
        end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()

    # ---------- 加权平均成本（全局） ----------
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

    # ---------- 时间段内到货（采购） ----------
    purchase_data = (PurchaseOrderItem
                     .select(PurchaseOrderItem.product,
                             Product.name.alias('product_name'),
                             fn.SUM(PurchaseOrderItem.quantity).alias('in_qty'),
                             fn.SUM(PurchaseOrderItem.subtotal).alias('in_amount'))
                     .join(PurchaseOrder)
                     .join(Product, on=(PurchaseOrderItem.product == Product.id))
                     .where((PurchaseOrder.order_date.between(start_date, end_date)) &
                            (PurchaseOrder.user == current_user))
                     .group_by(PurchaseOrderItem.product, Product.name)
                     .dicts())

    # ---------- 时间段内出货（销售） ----------
    sales_data = (SalesOrderItem
                  .select(SalesOrderItem.product,
                          Product.name.alias('product_name'),
                          fn.SUM(SalesOrderItem.quantity).alias('out_qty'),
                          fn.SUM(SalesOrderItem.subtotal).alias('out_amount'))
                  .join(SalesOrder)
                  .join(Product, on=(SalesOrderItem.product == Product.id))
                  .where((SalesOrder.order_date.between(start_date, end_date)) &
                         (SalesOrder.user == current_user))
                  .group_by(SalesOrderItem.product, Product.name)
                  .dicts())

    # 合并数据
    products = {}
    for row in purchase_data:
        pid = row['product']
        name = row['product_name']
        products[pid] = {
            'name': name,
            'in_qty': row['in_qty'] or 0,
            'in_amount': row['in_amount'] or 0.0,
            'out_qty': 0,
            'out_amount': 0.0
        }
    for row in sales_data:
        pid = row['product']
        name = row['product_name']
        if pid not in products:
            products[pid] = {
                'name': name,
                'in_qty': 0,
                'in_amount': 0.0,
                'out_qty': 0,
                'out_amount': 0.0
            }
        products[pid]['out_qty'] = row['out_qty'] or 0
        products[pid]['out_amount'] = row['out_amount'] or 0.0

    # 计算毛利和毛利率
    rows = []
    total_in_qty = 0
    total_in_amount = 0.0
    total_out_qty = 0
    total_out_amount = 0.0

    for pid, data in products.items():
        in_qty = data['in_qty']
        in_amount = data['in_amount']
        out_qty = data['out_qty']
        out_amount = data['out_amount']
        avg_cost = product_cost.get(pid, 0.0)
        cost_total = avg_cost * out_qty
        profit = out_amount - cost_total
        margin = (profit / out_amount * 100) if out_amount > 0 else 0.0

        rows.append({
            'product_name': data['name'],
            'in_qty': in_qty,
            'in_amount': in_amount,
            'out_qty': out_qty,
            'out_amount': out_amount,
            'profit': profit,
            'margin': round(margin, 2)
        })

        total_in_qty += in_qty
        total_in_amount += in_amount
        total_out_qty += out_qty
        total_out_amount += out_amount
    total_profit = sum(row['profit'] for row in rows)
    total_margin = (total_profit / total_out_amount * 100) if total_out_amount > 0 else 0.0

    return render_template('report_inventory_period.html',
                           start_date=start_date,
                           end_date=end_date,
                           rows=rows,
                           total_in_qty=total_in_qty,
                           total_in_amount=total_in_amount,
                           total_out_qty=total_out_qty,
                           total_out_amount=total_out_amount,
                           total_profit=total_profit,
                           total_margin=round(total_margin, 2))