import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from order_mapping_core.parsing import parse_chat


class ParseChatTests(unittest.TestCase):
    def test_three_chat_samples_keep_every_product_and_dates(self):
        original = Path(__file__).resolve().parents[3] / "original"
        if not original.is_dir():
            self.skipTest("原始业务样例未随可复制技能包分发；仓库内另做三批回放验证")
        for filename, expected in (
            ("星月站11.3下单.txt", 42),
            ("学院路站11.3下单.txt", 64),
            ("松北大道站11.3下单.txt", 53),
        ):
            with self.subTest(filename=filename):
                text = (original / filename).read_text(encoding="utf-8")
                items = parse_chat(text, filename)
                self.assertEqual(len(items), expected)
                self.assertTrue(all(item["raw_text"] == text[item["char_start"]:item["char_end"]] for item in items))
                self.assertTrue(all(item["source_name"] == filename for item in items))
        star = parse_chat((original / "星月站11.3下单.txt").read_text(encoding="utf-8"))
        self.assertEqual([item["date_context"] for item in star if item["name"] in ("油豆角", "茄子")], ["11月4", "11月5号"])
        self.assertEqual(sum(item["name"] == "菜花" for item in star), 2)

    def test_delimiters_chinese_quantity_names_and_notes(self):
        text = "5色糙米10斤、 一品鲜酱油一桶；鲤鱼5条收拾好，豆油2桶\n孜然粉2\n萝卜丁咸菜大袋的"
        items = parse_chat(text)
        self.assertEqual([(x["name"], x["quantity"], x["unit"]) for x in items], [
            ("5色糙米", "10", "斤"), ("一品鲜酱油", "1", "桶"),
            ("鲤鱼", "5", "条"), ("豆油", "2", "桶"),
            ("孜然粉", "2", None), ("萝卜丁咸菜", None, None),
        ])
        self.assertIn("收拾好", items[2]["notes"])
        self.assertIn("missing_unit", [issue["code"] for issue in items[4]["issues"]])
        self.assertIn("missing_quantity", [issue["code"] for issue in items[5]["issues"]])

    def test_date_header_and_space_separated_items(self):
        items = parse_chat("11月4日\n大米1袋 鸡蛋两斤\n大米1袋")
        self.assertEqual(len(items), 3)
        self.assertEqual([x["quantity"] for x in items], ["1", "2", "1"])
        self.assertTrue(all(x["date_context"] == "11月4日" for x in items))
        self.assertEqual([x["source_line"] for x in items], [2, 2, 3])

    def test_chinese_decimal_and_quantity_after_a_numeric_product_name(self):
        items = parse_chat("五花肉三点五斤；5色糙米10斤")
        self.assertEqual([(x["name"], x["quantity"], x["unit"]) for x in items],
                         [("五花肉", "3.5", "斤"), ("5色糙米", "10", "斤")])

    def test_chinese_integer_with_zero_does_not_drop_final_digit(self):
        items = parse_chat("咸鸭蛋一百零二个")
        self.assertEqual((items[0]["name"], items[0]["quantity"], items[0]["unit"]),
                         ("咸鸭蛋", "102", "个"))

    def test_large_package_and_date_range_context(self):
        items = parse_chat("冷面1大包\n11月3号到5号早上的菜\n菜花6斤")
        self.assertEqual((items[0]["name"], items[0]["quantity"], items[0]["unit"]),
                         ("冷面", "1", "大包"))
        self.assertEqual(items[1]["date_context"], "11月3号到5号早上")

    def test_processing_and_packaging_notes_before_or_without_quantity(self):
        items = parse_chat("猪肘去毛5个\n生猪手撩毛8个\n萝卜丁咸菜大袋的")
        self.assertEqual([(item["name"], item["notes"]) for item in items],
                         [("猪肘", "去毛"), ("生猪手", "撩毛"),
                          ("萝卜丁咸菜", "大袋的")])
        self.assertIsNone(items[2]["quantity"])

    def test_addition_with_reference_keeps_event_but_not_in_product_name(self):
        item = parse_chat("松北大道站再加这样的刷锅5个")[0]
        self.assertEqual((item["name"], item["quantity"], item["unit"]),
                         ("刷锅", "5", "个"))
        self.assertEqual(item["event"], "追加")
        self.assertIn("这样的", item["notes"])
        self.assertEqual(item["raw_text"], "松北大道站再加这样的刷锅5个")

    def test_thick_shell_egg_quality_stays_in_name_and_delivery_notes(self):
        items = parse_chat("厚皮鸡蛋1筐\n鸡蛋2斤\n小黑茄子13斤")
        self.assertEqual((items[0]["name"], items[0]["quantity"], items[0]["unit"]),
                         ("厚皮鸡蛋", "1", "筐"))
        self.assertIn("厚皮", items[0]["notes"])
        self.assertEqual(items[0]["raw_text"], "厚皮鸡蛋1筐")
        self.assertEqual(items[1]["notes"], "")
        self.assertEqual((items[2]["name"], items[2]["notes"]), ("小黑茄子", ""))


if __name__ == "__main__":
    unittest.main()
