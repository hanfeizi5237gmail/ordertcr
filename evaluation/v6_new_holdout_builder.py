#!/usr/bin/env python3
"""Build an independent v6 holdout from frozen historical expectations.

The 6110 holdout was consumed for diagnosis.  This builder keeps the frozen
v2 oracle intact, re-renders its complete 159-event history with a new seed,
new separators/spacing and fresh source spans, then marks 6111 as the only
holdout in oracle_v3.  Labels stay in the evaluation file and never enter the
runtime request.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORACLE_DIR = ROOT / "evaluation/v6/oracle"
SOURCE = ORACLE_DIR / "oracle_v2.json"
OUT = ORACLE_DIR / "oracle_v3.json"
LABELS = {"songbei": "松北大道站", "xingyue": "星月站", "xueyuan": "学院路站", "newco": "新公司"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rerender(case: dict, seed: int, index: int) -> dict:
    """Re-render each event without changing its semantic event text."""
    out = copy.deepcopy(case)
    out["case_id"] = case["case_id"].replace("6110", str(seed), 1)
    out["seed"] = seed
    out["split"] = "holdout" if case["split"] == "holdout" else case["split"]
    pieces: list[str] = []
    expected = []
    cursor = 0
    events = copy.deepcopy(case["expected_events"])
    for event_index, event in enumerate(events):
        company = LABELS.get(event.get("company"), event.get("company") or "")
        date = event.get("date") or "2026-11-03"
        raw = event["raw_text"]
        # Every variant remains a normal business heading understood by the
        # parser.  The extra spaces and full-width punctuation exercise the
        # same boundary rules without altering the source item text.
        if (event_index + index) % 3 == 0:
            line = f"{company}\t{date}： {raw}"
        elif (event_index + index) % 3 == 1:
            line = f"{company} {date} :{raw}"
        else:
            line = f"{company}  {date}：{raw}"
        if pieces:
            separator = "\n\n" if (event_index + index) % 4 == 0 else "\n"
            pieces.append(separator)
            cursor += len(separator)
        pieces.append(line)
        raw_start = cursor + line.rfind(raw)
        raw_end = raw_start + len(raw)
        event["source_span"] = [raw_start, raw_end]
        event["date_basis"] = "source_date_explicitly_rendered"
        expected.append(event)
        cursor += len(line)
    out["raw_text"] = "".join(pieces) + "\n"
    out["expected_events"] = expected
    out["note"] = (case.get("note") or "") + "；v3 seed6111 重新渲染，分隔符与空白变化，语义原文保留"
    if "catalog_override" in case:
        out["catalog_override"] = copy.deepcopy(case["catalog_override"])
    return out


def build() -> dict:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    cases = []
    for case in source["cases"]:
        if case["seed"] == 6110:
            clone = rerender(case, 6111, len(cases))
            clone["split"] = "holdout"
            cases.append(clone)
        else:
            clone = copy.deepcopy(case)
            # 6110 remains in the file only as a diagnostic historical set;
            # it is excluded by split=holdout in formal replay.
            cases.append(clone)
    # Preserve the old cases but mark the consumed seed out of the formal
    # split.  This is an oracle versioning change, not a change to v2.
    for case in cases:
        if case["seed"] == 6110:
            case["split"] = "diagnostic"
    result = copy.deepcopy(source)
    result["cases"] = cases
    result["case_count"] = len(cases)
    result["seed_splits"] = {"6108": "development", "6109": "development",
                              "6110": "diagnostic", "6111": "holdout"}
    result["parent_oracle_sha256"] = sha(SOURCE)
    result["pre_run_corrections"] = [
        "6110 was used for diagnosis after the v6 knowledge/unit and replay-gate fixes; it is retained as diagnostic evidence.",
        "6111 is a deterministic re-render of the same frozen source events with new separators/spacing and fresh spans; it is historical metamorphic validation, not a real future order.",
    ]
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT.with_suffix(".sha256")).write_text(sha(OUT) + "  " + OUT.name + "\n", encoding="utf-8")
    readme = ORACLE_DIR / "README_v3.md"
    readme.write_text(
        "# v6 oracle v3\n\n"
        "v2 保持不变。6110 已用于诊断，不再作为最终未见保留集；6111 是按固定渲染规则生成的新 holdout，完整保留原始159事件，改变空白/分隔符并重新计算 source_span。\n\n"
        "该集仍属于历史样本变形回归，不等于真实新客户订单。运行端只读取生成的聊天文本、正常公司配置、当前目录和正式共享知识。\n",
        encoding="utf-8",
    )
    return result


if __name__ == "__main__":
    data = build()
    print(json.dumps({"output": str(OUT), "sha256": sha(OUT),
                      "case_count": data["case_count"],
                      "holdout_cases": sum(c["split"] == "holdout" for c in data["cases"])}, ensure_ascii=False))
