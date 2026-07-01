# 2026-06-30 OZON Listing Review 全面优化

## 改动范围

OZON 刊登草稿审核页 (`listing_review.html`) 四大模块重构 + 数据模型扩展 + API 增强。

## 新增字段

### OzonDraft (models.py)
- `media_json` TEXT — 草稿媒体池 JSON（图片+视频统一管理）
- `rich_content_json` TEXT — 富文本块 JSON（替代追加到 description_ru 的旧方案）

### OzonDraftSku (models.py)
- `barcode` VARCHAR(100) — 条码

### 迁移
- 新建 `migrate_ozon_draft_media.py`：ALTER TABLE 新增上述 3 列

## API 改动 (blueprints/ozon.py)

### 增强
- `POST /api/draft/<id>/media/upload-image` — 上传图片后写入 draft.media_json，生成缩略图
- `POST /api/draft/<id>/save-rich-content` — 改为写入 rich_content_json，不再追加到 description_ru
- `POST /listings/<id>/save` — 修复 SKU 表单名称（index→ID 匹配），保存 offer_id/barcode/pricing
- `GET /listings/<id>` — 新增传入 color_attr（颜色属性Schema）、media（媒体池解析）
- `GET /listings/<id>/validate` — 颜色条件校验、媒体池校验、SKU 完善校验

### 新增
- `POST /api/draft/<id>/media/save` — 统一保存图片+视频媒体池
- `POST /api/draft/<id>/media/import-from-source` — 从采集源导入图片/视频到草稿媒体池
- `_load_media_json(draft)` — 媒体池 JSON 解析辅助函数

## 模板改动 (templates/ozon/listing_review.html)

### Tab 3: SKU/价格 — 三层结构
- **第一层**：SKU 基础信息（刊登开关、源SKU、offer_id、数量、条码、颜色*、款式）
- **第二层**：OZON 价格（刊登币种、刊登价、价格确认）
- **第三层**：变体属性说明（颜色是否必填由 Schema 决定）
- 颜色列仅当 `color_attr` 存在时才显示，`*` 标记仅当 `is_required=true`
- 表单 input name 改为 `offer_id_{sku.id}` 等数据库 ID 匹配格式

### Tab 4: 图片/视频 — 媒体池模式
- [从采集图片导入] / [上传本地图片] / [选择系统生成图] / [添加图片URL]
- 采集图片网格（可点击选中/取消）
- 草稿已选图片卡片：设为主图 / 取消选择 / 设为附图 / 用于富文本
- 视频管理：名称/链接/封面编辑、使用开关、预览、删除、添加视频
- 视频自动导入：`autoFillDraftAll` 增强

### Tab 5: 富文本 — image_id 引用 + 预览增强
- 折叠摘要增加"根据采集生成OZON JSON"按钮
- 图片块不再仅有 URL 输入，改为 [选择图片素材]（从媒体池选）+ 缩略图预览
- 保留高级模式：手动填写 URL（折叠隐藏）
- 图片块选择器显示媒体池已选图片 + 采集源图片
- 保存改为写入 `rich_content_json`

### CSS
- 新增 `.media-img-card` 媒体池卡片样式
- 新增 `.video-item` 视频卡片样式
- 新增 `.rich-preview-collapsed::after` 渐变遮罩

## 核心设计原则

1. **媒体池模式**：图片不直接存 URL，而是统一管理在 `draft.media_json` 中，富文本/SKU/主图都引用 `image_id`
2. **颜色 Schema 驱动**：颜色是否必填由 `OzonCategoryAttribute.is_required` 决定，不写死
3. **发布时统一转换**：发布 OZON 前将 image_id 转 URL，不在编辑阶段强求公网 URL

## 影响文件

| 文件 | 改动类型 |
|------|----------|
| `models.py` | 新增 3 个字段 |
| `migrate_ozon_draft_media.py` | 新建迁移脚本 |
| `blueprints/ozon.py` | 2 个增强路由 + 2 个新路由 + 3 个路由修复 + 1 个辅助函数 |
| `templates/ozon/listing_review.html` | 3 个 Tab 重写 + 全部 JS 重写 |

## 验证

