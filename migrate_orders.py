from models import db

db.connect()

# 创建客户订单表
db.execute_sql("""
CREATE TABLE IF NOT EXISTS customerorder (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    order_date DATE NOT NULL,
    total_amount REAL NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    remark TEXT,
    FOREIGN KEY (customer_id) REFERENCES customer(id)
)
""")

# 创建订单明细表
db.execute_sql("""
CREATE TABLE IF NOT EXISTS customerorderitem (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL,
    subtotal REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES customerorder(id),
    FOREIGN KEY (product_id) REFERENCES product(id)
)
""")

# 给 salesorder 表增加 customer_order_id 字段（可选关联到订单）
try:
    db.execute_sql("ALTER TABLE salesorder ADD COLUMN customer_order_id INTEGER REFERENCES customerorder(id)")
except Exception as e:
    print("salesorder.customer_order_id 可能已存在")

# 给 customerrefund 表增加 customer_order_id 字段
try:
    db.execute_sql("ALTER TABLE customerrefund ADD COLUMN customer_order_id INTEGER REFERENCES customerorder(id)")
except Exception as e:
    print("customerrefund.customer_order_id 可能已存在")

db.close()
print("订单模块迁移完成！")