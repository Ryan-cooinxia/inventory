"""
OZON 商品采集器 — URL 抓取 + AI 解析
支持：1688 / 淘宝 / 天猫 / 拼多多 等平台
"""
import re
import json
import datetime
from urllib.parse import urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

# 浏览器 UA，降低被反爬概率
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

FETCH_TIMEOUT = 15  # 秒
MAX_HTML_SIZE = 500_000  # 最多保留 500KB HTML
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)


def _convert_to_mobile_url(url: str) -> dict:
    """
    将桌面淘宝/天猫 URL 转换为手机版 H5 URL。

    返回:
      {"url": str, "converted": bool, "reason": str}
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    params = parse_qs(parsed.query)

    # 提取商品 ID
    item_id = None

    # detail.tmall.com/item.htm?id=xxx
    # detail.tmall.com/item.htm?itemId=xxx  → 天猫
    # item.taobao.com/item.htm?id=xxx      → 淘宝
    # detail.taobao.com/item.htm?id=xxx    → 淘宝
    desktop_hosts = {
        'detail.tmall.com', 'detail.tmall.hk',
        'item.taobao.com', 'detail.taobao.com',
    }

    if not any(h in host for h in desktop_hosts):
        return {"url": url, "converted": False, "reason": "非已知桌面域名"}

    # 尝试提取 ID
    for key in ('id', 'itemId', 'item_id', 'spu_id'):
        val = params.get(key, [''])[0]
        if val and val.isdigit():
            item_id = val
            break

    # 也尝试从 URL path 提取
    if not item_id:
        path_match = re.search(r'/(\d{8,16})(?:\.html?)?', parsed.path)
        if path_match:
            item_id = path_match.group(1)

    if not item_id:
        return {"url": url, "converted": False, "reason": "无法提取商品ID"}

    h5_url = f"https://h5.m.taobao.com/awp/core/detail.htm?id={item_id}"
    return {
        "url": h5_url,
        "converted": True,
        "reason": f"桌面版 → H5 (id={item_id})",
        "item_id": item_id,
    }

# JS 动态渲染平台（requests 无法抓取有效商品内容）
JS_RENDERED_DOMAINS = {
    # 淘宝/天猫
    'item.taobao.com', 'detail.tmall.com', 'detail.tmall.hk',
    'chaoshi.detail.tmall.com', 'h5.m.taobao.com', 'm.taobao.com',
    # 1688
    'detail.1688.com', 'm.1688.com', '1688.com',
    # 拼多多
    'mobile.yangkeduo.com', 'yangkeduo.com', 'pinduoduo.com',
}

# 图片 URL 过滤关键词 — 仅匹配 path 中的文件名/目录，不匹配域名
IMAGE_BLOCK_KEYWORDS = [
    'logo', 'icon', 'favicon', 'sprite', 'avatar', 'wangwang',
    'qrcode', 'qr_code', 'coupon', 'banner', 'kefu',
    'customer-service', 'seller-logo', 'shop-logo', 'shop_avatar',
    'weixin', 'wechat', 'dacu', 'tmallcc',
]

# 图片 URL 过滤关键词 — 仅匹配域名（platform domain 除外）
IMAGE_DOMAIN_BLOCK_KEYWORDS = [
    'alipay', 'weixin', 'wechat', 'facebook', 'twitter',
]

# 图片 URL 路径/文件名模式（平台 UI 图、广告图）
IMAGE_BLOCK_PATTERNS = [
    r'\.ico(\?|$)',                          # favicon
    r'\.svg(\?|$)',                          # SVG icon
    r'/(?:icon|logo|banner|ad)s?/',          # icons/logos/banners/ads 目录
    r'_\d{2,3}x\d{2,3}\.',                   # 小缩略图（如 80x80）
    r'/avatar/',                              # 头像
    r'//img\.alicdn\.com/(?:simba|tfscom)/', # 阿里广告/UI 图
    r'//gw\.alicdn\.com/',                    # 阿里旺旺/客服 UI
    r'/wwc\.alicdn\.com/',                    # 旺旺图标
    r'//img\.tbcdn\.cn/',                     # 淘宝 UI CDN
    r'/tps/i\d+/',                            # 淘宝图标
    r'/assets/(?:icon|logo|btn|button)s?/',   # 前端资源图标
    r'/(?:rank|star|heart|comment|like)s?/',  # 评分星/赞等图标
]


def _playwright_error_msg(e: Exception, context: str = "Headless") -> str:
    """将 Playwright 异常转为友好的中文提示"""
    msg = str(e)
    if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
        return (
            f"{context} 失败：Playwright 浏览器内核未安装或版本不匹配。"
            "请在服务器上运行: python -m playwright install chromium"
        )
    if "Target page, context or browser has been closed" in msg:
        return f"{context} 失败：浏览器意外关闭，可能是内存不足或页面崩溃。"
    if "net::ERR_" in msg:
        return f"{context} 失败：网络错误（{msg[:100]}），请检查网络连接。"
    return f"{context} 异常: {msg[:300]}"


# ═════════════════════════════════════════════════════════
# 图片中文文本过滤词库（匹配 alt / nearby_text）
# ═════════════════════════════════════════════════════════

# 平台/营销/认证类 — 命中即 reject（不是商品内容）
REJECT_TEXT_KEYWORDS = [
    '天猫', '淘宝', '淘宝网', 'tmall', 'taobao',
    '88vip', '88 vip', '去开通', '立即开通', '开通会员',
    '采购优选', '实力商家', '源头工厂', '产业带',
    '可信网站', '身份验证', '实名认证', '企业认证',
    '举报中心', '扫黄打非', '知识产权', '网络警察',
    '放心消费', '诚信商家', '正品保障', '假一赔十',
    '消费者保障', '七天退换', '品质保证',
    '广告', '推广', '推荐', '热卖', '爆款',
    '联系客服', '在线客服', '客服热线', '咨询客服',
    '扫码', '二维码', '公众号', '小程序', '下载app',
    '领取优惠', '领券', '优惠券', '满减', '红包',
    '分享', '收藏', '关注', '订阅', '点赞',
    '首页', '导航', '返回顶部', '回到顶部',
]

# 品牌/店铺类 — 在商品区命中标记为 needs_review（可能是商品上的品牌）
BRAND_TEXT_KEYWORDS = [
    '旗舰店', '官方店', '专卖店', '专营店',
    '会员', '会员权益', '会员中心', '会员等级',
    '店铺', '店铺等级', '店铺评分',
    '认证', '认证标识', '企业认证',
]


def _check_text_keywords(alt: str, nearby_text: str) -> dict:
    """
    检测图片的 alt 文本和附近文本中的中文关键词。

    返回: {"hit_reject": [matched_keywords], "hit_brand": [matched_keywords]}
    """
    combined = (alt or '') + ' ' + (nearby_text or '')
    if not combined.strip():
        return {"hit_reject": [], "hit_brand": []}

    combined_lower = combined.lower()
    hit_reject = [kw for kw in REJECT_TEXT_KEYWORDS if kw.lower() in combined_lower]
    hit_brand = [kw for kw in BRAND_TEXT_KEYWORDS if kw.lower() in combined_lower]

    return {"hit_reject": hit_reject, "hit_brand": hit_brand}


# ═════════════════════════════════════════════════════════
# source_area → 默认分类规则
# ═════════════════════════════════════════════════════════

SOURCE_AREA_RULES = {
    'main_gallery':        ('usable',        '商品主图轮播区'),
    'sku_panel':           ('usable',        'SKU变体图片选择区'),
    'detail_content':      ('usable',        '商品详情描述区'),
    'shop_header':         ('rejected',      '店铺头部/Logo装修区'),
    'floating':            ('rejected',      '浮动悬浮元素（客服/广告/二维码）'),
    'sidebar':             ('rejected',      '侧栏推荐/广告区'),
    'footer':              ('rejected',      '页脚区域'),
    'nav':                 ('rejected',      '导航栏区域'),
    'ad':                  ('rejected',      '广告位'),
    'certification':       ('rejected',      '认证标识/可信网站标识'),
    'review':              ('rejected',      '评论/买家秀区域'),
    'recommendation':      ('rejected',      '推荐/相似商品区域'),
    'rejected':            ('rejected',      '插件已判定为非商品图片'),
    'unknown':             ('needs_review',  '无法判断来源区域'),
}


def fetch_url(url: str) -> dict:
    """
    抓取商品页面 URL，返回页面信息字典。
    对于淘宝/天猫桌面版，自动转换为 H5 手机版抓取。

    成功: {"ok": True, "html": str, "title": str, "text": str, "capture_url": str}
    失败: {"ok": False, "error": str}
    """
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL 必须以 http:// 或 https:// 开头"}

    orig_url = url
    capture_url = url

    # ── 桌面淘宝/天猫 → H5 转换 ─────────────────────
    conversion = _convert_to_mobile_url(url)
    if conversion["converted"]:
        capture_url = conversion["url"]

    domain = urlparse(capture_url).netloc.lower()
    is_mobile = 'h5.m.taobao.com' in domain or 'm.taobao.com' in domain

    headers = {
        "User-Agent": MOBILE_UA if is_mobile else USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Cache-Control": "max-age=0",
    }

    try:
        resp = requests.get(capture_url, headers=headers, timeout=FETCH_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()

        # 自动检测编码
        resp.encoding = resp.apparent_encoding or resp.encoding or "utf-8"

        html = resp.text[:MAX_HTML_SIZE]

        soup = BeautifulSoup(html, "lxml")

        # 提取页面标题
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        # 提取可见文本（限制长度，减少 AI token）
        for tag in soup(["script", "style", "noscript", "iframe", "svg", "nav", "footer", "header"]):
            tag.decompose()

        text = soup.get_text(separator="\n", strip=True)
        # 压缩空白行
        text = re.sub(r"\n{3,}", "\n\n", text)

        # ── 检测反爬/登录墙 ──────────────────────────
        anti_bot_signals = [
            "请输入验证码", "滑块验证", "验证码", "点击验证",
            "请登录", "登录后可见", "请先登录", "login",
            "security check", "captcha", "访问验证",
            "系统检测到异常", "请输入手机号",
        ]
        text_lower = text.lower()
        hit = next((s for s in anti_bot_signals if s.lower() in text_lower), None)

        if hit:
            return {
                "ok": False,
                "error": (
                    f'页面被反爬机制拦截（检测到"{hit}"）。'
                    "请改用「粘贴内容」模式：在浏览器打开商品页 → Ctrl+A 全选 → Ctrl+C 复制 → 粘贴到采集面板。"
                ),
                "hint": "use_paste_mode",
            }

        # ── JS 渲染平台特殊检测 ─────────────────────
        is_js_platform = any(d in domain for d in JS_RENDERED_DOMAINS)

        if is_js_platform:
            # JS 渲染平台要求更严格：检查关键信号
            has_product_signal = _check_product_signals(text, title)
            if not has_product_signal:
                # ── 回退到 Playwright headless ──────────
                hresult = fetch_url_headless(capture_url)
                if hresult["ok"]:
                    return hresult
                return {
                    "ok": False,
                    "error": (
                        f"该平台（{domain}）商品页面为 JavaScript 动态渲染，服务器无法直接获取有效商品内容。"
                        f"Headless 浏览器也未成功（{hresult.get('error', '未知')}）。"
                        "请改用「粘贴内容」模式：在浏览器打开商品页 → Ctrl+A 全选 → Ctrl+C 复制 → 粘贴到采集面板。"
                        "或使用浏览器插件采集。"
                    ),
                    "hint": "use_paste_mode",
                    "platform_js_render": True,
                }

            # 通过了信号检测，但内容可能不完整 → 尝试 headless 补充
            if len(text.strip()) < 800:
                hresult = fetch_url_headless(capture_url)
                if hresult["ok"] and len(hresult.get("text", "").strip()) > len(text.strip()):
                    return hresult
                return {
                    "ok": True,
                    "html": html,
                    "title": title,
                    "text": text,
                    "quality_warning": f"{domain} 页面内容较少（{len(text.strip())}字），" + \
                        "可能缺失详情描述。建议同时使用粘贴模式补充。",
                    "detail_missing": True,
                }

        # ── 通用内容量检测 ──────────────────────────
        if len(text.strip()) < 300:
            # ── 尝试 headless 回退 ────────────────────
            if not is_js_platform:
                # 非 JS 平台也尝试一下
                hresult = fetch_url_headless(capture_url)
                if hresult["ok"]:
                    return hresult
            return {
                "ok": False,
                "error": (
                    "页面内容过少（可能为 JS 动态渲染页面或反爬页面）。"
                    "请改用「粘贴内容」模式：在浏览器打开商品页 → Ctrl+A 全选 → Ctrl+C 复制 → 粘贴到采集面板。"
                ),
                "hint": "use_paste_mode",
            }

        text = text[:15_000]  # 最多 15000 字符

        # ── H5 质量检查 ──────────────────────────
        h5_warnings = []
        if is_mobile:
            if not title or len(title.strip()) < 5:
                h5_warnings.append("H5 页面标题缺失")
            if not re.search(r'(?:价格|售价|[¥￥]\s*\d+)', text):
                h5_warnings.append("H5 页面未检测到价格信息")
            if not re.search(r'(?:颜色|规格|尺寸|型号|套餐)', text):
                h5_warnings.append("H5 页面未检测到 SKU 信息")

        result = {
            "ok": True,
            "html": html,
            "title": title,
            "text": text,
            "capture_url": capture_url,
            "original_url": orig_url if orig_url != capture_url else None,
            "converted_to_mobile": conversion["converted"],
            "detail_missing": False,
        }
        if h5_warnings:
            result["quality_warning"] = '; '.join(h5_warnings)
            result["detail_missing"] = True
        return result

    except requests.exceptions.Timeout:
        return {"ok": False, "error": f"请求超时（{FETCH_TIMEOUT}秒），该网站可能无法直接访问"}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "无法连接到该网站，请检查 URL 是否正确或尝试粘贴页面内容"}
    except requests.exceptions.HTTPError as e:
        return {"ok": False, "error": f"网站返回错误 (HTTP {e.response.status_code})，可能需登录才能访问"}
    except Exception as e:
        return {"ok": False, "error": f"抓取失败: {str(e)[:200]}"}


def _check_product_signals(text: str, title: str) -> bool:
    """检查页面文本是否包含商品关键信号（价格/规格/标题非空）"""
    signals = 0
    # 价格信号
    if re.search(r'[¥￥]\s*\d+[\d.]*', text):
        signals += 1
    if re.search(r'(?:价格|售价|单价|促销价|批发价|券后价|到手价)\s*[：:]\s*[¥￥]?\s*\d+', text):
        signals += 1
    # SKU/规格信号
    if re.search(r'(?:颜色|规格|尺寸|款式|型号|套餐|数量)', text):
        signals += 1
    # 标题信号
    if title and len(title.strip()) > 10:
        signals += 1
    # 内容量
    if len(text.strip()) > 500:
        signals += 1

    return signals >= 2  # 至少 2 个信号才认为有效


# ═════════════════════════════════════════════════════════
# Playwright Headless 浏览器采集
# ═════════════════════════════════════════════════════════

HEADLESS_TIMEOUT = 25_000  # 毫秒（Playwright 用毫秒）
HEADLESS_MAX_HTML = 500_000


def fetch_url_headless(url: str) -> dict:
    """
    使用 Playwright headless Chromium 抓取 JS 渲染页面。

    返回格式与 fetch_url() 一致：
      成功: {"ok": True, "html": str, "title": str, "text": str, "capture_url": str}
      失败: {"ok": False, "error": str}
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
    except ImportError:
        return {"ok": False, "error": "Playwright 未安装，请运行: pip install playwright && playwright install chromium"}

    actual_url = url

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=MOBILE_UA,
                viewport={"width": 390, "height": 844},  # iPhone 14 尺寸
                locale="zh-CN",
            )
            page = context.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=HEADLESS_TIMEOUT)
            except PwTimeout:
                # DOM 加载超时也继续，可能有部分内容
                pass
            except Exception as e:
                browser.close()
                return {"ok": False, "error": f"Headless 浏览器无法访问该页面: {str(e)[:200]}"}

            actual_url = page.url

            # 等待网络空闲（最多 8 秒）
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PwTimeout:
                pass  # 网络不空闲也继续

            # 额外等待关键元素（自适应各平台）
            try:
                page.wait_for_selector(
                    "img, .sku-item, .mod-detail, .offer-title, h1, .product-title, .detail-content",
                    timeout=5000
                )
            except PwTimeout:
                pass

            # ── 渐进式滚动，触发全部懒加载内容 ──────────
            max_scroll_iterations = 25
            scroll_step_px = 900  # 约一个视口高度
            prev_height = 0

            for i in range(max_scroll_iterations):
                scroll_height = page.evaluate("document.body.scrollHeight || document.documentElement.scrollHeight")
                target_y = (i + 1) * scroll_step_px

                # 已滚过底部且页面高度不再增长 → 结束
                if target_y >= scroll_height and scroll_height == prev_height:
                    break

                page.evaluate(f"window.scrollTo(0, {target_y})")
                page.wait_for_timeout(1200)  # 给懒加载图片足够加载时间
                prev_height = scroll_height

            # 滚到底后额外等待，确保最后一批图片加载完成
            page.wait_for_timeout(2500)
            # 不回到顶部 —— 保持底部位置，避免 DOM 回收已加载的懒加载图片

            # ── 收集页面中所有图片 URL（多源） ──────────
            collected_image_urls = page.evaluate("""() => {
                const urls = new Set();

                // 1. 从所有 <img> 元素收集 src 和懒加载属性
                document.querySelectorAll('img').forEach(img => {
                    [img.src, img.dataset && img.dataset.src,
                     img.getAttribute('data-ks-lazyload'),
                     img.getAttribute('data-original'),
                     img.getAttribute('data-lazy-src'),
                     img.getAttribute('lazy-src')].forEach(u => {
                        if (u && u.startsWith('http') && !u.startsWith('data:')
                            && !u.endsWith('.svg') && !u.endsWith('.ico')) {
                            urls.add(u.split('?')[0].length > 20 ? u : null);
                        }
                    });
                });

                // 2. 从 background-image CSS 属性收集
                document.querySelectorAll('[style*="background-image"]').forEach(el => {
                    try {
                        const bg = getComputedStyle(el).backgroundImage;
                        const match = bg && bg.match(/url\\(["']?(https?:\\/\\/[^"')]+)["']?\\)/);
                        if (match) urls.add(match[1]);
                    } catch(e) {}
                });

                // 3. 从 performance resource entries 收集已下载的图片
                try {
                    performance.getEntriesByType('resource')
                        .filter(r => r.initiatorType === 'img'
                                  || /\\.(jpe?g|png|webp|avif)/i.test(r.name))
                        .forEach(r => {
                            if (r.name.startsWith('http')) urls.add(r.name);
                        });
                } catch(e) {}

                urls.delete(null);
                return Array.from(urls);
            }""")

            html = page.content()
            title = page.title() or ""

            # 浏览器内提取文本（比 BeautifulSoup 更准确）
            body_text = page.evaluate("""() => {
                // 移除不可见元素
                const clone = document.body.cloneNode(true);
                const removes = clone.querySelectorAll('script, style, noscript, iframe, svg, nav, footer, [aria-hidden="true"]');
                removes.forEach(el => el.remove());
                return clone.innerText || '';
            }""")

            browser.close()

            # 截断 + 压缩空白
            html = html[:HEADLESS_MAX_HTML]
            body_text = re.sub(r"\n{3,}", "\n\n", body_text or "")
            body_text = body_text[:30_000]

            # 检查内容质量
            if len(body_text.strip()) < 200:
                return {
                    "ok": False,
                    "error": "Headless 浏览器获取的页面内容过少，该页面可能需要登录或有反爬保护。请尝试粘贴模式或浏览器插件采集。",
                    "hint": "use_paste_mode",
                }

            result = {
                "ok": True,
                "html": html,
                "title": title,
                "text": body_text,
                "capture_url": actual_url,
                "collected_via": "playwright_headless",
                "detail_missing": False,
                "collected_image_urls": collected_image_urls,
            }

            # H5 质量检查
            if len(body_text.strip()) < 800:
                result["detail_missing"] = True
                result["quality_warning"] = f"Headless 页面内容较少（{len(body_text.strip())}字），可能缺失详情描述"

            return result

    except Exception as e:
        return {"ok": False, "error": _playwright_error_msg(e, "Headless 采集")}



