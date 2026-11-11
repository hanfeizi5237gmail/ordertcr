"""Fresh v3 XLSX readback, candidate/comment/color checks, and reconciliation."""

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import time

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
EVALUATION = ROOT / "evaluation"
CATALOG_PATH = ROOT / "original" / "食堂食材基准价价格表10.12-10.18.xlsx"
YELLOW = "FFF2CC"
RED = "FCE4D6"


def dec(value):
    return None if value is None else Decimal(str(value))


def same_dec(left, right):
    return dec(left) == dec(right)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rgb(cell):
    color = cell.fill.fgColor
    if color.type != "rgb" or not color.rgb:
        return None
    value = color.rgb.upper()
    if value in {"00000000", "000000"}:
        return None
    return value[-6:]


def row_fill(sheet, row_number, width):
    return {rgb(sheet.cell(row_number, column)) for column in range(1, width + 1)}


def comments(sheet, row_number, width):
    return "\n".join(
        cell.comment.text
        for column in range(1, width + 1)
        if (cell := sheet.cell(row_number, column)).comment
    )


def catalog_records():
    workbook = load_workbook(CATALOG_PATH, data_only=True, read_only=True)
    sheet = workbook.active
    records = {}
    for row_number, values in enumerate(sheet.iter_rows(values_only=True), 1):
        values = list(values)
        if row_number == 1 or len(values) < 5 or values[1] is None:
            continue
        code = str(values[1])
        record = {
            "category": str(values[0] or ""),
            "code": code,
            "name": str(values[2] or ""),
            "price": str(values[3]),
            "spec": str(values[4] or ""),
            "row": row_number,
            "sheet": sheet.title,
        }
        records.setdefault(code, []).append(record)
    return records


def assert_catalog_record(item, records, context):
    code = str(item.get("code"))
    candidates = records.get(code, [])
    assert candidates, f"{context}: catalog code missing: {code}"
    assert any(
        record["name"] == str(item.get("name"))
        and record["spec"].strip() == str(item.get("spec")).strip()
        and same_dec(record["price"], item.get("price"))
        for record in candidates
    ), f"{context}: catalog fields differ for {code}"


def expected_standard_values(row):
    product_known = row.get("product_status") == "matched" or row.get("status") == "matched"
    selected = row.get("selected") if product_known else None
    quantity_known = row.get("quantity_status") == "matched" or row.get("status") == "matched"
    return {
        "name": selected["name"] if selected else row.get("name", row.get("raw_text")),
        "spec": selected["spec"] if selected else row.get("unit") or row.get("raw_spec"),
        "quantity": row.get("invoice_quantity") if selected and quantity_known else row.get("quantity"),
        "price": selected.get("price") if selected else None,
        "discount": row.get("discount"),
        "unit_price": row.get("unit_price") if selected else None,
        "amount": row.get("amount") if row.get("status") == "matched" else None,
    }


def expected_finance_values(order, row):
    values = expected_standard_values(row)
    matched = row.get("status") == "matched"
    return {
        "merchant": order.get("finance_name") or order["company_name"],
        "name": values["name"],
        "unit_price": values["unit_price"] if matched else None,
        "unit": row.get("price_unit") if matched else row.get("unit"),
        "quantity": row.get("invoice_quantity") if matched else row.get("quantity"),
        "amount": row.get("finance_amount") if matched else None,
    }


def verify_comments_and_candidates(sheet, row_number, row):
    text = comments(sheet, row_number, 10)
    assert row["raw_text"] in text, f"row {row['line_id']}: raw text missing from comment"
    candidates = row.get("candidates", [])
    if row.get("status") != "matched":
        if candidates:
            assert f"合理候选共{len(candidates)}项" in text, f"{row['line_id']}: candidate count missing"
            for index, candidate in enumerate(candidates, 1):
                line = (
                    f"{index}. 材料编号={candidate.get('code')}；商品名称={candidate.get('name')}；"
                    f"规格={candidate.get('spec')}；挂牌价={candidate.get('price')}；"
                    f"目录={candidate.get('sheet')}!第{candidate.get('row')}行"
                )
                assert line in text, f"{row['line_id']}: candidate {index} incomplete"
        else:
            assert "未找到满足当前要求的有效目录条目" in text, f"{row['line_id']}: no-candidate note missing"
    if row.get("product_status") == "matched" and row.get("quantity_status") != "matched":
        assert "原始数量" in text, f"{row['line_id']}: raw quantity note missing"
        assert "J列留空" in text, f"{row['line_id']}: blank amount guard missing"


