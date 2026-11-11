"""Shared expression rules must preserve identity, attributes and current prices."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from order_mapping_core.knowledge import load_knowledge, match_shared
from order_mapping_core.matching import match_item
from order_mapping_core.parsing import parse_chat


def record(code, name, price='7', spec='500g', category='肉制品'):
    return dict(code=code, name=name, price=price, spec=spec, category=category,
                sheet='当前目录', row=2)


class SharedExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.knowledge, _ = load_knowledge()

    def match(self, name, records, unit='斤'):
        item = dict(name=name, quantity='2', unit=unit, notes='', raw_text=name+'2'+unit)
        return match_shared(item, records, self.knowledge)

    def test_multicharacter_attributes_move_without_memorized_product_rules(self):
        records = [record('new-a', '带骨羊后腿（冷冻）'),
                   record('new-b', '去骨羊后腿（冷冻）', '99'),
                   record('new-c', '带骨羊后腿（鲜活）', '99')]
        row = self.match('冷冻带骨羊后腿', records)
        self.assertEqual(row['selected']['code'], 'new-a')
        self.assertEqual(row['invoice_quantity'], '2')
        self.assertEqual(row['match_basis'], 'shared_expression')
        self.assertTrue(row['knowledge_rule_ids'])
        self.assertIsNone(match_item(dict(name='冷冻带骨羊后腿', quantity='2', unit='斤'), records)['selected'])

    def test_category_label_and_parenthesized_pricing_unit_are_not_pack_sizes(self):
        for name, target in [('调料用白芷', '白芷（调料）'), ('白芷调料', '（调料）白芷'),
                             ('桃胶', '桃胶（斤）')]:
            with self.subTest(name=name):
                row = self.match(name, [record('new', target, category='调料类')])
                self.assertEqual(row['selected']['code'], 'new')
        self.assertIsNone(self.match('桃胶', [record('new', '桃胶（斤）', spec='250g')])['selected'])

    def test_current_equivalent_prices_and_specs_are_rechecked(self):
        records = [record('a', '冷冻带骨羊后腿', '8'), record('b', '带骨羊后腿（冷冻）', '9')]
        self.assertEqual(self.match('带骨冷冻羊后腿', records)['selected']['code'], 'b')
        records[0]['price'] = '10'
        self.assertEqual(self.match('带骨冷冻羊后腿', records)['selected']['code'], 'a')
        self.assertEqual(self.match('带骨冷冻羊后腿', records[1:])['selected']['code'], 'b')
        records[1]['spec'] = '250g'
        self.assertIsNone(self.match('带骨冷冻羊后腿', records)['selected'])
        self.assertIsNone(self.match('带骨冷冻羊后腿', [])['selected'])

    def test_no_brand_form_size_unknown_word_or_dry_compound_erasure(self):
        cases = [
            ('山月牌冷冻带骨羊后腿', '带骨羊后腿（冷冻）'),
            ('冷冻去皮鸭胸肉', '带皮鸭胸肉（冷冻）'),
            ('鲜活带皮鸭胸肉', '带皮鸭胸肉（冷冻）'),
            ('冷冻大号海鲈鱼', '冷冻海鲈鱼（中号）'),
            ('冷冻带皮鸭胸肉切片', '带皮鸭胸肉（冷冻）'),
            ('冷冻带皮鸭胸肉优选', '带皮鸭胸肉（冷冻）'),
            ('豆腐干', '干豆腐'),
            ('干桑葚', '桑葚干（斤）'),
            ('鲜海菇', '海鲜菇'),
            ('鲜味酱', '味酱（鲜）'),
            ('冷冻干燥草莓', '干燥草莓（冷冻）'),
            ('冻干草莓', '冷冻草莓干'),
        ]
        for name, target in cases:
            with self.subTest(name=name):
                self.assertIsNone(self.match(name, [record('a', target)])['selected'])

    def test_box_quantity_does_not_become_weight(self):
        row = self.match('调料用白芷', [record('a', '白芷（调料）', category='调料类')], '箱')
        self.assertIsNone(row['invoice_quantity'])
        self.assertNotEqual(row['status'], 'matched')
        self.assertEqual((row['quantity'], row['unit']), ('2', '箱'))

    def test_kb16_scoped_dry_and_seaweed_word_order_variants(self):
        cases = [
            ('桑葚干', '桑葚干（斤）'), ('干桑葚', '桑葚干（斤）'),
            ('干的桑葚', '桑葚干（斤）'), ('贡菜干', '干 贡菜（斤）'),
            ('干贡菜', '干 贡菜（斤）'), ('干的贡菜', '干 贡菜（斤）'),
            ('干烤麸大片', '烤麸干(大片)'), ('大片干烤麸', '烤麸干(大片)'),
            ('烤麸干大片', '烤麸干(大片)'), ('鲜海带扣带盐', '鲜海带扣（带盐）'),
            ('带盐鲜海带扣', '鲜海带扣（带盐）'), ('鲜的带盐海带扣', '鲜海带扣（带盐）'),
        ]
        for name, target in cases:
            with self.subTest(name=name):
                category = ('水产品' if '海带' in target else
                            '面类' if '烤麸' in target else '调料类')
                row = self.match(name, [record('kb16', target, category=category)])
                self.assertEqual(row['selected']['name'], target)
                self.assertIn(row['match_basis'], ('shared_knowledge', 'shared_expression'))

    def test_kb16_near_names_are_not_collapsed(self):
        for name, target in [
            ('鲜海带根', '鲜海带扣（带盐）'),
            ('鲜海带丝', '鲜海带扣（带盐）'),
            ('桑葚酸奶', '桑葚干（斤）'),
            ('干面筋', '烤麸干(大片)'),
        ]:
            with self.subTest(name=name):
                row = self.match(name, [record('near', target, category='调料类')])
                self.assertIsNone(row['selected'])

    def test_catalog_grounded_relations_transfer_to_unlisted_goods(self):
        cases = [
            ('蜜梨要进口的', '进口蜜梨'),
            ('这批蜜梨按进口的', '蜜梨（进口）'),
            ('蜜梨按翠冠品种', '翠冠蜜梨'),
            ('翠冠品种蜜梨', '翠冠蜜梨'),
            ('干制的马齿苋', '干马齿苋'),
            ('马齿苋要干的', '干马齿苋'),
            ('马齿苋按干品', '干马齿苋'),
            ('海鲈鱼要冰鲜的', '海鲈鱼（冰鲜）'),
            ('羊肚要清真供货', '清真羊肚'),
            ('羊肚按清真要求', '清真羊肚'),
        ]
        for name, target in cases:
            with self.subTest(name=name):
                row = self.match(name, [record('unseen', target)])
                self.assertIsNotNone(row['selected'])
                self.assertEqual(row['selected']['name'], target)
                self.assertEqual(row['status'], 'matched')
                self.assertEqual(row['name'], name)

    def test_relation_keeps_all_words_and_requires_one_interpretation(self):
        for name in ('蜜梨要进口特选的', '蜜梨不要进口的', '蜜梨按山月牌进口的',
                     '海鲈鱼要冷冻冰鲜的', '蜜梨要进口的每袋500g'):
            with self.subTest(name=name):
                row = self.match(name, [record('a', '进口蜜梨'), record('b', '海鲈鱼（冰鲜）')])
                self.assertIsNone(row['selected'])
        row = self.match('豆腐要干的', [record('a', '干豆腐', '4'), record('b', '豆腐干', '9')])
        self.assertIsNone(row['selected'])
        self.assertEqual({r['code'] for r in row['candidates']}, {'a', 'b'})
        self.assertIn('expression_interpretation_ambiguous', {i['code'] for i in row['issues']})

    def test_relation_current_catalog_reprice_remove_and_spec_change(self):
        rows = [record('a', '进口蜜梨', '5'), record('b', '蜜梨（进口）', '7')]
        self.assertEqual(self.match('蜜梨要进口的', rows)['selected']['code'], 'b')
        rows[0]['price'] = '8'
        self.assertEqual(self.match('蜜梨要进口的', rows)['selected']['code'], 'a')
        self.assertEqual(self.match('蜜梨要进口的', rows[1:])['selected']['code'], 'b')
        rows[1]['spec'] = '250g'
        self.assertIsNone(self.match('蜜梨要进口的', rows)['selected'])
        self.assertIsNone(self.match('蜜梨要进口的', [])['selected'])

    def test_explicit_spec_is_filtered_before_equivalent_price_sorting(self):
        rows = [record('a', '进口蜜梨', '19'), record('b', '进口蜜梨', '7', spec='250g')]
        item = dict(name='蜜梨要进口的', notes='250g', quantity='2', unit='斤', raw_text='蜜梨要进口的2斤（250g）')
        row = match_shared(item, rows, self.knowledge)
        self.assertEqual(row['selected']['code'], 'b')
        self.assertEqual(row['invoice_quantity'], '4')

    def test_kb18_complete_attribute_relations_preserve_base_and_qualifier(self):
        for request in ('蜜梨的进口款', '要进口的蜜梨', '蜜梨，要求进口',
                        '蜜梨,要求:进口'):
            with self.subTest(request=request):
                row = self.match(request, [record('new', '进口蜜梨')])
                self.assertEqual(row['selected']['code'], 'new')
                self.assertEqual(row['name'], request)
        for request in ('蜜梨的进口特选款', '要山月牌进口的蜜梨',
                        '蜜梨，要求进口大号', '蜜梨的非进口款',
                        '要切片的蜜梨', '蜜梨，要求不要进口'):
            with self.subTest(request=request):
                self.assertIsNone(self.match(request, [record('new', '进口蜜梨')])['selected'])

    def test_kb18_different_compositions_stay_ambiguous(self):
        for name in ('豆腐的干款', '要干的豆腐', '豆腐，要求干'):
            with self.subTest(name=name):
                row = self.match(name, [record('a', '干豆腐', '4'), record('b', '豆腐干', '9')])
                self.assertIsNone(row['selected'])
                self.assertEqual({r['code'] for r in row['candidates']}, {'a', 'b'})


class MissingUnitTests(unittest.TestCase):
    def test_missing_quantity_and_unit_are_both_reported(self):
        row = parse_chat('桃胶')[0]
        self.assertTrue({'missing_quantity', 'missing_unit'}.issubset({i['code'] for i in row['issues']}))

    def test_invalid_quantity_with_explicit_unit_is_not_missing_unit(self):
        row = parse_chat('桃胶?斤')[0]
        self.assertEqual(row['unit'], '斤')
        self.assertNotIn('missing_unit', {i['code'] for i in row['issues']})


if __name__ == '__main__':
    unittest.main()
