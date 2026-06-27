import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const path = "G:/inventory/项目改动日志/2026-06-25_AI生图商品理解与事实档案开发文档.xlsx";
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(path));

const tasks = wb.worksheets.getItem("03_开发任务清单");
const used = tasks.getUsedRange();
const vals = used.values;
for (let r = 0; r < vals.length; r++) {
  const id = vals[r]?.[0];
  if (["T-021","T-022","T-025"].includes(id)) {
    tasks.getCell(r, 5).values = [["有缺陷"]];
  }
  if (id === "T-023") {
    tasks.getCell(r, 2).values = [["本地图片加载、历史成功任务回填、批量进度与识别幂等"]];
    tasks.getCell(r, 7).values = [["全量处理24张；历史ImageFact回填Evidence；失败有原因；重复运行默认跳过模型但不跳过证据修复"]];
    tasks.getCell(r, 5).values = [["未开始"]];
  }
}

const risks = wb.worksheets.getItem("06_风险与决策");
const riskTable = risks.tables.items.find(t => t.name === "RiskDecisions");
riskTable.rows.add(null, [[
  "D-015","P0","历史数据迁移",
  "已有成功ImageAnalysisJob被新服务跳过，但对应ProductFactEvidence缺失",
  "模型识别成功却永久无法进入产品详情",
  "跳过付费调用前先检查并回填历史ImageFact/parsed_json到证据层",
  "待实施",
  "增加幂等backfill服务及回归测试"
]]);

const log = wb.worksheets.getItem("07_开发日志");
log.getRange("A7:J7").values = [[
  new Date("2026-06-25T00:00:00"),
  "ISSUE-002",
  "缺陷复查",
  "首次修复后仍返回0/24，产品详情仍无图片证据，页面首屏右侧大面积空白",
  "确认旧按钮/旧路由未删除；历史success任务被直接skip但未回填Evidence；base仍为container；产品详情仍是row下裸card。要求合并入口、历史回填和真正重构DOM网格。",
  "blueprints/ozon.py; services/vision_tool.py; templates/ozon/adaptation_workspace.html; templates/base.html",
  "数据库：20条ImageFact、10张成功图片、图片Evidence=0；最新截图与源码复查",
  "有缺陷",
  "Codex",
  "提交信息与实际实现不一致，需二次修复并以浏览器截图验收"
]];
log.getRange("A7").format.numberFormat = "yyyy-mm-dd";

const out = await SpreadsheetFile.exportXlsx(wb);
await out.save(path);

for (const sheetName of ["03_开发任务清单","06_风险与决策","07_开发日志"]) {
  const preview = await wb.render({sheetName, autoCrop:"all", scale:0.75, format:"png"});
  await fs.writeFile(`G:/inventory/tmp/update_ai_dev_doc_followup/${sheetName}.png`, new Uint8Array(await preview.arrayBuffer()));
}

const errors = await wb.inspect({
  kind:"match",
  searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options:{useRegex:true,maxResults:100},
  summary:"formula errors"
});
await fs.writeFile("G:/inventory/tmp/update_ai_dev_doc_followup/errors.txt", errors.ndjson ?? String(errors), "utf8");
console.log(path);
