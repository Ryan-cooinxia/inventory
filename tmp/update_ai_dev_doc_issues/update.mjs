import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const path = "G:/inventory/项目改动日志/2026-06-25_AI生图商品理解与事实档案开发文档.xlsx";
const input = await FileBlob.load(path);
const wb = await SpreadsheetFile.importXlsx(input);

const tasksSheet = wb.worksheets.getItem("03_开发任务清单");
const taskTable = tasksSheet.tables.items.find(t => t.name === "DevTasks");
taskTable.rows.add(null, [
  ["T-021","图片理解","统一两个图片识别入口","避免旧接口与新服务行为不一致","P0","有缺陷","后端","两个按钮调用同一 vision_tool 服务，返回逐图结果"],
  ["T-022","事实证据","修复图片事实关联 ProductFact/ProductFactSku","让图片识别结果进入统一证据链","P0","有缺陷","后端","24 张图片结果可写入 ProductFactEvidence 并正确绑定 SKU"],
  ["T-023","图片理解","本地图片加载、批量进度与识别幂等","支持本地相对路径并避免重复事实","P0","未开始","后端+前端","全量处理24张；失败有原因；重复运行默认跳过"],
  ["T-024","商品理解","生成跨品类产品详情聚合视图","将网页和图片证据整理为可核对商品档案","P0","开发中","后端+前端","动态分组、证据计数、未知/冲突/完整度可见"],
  ["T-025","页面交互","重构适配工作台宽屏布局","解决网格错位、内容过窄和大面积空白","P1","有缺陷","前端","container-fluid；合法Bootstrap列；多分辨率布局通过"]
]);

const acceptSheet = wb.worksheets.getItem("05_验收标准");
const acceptTable = acceptSheet.tables.items.find(t => t.name === "Acceptance");
acceptTable.rows.add(null, [
  ["P0-C","源图片读取","站内相对URL和本地路径均可识别","优先 local_path；24张图片逐图返回结果","24张本地来源图","有缺陷","P0","当前旧入口为0/24"],
  ["P0-C","图片证据入库","视觉事实进入统一证据链","ImageFact 与 ProductFactEvidence 数量和关联正确","source_id=49","有缺陷","P0","当前图片证据为0"],
  ["P0-C","识别幂等","重复分析不制造重复事实","相同图片+模型+Prompt默认跳过，可显式重跑修订","连续执行2次","未开始","P0","当前ImageFact重复"],
  ["P1","工作台布局","宽屏和不同缩放比例下结构稳定","无裸card作为row直接子元素；详情、事实、OZON区域清晰","1366/1600/1920","有缺陷","P1","当前网格被产品详情卡片破坏"]
]);

const riskSheet = wb.worksheets.getItem("06_风险与决策");
const riskTable = riskSheet.tables.items.find(t => t.name === "RiskDecisions");
riskTable.rows.add(null, [
  ["D-012","P0","识别架构","旧/新两套图片识别流程并存","按钮结果不一致、重复数据、难维护","统一到 services.vision_tool，旧接口只做代理或删除","待实施","禁止继续维护两套Prompt与入库逻辑"],
  ["D-013","P0","数据关联","ProductFact.source_id 不存在且异常被吞掉","模型成功但图片证据无法进入Product Brief","通过group/item解析fact；禁止空except","待实施","错误写入ImageAnalysisJob"],
  ["D-014","P1","页面布局","产品详情裸card插入Bootstrap row","三栏错位、宽屏空白、可读性差","container-fluid + 合法嵌套row/col","待实施","详情作为右侧全宽主视图"]
]);

const logSheet = wb.worksheets.getItem("07_开发日志");
logSheet.getRange("A6:J6").values = [[
  new Date("2026-06-25T00:00:00"),
  "ISSUE-001",
  "缺陷分析",
  "源图片识别0/24、图片证据未进入商品档案、适配工作台布局错位",
  "确认旧入口无法读取相对URL；新服务引用不存在的ProductFact.source_id；异常被静默吞掉；产品详情裸card破坏Bootstrap网格。形成统一识别、事实关联、幂等和布局重构方案。",
  "blueprints/ozon.py; services/vision_tool.py; services/product_fact_service.py; templates/ozon/adaptation_workspace.html; templates/base.html",
  "数据库只读核对：24张媒体、20条ImageFact、13条文本Evidence、0条图片Evidence；截图复核",
  "有缺陷",
  "Codex",
  "仅分析并记录，代码修复尚未实施"
]];
logSheet.getRange("A6").format.numberFormat = "yyyy-mm-dd";

const out = await SpreadsheetFile.exportXlsx(wb);
await out.save(path);

for (const sheetName of ["03_开发任务清单","05_验收标准","06_风险与决策","07_开发日志"]) {
  const preview = await wb.render({sheetName, autoCrop:"all", scale:0.8, format:"png"});
  await fs.writeFile(`G:/inventory/tmp/update_ai_dev_doc_issues/${sheetName}.png`, new Uint8Array(await preview.arrayBuffer()));
}

const errors = await wb.inspect({
  kind:"match",
  searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options:{useRegex:true,maxResults:100},
  summary:"formula errors"
});
await fs.writeFile("G:/inventory/tmp/update_ai_dev_doc_issues/errors.txt", errors.ndjson ?? String(errors), "utf8");
console.log(path);
