# OZON 源商品到 Listing 适配层实施方案

版本：v0.1
日期：2026-06-14
用途：给 Claude Code 或其他编码助手作为实施依据
关联文档：

- `docs/ozon_listing_prd.md`
- `docs/ozon_api_and_collection_contract.md`
- `docs/ozon_development_review_process.md`
- `docs/ozon_review_log.md`

## 1. 问题定义

当前目标用户是 OZON 中小卖家，常见经营模式包括：

- 无货源分销
- 少批量囤货
- 多平台找货源
- 快速测试商品
- 从 1688、淘宝、天猫、拼多多等平台采集商品资料

核心问题：

```text
国内源商品页面 ≠ OZON 可发布商品卡
```

源平台数据通常只能说明“货源是什么”，不能直接作为 OZON Listing。原因包括：

- 国内标题包含夸张营销词、促销词、平台语境。
- SKU 名称混乱，颜色、套餐、尺寸、数量、配件经常混在一起。
- 图片包含中文、水印、二维码、价格、销量、厂家招商信息。
- 规格参数缺失、不完整或不适合 OZON 类目字段。
- 国内类目与 OZON 类目不一一对应。
- 一个源商品可能需要拆成多个 OZON 商品。
- 多个源商品也可能组合成一个 OZON 套装商品。
- 采购价格不是 OZON 售价。
- AI 不能直接把国内页面翻译后发布，否则容易产生事实错误、合规风险和差评风险。

因此系统必须新增一个中间层：

```text
源商品采集层 -> 商品事实库 -> OZON 适配层 -> 人工审核 -> OZON 发布层
```

本方案重点设计“商品事实库”和“OZON 适配层”。

## 2. 产品原则

### 2.1 源数据只作为证据，不直接发布

1688、淘宝、天猫、拼多多采集的数据只能进入“源商品资料”和“商品事实库”，不得绕过适配层直接生成发布请求。

### 2.2 商品事实优先于 AI 文案

系统先确认：

- 商品是什么
- 有哪些 SKU
- 每个 SKU 的真实差异
- 包装内容是什么
- 有哪些可证实功能
- 有哪些尺寸、重量、材质、适配型号
- 哪些字段没有证据

再生成 OZON 标题、描述、属性和图片方案。

### 2.3 不确定字段留空或待确认

任何缺少来源证据的信息，状态必须是：

```text
unknown
pending_confirmation
manual_required
```

不能为了完整度硬填。

### 2.4 支持源商品与 OZON Listing 非一对一关系

系统必须支持：

- 一对一：一个源商品 -> 一个 OZON Listing
- 一对多：一个源商品 -> 多个 OZON Listing
- 多对一：多个源商品 -> 一个 OZON Listing

### 2.5 人工审核是发布前强制步骤

AI 可以建议，但不能自动发布。OZON 发布前必须经过人工审核。

## 3. 新增模块

建议新增模块名称：

```text
商品适配工作台
```

页面入口建议放在 OZON 模块下：

```text
OZON 运营 -> 商品适配
```

模块职责：

- 将一个或多个源商品转换为标准商品事实。
- 将标准商品事实适配成一个或多个 OZON Listing 草稿。
- 处理源商品与 OZON 商品不对应的问题。
- 显示字段证据、AI 建议、低置信度字段和人工确认状态。

## 4. 总体流程

```text
1. 采集源商品
2. 进入商品适配工作台
3. 选择适配关系：一对一 / 一对多 / 多对一
4. 生成或编辑商品事实
5. 拆解 SKU
6. 绑定证据
7. 推荐 OZON 类目
8. 映射 OZON 属性
9. 生成俄语 Listing 草稿
10. 生成图片方案
11. 计算建议售价
12. 发布前校验
13. 人工审核
14. 进入 OZON 发布流程
```

## 5. 信息架构调整

在现有 OZON 模块页面中新增或调整：

```text
OZON 工作台
平台接口
商品采集
采集列表
商品适配           <- 新增
商品事实库         <- 新增，可作为列表页或适配页内部 tab
图片方案
刊登草稿
发布任务
提示词库
定价规则
```

第一期最小调整：

- 新增 `商品适配` 页面。
- 在 `采集列表` 增加“进入适配”按钮。
- 在 `刊登草稿` 显示来源关系和事实字段。

## 6. 页面原型要求

根据审核流程，先创建独立 HTML 原型，不直接改真实模板。

建议新增原型文件：

```text
docs/prototypes/ozon_adaptation_workspace.v1.html
docs/prototypes/ozon_fact_library.v1.html
```

