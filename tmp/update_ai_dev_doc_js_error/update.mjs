import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const path = "G:/inventory/项目改动日志/2026-06-25_AI生图商品理解与事实档案开发文档.xlsx";
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(path));

const tasks = wb.worksheets.getItem("03_开发任务清单");
const values = tasks.getUsedRange().values;
for (let row = 0; row < values.length; row++) {
  if (values[row]?.[0] === "T-021") {
    tasks.getCell(row, 2).values = [["统一图片识别入口并修复前端脚本语法"]];
    tasks.getCell(row, 3).values = [["确保按钮脚本可加载、请求真实发出，并避免旧/新服务行为不一致"]];
    tasks.getCell(row, 5).values = [["有缺陷"]];
    tasks.getCell(row, 7).values = [["Node语法检查通过；浏览器控制台无SyntaxError；按钮发出统一analyze请求"]];
  }
}

const riskSheet = wb.worksheets.getItem("06_风险与决策");
const riskTable = riskSheet.tables.items.find(t => t.name === "RiskDecisions");
riskTable.rows.add(null, [[
  "D-016","P0","前端质量",
  "模板JavaScript包含多余闭合符和非法多行字符串，导致整段脚本无法加载",
  "AI识别、保存、审核等页面按钮全部可能无反应",
  "提交前强制执行模板脚本语法检查、控制台检查和按钮冒烟测试",
  "待实施",
  "禁止依赖隐式全局event；关键按钮使用稳定ID"
]]);

const log = wb.worksheets.getItem("07_开发日志");
log.getRange("A8:J8").values = [[
  new Date("2026-06-26T00:00:00"),
  "ISSUE-003",
  "缺陷复查",
  "点击AI识别图片按钮完全无反应",
  "定位到adaptation_workspace.html整段脚本语法失败：analyzeImages多余的});；analyzeProductFacts使用非法跨行单引号字符串；同时依赖隐式全局event。另发现api_merge_fact路由装饰器缺失。",
  "templates/ozon/adaptation_workspace.html; blueprints/ozon.py; services/product_fact_service.py",
  "Node模板脚本静态检查返回 SyntaxError: Unexpected token ')'；源码行号复核",
  "有缺陷",
  "Codex",
  "仅完成诊断和文档记录，业务代码尚未修复"
]];
log.getRange("A8").format.numberFormat = "yyyy-mm-dd";

const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(path);

for (const sheetName of ["03_开发任务清单","06_风险与决策","07_开发日志"]) {
  const preview = await wb.render({sheetName, autoCrop:"all", scale:0.75, format:"png"});
  await fs.writeFile(`G:/inventory/tmp/update_ai_dev_doc_js_error/${sheetName}.png`, new Uint8Array(await preview.arrayBuffer()));
}

const errors = await wb.inspect({
  kind:"match",
  searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options:{useRegex:true,maxResults:100},
  summary:"formula errors",
});
await fs.writeFile("G:/inventory/tmp/update_ai_dev_doc_js_error/errors.txt", errors.ndjson ?? String(errors), "utf8");
console.log(path);