def extract_product(html_or_text: str, api_key: str, provider: str = "openai", source_url: str = "") -> dict:
    """
    用 AI 从 HTML 或纯文本中提取商品结构化数据。

    参数:
        html_or_text: 网页 HTML 或粘贴的文本内容
        api_key: OpenAI API Key
        provider: "openai" 或 "deepseek"
        source_url: 可选的源 URL

    返回:
        {"ok": True, "data": {...}}  或  {"ok": False, "error": str}
    """
    import openai

    # 截断过长的输入
    if len(html_or_text) > 30_000:
        html_or_text = html_or_text[:30_000]

    base_url = "https://api.deepseek.com" if provider == "deepseek" else None
    client = openai.OpenAI(api_key=api_key, base_url=base_url)

    prompt = f"""你是一个精准的电商商品信息提取助手。请从以下网页内容中提取商品信息，输出符合指定 JSON Schema 的对象。

    请严格遵守以下规则：
    1. 页面类型识别（source_page_type）:
       - product: 标准商品详情页（有明确商品标题、SKU、价格）
       - company: 公司介绍/店铺首页（无具体商品）
       - search: 搜索结果列表
       - unknown: 无法判断
       - 1688 页面中，"公司名称""店铺名称""供应商名称""工商信息"绝对不能作为 product.title_cn
       - 如果标题疑似公司名（含"有限公司""官方旗舰店""实力商家""认证企业"），标记为不可靠

    2. SKU 提取：
       - 有多个 SKU（不同颜色/规格/套餐）时逐一提取
       - 1688 标准 SKU 表格通常在 <table class="table-sku"> 或其附近
       - 如果页面明确有多规格但只提取了 1 个 SKU，标记 needs_manual

    3. 价格提取：
       - 价格必须来自本商品的售价/SKU价/批发价/阶梯价/促销价 → purchase_price_cny
       - 不能从广告推荐、相似商品、导航栏提取价格
       - 如果价格来源不确定或来自 URL 参数，填到 price_candidates 而非 purchase_price_cny
       - 价格单位人民币元（CNY），只提取数字
       - 淘宝/天猫如果价格缺失，检查页面底部 JSON-LD 或 meta 中的价格字段
       - SKU 价格全部为空时标记 pricing_complete: false

    4. 图片提取：
       - 只提取商品主图、SKU变体图、详情描述图
       - 排除：平台Logo、店铺Logo、头像、客服图标、广告banner、优惠券、二维码、装饰图
       - role: main（主图）、sku（变体图）、detail（详情图）
       - URL 必须完整

    5. 商品参数提取：
       - 提取页面上所有商品规格参数 → specs_json: [{{"name":"品牌","value":"DJI","source_text":"..."}}]
       - 包括：品牌、型号、材质、尺寸、重量、颜色、产地、保修、电池、功率等

    6. platform 推断：detail.1688.com→1688, item.taobao.com→taobao, detail.tmall.com→tmall, 其他→manual

    参考页面 URL：{source_url or "未知"}

    输出 JSON Schema：
    {{
      "source_page_type": "product|company|search|unknown",
      "title_reliable": true/false,
      "product": {{
        "title_cn": "商品中文标题（不是公司名/店铺名）",
        "category_cn": "商品类目",
        "description_cn": "商品描述",
        "shop_name": "店铺名",
        "item_id": "源平台商品ID或null"
      }},
      "specs_json": [{{"name":"参数名","value":"参数值","source_text":"原始文本"}}],
      "skus": [
        {{
          "source_order": 1,
          "source_sku_name": "规格名称",
          "color_cn": "颜色或null",
          "size_cn": "尺寸或null",
          "style_cn": "款式或null",
          "bundle_quantity": 1,
          "package_contents_cn": ["包装内容"],
          "purchase_price_cny": 确认价格数字或null
        }}
      ],
      "price_candidates": [{{"price": 数字,"source":"dom/text/url/h5","confidence":"low/medium/high","note":"说明"}}],
      "pricing_complete": true/false,
      "media": [
        {{
          "source_url": "完整图片URL（只限商品图片）",
          "role": "main/sku/detail"
        }}
      ],
      "pricing": {{
        "source_price_cny": 最低确认价或null,
        "candidate_price_cny": 候选价或null,
        "price_note_cn": "价格说明或null"
      }}
    }}

    请只输出 JSON 对象，不要额外的解释或 markdown 标记。"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o" if provider == "openai" else "deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个精准的电商数据提取工具，只输出 JSON。你严格区分确认价格和候选价格，只提取真正的商品图片。"},
                {"role": "user", "content": prompt + "\n\n---网页内容---\n" + html_or_text},
            ],
            temperature=0.1,
            max_tokens=4000,
        )

        raw = response.choices[0].message.content.strip()

        # 清理可能的 markdown 包裹
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)
        return {"ok": True, "data": data}

    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"AI 返回格式异常，请重试: {str(e)[:200]}"}
    except openai.AuthenticationError:
        return {"ok": False, "error": "AI API Key 无效，请在 AI 设置中配置"}
    except openai.RateLimitError:
        return {"ok": False, "error": "AI API 调用频繁，请稍后重试"}
    except Exception as e:
        return {"ok": False, "error": f"AI 调用失败: {str(e)[:300]}"}


# ═════════════════════════════════════════════════════════
# 图片过滤
# ═════════════════════════════════════════════════════════

def classify_source_image_url(url: str, role: str = None, meta: dict = None) -> dict:
    """
    统一图片合规分类 —— URL 规则 + 插件元数据双重判断。

    meta 字段（来自浏览器插件）:
      source_area: main_gallery|sku_panel|detail_content|shop_header|floating|sidebar|footer|nav|ad|certification|unknown
      dom_path:    CSS 选择器路径
      width:       图片宽度 (px)
      height:      图片高度 (px)
      alt:         img alt 文本
      nearby_text: 图片附近的可见文本（父元素内文本，最多 200 字）

    返回:
      {"status": "usable" | "needs_review" | "rejected", "reason": str, "details": dict}
    """
    if meta is None:
        meta = {}

    url_lower = url.lower()
    source_area = meta.get('source_area', 'unknown')
    alt = meta.get('alt', '') or ''
    nearby = meta.get('nearby_text', '') or ''
    width = meta.get('width', 0) or 0
    height = meta.get('height', 0) or 0

    # ═══════ 0) 极小尺寸/占位图过滤 ═══════
    if width > 0 and height > 0:
        if width < 60 or height < 60:
            return {"status": "rejected",
                    "reason": f"尺寸过小({width}x{height}px)，疑似图标/占位图",
                    "details": {"rule": "min_size", "source_area": source_area}}

    # 解析 URL 路径
    from urllib.parse import urlparse as _urlparse
    parsed = _urlparse(url_lower)
    host = parsed.netloc
    path = parsed.path

    # ═══════ 1) 扩展名过滤 ═══════
    if re.search(r'\.ico(\?|$)', url_lower) or url_lower.endswith('.svg'):
        return {"status": "rejected",
                "reason": "图标/矢量资源(.ico/.svg)",
                "details": {"rule": "extension", "source_area": source_area}}

    # ═══════ 2) 域名关键词 ═══════
    domain_hits = [kw for kw in IMAGE_DOMAIN_BLOCK_KEYWORDS if kw in host]
    if domain_hits:
        return {"status": "rejected",
                "reason": f"域名匹配非商品域名关键词: {', '.join(domain_hits)}",
                "details": {"rule": "domain_block", "source_area": source_area}}

    # ═══════ 3) 中文文本关键词检测 ═══════
    text_check = _check_text_keywords(alt, nearby)

    # ═══════ 4) source_area 规则（插件提供的最可靠信号）═══════
    area_rule = SOURCE_AREA_RULES.get(source_area)
    if area_rule:
        area_status, area_desc = area_rule

        # 商品区域 (main_gallery/sku_panel/detail_content)
        if area_status == 'usable':
            # 4a) 品牌 Logo 检测 — 不直接拒绝，标记为 needs_review
            if text_check['hit_brand']:
                return {"status": "needs_review",
                        "reason": f"含品牌/店铺元素（{', '.join(text_check['hit_brand'][:3])}），需人工确认是否为商品本体上的品牌标识",
                        "details": {"rule": "brand_on_product", "source_area": source_area,
                                    "hit_keywords": text_check['hit_brand'],
                                    "area_desc": area_desc}}

            # 4b) 商品区域 + 拒绝关键词 → needs_review（商品图上的水印标语）
            if text_check['hit_reject']:
                return {"status": "needs_review",
                        "reason": f"商品区域含可疑文本（{', '.join(text_check['hit_reject'][:3])}），可能是商品主图上的平台水印",
                        "details": {"rule": "suspect_text_in_product", "source_area": source_area,
                                    "hit_keywords": text_check['hit_reject'],
                                    "area_desc": area_desc}}

            # 4c) 商品区域 + 尺寸合理 → 可用
            return {"status": "usable", "reason": None,
                    "details": {"rule": "product_area", "source_area": source_area,
                                "area_desc": area_desc}}

        # 非商品区域 (shop_header/floating/sidebar/footer/nav/ad/certification)
        if area_status == 'rejected':
            reason = f"来源为{area_desc}，非商品图片"
            if text_check['hit_reject']:
                reason += f"（文本: {', '.join(text_check['hit_reject'][:3])}）"
            return {"status": "rejected",
                    "reason": reason,
                    "details": {"rule": "non_product_area", "source_area": source_area,
                                "area_desc": area_desc,
                                "hit_keywords": text_check['hit_reject']}}

        # 未知区域
        if area_status == 'needs_review':
            # 拒绝关键词 → 直接 reject
            if text_check['hit_reject']:
                return {"status": "rejected",
                        "reason": f"来源未知区域+含平台/营销文本（{', '.join(text_check['hit_reject'][:3])}）",
                        "details": {"rule": "unknown_area_with_reject_text",
                                    "source_area": source_area, "area_desc": area_desc}}
            return {"status": "needs_review",
                    "reason": f"来源为{area_desc}，需人工判断",
                    "details": {"rule": "unknown_area", "source_area": source_area,
                                "area_desc": area_desc}}

    # ═══════ 5) URL path 关键词（回退规则，无 area 信息时用）═══════
    path_hits = [kw for kw in IMAGE_BLOCK_KEYWORDS if kw in path]
    if path_hits:
        reason = f"URL路径匹配UI关键词: {', '.join(path_hits)}"
        if role in ('main', 'sku', 'detail'):
            return {"status": "needs_review", "reason": reason,
                    "details": {"rule": "url_path_soft", "source_area": source_area}}
        else:
            return {"status": "rejected", "reason": reason,
                    "details": {"rule": "url_path_hard", "source_area": source_area}}

    # ═══════ 6) URL 模式匹配（回退规则）═══════
    for pat in IMAGE_BLOCK_PATTERNS:
        if re.search(pat, url_lower):
            if role in ('main', 'sku', 'detail'):
                return {"status": "needs_review",
                        "reason": f"URL命中UI模式(商品区): {pat}",
                        "details": {"rule": "url_pattern_soft", "source_area": source_area}}
            else:
                return {"status": "rejected",
                        "reason": f"URL命中UI模式: {pat}",
                        "details": {"rule": "url_pattern_hard", "source_area": source_area}}

    # ═══════ 7) 纯文本关键词检查（无 area 但有文本）═══════
    if text_check['hit_reject'] and source_area == 'unknown':
        return {"status": "rejected",
                "reason": f"文本含平台/营销关键词（{', '.join(text_check['hit_reject'][:3])}）",
                "details": {"rule": "text_reject", "source_area": source_area}}
    if text_check['hit_brand'] and source_area == 'unknown':
        return {"status": "needs_review",
                "reason": f"文本含品牌/店铺关键词（{', '.join(text_check['hit_brand'][:3])}），需人工确认",
                "details": {"rule": "text_brand", "source_area": source_area}}

    # ═══════ 8) 无任何命中 → 可用 ═══════
    return {"status": "usable", "reason": None,
            "details": {"rule": "default", "source_area": source_area}}


# ═════════════════════════════════════════════════════════
# URL 候选价格提取
# ═════════════════════════════════════════════════════════

def extract_candidate_price_from_url(source_url: str) -> dict:
    """
    从商品 URL 中提取可能的候选价格（低置信度，需人工确认）。

    解析:
    - 天猫 utparam 参数中的价格字段
    - URL query 中可能的 price 字段
    - URL path 中可能的价格片段

    返回:
      {"price": float|null, "source": "url_param"|null, "confidence": "low"|null, "note": str}
    """
    try:
        parsed = urlparse(source_url)
        params = parse_qs(parsed.query)

        # 天猫 URL 参数价格
        for key in ('item_price', 'ump_price', 'price', 'skuPrice', 'promoPrice'):
            val = params.get(key)
            if val:
                try:
                    price = float(re.sub(r'[^\d.]', '', str(val[0])))
                    if 0.01 < price < 100000:
                        return {
                            "price": price,
                            "source": "url_param",
                            "confidence": "low",
                            "note": f"从 URL 参数 {key}={price} 提取，需人工确认"
                        }
                except (ValueError, TypeError):
                    pass

        # 天猫 utparam 编码参数
        utparam = params.get('utparam', [''])[0]
        if utparam:
            try:
                decoded = unquote(utparam)
                price_match = re.search(r'(?:itemPrice|umpPrice|price)[=:]\s*([\d.]+)', decoded)
                if price_match:
                    price = float(price_match.group(1))
                    if 0.01 < price < 100000:
                        return {
                            "price": price,
                            "source": "url_param",
                            "confidence": "low",
                            "note": f"从 utparam 解码价格 {price}，需人工确认"
                        }
            except Exception:
                pass

        # 拼多多 URL 价格参数
        for key in ('goods_price', 'price'):
            val = params.get(key)
            if val:
                try:
                    price_str = str(val[0])
                    # 拼多多价格可能是分（1元=100分）
                    price = float(re.sub(r'[^\d.]', '', price_str))
                    if 0.01 < price < 100000:
                        # 如果 > 1000 可能是分为单位
                        if price > 1000:
                            price_yuan = price / 100
                            if 0.01 < price_yuan < 100000:
                                return {
                                    "price": round(price_yuan, 2),
                                    "source": "url_param",
                                    "confidence": "low",
                                    "note": f"从 URL 参数 {key}={price}（可能以分为单位，换算 {price_yuan} 元），需人工确认"
                                }
                        return {
                            "price": price,
                            "source": "url_param",
                            "confidence": "low",
                            "note": f"从 URL 参数 {key}={price} 提取，需人工确认"
                        }
                except (ValueError, TypeError):
                    pass

    except Exception:
        pass

    return {"price": None, "source": None, "confidence": None, "note": None}


# ═════════════════════════════════════════════════════════
# 采集质量检查
# ═════════════════════════════════════════════════════════

# ── OZON 属性名 俄→英 映射表 ──
OZON_ATTR_RU_TO_EN = {
    'артикул': 'article', 'тип': 'type', 'бренд': 'brand',
    'вес': 'weight', 'вес товара': 'weight', 'вес товара, г': 'weight',
    'материал': 'material', 'цвет': 'color',
    'размер': 'size', 'размеры': 'dimensions', 'размеры, мм': 'dimensions',
    'гарантия': 'warranty', 'страна': 'origin', 'страна-изготовитель': 'origin',
    'емкость': 'battery_capacity', 'емкость аккумулятора': 'battery_capacity',
    'аккумулятор': 'battery_capacity', 'батарея': 'battery_capacity',
    'питание': 'power', 'мощность': 'power',
    'совместимость': 'compatibility',
    'назначение': 'usage', 'комплектация': 'package_contents',
    'особенности': 'features', 'модель': 'model',
    'производитель': 'manufacturer', 'серия': 'series',
    'частота': 'frequency', 'чувствительность': 'sensitivity',
    'битрейт': 'bitrate', 'дисплей': 'display', 'экран': 'screen',
    'водонепроницаемость': 'waterproof', 'защита': 'protection',
    'подключение': 'connectivity', 'интерфейс': 'interface',
    'формат': 'format', 'разрешение': 'resolution',
    'стабилизация': 'stabilization', 'угол обзора': 'viewing_angle',
    'рабочая температура': 'operating_temperature',
    'время работы': 'battery_life', 'время зарядки': 'charging_time',
    'дальность': 'wireless_range', 'радиус действия': 'wireless_range',
    'количество': 'quantity', 'шт': 'quantity',
}

def map_ozon_attributes_to_fields(source_attributes):
    """将 OZON 俄语属性列表映射为系统字段 dict。
    返回: {field_name: value, ...}"""
    result = {}
    if not source_attributes:
        return result
    for attr in source_attributes:
        name = (attr.get('name') or attr.get('key') or '').lower().strip().rstrip(',:;')
        value = attr.get('value') or attr.get('text') or ''
        # 精确匹配
        if name in OZON_ATTR_RU_TO_EN:
            result[OZON_ATTR_RU_TO_EN[name]] = value
            continue
        # 模糊匹配（属性名包含关键词）
        for ru_key, en_key in OZON_ATTR_RU_TO_EN.items():
            if ru_key in name or name in ru_key:
                result[en_key] = value
                break
    return result


def collect_quality_check(data: dict, source_url: str) -> dict:
    """
    检查 AI 提取结果的质量，判断是否满足适配条件。
    """
    prod = data.get("product", {})
    skus = data.get("skus", [])
    media_list = data.get("media", [])
    pricing = data.get("pricing", {})
    source_page_type = data.get("source_page_type", "unknown")
    title_reliable = data.get("title_reliable", True)

    missing_fields = []
    warnings = []
    needs_manual_capture = False
    bad_source_page = False

    # ── 1688 页面类型检查 ──────────────────────────
    domain = urlparse(source_url).netloc.lower()
    is_1688 = '1688' in domain
    is_js_platform = any(d in domain for d in JS_RENDERED_DOMAINS)

    if is_1688:
        # 检查 URL 格式：必须是 offer 详情页
        if '/offer/' not in source_url:
            bad_source_page = True
            warnings.append("1688 URL 不是商品详情页（/offer/），建议打开具体商品页或用浏览器插件采集")
            needs_manual_capture = True

        # 检查 source_page_type
        if source_page_type in ('company', 'search', 'unknown'):
            bad_source_page = True
            warnings.append(f"页面类型为 {source_page_type}，不是商品页，建议用浏览器插件采集")
            needs_manual_capture = True

    # ── 标题检查 ──────────────────────────────────
    title = prod.get("title_cn", "").strip()
    if not title:
        missing_fields.append("商品标题")
        needs_manual_capture = True
    elif not title_reliable:
        warnings.append(f"商品标题可能不准确（疑似公司名/店铺名）: {title[:60]}")
        needs_manual_capture = is_1688  # 1688 强制要求

    # 检查公司名特征
    company_signals = ["有限公司", "官方旗舰店", "实力商家", "认证企业",
                       "工商注册", "营业执照", "企业资质", "有限责任公司"]
    for sig in company_signals:
        if sig in title:
            bad_source_page = True
            warnings.append(f'标题疑似公司名（含"{sig}"），不是商品标题')
            if is_1688:
                needs_manual_capture = True
            break

    # ── SKU 检查 ──────────────────────────────────
    if not skus:
        missing_fields.append("SKU")
        needs_manual_capture = True
    elif len(skus) == 1 and is_1688:
        # 1688 通常有多规格，只有 1 个 SKU 时警告
        warnings.append("1688 只提取到 1 个 SKU，可能有多个规格未识别，建议用插件采集")

    # ── 图片检查 ──────────────────────────────────
    usable_count = 0
    rejected_count = 0
    for m in media_list:
        url = m.get("source_url", "")
        role = m.get("role")
        if url:
            classification = classify_source_image_url(url, role)
            if classification["status"] != "rejected":
                usable_count += 1
            else:
                rejected_count += 1

    if usable_count == 0:
        missing_fields.append("可用图片")
        needs_manual_capture = True
    if rejected_count > 0:
        warnings.append(f"已自动过滤 {rejected_count} 张非商品图片（平台UI/图标等）")

    # ── 价格检查 ──────────────────────────────────
    has_confirmed_price = any(sku.get("purchase_price_cny") for sku in skus)
    has_confirmed_price = has_confirmed_price or bool(pricing.get("source_price_cny"))
    # OZON 商品：有 reference_price_rub 也算价格已识别（参考售价 ≠ 采购价）
    has_ozon_ref_price = bool(pricing.get("reference_price_rub"))
    price_candidates = data.get("price_candidates", [])
    has_candidate = bool(price_candidates) or any(sku.get("candidate_price_cny") for sku in skus)
    has_candidate = has_candidate or bool(pricing.get("candidate_price_cny"))
    has_candidate = has_candidate or has_ozon_ref_price
    pricing_complete = data.get("pricing_complete", has_confirmed_price or has_ozon_ref_price)

    price_unconfirmed = not has_confirmed_price and not has_ozon_ref_price
    if not has_confirmed_price and not has_candidate:
        warnings.append("未识别到采购价，请手动填写")
    elif not has_confirmed_price and has_candidate and not has_ozon_ref_price:
        warnings.append("仅有候选价格（非确认价），需人工确认后填入")

    # ── 详情检查 ──────────────────────────────────
    detail_missing = False
    if is_js_platform and not prod.get("description_cn", "").strip():
        detail_missing = True
        warnings.append("详情描述缺失（H5/JS 平台），建议用浏览器插件补充")

    # ── 1688 质量闸门 ─────────────────────────────
    if is_1688 and (bad_source_page or needs_manual_capture or len(missing_fields) >= 2):
        needs_manual_capture = True
        if bad_source_page:
            warnings.insert(0, "‼️ 1688 采集质量不足，强烈建议使用浏览器插件重新采集")

    # ── OZON 商品采集质量检查 ──
    platform = data.get('platform', '')
    if platform == 'ozon_product':
        mp = missing_fields
        if not data.get('title_ru'): mp.append('商品标题(俄)')
        if not data.get('price_min_rub') and not data.get('price_max_rub'): mp.append('价格')
        if not data.get('skus', []): mp.append('SKU')
        if not data.get('media', []): mp.append('图片')
        ozon_ok = len(mp) <= 2
        return {
            'ok_for_adaptation': ozon_ok,
            'missing_fields': mp,
            'warnings': warnings,
            'needs_manual_capture': len(mp) > 3,
            'bad_source_page': False,
            'detail_missing': not data.get('description_ru'),
            'price_unconfirmed': not data.get('price_min_rub'),
            'image_count': len(data.get('media', [])),
            'usable_image_count': len(data.get('media', [])),
            'rejected_image_count': 0,
            'js_render_platform': False,
            'source_page_type': 'ozon_product',
            'price_candidates_count': 1 if data.get('price_min_rub') else 0,
        }

    ok_for_adaptation = not needs_manual_capture and not bad_source_page

    return {
        "ok_for_adaptation": ok_for_adaptation,
        "missing_fields": missing_fields,
        "warnings": warnings,
        "needs_manual_capture": needs_manual_capture,
        "bad_source_page": bad_source_page,
        "detail_missing": detail_missing,
        "price_unconfirmed": price_unconfirmed,
        "image_count": len(media_list),
        "usable_image_count": usable_count,
        "rejected_image_count": rejected_count,
        "js_render_platform": is_js_platform,
        "source_page_type": source_page_type,
        "price_candidates_count": len(price_candidates),
    }


def collect_ozon_product_url(url: str, user=None, api_key: str = None, provider: str = None) -> dict:
    """采集 OZON 商品链接，返回结构化数据。"""
    import requests as req
    from bs4 import BeautifulSoup

    result = {
        'platform': 'ozon_product',
        'source_url': url,
        'title_ru': '', 'title_cn': '',
        'brand': '', 'seller_name': '',
        'category_path': '', 'ozon_category_id': '', 'type_id': '',
        'price_min_rub': None, 'price_max_rub': None,
        'rating': None, 'review_count': None,
        'description_ru': '',
        'specs_json': [],
        'skus': [], 'media': [],
        'missing_fields': [], 'raw_json': {},
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'ru-RU,ru;q=0.9',
    }

    try:
        resp = req.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        result['missing_fields'].append(f'页面获取失败: {str(e)[:100]}')
        return result

    # 尝试从页面提取 JSON-LD 或 script data
    soup = BeautifulSoup(html, 'html.parser')

    # 提取标题
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.text.strip()
        result['title_ru'] = title[:300]
        result['title_cn'] = title[:300]  # 后续可翻译

    # 提取 meta 信息
    for meta in soup.find_all('meta'):
        if meta.get('name') == 'description':
            result['description_ru'] = meta.get('content', '')[:2000]
        if meta.get('property') == 'og:title':
            result['title_ru'] = meta.get('content', '')[:300]
        if meta.get('property') == 'og:image':
            result['media'].append({'url': meta.get('content', ''), 'role': 'main'})

    # 尝试从 script JSON 提取数据
    for script in soup.find_all('script'):
        text = script.string or ''
        if 'window.__NUXT__' in text or 'window.__INITIAL_STATE__' in text:
            try:
                # 简单提取 JSON 片段
                import re
                json_match = re.search(r'(?:window\.__NUXT__\s*=|window\.__INITIAL_STATE__\s*=)\s*(\{.*?\});', text, re.DOTALL)
                if json_match:
                    result['raw_json'] = json_match.group(1)[:50000]
            except Exception:
                pass

    # 提取图片
    for img in soup.find_all('img'):
        src = img.get('src') or img.get('data-src') or ''
        if src and src.startswith('http') and 'ozon' in src.lower():
            alt = img.get('alt', '')
            role = 'main' if 'main' in alt.lower() else 'detail'
            result['media'].append({'url': src, 'role': role, 'alt': alt[:200]})

    # 去重图片
    seen = set()
    unique_media = []
    for m in result['media']:
        if m['url'] not in seen:
            seen.add(m['url'])
            unique_media.append(m)
    result['media'] = unique_media[:30]

    # 标记缺失字段
    if not result['title_ru']: result['missing_fields'].append('title')
    if not result['price_min_rub']: result['missing_fields'].append('price')
    if not result['skus']: result['missing_fields'].append('skus')
    if not result['description_ru']: result['missing_fields'].append('description')

    return result
