"""
CRUD 公共工具 — 减少蓝图中重复的分页、表单解析、安全查询等代码
"""
from flask import flash


# ── 分页 ──

def paginate(query, request, default_per_page=20, allowed_sizes=(10, 20, 50, 100)):
    """
    统一的请求分页处理。
    返回 (paginated_query, page, per_page, total_pages, total_count)
    """
    page = max(1, int(request.args.get('page', 1) or 1))
    per_page = int(request.args.get('per_page', default_per_page))
    if per_page not in allowed_sizes:
        per_page = default_per_page

    total = query.count()
    total_pages = max(1, (total + per_page - 1) // per_page)
    result = query.paginate(page, per_page)
    return result, page, per_page, total_pages, total


# ── 安全查询 ──

def get_or_none_user(model, record_id, user):
    """按 ID + 当前用户安全查询，返回记录或 None"""
    return model.get_or_none((model.id == record_id) & (model.user == user))


# ── 表单解析 ──

def parse_order_items_from_form(request, user):
    """
    从表单解析 product_id[] / quantity[] / unit_price[] 三组字段。
    返回 (items, errors)，其中 items 为 [{product, product_id, quantity, unit_price, subtotal}]
    """
    from models import Product
    from helpers import parse_positive_float, parse_non_negative_float

    product_ids = request.form.getlist('product_id[]')
    quantities = request.form.getlist('quantity[]')
    unit_prices = request.form.getlist('unit_price[]')

    items = []
    errors = []

    for pid, qty, price in zip(product_ids, quantities, unit_prices):
        if not pid or not qty or not price:
            continue

        product = get_or_none_user(Product, int(pid), user)
        qty_val = parse_positive_float(qty)
        price_val = parse_non_negative_float(price)

        if not product or qty_val is None or price_val is None:
            errors.append(f'无效的产品、数量或单价')
            continue

        items.append({
            'product': product,
            'product_id': product.id,
            'quantity': qty_val,
            'unit_price': price_val,
            'subtotal': qty_val * price_val,
        })

    return items, errors


def safe_redirect_fallback(url):
    """防止开放重定向：只允许 / 开头的相对路径"""
    if url and url.startswith('/') and not url.startswith('//'):
        return url
    return None
