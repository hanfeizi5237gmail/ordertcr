import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from order_mapping_core.units import invoice_quantity


class UnitsTests(unittest.TestCase):
    def test_jin_and_grams_convert_to_catalog_weight(self):
        record = {"code": "p", "name": "土豆", "spec": "500g", "price": "3.30"}
        self.assertEqual(invoice_quantity("1.5", "kg", record), {"quantity": "3", "price_unit": "500g"})
        self.assertEqual(invoice_quantity("3", "斤", record)["quantity"], "3")

    def test_bag_from_name_and_explicit_box_rule(self):
        bag = {"code": "b", "name": "五色糙米2.5kg/袋", "spec": "袋"}
        self.assertEqual(invoice_quantity("2", "袋", bag), {"quantity": "2", "price_unit": "袋"})
        tofu = {"code": "t", "name": "北大荒老豆腐", "spec": "490g"}
        with self.assertRaises(ValueError):
            invoice_quantity("7", "盒", tofu)
        rules = {"t": {"price_unit": "盒", "request_units": {"盒": "1"}, "grams_per_unit": "490", "allow_fractional": False}}
        self.assertEqual(invoice_quantity("7", "盒", tofu, rules), {"quantity": "7", "price_unit": "盒"})

    def test_count_to_weight_and_fractional_whole_pack_rejected(self):
        fish = {"code": "f", "name": "鲜活鲤鱼", "spec": "500g"}
        with self.assertRaises(ValueError):
            invoice_quantity("5", "条", fish)
        package = {"code": "pack", "name": "腐竹1kg", "spec": "1kg"}
        rules = {"pack": {"price_unit": "袋", "grams_per_unit": "1000", "allow_fractional": False}}
        with self.assertRaises(ValueError):
            invoice_quantity("3", "斤", package, rules)

    def test_known_packs_and_invalid_quantity(self):
        record = {"code": "can", "name": "玉米粒425g/罐", "spec": "425g"}
        self.assertEqual(invoice_quantity("6", "罐", record), {"quantity": "6", "price_unit": "罐"})
        with self.assertRaises(ValueError):
            invoice_quantity(None, "罐", record)
        with self.assertRaises(ValueError):
            invoice_quantity("0", "罐", record)

    def test_standard_kilogram_alias_and_volume_ratio(self):
        mass = {"code": "mass", "name": "土豆", "spec": "500g"}
        volume = {"code": "volume", "name": "酱油", "spec": "500ml"}
        self.assertEqual(invoice_quantity("2", "千克", mass)["quantity"], "4")
        self.assertEqual(invoice_quantity("1", "L", volume)["quantity"], "2")

    def test_jin_catalog_spec_accepts_grams(self):
        record = {"code": "j", "name": "花椒粒", "spec": "斤"}
        self.assertEqual(invoice_quantity("1000", "g", record), {"quantity": "2", "price_unit": "斤"})

    def test_invalid_fractional_rule_does_not_allow_half_pack(self):
        record = {"code": "p", "name": "腐竹1kg/袋", "spec": "1kg"}
        with self.assertRaises(ValueError):
            invoice_quantity("3", "斤", record, {"p": {"allow_fractional": "false"}})


if __name__ == "__main__":
    unittest.main()