- `python app.py` 启动无报错 ✓
- `node --check` JS 语法检查通过 ✓
- Tab 切换独立脚本：即使业务 JS 出错也不影响 tab 切换 ✓

## 修复记录（2026-06-30 二次修复）

### JS 语法错误导致 Tab 切换失效

**根因**：富文本/图片选择器部分 JS 字符串拼接中存在 `\\'` 模式，在单引号 JS 字符串内错误截断字符串，导致 SyntaxError，整个 `<script>` 不执行。

**修复内容**：
1. Tab 切换独立为小 `<script>` 块（在业务脚本前），确保业务 JS 出错不影响 tab
2. 所有 `onerror="this.style.display=\\'none\\'"` → `onerror="this.style.display=&quot;none&quot;"`
3. 所有 `onclick="...\\''+xxx+'\\'..."` → `onclick="...' + jsArg(xxx) + '..."`
4. 新增 `jsArg(v)` 辅助函数（`JSON.stringify`）
5. `renderMediaPool()` 改用纯 DOM API（`document.createElement`），零字符串拼接
6. Tab 按钮全部加 `type="button"`
- 迁移 `migrate_ozon_draft_media.py` 执行成功 ✓

## 修复记录（2026-07-01 主图合并 + 拖动排序）

### 问题
- 商品主图图集只显示 1 张：`media.images` 存在时页面只显示媒体池中已有图片，未合并采集源中的其他 main 图
- 缺少拖动排序：无法调整主图顺序

### 修复内容

#### 后端 (blueprints/ozon.py)
1. **`listing_review()` — 合并缺失主图**：读取 `draft.media_json` 后，遍历 `source_media_main`，将不在媒体池中的采集主图补充进 `media['images']`。去重按 `source_media_id` / `url` / `local_path`。首张缺主图自动 `is_cover=true`。
2. **`api_draft_media_save()` — 保存前归一化**：按 `sort_order` 重新编号，第一张 `is_cover=true`，其余 `false`。
3. **`api_draft_media_import_from_source()` — 修正文档注释**：docstring 仍写着"后续 main 降级为 gallery"，实际代码主图全量 `role='main'`，修正描述。

#### 前端 (templates/ozon/listing_review.html)
1. **拖动排序**：主图卡片添加 `draggable=true` + `dragstart`/`dragover`/`drop` 事件。
   - `onMainDragStart` / `onMainDragOver` / `onMainDrop` / `reorderMainImages`
   - 拖放后按新顺序重编号 `sort_order`，第一位 `is_cover=true`。
2. **`setCoverImage()` 改为移动逻辑**：不再只改 `is_cover` 标记，而是将目标移到主图数组第一位。
3. **`disableImage()` / `deleteImage()`**：停用/删除封面后，按 `sort_order` 排序选下一张 main 为封面。
4. **`normalizeMainImagesBeforeSave()`**：保存前归一化主图顺序和封面。
5. **`saveMediaPool()`**：保存前调用 `normalizeMainImagesBeforeSave()`。
6. **`renderMediaPool()`**：主图按 `sort_order` 排序渲染。
7. **DOMContentLoaded**：增加 `renderMediaPool()` 调用，确保页面加载时图片网格即渲染。
8. **副标题文案**：改为"拖动调整顺序，第一张为封面图"。

### 验证
- `python app.py` 启动无报错 ✓
- `node --check` JS 语法检查通过 ✓

## 修复记录（2026-07-01 历史脏角色修正 + 去重逻辑修正）

### 问题
- 草稿 2 的 `media_json` 中，11 张采集主图只有第一张 `role=main`，其余 10 张被历史代码写成 `role=gallery`
- 导入接口按 `source_media_id` 判重后直接 `skipped_duplicate`，不检查 role 是否正确
- URL/path 去重可能误杀 OZON 图片变体（wc1000/ww1200/cover 等不同尺寸 URL）
- 导致页面只显示 1 张主图，提示"跳过重复图片 18 张"

### 修复内容

#### 后端 (blueprints/ozon.py) — `listing_review()`
1. **第一步：按采集源修正角色** — 构建 `source_role_by_id`，遍历 `media.images`，若 `source_media_id` 指向采集源 `main`/`sku` 但草稿 role 错误，则覆盖修正
2. **第二步：合并缺失主图** — 仅按 `source_media_id` 去重，移除 URL/path 拦截
3. **第三步：归一化排序** — 所有 main 按 `sort_order` 排序，重新编号，首张 `is_cover=true`

