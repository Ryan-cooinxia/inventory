# OZON 图片识别工具模型接入方案

版本：v0.1
日期：2026-06-14
用途：在 DeepSeek 主模型无法识别图片的前提下，为 OZON 商品采集、商品事实抽取、图片合规检查增加视觉识别能力
关联文档：

- `docs/ozon_listing_prd.md`
- `docs/ozon_api_and_collection_contract.md`
- `docs/ozon_source_to_listing_adaptation_plan.md`
- `docs/ozon_category_attribute_acquisition_plan.md`

## 1. 背景

当前系统和 Claude Code 工作流主要使用 DeepSeek V4 Pro 作为主模型。该模型适合文本推理、字段整理、俄语文案生成、规则判断，但如果当前接入方式不支持图片输入，就无法直接理解采集回来的商品图、SKU 图、详情图、参数图和截图。

OZON 自动刊登场景中，图片非常关键：

- 很多 SKU 差异只体现在图片里。
- 国内详情图里常包含尺寸、材质、功能、包装清单。
- 1688/淘宝/拼多多的文字采集可能不完整。
- 图片中可能含中文、水印、价格、二维码、平台 Logo。
- 主图是否符合 OZON 要求需要图像检查。

因此需要新增一个“小工具模型”：专门负责图片理解和 OCR，把图片转成结构化 JSON，再交给 DeepSeek 主模型继续处理。

## 2. 核心设计

不要替换 DeepSeek 主模型，而是新增视觉工具层：

```text
图片/截图/详情图
-> Vision Tool Model
-> 结构化视觉识别 JSON
-> DeepSeek 主模型
-> 商品事实库 / OZON 适配 / 俄语文案 / 发布校验
```

职责划分：

| 层级 | 负责内容 |
| --- | --- |
| Vision Tool Model | 图片 OCR、主体识别、SKU 差异识别、图片合规检查、参数图信息抽取 |
| DeepSeek 主模型 | 综合推理、事实整理、OZON 类目适配、俄语文案、规则判断 |
| 人工审核 | 确认事实字段、确认图片是否可用、确认发布 |

## 3. 视觉模型候选

系统设计上不要绑定单一厂商，应做成可配置 provider。

建议支持：

```text
openai_vision
qwen_vl
gemini_vision
custom_http
```

第一期可以只实现一个 provider，但数据结构要预留扩展。

### 3.1 选择建议

如果优先考虑中文 OCR、国内图片、电商图理解：

- 优先考虑 Qwen-VL 类视觉模型。

如果优先考虑接口稳定、结构化输出、多语言和后续生态：

- 可考虑 OpenAI 视觉模型。

如果优先考虑低成本和长上下文生态：

- 可考虑 Gemini 视觉模型。

如果已有企业内部视觉模型：

- 使用 `custom_http` 对接。

## 4. 使用场景

### 4.1 SKU 图片识别

输入：

- SKU 图
- SKU 名称
- 源 SKU 顺序

输出：

- 主体颜色
- 款式差异
- 配件差异
- 包装差异
- 是否与 SKU 名称一致
- 置信度

### 4.2 详情图 OCR

输入：

- 国内详情图
- 参数图
- 包装清单图

输出：

- 图片文字
- 尺寸参数
- 功能点
- 材质
- 包装内容
- 适配型号
- 注意事项

### 4.3 图片合规检查

输入：

- 主图
- SKU 图
- 详情图
- AI 生成图

输出：

- 是否有中文
- 是否有平台 Logo
- 是否有水印
- 是否有二维码
- 是否有价格/折扣/销量
- 是否有联系方式
- 是否有虚假认证标识
- 是否适合 OZON 商品卡

### 4.4 商品事实补充

输入：

- 多张源图
- 已采集文本

输出：

- 图片中能证明的事实
- 图片中无法确认但疑似的信息
- 需要人工确认的信息

### 4.5 图片生成前分析

输入：

- 源图

输出：

- 产品主体描述
- 不可改变的外观事实
- 可清理的背景/文字
- 图片生成负面提示词

## 5. 视觉识别输出 JSON 标准

视觉工具模型必须输出结构化 JSON，不直接输出给买家的文案。

