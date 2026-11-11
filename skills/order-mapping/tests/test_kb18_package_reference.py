"""Packaging references must distinguish demand from catalog evidence."""
import json
from pathlib import Path
import sys
import unittest

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
from order_mapping_core.parsing import parse_chat
from order_mapping_core.knowledge import match_shared


class PackageReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.knowledge = json.loads((SKILL / 'knowledge/procurement.json').read_text())

    def test_explicit_pack_size_keeps_unknown_reference_without_false_claim(self):
        for name, size, unit in [('星野果酱', 600, '袋'), ('海岛果酱', 850, '包')]:
            with self.subTest(unit=unit):
                text = f'{name}（{size}克/{unit}）2{unit}'
                records = [
                    {'code': 'unknown', 'name': name, 'category': '调料', 'spec': '1kg', 'price': '3'},
                    {'code': 'conflict', 'name': f'{name}（250克/{unit}）', 'category': '调料', 'spec': '250g', 'price': '2'},
                ]
                original = [dict(record) for record in records]
                item, = parse_chat(text, source_name='synthetic.txt')
                result = match_shared(item, records, self.knowledge)
                self.assertEqual(text[item['char_start']:item['char_end']], item['raw_text'])
                self.assertEqual(result['status'], 'needs_review')
                self.assertIsNone(result['selected'])
                self.assertIsNone(result['invoice_quantity'])
                self.assertIsNone(result.get('amount'))
                self.assertEqual([r['code'] for r in result['candidates']], ['unknown'])
                self.assertNotIn('缺每袋净重', result['match_reason'])
                self.assertIn('目录', result['match_reason'])
                self.assertIn('包装', result['match_reason'])
                reason = result['candidates'][0]['match_reason']
                self.assertIn('1kg', reason)
                self.assertIn('未证明', reason)
                self.assertIn('不可自动替代', reason)
                self.assertEqual(records, original)

    def test_unspecified_pack_size_retains_existing_warning(self):
        for text in ('星野果酱2袋', '星野果酱（600克）2袋'):
            with self.subTest(text=text):
                item, = parse_chat(text, source_name='synthetic.txt')
                reference_name = '星野果酱大瓶' if text == '星野果酱2袋' else '星野果酱'
                records = [{'code': 'a', 'name': reference_name, 'category': '调料', 'spec': '1kg', 'price': '3'}]
                result = match_shared(item, records, self.knowledge)
                self.assertIn('缺每袋净重或包装关系', result['match_reason'])

    def test_exact_pack_size_match_remains_selectable(self):
        item, = parse_chat('星野果酱（600克/袋）2袋', source_name='synthetic.txt')
        records = [{'code': 'a', 'name': '星野果酱（600克/袋）',
                    'category': '调料', 'spec': '600g', 'price': '3'}]
        result = match_shared(item, records, self.knowledge)
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['selected']['code'], 'a')


if __name__ == '__main__':
    unittest.main()
