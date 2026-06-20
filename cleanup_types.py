"""清理因 get_category_types bug 导致的脏数据 —— 运行一次即可"""
import sys
sys.path.insert(0, '.')
from models import db, OzonCategoryType
from app import app

with app.app_context():
    db.connect(reuse_if_open=True)

    before = OzonCategoryType.select().count()
    print(f'当前 type 总数: {before}')

    # 删除所有 type 记录（之后用修复后的代码重新同步）
    deleted = OzonCategoryType.delete().execute()

    after = OzonCategoryType.select().count()
    print(f'已删除: {deleted} 条')
    print(f'剩余: {after} 条')
    print('\n✅ 清理完成。请在前端用 ⚡ 一键同步全部 重新拉取。')

    db.close()
