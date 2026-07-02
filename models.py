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
    extension_token = CharField(null=True, max_length=100)  # 浏览器插件认证 Token
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


# ═══════════════════════════════════════════════════════════════
# 库存转换 / 套装拆分
# ═══════════════════════════════════════════════════════════════

class ProductSplitRule(BaseModel):
    """拆包规则：一个原产品拆成多个目标产品"""
    user = ForeignKeyField(User, backref='split_rules')
    name = CharField(max_length=200)                        # 规则名称
    source_product = ForeignKeyField(Product, backref='as_split_source')
    cost_method = CharField(max_length=20, default='ratio') # ratio / manual
    remark = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'source_product'), False),
        )


class ProductSplitRuleItem(BaseModel):
    """拆包规则明细：目标产品 + 数量 + 成本"""
    rule = ForeignKeyField(ProductSplitRule, backref='items')
    target_product = ForeignKeyField(Product, backref='as_split_target')
    quantity = FloatField(default=1)                        # 每拆1个源产品产出几个目标产品
    cost_ratio = FloatField(null=True)                      # 成本分摊比例（0-1），cost_method=ratio 时使用
    manual_unit_cost = FloatField(null=True)                # 手动指定单位成本，cost_method=manual 时使用
    user = ForeignKeyField(User, backref='split_rule_items', null=True)

    class Meta:
        indexes = (
            (('rule', 'target_product'), True),
        )


class ProductSplitOrder(BaseModel):
    """拆包单"""
    user = ForeignKeyField(User, backref='split_orders')
    split_no = CharField(max_length=50)                     # 单号
    split_date = DateField(default=datetime.date.today)
    rule = ForeignKeyField(ProductSplitRule, null=True, backref='orders')
    source_product = ForeignKeyField(Product, backref='split_source_orders')
    source_quantity = FloatField(default=0)                 # 拆了几个源产品
    source_unit_cost = FloatField(default=0)                # 源产品单位成本（加权平均）
    source_total_cost = FloatField(default=0)               # 源产品总成本
    status = CharField(max_length=20, default='draft')      # draft / confirmed / cancelled
    remark = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    cancelled_at = DateTimeField(null=True)

    class Meta:
        indexes = (
            (('user', 'status'), False),
        )


class ProductSplitOrderItem(BaseModel):
    """拆包单明细：产出明细"""
    order = ForeignKeyField(ProductSplitOrder, backref='items')
    target_product = ForeignKeyField(Product, backref='split_target_items')
    quantity = FloatField(default=0)                        # 产出数量
    unit_cost = FloatField(default=0)                       # 单位成本
    total_cost = FloatField(default=0)                      # 总成本
    user = ForeignKeyField(User, backref='split_order_items', null=True)

    class Meta:
        indexes = (
            (('order', 'target_product'), True),
        )


# ═══════════════════════════════════════════════════════════════
# 套装组合（零件 → 套装，与拆包方向相反）
# ═══════════════════════════════════════════════════════════════

class ProductAssemblyRule(BaseModel):
    """组合规则：多个零件组成一个套装"""
    user = ForeignKeyField(User, backref='assembly_rules')
    name = CharField(max_length=200)
    bundle_product = ForeignKeyField(Product, backref='as_assembly_bundle')  # 组装产出的套装
    cost_method = CharField(max_length=20, default='sum')  # sum（零件成本累加）/ manual
    remark = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'bundle_product'), False),
        )


class ProductAssemblyRuleItem(BaseModel):
    """组合规则明细：零件 + 数量"""
    rule = ForeignKeyField(ProductAssemblyRule, backref='items')
    component_product = ForeignKeyField(Product, backref='as_assembly_component')
    quantity = FloatField(default=1)          # 每组装1个套装需要几个该零件
    cost_ratio = FloatField(null=True)        # 手动分摊比例（cost_method=manual 时用）
    manual_unit_cost = FloatField(null=True)
    user = ForeignKeyField(User, backref='assembly_rule_items', null=True)

    class Meta:
        indexes = (
            (('rule', 'component_product'), True),
        )


class ProductAssemblyOrder(BaseModel):
    """组合单"""
    user = ForeignKeyField(User, backref='assembly_orders')
    assembly_no = CharField(max_length=50)                  # 单号 AS-YYYYMMDD-NNN
    assembly_date = DateField(default=datetime.date.today)
    rule = ForeignKeyField(ProductAssemblyRule, null=True, backref='orders')
    bundle_product = ForeignKeyField(Product, backref='assembly_bundle_orders')
    bundle_quantity = FloatField(default=0)                 # 组装几个套装
    total_cost = FloatField(default=0)                      # 总成本（零件成本之和）
    status = CharField(max_length=20, default='draft')      # draft / confirmed / cancelled
    remark = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    cancelled_at = DateTimeField(null=True)

    class Meta:
        indexes = (
            (('user', 'status'), False),
        )