```json
{
  "schema_version": "1.0",
  "task_type": "sku_image|detail_ocr|compliance_check|fact_extraction|image_prompt_input",
  "image": {
    "media_id": "img-001",
    "source_url": "https://example.com/image.jpg",
    "local_path": null,
    "role": "sku|main|detail|scene|package|size"
  },
  "detected": {
    "objects": [],
    "colors": [],
    "materials": [],
    "visible_text": [],
    "dimensions": [],
    "package_contents": [],
    "functions": [],
    "compatibility": []
  },
  "compliance": {
    "has_chinese": false,
    "has_non_russian_text": false,
    "has_platform_logo": false,
    "has_watermark": false,
    "has_qr_code": false,
    "has_price_or_discount": false,
    "has_contact_info": false,
    "has_unverified_certification": false,
    "ozon_ready": false,
    "issues": []
  },
  "facts": [
    {
      "field_path": "skus[0].color_cn",
      "value": "黄色",
      "evidence": "图片主体为黄色外壳",
      "confidence": 0.9,
      "requires_manual_confirmation": false
    }
  ],
  "uncertain": [
    {
      "field_path": "material",
      "guess": "塑料",
      "reason": "仅凭图片无法确认具体材质",
      "confidence": 0.35,
      "requires_manual_confirmation": true
    }
  ],
  "summary_cn": "图片显示一款黄色车载应急手电筒，带侧面灯条和安全锤结构。",
  "model": {
    "provider": "openai_vision|qwen_vl|gemini_vision|custom_http",
    "model_name": "",
    "prompt_id": "",
    "processed_at": "2026-06-14T00:00:00+08:00"
  }
}
```

## 6. 提示词原则

视觉模型提示词必须强调：

- 只描述图片中能看到的内容。
- 不要猜品牌、材质、认证、保修、适配型号。
- 不确定就输出 `uncertain`。
- OCR 文字原样提取，不要改写。
- 识别是否存在中文、价格、折扣、平台 Logo、水印、二维码。
- 输出必须是 JSON。

### 6.1 SKU 图片识别提示词模板

```text
你是电商商品图片识别助手。请分析这张 SKU 图片，只输出 JSON。

任务：
1. 识别图片中的商品主体、颜色、款式、配件、包装差异。
2. 只记录图片中能确认的信息。
3. 不要猜品牌、材质、认证、保修、适配型号。
4. 如果无法确认，放入 uncertain。
5. 检查是否有中文、水印、价格、二维码、平台 Logo、联系方式。

输出字段必须符合 vision_result schema。
```

### 6.2 详情图 OCR 提示词模板

```text
你是电商详情图 OCR 和事实抽取助手。请读取图片中的文字和参数，只输出 JSON。

任务：
1. 提取所有可读文字，保留原文。
2. 识别尺寸、重量、材质、功能、包装清单、适配型号。
3. 不要把无法确认的信息写成事实。
4. 标记中文、英文、俄语或混合文字。
5. 检查图片是否适合 OZON 商品卡。
```

## 7. 数据模型建议

### 7.1 VisionModelConfig

用途：配置视觉工具模型。

字段：

```text
id
user_id
provider
model_name
api_base
api_key_encrypted
enabled
timeout_seconds
max_images_per_batch
notes
created_at
updated_at
```

### 7.2 ImageAnalysisJob

用途：记录图片识别任务。

字段：

```text
id
user_id
media_id
source_product_id
draft_id
task_type
provider
model_name
status                  pending | running | success | failed
request_json
response_json
parsed_json
error_message
created_at
updated_at
```

### 7.3 ImageFact

用途：把视觉模型识别结果沉淀为商品事实证据。

字段：

```text
id
user_id
image_analysis_job_id
media_id
field_path
value
evidence_text
confidence
requires_manual_confirmation
accepted
created_at
updated_at
```

## 8. 系统页面建议

新增或扩展：

```text
OZON 运营 -> 模型接口 -> 视觉模型配置
OZON 运营 -> 商品适配 -> 图片识别结果
OZON 运营 -> 图片方案 -> 图片合规检查
```

### 8.1 视觉模型配置页

字段：

- Provider
- Model Name
- API Base
- API Key
- 是否启用
- 测试图片上传
- 测试结果

### 8.2 图片识别结果面板

在商品适配工作台中显示：

- 图片缩略图
- OCR 文本
- 商品主体描述
- 识别出的事实字段
- 不确定字段
- 合规问题
- 接受为事实
- 忽略
- 人工修正

## 9. 处理流程

### 9.1 采集后自动分析

```text
1. 商品采集完成
2. 系统识别图片类型：主图 / SKU 图 / 详情图 / 参数图
3. 创建 ImageAnalysisJob
4. 调用 Vision Tool Model
5. 保存 parsed_json
6. 生成 ImageFact
7. 在商品适配工作台展示
```

