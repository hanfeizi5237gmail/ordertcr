import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { Workbook, SpreadsheetFile, FileBlob } from '@oai/artifact-tool';

const root=path.resolve(import.meta.dirname,'..');
const sourceHashes=new Map();
const hash=b=>createHash('sha256').update(b).digest('hex');
const read=async p=>{const bytes=await fs.readFile(path.join(root,p));const digest=hash(bytes);if(sourceHashes.has(p)&&sourceHashes.get(p)!==digest)throw Error(`source changed while reading: ${p}`);sourceHashes.set(p,digest);return JSON.parse(bytes.toString('utf8'));};
const outdir=path.join(root,'evaluation/v7');
const source=[];
const load=async p=>{const d=await read(p);source.push([p,'本地 JSON','']);return d;};
const overview=[['验证集','模式','范围','事件','自动','自动正确','A','B','C','业务处理正确率','来源']];
const groups=[['验证集','模式','维度','分组','事件','自动','自动正确','A','B','C','来源']];
const detail=[['验证集','模式','seed','事件ID','类型','公司','日期','场景','源行','原文','预期SKU','预期品名','预期规格','输出SKU','输出品名','输出规格','预期数量','输出数量','预期单位','输出单位','计价量','挂牌价','折扣','单价','金额','状态','自动','自动正确','业务处理正确','问题 errors','候选完整性','候选见证','来源']];
const extras=[detail[0].slice()];
const packageReview=[['seed','模式','事件ID','原文','旧预期必出见证','旧oracle错误','实际SKU','实际计价量','实际金额','实际财务金额','实际候选','修订参考合同','候选全集','修订重评分']];
const regression=[['旧验证集','模式','事件数','旧自动','新自动','旧自动正确','新自动正确','旧A','新A','旧B','新B','变化事件数','范围说明']];
const regressionChanges=[['旧验证集','模式','seed','事件ID','原文','新状态','逐字段变化']];
const fixed=[['组（seed/场景/模式）','站点','身份一致','SKU分母','一致率','对齐分母','事件分母','未选','不同SKU','未定义','缺失','多输出','错公司']];
const original=[['站点','源行','原文','状态','输出SKU','输出品名','规格','数量','单位','计价量','金额','问题','候选见证','结果文件']];
const nz=v=>v===undefined||v===null?null:v;
const num=v=>v==null||v===''?null:Number.isFinite(Number(v))?Number(v):null;
const rate=v=>v==null?null:Number(v)/100;
const cell=v=>v==null?null:typeof v==='object'?JSON.stringify(v):typeof v==='string'&&v.startsWith('=')?`'${v}`:v;
const fmt=x=>(x||[]).map(a=>`${a.code??a.sku??''}:${a.name??''} ${a.spec??''} ${a.price??''}`).join('；');
function metrics(m){return [m.events??m.input_events??null,m.automatic??null,m.automatic_correct??null,rate(m.A??m.A_percent),rate(m.B??m.B_percent),rate(m.C??m.C_percent)];}
function addSummary(label,mode,scope,m,p){overview.push([label,mode,scope,...metrics(m),rate(m.business_processing_rate),p]);}
function addGroup(label,mode,kind,name,m,p){groups.push([label,mode,kind,name,...metrics(m),p]);}
function addEvent(label,mode,seed,e,p){
 const ex=e.expected||{}, a=e.actual||{}, row=a.row||{}, sel=row.selected||{}, want=ex.expected_catalog_item||ex.selected||ex.known_identity_record||{};
 const errors=Array.isArray(e.errors)?e.errors.join('；'):JSON.stringify(e.errors??'');
 const completeness=e.candidate_completeness??ex.candidate_completeness??ex.candidate_set_status??ex.candidate_scope?.semantic_completeness??'unknown';
 const target=e.expected==null&&e.event_id==null?extras:detail;
 target.push([label,mode,seed,e.event_id??ex.event_id??'',e.kind??ex.kind??ex.outcome??'',e.company??ex.company??a.company_id??'',e.date??ex.date??'',e.scenario??ex.scenario??'',e.source_line??ex.source_line??row.source_line??'',e.source_text??ex.source_line_text??row.raw_text??'',want.sku??want.code??'',want.name??'',want.spec??'',sel.code??'',sel.name??'',sel.spec??'',num(ex.quantity),num(row.quantity),ex.unit??ex.raw_unit??'',row.unit??'',num(row.invoice_quantity),num(sel.price),num(row.discount),num(row.unit_price),num(row.amount),row.status??'未对齐',nz(e.automatic),nz(e.automatic_correct),nz(e.correct??e.review_correct??e.manual_checks_passed),errors,completeness,fmt(row.candidates),p]);
}
function groupEvents(label,mode,events,p){
 for(const [dim,key] of [['公司','company'],['场景','scenario']]){
  const buckets=new Map();for(const e of events){const val=e[key]??e.expected?.[key];if(!val)continue;const x=buckets.get(val)||[];x.push(e);buckets.set(val,x)}
  for(const [name,arr] of buckets){const inputs=arr.filter(e=>e.expected!=null||e.event_id!=null).length,auto=arr.filter(e=>e.automatic===true).length,good=arr.filter(e=>e.automatic_correct===true).length;addGroup(label,mode,dim,name,{events:inputs,automatic:auto,automatic_correct:good,A:auto?100*good/auto:null,B:inputs?100*good/inputs:null,C:null},p);}
 }
}
const lanes=[
 ['KB18迁移开发64','evaluation/v7/kb18_transfer_final_dev/audit/report.json'],
 ['KB18迁移首次保留32','evaluation/v7/kb18_transfer_final_holdout/audit/report.json'],
 ['KB18旧V3预期96','evaluation/v7/kb18_v3_final/report.json'],
 ['KB18 v1最终54','evaluation/v7/kb18_v1_final/report.json'],
 ['KB18历史语义84','evaluation/v7/semantic_kb18_final/report.json'],
];
for(const [label,p] of lanes){const d=await load(p);
 for(const [mode,summary] of Object.entries(d.mode_summaries??d.mode_aggregates)){addSummary(label,mode,'全量',summary.summary??summary.all??summary,p); const events=[];
  for(const run of d.runs.filter(r=>(r.mode??r.lane)===mode)){
   let originalExpected=null;if(label.includes('迁移')){const ref=await read(`evaluation/v7/kb18_transfer_challenge/${run.seed}/expected.json`);originalExpected=new Map(ref.events.map(e=>[e.event_id,e]));}
   const declaredExtras=Number(run.unexpected_outputs??0);const representedExtras=run.events.filter(e=>e.expected==null&&e.event_id==null).length;
   if(declaredExtras>representedExtras)throw Error(`${label} ${mode} seed ${run.seed}: extras not included in event records`);
   for(const e of run.events){const enriched=!e.expected&&e.event_id&&originalExpected?{...e,expected:originalExpected.get(e.event_id)}:e;events.push(enriched);addEvent(label,mode,run.seed,enriched,p)}
  }
  const expected=(summary.summary??summary.all??summary).events;const inputs=events.filter(e=>e.expected!=null||e.event_id!=null).length;if(inputs!==expected)throw Error(`${label} ${mode}: inputs ${inputs} != ${expected}`);
  groupEvents(label,mode,events,p);
 }
}
for(const mode of ['catalog_only','shared','shared_with_company_contract']){
 const prefix=`evaluation/v7/fresh_kb18_final_${mode}`,p=`${prefix}/report.json`,d=await load(p), l=await load(`${prefix}/event_ledger.json`);
 addSummary('目录迁移324',mode,'全量',d.full_denominator_summary,p);
 for(const [k,m] of Object.entries(d.by_company))addGroup('目录迁移324',mode,'公司',k,m,p);
 for(const [k,m] of Object.entries(d.by_scenario))addGroup('目录迁移324',mode,'场景',k,m,p);
 let n=0;for(const [seed,v] of Object.entries(l))for(const e of v.rows){
  addEvent('目录迁移324',mode,seed,e,`${prefix}/event_ledger.json`);if(e.expected)n++;
  if(['7601-0041','7602-0014','7691-0062'].includes(e.expected?.event_id)){
   const row=e.actual?.row??{}, sel=row.selected??{};
   packageReview.push([seed,mode,e.expected.event_id,e.expected.source_line_text,'80002418：250克/袋',
    (e.errors??[]).join('；'),sel.code??'',num(row.invoice_quantity),num(row.amount),num(row.finance_amount),
    fmt(row.candidates),'250g/袋不符，不属合理适配且不强制展示；1kg为包装未证参考，不强制展示；均不可自动替代','unknown','未执行']);
  }
 }if(n!==324)throw Error(`fresh ${mode}: ${n}`);
}
if(packageReview.length!==10)throw Error(`package observations ${packageReview.length-1} != 9`);
const origpath='evaluation/v7/original_kb18_final/report.json',orig=await load(origpath);
for(const st of Object.values(orig.stations)){const data=await load(st.result);for(const e of data.events){const r=e.row??{},s=r.selected??{};original.push([st.station,r.source_line,r.raw_text,r.status,s.code??'',s.name??'',s.spec??'',num(r.quantity),r.unit??'',num(r.invoice_quantity),num(r.amount),(r.issues||[]).map(x=>x.code).join('；'),fmt(r.candidates),st.result])}}
if(original.length-1!==159)throw Error('original denominator mismatch');
const idpath='evaluation/v7/kb18_final_history_identity.json',id=await load(idpath);
for(const [g,v] of Object.entries(id.groups))for(const [st,m] of Object.entries({全部:v.metrics,...v.per_station}))fixed.push([g,st,m.strict132.correct,m.strict132.denominator,rate(m.strict132.rate_percent),m.coverage155.denominator,m.coverage159.denominator,m.fixed_sku_outcomes?.unselected??0,m.fixed_sku_outcomes?.different_sku??0,m.undefined_fixed_sku??0,m.output_missing_events??0,m.multiple_output_events??0,m.routing_company_mismatches??0]);
if(Object.keys(id.groups).length!==27)throw Error('history group mismatch');
const histpath='evaluation/v7/kb18_final_history/replay_audit.json',hist=await load(histpath);
for(const [mode,m] of Object.entries(hist.metrics))addSummary('历史变形1551',mode,'旧oracle参考',{...m,C:null,C_percent:null},histpath);
const regressionPath='evaluation/v7/kb18_oldlane_regression.json';
const regressionReport=await load(regressionPath);
for(const [lane,data] of Object.entries(regressionReport.lanes)){
 const modes=data.modes??data;
 for(const [mode,v] of Object.entries(modes)){
  if(!v?.old_summary||!v?.new_summary)continue;
  const old=v.old_summary.summary??v.old_summary, current=v.new_summary.summary??v.new_summary;
  const changes=v.changed_events??v.event_changes??[];
  regression.push([lane,mode,old.events??old.input_events??null,old.automatic??null,current.automatic??null,
   old.automatic_correct??null,current.automatic_correct??null,rate(old.A??old.A_percent),rate(current.A??current.A_percent),
   rate(old.B??old.B_percent),rate(current.B??current.B_percent),changes.length,
   lane==='9091_seen'?'已消费，非独立保留': '旧结果回归，非独立保留']);
  for(const c of changes)regressionChanges.push([lane,mode,c.seed??'',c.event_id??'',c.source_text??'',c.new_event_state??'',JSON.stringify(c.changes??c)]);
 }
}
overview.push(['原始三站159','shared','需求草稿',orig.total_events,null,null,null,null,null,null,origpath]);
overview.push(['历史固定身份27组','三seed×三场景×三模式','固定159/155/132',id.fixed_denominators.events,null,null,null,null,null,null,idpath]);
const extra=process.argv[2];if(extra){const p=path.isAbsolute(extra)?path.relative(root,extra):extra;const d=await load(p);if(d.summary&&d.events){
 if(p!=='evaluation/v7/kb18_agent_semantic_audit/report.json'||!d.scope?.includes('revision_1')||d.events.length!==32||d.extra_events!==0)throw Error('unexpected Agent report identity or unrepresented extras');
 addSummary('Agent语义前向9191','single_agent_revision1','已知CLI、非盲',d.summary,p);
 for(const e of d.events)addEvent('Agent语义前向9191','single_agent_revision1',9191,e,p);
 groupEvents('Agent语义前向9191','single_agent_revision1',d.events,p);
 }else throw Error('unsupported optional Agent report schema')}