class ProductAssemblyOrderItem(BaseModel):
    """组合单明细：消耗的零件"""
    order = ForeignKeyField(ProductAssemblyOrder, backref='items')
    component_product = ForeignKeyField(Product, backref='assembly_consumed_items')
    quantity = FloatField(default=0)                        # 消耗数量
    unit_cost = FloatField(default=0)                       # 单位成本（取自库存加权平均）
    total_cost = FloatField(default=0)                      # 总成本
    user = ForeignKeyField(User, backref='assembly_order_items', null=True)

    class Meta:
        indexes = (
            (('order', 'component_product'), True),
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
    is_settlement = BooleanField(default=False)  # 平账单（替代发货后手动平账，均价=0）
    user = ForeignKeyField(User, backref='sales_orders', null=True)

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
    """重新计算指定产品的库存并写入 Product.stock 字段（含拆包+组合）"""
    from peewee import fn
    in_qty = (PurchaseOrderItem
              .select(fn.SUM(PurchaseOrderItem.quantity))
              .where(PurchaseOrderItem.product_id == product_id)
              .scalar()) or 0
    out_qty = (SalesOrderItem
               .select(fn.SUM(SalesOrderItem.quantity))
               .where(SalesOrderItem.product_id == product_id)
               .scalar()) or 0

    # 拆包产出（增加）
    split_in = (ProductSplitOrderItem
                .select(fn.SUM(ProductSplitOrderItem.quantity))
                .join(ProductSplitOrder)
                .where((ProductSplitOrderItem.target_product_id == product_id) &
                       (ProductSplitOrder.status == 'confirmed'))
                .scalar()) or 0

    # 拆包消耗（减少）
    split_out = (ProductSplitOrder
                 .select(fn.SUM(ProductSplitOrder.source_quantity))
                 .where((ProductSplitOrder.source_product_id == product_id) &
                        (ProductSplitOrder.status == 'confirmed'))
                 .scalar()) or 0

    # 组合产出（增加）：套装被组装出来
    assembly_in = (ProductAssemblyOrder
                   .select(fn.SUM(ProductAssemblyOrder.bundle_quantity))
                   .where((ProductAssemblyOrder.bundle_product_id == product_id) &
                          (ProductAssemblyOrder.status == 'confirmed'))
                   .scalar()) or 0

    # 组合消耗（减少）：零件被用于组装
    assembly_out = (ProductAssemblyOrderItem
                    .select(fn.SUM(ProductAssemblyOrderItem.quantity))
                    .join(ProductAssemblyOrder)
                    .where((ProductAssemblyOrderItem.component_product_id == product_id) &
                           (ProductAssemblyOrder.status == 'confirmed'))
                    .scalar()) or 0

    stock = in_qty - out_qty - split_out + split_in - assembly_out + assembly_in
    Product.update(stock=stock).where(Product.id == product_id).execute()


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


# ═══════════════════════════════════════════════════════════════
# OZON 模块数据表（10 张新表，不影响现有业务）
# ═══════════════════════════════════════════════════════════════

class OzonAccount(BaseModel):
    """OZON 店铺 API 凭证"""
    user = ForeignKeyField(User, backref='ozon_accounts')
    platform = CharField(max_length=20, default='ozon')          # ozon（预留扩展）
    name = CharField(max_length=100)                              # 店铺名称
    shop_type = CharField(max_length=20, default='cross_border')  # cross_border / local
    environment = CharField(max_length=20, default='test')        # test / production
    client_id = CharField(max_length=200)                         # OZON API Client-Id
    api_key = CharField(max_length=200)                           # OZON API Key
    is_active = BooleanField(default=True)                        # 是否启用
    last_sync_at = DateTimeField(null=True)                       # 最近同步时间
    sync_status = CharField(max_length=20, null=True)             # ok / error
    sync_error = TextField(null=True)                             # 最近同步错误
    # 店铺维度配置：语言/货币
    seller_ui_language = CharField(max_length=10, default='zh')   # OZON 后台语言: zh / ru / en
    template_language = CharField(max_length=10, default='zh')    # Excel/在线模板字段值语言
    default_currency = CharField(max_length=10, default='CNY')    # 后台价格货币: CNY / RUB / USD / EUR
    currency_confirmed = BooleanField(default=False)              # 用户已确认货币设置
    locale_confirmed_at = DateTimeField(null=True)                # 确认时间
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'name'), True),  # 同用户下店铺名唯一
        )


class OzonSource(BaseModel):
    """采集商品原始资料"""
    user = ForeignKeyField(User, backref='ozon_sources')
    platform = CharField(max_length=20)                           # 1688 / taobao / tmall / pinduoduo / manual
    source_url = CharField(max_length=500)                        # 源商品链接
    capture_url = CharField(max_length=500, null=True)            # 实际抓取的 URL（如 H5 转换后）
    title_cn = CharField(max_length=300)                          # 中文标题
    category_cn = CharField(max_length=100, null=True)            # 中文类目
    description_cn = TextField(null=True)                         # 中文描述
    shop_name = CharField(max_length=200, null=True)              # 供应商店铺名
    sku_count = IntegerField(default=0)                           # SKU 数量（冗余）
    image_count = IntegerField(default=0)                         # 图片数量（冗余）
    raw_json = TextField()                                        # 完整采集 JSON（schema v1.0）
    status = CharField(max_length=20, default='collected')        # collected / parsed / drafted / archived
    capture_method = CharField(max_length=30, default='manual')   # browser_extension / open_api / manual
    captured_at = DateTimeField(default=datetime.datetime.now)    # 采集时间
    remark = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)
    deleted_at = DateTimeField(null=True, default=None)             # 软删除标记（入回收站时间）
    quality_json = TextField(null=True)                              # 采集质量检查结果 JSON
    detail_missing = BooleanField(default=False)                     # 详情页缺失标记（淘宝/天猫JS渲染）
    price_manual_confirmed = BooleanField(default=False)             # 价格是否人工确认

    class Meta:
        indexes = (
            (('user', 'platform'), False),
            (('user', 'status'), False),
        )


class OzonSourceSku(BaseModel):
    """源商品 SKU"""
    user = ForeignKeyField(User, backref='ozon_source_skus')
    source = ForeignKeyField(OzonSource, backref='skus')
    source_order = IntegerField()                                 # 源 SKU 顺序（不可变）
    source_sku_id = CharField(max_length=100)                     # 源 SKU 标识
    source_sku_name = CharField(max_length=200)                   # 源 SKU 名称
    color_cn = CharField(max_length=50, null=True)
    color_ru = CharField(max_length=100, null=True)
    size_cn = CharField(max_length=50, null=True)
    size_ru = CharField(max_length=100, null=True)
    style_cn = CharField(max_length=50, null=True)
    style_ru = CharField(max_length=100, null=True)
    bundle_quantity = IntegerField(default=1)                     # 套装数量
    package_contents = TextField(null=True)                       # 包装内容 JSON 数组
    material_cn = CharField(max_length=100, null=True)
    purchase_price_cny = FloatField(null=True)                    # 采购价 ¥
    image_refs = TextField(null=True)                             # 关联图片 ID JSON 数组
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('source', 'source_order'), True),  # 同一采集下顺序唯一
        )


class OzonSourceMedia(BaseModel):
    """源商品图片"""
    user = ForeignKeyField(User, backref='ozon_source_media')
    source = ForeignKeyField(OzonSource, backref='media')
    media_id = CharField(max_length=100)                          # 媒体标识
    media_source = CharField(max_length=30, default='source_page')# source_page / generated / edited / manual_upload
    role = CharField(max_length=30, null=True)                    # main / sku / detail / scene / ...
    source_url = CharField(max_length=500, null=True)             # 原始 URL
    local_path = CharField(max_length=300, null=True)             # 本地路径
    sku_refs = TextField(null=True)                               # 关联 SKU JSON 数组
    width = IntegerField(null=True)
    height = IntegerField(null=True)
    aspect_ratio = CharField(max_length=10, null=True)            # 如 '3:4'
    has_text = BooleanField(default=False)
    text_language = CharField(max_length=20, null=True)           # zh / ru / en / none / mixed
    needs_cleanup = BooleanField(default=False)
    for_ozon = BooleanField(default=False)                        # 是否可用于 OZON
    review_status = CharField(max_length=20, default='pending')   # pending / approved / rejected
    compliance_status = CharField(max_length=20, null=True)       # usable / needs_review / rejected（图片合规分类）
    reject_reason = CharField(max_length=200, null=True)          # 拒绝/需审查原因
    raw_json = TextField(null=True)                               # 扩展元数据 JSON
    created_at = DateTimeField(default=datetime.datetime.now)


