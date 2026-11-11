"""Identity must survive unresolved quantity and publication preconditions."""
import sys
import tempfile
from pathlib import Path
import unittest
from openpyxl import load_workbook
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from order_mapping_core.matching import match_item
from order_mapping_core.export import export_standard, export_finance

TMP = Path(__file__).resolve().parents[3] / 'evaluation/tmp'
TMP.mkdir(exist_ok=True,parents=True)


class IdentityTests(unittest.TestCase):
    def test_known_sku_with_unknown_weight_is_product_matched(self):
        item={'line_id':'x','raw_text':'鲤鱼5条','name':'鲤鱼','quantity':'5','unit':'条','issues':[]}
        rec={'code':'1','name':'鲤鱼','spec':'500g','price':'14.99','sheet':'目录','row':5}
        row=match_item(item,[rec])
        self.assertEqual(row['product_status'],'matched')
        self.assertEqual(row['quantity_status'],'needs_review')
        self.assertEqual(row['status'],'needs_review')
        row.update(discount='0.95',unit_price='14.2405',amount=None,finance_amount=None)
        with tempfile.TemporaryDirectory(dir=TMP) as directory:
            path=Path(directory)/'order.xlsx'
            export_standard(path,[row],company_name='测试',shipping_date='2026-11-03',order_number=None,
                            catalog={},pending=True)
            sheet=load_workbook(path).active
            self.assertEqual(sheet['D2'].value,'鲤鱼')
            self.assertEqual(sheet['E2'].value,'500g')
            self.assertEqual(sheet['F2'].value,5)
            self.assertEqual(sheet['G2'].value,14.99)
            self.assertEqual(sheet['I2'].value,14.2405)
            self.assertIsNone(sheet['J2'].value)
            self.assertEqual(sheet['D2'].fill.fgColor.rgb[-6:],'FFF2CC')
            self.assertIn('原始数量5条',sheet['F2'].comment.text)

    def test_unconfirmed_sku_never_uses_candidate_price(self):
        row=match_item({'line_id':'x','raw_text':'豆腐2盒','name':'豆腐','quantity':'2','unit':'盒','issues':[]},
                       [{'code':'1','name':'老豆腐','spec':'490g','price':'5','sheet':'目录','row':5}])
        self.assertNotEqual(row['product_status'],'matched')
        with tempfile.TemporaryDirectory(dir=TMP) as directory:
            p=Path(directory)/'x.xlsx'
            export_standard(p,[row],company_name='测试',shipping_date='2026-11-03',order_number=None,catalog={},pending=True)
            s=load_workbook(p).active
            self.assertIsNone(s['G2'].value)
            self.assertEqual(s['E2'].value,'盒')

    def test_invalid_history_alias_is_not_product_matched(self):
        row=match_item({'name':'豆腐','quantity':'1','unit':'盒','issues':[]},[],aliases={'豆腐':'deleted'})
        self.assertEqual(row['product_status'],'unmapped')