### 9.2 人工确认

```text
1. 用户查看识别结果
2. 接受可信事实
3. 修改错误识别
4. 拒绝低置信度字段
5. 被接受的 ImageFact 进入 ProductFact
```

### 9.3 给 DeepSeek 使用

DeepSeek 主模型输入不再是原图，而是：

```json
{
  "source_text": {},
  "source_skus": [],
  "vision_facts": [],
  "uncertain_fields": [],
  "image_compliance_issues": []
}
```

这样 DeepSeek 可以基于结构化视觉事实完成：

- 商品事实整理
- SKU 拆解
- OZON 文案
- 字段缺口检查
- 图片方案生成

## 10. 发布前校验新增项

图片相关阻断项：

- 主图未经过合规检查。
- SKU 图与 SKU 事实冲突。
- 图片含中文、水印、二维码、价格、平台 Logo。
- 视觉模型识别到的关键事实未人工确认。
- AI 生成图改变商品颜色、材质、配件或包装。

警告项：

- 视觉识别置信度低。
- 图片文字 OCR 不完整。
- 图片尺寸比例不符合 3:4。
- 详情图文字过多。

## 11. Claude Code 实施任务

### 任务 A：文档同步

更新：

- `docs/ozon_listing_prd.md`
- `docs/ozon_source_to_listing_adaptation_plan.md`
- `docs/ozon_api_and_collection_contract.md`

加入：

- 视觉工具模型
- 图片识别 JSON
- ImageAnalysisJob
- ImageFact
- 图片合规检查

### 任务 B：原型

新增原型：

```text
docs/prototypes/ozon_vision_model_config.v1.html
docs/prototypes/ozon_image_analysis_result.v1.html
```

验收：

- 能配置视觉模型。
- 能上传测试图片。
- 能展示 OCR、事实字段、合规问题。
- 能人工接受/拒绝识别结果。

### 任务 C：数据模型设计

先写：

```text
docs/ozon_vision_data_model.md
```

经用户审核后再实现：

- VisionModelConfig
- ImageAnalysisJob
- ImageFact

### 任务 D：服务层

新增服务：

```text
services/vision_tool.py
```

接口建议：

```python
analyze_image(media, task_type, context=None) -> dict
analyze_batch(images, task_type, context=None) -> list[dict]
normalize_vision_response(response) -> dict
```

### 任务 E：与商品适配工作台集成

实现：

- 从源商品图片创建识别任务。
- 将识别结果展示在适配工作台。
- 用户接受后写入商品事实。

## 12. 第一版最小实现

为了控制复杂度，第一版只做：

1. 支持配置一个视觉模型。
2. 支持手动上传或选择一张图片测试识别。
3. 支持 SKU 图识别。
4. 支持详情图 OCR。
5. 保存识别 JSON。
6. 人工选择是否接受识别出的事实。

暂不做：

- 批量图片自动识别。
- 自动改图。
- 自动生成详情图。
- 多视觉模型路由。
- 图像目标框标注。

## 13. 验收标准

产品验收：

- DeepSeek 不需要直接看图，也能获得图片事实。
- 商品适配工作台能看到图片识别结果。
- 图片事实必须可人工确认。
- 低置信度字段不会自动进入发布。

技术验收：

- 视觉模型配置与主模型配置分离。
- 图片识别结果结构化保存。
- 每条图片事实能追溯到图片和模型输出。
- 发布前校验能使用图片合规检查结果。

## 14. 风险与注意事项

- 视觉模型可能误识别材质、尺寸、品牌，不能自动当成事实。
- OCR 对复杂详情图可能漏字，需要保留原图和人工审核。
- 图片中的品牌 Logo、认证图标不能自动用于 Listing。
- 批量识别成本可能较高，需先手动或按需触发。
- 不同 provider 输出结构不同，必须做统一归一化。

## 15. 建议给 Claude Code 的第一条任务

```text
请根据 docs/ozon_vision_tool_model_plan.md，
先更新 OZON PRD、商品适配方案和采集 JSON 标准，
然后创建视觉模型配置页和图片识别结果页的低保真 HTML 原型。
不要修改业务代码。
```

## 16. 参考资料

- OpenAI Images and vision API 文档：https://developers.openai.com/api/docs/guides/images-vision
- Qwen-VL 论文与项目：https://arxiv.org/abs/2308.12966
