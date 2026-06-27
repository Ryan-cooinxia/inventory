import fs from "node:fs/promises";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const finalDir = "G:/inventory/项目改动日志";
const finalPath = `${finalDir}/2026-06-25_AI生图商品理解与事实档案开发文档.xlsx`;
await fs.mkdir(finalDir, { recursive: true });

const wb = Workbook.create();
const sheetNames = ["00_开发总览","01_目标与范围","02_商品事实模型","03_开发任务清单","04_页面与流程","05_验收标准","06_风险与决策","07_开发日志"];
sheetNames.forEach(n => wb.worksheets.add(n));

const C={navy:"#17324D",teal:"#0F766E",blue:"#2563EB",green:"#15803D",amber:"#D97706",red:"#DC2626",
  white:"#FFFFFF",ink:"#172033",gray:"#64748B",line:"#CBD5E1",light:"#F1F5F9",
  lightBlue:"#DBEAFE",lightGreen:"#DCFCE7",lightAmber:"#FEF3C7",lightRed:"#FEE2E2"};
function col(n){let s="";while(n){n--;s=String.fromCharCode(65+n%26)+s;n=Math.floor(n/26)}return s}
function setup(s,title,sub,last="H"){
  s.showGridLines=false;
  s.getRange(`A1:${last}1`).merge(); s.getRange("A1").values=[[title]];
  s.getRange(`A1:${last}1`).format={fill:C.navy,font:{bold:true,color:C.white,size:18},rowHeight:34,verticalAlignment:"center"};
  s.getRange(`A2:${last}2`).merge(); s.getRange("A2").values=[[sub]];
  s.getRange(`A2:${last}2`).format={fill:"#E8EEF5",font:{color:C.gray,italic:true,size:10},rowHeight:26,wrapText:true,verticalAlignment:"center"};
}
function section(s,row,text,last="H"){
  s.getRange(`A${row}:${last}${row}`).merge(); s.getRange(`A${row}`).values=[[text]];
  s.getRange(`A${row}:${last}${row}`).format={fill:C.teal,font:{bold:true,color:C.white,size:12},rowHeight:25};
}
function table(s,row,headers,rows,widths,name){
  const ec=col(headers.length), er=row+rows.length;
  s.getRange(`A${row}:${ec}${er}`).values=[headers,...rows];
  s.getRange(`A${row}:${ec}${row}`).format={fill:C.navy,font:{bold:true,color:C.white},rowHeight:28,wrapText:true};
  s.getRange(`A${row}:${ec}${er}`).format.wrapText=true;
  s.getRange(`A${row}:${ec}${er}`).format.verticalAlignment="top";
  s.getRange(`A${row}:${ec}${er}`).format.borders={insideHorizontal:{style:"thin",color:"#E2E8F0"},bottom:{style:"thin",color:C.line}};
  widths.forEach((w,i)=>s.getRange(`${col(i+1)}:${col(i+1)}`).format.columnWidth=w);
  s.tables.add(`A${row}:${ec}${er}`,true,name);
  s.freezePanes.freezeRows(row);
  return er;
}
function priorityFill(p){return ({P0:C.lightRed,P1:"#FFEDD5",P2:C.lightAmber,P3:C.lightBlue})[p]||C.white}
function statusFill(v){return ({"未开始":C.light,"开发中":C.lightBlue,"待验证":C.lightAmber,"已完成":C.lightGreen,"有缺陷":C.lightRed,"暂停":"#E5E7EB"})[v]||C.white}

