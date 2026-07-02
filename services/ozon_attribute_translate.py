"""OZON 属性翻译/校准服务

功能：
  - translate_source_attributes: 翻译采集源属性为中文
  - align_to_ozon_dictionary: 校准到 OZON 模板下拉值
  - 本地词典 → OZON 字典 value_cn → 模板下拉 → AI 兜底
"""
import re as _re
from datetime import datetime

# ── 本地词典（属性名）──
_NAME_DICT = {
    "Платформа": "平台", "Функции": "功能", "Артикул": "货号",
    "Тип": "类型", "Тип связи": "连接类型", "Длина кабеля, м": "线长，米",
    "Обратная связь": "反馈方式", "Количество кнопок": "按键数量",
    "Страна-изготовитель": "制造国", "Цвет": "颜色", "Материал": "材质",
    "Гарантия": "保修期", "Вес товара, г": "商品重量，克",
    "Размеры, мм": "尺寸，毫米", "Совместимость с фотокамерами": "与相机兼容性",
    "Назначение": "用途", "Комплектация": "包装内容",
    "Количество заводских упаковок": "出厂包装数量", "Бренд": "品牌",
    "Название модели": "型号名称", "Партномер": "零件号",
    "Наличие серийного номера": "是否有序列号",
    "Количество товара в УЕИ": "商品数量",
}

# ── 本地词典（属性值）──
_VALUE_DICT = {
    "Черный": "黑色", "черный": "黑色", "Белый": "白色", "белый": "白色",
    "Красный": "红色", "красный": "红色", "Синий": "蓝色", "синий": "蓝色",
    "Зеленый": "绿色", "зеленый": "绿色", "Серый": "灰色", "серый": "灰色",
    "Коричневый": "棕色", "коричневый": "棕色", "Бежевый": "米色", "бежевый": "米色",
    "Китай": "中国", "Геймпад": "游戏手柄",
    "Проводной": "有线", "Беспроводной": "无线",
    "Вибрация": "震动", "6 месяцев": "6个月", "1 год": "1年",
    "Bluetooth": "Bluetooth", "Пластик": "塑料", "Металл": "金属",
}

# ── 专有名词保护 ──
_SKIP_TRANSLATE = {
    'ps4', 'ps5', 'playstation', 'xbox', 'switch', 'nintendo',
    'pc', 'windows', 'ios', 'android', 'bluetooth', 'linux',
    'dji', 'canon', 'nikon', 'sony', 'gopro', 'samsung', 'apple',
    'xiaomi', 'huawei', 'bose', 'jbl', 'sennheiser', 'shure',
    'rode', 'zoom', 'smallrig', 'adobe', 'usb', 'hdmi', 'wifi',
    'led', 'lcd', 'oled', '4k', '8k', '1080p', '60fps', '120fps',
    'gps', 'nfc', 'rfid', 'sim', 'sd', 'microsd', 'ssd', 'hdd',
}


def is_proper_noun(value):
    """判断值是否为专有名词（不翻译）"""
    if not value:
        return False
    v = str(value).strip().lower()
    if v in _SKIP_TRANSLATE:
        return True
    # 纯 ASCII + 数字/符号 = 大概率专有名词
    if all(c.isascii() for c in v) and any(c.isalpha() for c in v):
        return True
    return False


def translate_attribute_name(name_ru, user=None):
    """翻译属性名：本地词典 → DB 缓存 → 返回原文"""
    if not name_ru:
        return ''
    name_ru = name_ru.strip()
    # 本地词典
    cn = _NAME_DICT.get(name_ru)
    if cn:
        return cn
    # DB 缓存
    if user:
        try:
            from models import OzonAttributeTranslationCache
            rec = (OzonAttributeTranslationCache
                   .select()
                   .where((OzonAttributeTranslationCache.user == user) &
                          (OzonAttributeTranslationCache.raw_name == name_ru) &
                          (OzonAttributeTranslationCache.name_cn.is_null(False)))
                   .first())
            if rec and rec.name_cn:
                return rec.name_cn
        except Exception:
            pass
    return name_ru  # 兜底返回原文


