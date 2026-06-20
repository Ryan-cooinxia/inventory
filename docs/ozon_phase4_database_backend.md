# OZON 模块 — 阶段 4：数据库和后端接口设计

版本：v0.1
日期：2026-06-13
状态：待审核

---

## 1. 设计原则

- 全部新表，不修改现有 `models.py` 中的任何表
- 所有用户数据隔离：`user = ForeignKeyField(User, ...)`
- 遵循现有 Peewee 模式：`BaseModel`、WAL、外键、索引
- 采集 JSON 完整保留为 `raw_json TextField`，不丢失源数据
- AI 产物与人工确认字段分离存储
- 状态流转可追溯，关键操作记录时间戳

---

## 2. 数据表设计（10 张新表）

### 2.1 OzonAccount — 店铺 API 凭证

```
用途：存储 OZON 店铺的 API 连接信息
对应页面：P2 平台接口
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| platform | CharField(20) | 固定 'ozon'，预留扩展 |
| name | CharField(100) | 店铺名称，如"OZON 测试店" |
| shop_type | CharField(20) | cross_border / local |
| environment | CharField(20) | test / production |
| client_id | CharField(200) | OZON API Client-Id |
| api_key | CharField(200) | OZON API Key（加密存储建议） |
| is_active | BooleanField | 是否启用 |
| last_sync_at | DateTimeField(null=True) | 最近同步时间 |
| sync_status | CharField(20, null=True) | ok / error |
| sync_error | TextField(null=True) | 最近同步错误 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

**索引**：`(user, name)` 唯一

---

### 2.2 OzonSource — 采集商品原始资料

```
用途：存储从各个平台采集的商品原始数据
对应页面：P3 采集列表
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| platform | CharField(20) | 1688 / taobao / tmall / pinduoduo / manual |
| source_url | CharField(500) | 源商品链接 |
| source_item_id | CharField(100, null=True) | 源平台商品 ID |
| title_cn | CharField(300) | 中文标题 |
| category_cn | CharField(100, null=True) | 中文类目 |
| description_cn | TextField(null=True) | 中文描述 |
| shop_name | CharField(200, null=True) | 供应商店铺名 |
| sku_count | IntegerField(default=0) | SKU 数量（冗余，方便列表展示） |
| image_count | IntegerField(default=0) | 图片数量（冗余） |
| raw_json | TextField() | 完整采集 JSON（schema v1.0） |
| status | CharField(20) | collected / parsed / drafted / archived |
| capture_method | CharField(30) | browser_extension / open_api / manual |
| captured_at | DateTimeField() | 采集时间 |
| remark | TextField(null=True) | 备注 |
| created_at | DateTimeField | 入库时间 |
| updated_at | DateTimeField | 更新时间 |

**索引**：`(user, platform)`、`(user, status)`

---

### 2.3 OzonSourceSku — 源商品 SKU

```
用途：解析采集 JSON 后的结构化 SKU 数据
对应页面：P4 商品加工（左侧源数据区）
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| source | FK(OzonSource) | 所属采集商品 |
| source_order | IntegerField() | 源 SKU 顺序（不可变） |
| source_sku_id | CharField(100) | 源 SKU 标识 |
| source_sku_name | CharField(200) | 源 SKU 名称 |
| color_cn | CharField(50, null=True) | 颜色（中） |
| color_ru | CharField(100, null=True) | 颜色（俄） |
| size_cn | CharField(50, null=True) | 尺寸（中） |
| size_ru | CharField(100, null=True) | 尺寸（俄） |
| style_cn | CharField(50, null=True) | 款式（中） |
| style_ru | CharField(100, null=True) | 款式（俄） |
| bundle_quantity | IntegerField(default=1) | 套装数量 |
| package_contents | TextField(null=True) | 包装内容 JSON 数组 |
| material_cn | CharField(100, null=True) | 材质（中） |
| purchase_price_cny | FloatField(null=True) | 采购价 ¥ |
| image_refs | TextField(null=True) | 关联图片 ID JSON 数组 |
| created_at | DateTimeField | 创建时间 |

**索引**：`(source, source_order)` 唯一

---

### 2.4 OzonSourceMedia — 源商品图片

```
用途：采集商品的所有图片及 OZON 可用性评估
对应页面：P4 商品加工（左侧图片区）、P5 图片方案
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| source | FK(OzonSource) | 所属采集商品 |
| media_id | CharField(100) | 媒体标识 |
| media_source | CharField(30) | source_page / generated / edited / manual_upload |
| role | CharField(30) | main / sku / detail / scene / selling_point / function / size / package |
| source_url | CharField(500, null=True) | 原始 URL |
| local_path | CharField(300, null=True) | 本地路径 |
| sku_refs | TextField(null=True) | 关联 SKU JSON 数组 |
| width | IntegerField(null=True) | 宽度 px |
| height | IntegerField(null=True) | 高度 px |
| aspect_ratio | CharField(10, null=True) | 宽高比，如 '3:4' |
| has_text | BooleanField(default=False) | 是否含文字 |
| text_language | CharField(20, null=True) | zh / ru / en / none / mixed |
| needs_cleanup | BooleanField(default=False) | 是否需要清理 |
| for_ozon | BooleanField(default=False) | 是否可用于 OZON |
| review_status | CharField(20) | pending / approved / rejected |
| created_at | DateTimeField | 创建时间 |

