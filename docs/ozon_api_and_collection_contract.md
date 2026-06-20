# OZON API 实测清单 + 商品采集 JSON 标准

版本：v0.2
日期：2026-06-14
适用范围：OZON 跨境店第一期，后续兼容本土店
关联文档：
- `docs/ozon_source_to_listing_adaptation_plan.md` — 源商品到 Listing 适配层方案
- `docs/ozon_category_attribute_acquisition_plan.md` — 类目属性字典与字段缺口检查
- `docs/ozon_vision_tool_model_plan.md` — 图片识别工具模型接入方案

## 1. 目标

本文件用于约束 OZON 自动刊登模块第一期的实施边界：

- 明确 OZON API 在测试店中必须跑通的接口、请求样例、验收标准和失败记录方式。
- 明确浏览器插件、官方开放平台、手工导入向系统提交商品资料时的 JSON 数据标准。
- 明确哪些字段是事实字段，哪些字段允许 AI 生成，哪些字段必须人工确认后才能发布。

第一期原则：AI 只负责起草和辅助，发布前必须人工审核。

## 2. 已知前提

- 已有 OZON 跨境店。
- 后续会支持 OZON 本土店。
- 已有 `Client-Id` / `Api-Key`。
- 允许通过 API 创建商品。
- 有测试店，可先跑通接口。
- 主营品类：3C 产品、无人机图传配件、摄影配件、车载工具。
- 采集来源：1688、拼多多、淘宝、天猫，优先浏览器插件 + 官方开放平台。

## 3. 第一阶段验收目标

第一阶段不要求完整自动化，只要求打通链路：

1. 配置 OZON 测试店 API 凭证。
2. 获取类目和类目属性。
3. 从采集 JSON 生成刊登草稿。
4. 人工补齐和审核草稿。
5. 发布 1 个单 SKU 商品到测试店。
6. 发布 1 个多 SKU 商品到测试店。
7. 上传或更新商品图片。
8. 更新商品价格和库存。
9. 查询发布任务和在线商品状态。
10. 将 OZON 商品 ID、offer_id、失败原因回写本地。

## 4. OZON API 实测清单

> 说明：接口路径以当前 OZON Seller API 常见命名为基准，实际开发前必须用测试店凭证验证最新文档和响应结构。如果接口版本变化，以实测结果为准。

### 4.1 连通性测试

目的：确认 `Client-Id` / `Api-Key` 可用，账号具备读取商品权限。

建议接口：

- `POST /v3/product/list`

请求要点：

```json
{
  "filter": {},
  "last_id": "",
  "limit": 1
}
```

验收标准：

- HTTP 状态码为 200。
- 返回结构中能看到商品列表或空列表。
- 非 200 时保存状态码、响应体、请求时间、店铺 ID。

失败处理：

- 401/403：检查 API Key、Client-Id、店铺权限。
- 429：记录限流，后续增加重试和队列。
- 5xx：记录响应体，允许稍后重试。

### 4.2 类目树测试

目的：为 3C、无人机配件、摄影配件、车载工具建立本地类目映射。

建议接口：

- 获取类目树接口，待实测确认最新路径。

测试输入：

- 3C 配件关键词
- 摄影配件关键词
- 车载工具关键词
- 无人机图传关键词

验收标准：

- 能拿到候选类目 ID 和类目名称。
- 每个主营品类至少选出 1-3 个候选 OZON 类目。
- 保存类目路径，例如：`Electronics > Accessories > ...`。

本地记录字段：

```json
{
  "local_category": "车载工具",
  "ozon_category_id": "待实测",
  "ozon_category_path": "待实测",
  "confidence": 0.0,
  "review_status": "pending"
}
```

### 4.3 类目属性测试

目的：获取类目必填字段、可选字段、属性值字典，决定草稿字段结构。

建议接口：

- `POST /v4/product/info/attributes`
- 属性值字典接口，待实测确认最新路径。

每个类目需要记录：

- 必填属性
- 可选属性
- 属性 ID
- 属性名称
- 字段类型
- 是否允许自定义值
- 是否多选
- 枚举值列表
- 单位要求

