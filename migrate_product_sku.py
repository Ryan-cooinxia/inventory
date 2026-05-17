from models import db

db.connect()

try:
    db.execute_sql("ALTER TABLE product ADD COLUMN sku VARCHAR(50)")
except Exception as e:
    print(f"sku 列可能已存在：{e}")
try:
    db.execute_sql("ALTER TABLE product ADD COLUMN brand VARCHAR(50) DEFAULT 'DJI'")
except Exception as e:
    print(f"brand 列可能已存在：{e}")
try:
    db.execute_sql("ALTER TABLE product ADD COLUMN category1 VARCHAR(50)")
except Exception as e:
    print(f"category1 列可能已存在：{e}")
try:
    db.execute_sql("ALTER TABLE product ADD COLUMN category2 VARCHAR(50)")
except Exception as e:
    print(f"category2 列可能已存在：{e}")

db.close()
print("产品表迁移完成！")