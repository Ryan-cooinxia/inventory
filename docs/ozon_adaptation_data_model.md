# OZON 适配层、类目属性、视觉模型 — 数据模型设计文档

版本：v0.1
日期：2026-06-14
用途：为新增的适配层、类目属性字典、视觉工具模型提供完整的数据表设计
依赖文档：
- `docs/ozon_source_to_listing_adaptation_plan.md`
- `docs/ozon_category_attribute_acquisition_plan.md`
- `docs/ozon_vision_tool_model_plan.md`
- `docs/ozon_phase4_database_backend.md`（现有 10 张表设计）
- `models.py`（现有实现）

---

## 1. 设计原则

- **全部新表**，不修改现有的 10 张 OZON 表（OzonAccount / OzonSource / OzonSourceSku / OzonSourceMedia / OzonDraft / OzonDraftSku / OzonImageSlot / OzonPublishJob / OzonPrompt / OzonPricingRule）
- **所有用户数据隔离**：`user = ForeignKeyField(User, ...)`
- **遵循现有 Peewee 模式**：继承 `BaseModel`、WAL、外键、索引
- **与现有表关联**：新表通过 ForeignKey 引用 OzonSource / OzonSourceSku / OzonDraft / OzonSourceMedia
- **AI 产物与人工确认分离**：AI 输出永远是可覆盖的草稿，人工确认字段独立存储
- **状态流转可追溯**，关键操作记录时间戳

---

## 2. 新增表概览（13 张）

| # | 表名 | 来源 | 用途 |
|---|------|------|------|
| 1 | SourceProductGroup | 适配层方案 | 适配任务组，关联一个或多个源商品 |
| 2 | SourceProductGroupItem | 适配层方案 | 适配任务与源商品的 N:N 关系 |
| 3 | ProductFact | 适配层方案 | 标准化后的商品事实 |
| 4 | ProductFactSku | 适配层方案 | 标准化后的 SKU 事实 |
| 5 | ProductFactEvidence | 适配层方案 | 事实字段与来源证据的关联 |
| 6 | ListingAdaptation | 适配层方案 | 商品事实到 OZON Listing 的适配方案 |
| 7 | OzonCategory | 类目属性方案 | OZON 类目信息缓存 |
| 8 | OzonCategoryAttribute | 类目属性方案 | 某类目的属性要求 |
| 9 | OzonAttributeMapping | 类目属性方案 | OZON 属性与本地字段的映射 |
| 10 | OzonFieldGap | 类目属性方案 | 单个草稿的字段缺口记录 |
| 11 | VisionModelConfig | 视觉模型方案 | 视觉工具模型配置 |
| 12 | ImageAnalysisJob | 视觉模型方案 | 图片识别任务 |
| 13 | ImageFact | 视觉模型方案 | 视觉识别结果沉淀为事实证据 |

---

## 3. 详细字段设计

### 3.1 SourceProductGroup — 适配任务组

用途：包装一次适配操作，可将一个或多个源商品绑定为一个适配任务。

关联关系：
- 一个 SourceProductGroup 可有多个 SourceProductGroupItem
- 一个 SourceProductGroup 生成一个 ProductFact（一对一适配）或多个 ProductFact（一对多）
- 多个 SourceProductGroup 可合并为一个 ProductFact（多对一）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| name | CharField(200) | 任务名称，默认取第一个源商品标题 |
| relation_type | CharField(20) | one_to_one / one_to_many / many_to_one |
| status | CharField(20) | draft / adapting / reviewed / converted / archived |
| notes | TextField(null=True) | 备注 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

索引：`(user, status)`

---

### 3.2 SourceProductGroupItem — 适配任务关联项

用途：适配任务与源商品的 N:N 关系。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| group | FK(SourceProductGroup) | 所属适配任务 |
| source | FK(OzonSource) | 关联的源商品 |
| role | CharField(20) | primary / accessory / alternative / reference |
| sort_order | IntegerField(default=0) | 排序 |
| include_in_listing | BooleanField(default=True) | 是否纳入 Listing |
| notes | TextField(null=True) | 备注 |

索引：`(group, source)` 唯一

---

### 3.3 ProductFact — 商品事实