### 6.1 商品适配工作台布局

建议三栏布局：

```text
左栏：源商品资料
中栏：商品事实库
右栏：OZON Listing 草稿
```

#### 左栏：源商品资料

展示：

- 来源平台
- 源链接
- 源标题
- 源 SKU 列表
- 源图片
- 源规格参数
- 价格
- 供应商信息
- 页面截图/证据

操作：

- 选择参与适配的源商品
- 查看源图
- 查看源 SKU
- 标记无效 SKU
- 标记证据

#### 中栏：商品事实库

展示：

- 标准商品名
- 商品类型
- SKU 拆解
- 颜色
- 尺寸
- 款式
- 套装数量
- 包装内容
- 材质
- 功能
- 适用场景
- 适配型号
- 尺寸重量
- 待确认字段
- 证据引用

操作：

- 从源商品生成事实
- 手动编辑事实
- 锁定事实字段
- 标记低置信度
- 合并多个源商品
- 拆分为多个 OZON 商品

#### 右栏：OZON Listing 草稿

展示：

- 适配策略：一对一 / 一对多 / 多对一
- OZON 类目候选
- OZON 属性映射
- 俄语标题
- 俄语卖点
- 俄语描述
- SKU 发布结构
- 图片方案
- 建议售价
- 发布前校验结果

操作：

- 选择 OZON 类目
- 应用属性映射
- 生成俄语草稿
- 生成图片方案
- 生成价格建议
- 进入刊登草稿审核

## 7. 数据模型建议

以下为新增或调整的数据模型。Claude Code 实施时，应先输出数据库设计文档和迁移方案，经用户审核后再改代码。

### 7.1 SourceProductGroup

用途：表示一次适配任务，可包含一个或多个源商品。

字段建议：

```text
id
user_id
name
relation_type              one_to_one | one_to_many | many_to_one
status                     draft | adapting | reviewed | converted | archived
notes
created_at
updated_at
```

### 7.2 SourceProductGroupItem

用途：适配任务与源商品的关系。

字段建议：

```text
id
group_id
source_product_id
role                       primary | accessory | alternative | reference
sort_order
include_in_listing         boolean
notes
```

### 7.3 ProductFact

用途：标准化后的商品事实。

字段建议：

```text
id
user_id
group_id
standard_name_cn
standard_name_ru
product_type
category_hint_cn
brand_name
model
material
functions_json
package_contents_json
usage_scenarios_json
compatibility_json
dimensions_json
weight_json
facts_json
unknown_fields_json
locked_fields_json
confidence
review_status              pending | approved | needs_changes
created_at
updated_at
```

### 7.4 ProductFactSku

用途：标准化后的 SKU 事实。

字段建议：

```text
id
fact_id
source_sku_id
source_order
standard_sku_name_cn
standard_sku_name_ru
color_cn
color_ru
size_cn
size_ru
style_cn
style_ru
bundle_quantity
package_contents_json
purchase_price_cny
image_refs_json
evidence_refs_json
confidence
manual_status              pending | confirmed | rejected
created_at
updated_at
```

### 7.5 ProductFactEvidence

用途：事实字段与来源证据的关系。

字段建议：

```text
id
fact_id
fact_sku_id
field_path                 例如 material 或 skus[0].color_cn
evidence_type              text | image | screenshot | html | api
source_product_id
source_url
media_id
content
confidence
created_at
```

### 7.6 ListingAdaptation

用途：商品事实到 OZON Listing 的适配方案。

字段建议：

```text
id
user_id
fact_id
relation_type              one_to_one | one_to_many | many_to_one
target_listing_count
ozon_category_id
ozon_category_name
category_confidence
attribute_mapping_json
title_ru
bullets_ru_json
description_ru
image_plan_json
pricing_json
validation_json
status                     draft | needs_review | ready | converted
created_at
updated_at
```

### 7.7 ListingAdaptationItem

用途：当一个事实拆成多个 OZON Listing 时，记录每个目标 Listing。

字段建议：

```text
id
adaptation_id
target_index
target_name
included_sku_ids_json
ozon_category_id
title_ru
reason
status
```

## 8. 状态流转

### 8.1 适配任务状态

```text
draft -> adapting -> reviewed -> converted
                  \-> needs_changes
                  \-> archived
```

### 8.2 商品事实状态

```text
pending -> approved
        \-> needs_changes
        \-> partial_confirmed
```

### 8.3 Listing 适配状态

