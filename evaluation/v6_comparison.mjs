import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

// Presentation only: all rates originate in the independent audit JSON files.
const root = path.resolve(import.meta.dirname, '..');
const phases = process.argv.slice(2);
if (!phases.length) throw new Error('Pass audit phase names after all replays finish');
const historicalPhase = phases.find(p => p.startsWith('historical'));
if (!historicalPhase) throw new Error('A historical phase is required for fixed-denominator identity audit');
const wb = Workbook.create();
const overview = [['验证类型', '阶段', '模式', '全量事件', '自动完成', '自动正确', 'A 自动正确率%',
  'B 全量自动正确覆盖率%', 'C 业务处理正确率%', '漏输出', '额外输出', '候选完整性未核事件']];
const detail = [['阶段', 'seed', '模式', '场景', '公司', '事件或评测记录编号', '原文', '预期商品', '输出商品',
  '预期规格', '输出规格', '原始数量预期', '解析数量', '原始单位预期', '解析单位', '计价数量预期',
  '计价数量输出', '原价预期', '原价输出', '折扣', '配货单价', '销售金额', '状态', '自动完成',
  '自动正确', '人工正确', '控制正确', '问题代码', '全部输出候选', '评测差异', '候选完整性', '输出去向']];
const grouped = [['阶段', '模式', '分组类型', '分组', '全量事件', '自动完成', '自动正确', 'A%', 'B%', 'C%']];
const sources = [];
const safe = value => value === undefined || value === null ? null :
  typeof value === 'object' ? JSON.stringify(value) : typeof value === 'string' && value.startsWith('=') ? `'${value}` : value;
for (const phase of phases) {
  if (!/^[\w-]+$/.test(phase)) throw new Error('invalid phase');
  const p = path.join(root, 'evaluation/v6', phase, 'replay_audit.json');
  const audit = JSON.parse(await fs.readFile(p, 'utf8'));
  sources.push(p);
  const kind = phase.startsWith('historical') ? '历史变形回归' :
    phase.includes('holdout') ? '首次冻结合成保留' : '已见合成回归';
  for (const [mode, m] of Object.entries(audit.metrics)) {
    overview.push([kind, phase, mode, m.input_events, m.automatic, m.automatic_correct, m.A, m.B, m.C,
      m.missing_outputs ?? null, m.unexpected_outputs ?? null, m.candidate_completeness_unverified ?? null]);
    for (const [type, section] of [['公司', audit.by_company], ['场景', audit.by_scenario]]) {
      for (const [label, group] of Object.entries(section?.[mode] ?? {}))
        grouped.push([phase, mode, type, label, group.input_events, group.automatic,
          group.automatic_correct, group.A, group.B, group.C]);
    }
  }
  for (const [rowIndex, item] of audit.rows.entries()) {
    const e = item.expected ?? {};
    const event = item.actual ?? {};
    const a = event.row ?? {};
    const es = e.selected ?? {};
    const selected = a.selected ?? {};
    const candidates = (a.candidates ?? []).map(c =>
      `${c.code} | ${c.name} | ${c.spec} | ${c.price} | ${c.match_reason ?? ''}`).join('\n');
    detail.push([phase, item.seed, item.mode, item.scenario, item.company,
      e.event_id ?? item.source_event_id ?? item.canonical_event_id ?? item.expected_id ?? item.line_id ??
        `评测记录:${item.case_id ?? item.batch ?? 'unassigned'}/${rowIndex + 1}`,
      a.raw_text ?? e.raw_text ?? item.raw_text ??
        (e.source_line_text ? `[漏输出；以下为整来源行，非事件独立区间] ${e.source_line_text}` : null),
      es.name ?? e.name ?? e.requested_name ?? item.expected_name,
      selected.name ?? a.name, es.spec ?? e.spec, selected.spec,
      e.quantity ?? item.expected_quantity, a.quantity, e.raw_unit ?? e.unit ?? item.expected_unit, a.unit,
      e.quantity_500g ?? e.invoice_quantity, a.invoice_quantity, es.price ?? e.price, selected.price, a.discount,
      a.unit_price, a.amount, a.status, item.automatic, item.automatic_correct,
      item.manual_correct, item.control_correct ?? item.control_handled_correct,
      (a.issues ?? []).map(x => `${x.code}: ${x.message}`).join('\n'), candidates,
      (item.errors ?? []).join('\n'), item.candidate_completeness,
      event.manifest_path ?? item.manifest_path]);
  }
}
const notes = [
  ['说明', '内容'],
  ['目标状态', 'doing：全量A/B及所有正常公司场景未同时达到95%。'],
  ['固定历史分母', '159全部聊天事件；155与人工对齐；132当前目录可核人工SKU。三站53/42/64。'],
  ['样本边界', '同一事件的表达/组合变体不是新增真实业务；保留合成也不能证明未来订单准确率。'],
  ['保留集消耗', '7391首次暴露数字规格边界问题；修复后的7391为已见回归。首次成绩另保留。'],
  ['候选口径', '候选集合与台账/批注一致不代表所有合理候选语义穷尽；未核状态保留。'],
  ['评测记录编号', '无独立event_id的压力样本以case_id和审计行序号标识，不能冒充原始业务事件ID。漏输出的整来源行有显式标签，不伪装为已解析原文。'],
  ['事实缺口', '独立审计已证明26/159硬缺口，宽松上界133/159=83.6478%；不是可达率。'],
  ['范围', '本地确定性脚本和Excel导出验证；千问办公、WorkBuddy及真实新增订单未实测。'],
  ...sources.map(p => ['审计文件', p]),
];
function addSheet(name, rows) {
  const sheet = wb.worksheets.add(name);
  sheet.showGridLines = false;
  const cleaned = rows.map(row => row.map(safe));
  sheet.getRangeByIndexes(0, 0, cleaned.length, cleaned[0].length).values = cleaned;
  sheet.getRangeByIndexes(0, 0, 1, cleaned[0].length).format = {
    fill: '#284B63', font: { bold: true, color: '#FFFFFF' }, rowHeight: 32, wrapText: true,
  };
  sheet.getUsedRange().format.columnWidth = 19;
  sheet.getUsedRange().format.verticalAlignment = 'center';
  sheet.freezePanes.freezeRows(1);
  return sheet;
}
const summarySheet = addSheet('验收概览', overview);
summarySheet.getRange('A:A').format.columnWidth = 24;
summarySheet.getRange('B:B').format.columnWidth = 31;
summarySheet.getRange('C:C').format.columnWidth = 32;
summarySheet.getRange('G:I').format.columnWidth = 29;
const detailSheet = addSheet('逐项对比', detail);
detailSheet.getRange('G:K').format.columnWidth = 29;
detailSheet.getRange('AB:AF').format.columnWidth = 48;
addSheet('公司与场景', grouped);
const identity = JSON.parse(await fs.readFile(path.join(root,
  `evaluation/v6/${historicalPhase}/identity_audit.json`), 'utf8'));
