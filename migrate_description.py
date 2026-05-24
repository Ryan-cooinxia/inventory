from models import db
db.connect()
try:
    db.execute_sql("ALTER TABLE product ADD COLUMN description TEXT")
    print("description 列添加成功")
except Exception as e:
    print(e)
db.close()