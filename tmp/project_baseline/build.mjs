import fs from "node:fs/promises";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const outputDir = "G:/inventory/outputs/project-baseline-20260625";
await fs.mkdir(outputDir, { recursive: true });

const wb = Workbook.create();
const names = [
  "00_项目总览","01_项目概述","02_核心流程","03_功能清单","04_产品规则",
  "05_系统架构","06_数据字典","07_运行手册","08_验收矩阵","09_风险清单",
  "10_版本路线图","11_证据口径"
];
for (const n of names) wb.worksheets.add(n);

const C = {
  navy:"#16324F", teal:"#0F766E", green:"#15803D", lightGreen:"#DCFCE7",
  blue:"#2563EB", lightBlue:"#DBEAFE", amber:"#D97706", lightAmber:"#FEF3C7",
  red:"#DC2626", lightRed:"#FEE2E2", gray:"#64748B", lightGray:"#F1F5F9",
  border:"#CBD5E1", white:"#FFFFFF", ink:"#172033", purple:"#7C3AED"
};
const baseDate = new Date("2026-06-25T00:00:00");

function title(sheet, text, subtitle, lastCol="H") {
  sheet.showGridLines = false;
  sheet.getRange(`A1:${lastCol}1`).merge();
  sheet.getRange("A1").values = [[text]];
  sheet.getRange(`A1:${lastCol}1`).format = {
    fill:C.navy, font:{bold:true,color:C.white,size:18}, rowHeight:34,
    verticalAlignment:"center"
  };
  sheet.getRange(`A2:${lastCol}2`).merge();
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${lastCol}2`).format = {
    fill:"#E8EEF5", font:{color:C.gray,italic:true,size:10}, rowHeight:26,
    verticalAlignment:"center", wrapText:true
  };
}
function section(sheet, row, text, lastCol="H") {
  sheet.getRange(`A${row}:${lastCol}${row}`).merge();
  sheet.getRange(`A${row}`).values = [[text]];
  sheet.getRange(`A${row}:${lastCol}${row}`).format = {
    fill:C.teal, font:{bold:true,color:C.white,size:12}, rowHeight:24,
    verticalAlignment:"center"
  };
}
function writeTable(sheet, startRow, headers, rows, widths, tableName) {
  const endCol = col(headers.length);
  const endRow = startRow + rows.length;
  sheet.getRange(`A${startRow}:${endCol}${endRow}`).values = [headers, ...rows];
  sheet.getRange(`A${startRow}:${endCol}${startRow}`).format = {
    fill:C.navy, font:{bold:true,color:C.white}, rowHeight:28,
    verticalAlignment:"center", wrapText:true,
    borders:{preset:"outside",style:"thin",color:C.border}
  };
  if (rows.length) {
    sheet.getRange(`A${startRow}:${endCol}${endRow}`).format.borders =
      {insideHorizontal:{style:"thin",color:"#E2E8F0"},bottom:{style:"thin",color:C.border}};
    sheet.getRange(`A${startRow}:${endCol}${endRow}`).format.wrapText = true;
    sheet.getRange(`A${startRow}:${endCol}${endRow}`).format.verticalAlignment = "top";
  }
  widths.forEach((w,i)=>sheet.getRange(`${col(i+1)}:${col(i+1)}`).format.columnWidth=w);
  sheet.freezePanes.freezeRows(startRow);
  sheet.tables.add(`A${startRow}:${endCol}${endRow}`, true, tableName);
  return endRow;
}
function col(n){ let s=""; while(n){ n--; s=String.fromCharCode(65+n%26)+s; n=Math.floor(n/26);} return s; }
function statusFill(status) {
  return ({
    "可用":C.lightGreen,"待验证":C.lightAmber,"开发中":C.lightBlue,"有缺陷":C.lightRed,
    "未开始":"#F1F5F9","暂停":"#E5E7EB","废弃":"#E5E7EB"
  })[status] || C.white;
}
function priorityFill(p){ return ({"P0":C.lightRed,"P1":"#FFEDD5","P2":C.lightAmber,"P3":C.lightBlue})[p]||C.white; }

// 01 项目概述
{
  const s=wb.worksheets.getItem("01_项目概述");
  title(s,"项目概述","项目现状基线：2026-06-25｜结论以代码、数据库、Git 与本地验证为依据","F");
  const rows=[
    ["项目名称","仓库记账与 OZON 跨境电商运营系统","事实","G:\\inventory"],
    ["目标用户","仓库/采购/销售/财务人员；OZON 跨境电商运营人员","推断+现有页面","多角色权限尚未系统化"],
    ["核心问题","统一管理库存、采购、销售、退款、报表，并将国内商品资料加工为可审核、可发布的 OZON 商品资料","代码+产品文档","当前两个业务域耦合在同一 Flask 应用"],
    ["核心价值","减少人工记账与重复录入；沉淀商品事实、图片、类目属性和发布任务；保留人工审核门禁","代码+数据模型","AI 结果仍需人工负责"],
    ["当前阶段","仓库主流程处于使用/维护期；OZON 处于开发与试运行并行期；AI 生图与母图抠图处于快速迭代、待系统验收期","综合判断","不是正式稳定运营基线"],
    ["明确不应做","未经人工确认直接发布；把视觉模型 bbox 当精确分割边界；用 Mock 文案冒充真实 AI 结果；在正式店直接试验高风险写操作","规则建议","应写入发布门禁"],
    ["规模快照","192 条路由、24 个蓝图文件、61 个模板、62 个模型类、61 张业务表","代码/数据库扫描","OZON 蓝图单文件 109 条路由，体量集中"],
    ["数据快照","data.db 约 481MB；类目属性值约 121.8 万条；类目属性约 21.9 万条","SQLite 只读统计","备份文件体积显著小于当前库，恢复有效性未证实"],
    ["测试快照","自动化测试文件 0；关键流程依赖人工点击与日志","文件扫描","不能用“已实现”替代“已验收”"],
    ["版本状态","main 比 origin/main 超前 32 个提交；存在已修改文件和大量未跟踪文件","Git 状态","远端并非完整灾备"],
    ["总体判断","具备真实业务数据与较完整页面骨架，但可接管性、可验证性、备份恢复和 AI 正确性仍是主要短板","审查结论","建议先做基线与 P0 风险收口，再扩展新功能"]
  ];
  writeTable(s,4,["主题","当前结论","证据类型","备注"],rows,[20,52,18,38],"ProjectOverview");
}

// 02 核心流程
{
  const s=wb.worksheets.getItem("02_核心流程");
  title(s,"核心业务流程","每一步明确操作者、输入、系统动作、输出与失败处理","G");
  const rows=[
    ["A1","仓库基础资料","仓库管理员","产品/客户/供应商信息","创建与维护基础档案","可供订单引用的主数据","校验失败则提示；重复/跨用户数据需审查"],
    ["A2","采购与入库","采购/仓库","供应商订单、收货数量、价格","登记采购单与入库明细，更新缓存库存","采购记录、库存增加、成本数据","事务/库存回滚能力需验证"],
    ["A3","客户订单与出库","销售/仓库","客户订单、发货数量","创建客户订单和销售出库，更新库存","出库单、库存减少、应收信息","缺货、重复出库、删除回滚需验收"],
    ["A4","拆包/组合","仓库管理员","规则、源产品、目标产品、数量","按确认单消耗/产出库存并分摊成本","拆分/组装单与新库存","取消、成本守恒和并发需验证"],
    ["A5","对账与报表","财务/管理者","日期区间、订单/收发数据","按期初前、期末累计、本期差额聚合","库存、经营、客户/供应商报表","空结果、跨期、用户隔离需回归"],
    ["B1","采集商品","OZON 运营","商品链接/页面/插件数据","抓取页面并解析标题、SKU、图片、价格","OzonSource、SKU、媒体记录","反爬/JS 页面缺失时转人工补录"],
    ["B2","事实整理","OZON 运营+AI","采集数据、图片证据","形成商品事实、SKU事实、适配信息","可审核的事实层","AI 不确定项必须人工确认"],
    ["B3","类目与属性","OZON 运营","商品事实、OZON 类目库","选择 type，加载必填属性与字典","类目绑定、属性 JSON","类型/字典变更时重新同步"],
    ["B4","图片识别与母图","OZON 运营+视觉模型","来源图片","检测主商品/排除区域，目标级分割，生成透明 PNG","待审核产品母图","bbox 偏差时扩大搜索框/人工调整；禁止自动批准"],
    ["B5","图片方案与生成","OZON 运营+生图模型","母图、参考图、卖点、槽位任务","按槽位生成候选图并评分/选择","8 槽位候选图与选用图","无可靠参考图则阻断或降级 text_only 并警告"],
    ["B6","商品文案","OZON 运营+AI","商品事实、类目规则","生成俄语标题/卖点/描述","草稿文案","当前旧 processing_generate 仍为 Mock，必须替换"],
    ["B7","人工审核","OZON 运营","文案、属性、图片、价格","执行阻断校验并人工确认","approved 草稿","任何必填缺失/图片未审核不得发布"],
    ["B8","测试店发布","OZON 运营","approved 草稿、店铺凭证","调用 OZON API，保存请求响应与 task_id","发布任务与平台商品","失败记录、重试；真实闭环尚缺数据库成功任务证据"],
    ["B9","在线商品维护","OZON 运营","平台商品、价格/库存/内容变更","同步详情、更新、归档/恢复","在线商品快照和操作记录","写操作必须先测试店验证并二次确认"]
  ];
  writeTable(s,4,["编号","流程环节","操作者","输入","系统动作","输出","失败与恢复"],rows,[9,20,20,30,40,32,42],"CoreFlows");
}

const features=[
["基础资料","用户登录/退出","可用","是","已注册认证蓝图与登录门禁；需补会话安全测试","P1","auth.py"],
["基础资料","产品管理","可用","是","415 条产品数据；旧数据 user 可为空","P1","products.py / Product"],
["基础资料","客户管理","可用","是","11 条客户数据；需验证跨用户隔离","P1","customers.py / Customer"],
["基础资料","供应商管理","可用","是","当前仅 1 条供应商数据","P2","suppliers.py / Supplier"],
["采购库存","供应商订单","可用","是","历史文档记录过明细编辑/删除 P0，需确认已回归","P0","supplier_orders.py"],
["采购库存","采购入库","可用","是","41 单/43 明细；库存联动存在","P1","purchases.py"],
["采购库存","客户订单","可用","是","18 单/31 明细","P1","orders.py"],
["采购库存","销售出库","可用","是","22 单/37 明细；库存联动存在","P1","sales.py"],
["采购库存","库存实时聚合与缓存","待验证","部分","Product.stock 为缓存，精确聚合另有 helper；一致性需审计","P1","models.py / helpers.py"],
["采购库存","拆包规则与拆包单","待验证","部分","模型/路由存在，但订单数据为 0","P1","inventory_split.py"],
["采购库存","组合规则与组合单","待验证","部分","规则有数据，订单数据为 0","P1","inventory_assembly.py"],
["财务报表","客户退款","可用","是","4 条记录","P2","refunds.py"],
["财务报表","客户资金流水","待验证","部分","表为空","P2","finance.py"],
["财务报表","库存/经营报表","待验证","部分","路由存在，缺少自动化回归","P1","reports.py / home.py"],
["财务报表","对账时段拆分","待验证","部分","跨 3 蓝图的日期逻辑复杂，需边界测试","P1","home/reports/supplier_orders"],
["系统工具","数据导入导出","待验证","部分","路由存在，需验证大文件、重复与回滚","P1","data_io.py"],
["系统工具","操作日志","可用","是","59 条记录；覆盖率是否完整未知","P2","OperationLog / logs.py"],
["系统工具","汇率定时更新","待验证","部分","后台线程+锁；外部失败与重启恢复需测","P2","exchange_rate.py"],
["OZON采集","商品链接采集","待验证","部分","49 个来源、487 SKU、1264 图片；平台反爬导致质量不稳定","P1","ozon_collector.py"],
["OZON采集","采集质量检查","开发中","部分","有 detail_missing/人工确认逻辑","P1","collect_quality_check"],
["OZON采集","商品回收站/软删除","待验证","部分","OzonSource 有 deleted_at；恢复路径需验收","P2","ozon.py"],
["事实层","商品事实整理","待验证","部分","26 ProductFact、186 SKU事实；证据表为 0","P1","ProductFact*"],
["事实层","图片视觉事实","开发中","部分","53 ImageFact/18 任务，但通用 vision_tool 仍有 TODO","P1","vision_tool.py"],
["适配工作台","商品分组与适配","待验证","部分","26 组，ListingAdaptation 仅 1 条","P1","ListingAdaptation"],
["类目属性","类目树同步","可用","是","568 类目、7422 类型","P1","OzonCategory*"],
["类目属性","属性与字典同步","可用","是","21.9 万属性、121.8 万字典值；库体积大","P1","OzonCategoryAttribute/Value"],
["类目属性","type_id 过滤与去重","待验证","部分","近期修复，需做回归","P1","ozon.py"],
["类目属性","中文翻译展示","待验证","部分","value_cn 字段与展示逻辑存在","P2","OzonAttributeValue"],
["模型配置","主模型配置","可用","是","DeepSeek/OpenAI 兼容配置存在","P1","VisionModelConfig / models page"],
["模型配置","视觉模型配置与测试","有缺陷","部分","专用主体检测可调用；通用 vision_tool 仍是占位实现","P0","vision_tool.py"],
["AI文案","俄语标题/卖点/描述生成","有缺陷","否","processing_generate 明确写入 Mock 内容","P0","ozon.py:1817"],
["母图处理","来源图片候选评分","待验证","部分","自动选择候选逻辑存在","P1","product_cutout.py"],
["母图处理","视觉模型自动识别主商品","开发中","部分","11 条检测记录；bbox 仅适合作种子框","P0","product_subject_detector.py"],
["母图处理","目标框拖拽/人工修正","开发中","部分","当前模板有 211 行未提交修改","P1","product_cutout.html"],
["母图处理","目标级抠图","开发中","部分","V3.2 搜索框+连通域；仍需真实样本集验收","P0","product_cutout.py"],
["母图处理","透明 PNG 质量检查","开发中","部分","像素保持、残留、完整度、边缘评分已实现","P1","_check_cutout_quality_v2"],
["母图处理","母图批准门禁","待验证","部分","服务端阻断存在，需覆盖异常分支","P0","product_cutout_approve"],
["图片生成","槽位规划","待验证","部分","8 槽位存在；ImagePlan 表当前 0 条","P1","OzonImageSlot/Plan"],
["图片生成","参考图自动选择","开发中","部分","会阻止 package 槽位误用主图","P1","ecommerce_image_reference.py"],
["图片生成","Seedream 4.5 适配","待验证","部分","代码与配置存在；日志仍标记真实付费调用待确认","P0","image_generation.py"],
["图片生成","多模型候选图生成","待验证","部分","31 候选图；缺少系统化质量对比","P1","OzonImageCandidate"],
["图片生成","候选图评分与选用","开发中","部分","人工评分字段和选用路由存在","P1","image_plan.html / ozon.py"],
["图片生成","自动视觉 QA","未开始","否","auto_qa 字段存在，但专用 QA 服务未落地","P1","模型字段/日志"],
["刊登审核","发布前校验","待验证","部分","必填属性与图片审核门禁存在","P0","listing_validate/approve"],
["刊登审核","人工审核通过","待验证","部分","approved 状态与阻断检查存在","P0","listing_approve"],
["OZON发布","创建商品 API","待验证","部分","代码调用 /v3/product/import；发布任务表为 0","P0","ozon_api.py"],
["OZON发布","发布失败记录与重试","待验证","部分","异常分类/任务字段存在；无生产数据","P1","OzonPublishJob"],
["在线商品","平台商品同步","待验证","部分","数据库已有 29 条在线商品","P1","online_products_sync"],
["在线商品","价格/库存更新","待验证","部分","接口版本部分标注待实测确认","P0","ozon_api.py"],
["在线商品","内容/图片更新","待验证","部分","路由存在，需测试店验证","P0","ozon.py"],
["在线商品","归档/恢复","待验证","部分","高风险写操作，需二次确认与测试店证据","P0","archive/unarchive"],
["安全","多用户数据隔离","待验证","部分","约定明确，但未见自动化全路由审计","P0","current_user 过滤"],
["安全","API Key 加密存储","待验证","部分","图片模型兼容明文回退；需统一迁移与密钥轮换","P0","crypto_utils/image_generation"],
["运维","开发启动","有缺陷","否","项目 .venv 启动器指向不存在的 Python","P0",".venv / run.bat"],
["运维","生产 Waitress 启动","有缺陷","否","同样依赖失效 .venv；端口文档与文件不一致","P0","waitress_server.py"],
["运维","数据库备份恢复","有缺陷","否","当前库 481MB，现有备份远小于当前库且未验证恢复","P0","data.db / backups"],
["质量","自动化测试","未开始","否","测试文件 0","P0","仓库扫描"],
["质量","CI/CD 与发布基线","未开始","否","main 超前远端 32 提交且工作区脏","P1","Git 状态"]
];

// 03 功能清单
{
  const s=wb.worksheets.getItem("03_功能清单");
  title(s,"项目功能清单","状态口径：只有有证据且可重复使用才标“可用”；未完成真实验证统一标“待验证”","G");
  const end=writeTable(s,4,["模块","功能","状态","是否可用","已知问题/证据","优先级","证据位置"],features,[18,28,12,12,54,10,32],"FeatureInventory");
  s.getRange(`C5:C${end}`).dataValidation={rule:{type:"list",values:["未开始","开发中","待验证","可用","有缺陷","暂停","废弃"]}};
  s.getRange(`F5:F${end}`).dataValidation={rule:{type:"list",values:["P0","P1","P2","P3"]}};
  features.forEach((r,i)=>{
    s.getRange(`C${i+5}`).format.fill=statusFill(r[2]);
    s.getRange(`F${i+5}`).format.fill=priorityFill(r[5]);
  });
}

// 04 产品规则
{
  const s=wb.worksheets.getItem("04_产品规则");
  title(s,"产品规则与验收标准","把 AI 开发经验转成可执行门禁，而不是依赖提示词碰运气","F");
  const rows=[
    ["母图","主体定义","只保留真实商品主体及明确属于该商品的附属配件；广告文字、装饰、店铺水印、非配件物体排除","输出透明 PNG 目视无广告残留；产品无缺边","P0","已部分实现"],
    ["母图","bbox 用法","视觉 bbox 只能作为种子区域，不得直接作为最终裁切边界","分割结果必须由 mask 反推真实边界；触边自动扩框","P0","V3.2 已实现，待样本验收"],
    ["母图","像素真实性","产品不透明区域必须来自原图，不允许 AI 重绘或修改产品像素","opaque 区域像素差异为 0","P0","代码已检查"],
    ["母图","自动批准","视觉检测、rembg 和质量分只能提供建议，不得自动批准","必须由用户点击确认母图","P0","服务端门禁需回归"],
    ["参考图","可靠性","优先白底图；没有白底图时可从其他商品图抠取母图；包装清单必须有对应证据","无可靠参考图时阻断 reference 模式或明确警告","P0","部分实现"],
    ["生图","结构保护","形状、比例、按钮、接口、部件数量、材质分区、屏幕 UI 不得改变","候选图结构评分达标且人工复核","P0","Prompt 已约束，QA 未完成"],
    ["生图","卖点分组","每张图只解决一组买家疑问；稳定承重/高度适配/家庭空间/训练功能等分组","每槽位有 buyer_question、main_claim 和证据","P1","字段存在"],
    ["生图","参考图原则","参考图只学习构图、光影、场景、版式和风格，不抄结构与参数","无虚构参数、无错误配件","P0","需 QA"],
    ["文案","事实约束","标题、卖点、描述只能使用已确认事实；不确定项不能编造","所有参数可追溯到 ProductFact/证据或人工确认","P0","当前旧路由 Mock，不满足"],
    ["类目属性","必填门禁","category_id、type_id 和所有必填属性必须完整","发布前 blocking_count=0","P0","已有校验"],
    ["价格","人工确认","来源价格不可靠或详情缺失时必须人工确认","price_manual_confirmed=true 才允许发布","P0","字段存在，门禁需核验"],
    ["图片审核","槽位完整","计划要求的图片必须全部审核通过","approved 图片数达到要求且 URL 可被 OZON 访问","P0","已有基础门禁"],
    ["发布","环境顺序","先测试店，后正式店；归档、恢复、改价、改库存属于高风险写操作","测试店留存请求、响应、平台结果截图","P0","文档规则"],
    ["发布","失败恢复","每次外部调用保存脱敏请求、响应、错误分类和重试次数","失败可重试且不会重复创建商品","P0","任务表存在，幂等性待验"],
    ["权限","用户隔离","任何查询、子查询、聚合、文件读取都必须绑定 current_user","跨用户 ID 访问返回 404/403","P0","规范存在，无自动化审计"],
    ["删除","可恢复性","核心业务数据优先软删除；硬删除前必须确认关联影响","删除后可恢复或有完整审计记录","P1","覆盖不统一"],
    ["密钥","秘密管理","API Key 只能加密存储、日志脱敏、不可回显完整值","数据库无明文 key；轮换后旧 key 失效","P0","需专项审计"],
    ["批量任务","可续跑","批量采集、识别、生图、同步中断后可从任务状态续跑","单项失败不拖垮整批，进度可见","P1","尚未系统化"],
    ["验收","状态口径","代码存在≠可用；真实数据成功且异常场景通过后才标可用","验收记录含步骤、预期、实际、证据","P0","本基线采用此口径"]
  ];
  writeTable(s,4,["领域","规则","具体要求","验收标准","优先级","当前状态"],rows,[14,20,50,46,10,24],"ProductRules");
}

// 05 架构
{
  const s=wb.worksheets.getItem("05_系统架构");
  title(s,"系统架构","当前为单体 Flask 应用：库存记账与 OZON 运营共用数据库、认证和部署","F");
  const rows=[
    ["客户端","Jinja2 + Bootstrap 5 + 原生 fetch + Chart.js","templates/、static/","页面、表单、局部 JSON 交互","61 个模板；复杂 OZON 页面脚本集中"],
    ["Web 层","Flask 3.0 + 22 个已注册蓝图","app.py、blueprints/","路由、认证、请求处理","192 条路由；ozon.py 单文件 109 条"],
    ["业务服务","采集、OZON API、生图、主体检测、抠图、汇率","services/","外部调用与核心算法","服务成熟度不一致；vision_tool 有 TODO"],
    ["数据访问","Peewee 4.0","models.py、crud_utils.py","ORM、用户过滤、聚合","62 个模型类"],
    ["数据库","SQLite WAL","data.db / WAL / SHM","业务数据、配置、任务、类目库","61 表、约 481MB"],
    ["图片文件","本地 uploads/output 路径 + 远程 URL","uploads/、output/","来源图、生成图、透明母图","文件与数据库引用一致性需审计"],
    ["外部服务","OZON Seller API","services/ozon_api.py","类目、商品、价格、库存、归档","写接口需测试店验证"],
    ["外部服务","OpenAI 兼容主模型/视觉模型","模型配置表","采集解析、视觉理解、未来 QA","通用视觉服务未完成"],
    ["外部服务","火山 ARK Seedream 4.5 / 其他生图模型","image_generation.py","参考图生图","真实付费调用证据不足"],
    ["外部服务","汇率 API","exchange_rate.py","定时拉取 CNY 汇率","失败降级与监控需补"],
    ["运行","Flask 开发服务器 / Waitress","app.py、waitress_server.py、run.bat","本地与生产启动","当前 .venv 启动器失效"],
    ["安全","Flask-Login + Flask-Limiter + current_user 过滤","app.py、各蓝图","登录、限流、租户隔离","默认开发 secret 存在；全路由隔离未自动审计"]
  ];
  writeTable(s,4,["层级","技术/组件","代码位置","职责","现状与风险"],rows,[16,32,32,34,44],"Architecture");
}

// 06 数据字典
{
  const s=wb.worksheets.getItem("06_数据字典");
  title(s,"核心数据字典","行数为 2026-06-25 对 data.db 的只读统计；只列核心与高体量对象","G");
  const rows=[
    ["User","用户/租户","2","username, password_hash, extension_token","多数业务表 user_id","认证与隔离边界","核心"],
    ["Product","产品主数据","415","sku, name, unit, stock, user_id","订单、拆包、组合","stock 是缓存字段","核心"],
    ["SupplierOrder / Item","供应商订单","12 / 21","supplier, status, quantity, price","PurchaseOrder","采购计划与未收货","核心"],
    ["PurchaseOrder / Item","采购入库","41 / 43","date, product, quantity, unit_price","Product","库存增加与成本来源","核心"],
    ["CustomerOrder / Item","客户订单","18 / 31","customer, status, amount","SalesOrder","销售需求","核心"],
    ["SalesOrder / Item","销售出库","22 / 37","customer, product, quantity, price","Product","库存减少与销售额","核心"],
    ["ProductSplit*","拆包规则/订单","规则有数据；订单 0","source/target product, cost","Product","一拆多库存转换","待验证"],
    ["ProductAssembly*","组合规则/订单","规则有数据；订单 0","bundle/component, cost","Product","多合一库存转换","待验证"],
    ["OzonAccount","店铺凭证","1","client_id, encrypted_api_key, environment","OzonDraft/Job/Online","外部账号与测试/正式环境","敏感"],
    ["OzonSource","采集商品","49","platform, source_url, raw_json, status","SKU/Media/Draft/Fact","采集入口","核心"],
    ["OzonSourceSku","来源 SKU","487","source_order, sku_name, price, image_refs","FactSku/DraftSku","SKU 事实来源","核心"],
    ["OzonSourceMedia","来源图片","1264","url/path, role, review, text flags","Cutout/Reference","视觉与生图证据","核心"],
    ["ProductFact / SKU","商品事实层","26 / 186","verified facts, review status","ListingAdaptation/Draft","AI 与发布之间的事实层","核心"],
    ["ProductFactEvidence","事实证据","0","fact, source, evidence","ProductFact","事实可追溯","缺失"],
    ["ListingAdaptation","平台适配","1","marketplace fields/status","Draft","适配层","低使用"],
    ["OzonCategory / Type","类目与商品类型","568 / 7422","description_category_id, type_id","Attribute/Draft","类目绑定","高体量"],
    ["OzonCategoryAttribute","类目属性","219091","attribute_id, type_id, required","Draft","必填属性规则","高体量"],
    ["OzonAttributeValue","属性字典值","1218394","dictionary_value_id, type_id, value_cn","Attribute","属性枚举翻译","超高体量"],
    ["VisionModelConfig","模型配置","2","provider, model, base_url, encrypted key","视觉/生图服务","AI 接口配置","敏感"],
    ["ImageAnalysisJob / Fact","图片分析","18 / 53","task_type, result, facts","Media","通用视觉分析","未完整落地"],
    ["OzonProductSubjectDetection","主体检测","11","bbox, excluded regions, confidence","Cutout","视觉检测历史","开发中"],
    ["OzonProductCutout","产品母图","15","paths, quality, revision, status","ImageSlot","透明 PNG 与审核","开发中"],
    ["OzonImagePlan / Reference","图片方案/参考","0 / 0","plan type, reference role","ImageSlot","可复用图片工作流","尚未落库使用"],
    ["OzonImageSlot","图片槽位","8","role, claims, references, status","Candidate/Draft","详情页图片任务","开发中"],
    ["OzonImageCandidate","候选生成图","31","provider, prompt, scores, status","ImageSlot","多模型比较","待验证"],
    ["OzonDraft / DraftSku","刊登草稿","1 / 6","category, attributes, text, pricing, status","PublishJob","发布前聚合对象","核心"],
    ["OzonPublishJob","发布任务","0","request, response, error, retry","Draft/Account","发布审计与恢复","缺少真实成功证据"],
    ["OzonOnlineProduct / Action","在线商品与操作","29 / 12","product_id, offer_id, state, action","Account","平台同步和写操作历史","待验证"],
    ["OperationLog","操作日志","59","user, action, target, ip","全局","审计","覆盖率待审计"],
    ["ExchangeRate","汇率","4","base, target, rate, updated_at","定价/报表","汇率缓存","外部依赖"]
  ];
  writeTable(s,4,["数据对象","业务含义","当前行数","关键字段","主要关系","用途","备注"],rows,[28,24,14,42,28,30,20],"DataDictionary");
}

// 07 运行手册
{
  const s=wb.worksheets.getItem("07_运行手册");
  title(s,"部署与运行手册（现状）","注意：这里同时记录“文档期望”和“本次验证结果”，避免照抄无效命令","F");
  const rows=[
    ["环境","操作系统/版本","Windows；Python 3.11；Node 仅用于本次文档生成","AGENTS.md","需固定可复现环境"],
    ["开发启动","G:\\inventory\\.venv\\Scripts\\python.exe app.py","失败：启动器指向不存在的 C:\\Users\\Lenovo\\AppData\\Local\\Programs\\Python\\Python311\\python.exe","本次实测","P0 修复虚拟环境"],
    ["生产启动","G:\\inventory\\.venv\\Scripts\\python.exe waitress_server.py","预计同样失败；文件实际监听 127.0.0.1:5000，与说明中的 8100 不一致","代码+推断","统一端口和环境变量"],
    ["快捷启动","run.bat / 启动.bat / launcher.py","会 taskkill 全部 python.exe，可能误杀无关 Python 服务","代码审查","改为 PID 文件定向停止"],
    ["停止","stop.bat → stop.py","需验证是否只停止本项目","代码位置","避免全局杀进程"],
    ["数据库","data.db + data.db-wal + data.db-shm","SQLite WAL；busy_timeout=3000；foreign_keys=1","models.py","停机一致性备份需包含 WAL checkpoint"],
    ["初始化","app.py:init_db()","安全建表并执行若干 ALTER/migration/清理","代码审查","启动时迁移缺少版本表与回滚"],
    ["配置","环境变量 + VisionModelConfig/UserApiKey/OzonAccount","密钥位置分散","代码审查","统一 secret 管理与加密迁移"],
    ["日志","OperationLog + 控制台 + 发布任务请求响应","业务审计不等于系统错误日志","数据库/代码","增加滚动日志与错误 ID"],
    ["备份","当前存在若干小体积备份文件","未见与 481MB 当前库等量的可恢复备份证据","文件扫描","建立每日备份+月度恢复演练"],
    ["部署","main 分支本地运行","本地比远端超前 32 提交，且有未提交/未跟踪文件","Git","先整理、提交、推送、打基线 tag"],
    ["恢复","暂无验证过的标准流程","无法证明从备份恢复后页面、图片和任务可用","现状","形成一键恢复演练记录"]
  ];
  writeTable(s,4,["类别","期望方式","本次检查结果","证据","下一动作"],rows,[16,40,56,22,38],"Runbook");
}

// 08 验收矩阵
{
  const s=wb.worksheets.getItem("08_验收矩阵");
  title(s,"核心功能验收矩阵","建议逐项填写“实际结果/证据链接”，通过前不得改成可用","H");
  const rows=[
    ["库存入库","已登录且有产品/供应商","创建采购单并入库","库存与成本正确增加","未执行","待验证","P0","含删除/修改/重复提交"],
    ["销售出库","有足够库存和客户订单","创建出库并关联订单","库存减少、订单状态正确","未执行","待验证","P0","含缺货与取消"],
    ["拆包","已有拆包规则和库存","确认拆包单","源库存减少、目标库存增加、总成本守恒","无订单数据","待验证","P1","含取消回滚"],
    ["组合","已有组合规则和零件库存","确认组合单","零件减少、套装增加、总成本守恒","无订单数据","待验证","P1","含库存不足"],
    ["多用户隔离","准备两个用户及各自数据","交叉访问 ID/列表/聚合","另一用户数据不可见不可写","未执行","待验证","P0","覆盖 192 路由"],
    ["采集商品","有效商品链接","采集并保存","标题/SKU/图片/价格与页面一致","已有 49 来源但未抽样复核","待验证","P1","淘宝/1688/其他平台分别测"],
    ["主体识别","广告图含商品与文字","点击自动识别","主商品框完整覆盖，排除框不侵入产品","示例出现 bbox 偏窄","有缺陷","P0","建立 30–50 张样本集"],
    ["目标级抠图","已确认目标框","生成透明 PNG","产品完整、文字/logo 清除、像素不变","V3.2 已实现，未系统验收","待验证","P0","透明/黑色/细边产品"],
    ["母图批准门禁","质量不合格结果","尝试批准","服务端拒绝并给出原因","代码存在","待验证","P0","不能只靠前端"],
    ["Seedream 参考图生成","有效配置+公网参考图","生成 1 张候选图","API 成功、结构接近参考、文件可访问","日志标记真实调用待确认","待验证","P0","会产生费用"],
    ["无白底图流程","只有广告图/场景图","识别→抠图→选母图→生图","最终商品结构真实且无广告残留","未端到端验证","待验证","P0","当前核心新增需求"],
    ["AI 文案","事实层已审核","生成俄语标题/卖点/描述","非 Mock、无虚构、可追溯","当前返回 Mock","有缺陷","P0","必须替换旧路由"],
    ["类目属性","选择 category/type","加载必填属性与字典","无重复、type_id 正确、中文提示可用","近期修复","待验证","P1","抽 5 类目"],
    ["发布前校验","准备缺失必填/图片未审草稿","校验与审批","明确阻断且不能绕过","代码存在","待验证","P0","服务端测试"],
    ["测试店发布","测试店凭证+approved 草稿","发布单 SKU","产生 task_id，平台可见，任务留痕","PublishJob=0","未开始","P0","真实外部验证"],
    ["在线商品更新","测试商品","改价/改库存/内容/图片","平台结果与本地记录一致","接口部分标注待实测","待验证","P0","逐接口测试"],
    ["归档恢复","测试商品","归档后恢复","平台状态正确，二次确认有效","未执行","待验证","P0","禁止正式店先测"],
    ["备份恢复","停止写入并备份","在隔离目录恢复并启动","数据量、图片、登录、核心页面一致","无证据","未开始","P0","每月演练"],
    ["异常恢复","模拟 API 超时/429/500","执行采集/发布/生图","错误分类、重试、无重复副作用","部分代码存在","待验证","P1","可注入故障"],
    ["启动部署","全新机器/干净环境","按手册安装并启动","30 分钟内可运行且版本一致",".venv 已失效","有缺陷","P0","可接管性标准"]
  ];
  writeTable(s,4,["功能","前置条件","操作步骤","预期结果","实际结果","最终状态","优先级","异常场景"],rows,[22,30,30,42,34,12,10,34],"AcceptanceMatrix");
}

const risks=[
["R-001","P0","运行环境","项目 .venv 启动器失效，指定 Python 不存在","无法按文档启动/验证/部署","本次 py_compile 与 import 均无法创建进程","重建虚拟环境并锁定 Python/依赖；更新启动脚本","未处理"],
["R-002","P0","备份恢复","当前数据库约 481MB，现有备份显著偏小且未做恢复演练","数据丢失后无法恢复","文件与数据库扫描","建立一致性备份、校验、异机恢复演练","未处理"],
["R-003","P0","AI 文案","processing_generate 仍写入 Mock Russian 内容","错误资料可能进入审核/发布","ozon.py:1817","替换为事实约束的真实模型服务并增加 Mock 禁止门禁","未处理"],
["R-004","P0","自动化测试","仓库没有测试文件","修改后无法可靠回归 192 条路由","文件扫描 TEST_FILES=0","先建 P0 核心流程测试与租户隔离测试","未处理"],
["R-005","P0","多用户隔离","规范要求 current_user 过滤，但无全路由自动审计","越权读取/修改数据","架构规范与缺少测试","编写双用户集成测试和查询审计","未处理"],
["R-006","P0","密钥安全","模型配置存在明文兼容回退，secret 管理分散","API Key 泄露、无法轮换","image_generation.py / 配置模型","统一加密格式、迁移旧明文、日志脱敏和轮换","未处理"],
["R-007","P0","母图正确性","视觉 bbox 可能偏窄，示例曾裁掉产品左侧","母图结构不完整并污染后续生图","真实截图+V3.2 修复记录","bbox 仅作种子；建立样本集与完整度门禁","处理中"],
["R-008","P0","生图真实性","自动视觉 QA 未落地，结构保护主要依赖 Prompt 和人工","生成图改变按钮/部件/参数","日志与模型字段","实现结构对比 QA，失败不得选用","未处理"],
["R-009","P0","发布闭环","OzonPublishJob 行数为 0，缺少真实成功发布证据","无法证明刊登流程可用","数据库统计","测试店发布单 SKU/多 SKU并留证","未处理"],
["R-010","P0","外部写操作","改价/库存/归档等接口部分标注待实测","正式店误操作或接口失效","ozon_api.py 注释","测试店逐项验证+环境门禁+二次确认","未处理"],
["R-011","P0","进程管理","启动器 taskkill 全部 python.exe/pythonw.exe","可能误杀其他业务","launcher.py/run.bat","PID 文件或端口绑定定向停止","未处理"],
["R-012","P0","数据正确性","历史需求记录供应商订单明细编辑/删除可能影响整单","采购与资金数据损坏","requirements prioritization","确认修复提交并做回归测试","待确认"],
["R-013","P1","Git/灾备","本地 main 超前远端 32 提交，工作区存在修改和大量未跟踪文件","机器损坏会丢失最新代码与设计","git status/log","清理敏感文件、提交、推送、打 tag","未处理"],
["R-014","P1","维护性","blueprints/ozon.py 单文件 109 路由","改动冲突、难测试、难接管","路由统计","按采集/事实/图片/发布/在线商品拆分蓝图","未处理"],
["R-015","P1","通用视觉服务","vision_tool.py 仍有 TODO/占位返回","部分页面可能展示假完成或无真实分析","代码扫描","统一视觉客户端与任务状态","未处理"],
["R-016","P1","图片文件一致性","图片同时使用本地路径和远程 URL","迁移/备份后引用失效","数据模型与服务","建立文件资产表、校验任务、相对路径策略","未处理"],
["R-017","P1","SQLite 体量","属性字典 121.8 万行，单库 481MB 且业务/字典共库","备份慢、锁竞争、迁移风险","数据库统计","拆分只读类目库或建立版本化缓存","未处理"],
["R-018","P1","启动迁移","应用启动时执行 ALTER 和清理，无正式迁移版本/回滚","启动失败或不可逆变更","app.py:init_db","引入迁移版本表、备份前置和失败回滚","未处理"],
["R-019","P1","抠图样本覆盖","当前算法围绕单一问题快速迭代，缺少代表性数据集","新图片类型回归","提交历史","建立带金标准 mask 的样本集与指标","未处理"],
["R-020","P1","Seedream 联调","日志仍列真实调用、image 参数和 watermark 验证为待确认","参考图可能未传入或结果不可控","项目改动日志","付费小样测试并保存脱敏请求快照","待确认"],
["R-021","P1","批量任务","采集/识别/生图缺少统一队列、幂等和续跑模型","批量中断后重复或丢任务","架构审查","统一 job 状态机和重试策略","未处理"],
["R-022","P1","审计完整性","OperationLog 仅 59 条，无法证明覆盖所有高风险操作","责任追踪不足","数据库统计","规定必须记录的操作并补自动化测试","未处理"],
["R-023","P1","默认密钥","Flask secret 缺环境变量时回退 dev-secret-key-change-me","会话安全风险","app.py","生产环境缺密钥直接拒绝启动","未处理"],
["R-024","P1","端口/文档漂移","说明写 8100，但 waitress_server.py 固定 5000","运维误判和冲突","AGENTS.md vs 文件","统一 FLASK_PORT/WAITRESS_PORT","未处理"],
["R-025","P1","事实证据","ProductFactEvidence 表为 0","AI 文案与属性难追溯","数据库统计","把来源片段/图片/人工确认写入证据表","未处理"],
["R-026","P2","编码质量","PowerShell 读取部分源码显示乱码，文件编码一致性可疑","日志/维护阅读困难","本次检查","统一 UTF-8 并加 editorconfig","待确认"],
["R-027","P2","汇率依赖","后台线程每小时调用第三方汇率 API","外部失败导致价格过期","exchange_rate.py","显示更新时间、失败告警、手工锁价","未处理"],
["R-028","P2","软删除一致性","仅部分对象有 deleted_at","误删恢复能力不一致","模型扫描","定义数据保留/删除矩阵","未处理"],
["R-029","P2","日志监控","缺少统一错误日志、健康检查和告警","线上故障发现晚","运行结构","增加 /health、滚动日志、关键任务告警","未处理"],
["R-030","P3","界面一致性","复杂页面处于频繁增量修改","学习成本与误操作","未提交模板改动","在核心流程稳定后统一交互","处理中"]
];

// 09 风险
{
  const s=wb.worksheets.getItem("09_风险清单");
  title(s,"问题与风险清单","P0：数据/安全/核心流程不可用；P1：重要错误；P2：局部问题；P3：体验问题","H");
  const end=writeTable(s,4,["编号","级别","领域","风险描述","可能影响","证据","建议措施","状态"],risks,[10,9,16,48,36,30,48,14],"RiskRegister");
  s.getRange(`B5:B${end}`).dataValidation={rule:{type:"list",values:["P0","P1","P2","P3"]}};
  s.getRange(`H5:H${end}`).dataValidation={rule:{type:"list",values:["未处理","处理中","待确认","已解决","接受"]}};
  risks.forEach((r,i)=>{
    s.getRange(`B${i+5}`).format.fill=priorityFill(r[1]);
    if(r[7]==="处理中") s.getRange(`H${i+5}`).format.fill=C.lightBlue;
    if(r[7]==="未处理") s.getRange(`H${i+5}`).format.fill=C.lightRed;
  });
}

// 10 路线图
{
  const s=wb.worksheets.getItem("10_版本路线图");
  title(s,"下一阶段路线图","优先级顺序：数据安全 → 可启动 → 核心正确性 → 稳定性 → 效率 → 新功能","H");
  const rows=[
    ["V0.1 基线收口","重建可运行环境","解决无法启动与不可接管","干净机器按文档 30 分钟内启动；开发/Waitress 均通过","依赖版本冲突","开发负责人","1–2天","P0"],
    ["V0.1 基线收口","数据库一致性备份与恢复演练","确保 481MB 数据库及图片可恢复","每日备份；校验哈希；隔离目录恢复并跑核心页面","WAL/图片遗漏","运维负责人","1–2天","P0"],
    ["V0.1 基线收口","Git 基线与敏感文件清理","保证代码可追溯与远端灾备","提交/推送 32 个本地提交；工作区干净；tag baseline-20260625","误提交密钥/大文件","开发负责人","0.5–1天","P0"],
    ["V0.2 正确性","供应商订单 P0 回归","确认旧缺陷不再破坏整单","编辑/删除单明细、整单金额、库存均正确","历史数据兼容","开发+业务","1天","P0"],
    ["V0.2 正确性","多用户隔离自动化测试","防止越权","两个用户覆盖列表、详情、写入、聚合和文件访问","路由多","开发负责人","2–4天","P0"],
    ["V0.2 正确性","替换 Mock 文案生成","让商品资料真实可用","基于审核事实生成俄语文案；无 Mock；保存模型/Prompt/证据","AI 幻觉与费用","AI开发","2–3天","P0"],
    ["V0.2 正确性","母图金标准样本集","稳定验证抠图完整性","至少 50 张复杂图；完整率/残留/边缘指标可重复","标注成本","AI开发+运营","2–4天","P0"],
    ["V0.2 正确性","母图 V3.2 收口","修复 bbox 偏窄与排除框冲突","目标完整、广告残留低、像素保持；批准门禁服务端通过","透明/黑色细边","AI开发","2–3天","P0"],
    ["V0.3 测试店闭环","Seedream 真实联调","证明参考图真实传入","脱敏请求快照显示 image 数组；生成图保存成功；watermark=false","付费/API变化","AI开发","0.5–1天","P0"],
    ["V0.3 测试店闭环","单 SKU 测试店发布","证明创建商品链路","PublishJob success；task_id 可查询；平台商品可见","平台校验变化","OZON开发+运营","1–2天","P0"],
    ["V0.3 测试店闭环","多 SKU/图片/价格/库存测试","证明完整写操作","逐接口请求响应、平台截图、回滚步骤齐全","写错数据","OZON开发+运营","2–3天","P0"],
    ["V0.3 测试店闭环","归档/恢复二次确认","控制高风险操作","仅测试店可执行；二次确认；OperationLog 完整","误操作","OZON开发","1天","P0"],
    ["V0.4 稳定性","统一任务状态机","批量任务可续跑","采集/识别/生图/发布统一 pending/running/success/failed/retry","改造范围大","开发负责人","3–5天","P1"],
    ["V0.4 稳定性","日志、健康检查、错误 ID","故障可定位","/health、文件日志、任务关联 ID、关键失败告警","日志噪声","开发负责人","2天","P1"],
    ["V0.4 稳定性","拆分 OZON 大蓝图","降低维护风险","按领域拆分且路由行为不变，测试通过","回归风险","开发负责人","3–5天","P1"],
    ["V0.5 AI 质量","自动视觉 QA","自动发现结构/文字/参数错误","候选图产生 QA 分与差异说明；失败不可选用","模型误判","AI开发","3–5天","P1"],
    ["V0.5 AI 质量","事实证据链","所有 AI 输出可追溯","ProductFactEvidence 有数据；文案/属性引用证据","录入成本","AI开发+运营","2–4天","P1"],
    ["V1.0 试运行","10–20 个真实商品全链路验收","判断系统是否可运营","采集→母图→生图→文案→审核→测试店发布成功率达标","样本差异","项目负责人","1–2周","P0"]
  ];
  writeTable(s,4,["版本","任务","用户价值","验收标准","主要风险","负责人","工作量","优先级"],rows,[18,30,34,52,26,18,14,10],"Roadmap");
}

// 11 证据口径
{
  const s=wb.worksheets.getItem("11_证据口径");
  title(s,"证据与判断口径","本工作簿是第一版现状基线，不替代逐功能实测；所有结论均可回到证据位置复核","F");
  const rows=[
    ["基线日期","2026-06-25","固定日期","后续开发应更新版本而非覆盖历史"],
    ["代码扫描","G:\\inventory","rg、Git、源码只读检查","统计路由、模型、模板、TODO、Mock 与关键服务"],
    ["数据库","G:\\inventory\\data.db","SQLite 只读连接","统计 61 张业务表与核心行数，未修改数据"],
    ["Git","main...origin/main [ahead 32]","git status/log/diff","工作区有 1 个已修改模板和多项未跟踪内容"],
    ["运行验证",".venv Python 启动器","py_compile/import 尝试","失败原因是虚拟环境启动器引用不存在的 Python"],
    ["可用","有实现证据，并有真实数据/使用迹象；仍可能需要补异常测试","状态定义","不是“绝对无缺陷”"],
    ["待验证","代码/页面存在，但没有足够真实验收证据","状态定义","默认不视为可生产"],
    ["开发中","近期持续修改，目标方案已形成但尚未稳定验收","状态定义","母图和图片生成属于此类"],
    ["有缺陷","已确认不能满足目标或无法运行","状态定义","Mock 文案、失效虚拟环境、备份恢复"],
    ["未开始","关键能力尚无实现或证据","状态定义","自动化测试、完整自动 QA"],
    ["限制","未调用付费 Seedream、未向 OZON 执行写操作、未登录页面逐项点击","审查边界","这些需要用户授权与测试店环境"],
    ["下一次更新","完成每个路线图任务后更新功能状态、验收矩阵和风险状态","维护规则","保留基线版本和变更日期"]
  ];
  writeTable(s,4,["证据项","位置/值","方法/口径","说明"],rows,[20,48,32,54],"Evidence");
}

// 00 Dashboard after source sheets exist
{
  const s=wb.worksheets.getItem("00_项目总览");
  title(s,"项目全景基线","2026-06-25｜目标：让项目可理解、可验证、可接管","L");
  s.getRange("A4:C4").merge(); s.getRange("A4").values=[["项目定位"]];
  s.getRange("A4:C4").format={fill:C.teal,font:{bold:true,color:C.white},rowHeight:24};
  s.getRange("A5:C7").merge(); s.getRange("A5").values=[["仓库记账 + OZON 跨境电商运营的一体化 Flask 系统。库存主流程已有真实数据；OZON 与 AI 图片链路正处于开发、联调和试运行阶段。"]];
  s.getRange("A5:C7").format={fill:C.lightGray,wrapText:true,verticalAlignment:"center",font:{size:11,color:C.ink}};
  s.getRange("D4:F4").merge(); s.getRange("D4").values=[["基线结论"]];
  s.getRange("D4:F4").format={fill:C.amber,font:{bold:true,color:C.white},rowHeight:24};
  s.getRange("D5:F7").merge(); s.getRange("D5").values=[["系统“有功能”但尚未“可放心接管”。当前最优先不是继续扩功能，而是修复运行环境、备份恢复、Mock 文案、自动化测试与测试店发布证据。"]];
  s.getRange("D5:F7").format={fill:C.lightAmber,wrapText:true,verticalAlignment:"center",font:{size:11,color:C.ink}};
  s.getRange("G4:I4").merge(); s.getRange("G4").values=[["规模"]];
  s.getRange("G4:I4").format={fill:C.blue,font:{bold:true,color:C.white},rowHeight:24};
  s.getRange("G5:I7").merge(); s.getRange("G5").values=[["192 路由｜62 模型类｜61 数据表｜61 模板\nSQLite 约 481MB｜本地领先远端 32 提交"]];
  s.getRange("G5:I7").format={fill:C.lightBlue,wrapText:true,verticalAlignment:"center",font:{size:11,color:C.ink}};
  s.getRange("J4:L4").merge(); s.getRange("J4").values=[["核心判断"]];
  s.getRange("J4:L4").format={fill:C.red,font:{bold:true,color:C.white},rowHeight:24};
  s.getRange("J5:L7").merge(); s.getRange("J5").values=[["P0 风险集中在：无法按文档启动、备份不可证明、AI 文案仍 Mock、零自动化测试、AI 结果与 OZON 写操作缺少真实闭环验收。"]];
  s.getRange("J5:L7").format={fill:C.lightRed,wrapText:true,verticalAlignment:"center",font:{size:11,color:C.ink}};

  section(s,9,"功能状态（公式取自 03_功能清单）","F");
  s.getRange("A10:B17").values=[
    ["状态","数量"],["可用",null],["待验证",null],["开发中",null],["有缺陷",null],["未开始",null],["暂停",null],["废弃",null]
  ];
  s.getRange("B11:B17").formulas=[
    ["=COUNTIF('03_功能清单'!$C$5:$C$100,\"可用\")"],
    ["=COUNTIF('03_功能清单'!$C$5:$C$100,\"待验证\")"],
    ["=COUNTIF('03_功能清单'!$C$5:$C$100,\"开发中\")"],
    ["=COUNTIF('03_功能清单'!$C$5:$C$100,\"有缺陷\")"],
    ["=COUNTIF('03_功能清单'!$C$5:$C$100,\"未开始\")"],
    ["=COUNTIF('03_功能清单'!$C$5:$C$100,\"暂停\")"],
    ["=COUNTIF('03_功能清单'!$C$5:$C$100,\"废弃\")"]
  ];
  s.getRange("A10:B17").format.borders={preset:"all",style:"thin",color:C.border};
  s.getRange("A10:B10").format={fill:C.navy,font:{bold:true,color:C.white}};
  const ch1=s.charts.add("bar",s.getRange("A10:B15")); ch1.title="功能成熟度：待验证项占主导"; ch1.hasLegend=false; ch1.setPosition("D9","F21");

  s.getRange("G9:L9").merge();
  s.getRange("G9").values=[["风险级别（公式取自 09_风险清单）"]];
  s.getRange("G9:L9").format={
    fill:C.teal,font:{bold:true,color:C.white,size:12},rowHeight:24,
    verticalAlignment:"center"
  };
  s.getRange("G10:H14").values=[["级别","数量"],["P0",null],["P1",null],["P2",null],["P3",null]];
  s.getRange("H11:H14").formulas=[
    ["=COUNTIF('09_风险清单'!$B$5:$B$100,\"P0\")"],
    ["=COUNTIF('09_风险清单'!$B$5:$B$100,\"P1\")"],
    ["=COUNTIF('09_风险清单'!$B$5:$B$100,\"P2\")"],
    ["=COUNTIF('09_风险清单'!$B$5:$B$100,\"P3\")"]
  ];
  s.getRange("G10:H14").format.borders={preset:"all",style:"thin",color:C.border};
  s.getRange("G10:H10").format={fill:C.navy,font:{bold:true,color:C.white}};
  const ch2=s.charts.add("doughnut",s.getRange("G10:H14")); ch2.title="风险分布"; ch2.hasLegend=true; ch2.setPosition("J9","L21");

  section(s,23,"下一步最应该做什么","L");
  s.getRange("A24:L29").values=[
    ["顺序","任务","为什么现在做","完成标志","","","","","","","",""],
    ["1","重建 Python 环境并修正启动/停止脚本","当前无法按仓库说明运行，所有验证都失去基础","干净环境可启动 Flask 与 Waitress","","","","","","","",""],
    ["2","做数据库+图片一致性备份和恢复演练","481MB 真实业务数据是最大资产","异机恢复后登录、库存、OZON 页面与图片可用","","","","","","","",""],
    ["3","建立 P0 自动化测试与双用户隔离测试","192 条路由靠人工无法安全回归","核心库存、权限、发布门禁测试可重复运行","","","","","","","",""],
    ["4","替换 Mock 文案并收口母图 V3.2","AI 结果正确性是后续发布的前提","文案可追溯；50 张抠图样本达到验收指标","","","","","","","",""],
    ["5","完成 Seedream 与 OZON 测试店真实闭环","用真实外部结果决定系统是否可试运行","候选图有参考图证据；PublishJob 有成功 task_id","","","","","","","",""]
  ];
  for(let r=24;r<=29;r++){ s.getRange(`D${r}:L${r}`).merge(); }
  s.getRange("A24:L24").format={fill:C.navy,font:{bold:true,color:C.white},rowHeight:28};
  s.getRange("A24:L29").format.wrapText=true;
  s.getRange("A24:L29").format.borders={preset:"all",style:"thin",color:C.border};
  s.getRange("A:A").format.columnWidth=10; s.getRange("B:B").format.columnWidth=26;
  s.getRange("C:C").format.columnWidth=42; s.getRange("D:L").format.columnWidth=11;
  s.freezePanes.freezeRows(3);
}

// common finishing touches
for (const n of names) {
  const s=wb.worksheets.getItem(n);
  const used=s.getUsedRange();
  if(used) {
    used.format.font = {...(used.format.font||{}), name:"Microsoft YaHei"};
    used.format.verticalAlignment = used.format.verticalAlignment || "top";
  }
}

// Date cell on overview evidence
wb.worksheets.getItem("01_项目概述").getRange("F1").format.numberFormat="yyyy-mm-dd";

const inspect = await wb.inspect({kind:"sheet,table,formula,drawing",maxChars:12000,tableMaxRows:3,tableMaxCols:8});
await fs.writeFile(`${outputDir}/inspect.txt`, inspect.ndjson ?? String(inspect), "utf8");

for (const n of names) {
  const preview = await wb.render({sheetName:n,autoCrop:"all",scale:0.9,format:"png"});
  await fs.writeFile(`${outputDir}/${n}.png`, new Uint8Array(await preview.arrayBuffer()));
}

const out=await SpreadsheetFile.exportXlsx(wb);
await out.save(`${outputDir}/inventory_project_baseline_2026-06-25.xlsx`);
console.log(`${outputDir}/inventory_project_baseline_2026-06-25.xlsx`);