验收标准：

- 每个主营类目至少完成一次属性拉取。
- 必填属性能映射到采集 JSON 或标记为待确认。
- 不允许 AI 硬填无来源属性。

属性映射样例：

```json
{
  "ozon_category_id": "123456",
  "attributes": [
    {
      "attribute_id": "85",
      "name_ru": "Бренд",
      "name_cn": "品牌",
      "required": true,
      "type": "dictionary",
      "source_field": "brand.name",
      "fill_policy": "source_or_empty",
      "manual_required": true
    }
  ]
}
```

### 4.4 创建/更新商品测试

目的：测试 OZON 商品卡创建能力。

建议接口：

- `POST /v3/product/import`
- `POST /v1/product/import/info`

测试商品：

- 单 SKU：车载工具或 3C 小配件。
- 多 SKU：颜色/规格不同的 3C 配件。

请求生成规则：

- `offer_id` 必须由本地生成并可追溯。
- SKU 顺序必须与源商品一致。
- 只提交人工审核通过字段。
- 待确认字段不提交或提交空值，不使用 AI 猜测值。

验收标准：

- 创建请求被 OZON 接收。
- 能查询导入任务状态。
- 成功后能获得 OZON 商品 ID 或任务成功标识。
- 失败时能记录具体字段错误。

发布任务记录：

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
  "created_at": "2026-06-13T00:00:00+08:00"
}
```

### 4.5 图片上传/更新测试

目的：确认商品主图、SKU 图、详情图能通过 API 关联到商品。

建议接口：

- `POST /v1/product/pictures/import`
- `POST /v2/product/pictures/info`

图片要求：

- 默认 3:4 竖版。
- 主体清晰居中。
- 白底或浅色干净背景。
- 不能含中文、平台 Logo、二维码、价格、折扣、销量、联系方式。
- 俄语文字少而准确；不确定时不放文字。

验收标准：

- 图片 URL 或图片任务被 OZON 接收。
- 能查询图片处理状态。
- 图片与正确 SKU 关联。

图片记录样例：

```json
{
  "image_id": "local-image-id",
  "source": "generated|source|edited",
  "role": "main|sku|detail|size|package",
  "sku_ref": "source-sku-id",
  "url": "https://example.com/image.jpg",
  "aspect_ratio": "3:4",
  "review_status": "approved",
  "checks": {
    "no_chinese": true,
    "no_watermark": true,
    "no_price": true,
    "product_consistent": true
  }
}
```

### 4.6 价格和库存测试

目的：验证发布后可独立修改价格和库存。

建议接口：

- 价格更新接口，待实测确认最新路径。
- 库存更新接口，待实测确认最新路径。

第一版定价公式：

```text
price_rub = (cost_cny + domestic_shipping_cny + packaging_cny + international_shipping_cny + risk_buffer_cny)
            * cny_to_rub_rate
            / (1 - commission_rate - ad_reserve_rate - target_margin_rate)
```

说明：

- 初版允许估算。
- 佣金、物流、广告预留可人工修改。
- 所有计算结果必须可追溯。

验收标准：

- 能修改测试商品价格。
- 能修改测试商品库存。
- 修改后可从在线商品接口读回。

### 4.7 在线商品状态测试

目的：查询已发布商品在 OZON 的状态。

建议接口：

- `POST /v3/product/list`
- 商品详情接口，待实测确认最新路径。

验收标准：

- 可通过 `offer_id` 或 OZON 商品 ID 找回商品。
- 能读取商品状态、可见性、错误信息。
- 本地状态能同步为：`draft`、`approved`、`publishing`、`published`、`failed`、`archived`。

### 4.8 订单/财务/运营数据测试

目的：为后续 OZON 工作台准备，不作为第一期发布阻断项。

优先指标：

- 支付金额
- 退款金额
- 净支付金额
- 访客数
- 买家数
- 转化率
- 退款率
- 客单价
- 在线商品数
- 支付订单
- 售后订单

验收标准：

- 能确认这些数据分别来自哪些 OZON API。
- 如果部分数据 API 不开放，标记为“暂不可自动同步”。

## 5. API 实测记录模板

每次实测都要记录：

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
  "response_body": {},
  "result": "pass|fail|blocked",
  "error_summary": "",
  "next_action": "",
  "tested_at": "2026-06-13T00:00:00+08:00"
}
```