def verify_standard(sheet, manifest, records):
    rows = manifest["order"]["rows"]
    assert sheet.max_row >= len(rows) + 2
    checks = {"rows": len(rows), "comments": 0, "candidate_sets": 0, "blank_amounts": 0, "colors": 0}
    for row_number, row in enumerate(rows, 2):
        expected = expected_standard_values(row)
        assert sheet.cell(row_number, 4).value == expected["name"], row["line_id"]
        assert sheet.cell(row_number, 5).value == expected["spec"], row["line_id"]
        assert same_dec(sheet.cell(row_number, 6).value, expected["quantity"]), row["line_id"]
        assert same_dec(sheet.cell(row_number, 7).value, expected["price"]), row["line_id"]
        assert same_dec(sheet.cell(row_number, 8).value, expected["discount"]), row["line_id"]
        assert same_dec(sheet.cell(row_number, 9).value, expected["unit_price"]), row["line_id"]
        assert same_dec(sheet.cell(row_number, 10).value, expected["amount"]), row["line_id"]
        if row.get("selected"):
            assert_catalog_record(row["selected"], records, row["line_id"])
        for candidate in row.get("candidates", []):
            assert_catalog_record(candidate, records, f"{row['line_id']} candidate")
        verify_comments_and_candidates(sheet, row_number, row)
        checks["comments"] += 1
        checks["candidate_sets"] += bool(row.get("candidates"))
        if row.get("amount") is None:
            assert sheet.cell(row_number, 10).value is None, row["line_id"]
            checks["blank_amounts"] += 1
        expected_color = None if row.get("status") == "matched" else (RED if row.get("status") == "unmapped" else YELLOW)
        actual_colors = row_fill(sheet, row_number, 10)
        assert actual_colors == {expected_color}, f"{row['line_id']}: colors {actual_colors} != {expected_color}"
        checks["colors"] += 1
    total = sum((dec(row.get("amount")) or Decimal(0)) for row in rows)
    assert same_dec(total, manifest["totals"]["standard"])
    footer = len(rows) + 2
    assert same_dec(sheet.cell(footer, 10).value, total)
    checks["standard_total"] = str(total)
    return checks


def verify_finance(order, manifest, finance_sheet):
    rows = manifest["order"]["rows"]
    assert finance_sheet.max_row >= len(rows) + 1
    total = Decimal(0)
    for row_number, row in enumerate(rows, 2):
        expected = expected_finance_values(order, row)
        assert finance_sheet.cell(row_number, 3).value == expected["merchant"], row["line_id"]
        assert finance_sheet.cell(row_number, 4).value == expected["name"], row["line_id"]
        assert same_dec(finance_sheet.cell(row_number, 5).value, expected["unit_price"]), row["line_id"]
        assert finance_sheet.cell(row_number, 6).value == expected["unit"], row["line_id"]
        assert same_dec(finance_sheet.cell(row_number, 7).value, expected["quantity"]), row["line_id"]
        assert same_dec(finance_sheet.cell(row_number, 8).value, expected["amount"]), row["line_id"]
        if row.get("status") == "matched":
            total += dec(row.get("finance_amount")) or Decimal(0)
        if row.get("amount") is None:
            assert finance_sheet.cell(row_number, 8).value is None, row["line_id"]
        expected_color = None
        if row.get("status") != "matched":
            expected_color = RED if row.get("status") == "unmapped" else YELLOW
        elif row.get("finance_amount") is None:
            expected_color = YELLOW
        actual_colors = row_fill(finance_sheet, row_number, 8)
        assert actual_colors == {expected_color}, f"{row['line_id']}: finance colors {actual_colors} != {expected_color}"
    assert same_dec(total, manifest["totals"]["finance"])
    summary = finance_sheet.parent["订单明细"]
    assert same_dec(summary.cell(2, 4).value, total)
    assert same_dec(summary.cell(3, 4).value, total)
    assert same_dec(summary.cell(3, 4).value, manifest["totals"]["finance"])
    return {"rows": len(rows), "finance_total": str(total), "blank_amounts": sum(row.get("amount") is None for row in rows)}


def verify_artifact_shas(result, manifests):
    verified = 0
    for output, manifest in zip(result["outputs"], manifests):
        for key, artifact in manifest["files"].items():
            path = Path(artifact["path"])
            assert path.exists(), str(path)
            digest = sha256(path)
            assert digest == artifact["sha256"], f"manifest {key} SHA mismatch"
            assert digest == output["files"][key]["sha256"], f"v3_accuracy {key} SHA mismatch"
            verified += 1
        assert Path(manifest["manifest_path"]) == Path(output["manifest_path"])
    merged = result["merged_finance"]["files"]["finance"]
    assert sha256(merged["path"]) == merged["sha256"]
    return verified + 1


