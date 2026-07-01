"""迁移：OzonAccount 新增店铺语言/货币配置字段"""
import sys
sys.path.insert(0, '.')

from models import db, OzonAccount

MIGRATION_NAME = 'ozon_account_locale_currency'


def migrate():
    """添加 seller_ui_language, template_language, default_currency 等字段"""
    field_specs = [
        ('seller_ui_language', 'VARCHAR(10) DEFAULT \'zh\''),
        ('template_language', 'VARCHAR(10) DEFAULT \'zh\''),
        ('default_currency', 'VARCHAR(10) DEFAULT \'CNY\''),
        ('currency_confirmed', 'BOOLEAN DEFAULT 0'),
        ('locale_confirmed_at', 'DATETIME'),
    ]
    for field_name, field_spec in field_specs:
        try:
            db.execute_sql(
                f'ALTER TABLE ozonaccount ADD COLUMN {field_name} {field_spec};'
            )
            print(f'  [+ ] {field_name} ({field_spec})')
        except Exception as e:
            if 'duplicate column' in str(e).lower() or 'already exists' in str(e).lower():
                print(f'  [=] {field_name} already exists')
            else:
                raise

    print(f'\n[{MIGRATION_NAME}] Migration complete.')


def rollback():
    columns = [
        'seller_ui_language', 'template_language',
        'default_currency', 'currency_confirmed', 'locale_confirmed_at',
    ]
    for col in columns:
        try:
            db.execute_sql(f'ALTER TABLE ozonaccount DROP COLUMN {col};')
            print(f'  [- ] {col}')
        except Exception as e:
            print(f'  [x] {col}: {e}')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--rollback', action='store_true')
    args = p.parse_args()

    if args.rollback:
        rollback()
    else:
        migrate()
