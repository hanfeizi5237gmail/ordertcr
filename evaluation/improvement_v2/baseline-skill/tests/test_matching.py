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


if __name__ == "__main__":
    unittest.main()
