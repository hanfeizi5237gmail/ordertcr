"""Read-only historical alignment audit; manual orders are references, not truth."""
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
EVAL = Path(__file__).resolve().parent
CATALOG = ROOT / "original/食堂食材基准价价格表10.12-10.18.xlsx"
MANUAL = ROOT / "original/学院路站11.3.xlsx"
CHAT = ROOT / "original/学院路站11.3下单.txt"


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decimal(value):
    return str(Decimal(str(value))) if value is not None else None


def catalog_rows():
    sheet = load_workbook(CATALOG, read_only=True, data_only=True).active
    return [dict(code=str(code), name=name, spec=spec, price=decimal(price), catalog_row=i)
            for i, (_, code, name, price, spec) in enumerate(
                sheet.iter_rows(min_row=5, max_col=5, values_only=True), 5) if code]


def manual_rows():
    sheet = load_workbook(MANUAL, read_only=True, data_only=True).active
    return [dict(row=i, name=sheet.cell(i, 4).value, spec=sheet.cell(i, 5).value,
                 quantity=decimal(sheet.cell(i, 6).value), price=decimal(sheet.cell(i, 7).value))
            for i in range(2, sheet.max_row + 1) if sheet.cell(i, 4).value]


def alignment(source_line):
    # Order-preserving identity reviewed against both full sources. C58 is a
    # repeated cabbage request absent from the manual sheet; C64 is an extra
    # hairy pig-trotter request. M64 garlic is manual-only.
    if 1 <= source_line <= 57:
        return source_line + 1
    if 59 <= source_line <= 63:
        return source_line
    return None


