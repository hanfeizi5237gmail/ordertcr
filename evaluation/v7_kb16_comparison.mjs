import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';
const root=path.resolve(import.meta.dirname,'..');
const read=async p=>JSON.parse(await fs.readFile(path.join(root,p),'utf8'));
const safe=v=>v===undefined||v===null?null:typeof v==='object'?JSON.stringify(v):typeof v==='string'&&v.startsWith('=')?`'${v}`:v;
const rows=[]; const overview=[['验证集','模式/阶段','分组','全量事件','自动完成','自动正确','全量正确','A%','B%','C','漏输出','额外输出','备注']];
function addOverview(kind,mode,label,m,note=''){overview.push([kind,mode,label,m.input_events??m.events,m.automatic,m.automatic_correct,m.correct??m.automatic_correct,m.A_percent??m.A,m.B_percent??m.B,m.C_percent??m.C,m.missing_outputs??0,m.unexpected_outputs??0,note]);}
function addLedger(kind,mode,seed,r){const e=r.expected||{};const a=r.actual||{};const ar=a.row||{};const sel=ar.selected||{};const es=e.expected_catalog_item||e.selected||{};rows.push([kind,mode,seed,r.event_id||e.event_id||'',e.kind||r.kind||'',e.company||r.company||'',e.date||r.date||'',e.source_line||r.source_line||'',e.source_line_text||r.source_text||'',es.code||es.sku||'',es.name||'',es.spec||'',sel.code||'',sel.name||ar.name||'',sel.spec||'',e.quantity||'',ar.quantity||'',e.unit||'',ar.unit||'',ar.invoice_quantity||'',sel.price||'',ar.discount||'',ar.unit_price||'',ar.amount||'',ar.status||'',r.automatic??false,r.automatic_correct??false,r.review_correct??'',r.manual_checks_passed??'',(r.errors||[]).join(';'),r.candidate_completeness||'unknown']);}
const audit=await read('evaluation/v7/kb16_final_audit_v5/report.json');
for(const [mode,m] of Object.entries(audit.mode_summaries)) addOverview('KB16三seed原文挑战',mode,'全量54',m,'8801/8802开发、8891已消费保留；C未证');
for(const r of audit.runs.filter(x=>x.mode==='shared')) for(const e of r.events) addLedger('KB16三seed原文挑战','shared',r.seed,e);
const sem=await read('evaluation/v7/semantic_kb16_final/report.json');
for(const [mode,m] of Object.entries(sem.mode_summaries)){addOverview('语义已见回归',mode,'全量84',m.summary,'历史变体，非未见真实订单');addOverview('语义已见回归',mode,'正常72',m.normal,'正常子集');}
for(const r of sem.runs.filter(x=>x.mode==='shared')) for(const e of r.events) addLedger('语义已见回归','shared',r.seed,e);
const fresh=await read('evaluation/v7/fresh_kb16_final_shared/report.json');
addOverview('目录迁移回归','shared','全量324',fresh.full_denominator_summary,'正常306/306；18条异常/控制保留');addOverview('目录迁移回归','shared','正常306',fresh.normal_subset,'C未证');
const led=await read('evaluation/v7/fresh_kb16_final_shared/event_ledger.json');
for(const [seed,v] of Object.entries(led)) for(const r of (v.rows||[])) addLedger('目录迁移回归','shared',seed,r);
const v2=await read('evaluation/v7/kb16_v2_final_bundled_audit_v6/report.json');
for(const [mode,m] of Object.entries(v2.mode_summaries)) addOverview('KB16 v2外部概念挑战',mode,'全量96',m,'8901/8902开发、8991首见保留；8个外部概念；预期含不充分语义假设');
for(const r of v2.runs.filter(x=>x.mode==='shared')) for(const e of r.events) addLedger('KB16 v2外部概念挑战','shared',r.seed,e);
const orig=await read('evaluation/v7/original_kb16_final/report.json');
for(const [k,m] of Object.entries(orig.stations)) addOverview('原始三站回放','shared',m.station,m,'53/42/64事件，历史回放，非独立准确率');
const hist=await read('evaluation/v7/kb16_final_history/replay_audit.json');
const identity=await read('evaluation/v7/kb16_final_history_identity.json');
for(const [mode,m] of Object.entries(hist.metrics)) addOverview('历史随机变形回放',mode,'1551对齐事件',m,'历史oracle回放，不能冒充新订单');
const fixed=[['组','站点','固定SKU一致','分母132','一致率%','对齐分母155','全量分母159','未选','不同历史SKU','undefined','缺输出']];
for(const [g,gv] of Object.entries(identity.groups||{})){for(const [st,m] of Object.entries({all:gv.metrics,...(gv.per_station||{})})){fixed.push([g,st,m.strict132?.correct,m.strict132?.denominator,m.strict132?.rate_percent,m.coverage155?.denominator,m.coverage159?.denominator,m.fixed_sku_outcomes?.unselected||0,m.fixed_sku_outcomes?.different_sku||0,m.undefined_fixed_sku||0,m.output_missing_events||0]);}}
const notes=[['口径','说明'],['当前状态','doing；没有宣布整体95% verified。'],['共享知识','KB16 knowledge_version=16，93概念、2单位、1受限表达结构；SHA f2aa4a599c20cb13454d78a68d3361d0ab498599e9a9b12c9276a9b0a3837663。'],['A','实际自动完成且全字段正确 / 实际自动完成；review/control误自动计A错，append/change/cancel不冒充自动。'],['B','自动正确 / 全量输入事件；KB16 v1 shared=26/54=48.1481%，业务处理正确率51/54=94.4444%；fresh=306/324=94.4444%，正常子集100%。'],['C','候选集合完整性和异常语义未充分独立证明，保持null/unknown；转人工不计自动率。'],['历史固定分母','聊天事件159；与人工对齐155；当前目录可核SKU132；站点53/42/64。历史人工重量、替代和临时调整不作为运行真值。'],['异常','未知金额留空；候选、颜色和原生批注只在已审计范围内声明。'],['v2边界','v2每seed32事件，24条件正常+4 review+4控制；8991作为首见保留但表达池跨seed复用，不能证明未来真实订单泛化；牛心管切段等预期存在目录证据不足。'],['财务','原始159合并财务复读 finance_errors=0；三模式财务机械审计通过。'],['宿主','千问办公、WorkBuddy、真实新增订单、真实系统导入和正式入账未验证。'],['原件','八份 original 文件前后SHA一致；original目录只读。']];
const wb=Workbook.create();
function addSheet(name,data,widths=[]){const s=wb.worksheets.add(name);s.showGridLines=false;const clean=data.map(r=>r.map(safe));s.getRangeByIndexes(0,0,clean.length,clean[0].length).values=clean;s.getRangeByIndexes(0,0,1,clean[0].length).format={fill:'#284B63',font:{bold:true,color:'#FFFFFF'},wrapText:true};s.getUsedRange().format.verticalAlignment='center';s.getUsedRange().format.wrapText=true;s.freezePanes.freezeRows(1);for(const [col,w] of widths)s.getRange(`${col}:${col}`).format.columnWidth=w;return s;}
addSheet('验收概览',overview,[['A',24],['B',28],['C',27],['M',55]]);
addSheet('逐项对比',[['验证集','模式','seed','事件ID','事件类型','公司','日期','源行','原文','预期SKU','预期商品','预期规格','输出SKU','输出商品','输出规格','原始数量','解析数量','原始单位','解析单位','计价数量','原价','折扣','配货单价','销售金额','状态','自动完成','自动正确','人工复核','人工检查','候选完整性','问题'] ,...rows],[['I',38],['AC',48],['AD',60]]);
addSheet('固定分母身份',fixed,[['A',30]]); addSheet('口径与来源',notes,[['A',22],['B',115]]);
await wb.recalculate();
const checks=[];for(const [sn,range] of [['验收概览','A1:M15'],['逐项对比','A1:AD8'],['固定分母身份','A1:K3'],['口径与来源','A1:B12']]){checks.push((await wb.inspect({kind:'table',range:`'${sn}'!${range}`,include:'values,formulas',tableMaxRows:15,tableMaxCols:30})).ndjson);const img=await wb.render({sheetName:sn,range,scale:1.1,format:'png'});await fs.writeFile(path.join(root,`evaluation/v7/kb16_${sn}.png`),new Uint8Array(await img.arrayBuffer()));}
checks.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:100},summary:'formula error scan'})).ndjson);
await fs.writeFile(path.join(root,'evaluation/v7/kb16_comparison_inspect.ndjson'),checks.join('\n'));
const out=await SpreadsheetFile.exportXlsx(wb);await out.save(path.join(root,'evaluation/v7/kb16逐项对比.xlsx'));await fs.writeFile(path.join(root,'evaluation/v7/kb16_comparison_build.json'),JSON.stringify({authoring:'@oai/artifact-tool',overviewRows:overview.length-1,detailRows:rows.length,sourcePhases:['kb16_final_audit_v5','semantic_kb16_final','fresh_kb16_final_shared','kb16_v2_final_bundled_audit_v3','original_kb16_final','kb16_final_history']},null,2));console.log(JSON.stringify({overviewRows:overview.length-1,detailRows:rows.length}));
