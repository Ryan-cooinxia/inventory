# 产品母图自动识别与目标级抠图实施方案

> 本文可直接交给 Claude Code 执行。

## 开始前指令

请先阅读：

```text
G:\inventory\AGENTS.md
```

然后检查当前工作区、最近提交以及以下文件：

```text
services/product_cutout.py
services/vision_tool.py
templates/ozon/product_cutout.html
blueprints/ozon.py
models.py
app.py
```

本轮只实施本文的 **P0：自动识别商品主体闭环**。

不要同时开发：

- 自动图片质量视觉审核
- AI 电商生图
- A+ 详情页规划
- 批量 SKU 母图
- 生成式修图

不得覆盖或破坏现有：

- Seedream 参考图生图
- 图片候选与评分
- 产品母图历史记录
- 多用户数据隔离

---

# 一、目标

将当前产品母图流程从：

```text
整张图片 → rembg → 错误地保留商品、文字、Logo 和色块
```

升级为：

```text
原始商品图片
→ 视觉模型自动识别真正出售的商品
→ 自动生成主商品、配件及排除区域
→ 用户确认或微调
→ 目标级分割
→ 原图 RGB + 分割蒙版
→ 透明产品母图
→ 质量门禁
→ 人工批准
```

核心原则：

> AI 可以判断商品在哪里，但正式产品母图的商品像素必须来自原图，禁止生成式模型重绘产品。

---

# 二、当前问题与根因

当前测试截图显示：

```text
目标数量：0
分割方式：rembg_full
质量评分：90
```

结果仍保留：

- 左侧大型 `XPro C`
- 紫色相机版本标签
- `兼容广泛`
- `无线引闪`
- `2.4G`
- `TTL 自动闪光`
- 底部外部 Godox 宣传 Logo
- 红色、粉色背景和装饰

## 2.1 实际走了错误路径

当前页面提供：

```text
目标抠图 → rembg_crop
快速抠图 → rembg_full
```

测试结果为 `rembg_full`，说明系统对整张广告图执行了显著性前景分割。

rembg 不理解：

- 哪个前景是真正商品
- 哪些文字是广告
- 哪个 Logo 位于商品外部
- 哪些物体是真实配件

## 2.2 页面显示坐标可能被当作原图坐标

前端框选基于页面渲染尺寸，但后端需要原图像素坐标。

必须通过：

```javascript
scaleX = image.naturalWidth / renderedWidth
scaleY = image.naturalHeight / renderedHeight
```

转换后再提交。

## 2.3 排除目标没有参与最终蒙版

当前后端主要处理：

```python
keep_targets = [t for t in targets if t.get("keep")]
```

`keep=False` 的广告文字、Logo、人物和装饰区域没有从最终蒙版扣除。

## 2.4 质量评分计算错误

当前代码将 mask 转成 0/1：

```python
binary = (mask_arr > 30).astype(np.uint8)
```

随后却再次除以 255：

```python
fill = region.mean() / 255
outside_residual = outside_pixels.mean() / 255
```

这是错误的。0/1 数组的 `mean()` 已经是 0–1。

## 2.5 无目标框时质量指标被虚构

`rembg_full` 没有目标框时：

- `outside_residual` 默认 0
- `completeness` 默认 1
- 基础分默认 90

因此明显错误的广告图仍获得 90 分。

## 2.6 批准接口没有强制质量门禁

当前错误结果仍可点击“确认母图”。

批准接口必须在服务端检查：

- 是否有主商品目标
- 是否质量通过
- 是否保留原图像素
- 是否存在框外残留
- 是否使用了不适合复杂图片的 `rembg_full`

---

# 三、目标架构

## 3.1 职责分离

### 视觉模型

负责：

- 判断真正出售的主商品
- 识别真实配件候选
- 识别商品外部广告文字
- 识别商品外部宣传 Logo
- 识别人物、手部、其他商品和装饰
- 返回原图像素坐标

不负责：

- 修改图片
- 删除背景
- 重绘产品
- 输出最终母图

### 分割模型

负责：

