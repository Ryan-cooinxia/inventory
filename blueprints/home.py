from flask import Blueprint, render_template
from models import *
from peewee import fn
import datetime

home_bp = Blueprint('home', __name__)

@home_bp.route('/')
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

    # 供货商退款总额（暂无）
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