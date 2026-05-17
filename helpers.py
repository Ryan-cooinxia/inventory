# helpers.py
from models import Product, PurchaseOrderItem, SalesOrderItem
from peewee import fn

def get_product_stock(product_id):
    """返回指定产品的当前库存"""
    total_in = (PurchaseOrderItem
                .select(fn.SUM(PurchaseOrderItem.quantity))
                .where(PurchaseOrderItem.product == product_id)
                .scalar()) or 0
    total_out = (SalesOrderItem
                 .select(fn.SUM(SalesOrderItem.quantity))
                 .where(SalesOrderItem.product == product_id)
                 .scalar()) or 0
    return total_in - total_out

def check_stock_before_ship(items, extra_items=None):
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
        stock = get_product_stock(pid)
        if need_qty > stock:
            product = Product.get_or_none(Product.id == pid)
            name = product.name if product else f'产品#{pid}'
            errors.append(f'{name}：需要发货 {need_qty}，当前库存仅 {stock}')
    return len(errors) == 0, errors

def generate_sku(product):
    """根据品牌生成 SKU"""
    brand = product.brand or ''
    letters = ''.join(filter(str.isalpha, brand)).upper()
    prefix = letters[:4] if letters else 'BRD'
    return f"{prefix}{product.id:06d}"