class OzonDraft(BaseModel):
    """OZON 刊登草稿（核心表）"""
    user = ForeignKeyField(User, backref='ozon_drafts')
    account = ForeignKeyField(OzonAccount, null=True, backref='drafts')  # 目标店铺
    source = ForeignKeyField(OzonSource, backref='drafts')               # 源采集商品
    status = CharField(max_length=20, default='draft')            # draft / needs_review / ready / approved / publishing / published / failed
    ozon_category_id = CharField(max_length=50, null=True)        # OZON 类目 ID (description_category_id)
    type_id = CharField(max_length=50, null=True)                  # OZON 商品类型 ID
    category_path_ru = CharField(max_length=500, null=True)        # 类目路径（俄）
    category_path_cn = CharField(max_length=500, null=True)        # 类目路径（中）
    type_name_ru = CharField(max_length=200, null=True)            # 商品类型名（俄）
    type_name_cn = CharField(max_length=200, null=True)            # 商品类型名（中）
    title_ru = CharField(max_length=300, null=True)               # 俄语标题
    description_ru = TextField(null=True)                         # 俄语描述
    bullets_ru = TextField(null=True)                             # 俄语卖点 JSON 数组
    hashtags_ru = TextField(null=True)                            # 主题标签（#экшнкамера #DJI）
    attributes_json = TextField(null=True)                        # 类目属性 JSON
    skus_json = TextField(null=True)                              # SKU 数据 JSON（快照）
    pricing_json = TextField(null=True)                           # 定价数据 JSON
    media_json = TextField(null=True)                             # 草稿媒体池 JSON（图片+视频统一管理）
    rich_content_json = TextField(null=True)                      # 富文本块 JSON（替代追加到 description_ru）
    ai_title_confidence = FloatField(null=True)                   # AI 标题置信度
    ai_description_confidence = FloatField(null=True)
    ai_bullets_confidence = FloatField(null=True)
    ai_category_confidence = FloatField(null=True)
    validation_result = TextField(null=True)                      # 校验结果 JSON
    price_manual_confirmed = BooleanField(default=False)          # 价格是否人工确认
    reviewer_notes = TextField(null=True)                         # 审核备注
    ozon_product_id = CharField(max_length=50, null=True)         # 发布后回写的 OZON 商品 ID
    ozon_offer_id = CharField(max_length=100, null=True)          # 发布后回写的 offer_id
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'status'), False),
            (('user', 'account'), False),
        )


class OzonDraftSku(BaseModel):
    """草稿 SKU 明细"""
    user = ForeignKeyField(User, backref='ozon_draft_skus')
    draft = ForeignKeyField(OzonDraft, backref='draft_skus')
    source_sku = ForeignKeyField(OzonSourceSku, null=True, backref='draft_skus')
    source_order = IntegerField()                                 # 顺序号（与源一致）
    source_sku_name = CharField(max_length=200)                   # 源 SKU 名称
    color_ru = CharField(max_length=100, null=True)
    style_ru = CharField(max_length=100, null=True)
    bundle_quantity = IntegerField(default=1)
    purchase_price_cny = FloatField(null=True)
    offer_id = CharField(max_length=100, null=True)               # OZON offer_id
    barcode = CharField(max_length=100, null=True)                # 条码
    ozon_sku_id = CharField(max_length=50, null=True)             # OZON SKU ID
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('draft', 'source_order'), True),
        )


class OzonImagePlan(BaseModel):
    """图片方案 — 管理副图或 A+ 详情页方案"""
    user = ForeignKeyField(User, backref='ozon_image_plans')
    draft = ForeignKeyField(OzonDraft, backref='image_plans')

    plan_type = CharField(max_length=20)            # listing / aplus
    target_marketplace = CharField(max_length=30, default='ozon')
    target_language = CharField(max_length=10, default='ru')

    product_understanding_json = TextField(null=True)
    buyer_questions_json = TextField(null=True)
    selling_point_groups_json = TextField(null=True)
    immutable_structure_json = TextField(null=True)
    verified_parameters_json = TextField(null=True)

    status = CharField(max_length=20, default='draft')
    # draft / analyzed / planned / generating / reviewing / approved

    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'draft', 'plan_type'), True),
        )


class OzonImageReference(BaseModel):
    """参考图关联 — 将 OzonSourceMedia 绑定到图片方案"""
    user = ForeignKeyField(User, backref='ozon_image_references')
    plan = ForeignKeyField(OzonImagePlan, backref='references')
    media = ForeignKeyField(OzonSourceMedia, backref='image_references')

    reference_role = CharField(max_length=30)        # primary / sku / detail / structure / style
    priority = IntegerField(default=0)
    is_required = BooleanField(default=False)

    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('plan', 'media', 'reference_role'), True),
        )


class OzonProductCutout(BaseModel):
    """产品母图 — 自动抠图生成的透明 PNG"""
    user = ForeignKeyField(User, backref='ozon_product_cutouts')
    source = ForeignKeyField(OzonSource, backref='product_cutouts')
    source_media = ForeignKeyField(OzonSourceMedia, backref='cutouts')
    source_sku = ForeignKeyField(OzonSourceSku, null=True, backref='cutouts')

    transparent_path = CharField(max_length=300)          # 透明 PNG 路径
    mask_path = CharField(max_length=300, null=True)      # 蒙版路径
    preview_path = CharField(max_length=300, null=True)   # 预览缩略图

    provider = CharField(max_length=50, default='rembg')  # rembg / dashscope / manual
    quality_score = IntegerField(null=True)
    quality_json = TextField(null=True)

    status = CharField(max_length=20, default='pending')  # pending / generated / approved / rejected
    is_primary = BooleanField(default=False)
    reviewer_notes = TextField(null=True)

    # ── V2: 目标级分割 ──
    target_spec_json = TextField(null=True)           # 商品/配件/排除对象及bbox
    raw_mask_path = CharField(max_length=300, null=True)
    cleaned_mask_path = CharField(max_length=300, null=True)
    segmentation_provider = CharField(max_length=50, null=True)  # rembg_crop / rembg_full / sam2_box / manual
    target_count = IntegerField(default=1)
    has_accessories = BooleanField(default=False)
    outside_residual_score = FloatField(null=True)
    completeness_score = FloatField(null=True)
    edge_quality_score = FloatField(null=True)
    revision = IntegerField(default=1)

    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'source_media'), False),
            (('user', 'status'), False),
        )


