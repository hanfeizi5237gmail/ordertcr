"""Publish the current v4 replay evidence without changing source or replay outputs."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V4 = ROOT / "evaluation" / "v4"
REPORT_PATH = V4 / "replay_audit.json"
LABELS = {"songbei": "松北大道站", "xingyue": "星月站", "xueyuan": "学院路站"}


def rel(path: str | Path, folder: Path) -> str:
    return Path(os.path.relpath(Path(path), folder)).as_posix()


def per_site_identity(items: list[dict], metrics: dict) -> dict:
    """Expose the frozen strict-SKU score for each original company."""
    per_site = {}
    for station in LABELS:
        rows = [item for item in items if item["source_station"] == station]
        covered = [item for item in rows if item["manual_expected_code"] is not None]
        failures = [item for item in covered if not item["manual_identity_correct"]]
        per_site[station] = {
            "source_events": len(rows),
            "manual_aligned_events": sum(item["manual_row"] is not None for item in rows),
            "current_catalog_manual_sku_events": len(covered),
            "manual_identity_correct": len(covered) - len(failures),
            "strict_sku_failures": [
                {"source_event_id": item["source_event_id"], "raw_text": item["raw_text"],
                 "manual_expected_code": item["manual_expected_code"], "selected_code": item["selected_code"],
                 "review_expected_codes": item["review_expected_codes"],
                 "reference_exclusion": item["reference_exclusion"]}
                for item in failures
            ],
        }
    for site_key, total_key in (
        ("source_events", "source_event_denominator"),
        ("manual_aligned_events", "manual_aligned_denominator"),
        ("current_catalog_manual_sku_events", "current_catalog_manual_sku_denominator"),
        ("manual_identity_correct", "manual_identity_correct"),
    ):
        if sum(site[site_key] for site in per_site.values()) != metrics[total_key]:
            raise ValueError(f"v4 per-site {site_key} does not reconcile with replay totals")
    return per_site


def main() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    validation = {
        "passed": not report["errors"]
        and report["original_hashes_unchanged"]
        and not report["manual_row_mismatches"],
        "evaluation_type": report["evaluation_type"],
        "simulation_manifest": report["simulation_manifest"],
        "simulation_sha256": report["simulation_sha256"],
        "manual_workbook_sha": report["manual_workbook_sha"],
        "manual_rows_verified": report["manual_rows_verified"],
        "manual_row_mismatches": report["manual_row_mismatches"],
        "comparison_workbook": report["comparison_workbook"],
        "original_hashes_unchanged": report["original_hashes_unchanged"],
        "errors": report["errors"],
        "scenarios": {},
        "limitations": report["limitations"],
    }
    for scenario, data in report["scenarios"].items():
        validation["scenarios"][scenario] = {
            "sample_count": data["sample_count"],
            "event_count": data["event_count"],
            "lanes": {},
        }
        for lane, lane_data in data["lanes"].items():
            m = lane_data["metrics"]
            validation["scenarios"][scenario]["lanes"][lane] = {
                "source_events": m["source_event_denominator"],
                "manual_aligned_events": m["manual_aligned_denominator"],
                "current_catalog_manual_sku_events": m["current_catalog_manual_sku_denominator"],
                "parsed_exact_span": m["parsed_exact_span"],
                "review_quantity_unit_correct": m["review_quantity_unit_correct"],
                "manual_identity_correct": m["manual_identity_correct"],
                "expected_exception_events": m["expected_exception_denominator"],
                "exception_candidates_match": m["exception_candidates_match_v3"],
                "exception_acceptance_passed": m["exception_acceptance_passed"],
                "output_rows_verified": m["output_rows_verified"],
                "unresolved_rows": m["unresolved"],
                "merged_finance_verified": lane_data["merge_verified"],
                "merged_finance_attempted": lane_data["merge_attempted"],
                "per_site_identity": per_site_identity(lane_data["items"], m),
            }
    (V4 / "validation_result.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# v4 拆分、重排与跨站组合交叉核验",
        "",
        "本报告对应当前 `evaluation/v4/replay_audit.json`，属于三站历史材料的变形回归，不是未见订单盲测。",
        "",
        "## 结果",
        "",
        "固定种子 `20261108` 生成单站拆分21份、单站重排3份、跨站组合10份；每种场景都覆盖三站原始159条事件一次。当前 skill 的 profile-only 回放和历史决策回放都保留完整事件，不因异常、未决或输出失败删除分母。",
        "",
        "| 场景 | 回放口径 | 事件 | 人工对齐 | 当前目录可核SKU | 解析跨度 | 原量/单位 | 人工SKU一致 | 异常候选 | 异常处理 | 输出复读 | 财务合并 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario, data in report["scenarios"].items():
        for lane, lane_data in data["lanes"].items():
            m = lane_data["metrics"]
            lines.append(
                f"|{scenario}|{lane}|{m['source_event_denominator']}|{m['manual_aligned_denominator']}|"
                f"{m['current_catalog_manual_sku_denominator']}|{m['parsed_exact_span']}|"
                f"{m['review_quantity_unit_correct']}|{m['manual_identity_correct']}|"
                f"{m['exception_candidates_match_v3']}/{m['expected_exception_denominator']}|"
                f"{m['exception_acceptance_passed']}/{m['expected_exception_denominator']}|"
                f"{m['output_rows_verified']}|{lane_data['merge_verified']}/{lane_data['merge_attempted']}|"
            )
    lines += [
        "",
        "profile-only 与历史决策回放都保持严格人工表口径 `127/132 = 96.21%`；这是当前目录可核人工SKU的历史参考回归。全部对齐事件仍是 `127/155 = 81.94%`，全部聊天事件覆盖是 `127/159 = 79.87%`。这些数字不能称为整表自动正确率，也不能称为未见订单准确率。",
        "",
        "## 分站严格人工SKU差异",
        "",
        "| 场景 | 回放口径 | 站点 | 聊天事件 | 人工对齐 | 当前目录可核人工SKU一致 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for scenario, data in validation["scenarios"].items():
        for lane, lane_data in data["lanes"].items():
            for station, site in lane_data["per_site_identity"].items():
                lines.append(f"|{scenario}|{lane}|{LABELS[station]}|{site['source_events']}|"
                             f"{site['manual_aligned_events']}|"
                             f"{site['manual_identity_correct']}/{site['current_catalog_manual_sku_events']}|")
    failures = validation["scenarios"]["single_station_split"]["lanes"]["historical_decisions"]["per_site_identity"]
    lines += [
        "",
        "五项严格人工SKU未中与v3一致；历史决策按原文筛选后，有四项选择不同SKU、一项保留未决。profile-only不注入逐项Agent决策，五项均保留未选状态。不能为提高同表得分强行沿用冲突的人工商品。",
        "",
        "| 站点 | 原文 | 人工SKU | 历史决策SKU | 独立审阅允许SKU |",
        "|---|---|---|---|---|",
    ]
    for station, site in failures.items():
        for item in site["strict_sku_failures"]:
            selected = f"`{item['selected_code']}`" if item["selected_code"] else "未选"
            lines.append(f"|{LABELS[station]}|{item['raw_text']}|`{item['manual_expected_code']}`|"
                         f"{selected}|"
                         f"{', '.join('`' + code + '`' for code in item['review_expected_codes']) or '未决'}|")
    lines += [
        "",
        "松北四项分别涉及下五花部位、玉米粒袋/罐包装、秋林/农大品牌和罗马/西生菜品种；星月一项涉及鸡腿块与人工泛称鸡肉块的部位差异。分站最弱为松北40/44（90.91%），不能用三站合计96.21%表述为每站达95%。",
        "",
        "## 直接核对人工原始工作簿",
        "",
        f"三份人工标准订单按 review 对齐事件读取并复核 `{report['manual_rows_verified']}` 个事件行；不同人工行153行；不一致0行。人工工作簿SHA-256为：`{json.dumps(report['manual_workbook_sha'], ensure_ascii=False)}`。人工表数量和历史价格只作为人工文件事实核对，不反填为本批下单需求或当前目录价格。",
        "",
        "## 交付证据",
        "",
        f"- [模拟样本清单](simulation/manifest.json)，固定seed及34份TXT的来源/字符跨度记录。",
        f"- [模拟样本自测](simulation/self-test.json)，同seed复现、重解析、159事件覆盖和六类分隔符通过。",
        f"- [回放逐项JSON](replay_audit.json)，包含954条场景/口径事件、状态、SKU、金额、异常候选、原始来源和输出指纹。",
        f"- [159事件逐项对比工作簿](../v4逐项对比.xlsx)，概览、逐事件和人工工作簿核验三张表；保存后954行逐字段复读。",
        f"- [视觉与保存后复核](../visual_qa_v4.md)。",
        f"- [结构化验证结果](validation_result.json)。",
        f"- [最新回放输出索引](../../outputs/v4/README.md)。",
        "",
        "## 限制",
        "",
        "- profile-only 只运行确定性执行器和已注册历史profile，没有在回放中重新调用Agent语义判断；历史决策口径复用了同一批历史拟合决策，因此不是独立准确率。",
        "- 跨站组合先依据模拟清单中的真实来源站点分区，再使用对应公司profile；这验证了组合后不串用profile，不验证从一份混合聊天自动识别公司。",
        "- 把事件反事实改归其他公司没有目标公司的独立人工答案，本轮不继承原站SKU，也没有将该测试冒充通过。",
        "- 拆分可能把“再加/这样的/改量/取消”事件链分到不同批次，本轮验证的是事件保留和解析不变性，不替代完整跨批事件语义验收。",
        "- 真实宿主加载、未见新订单、订单系统导入、实际配送和正式财务入账仍未验证。",
        "",
        f"当前结构化结果：`{V4 / 'validation_result.json'}`。",
    ]
    (V4 / "v4_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    output_root = Path(report["output_root"])
    index = [
        "# v4 模拟回放输出",
        "",
        "以下输出来自当前回放的隔离runtime，均为测试、待人工处理和财务预估草稿。JSON台账中保留每一批标准订单、财务草稿、原生批注、颜色和manifest；不要把它们当作正式配送或入账文件。",
        "",
        f"- 最新回放根目录：`{output_root}`",
        f"- [v4回放报告](../../evaluation/v4/v4_report.md)",
        f"- [结构化验证](../../evaluation/v4/validation_result.json)",
        f"- [逐项回放JSON](../../evaluation/v4/replay_audit.json)",
        f"- [模拟样本清单](../../evaluation/v4/simulation/manifest.json)",
        f"- [逐项对比工作簿](../../evaluation/v4逐项对比.xlsx)",
        "",
        "每个场景均有 `profile_only` 和 `historical_decisions` 两条口径；跨站组合输出按事件来源站点拆成公司批次后合并财务，原始混合TXT仅作为模拟输入保存。",
    ]
    (ROOT / "outputs/v4/README.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(json.dumps({"passed": validation["passed"], "validation": str(V4 / "validation_result.json"), "report": str(V4 / "v4_report.md")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
