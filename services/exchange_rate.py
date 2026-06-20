"""
汇率服务 — 后台定时更新，请求只读缓存
"""
import threading
import datetime
import requests
from models import db, ExchangeRate

API_URL = "https://api.exchangerate-api.com/v4/latest/CNY"
NEEDED_CURRENCIES = ['RUB', 'USD', 'EUR', 'GBP']
UPDATE_INTERVAL = 3600  # 每小时更新一次
_lock = threading.Lock()
_updater_started = False


def fetch_online_rates():
    """从网络获取最新汇率"""
    try:
        resp = requests.get(API_URL, timeout=10)
        data = resp.json()
        return data.get('rates', {})
    except Exception as e:
        print(f"[汇率] 获取失败：{e}")
        return None


def update_exchange_rates():
    """将在线汇率写入数据库"""
    rates = fetch_online_rates()
    if not rates:
        return False

    with _lock:
        for target in NEEDED_CURRENCIES:
            if target not in rates:
                continue
            rate = rates[target]
            record = ExchangeRate.get_or_none(
                (ExchangeRate.base_currency == 'CNY') &
                (ExchangeRate.target_currency == target)
            )
            if record:
                record.rate = rate
                record.updated_at = datetime.datetime.now()
                record.save()
            else:
                ExchangeRate.create(
                    base_currency='CNY',
                    target_currency=target,
                    rate=rate,
                    updated_at=datetime.datetime.now()
                )
    return True


def _background_updater():
    """后台线程：定时更新汇率"""
    update_exchange_rates()
    t = threading.Timer(UPDATE_INTERVAL, _background_updater)
    t.daemon = True
    t.start()


def start_background_updater():
    """启动后台汇率更新（应用启动时调用一次）"""
    global _updater_started
    if _updater_started:
        return
    _updater_started = True
    t = threading.Thread(target=_background_updater, daemon=True)
    t.start()


def get_rate(base, target):
    """获取汇率（从数据库缓存读，不阻塞）"""
    record = ExchangeRate.get_or_none(
        (ExchangeRate.base_currency == base) &
        (ExchangeRate.target_currency == target)
    )
    if record:
        return record.rate

    # 首次使用，立即同步拉取一次
    update_exchange_rates()
    record = ExchangeRate.get_or_none(
        (ExchangeRate.base_currency == base) &
        (ExchangeRate.target_currency == target)
    )
    return record.rate if record else None