def render(path, folder):
    folder.mkdir(parents=True, exist_ok=True)
    started = time.time()
    result = subprocess.run(
        ["/usr/bin/qlmanage", "-t", "-s", "2200", "-o", str(folder), str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    images = sorted(folder.glob("*.png"))
    assert images, f"qlmanage produced no image for {path}"
    assert all(image.stat().st_mtime >= started - 2 for image in images), f"stale image for {path}"
    return [str(image) for image in images]


def main():
    result = json.loads((EVALUATION / "v3_accuracy.json").read_text(encoding="utf-8"))
    assert result.get("passed") is True
    assert result.get("original_hashes_unchanged") is True
    records = catalog_records()
    catalog_digest = sha256(CATALOG_PATH)
    assert result["merged_finance"]["catalog"]["sha256"] == catalog_digest
    manifests = []
    station_reports = []
    previews = []
    expected_merged_rows = []
    for output in result["outputs"]:
        manifest_path = Path(output["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifests.append(manifest)
        assert manifest["row_count"] == output["row_count"]
        assert manifest["catalog"]["sha256"] == catalog_digest
        standard_book = load_workbook(output["files"]["standard"]["path"], data_only=False)
        finance_book = load_workbook(output["files"]["finance"]["path"], data_only=False)
        standard_checks = verify_standard(standard_book.active, manifest, records)
        finance_checks = verify_finance(manifest["order"], manifest, finance_book["商品明细"])
        expected_merged_rows.extend(
            [expected_finance_values(manifest["order"], row) for row in manifest["order"]["rows"]]
        )
        station_reports.append({
            "batch_id": output["batch_id"],
            "row_count": manifest["row_count"],
            "standard": standard_checks,
            "finance": finance_checks,
            "manifest_sha256": sha256(manifest_path),
        })
        folder = EVALUATION / "rendered-v3" / output["batch_id"]
        previews.append({"batch_id": output["batch_id"], "kind": "standard", "images": render(output["files"]["standard"]["path"], folder)})

    merged_path = Path(result["merged_finance"]["files"]["finance"]["path"])
    merged_book = load_workbook(merged_path, data_only=False)
    merged_summary = merged_book["订单明细"]
    merged_detail = merged_book["商品明细"]
    assert merged_detail.max_row == len(expected_merged_rows) + 1
    assert result["merged_finance"]["row_count"] == len(expected_merged_rows)
    assert result["merged_finance"]["order_count"] == len(manifests) == 3
    assert result["merged_finance"]["source_manifests"] == [output["manifest_path"] for output in result["outputs"]]
    for summary_row, output, manifest in zip(range(2, 5), result["outputs"], manifests):
        assert merged_summary.cell(summary_row, 3).value == manifest["order"]["finance_name"]
        assert same_dec(merged_summary.cell(summary_row, 4).value, manifest["totals"]["finance"])
    merged_total = sum((dec(row["amount"]) or Decimal(0)) for row in expected_merged_rows)
    assert same_dec(merged_summary.cell(5, 4).value, merged_total)
    assert same_dec(merged_total, result["merged_finance"]["total"])
    merged_source_rows = [row for manifest in manifests for row in manifest["order"]["rows"]]
    for row_number, (expected, source_row) in enumerate(zip(expected_merged_rows, merged_source_rows), 2):
        assert merged_detail.cell(row_number, 3).value == expected["merchant"]
        assert merged_detail.cell(row_number, 4).value == expected["name"]
        assert same_dec(merged_detail.cell(row_number, 5).value, expected["unit_price"])
        assert merged_detail.cell(row_number, 6).value == expected["unit"]
        assert same_dec(merged_detail.cell(row_number, 7).value, expected["quantity"])
        assert same_dec(merged_detail.cell(row_number, 8).value, expected["amount"])
        expected_color = None
        if source_row["status"] != "matched":
            expected_color = RED if source_row["status"] == "unmapped" else YELLOW
        elif source_row.get("finance_amount") is None:
            expected_color = YELLOW
        assert row_fill(merged_detail, row_number, 8) == {expected_color}, source_row["line_id"]
    previews.append({
        "batch_id": result["merged_finance"].get("batch_id", "merged-finance"),
        "kind": "merged_finance",
        "images": render(merged_path, EVALUATION / "rendered-v3" / "merged-finance"),
    })
    report = {
        "passed": True,
        "evaluation_type": result["evaluation_type"],
        "evaluation_sha256": sha256(EVALUATION / "v3_accuracy.json"),
        "xlsx_rows_verified": sum(report["row_count"] for report in station_reports),
        "station_reports": station_reports,
        "merged_finance": {
            "row_count": len(expected_merged_rows),
            "detail_rows_verified": len(expected_merged_rows),
            "summary_rows_verified": 3,
            "total": str(merged_total),
            "source_manifests": result["merged_finance"]["source_manifests"],
        },
        "artifact_sha_verified": verify_artifact_shas(result, manifests),
        "catalog_sha256": catalog_digest,
        "native_comments_verified": True,
        "candidate_sets_verified": sum(report["standard"]["candidate_sets"] for report in station_reports),
        "exception_colors_verified": sum(report["standard"]["colors"] for report in station_reports),
        "previews": previews,
        "limitations": [
            "缩略图用于检查表头、商品名、颜色、空金额和小计；原生批注由openpyxl逐项复读，缩略图不显示批注浮窗。",
            "未执行真实宿主加载、真实订单系统导入、实际配送或财务入账。",
            "该回归使用历史参考样本，不代表未见订单准确率。",
        ],
    }
    (EVALUATION / "v3_delivery_verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