class OzonProductSubjectDetection(BaseModel):
    """视觉模型商品主体检测记录（保留历史）"""
    user = ForeignKeyField(User, backref='product_subject_detections')
    source = ForeignKeyField(OzonSource, backref='subject_detections')
    source_media = ForeignKeyField(OzonSourceMedia, backref='subject_detections')

    provider = CharField(max_length=50)          # qwen_vl / openai_vision 等
    model_name = CharField(max_length=100)

    image_width = IntegerField()
    image_height = IntegerField()

    detection_json = TextField()                 # 识别结果 JSON
    raw_response_json = TextField(null=True)     # 视觉模型原始响应

    main_product_confidence = FloatField(null=True)
    status = CharField(max_length=20, default='detected')  # detected / confirmed / rejected / failed

    error_message = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    confirmed_at = DateTimeField(null=True)

    class Meta:
        indexes = (
            (('user', 'source_media'), False),
            (('user', 'status'), False),
        )


class OzonImageSlot(BaseModel):
    """图片槽位"""
    user = ForeignKeyField(User, backref='ozon_image_slots')
    draft = ForeignKeyField(OzonDraft, backref='image_slots')
    plan = ForeignKeyField(OzonImagePlan, null=True, backref='slots')
    cutout = ForeignKeyField(OzonProductCutout, null=True, backref='slots')

    slot_order = IntegerField()                                   # 槽位序号 1-8
    role = CharField(max_length=30)                               # main / sku / scene / selling_point / function / detail / size / package
    scope = CharField(max_length=10, default='all')               # all / sku
    scope_sku_ref = CharField(max_length=100, null=True)
    prompt_cn = TextField(null=True)
    prompt_ru = TextField(null=True)
    negative_prompt = TextField(null=True)
    style = CharField(max_length=50, null=True)
    generated_url = CharField(max_length=500, null=True)
    local_path = CharField(max_length=300, null=True)
    status = CharField(max_length=20, default='planned')          # planned / generated / reviewed / approved / rejected
    review_notes = TextField(null=True)

    # ── P0 新增：买家疑问/卖点/参考图/生成模式 ──
    buyer_question = TextField(null=True)
    main_claim = TextField(null=True)
    proof_points_json = TextField(null=True)
    visual_evidence_json = TextField(null=True)
    reference_media_ids_json = TextField(null=True)
    text_overlay_json = TextField(null=True)
    verified_parameters_json = TextField(null=True)
    generation_mode = CharField(max_length=20, default='reference')  # reference / text_only / composite
    qa_required = BooleanField(default=True)

    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('draft', 'slot_order'), True),
        )


class OzonImageCandidate(BaseModel):
    """图片槽位的多模型候选图"""
    user = ForeignKeyField(User, backref='ozon_image_candidates')
    draft = ForeignKeyField(OzonDraft, backref='image_candidates')
    slot = ForeignKeyField(OzonImageSlot, backref='candidates')

    provider = CharField(max_length=50)          # img_gen_image2 / img_gen_openai / img_gen_wanx ...
    model_name = CharField(max_length=100)
    prompt_version = CharField(max_length=100, null=True)

    prompt = TextField()
    negative_prompt = TextField(null=True)

    image_url = CharField(max_length=500, null=True)
    local_path = CharField(max_length=300, null=True)

    request_json = TextField(null=True)
    response_json = TextField(null=True)
    error_message = TextField(null=True)

    status = CharField(max_length=20, default='generated')
    # generated / failed / selected / rejected

    structure_score = IntegerField(null=True)      # 产品结构还原，0-30
    detail_score = IntegerField(null=True)         # 关键细节，0-25
    text_score = IntegerField(null=True)           # 文字/屏幕/按钮，0-15
    commercial_score = IntegerField(null=True)     # 电商美观，0-20
    postprocess_score = IntegerField(null=True)    # 后期可处理，0-10
    total_score = IntegerField(null=True)

    review_notes = TextField(null=True)

    # ── P0 新增：返修链 / 生成模式 / 自动QA / 请求快照 ──
    parent_candidate = ForeignKeyField('self', null=True, backref='revisions')
    generation_mode = CharField(max_length=20, default='reference')     # reference / text_only / composite
    reference_snapshot_json = TextField(null=True)
    auto_qa_json = TextField(null=True)
    auto_qa_score = IntegerField(null=True)
    auto_qa_status = CharField(max_length=20, null=True)                # passed / warning / failed
    revision_prompt = TextField(null=True)
    revision_count = IntegerField(default=0)

    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('slot', 'provider', 'model_name'), False),
            (('draft', 'slot'), False),
            (('user', 'status'), False),
        )


class OzonPublishJob(BaseModel):
    """发布任务"""
    user = ForeignKeyField(User, backref='ozon_publish_jobs')
    account = ForeignKeyField(OzonAccount, backref='publish_jobs')
    draft = ForeignKeyField(OzonDraft, backref='publish_jobs')
    action = CharField(max_length=30, default='create_product')   # create_product / update_product / update_price / update_stock / upload_image
    status = CharField(max_length=20, default='pending')          # pending / processing / success / failed
    request_json = TextField(null=True)
    response_json = TextField(null=True)
    error_message = TextField(null=True)
    ozon_task_id = CharField(max_length=50, null=True)
    retry_count = IntegerField(default=0)
    created_at = DateTimeField(default=datetime.datetime.now)
    completed_at = DateTimeField(null=True)

    class Meta:
        indexes = (
            (('user', 'status'), False),
            (('draft', 'created_at'), False),
        )


class OzonPrompt(BaseModel):
    """提示词模板"""
    user = ForeignKeyField(User, backref='ozon_prompts')
    name = CharField(max_length=100)                              # 模板名称
    prompt_type = CharField(max_length=20)                        # title / bullets / description / image
    category = CharField(max_length=50, default='common')         # 适用品类
    content = TextField()                                         # 提示词内容（含 {变量}）
    variables = TextField(null=True)                              # 变量说明 JSON
    is_default = BooleanField(default=False)                      # 是否系统默认
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'prompt_type', 'category'), True),
        )