## 6. 商品采集 JSON 总体标准

插件或开放平台提交的数据必须符合以下顶层结构：

```json
{
  "schema_version": "1.0",
  "source": {},
  "product": {},
  "brand": {},
  "supplier": {},
  "skus": [],
  "media": [],
  "attributes": [],
  "logistics": {},
  "pricing": {},
  "evidence": [],
  "ai_outputs": {},
  "quality": {}
}
```

字段原则：

- 采集事实字段必须保留来源。
- 不确定字段用 `null`，不要用猜测值。
- SKU 顺序必须与源商品一致。
- 图片必须保留原始 URL、来源页、用途和对应 SKU。
- AI 生成内容必须放入 `ai_outputs`，不能覆盖原始字段。

## 7. source 来源信息

```json
{
  "source": {
    "platform": "1688|taobao|tmall|pinduoduo|manual",
    "url": "https://detail.1688.com/...",
    "item_id": "source-item-id",
    "shop_name": "供应商店铺名",
    "captured_at": "2026-06-13T00:00:00+08:00",
    "capture_method": "browser_extension|open_api|manual",
    "html_snapshot_id": "optional-local-snapshot-id",
    "screenshot_ids": ["local-screenshot-id"]
  }
}
```

必填：

- `platform`
- `url`
- `captured_at`
- `capture_method`

## 8. product 商品基础信息

```json
{
  "product": {
    "title_cn": "15合1多功能车载应急手电筒",
    "title_source": "source_page",
    "category_cn": "车载工具",
    "description_cn": "源页面中的商品说明",
    "model": null,
    "origin": null,
    "warranty": null,
    "certifications": [],
    "facts_locked": [
      "title_cn",
      "sku_order",
      "sku_names",
      "colors",
      "package_contents"
    ]
  }
}
```

注意：

- `origin`、`warranty`、`certifications` 没有证据时必须为 `null` 或空数组。
- `facts_locked` 表示 AI 不得修改的事实字段。

## 9. brand 品牌信息

```json
{
  "brand": {
    "name": null,
    "source": null,
    "authorization_evidence": null,
    "risk_level": "unknown|low|medium|high",
    "notes": "无法确认品牌授权时，不新增品牌元素"
  }
}
```

规则：

- 不能新增品牌 Logo。
- 不能编造授权。
- 无品牌或无法确认时，OZON 品牌字段按类目规则处理，必要时人工确认。

## 10. supplier 供应商信息

```json
{
  "supplier": {
    "name": "源平台供应商名",
    "contact": null,
    "source_shop_url": "https://...",
    "min_order_quantity": 1,
    "shipping_from": "广东 深圳",
    "notes": ""
  }
}
```

供应商信息默认只用于内部，不展示给 OZON 买家。

## 11. skus SKU 标准

```json
{
  "skus": [
    {
      "source_order": 1,
      "source_sku_id": "sku-001",
      "source_sku_name": "黄色 标配",
      "local_sku": null,
      "offer_id": null,
      "color_cn": "黄色",
      "color_ru": null,
      "size_cn": null,
      "size_ru": null,
      "style_cn": "标配",
      "style_ru": null,
      "bundle_quantity": 1,
      "package_contents_cn": ["手电筒", "Type-C 线"],
      "material_cn": null,
      "purchase_price_cny": 19.8,
      "stock_source": null,
      "image_refs": ["img-sku-001"],
      "evidence_refs": ["ev-sku-001"],
      "confidence": 0.95,
      "manual_status": "pending"
    }
  ]
}
```

强规则：

- `source_order` 必须与源商品 SKU 顺序一致。
- 不允许把 SKU 改成 A 款/B 款。
- 不允许自动合并或拆分 SKU。
- 颜色、尺寸、套装数量、包装内容不确定时留空。

