from models import db

db.connect()

# 供应商订单主表
db.execute_sql("""
CREATE TABLE IF NOT EXISTS supplierorder (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER NOT NULL,
    order_date DATE NOT NULL,
    total_amount REAL DEFAULT 0,
    status VARCHAR(20) DEFAULT 'pending',
    estimated_delivery DATE,
    remark TEXT,
    FOREIGN KEY (supplier_id) REFERENCES supplier(id)
)
""")

# 供应商订单明细表
db.execute_sql("""
CREATE TABLE IF NOT EXISTS supplierorderitem (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL,
    subtotal REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES supplierorder(id),
    FOREIGN KEY (product_id) REFERENCES product(id)
)
""")

# 给采购入库单表增加 supplier_order_id 字段（可选关联）
try:
    db.execute_sql("ALTER TABLE purchaseorder ADD COLUMN supplier_order_id INTEGER REFERENCES supplierorder(id)")
except Exception as e:
    print("purchaseorder.supplier_order_id 可能已存在")

db.close()
print("供应商订单模块迁移完成！")