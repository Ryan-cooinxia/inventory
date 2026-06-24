# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用命令

```bash
# 开发运行（默认 127.0.0.1:5000）
G:\inventory\.venv\Scripts\python.exe app.py

# 生产运行（Waitress，端口 8100）
G:\inventory\.venv\Scripts\python.exe waitress_server.py

# 自定义端口
FLASK_PORT=8100 G:\inventory\.venv\Scripts\python.exe app.py

# 安装依赖
G:\inventory\.venv\Scripts\pip.exe install -r requirements.txt

# 数据迁移（按需执行对应的 migrate_*.py）
G:\inventory\.venv\Scripts\python.exe migrate_stock.py

# Git push（HTTPS 备用，SSH 超时时使用）
git push https://github.com/Ryan-cooinxia/inventory.git main
```

## 技术栈

Flask 3.0 + Peewee 4.0 + SQLite (WAL) + Flask-Login + Jinja2 + Bootstrap 5 + Chart.js
生产服务器：Waitress | 速率限制：Flask-Limiter | AI：OpenAI API (GPT-4o)
文件解析：openpyxl + pdfplumber | 运行环境：Python 3.11 + Windows

## 架构模式

### 请求生命周期

```
app.before_request → db.connect(reuse_if_open=True)
    ↓
@login_required（几乎所有路由）→ current_user 可用
    ↓
业务逻辑：所有查询链 .where(model.user == current_user)
    ↓
app.after_request → db.close()
```

### 蓝图约定

- 每个蓝图文件顶部 `Blueprint('name', __name__)`
- 所有路由加 `@login_required`（auth_bp 除外）
- 所有数据库查询必须过滤 `user == current_user`，包括子查询和聚合
- 表单解析使用 `crud_utils.parse_order_items_from_form()` 统一处理
- 分页使用 `crud_utils.paginate()` 统一处理
- 安全单条查询使用 `crud_utils.get_or_none_user()`
- 操作审计使用 `log_utils.log_action(user, action_type, target_type, ...)`

### 多用户数据隔离

核心模式：**每条路由、每个查询都要过滤 user_id**

```python
# 正确示例 — 所有查询链路都有 user 过滤
orders = SupplierOrder.select().where(SupplierOrder.user == current_user)
receipts = (PurchaseOrderItem
            .select(fn.SUM(PurchaseOrderItem.quantity))
            .join(PurchaseOrder)
            .where((PurchaseOrder.supplier_order.in_(order_ids)) &
                   (PurchaseOrderItem.product == product) &
                   (PurchaseOrder.user == current_user)))  # ← 不可遗漏
```

### 对账时段模式（跨 3 个蓝图）

对账时段 = date range 拆分收货量，公式：**本时段入库 = 截止期末收货 - 期初前收货**

涉及文件：`blueprints/home.py`、`blueprints/reports.py`、`blueprints/supplier_orders.py`

```python
# 参数解析（默认两个日期都 = 今天）
reconcile_start = parse_date(request.args.get('reconcile_start', '')) or today
reconcile_end   = parse_date(request.args.get('reconcile_end', ''))   or today

# 三段式拆分
received_before    = SUM(收货日期 < reconcile_start)            # 期初剩余 = 总订 - received_before
received_up_to_end = SUM(收货日期 <= reconcile_end)             # 期末剩余 = 总订 - received_up_to_end
received_in_period = received_up_to_end - received_before       # 本时段入库

# 注意：received_in_period 必须在 if/else 之外初始化 = 0，否则 UnboundLocalError
```

### 库存计算

- `Product.stock` 是缓存字段，出入库时通过信号/手动更新
- `helpers.get_product_stock()` 在需要精确值时实时聚合出入库量（带 user 过滤）
- 首页库存列表只显示 `stock > 0` 的产品，并计算加权平均成本 × 库存量 = 库存货值

### 后台服务