// 01
{
 const s=wb.worksheets.getItem("01_目标与范围"); setup(s,"AI 生图功能目标与范围","核心目标不是“生成图片”，而是生成能够吸引点击、解释商品并促进下单的可信电商图片","F");
 const rows=[
 ["业务目标","针对真实产品生成主图、SKU 图和详情图，提高点击、浏览、理解与下单转化","P0"],
 ["目标用户","OZON 跨境电商运营人员、图片审核人员、商品资料整理人员","P0"],
 ["核心输入","商品链接、网页文字、参数、SKU、包装清单、采集图片、产品/配件母图、人工补充信息","P0"],
 ["核心输出","已审核商品事实档案、图片方案、参考素材绑定、候选图、QA 结果、可发布图片组","P0"],
 ["主图职责","让客户快速识别商品，突出核心价值，产生点击欲望；不得改变真实产品结构","P0"],
 ["SKU 图职责","准确区分型号、颜色、数量、版本和包装内容；不同 SKU 不得串图串参数","P0"],
 ["详情图职责","回答买家疑问、证明卖点、展示功能/细节/尺寸/场景，减少购买顾虑","P0"],
 ["当前基础","已能从部分来源图片识别主体并生成透明产品母图；已有多模型生图与参考图代码骨架","事实"],
 ["当前缺口","系统尚缺可靠的商品多模态理解、事实证据链、冲突确认和自动视觉 QA","P0"],
 ["最紧急开发","商品多模态理解 + Product Brief 商品事实档案 + 证据链 + 人工确认页面","P0"],
 ["本阶段不优先","扩充更多模型、更多风格、无事实约束的批量生图、完全自动发布","边界"],
 ["核心原则","只有已确认事实才能进入图片方案和 Prompt；AI 推断、未知和冲突信息不得伪装成事实","P0"]
 ];
 table(s,4,["主题","明确要求","级别"],rows,[22,72,12],"GoalScope");
}

// 02
{
 const s=wb.worksheets.getItem("02_商品事实模型"); setup(s,"商品事实档案与证据模型","建立生图前唯一可信的 Product Brief；每条事实必须能回到网页、图片或人工确认","H");
 const rows=[
 ["product_identity","商品身份","name, brand, model, category, product_type","链接标题、详情、图片文字","品牌/型号不确定时必须待确认","主图/全部"],
 ["physical_structure","物理结构","shape, proportion, color, materials, components, buttons_ports","产品母图、细节图","形状、数量、位置属于不可变结构","全部图片"],
 ["sku_variants","SKU 差异","sku, version, color, size, quantity, package_contents, reference_images","SKU 文本、SKU 图、包装图","SKU 之间不得共享未经确认的差异","SKU图"],
 ["verified_parameters","已确认参数","name, value, unit, applicable_sku","详情参数图、网页参数表","参数需保留单位和适用 SKU","详情图"],
 ["selling_points","卖点","claim, buyer_value, proof_points, priority","网页描述、图片、运营确认","卖点必须有事实或视觉证据","主图/详情图"],
 ["usage_scenarios","使用场景","scene, environment, prerequisite, applicable_sku","详情图、说明书、人工确认","不得把不适用场景用于商品","详情图"],
 ["target_customers","目标客户","user_type, need, pain_point","商品用途与运营判断","属于策略判断，不等于产品事实","图片规划"],
 ["buyer_questions","买家疑问","question, answer, evidence, image_role","评论/运营经验/商品信息","每张详情图建议只回答一组疑问","详情图"],
 ["immutable_features","不可变特征","feature, location, count, visual_reference","母图、细节图","生图时必须严格保护","全部图片"],
 ["prohibited_claims","禁用表达","claim, reason, regulation_source","平台规则、人工配置","无认证、无证据、夸大词禁止使用","文案/图片"],
 ["unknown_fields","未知信息","field, reason, required_action","信息缺失","不得自动补全","阻断/警告"],
 ["conflicts","信息冲突","field, values, sources, resolution","多图片/网页冲突","人工解决前不得进入 Prompt","阻断"],
 ["evidence","事实证据","source_type, source_id, excerpt/region, confidence, confirmed_by","网页段落、图片框、人工输入","所有 P0 事实至少一条证据","审计"]
 ];
 table(s,4,["对象","中文含义","建议字段","来源","关键规则","主要消费者"],rows,[24,22,50,32,52,22],"FactModel");
 section(s,20,"事实状态与使用门禁","H");
 const statusRows=[
 ["extracted","AI/解析器已提取，尚未确认","不能用于高风险参数或发布事实","待人工审核"],
 ["inferred","AI 根据上下文推断","仅供建议，不得直接进入 Prompt","补充证据或拒绝"],
 ["verified","证据充分且规则校验通过","可进入图片规划","仍可人工修订"],
 ["confirmed","由运营人员明确确认","可用于图片 Prompt 与发布","记录确认人和时间"],
 ["conflict","多个来源矛盾","阻断相关图片任务","人工选择正确值"],
 ["unknown","没有可靠信息","不得编造","人工补充或降低图片任务范围"],
 ["rejected","错误或不适用","禁止使用","保留拒绝原因"]
 ];
 table(s,21,["状态","含义","系统行为","处理动作"],statusRows,[18,36,52,34],"FactStatus");
}

