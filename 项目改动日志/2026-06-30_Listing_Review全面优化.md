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