class OzonPricingRule(BaseModel):
    """定价规则"""
    user = ForeignKeyField(User, backref='ozon_pricing_rules')
    name = CharField(max_length=100)
    exchange_rate_source = CharField(max_length=10, default='auto')  # auto / manual
    manual_exchange_rate = FloatField(null=True)                     # 手动汇率
    target_margin_rate = FloatField(default=0.35)                    # 目标毛利率
    ad_reserve_rate = FloatField(default=0.05)                       # 广告预留
    commission_rate = FloatField(default=0.10)                       # OZON 佣金
    risk_buffer_type = CharField(max_length=10, default='fixed')     # fixed / percent
    risk_buffer_value = FloatField(default=3.0)                      # 风险缓冲值
    logistics_tiers = TextField(null=True)                            # 物流阶梯 JSON
    is_default = BooleanField(default=False)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)


# ═══════════════════════════════════════════════════════════════
# OZON 适配层（6 张新表）
# ═══════════════════════════════════════════════════════════════

class SourceProductGroup(BaseModel):
    """适配任务组 — 将源商品绑定为一个适配任务"""
    user = ForeignKeyField(User, backref='source_product_groups')
    name = CharField(max_length=200)                                  # 任务名
    relation_type = CharField(max_length=20, default='one_to_one')    # one_to_one / one_to_many / many_to_one
    status = CharField(max_length=20, default='draft')               # draft / adapting / reviewed / converted / archived
    notes = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'status'), False),
        )


class SourceProductGroupItem(BaseModel):
    """适配任务关联的源商品（N:N 中间表）"""
    user = ForeignKeyField(User, backref='source_product_group_items')
    group = ForeignKeyField(SourceProductGroup, backref='items')
    source = ForeignKeyField(OzonSource, backref='group_items')
    role = CharField(max_length=20, default='primary')               # primary / accessory / alternative / reference
    sort_order = IntegerField(default=0)
    include_in_listing = BooleanField(default=True)
    notes = TextField(null=True)

    class Meta:
        indexes = (
            (('group', 'source'), True),
        )


class ProductFact(BaseModel):
    """标准化商品事实 — 源商品与 OZON Listing 之间的中间层"""
    user = ForeignKeyField(User, backref='product_facts')
    group = ForeignKeyField(SourceProductGroup, null=True, backref='product_facts')
    standard_name_cn = CharField(max_length=300)                      # 标准商品名（中）
    standard_name_ru = CharField(max_length=300, null=True)           # 标准商品名（俄）
    product_type = CharField(max_length=100, null=True)               # 商品类型（如：无线麦克风）
    category_hint_cn = CharField(max_length=200, null=True)           # 本地品类提示
    brand_name = CharField(max_length=100, null=True)                 # 品牌
    model = CharField(max_length=100, null=True)                      # 型号
    material = CharField(max_length=100, null=True)                   # 材质
    origin = CharField(max_length=50, null=True)                      # 产地
    warranty = CharField(max_length=100, null=True)                   # 保修
    functions_json = TextField(null=True)                             # 功能列表 JSON
    package_contents_json = TextField(null=True)                      # 包装内容 JSON
    usage_scenarios_json = TextField(null=True)                       # 使用场景 JSON
    compatibility_json = TextField(null=True)                         # 适配型号 JSON
    dimensions_json = TextField(null=True)                            # 尺寸 JSON（含单位）
    weight_json = TextField(null=True)                                # 重量 JSON（含单位）
    certifications_json = TextField(null=True)                        # 认证 JSON
    battery_capacity = CharField(max_length=50, null=True)            # 电池容量
    power = CharField(max_length=50, null=True)                       # 功率
    wireless_range = CharField(max_length=50, null=True)              # 无线范围
    facts_json = TextField(null=True)                                 # 扩展事实 JSON
    unknown_fields_json = TextField(null=True)                        # 未知字段 JSON
    locked_fields_json = TextField(null=True)                         # 锁定字段 JSON
    confidence = FloatField(null=True)                                # 整体置信度 0-1
    review_status = CharField(max_length=20, default='pending')      # pending / approved / needs_changes / partial_confirmed
    reviewer_notes = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'review_status'), False),
            (('user', 'product_type'), False),
        )


class ProductFactSku(BaseModel):
    """标准化 SKU 事实 — 每条对应一个源 SKU"""
    user = ForeignKeyField(User, backref='product_fact_skus')
    fact = ForeignKeyField(ProductFact, backref='fact_skus')
    source_sku = ForeignKeyField(OzonSourceSku, null=True, backref='fact_skus')
    source_order = IntegerField()                                     # SKU 顺序号（与源一致）
    standard_sku_name_cn = CharField(max_length=200, null=True)       # 标准 SKU 名（中）
    standard_sku_name_ru = CharField(max_length=200, null=True)       # 标准 SKU 名（俄）
    color_cn = CharField(max_length=50, null=True)
    color_ru = CharField(max_length=100, null=True)
    size_cn = CharField(max_length=50, null=True)
    size_ru = CharField(max_length=100, null=True)
    style_cn = CharField(max_length=100, null=True)
    style_ru = CharField(max_length=100, null=True)
    bundle_quantity = IntegerField(default=1)
    package_contents_json = TextField(null=True)
    purchase_price_cny = FloatField(null=True)
    image_refs_json = TextField(null=True)                            # 关联图片 ID JSON
    evidence_refs_json = TextField(null=True)                         # 证据引用 JSON
    confidence = FloatField(null=True)                                # 置信度 0-1
    manual_status = CharField(max_length=20, default='pending')      # pending / confirmed / rejected
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('fact', 'source_order'), True),
        )