---

### 2.5 OzonDraft — OZON 刊登草稿

```
用途：AI 加工后的完整刊登草稿，是核心数据表
对应页面：P6 刊登草稿列表、P7 刊登草稿审核
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| account | FK(OzonAccount, null=True) | 目标店铺 |
| source | FK(OzonSource) | 源采集商品 |
| status | CharField(20) | draft → needs_review → ready → approved → publishing → published / failed |
| ozon_category_id | CharField(50, null=True) | OZON 类目 ID |
| ozon_category_path | CharField(300, null=True) | 类目路径 |
| title_ru | CharField(300, null=True) | 俄语标题 |
| description_ru | TextField(null=True) | 俄语描述 |
| bullets_ru | TextField(null=True) | 俄语卖点 JSON 数组 |
| attributes_json | TextField(null=True) | 类目属性 JSON |
| skus_json | TextField(null=True) | SKU 数据 JSON（快照） |
| pricing_json | TextField(null=True) | 定价数据 JSON |
| ai_title_confidence | FloatField(null=True) | AI 标题置信度 |
| ai_description_confidence | FloatField(null=True) | AI 描述置信度 |
| ai_bullets_confidence | FloatField(null=True) | AI 卖点置信度 |
| ai_category_confidence | FloatField(null=True) | AI 类目置信度 |
| validation_result | TextField(null=True) | 校验结果 JSON |
| price_manual_confirmed | BooleanField(default=False) | 价格是否人工确认 |
| reviewer_notes | TextField(null=True) | 审核备注 |
| ozon_product_id | CharField(50, null=True) | 发布后回写的 OZON 商品 ID |
| ozon_offer_id | CharField(100, null=True) | 发布后回写的 offer_id |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

**索引**：`(user, status)`、`(user, account)`

---

### 2.6 OzonDraftSku — 草稿 SKU 明细

```
用途：草稿中的 SKU 数据，与源 SKU 一一对应
对应页面：P7 刊登草稿审核（SKU 区）
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| draft | FK(OzonDraft) | 所属草稿 |
| source_sku | FK(OzonSourceSku, null=True) | 关联源 SKU |
| source_order | IntegerField() | 顺序号（与源一致） |
| source_sku_name | CharField(200) | 源 SKU 名称 |
| color_ru | CharField(100, null=True) | 颜色（俄） |
| style_ru | CharField(100, null=True) | 款式（俄） |
| bundle_quantity | IntegerField(default=1) | 套装数量 |
| purchase_price_cny | FloatField(null=True) | 采购价 ¥ |
| offer_id | CharField(100, null=True) | OZON offer_id |
| ozon_sku_id | CharField(50, null=True) | OZON SKU ID |
| created_at | DateTimeField | 创建时间 |

**索引**：`(draft, source_order)` 唯一

---

### 2.7 OzonImageSlot — 图片槽位