## 12. media 图片标准

```json
{
  "media": [
    {
      "media_id": "img-main-001",
      "source": "source_page|generated|edited|manual_upload",
      "role": "main|sku|detail|scene|selling_point|function|size|package",
      "source_url": "https://...",
      "local_path": null,
      "sku_refs": ["sku-001"],
      "width": 1024,
      "height": 1360,
      "aspect_ratio": "3:4",
      "has_text": true,
      "text_language": "zh|ru|en|none|mixed",
      "needs_cleanup": true,
      "for_ozon": false,
      "evidence_refs": ["ev-img-001"],
      "review_status": "pending"
    }
  ]
}
```

图片处理规则：

- 源图保留，不覆盖。
- 生成图或处理图新增记录。
- OZON 可用图片必须 `for_ozon = true` 且 `review_status = approved`。

## 13. attributes 源属性标准

```json
{
  "attributes": [
    {
      "name_cn": "材质",
      "value_cn": "ABS",
      "unit": null,
      "source": "source_page|image_ocr|manual",
      "evidence_refs": ["ev-attr-001"],
      "confidence": 0.9,
      "ozon_attribute_id": null,
      "ozon_value_id": null,
      "manual_status": "pending"
    }
  ]
}
```

规则：

- 尺寸、重量、承重、适配型号、材质比例等必须有来源。
- 低置信度属性不能自动发布。

## 14. logistics 物流字段

```json
{
  "logistics": {
    "net_weight_g": null,
    "gross_weight_g": null,
    "package_length_mm": null,
    "package_width_mm": null,
    "package_height_mm": null,
    "shipping_profile": null,
    "evidence_refs": [],
    "manual_status": "pending"
  }
}
```

第一期允许人工填写。

## 15. pricing 采集价格字段

```json
{
  "pricing": {
    "source_price_cny": 19.8,
    "domestic_shipping_cny": null,
    "estimated_international_shipping_cny": null,
    "commission_rate": null,
    "ad_reserve_rate": 0.05,
    "target_margin_rate": 0.35,
    "cny_to_rub_rate": null,
    "suggested_price_rub": null,
    "manual_price_rub": null,
    "pricing_status": "estimated|manual_confirmed"
  }
}
```

规则：

- 第一版售价可估算。
- 发布前必须允许人工修改。
- 最终发布使用 `manual_price_rub`，没有人工确认时不发布。

## 16. evidence 证据标准

```json
{
  "evidence": [
    {
      "evidence_id": "ev-sku-001",
      "type": "text|image|screenshot|html|api",
      "source_url": "https://...",
      "selector": ".sku-list",
      "content": "黄色 标配",
      "media_id": null,
      "captured_at": "2026-06-13T00:00:00+08:00"
    }
  ]
}
```

用途：

- 支撑 AI 生成。
- 支撑人工审核。
- 出现争议时回溯来源。

## 17. ai_outputs AI 产物标准

```json
{
  "ai_outputs": {
    "ozon_title_ru": {
      "value": "Многофункциональный аварийный фонарь для автомобиля 15 в 1",
      "model": "text-model-name",
      "prompt_id": "ozon-title-3c-v1",
      "confidence": 0.82,
      "status": "draft"
    },
    "ozon_bullets_ru": {
      "value": [
        "Подходит для аварийных ситуаций в дороге",
        "Компактный корпус и несколько режимов работы"
      ],
      "confidence": 0.78,
      "status": "draft"
    },
    "image_plan": {
      "style": "极简应急工具",
      "slots": [
        {
          "slot_order": 1,
          "role": "main",
          "scope": "sku",
          "prompt": "Create a 3:4 vertical e-commerce product image...",
          "negative_prompt": "No Chinese text, no price, no platform logo...",
          "status": "draft"
        }
      ]
    }
  }
}
```

规则：

- AI 产物默认是草稿。
- 不得覆盖人工确认字段。
- 低置信度内容进入待确认。

## 17b. fact_extraction 商品事实提取标准

视觉工具模型识别图片后，与源文本一起交给 DeepSeek 主模型处理，输出结构化商品事实 JSON。