用途：标准化后的商品事实，是源商品与 OZON Listing 之间的中间层。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| group | FK(SourceProductGroup, null=True) | 关联的适配任务（可选） |
| standard_name_cn | CharField(300) | 标准商品名（中） |
| standard_name_ru | CharField(300, null=True) | 标准商品名（俄，AI生成后填充） |
| product_type | CharField(100, null=True) | 商品类型（如：无线麦克风） |
| category_hint_cn | CharField(200, null=True) | 本地品类提示 |
| brand_name | CharField(100, null=True) | 品牌名 |
| model | CharField(100, null=True) | 型号 |
| material | CharField(100, null=True) | 材质 |
| origin | CharField(50, null=True) | 产地 |
| warranty | CharField(100, null=True) | 保修 |
| functions_json | TextField(null=True) | 功能列表 JSON 数组 |
| package_contents_json | TextField(null=True) | 包装内容 JSON 数组 |
| usage_scenarios_json | TextField(null=True) | 使用场景 JSON 数组 |
| compatibility_json | TextField(null=True) | 适配型号 JSON |
| dimensions_json | TextField(null=True) | 尺寸 JSON（含单位） |
| weight_json | TextField(null=True) | 重量 JSON（含单位） |
| certifications_json | TextField(null=True) | 认证 JSON 数组 |
| battery_capacity | CharField(50, null=True) | 电池容量（电子品类） |
| power | CharField(50, null=True) | 功率（车载工具等） |
| wireless_range | CharField(50, null=True) | 无线范围（图传/音频品类） |
| facts_json | TextField(null=True) | 扩展事实 JSON（未归类的字段） |
| unknown_fields_json | TextField(null=True) | 未知字段 JSON（字段名→原因） |
| locked_fields_json | TextField(null=True) | 锁定字段 JSON（字段名列表） |
| confidence | FloatField(null=True) | 整体置信度 0-1 |
| review_status | CharField(20) | pending / approved / needs_changes / partial_confirmed |
| reviewer_notes | TextField(null=True) | 审核备注 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

索引：`(user, review_status)`、`(user, product_type)`

---

### 3.4 ProductFactSku — 商品事实 SKU

用途：标准化后的 SKU 事实，每条对应一个源 SKU。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| fact | FK(ProductFact) | 所属商品事实 |
| source_sku | FK(OzonSourceSku, null=True) | 关联源 SKU |
| source_order | IntegerField() | SKU 顺序号（与源一致） |
| standard_sku_name_cn | CharField(200, null=True) | 标准 SKU 名（中） |
| standard_sku_name_ru | CharField(200, null=True) | 标准 SKU 名（俄） |
| color_cn | CharField(50, null=True) | 颜色（中） |
| color_ru | CharField(100, null=True) | 颜色（俄） |
| size_cn | CharField(50, null=True) | 尺寸（中） |
| size_ru | CharField(100, null=True) | 尺寸（俄） |
| style_cn | CharField(100, null=True) | 款式（中） |
| style_ru | CharField(100, null=True) | 款式（俄） |
| bundle_quantity | IntegerField(default=1) | 套装数量 |
| package_contents_json | TextField(null=True) | 包装内容 JSON 数组 |
| purchase_price_cny | FloatField(null=True) | 采购价 ¥ |
| image_refs_json | TextField(null=True) | 关联图片 ID JSON |
| evidence_refs_json | TextField(null=True) | 证据引用 JSON |
| confidence | FloatField(null=True) | 置信度 0-1 |
| manual_status | CharField(20) | pending / confirmed / rejected |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

索引：`(fact, source_order)` 唯一

---

### 3.5 ProductFactEvidence — 事实证据

用途：事实字段与来源证据的关联，支撑"这个字段是哪来的"溯源。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| fact | FK(ProductFact, null=True) | 关联商品事实 |
| fact_sku | FK(ProductFactSku, null=True) | 关联 SKU 事实（可选） |
| field_path | CharField(200) | 字段路径，如 material 或 skus[0].color_cn |
| evidence_type | CharField(30) | text / image / screenshot / html / api / ocr / ai |
| source | FK(OzonSource, null=True) | 来源商品 |
| media | FK(OzonSourceMedia, null=True) | 来源图片 |
| source_url | CharField(500, null=True) | 证据 URL |
| content | TextField(null=True) | 证据内容（截取） |
| confidence | FloatField(null=True) | 证据置信度 |
| created_at | DateTimeField | 创建时间 |

索引：`(user, fact)`、`(fact, field_path)`

---

### 3.6 ListingAdaptation — Listing 适配方案

