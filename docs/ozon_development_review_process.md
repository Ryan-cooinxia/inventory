# OZON 模块开发审核流程

版本：v0.2
日期：2026-06-14
适用范围：OZON 自动刊登工具从 PRD、原型、数据结构、接口、页面到发布联调的全部开发过程

关联文档：
- `docs/ozon_source_to_listing_adaptation_plan.md` — 源商品到 Listing 适配层方案
- `docs/ozon_category_attribute_acquisition_plan.md` — 类目属性字典与字段缺口检查
- `docs/ozon_vision_tool_model_plan.md` — 图片识别工具模型接入方案

## 1. 目标

本项目采用“审核门禁开发方式”。所有关键开发动作都必须先形成可审核产物，用户确认后再进入下一阶段。

特别要求：

- 交互页面必须先做独立 HTML 原型。
- 未审核通过的原型不能进入真实 Flask 模板开发。
- 数据库结构、OZON API 发布逻辑、AI 自动生成规则、价格计算规则必须单独说明。
- 每次开发完成后必须记录审核状态。
- 用户确认后才能进入下一阶段。

## 2. 审核门禁总原则

1. 先方案，后实现。
2. 先原型，后页面。
3. 先测试店，后正式店。
4. 先人工审核，后批量自动化。
5. 先记录变更，后开始修改。
6. 未确认的关键字段、流程和规则不得默认实现为最终逻辑。

## 3. 阶段划分

### 阶段 0：需求确认和资料整理

产物：

- PRD 文档
- OZON API 实测清单
- 商品采集 JSON 标准
- 开发审核流程文档

审核重点：

- 功能边界是否准确
- 一期和二期是否拆分合理
- 是否符合“人工审核后发布”的原则

通过标准：

- 用户确认 PRD 和数据标准可作为开发基准。

### 阶段 1：信息架构和菜单结构

产物：

- 页面列表
- 菜单结构
- 页面跳转关系
- 每个页面的主要操作说明

审核重点：

- 菜单是否符合日常操作习惯
- 是否覆盖采集、加工、审核、发布、回写
- 是否有多余页面或遗漏页面

通过标准：

- 用户确认页面列表和跳转关系。

### 阶段 2：低保真页面原型

产物：

- 独立 HTML 原型文件
- 每个页面的核心字段和按钮
- 页面状态说明

原型目录：

```text
docs/prototypes/
```

建议文件：

```text
ozon_dashboard.v1.html
ozon_sources.v1.html
ozon_adaptation_workspace.v1.html
ozon_fact_library.v1.html
ozon_category_attributes.v1.html
ozon_field_gap_check.v1.html
ozon_vision_model_config.v1.html
ozon_image_analysis_result.v1.html
ozon_image_plan.v1.html
ozon_listing_review.v1.html
ozon_accounts.v1.html
```

审核重点：

- 页面是否能支撑业务流程（采集→适配→草稿→发布）
- 适配工作台三栏布局是否清晰
- 商品事实字段是否完整
- 字段缺口检查是否直观
- 视觉模型配置和识别结果是否易用
- 操作按钮是否清晰
- 审核入口是否明显
- 是否能看出低置信度和待确认字段

通过标准：

- 用户确认页面布局和核心交互。

### 阶段 3：高保真交互原型

产物：

- 接近最终页面效果的 HTML 原型
- 表格、表单、状态标签、校验提示、弹窗或侧边栏交互
- 页面截图

审核重点：

- 页面是否易用
- 审核流程是否顺手
- 错误提示是否明确
- 是否避免信息过载
- 是否符合现有系统风格

通过标准：

- 用户确认某一版原型为 `approved`。

命名规则：

```text
ozon_listing_review.v1.html
ozon_listing_review.v2.html
ozon_listing_review.approved.html
```

### 阶段 4：数据库和后端接口设计

产物：

- 数据表设计
- 字段说明
- 状态流转
- 后端路由清单
- API 输入输出结构

审核重点：

- 是否支持采集 JSON 标准
- 是否能保留源数据证据
- 是否能记录 AI 产物和人工审核结果
- 是否能记录 OZON 发布任务和失败原因

通过标准：

- 用户确认数据结构和接口方向。

### 阶段 5：前端页面实现

产物：

- 从 `approved` 原型转成 Flask/Jinja 页面
- 页面可在本地系统打开
- 表单和列表可基础操作

审核重点：

- 实现是否忠于已确认原型
- 是否破坏已有系统页面
- 是否有明显交互问题
- 移动端是否能基本使用

通过标准：

- 用户确认页面实现可进入联调。

