# helpers.py
from models import (Product, PurchaseOrder, PurchaseOrderItem,
    SalesOrder, SalesOrderItem,
    ProductSplitOrder, ProductSplitOrderItem,
    ProductAssemblyOrder, ProductAssemblyOrderItem)
from peewee import fn

def parse_positive_float(value):
    """解析必须大于 0 的数字，失败时返回 None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

def parse_non_negative_float(value):
    """解析必须大于等于 0 的数字，失败时返回 None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None

def get_product_stock(product_id, user=None):
    """返回指定产品的当前库存（优先读缓存字段 stock）"""
    if user is None:
        product = Product.get_or_none(Product.id == product_id)
        return product.stock if product else 0

    # 多用户场景下仍需实时聚合
    in_query = (PurchaseOrderItem
                .select(fn.SUM(PurchaseOrderItem.quantity))
                .where(PurchaseOrderItem.product == product_id))
    out_query = (SalesOrderItem
                 .select(fn.SUM(SalesOrderItem.quantity))
                 .where(SalesOrderItem.product == product_id))
    in_query = (in_query
                .join(PurchaseOrder)
                .where(PurchaseOrder.user == user))
    out_query = (out_query
                 .join(SalesOrder)
                 .where(SalesOrder.user == user))

    # 拆包产出（增加库存）
    split_in = (ProductSplitOrderItem
                .select(fn.SUM(ProductSplitOrderItem.quantity))
                .join(ProductSplitOrder)
                .where((ProductSplitOrderItem.target_product == product_id) &
                       (ProductSplitOrder.user == user) &
                       (ProductSplitOrder.status == 'confirmed'))
                .scalar()) or 0

    # 拆包消耗（减少库存）
    split_out = (ProductSplitOrder
                 .select(fn.SUM(ProductSplitOrder.source_quantity))
                 .where((ProductSplitOrder.source_product == product_id) &
                        (ProductSplitOrder.user == user) &
                        (ProductSplitOrder.status == 'confirmed'))
                 .scalar()) or 0

    # 组合产出（增加库存）：套装被组装出来
    assembly_in = (ProductAssemblyOrder
                   .select(fn.SUM(ProductAssemblyOrder.bundle_quantity))
                   .where((ProductAssemblyOrder.bundle_product == product_id) &
                          (ProductAssemblyOrder.user == user) &
                          (ProductAssemblyOrder.status == 'confirmed'))
                   .scalar()) or 0

    # 组合消耗（减少库存）：零件被用于组装
    assembly_out = (ProductAssemblyOrderItem
                    .select(fn.SUM(ProductAssemblyOrderItem.quantity))
                    .join(ProductAssemblyOrder)
                    .where((ProductAssemblyOrderItem.component_product == product_id) &
                           (ProductAssemblyOrder.user == user) &
                           (ProductAssemblyOrder.status == 'confirmed'))
                    .scalar()) or 0

    total_in = in_query.scalar() or 0
    total_out = out_query.scalar() or 0
    return total_in - total_out - split_out + split_in - assembly_out + assembly_in

def check_stock_before_ship(items, extra_items=None, user=None):
    """检查库存是否足够，返回 (is_ok, error_messages)"""
    required = {}
    for it in items:
        pid = it['product_id'] if isinstance(it, dict) else it.product.id
        qty = it['quantity'] if isinstance(it, dict) else it.quantity
        required[pid] = required.get(pid, 0) + qty
    if extra_items:
        for it in extra_items:
            pid = it['product_id'] if isinstance(it, dict) else it.product.id
            qty = it['quantity'] if isinstance(it, dict) else it.quantity
            required[pid] = required.get(pid, 0) + qty

    errors = []
    for pid, need_qty in required.items():
        stock = get_product_stock(pid, user=user)
        if need_qty > stock:
            query = Product.id == pid
            if user is not None:
                query = query & (Product.user == user)
            product = Product.get_or_none(query)
            name = product.name if product else f'产品#{pid}'
            errors.append(f'{name}：需要发货 {need_qty}，当前库存仅 {stock}')
    return len(errors) == 0, errors

def generate_sku(product):
    """根据品牌生成 SKU"""
    brand = product.brand or ''
    letters = ''.join(filter(str.isalpha, brand)).upper()
    prefix = letters[:4] if letters else 'BRD'
    return f"{prefix}{product.id:06d}"
