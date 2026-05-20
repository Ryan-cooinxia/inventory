from models import db, User
from werkzeug.security import generate_password_hash

db.connect()

# 创建 User 表（如果还未创建）
db.create_tables([User], safe=True)

# 创建默认管理员
if not User.select().where(User.username == 'admin').exists():
    User.create(
        username='admin',
        password_hash=generate_password_hash('admin123'),
        display_name='管理员',
        is_admin=True
    )
    print("已创建默认管理员账号：admin / admin123")

# 需要添加 user_id 列的所有表（与 models.py 中定义 user 字段的表一致）
tables = [
    'product', 'customer', 'supplier',
    'purchaseorder', 'purchaseorderitem',
    'salesorder', 'salesorderitem',
    'customerorder', 'customerorderitem',
    'supplierorder', 'supplierorderitem',
    'customerrefund', 'customertransaction'
]

for table in tables:
    try:
        db.execute_sql(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER REFERENCES user(id)")
        print(f"{table} → user_id 列已添加")
    except Exception as e:
        # 列已存在或其他错误，打印出来
        print(f"{table} → {e}")

# 将现有数据的 user_id 统一设为管理员（id=1）
admin = User.get_or_none(User.id == 1)
if admin:
    for table in tables:
        db.execute_sql(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (admin.id,))
    print("已将所有现有数据关联到管理员")

db.close()
print("迁移完成！")