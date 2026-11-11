"""Regression tests for demand constraints and current-catalog changes."""

import copy
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from order_mapping_core.catalog import _parse_catalog
from order_mapping_core.knowledge import catalog_knowledge_audit, load_knowledge, match_shared


class CatalogConstraintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = Path(__file__).resolve().parents[3] / 'original/食堂食材基准价价格表10.12-10.18.xlsx'
        if not catalog.exists():
            raise unittest.SkipTest('业务原目录不随通用 skill 分发')
        cls.records = _parse_catalog(catalog.read_bytes())
        cls.knowledge, _ = load_knowledge()

    def match(self, name, unit, notes='', records=None):
        item = {'name': name, 'quantity': '2', 'unit': unit, 'notes': notes,
                'raw_text': name + '2' + unit + notes}
        return match_shared(item, self.records if records is None else records, self.knowledge)

    def test_audit_shows_partially_missing_selection_names(self):
        onion = next(rule for rule in self.knowledge['rules'] if rule['id'] == 'onion_generic')
        records = [record for record in self.records if record['name'] != '黄洋葱']
        audit = catalog_knowledge_audit({'schema_version': 1, 'rules': [onion]}, records)[0]

        self.assertIn('selection_missing_names', audit)
        self.assertIn('黄洋葱', audit['selection_missing_names'])
        self.assertNotEqual(audit['status'], 'available')
        self.assertTrue(audit['selection_candidate_codes'])

    def test_changed_catalog_size_cannot_use_old_320g_package_rule(self):
        records = copy.deepcopy(self.records)
        changed = []
        for record in records:
            if record['name'].replace(' ', '').lower() == '亨氏番茄沙司320g/袋':
                record['spec'] = '340g'
                changed.append(record)
        self.assertEqual(len(changed), 2, '测试需覆盖当前目录中两条 320g 商品')

        row = self.match('番茄沙司', '袋', records=records)

        self.assertNotEqual(row['status'], 'matched')
        self.assertIsNone(row['invoice_quantity'])

    def test_unprefixed_tomato_variety_does_not_choose_maofen(self):
        row = self.match('西红柿', '斤', '子弹头柿子')

        if row['selected'] is not None:
            self.assertEqual(row['selected']['name'], '子弹头柿子')

    def test_chinese_grams_in_package_note_exclude_other_size(self):
        row = self.match('番茄沙司', '袋', '每袋250克')

        if row['selected'] is not None:
            self.assertIn('250', row['selected']['name'] + row['selected']['spec'])

    def test_new_tomato_is_retrieved_across_both_synonymous_requests(self):
        tomato = next(record for record in self.records if record['name'] == '毛粉柿子')
        records = self.records + [{**tomato, 'code': 'AUDIT-new-tomato', 'name': '优质番茄', 'price': '1'}]

        for name in ('西红柿', '番茄'):
            with self.subTest(name=name):
                row = self.match(name, '斤', records=records)
                self.assertIn('AUDIT-new-tomato', {record['code'] for record in row['candidates']})
                self.assertIn('knowledge_new_candidates', {issue['code'] for issue in row['issues']})
                self.assertIsNone(row['selected'])
                self.assertIsNone(row['invoice_quantity'])

    def test_original_catalog_still_selects_generic_tomato(self):
        row = self.match('西红柿', '斤')

        self.assertEqual(row['status'], 'matched')
        self.assertEqual(row['selected']['name'], '毛粉柿子')
        self.assertEqual(row['invoice_quantity'], '2')

    def test_equal_pack_sizes_keep_normal_selection_and_quantity(self):
        row = self.match('番茄沙司', '袋')

        self.assertEqual(row['status'], 'matched')
        self.assertEqual(row['selected']['code'], '80004512')
        self.assertEqual(row['invoice_quantity'], '2')
        self.assertEqual(row['price_unit'], '袋')

    def test_unqualified_pepper_compares_current_prices_but_keeps_explicit_color(self):
        for records in (self.records, [
                {**r, 'price': '20'} if r['name'] == '尖椒' else r for r in self.records]):
            with self.subTest(current_prices=records is self.records):
                candidates = [r for r in records if r['name'] in ('尖椒', '地产尖椒')]
                expected = max(candidates, key=lambda r: Decimal(r['price']))
                row = self.match('尖椒', '斤', records=records)
                self.assertEqual(row['selected']['code'], expected['code'])
        self.assertEqual(self.match('地产尖椒', '斤')['selected']['name'], '地产尖椒')
        self.assertEqual(self.match('红色尖椒', '斤')['selected']['name'], '红尖椒')

    def test_unverified_processing_keeps_product_and_original_quantity_for_review(self):
        row = self.match('鲤鱼', '斤', '收拾好')

        self.assertEqual(row['product_status'], 'matched')
        self.assertEqual(row['selected']['name'], '鲜活江鲤鱼')
        self.assertEqual((row['quantity'], row['unit']), ('2', '斤'))
        self.assertNotEqual(row['status'], 'matched')
        self.assertIn('processing_unverified', {issue['code'] for issue in row['issues']})
        self.assertEqual(row['quantity_status'], 'needs_review')
        self.assertIsNone(row['invoice_quantity'])


if __name__ == '__main__':
    unittest.main()
