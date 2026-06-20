# OZON 在线商品管理模块方案

版本：v0.1
日期：2026-06-14
用途：将 OZON 店铺已发布商品的列表、查看、修改、归档/恢复等操作接入本系统
适用对象：OZON 中小卖家，参考店小秘、妙手等 ERP 的“在线商品/商品管理”能力

## 1. 当前系统进度判断

根据当前工作区代码，Claude Code 已经完成或正在完成：

- `blueprints/ozon.py`：OZON 运营蓝图已存在。
- `templates/ozon/`：已有工作台、平台接口、采集列表、商品适配、刊登草稿、发布任务、提示词库、定价规则等页面。
- `services/ozon_api.py`：已有 OZON API 客户端。
- `OzonAPIClient.list_products()`：已封装 `POST /v3/product/list`。
- `OzonAPIClient.import_product()`：已封装创建/更新商品。
- `OzonAPIClient.import_product_info()`：已封装发布任务查询。
- 类目属性、字段缺口、视觉模型、商品适配等能力已有文档和部分实现。

目前缺口：

- 还没有独立的“在线商品”页面。
- 还没有本地在线商品缓存表。
- 还没有从 OZON 拉取商品列表后落库。
- 还没有在线商品详情页。
- 还没有在线商品的归档/恢复操作。
- 价格、库存更新在 `services/ozon_api.py` 中仍是待实测占位。

## 2. 模块应该放在哪里

建议放在主导航：

```text
OZON 运营
  工作台
  平台接口
  模型接口
  ----------------
  采集列表
  商品适配
  刊登草稿
  在线商品       <- 新增
  发布任务
  ----------------
  商品事实库
  类目属性字典
  提示词库
  定价规则
```

原因：

- `刊登草稿` 是发布前。
- `在线商品` 是发布后。
- `发布任务` 是发布过程日志。

三者顺序应该是：

```text
刊登草稿 -> 在线商品 -> 发布任务
```

## 3. 模块定位

模块名称：

```text
在线商品
```

路由建议：

```text
/ozon/online-products
/ozon/online-products/<id>
/ozon/online-products/sync
/ozon/online-products/<id>/archive
/ozon/online-products/<id>/unarchive
/ozon/online-products/<id>/update-price
/ozon/online-products/<id>/update-stock
```

页面文件建议：

```text
templates/ozon/online_products.html
templates/ozon/online_product_detail.html
```

原型文件建议：

```text
docs/prototypes/ozon_online_products.v1.html
docs/prototypes/ozon_online_product_detail.v1.html
```

## 4. 第一版功能范围

第一版建议只做“读取 + 缓存 + 归档/恢复”，不要一次性做复杂批量改价和库存。

### 4.1 必做

- 选择 OZON 店铺。
- 从 OZON API 拉取商品列表。
- 将商品列表保存到本地缓存。
- 在线商品列表展示。
- 支持按状态、店铺、标题、offer_id 搜索。
- 查看在线商品详情。
- 显示 OZON 商品 ID、offer_id、SKU、状态、可见性、价格、库存、错误信息。
- 支持单个商品归档。
- 支持单个商品恢复。
- 记录每次同步和归档/恢复操作日志。

### 4.2 第二版再做

- 批量归档。
- 批量恢复。
- 批量同步所有店铺。
- 修改标题/描述/属性。
- 修改价格。
- 修改库存。
- 修改图片。
- 与本地库存自动联动。
- 与刊登草稿双向对照。

## 5. 数据模型建议

### 5.1 OzonOnlineProduct

用途：缓存 OZON 店铺已在线商品。

字段建议：

```text
id
user_id
account_id
ozon_product_id
offer_id
sku
name
status
visibility
is_archived
price
currency
stock
category_id
category_name
primary_image
errors_json
commissions_json
raw_json
last_synced_at
created_at
updated_at
```

索引建议：

```text
(user_id, account_id)
(user_id, offer_id)
(user_id, ozon_product_id)
(user_id, status)
```

唯一约束建议：

```text
(user_id, account_id, offer_id)
```

### 5.2 OzonOnlineProductAction

用途：记录在线商品操作日志。

字段建议：

```text
id
user_id
account_id
online_product_id
action_type              sync | archive | unarchive | update_price | update_stock | update_content
status                   pending | success | failed
request_json
response_json
error_message
created_at
updated_at
```

## 6. API 服务层建议

在 `services/ozon_api.py` 中补充或确认：

### 6.1 已有

```python
list_products(last_id="", limit=100, filter_dict=None)
```

用途：

- 拉取在线商品列表。

### 6.2 需要新增

具体接口路径以 OZON API 实测为准，先封装方法，不要硬编码到页面逻辑里。

