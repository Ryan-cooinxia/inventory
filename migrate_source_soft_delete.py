"""迁移：OzonSource 增加 deleted_at 软删除字段"""
import sys
sys.path.insert(0, '.')

from models import db, OzonSource

db.connect(reuse_if_open=True)

# 添加 deleted_at 列
db.execute_sql("ALTER TABLE ozonsource ADD COLUMN deleted_at DATETIME")

print("✓ OzonSource.deleted_at 列已添加")
db.close()