- 根据 bbox 或点提示生成 mask
- 精确识别商品边缘

不负责：

- 判断商品语义
- 修改产品 RGB

### 后端合成

正式产品母图必须通过：

```python
result = original.convert("RGBA")
result.putalpha(cleaned_mask)
```

商品内部 RGB 必须保持原图不变。

---

# 四、P0 实施范围

## P0-1：新增商品主体自动识别服务

新增：

```text
services/product_subject_detector.py
```

接口：

```python
def detect_product_subject(user, media):
    """
    返回：
    {
        "image_width": 800,
        "image_height": 800,
        "main_product": {
            "label": "Godox XPro-C 无线引闪器",
            "bbox": [355, 75, 790, 760],
            "confidence": 0.99,
            "description": "右侧黑色带LCD屏幕的无线引闪器"
        },
        "accessories": [],
        "exclude_regions": [
            {
                "type": "advertising_text",
                "bbox": [25, 45, 350, 650],
                "confidence": 0.98,
                "description": "商品外部宣传文字"
            },
            {
                "type": "external_logo",
                "bbox": [15, 670, 410, 798],
                "confidence": 0.96,
                "description": "商品外部 Godox 宣传Logo"
            }
        ],
        "uncertain": [],
        "warnings": [],
        "requires_confirmation": True
    }
    """
```

优先复用项目现有：

```text
VisionModelConfig
```

以及已有视觉模型 API 调用逻辑。

不得复制出另一套 API Key 或模型配置体系。

## P0-2：视觉识别 Prompt

使用类似以下 Prompt：

```text
你是电商商品主体检测器。

任务：
结合商品标题、类目和 SKU 信息，识别图片中真正出售的主商品、真实附属配件，以及不应进入透明产品母图的区域。

必须区分：
1. 主商品
2. 真实附属配件
3. 其他商品
4. 人物或手部
5. 商品外部广告文字
6. 商品外部宣传 Logo 或平台水印
7. 背景及装饰色块

规则：
- 商品屏幕、按钮、旋钮、接口、型号和印刷在商品本体上的文字属于商品结构，不能排除。
- 商品外部的大型标题、参数、促销标签和宣传图形必须排除。
- 不要把不确定的小物件自动认定为配件。
- 只有能够确认属于包装内容的实物才能标记为配件。
- bbox 必须使用原图像素坐标。
- 如果不能确认主商品，返回 uncertain，不得猜测。
- 只输出 JSON，不输出 Markdown。
```

JSON Schema：

```json
{
  "image_width": 0,
  "image_height": 0,
  "main_product": {
    "label": "",
    "bbox": [0, 0, 0, 0],
    "confidence": 0.0,
    "description": ""
  },
  "accessories": [],
  "exclude_regions": [],
  "uncertain": [],
  "warnings": [],
  "requires_confirmation": true
}
```

## P0-3：新增自动识别路由

新增：

```text
POST /ozon/product-cutout/<media_id>/detect-subject
```

要求：

```python
@login_required
```

查询必须过滤：

```python
OzonSourceMedia.user == current_user
```

路由职责：

1. 获取原图。
2. 获取商品标题、类目和 SKU。
3. 调用视觉模型。
4. 校验返回 JSON。
5. 返回识别框和置信度。
6. 保存原始响应和解析结果。

该路由不得立即执行抠图。

API Key 不得写入日志或数据库快照。

---

# 五、数据结构

推荐新增独立模型，以保留多次识别历史：

```python
class OzonProductSubjectDetection(BaseModel):
    user = ForeignKeyField(User, backref='product_subject_detections')
    source = ForeignKeyField(OzonSource, backref='subject_detections')
    source_media = ForeignKeyField(
        OzonSourceMedia,
        backref='subject_detections'
    )

    provider = CharField(max_length=50)
    model_name = CharField(max_length=100)

    image_width = IntegerField()
    image_height = IntegerField()

    detection_json = TextField()
    raw_response_json = TextField(null=True)

    main_product_confidence = FloatField(null=True)
    status = CharField(max_length=20, default='detected')
    # detected / confirmed / rejected / failed

    error_message = TextField(null=True)

    created_at = DateTimeField(default=datetime.datetime.now)
    confirmed_at = DateTimeField(null=True)

    class Meta:
        indexes = (
            (('user', 'source_media'), False),
            (('user', 'status'), False),
        )
```

