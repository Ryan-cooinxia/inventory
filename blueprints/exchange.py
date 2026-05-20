# blueprints/exchange.py
from flask import Blueprint, render_template, request, jsonify
from services import get_rate, update_exchange_rates
from models import ExchangeRate
from flask_login import login_required, current_user
import datetime

exchange_bp = Blueprint('exchange', __name__)

@exchange_bp.route('/exchange')
def exchange_page():
    # 显示汇率换算页面，同时提供最近更新的时间
    rates = ExchangeRate.select().order_by(ExchangeRate.target_currency)
    last_update = None
    if rates:
        last_update = max(r.updated_at for r in rates)
    return render_template('exchange.html', rates=rates, last_update=last_update)

@exchange_bp.route('/api/exchange/calculate')
def calculate():
    # 计算：amount * 汇率，或 amount / 汇率（反向）
    from_currency = request.args.get('from', 'CNY')
    to_currency = request.args.get('to', 'RUB')
    amount = float(request.args.get('amount', 0))

    if from_currency == to_currency:
        rate = 1.0
    else:
        # 获取 from -> to 的汇率
        # 我们只存储了 CNY -> X，如果是反向换算，需要取倒数
        if from_currency == 'CNY':
            rate = get_rate('CNY', to_currency)
        elif to_currency == 'CNY':
            rate = get_rate('CNY', from_currency)
            if rate:
                rate = 1 / rate
        else:
            # 通过CNY交叉换算
            rate1 = get_rate('CNY', from_currency)  # CNY -> from
            rate2 = get_rate('CNY', to_currency)    # CNY -> to
            if rate1 and rate2:
                rate = rate2 / rate1
            else:
                rate = None

    if rate is None:
        return jsonify({'error': '汇率获取失败，请稍后再试'}), 503

    result = amount * rate
    return jsonify({
        'from': from_currency,
        'to': to_currency,
        'amount': amount,
        'rate': round(rate, 6),
        'result': round(result, 2)
    })