"""Ten-column orders and two-sheet financial views with native Excel comments."""

import math
import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill

from .money import decimal_value, sum_amounts

ORDER_HEADERS = ["发货日期", "订单编号", "所属公司", "商品名称", "规格", "配货数量", "原价", "折扣率", "配货单价", "销售金额"]
FINANCE_HEADERS = ["收货日期", "订单号", "商户名", "商品名", "单价(基本单位)", "销售单位", "出库数(基本单位)", "销售额(含税)"]
RED = "FCE4D6"
YELLOW = "FFF2CC"


def _write(cell, value):
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"


def _number(value):
    if value is None:
        return None
    number = Decimal(str(value))
    digits = list(number.as_tuple().digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
    if not number.is_finite() or len(digits) > 15:
        raise ValueError("Excel数值超过15位有效数字精度，请核对金额后再导出")
    return number


def _validate_matched(row, *, finance=False):
    selected = row.get("selected")
    if not isinstance(selected, dict) or not all(selected.get(key) for key in ("code", "name", "spec")):
        raise ValueError(f"已匹配行缺少完整目录记录：{row.get('line_id')}")
    decimal_value(selected.get("price"), "目录原价", minimum=0)
    decimal_value(row.get("discount"), "折扣率", minimum=0)
    decimal_value(row.get("invoice_quantity"), "计价数量", positive=True)
    decimal_value(row.get("unit_price"), "销售单价", minimum=0)
    decimal_value(row.get("amount"), "订单金额", minimum=0)
    if not row.get("price_unit"):
        raise ValueError(f"已匹配行缺少计价单位：{row.get('line_id')}")
    if finance:
        decimal_value(row.get("finance_amount"), "财务金额", minimum=0)


def _comment(text):
    note = Comment(text, "User")
    note.width = 640
    note.height = min(1000, max(150, 20 * (text.count("\n") + math.ceil(len(text) / 55))))
    return note


def _row_comment(sheet, row, text, primary=4):
    """Keep every candidate; long notes continue in adjacent native comments."""
    columns = [primary] + [c for c in [4, 5, 6, 7, 8, 9, 10, 1, 2, 3] if c != primary and c <= sheet.max_column]
    chunks = [text[i:i + 29000] for i in range(0, len(text), 29000)] or [""]
    if len(chunks) > len(columns):
        raise ValueError("完整候选批注超过本行Excel容量，请缩小合理候选范围后重试；未截断导出")
    for index, chunk in enumerate(chunks):
        suffix = f"\n批注续页 {index + 1}/{len(chunks)}，依次查看本行D/E/F/G/H/I/J/A/B/C批注。" if len(chunks) > 1 else ""
        cell = sheet.cell(row, columns[index])
        existing = cell.comment.text + "\n" if cell.comment else ""
        if len(existing + chunk + suffix) > 32767:
            raise ValueError("单元格原生批注容量不足，未发布截断的候选")
        cell.comment = _comment(existing + chunk + suffix)


def _issue_text(row):
    source = f"{row.get('source_name', '结构化输入')}:{row.get('source_line', '?')}，line_id={row.get('line_id', '?')}"
    lines = [f"原文：{row.get('raw_text', row.get('name', ''))}", f"来源：{source}", f"状态：{row.get('status')}"]
    for issue in row.get("issues", []):
        lines.append(f"异常[{issue['code']}]：{issue['message']}")
    if row.get("notes"):
        lines.append(f"加工/补充要求：{row['notes']}")
    candidates = row.get("candidates", [])
    if candidates:
        lines.append(f"合理候选共{len(candidates)}项（建议候选尚未确认，不能直接当成已选SKU）：")
        for index, item in enumerate(candidates, 1):
            lines.append(f"{index}. 材料编号={item.get('code')}；商品名称={item.get('name')}；规格={item.get('spec')}；挂牌价={item.get('price')}；目录={item.get('sheet')}!第{item.get('row')}行"
                         + (f"；差异/理由={item['match_reason']}" if item.get("match_reason") else ""))
    else:
        lines.append("未找到满足当前要求的有效目录条目；请人工核定商品/数量/单位，或补充有效目录。")
    lines.append("未确认项按原文保留，未知价格/金额为空，不表示免费或已结算。")
    return "\n".join(lines)


def _catalog_text(catalog):
    return (f"本批基准价版本：{catalog.get('version_id')}\n来源：{catalog.get('source_name')}\n"
            f"接收时间：{catalog.get('received_at')}\nSHA-256：{catalog.get('sha256')}\n"
            "异常原文行的规格/数量可能是原始单位，须先处理颜色与批注再用于结算。")


def _style(sheet, headers, widths):
    for col, (header, width) in enumerate(zip(headers, widths), 1):
        cell = sheet.cell(1, col)
        _write(cell, header)
        cell.font = Font(name="宋体", size=11, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="284B63")
        cell.alignment = Alignment(vertical="center", horizontal="center", wrap_text=True)
        sheet.column_dimensions[cell.column_letter].width = width
    sheet.row_dimensions[1].height = 34
    sheet.freeze_panes = "D2"
    sheet.sheet_view.showGridLines = True
    sheet.print_title_rows = "1:1"
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_margins.left = 0.25
    sheet.page_margins.right = 0.25
    sheet.page_margins.top = 0.5
    sheet.page_margins.bottom = 0.5


def _finish_rows(sheet):
    for cells in sheet.iter_rows(min_row=2):
        for cell in cells:
            cell.font = Font(name="宋体", size=11)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        label = str(cells[3].value or "") if len(cells) > 3 else ""
        sheet.row_dimensions[cells[0].row].height = max(24, 18 * math.ceil(max(1, len(label)) / 22))


def _save(book, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".workbook-", suffix=".xlsx", dir=path.parent)
    os.close(descriptor)
    try:
        book.save(temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_standard(path, rows, *, company_name, shipping_date, order_number, catalog, mode="order", pending=False):
    rows = list(rows)
    for row in rows:
        if row.get("status") == "matched":
            _validate_matched(row)
    book = Workbook()
    sheet = book.active
    sheet.title = "下单需求_待复核" if mode == "order" else ("配货结算_待复核" if pending else "配货结算")
    _style(sheet, ORDER_HEADERS, [14, 25, 27, 46, 20, 14, 12, 12, 15, 17])
    sheet["D1"].comment = _comment(_catalog_text(catalog))
    for index, row in enumerate(rows, 2):
        selected = row.get("selected") if row.get("status") == "matched" else None
        qty = row.get("invoice_quantity") if selected else row.get("quantity")
        values = [date.fromisoformat(shipping_date), order_number, company_name,
                  selected["name"] if selected else row.get("name", row.get("raw_text")),
                  selected["spec"] if selected else row.get("unit") or row.get("raw_spec"),
                  _number(qty), _number(selected["price"]) if selected else None,
                  _number(row.get("discount")), _number(row.get("unit_price")) if selected else None,
                  _number(row.get("amount")) if selected else None]
        for col, value in enumerate(values, 1):
            _write(sheet.cell(index, col), value)
        sheet.cell(index, 1).number_format = "yyyy/mm/dd"
        sheet.cell(index, 2).number_format = "@"
        for col in [7, 9, 10]:
            sheet.cell(index, col).number_format = "0.00"
        if row.get("status") != "matched":
            color = RED if row.get("status") == "unmapped" else YELLOW
            for col in range(1, 11):
                sheet.cell(index, col).fill = PatternFill("solid", fgColor=color)
            _row_comment(sheet, index, _issue_text(row))
        else:
            note = (f"原文：{row.get('raw_text')}\n来源：{row.get('source_name', '结构化输入')}:{row.get('source_line', '?')}\n"
                    f"目录编号：{selected['code']}\n计价单位：{row.get('price_unit')}\n"
                    f"数量依据：{row.get('quantity_basis', '下单需求')}\n加工备注：{row.get('notes') or '无'}")
            _row_comment(sheet, index, note)
        if not order_number and not sheet.cell(index, 2).comment:
            sheet.cell(index, 2).comment = _comment("复核/需求文件尚未分配正式订单流水；处理完成后由已确认编号策略分配。")
    footer = len(rows) + 2
    missing = [row.get("line_id", "?") for row in rows if row.get("status") != "matched"]
    _write(sheet.cell(footer, 9), "已确认金额小计" if missing else "金额合计")
    sheet.cell(footer, 10).value = _number(sum_amounts(row["amount"] for row in rows if row.get("status") == "matched"))
    sheet.cell(footer, 10).number_format = "0.00"
    if missing:
        sheet.cell(footer, 10).comment = _comment(f"未计金额的异常行共{len(missing)}条：{', '.join(missing)}。本值仅为已确认金额小计，不是完整应付总额。")
    _finish_rows(sheet)
    _save(book, path)


def export_finance(path, orders, *, catalog, draft=True, reasons=None):
    orders = list(orders)
    for order in orders:
        if not draft and (not order.get("receipt_date") or not order.get("order_number")):
            raise ValueError("正式财务导出需要明确收货日期和订单编号")
        for row in order["rows"]:
            if not draft and row.get("status") != "matched":
                raise ValueError(f"正式财务导出包含未解决行：{row.get('line_id')}")
            if row.get("status") == "matched" and (not draft or row.get("finance_amount") is not None):
                _validate_matched(row, finance=True)
    book = Workbook()
    summary = book.active
    summary.title = "订单明细"
    detail = book.create_sheet("商品明细")
    _style(summary, ["收货日期", "订单号", "商户名", "销售额(不含税,运)"], [16, 27, 31, 29])
    _style(detail, FINANCE_HEADERS, [16, 27, 31, 46, 24, 18, 24, 22])
    status = "预估/待确认草稿，不能当成已入账或实际出库凭证。" if draft else "依据已明确的实绩、销售价及税运规则生成。"
    note = status + "\n" + "\n".join(reasons or []) + "\n" + _catalog_text(catalog)
    summary["A1"].comment = _comment(note)
    detail["A1"].comment = _comment(note)
    summary["D1"].comment = _comment("沿用参照模板表头；含税/不含税与运费含义须按本批已确认规则解释。" + ("当前未完成业务确认。" if draft else ""))
    detail_row = 2
    all_values = []
    for index, order in enumerate(orders, 2):
        values = [row.get("finance_amount") if row.get("status") == "matched" else None for row in order["rows"]]
        total = sum_amounts(values)
        all_values.extend(values)
        receipt = order.get("receipt_date")
        for col, value in enumerate([date.fromisoformat(receipt) if receipt else None, order.get("order_number"),
                                     order.get("finance_name") or order["company_name"], _number(total)], 1):
            _write(summary.cell(index, col), value)
        summary.cell(index, 1).number_format = "yyyy/mm/dd"
        summary.cell(index, 4).number_format = "0.00"
        if draft or any(v is None for v in values):
            summary.cell(index, 4).comment = _comment("预估/已确认金额小计；存在未确认业务前提或未计金额的异常行，不是完整记账总额。")
        for row in order["rows"]:
            matched = row.get("status") == "matched"
            selected = row.get("selected") if matched else None
            row_values = [date.fromisoformat(receipt) if receipt else None, order.get("order_number"),
                          order.get("finance_name") or order["company_name"],
                          selected["name"] if selected else row.get("name", row.get("raw_text")),
                          _number(row.get("unit_price")) if matched else None,
                          row.get("price_unit") if matched else row.get("unit"),
                          _number(row.get("invoice_quantity") if matched else row.get("quantity")),
                          _number(row.get("finance_amount")) if matched else None]
            for col, value in enumerate(row_values, 1):
                _write(detail.cell(detail_row, col), value)
            detail.cell(detail_row, 1).number_format = "yyyy/mm/dd"
            detail.cell(detail_row, 5).number_format = "0.00"
            detail.cell(detail_row, 8).number_format = "0.00"
            if not matched:
                for col in range(1, 9):
                    detail.cell(detail_row, col).fill = PatternFill("solid", fgColor=RED if row.get("status") == "unmapped" else YELLOW)
                _row_comment(detail, detail_row, _issue_text(row))
            elif row.get("finance_amount") is None:
                for col in range(1, 9):
                    detail.cell(detail_row, col).fill = PatternFill("solid", fgColor=YELLOW)
                detail.cell(detail_row, 4).comment = _comment("财务金额或计价前提尚未确认；本行未计入金额小计。\n" + str(row.get("raw_text")))
            elif draft:
                detail.cell(detail_row, 4).comment = _comment("预估/待配送；本行数量、日期及价格尚不能视为已入账实绩。\n" + str(row.get("raw_text")))
            detail_row += 1
    footer = len(orders) + 2
    _write(summary.cell(footer, 3), "预估/已确认金额小计" if draft or any(v is None for v in all_values) else "金额合计")
    summary.cell(footer, 4).value = _number(sum_amounts(all_values))
    summary.cell(footer, 4).number_format = "0.00"
    _finish_rows(summary)
    _finish_rows(detail)
    _save(book, path)