```text
draft -> needs_review -> ready -> converted_to_listing_draft
                    \-> blocked
```

## 9. AI 适配任务定义

AI 不直接发布，只输出建议和置信度。

### 9.1 AI 任务：识别商品事实

输入：

- 源标题
- 源 SKU
- 源属性
- 图片 OCR
- 详情图文字
- 价格和包装信息

输出：

```json
{
  "standard_name_cn": "",
  "product_type": "",
  "facts": {},
  "skus": [],
  "unknown_fields": [],
  "risk_notes": [],
  "confidence": 0.0
}
```

### 9.2 AI 任务：SKU 拆解

输入：

- 源 SKU 名称
- SKU 图片
- SKU 顺序

输出：

```json
{
  "skus": [
    {
      "source_order": 1,
      "source_sku_id": "",
      "color_cn": "",
      "size_cn": "",
      "style_cn": "",
      "bundle_quantity": 1,
      "package_contents_cn": [],
      "unknown_parts": [],
      "confidence": 0.0
    }
  ]
}
```

规则：

- 不改变源 SKU 顺序。
- 不把 SKU 改成 A 款/B 款。
- 不合并或拆分 SKU，除非用户在适配关系里明确选择。

### 9.3 AI 任务：推荐适配关系

输入：

- 源商品数量
- SKU 数量
- SKU 差异
- 商品类型
- 价格差异
- 图片差异

输出：

```json
{
  "recommended_relation_type": "one_to_one|one_to_many|many_to_one",
  "reason": "",
  "split_suggestions": [],
  "merge_suggestions": [],
  "confidence": 0.0
}
```

### 9.4 AI 任务：OZON 本地化文案

输入：

- 已确认商品事实
- OZON 类目
- OZON 属性字段
- 用户选择的目标受众

输出：

```json
{
  "title_ru": "",
  "bullets_ru": [],
  "description_ru": "",
  "blocked_claims": [],
  "needs_confirmation": []
}
```

规则：

- 不翻译国内夸张标题。
- 根据事实重写自然俄语。
- 不生成促销、销量、保修、授权、认证等无证据内容。

## 10. 适配策略规则

### 10.1 一对一

适用情况：

- 源商品结构简单。
- SKU 变体清晰。
- 所有 SKU 属于同一产品类型。
- 图片和参数能支撑一个 OZON 商品卡。

输出：

```text
1 个 ProductFact -> 1 个 OzonListingDraft
```

### 10.2 一对多

适用情况：

- 一个源链接包含多个不同产品。
- SKU 之间不是同一商品变体，而是不同配件或套装。
- OZON 类目可能不同。
- 价格、用途、图片差异过大。

例子：

- 一个链接同时卖支架、线材、镜头盖、清洁套装。

输出：

```text
1 个 SourceProduct -> 多个 ProductFact 或多个 OzonListingDraft
```

### 10.3 多对一

适用情况：

- 多个源链接共同组成一个套装。
- 主商品和配件来自不同供应商。
- 用户希望在 OZON 发布组合商品。

例子：

- 摄影灯 + 支架 + 收纳包组合成套装。

输出：

```text
多个 SourceProduct -> 1 个 ProductFact -> 1 个 OzonListingDraft
```

## 11. 发布前校验新增项

在原有发布校验基础上，新增适配层校验：

阻断项：

- 未选择适配关系。
- 未生成商品事实。
- 商品事实未人工确认。
- SKU 拆解未确认。
- 源商品与目标 Listing 关系不明确。
- 一对多拆分后仍有 SKU 未归属。
- 多对一组合后包装清单未确认。
- OZON 草稿字段没有对应事实来源。

警告项：

- AI 推荐适配关系置信度低。
- 部分属性缺少证据。
- 部分图片需要重新处理。
- 售价使用估算物流成本。

## 12. 对现有文档的更新要求

Claude Code 实施前，先更新这些文档：

1. `docs/ozon_listing_prd.md`
   - 增加“商品适配工作台”
   - 增加“商品事实库”
   - 增加“一对一 / 一对多 / 多对一”

2. `docs/ozon_api_and_collection_contract.md`
   - 在采集 JSON 中增加 `adaptation` 或 `fact_extraction` 相关结构。

3. `docs/ozon_development_review_process.md`
   - 增加商品适配原型的审核点。

4. `docs/ozon_review_log.md`
   - 记录本方案为新增需求变更。

## 13. Claude Code 实施任务拆分

下面是建议直接交给 Claude Code 的任务顺序。

### 任务 A：文档同步

目标：

