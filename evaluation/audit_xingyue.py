"""Reference-fitted Xingyue alignment, preserving duplicate and date events."""
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path

from audit_xueyuan import ROOT, EVAL, CATALOG, catalog_rows, normalized, sha, decimal
from openpyxl import load_workbook

MANUAL = ROOT / "original/星月站11.3.xlsx"
CHAT = ROOT / "original/星月站11.3下单.txt"


def manual_rows():
    sheet = load_workbook(MANUAL, read_only=True, data_only=True).active
    return {i: {"row": i, "name": sheet.cell(i, 4).value, "spec": sheet.cell(i, 5).value,
                "quantity": decimal(sheet.cell(i, 6).value), "price": decimal(sheet.cell(i, 7).value)}
            for i in range(2, 41)}


def manual_target(line):
    if 1 <= line <= 10:
        return line + 1
    if 14 <= line <= 32:
        return line - 2
    return {35: 31, 36: 32, 37: 33, 38: 34, 39: 35, 40: 36,
            42: 31, 43: 37, 44: 38, 45: 35, 48: 39, 49: 40}.get(line)


def main():
    catalog = catalog_rows()
    by_name = defaultdict(list)
    for record in catalog:
        by_name[normalized(record["name"])].append(record)
    report = json.loads((EVAL / "business_validation.json").read_text(encoding="utf-8"))
    scenario = next(s for s in report["scenarios"] if s["station"] == "星月站")
    rows = json.loads(Path(scenario["manifest"]).read_text(encoding="utf-8"))["order"]["rows"]
    manual = manual_rows()
    assert len(rows) == 42 and len(manual) == 39
    assert [r["source_line"] for r in rows if r["source_line"] in (39, 41, 42, 45, 48, 49)] == [39, 41, 42, 45, 48, 49]
    alignment = []
    linked = defaultdict(list)
    aliases = defaultdict(list)
    for item in rows:
        line = item["source_line"]
        target = manual_target(line)
        human = manual.get(target)
        evidence = {"line_id": item["line_id"], "chat_line": line, "raw_text": item["raw_text"],
                    "date_context": item.get("date_context"), "parsed_name": item["name"],
                    "requested_quantity": item.get("quantity"), "requested_unit": item.get("unit"),
                    "manual_row": target, "manual": human,
                    "alignment_kind": "manual_missing" if target is None else "possible_merged_manual" if line in (42, 45) else "ordered_name_anchor"}
        if human:
            linked[target].append(line)
            candidates = [c for c in by_name[normalized(human["name"])] if normalized(c["spec"]) == normalized(human["spec"])]
            price_matches = [c for c in candidates if Decimal(c["price"]) == Decimal(human["price"])]
            selected = price_matches[0] if len(price_matches) == 1 else candidates[0] if len(candidates) == 1 else None
            evidence["manual_catalog_code"] = selected["code"] if selected else None
            evidence["manual_price_matches_catalog"] = bool(selected and Decimal(selected["price"]) == Decimal(human["price"]))
            evidence["workflow_selected_code"] = item["selected"]["code"] if item.get("selected") else None
            # A merged/manual substituted identity is evidence of a discrepancy,
            # not permission to turn a distinct input variety into that SKU.
            if selected and line not in (42, 44):
                aliases[item["name"]].append((evidence, selected))
        alignment.append(evidence)
    assert len(alignment) == 42 and len(linked) == 39
    assert linked[31] == [35, 42] and linked[35] == [39, 45]
    assert next(r for r in alignment if r["chat_line"] == 41)["manual_row"] is None
    output = {"purpose": "reference_fitted_alignment_not_independent_accuracy", "company_id": "xingyue",
              "catalog_version": sha(CATALOG), "manual_sha256": sha(MANUAL), "chat_sha256": sha(CHAT),
              "workflow_manifest": scenario["manifest"], "counts": {"chat_items": 42, "manual_detail_rows": 39,
              "chat_events_with_manual_link": 41, "manual_missing_chat_events": 1,
              "manual_rows_with_multiple_chat_events": 2}, "rows": alignment,
              "ambiguous_history": [
                  {"manual_row": 31, "chat_lines": [35, 42], "reason": "油豆角6斤与豆角5斤可能被人工合并为油豆角11.2；不能推断实称或将豆角自动改品种"},
                  {"manual_row": 35, "chat_lines": [39, 45], "reason": "11月4日两条菜花6斤仍为两个原始事件；人工一行青梗菜花12.2，不拆分或删除重复"},
                  {"chat_line": 41, "reason": "11月4日第二条黄瓜5斤未见独立人工行，保留原始需求并待人工核定"}],
              "limitations": ["11月3、4、5日期上下文在逐行证据保留；人工表统一日期不证明已合并发货",
                              "人工称重和合并没有可验证的逐项分配；不复制实际数量或旧目录外价格"]}
    profile_aliases = {}
    for name, observed in sorted(aliases.items()):
        codes = {c["code"] for _, c in observed}
        if len(codes) != 1:
            continue
        notes = []
        for event, catalog_record in observed:
            human = event["manual"]
            notes.append(f"聊天{event['chat_line']}行“{event['raw_text']}”（日期上下文{event['date_context'] or '未单列'}）"
                         f"→人工表{human['row']}行“{human['name']}”→目录{catalog_record['catalog_row']}行"
                         f"SKU {catalog_record['code']}规格{catalog_record['spec']}挂牌价{catalog_record['price']}；"
                         "仅SKU历史偏好，不导入实配数量")
        profile_aliases[name] = {"code": next(iter(codes)),
                                 "request_units": sorted({e["requested_unit"] for e, _ in observed if e["requested_unit"]}),
                                 "evidence": "；".join(notes)}
    # Only the four unchanged integer package counts below become a
    # company-specific reference fit. Measured or merged quantities do not.
    pack_evidence = {1: ("大包", "80006120"), 6: ("大包", "80005696"),
                     7: ("袋", "80002361"), 32: ("袋", "80004266")}
    unit_rules = {}
    for source_line, (unit, code) in pack_evidence.items():
        event = next(r for r in alignment if r["chat_line"] == source_line)
        assert event["manual_catalog_code"] == code and event["requested_unit"] == unit
        assert Decimal(event["requested_quantity"]) == Decimal(event["manual"]["quantity"])
        unit_rules[code] = {"price_unit": unit, "request_units": {unit: "1"}, "allow_fractional": False,
                            "evidence": f"星月参考样本聊天{source_line}行“{event['raw_text']}”"
                                        f"与人工表{event['manual_row']}行SKU {code}计价数量同为{event['requested_quantity']}；"
                                        "仅本公司reference-fitted模式的包装偏好，不代表实际称重。"}
    profile = {"schema_version": 1, "profile_id": "xingyue-20261103-reference-fitted-v1",
               "company_id": "xingyue", "catalog_version": sha(CATALOG),
               "evidence": {"kind": "user_provided_reference", "source": MANUAL.name, "sha256": sha(MANUAL)},
               "aliases": profile_aliases, "unit_rules": unit_rules}
    (EVAL / "xingyue_alignment.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (EVAL / "xingyue.history-profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**output["counts"], "profile_aliases": len(profile_aliases), "unit_rules": len(unit_rules),
                      "manual_catalog_sku_identified": sum(bool(x.get("manual_catalog_code")) for x in alignment)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