用途：商品事实到 OZON Listing 的适配方案记录。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| fact | FK(ProductFact) | 关联商品事实 |
| relation_type | CharField(20) | one_to_one / one_to_many / many_to_one |
| target_listing_count | IntegerField(default=1) | 目标 Listing 数量 |
| ozon_category_id | CharField(50, null=True) | 选定的 OZON 类目 ID |
| ozon_category_name | CharField(300, null=True) | OZON 类目名 |
| category_confidence | FloatField(null=True) | 类目推荐置信度 |
| attribute_mapping_json | TextField(null=True) | 属性映射 JSON |
| title_ru | CharField(300, null=True) | AI 生成的俄语标题 |
| bullets_ru_json | TextField(null=True) | 俄语卖点 JSON |
| description_ru | TextField(null=True) | 俄语描述 |
| image_plan_json | TextField(null=True) | 图片方案 JSON |
| pricing_json | TextField(null=True) | 定价 JSON |
| validation_json | TextField(null=True) | 校验结果 JSON |
| status | CharField(20) | draft / needs_review / ready / converted |
| draft | FK(OzonDraft, null=True) | 转换后的草稿（反向引用） |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

索引：`(user, status)`、`(fact, relation_type)`

---

### 3.7 OzonCategory — OZON 类目信息

用途：缓存从 OZON API 拉取的类目树数据。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| ozon_category_id | CharField(50) | OZON 类目 ID |
| name | CharField(200) | 类目名（俄） |
| name_cn | CharField(200, null=True) | 类目名（中，翻译或人工标注） |
| path | CharField(500, null=True) | 类目路径，如 Электроника > Аудио > Микрофоны |
| parent_id | CharField(50, null=True) | 父级类目 ID |
| is_leaf | BooleanField(default=True) | 是否叶子节点 |
| source | CharField(20) | api / manual |
| raw_json | TextField(null=True) | API 原始响应 JSON |
| last_synced_at | DateTimeField(null=True) | 最近同步时间 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

索引：`(user, ozon_category_id)` 唯一

---

### 3.8 OzonCategoryAttribute — 类目属性

用途：某 OZON 类目的属性要求（必填/选填/类型/字典值）。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| ozon_category_id | CharField(50) | 所属 OZON 类目 ID |
| attribute_id | CharField(50) | OZON 属性 ID |
| name | CharField(200) | 属性名（俄） |
| name_cn | CharField(200, null=True) | 属性名（中） |
| description | TextField(null=True) | 属性说明 |
| is_required | BooleanField(default=False) | 是否必填 |
| is_collection | BooleanField(default=False) | 是否多选 |
| is_dictionary | BooleanField(default=False) | 是否使用字典值 |
| data_type | CharField(30) | string / number / boolean / enum / text |
| unit | CharField(30, null=True) | 单位（mm / g / ...） |
| allowed_values_json | TextField(null=True) | 属性值字典 JSON |
| raw_json | TextField(null=True) | API 原始响应 JSON |
| last_synced_at | DateTimeField(null=True) | 最近同步时间 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

索引：`(user, ozon_category_id, attribute_id)` 唯一

---

### 3.9 OzonAttributeMapping — 属性映射规则

用途：将 OZON 属性映射到本地商品事实字段。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| ozon_category_id | CharField(50) | 类目 ID |
| attribute_id | CharField(50) | 属性 ID |
| local_field_path | CharField(200) | 本地字段路径，如 brand_name / skus[0].color_cn |
| fill_policy | CharField(30) | 填充策略（见下） |
| manual_required | BooleanField(default=False) | 是否需要人工确认 |
| default_value | CharField(200, null=True) | 默认值 |
| confidence | FloatField(null=True) | 映射置信度 |
| notes | TextField(null=True) | 备注 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

fill_policy 枚举：
- `source_required` — 必须来自源数据，缺失则阻断
- `source_or_empty` — 有来源则填，无来源留空
- `manual_required` — 必须人工填写
- `dictionary_match` — 必须匹配 OZON 字典值
- `computed` — 系统计算
- `not_supported` — 暂不支持

索引：`(user, ozon_category_id, attribute_id)` 唯一

---

### 3.10 OzonFieldGap — 字段缺口

