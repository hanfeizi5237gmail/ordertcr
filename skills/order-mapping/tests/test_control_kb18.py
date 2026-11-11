from pathlib import Path
import sys
import unittest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_raw_messages_v6 import RawMessageTests


class ControlChainTests(RawMessageTests):
    def test_ambiguous_control_blocks_candidates_and_workbook_amounts(self):
        result = self.run_text('【甲站】\n青萝卜2斤\n青萝卜追加1斤\n青萝卜刚才改3斤\n青萝卜刚才那条不要了')
        rows = [event['row'] for event in result['events']]
        self.assertEqual([row['name'] for row in rows], ['青萝卜'] * 4)
        self.assertEqual([row['event'] for row in rows], [None, '追加', '改量', '取消'])
        self.assertTrue(all(row['event_state'] == 'pending' for row in rows))
        for row in rows:
            self.assertIsNone(row['invoice_quantity'])
            self.assertIsNone(row['amount'])
            self.assertIsNone(row['finance_amount'])
        import json
        manifest = json.loads(Path(result['manifests'][0]).read_text())
        sheet = load_workbook(manifest['files']['standard']['path']).active
        for excel_row in range(2, 6):
            self.assertIsNone(sheet[f'J{excel_row}'].value)
            self.assertIsNotNone(sheet[f'D{excel_row}'].comment)

    def test_ambiguous_existing_change_has_no_billable_quantity(self):
        result = self.run_text('【甲站】\n青萝卜2斤\n青萝卜追加1斤\n青萝卜改成3斤')
        rows = [event['row'] for event in result['events']]
        self.assertTrue(all(row['event_state'] == 'pending' for row in rows))
        for row in rows:
            self.assertIsNone(row['invoice_quantity'])
            self.assertIsNone(row['amount'])
            self.assertIsNone(row['finance_amount'])

    def test_synonymous_names_share_ambiguous_control_chain(self):
        from openpyxl import Workbook
        from order_mapping_core.catalog import CatalogStore
        path = self.root / 'synonyms.xlsx'
        wb = Workbook(); sheet = wb.active
        sheet.append(['类别名称', '材料编号', '材料名称', '挂牌价', '规格'])
        sheet.append(['蔬菜', '2', '土豆（大885）', '2', '500g'])
        wb.save(path)
        CatalogStore(self.root / 'runtime').register(path)
        result = self.run_text('【甲站】\n土豆2斤\n马铃薯追加1斤\n土豆刚才改3斤')
        rows = [event['row'] for event in result['events']]
        self.assertTrue(all(row['event_state'] == 'pending' for row in rows))
        self.assertTrue(all(row['invoice_quantity'] is None and row['amount'] is None for row in rows))

    def test_distinct_brand_cut_and_form_do_not_share_control(self):
        from openpyxl import Workbook
        from order_mapping_core.catalog import CatalogStore
        cases = [('甲牌牛奶', '乙牌牛奶'), ('猪前腿肉', '猪后腿肉'), ('鲜香菇', '干香菇')]
        path = self.root / 'different.xlsx'
        wb = Workbook(); sheet = wb.active
        sheet.append(['类别名称', '材料编号', '材料名称', '挂牌价', '规格'])
        for index, name in enumerate(name for pair in cases for name in pair):
            sheet.append(['食材', str(index + 10), name, '2', '500g'])
        wb.save(path)
        CatalogStore(self.root / 'runtime').register(path)
        for left, right in cases:
            with self.subTest(left=left, right=right):
                result = self.run_text(f'【甲站】\n{left}2斤\n{right}3斤\n取消{left}')
                rows = [event['row'] for event in result['events']]
                self.assertEqual(rows[0]['event_state'], 'inactive')
                self.assertEqual(rows[1]['event_state'], 'active')
                self.assertIsNotNone(rows[1]['amount'])
                self.assertEqual(rows[2]['event_state'], 'cancellation_record')

    def test_same_name_with_unresolved_requirement_blocks_all_related_lines(self):
        result = self.run_text('【甲站】\n青萝卜2斤\n青萝卜2斤其他品牌\n青萝卜刚才改3斤')
        rows = [event['row'] for event in result['events']]
        self.assertTrue(all(row['event_state'] == 'pending' for row in rows))
        self.assertTrue(all(row['amount'] is None and row['invoice_quantity'] is None for row in rows))

    def test_unresolved_same_name_requirement_blocks_synonymous_append(self):
        """An alias append must not stay billable when its same-name group is unresolved."""
        from openpyxl import Workbook
        from order_mapping_core.catalog import CatalogStore
        path = self.root / 'synonymous_append.xlsx'
        wb = Workbook(); sheet = wb.active
        sheet.append(['类别名称', '材料编号', '材料名称', '挂牌价', '规格'])
        sheet.append(['蔬菜', '2', '土豆（大885）', '2', '500g'])
        wb.save(path)
        CatalogStore(self.root / 'runtime').register(path)
        result = self.run_text('【甲站】\n土豆2斤\n马铃薯追加1斤\n土豆2斤其他品牌\n土豆刚才改3斤')
        rows = [event['row'] for event in result['events']]
        self.assertTrue(all(row['event_state'] == 'pending' for row in rows))
        self.assertTrue(all(row['invoice_quantity'] is None and row['amount'] is None
                            and row['finance_amount'] is None for row in rows))

    def test_cache_keeps_raw_constraints_separate(self):
        from order_mapping_core import raw_messages
        from order_mapping_core.knowledge import match_shared as actual_match
        observed = []
        def tracking_match(item, records, knowledge, **kwargs):
            observed.append(item['raw_text'])
            return actual_match(item, records, knowledge, **kwargs)
        original = raw_messages.match_shared
        raw_messages.match_shared = tracking_match
        try:
            self.run_text('【甲站】\n青萝卜2斤\n2斤青萝卜\n取消青萝卜')
        finally:
            raw_messages.match_shared = original
        self.assertIn('青萝卜2斤', observed)
        self.assertIn('2斤青萝卜', observed)

    def test_pending_control_does_not_block_other_company_or_date(self):
        result = self.run_text('【甲站】\n青萝卜2斤\n青萝卜追加1斤\n青萝卜刚才改3斤\n'
                               '【乙站】\n青萝卜4斤\n2026-11-04\n【甲站】\n青萝卜5斤')
        rows = [event['row'] for event in result['events']]
        self.assertTrue(all(row['event_state'] == 'pending' for row in rows[:3]))
        self.assertTrue(all(row['amount'] is None and row['invoice_quantity'] is None for row in rows[:3]))
        self.assertTrue(all(row['event_state'] == 'active' and row['amount'] is not None for row in rows[3:]))


if __name__ == '__main__':
    unittest.main()
