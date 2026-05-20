# services.py
import requests
from models import db, ExchangeRate
import datetime

API_URL = "https://api.exchangerate-api.com/v4/latest/CNY"   # 以CNY为基准

def fetch_online_rates():
    """从网络获取最新汇率，返回字典 {目标货币: 汇率}，例如 {'RUB': 12.5, ...}"""
    try:
        resp = requests.get(API_URL, timeout=10)
        data = resp.json()
        rates = data['rates']  # 已经是基于CNY的汇率
        return rates
    except Exception as e:
        print(f"获取汇率失败：{e}")
        return None

def update_exchange_rates():
    """更新数据库中的汇率数据（主要关心常用货币）"""
    rates = fetch_online_rates()
    if not rates:
        return False

    # 我们关心的目标货币
    needed = ['RUB', 'USD', 'EUR', 'GBP']
    for target in needed:
        if target in rates:
            rate = rates[target]
            # 尝试更新已有记录，否则新建
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

def get_rate(base, target):
    """获取汇率，如果超过24小时或不存在，则尝试更新"""
    record = ExchangeRate.get_or_none(
        (ExchangeRate.base_currency == base) &
        (ExchangeRate.target_currency == target)
    )
    if record:
        age = (datetime.datetime.now() - record.updated_at).total_seconds()
        # 超过24小时则异步更新（实际这里是同步更新，会阻塞，可优化）
        if age > 86400:
            update_exchange_rates()
            # 重新查询
            record = ExchangeRate.get_or_none(
                (ExchangeRate.base_currency == base) &
                (ExchangeRate.target_currency == target)
            )
    else:
        # 没有记录，直接更新
        update_exchange_rates()
        record = ExchangeRate.get_or_none(
            (ExchangeRate.base_currency == base) &
            (ExchangeRate.target_currency == target)
        )
    return record.rate if record else None