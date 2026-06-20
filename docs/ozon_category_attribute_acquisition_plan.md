# OZON 类目属性获取与字段缺口检查方案

版本：v0.1
日期：2026-06-14
用途：解决“不同 OZON 产品/品类需要哪些不同信息”的问题
关联文档：

- `docs/ozon_listing_prd.md`
- `docs/ozon_api_and_collection_contract.md`
- `docs/ozon_source_to_listing_adaptation_plan.md`

## 1. 问题

OZON 不同类目的商品卡字段要求不同。国内源商品页面采集到的信息，未必覆盖 OZON 类目必填字段。

如果系统不知道某个 OZON 类目需要哪些字段，就会导致：

- 商品上传失败。
- 商品卡字段缺失。
- 类目选择错误。
- 属性值格式错误。
- AI 为了补齐字段而编造信息。
- 发布准确率下降。

因此，系统必须建立一套“类目属性字典 + 字段缺口检查”机制。

## 2. 字段来源优先级

### 2.1 第一优先级：OZON Seller API

这是最重要、最稳定、最适合系统自动化的来源。

需要实测的接口类型：

```text
1. 类目树接口
2. 类目属性接口
3. 属性值字典接口
4. 商品创建/导入接口
5. 商品导入任务状态接口
```

常见接口命名可能包括：

```text
POST /v1/description-category/tree
POST /v1/description-category/attribute
POST /v1/description-category/attribute/values
POST /v3/product/import
POST /v1/product/import/info
```

注意：

- OZON API 版本可能变化，最终以测试店实测结果为准。
- 不要把接口路径写死为不可修改配置。
- 每次拉取类目属性时保存接口响应快照。

系统应该从 API 获取：

- 类目 ID
- 类目路径
- 属性 ID
- 属性名称
- 是否必填
- 是否多选
- 字段类型
- 是否使用字典值
- 属性值字典
- 单位要求
- 允许值范围
- 属性说明

### 2.2 第二优先级：OZON 卖家后台商品创建页

卖家后台创建商品时，选择类目后会展示该类目需要填写的字段。

用途：

- 人工核对 API 返回字段是否完整。
- 观察字段在后台中的实际展示名称。
- 查看哪些字段会影响发布。
- 查看字段填写示例。

使用方式：

- 运营人员在测试店后台选择目标类目。
- 截图保存字段列表。
- 与 API 返回字段对照。
- 将差异记录到类目属性字典。

### 2.3 第三优先级：OZON 类目导入模板

部分平台会提供按类目下载的商品导入 Excel 模板。

用途：

- 获取类目字段列名。
- 获取必填/选填提示。
- 获取枚举字段示例。
- 辅助校验 API 字段。

使用方式：

- 在卖家后台下载目标类目的导入模板。
- 保存模板文件。
- 解析表头和说明。
- 与 API 字段做映射。

### 2.4 第四优先级：OZON 已在线竞品商品卡

竞品商品页可以辅助判断：

- 同类商品常见标题结构。
- 常见图片表达方式。
- 常见卖点。
- 俄语表达习惯。
- 哪些属性影响前台展示。

但竞品页不能作为事实来源。

禁止：

- 复制竞品品牌、认证、保修、适配型号。
- 把竞品参数当作自己商品参数。
- 把竞品图片当作源图。

### 2.5 第五优先级：发布失败错误

OZON API 发布失败时，错误返回经常会提示：

- 缺少哪个属性。
- 属性值格式错误。
- 类目不支持某字段。
- 图片不符合要求。
- 价格或库存错误。

这些错误应该进入“字段缺口知识库”。

## 3. 系统新增能力：类目属性字典

建议新增模块：

```text
OZON 运营 -> 类目属性字典
```

或者作为“平台接口/商品适配”的子页面。

### 3.1 页面功能

展示：

- 本地品类
- OZON 类目 ID
- OZON 类目路径
- 属性数量
- 必填属性数量
- 已映射字段数量
- 缺口字段数量
- 最近同步时间

操作：

- 拉取类目树
- 拉取类目属性
- 拉取属性值字典
- 查看必填字段
- 手动映射本地字段
- 标记字段来源
- 标记字段是否需要人工确认

## 4. 数据模型建议

### 4.1 OzonCategory

用途：保存 OZON 类目信息。

字段：

```text
id
user_id
ozon_category_id
name
path
parent_id
is_leaf
source
raw_json
last_synced_at
created_at
updated_at
```

### 4.2 OzonCategoryAttribute

用途：保存某类目的属性要求。

字段：

```text
id
user_id
ozon_category_id
attribute_id
name
name_ru
description
is_required
is_collection
is_dictionary
data_type
unit
allowed_values_json
raw_json
last_synced_at
created_at
updated_at
```

### 4.3 OzonAttributeMapping

用途：保存 OZON 属性与本地商品事实字段的映射。

字段：

```text
id
user_id
ozon_category_id
attribute_id
local_field_path
fill_policy
manual_required
default_value
confidence
notes
created_at
updated_at
```

`fill_policy` 建议值：

```text
source_required       必须来自源数据
source_or_empty       有来源则填，无来源留空
manual_required       必须人工填写
dictionary_match      必须匹配 OZON 字典值
computed              系统计算
not_supported         暂不支持
```

### 4.4 OzonFieldGap

用途：记录某个商品草稿缺少哪些类目字段。

字段：

