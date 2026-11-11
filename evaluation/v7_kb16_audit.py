#!/usr/bin/env python3
"""Score KB16 after all raw three-mode runs have completed."""
import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evaluation"))
from v6_finance_audit import audit_result


CONTROL_EVENTS = ("追加", "改量", "取消")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def eq(left, right):
    if left is None or right is None:
        return left is None and right is None
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError, TypeError):
        return str(left) == str(right)


def is_automatic(row):
    """Whether a result row contributes to A's actual-output denominator."""
    return (
        row.get("status") == "matched"
        and row.get("event_state", "active") == "active"
    )


def metric(rows):
    """A uses actual automatic outputs; B is automatic_correct over every expected event; business processing is reported separately."""
    automatic = [row for row in rows if row["automatic"]]
    automatic_correct = sum(row["automatic_correct"] for row in automatic)
    inputs = [row for row in rows if row.get("kind") != "unexpected_output"]
    business_correct = sum(row["correct"] for row in inputs)
    input_count = len(inputs)
    return {
        "events": input_count,
        "automatic": len(automatic),
        "automatic_correct": automatic_correct,
        "correct": business_correct,
        "business_correct": business_correct,
        "A": round(100 * automatic_correct / len(automatic), 4) if automatic else None,
        "B": round(100 * automatic_correct / input_count, 4) if input_count else None,
        "business_processing_rate": round(100 * business_correct / input_count, 4) if input_count else None,
        "review_correct": sum(row["review_correct"] for row in rows),
        "control_correct": sum(row["control_correct"] for row in rows),
        "control_safe": sum(row["control_safe"] for row in rows),
        "C": None,
        "C_status": "unknown: candidate_completeness is undefined",
    }


def valid_span(row, event, chat):
    start, end = row.get("char_start"), row.get("char_end")
    line = event.get("source_line")
    lines = chat.splitlines(keepends=True)
    if type(start) is not int or type(end) is not int or type(line) is not int or not 1 <= line <= len(lines):
        return False
    line_start = sum(map(len, lines[:line - 1]))
    line_end = line_start + len(lines[line - 1].rstrip("\r\n"))
    return line_start <= start <= end <= line_end and chat[start:end] == row.get("raw_text") == event["source_line_text"]


def align_events(expected, actual, chat):
    """One-to-one alignment by source line and exact source span/text."""
    remaining = list(enumerate(actual))
    pairs = []
    for event in expected:
        candidates = []
        for pos, item in remaining:
            row = item.get("row", {})
            if row.get("source_line") != event["source_line"]:
                continue
            if valid_span(row, event, chat):
                candidates.append((pos, item))
        if len(candidates) == 1:
            pos, item = candidates[0]
            remaining.remove((pos, item))
            pairs.append((event, item, None))
        elif not candidates:
            pairs.append((event, None, "missing_output"))
        else:
            pairs.append((event, None, "multiple_matching_outputs"))
    extras = [item for _, item in remaining]
    return pairs, extras