const tasks=[
["T-001","商品理解","定义 Product Brief JSON Schema","统一网页、图片、SKU、参数、证据输出结构","P0","未开始","后端","Schema 文档+校验器通过样例"],
["T-002","数据模型","完善 ProductFactEvidence 实际落库","让每条事实可追溯","P0","未开始","后端","事实可关联来源图片/网页片段/确认人"],
["T-003","网页解析","提取标题、详情、参数表、SKU、包装清单","形成文字事实候选","P0","未开始","采集服务","至少 3 类来源页面抽样正确"],
["T-004","图片理解","逐张识别产品、配件、文字、参数、场景、SKU","形成图片事实候选","P0","未开始","视觉服务","每张图返回结构化结果与区域证据"],
["T-005","主体与配件","区分主商品、附属配件、广告元素","确保母图与包装信息正确","P0","开发中","视觉/抠图","主体完整，广告文字/logo 不作为配件"],
["T-006","SKU归属","图片与 SKU 自动匹配","防止版本、颜色、配件混用","P0","未开始","后端+视觉","每张 SKU 图有明确 sku_refs 与置信度"],
["T-007","事实合并","同义归一、去重、单位标准化","生成可读商品档案","P0","未开始","后端","同一事实不重复，原始值仍可追溯"],
["T-008","冲突检测","识别网页、图片、SKU 之间的矛盾","阻止错误信息进入生图","P0","未开始","后端","冲突列表可查看来源并人工裁决"],
["T-009","人工确认页","审核、修改、拒绝、补充事实","让运营掌控最终事实","P0","未开始","前后端","所有关键事实可确认并记录历史"],
["T-010","事实版本快照","生图时保存 Product Brief 版本","保证生成结果可复现","P0","未开始","后端","候选图记录 fact_snapshot/version"],
["T-011","图片任务规划","按主图/SKU/详情图生成槽位任务","将事实转成图片职责","P0","未开始","规则服务","每槽位含疑问、主张、证据、禁改项"],
["T-012","参考素材绑定","自动绑定母图、SKU图、配件图、结构/参数证据图","为生图提供真实依据","P0","开发中","图片服务","无可靠参考图时阻断或明确降级"],
["T-013","Prompt 构建","仅从确认事实生成 Prompt","避免幻觉与 SKU 串用","P0","未开始","AI服务","Prompt 中每个参数可回溯事实 ID"],
["T-014","候选图生成","同任务生成多候选并保存完整请求快照","支持比较与返修","P1","开发中","生图服务","保存模型、Prompt、参考图、事实版本"],
["T-015","自动视觉 QA","检查结构、部件、SKU、文字、参数、清晰度、水印","阻止错误图进入选用","P1","未开始","视觉服务","失败候选不能批准，输出问题区域"],
["T-016","定向返修","根据 QA 问题只修改错误部分","降低反复重做成本","P1","未开始","生图服务","记录 parent_candidate 和返修指令"],
["T-017","人工选图","对候选图评分、选择、拒绝和备注","沉淀可用结果","P1","开发中","前后端","最终图必须人工选择"],
["T-018","效果反馈","记录点击率、转化率和人工评价","验证图片是否真正促进下单","P2","未开始","数据分析","图片版本与运营指标可关联"],
["T-019","样本与测试","建立 10–20 个真实商品端到端样本","验证不同产品类型","P0","未开始","项目","事实准确率和图片合格率有统计"],
["T-020","文档维护","每次改动更新本 Excel 和 Markdown 日志","保证可接管与可追溯","P0","已完成","全体开发","配置规则已写入 AGENTS/CLAUDE"]
];
{
 const s=wb.worksheets.getItem("03_开发任务清单"); setup(s,"开发任务清单","最紧急路径：商品事实档案 → 证据链 → 人工确认 → 图片任务规划 → 参考图绑定 → 自动 QA","H");
 const end=table(s,4,["编号","模块","任务","用户价值","优先级","状态","负责人","验收产物"],tasks,[11,18,38,38,10,12,16,46],"DevTasks");
 s.getRange(`E5:E${end}`).dataValidation={rule:{type:"list",values:["P0","P1","P2","P3"]}};
 s.getRange(`F5:F${end}`).dataValidation={rule:{type:"list",values:["未开始","开发中","待验证","已完成","有缺陷","暂停"]}};
 tasks.forEach((r,i)=>{s.getRange(`E${i+5}`).format.fill=priorityFill(r[4]);s.getRange(`F${i+5}`).format.fill=statusFill(r[5]);});
}