```text
id
user_id
draft_id
ozon_category_id
attribute_id
field_name
gap_type
severity
source_status
suggested_action
resolved
created_at
updated_at
```

`gap_type` 建议值：

```text
missing_required
missing_dictionary_value
low_confidence
needs_manual_confirmation
format_error
unit_missing
```

## 5. 字段缺口检查流程

当用户选择 OZON 类目后，系统执行：

```text
1. 读取该类目的属性字典
2. 找出必填属性
3. 根据 OzonAttributeMapping 查找本地事实字段
4. 检查商品事实库中是否有对应值
5. 检查值是否有证据
6. 检查值是否符合 OZON 字典
7. 检查单位和格式
8. 生成缺口清单
9. 阻断或警告发布
```

输出示例：

```json
{
  "category_id": "123456",
  "required_total": 12,
  "resolved": 8,
  "blocking_gaps": [
    {
      "attribute_id": "85",
      "name": "品牌",
      "reason": "缺少品牌字段，且该类目必填",
      "suggested_action": "人工选择无品牌/其他品牌规则，或补充品牌证据"
    }
  ],
  "warnings": [
    {
      "attribute_id": "10096",
      "name": "材质",
      "reason": "源数据中未明确材质，不能自动填写"
    }
  ]
}
```

## 6. 获取类目字段的实际工作流

### 6.1 首次录入一个新品类

1. 用户选择本地品类，例如“车载工具”。
2. 系统调用 OZON 类目树接口，搜索候选类目。
3. 用户选择目标 OZON 类目。
4. 系统调用类目属性接口。
5. 系统调用属性值字典接口。
6. 保存类目属性字典。
7. 系统生成字段缺口清单。
8. 用户补充映射规则。
9. 该类目进入“可用”状态。

### 6.2 已有类目再次发布

1. 用户选择商品事实。
2. 系统根据本地品类推荐已保存 OZON 类目。
3. 系统直接读取本地类目属性字典。
4. 执行字段缺口检查。
5. 缺字段则进入待补充。
6. 字段满足后进入刊登草稿。

### 6.3 发布失败后反向补全字典

1. OZON API 返回错误。
2. 系统解析错误。
3. 如果错误是缺少属性或属性格式错误，写入 `OzonFieldGap`。
4. 标记当前类目字典需要更新。
5. 用户重新拉取属性或人工补充映射。

## 7. 对准确率的解决方案

为了提高上传准确率，系统不能只依赖 AI，而要组合：

```text
OZON 类目属性字典
+ 商品事实库
+ 字段映射规则
+ 发布前缺口检查
+ 测试店发布错误学习
+ 人工确认
```

准确率提升路径：

1. 第一批类目人工确认字段。
2. 保存字段映射。
3. 每次发布前自动检查缺口。
4. 每次失败都沉淀错误原因。
5. 后续同类目商品复用字段映射。

## 8. 哪些信息必须人工补充

以下字段如果源数据没有明确证据，必须人工补充或留空：

- 品牌
- 材质
- 认证
- 适配型号
- 保修
- 产地
- 尺寸
- 重量
- 包装清单
- 电池容量
- 功率
- 无线频段
- 兼容设备
- 车载适配车型

## 9. Claude Code 实施任务

### 任务 A：文档更新

更新：

- `docs/ozon_listing_prd.md`
- `docs/ozon_api_and_collection_contract.md`
- `docs/ozon_source_to_listing_adaptation_plan.md`

增加：

- 类目属性字典
- 字段缺口检查
- 类目字段来源优先级

### 任务 B：原型

新增原型：

```text
docs/prototypes/ozon_category_attributes.v1.html
docs/prototypes/ozon_field_gap_check.v1.html
```

页面要求：

- 能查看类目属性。
- 能区分必填/选填。
- 能查看属性值字典。
- 能配置本地字段映射。
- 能查看某个草稿缺少哪些字段。

### 任务 C：API 实测脚本

先做只读实测：

```text
1. 测试类目树接口
2. 测试类目属性接口
3. 测试属性值字典接口
4. 保存响应样例
```

交付：

```text
docs/ozon_api_test_results/
```

每个接口保存：

- request JSON
- response JSON
- 测试时间
- 店铺类型
- 结论

### 任务 D：数据模型设计

先写设计文档：

```text
docs/ozon_category_attribute_data_model.md
```

经用户审核后再实现数据表。

### 任务 E：发布前校验器

实现：

- 读取类目必填字段。
- 读取商品事实字段。
- 执行字段映射。
- 生成缺口清单。
- 阻断不完整草稿发布。

## 10. 第一版最小可行实现

如果先做最小版本：

1. 手工录入 OZON 类目 ID。
2. 调 API 拉取该类目属性。
3. 保存必填属性列表。
4. 手工配置属性到商品事实字段的映射。
5. 发布前生成缺口清单。
6. 缺少必填字段时阻断发布。

暂不做：

- 自动类目推荐
- 全类目缓存
- 自动竞品字段分析
- 自动解析发布错误

## 11. 结论

你需要获取不同 OZON 产品/品类所需信息的地方，按优先级是：

```text
1. OZON Seller API 类目属性接口
2. OZON 卖家后台创建商品页
3. OZON 类目导入模板
4. OZON 已在线竞品商品卡
5. OZON API 发布失败错误
```

系统实现上，应将这些信息沉淀成：

```text
类目属性字典 + 字段映射规则 + 字段缺口检查
```

这样才能避免“采集到什么就上传什么”，并显著提高发布准确率。