def score_event(event, item, company_ids, chat, catalog_by_sku=None):
    errors = []
    row = item.get("row", {}) if item else {}
    selected = row.get("selected") or {}

    def check(condition, code):
        if not condition:
            errors.append(code)

    if item is None:
        return {"event_id": event["event_id"], "kind": event["kind"], "concept": event.get("concept"),
                "company": event.get("company"), "date": event.get("date"), "source_line": event["source_line"],
                "source_text": event["source_line_text"], "automatic": False, "automatic_correct": False,
                "correct": False, "review_correct": False, "control_correct": False, "control_safe": False,
                "errors": ["missing_output"]}

    check(valid_span(row, event, chat), "source_span")
    check(item.get("company_id") == company_ids.get(event.get("company"), "__missing__"), "company")
    check(item.get("shipping_date") == event.get("date"), "date")
    kind = event["kind"]
    actual_automatic = is_automatic(row)
    automatic = actual_automatic
    review_correct = False
    control_correct = False
    control_safe = False

    if kind == "conditional_match":
        expected = event["expected_catalog_item"]
        quantity_ok = eq(row.get("quantity"), event["quantity"]) and row.get("unit") == event["unit"]
        amount = Decimal(str(expected["price"])) * Decimal(str(event["quantity_500g"])) * Decimal("0.95")
        check(actual_automatic, "not_automatic")
        check(str(selected.get("code")) == str(expected["sku"]), "sku")
        check(selected.get("name") == expected["name"] and selected.get("spec") == expected["spec"], "name_spec")
        check(quantity_ok, "quantity_unit")
        check(eq(row.get("invoice_quantity"), event["quantity_500g"]) and row.get("price_unit") == "500g", "invoice_quantity_unit")
        check(eq(selected.get("price"), expected["price"]), "catalog_price")
        check(eq(row.get("discount"), "0.95"), "discount")
        check(eq(row.get("unit_price"), Decimal(str(expected["price"])) * Decimal("0.95")), "unit_price")
        check(eq(row.get("amount"), amount) and eq(row.get("finance_amount"), amount), "amount")
    elif kind.startswith("unknown_") or kind in {"conflicting_part", "missing_quantity", "missing_unit"}:
        # Review correctness is safety correctness; it does not establish semantic completeness.
        check(not actual_automatic, "review_automatic")
        check(row.get("status") in {"needs_review", "unmapped"}, "review_status")
        check(row.get("amount") is None and row.get("finance_amount") is None, "review_amount_nonblank")
        check(row.get("invoice_quantity") is None, "review_invoice_quantity_filled")
        if kind == "unknown_product":
            check(not selected, "unknown_product_selected")
        if kind == "missing_quantity":
            check(row.get("quantity") is None, "missing_quantity_filled")
        if kind == "missing_unit":
            check(row.get("unit") is None or row.get("invoice_quantity") is None, "missing_unit_filled")
        review_correct = not errors
    elif kind in {"initial", "append", "change", "cancel"}:
        actions = {"append": "追加", "change": "改量", "cancel": "取消"}
        if actual_automatic and kind in {"initial", "append"}:
            expected_sku = event.get("assumed_selected_sku")
            expected_qty = event.get("quantity_500g")
            if kind == "initial" and expected_qty is None:
                expected_qty = event.get("assumed_active_total_500g")
            expected_item = event.get("expected_catalog_item") or (catalog_by_sku or {}).get(str(expected_sku))
            check(bool(expected_sku) and expected_qty is not None and bool(expected_item), "control_oracle_incomplete")
            if expected_item:
                check(str(expected_item.get("sku")) == str(expected_sku), "control_oracle_sku")
                check(str(selected.get("code")) == str(expected_sku), "control_sku")
                check(selected.get("name") == expected_item.get("name") and selected.get("spec") == expected_item.get("spec"), "control_name_spec")
                check(eq(selected.get("price"), expected_item.get("price")), "control_catalog_price")
            check(expected_qty is not None and eq(row.get("invoice_quantity"), expected_qty) and row.get("price_unit") == "500g", "control_invoice_quantity")
            check(event.get("quantity") is not None and eq(row.get("quantity"), event.get("quantity")) and event.get("unit") is not None and row.get("unit") == event.get("unit"), "control_quantity_unit_oracle")
            check(eq(row.get("discount"), "0.95"), "control_discount")
            if expected_item and expected_item.get("price") is not None and expected_qty is not None:
                unit_price = Decimal(str(expected_item["price"])) * Decimal("0.95")
                check(eq(row.get("unit_price"), unit_price), "control_unit_price")
                amount = unit_price * Decimal(str(expected_qty))
                check(eq(row.get("amount"), amount) and eq(row.get("finance_amount"), amount), "control_amount")
            if kind == "append":
                check(row.get("event") == actions[kind], "control_event_kind")
                reference = event.get("expected_predecessor_id")
                check(reference is not None and row.get("predecessor_id") == reference, "control_relation_unproven")
            control_correct = not errors
        else:
            check(not actual_automatic, "control_automatic")
            check(row.get("status") in {"needs_review", "unmapped"}, "control_review_status")
            check(row.get("amount") is None and row.get("finance_amount") is None, "control_amount_nonblank")
            check(row.get("invoice_quantity") is None, "control_invoice_quantity_filled")
            if kind != "initial":
                check(row.get("event") == actions[kind], "control_event_kind")
                check(row.get("event_state") == "pending" or (kind == "append" and row.get("event_state") == "active"), "control_relation_not_retained")
            control_safe = not errors
            control_correct = control_safe and kind != "initial"
    else:
        check(False, "unknown_expected_kind")

    automatic_correct = automatic and not errors and (kind in {"conditional_match", "initial", "append"})
    correct = (automatic_correct if kind == "conditional_match" else review_correct or control_correct or control_safe)
    return {"event_id": event["event_id"], "kind": kind, "concept": event.get("concept"), "company": event.get("company"),
            "date": event.get("date"), "source_line": event["source_line"], "source_text": event["source_line_text"],
            "outcome": event.get("outcome"), "automatic": automatic, "automatic_correct": automatic_correct,
            "correct": correct, "review_correct": review_correct, "control_correct": control_correct,
            "control_safe": control_safe, "errors": errors, "expected": event, "actual": item}


