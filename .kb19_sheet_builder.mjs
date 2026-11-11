import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "/Users/hanfei/Documents/workspace/smartcode/ordertcr";
const auditPath = `${root}/evaluation/v7/kb19_current_r4/audit/report.json`;
const outPath = `${root}/evaluation/v7/kb19逐项对比.xlsx`;
const report = JSON.parse(await fs.readFile(auditPath, "utf8"));
const modes = ["catalog_only", "shared", "shared_with_company_contract"];
const splitOf = (seed) => seed === 9291 ? "first_holdout" : "development";
const cStatus = "unknown: complete exception contract/semantic candidate set not verified";
const metricRow = (mode, a) => [mode, a.all.events, a.normal.events, a.all.automatic, a.all.automatic_correct, a.all.A, a.all.B, a.normal.automatic_correct, a.all.review_correct, a.all.C, cStatus];

const wb = Workbook.create();
const summary = wb.worksheets.add("汇总");
const runs = wb.worksheets.add("Seed_模式");
const stations = wb.worksheets.add("分站");
const scenarios = wb.worksheets.add("场景");
const items = wb.worksheets.add("逐项对比");
const lateSheet = wb.worksheets.add("late9391");
const lateReport = JSON.parse(await fs.readFile(`${root}/evaluation/v7/kb19_late_final_r2/audit/report.json`, "utf8"));
const titleStyle = { font: { name: "Arial", size: 14, bold: true, color: "#17365D" } };
const headerStyle = { fill: "#1F4E78", font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true };
const bodyStyle = { font: { name: "Arial", size: 10, color: "#1F1F1F" }, verticalAlignment: "center", wrapText: true };
const noteStyle = { font: { name: "Arial", size: 10, italic: true, color: "#666666" }, wrapText: true };
const thinBorders = { preset: "all", style: "thin", color: "#D9E2F3" };
for (const sheet of [summary, runs, stations, scenarios, items, lateSheet]) sheet.showGridLines = false;

summary.getRange("A1:K1").merge();
summary.getRange("A1").values = [["KB19 current_r4 逐项对比与三模式审计"]];
summary.getRange("A1:K1").format = titleStyle;
summary.getRange("A2:K4").merge(true);
summary.getRange("A2").values = [["阶段：KB19 current_r4。固定挑战为 3 个 seed × 3 个模式，每个 seed 32 条（24 条条件性正常、8 条异常/待核）。A 只按实际自动输出计算，B 按全部事件计算；C 按全量业务处理正确率定义，因异常完整合同与候选语义全集未证保持未知。条件性候选不足不能当作完整自动成功，异常安全转人工单列。r2 仅作前后对比，后续阶段仍不得将正常子集替代全量。"]];
summary.getRange("A2:K4").format = noteStyle;
summary.getRange("A6:K6").values = [["模式", "全量事件", "正常事件", "自动输出", "自动且正确", "A (%)", "B (%)", "正常自动正确", "异常/待核安全", "C", "C 状态"]];
summary.getRange("A6:K6").format = headerStyle;
summary.getRange("A7:K9").values = modes.map((m) => metricRow(m, report.mode_aggregates[m]));
summary.getRange("A7:K9").format = bodyStyle;
summary.getRange("A6:K9").format.borders = thinBorders;
summary.getRange("A11:D15").values = [
  ["验收口径", "定义", "r4 结论", "证据"],
  ["A", "自动输出中逐项字段全正确的比例；没有自动输出时无分母", "shared 与 shared_with_company_contract = 100%；catalog_only = 未定义", "kb19_current_r4/audit/report.json"],
  ["B", "自动且正确 / 全部冻结事件；异常和待核仍进入分母", "shared = 62.5000%；shared_with_company_contract = 62.5000%；catalog_only = 0%", "kb19_current_r4/audit/report.json"],
  ["C", "全量业务处理正确率 = 自动正确 + 确应人工且正确保留/标色/批注，再除以全量输入", "未知；异常完整合同与候选语义全集尚未证实", "C_status = unknown: complete exception contract/semantic candidate set not verified"],
  ["95%", "整体自动正确目标，需在固定分母和主要分站/场景上同时满足", "未达；本工作簿主表记录 r4，不能宣称完成", "报告与 validation_result"],
];
summary.getRange("A11:D15").format = bodyStyle;
summary.getRange("A11:D15").format.borders = thinBorders;
summary.getRange("A11:D11").format = headerStyle;

