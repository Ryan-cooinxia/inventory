# OZON 页面原型目录

本目录用于存放 OZON 自动刊登模块的独立 HTML 页面原型。

规则：

- 原型先在本目录创建，不直接写入 `templates/`。
- 每个页面保留版本。
- 用户确认后复制一份 `.approved.html`。
- 后续真实系统页面必须以 `.approved.html` 为准。

建议命名：

```text
ozon_dashboard.v1.html
ozon_sources.v1.html
ozon_processing.v1.html
ozon_image_plan.v1.html
ozon_listing_review.v1.html
ozon_accounts.v1.html

ozon_dashboard.approved.html
ozon_sources.approved.html
ozon_processing.approved.html
ozon_image_plan.approved.html
ozon_listing_review.approved.html
ozon_accounts.approved.html
```

第一批建议原型：

1. OZON 工作台
2. 商品采集列表
3. 商品加工页
4. 图片方案页
5. 刊登草稿审核页
6. 平台接口配置页