// 04
{
 const s=wb.worksheets.getItem("04_页面与流程"); setup(s,"页面与核心流程","用户必须能看懂：系统从哪里得到事实、哪些是推断、哪些需要自己确认","G");
 const rows=[
 ["1","进入采集商品","商品链接、采集图片","加载来源资料与已有母图","商品资料工作台","采集失败可补录/重新抓取"],
 ["2","批量图片理解","全部来源图","识别主体、配件、文字、参数、场景和 SKU","逐图分析结果","失败图片可单独重试"],
 ["3","网页信息解析","标题、详情、参数、SKU","提取结构化文字事实","文字事实候选","保留原文与位置"],
 ["4","事实合并","图片事实+网页事实","去重、归一、单位标准化","Product Brief 草稿","不覆盖原始证据"],
 ["5","冲突与未知检查","所有事实候选","标记冲突、未知、低置信度","待处理清单","P0 冲突阻断下一步"],
 ["6","人工确认","事实、证据、冲突","确认/修改/拒绝/补充","已确认 Product Brief","保存操作人和历史"],
 ["7","产品/SKU/配件母图确认","抠图结果","选择主母图、SKU母图、配件母图","可复用参考素材","不完整母图不得通过"],
 ["8","图片方案生成","确认事实+买家疑问","生成主图/SKU/详情槽位任务","图片任务清单","允许人工调整槽位"],
 ["9","参考素材绑定","母图和证据图","为每个槽位绑定参考图与允许事实","可执行生成任务","无可靠参考时阻断/降级"],
 ["10","候选图生成","任务、Prompt、参考图","调用模型并保存快照","多张候选图","外部失败可重试"],
 ["11","自动 QA","候选图+真实参考","检测结构/文字/参数/SKU/水印","QA 状态与问题区域","失败不得选用"],
 ["12","人工选图与返修","候选图、QA","选用/拒绝/定向返修","最终图片组","保留版本链"],
 ["13","发布与效果反馈","最终图、平台数据","发布后关联点击/转化和反馈","效果记录","用于优化规则而非自动篡改事实"]
 ];
 table(s,4,["步骤","页面/环节","输入","系统动作","输出","失败处理"],rows,[10,24,32,48,30,38],"PageFlow");
}

