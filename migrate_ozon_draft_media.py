"""OZON 草稿媒体池 + 富文本 JSON + SKU 条码 迁移

新增列：
  ozondraft.media_json         TEXT  -- 草稿媒体池 JSON（图片+视频统一管理）
  ozondraft.rich_content_json  TEXT  -- 富文本块 JSON
  ozondraftsku.barcode         VARCHAR(100) -- 条码
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import db


def migrate():
    print("Adding ozondraft.media_json ...")
    try:
        db.execute_sql("ALTER TABLE ozondraft ADD COLUMN media_json TEXT")
        print("  OK")
    except Exception as e:
        if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
            print(f"  SKIP (already exists): {e}")
        else:
            raise

    print("Adding ozondraft.rich_content_json ...")
    try:
        db.execute_sql("ALTER TABLE ozondraft ADD COLUMN rich_content_json TEXT")
        print("  OK")
    except Exception as e:
        if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
            print(f"  SKIP (already exists): {e}")
        else:
            raise

    print("Adding ozondraftsku.barcode ...")
    try:
        db.execute_sql("ALTER TABLE ozondraftsku ADD COLUMN barcode VARCHAR(100)")
        print("  OK")
    except Exception as e:
        if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
            print(f"  SKIP (already exists): {e}")
        else:
            raise

    print("Done — ozondraft media/rich_content + ozondraftsku barcode migration complete.")


def rollback():
    """删除本次迁移添加的列（危险，需确认）"""
    confirm = input("Type 'yes' to confirm dropping columns: ")
    if confirm != 'yes':
        print("Aborted.")
        return
    # SQLite 不支持 DROP COLUMN，需要重建表（跳过自动回滚）
    print("WARNING: SQLite does not support DROP COLUMN.")
    print("To rollback, manually recreate the tables without these columns.")
    print("Or restore from backup.")


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--rollback', action='store_true')
    args = p.parse_args()
    if args.rollback:
        rollback()
    else:
        migrate()