class ProductFactEvidence(BaseModel):
    """事实字段的证据溯源"""
    user = ForeignKeyField(User, backref='product_fact_evidences')
    fact = ForeignKeyField(ProductFact, null=True, backref='evidences')
    fact_sku = ForeignKeyField(ProductFactSku, null=True, backref='evidences')
    field_path = CharField(max_length=200)                             # 字段路径，如 material / skus[0].color_cn
    evidence_type = CharField(max_length=30, default='text')          # text / image / screenshot / html / api / ocr / ai / manual
    source = ForeignKeyField(OzonSource, null=True, backref='evidences')
    media = ForeignKeyField(OzonSourceMedia, null=True, backref='evidences')
    source_url = CharField(max_length=500, null=True)
    content = TextField(null=True)
    confidence = FloatField(null=True)

    # ── 新增：事实值、状态、来源定位、SKU归属、冲突标记 ──
    value_json = TextField(null=True)                    # 事实值 JSON
    fact_status = CharField(max_length=20, default='extracted')  # extracted/inferred/verified/confirmed/conflict/unknown/rejected
    source_type = CharField(max_length=20, null=True)    # text/image/ocr/html/api/manual
    source_locator_json = TextField(null=True)            # 网页段落/图片bbox/OCR区域等
    applicable_sku_id = IntegerField(null=True)           # 适用 SKU ID
    evidence_hash = CharField(max_length=64, null=True)   # SHA256 去重
    conflict_group = IntegerField(null=True)              # 同一冲突组编号
    confirmed_by = ForeignKeyField(User, null=True, backref='confirmed_evidences')
    confirmed_at = DateTimeField(null=True)
    rejected_reason = TextField(null=True)

    # ── 动态属性：跨品类通用 ──
    group_key = CharField(max_length=30, null=True)          # identity/structure/specification/function/compatibility/sku/package/selling_point/custom
    label_cn = CharField(max_length=100, null=True)          # 中文显示名
    value_type = CharField(max_length=20, null=True)         # text/number/boolean/list/measurement
    unit = CharField(max_length=20, null=True)               # mm/g/Hz/V/W等
    sort_order = IntegerField(default=0)

    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'fact'), False),
            (('fact', 'field_path'), False),
            (('user', 'evidence_hash'), False),
            (('fact', 'conflict_group'), False),
            (('fact', 'group_key'), False),
        )


class ProductFactRevision(BaseModel):
    """Product Brief 版本快照 — 不可覆盖"""
    user = ForeignKeyField(User, backref='product_fact_revisions')
    fact = ForeignKeyField(ProductFact, backref='revisions')

    revision = IntegerField(default=1)
    brief_json = TextField()                             # 完整 Product Brief JSON
    status = CharField(max_length=20, default='draft')   # draft / confirmed / approved / archived
    source_snapshot_json = TextField(null=True)           # 来源快照

    created_by = ForeignKeyField(User, null=True, backref='created_revisions')
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'fact'), False),
            (('fact', 'revision'), True),
        )


class ProductFactSchema(BaseModel):
    """品类事实模板 — 指导不同品类提取不同属性"""
    user = ForeignKeyField(User, backref='product_fact_schemas')
    category_key = CharField(max_length=100, unique=True)   # electronics.camera_accessory
    display_name = CharField(max_length=100)                 # 摄影配件
    schema_json = TextField()                                # 完整 schema JSON
    is_active = BooleanField(default=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'category_key'), True),
        )


class ListingAdaptation(BaseModel):
    """商品事实到 OZON Listing 的适配方案"""
    user = ForeignKeyField(User, backref='listing_adaptations')
    fact = ForeignKeyField(ProductFact, backref='listing_adaptations')
    relation_type = CharField(max_length=20, default='one_to_one')    # one_to_one / one_to_many / many_to_one
    target_listing_count = IntegerField(default=1)
    ozon_category_id = CharField(max_length=50, null=True)
    ozon_category_name = CharField(max_length=300, null=True)
    type_id = CharField(max_length=50, null=True)                     # 绑定的 OZON type_id
    type_name_ru = CharField(max_length=200, null=True)               # type 俄语名
    type_name_cn = CharField(max_length=200, null=True)               # type 中文名
    category_path = CharField(max_length=500, null=True)              # 类目路径（面包屑）
    category_confidence = FloatField(null=True)
    attribute_mapping_json = TextField(null=True)                      # 属性映射 JSON
    title_ru = CharField(max_length=300, null=True)                    # AI 生成的俄语标题
    bullets_ru_json = TextField(null=True)                             # 俄语卖点 JSON
    description_ru = TextField(null=True)
    image_plan_json = TextField(null=True)
    pricing_json = TextField(null=True)
    validation_json = TextField(null=True)                             # 校验结果 JSON
    status = CharField(max_length=20, default='draft')                # draft / needs_review / ready / converted
    draft = ForeignKeyField(OzonDraft, null=True, backref='adaptations')  # 转换后的草稿
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'status'), False),
            (('fact', 'relation_type'), False),
        )


# ═══════════════════════════════════════════════════════════════
# OZON 类目属性字典（4 张新表）
# ═══════════════════════════════════════════════════════════════

class OzonCategory(BaseModel):
    """OZON 类目信息缓存"""
    user = ForeignKeyField(User, backref='ozon_categories')
    ozon_category_id = CharField(max_length=50)                       # OZON 类目 ID
    name = CharField(max_length=200)                                  # 类目名（俄）
    name_cn = CharField(max_length=200, null=True)                    # 类目名（中）
    path = CharField(max_length=500, null=True)                       # 类目路径
    parent_id = CharField(max_length=50, null=True)                   # 父级类目 ID
    is_leaf = BooleanField(default=True)
    source = CharField(max_length=20, default='api')                  # api / manual
    raw_json = TextField(null=True)                                   # API 原始响应 JSON
    last_synced_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'ozon_category_id'), True),
        )


class OzonCategoryType(BaseModel):
    """OZON 类目 type_id 节点（叶子层，绑定到 description_category_id）"""
    user = ForeignKeyField(User, backref='ozon_category_types')
    account = ForeignKeyField(OzonAccount, null=True, backref='category_types')
    description_category_id = CharField(max_length=50)                 # 所属 description_category_id
    type_id = CharField(max_length=50)                                 # type_id（叶子商品类型）
    type_name = CharField(max_length=200)                              # type 名称（俄）
    type_name_cn = CharField(max_length=200, null=True)                # type 名称（中）
    path = CharField(max_length=500, null=True)                        # 类目路径
    is_active = BooleanField(default=True)
    raw_json = TextField(null=True)
    last_synced_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'description_category_id', 'type_id'), True),   # 唯一约束
        )


