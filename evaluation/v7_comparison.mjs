import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile, FileBlob } from '@oai/artifact-tool';
const root = path.resolve(import.meta.dirname, '..');
if (process.argv.includes('--preview-original')) {
  const before = await SpreadsheetFile.importXlsx(await FileBlob.load(path.join(root,'evaluation/v7逐项对比.xlsx')));
  const preview = await before.render({sheetName:'验收概览',range:'A1:H8',scale:1.5,format:'png'});
  await fs.writeFile(path.join(root,'evaluation/v7/comparison_before.png'),new Uint8Array(await preview.arrayBuffer()));
  process.exit(0);
}
const freshPath = path.join(root, 'evaluation/v7/fresh_kb15_shared');
const oldPath = path.join(root, 'evaluation/v7/kb15_rc1/replay_audit.json');
const fresh = JSON.parse(await fs.readFile(path.join(freshPath, 'report.json'), 'utf8'));
const ledgers = JSON.parse(await fs.readFile(path.join(freshPath, 'event_ledger.json'), 'utf8'));
const old = JSON.parse(await fs.readFile(oldPath, 'utf8'));
const semantic = JSON.parse(await fs.readFile(path.join(root,'evaluation/v7/semantic_kb15/report.json'),'utf8'));
const initial = JSON.parse(await fs.readFile(path.join(root,'evaluation/v7/semantic_audit_v1/recheck/report.json'),'utf8'));
const identity = JSON.parse(await fs.readFile(path.join(root,'evaluation/v7/fixed_identity_kb15.json'),'utf8'));
const wb = Workbook.create();
const overview = [['验证类型','阶段/模式','公司/场景','全量事件','自动完成','自动正确','A%','B%','C%','漏输出','额外输出','备注']];
const detail = [['验证类型','阶段/模式','seed','场景','公司','事件编号','原文','预期商品','输出商品','预期规格','输出规格','原始数量','解析数量','原始单位','解析单位','计价数量输出','原价输出','折扣','配货单价','销售金额','状态','自动完成','自动正确','人工检查通过','问题代码','候选完整性']];
const grouped = [['验证类型','阶段/模式','分组类型','分组','全量事件','自动完成','自动正确','A%','B%','C%']];
const safe = value => value === undefined || value === null ? null : typeof value === 'object' ? JSON.stringify(value) : typeof value === 'string' && value.startsWith('=') ? `'${value}` : value;
const addOverview = (kind, stage, label, m, note='') => overview.push([kind, stage, label, m.input_events ?? m.events, m.automatic, m.automatic_correct, m.A_percent ?? m.A, m.B_percent ?? m.B, m.C_percent ?? m.C, m.missing_outputs ?? 0, m.unexpected_outputs ?? 0, note]);
for (const mode of ['catalog','shared','contract']) {
  const r=JSON.parse(await fs.readFile(path.join(root,`evaluation/v7/fresh_kb15_${mode}/report.json`),'utf8'));
  addOverview('目录全名合成回归',`KB15 ${mode}`,'全量324',r.full_denominator_summary,'C未证；三模式均306正确，共享增益0');
  addOverview('目录全名合成回归',`KB15 ${mode}`,'正常306',r.normal_subset);
}
for (const [mode,m] of Object.entries(semantic.mode_summaries)) {
  addOverview('新表达已见回归',`KB15 ${mode}`,'全量84',m.summary,'8概念、三seed表达变体，不是新增真实业务');
  addOverview('新表达已见回归',`KB15 ${mode}`,'正常72',m.normal);
}
for (const run of initial.runs) addOverview('首次语义执行',`KB14 ${run.lane}`,String(run.seed),run.summary,run.lane==='agent'?'本批Agent语义决策辅助轨，不能当脚本性能':'冻结预期首次评分');
for (const [company,m] of Object.entries(fresh.by_company)) addOverview('目录全名合成回归','KB15 shared',`公司:${company}`,m,'公司聚合含人工待核事件');
for (const [scenario,m] of Object.entries(fresh.by_scenario)) addOverview('目录全名合成回归','KB15 shared',`场景:${scenario}`,m,'');
for (const [mode,m] of Object.entries(old.metrics)) addOverview('历史变形回归','kb15_rc1',mode,m,'历史oracle回放，非独立新订单准确率');
for (const [mode,groupsByType] of Object.entries(old.by_company)) for (const [label,m] of Object.entries(groupsByType)) grouped.push(['历史变形回归',mode,'公司',label,m.input_events,m.automatic,m.automatic_correct,m.A,m.B,m.C]);
for (const [mode,groupsByType] of Object.entries(old.by_scenario)) for (const [label,m] of Object.entries(groupsByType)) grouped.push(['历史变形回归',mode,'场景',label,m.input_events,m.automatic,m.automatic_correct,m.A,m.B,m.C]);
for (const [seed,ledger] of Object.entries(ledgers)) {
  for (const r of ledger.rows) {
    const e = r.expected || {}; const a = r.actual || {}; const ar = a.row || {}; const es = e.selected || {}; const sel = ar.selected || {};
    detail.push(['目录全名合成回归','KB15 shared',seed,e.scenario,e.company,e.event_id || '',ar.raw_text || e.source_line_text || '',es.name || e.requested_name || '',sel.name || ar.name || '',es.spec || '',sel.spec || '',e.raw_quantity || e.quantity || '',ar.quantity || '',e.raw_unit || '',ar.unit || '',ar.invoice_quantity || '',sel.price || '',ar.discount || '',ar.unit_price || '',ar.amount || '',ar.status || '',r.automatic,r.automatic_correct,r.manual_checks_passed,(r.errors || []).join('\n'),r.candidate_completeness || 'unknown']);
  }
}
for (const run of [...semantic.runs,...initial.runs.filter(r=>r.lane==='agent')]) {
  const stage=run.lane==='agent'?'KB14 Agent辅助':'KB15 '+run.lane;
  for (const r of run.events) {
    const e=r.expected||{}, ar=r.actual?.row||{}, sel=ar.selected||{}, es=e.selected||{};
    detail.push(['语义表达',stage,run.seed,e.outcome,r.company,r.event_id,r.source_text,es.name,sel.name||ar.name,es.spec,sel.spec,e.quantity,ar.quantity,e.unit,ar.unit,ar.invoice_quantity,sel.price,ar.discount,ar.unit_price,ar.amount,ar.status,r.automatic,r.automatic_correct,null,r.errors.join('\n'),'C未证']);
  }
  for(const type of ['by_company','by_scenario','by_concept']) for(const [label,m] of Object.entries(run[type])) grouped.push(['语义表达',`${stage}/${run.seed}`,type,label,m.events,m.automatic,m.automatic_correct,m.A,m.B,m.C]);
}
for (const r of old.rows) { const e=r.expected||{}; const a=r.actual||{}; const ar=a.row||{}; const es=e.selected||{}; const sel=ar.selected||{}; detail.push(['历史变形回归','kb15_rc1',r.seed,r.scenario,r.company,r.case_id||r.source_event_id||'',ar.raw_text||e.raw_text||'',es.name||e.name||e.requested_name||'',sel.name||ar.name||'',es.spec||e.spec||'',sel.spec||'',e.quantity||'',ar.quantity||'',e.raw_unit||e.unit||'',ar.unit||'',ar.invoice_quantity||'',sel.price||'',ar.discount||'',ar.unit_price||'',ar.amount||'',ar.status||'',r.automatic,r.automatic_correct,r.manual_correct,r.errors.join('\n'),r.candidate_completeness || 'historical_oracle']); }
const fixed=[['组','站点','历史SKU一致','SKU分母','一致率%','对齐分母','全量分母','未选','异历史SKU','undefined','未输出']];
for (const [key,g] of Object.entries(identity.groups)) for (const [station,m] of Object.entries({all:g.metrics,...g.per_station})) fixed.push([key,station,m.strict132.correct,m.strict132.denominator,m.strict132.rate_percent,m.coverage155.denominator,m.coverage159.denominator,m.fixed_sku_outcomes.unselected,m.fixed_sku_outcomes.different_sku,m.undefined_fixed_sku,m.output_missing_events]);
const notes = [['口径与来源','内容'],['当前状态','doing，整体95%未通过。KB15：85概念、2单位、1受限表达结构；188核心测试通过。'],['固定业务分母','原始聊天159；聊天与人工对齐155；当前目录可核人工SKU132；站点53/42/64。'],['目录全名合成','7601/7602/7691；三模式各324事件、306自动正确，A100/B94.4444，正常306/306；三模式共享增益0。'],['语义表达回归','8701/8702/8791；8概念×3表达＋4异常/seed。KB15共享51/84，正常51/72；首次保留成绩和KB14 Agent辅助轨分别保留。'],['异常口径','颜色/原生批注/空金额等指定机械检查有证据；候选全集未独立充分证明，C保持空。旧315/324审计结论已勘误，不作最终分数。'],['历史回放','kb15_rc1使用冻结历史oracle，历史A/B/C和SKU一致率不作为独立新订单准确率。'],['事实上界','17项源事实硬缺口、9项未决；历史全量自动覆盖宽松上界142/159=89.31%，不是已实现率。'],['宿主边界','KB14 8791已执行独立Agent语义辅助轨；千问办公、WorkBuddy及真实新增订单未实际测试，未真实导入或正式入账。'],['来源','evaluation/v7/fresh_kb15_*、semantic_kb15、semantic_audit_v1/recheck、kb15_rc1、fixed_identity_kb15、postfix_C_errata和source_fact_limits_review。']];
const priority={'新表达已见回归':0,'首次语义执行':1,'目录全名合成回归':2,'历史变形回归':3};
overview.splice(1,overview.length-1,...overview.slice(1).sort((a,b)=>(priority[a[0]]??9)-(priority[b[0]]??9)));
function addSheet(name, rows) { const s=wb.worksheets.add(name); s.showGridLines=false; const cleaned=rows.map(row=>row.map(safe)); s.getRangeByIndexes(0,0,cleaned.length,cleaned[0].length).values=cleaned; s.getRangeByIndexes(0,0,1,cleaned[0].length).format={fill:'#284B63',font:{bold:true,color:'#FFFFFF'},rowHeight:32,wrapText:true}; s.getUsedRange().format.columnWidth=19; s.getUsedRange().format.verticalAlignment='center'; s.getUsedRange().format.wrapText=true; s.freezePanes.freezeRows(1); return s; }
const o=addSheet('验收概览',overview); o.getRange('A:A').format.columnWidth=24; o.getRange('B:B').format.columnWidth=27; o.getRange('C:C').format.columnWidth=30; o.getRange('L:L').format.columnWidth=55;
const d=addSheet('逐项对比',detail); d.getRange('G:G').format.columnWidth=38; d.getRange('Y:Z').format.columnWidth=48;
addSheet('公司与场景',grouped); const f=addSheet('固定分母身份',fixed); f.getRange('A:A').format.columnWidth=65;
const n=addSheet('口径与来源',notes); n.getRange('B:B').format.columnWidth=110; n.getUsedRange().format.rowHeight=58;
o.getUsedRange().format.rowHeight=48; d.getUsedRange().format.rowHeight=58;
wb.recalculate();
const inspections=[];
for(const [sheet,range] of [['验收概览','A1:L8'],['逐项对比','A1:L6'],['公司与场景','A1:J6'],['固定分母身份','A1:K6'],['口径与来源','A1:B6']]) {
  inspections.push((await wb.inspect({kind:'table',range:`'${sheet}'!${range}`,include:'values,formulas',tableMaxRows:8,tableMaxCols:12})).ndjson);
  if(process.argv.includes('--overview-only') && sheet!=='验收概览') continue;
  const image=await wb.render({sheetName:sheet,range,scale:1.3,format:'png'});
  await fs.writeFile(path.join(root,`evaluation/v7/comparison_${sheet}.png`),new Uint8Array(await image.arrayBuffer()));
}
inspections.push((await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:100},summary:'formula error scan'})).ndjson);
await fs.writeFile(path.join(root,'evaluation/v7/comparison_inspect.ndjson'),inspections.join('\n'));
const output = await SpreadsheetFile.exportXlsx(wb); await output.save(path.join(root,'evaluation/v7逐项对比.xlsx'));
await fs.writeFile(path.join(root,'evaluation/v7/comparison_build.json'), JSON.stringify({detailRows:detail.length-1,overviewRows:overview.length-1,groupedRows:grouped.length-1,fixedRows:fixed.length-1,sources:[freshPath,oldPath,'semantic_kb15','semantic_audit_v1/recheck','fixed_identity_kb15'],authoring:'@oai/artifact-tool'},null,2));
console.log(JSON.stringify({detailRows:detail.length-1, workbook:'evaluation/v7逐项对比.xlsx'}));