def translate_attribute_value(value_ru, user=None):
    """翻译属性值：本地词典 → DB 缓存 → OZON 字典 → 返回原文"""
    if not value_ru:
        return '', 'unknown'
    value_ru = value_ru.strip()

    # 专有名词不翻译
    if is_proper_noun(value_ru):
        return value_ru, 'proper_noun'

    # 本地词典
    cn = _VALUE_DICT.get(value_ru) or _VALUE_DICT.get(value_ru.lower())
    if cn:
        return cn, 'glossary'

    # DB 缓存
    if user:
        try:
            from models import OzonAttributeTranslationCache
            rec = (OzonAttributeTranslationCache
                   .select()
                   .where((OzonAttributeTranslationCache.user == user) &
                          (OzonAttributeTranslationCache.raw_value == value_ru) &
                          (OzonAttributeTranslationCache.value_cn.is_null(False)))
                   .first())
            if rec and rec.value_cn:
                return rec.value_cn, 'cache'
        except Exception:
            pass

    # OZON 字典
    if user:
        try:
            from models import OzonAttributeValue
            dv = (OzonAttributeValue
                  .select()
                  .where((OzonAttributeValue.user == user) &
                         (OzonAttributeValue.value == value_ru) &
                         (OzonAttributeValue.value_cn.is_null(False)) &
                         (OzonAttributeValue.value_cn != '') &
                         (OzonAttributeValue.value_cn != value_ru))
                  .first())
            if dv:
                return dv.value_cn, 'ozon_dict'
        except Exception:
            pass

    # 无法翻译
    return value_ru, 'needs_review'


def translate_source_attributes(src_attrs, user=None):
    """翻译采集源属性列表，返回 (translated_items, stats)"""
    items = []
    stats = {'translated': 0, 'proper_nouns': 0, 'needs_review': 0, 'aligned': 0}

    for sa in src_attrs:
        raw_name = sa.get('name') or sa.get('key') or ''
        raw_value = str(sa.get('value') or sa.get('text') or '')

        name_cn = translate_attribute_name(raw_name, user)
        value_cn, source = translate_attribute_value(raw_value, user)

        # 校准到 OZON 字典
        aligned = False
        if source == 'needs_review' and user:
            aligned = align_to_ozon_dict_by_text(raw_value, user, sa)
            if aligned:
                source = 'ozon_dict'
                stats['aligned'] += 1

        item = {
            'raw_name': raw_name, 'raw_value': raw_value,
            'name_cn': name_cn, 'value_cn': value_cn,
            'status': 'confirmed' if source in ('glossary', 'ozon_dict', 'cache') else 'needs_review',
            'source': source,
        }
        items.append(item)

        if source == 'proper_noun':
            stats['proper_nouns'] += 1
        elif source == 'needs_review':
            stats['needs_review'] += 1
        else:
            stats['translated'] += 1

    # 保存到缓存
    if user:
        _save_translation_cache(user, items)

    return items, stats


def align_to_ozon_dict_by_text(value_ru, user, source_attr=None):
    """尝试将俄语值匹配到 OZON 字典值，返回匹配的 value_cn 或 None"""
    if not value_ru or not user:
        return None
    try:
        from models import OzonAttributeValue
        dv = (OzonAttributeValue
              .select()
              .where((OzonAttributeValue.user == user) &
                     (OzonAttributeValue.value == value_ru.strip()) &
                     (OzonAttributeValue.value_cn.is_null(False)) &
                     (OzonAttributeValue.value_cn != ''))
              .first())
        if dv:
            return dv.value_cn
    except Exception:
        pass
    return None


def _save_translation_cache(user, items):
    """保存翻译结果到缓存表"""
    try:
        from models import OzonAttributeTranslationCache
        for item in items:
            if item['status'] != 'confirmed':
                continue
            OzonAttributeTranslationCache.get_or_create(
                user=user,
                raw_name=item['raw_name'],
                raw_value=item['raw_value'] or '',
                defaults={
                    'name_cn': item['name_cn'] or None,
                    'value_cn': item['value_cn'] or None,
                    'source': item['source'],
                    'confidence': 0.9 if item['source'] == 'glossary' else 0.7,
                }
            )
    except Exception:
        pass
