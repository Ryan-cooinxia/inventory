from models import db
db.connect()
try:
    db.execute_sql("ALTER TABLE supplierorder ADD COLUMN order_number VARCHAR(50)")
except Exception as e:
    print(e)
db.close()
print("订单单号字段添加完成")