const runHeaders = ["seed", "阶段", "模式", "事件", "正常", "异常/待核", "自动", "自动且正确", "A (%)", "B (%)", "正常自动且正确", "正常分母", "review_correct", "C", "C 状态", "finance_errors"];
runs.getRange("A1:P1").values = [runHeaders];
runs.getRange("A1:P1").format = headerStyle;
const runRows = [];
for (const r of report.runs) {
  const a = r.aggregate;
  runRows.push([r.seed, splitOf(r.seed), r.mode, a.all.events, a.normal.events, a.all.events - a.normal.events, a.all.automatic, a.all.automatic_correct, a.all.A, a.all.B, a.normal.automatic_correct, a.normal.events, a.all.review_correct, a.all.C, cStatus, r.finance_audit.errors.length]);
}
runs.getRange(`A2:P${runRows.length + 1}`).values = runRows;
runs.getRange(`A2:P${runRows.length + 1}`).format = bodyStyle;
runs.getRange(`A1:P${runRows.length + 1}`).format.borders = thinBorders;

const stationHeaders = ["模式", "分站", "事件", "自动", "自动且正确", "A (%)", "B (%)", "正确处理", "review_correct", "C", "C 状态"];
stations.getRange("A1:K1").values = [stationHeaders];
stations.getRange("A1:K1").format = headerStyle;
const stationRows = [];
for (const mode of modes) for (const [company, a] of Object.entries(report.mode_aggregates[mode].by_company)) stationRows.push([mode, company, a.events, a.automatic, a.automatic_correct, a.A, a.B, a.correct, a.review_correct, a.C, cStatus]);
stations.getRange(`A2:K${stationRows.length + 1}`).values = stationRows;
stations.getRange(`A2:K${stationRows.length + 1}`).format = bodyStyle;
stations.getRange(`A1:K${stationRows.length + 1}`).format.borders = thinBorders;

const scenarioHeaders = ["模式", "场景", "事件", "自动", "自动且正确", "A (%)", "B (%)", "正确处理", "review_correct", "C", "C 状态"];
scenarios.getRange("A1:K1").values = [scenarioHeaders];
scenarios.getRange("A1:K1").format = headerStyle;
const scenarioRows = [];
for (const mode of modes) for (const [scenario, a] of Object.entries(report.mode_aggregates[mode].by_scenario)) scenarioRows.push([mode, scenario, a.events, a.automatic, a.automatic_correct, a.A, a.B, a.correct, a.review_correct, a.C, cStatus]);
scenarios.getRange(`A2:K${scenarioRows.length + 1}`).values = scenarioRows;
scenarios.getRange(`A2:K${scenarioRows.length + 1}`).format = bodyStyle;
scenarios.getRange(`A1:K${scenarioRows.length + 1}`).format.borders = thinBorders;

const itemHeaders = ["seed", "阶段", "模式", "event_id", "分站", "日期", "场景", "类型", "原文", "预期 SKU", "预期名称", "预期规格", "预期量", "预期单位", "实际状态", "实际 SKU", "实际名称", "实际规格", "实际量", "实际单位", "实际金额", "自动", "自动且正确", "待核安全", "候选完整性", "错误/边界", "说明"];
items.getRange("A1:AA1").values = [itemHeaders];
items.getRange("A1:AA1").format = headerStyle;
const itemRows = [];
for (const r of report.runs) for (const e of r.events) {
  const expected = e.expected || {};
  const es = expected.selected || expected.expected_catalog_item || {};
  const actual = (e.actual && e.actual.row) || {};
  const selected = actual.selected || {};
  const conditional = e.conditional_undefined === true;
  const reviewSafe = e.review_correct === true;
  const errors = (e.errors || []).join(", ");
  const note = conditional ? "基础名+500g 缺少鲜冻/去皮等区分属性；完整异常合同/语义候选未证，不能计入C或95%成功" : (reviewSafe ? "异常保留待核，数量/金额保持空白" : (errors || ""));
  itemRows.push([r.seed, splitOf(r.seed), r.mode, e.event_id || "", e.company || "", e.date || "", e.scenario || "", e.kind || "", e.source_text || expected.source_line_text || "", es.sku || es.code || "", es.name || "", es.spec || "", expected.quantity || "", expected.unit || "", actual.status || actual.product_status || "", selected.sku || selected.code || "", selected.name || "", selected.spec || "", actual.quantity || "", actual.unit || "", actual.amount || actual.finance_amount || "", e.automatic === true ? "是" : "否", e.automatic_correct === true ? "是" : "否", reviewSafe ? "是" : "否", expected.candidate_completeness || (conditional ? "unknown" : ""), errors, note]);
}
items.getRange(`A2:AA${itemRows.length + 1}`).values = itemRows;
items.getRange(`A2:AA${itemRows.length + 1}`).format = bodyStyle;
items.getRange(`A1:AA${itemRows.length + 1}`).format.borders = thinBorders;