在 `app.py` 注册表并提供幂等迁移。

如果不增加独立表，也可以先扩展 `OzonProductCutout`，但必须能够保留识别结果和模型信息：

```python
subject_detection_json = TextField(null=True)
subject_detection_provider = CharField(max_length=50, null=True)
subject_detection_model = CharField(max_length=100, null=True)
subject_detection_confidence = FloatField(null=True)
subject_detected_at = DateTimeField(null=True)
```

---

# 六、前端交互

修改：

```text
templates/ozon/product_cutout.html
```

## 6.1 增加自动识别按钮

每张推荐原图增加：

```text
🔍 自动识别商品
```

点击后：

1. 显示“识别中”。
2. 调用 `detect-subject`。
3. 自动绘制识别结果。
4. 显示商品名称和置信度。
5. 停留在用户确认状态。

## 6.2 目标框颜色

```text
绿色：主商品
蓝色：真实配件
红色：广告文字、外部 Logo、人物、其他商品和装饰
```

## 6.3 用户可执行操作

- 移动目标框
- 缩放目标框
- 删除目标框
- 修改目标类型
- 手工新增目标框
- 设置主商品
- 将候选配件设为保留或排除
- 点击“确认并抠图”

## 6.4 置信度显示

```text
主商品：Godox XPro-C 引闪器
置信度：99%
配件：0
排除区域：3
需要人工确认
```

门槛：

```python
AUTO_DRAW_CONFIDENCE = 0.90
MIN_DETECTION_CONFIDENCE = 0.70
```

规则：

- `>= 0.90`：自动画框，但仍需确认。
- `0.70–0.89`：画黄色警告框，要求重点检查。
- `< 0.70`：不自动执行抠图，提示手工框选。
- 没有主商品：禁止目标抠图。
- 多个主商品候选无法判断：要求用户选择。

---

# 七、坐标系统修复

视觉模型返回原图坐标。

前端显示时：

```javascript
displayX = originalX * renderedWidth / naturalWidth;
displayY = originalY * renderedHeight / naturalHeight;
```

用户编辑后提交时：

```javascript
const rect = img.getBoundingClientRect();
const scaleX = img.naturalWidth / rect.width;
const scaleY = img.naturalHeight / rect.height;

const originalBBox = [
  Math.round(displayX1 * scaleX),
  Math.round(displayY1 * scaleY),
  Math.round(displayX2 * scaleX),
  Math.round(displayY2 * scaleY)
];
```

Target 结构：

```json
{
  "type": "main_product",
  "keep": true,
  "bbox": [355, 75, 790, 760],
  "display_bbox": [160, 34, 350, 342],
  "image_width": 800,
  "image_height": 800,
  "label": "Godox XPro-C 引闪器",
  "confidence": 0.99,
  "source": "vision_model"
}
```

服务端必须校验：

- 图片宽高与实际原图一致
- bbox 不超出图片
- `x2 > x1`
- `y2 > y1`
- 主商品框面积不小于原图 5%
- 目标数量合理

无效时返回明确错误，禁止回退 `rembg_full`。

---

# 八、目标级分割

## 8.1 P0 使用 rembg_crop

当前阶段继续复用 `rembg_crop`：

```text
商品框裁剪
→ rembg only_mask
→ mask 放回原图坐标
→ bbox 外强制透明
```

核心要求：

```python
local_mask = remove(
    crop.convert("RGB"),
    only_mask=True,
    post_process_mask=True
)
```

不得使用 rembg 输出的 RGB 产品图。

## 8.2 排除框必须生效

最终蒙版：

```python
final_mask = union(all_keep_masks)

for remove_target in remove_targets:
    final_mask[remove_bbox] = 0
```

处理顺序：

