# blueprints/home.py
from flask import Blueprint, render_template
from flask_login import login_required, current_user      # 新增
from models import *
from peewee import fn
import datetime

home_bp = Blueprint('home', __name__)

@home_bp.route('/')
@login_required                                           # 新增
def index():
    today = datetime.date.today()

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

    # 当日入库
    today_purchase_orders = (PurchaseOrder
                             .select()
                             .where(PurchaseOrder.order_date == today,
                                    PurchaseOrder.user == current_user)
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

    # 当日出库
    today_sales_orders = (SalesOrder
                          .select()
                          .where(SalesOrder.order_date == today,
                                 SalesOrder.user == current_user)
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

    return render_template('index.html',
                           today=today,
                           supplier_unreceived_total=supplier_unreceived_total,
                           customer_unshipped_total=customer_unshipped_total,
                           customer_refund_balance=customer_refund_balance,
                           supplier_refund_total=supplier_refund_total,
                           purchase_list=purchase_list,
                           sales_list=sales_list,
                           inventory_list=inventory_list,
                           negative_stock_products=negative_stock_products)