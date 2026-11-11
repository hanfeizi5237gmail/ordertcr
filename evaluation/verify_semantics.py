"""Reproducible, offline checks for the independently labeled chat cases.

Run from any directory with the bundled Python. This reads only the three
original chats, the price catalog, and scenarios/semantic_cases.json. It
writes semantic_report.json and semantic_report.md under --output-dir (default:
this evaluation directory). Human-language expected_behavior/must_not labels
are included for review; automated claims are limited to explicit guards.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "order-mapping" / "scripts"))
from order_mapping_core.matching import match_item  # noqa: E402
from order_mapping_core.parsing import parse_chat  # noqa: E402


# Only unambiguous candidate exclusions and essential exemplars are automated;
# this table is not a replacement for human review of all catalog semantics.
_CANDIDATE_GUARDS = {
    "xingyue-chicken-block-raw-prepared": {"include": ["鸡肉块", "正大美味鸡块1kg/袋"]},
    "xingyue-pickled-cabbage-case": {"exclude": ["酸菜鱼调料", "酸菜馅"]},
    "xingyue-cauliflower-first": {"exclude": ["韭菜花"]},
    "xingyue-cauliflower-repeat": {"exclude": ["韭菜花"]},
    "xingyue-pork-elbow-hair": {"include": ["前肘剔骨.燎毛"]},
    "xueyuan-tofu-box-variety": {"exclude": ["麻婆豆腐", "鱼豆腐", "臭豆腐", "豆腐脑", "干豆腐"]},
    "xueyuan-fresh-raw-cut-hair": {"include": ["生猪前手（燎毛）"], "exclude": ["猪手冻"]},
    "xueyuan-red-pepper-color": {"include": ["红尖椒"], "exclude": ["地产尖椒"]},
    "songbei-pork-ribs-cut": {"exclude": ["排骨酱", "牛排骨"]},
    "songbei-egg-basket": {"exclude": ["鸡蛋面", "鸡蛋干", "鸡蛋挂面"]},
    "songbei-bagged-corn": {"include": ["冻玉米粒嘉利得0.9kg*10袋/件"], "exclude": ["罐头", "/罐"]},
    "songbei-autumn-red-sausage": {"include": ["秋林里道斯红肠"], "exclude": ["农大红肠", "风味红肠", "一手店红肠"]},
}


def _catalog_records():
    filename = ROOT / "original" / "食堂食材基准价价格表10.12-10.18.xlsx"
    workbook = load_workbook(filename, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        return [dict(code=str(code), name=name, category=category, spec=spec,
                     price=str(price), sheet=sheet.title, row=row)
                for row, (category, code, name, price, spec)
                in enumerate(sheet.iter_rows(min_row=5, max_col=5, values_only=True), 5)
                if code is not None]
    finally:
        workbook.close()


def _case_checks(case, item, original, records):
    expected = case["expected"]
    checks = []

    def check(label, condition, found=None):
        entry = {"check": label, "passed": bool(condition)}
        if not condition:
            entry["observed"] = found
        checks.append(entry)

    if expected.get("is_item") is False:
        check("date title not emitted as product", item is None,
              item.get("raw_text") if item else None)
        context = parse_chat(f"{case['raw_text']}\n验证商品1斤")
        actual = context[0]["date_context"] if len(context) == 1 else None
        check("date title context preserved", actual == expected["date_context"], actual)
        return checks

    check("one product at source line", item is not None,
          None if item is None else item["raw_text"])
    if item is None:
        return checks
    check("raw text and character span", item["raw_text"] == case["raw_text"]
          and original[item["char_start"]:item["char_end"]] == case["raw_text"],
          item["raw_text"])
    for key in ("name", "quantity", "unit", "date_context"):
        if key in expected:
            check(f"expected {key}", item[key] == expected[key], item[key])
    for key in ("processing_note", "packaging_note", "specification_note", "reference_note"):
        if key in expected:
            check(f"preserve {key}", expected[key] in item.get("notes", ""), item.get("notes"))
    if "event" in expected:
        check("preserve event", item.get("event") == expected["event"], item.get("event"))
    result = match_item(item, records)
    check("no unconfirmed SKU selection", result["status"] != "matched", result["status"])
    check("no unconfirmed invoice quantity", result["invoice_quantity"] is None,
          result["invoice_quantity"])
    check("candidate list not truncated", isinstance(result["candidates"], list), None)
    names = [str(candidate["name"]) for candidate in result["candidates"]]
    guards = _CANDIDATE_GUARDS.get(case["id"], {})
    for name in guards.get("include", []):
        check(f"candidate includes {name}", name in names, names)
    for fragment in guards.get("exclude", []):
        check(f"candidate excludes {fragment}", all(fragment not in name for name in names), names)
    return checks


def _random_variants(seed, trials):
    """Mutate only separators, not names/quantities or original files."""
    rng = random.Random(seed)
    sample_lines = [
        "香其酱10袋", "白糖5斤", "味素5斤", "酸菜4袋", "胡萝卜2斤",
        "油麦菜5斤", "鸡爪3袋", "大米1袋", "蒜瓣3斤", "5色糙米10斤",
    ]
    expected = [(item["name"], item["quantity"], item["unit"])
                for text in sample_lines for item in parse_chat(text)]
    separators = ("，", ",", "、", "；", "\n", " ")
    failures = []
    for trial in range(trials):
        text = sample_lines[0]
        for next_line in sample_lines[1:]:
            text += rng.choice(separators) + next_line
        result = parse_chat(text, f"separator-variant-{trial}")
        observed = [(item["name"], item["quantity"], item["unit"]) for item in result]
        if observed != expected or any(item["raw_text"] != text[item["char_start"]:item["char_end"]] for item in result):
            failures.append({"trial": trial, "input": text, "observed": observed})
    return {"seed": seed, "trials": trials, "passed": trials - len(failures),
            "failed": len(failures), "failures": failures[:10]}


def run(output_dir, *, seed=20261107, trials=100):
    scenario_path = ROOT / "evaluation" / "scenarios" / "semantic_cases.json"
    cases = json.loads(scenario_path.read_text(encoding="utf-8"))["cases"]
    records = _catalog_records()
    chat_cache = {}
    results = []
    for case in cases:
        path = (scenario_path.parent / case["source_file"]).resolve()
        if path not in chat_cache:
            text = path.read_text(encoding="utf-8")
            chat_cache[path] = (text, parse_chat(text, path.name))
        text, items = chat_cache[path]
        matches = [item for item in items if item["source_line"] == case["source_line"]]
        item = matches[0] if len(matches) == 1 else None
        checks = _case_checks(case, item, text, records)
        results.append({"id": case["id"], "source": path.name,
                        "source_line": case["source_line"],
                        "passed": all(check["passed"] for check in checks),
                        "checks": checks,
                        "expected_behavior": case["expected_behavior"],
                        "must_not": case["must_not"]})
    variants = _random_variants(seed, trials)
    report = {
        "scenario_source": str(scenario_path),
        "catalog_source": "original/食堂食材基准价价格表10.12-10.18.xlsx",
        "automated_scope": "结构化期望、来源跨度、日期标题、备注保留、指定品种/形态候选守卫和不得自动选SKU/计价；其余中文语义仍需人工评估",
        "total_cases": len(results), "passed_cases": sum(result["passed"] for result in results),
        "failed_cases": sum(not result["passed"] for result in results),
        "checks": sum(len(result["checks"]) for result in results),
        "passed_checks": sum(check["passed"] for result in results for check in result["checks"]),
        "cases": results, "separator_variants": variants,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "semantic_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 语义样例独立验证", "",
             f"机器可核对样例：{report['passed_cases']}/{report['total_cases']}；检查项：{report['passed_checks']}/{report['checks']}。",
             f"随机分隔变体：{variants['passed']}/{variants['trials']}（seed={seed}，使用逗号、空格、换行、顿号等）。",
             "", "机器检查覆盖结构化期望、原文跨度、日期上下文、备注、指定品种/形态候选守卫和未确认商品不自动计价；其余中文业务语义仍需人工复核。", "",
             "## 未通过项", ""]
    for result in results:
        if not result["passed"]:
            failures = "; ".join(f"{check['check']}: {check.get('observed')!r}"
                                 for check in result["checks"] if not check["passed"])
            lines.append(f"- {result['id']}（{result['source']}:{result['source_line']}）：{failures}")
    if variants["failed"]:
        lines.append(f"- 随机分隔变体失败 {variants['failed']} 条；完整输入和结果见JSON。")
    if report["failed_cases"] == variants["failed"] == 0:
        lines.append("- 无机器检查失败项。")
    (output_dir / "semantic_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evaluation")
    parser.add_argument("--seed", type=int, default=20261107)
    parser.add_argument("--trials", type=int, default=100)
    options = parser.parse_args()
    outcome = run(options.output_dir, seed=options.seed, trials=options.trials)
    print(json.dumps({"passed_cases": outcome["passed_cases"],
                      "total_cases": outcome["total_cases"],
                      "separator_variants": outcome["separator_variants"]["passed"],
                      "trials": options.trials}, ensure_ascii=False))
    sys.exit(0 if outcome["failed_cases"] == 0 and outcome["separator_variants"]["failed"] == 0 else 1)