### 阶段 6：OZON API 联调

产物：

- 测试店接口连通
- 类目属性拉取
- 创建商品测试
- 图片上传测试
- 价格库存更新测试
- 发布任务日志

审核重点：

- 是否只使用测试店
- 是否完整记录请求和响应
- 失败原因是否可读
- 是否没有误发布到正式店

通过标准：

- 用户确认测试店发布链路通过。

### 阶段 7：正式店发布准备

产物：

- 正式店发布检查清单
- 回滚方案
- 发布权限确认
- 第一批正式商品清单

审核重点：

- 是否已通过测试店验证
- 是否已人工确认商品内容
- 是否有失败回滚方案

通过标准：

- 用户明确确认可以对正式店执行发布。

## 4. 每次开发前必须说明

每次开始动代码前，需要先给出：

- 本次目标
- 本次要改哪些文件
- 是否涉及数据库
- 是否涉及 OZON API
- 是否涉及 AI 生成规则
- 是否已有 approved 原型
- 是否影响现有订单、库存、财务功能
- 完成后如何验证

用户要求暂停或转为文档时，必须停止代码实现。

## 5. 每次开发后必须交付

每次完成后，需要提供：

- 完成了什么
- 修改了哪些文件
- 是否新增文档或原型
- 如何验证
- 当前风险
- 下一步建议

如果创建了页面，必须提供：

- 页面路径
- 页面截图或本地访问方式
- 与原型的差异说明

## 6. 原型审核规则

### 6.1 原型必须独立

页面原型先放在：

```text
docs/prototypes/
```

不得一开始直接写入：

```text
templates/
```

### 6.2 原型版本必须保留

每次大改保留新版本：

```text
page_name.v1.html
page_name.v2.html
page_name.v3.html
page_name.approved.html
```

### 6.3 approved 原型的约束

一旦用户确认某个原型为 approved：

- 后续真实页面应以 approved 原型为准。
- 不得随意改变核心流程。
- 如需改变，需要重新提交变更说明。

## 7. 数据库变更审核规则

数据库变更必须先列出：

- 新增表
- 新增字段
- 字段类型
- 是否可为空
- 默认值
- 与用户隔离相关的 `user_id`
- 迁移脚本
- 回滚方式

未经确认，不执行数据库结构变更。

## 8. OZON API 操作审核规则

### 8.1 只读接口

读取类目、商品列表、属性字典、状态等接口，可在用户确认目标后执行。

### 8.2 写入接口

创建商品、更新图片、更新价格、更新库存、归档商品等写操作必须满足：

- 使用测试店。
- 展示请求摘要。
- 用户确认后执行。
- 记录请求和响应。

正式店写入必须单独确认。

## 9. AI 生成规则审核

AI 相关规则包括：

- 标题生成
- 卖点生成
- 描述生成
- 类目建议
- 属性抽取
- 图片提示词
- 图片生成
- 翻译

这些规则必须可追溯：

- 使用哪个提示词
- 输入了哪些源数据
- 输出了哪些字段
- 置信度是多少
- 哪些字段需要人工确认

AI 不得覆盖人工确认字段。

## 10. 审核状态定义

```text
pending_review  待审核
approved        已通过
needs_changes   需修改
blocked         阻塞
superseded      已被新版本替代
```

## 11. 审核记录位置

所有审核记录写入：

```text
docs/ozon_review_log.md
```

每个阶段至少一条记录。

## 12. 与现有文档关系

当前 OZON 模块文档：

```text
docs/ozon_listing_prd.md
docs/ozon_api_and_collection_contract.md
docs/ozon_development_review_process.md
docs/ozon_review_log.md
docs/ozon_phase1_architecture.md
docs/ozon_phase4_database_backend.md
docs/ozon_source_to_listing_adaptation_plan.md
docs/ozon_category_attribute_acquisition_plan.md
docs/ozon_vision_tool_model_plan.md
docs/prototypes/
```

阅读顺序：

1. `ozon_listing_prd.md`
2. `ozon_api_and_collection_contract.md`
3. `ozon_phase1_architecture.md`
4. `ozon_source_to_listing_adaptation_plan.md`
5. `ozon_category_attribute_acquisition_plan.md`
6. `ozon_vision_tool_model_plan.md`
7. `ozon_development_review_process.md`
8. `ozon_review_log.md`
9. `docs/prototypes/`

## 13. 下一步建议

下一步先进入阶段 1：

- 输出 OZON 模块菜单结构。
- 输出页面跳转图。
- 明确第一批原型页面。
- 用户审核通过后，再开始低保真 HTML 原型。
