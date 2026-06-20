"""OZON 适配层 + 类目属性 + 视觉模型 数据库迁移（13 张新表）"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import db
from models import (
    # 适配层 6 张
    SourceProductGroup, SourceProductGroupItem,
    ProductFact, ProductFactSku, ProductFactEvidence,
    ListingAdaptation,
    # 类目属性 4 张
    OzonCategory, OzonCategoryAttribute,
    OzonAttributeMapping, OzonFieldGap,
    # 视觉模型 3 张
    VisionModelConfig, ImageAnalysisJob, ImageFact,
)

TABLES = [
    # 适配层
    SourceProductGroup, SourceProductGroupItem,
    ProductFact, ProductFactSku, ProductFactEvidence,
    ListingAdaptation,
    # 类目属性
    OzonCategory, OzonCategoryAttribute,
    OzonAttributeMapping, OzonFieldGap,
    # 视觉模型
    VisionModelConfig, ImageAnalysisJob, ImageFact,
]

def migrate():
    print("Creating 13 new OZON tables (adaptation + category + vision)...")
    db.create_tables(TABLES, safe=True)
    print(f"Created/verified {len(TABLES)} tables.")
    print("Done — no seed data needed for these tables.")


def rollback():
    """删除本次迁移创建的 13 张表（危险操作，需二次确认）"""
    print("WARNING: This will DROP all 13 new tables and their data!")
    confirm = input("Type 'yes' to confirm: ")
    if confirm != 'yes':
        print("Aborted.")
        return
    db.drop_tables(TABLES, safe=True)
    print(f"Dropped {len(TABLES)} tables.")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('action', nargs='?', default='migrate', choices=['migrate', 'rollback'])
    args = parser.parse_args()
    if args.action == 'rollback':
        rollback()
    else:
        migrate()