const identityRows = [['seed/场景/模式', '原始事件', '公司', '原文', '人工文件', '人工行',
  '人工商品', '人工规格', '固定132内', '人工参照SKU', '输出SKU', '身份核验', '输出记录数', '输出状态']];
for (const [group, values] of Object.entries(identity.groups))
  for (const e of values.events)
    identityRows.push([group, e.event_id, e.station, e.source_raw_text, e.manual_file, e.manual_row,
      e.manual_name, e.manual_spec, e.sku_in_fixed_132, e.expected_code, e.actual_code,
      e.identity_outcome, e.output_count, e.actual_status]);
addSheet('历史人工SKU', identityRows);
const noteSheet = addSheet('口径与来源', notes);
noteSheet.getRange('B:B').format.columnWidth = 110;
noteSheet.getUsedRange().format.wrapText = true;
wb.recalculate();
const inspect = await wb.inspect({kind:'region', sheetId: '验收概览', range:'A1:L8', maxChars:3000});
await fs.writeFile(path.join(root, 'evaluation/v6/comparison_inspect.json'), JSON.stringify(inspect, null, 2));
const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(path.join(root, 'evaluation/v6逐项对比.xlsx'));
const preview = await wb.render({sheetName:'验收概览', autoCrop:'all', scale:1, format:'png'});
await fs.writeFile(path.join(root, 'evaluation/v6/comparison_preview.png'),
  new Uint8Array(await preview.arrayBuffer()));
await fs.writeFile(path.join(root, 'evaluation/v6/comparison_build.json'), JSON.stringify({
  phases, detailRows: detail.length - 1, overviewRows: overview.length - 1, groupedRows: grouped.length - 1,
  historicalIdentityRows: identityRows.length - 1,
  sources, authoring: '@oai/artifact-tool',
}, null, 2));
console.log(JSON.stringify({detailRows:detail.length-1, workbook:'evaluation/v6逐项对比.xlsx'}));
