from models import db
db.connect()
try:
    db.execute_sql("ALTER TABLE customerorder ADD COLUMN invoice_required INTEGER DEFAULT 0")
    print("字段 invoice_required 添加成功")
except Exception as e:
    print("字段已存在或出错：", e)
db.close()