"""Explicit specification labels must not become product names or quantities."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from order_mapping_core.matching import match_item
from order_mapping_core.parsing import parse_chat


def record(code, name, spec='500g'):
    return dict(code=code, name=name, spec=spec, price='7', category='食材',
                sheet='synthetic', row=2)


class KB19SpecificationTests(unittest.TestCase):
    def test_labeled_specification_keeps_order_quantity_and_source_span(self):
        for text in ('冰鲜紫河鱼规格500g，数量2斤',
                     '冰鲜紫河鱼，规格：500g，数量2斤'):
            with self.subTest(text=text):
                rows = parse_chat(text)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual((row['name'], row['quantity'], row['unit']),
                                 ('冰鲜紫河鱼', '2', '斤'))
                self.assertIn('500g', row['notes'])
                self.assertEqual(text[row['char_start']:row['char_end']], text)
                result = match_item(row, [record('ok', '冰鲜紫河鱼')])
                self.assertEqual(result['status'], 'matched')
                self.assertEqual(result['invoice_quantity'], '2')

    def test_specification_without_order_quantity_remains_missing(self):
        row, = parse_chat('冰鲜紫河鱼规格500g')
        self.assertEqual(row['name'], '冰鲜紫河鱼')
        self.assertIsNone(row['quantity'])
        self.assertIsNone(row['unit'])
        self.assertIn('missing_quantity', {i['code'] for i in row['issues']})

    def test_generic_name_retrieves_its_products_and_keeps_form_ambiguity(self):
        row, = parse_chat('紫河鱼规格500g，数量2斤')
        result = match_item(row, [record('fresh', '冰鲜紫河鱼'),
                                  record('frozen', '冷冻紫河鱼'),
                                  record('salt', '调味盐500g')])
        self.assertEqual({c['code'] for c in result['candidates']}, {'fresh', 'frozen'})
        self.assertIsNone(result['selected'])
        self.assertIsNone(result['invoice_quantity'])

    def test_size_mismatch_and_unrecognized_requirement_stay_pending(self):
        for text in ('冰鲜紫河鱼规格600g，数量2斤',
                     '冰鲜紫河鱼云端牌规格500g，数量2斤'):
            with self.subTest(text=text):
                row, = parse_chat(text)
                result = match_item(row, [record('fish', '冰鲜紫河鱼')])
                self.assertNotEqual(result['status'], 'matched')
                self.assertIsNone(result['invoice_quantity'])


if __name__ == '__main__':
    unittest.main()
