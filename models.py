from peewee import *
from flask_login import UserMixin
import datetime

db = SqliteDatabase('data.db')

class BaseModel(Model):
    class Meta:
        database = db

class User(UserMixin, BaseModel):
    """用户表"""
    username = CharField(unique=True)     # 登录用户名
    password_hash = CharField()           # 密码哈希值
    display_name = CharField(null=True)   # 显示名称（可选）
    is_admin = BooleanField(default=False)# 是否为管理员
    created_at = DateTimeField(default=datetime.datetime.now)
    
class Product(BaseModel):
    sku = CharField(max_length=50, unique=True, null=True)
    brand = CharField(max_length=50, null=True, default='DJI')
    category1 = CharField(max_length=50, null=True)
    category2 = CharField(max_length=50, null=True)
    name = CharField()
    spec = CharField(null=True)
    unit = CharField()
    user = ForeignKeyField(User, backref='products', null=True)   # 暂时允许为空，用于迁移旧数据
    description = TextField(null=True)       # 新增：产品说明

class Customer(BaseModel):
    name = CharField()
    contact = CharField(null=True)
    phone = CharField(null=True)
    address = TextField(null=True)
    user = ForeignKeyField(User, backref='customers', null=True)   # 暂时允许为空，用于迁移旧数据
    planned_refund = FloatField(default=0.0)

class Supplier(BaseModel):
    name = CharField()
    contact = CharField(null=True)
    phone = CharField(null=True)
    user = ForeignKeyField(User, backref='suppliers', null=True)   # 暂时允许为空，用于迁移旧数据

class SupplierOrder(BaseModel):
    supplier = ForeignKeyField(Supplier, backref='supplier_orders')
    order_number = CharField(max_length=50, null=True)  # 新增
    order_date = DateField(default=datetime.date.today)
    total_amount = FloatField(default=0)
    status = CharField(default='pending')
    estimated_delivery = DateField(null=True)
    remark = TextField(null=True)
    user = ForeignKeyField(User, backref='supplier_orders', null=True)   # 暂时允许为空，用于迁移旧数据

class SupplierOrderItem(BaseModel):
    """供应商订单明细"""
    order = ForeignKeyField(SupplierOrder, backref='items')
    product = ForeignKeyField(Product)
    quantity = FloatField()
    unit_price = FloatField()
    subtotal = FloatField()
    user = ForeignKeyField(User, backref='supplier_order_items', null=True)   # 暂时允许为空，用于迁移旧数据
    
class PurchaseOrder(BaseModel):
    supplier = ForeignKeyField(Supplier, backref='purchase_orders')
    order_date = DateField(default=datetime.date.today)
    total_amount = FloatField(default=0)
    remark = TextField(null=True)
    supplier_order = ForeignKeyField(SupplierOrder, null=True, backref='purchase_receipts')
    ship_method = CharField(max_length=50, null=True)
    tracking_number = CharField(max_length=100, null=True)
    user = ForeignKeyField(User, backref='purchase_orders', null=True)   # 暂时允许为空，用于迁移旧数据

class PurchaseOrderItem(BaseModel):
    order = ForeignKeyField(PurchaseOrder, backref='items')
    product = ForeignKeyField(Product)
    quantity = FloatField()
    unit_price = FloatField()
    subtotal = FloatField()
    user = ForeignKeyField(User, backref='purchase_order_items', null=True)   # 暂时允许为空，用于迁移旧数据

class CustomerOrder(BaseModel):
    """客户订单"""
    customer = ForeignKeyField(Customer, backref='customer_orders')
    order_date = DateField(default=datetime.date.today)
    total_amount = FloatField(default=0)
    status = CharField(default='pending')
    remark = TextField(null=True)
    invoice_required = BooleanField(default=False)
    user = ForeignKeyField(User, backref='customer_orders', null=True)   # 暂时允许为空，用于迁移旧数据

class CustomerOrderItem(BaseModel):
    order = ForeignKeyField(CustomerOrder, backref='items')
    product = ForeignKeyField(Product)
    quantity = FloatField()
    unit_price = FloatField()
    subtotal = FloatField()
    user = ForeignKeyField(User, backref='customer_order_items', null=True)   # 暂时允许为空，用于迁移旧数据

class SalesOrder(BaseModel):
    """出库单（可关联到订单）"""
    customer = ForeignKeyField(Customer, backref='sales_orders')
    customer_order = ForeignKeyField(CustomerOrder, null=True, backref='shipments')
    order_date = DateField(default=datetime.date.today)
    total_amount = FloatField(default=0)
    remark = TextField(null=True)
    ship_method = CharField(max_length=50, null=True)
    tracking_number = CharField(max_length=100, null=True)
    user = ForeignKeyField(User, backref='sales_orders', null=True)   # 暂时允许为空，用于迁移旧数据

class SalesOrderItem(BaseModel):
    order = ForeignKeyField(SalesOrder, backref='items')
    product = ForeignKeyField(Product)
    quantity = FloatField()
    unit_price = FloatField()
    subtotal = FloatField()
    user = ForeignKeyField(User, backref='sales_order_items', null=True)   # 暂时允许为空，用于迁移旧数据

class CustomerRefund(BaseModel):
    customer = ForeignKeyField(Customer, backref='refunds')
    sales_order = ForeignKeyField(SalesOrder, null=True, backref='refunds')
    customer_order = ForeignKeyField(CustomerOrder, null=True, backref='refunds')  # 可关联到订单
    refund_date = DateField(default=datetime.date.today)
    amount = FloatField()
    remark = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    user = ForeignKeyField(User, backref='refunds', null=True)   # 暂时允许为空，用于迁移旧数据

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
    user = ForeignKeyField(User, backref='transactions', null=True)   # 暂时允许为空，用于迁移旧数据

class ExchangeRate(BaseModel):
    """汇率表"""
    base_currency = CharField(max_length=10)       # 基础货币，如 CNY
    target_currency = CharField(max_length=10)     # 目标货币，如 RUB
    rate = FloatField()                            # 汇率
    updated_at = DateTimeField(default=datetime.datetime.now)

class OperationLog(BaseModel):
    """操作日志"""
    user = ForeignKeyField(User, backref='operation_logs')          # 操作用户
    action_type = CharField(max_length=20)                          # 操作类型：create/update/delete
    target_type = CharField(max_length=50)                          # 操作对象：Product/Customer/Order等
    target_id = IntegerField(null=True)                             # 操作对象的ID
    description = TextField(null=True)                              # 描述信息
    ip_address = CharField(max_length=50, null=True)                # 操作者IP
    created_at = DateTimeField(default=datetime.datetime.now)       # 操作时间

class UserApiKey(BaseModel):
    user = ForeignKeyField(User, backref='api_key', unique=True)
    api_key = CharField()           # 加密后的 API Key
    api_provider = CharField(default='deepseek')  # deepseek / openai