class OzonCategoryAttribute(BaseModel):
    """OZON 类目属性 Schema（绑定到 description_category_id + type_id）"""
    user = ForeignKeyField(User, backref='ozon_category_attributes')
    account = ForeignKeyField(OzonAccount, null=True, backref='category_attributes')
    ozon_category_id = CharField(max_length=50)                       # description_category_id
    type_id = CharField(max_length=50, null=True)                     # 绑定的 type_id
    attribute_id = CharField(max_length=50)                           # OZON 属性 ID
    name = CharField(max_length=200)                                  # 属性名（俄）
    name_cn = CharField(max_length=200, null=True)                    # 属性名（中）
    description = TextField(null=True)
    is_required = BooleanField(default=False)
    is_collection = BooleanField(default=False)                       # 是否多选
    is_dictionary = BooleanField(default=False)                       # 是否使用字典值
    dictionary_id = IntegerField(null=True)                           # OZON 字典 ID（用于拉取值列表）
    data_type = CharField(max_length=30, null=True)                   # string / number / boolean / enum / text
    unit = CharField(max_length=30, null=True)                        # mm / g / ...
    group_name = CharField(max_length=200, null=True)                 # 属性分组名
    max_value_count = IntegerField(default=1)                         # 最大取值数
    source = CharField(max_length=20, default='api')                  # api / manual
    schema_hash = CharField(max_length=64, null=True)                 # 属性 Schema 指纹（检测 OZON 字段变化）
    allowed_values_json = TextField(null=True)                        # 属性值字典 JSON
    raw_json = TextField(null=True)                                   # API 原始响应 JSON
    last_synced_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'account', 'ozon_category_id', 'type_id', 'attribute_id'), True),
        )


class OzonAttributeValue(BaseModel):
    """OZON 属性字典值"""
    user = ForeignKeyField(User, backref='ozon_attribute_values')
    account = ForeignKeyField(OzonAccount, null=True, backref='attribute_values')
    attribute_id = CharField(max_length=50)                            # 所属属性 ID
    type_id = CharField(max_length=50, null=True)                      # 绑定的 type_id
    value_id = CharField(max_length=50)                                # OZON 字典值 ID
    value = CharField(max_length=500)                                  # 字典值（俄）
    value_cn = CharField(max_length=500, null=True)                    # 字典值（中）
    info = CharField(max_length=500, null=True)                        # 附加信息
    last_synced_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'account', 'type_id', 'attribute_id', 'value_id'), True),
        )


class OzonAttributeMapping(BaseModel):
    """OZON 属性 → 本地商品事实字段 映射规则"""
    user = ForeignKeyField(User, backref='ozon_attribute_mappings')
    ozon_category_id = CharField(max_length=50)                       # 类目 ID
    attribute_id = CharField(max_length=50)                           # 属性 ID
    local_field_path = CharField(max_length=200)                      # 本地字段路径
    fill_policy = CharField(max_length=30, default='manual_required') # source_required / source_or_empty / manual_required / dictionary_match / computed / not_supported
    manual_required = BooleanField(default=False)
    default_value = CharField(max_length=200, null=True)
    confidence = FloatField(null=True)
    notes = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'ozon_category_id', 'attribute_id'), True),
        )


class OzonFieldGap(BaseModel):
    """字段缺口 — 草稿发布前缺少哪些类目必填字段"""
    user = ForeignKeyField(User, backref='ozon_field_gaps')
    draft = ForeignKeyField(OzonDraft, null=True, backref='field_gaps')
    adaptation = ForeignKeyField(ListingAdaptation, null=True, backref='field_gaps')
    ozon_category_id = CharField(max_length=50)
    attribute_id = CharField(max_length=50)
    field_name = CharField(max_length=200)
    gap_type = CharField(max_length=30)                               # missing_required / missing_dictionary_value / low_confidence / needs_manual_confirmation / format_error / unit_missing
    severity = CharField(max_length=10, default='error')             # error / warning / info
    source_status = CharField(max_length=30, null=True)               # null / unknown / low_confidence / has_value
    suggested_action = TextField(null=True)
    resolved = BooleanField(default=False)
    resolved_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'draft'), False),
            (('draft', 'resolved'), False),
        )


# ═══════════════════════════════════════════════════════════════
# OZON 同步任务 + 常用 type（新模型）
# ═══════════════════════════════════════════════════════════════

class OzonCategorySyncJob(BaseModel):
    """OZON 类目/type/属性同步任务追踪"""
    user = ForeignKeyField(User, backref='ozon_sync_jobs')
    account = ForeignKeyField(OzonAccount, null=True, backref='sync_jobs')
    job_type = CharField(max_length=30)                                  # tree / current_type / category_batch / used_types
    target_category_id = CharField(max_length=50, null=True)
    target_type_id = CharField(max_length=50, null=True)
    status = CharField(max_length=20, default='pending')               # pending / running / done / failed / partial
    total_count = IntegerField(default=0)
    processed_count = IntegerField(default=0)
    success_count = IntegerField(default=0)
    skipped_count = IntegerField(default=0)
    error_count = IntegerField(default=0)
    warnings_json = TextField(null=True)                                 # JSON list
    errors_json = TextField(null=True)                                   # JSON list
    message = TextField(null=True)
    started_at = DateTimeField(null=True)
    finished_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'status'), False),
            (('user', 'created_at'), False),
        )


class OzonFavoriteCategoryType(BaseModel):
    """用户收藏的常用 type"""
    user = ForeignKeyField(User, backref='ozon_favorite_types')
    account = ForeignKeyField(OzonAccount, null=True, backref='favorite_types')
    description_category_id = CharField(max_length=50)
    type_id = CharField(max_length=50)
    type_name = CharField(max_length=300, null=True)
    path = CharField(max_length=500, null=True)                          # 类目路径
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'description_category_id', 'type_id'), True),
        )


# ═══════════════════════════════════════════════════════════════
# OZON 视觉工具模型（3 张新表）
# ═══════════════════════════════════════════════════════════════

class VisionModelConfig(BaseModel):
    """视觉工具模型配置 — 独立于主模型"""
    user = ForeignKeyField(User, backref='vision_model_configs')
    provider = CharField(max_length=20)                               # openai_vision / qwen_vl / gemini_vision / custom_http
    model_name = CharField(max_length=100)                            # 模型名
    api_base = CharField(max_length=300)                              # API Base URL
    api_key_encrypted = CharField(max_length=500, null=True)          # 加密后的 API Key
    enabled = BooleanField(default=False)
    timeout_seconds = IntegerField(default=60)
    max_images_per_batch = IntegerField(default=5)
    notes = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'provider'), True),
        )


