from models import db, Product

def generate_sku(product):
    brand = product.brand or ''
    letters = ''.join(filter(str.isalpha, brand)).upper()
    prefix = letters[:4] if letters else 'BRD'
    return f"{prefix}{product.id:06d}"

db.connect()
# 只更新 SKU 为空或为 NULL 的产品
products = Product.select().where(Product.sku.is_null() | (Product.sku == ''))
for p in products:
    p.sku = generate_sku(p)
    p.save()
db.close()
print(f"已为 {len(products)} 个产品更新 SKU")