def validate_execution_inventory(status_doc, freeze):
    if not isinstance(status_doc, dict) or status_doc.get("status") != "runs_complete_before_audit" or status_doc.get("phase") != "all_runs_completed_before_expected_read" or status_doc.get("source_equal") is not True:
        raise ValueError("execution not complete with stable source")
    statuses = status_doc.get("executions")
    required = {(seed, mode) for seed in (8801, 8802, 8891) for mode in ("catalog_only", "shared", "shared_with_company_contract")}
    if not isinstance(statuses, list) or len(statuses) != len(required) or {(e.get("seed"), e.get("mode")) for e in statuses} != required:
        raise ValueError("execution mode/seed inventory incomplete or duplicated")
    for entry in statuses:
        if entry.get("register_rc") != 0 or entry.get("run_rc") != 0:
            raise ValueError("execution status failed")
        for key in ("request", "result"):
            try:
                path = Path(entry[key])
                relative = str(path.relative_to(ROOT))
            except (KeyError, ValueError, TypeError):
                raise ValueError("execution path not in workspace")
            if relative not in freeze["files_sha256"] or not path.is_file() or sha(path) != freeze["files_sha256"][relative]:
                raise ValueError("execution input is not a verified frozen artifact: " + relative)
    return statuses


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    args = parser.parse_args()
    dest = args.output.resolve()
    status_doc = read(args.status)
    freeze = read(Path(args.status).parent / "input_freeze.json")
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit("audit output must be a new empty directory")
    dest.mkdir(parents=True, exist_ok=True)
    mismatches = [path for path, digest in freeze["files_sha256"].items() if not (ROOT / path).is_file() or sha(ROOT / path) != digest]
    # The rc5 runner froze source fingerprints before execution, but later
    # evaluation-only work may update the live skill files.  Those source
    # drifts are reported for provenance; the immutable challenge, request,
    # runtime and output artifacts still remain a hard gate.
    artifact_mismatches = [path for path in mismatches if not (path.startswith("skills/") or path.startswith("original/"))]
    if artifact_mismatches:
        raise ValueError("frozen artifact changed: " + ",".join(artifact_mismatches))
    statuses = validate_execution_inventory(status_doc, freeze)

    runs = []
    by_mode = defaultdict(list)
    for status in statuses:
        seed, mode = status["seed"], status["mode"]
        base = ROOT / "evaluation/v7/kb16_challenge_v1" / str(seed)
        expected = read(base / "expected.json")  # deliberately after all run/freeze checks
        result = read(status["result"])
        chat = (base / "input/聊天.txt").read_text(encoding="utf-8-sig")
        request = read(base / "input/request.json")
        company_ids = {x["company_name"]: f"c{i}" for i, x in enumerate(request["companies"], 1)}
        company_ids["未登记单位"] = "unassigned"
        pairs, extras = align_events(expected["events"], result.get("events", []), chat)
        catalog_by_sku = {str(e["expected_catalog_item"]["sku"]): e["expected_catalog_item"] for e in expected["events"] if e.get("expected_catalog_item")}
        scored = [score_event(event, item, company_ids, chat, catalog_by_sku) for event, item, _ in pairs]
        for extra in extras:
            row = extra.get("row", {})
            scored.append({"event_id": None, "kind": "unexpected_output", "concept": None, "company": None, "date": None,
                           "source_line": row.get("source_line"), "source_text": row.get("raw_text"), "automatic": is_automatic(row),
                           "automatic_correct": False, "correct": False, "review_correct": False, "control_correct": False,
                           "control_safe": False, "errors": ["unexpected_output"], "actual": extra})
        finance = audit_result(result)
        summary = metric(scored)
        run = {"seed": seed, "mode": mode, "summary": summary,
               "by_company": {key: metric([x for x in scored if x.get("company") == key]) for key in sorted({x.get("company") for x in scored}, key=lambda x: str(x))},
               "by_concept": {key: metric([x for x in scored if x.get("concept") == key]) for key in sorted({x.get("concept") for x in scored}, key=lambda x: str(x))},
               "by_kind": {key: metric([x for x in scored if x.get("kind") == key]) for key in sorted({x.get("kind") for x in scored}, key=lambda x: str(x))},
               "failure_categories": dict(Counter(error for x in scored for error in x["errors"])),
               "missing_outputs": sum("missing_output" in x["errors"] for x in scored),
               "unexpected_outputs": sum("unexpected_output" in x["errors"] for x in scored),
               "finance_audit": finance, "events": scored}
        runs.append(run)
        by_mode[mode].extend(scored)

    mode_summaries = {mode: metric(rows) for mode, rows in by_mode.items()}
    report = {"schema_version": 2, "input_freeze_sha256": sha(Path(args.status).parent / "input_freeze.json"),
              "freeze_source_mismatches": [path for path in mismatches if path.startswith(("skills/", "original/"))],
              "evaluator_sha256": sha(Path(__file__)), "mode_summaries": mode_summaries, "runs": runs,
              "C_policy": {"status": "unknown", "reason": "candidate_completeness is undefined; control safety is reported separately", "control_events_in_B_denominator": True},
              "scope": "KB16 frozen challenge; not real customer acceptance"}
    (dest / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# KB16 replay audit", "", "A uses actual automatic outputs (matched active rows), so review rows that are incorrectly auto matched count as A errors. B is automatic-correct over all expected input events; review/control rows remain in the denominator but do not count as automatic success. `business_processing_rate` separately includes correctly preserved review/control handling. C is unknown because candidate completeness is undefined; control safety is reported separately.", "", "| mode | events | automatic | automatic correct | correct | A | B | missing | unexpected | finance errors |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for mode, summary in mode_summaries.items():
        selected = [run for run in runs if run["mode"] == mode]
        lines.append(f"| {mode} | {summary['events']} | {summary['automatic']} | {summary['automatic_correct']} | {summary['correct']} | {summary['A']} | {summary['B']} | {sum(run['missing_outputs'] for run in selected)} | {sum(run['unexpected_outputs'] for run in selected)} | {sum(len(run['finance_audit']['errors']) for run in selected)} |")
    lines += ["", "Per-seed, company, concept, kind, control relation and finance replay are in report.json. Expected files were read only after all raw runs and freeze checks. No candidate completeness is inferred from expected examples."]
    (dest / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"mode_summaries": mode_summaries}, ensure_ascii=False))


if __name__ == "__main__":
    main()