lateSheet.getRange("A1:K1").merge();
lateSheet.getRange("A1").values = [["9391 late holdout（单独轨道，不并入 current_r4 固定96）"]];
lateSheet.getRange("A1:K1").format = titleStyle;
lateSheet.getRange("A2:K3").merge(true);
lateSheet.getRange("A2").values = [["稳定 late_final_r2：1 个 seed（9391）、32 个业务订单组/32 条事件、6 个独立目录概念、四公司 24 条正常 + 8 条异常。r1 因冻结后补 contract_version 触发 source_drift_after_execution，拒绝评分；r2 结果单独列示。9201/9202 复用概念，不把事件变体当新增业务样本。C 按全量业务处理正确率定义，当前仍未知。"]];
lateSheet.getRange("A2:K3").format = noteStyle;
lateSheet.getRange("A5:K5").values = [["模式", "全量事件", "正常事件", "自动", "自动且正确", "A (%)", "B (%)", "正常自动且正确", "异常/待核安全", "C", "C 状态"]];
lateSheet.getRange("A5:K5").format = headerStyle;
lateSheet.getRange("A6:K8").values = modes.map((m) => { const a = lateReport.mode_aggregates[m]; return [m, a.all.events, a.normal.events, a.all.automatic, a.all.automatic_correct, a.all.A, a.all.B, a.normal.automatic_correct, a.all.review_correct, a.all.C, "unknown: complete exception contract/semantic candidate set not verified"]; });
lateSheet.getRange("A6:K8").format = bodyStyle;
lateSheet.getRange("A5:K8").format.borders = thinBorders;
lateSheet.getRange("A10:D14").values = [["分站", "全量事件", "自动且正确", "B (%)"], ...Object.entries(lateReport.mode_aggregates.shared.by_company).map(([name, a]) => [name, a.events, a.automatic_correct, a.B])];
lateSheet.getRange("A10:D10").format = headerStyle;
lateSheet.getRange("A11:D14").format = bodyStyle;
lateSheet.getRange("A10:D14").format.borders = thinBorders;
lateSheet.getRange("F10:I12").values = [["场景", "事件", "自动且正确", "B (%)"], ...Object.entries(lateReport.mode_aggregates.shared.by_scenario).map(([name, a]) => [name, a.events, a.automatic_correct, a.B])];
lateSheet.getRange("F10:I10").format = headerStyle;
lateSheet.getRange("F11:I12").format = bodyStyle;
lateSheet.getRange("F10:I12").format.borders = thinBorders;
lateSheet.getRange("A16:K16").values = [["r1 状态", "rejected", "source_drift_after_execution（冻结后补 contract_version）", "原始消息未变化；失败证据保留；不能称无中断首次盲评", null, null, null, null, null, null, null]];
lateSheet.getRange("A16:K16").format = noteStyle;

for (const sheet of [summary, runs, stations, scenarios, items, lateSheet]) {
  const used = sheet.getUsedRange();
  if (used) { used.format.verticalAlignment = "center"; used.format.autofitColumns(); used.format.autofitRows(); }
}
summary.getRange("A:A").format.columnWidth = 24;
summary.getRange("B:B").format.columnWidth = 26;
summary.getRange("C:D").format.columnWidth = 28;
summary.getRange("E:E").format.columnWidth = 18;
summary.getRange("F:G").format.columnWidth = 15;
summary.getRange("H:I").format.columnWidth = 20;
summary.getRange("J:J").format.columnWidth = 14;
summary.getRange("K:K").format.columnWidth = 48;
summary.getRange("A2:K4").format.rowHeight = 28;
summary.getRange("A11:D11").format.rowHeight = 30;
summary.getRange("A12:D12").format.rowHeight = 48;
summary.getRange("A13:D13").format.rowHeight = 56;
summary.getRange("A14:D14").format.rowHeight = 64;
summary.getRange("A15:D15").format.rowHeight = 48;
runs.freezePanes.freezeRows(1);
stations.freezePanes.freezeRows(1);
scenarios.freezePanes.freezeRows(1);
items.freezePanes.freezeRows(1);
items.freezePanes.freezeColumns(4);
items.getRange("I:I").format.columnWidth = 28;
items.getRange("Z:AA").format.columnWidth = 30;

wb.recalculate();
const check = await wb.inspect({ kind: "table", sheetId: "汇总", range: "A1:K15", include: "values,formulas", tableMaxRows: 20, tableMaxCols: 12, maxChars: 10000 });
console.log(check.ndjson);
const preview = await wb.render({ sheetName: "汇总", range: "A1:K15", scale: 1, format: "png" });
await fs.writeFile(`${root}/evaluation/v7/kb19逐项对比_preview.png`, new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(outPath);
console.log(JSON.stringify({ outPath, itemRows: itemRows.length, runRows: runRows.length, stationRows: stationRows.length, scenarioRows: scenarioRows.length }));
