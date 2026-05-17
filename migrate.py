from models import db

db.connect()

# 给采购入库单表增加两列
db.execute_sql("ALTER TABLE purchaseorder ADD COLUMN ship_method VARCHAR(50)")
db.execute_sql("ALTER TABLE purchaseorder ADD COLUMN tracking_number VARCHAR(100)")

# 给销售出库单表增加两列
db.execute_sql("ALTER TABLE salesorder ADD COLUMN ship_method VARCHAR(50)")
db.execute_sql("ALTER TABLE salesorder ADD COLUMN tracking_number VARCHAR(100)")

db.close()
print("数据库迁移完成！")