```
用途：草稿的图片方案，每个槽位对应一张需生成/审核的图片
对应页面：P5 图片方案
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| draft | FK(OzonDraft) | 所属草稿 |
| slot_order | IntegerField() | 槽位序号 1-8 |
| role | CharField(30) | main / sku / scene / selling_point / function / detail / size / package |
| scope | CharField(10) | all / sku |
| scope_sku_ref | CharField(100, null=True) | 适用范围 SKU 标识 |
| prompt_cn | TextField(null=True) | 中文提示词 |
| prompt_ru | TextField(null=True) | 俄语提示词 |
| negative_prompt | TextField(null=True) | 负面提示词 |
| style | CharField(50, null=True) | 视觉风格 |
| generated_url | CharField(500, null=True) | 生成图片 URL |
| local_path | CharField(300, null=True) | 本地路径 |
| status | CharField(20) | planned → generated → reviewed → approved / rejected |
| review_notes | TextField(null=True) | 审核备注 |
| created_at | DateTimeField | 创建时间 |

**索引**：`(draft, slot_order)` 唯一

---

### 2.8 OzonPublishJob — 发布任务

```
用途：记录每次 OZON API 发布的请求、响应和结果
对应页面：P8 发布任务
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| account | FK(OzonAccount) | 目标店铺 |
| draft | FK(OzonDraft) | 关联草稿 |
| action | CharField(30) | create_product / update_product / update_price / update_stock / upload_image |
| status | CharField(20) | pending → processing → success / failed |
| request_json | TextField(null=True) | API 请求 JSON |
| response_json | TextField(null=True) | API 响应 JSON |
| error_message | TextField(null=True) | 错误信息 |
| ozon_task_id | CharField(50, null=True) | OZON 返回的任务 ID |
| retry_count | IntegerField(default=0) | 重试次数 |
| created_at | DateTimeField | 创建时间 |
| completed_at | DateTimeField(null=True) | 完成时间 |

**索引**：`(user, status)`、`(draft, created_at)`

---

### 2.9 OzonPrompt — 提示词模板

```
用途：按品类和类型的 AI 提示词模板
对应页面：P9 提示词库
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| name | CharField(100) | 模板名称 |
| prompt_type | CharField(20) | title / bullets / description / image |
| category | CharField(50) | 适用品类，'common' 为通用 |
| content | TextField() | 提示词内容（含 {变量} 占位） |
| variables | TextField(null=True) | 变量说明 JSON |
| is_default | BooleanField(default=False) | 是否系统默认 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

**索引**：`(user, prompt_type, category)` 唯一

---

### 2.10 OzonPricingRule — 定价规则

```
用途：OZON 售价计算公式的配置参数
对应页面：P10 定价规则
```

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离（或系统级别共享） |
| name | CharField(100) | 规则名称 |
| exchange_rate_source | CharField(10) | auto / manual |
| manual_exchange_rate | FloatField(null=True) | 手动汇率（1 CNY = X RUB） |
| target_margin_rate | FloatField(default=0.35) | 目标毛利率 |
| ad_reserve_rate | FloatField(default=0.05) | 广告预留比例 |
| commission_rate | FloatField(default=0.10) | OZON 佣金率 |
| risk_buffer_type | CharField(10) | fixed / percent |
| risk_buffer_value | FloatField(default=3.0) | 风险缓冲值 |
| logistics_tiers | TextField(null=True) | 物流阶梯 JSON |
| is_default | BooleanField(default=False) | 是否默认规则 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

---

## 3. 完整状态流转

### 3.1 采集商品状态 (OzonSource.status)

```
collected ──→ parsed ──→ drafted ──→ archived
   │                        │
   └── 解析 JSON 入库       └── 生成草稿后
```

### 3.2 刊登草稿状态 (OzonDraft.status)

```
draft ──→ needs_review ──→ ready ──→ approved ──→ publishing ──→ published
                                     │
                                     └──→ failed (可回到 draft)
```

| 状态 | 含义 | 谁操作 |
|------|------|--------|
| draft | AI 刚生成，未整理 | 系统 |
| needs_review | 待人工审核 | 系统自动标记 |
| ready | 已整理，待最终审核 | 运营 |
| approved | 审核通过，可发布 | 运营 |
| publishing | 正在调用 OZON API | 系统 |
| published | OZON 返回成功 | 系统 |
| failed | 发布失败 | 系统 |

### 3.3 图片槽位状态 (OzonImageSlot.status)

```
planned ──→ generated ──→ reviewed ──→ approved
                         │
                         └──→ rejected (可回到 planned)
```

### 3.4 发布任务状态 (OzonPublishJob.status)

```
pending ──→ processing ──→ success
                        │
                        └──→ failed (可 retry → pending)