// 05
{
 const s=wb.worksheets.getItem("05_验收标准"); setup(s,"分阶段验收标准","只有真实样本通过，任务状态才能从“待验证”改为“已完成”","H");
 const rows=[
 ["P0-A","商品事实档案","任意采集商品能生成结构化 Product Brief","字段包含身份、结构、SKU、参数、卖点、场景、未知、冲突、证据","10 个真实商品","未开始","P0","不能出现无来源的已确认事实"],
 ["P0-A","证据链","每条关键事实可查看来源","能定位网页片段或图片及区域；记录置信度和确认人","关键事实 100%","未开始","P0","ProductFactEvidence 必须有真实数据"],
 ["P0-A","SKU正确性","不同 SKU 信息不混用","颜色、型号、版本、包装内容、图片引用正确","至少 3 个多 SKU 商品","未开始","P0","无证据时标未知"],
 ["P0-A","冲突处理","矛盾信息被系统识别并阻断","可选择正确值、保留其他值和来源","构造 10 组冲突","未开始","P0","未解决不得生成相关图"],
 ["P0-A","人工确认","运营可编辑、拒绝、补充事实","所有操作有用户、时间和历史","完整操作回归","未开始","P0","服务端权限校验"],
 ["P0-B","图片规划","自动生成主图/SKU/详情任务","每任务含角色、买家疑问、主张、证据、禁改项","10 个商品","未开始","P0","任务可人工修改"],
 ["P0-B","参考图绑定","每任务绑定正确产品/SKU/配件/参数图","无可靠参考图时系统阻断或警告","覆盖无白底图情况","待验证","P0","package 槽位不能误用主图"],
 ["P0-B","Prompt事实约束","Prompt 只包含确认事实","随机抽查参数均可追溯 fact_id","20 个任务","未开始","P0","不得自动补未知参数"],
 ["P1","候选图快照","每张候选图可复现","保存模型、Prompt、参数、参考图和事实版本","全部候选","待验证","P1","失败请求也保存脱敏信息"],
 ["P1","自动视觉QA","识别结构、部件、SKU、文字和水印错误","输出分数、问题描述、区域；失败图不可批准","至少 100 张候选","未开始","P1","人工可复核/纠错"],
 ["P1","返修链","定向返修保留父子版本","能查看修改原因和结果差异","10 次返修","未开始","P1","不能覆盖原图"],
 ["E2E","端到端闭环","采集→事实→母图→规划→生成→QA→选图","10–20 个真实商品完整通过","真实商品集","未开始","P0","统计成功率和人工耗时"]
 ];
 const end=table(s,4,["阶段","验收项","预期结果","具体标准","测试样本","状态","优先级","备注"],rows,[12,24,40,54,22,12,10,34],"Acceptance");
 rows.forEach((r,i)=>{s.getRange(`F${i+5}`).format.fill=statusFill(r[5]);s.getRange(`G${i+5}`).format.fill=priorityFill(r[6]);});
}

// 06
{
 const s=wb.worksheets.getItem("06_风险与决策"); setup(s,"风险与关键决策","把不能交给模型自行判断的事项提前写成系统规则","H");
 const rows=[
 ["D-001","P0","商品事实","AI 推断被当成事实","生成错误参数和卖点","事实状态分层；只有 verified/confirmed 可使用","已决定","进入数据模型和 Prompt 门禁"],
 ["D-002","P0","SKU","不同 SKU 图片/参数混用","误导买家与售后风险","每条事实和参考图绑定 applicable_sku","已决定","无归属则待确认"],
 ["D-003","P0","参考图","没有白底图或产品图含广告","无法可靠生图","先抠取产品/配件母图；不完整结果不得使用","已决定","现有抠图继续迭代"],
 ["D-004","P0","视觉检测","bbox 不精确","裁掉产品结构","bbox 只作种子，mask 决定真实边界","已决定","V3.2 原则"],
 ["D-005","P0","生图结构","模型改变按钮、部件、比例","商品失真","不可变结构+参考图+自动 QA+人工审核","已决定","Prompt 单独不足"],
 ["D-006","P0","文字参数","图片出现乱码或无证据参数","降低信任/违规","文字层与生成背景分离优先；参数必须有证据","待实施","可考虑后期排版"],
 ["D-007","P0","自动化","系统自动生成并批准","错误结果直接流入发布","AI 只能建议，最终图必须人工选择","已决定","服务端门禁"],
 ["D-008","P1","成本","逐图视觉理解和多候选生成费用高","处理成本不可控","缓存事实、按图片哈希复用、分层模型、记录费用","待实施","先测单商品成本"],
 ["D-009","P1","可复现","规则/模型变化后无法解释历史结果","难以返修和审计","保存事实、Prompt、参考图、模型配置快照","已决定","候选图级快照"],
 ["D-010","P1","效果目标","只看美观不看转化","无法验证业务价值","后续关联点击率/转化率和人工评分","规划中","P2 数据闭环"],
 ["D-011","P0","文档漂移","代码修改但开发文档不更新","项目再次失控","每次 AI 生图改动必须更新本 Excel 与 Markdown 日志","已决定","已写入 AGENTS/CLAUDE"]
 ];
 const end=table(s,4,["编号","优先级","领域","风险/决策点","影响","处理原则","状态","实施备注"],rows,[11,10,18,40,36,50,14,32],"RiskDecisions");
 rows.forEach((r,i)=>s.getRange(`B${i+5}`).format.fill=priorityFill(r[1]));
}