- 将本方案同步到 PRD 和接口契约文档。

交付：

- 更新后的 PRD
- 更新后的采集 JSON 标准
- 更新后的审核日志

验收：

- 文档中明确商品适配层。
- 文档中明确源商品和 OZON Listing 非一对一关系。

### 任务 B：信息架构调整

目标：

- 更新 OZON 模块页面清单和跳转关系。

交付：

- `docs/ozon_phase1_architecture.md` 或新增版本
- 页面清单中包含“商品适配”

验收：

- 从采集列表可以进入商品适配。
- 从商品适配可以进入刊登草稿。

### 任务 C：低保真原型

目标：

- 创建商品适配工作台原型。

交付：

```text
docs/prototypes/ozon_adaptation_workspace.v1.html
docs/prototypes/ozon_fact_library.v1.html
```

验收：

- 三栏布局完整。
- 能看到源商品、商品事实、OZON 草稿。
- 能选择一对一 / 一对多 / 多对一。
- 能标记待确认字段。

### 任务 D：数据模型设计

目标：

- 输出适配层数据表设计，不立刻改代码。

交付：

```text
docs/ozon_adaptation_data_model.md
```

验收：

- 包含 SourceProductGroup、ProductFact、ProductFactSku、ProductFactEvidence、ListingAdaptation。
- 包含字段类型、索引、用户隔离、状态流转。

### 任务 E：后端实现

目标：

- 在用户审核通过数据模型后，实现适配层后端。

交付：

- 新增或更新 models
- 新增迁移脚本
- 新增适配相关 blueprint 路由

验收：

- 可创建适配任务。
- 可绑定源商品。
- 可保存商品事实。
- 可生成 Listing 适配草稿。

### 任务 F：页面实现

目标：

- 将 approved 原型转为真实页面。

交付：

- 商品适配工作台页面
- 商品事实编辑页面或 tab
- 与采集列表、刊登草稿页面打通

验收：

- 页面与 approved 原型一致。
- 支持手动编辑事实字段。
- 支持保存适配关系。

### 任务 G：AI 适配建议

目标：

- 接入 AI 任务，生成商品事实和适配建议。

交付：

- AI 识别商品事实
- AI 拆解 SKU
- AI 推荐适配关系
- AI 生成 OZON 本地化文案

验收：

- AI 输出不覆盖人工确认字段。
- AI 输出包含置信度和待确认字段。

## 14. 第一版最小可交付范围

如果要控制开发成本，第一版只做：

1. 采集列表进入商品适配。
2. 商品适配页面支持一对一。
3. 支持手动编辑商品事实。
4. 支持 SKU 拆解确认。
5. 支持从商品事实生成 OZON 草稿。
6. 发布前校验要求商品事实已确认。

延后：

- 一对多自动拆分
- 多对一组合
- AI 自动推荐适配关系
- 自动类目匹配
- 复杂证据图谱

## 15. 验收清单

### 15.1 产品验收

- 国内源商品不会直接进入 OZON 发布。
- 所有源数据先进入商品事实库。
- OZON Listing 字段能追溯到事实字段。
- SKU 顺序不被改变。
- 不确定字段被标记为待确认。
- 支持至少一对一适配。
- 系统结构预留一对多和多对一。

### 15.2 页面验收

- 采集列表有“进入适配”入口。
- 商品适配工作台能同时查看源商品、商品事实、OZON 草稿。
- 低置信度字段醒目。
- 人工确认按钮明确。
- 未确认事实不能进入发布。

### 15.3 技术验收

- 所有新增数据按 `user_id` 隔离。
- 源数据、事实数据、OZON 草稿分表或分层保存。
- AI 输出保存为草稿，不覆盖人工确认字段。
- 发布前校验能识别未适配商品。

## 16. 给 Claude Code 的执行提示

执行时必须遵守：

1. 不要直接写业务代码，先更新文档和原型。
2. 页面原型放在 `docs/prototypes/`。
3. 数据库变更必须先写设计文档，用户确认后再实施。
4. 真实发布 API 写操作必须等测试店确认后再接。
5. 不要删除或覆盖现有库存、订单、财务功能。
6. 所有新增查询必须按当前用户隔离。
7. 每个阶段完成后更新 `docs/ozon_review_log.md`。

建议 Claude Code 第一条任务：

```text
请根据 docs/ozon_source_to_listing_adaptation_plan.md，
先更新 PRD、接口契约和审核日志，
然后创建商品适配工作台的低保真 HTML 原型，
不要修改业务代码。
```
