import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from order_mapping_core.parsing import parse_chat


class ParseChatV7FreshRegressionTests(unittest.TestCase):
    def assert_spans(self, text, rows):
        self.assertTrue(all(text[row['char_start']:row['char_end']] == row['raw_text'] for row in rows))
        self.assertEqual(len(rows), len({(row['char_start'], row['char_end']) for row in rows}))

    def test_quantity_colon_is_not_part_of_product_and_keeps_adjacent_item(self):
        text = '网纹瓜：1斤；香菜2斤'
        rows = parse_chat(text, product_names=['网纹瓜', '香菜'])
        self.assertEqual([(row['name'], row['quantity'], row['unit']) for row in rows],
                         [('网纹瓜', '1', '斤'), ('香菜', '2', '斤')])
        self.assert_spans(text, rows)

    def test_complete_catalog_slash_name_and_parenthesized_slash_are_protected(self):
        text = ('红丁萝卜/红萝卜丁2斤；土豆5斤/白菜3斤；'
                '丹丹郫县豆瓣酱（250克/袋）2袋；青萝卜1斤')
        rows = parse_chat(text, product_names=[
            '红丁萝卜/红萝卜丁', '土豆', '白菜',
            '丹丹郫县豆瓣酱（250克/袋）', '青萝卜'])
        self.assertEqual([(row['name'], row['quantity'], row['unit']) for row in rows], [
            ('红丁萝卜/红萝卜丁', '2', '斤'), ('土豆', '5', '斤'), ('白菜', '3', '斤'),
            ('丹丹郫县豆瓣酱（250克/袋）', '2', '袋'), ('青萝卜', '1', '斤')])
        self.assert_spans(text, rows)

    def test_complete_explanatory_header_is_skipped_without_swallowing_order(self):
        text = ('各单位分开记账，日期与公司以文本为准。\n'
                '公司：甲站\n配送日期：2026-12-01\n青萝卜2斤')
        rows = parse_chat(text, company_aliases=['甲站'], product_names=['青萝卜'])
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['name'], rows[0]['quantity'], rows[0]['unit']), ('青萝卜', '2', '斤'))
        self.assert_spans(text, rows)

    def test_unrelated_slash_still_separates_adjacent_orders(self):
        text = '土豆5斤/白菜3斤'
        rows = parse_chat(text, product_names=['土豆', '白菜'])
        self.assertEqual([(row['name'], row['quantity'], row['unit']) for row in rows],
                         [('土豆', '5', '斤'), ('白菜', '3', '斤')])
        self.assert_spans(text, rows)

    def test_labeled_unknown_quantity_is_one_event_with_complete_span(self):
        for suffix in ('订购数量待确认', '订购2', '数量未定'):
            text = '蜜梨要进口的，' + suffix
            with self.subTest(suffix=suffix):
                rows = parse_chat(text)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['name'], '蜜梨要进口的')
                self.assertEqual(rows[0]['quantity'], '2' if suffix == '订购2' else None)
                self.assertIsNone(rows[0]['unit'])
                self.assert_spans(text, rows)

    def test_cut_and_grind_requests_preserve_notes_and_original_quantity(self):
        for text, name, note in [('猪心管切段2斤', '猪心管', '切段'),
                                 ('切段的猪心管2斤', '猪心管', '切段'),
                                 ('白芝麻磨成粉2斤', '白芝麻', '磨成粉')]:
            with self.subTest(text=text):
                rows = parse_chat(text)
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0]['name'], rows[0]['quantity'], rows[0]['unit']), (name, '2', '斤'))
                self.assertIn(note, rows[0]['notes'])
                self.assert_spans(text, rows)

    def test_kb18_requirement_with_only_order_quantity_keeps_one_event(self):
        text = '紫河鱼，要求冷冻3斤；蜜梨，要求进口2公斤；白菜2斤'
        rows = parse_chat(text, product_names=['紫河鱼', '蜜梨', '白菜'])
        self.assertEqual([(r['name'], r['quantity'], r['unit']) for r in rows], [
            ('紫河鱼，要求冷冻', '3', '斤'), ('蜜梨，要求进口', '2', '公斤'), ('白菜', '2', '斤')])
        self.assert_spans(text, rows)

    def test_kb18_quantity_first_with_requirement_measurement_is_not_overwritten(self):
        text = '3斤紫河鱼，要求冷冻500g；白菜2斤'
        rows = parse_chat(text, product_names=['紫河鱼', '白菜'])
        self.assertEqual(len(rows), 2)
        self.assertEqual((rows[0]['quantity'], rows[0]['unit']), ('3', '斤'))
        self.assertIn('500g', rows[0]['notes'])
        self.assertIn('冷冻', rows[0]['notes'])
        self.assert_spans(text, rows)

    def test_kb18_explicit_new_product_is_not_a_requirement(self):
        text = '紫河鱼3斤，牛肉2斤；1斤白菜，紫河鱼2斤'
        rows = parse_chat(text, product_names=['紫河鱼', '牛肉', '白菜'])
        self.assertEqual(len(rows), 4)
        self.assertEqual([(r['quantity'], r['unit']) for r in rows], [('3', '斤'), ('2', '斤'), ('1', '斤'), ('2', '斤')])
        self.assert_spans(text, rows)


if __name__ == '__main__':
    unittest.main()
