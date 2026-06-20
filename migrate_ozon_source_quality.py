"""Add OZON source quality columns used by the collection review flow."""
import sys

sys.path.insert(0, '.')

from models import db


MIGRATIONS = {
    'ozonsource': [
        ('quality_json', 'TEXT'),
        ('detail_missing', 'INTEGER NOT NULL DEFAULT 0'),
        ('price_manual_confirmed', 'INTEGER NOT NULL DEFAULT 0'),
    ],
    'ozonsourcemedia': [
        ('compliance_status', 'VARCHAR(20)'),
        ('reject_reason', 'VARCHAR(200)'),
        ('raw_json', 'TEXT'),
    ],
}


def add_missing_columns():
    db.connect(reuse_if_open=True)
    try:
        for table, columns in MIGRATIONS.items():
            existing = {row[1] for row in db.execute_sql(f'PRAGMA table_info({table})').fetchall()}
            if not existing:
                print(f'[SKIP] {table} does not exist')
                continue

            for name, ddl in columns:
                if name in existing:
                    print(f'[SKIP] {table}.{name} already exists')
                    continue
                db.execute_sql(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
                print(f'[OK] added {table}.{name}')
    finally:
        if not db.is_closed():
            db.close()


if __name__ == '__main__':
    add_missing_columns()