```python
archive_products(product_ids=None, offer_ids=None)
unarchive_products(product_ids=None, offer_ids=None)
get_product_info(product_ids=None, offer_ids=None)
update_prices(prices)
update_stocks(stocks)
```

建议：

- 第一版只实现 `archive_products`、`unarchive_products`、`get_product_info`。
- `update_prices`、`update_stocks` 等接口先实测后再开放页面操作。

## 7. 在线商品列表页面设计

页面名称：

```text
OZON 在线商品
```

页面顶部：

- 店铺选择
- 同步 OZON 商品按钮
- 最近同步时间
- 同步状态

筛选区：

- 店铺
- 商品状态
- 是否归档
- offer_id
- 商品标题
- OZON 商品 ID

表格字段：

```text
复选框
图片
商品标题
offer_id
OZON 商品 ID
SKU
状态
可见性
价格
库存
最近同步
操作
```

操作按钮：

```text
查看
同步详情
归档
恢复
```

第一版暂不直接提供：

```text
改价
改库存
改标题
改图片
```

这些先放到详情页或第二版。

## 8. 在线商品详情页面设计

展示：

- 基础信息
- OZON 状态
- 本地关联草稿
- SKU/变体信息
- 图片
- 价格
- 库存
- 类目属性
- 平台返回错误
- 原始 API JSON
- 操作记录

操作：

- 从 OZON 同步详情
- 归档
- 恢复
- 跳转到关联刊登草稿
- 复制 offer_id
- 查看原始 JSON

第二版操作：

- 修改价格
- 修改库存
- 更新图片
- 更新描述
- 重新发布修改

## 9. 与现有页面的关系

### 9.1 与刊登草稿

刊登草稿发布成功后：

- 写入 `OzonDraft.ozon_product_id`
- 写入 `OzonDraft.ozon_offer_id`
- 同步或创建 `OzonOnlineProduct`

在草稿详情页显示：

```text
已发布到在线商品：点击查看
```

### 9.2 与发布任务

发布任务只记录“操作过程”。

在线商品记录“平台当前状态”。

不要把在线商品列表放到发布任务里。

### 9.3 与商品适配

商品适配是发布前的源数据/事实/Listing 转换。

在线商品是发布后的平台商品运营。

不要混在一个页面里。

## 10. 状态设计

本地在线商品状态建议：

```text
active          正常在线
hidden          不可见
archived        已归档
blocked         平台拦截/异常
sync_error      同步失败
unknown         未知
```

同步逻辑：

- 每次从 OZON 拉取后更新本地状态。
- 如果本地商品未出现在本次拉取结果中，不立刻删除，只标记 `unknown` 或保留上次状态。

## 11. 实施顺序

### 阶段 1：原型和文档

交付：

```text
docs/prototypes/ozon_online_products.v1.html
docs/prototypes/ozon_online_product_detail.v1.html
```

用户审核通过后进入代码实现。

### 阶段 2：数据模型

新增：

```text
OzonOnlineProduct
OzonOnlineProductAction
```

新增迁移：

```text
migrate_ozon_online_products.py
```

### 阶段 3：只读同步

实现：

- `/ozon/online-products`
- `/ozon/online-products/sync`
- `list_products()` 拉取并缓存。

验收：

- 可选择店铺同步商品。
- 可看到 OZON 商品列表。
- 可搜索 offer_id。

### 阶段 4：详情页

实现：

- `/ozon/online-products/<id>`
- 展示原始 JSON。
- 展示关联草稿。

### 阶段 5：归档/恢复

实现：

- 单个商品归档。
- 单个商品恢复。
- 记录操作日志。

注意：

- 归档/恢复属于平台写操作，必须先在测试店验证。
- 正式店归档必须二次确认。

### 阶段 6：批量与修改

后续实现：

- 批量归档。
- 批量恢复。
- 改价。
- 改库存。
- 修改图片和内容。

## 12. 风险与注意事项

- OZON API 返回字段可能与预期不同，必须保存 `raw_json`。
- 归档/恢复是高风险操作，正式店必须二次确认。
- 改价和改库存会影响真实销售，必须分阶段开放。
- 在线商品状态以 OZON 同步结果为准，本地只做缓存。
- 不要把同步不到的商品直接删除。

## 13. 给 Claude Code 的任务提示

第一步：

```text
请根据 docs/ozon_online_products_management_plan.md，
先创建在线商品列表和在线商品详情的低保真 HTML 原型，
放到 docs/prototypes/，
不要修改业务代码。
```

第二步：

```text
在原型审核通过后，设计 OzonOnlineProduct 和 OzonOnlineProductAction 数据模型，
先输出设计说明，再实现迁移和页面。
```

第三步：

```text
先实现只读同步 /v3/product/list 到本地缓存。
归档、恢复、改价、改库存等写操作等测试店验证后再开放。
```