#### 后端 (blueprints/ozon.py) — `api_draft_media_import_from_source()`
1. **已存在不跳过，先修正** — `existing_by_source_id` 改为 dict 映射；遇到已存在的 source_media_id 时检查 role，若采集源是 `main`/`sku` 而草稿是其他值则修正，计数 `images_fixed_role`
2. **移除 URL/path 去重** — source_media 导入仅按 `source_media_id` 判重
3. **导入后归一化** — 所有 main 重新排序编号，首张 = 封面
4. **返回新增字段** — `images_fixed_role` 计数

#### 前端 (templates/ozon/listing_review.html)
- 导入结果提示增加 `images_fixed_role` 显示："修正主图角色 N 张"

### 验证
- `python app.py` 启动无报错 ✓
- `node --check` JS 语法检查通过 ✓

## 新增功能（2026-07-01 offer_id 随机生成）

### 需求
SKU/价格 tab 中 offer_id 增加"随机生成"和"批量生成"功能，格式 `品牌-日期-随机码`，生成后可编辑、可保存、发布前可校验。

### 改动内容

#### 前端 (templates/ozon/listing_review.html)

**模板**：
- SKU 表格顶部增加「🎲 批量生成 offer_id」按钮
- 每个 offer_id 输入框右侧增加「🎲」单行随机生成按钮（input-group 包裹）

**JS 函数**：
- `generateOfferId(prefix)` — 生成 `PREFIX-YYYYMMDD-XXXXXX` 格式（排除易混淆字符 O/0/I/1）
- `getOfferPrefix()` — 从源属性面板提取品牌 → 标题检测已知品牌 → 兜底 `OZON`
- `generateUniqueOfferId(prefix, excludeInput)` — 确保草稿内不重复（最多 20 次重试）
- `getExistingOfferIds(excludeInput)` — 收集当前页面已有 offer_id
- 单个生成：已有值时先 confirm 确认覆盖
- 批量生成：仅填充空值，不覆盖已有值

#### 后端 (blueprints/ozon.py) — `listing_validate()`

增强 offer_id 校验（3 项阻断检测）：
1. **格式校验** — `^[A-Za-z0-9_-]{3,80}$`
2. **草稿内去重** — 同一 draft 下 offer_id 不重复
3. **跨草稿去重** — 同用户/同账号下，排除当前 draft，查询 `OzonDraft.ozon_offer_id` 是否冲突

### 验证
- `python app.py` 启动无报错 ✓
- `node --check` JS 语法检查通过 ✓

## 重构（2026-07-01 审核通过 = 先保存再校验，不刷新）

### 问题
- 独立"校验"按钮触发 `location.href` 重定向 → 用户刚填的数据丢失
- "审核通过"是 form POST → 同样刷新页面

### 改动

#### 前端按钮
- 删除顶部栏 "🔍 发布前校验" + 右栏 "🔍 校验" + form POST "审核通过"
- 改为 2 个 JS 按钮 "✅ 审核通过"（顶部 + 右栏各一）
- 右栏校验区改为 `<div id="validationChecks">` 动态渲染

#### 前端 JS
- `collectSkuData()` / `collectPricingData()` / `collectContentData()` / `collectFullDraftPayload()` — 收集页面数据
- `saveAllDraftData()` → `POST /api/draft/<id>/save-all`
- `approveDraft()` — 保存 → 校验 → 改状态，全程不刷新
- `renderValidationResult(validation)` — 动态更新校验列表
- `publishToOzon()` — 先 save-all → approve → 通过才跳转发布
- SKU 行/输入框加了 class (`sku-row`, `offer-id-input`, `sku-quantity-input` 等) 和 id (`ozonCurrency`, `ozonListingPrice`, `priceManualConfirmed`)

#### 后端新增 API
- `POST /api/draft/<id>/save-all` — 一次性保存 content/pricing/SKU/media/rich_content，返回 JSON
- `POST /api/draft/<id>/approve` — 执行完整校验 → 通过则 `status='approved'`，返回 JSON（不重定向）