class ImageAnalysisJob(BaseModel):
    """图片识别任务记录"""
    user = ForeignKeyField(User, backref='image_analysis_jobs')
    media = ForeignKeyField(OzonSourceMedia, backref='analysis_jobs')
    source = ForeignKeyField(OzonSource, null=True, backref='analysis_jobs')
    draft = ForeignKeyField(OzonDraft, null=True, backref='analysis_jobs')
    task_type = CharField(max_length=30)                              # sku_image / detail_ocr / compliance_check / fact_extraction
    provider = CharField(max_length=20)
    model_name = CharField(max_length=100)
    status = CharField(max_length=20, default='pending')             # pending / running / success / failed
    request_json = TextField(null=True)                               # 请求 JSON（不含 API Key）
    response_json = TextField(null=True)                              # API 原始响应 JSON
    parsed_json = TextField(null=True)                                # 归一化后视觉识别结果 JSON
    error_message = TextField(null=True)
    processing_time_ms = IntegerField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'status'), False),
            (('media', 'task_type'), False),
        )


class ImageFact(BaseModel):
    """视觉识别结果沉淀为可人工确认的事实证据"""
    user = ForeignKeyField(User, backref='image_facts')
    image_analysis_job = ForeignKeyField(ImageAnalysisJob, backref='image_facts')
    media = ForeignKeyField(OzonSourceMedia, backref='image_facts')
    field_path = CharField(max_length=200)                             # 字段路径，如 material / skus[0].color_cn
    value = TextField()                                                # 识别值
    evidence_text = TextField(null=True)                               # 证据文本
    confidence = FloatField()                                          # 置信度 0-1
    requires_manual_confirmation = BooleanField(default=False)         # 是否需要人工确认
    accepted = BooleanField(default=False)                             # 是否被人工接受
    accepted_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'image_analysis_job'), False),
            (('media', 'field_path'), False),
        )


# ═══════════════════════════════════════════════════════════════
# OZON 在线商品管理（2 张新表）
# ═══════════════════════════════════════════════════════════════

class OzonOnlineProduct(BaseModel):
    """OZON 店铺已在线商品缓存"""
    user = ForeignKeyField(User, backref='ozon_online_products')
    account = ForeignKeyField(OzonAccount, backref='online_products')
    ozon_product_id = CharField(max_length=50)                           # OZON 商品 ID
    offer_id = CharField(max_length=200)                                 # 本地标识
    sku = IntegerField(default=1)
    name = CharField(max_length=500)                                     # 商品标题
    status = CharField(max_length=30, default='active')                  # active/hidden/archived/blocked
    visibility = CharField(max_length=30, null=True)                     # VISIBLE/HIDDEN
    is_archived = BooleanField(default=False)                            # OZON 归档状态
    price = DecimalField(max_digits=12, decimal_places=2, null=True)     # 售价 RUB
    old_price = DecimalField(max_digits=12, decimal_places=2, null=True)
    min_price = DecimalField(max_digits=12, decimal_places=2, null=True)
    currency = CharField(max_length=10, default='RUB')
    stock_present = IntegerField(default=0)                              # 可售库存
    stock_reserved = IntegerField(default=0)                             # 预留库存
    category_id = CharField(max_length=50, null=True)
    category_name = CharField(max_length=300, null=True)
    type_id = CharField(max_length=50, null=True)
    primary_image = CharField(max_length=500, null=True)
    images_json = TextField(null=True)                                   # 图片列表 JSON
    attributes_json = TextField(null=True)                               # 属性列表 JSON
    errors_json = TextField(null=True)                                   # 平台错误 JSON
    raw_json = TextField(null=True)                                      # API 原始响应
    local_is_archived = BooleanField(default=False)                      # 本地归档（软删除）
    draft_id = IntegerField(null=True)                                   # 关联刊登草稿 ID
    last_synced_at = DateTimeField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'account'), False),
            (('user', 'offer_id'), False),
            (('user', 'ozon_product_id'), False),
            (('user', 'status'), False),
            (('user', 'account', 'offer_id'), True),  # 唯一约束
        )


class OzonOnlineProductAction(BaseModel):
    """在线商品操作日志"""
    user = ForeignKeyField(User, backref='ozon_online_product_actions')
    account = ForeignKeyField(OzonAccount, null=True, backref='online_product_actions')
    online_product = ForeignKeyField(OzonOnlineProduct, null=True, backref='actions')
    action_type = CharField(max_length=30)                               # sync/archive/unarchive/update_price/update_stock/update_content
    status = CharField(max_length=20, default='pending')                 # pending/success/failed
    request_json = TextField(null=True)
    response_json = TextField(null=True)
    error_message = TextField(null=True)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'action_type'), False),
            (('online_product', 'created_at'), False),
        )


class OzonExcelTemplate(BaseModel):
    """OZON 官方 Excel 模板 — 绑定 dcid + type_id 对"""
    user = ForeignKeyField(User, backref='ozon_excel_templates')
    account = ForeignKeyField(OzonAccount, null=True, backref='excel_templates')
    dcid = CharField(max_length=50)                          # description_category_id
    type_id = CharField(max_length=50)                       # OZON type_id
    type_name = CharField(max_length=200, null=True)         # type 名称
    original_filename = CharField(max_length=300)            # 原始上传文件名
    stored_path = CharField(max_length=500)                  # 服务端存储路径
    file_size_bytes = IntegerField(null=True)                # 文件大小
    schema_hash = CharField(max_length=64)                   # 表头结构哈希（版本追踪）
    headers_json = TextField(null=True)                      # 解析出的列头 JSON
    required_columns_json = TextField(null=True)             # 必填列 JSON
    data_validations_json = TextField(null=True)             # 数据验证规则 JSON
    sheet_names_json = TextField(null=True)                  # 所有 Sheet 名称 JSON
    data_start_row = IntegerField(default=5)                 # 数据起始行号
    header_row = IntegerField(default=2)                     # 表头行号
    status = CharField(max_length=20, default='active')      # active / outdated
    notes = TextField(null=True)                             # 备注
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'dcid', 'type_id'), False),
            (('user', 'status'), False),
        )


class OzonTemplateExportJob(BaseModel):
    """OZON 模板 Excel 导出任务"""
    user = ForeignKeyField(User, backref='ozon_template_export_jobs')
    draft = ForeignKeyField(OzonDraft, backref='template_exports')
    template = ForeignKeyField(OzonExcelTemplate, backref='exports')
    export_path = CharField(max_length=500)                  # 生成的 Excel 文件路径
    field_mapping_json = TextField(null=True)                # 字段映射快照 JSON
    filled_rows_count = IntegerField(default=0)              # 填充的行数
    ozon_upload_result = CharField(max_length=20, null=True) # validated / errors / published / needs_fix
    ozon_upload_notes = TextField(null=True)                 # 上传结果备注
    created_at = DateTimeField(default=datetime.datetime.now)

    class Meta:
        indexes = (
            (('user', 'draft'), False),
            (('draft', 'created_at'), False),
        )
