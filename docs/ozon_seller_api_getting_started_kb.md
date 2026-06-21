# OZON Seller API Getting Started 知识库

版本：v0.1
整理日期：2026-06-20
来源页面：<https://docs.ozon.ru/api/seller/zh/?__rr=2#tag/Getting-started>
官方文档根入口：<https://docs.ozon.ru/api/seller/>
适用范围：OZON Seller API 接入、店铺凭证配置、连通性测试、商品刊登链路联调

## 0. 访问记录与可信度说明

本文件用于沉淀 OZON Seller API 的接入知识，作为本项目 OZON 模块后续开发、联调和运营排查的基础资料。

2026-06-20 读取官方页面时，`docs.ozon.ru` 对命令行抓取返回 Antibot Challenge，无法稳定取得完整页面源码或 OpenAPI JSON。因此本文不做逐字摘录，而是将官方 Getting Started 页面入口、OZON Seller API 通用接入规范、本项目已有 `services/ozon_api.py` 封装和现有 OZON 方案文档整理为可执行知识库。正式联调前仍需用测试店 `Client-Id` / `Api-Key` 对接口路径和响应结构做一次实测确认。

## 1. API 基础信息

OZON Seller API 面向卖家系统集成，用于读取商品、类目、属性、价格、库存、发布任务等数据，也可提交商品创建/更新任务。

基础域名：

```text
https://api-seller.ozon.ru
```

请求方式：

- 绝大多数接口使用 `POST`。
- 请求体使用 JSON。
- 响应体通常也是 JSON。
- 所有请求必须走 HTTPS。

基础请求头：

```http
Client-Id: <OZON Client Id>
Api-Key: <OZON API Key>
Content-Type: application/json
```

本系统对应封装位置：

- `services/ozon_api.py`
- `OzonAPIClient`
- `OZON_API_BASE = "https://api-seller.ozon.ru"`

## 2. 凭证与安全要求

`Client-Id` 和 `Api-Key` 是店铺级 API 调用凭证。配置后即可代表该店铺调用 Seller API，因此必须按敏感信息处理。

系统落地要求：

- API Key 只允许加密存储，不在页面明文回显。
- 日志中只记录脱敏后的 `Client-Id` / `Api-Key`。
- 所有接口错误日志禁止完整输出 API Key。
- 每个用户只能访问自己配置的 OZON 账户。
- 新增或修改凭证后必须立即执行连通性测试。
- 删除店铺账户前必须确认是否存在发布任务、在线商品缓存或历史日志。

建议脱敏格式：

```json
{
  "Client-Id": "123***",
  "Api-Key": "***"
}
```

## 3. 最小接入流程

### 3.1 准备店铺 API 凭证

运营人员需要在 OZON 卖家后台开通或获取 Seller API 凭证，然后在本系统的 OZON 账户管理页录入：

- 店铺名称
- `Client-Id`
- `Api-Key`
- 店铺类型：跨境店 / 本土店
- 是否启用

### 3.2 执行连通性测试

连通性测试推荐使用商品列表接口，只读取 1 条数据，不产生写操作。

接口：

```text
POST /v3/product/list
```

请求示例：

```json
{
  "filter": {},
  "last_id": "",
  "limit": 1
}
```

验收标准：

- HTTP 状态码为 2xx。
- 返回商品列表或空列表均视为连通成功。
- 能记录请求耗时、店铺 ID、返回总数。
- 非 2xx 时记录状态码、响应体摘要和错误类型。

本系统当前封装：

- `OzonAPIClient.test_connectivity()`
- `test_account(account)`

### 3.3 同步类目树

商品发布前必须先确定 OZON 类目和商品类型。类目树用于建立本地经营品类到 OZON 类目的映射。

接口：

```text
POST /v1/description-category/tree
```

常用请求字段：

```json
{
  "language": "DEFAULT"
}
```

如需查询指定类目的子树，可追加：

```json
{
  "language": "DEFAULT",
  "category_id": 123456
}
```

本系统需要保存：

- `description_category_id` / `category_id`
- 类目名称
- 父级类目
- 类目路径
- 是否叶子节点
- `type_id`
- `type_name`
- 原始响应快照
- 最近同步时间

### 3.4 获取类目属性

OZON 不同类目/商品类型的必填属性不同。创建商品前，必须先读取目标类目的属性清单，并区分必填、选填、字典值、单位、是否多选等信息。

接口：

```text
POST /v1/description-category/attribute
```

请求示例：

```json
{
  "description_category_id": 123456,
  "type_id": 78910,
  "language": "DEFAULT"
}
```

本系统需要保存：

- 属性 ID
- 属性名称
- 属性说明
- 是否必填
- 是否多值
- 是否字典属性
- 字典 ID
- 字段类型
- 单位
- 分组名称
- 最大值数量
- 原始响应快照

本系统当前封装：

- `OzonAPIClient.get_category_attributes(description_category_id, type_id)`

