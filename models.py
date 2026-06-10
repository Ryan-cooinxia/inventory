from peewee import *
from flask_login import UserMixin
import datetime

# 使用 WAL 模式提升并发读写性能，避免写入锁阻塞读取
db = SqliteDatabase('data.db', pragmas={
    'journal_mode': 'wal',          # Write-Ahead Logging — 读写不互斥
    'cache_size': -64000,           # 64MB 缓存
    'foreign_keys': 1,              # 启用外键约束
    'busy_timeout': 3000,           # 锁等待 3 秒
    'synchronous': 'NORMAL',        # 平衡安全与性能
})

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
    description = TextField(null=True)       # 产品说明
    stock = FloatField(default=0.0)          # 缓存库存（出入库时自动更新）

class ProductBundle(BaseModel):
    """用户自定义的套装组合方案"""
    name = CharField()
    user = ForeignKeyField(User, backref='product_bundles', null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'name'), True),
        )

class ProductBundleItem(BaseModel):
    """套装组成关系：一个套装方案由多个单品组成"""
    bundle = ForeignKeyField(ProductBundle, backref='items')
    component_product = ForeignKeyField(Product, backref='included_in_bundles')
    quantity = FloatField(default=1)
    user = ForeignKeyField(User, backref='product_bundle_items', null=True)

    class Meta:
        indexes = (
            (('bundle', 'component_product'), True),
        )

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


# ── 库存缓存：出入库时自动更新 Product.stock ──

def update_product_stock(product_id):
    """重新计算指定产品的库存并写入 Product.stock 字段"""
    from peewee import fn
    in_qty = (PurchaseOrderItem
              .select(fn.SUM(PurchaseOrderItem.quantity))
              .where(PurchaseOrderItem.product_id == product_id)
              .scalar()) or 0
    out_qty = (SalesOrderItem
               .select(fn.SUM(SalesOrderItem.quantity))
               .where(SalesOrderItem.product_id == product_id)
               .scalar()) or 0
    Product.update(stock=in_qty - out_qty).where(Product.id == product_id).execute()


# 重写入库明细的 save/delete，自动更新库存
_original_poi_save = PurchaseOrderItem.save
_original_poi_delete = PurchaseOrderItem.delete_instance

def _poi_save(self, *args, **kwargs):
    _original_poi_save(self, *args, **kwargs)
    update_product_stock(self.product_id)

def _poi_delete(self, *args, **kwargs):
    pid = self.product_id
    _original_poi_delete(self, *args, **kwargs)
    update_product_stock(pid)

PurchaseOrderItem.save = _poi_save
PurchaseOrderItem.delete_instance = _poi_delete


# 重写出库明细的 save/delete，自动更新库存
_original_soi_save = SalesOrderItem.save
_original_soi_delete = SalesOrderItem.delete_instance

def _soi_save(self, *args, **kwargs):
    _original_soi_save(self, *args, **kwargs)
    update_product_stock(self.product_id)

def _soi_delete(self, *args, **kwargs):
    pid = self.product_id
    _original_soi_delete(self, *args, **kwargs)
    update_product_stock(pid)

SalesOrderItem.save = _soi_save
SalesOrderItem.delete_instance = _soi_delete