// 07 living log
{
 const s=wb.worksheets.getItem("07_开发日志"); setup(s,"持续开发日志","本工作表为 AI 生图功能的主变更记录；以后每次改动必须追加一行，不得删除历史记录","J");
 const rows=[
 [new Date("2026-06-25T00:00:00"),"DOC-001","需求基线","明确 AI 生图业务目标与下一阶段优先级","建立商品多模态理解、Product Brief、证据链、人工确认、图片规划与 QA 的开发方案","AGENTS.md; CLAUDE.md; 本Excel","未执行代码测试","已完成","Codex","首次建立主开发文档"],
 [null,"","","","","","","","",""]
 ];
 const end=table(s,4,["日期","变更编号","类型","需求/问题","改动内容","影响文件/模块","测试与证据","状态","执行者","备注"],rows,[14,14,16,42,58,38,38,14,16,34],"DevelopmentLog");
 s.getRange(`A5:A${end}`).format.numberFormat="yyyy-mm-dd";
 s.getRange(`C5:C200`).dataValidation={rule:{type:"list",values:["需求基线","需求变更","新增功能","缺陷修复","数据模型","页面交互","Prompt/规则","模型配置","测试验收","文档更新"]}};
 s.getRange(`H5:H200`).dataValidation={rule:{type:"list",values:["未开始","开发中","待验证","已完成","有缺陷","回滚"]}};
 section(s,8,"每次改动必须填写的最小信息","J");
 s.getRange("A9:J12").values=[
 ["必填项","日期、变更编号、类型、需求/问题、改动内容、影响文件/模块、测试与证据、状态、执行者","","","","","","","",""],
 ["状态规则","代码写完但未验证=待验证；真实样本通过=已完成；发现回归=有缺陷","","","","","","","",""],
 ["同步规则","同一次改动还必须创建/更新 项目改动日志 下的 Markdown 日志","","","","","","","",""],
 ["完成定义","代码 + 测试证据 + Markdown 日志 + 本 Excel 更新，四项齐全才算完成","","","","","","","",""]
 ];
 for(let r=9;r<=12;r++) s.getRange(`B${r}:J${r}`).merge();
 s.getRange("A9:J12").format.wrapText=true;
 s.getRange("A9:A12").format={fill:C.lightBlue,font:{bold:true,color:C.ink}};
 s.getRange("A9:J12").format.borders={preset:"all",style:"thin",color:C.line};
}