- 汇率在 `app.py` 启动时通过 `services.py` 后台线程每小时拉取一次
- 使用 `threading.Lock` 保护写入，请求只读缓存（数据库）
- API: `https://api.exchangerate-api.com/v4/latest/CNY` → RUB/USD/EUR/GBP

## 关键约定

- **运行验证**：每次修改代码后必须先 `python app.py` 启动验证，确认无报错再提交
- **GPK API**：页面内交互使用原生 `fetch()` + JSON，不走表单提交
- **模板**：Jinja2 + Bootstrap 5，36 个模板，`base.html` 为基模板
- **数据库**：WAL 模式，`busy_timeout=3000`，外键已启用，`safe=True` 建表
- **日期处理**：统一 `datetime.date` 类型，模板传 `str()` 格式，`<` / `<=` / `.between()` 做范围查询
- **错误处理**：Peewee `.scalar() or 0` 处理空结果，`fn.SUM()` 可能返回 `None`
- **路由安全**：`safe_redirect_fallback()` 防止开放重定向，只允许 `/` 开头的相对路径
- **速率限制**：注册 3次/小时，登录 5次/分钟（Flask-Limiter）
- **Git 仓库**：`https://github.com/Ryan-cooinxia/inventory.git`，分支 `main`

## 图片识别（vision-tools MCP）

当用户提供本地截图路径时，必须优先调用 `analyze_image` 工具进行分析：

```
mcp__vision-tools__analyze_image
```

### 使用规则

1. 用户给出截图文件路径 → 直接调用 `analyze_image`
2. 根据截图类型选择 `task_type`：
   - OZON 卖家后台截图 → `ozon_backend`（提取字段、类目路径、按钮、表单结构）
   - 本系统页面截图 → `ui_review`（对比当前页面实现，找差异）
   - 商品图片 → `product_image`（检查合规：中文、水印、logo）
   - 纯文字提取 → `ocr`
   - 其他 → `general`
3. **不要凭空猜测模糊文字**。模型返回中标注为 uncertain 的内容必须向用户确认。
4. OZON 后台截图分析后，将提取的字段/类目与本地数据库对照，补全缺失项。
5. 置信度低的内容用 `[不确定: xxx]` 标记。

### 环境要求

- API Key: 设置环境变量 `OPENAI_API_KEY` 或 `DASHSCOPE_API_KEY`
- 默认模型: `gpt-4o`（可通过 `VISION_MODEL` 环境变量覆盖）
- MCP 配置: `.mcp.json`

## 项目改动日志

每次大改动后在 `G:\inventory\项目改动日志\` 目录下记录，命名格式 `YYYY-MM-DD_描述.md`。
最新日志：[2026-06-21_22_OZON模块全面优化.md](项目改动日志/2026-06-21_22_OZON模块全面优化.md)

## 最近关键修复/优化（2026-06-21~22）

### 数据库索引问题
- `OzonCategoryAttribute` 和 `OzonAttributeValue` 存在过时唯一索引不含 `type_id`
- 导致同 dcid 下多 type 的属性/字典值静默丢失
- 修复：DROP 旧索引，补齐所有缺失数据

### 翻译体系
- `OzonAttributeValue` 有 `value_cn` 字段（ALTER TABLE 手动添加，models.py 已同步）
- 字典值翻译优先显示中文，俄语原文作为小字提示

### 图片生成
- 图片生成配置存在 `VisionModelConfig` 表，provider 前缀 `img_gen_`
- 图片生成需要 OpenAI DALL-E 3 或通义万相 Key
- 加工页 → 生成内容 → 保存 → 图片方案 → 批量生成

### 适配工作台属性加载
- 选 type 后自动从本地 DB 加载属性（`loadAttributeForm`），不再需要手动同步
- 属性加载成功则隐藏"同步属性/字典"按钮
- `api_get_category_attributes` 和 `adaptation_workspace` 均已加 type_id 过滤+去重