### 3.5 获取属性字典值

如果某个属性是字典属性，不应由 AI 或人工随意填写自由文本，而应从 OZON 字典中选择或匹配。

接口：

```text
POST /v1/description-category/attribute/values
```

请求示例：

```json
{
  "description_category_id": 123456,
  "type_id": 78910,
  "attribute_id": 85,
  "last_value_id": 0,
  "limit": 5000
}
```

落地要求：

- 字典值必须分页拉取完整。
- 本地保存 `value_id`、俄语值、中文翻译、原始值。
- 商品草稿提交前必须校验字典值是否匹配。
- 未匹配成功的字段进入人工确认队列。

## 4. 商品发布最小链路

### 4.1 生成本地刊登草稿

国内源商品不能直接发布到 OZON，必须先经过：

```text
源商品采集 -> 商品事实库 -> OZON 类目/属性适配 -> 刊登草稿 -> 人工审核 -> API 发布
```

草稿必须包含：

- 本地 `offer_id`
- 俄语标题
- 俄语描述
- 类目 ID / 商品类型 ID
- SKU 数据
- 价格
- 库存
- 图片 URL
- OZON 属性列表
- 字段来源证据
- 人工审核状态

### 4.2 创建或更新商品

接口：

```text
POST /v3/product/import
```

本系统当前封装：

- `OzonAPIClient.import_product(product_data)`

请求结构原则：

- 使用 `items` 数组提交商品。
- 每个商品必须有可追溯的 `offer_id`。
- 价格、税率、条码等字段按 OZON 要求转为字符串。
- 图片统一转为对象数组，例如 `{"file_name": "", "link": "https://..."}`。
- 只提交人工审核通过的字段。
- 缺少证据的属性不能由 AI 硬填。

### 4.3 查询导入任务

商品导入通常是异步任务。提交成功不等于商品最终发布成功，必须查询任务状态并回写本地发布任务。

接口：

```text
POST /v1/product/import/info
```

请求示例：

```json
{
  "task_id": 123456789
}
```

本系统当前封装：

- `OzonAPIClient.import_product_info(task_id)`

本地发布任务需要记录：

```json
{
  "job_id": "local-job-id",
  "account_id": "test-shop",
  "draft_id": "local-draft-id",
  "action": "create_product",
  "status": "pending|success|failed",
  "request_json": {},
  "response_json": {},
  "error_message": "",
  "created_at": "2026-06-20T00:00:00+08:00"
}
```

## 5. 商品读取与运营维护

### 5.1 商品列表

接口：

```text
POST /v3/product/list
```

用途：

- 店铺连通性测试
- 在线商品分页同步
- 本地商品缓存刷新

分页字段：

- `last_id`
- `limit`

本系统当前封装：

- `OzonAPIClient.list_products(last_id="", limit=100, filter_dict=None)`

### 5.2 商品详情

接口：

```text
POST /v3/product/info/list
```

用途：

- 根据 `offer_id`、`product_id` 或 `sku` 读取商品详情。
- 回写 OZON 商品 ID、状态、价格、库存等信息。

本系统当前封装：

- `OzonAPIClient.get_product_info(offer_ids=None, product_ids=None, skus=None)`

### 5.3 价格更新

当前项目封装接口：

```text
POST /v4/product/info/prices
```

本系统当前封装：

- `OzonAPIClient.update_product_prices(prices_list)`

请求示例：

```json
{
  "prices": [
    {
      "offer_id": "LOCAL-OFFER-001",
      "price": "100.00",
      "old_price": "120.00",
      "min_price": "90.00",
      "currency_code": "RUB"
    }
  ]
}
```

注意：项目中仍保留了旧的 `update_prices()` 占位方法，路径待实测确认，不应直接用于生产。

### 5.4 库存更新

当前项目封装接口：

```text
POST /v2/product/import/stocks
```

本系统当前封装：

- `OzonAPIClient.update_product_stocks(stocks_list)`

请求示例：

```json
{
  "stocks": [
    {
      "offer_id": "LOCAL-OFFER-001",
      "product_id": 12345,
      "stock": 100,
      "warehouse_id": 0
    }
  ]
}
```

注意：库存接口通常依赖 OZON 仓库 ID。正式联调前必须确认当前店铺仓库、FBO/FBS/跨境模式下的字段要求。

### 5.5 商品归档与取消归档

接口：

```text
POST /v1/product/archive
POST /v1/product/unarchive
```

本系统当前封装：

- `OzonAPIClient.archive_products(product_ids)`
- `OzonAPIClient.unarchive_products(product_ids)`

归档操作会影响线上商品状态，必须记录操作日志。

## 6. 图片接口

现有方案中规划的图片接口：

```text
POST /v1/product/pictures/import
POST /v2/product/pictures/info
```

当前状态：

- `OzonAPIClient.upload_image()` 仍为 `NotImplementedError`。
- 需等待官方文档和测试店实测确认具体请求结构。

图片进入 OZON 前的本地审核规则：

