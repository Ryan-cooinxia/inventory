# 仓库记账系统 (Inventory Management System)

基于 **Flask + Peewee + SQLite** 的仓库进销存管理系统。

## 技术栈

| 层面 | 技术 |
|------|------|
| Web框架 | Flask 3.0 |
| ORM | Peewee 4.0 |
| 数据库 | SQLite (WAL模式) |
| 认证 | Flask-Login |
| 前台模板 | Jinja2 |
| 生产服务器 | Waitress |
| 并发限制 | Flask-Limiter |
| AI集成 | OpenAI API |
| 文件解析 | openpyxl, pdfplumber |

## 项目结构

```
G:\inventory\
├── app.py              # 主入口，蓝图注册，数据库初始化
├── models.py           # Peewee 数据模型 (Product, Order, User 等)
├── helpers.py          # 库存计算、SKU生成、数据校验
├── services.py         # 汇率后台更新服务
├── crypto_utils.py     # 加密工具
├── extensions.py       # Flask扩展 (limiter)
├── waitress_server.py  # 生产启动脚本
├── requirements.txt    # 依赖
├── data.db             # SQLite 数据库
│
├── blueprints/         # 所有路由蓝图
│   ├── home.py         # 首页/仪表盘
│   ├── products.py     # 产品管理
│   ├── customers.py    # 客户管理
│   ├── suppliers.py    # 供应商管理
│   ├── purchases.py    # 入库单
│   ├── sales.py        # 出库单
│   ├── orders.py       # 客户订单
│   ├── supplier_orders.py  # 供应商订单
│   ├── reports.py      # 统计报表
│   ├── refunds.py      # 退款管理
│   ├── finance.py      # 财务模块
│   ├── data_io.py      # 数据导入导出
│   ├── exchange.py     # 汇率换算
│   ├── tools.py        # 定价工具箱
│   ├── auth.py         # 用户认证
│   ├── logs.py         # 操作日志
│   ├── ai_import.py    # AI智能导入
│   ├── agent.py        # AI智能助手
│   └── utils.py        # 工具接口
│
├── templates/          # Jinja2 模板
├── static/             # 静态资源
│
└── migrate_*.py        # 各类数据迁移脚本
```

## 常用命令

```bash
# 开发模式运行
cd G:\inventory
G:\inventory\.venv\Scripts\python.exe app.py

# 生产模式运行
G:\inventory\.venv\Scripts\python.exe waitress_server.py

# 安装依赖
G:\inventory\.venv\Scripts\pip.exe install -r requirements.txt

# 数据迁移
G:\inventory\.venv\Scripts\python.exe migrate_stock.py
```

## 核心数据模型

- **Product** — 产品，含缓存库存字段 `stock`
- **ProductBundle / ProductBundleItem** — 套装组合
- **PurchaseOrder / PurchaseOrderItem** — 入库单
- **SalesOrder / SalesOrderItem** — 出库单
- **CustomerOrder / CustomerOrderItem** — 客户订单
- **SupplierOrder / SupplierOrderItem** — 供应商订单
- **CustomerRefund / CustomerTransaction** — 退款与交易
- **ExchangeRate** — 汇率（CNY→RUB/USD/EUR/GBP，每小时自动更新）
- **User** — 用户（支持多用户数据隔离）
- **UserApiKey** — 用户API密钥
- **OperationLog** — 操作日志

## 关键约定

- 所有蓝图路由都有 `user_id` 过滤实现多用户数据隔离
- 数据库使用 WAL 模式，支持并发读写
- 汇率在应用启动时通过后台线程自动拉取并定时更新
- 库存有缓存字段 `Product.stock`，出入库时自动更新
- 模板使用 Jinja2，前台交互使用原生 JS + Fetch API