### 验证
- `python app.py` 启动无报错 ✓
- `node --check` JS 语法检查通过 ✓

## 修复记录（2026-07-01 属性归一化 + 发布链路全面修复）

### 问题
- `listing_publish` 中 `attributes_json` 假设为 list 格式，但实际存储格式有三种混用（list / wrapper dict / 平面 map），导致 `AttributeError: 'str' object has no attribute 'get'`
- `_build_product_data` 只接受 list 格式属性，dict 格式被静默丢弃
- offer_id 硬编码 `f"draft_{draft.id}"`，忽略用户填写值
- 价格字段 `null`，不取用户确认的刊登价
- 图片从旧 `OzonImageSlot` 取，媒体池主图被忽略
- 保存接口属性格式不一致：save_attributes 存 `{'attributes': {}}` wrapper

### 修复内容

#### 新增属性归一化层 (blueprints/ozon.py)
在 `_record_publish_failure` 之后添加 6 个 helper 函数：
1. **`_safe_json_loads(raw, default)`** — 安全 JSON 解析，失败返回 default
2. **`_load_draft_attributes_map(draft)`** — 统一读取 attributes_json，兼容 list / wrapper dict / 平面 map 三种历史格式，固定返回 `{"aid": {"value": "...", "value_id": "..."}}`
3. **`_is_draft_attr_filled(value)`** — 判断属性值是否已填写（支持 dict/list/str）
4. **`_filled_draft_attribute_ids(draft)`** — 返回已填写的 attribute_id 集合
5. **`_build_ozon_attribute_list(draft)`** — 将草稿属性转成 OZON API 的 attributes list 格式

#### 修复 `listing_publish()` — 必填属性校验
- `json.loads(draft.attributes_json or '[]')` → `_filled_draft_attribute_ids(draft)`
- 不再假设 list 格式，不再对字符串 key 调 `.get()`
- 缺失时返回具体属性中文名 + 数量

#### 重写 `_build_product_data()`
- **offer_id**：从第一个 SKU 的 `sku.offer_id` 取，缺失则 raise ValueError
- **价格**：从 `pricing_json.listing_price` + `listing_currency` 取，缺失则 raise ValueError
- **图片**：从 `media_json` 媒体池取 `selected + role=main`，按 `sort_order` 排序，取 `ozon_url / public_url / url`
- **属性**：使用 `_build_ozon_attribute_list(draft)` 统一转换
- 增加 `currency_code` 字段
- `_build_product_data` 抛 `ValueError` 时，`listing_publish` 返回 400 JSON 而非 500

#### 统一保存接口属性格式
- **`api_draft_save_attributes()`**：使用 `_load_draft_attributes_map` 合并，写入扁平 map
- **`api_draft_fill_from_source()`**：同上，使用归一化层读写
- 不再包装 `{'attributes': {}}`，统一为平面 map

#### 更新 `listing_review()` 属性加载
- `json.loads(draft.attributes_json or '{}')` + wrapper detection → `_load_draft_attributes_map(draft)`
- 前端已有 `if(saved.attributes) saved=saved.attributes` 兼容逻辑

#### 发布接口兜底异常处理
- `_build_product_data(draft)` 异常 → 捕获 ValueError → 返回 400 JSON
- 最后 `except Exception` 增加 `current_app.logger.exception()`

#### API 文档同步
- `services/ozon_api.py` `import_product` docstring：`category_id` → `description_category_id` + `type_id`

### 影响文件
| 文件 | 改动 |
|------|------|
| `blueprints/ozon.py` | 新增 6 个 helper + 重写 `_build_product_data` + 修复 `listing_publish` 校验 + 统一 `save_attributes`/`fill_from_source` + 更新 `listing_review` |
| `services/ozon_api.py` | 更新 `import_product` docstring |
| `templates/ozon/listing_review.html` | 新增 `safeJson()` + 关键 fetch 调用改用 `safeJson()` |

### 验证
- `python app.py` 启动无报错 ✓
- JS 语法检查通过（2 个主脚本） ✓
- 属性归一化层兼容 3 种历史格式 ✓
- 保存接口写入格式统一为平面 map ✓