def main():
    catalog = catalog_rows()
    manual = manual_rows()
    by_name = defaultdict(list)
    for record in catalog:
        by_name[normalized(record["name"])].append(record)
    report = json.loads((EVAL / "business_validation.json").read_text(encoding="utf-8"))
    scenario = next(s for s in report["scenarios"] if s["station"] == "学院路站")
    manifest = json.loads(Path(scenario["manifest"]).read_text(encoding="utf-8"))
    source = manifest["order"]["rows"]
    assert len(source) == 64 and len(manual) == 63
    assert [x["source_line"] for x in source] == list(range(1, 65))
    assert manual[57]["name"] == "青椒/大辣椒" and manual[-1]["name"] == "蒜瓣"
    manual_by_row = {x["row"]: x for x in manual}
    records, aliases, alias_conflicts = [], defaultdict(list), []
    for item in source:
        number = item["source_line"]
        manual_row = manual_by_row.get(alignment(number))
        entry = {"line_id": item["line_id"], "chat_line": number, "raw_text": item["raw_text"],
                 "parsed_name": item["name"], "requested_quantity": item.get("quantity"),
                 "requested_unit": item.get("unit"), "workflow_status": item["status"],
                 "selected_code": item["selected"]["code"] if item.get("selected") else None,
                 "manual": manual_row, "alignment": "ordered_name_anchor" if manual_row else "chat_only"}
        if manual_row is None:
            entry.update(manual_catalog_status="not_applicable", manual_code=None,
                         sku_comparison="manual_row_missing", spec_comparison="not_applicable",
                         quantity_comparison="not_applicable", differences=["聊天项目未见人工对应行；不能判为解析错误或已取消"])
            records.append(entry)
            continue
        same_name = by_name[normalized(manual_row["name"])]
        exact = [r for r in same_name if normalized(r["spec"]) == normalized(manual_row["spec"])
                 and Decimal(r["price"]) == Decimal(manual_row["price"])]
        identity = [r for r in same_name if normalized(r["spec"]) == normalized(manual_row["spec"])]
        entry["catalog_candidates"] = [r["code"] for r in same_name]
        entry["manual_catalog_status"] = "exact_unique" if len(exact) == 1 else (
            "name_price_or_spec_conflict" if same_name else "name_absent")
        entry["manual_code"] = exact[0]["code"] if len(exact) == 1 else None
        resolved = identity[0] if len(identity) == 1 else exact[0] if len(exact) == 1 else None
        entry["manual_identity_code"] = resolved["code"] if resolved else None
        entry["catalog_price_matches_manual"] = bool(resolved and
            Decimal(resolved["price"]) == Decimal(manual_row["price"]))
        selected = item.get("selected")
        entry["sku_comparison"] = ("same" if entry["manual_identity_code"] and selected and selected["code"] == entry["manual_identity_code"]
                                   else "different" if entry["manual_identity_code"] and selected
                                   else "not_selected" if entry["manual_identity_code"] else "manual_not_exact_catalog")
        entry["spec_comparison"] = ("same" if selected and normalized(selected["spec"]) == normalized(manual_row["spec"])
                                    else "different" if selected else "not_selected")
        if selected and item.get("invoice_quantity") is not None:
            entry["quantity_comparison"] = ("same" if Decimal(item["invoice_quantity"]) == Decimal(manual_row["quantity"])
                                            else "different_manual_quantity_possible_actual_or_edit")
        else:
            entry["quantity_comparison"] = "not_comparable_without_unit_or_actual_basis"
        differences = []
        if entry["manual_catalog_status"] != "exact_unique":
            differences.append("人工品目未能按名称+规格+挂牌价唯一定位当前目录SKU")
        if entry["sku_comparison"] == "different":
            differences.append("当前选择与人工目录SKU不同")
        if entry["sku_comparison"] == "not_selected":
            differences.append("当前流程未选定人工目录SKU")
        if entry["spec_comparison"] == "different":
            differences.append("规格不同")
        if entry["quantity_comparison"] == "different_manual_quantity_possible_actual_or_edit":
            differences.append("数量不同；人工值可能来自实称/改量，聊天不可推出")
        if item["status"] != "matched" and selected:
            differences.append("SKU已有候选但数量/事件仍阻塞金额")
        entry["differences"] = differences
        records.append(entry)
        if resolved:
            aliases[item["name"]].append((entry, resolved))

    # The provided sample is authorized as reference fitting for this company
    # and catalog; it is not a claim about future delivery quantities.
    profile_aliases = {}
    for name, observed in sorted(aliases.items()):
        codes = {catalog_row["code"] for _, catalog_row in observed}
        if len(codes) != 1:
            alias_conflicts.append({"name": name, "codes": sorted(codes)})
            continue
        notes = []
        for row, catalog_row in observed:
            price_note = (f"；人工旧价{row['manual']['price']}与当前目录价{catalog_row['price']}不同，取当前目录"
                          if Decimal(row["manual"]["price"]) != Decimal(catalog_row["price"]) else "")
            notes.append(f"聊天{row['chat_line']}行“{row['raw_text']}”→人工表{row['manual']['row']}行“{row['manual']['name']}”"
                         f"→目录{catalog_row['catalog_row']}行SKU {catalog_row['code']}，规格{catalog_row['spec']}"
                         f"，挂牌价{catalog_row['price']}{price_note}")
        profile_aliases[name] = {"code": next(iter(codes)),
                                 "request_units": sorted({row["requested_unit"] for row, _ in observed if row["requested_unit"]}),
                                 "evidence": "；".join(notes)}

    standalone = manual_by_row[64]
    exact_manual = sum(r["manual_catalog_status"] == "exact_unique" for r in records)
    identity_manual = sum(r.get("manual_identity_code") is not None for r in records)
    same_selected = sum(r["sku_comparison"] == "same" for r in records)
    same_matched = sum(r["sku_comparison"] == "same" and r["workflow_status"] == "matched" for r in records)
    result = {"purpose": "historical_reference_audit_not_independent_accuracy", "catalog_sha256": sha(CATALOG),
              "manual_sha256": sha(MANUAL), "chat_sha256": sha(CHAT), "workflow_manifest": scenario["manifest"],
              "alignment_rule": "C1-C57=M2-M58; C58 chat-only; C59-C63=M59-M63; M64 manual-only; C64 chat-only",
              "counts": {"chat_items": 64, "manual_detail_rows": 63, "aligned_pairs": 62,
                         "manual_name_covered_in_catalog": sum(bool(by_name[normalized(r["name"])]) for r in manual),
                         "manual_exact_name_spec_price_unique": sum(len([c for c in by_name[normalized(r["name"])]
                             if normalized(c["spec"]) == normalized(r["spec"]) and Decimal(c["price"]) == Decimal(r["price"])]) == 1 for r in manual),
                         "aligned_manual_exact_catalog_denominator": exact_manual,
                         "aligned_manual_name_spec_identity_denominator": identity_manual,
                         "workflow_matched_same_sku": same_matched,
                         "workflow_selected_same_sku_including_blocked": same_selected,
                         "manual_only_rows": 1, "chat_only_rows": 2},
              "rows": records, "manual_only": [{"manual": standalone, "reason": "聊天无蒜瓣来源，人工增项不可当成自动识别真值"}],
              "first_20_differences": [r for r in records if r["differences"]][:20],
              "limitations": ["人工订单不证明11月3日目录价格适用，也不证明实称/改量原因",
                              "有多个人工可选SKU时不强行定位；非精确目录行不进入SKU准确率分母",
                              "历史样例参与偏好学习后不能再充当独立前向准确率样本"]}
    pack_evidence = {"挂面": ("袋", "80001876", "500g", 4), "盐": ("袋", "80002987", "350g", 12),
                     "水煮肉片料": ("包", "80002082", "100g", 14), "太太乐鸡精": ("袋", "80001608", "1kg", 13),
                     "腐竹": ("袋", "80001868", "1kg", 21), "豆腐": ("盒", "80003399", "490g", 41),
                     "番茄沙司": ("袋", "80004512", "320g", 15),
                     "海天鲜生抽": ("瓶", "80001298", "500ml", 7),
                     "牛奶": ("箱", "80004048", "240ml*16袋", 60)}
    unit_rules = {}
    for name, (unit, code, spec, manual_row) in pack_evidence.items():
        row = next(r for r in records if r["parsed_name"] == name and r["manual"] and r["manual"]["row"] == manual_row)
        assert row["manual_identity_code"] == code and row["requested_unit"] == unit
        assert Decimal(row["requested_quantity"]) == Decimal(row["manual"]["quantity"])
        unit_rules[code] = {"price_unit": unit, "request_units": {unit: "1"}, "allow_fractional": False,
                            "evidence": f"学院路参考样本聊天{row['chat_line']}行“{row['raw_text']}”与人工表{manual_row}行"
                                        f"数量均为{row['requested_quantity']}{unit}；当前目录SKU {code}规格{spec}。"
                                        "仅本公司参考拟合使用，不证明未来配货实际数量。"}
        if code == "80001298":
            unit_rules[code]["ml_per_unit"] = "500"
    profile = {"schema_version": 1, "profile_id": "xueyuan-20261103-reference-fitted-v1",
               "company_id": "xueyuan", "catalog_version": sha(CATALOG),
               "evidence": {"kind": "user_provided_reference", "source": MANUAL.name, "sha256": sha(MANUAL)},
               "aliases": profile_aliases, "unit_rules": unit_rules}
    (EVAL / "xueyuan_regression.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (EVAL / "xueyuan.history-profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 学院路聊天与人工订单逐行差异审计", "", "本审计只使用人工表作有噪声历史参考，不作为独立准确率真值。",
             "", "|口径|数量|", "|---|---:|", f"|聊天项|64|", f"|人工商品行|63|", "|明确顺序对齐|62|",
             f"|人工表名称在当前目录出现|{result['counts']['manual_name_covered_in_catalog']}/63|",
             f"|人工表名称+规格+挂牌价唯一定位SKU|{result['counts']['manual_exact_name_spec_price_unique']}/63|",
             f"|对齐行可核目录SKU分母|{exact_manual}/62|",
             f"|对齐行名称+规格唯一SKU身份分母（允许旧价差）|{identity_manual}/62|",
             f"|旧流程已匹配且SKU与人工一致|{same_matched}/{identity_manual}|",
             f"|旧流程selected含数量阻塞且SKU一致|{same_selected}/{identity_manual}|", "",
             "顺序锚点：聊天1–57行对人工2–58行；聊天58行重复小白菜没有人工对应行；聊天59–63行对人工59–63行；人工64行蒜瓣为人工增项；聊天64行生猪手撩毛8个无人工对应行。",
             "", "数量与SKU分开评估：人工小葱等实称与需求数不同，不计为聊天数量解析错误。SKU身份分母按名称+规格唯一确定，价格差异单列；旧价严格一致分母也保留供核对。", "",
             "## 前20条差异", "", "|聊天位置|原文|人工行/商品|人工目录SKU|旧流程状态/SKU|差异|", "|---|---|---|---|---|---|"]
    for row in result["first_20_differences"]:
        human = row["manual"]
        lines.append("|{}|{}|{}|{}|{} / {}|{}|".format(row["chat_line"], row["raw_text"],
                      f"{human['row']} {human['name']}" if human else "无", row.get("manual_identity_code") or "未唯一定位",
                      row["workflow_status"], row["selected_code"] or "未选定", "；".join(row["differences"])))
    lines += ["", "## 回归输入结论", "", "用户提供人工参考样本并授权在此样本上优化回归。`xueyuan.history-profile.json` 按公司、目录SHA、原品名及原单位保存SKU偏好和证据，可供参考样本拟合回归；这不是独立前向准确率，也不代表未来业务默认已由用户确认。",
              "", "九条整数包装1:1关系只在本公司 reference-fitted 参考拟合模式使用，并附原文、人工行和目录规格；海天鲜生抽瓶装含量按目录500ml。番茄沙司同规格目录双价采用明确最高价15的SKU 80004512。不复制实际配送数量或旧价格。两条油人工旧价73.9不同于当前目录65.90，商品身份仍可对齐，计价只用当前目录。粉条3斤→人工2包与麻辣块1块→人工整包均不学成通用换算；目录外商品仍异常。", ""]
    (EVAL / "xueyuan_regression.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(result["counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
