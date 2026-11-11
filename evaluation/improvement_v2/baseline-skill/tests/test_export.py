import importlib
import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
TMP = Path(__file__).resolve().parents[3] / "evaluation" / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        try:
            self.api = importlib.import_module("order_mapping_core.export")
        except ModuleNotFoundError as exc:
            self.fail(f"Excel导出契约尚未实现: {exc}")
        self.catalog = {"version_id": "v1", "sha256": "abc", "source_name": "price.xlsx", "received_at": "2026-11-07"}

    def rows(self):
        return [
            {"line_id": "a", "raw_text": "青菜2斤", "name": "青菜", "quantity": "2", "unit": "斤",
             "status": "matched", "selected": {"code": "1", "name": "青菜", "spec": "500g", "price": "1.5"},
             "invoice_quantity": "2", "price_unit": "斤", "discount": "0.95", "unit_price": "1.425",
             "amount": "2.85", "finance_amount": "2.85", "issues": [], "candidates": []},
            {"line_id": "b", "raw_text": "鲤鱼3条收拾好", "name": "鲤鱼", "quantity": "3", "unit": "条",
             "status": "ambiguous", "selected": None, "discount": "0.95", "invoice_quantity": None,
             "unit_price": None, "amount": None, "finance_amount": None,
             "issues": [{"code": "MULTIPLE", "message": "品种不明确，条数不能猜重量"}],
             "candidates": [{"code": "2", "name": "鲜活鲤鱼", "spec": "500g", "price": "10", "sheet": "Sheet0", "row": 7},
                            {"code": "3", "name": "江鲤鱼", "spec": "500g", "price": "15", "sheet": "Sheet0", "row": 8}]},
        ]

    def test_unknown_row_keeps_raw_units_all_candidates_and_blank_money(self):
        path = self.root / "order.xlsx"
        self.api.export_standard(path, self.rows(), company_name="测试公司", shipping_date="2026-11-03",
                                 order_number=None, catalog=self.catalog, mode="order", pending=True)
        sheet = load_workbook(path).active
        self.assertEqual(sheet.max_column, 10)
        self.assertEqual([sheet.cell(3, c).value for c in [4, 5, 6]], ["鲤鱼", "条", 3])
        self.assertEqual([sheet.cell(3, c).value for c in [7, 9, 10]], [None, None, None])
        self.assertEqual(sheet["D3"].fill.fgColor.rgb[-6:], "FFF2CC")
        note = sheet["D3"].comment.text
        for text in ["鲤鱼3条收拾好", "鲜活鲤鱼", "江鲤鱼", "500g", "10", "15", "Sheet0", "7", "8"]:
            self.assertIn(text, note)
        self.assertEqual(sheet["I4"].value, "已确认金额小计")
        self.assertEqual(sheet["J4"].value, 2.85)
        self.assertIn("未计金额", sheet["J4"].comment.text)
        self.assertIn("price.xlsx", sheet["D1"].comment.text)
        self.assertEqual(sheet["A2"].value.year, 2026)

    def test_no_match_is_red_and_untrusted_text_is_literal(self):
        row = self.rows()[1]
        row.update(name="=1+1", raw_text="=1+1", status="unmapped", candidates=[])
        path = self.root / "literal.xlsx"
        self.api.export_standard(path, [row], company_name="测试公司", shipping_date="2026-11-03",
                                 order_number=None, catalog=self.catalog, mode="order", pending=True)
        sheet = load_workbook(path).active
        self.assertEqual(sheet["D2"].value, "=1+1")
        self.assertEqual(sheet["D2"].data_type, "s")
        self.assertEqual(sheet["D2"].fill.fgColor.rgb[-6:], "FCE4D6")

    def test_large_candidate_set_is_not_silently_truncated(self):
        row = self.rows()[1]
        row["candidates"] = [{"code": str(i), "name": f"候选{i}" + "长商品名" * 35, "spec": "500g", "price": "1", "sheet": "目录", "row": i}
                             for i in range(240)]
        path = self.root / "many.xlsx"
        self.api.export_standard(path, [row], company_name="测试公司", shipping_date="2026-11-03",
                                 order_number=None, catalog=self.catalog, mode="order", pending=True)
        sheet = load_workbook(path).active
        comments = "".join(sheet.cell(2, c).comment.text for c in [4, 5, 6, 7, 8, 9, 10, 1, 2, 3] if sheet.cell(2, c).comment)
        self.assertIn("候选239", comments)

    def test_finance_unknown_amount_is_not_a_zero_or_complete_total(self):
        path = self.root / "finance.xlsx"
        order = {"company_name": "测试公司", "finance_name": "测试商户", "shipping_date": "2026-11-03",
                 "receipt_date": None, "order_number": None, "rows": self.rows()}
        self.api.export_finance(path, [order], catalog=self.catalog, draft=True, reasons=["收货日期与税运未确认"])
        book = load_workbook(path)
        self.assertEqual(book.sheetnames, ["订单明细", "商品明细"])
        self.assertIsNone(book["商品明细"]["H3"].value)
        self.assertEqual(book["商品明细"]["D3"].value, "鲤鱼")
        self.assertEqual(book["订单明细"]["D2"].value, 2.85)
        self.assertIn("小计", book["订单明细"]["D2"].comment.text)
        self.assertIn("预估", book["商品明细"]["A1"].comment.text)

    def test_incomplete_matched_standard_row_is_rejected_before_publication(self):
        row = self.rows()[0]
        row["invoice_quantity"] = None
        row["amount"] = None
        path = self.root / "incomplete.xlsx"
        with self.assertRaises(ValueError):
            self.api.export_standard(path, [row], company_name="测试公司", shipping_date="2026-11-03",
                                     order_number="OR_20261103000001", catalog=self.catalog, mode="settlement")
        self.assertFalse(path.exists())

    def test_matched_standard_row_rejects_invalid_amount_or_quantity(self):
        for field, value in [("invoice_quantity", "0"), ("unit_price", "NaN"),
                             ("amount", "-1"), ("discount", "Infinity")]:
            with self.subTest(field=field):
                row = self.rows()[0]
                row[field] = value
                path = self.root / f"invalid-{field}.xlsx"
                with self.assertRaises(ValueError):
                    self.api.export_standard(path, [row], company_name="测试公司", shipping_date="2026-11-03",
                                             order_number=None, catalog=self.catalog)
                self.assertFalse(path.exists())

    def test_excel_precision_limit_rejects_unrepresentable_amount(self):
        row = self.rows()[0]
        row["amount"] = "1234567890123456"
        path = self.root / "precision.xlsx"
        with self.assertRaisesRegex(ValueError, "Excel"):
            self.api.export_standard(path, [row], company_name="测试公司", shipping_date="2026-11-03",
                                     order_number=None, catalog=self.catalog)
        self.assertFalse(path.exists())

    def test_unresolved_stale_amounts_never_enter_export_totals(self):
        row = self.rows()[1]
        row["amount"] = "900"
        row["finance_amount"] = "800"
        standard = self.root / "order.xlsx"
        finance = self.root / "finance.xlsx"
        self.api.export_standard(standard, [row], company_name="测试公司", shipping_date="2026-11-03",
                                 order_number=None, catalog=self.catalog, pending=True)
        self.api.export_finance(finance, [{"company_name": "测试公司", "receipt_date": None,
                                           "order_number": None, "rows": [row]}], catalog=self.catalog, draft=True)
        sheet = load_workbook(standard).active
        self.assertIsNone(sheet["J2"].value)
        self.assertEqual(sheet["I3"].value, "已确认金额小计")
        self.assertEqual(sheet["J3"].value, 0)
        book = load_workbook(finance)
        self.assertIsNone(book["商品明细"]["H2"].value)
        self.assertEqual(book["订单明细"]["D2"].value, 0)

    def test_formal_finance_rejects_missing_delivery_and_unresolved_rows(self):
        path = self.root / "formal.xlsx"
        complete = self.rows()[0]
        order = {"company_name": "测试公司", "receipt_date": None,
                 "order_number": "OR_20261103000001", "rows": [complete]}
        with self.assertRaises(ValueError):
            self.api.export_finance(path, [order], catalog=self.catalog, draft=False)
        self.assertFalse(path.exists())
        order["receipt_date"] = "2026-11-04"
        order["rows"] = [complete, self.rows()[1]]
        with self.assertRaises(ValueError):
            self.api.export_finance(path, [order], catalog=self.catalog, draft=False)
        order["rows"] = [complete]
        complete["finance_amount"] = None
        with self.assertRaises(ValueError):
            self.api.export_finance(path, [order], catalog=self.catalog, draft=False)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
