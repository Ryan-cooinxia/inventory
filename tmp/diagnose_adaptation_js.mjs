import fs from "node:fs/promises";
import vm from "node:vm";

const html = await fs.readFile(
  "G:/inventory/templates/ozon/adaptation_workspace.html",
  "utf8",
);
const scripts = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/gi)]
  .map((match) => match[1]);
const js = scripts
  .join("\n")
  .replace(/\{\{[\s\S]*?\}\}/g, "0")
  .replace(/\{%[\s\S]*?%\}/g, "");

try {
  new vm.Script(js, { filename: "adaptation_workspace.js" });
  console.log("JS_PARSE_OK");
} catch (error) {
  console.log(error.stack);
}
