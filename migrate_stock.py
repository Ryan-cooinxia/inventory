"""
一次性迁移：为 Product 表添加 stock 字段并初始化库存数据
运行: python migrate_stock.py
"""
from models import db, Product
from models import update_product_stock

# 添加 stock 列（SQLite 不支持 ADD COLUMN IF NOT EXISTS，用 try/except 兜底）
try:
    db.execute_sql('ALTER TABLE product ADD COLUMN stock REAL NOT NULL DEFAULT 0')
    print('[OK] Added product.stock column')
except Exception as e:
    if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
        print('[INFO] stock column already exists, skipping')
    else:
        raise

# 计算所有产品的初始库存
products = Product.select()
for p in products:
    update_product_stock(p.id)

print(f'[OK] Initialized stock for {len(list(products))} products')
