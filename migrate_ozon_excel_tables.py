"""OZON 官方 Excel 模板发布通道 — 数据库迁移（2 张新表）

新增:
  OzonExcelTemplate      — 上传的官方 Excel 模板
  OzonTemplateExportJob  — 模板导出生成记录
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import db, OzonExcelTemplate, OzonTemplateExportJob

TABLES = [OzonExcelTemplate, OzonTemplateExportJob]


def migrate():
    print("Creating OZON Excel template tables...")
    db.create_tables(TABLES, safe=True)
    print(f"  Created/verified {len(TABLES)} tables.")
    print("Done — OzonExcelTemplate + OzonTemplateExportJob ready.")


def rollback():
    print("WARNING: This will DROP the 2 Excel template tables and all their data!")
    confirm = input("Type 'yes' to confirm: ")
    if confirm != 'yes':
        print("Aborted.")
        return
    db.drop_tables(TABLES, safe=True)
    print(f"  Dropped {len(TABLES)} tables.")


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('action', nargs='?', default='migrate', choices=['migrate', 'rollback'])
    args = p.parse_args()
    if args.action == 'rollback':
        rollback()
    else:
        migrate()
