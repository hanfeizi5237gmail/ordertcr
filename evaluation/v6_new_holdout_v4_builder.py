#!/usr/bin/env python3
"""Create the next frozen holdout after v3 was consumed for diagnostics."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORACLE_DIR = ROOT / "evaluation/v6/oracle"
SOURCE = ORACLE_DIR / "oracle_v3.json"
OUT = ORACLE_DIR / "oracle_v4.json"
LABELS = {"songbei": "松北大道站", "xingyue": "星月站", "xueyuan": "学院路站", "newco": "新公司"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clone_case(case: dict, index: int) -> dict:
    out = copy.deepcopy(case)
    out["case_id"] = case["case_id"].replace("6111", "6112", 1)
    out["seed"] = 6112
    out["split"] = "holdout"
    pieces = []
    expected = []
    cursor = 0
    for event_index, event in enumerate(case["expected_events"]):
        event = copy.deepcopy(event)
        company = LABELS.get(event.get("company"), event.get("company") or "")
        date = event.get("date") or "2026-11-03"
        raw = event["raw_text"]
        # A third rendering shape: separate station/date headings followed by
        # the raw item line.  This exercises context carry-over and keeps the
        # source span limited to the original item text.
        line = f"【{company}】\n{date}\n{raw}"
        if pieces:
            sep = "\n\n" if (event_index + index) % 2 == 0 else "\n"
            pieces.append(sep)
            cursor += len(sep)
        pieces.append(line)
        start = cursor + line.rfind(raw)
        event["source_span"] = [start, start + len(raw)]
        # v4's generated text explicitly carries the date for every item;
        # stale v2 date_context witness codes are not applicable here.
        event["required_issue_codes"] = [
            code for code in event.get("required_issue_codes", []) if code != "date_context"
        ]
        expected.append(event)
        cursor += len(line)
    out["raw_text"] = "".join(pieces) + "\n"
    out["expected_events"] = expected
    out["note"] = (case.get("note") or "") + "；v4 seed6112 使用括号公司标签、显式日期和新空白变体"
    return out


def build() -> dict:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    cases = []
    for case in source["cases"]:
        if case["seed"] == 6111:
            cases.append(clone_case(case, len(cases)))
        else:
            clone = copy.deepcopy(case)
            if clone["seed"] == 6110:
                clone["split"] = "diagnostic"
            cases.append(clone)
    result = copy.deepcopy(source)
    result["cases"] = cases
    result["case_count"] = len(cases)
    result["seed_splits"] = {"6108": "development", "6109": "development",
                              "6110": "diagnostic", "6111": "diagnostic", "6112": "holdout"}
    result["parent_oracle_sha256"] = digest(SOURCE)
    result["pre_run_corrections"] = [
        "6110 and 6111 were consumed for diagnosis and retained as diagnostic evidence.",
        "6112 re-renders the full source event set with explicit company/date brackets and removes only date_context witness codes that are inapplicable when the generated text carries an explicit date.",
    ]
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT.with_suffix(".sha256")).write_text(digest(OUT) + "  " + OUT.name + "\n", encoding="utf-8")
    (ORACLE_DIR / "README_v4.md").write_text(
        "# v6 oracle v4\n\n"
        "6110、6111 已用于诊断；6112 是新的正式 holdout。它保留159条历史商品事件，使用显式公司/日期括号和新空白变体。日期问题码只在输入未提供日期依据时适用。该集仍属于历史变形回归，不等于真实新订单。\n",
        encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    result = build()
    print(json.dumps({"output": str(OUT), "sha256": digest(OUT),
                      "case_count": result["case_count"],
                      "holdout_cases": sum(c["split"] == "holdout" for c in result["cases"])}, ensure_ascii=False))