```json
{
  "fact_extraction": {
    "standard_name_cn": "DJI Mic Mini 2 无线麦克风",
    "product_type": "无线麦克风",
    "category_hint_cn": "3C数码 > 音频配件",
    "sku_facts": [
      {
        "source_order": 1,
        "source_sku_id": "sku-001",
        "color_cn": "黑色",
        "size_cn": null,
        "style_cn": "一拖二含充电盒",
        "bundle_quantity": 1,
        "package_contents_cn": ["发射器×2", "接收器×1", "充电盒×1", "充电线×1"],
        "material_cn": null,
        "confidence": 0.95
      }
    ],
    "unknown_fields": ["material", "battery_capacity", "wireless_range"],
    "risk_notes": ["品牌 DJI 需确认授权或按类目规则处理"],
    "confidence": 0.82
  }
}
```

## 17c. adaptation 适配方案标准

商品事实到 OZON Listing 的适配结构：

```json
{
  "adaptation": {
    "relation_type": "one_to_one",
    "source_product_ids": ["src-001"],
    "target_ozon_category_id": "123456",
    "target_ozon_category_path": "Электроника > Аудио > Микрофоны",
    "attribute_mapping": [
      {
        "ozon_attribute_id": "85",
        "ozon_attribute_name": "Бренд",
        "local_field_path": "brand.name",
        "fill_policy": "manual_required",
        "confidence": 0.5
      }
    ],
    "field_gaps": {
      "required_total": 12,
      "resolved": 8,
      "blocking_gaps": [
        {
          "attribute_id": "85",
          "name": "Бренд",
          "reason": "缺少品牌字段，该类目必填",
          "suggested_action": "人工选择无品牌或补充品牌证据"
        }
      ]
    }
  }
}
```

## 17d. vision_result 视觉识别结果标准

视觉工具模型输出结构（详见 `docs/ozon_vision_tool_model_plan.md` §5）：

```json
{
  "vision_result": {
    "schema_version": "1.0",
    "task_type": "sku_image|detail_ocr|compliance_check|fact_extraction",
    "image": {
      "media_id": "img-001",
      "role": "sku|main|detail|scene|package|size"
    },
    "detected": {
      "objects": [],
      "colors": [],
      "visible_text": [],
      "dimensions": [],
      "package_contents": []
    },
    "compliance": {
      "has_chinese": false,
      "has_watermark": false,
      "has_qr_code": false,
      "has_price_or_discount": false,
      "has_platform_logo": false,
      "ozon_ready": false,
      "issues": []
    },
    "facts": [
      {
        "field_path": "skus[0].color_cn",
        "value": "黑色",
        "evidence": "图片主体为黑色外壳",
        "confidence": 0.9
      }
    ],
    "uncertain": [
      {
        "field_path": "material",
        "guess": "塑料",
        "confidence": 0.35,
        "requires_manual_confirmation": true
      }
    ]
  }
}
```

## 18. quality 质量状态

```json
{
  "quality": {
    "overall_status": "pending_review|approved|blocked",
    "issues": [
      {
        "level": "error|warning|info",
        "field": "skus[0].color_ru",
        "message": "俄语颜色未确认",
        "blocking": false
      }
    ],
    "reviewer": null,
    "reviewed_at": null
  }
}
```

阻断发布的错误：

- 必填事实字段缺失。
- SKU 顺序不一致。
- 图片不合规。
- 文案含禁止词。
- 类目必填属性未处理。
- 价格未人工确认。

## 19. 最小可用采集 JSON 样例