const notes=[['项目','说明'],['范围','KB18 本地验证；A/B 目标与候选全集仍未整体通过。'],['独立商品','迁移开发与首次保留使用不同商品；重复表达、seed 和公司不是独立真实样本。'],['首次保留','9191 首次保留已消费；开发表现不可覆盖保留结果。'],['Agent前向','9191 单Agent已知CLI结果，revision1经修订，非盲；单独列示，不计入CLI得分。CLI首次保留正常样本为0/24。'],['v3 oracle','观察历史结果后只修订三条包装参考合同；未按V3重评分，旧oracle计分保留。'],['A','自动正确 / 自动输出；额外输出如为自动，计入A分母；逐项另列额外输出。'],['B','自动正确 / 全部输入事件；业务处理正确率单独列示。'],['C','候选全集及异常语义未独立充分证明；C保留空白/unknown。'],['原文与历史','原文159条为需求草稿，历史固定159/155/132身份为人工参照，不是实时新订单准确率。'],['候选与错误','逐项问题来自原评测；候选见证不是全集，包装专项展示旧评分及后置修订。'],['历史明细','540次 CLI 不在本簿重复展开；完整审计见 evaluation/v7/kb18_final_history/replay_audit.json。'],['宿主与真实订单','其他宿主、真实新增客户订单、系统导入及正式入账未验证。']];
source.push([histpath,'历史540次审计','不重复嵌入全部运行行']);
for(const p of ['evaluation/v7/fresh_challenge_v2_kb18_oracle_v3/FREEZE.json',
                'evaluation/v7/fresh_challenge_v2_kb18_oracle_v3/changes.json',
                'evaluation/v7/kb18_original_exception_probe.json'])await load(p);