用途：记录某个草稿在发布前缺少哪些类目必填字段。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| draft | FK(OzonDraft, null=True) | 关联草稿 |
| adaptation | FK(ListingAdaptation, null=True) | 关联适配方案（草稿生成前使用） |
| ozon_category_id | CharField(50) | 类目 ID |
| attribute_id | CharField(50) | 属性 ID |
| field_name | CharField(200) | 字段名 |
| gap_type | CharField(30) | 缺口类型（见下） |
| severity | CharField(10) | error / warning / info |
| source_status | CharField(30, null=True) | 本地字段状态（null / unknown / low_confidence / has_value） |
| suggested_action | TextField(null=True) | 建议操作 |
| resolved | BooleanField(default=False) | 是否已解决 |
| resolved_at | DateTimeField(null=True) | 解决时间 |
| created_at | DateTimeField | 创建时间 |

gap_type 枚举：
- `missing_required` — 必填字段缺失
- `missing_dictionary_value` — 缺少可匹配字典值
- `low_confidence` — 置信度低，不足以自动填入
- `needs_manual_confirmation` — 需要人工确认
- `format_error` — 格式错误
- `unit_missing` — 缺少单位

索引：`(user, draft)`、`(draft, resolved)`

---

### 3.11 VisionModelConfig — 视觉模型配置

用途：配置视觉工具模型，与主模型配置分离。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| provider | CharField(20) | openai_vision / qwen_vl / gemini_vision / custom_http |
| model_name | CharField(100) | 模型名，如 qwen-vl-max |
| api_base | CharField(300) | API Base URL |
| api_key_encrypted | CharField(500, null=True) | 加密后的 API Key |
| enabled | BooleanField(default=False) | 是否启用 |
| timeout_seconds | IntegerField(default=60) | 请求超时 |
| max_images_per_batch | IntegerField(default=5) | 每批最大图片数 |
| notes | TextField(null=True) | 备注 |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

索引：`(user, provider)` 唯一（每个用户每个 provider 一套配置）

---

### 3.12 ImageAnalysisJob — 图片识别任务

用途：记录每次图片调用的输入、输出和状态。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| media | FK(OzonSourceMedia) | 关联的源图片 |
| source | FK(OzonSource, null=True) | 关联的源商品（可选） |
| draft | FK(OzonDraft, null=True) | 关联的草稿（可选） |
| task_type | CharField(30) | sku_image / detail_ocr / compliance_check / fact_extraction |
| provider | CharField(20) | 使用的 provider |
| model_name | CharField(100) | 使用的模型名 |
| status | CharField(20) | pending / running / success / failed |
| request_json | TextField(null=True) | API 请求 JSON（不含 API Key） |
| response_json | TextField(null=True) | API 原始响应 JSON |
| parsed_json | TextField(null=True) | 归一化后的视觉识别结果 JSON |
| error_message | TextField(null=True) | 失败原因 |
| processing_time_ms | IntegerField(null=True) | 处理耗时 ms |
| created_at | DateTimeField | 创建时间 |
| updated_at | DateTimeField | 更新时间 |

索引：`(user, status)`、`(media, task_type)`

---

### 3.13 ImageFact — 图片识别事实

用途：将视觉模型识别结果沉淀为可人工确认的事实证据。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Auto | 主键 |
| user | FK(User) | 数据隔离 |
| image_analysis_job | FK(ImageAnalysisJob) | 所属识别任务 |
| media | FK(OzonSourceMedia) | 关联图片 |
| field_path | CharField(200) | 字段路径，如 material / skus[0].color_cn |
| value | TextField() | 识别值 |
| evidence_text | TextField(null=True) | 证据文本（OCR 原文或主体描述） |
| confidence | FloatField() | 置信度 0-1 |
| requires_manual_confirmation | BooleanField(default=False) | 是否需要人工确认 |
| accepted | BooleanField(default=False) | 是否被人工接受 |
| accepted_at | DateTimeField(null=True) | 接受时间 |
| created_at | DateTimeField | 创建时间 |

索引：`(user, image_analysis_job)`、`(media, field_path)`

---

## 4. ER 关系概览

```text
现有表                         新增表（适配层）              新增表（类目属性）        新增表（视觉模型）
───────                        ────────────                ──────────────          ────────────
OzonSource ──────────→ SourceProductGroupItem ──→ SourceProductGroup
                                │                              OzonCategory
                                ├─→ ProductFact ◄── ListingAdaptation ──→ OzonDraft
                                │       │                              └──→ OzonFieldGap
                                │       ├── ProductFactSku ◄── OzonSourceSku    ↑
                                │       └── ProductFactEvidence ◄── OzonSource   │
                                │              ↑                     OzonCategoryAttribute ──→ OzonAttributeMapping
                                │              │
OzonSourceMedia ◄─────── ImageAnalysisJob ──→ ImageFact ──→ ProductFactEvidence
                                │
VisionModelConfig (独立配置)
```

