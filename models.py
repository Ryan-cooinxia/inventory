from peewee import *
import datetime

db = SqliteDatabase('data.db')

class BaseModel(Model):
    class Meta:
        database = db

class Product(BaseModel):
    sku = CharField(max_length=50, unique=True, null=True)
    brand = CharField(max_length=50, null=True, default='DJI')
    category1 = CharField(max_length=50, null=True)
    category2 = CharField(max_length=50, null=True)
    name = CharField()
    spec = CharField(null=True)
    unit = CharField()

class Customer(BaseModel):
    name = CharField()
    contact = CharField(null=True)
    phone = CharField(null=True)
    address = TextField(null=True)

class Supplier(BaseModel):
    name = CharField()
    contact = CharField(null=True)
    phone = CharField(null=True)

class SupplierOrder(BaseModel):
    supplier = ForeignKeyField(Supplier, backref='supplier_orders')
    order_number = CharField(max_length=50, null=True)  # 新增
    order_date = DateField(default=datetime.date.today)
    total_amount = FloatField(default=0)
    status = CharField(default='pending')
    estimated_delivery = DateField(null=True)
    remark = TextField(null=True)

class SupplierOrderItem(BaseModel):
    """供应商订单明细"""
    order = ForeignKeyField(SupplierOrder, backref='items')
    product = ForeignKeyField(Product)
    quantity = FloatField()
    unit_price = FloatField()
    subtotal = FloatField()
    
class PurchaseOrder(BaseModel):
    supplier = ForeignKeyField(Supplier, backref='purchase_orders')
    order_date = DateField(default=datetime.date.today)
    total_amount = FloatField(default=0)
    remark = TextField(null=True)
    supplier_order = ForeignKeyField(SupplierOrder, null=True, backref='purchase_receipts')
    ship_method = CharField(max_length=50, null=True)
    tracking_number = CharField(max_length=100, null=True)

class PurchaseOrderItem(BaseModel):
    order = ForeignKeyField(PurchaseOrder, backref='items')
    product = ForeignKeyField(Product)
    quantity = FloatField()
    unit_price = FloatField()
    subtotal = FloatField()

class CustomerOrder(BaseModel):
    """客户订单"""
    customer = ForeignKeyField(Customer, backref='customer_orders')
    order_date = DateField(default=datetime.date.today)
    total_amount = FloatField(default=0)
    status = CharField(default='pending')
    remark = TextField(null=True)
    invoice_required = BooleanField(default=False)

class CustomerOrderItem(BaseModel):
    order = ForeignKeyField(CustomerOrder, backref='items')
    product = ForeignKeyField(Product)
    quantity = FloatField()
    unit_price = FloatField()
    subtotal = FloatField()

class SalesOrder(BaseModel):
    """出库单（可关联到订单）"""
    customer = ForeignKeyField(Customer, backref='sales_orders')
    customer_order = ForeignKeyField(CustomerOrder, null=True, backref='shipments')
    order_date = DateField(default=datetime.date.today)
    total_amount = FloatField(default=0)
    remark = TextField(null=True)
    ship_method = CharField(max_length=50, null=True)
    tracking_number = CharField(max_length=100, null=True)

class SalesOrderItem(BaseModel):
    order = ForeignKeyField(SalesOrder, backref='items')
    product = ForeignKeyField(Product)
    quantity = FloatField()
    unit_price = FloatField()
    subtotal = FloatField()

class CustomerRefund(BaseModel):
    customer = ForeignKeyField(Customer, backref='refunds')
    sales_order = ForeignKeyField(SalesOrder, null=True, backref='refunds')
    customer_order = ForeignKeyField(CustomerOrder, null=True, backref='refunds')  # 可关联到订单
    refund_date = DateField(default=datetime.date.today)
    amount = FloatField()
    remark = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

class CustomerTransaction(BaseModel):
    TRANSACTION_TYPES = [
        ('order', '订单款'),
        ('refund', '退款'),
        ('loan', '借款'),
        ('repay', '还款'),
        ('penalty', '违约金'),
        ('other', '其他'),
    ]

    customer = ForeignKeyField(Customer, backref='transactions')
    transaction_date = DateField(default=datetime.date.today)
    transaction_type = CharField(choices=TRANSACTION_TYPES)
    amount = FloatField()
    remark = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

# 更新 init_db 中的表列表（后面在 app.py 中修改）