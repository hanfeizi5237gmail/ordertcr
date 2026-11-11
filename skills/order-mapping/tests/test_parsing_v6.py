import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from order_mapping_core.parsing import parse_chat


class ParseChatV6Tests(unittest.TestCase):
    def test_labeled_business_context_and_quantity_first_connector(self):
        text = ('公司：甲站\n本批确认日期均为2026年，当前目录为目录A。\n'
                '配送日期：2026-11-17\n8斤的哈密瓜\n')
        rows = parse_chat(text, company_aliases=['甲站'])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['name'], rows[0]['quantity'], rows[0]['unit'],
                          rows[0]['target_station'], rows[0]['date_context']),
                         ('哈密瓜', '8', '斤', '甲站', '2026-11-17'))
        self.assert_spans(text, rows)
        adjacent = parse_chat('西柚2kg 2斤的平菇（鲜蘑）')
        self.assertEqual([(r['name'], r['quantity'], r['unit']) for r in adjacent],
                         [('西柚', '2', 'kg'), ('平菇（鲜蘑）', '2', '斤')])

    def test_inline_company_switch_after_separator(self):
        text = ('各单位分开记账，下面公司名和配送日期适用于其后订单。\n'
                '公司：甲站\n配送日期：2026-12-08\n甲站：青萝卜3斤；乙站：白菜4斤')
        rows = parse_chat(text, company_aliases=['甲站', '乙站'])
        self.assertEqual([(r['name'], r['target_station']) for r in rows], [('青萝卜', '甲站'), ('白菜', '乙站')])
        self.assert_spans(text, rows)

    def test_invalid_quantities_preserve_sign_and_known_identity(self):
        text = '黑芝麻0斤\n干桂圆肉-2斤\n晴王葡萄x斤'
        rows = parse_chat(text)
        self.assertEqual([(r['name'], r['quantity'], r['unit']) for r in rows],
                         [('黑芝麻', '0', '斤'), ('干桂圆肉', '-2', '斤'), ('晴王葡萄', None, '斤')])
        self.assertTrue(all('invalid_quantity' in {i['code'] for i in r['issues']} for r in rows))
        self.assert_spans(text, rows)
        variants = parse_chat('干桂圆肉- 2斤\n干桂圆肉，数量-2斤\n黑豆、绿豆各0斤')
        self.assertEqual([(r['name'], r['quantity']) for r in variants],
                         [('干桂圆肉', '-2'), ('干桂圆肉', '-2'), ('黑豆', '0'), ('绿豆', '0')])
        self.assertTrue(all('invalid_quantity' in {i['code'] for i in r['issues']} for r in variants))
        invalid = parse_chat('黑豆1/0斤\n黑豆−2斤\n黑豆负二斤\n黑豆二二斤\n黑豆、绿豆各1/0斤')
        self.assertEqual([r['name'] for r in invalid], ['黑豆', '黑豆', '黑豆', '黑豆', '黑豆', '绿豆'])
        self.assertEqual([r['quantity'] for r in invalid], [None, '-2', '-2', None, None, None])
        self.assertTrue(all('invalid_quantity' in {i['code'] for i in r['issues']} for r in invalid))

    def test_total_amendment_duplicate_and_packaging_clauses(self):
        text = ('菠菜再追加2斤\n菠菜总量改为4斤\n'
                '重复转发上一条，请勿重复下单：菠菜3斤\n西兰花2kg整袋装1袋')
        rows = parse_chat(text)
        self.assertEqual(len(rows), 4)
        self.assertEqual([r['event'] for r in rows[:3]], ['追加', '改量', '重复'])
        self.assertEqual(rows[1]['amendment_scope'], 'total')
        self.assertEqual(rows[2]['name'], '菠菜')
        self.assert_spans(text, rows)

    def test_catalog_name_boundary_with_quantity_first_continuous_input(self):
        text = '9斤的荔浦芋头（优质）麻山药6kg'
        rows = parse_chat(text, product_names=['荔浦芋头（优质）', '麻山药'])
        self.assertEqual([(r['name'], r['quantity'], r['unit']) for r in rows],
                         [('荔浦芋头（优质）', '9', '斤'), ('麻山药', '6', 'kg')])
        self.assert_spans(text, rows)
        text = '2斤青萝卜(去皮)白菜3斤'
        rows = parse_chat(text, product_names=['青萝卜', '白菜'])
        self.assertEqual([r['name'] for r in rows], ['青萝卜', '白菜'])
        self.assertIn('去皮', rows[0]['notes'])
        self.assert_spans(text, rows)
        self.assertEqual(len(parse_chat('2斤青萝卜每袋3斤', product_names=['青萝卜'])), 1)

    def test_numeric_product_suffix_does_not_consume_the_order_quantity(self):
        text = '北极冰虾80*1005kg\n土豆（大885）3斤\n北极冰虾80*100'
        rows = parse_chat(text, product_names=['北极冰虾80*100', '土豆（大885）'])
        self.assertEqual([(r['name'], r['quantity'], r['unit']) for r in rows],
                         [('北极冰虾80*100', '5', 'kg'), ('土豆（大885）', '3', '斤'),
                          ('北极冰虾80*100', None, None)])
        self.assert_spans(text, rows)

    def test_quantity_first_item_immediately_after_a_completed_item(self):
        text = '鲜藕７斤2斤的西梅\n荷兰豆九斤1斤的黄元帅苹果'
        rows = parse_chat(text, product_names=['鲜藕', '西梅', '荷兰豆', '黄元帅苹果'])
        self.assertEqual([(r['name'], r['quantity'], r['unit']) for r in rows],
                         [('鲜藕', '7', '斤'), ('西梅', '2', '斤'),
                          ('荷兰豆', '9', '斤'), ('黄元帅苹果', '1', '斤')])
        self.assert_spans(text, rows)

    def test_inline_company_date_and_quantity_clause_are_one_event(self):
        raw='新公司 2026-11-10：荷兰豆，数量2斤\n新公司 2026-11-11：荷兰豆改为数量3斤'
        rows=parse_chat(raw,company_aliases=['新公司'])
        self.assertEqual([(r['name'],r['quantity'],r['unit'],r['target_station'],r['date_context']) for r in rows],
                         [('荷兰豆','2','斤','新公司','2026-11-10'),('荷兰豆','3','斤','新公司','2026-11-11')])
        self.assertEqual(rows[1]['event'],'改量')
        self.assertTrue(all(raw[r['char_start']:r['char_end']]==r['raw_text'] for r in rows))

    def test_packaging_requirement_suffix_does_not_become_another_product(self):
        raw='其他品牌淀粉，数量2斤，换另一种包装'
        rows=parse_chat(raw)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['raw_text'],raw)
        self.assertIn('换另一种包装',rows[0]['notes'])

    def assert_spans(self, text, rows):
        self.assertTrue(all(text[row['char_start']:row['char_end']] == row['raw_text'] for row in rows))
        self.assertEqual(len(rows), len({(row['char_start'], row['char_end']) for row in rows}))

    def test_slash_adjacency_fullwidth_prefix_and_fraction(self):
        text = '土豆5斤白菜3斤/５斤红椒、青椒半斤、白菜1/2斤\n土豆2'
        rows = parse_chat(text)
        self.assertEqual([(r['name'], r['quantity'], r['unit']) for r in rows], [
            ('土豆', '5', '斤'), ('白菜', '3', '斤'), ('红椒', '5', '斤'),
            ('青椒', '0.5', '斤'), ('白菜', '0.5', '斤'), ('土豆', '2', None)])
        self.assertIn('missing_unit', {issue['code'] for issue in rows[-1]['issues']})
        self.assert_spans(text, rows)

    def test_parenthesized_packaging_is_not_order_quantity(self):
        text = '大米（每袋5斤）2袋；大米（每袋5斤）\n土豆5斤（每袋5斤）'
        rows = parse_chat(text)
        self.assertEqual([(r['name'], r['quantity'], r['unit']) for r in rows], [
            ('大米（每袋5斤）', '2', '袋'), ('大米（每袋5斤）', None, None),
            ('土豆', '5', '斤')])
        self.assertIn('每袋5斤', rows[2]['notes'])
        self.assertIn('missing_quantity', {issue['code'] for issue in rows[1]['issues']})
        self.assert_spans(text, rows)

    def test_shared_quantity_each_and_missing_unit(self):
        text = '青椒、红椒各5斤\n青椒、红椒各5\n青椒、红椒5斤'
        rows = parse_chat(text)
        self.assertEqual([(r['name'], r['quantity'], r['unit']) for r in rows[:2]],
                         [('青椒', '5', '斤'), ('红椒', '5', '斤')])
        self.assertEqual([text[s:e] for s, e in (r['shared_quantity_source_span'] for r in rows[:2])],
                         ['5斤', '5斤'])
        self.assertEqual([(r['name'], r['quantity'], r['unit']) for r in rows[2:4]],
                         [('青椒', '5', None), ('红椒', '5', None)])
        self.assertTrue(all(any(i['code'] == 'missing_unit' for i in r['issues']) for r in rows[2:4]))
        # Without 各, the first product has no independent quantity.
        self.assertEqual((rows[4]['name'], rows[4]['quantity'], rows[5]['name']), ('青椒', None, '红椒'))
        self.assert_spans(text, rows)

    def test_date_and_company_headings_are_text_evidence(self):
        text = '11.3\n星月站：土豆5斤\n【星月站】\n11月3号\n白菜3斤\n2026年11月4日\n青椒2斤'
        rows = parse_chat(text, 'neutral.txt')
        self.assertEqual([(r['name'], r['date_context'], r['target_station']) for r in rows], [
            ('土豆', '11.3', '星月站'), ('白菜', '11月3号', '星月站'),
            ('青椒', '2026年11月4日', '星月站')])
        self.assert_spans(text, rows)
        no_heading = parse_chat('土豆5斤', '星月站11.3下单.txt')[0]
        self.assertIsNone(no_heading['target_station'])

    def test_station_order_heading_without_colon_is_context(self):
        rows = parse_chat('2027-02-01\n星月站下单\n土豆5斤\n学院路站订单\n白菜2斤')
        self.assertEqual([(r['name'], r['target_station']) for r in rows],
                         [('土豆', '星月站'), ('白菜', '学院路站')])

    def test_missing_quantity_clause_stays_with_product_event(self):
        rows = parse_chat('鲜百合片，数量未定\n贡菜，多少斤待定')
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['raw_text'] for r in rows], ['鲜百合片，数量未定', '贡菜，多少斤待定'])
        self.assertTrue(all(r['quantity'] is None and r['unit'] is None for r in rows))

    def test_add_change_cancel_keep_source_spans_and_predecessor_clues(self):
        text = '土豆5斤\n再加土豆2斤\n土豆改成3斤\n取消土豆\n土豆5斤\n土豆5斤'
        rows = parse_chat(text)
        self.assertEqual(len(rows), 6)
        self.assertEqual([r['event'] for r in rows], [None, '追加', '改量', '取消', None, None])
        self.assertEqual([r['name'] for r in rows], ['土豆'] * 6)
        self.assertEqual([r['quantity'] for r in rows], ['5', '2', '3', None, '5', '5'])
        self.assertIn('missing_quantity', {i['code'] for i in rows[3]['issues']})
        for row in rows[1:4]:
            self.assertTrue(row['predecessor_clue'])
        self.assertEqual(rows[-1]['raw_text'], rows[-2]['raw_text'])
        self.assertNotEqual(rows[-1]['char_start'], rows[-2]['char_start'])
        self.assert_spans(text, rows)

    def test_iso_date_configured_company_alias_and_halfwidth_colon(self):
        text = '2026-11-03\n【新公司】\n土豆2斤\n新公司: 白菜3斤\n新公司：红椒4斤'
        rows = parse_chat(text, 'neutral.txt', company_aliases=['新公司'])
        self.assertEqual([(r['name'], r['date_context'], r['target_station']) for r in rows], [
            ('土豆', '2026-11-03', '新公司'), ('白菜', '2026-11-03', '新公司'),
            ('红椒', '2026-11-03', '新公司')])
        self.assert_spans(text, rows)
        # An unknown bracketed phrase is retained as input, never a trusted company.
        unknown = parse_chat('【未知公司】\n土豆2斤', company_aliases=['新公司'])
        # Unknown explicit headings must survive to routing so a default
        # company cannot silently absorb the following products.
        self.assertEqual(unknown[-1]['target_station'], '未知公司')

    def test_suffix_cancellation_and_explicit_relation_clues(self):
        text = '土豆5斤\n再加土豆2斤\n土豆改成3斤\n土豆取消\n土豆不要了\n土豆算了'
        rows = parse_chat(text)
        self.assertEqual([r['event'] for r in rows], [None, '追加', '改量', '取消', '取消', '取消'])
        self.assertEqual([r['predecessor_clue'] for r in rows[1:]], ['土豆'] * 5)
        self.assertEqual([r['name'] for r in rows], ['土豆'] * 6)
        self.assertTrue(all(r['quantity'] is None for r in rows[-3:]))
        self.assert_spans(text, rows)

    def test_originals_keep_159_events(self):
        original = Path(__file__).resolve().parents[3] / 'original'
        if not original.is_dir():
            self.skipTest('原始业务文件不随可移植skill包分发；在项目内运行此历史回归')
        for filename, expected in (('星月站11.3下单.txt', 42),
                                   ('学院路站11.3下单.txt', 64),
                                   ('松北大道站11.3下单.txt', 53)):
            with self.subTest(filename=filename):
                text = (original / filename).read_text(encoding='utf-8')
                rows = parse_chat(text, filename)
                self.assertEqual(len(rows), expected)
                self.assert_spans(text, rows)


if __name__ == '__main__':
    unittest.main()
