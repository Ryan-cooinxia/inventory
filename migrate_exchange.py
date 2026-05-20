from models import db, ExchangeRate

db.connect()
db.create_tables([ExchangeRate], safe=True)
db.close()
print("ExchangeRate 表创建成功")