const wb=Workbook.create();
function sheet(name,data,widths){const s=wb.worksheets.add(name);s.showGridLines=false;s.getRangeByIndexes(0,0,data.length,data[0].length).values=data.map(r=>r.map(cell));const header=s.getRangeByIndexes(0,0,1,data[0].length);header.format={fill:'#26445B',font:{name:'Arial',size:10,bold:true,color:'#FFFFFF'},wrapText:true,rowHeight:30};s.getUsedRange().format.verticalAlignment='center';s.freezePanes.freezeRows(1);for(const [col,w] of Object.entries(widths))s.getRange(`${col}:${col}`).format.columnWidth=w;return s;}
const a=sheet('验收概览',overview,{A:20,B:29,C:18,D:11,E:11,F:12,G:12,H:12,I:12,J:16,K:65});a.getRange(`G2:J${overview.length}`).setNumberFormat('0.00%');
const b=sheet('公司与场景',groups,{A:20,B:29,C:12,D:26,E:11,F:11,G:12,H:12,I:12,J:12,K:60});b.getRange(`H2:J${groups.length}`).setNumberFormat('0.00%');
const det=sheet('逐项对比',detail,{A:19,B:28,C:10,D:16,E:18,F:16,G:16,H:22,I:9,J:42,K:15,L:24,M:12,N:15,O:24,P:12,Q:12,R:12,S:12,T:12,U:12,V:12,W:10,X:12,Y:14,Z:15,AA:10,AB:12,AC:15,AD:48,AE:20,AF:55,AG:55});
det.getRange(`AD2:AF${detail.length}`).format.wrapText=true;
det.getRange(`AD2:AF${detail.length}`).format.autofitRows();
sheet('额外输出',extras,{A:19,B:28,C:10,D:16,E:18,F:16,G:16,H:22,I:9,J:42,K:15,L:24,M:12,N:15,O:24,P:12,Q:12,R:12,S:12,T:12,U:12,V:12,W:10,X:12,Y:14,Z:15,AA:10,AB:12,AC:15,AD:48,AE:20,AF:55,AG:55});
const reg=sheet('旧结果回归',regression,{A:20,B:28,C:12,D:12,E:12,F:14,G:14,H:12,I:12,J:12,K:12,L:14,M:32});reg.getRange(`H2:K${regression.length}`).setNumberFormat('0.00%');
sheet('回归变化',regressionChanges,{A:20,B:28,C:12,D:16,E:44,F:18,G:90});
const pk=sheet('包装参考裁定',packageReview,{A:12,B:28,C:18,D:42,E:28,F:30,G:16,H:14,I:14,J:15,K:55,L:80,M:14,N:16});
pk.getRange(`K2:L${packageReview.length}`).format.wrapText=true;pk.getRange(`K2:L${packageReview.length}`).format.autofitRows();
sheet('原文159',original,{A:16,B:10,C:42,D:16,E:16,F:23,G:14,H:12,I:12,J:12,K:14,L:35,M:52,N:62});
const f=sheet('固定分母身份',fixed,{A:55,B:16,C:13,D:13,E:13,F:13,G:13,H:12,I:12,J:12,K:12,L:12,M:12});f.getRange(`E2:E${fixed.length}`).setNumberFormat('0.00%');
sheet('口径与来源',notes,{A:20,B:115});sheet('数据索引',[['路径','类型','说明'],...source],{A:80,B:20,C:52});
wb.recalculate();
const checks=[];for(const [name,range] of [['验收概览','A1:J12'],['公司与场景','A1:J10'],['逐项对比','A1:J10'],['逐项对比','X1:AG8'],['额外输出','A1:J4'],['旧结果回归','A1:M12'],['回归变化','A1:G8'],['包装参考裁定','A1:G10'],['包装参考裁定','H1:N10'],['原文159','A1:J10'],['固定分母身份','A1:J10'],['口径与来源','A1:B12'],['数据索引','A1:C12']]){checks.push((await wb.inspect({kind:'table',range:`'${name}'!${range}`,include:'values,formulas',tableMaxRows:12,tableMaxCols:12,maxChars:7000})).ndjson);const img=await wb.render({sheetName:name,range,scale:1,format:'png'});await fs.writeFile(path.join(outdir,`kb18_preview_${name}_${range.replace(':','_')}.png`),new Uint8Array(await img.arrayBuffer()))}
const scan=await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',options:{useRegex:true,maxResults:100},summary:'formula error scan'});checks.push(scan.ndjson);
await fs.writeFile(path.join(outdir,'kb18_comparison_inspect.ndjson'),checks.join('\n'));
const target=path.join(outdir,'kb18逐项对比.xlsx');await (await SpreadsheetFile.exportXlsx(wb)).save(target);
const reread=await SpreadsheetFile.importXlsx(await FileBlob.load(target));
for(const [name,expected] of [['验收概览',overview.length],['公司与场景',groups.length],['逐项对比',detail.length],['额外输出',extras.length],['旧结果回归',regression.length],['回归变化',regressionChanges.length],['包装参考裁定',packageReview.length],['原文159',original.length],['固定分母身份',fixed.length]]){const ws=reread.worksheets.getItem(name);if(!ws.getRange(`A${expected}`).values[0][0])throw Error(`export readback ${name} last row`)}
for(const [p,digest] of sourceHashes)if(hash(await fs.readFile(path.join(root,p)))!==digest)throw Error(`source changed during workbook build: ${p}`);
const meta={authoring:'@oai/artifact-tool',overview:overview.length-1,groups:groups.length-1,detail:detail.length-1,extras:extras.length-1,regression:regression.length-1,regression_changes:regressionChanges.length-1,package_review:packageReview.length-1,original:original.length-1,fixed_rows:fixed.length-1,history_groups:27,source_count:source.length,source_hashes:Object.fromEntries(sourceHashes),optional_agent_report:extra??null,formula_scan:scan.ndjson,readback:'passed'};
await fs.writeFile(path.join(outdir,'kb18_comparison_build.json'),JSON.stringify(meta,null,2)+'\n');console.log(JSON.stringify(meta));
