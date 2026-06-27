import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const path = "G:/inventory/项目改动日志/2026-06-25_AI生图商品理解与事实档案开发文档.xlsx";
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(path));
const sheet = wb.worksheets.getItem("07_开发日志");

sheet.getRange("A8:J8").clear({applyTo:"all"});
sheet.getRange("A8:J8").merge();
sheet.getRange("A8").values = [["每次改动必须填写的最小信息"]];
sheet.getRange("A8:J8").format = {
  fill:"#0F766E",
  font:{bold:true,color:"#FFFFFF",name:"Microsoft YaHei"},
  rowHeight:25,
};

sheet.getRange("A13:J13").values = [[
  new Date("2026-06-26T00:00:00"),
  "ISSUE-003",
  "缺陷复查",
  "点击AI识别图片按钮完全无反应",
  "定位到adaptation_workspace.html整段脚本语法失败：analyzeImages多余的});；analyzeProductFacts使用非法跨行单引号字符串；同时依赖隐式全局event。另发现api_merge_fact路由装饰器缺失。",
  "templates/ozon/adaptation_workspace.html; blueprints/ozon.py; services/product_fact_service.py",
  "Node模板脚本静态检查返回 SyntaxError: Unexpected token ')'；源码行号复核",
  "有缺陷",
  "Codex",
  "仅完成诊断和文档记录，业务代码尚未修复",
]];
sheet.getRange("A13").format.numberFormat = "yyyy-mm-dd";
sheet.getRange("A13:J13").format = {
  wrapText:true,
  verticalAlignment:"top",
  borders:{preset:"all",style:"thin",color:"#CBD5E1"},
  font:{name:"Microsoft YaHei"},
};

const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(path);
const preview = await wb.render({sheetName:"07_开发日志",autoCrop:"all",scale:0.75,format:"png"});
await fs.writeFile("G:/inventory/tmp/fix_ai_dev_log_layout/07_开发日志.png",new Uint8Array(await preview.arrayBuffer()));
console.log(path);
