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


def normalize_source_attributes(source, user, force=False):
    """
    对 source.raw_json 中的 source_attributes 做双语标准化，
    写入 raw_json.localized.source_attributes，保存 source。
    返回 stats。
    """
    import json
    raw = {}
    try: raw = json.loads(source.raw_json or '{}')
    except: raw = {}

    src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
    if not src_attrs:
        return {'translated': 0, 'message': '无源属性'}

    # 已有 localized 且非强制 → 跳过
    loc = raw.get('localized', {})
    existing = loc.get('source_attributes', [])
    if existing and not force:
        return {'translated': len(existing), 'message': '已有双语数据', 'skipped': True}

    items, stats = translate_source_attributes(src_attrs, user)

    localized_attrs = []
    for item in items:
        localized_attrs.append({
            'raw_name': item['raw_name'],
            'raw_value': item['raw_value'],
            'name_cn': item['name_cn'],
            'value_cn': item['value_cn'],
            'status': item['status'],
            'source': item['source'],
            'confidence': 0.95 if item['source'] == 'glossary' else (0.85 if item['source'] == 'ozon_dict' else 0.6),
        })

    loc['source_attributes'] = localized_attrs
    from datetime import datetime
    loc['translated_at'] = datetime.now().isoformat()
    raw['localized'] = loc
    source.raw_json = json.dumps(raw, ensure_ascii=False)
    try:
        source.save()
    except Exception:
        pass

    return stats


def get_localized_source_attributes(source):
    """
    优先读取 localized.source_attributes，
    不存时 fallback 为原始 source_attributes 的简单包装。
    """
    import json
    raw = {}
    try: raw = json.loads(source.raw_json or '{}')
    except: raw = {}

    loc = raw.get('localized', {})
    loc_attrs = loc.get('source_attributes', [])
    if loc_attrs:
        return loc_attrs

    # fallback：简单包装原始属性
    src_attrs = raw.get('source_attributes') or raw.get('specs_json') or []
    wrapped = []
    for sa in src_attrs:
        wrapped.append({
            'raw_name': sa.get('name') or sa.get('key') or '',
            'raw_value': str(sa.get('value') or sa.get('text') or ''),
            'name_cn': sa.get('name_cn') or sa.get('name') or '',
            'value_cn': sa.get('value_cn') or str(sa.get('value') or ''),
            'status': 'needs_review',
            'source': 'fallback',
            'confidence': 0.5,
        })
    return wrapped


# ── 商品意图识别（用于类目推荐）──
CATEGORY_INTENT_RULES = [
    {
        'intent': 'gamepad', 'cn': '游戏手柄/游戏机配件',
        'keywords': ['джойстик', 'геймпад', 'gamepad', 'joystick', 'controller',
                     'ps4', 'ps5', 'playstation', 'xbox', 'nintendo'],
        'preferred_kw': ['игров', 'аксессуар', 'контроллер', '游戏', '手柄', '配件'],
        'conflicts': ['микрофон', 'наушник', 'камер', 'экшн'],
    },
    {
        'intent': 'action_camera', 'cn': '运动相机',
        'keywords': ['экшн камер', 'action camera', 'osmo action', 'gopro', '运动相机'],
        'preferred_kw': ['камер', 'экшн', 'спорт'],
        'conflicts': ['микрофон', 'наушник', 'джойстик', 'геймпад'],
    },
    {
        'intent': 'microphone', 'cn': '麦克风',
        'keywords': ['микрофон', 'microphone', 'mic', 'dji mic', '麦克风', '话筒'],
        'preferred_kw': ['микрофон', 'аудио', 'звук'],
        'conflicts': ['камер', 'джойстик', 'геймпад', 'наушник'],
    },
    {
        'intent': 'camera_accessory', 'cn': '相机配件',
        'keywords': ['видоискател', 'фильтр', 'адаптер', 'переходник', 'крышка',
                     '取景器', '遮光罩', '转接环'],
        'preferred_kw': ['аксессуар', 'фото', 'камер', '配件'],
    },
    {
        'intent': 'headphones', 'cn': '耳机',
        'keywords': ['наушник', 'headphon', '耳机', 'гарнитур', 'earbud'],
        'preferred_kw': ['наушник', 'аудио', 'звук'],
        'conflicts': ['микрофон', 'камер', 'джойстик'],
    },
    {
        'intent': 'drone', 'cn': '无人机',
        'keywords': ['квадрокоптер', 'дрон', 'drone', '无人机'],
        'preferred_kw': ['дрон', 'квадрокоптер', 'летател'],
        'conflicts': ['микрофон', 'наушник', 'джойстик'],
    },
]


def infer_product_intent(title, attr_names='', attr_values=''):
    """从标题+属性推断商品意图（用于类目推荐的前置步骤）"""
    import re as _re_intent
    text = _re_intent.sub(r'\s+', ' ', str(title or '') + ' ' + str(attr_names or '') + ' ' + str(attr_values or '')).lower()
    scores = []
    for rule in CATEGORY_INTENT_RULES:
        hits = sum(1 for kw in rule['keywords'] if kw.lower() in text)
        conflict_hits = sum(1 for kw in rule.get('conflicts', []) if kw.lower() in text)
        if hits > 0 and conflict_hits == 0:
            scores.append((rule, hits))
        elif hits > conflict_hits:
            scores.append((rule, hits - conflict_hits))
    scores.sort(key=lambda x: -x[1])
    if scores:
        return scores[0][0]  # 返回最佳匹配规则
    return None


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