```

---

## 4. Blueprint 路由设计

### 4.1 蓝图定义

```python
# blueprints/ozon.py
ozon_bp = Blueprint('ozon', __name__, url_prefix='/ozon')
```

所有路由加 `@login_required`，所有查询过滤 `user == current_user`。

### 4.2 完整路由表（23 条）

#### 总览区

| 方法 | 路由 | 功能 | 对应原型 |
|------|------|------|----------|
| GET | `/ozon/dashboard` | 工作台仪表盘 | P1 |
| GET | `/ozon/accounts` | 店铺列表 | P2 |
| POST | `/ozon/accounts/add` | 新增店铺 | P2 |
| GET | `/ozon/accounts/<id>/edit` | 编辑店铺表单 | P2 |
| POST | `/ozon/accounts/<id>/edit` | 保存编辑 | P2 |
| POST | `/ozon/accounts/<id>/delete` | 删除店铺 | P2 |
| POST | `/ozon/accounts/<id>/test` | 测试连通性 | P2 |

#### 操作区

| 方法 | 路由 | 功能 | 对应原型 |
|------|------|------|----------|
| GET | `/ozon/sources` | 采集列表 | P3 |
| POST | `/ozon/sources/add` | 手动粘贴 JSON 入库 | P3 |
| GET | `/ozon/sources/<id>` | 采集详情 | P3 |
| POST | `/ozon/sources/<id>/delete` | 删除采集 | P3 |
| GET | `/ozon/processing/<source_id>` | 商品加工页（AI 生成） | P4 |
| POST | `/ozon/processing/<source_id>/generate` | 触发 AI 生成 | P4 |
| POST | `/ozon/processing/<source_id>/save` | 保存草稿 | P4 |
| GET | `/ozon/image-plan/<draft_id>` | 图片方案页 | P5 |
| POST | `/ozon/image-plan/<draft_id>/save` | 保存图片方案 | P5 |
| GET | `/ozon/listings` | 刊登草稿列表 | P6 |
| GET | `/ozon/listings/<draft_id>` | 草稿审核详情 | P7 |
| POST | `/ozon/listings/<draft_id>/save` | 保存草稿编辑 | P7 |
| POST | `/ozon/listings/<draft_id>/validate` | 执行发布前校验 | P7 |
| POST | `/ozon/listings/<draft_id>/approve` | 审核通过 | P7 |
| POST | `/ozon/listings/<draft_id>/publish` | 提交发布 | P7 |
| POST | `/ozon/listings/<draft_id>/delete` | 删除草稿 | P6 |
| GET | `/ozon/publish-jobs` | 发布任务列表 | P8 |
| POST | `/ozon/publish-jobs/<job_id>/retry` | 重试失败任务 | P8 |

#### 配置区

| 方法 | 路由 | 功能 | 对应原型 |
|------|------|------|----------|
| GET/POST | `/ozon/prompts` | 提示词库列表 + 新增 | P9 |
| POST | `/ozon/prompts/<id>/delete` | 删除提示词 | P9 |
| GET/POST | `/ozon/pricing` | 定价规则查看 + 保存 | P10 |

### 4.3 菜单注册

在 `app.py` 中注册蓝图后，通过上下文处理器将 OZON 菜单注入所有模板的导航栏：

```python
@app.context_processor
def inject_ozon_menu():
    return {'ozon_menu': True}  # 模板据此渲染 OZON 下拉菜单