- 默认 3:4 竖版。
- 主体清晰居中。
- 白底或浅色干净背景。
- 不含中文、平台 Logo、二维码、价格、折扣、销量、联系方式。
- 俄语文字少而准确，不确定时不放文字。
- SKU 图必须与对应 SKU 绑定。

## 7. 错误处理规范

### 7.1 HTTP 错误分类

| 状态码 | 含义 | 系统处理 |
| --- | --- | --- |
| 400 | 请求参数或业务校验错误 | 解析字段错误，写入发布任务失败原因 |
| 401 | 认证失败 | 提示检查 `Client-Id` / `Api-Key` |
| 403 | 权限不足 | 检查店铺权限、接口权限、账户状态 |
| 429 | 请求频率限制 | 延迟重试，记录限流日志 |
| 5xx | OZON 服务端错误 | 可重试，保留响应体摘要 |
| Timeout | 请求超时 | 可重试，记录耗时 |
| ConnectionError | 网络连接失败 | 可重试，提示网络或域名访问问题 |

### 7.2 本系统异常类型

`services/ozon_api.py` 已定义：

- `OzonAPIError`
- `OzonAuthError`
- `OzonRateLimitError`
- `OzonServerError`
- `OzonValidationError`

当前默认策略：

- 请求超时：30 秒。
- 可重试状态：`429, 500, 502, 503, 504`。
- 最大重试次数：2 次。
- 重试间隔：按尝试次数递增。
- 响应体日志截断到约 2000 字符。

### 7.3 业务校验错误沉淀

OZON 返回的发布失败原因不能只展示给用户，还应沉淀到字段缺口知识库。

建议记录：

```json
{
  "endpoint": "POST /v3/product/import",
  "http_status": 400,
  "field": "brand",
  "attribute_id": "85",
  "message": "required attribute is missing",
  "local_action": "写入 OzonFieldGap，等待人工补齐或字典匹配",
  "created_at": "2026-06-20T00:00:00+08:00"
}
```

## 8. 联调验收清单

第一阶段只要求打通最小链路，不要求完全自动化。

必须完成：

1. 配置 OZON 测试店 API 凭证。
2. 使用 `POST /v3/product/list` 完成连通性测试。
3. 拉取类目树。
4. 选择 3C、无人机图传配件、摄影配件、车载工具的候选类目。
5. 拉取目标类目的属性和字典值。
6. 从采集 JSON 生成刊登草稿。
7. 人工补齐并审核草稿。
8. 提交 1 个单 SKU 商品导入任务。
9. 提交 1 个多 SKU 商品导入任务。
10. 查询导入任务结果。
11. 更新价格。
12. 更新库存。
13. 查询在线商品详情。
14. 将 OZON 商品 ID、`offer_id`、任务状态、失败原因回写本地。

## 9. 接口实测记录模板

每次实测都应记录以下内容：

```json
{
  "test_id": "ozon-api-001",
  "account_name": "OZON 测试店",
  "endpoint": "POST /v3/product/list",
  "request_headers_masked": {
    "Client-Id": "123***",
    "Api-Key": "***"
  },
  "request_body": {},
  "http_status": 200,
  "response_body_sample": {},
  "elapsed_ms": 1200,
  "result": "success|failed",
  "error_type": "",
  "next_action": "",
  "tested_at": "2026-06-20T00:00:00+08:00"
}
```

## 10. 开发注意事项

- 不要把接口路径写死在多个业务页面中，统一通过 `OzonAPIClient` 调用。
- 所有写操作先落本地发布任务，再调用 OZON API，再回写响应。
- `offer_id` 必须由本地生成并保持稳定，不能依赖标题或 SKU 名称临时拼接。
- 类目属性、字典值、发布错误都要保存原始响应快照，便于后续排查。
- 价格和库存更新应独立于商品全量导入，避免覆盖商品卡其他字段。
- AI 只能起草标题、描述、卖点和候选属性；必填属性必须有事实来源或人工确认。
- 线上商品状态同步应支持分页、限流退避和失败重试。
- 所有 OZON 数据必须按当前登录用户隔离。

## 11. 与现有文档关系

本文件是“官方接入基础知识库”，与以下文档互补：

- `docs/ozon_api_and_collection_contract.md`：API 实测清单与商品采集 JSON 标准。
- `docs/ozon_category_attribute_acquisition_plan.md`：类目属性字典与字段缺口检查方案。
- `docs/ozon_source_to_listing_adaptation_plan.md`：源商品到 OZON Listing 的适配层方案。
- `docs/ozon_phase4_database_backend.md`：数据库和后端路由设计。

后续若官方文档可稳定访问，应补充：

- 官方 Getting Started 页面逐项摘录摘要。
- 官方认证说明截图或链接锚点。
- 官方 OpenAPI 规格版本号。
- 已实测接口的请求/响应样例。
- 与当前 `OzonAPIClient` 不一致的接口变更记录。
