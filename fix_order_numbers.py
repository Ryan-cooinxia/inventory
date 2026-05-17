from models import db

db.connect()
orders = db.execute_sql("SELECT id, order_date FROM supplierorder WHERE order_number IS NULL").fetchall()
for order in orders:
    order_id, order_date = order
    new_number = f"MD-{order_date.replace('-', '')}-{order_id:04d}"
    db.execute_sql("UPDATE supplierorder SET order_number = ? WHERE id = ?", (new_number, order_id))
db.close()
print(f"已为 {len(orders)} 条历史订单补充单号！")