```

导航栏位置：在"统计报表"之后插入"OZON 运营"下拉菜单。

---

## 5. 关键 API 交互设计

### 5.1 商品加工 AI 生成 (POST /ozon/processing/<id>/generate)

**请求**：无额外参数（source_id 来自 URL）

**处理流程**：
1. 读取 `OzonSource.raw_json`
2. 调用 AI API（如 DeepSeek/GPT-4o），使用提示词模板
3. 生成：俄语标题、卖点、描述、类目候选、属性候选
4. 创建 `OzonDraft` 记录，状态 = `draft`
5. 创建 `OzonDraftSku` 记录，保持源顺序
6. 创建 `OzonImageSlot` 记录（8 个槽位，状态 = `planned`）

**响应**：
```json
{
  "success": true,
  "draft_id": 1,
  "redirect": "/ozon/listings/1"
}
```

### 5.2 发布前校验 (POST /ozon/listings/<id>/validate)

**校验项（参考 PRD 第 10 节）**：

```python
checks = [
    ("未选择 OZON 店铺", draft.account is not None),
    ("俄语标题未填写", draft.title_ru and len(draft.title_ru) > 0),
    ("OZON 类目未选择", draft.ozon_category_id is not None),
    ("缺少 SKU 数据", OzonDraftSku.select().where(OzonDraftSku.draft == draft).count() > 0),
    ("价格未人工确认", draft.price_manual_confirmed),
    ("图片未全部审核通过", all_slots_approved),
    ("买家可见内容含禁止词", not has_blocked_words),
]
blocking = [c for c in checks if not c[1]]
```

**响应**：
```json
{
  "blocking_count": 3,
  "checks": [
    {"label": "俄语标题已填写", "pass": true, "level": "success"},
    {"label": "类目必填属性 'Бренд' 未填写", "pass": false, "level": "error", "blocking": true},
    ...
  ]
}
```

### 5.3 提交发布 (POST /ozon/listings/<id>/publish)

**前置条件**：
- `draft.status == 'approved'`
- `draft.account is not None`
- 校验通过（0 阻断项）

**处理流程**：
1. 创建 `OzonPublishJob`，状态 = `pending`
2. 将 job 状态改为 `processing`
3. 构造 OZON API 请求体（`POST /v3/product/import`）
4. 调用 OZON API
5. 根据响应更新：
   - 成功：`job.status = 'success'`，`draft.status = 'published'`，回写 `ozon_product_id`
   - 失败：`job.status = 'failed'`，记录 `error_message`，`draft.status = 'failed'`
6. 完整保存 `request_json` 和 `response_json`

---

## 6. 与现有系统集成点

### 6.1 汇率复用

`OzonPricingRule` 可配置汇率来源为 `auto`，此时读取现有 `ExchangeRate` 表的 `CNY→RUB` 汇率。

### 6.2 用户认证

所有 OZON 路由使用 `@login_required`，所有查询过滤 `user == current_user`。

### 6.3 导航栏

通过 `app.py` 的 `context_processor` 注入 OZON 菜单项，不修改 `base.html` 结构（仅新增一个下拉菜单组）。

### 6.4 不修改现有功能

- 不修改 `models.py` 现有表
- 不修改现有蓝图文件
- 不影响订单、库存、财务、客户功能

---

## 7. ER 关系图（文本）

```text
User ──┬── OzonAccount (1:N)
       ├── OzonSource (1:N) ──┬── OzonSourceSku (1:N)
       │                      └── OzonSourceMedia (1:N)
       ├── OzonDraft (1:N) ──┬── OzonDraftSku (1:N)
       │                     ├── OzonImageSlot (1:N, 最多 8)
       │                     └── OzonPublishJob (1:N)
       ├── OzonPrompt (1:N)
       └── OzonPricingRule (1:N)

OzonDraft.account ──→ OzonAccount
OzonDraft.source ──→ OzonSource
OzonDraftSku.source_sku ──→ OzonSourceSku
OzonPublishJob.account ──→ OzonAccount
OzonPublishJob.draft ──→ OzonDraft
```

---

## 8. 迁移策略

### 8.1 迁移脚本

```bash
# 创建所有 OZON 表
G:\inventory\.venv\Scripts\python.exe migrate_ozon.py
```

迁移脚本使用 `db.create_tables([...], safe=True)`，不破坏现有数据。

### 8.2 初始数据

- 预置 4 条默认 `OzonPrompt`（标题/卖点/描述/图片 通用模板）
- 预置 1 条默认 `OzonPricingRule`

### 8.3 回滚方式

```sql
DROP TABLE IF EXISTS ozonpublishjob;
DROP TABLE IF EXISTS ozonimageslot;
DROP TABLE IF EXISTS ozondraftsku;
DROP TABLE IF EXISTS ozondraft;
DROP TABLE IF EXISTS ozonsourcemedia;
DROP TABLE IF EXISTS ozonsourcesku;
DROP TABLE IF EXISTS ozonsource;
DROP TABLE IF EXISTS ozonprompt;
DROP TABLE IF EXISTS ozonpricingrule;
DROP TABLE IF EXISTS ozonaccount;
```

---

## 9. 文件规划

| 文件 | 说明 |
|------|------|
| `models.py` | 新增 10 个 OZON Model 类（追加在现有模型之后） |
| `blueprints/ozon.py` | OZON 蓝图，约 600-800 行 |
| `templates/ozon/` | 10 个 Jinja2 模板（从原型转化） |
| `migrate_ozon.py` | OZON 表创建脚本 |
| `app.py` | 注册蓝图 + context_processor（追加 4-5 行） |

---

## 10. 下一步

用户确认数据结构和路由设计后，进入阶段 5：前端页面实现（原型 → Jinja2 模板）。

阶段 5 实施顺序建议：
1. `models.py` 新增 OZON 模型
2. `migrate_ozon.py` 建表 + 初始数据
3. `blueprints/ozon.py` 路由骨架
4. 逐个页面转化：工作台 → 平台接口 → 采集列表 → 商品加工 → 图片方案 → 草稿列表 → 草稿审核 → 发布任务 → 提示词库 → 定价规则
5. `app.py` 注册蓝图 + 导航菜单
6. 启动验证 + 浏览器测试