// 00 dashboard
{
 const s=wb.worksheets.getItem("00_开发总览"); setup(s,"AI 生图下一阶段开发总览","主开发文档｜持续维护｜最后建立日期：2026-06-25","L");
 const cards=[
 ["A4:C4","A5:C7","开发目的","用真实商品资料生成更能吸引点击、解释商品并促进下单的主图、SKU图和详情图",C.teal,C.lightGreen],
 ["D4:F4","D5:F7","最紧急需求","商品多模态理解 + Product Brief 商品事实档案 + 证据链 + 人工确认页面",C.red,C.lightRed],
 ["G4:I4","G5:I7","当前基础","已具备商品采集、主体识别、母图抠图、多模型生图的部分能力",C.blue,C.lightBlue],
 ["J4:L4","J5:L7","完成门槛","只有确认事实才能进入 Prompt；生成结果必须经过自动 QA 和人工选择",C.amber,C.lightAmber]
 ];
 cards.forEach(([h,b,t,v,hc,bc])=>{s.getRange(h).merge();s.getRange(h.split(":")[0]).values=[[t]];s.getRange(h).format={fill:hc,font:{bold:true,color:C.white},rowHeight:24};s.getRange(b).merge();s.getRange(b.split(":")[0]).values=[[v]];s.getRange(b).format={fill:bc,font:{color:C.ink,size:11},wrapText:true,verticalAlignment:"center"};});
 section(s,9,"开发任务状态（取自 03_开发任务清单）","F");
 s.getRange("A10:B15").values=[["状态","数量"],["未开始",null],["开发中",null],["待验证",null],["已完成",null],["有缺陷",null]];
 s.getRange("B11:B15").formulas=[
  ["=COUNTIF('03_开发任务清单'!$F$5:$F$100,\"未开始\")"],
  ["=COUNTIF('03_开发任务清单'!$F$5:$F$100,\"开发中\")"],
  ["=COUNTIF('03_开发任务清单'!$F$5:$F$100,\"待验证\")"],
  ["=COUNTIF('03_开发任务清单'!$F$5:$F$100,\"已完成\")"],
  ["=COUNTIF('03_开发任务清单'!$F$5:$F$100,\"有缺陷\")"]
 ];
 s.getRange("A10:B15").format.borders={preset:"all",style:"thin",color:C.line}; s.getRange("A10:B10").format={fill:C.navy,font:{bold:true,color:C.white}};
 section(s,9,"P0 实施顺序","L");
 s.getRange("G10:L16").values=[
 ["顺序","工作包","完成标志","","",""],
 ["1","事实 Schema + 证据落库","Product Brief 与 ProductFactEvidence 可真实使用","","",""],
 ["2","网页/图片多模态提取","逐图、逐段生成带来源的事实候选","","",""],
 ["3","合并、冲突检测、人工确认","关键事实可确认、拒绝、修改并保留历史","","",""],
 ["4","图片任务规划与参考图绑定","主图/SKU/详情槽位均有职责、事实和参考图","","",""],
 ["5","事实约束 Prompt + 候选图快照","每张图可追溯到事实版本和参考素材","","",""],
 ["6","自动 QA + 人工选图","错误图被阻断，形成可发布图片组","","",""]
 ];
 for(let r=10;r<=16;r++) s.getRange(`I${r}:L${r}`).merge();
 s.getRange("G10:L10").format={fill:C.navy,font:{bold:true,color:C.white}}; s.getRange("G10:L16").format.wrapText=true;s.getRange("G10:L16").format.borders={preset:"all",style:"thin",color:C.line};
 s.getRange("G11:L16").format.rowHeight=34;
 section(s,18,"文档维护规则","L");
 s.getRange("A19:L22").values=[
 ["主文档位置",finalPath,"","","","","","","","","",""],
 ["每次必须更新","03_开发任务清单、05_验收标准、06_风险与决策、07_开发日志","","","","","","","","",""],
 ["同时记录","在 G:\\inventory\\项目改动日志 创建或更新对应 Markdown 改动日志","","","","","","","","",""],
 ["完成定义","代码、测试证据、Markdown 日志、本 Excel 四项同步完成","","","","","","","","",""]
 ];
 for(let r=19;r<=22;r++) s.getRange(`B${r}:L${r}`).merge();
 s.getRange("A19:A22").format={fill:C.lightBlue,font:{bold:true}};s.getRange("A19:L22").format.borders={preset:"all",style:"thin",color:C.line};s.getRange("A19:L22").format.wrapText=true;
 s.getRange("A:A").format.columnWidth=14;s.getRange("B:B").format.columnWidth=26;s.getRange("C:F").format.columnWidth=11;s.getRange("G:G").format.columnWidth=9;s.getRange("H:H").format.columnWidth=30;s.getRange("I:L").format.columnWidth=13;
}

for(const n of sheetNames){
 const s=wb.worksheets.getItem(n), used=s.getUsedRange();
 if(used) used.format.font={...(used.format.font||{}),name:"Microsoft YaHei"};
}

const check=await wb.inspect({kind:"sheet,table,formula",maxChars:9000,tableMaxRows:4,tableMaxCols:10});
await fs.writeFile("G:/inventory/tmp/ai_image_dev_doc/inspect.txt",check.ndjson??String(check),"utf8");
for(const n of sheetNames){
 const p=await wb.render({sheetName:n,autoCrop:"all",scale:0.9,format:"png"});
 await fs.writeFile(`G:/inventory/tmp/ai_image_dev_doc/${n}.png`,new Uint8Array(await p.arrayBuffer()));
}
const out=await SpreadsheetFile.exportXlsx(wb);
await out.save(finalPath);
console.log(finalPath);
