import importlib
import sys
import unittest
from decimal import Decimal, localcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class MoneyTests(unittest.TestCase):
    def module(self):
        try:
            return importlib.import_module("order_mapping_core.money")
        except ModuleNotFoundError as exc:
            self.fail(f"金额契约尚未实现: {exc}")

    def test_exact_and_line_cent_keep_unit_precision(self):
        money = self.module()
        self.assertEqual(money.calculate("4.49", "0.95", "4", "sample_exact"),
                         {"unit_price": "4.2655", "amount": "17.062"})
        self.assertEqual(money.calculate("4.49", "0.95", "4", "line_cent"),
                         {"unit_price": "4.2655", "amount": "17.06"})

    def test_half_cent_rounds_up_and_not_bankers_rounding(self):
        money = self.module()
        self.assertEqual(money.calculate("1.05", "1", "5.5", "line_cent")["amount"], "5.78")
        self.assertEqual(money.calculate("1.25", "1", "3.9", "line_cent")["amount"], "4.88")

    def test_invalid_decimal_or_policy_cannot_become_zero(self):
        money = self.module()
        for price, discount, qty, policy in [
            ("NaN", "0.95", "1", "line_cent"),
            ("1", "Infinity", "1", "line_cent"),
            ("-1", "0.95", "1", "line_cent"),
            ("1", "0.95", "0", "line_cent"),
            ("1", "0.95", "-1", "line_cent"),
            ("1", "0.95", "1", "unknown"),
        ]:
            with self.subTest(price=price, discount=discount, qty=qty, policy=policy):
                with self.assertRaises(ValueError):
                    money.calculate(price, discount, qty, policy)

    def test_natural_discount_is_normalized_without_percent_confusion(self):
        money = self.module()
        for text in ["0.95", "95%", "9.5折"]:
            self.assertEqual(money.discount_value(text), "0.95")
        self.assertEqual(money.discount_value("0.95%"), "0.0095")
        with self.assertRaises(ValueError):
            money.discount_value("95")

    def test_accepted_high_precision_inputs_do_not_silently_round_exact_amounts(self):
        money = self.module()
        price = "1234567890123456789.12345"
        discount = "0.123456789012345678901234"
        with localcontext() as context:
            context.prec = 100
            expected = Decimal(price) * Decimal(discount) * Decimal(price)
        self.assertEqual(Decimal(money.calculate(price, discount, price, "sample_exact")["amount"]), expected)

    def test_exact_output_can_be_summed_without_input_precision_limit(self):
        money = self.module()
        price = "1234567890123456789.12345"
        discount = "0.123456789012345678901234"
        amount = money.calculate(price, discount, price, "sample_exact")["amount"]
        with localcontext() as context:
            context.prec = 128
            expected = Decimal(amount) * 2
        self.assertEqual(money.sum_amounts([amount, amount]), expected)


if __name__ == "__main__":
    unittest.main()
