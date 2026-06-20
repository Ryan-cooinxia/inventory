"""OZON 模块数据库迁移 — 创建 10 张新表并预置默认数据"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import db, User
from models import (
    OzonAccount, OzonSource, OzonSourceSku, OzonSourceMedia,
    OzonDraft, OzonDraftSku, OzonImageSlot, OzonPublishJob,
    OzonPrompt, OzonPricingRule,
)

TABLES = [
    OzonAccount, OzonSource, OzonSourceSku, OzonSourceMedia,
    OzonDraft, OzonDraftSku, OzonImageSlot, OzonPublishJob,
    OzonPrompt, OzonPricingRule,
]

def migrate():
    print("Creating OZON tables...")
    db.create_tables(TABLES, safe=True)
    print(f"Created/verified {len(TABLES)} tables.")

    # 预置默认提示词模板（关联到第一个用户，如果没有用户则跳过）
    first_user = User.select().first()
    if not first_user:
        print("No users found — skipping seed data.")
        return

    defaults = [
        {
            'name': '俄语标题生成（通用）',
            'prompt_type': 'title',
            'category': 'common',
            'content': (
                'Ты профессиональный копирайтер для OZON. Напиши заголовок товара на русском языке.\n'
                'Правила:\n'
                '1) длина 50-150 символов\n'
                '2) включи ключевые слова: тип товара, основная функция, материал\n'
                '3) избегай Caps Lock, лишних знаков\n'
                '4) не использовать субъективные оценки ("лучший", "самый")\n'
                'Источник: {source_title} | {source_description}'
            ),
            'variables': '["source_title", "source_description", "category"]',
            'is_default': True,
        },
        {
            'name': '俄语卖点生成（通用）',
            'prompt_type': 'bullets',
            'category': 'common',
            'content': (
                'Выдели 3-5 ключевых преимуществ товара на русском языке.\n'
                'Каждое начинай с "•". Формат: коротко, по делу.\n'
                'Правила:\n'
                '1) только фактические характеристики\n'
                '2) избегай повторов с заголовком\n'
                '3) не более 100 символов на пункт\n'
                'Товар: {source_title} | Характеристики: {attributes}'
            ),
            'variables': '["source_title", "attributes"]',
            'is_default': True,
        },
        {
            'name': '俄语描述生成（通用）',
            'prompt_type': 'description',
            'category': 'common',
            'content': (
                'Напиши подробное описание товара для OZON на русском языке.\n'
                'Структура:\n'
                '1) Введение (1-2 предложения)\n'
                '2) Основные характеристики\n'
                '3) Комплектация\n'
                '4) Применение\n'
                'Правила: не использовать HTML-теги, избегать субъективных оценок.\n'
                'Товар: {source_title} | Характеристики: {attributes} | Комплектация: {package_content}'
            ),
            'variables': '["source_title", "attributes", "package_content"]',
            'is_default': True,
        },
        {
            'name': '主图提示词（通用）',
            'prompt_type': 'image',
            'category': 'common',
            'content': (
                'Создай изображение товара для маркетплейса.\n'
                'Стиль: {style}. Формат: 3:4. Фон: белый.\n'
                'Товар: {product_name}, цвет {color}.\n'
                'Запрещено: китайский текст, логотипы, цены, водяные знаки, QR-коды.'
            ),
            'variables': '["style", "product_name", "color"]',
            'is_default': True,
        },
    ]

    for d in defaults:
        existing = OzonPrompt.select().where(
            (OzonPrompt.user == first_user) &
            (OzonPrompt.prompt_type == d['prompt_type']) &
            (OzonPrompt.category == d['category']) &
            (OzonPrompt.is_default == True)
        ).first()
        if not existing:
            OzonPrompt.create(user=first_user, **d)
            print(f"  Seeded prompt: {d['name']}")

    # 预置默认定价规则
    rule = OzonPricingRule.select().where(
        (OzonPricingRule.user == first_user) &
        (OzonPricingRule.is_default == True)
    ).first()
    if not rule:
        OzonPricingRule.create(
            user=first_user,
            name='默认定价规则',
            is_default=True,
            exchange_rate_source='auto',
            target_margin_rate=0.35,
            ad_reserve_rate=0.05,
            commission_rate=0.10,
            risk_buffer_type='fixed',
            risk_buffer_value=3.0,
            logistics_tiers='[{"weight_min":0,"weight_max":500,"domestic":3.0,"international":12.0,"packaging":1.5},{"weight_min":501,"weight_max":1000,"domestic":5.0,"international":18.0,"packaging":2.0},{"weight_min":1001,"weight_max":2000,"domestic":8.0,"international":25.0,"packaging":3.0}]',
        )
        print("  Seeded default pricing rule.")

    print("Migration complete.")


if __name__ == '__main__':
    migrate()
