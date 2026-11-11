"""KB19 unseen relation forms: only catalog-proven compositions may match."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from order_mapping_core.knowledge import load_knowledge, match_shared


def record(code, name, price='7', spec='500g', category='食材'):
    return dict(code=code, name=name, price=price, spec=spec, category=category,
                sheet='KB19 temporary catalog', row=2)


class KB19RelationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.knowledge, _ = load_knowledge()

    def match(self, name, target):
        item = {'name': name, 'quantity': '2', 'unit': '斤', 'notes': '',
                'raw_text': name + '2斤'}
        return match_shared(item, [record('kb19', target)], self.knowledge)

    def test_unseen_selection_and_supply_phrases_are_catalog_grounded(self):
        cases = [
            ('山楂选干的', '干山楂'),
            ('山楂请按干供货', '干山楂'),
            ('山楂要干', '干山楂'),
            ('选干的山楂', '干山楂'),
            ('干的山楂要', '干山楂'),
            ('冷冻的小人仙肉要', '冷冻小人仙肉'),
        ]
        for request, target in cases:
            with self.subTest(request=request):
                row = self.match(request, target)
                self.assertIsNotNone(row['selected'])
                self.assertEqual(row['selected']['name'], target)

    def test_post_quantity_relation_uses_notes_without_overwriting_original_name(self):
        item = {'name': '蛋鸡', 'quantity': '1', 'unit': '公斤', 'notes': '要冷冻',
                'raw_text': '蛋鸡1公斤要冷冻'}
        rows = [record('old', '淘汰蛋鸡'), record('new', '蛋鸡（冷冻）')]
        row = match_shared(item, rows, self.knowledge)
        self.assertEqual(row['selected']['code'], 'new')
        self.assertEqual(row['name'], '蛋鸡')
        self.assertEqual(row['notes'], '要冷冻')

    def test_post_quantity_selection_and_supply_preserve_constraints(self):
        # A complete current-catalog name proves the attribute. The selection
        # marker is grammar, but every unknown or negative word still matters.
        for notes in ('选鲜活的', '按鲜活供货', '请按鲜活供货'):
            with self.subTest(notes=notes):
                item = {'name': '陌生鱼', 'quantity': '2', 'unit': '公斤',
                        'notes': notes, 'raw_text': '陌生鱼2公斤' + notes}
                rows = [record('fresh', '鲜活陌生鱼'), record('frozen', '冷冻陌生鱼')]
                row = match_shared(item, rows, self.knowledge)
                self.assertEqual(row['status'], 'matched')
                self.assertEqual(row['selected']['code'], 'fresh')
                self.assertEqual(row['invoice_quantity'], '4')
                self.assertEqual((row['name'], row['notes'], row['quantity'], row['unit']),
                                 ('陌生鱼', notes, '2', '公斤'))

        for notes in ('选云端牌鲜活的', '不要鲜活的', '请按鲜活供货切片',
                      '选鲜活的每袋500g', '按冷冻鲜活供货'):
            with self.subTest(notes=notes):
                item = {'name': '陌生鱼', 'quantity': '2', 'unit': '公斤',
                        'notes': notes, 'raw_text': '陌生鱼2公斤' + notes}
                rows = [record('fresh', '鲜活陌生鱼'), record('frozen', '冷冻陌生鱼')]
                row = match_shared(item, rows, self.knowledge)
                self.assertNotEqual(row['status'], 'matched')
                self.assertIsNone(row['invoice_quantity'])

    def test_unknown_words_negation_and_cross_identity_do_not_disappear(self):
        for request in ('山楂选特选干的', '山楂不要干的', '山楂选干的切片'):
            with self.subTest(request=request):
                rows = [record('a', '干山楂'), record('b', '干豆腐')]
                item = {'name': request, 'quantity': '2', 'unit': '斤',
                        'notes': '', 'raw_text': request + '2斤'}
                self.assertIsNone(match_shared(item, rows, self.knowledge)['selected'])

        # A single catalog-proven composition is selectable; the same request
        # becomes ambiguous when both word orders are current candidates.
        rows = [record('a', '干豆腐'), record('b', '豆腐干')]
        item = {'name': '豆腐选干的', 'quantity': '2', 'unit': '斤',
                'notes': '', 'raw_text': '豆腐选干的2斤'}
        row = match_shared(item, rows, self.knowledge)
        self.assertIsNone(row['selected'])
        self.assertEqual({r['code'] for r in row['candidates']}, {'a', 'b'})
        self.assertIn('expression_interpretation_ambiguous', {i['code'] for i in row['issues']})


if __name__ == '__main__':
    unittest.main()
