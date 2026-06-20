"""OZON Phase 3: 新增同步任务模型 + 常用type模型 + 索引更新"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import app
from models import db, OzonCategorySyncJob, OzonFavoriteCategoryType
from models import OzonCategoryAttribute, OzonAttributeValue

def migrate():
    with app.app_context():
        db.connect(reuse_if_open=True)
        cursor = db.execute_sql

        # 1. 创建新表
        db.create_tables([OzonCategorySyncJob, OzonFavoriteCategoryType], safe=True)
        print("[OK] OzonCategorySyncJob + OzonFavoriteCategoryType")

        # 2. 给 OzonAttributeValue 加 type_id 列
        try:
            cursor("ALTER TABLE ozonattributevalue ADD COLUMN type_id VARCHAR(50)")
            print("[OK] 添加 ozonattributevalue.type_id")
        except Exception as e:
            if 'duplicate' in str(e).lower() or 'already' in str(e).lower():
                print("[SKIP] ozonattributevalue.type_id 已存在")
            else:
                print(f"[OK] {e}")

        # 3. 重建唯一索引 — OzonCategoryAttribute
        try:
            cursor("CREATE UNIQUE INDEX IF NOT EXISTS "
                   "ozoncategoryattribute_user_account_cat_type_attr "
                   "ON ozoncategoryattribute (user_id, account_id, ozon_category_id, type_id, attribute_id)")
            print("[OK] 索引 ozoncategoryattribute (user,account,cat,type,attr)")
        except Exception as e:
            print(f"[WARN] {e}")

        # 4. 重建唯一索引 — OzonAttributeValue
        try:
            cursor("CREATE UNIQUE INDEX IF NOT EXISTS "
                   "ozonattributevalue_user_account_type_attr_val "
                   "ON ozonattributevalue (user_id, account_id, type_id, attribute_id, value_id)")
            print("[OK] 索引 ozonattributevalue (user,account,type,attr,val)")
        except Exception as e:
            print(f"[WARN] {e}")

        db.close()
        print("\n✅ 迁移完成")


if __name__ == '__main__':
    migrate()