1. 合并主商品和配件 mask。
2. 扣除广告文字、外部 Logo、人物和其他商品区域。
3. 去除孤立噪点。
4. 填补商品内部小孔洞。
5. 轻微闭运算。
6. 最多 1 像素边缘收缩。
7. 最多 0.5–1 像素羽化。

不得腐蚀：

- 按钮
- 旋钮
- 热靴
- 天线
- 线缆
- 透明部件
- 细小结构

## 8.3 禁止复杂图片使用 rembg_full

满足任一条件时，服务端拒绝 `rembg_full`：

- 图片包含文字
- 图片包含复杂广告版式
- 图片背景复杂
- 图片角色不是确认过的纯白底主图
- 用户处于正式产品母图流程

返回：

```text
该图片包含文字或复杂背景，请先自动识别或手工框选商品，再使用目标抠图。
```

不要静默降级。

前端将按钮文案改为：

```text
⚡ 简单白底图抠图
```

复杂图片上禁用该按钮。

---

# 九、原图像素合成

正式结果：

```python
original_rgba = original.convert("RGBA")
original_rgba.putalpha(cleaned_mask)
```

必须保存：

- 原始 mask
- 清理后 mask
- 原尺寸透明 PNG
- 可选紧边裁剪透明 PNG
- 棋盘格预览图

禁止：

- AI 图片编辑
- 生成式补全
- 商品重绘
- 全图锐化
- 全图美化
- 改变屏幕文字
- 改变按钮、旋钮或接口

---

# 十、质量检查修复

## 10.1 修复 0/1 数组计算

错误：

```python
fill = region.mean() / 255
outside_residual = outside_pixels.mean() / 255
```

正确：

```python
fill = float(region.mean())
outside_residual = float(outside_pixels.mean())
```

因为 `binary` 已为 0/1。

增加测试：

```text
全背景 → 0.0
全前景 → 1.0
一半前景 → 约 0.5
```

## 10.2 rembg_full 不得虚构指标

没有目标框时：

```python
outside_residual_score = None
completeness_score = None
```

页面显示：

```text
框外残留：未检测
完整性：未检测
```

不得显示：

```text
框外残留 0%
完整 100%
```

## 10.3 正式质量结果

返回：

```json
{
  "score": 86,
  "pass": true,
  "target_count": 1,
  "keep_target_count": 1,
  "remove_target_count": 3,
  "outside_foreground_ratio": 0.003,
  "completeness_score": 0.97,
  "pixel_preserved": true,
  "opaque_pixel_difference": 0,
  "edge_quality_score": 0.91,
  "warnings": []
}
```

正式通过条件：

```python
pass = (
    target_count >= 1
    and outside_foreground_ratio < 0.01
    and completeness_score >= 0.90
    and pixel_preserved is True
    and opaque_pixel_difference == 0
    and not high_risk_warnings
)
```

---

# 十一、批准接口强制门禁

修改：

```text
POST /ozon/product-cutout/<cutout_id>/approve
```

批准前必须读取 `quality_json`。

以下情况拒绝批准：

- 没有主商品目标
- `target_count == 0`
- `quality.pass != True`
- `pixel_preserved != True`
- `outside_foreground_ratio >= 0.01`
- 商品完整性不足
- 存在高风险 warning
- `rembg_full` 用于复杂广告图
- 自动识别结果尚未由用户确认

返回具体错误：

```json
{
  "ok": false,
  "error": "该结果仍包含商品框外广告内容，请确认自动识别框后重新执行目标抠图"
}
```

前端失败结果：

- 红色边框
- 显示失败原因
- 隐藏“确认母图”
- 保留“重新识别”和“重新抠图”

服务端门禁必须存在，不能只隐藏按钮。

---

# 十二、XPro-C 当前案例验收

测试原图：

```text
01_main.jpeg
```

自动识别预期：

```text
主商品：右侧 Godox XPro-C 引闪器
配件：无
```

主商品框约：

```text
[355, 75, 790, 760]
```

排除内容：

