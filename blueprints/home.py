# blueprints/home.py
from flask import Blueprint, render_template, request
from flask_login import login_required, current_user      # 新增
from models import *
from peewee import fn
import datetime

home_bp = Blueprint('home', __name__)

@home_bp.route('/')
@login_required                                           # 新增
def index():
    today = datetime.date.today()

    # 对账时段（默认今天）
    start_str = request.args.get('reconcile_start', '')
    end_str = request.args.get('reconcile_end', '')
    if start_str:
        try:
            reconcile_start = datetime.datetime.strptime(start_str, '%Y-%m-%d').date()
        except ValueError:
            reconcile_start = today
    else:
        reconcile_start = today
    if end_str:
        try:
            reconcile_end = datetime.datetime.strptime(end_str, '%Y-%m-%d').date()
        except ValueError:
            reconcile_end = today
    else:
        reconcile_end = today

    # 所有查询均添加 .where(模型.user == current_user)
    supplier_order_total = (SupplierOrder
                            .select(fn.SUM(SupplierOrder.total_amount))
                            .where(SupplierOrder.user == current_user)
                            .scalar()) or 0

    supplier_received_amount = (PurchaseOrder
                                .select(fn.SUM(PurchaseOrder.total_amount))
                                .where(PurchaseOrder.supplier_order.is_null(False) &
                                       (PurchaseOrder.user == current_user))
                                .scalar()) or 0
    supplier_unreceived_total = max(supplier_order_total - supplier_received_amount, 0)

    customer_order_total = (CustomerOrder
                            .select(fn.SUM(CustomerOrder.total_amount))
                            .where(CustomerOrder.user == current_user)
                            .scalar()) or 0

    customer_shipped_amount = (SalesOrder
                               .select(fn.SUM(SalesOrder.total_amount))
                               .where(SalesOrder.customer_order.is_null(False) &
                                      (SalesOrder.user == current_user))
                               .scalar()) or 0
    customer_unshipped_total = max(customer_order_total - customer_shipped_amount, 0)

    # 客户预计退款总和（所有客户的 planned_refund 字段之和）
    planned_refund_sum = (Customer
                         .select(fn.SUM(Customer.planned_refund))
                         .where(Customer.user == current_user)
                         .scalar()) or 0

    # 客户实际退款总和（所有退款记录的金额之和）
    actual_refund_sum = (CustomerRefund
                        .select(fn.SUM(CustomerRefund.amount))
                        .where(CustomerRefund.user == current_user)
                        .scalar()) or 0

    # 客户退款总余额 = 预计退款 - 实际退款
    customer_refund_balance = planned_refund_sum - actual_refund_sum

    supplier_refund_total = 0.0

    # 时段内入库
    purchase_orders = (PurchaseOrder
                       .select()
                       .where(PurchaseOrder.order_date.between(reconcile_start, reconcile_end),
                              PurchaseOrder.user == current_user)
                       .order_by(PurchaseOrder.id.desc()))
    purchase_list = []
    for po in purchase_orders:
        items = list(po.items)
        product_names = ', '.join([item.product.name for item in items])
        purchase_list.append({
            'id': po.id,
            'supplier': po.supplier.name,
            'total_amount': po.total_amount,
            'product_names': product_names,
            'order_date': po.order_date
        })

    # 时段内出库
    sales_orders = (SalesOrder
                    .select()
                    .where(SalesOrder.order_date.between(reconcile_start, reconcile_end),
                           SalesOrder.user == current_user)
                    .order_by(SalesOrder.id.desc()))
    sales_list = []
    for so in sales_orders:
        items = list(so.items)
        product_names = ', '.join([item.product.name for item in items])
        sales_list.append({
            'id': so.id,
            'customer': so.customer.name,
            'total_amount': so.total_amount,
            'product_names': product_names,
            'order_date': so.order_date
        })

    # 库存（仅当前用户的产品）
    products = Product.select().where(Product.user == current_user)
    inventory_list = []
    negative_stock_products = []
    for p in products:
        total_in = (PurchaseOrderItem
                    .select(fn.SUM(PurchaseOrderItem.quantity))
                    .join(PurchaseOrder)
                    .where((PurchaseOrderItem.product == p) & (PurchaseOrder.user == current_user))
                    .scalar()) or 0
        total_out = (SalesOrderItem
                     .select(fn.SUM(SalesOrderItem.quantity))
                     .join(SalesOrder)
                     .where((SalesOrderItem.product == p) & (SalesOrder.user == current_user))
                     .scalar()) or 0
        stock = total_in - total_out

        if stock < 0:
            negative_stock_products.append({'sku': p.sku or '', 'name': p.name, 'stock': stock})

        if stock > 0:
            purchase_data = (PurchaseOrderItem
                             .select(fn.SUM(PurchaseOrderItem.subtotal).alias('total_cost'),
                                     fn.SUM(PurchaseOrderItem.quantity).alias('total_qty'))
                             .join(PurchaseOrder)
                             .where((PurchaseOrderItem.product == p) & (PurchaseOrder.user == current_user))
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

    # ── 对账明细：全部供应商订单按产品聚合，按时段拆分收货 ──
    order_filter = (SupplierOrder.user == current_user)
    so_query = (SupplierOrderItem
                .select(SupplierOrderItem.product,
                        fn.SUM(SupplierOrderItem.quantity).alias('total_qty'),
                        fn.SUM(SupplierOrderItem.subtotal).alias('total_amount'))
                .join(SupplierOrder)
                .where(order_filter)
                .group_by(SupplierOrderItem.product)
                .order_by(fn.SUM(SupplierOrderItem.subtotal).desc()))

    reconcile_rows = []
    total_ordered_value = 0.0
    total_received_value = 0.0
    total_received_in_period_value = 0.0

    for item in so_query:
        product = item.product
        total_qty = item.total_qty
        total_amount = item.total_amount

        # 相关供应商订单 ID
        so_ids = (SupplierOrderItem
                  .select(SupplierOrderItem.order)
                  .join(SupplierOrder)
                  .where((SupplierOrderItem.product == product) & order_filter))

        # 累计已到货
        received_total = (PurchaseOrderItem
                         .select(fn.SUM(PurchaseOrderItem.quantity))
                         .join(PurchaseOrder)
                         .where((PurchaseOrder.supplier_order.in_(so_ids)) &
                                (PurchaseOrderItem.product == product) &
                                (PurchaseOrder.user == current_user))
                         .scalar()) or 0

        pending_total = total_qty - received_total
        received_in_period = 0
        pending_before = 0

        # 按时段拆分
        received_before = (PurchaseOrderItem
                          .select(fn.SUM(PurchaseOrderItem.quantity))
                          .join(PurchaseOrder)
                          .where((PurchaseOrder.supplier_order.in_(so_ids)) &
                                 (PurchaseOrderItem.product == product) &
                                 (PurchaseOrder.user == current_user) &
                                 (PurchaseOrder.order_date < reconcile_start))
                          .scalar()) or 0
        received_up_to_end = (PurchaseOrderItem
                              .select(fn.SUM(PurchaseOrderItem.quantity))
                              .join(PurchaseOrder)
                              .where((PurchaseOrder.supplier_order.in_(so_ids)) &
                                     (PurchaseOrderItem.product == product) &
                                     (PurchaseOrder.user == current_user) &
                                     (PurchaseOrder.order_date <= reconcile_end))
                              .scalar()) or 0
        received_in_period = received_up_to_end - received_before
        pending_before = total_qty - received_before

        received_value = received_total * (total_amount / total_qty) if total_qty > 0 else 0
        received_in_period_value = received_in_period * (total_amount / total_qty) if total_qty > 0 else 0

        reconcile_rows.append({
            'product': product.name,
            'sku': product.sku or '',
            'unit': product.unit,
            'total_qty': total_qty,
            'total_amount': total_amount,
            'received_total': received_total,
            'pending_total': pending_total,
            'received_in_period': received_in_period,
            'pending_before': pending_before,
        })
        total_ordered_value += total_amount
        total_received_value += received_value
        total_received_in_period_value += received_in_period_value

    # ── 客户订货对账表：按产品聚合，按时段拆分发货 ──
    customer_rows = []
    total_customer_ordered_value = 0.0
    total_customer_shipped_value = 0.0
    total_customer_shipped_in_period_value = 0.0

    co_filter = (CustomerOrder.user == current_user)
    co_query = (CustomerOrderItem
                .select(CustomerOrderItem.product,
                        fn.SUM(CustomerOrderItem.quantity).alias('total_qty'),
                        fn.SUM(CustomerOrderItem.subtotal).alias('total_amount'))
                .join(CustomerOrder)
                .where(co_filter)
                .group_by(CustomerOrderItem.product)
                .order_by(fn.SUM(CustomerOrderItem.subtotal).desc()))

    for item in co_query:
        product = item.product
        total_qty = item.total_qty
        total_amount = item.total_amount

        co_ids = (CustomerOrderItem
                  .select(CustomerOrderItem.order)
                  .join(CustomerOrder)
                  .where((CustomerOrderItem.product == product) & co_filter))

        shipped_total = (SalesOrderItem
                        .select(fn.SUM(SalesOrderItem.quantity))
                        .join(SalesOrder)
                        .where((SalesOrder.customer_order.in_(co_ids)) &
                               (SalesOrderItem.product == product) &
                               (SalesOrder.user == current_user))
                        .scalar()) or 0

        shipped_in_period = 0
        shipped_before_total = 0

        shipped_before = (SalesOrderItem
                         .select(fn.SUM(SalesOrderItem.quantity))
                         .join(SalesOrder)
                         .where((SalesOrder.customer_order.in_(co_ids)) &
                                (SalesOrderItem.product == product) &
                                (SalesOrder.user == current_user) &
                                (SalesOrder.order_date < reconcile_start))
                         .scalar()) or 0
        shipped_up_to_end = (SalesOrderItem
                             .select(fn.SUM(SalesOrderItem.quantity))
                             .join(SalesOrder)
                             .where((SalesOrder.customer_order.in_(co_ids)) &
                                    (SalesOrderItem.product == product) &
                                    (SalesOrder.user == current_user) &
                                    (SalesOrder.order_date <= reconcile_end))
                             .scalar()) or 0
        shipped_in_period = shipped_up_to_end - shipped_before
        pending_total = total_qty - shipped_total
        shipped_before_total += shipped_before

        shipped_value = shipped_total * (total_amount / total_qty) if total_qty > 0 else 0
        shipped_in_period_value = shipped_in_period * (total_amount / total_qty) if total_qty > 0 else 0

        customer_rows.append({
            'product': product.name,
            'sku': product.sku or '',
            'unit': product.unit,
            'total_qty': total_qty,
            'total_amount': total_amount,
            'shipped_total': shipped_total,
            'pending_total': pending_total,
            'shipped_in_period': shipped_in_period,
        })
        total_customer_ordered_value += total_amount
        total_customer_shipped_value += shipped_value
        total_customer_shipped_in_period_value += shipped_in_period_value

    # ── 仓库订货对账表：供货商订购 - 客户订货 ──
    warehouse_rows = []
    # 收集所有产品及其供需量
    product_supply = {}   # product_id -> supplier ordered qty
    product_demand = {}   # product_id -> customer ordered qty
    all_product_ids = set()

    sup_q = (SupplierOrderItem
             .select(SupplierOrderItem.product, fn.SUM(SupplierOrderItem.quantity).alias('qty'))
             .join(SupplierOrder)
             .where(SupplierOrder.user == current_user)
             .group_by(SupplierOrderItem.product))
    for r in sup_q:
        product_supply[r.product.id] = r.qty
        all_product_ids.add(r.product.id)

    cus_q = (CustomerOrderItem
             .select(CustomerOrderItem.product, fn.SUM(CustomerOrderItem.quantity).alias('qty'))
             .join(CustomerOrder)
             .where(CustomerOrder.user == current_user)
             .group_by(CustomerOrderItem.product))
    for r in cus_q:
        product_demand[r.product.id] = r.qty
        all_product_ids.add(r.product.id)

    for pid in all_product_ids:
        sup_qty = product_supply.get(pid, 0)
        cus_qty = product_demand.get(pid, 0)
        diff = sup_qty - cus_qty
        product = Product.get_by_id(pid)
        warehouse_rows.append({
            'product': product.name,
            'sku': product.sku or '',
            'unit': product.unit,
            'supply_qty': sup_qty,
            'demand_qty': cus_qty,
            'diff': diff,
        })
    warehouse_rows.sort(key=lambda r: r['diff'])  # 最缺的排最前

    return render_template('index.html',
                           today=today,
                           reconcile_start=reconcile_start,
                           reconcile_end=reconcile_end,
                           supplier_unreceived_total=supplier_unreceived_total,
                           customer_unshipped_total=customer_unshipped_total,
                           customer_refund_balance=customer_refund_balance,
                           supplier_refund_total=supplier_refund_total,
                           purchase_list=purchase_list,
                           sales_list=sales_list,
                           inventory_list=inventory_list,
                           negative_stock_products=negative_stock_products,
                           reconcile_rows=reconcile_rows,
                           total_ordered_value=total_ordered_value,
                           total_received_value=total_received_value,
                           total_received_in_period_value=total_received_in_period_value,
                           customer_rows=customer_rows,
                           total_customer_ordered_value=total_customer_ordered_value,
                           total_customer_shipped_value=total_customer_shipped_value,
                           total_customer_shipped_in_period_value=total_customer_shipped_in_period_value,
                           warehouse_rows=warehouse_rows)