from models import db

db.connect()
db.execute_sql("ALTER TABLE customer ADD COLUMN address TEXT")
db.close()
print("客户地址字段添加成功！")