简化关系：
1. **适配层**：OzonSource → SourceProductGroup → ProductFact → ListingAdaptation → OzonDraft
2. **类目属性**：OzonCategory → OzonCategoryAttribute → OzonAttributeMapping → OzonFieldGap → OzonDraft
3. **视觉模型**：VisionModelConfig → ImageAnalysisJob → ImageFact → ProductFactEvidence

---

## 5. 状态流转

### 5.1 适配任务 (SourceProductGroup)

```
draft → adapting → reviewed → converted → archived
              ↘ needs_changes ↗
```

### 5.2 商品事实 (ProductFact)

```
pending → approved → (被 ListingAdaptation 引用)
        ↘ needs_changes → pending (重新确认)
        ↘ partial_confirmed
```

### 5.3 SKU 事实 (ProductFactSku.manual_status)

```
pending → confirmed
        ↘ rejected (标记为无效 SKU)
```

### 5.4 Listing 适配 (ListingAdaptation)

```
draft → needs_review → ready → converted (生成 OzonDraft)
                    ↘ blocked (存在阻断缺口)
```

### 5.5 图片识别 (ImageAnalysisJob)

```
pending → running → success → (ImageFact 生成)
                 ↘ failed → (重试或标记)
```

### 5.6 字段缺口 (OzonFieldGap)

```
(创建, resolved=false) → resolved=true (人工解决或字段已补充)
```

---

## 6. 迁移策略

### 6.1 迁移文件

建议分 3 次迁移，每次独立验证：

1. **migrate_adaptation_layer.py** — 适配层 6 张表（3.1-3.6）
2. **migrate_category_attributes.py** — 类目属性 4 张表（3.7-3.10）
3. **migrate_vision_models.py** — 视觉模型 3 张表（3.11-3.13）

### 6.2 迁移原则

- 使用 `database.create_tables([...], safe=True)` 创建表
- 不清空已有数据
- 新表不影响现有功能
- 每张表都有 `user` 外键，确保数据隔离
- 所有查询遵循 `user == current_user` 模式

### 6.3 回滚方式

- 第一阶段（原型审核通过前）：直接删除 SQLite 文件中的对应表
- 第二阶段（已上线）：使用 `migrate_*.py drop` 命令删除表

---

## 7. 与现有 OzonDraft 的关系

ListingAdaptation 和 OzonDraft 是互补关系：

- **ListingAdaptation** 负责"从事实到 OZON Listing"的适配决策：
  - 选择适配关系
  - 选择 OZON 类目
  - 执行属性映射
  - 检查字段缺口
- **OzonDraft** 负责"OZON 可直接发布的商品卡"数据：
  - 俄语标题、描述、卖点
  - 已映射的属性 JSON
  - SKU 发布结构
  - 价格（人工确认）
  - 图片方案

ListingAdaptation.converted 时自动生成或更新 OzonDraft 记录。

---

## 8. 设计决策记录

1. **ProductFact + ProductFactSku vs 直接使用 OzonSource + OzonSourceSku**
   - 决策：新增 ProductFact/ProductFactSku
   - 原因：源数据会变（重新采集），事实应该稳定；一个事实可来自多个源商品（多对一）；事实字段比源字段更规范化

2. **OzonFieldGap 按 draft 存储 vs 实时计算**
   - 决策：按 draft 存储
   - 原因：发布前检查结果是某个时间点的快照，需要可追溯；实时计算涉及类目字典版本变化

3. **VisionModelConfig 独立于主模型配置**
   - 决策：独立存储
   - 原因：视觉模型和主模型是独立的能力层，可分别启用、切换 provider

4. **ImageFact 需要人工 acceptance**
   - 决策：每条 ImageFact 都可以单独接受或拒绝
   - 原因：视觉识别可能误识别，低置信度结果不应自动成为事实

---

## 9. 待用户确认

1. 13 张新表的字段是否完整，有无遗漏
2. 状态流转是否合理
3. 与现有 OzonDraft 的关系设计是否可以接受
4. 迁移分为 3 次独立执行是否合适
5. ProductFactEvidence 到 ImageFact 的关联路径是否清晰
6. fill_policy / gap_type 等枚举值是否覆盖所有场景
7. 确认后进入代码实现阶段（先写迁移，再写路由，最后做页面）