- 左侧巨大 `XPro C`
- 紫色相机版本标签
- `兼容广泛`
- `无线引闪`
- `2.4G`
- `TTL 自动闪光`
- 底部外部 Godox Logo
- 红色、粉色背景和装饰

最终透明图必须：

- 只保留右侧引闪器
- 保留 LCD 屏幕
- 保留 `CH1`
- 保留 `A`、`M`
- 保留 `1/64`
- 保留 `+0.3`
- 保留 `Zoom 24 mm`
- 保留底部菜单
- 保留五个侧边按钮
- 保留四个屏幕下方按钮
- 保留 `MODE`、`RST`、`MENU`、`TCM`
- 保留 `SET` 旋钮
- 保留热靴及机身边缘
- 产品 RGB 来自原图
- 外部广告残留低于 1%
- 通过质量门禁后才能批准

新结果必须显示：

```text
检测方式：视觉模型
分割方式：rembg_crop
保留目标：1
排除目标：至少 2
像素保持：是
质量通过：是
```

---

# 十三、自动化测试

至少增加以下测试：

1. 页面渲染尺寸与原图尺寸不同时，bbox 坐标正确转换。
2. 无主商品目标时，目标抠图返回错误。
3. 无 targets 的 `rembg_crop` 不允许执行。
4. 复杂广告图的 `rembg_full` 不允许通过。
5. 排除 bbox 会从最终 mask 中扣除。
6. 0/1 mask 比例计算正确。
7. `target_count=0` 的结果不能批准。
8. `quality.pass=False` 的结果不能批准。
9. 产品不透明区域 RGB 与原图完全一致。
10. 视觉模型返回非法 bbox 时被拒绝。
11. 视觉模型无法确认商品时转为人工框选，不转 `rembg_full`。
12. 所有查询均按 `current_user` 隔离。

---

# 十四、实施顺序

## P0：本轮完成

1. 新增商品主体识别服务。
2. 新增识别路由。
3. 复用现有视觉模型配置。
4. 页面增加“自动识别商品”。
5. 自动绘制主商品、配件和排除框。
6. 修复显示坐标与原图坐标转换。
7. 用户确认后执行 `rembg_crop`。
8. 排除框参与最终蒙版。
9. 禁止复杂图片使用 `rembg_full`。
10. 修复质量评分。
11. 增加批准接口强制门禁。
12. 使用 XPro-C 原图完成验收。

## P1：P0 验收后再做

1. 接入 SAM 2 box prompt。
2. 支持正向点和负向点。
3. 支持蒙版擦除与恢复。
4. 增加边缘背景色去污染。

## P2：后续

1. 多配件实例分割。
2. SKU 级产品母图。
3. 批量母图准备。
4. 母图与 Seedream/Composite 生图联动。

---

# 十五、开发与验证要求

1. 不要删除已有母图历史。
2. 数据库迁移必须幂等。
3. 所有路由使用 `@login_required`。
4. 所有查询过滤 `user == current_user`。
5. 不记录明文 API Key。
6. 不使用生成式图片模型输出正式母图。
7. 不允许失败后静默回退。
8. 错误提示必须明确。

每次修改后执行：

```text
Python 编译检查
Jinja 模板解析
相关自动化测试
git diff --check
app.py 启动检查
```

启动命令以当前实际可用 Python 环境为准；如果仓库 `.venv` 已失效，先修复环境，不得虚报启动验证成功。

大改动完成后创建：

```text
G:\inventory\项目改动日志\2026-06-25_产品母图自动识别与目标级抠图.md
```

---

# 十六、完成汇报格式

完成 P0 后，请按以下格式汇报：

```text
1. 修改文件清单
2. 数据库迁移内容
3. 自动主体识别使用的模型和 Prompt
4. XPro-C 识别 JSON
5. 实际主商品 bbox
6. 保留与排除目标数量
7. 分割 provider
8. 质量检查 JSON
9. 透明母图保存路径
10. 自动化测试结果
11. 应用启动验证结果
12. 仍未完成或存在风险的内容
```

不要在 P0 验收前开始 P1。

