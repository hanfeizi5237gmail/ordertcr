import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from order_mapping_core.matching import match_item


RECORDS = [
    {"code": "1", "name": "农大红肠", "category": "肉制品", "spec": "500g", "price": "46.47", "sheet": "Sheet0", "row": 1466},
    {"code": "2", "name": "秋林里道斯红肠", "category": "肉制品", "spec": "500g", "price": "41.00", "sheet": "Sheet0", "row": 1468},
    {"code": "3", "name": "红肠", "category": "肉制品", "spec": "500g", "price": "42.00", "sheet": "Sheet0", "row": 1470},
    {"code": "4", "name": "红肠", "category": "肉制品", "spec": "500g", "price": "43.00", "sheet": "Sheet0", "row": 1471},
    {"code": "5", "name": "红肠", "category": "肉制品", "spec": "袋", "price": "90.00", "sheet": "Sheet0", "row": 1472},
]


class MatchTests(unittest.TestCase):
    def test_current_catalog_mutual_names_preserve_one_record_and_current_price(self):
        records = [{'code': 'current', 'name': '红丁萝卜/红萝卜丁', 'category': '蔬菜',
                    'spec': '500g', 'price': '9.99'}]
        for name in ('红丁萝卜', '红萝卜丁'):
            with self.subTest(name=name):
                row = match_item({'name': name, 'quantity': '2', 'unit': '斤'}, records)
                self.assertEqual(row['status'], 'matched')
                self.assertEqual(row['selected'], records[0])
                self.assertEqual(row['invoice_quantity'], '2')
                self.assertEqual(row['match_basis'], 'catalog_mutual_name')

    def test_slash_size_and_different_identity_are_not_automatic_aliases(self):
        records = [{'code': 'pack', 'name': '丹丹郫县豆瓣酱（250克/袋）',
                    'category': '调料类', 'spec': '250g', 'price': '6.47'},
                   {'code': 'different', 'name': '芝麻海苔/调味裙带菜mkl',
                    'category': '方便食品', 'spec': '袋', 'price': '11.99'}]
        for name in ('袋',):
            with self.subTest(name=name):
                self.assertIsNone(match_item({'name': name, 'quantity': '2', 'unit': '袋'}, records)['selected'])
        for name in ('芝麻海苔', '调味裙带菜mkl'):
            with self.subTest(name=name):
                row = match_item({'name': name, 'quantity': '2', 'unit': '袋'}, records)
                self.assertEqual(row['status'], 'matched')
                self.assertEqual(row['selected']['code'], 'different')

    def test_mutual_names_do_not_override_a_conflicting_exact_catalog_record(self):
        records = [{'code': 'slash', 'name': '红丁萝卜/红萝卜丁', 'category': '蔬菜',
                    'spec': '500g', 'price': '9.99'},
                   {'code': 'exact', 'name': '红丁萝卜', 'category': '蔬菜',
                    'spec': '1kg', 'price': '18'}]
        row = match_item({'name': '红丁萝卜', 'quantity': '2', 'unit': '斤'}, records)
        self.assertIsNone(row['selected'])
        self.assertEqual({r['code'] for r in row['candidates']}, {'slash', 'exact'})

    def test_new_explicit_brand_keeps_unverified_reference_but_never_selects_it(self):
        records = [{'code': 'a', 'name': '西兰花', 'category': '蔬菜', 'spec': '500g', 'price': '4'}]
        row = match_item({'name': '欢乐园牌西兰花', 'quantity': '2', 'unit': '斤'}, records)
        self.assertIsNone(row['selected'])
        self.assertEqual([r['code'] for r in row['candidates']], ['a'])
        self.assertIn('品牌未证', row['candidates'][0]['match_reason'])
        self.assertIsNone(row['invoice_quantity'])
        self.assertIn('brand_unverified', {i['code'] for i in row['issues']})

    def test_unverified_brand_reference_does_not_expand_to_different_form_or_brand(self):
        records = [
            {'code': 'plain', 'name': '高粱米', 'spec': '500g', 'price': '4'},
            {'code': 'flour', 'name': '高粱米面', 'spec': '500g', 'price': '5'},
            {'code': 'other', 'name': '北大荒高粱米', 'spec': '500g', 'price': '6'},
        ]
        row = match_item({'name': '山月牌高粱米', 'quantity': '2', 'unit': '斤'}, records)
        self.assertEqual([r['code'] for r in row['candidates']], ['plain'])
        self.assertIsNone(row['selected'])
        self.assertIsNone(row['invoice_quantity'])
        # Removing the only base-name record must not leave a stale reference.
        row = match_item({'name': '山月牌高粱米', 'quantity': '2', 'unit': '斤'}, records[1:])
        self.assertEqual(row['candidates'], [])
        self.assertIsNone(row['selected'])

    def test_current_explicit_brand_record_remains_selectable(self):
        records = [{'code': 'current', 'name': '山月牌高粱米', 'spec': '500g', 'price': '8'},
                   {'code': 'plain', 'name': '高粱米', 'spec': '500g', 'price': '9'}]
        row = match_item({'name': '山月牌高粱米', 'quantity': '2', 'unit': '斤'}, records)
        self.assertEqual(row['selected']['code'], 'current')
        self.assertEqual(row['invoice_quantity'], '2')

    def test_package_references_exclude_explicit_conflicting_pack_size(self):
        records = [
            {'code': 'basis', 'name': '丹丹郫县豆瓣酱', 'spec': '1kg', 'price': '34.20'},
            {'code': 'bag', 'name': '丹丹郫县豆瓣酱（250克/袋）', 'spec': '250g', 'price': '6.47'},
            {'code': 'bucket', 'name': '丹丹郫县豆瓣酱6KG*4桶/件', 'spec': '6kg', 'price': '113.59'},
        ]
        row = match_item({'name': '丹丹郫县豆瓣酱（700克/袋）', 'notes': '',
                          'quantity': '2', 'unit': '袋'}, records)
        self.assertIsNone(row['selected'])
        self.assertEqual([r['code'] for r in row['candidates']], ['basis'])
        self.assertIsNone(row['invoice_quantity'])

    def test_new_package_and_part_constraints_filter_candidates(self):
        records = [{'code': 'a', 'name': '西兰花', 'category': '蔬菜', 'spec': '500g', 'price': '4'}]
        row = match_item({'name': '西兰花2kg整袋装', 'quantity': '1', 'unit': '袋'}, records)
        self.assertIsNone(row['selected'])
        self.assertEqual(row['candidates'], [])
        self.assertIn('packaging_unverified', {i['code'] for i in row['issues']})
        row = match_item({'name': '牛前腱', 'quantity': '2', 'unit': '斤'}, records)
        self.assertIn('part_unverified', {i['code'] for i in row['issues']})

    def test_exact_name_prefers_highest_same_spec_but_not_other_packaging(self):
        result = match_item({"name": "红肠", "quantity": "2", "unit": "斤"}, RECORDS[:-1])
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["selected"]["code"], "4")
        self.assertEqual({record["code"] for record in result["candidates"]}, {"3", "4"})
        self.assertEqual(result["invoice_quantity"], "2")

    def test_same_name_different_packaging_is_ambiguous(self):
        result = match_item({"name": "红肠", "quantity": "2", "unit": "斤"}, RECORDS)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["selected"])
        self.assertEqual({record["code"] for record in result["candidates"]}, {"3", "4", "5"})

    def test_equivalent_jin_and_500g_specs_compare_prices(self):
        records = [
            {"code": "a", "name": "花椒粒", "spec": "500g", "price": "45.29"},
            {"code": "b", "name": "花椒粒", "spec": "斤", "price": "46.00"},
        ]
        result = match_item({"name": "花椒粒", "quantity": "1", "unit": "斤"}, records)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["selected"]["code"], "b")
        self.assertEqual(result["invoice_quantity"], "1")

    def test_explicit_brand_is_not_overridden_by_higher_other_brand(self):
        result = match_item({"name": "秋林红肠", "quantity": "2", "unit": "斤"}, RECORDS)
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["selected"])
        self.assertIn("2", {record["code"] for record in result["candidates"]})
        self.assertIsNone(result["invoice_quantity"])

    def test_approved_alias_selects_exact_code_and_rejects_stale_alias(self):
        item = {"name": "秋林红肠", "quantity": "2", "unit": "斤"}
        result = match_item(item, RECORDS, aliases={"秋林红肠": "2"})
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["selected"]["code"], "2")
        self.assertEqual(result["invoice_quantity"], "2")
        stale = match_item(item, RECORDS, aliases={"秋林红肠": "missing"})
        self.assertEqual(stale["status"], "needs_review")
        self.assertIsNone(stale["selected"])

    def test_unmapped_preserves_original_quantity_and_unit(self):
        original = {"name": "刷锅", "raw_text": "刷锅5个", "quantity": "5", "unit": "个"}
        result = match_item(original, RECORDS)
        self.assertEqual(result["status"], "unmapped")
        self.assertEqual((result["quantity"], result["unit"], result["raw_text"]), ("5", "个", "刷锅5个"))
        self.assertEqual((result["candidates"], result["selected"], result["invoice_quantity"]), ([], None, None))

    def test_unknown_unit_needs_review_even_when_name_matches(self):
        result = match_item({"name": "红肠", "quantity": "2", "unit": "个"}, RECORDS[:-1])
        self.assertEqual(result["status"], "needs_review")
        self.assertIsNone(result["invoice_quantity"])

    def test_semantic_candidates_respect_brands_and_packaging(self):
        sausage = match_item({"name": "秋林红肠", "quantity": "2", "unit": "斤"}, RECORDS)
        self.assertEqual([candidate["name"] for candidate in sausage["candidates"]], ["秋林里道斯红肠"])
        corn = [
            {"code": "bag", "name": "冻玉米粒0.9kg/袋", "spec": "0.9kg"},
            {"code": "can", "name": "玉米粒425g/罐", "spec": "425g"},
        ]
        selected = match_item({"name": "玉米粒", "quantity": "2", "unit": "袋"}, corn)
        self.assertEqual([candidate["code"] for candidate in selected["candidates"]], ["bag"])
        self.assertNotEqual(selected["status"], "matched")

    def test_generic_noun_does_not_offer_sauce_or_unrelated_processed_food(self):
        records = [
            {"code": "sauce", "name": "海天排骨酱", "category": "调料类", "spec": "500g"},
            {"code": "meat", "name": "猪排骨切段", "category": "肉制品", "spec": "500g"},
            {"code": "noodle", "name": "鸡蛋挂面", "category": "面类", "spec": "袋"},
            {"code": "egg", "name": "鲜鸡蛋", "category": "蛋类", "spec": "500g"},
        ]
        ribs = match_item({"name": "排骨", "quantity": "5", "unit": "斤"}, records)
        eggs = match_item({"name": "厚皮鸡蛋", "quantity": "1", "unit": "筐"}, records)
        self.assertEqual([candidate["code"] for candidate in ribs["candidates"]], ["meat"])
        self.assertEqual([candidate["code"] for candidate in eggs["candidates"]], ["egg"])

    def test_unverified_packaging_and_specific_pickled_vegetable_remain_review(self):
        records = [
            {"code": "acid", "name": "翠花酸菜750g", "category": "调料类", "spec": "750g"},
            {"code": "case", "name": "翠花酸菜750g*10袋", "category": "调料类", "spec": "750g*10袋"},
            {"code": "fish", "name": "鱼酸菜", "category": "调料类", "spec": "400g"},
            {"code": "pickle", "name": "雪里红咸菜", "category": "调料类", "spec": "500g"},
            {"code": "meatball", "name": "安井鸡肉丸称重", "category": "方便食品", "spec": "500g"},
            {"code": "bag", "name": "正大鸡丸400g/袋", "category": "方便食品", "spec": "400g"},
        ]
        cabbage = match_item({"name": "酸菜", "quantity": "1", "unit": "箱"}, records)
        self.assertEqual([candidate["code"] for candidate in cabbage["candidates"]], ["case"])
        self.assertIn("每箱", cabbage["match_reason"])
        self.assertIsNone(cabbage["invoice_quantity"])
        exact_bag = match_item({"name": "翠花酸菜750g", "quantity": "1", "unit": "箱"}, records)
        self.assertIsNone(exact_bag["selected"])
        self.assertNotIn("acid", [candidate["code"] for candidate in exact_bag["candidates"]])
        pickle = match_item({"name": "萝卜丁咸菜", "quantity": None, "unit": None}, records)
        self.assertEqual(pickle["status"], "unmapped")
        self.assertIn("品种", pickle["match_reason"])
        ball = match_item({"name": "鸡丸", "quantity": "1", "unit": "袋"}, records)
        self.assertEqual([candidate["code"] for candidate in ball["candidates"]], ["bag"])
        self.assertIn("每袋", ball["match_reason"])

    def test_semantic_candidates_filter_explicit_brand_before_reporting(self):
        records = [
            {"code": "li", "name": "李锦记香辣酱", "category": "调料类", "spec": "205g"},
            {"code": "mei", "name": "美乐香辣酱", "category": "调料类", "spec": "350g"},
            {"code": "sun-bean", "name": "太阳岛黄豆酱", "category": "调料类", "spec": "600g"},
            {"code": "sun-garlic", "name": "太阳岛蒜蓉辣酱", "category": "调料类", "spec": "101g"},
        ]
        result = match_item({"name": "太阳岛香辣酱", "quantity": "1", "unit": "袋"}, records)
        self.assertEqual(result["status"], "unmapped")
        self.assertEqual(result["candidates"], [])
        self.assertIn("显式品牌太阳岛", result["match_reason"])
        self.assertIn("同品候选", result["match_reason"])

    def test_specific_cucumber_only_suggests_unspecified_fresh_variety(self):
        records = [
            {"code": "fresh", "name": "黄瓜", "category": "蔬菜", "spec": "500g"},
            {"code": "old", "name": "老黄瓜", "category": "蔬菜", "spec": "500g"},
            {"code": "fruit", "name": "水果黄瓜", "category": "蔬菜", "spec": "500g"},
            {"code": "pickled", "name": "酸黄瓜", "category": "调料类", "spec": "700g"},
            {"code": "dry", "name": "黄瓜干", "category": "调料类", "spec": "500g"},
            {"code": "beef", "name": "清真牛黄瓜条", "category": "肉制品", "spec": "500g"},
            {"code": "beef-brand", "name": "皓月牛肉黄瓜条", "category": "肉制品", "spec": "500g"},
        ]
        result = match_item({"name": "旱黄瓜", "quantity": "5.5", "unit": "斤"}, records)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual([candidate["code"] for candidate in result["candidates"]], ["fresh"])
        self.assertNotIn("pickled", {candidate["code"] for candidate in result["candidates"]})
        self.assertNotIn("dry", {candidate["code"] for candidate in result["candidates"]})
        self.assertNotIn("beef", {candidate["code"] for candidate in result["candidates"]})
        self.assertIn("品种不同", result["match_reason"])

    def test_romaine_only_suggests_unspecified_lettuce(self):
        records = [
            {"code": "generic", "name": "生菜", "category": "蔬菜", "spec": "500g"},
            {"code": "western", "name": "西生菜", "category": "蔬菜", "spec": "500g"},
            {"code": "ball", "name": "球生菜", "category": "蔬菜", "spec": "500g"},
            {"code": "purple", "name": "紫生菜", "category": "蔬菜", "spec": "500g"},
        ]
        result = match_item({"name": "罗马生菜", "quantity": "1", "unit": "颗"}, records)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual([candidate["code"] for candidate in result["candidates"]], ["generic"])
        self.assertIn("罗马", result["match_reason"])
        self.assertIsNone(result["selected"])

    def test_five_color_rice_does_not_offer_plain_or_seven_color_rice(self):
        records = [
            {"code": "plain", "name": "糙米", "category": "米类", "spec": "500g"},
            {"code": "seven", "name": "七色糙米", "category": "米类", "spec": "2.5kg/袋"},
        ]
        result = match_item({"name": "5色糙米", "quantity": "10", "unit": "斤"}, records)
        self.assertEqual(result["status"], "unmapped")
        self.assertEqual(result["candidates"], [])
        self.assertIn("五色", result["match_reason"])

    def test_peeled_smoky_cinnamon_only_suggests_generic_bark(self):
        records = [
            {"code": "generic", "name": "（调料）桂皮", "category": "调料类", "spec": "500g", "price": "29.41"},
            {"code": "powder", "name": "桂皮粉", "category": "调料类", "spec": "500g", "price": "31.00"},
        ]
        result = match_item({"name": "烟桂", "raw_text": "烟桂 1斤去皮的那种", "quantity": "1", "unit": "斤", "notes": "去皮的那种"}, records)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual([candidate["code"] for candidate in result["candidates"]], ["generic"])
        self.assertIn("去皮", result["match_reason"])
        self.assertIsNone(result["selected"])


if __name__ == "__main__":
    unittest.main()
