#!/usr/bin/env python3
"""Read-only verification of the eight original order-mapping examples.

Run from any directory: python ordertcr/evaluation/verify_originals.py
Only evaluation/originals_verification.{json,md} are written.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from openpyxl import load_workbook


EVALUATION = Path(__file__).resolve().parent
ROOT = EVALUATION.parent
ORIGINAL = ROOT / "original"
BASELINE = ROOT / "业务样例核对.md"
EXPECTED_NAMES = {
    "食堂食材基准价价格表10.12-10.18.xlsx",
    "松北大道站11.3下单.txt",
    "星月站11.3下单.txt",
    "学院路站11.3下单.txt",
    "松北大道站11.3.xlsx",
    "星月站11.3.xlsx",
    "学院路站11.3.xlsx",
    "汶岂喜菜进账10.31.xlsx",
}
ORDER_HEADER = (
    "发货日期", "订单编号", "所属公司", "商品名称", "规格", "配货数量",
    "原价", "折扣率", "配货单价", "销售金额",
)
FINANCE_HEADERS = {
    "订单明细": ("收货日期", "订单号", "商户名", "销售额(不含税,运)"),
    "商品明细": (
        "收货日期", "订单号", "商户名", "商品名", "单价(基本单位)",
        "销售单位", "出库数(基本单位)", "销售额(含税)",
    ),
}
ORDER_EXPECTED = (
    ("松北大道站11.3.xlsx", "松北大道", 52, "OR_20261103000001", "松北大道消防救援站", "2652.75530", "2652.76", "2652.78", "2653.21"),
    ("星月站11.3.xlsx", "星月", 39, "OR_20261103000002", "星月街消防站", "2557.37074", "2557.37", "2557.40", "2557.65"),
    ("学院路站11.3.xlsx", "学院路", 63, "OR_20261103000003", "学院路消防救援", "3524.60317", "3524.60", "3524.62", "3524.92"),
)
FINANCE_EXPECTED = {
    ("2026-10-31", "PL129171441", "消防队星月街站"): Decimal("1137.47"),
    ("2026-10-31", "PL129168925", "消防队松北大道站"): Decimal("2395.66"),
    ("2026-10-31", "PL129167896", "消防队学院路站"): Decimal("1988.88"),
}
CENT = Decimal("0.01")


def decimal(value: object, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label}: 数值为空或类型不正确")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{label}: 非法数值 {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"{label}: 数值不是有限数")
    return result


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def fail(report: dict, code: str, location: str, detail: str) -> None:
    report["failures"].append({"code": code, "location": location, "detail": detail})


def assert_equal(report: dict, code: str, location: str, actual: object, expected: object) -> None:
    if actual != expected:
        fail(report, code, location, f"实际 {actual!r}；期望 {expected!r}")


def read_baseline_hashes(report: dict) -> dict[str, str]:
    text = BASELINE.read_text(encoding="utf-8")
    section = text.split("## 6. 原始文件指纹", 1)
    if len(section) != 2:
        fail(report, "baseline_hash_section", str(BASELINE), "找不到原始文件指纹章节")
        return {}
    hashes: dict[str, str] = {}
    for name, digest in re.findall(r"^\|\s*([^|]+?)\s*\|\s*([a-f0-9]{64})\s*\|", section[1], re.M):
        hashes[name.strip()] = digest
    assert_equal(report, "baseline_hash_names", str(BASELINE), set(hashes), EXPECTED_NAMES)
    return hashes


def verify_hashes(report: dict) -> None:
    expected = read_baseline_hashes(report)
    for name in sorted(EXPECTED_NAMES):
        path = ORIGINAL / name
        if not path.is_file():
            fail(report, "missing_source", str(path), "原件不存在")
            continue
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        actual = hasher.hexdigest()
        match = actual == expected.get(name)
        report["sources"].append({
            "file": name,
            "bytes": path.stat().st_size,
            "sha256": actual,
            "baseline_sha256": expected.get(name),
            "matches_baseline": match,
        })
        if not match:
            fail(report, "sha256_mismatch", name, "文件SHA-256与业务样例核对记录不一致")


def cells(ws, row: int, columns: int) -> tuple:
    return tuple(cell.value for cell in next(ws.iter_rows(min_row=row, max_row=row, min_col=1, max_col=columns)))


def verify_orders(report: dict) -> None:
    for name, sheet, count, number, company, total, final_cent, line_cent, unit_cent in ORDER_EXPECTED:
        path = ORIGINAL / name
        if not path.exists():
            continue
        values = load_workbook(path, read_only=True, data_only=True)
        formulas = load_workbook(path, read_only=True, data_only=False)
        try:
            assert_equal(report, "sheet_names", name, values.sheetnames, [sheet])
            ws, wf = values[sheet], formulas[sheet]
            last = count + 2
            assert_equal(report, "detail_count", sheet, ws.max_row - 2, count)
            assert_equal(report, "last_row", sheet, ws.max_row, last)
            header = cells(ws, 1, 10)
            wanted_header = list(ORDER_HEADER)
            if sheet == "星月":
                wanted_header[5] = "配送数量"  # 原件与目标统一字段的已记录差异
            assert_equal(report, "header", f"{sheet}!A1:J1", header, tuple(wanted_header))
            if sheet == "学院路":
                for row in ws.iter_rows(min_col=11, max_col=11):
                    if row[0].value is not None:
                        fail(report, "extra_business_column", f"{sheet}!K{row[0].row}", "K列应为空")

            rows = []
            prices, line_rounds, unit_rounds = [], [], []
            for row_num in range(2, last):
                loc = f"{sheet}!{row_num}"
                a, b, c, d, e, f, g, h, i, j = cells(ws, row_num, 10)
                before = len(report["failures"])
                assert_equal(report, "order_date", f"{loc}:A", a, datetime(2026, 11, 3))
                assert_equal(report, "order_number", f"{loc}:B", b, number)
                assert_equal(report, "company", f"{loc}:C", c, company)
                if not d or not e:
                    fail(report, "missing_item", loc, "商品名称或规格为空")
                try:
                    quantity = decimal(f, f"{loc}:F")
                    price = decimal(g, f"{loc}:G")
                    discount = decimal(h, f"{loc}:H")
                    actual_unit = decimal(i, f"{loc}:I")
                    actual_amount = decimal(j, f"{loc}:J")
                    assert_equal(report, "discount", f"{loc}:H", discount, Decimal("0.95"))
                    expected_unit = price * discount
                    expected_amount = expected_unit * quantity
                    assert_equal(report, "unit_price_calculation", f"{loc}:I", actual_unit, expected_unit)
                    assert_equal(report, "line_amount_calculation", f"{loc}:J", actual_amount, expected_amount)
                    prices.append(actual_amount)
                    line_rounds.append(cents(actual_amount))
                    unit_rounds.append(cents(cents(expected_unit) * quantity))
                except ValueError as exc:
                    fail(report, "invalid_number", loc, str(exc))
                rows.append({"row": row_num, "ok": len(report["failures"]) == before})

            computed_total = sum(prices, Decimal("0"))
            rounded_total = cents(computed_total)
            summed_rows = sum(line_rounds, Decimal("0"))
            unit_first = sum(unit_rounds, Decimal("0"))
            assert_equal(report, "exact_total", sheet, computed_total, Decimal(total))
            assert_equal(report, "rounded_total", sheet, rounded_total, Decimal(final_cent))
            assert_equal(report, "line_rounded_total", sheet, summed_rows, Decimal(line_cent))
            assert_equal(report, "unit_first_total", sheet, unit_first, Decimal(unit_cent))
            expected_formula = f"=SUM(J2:J{last - 1})"
            assert_equal(report, "total_formula", f"{sheet}!J{last}", wf.cell(last, 10).value, expected_formula)
            try:
                cached = decimal(ws.cell(last, 10).value, f"{sheet}!J{last}缓存")
                assert_equal(report, "total_cache", f"{sheet}!J{last}", cached, computed_total)
            except ValueError as exc:
                fail(report, "total_cache", f"{sheet}!J{last}", str(exc))
                cached = None
            report["standard_orders"].append({
                "file": name, "sheet": sheet, "detail_rows": len(rows),
                "header": list(header), "row_checks": rows,
                "exact_total": str(computed_total), "rounded_at_total": str(rounded_total),
                "sum_of_rounded_lines": str(summed_rows),
                "sum_after_unit_price_rounded": str(unit_first),
                "total_formula": wf.cell(last, 10).value,
                "cached_total": str(cached) if cached is not None else None,
            })
        finally:
            values.close()
            formulas.close()


def verify_finance(report: dict) -> None:
    path = ORIGINAL / "汶岂喜菜进账10.31.xlsx"
    if not path.exists():
        return
    values = load_workbook(path, read_only=True, data_only=True)
    formulas = load_workbook(path, read_only=True, data_only=False)
    try:
        assert_equal(report, "finance_sheets", path.name, values.sheetnames, ["订单明细", "商品明细"])
        detail = values["商品明细"]
        summary = values["订单明细"]
        assert_equal(report, "finance_detail_rows", "商品明细", detail.max_row - 1, 152)
        assert_equal(report, "finance_summary_rows", "订单明细", summary.max_row - 2, 3)
        assert_equal(report, "finance_detail_header", "商品明细!A1:H1", cells(detail, 1, 8), FINANCE_HEADERS["商品明细"])
        assert_equal(report, "finance_summary_header", "订单明细!A1:D1", cells(summary, 1, 4), FINANCE_HEADERS["订单明细"])

        totals = defaultdict(lambda: Decimal("0"))
        rows = []
        for row_num in range(2, detail.max_row + 1):
            loc = f"商品明细!{row_num}"
            date, number, merchant, item, price, unit, qty, amount = cells(detail, row_num, 8)
            before = len(report["failures"])
            key = (date, number, merchant)
            if key not in FINANCE_EXPECTED:
                fail(report, "finance_identity", loc, f"未知日期/订单号/商户组合：{key!r}")
            if not item or not unit:
                fail(report, "finance_item", loc, "商品或销售单位为空")
            try:
                expected = cents(decimal(price, f"{loc}:E") * decimal(qty, f"{loc}:G"))
                actual = decimal(amount, f"{loc}:H")
                assert_equal(report, "finance_line_amount", f"{loc}:H", actual, expected)
                totals[key] += actual
            except ValueError as exc:
                fail(report, "finance_invalid_number", loc, str(exc))
            rows.append({"row": row_num, "ok": len(report["failures"]) == before})

        assert_equal(report, "finance_groups", "商品明细", set(totals), set(FINANCE_EXPECTED))
        for key, expected in FINANCE_EXPECTED.items():
            assert_equal(report, "finance_group_total", str(key), totals.get(key), expected)
        summary_rows = []
        for row_num in range(2, 5):
            date, number, merchant, amount = cells(summary, row_num, 4)
            key = (date, number, merchant)
            if key not in FINANCE_EXPECTED:
                fail(report, "finance_summary_identity", f"订单明细!{row_num}", f"未知汇总键 {key!r}")
            try:
                actual = decimal(amount, f"订单明细!D{row_num}")
                assert_equal(report, "finance_summary_amount", f"订单明细!D{row_num}", actual, totals.get(key))
                summary_rows.append({"row": row_num, "date": date, "order_number": number, "merchant": merchant, "amount": str(actual)})
            except ValueError as exc:
                fail(report, "finance_summary_amount", f"订单明细!D{row_num}", str(exc))
        aggregate = sum(totals.values(), Decimal("0"))
        assert_equal(report, "finance_aggregate", "财务合计", aggregate, Decimal("5522.01"))
        assert_equal(report, "finance_total_formula", "订单明细!D5", formulas["订单明细"]["D5"].value, "=SUM(D2:D4)")
        try:
            cached = decimal(summary["D5"].value, "订单明细!D5缓存")
            assert_equal(report, "finance_total_cache", "订单明细!D5", cached, aggregate)
        except ValueError as exc:
            fail(report, "finance_total_cache", "订单明细!D5", str(exc))
            cached = None
        report["finance"] = {
            "file": path.name, "detail_rows": len(rows), "row_checks": rows,
            "summary_rows": summary_rows, "aggregate": str(aggregate),
            "total_formula": formulas["订单明细"]["D5"].value,
            "cached_total": str(cached) if cached is not None else None,
            "header_semantics_note": "商品明细H列标为含税，订单明细D列标为不含税、运；金额相等并不能解释税运口径。",
        }
    finally:
        values.close()
        formulas.close()


def render_markdown(report: dict) -> str:
    matched = sum(item["matches_baseline"] for item in report["sources"])
    order_rows = sum(item["detail_rows"] for item in report["standard_orders"])
    finance = report.get("finance") or {}
    lines = [
        "# 原始样例可重跑核验", "",
        "运行：`python ordertcr/evaluation/verify_originals.py`。脚本只读`original/`八份原件和`业务样例核对.md`，并更新本报告与同名JSON。", "",
        f"结果：**{'通过' if not report['failures'] else '未通过'}**；8份SHA匹配{matched}/8，人工订单明细{order_rows}/154行，财务商品明细{finance.get('detail_rows', 0)}/152行，失败项{len(report['failures'])}。", "",
        "## 原件指纹", "", "| 文件 | 与业务样例核对.md一致 | SHA-256 |", "|---|---|---|",
    ]
    for source in report["sources"]:
        lines.append(f"| {source['file']} | {'是' if source['matches_baseline'] else '否'} | `{source['sha256']}` |")
    lines += ["", "## 人工订单", "", "| 站点 | 逐行校验 | 精确J合计 | 合计最后舍入 | 逐行舍入后合计 | 先单价舍入后合计 | SUM缓存 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for item in report["standard_orders"]:
        passed = sum(row["ok"] for row in item["row_checks"])
        lines.append(f"| {item['sheet']} | {passed}/{item['detail_rows']} | {item['exact_total']} | {item['rounded_at_total']} | {item['sum_of_rounded_lines']} | {item['sum_after_unit_price_rounded']} | {item['cached_total']} |")
    lines += ["", "订单逐行验证G×H=I、I×F=J，核对H为0.95、日期/订单号/公司一致和J尾行SUM公式及缓存。星月F1原样为“配送数量”；学院路已用区域到K列但K列无业务字段。逐行通过状态及失败定位在JSON。", "", "## 财务10.31", ""]
    if finance:
        passed = sum(row["ok"] for row in finance["row_checks"])
        lines += [f"商品明细逐行以`ROUND_HALF_UP(E×G,2)`核对H：{passed}/{finance['detail_rows']}。", "", "| 订单号 | 商户 | 商品明细与订单明细合计 |", "|---|---|---:|"]
        for row in finance["summary_rows"]:
            lines.append(f"| {row['order_number']} | {row['merchant']} | {row['amount']} |")
        lines += ["", f"三站合计{finance['aggregate']}；`订单明细!D5`公式`{finance['total_formula']}`，缓存{finance['cached_total']}。", "", finance["header_semantics_note"]]
    lines += ["", "## 失败与边界", ""]
    if report["failures"]:
        for item in report["failures"]:
            lines.append(f"- `{item['code']}` {item['location']}：{item['detail']}")
    else:
        lines.append("- 本次读到的结构、逐行算式、缓存合计、样例统计与八份SHA均符合业务样例核对记录。")
    lines += ["- 人工订单含实称和商品替换，不能作为聊天拆单的逐行真值；10.31财务表也不证明11.3的实际配送或财务价格。", "- 只验证工作簿保存的数据及公式缓存，未在Excel重新计算、视觉检查或真实系统导入。", ""]
    return "\n".join(lines)


def atomic_write(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as out:
        temporary = Path(out.name)
        out.write(text)
    temporary.replace(path)


def main() -> int:
    report: dict = {
        "source_record": str(BASELINE.relative_to(ROOT)),
        "scope": "八原件只读；标准订单与财务逐行计算、缓存合计和SHA复核",
        "sources": [], "standard_orders": [], "finance": None, "failures": [],
    }
    try:
        verify_hashes(report)
        verify_orders(report)
        verify_finance(report)
    except (OSError, ValueError, KeyError, IndexError) as exc:
        fail(report, "verification_error", "verify_originals.py", str(exc))
    report["passed"] = not report["failures"]
    atomic_write(EVALUATION / "originals_verification.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    atomic_write(EVALUATION / "originals_verification.md", render_markdown(report))
    print(f"核验{'通过' if report['passed'] else '失败'}：人工订单{sum(x['detail_rows'] for x in report['standard_orders'])}行，财务{(report.get('finance') or {}).get('detail_rows', 0)}行，错误{len(report['failures'])}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