```json
{
  "schema_version": "1.0",
  "source": {
    "platform": "1688",
    "url": "https://detail.1688.com/example",
    "item_id": "123456",
    "shop_name": "示例供应商",
    "captured_at": "2026-06-13T00:00:00+08:00",
    "capture_method": "browser_extension",
    "html_snapshot_id": null,
    "screenshot_ids": []
  },
  "product": {
    "title_cn": "15合1多功能车载应急手电筒",
    "title_source": "source_page",
    "category_cn": "车载工具",
    "description_cn": "源页面商品描述",
    "model": null,
    "origin": null,
    "warranty": null,
    "certifications": [],
    "facts_locked": ["title_cn", "sku_order", "sku_names"]
  },
  "brand": {
    "name": null,
    "source": null,
    "authorization_evidence": null,
    "risk_level": "unknown",
    "notes": ""
  },
  "supplier": {
    "name": "示例供应商",
    "contact": null,
    "source_shop_url": "https://shop.example.com",
    "min_order_quantity": 1,
    "shipping_from": "广东 深圳",
    "notes": ""
  },
  "skus": [
    {
      "source_order": 1,
      "source_sku_id": "sku-yellow",
      "source_sku_name": "黄色 标配",
      "local_sku": null,
      "offer_id": null,
      "color_cn": "黄色",
      "color_ru": null,
      "size_cn": null,
      "size_ru": null,
      "style_cn": "标配",
      "style_ru": null,
      "bundle_quantity": 1,
      "package_contents_cn": ["手电筒", "Type-C 线"],
      "material_cn": null,
      "purchase_price_cny": 19.8,
      "stock_source": null,
      "image_refs": ["img-sku-yellow"],
      "evidence_refs": ["ev-sku-yellow"],
      "confidence": 0.95,
      "manual_status": "pending"
    }
  ],
  "media": [
    {
      "media_id": "img-sku-yellow",
      "source": "source_page",
      "role": "sku",
      "source_url": "https://example.com/yellow.jpg",
      "local_path": null,
      "sku_refs": ["sku-yellow"],
      "width": null,
      "height": null,
      "aspect_ratio": null,
      "has_text": false,
      "text_language": "none",
      "needs_cleanup": false,
      "for_ozon": false,
      "evidence_refs": ["ev-img-yellow"],
      "review_status": "pending"
    }
  ],
  "attributes": [],
  "logistics": {
    "net_weight_g": null,
    "gross_weight_g": null,
    "package_length_mm": null,
    "package_width_mm": null,
    "package_height_mm": null,
    "shipping_profile": null,
    "evidence_refs": [],
    "manual_status": "pending"
  },
  "pricing": {
    "source_price_cny": 19.8,
    "domestic_shipping_cny": null,
    "estimated_international_shipping_cny": null,
    "commission_rate": null,
    "ad_reserve_rate": 0.05,
    "target_margin_rate": 0.35,
    "cny_to_rub_rate": null,
    "suggested_price_rub": null,
    "manual_price_rub": null,
    "pricing_status": "estimated"
  },
  "evidence": [
    {
      "evidence_id": "ev-sku-yellow",
      "type": "text",
      "source_url": "https://detail.1688.com/example",
      "selector": ".sku-list",
      "content": "黄色 标配",
      "media_id": null,
      "captured_at": "2026-06-13T00:00:00+08:00"
    }
  ],
  "ai_outputs": {},
  "quality": {
    "overall_status": "pending_review",
    "issues": [],
    "reviewer": null,
    "reviewed_at": null
  }
}
```

## 20. 开发顺序建议

1. 实现 OZON API 测试脚本，只读测试 `/v3/product/list`。
2. 固化采集 JSON 入库接口。
3. 用 10-20 个商品生成真实采集 JSON。
4. 按类目跑 OZON 属性接口，建立类目映射表。
5. 生成刊登草稿，不发布。
6. 做发布前校验器。
7. 发布测试店单 SKU 商品。
8. 发布测试店多 SKU 商品。
9. 接图片、价格、库存更新。
10. 做工作台数据回写。

## 21. 待确认问题

- OZON 类目树接口和属性字典接口的最新路径及响应结构。
- 3C/无人机配件/摄影配件/车载工具的首批目标 OZON 类目。
- 是否已有稳定图片存储服务，用于 OZON API 读取图片 URL。
- 测试店是否允许真实创建商品卡。
- 本土店与跨境店字段差异是否会影响第一期数据结构。
- 浏览器插件是否需要保存 HTML 快照，还是只保存结构化数据和截图。
