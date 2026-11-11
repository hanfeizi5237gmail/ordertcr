import unittest
from decimal import Decimal

from v7_kb16_audit import align_events, metric, score_event


def event(kind="conditional_match", **overrides):
    value = {
        "event_id": "E1",
        "kind": kind,
        "company": "星月站",
        "date": "2027-02-01",
        "source_line": 1,
        "source_line_text": "冷冻鲷鱼片2斤",
        "concept": "鲷鱼片",
        "quantity": "2",
        "unit": "斤",
        "quantity_500g": "2",
        "expected_catalog_item": {
            "sku": "S1",
            "name": "冷冻鲷鱼片",
            "spec": "500g",
            "price": "18.99",
        },
    }
    value.update(overrides)
    return value


def item(**row_overrides):
    row = {
        "source_line": 1,
        "raw_text": "冷冻鲷鱼片2斤",
        "char_start": 0,
        "char_end": 7,
        "status": "matched",
        "event_state": "active",
        "event": None,
        "selected": {"code": "S1", "name": "冷冻鲷鱼片", "spec": "500g", "price": "18.99"},
        "quantity": "2",
        "unit": "斤",
        "invoice_quantity": "2",
        "price_unit": "500g",
        "discount": "0.95",
        "unit_price": "18.0405",
        "amount": "36.0810",
        "finance_amount": "36.0810",
    }
    row.update(row_overrides)
    return {"company_id": "c1", "shipping_date": "2027-02-01", "row": row}


class KB16AuditContractTests(unittest.TestCase):
    def test_metric_uses_actual_automatic_rows_and_keeps_all_events_in_b(self):
        rows = [
            {"automatic": True, "automatic_correct": True, "correct": True, "review_correct": False, "control_correct": False, "control_safe": False},
            # A review event that was wrongly auto matched is still in A's denominator.
            {"automatic": True, "automatic_correct": False, "correct": False, "review_correct": False, "control_correct": False, "control_safe": False},
            {"automatic": False, "automatic_correct": False, "correct": True, "review_correct": True, "control_correct": False, "control_safe": False},
        ]
        result = metric(rows)
        self.assertEqual(result["automatic"], 2)
        self.assertEqual(result["automatic_correct"], 1)
        self.assertEqual(result["events"], 3)
        self.assertEqual(result["A"], 50.0)
        self.assertAlmostEqual(result["B"], 33.3333)
        self.assertAlmostEqual(result["business_processing_rate"], 66.6667)
        self.assertEqual(result["C_status"], "unknown: candidate_completeness is undefined")

    def test_review_auto_match_is_an_automatic_error(self):
        review = event("unknown_brand", source_line_text="火星牌冷冻鲷鱼片2斤")
        actual = item(raw_text="火星牌冷冻鲷鱼片2斤", selected={"code": "S1"})
        actual["row"].update({"char_end": 10, "amount": None, "finance_amount": None, "invoice_quantity": None})
        scored = score_event(review, actual, {"星月站": "c1"}, "火星牌冷冻鲷鱼片2斤")
        self.assertTrue(scored["automatic"])
        self.assertFalse(scored["automatic_correct"])
        self.assertFalse(scored["review_correct"])
        self.assertIn("review_automatic", scored["errors"])

    def test_conditional_checks_discount_unit_price_and_amount(self):
        scored = score_event(event(), item(), {"星月站": "c1"}, "冷冻鲷鱼片2斤")
        self.assertTrue(scored["automatic"])
        self.assertTrue(scored["automatic_correct"])
        self.assertTrue(scored["correct"])
        self.assertEqual(scored["errors"], [])

        bad = item(discount="0.90", unit_price="18.99", amount="0", finance_amount="0")
        scored_bad = score_event(event(), bad, {"星月站": "c1"}, "冷冻鲷鱼片2斤")
        self.assertFalse(scored_bad["automatic_correct"])
        self.assertTrue({"discount", "unit_price", "amount"}.issubset(scored_bad["errors"]))

    def test_initial_control_can_be_priced_only_when_all_fields_match(self):
        control = event(
            "initial",
            assumed_selected_sku="S1",
            assumed_active_total_500g="2",
            source_line_text="初始鲷鱼片2斤",
        )
        actual = item(raw_text="初始鲷鱼片2斤")
        actual["row"]["char_end"] = 7
        scored = score_event(control, actual, {"星月站": "c1"}, "初始鲷鱼片2斤")
        self.assertTrue(scored["automatic"])
        self.assertTrue(scored["automatic_correct"])
        self.assertTrue(scored["control_correct"])
        self.assertTrue(scored["correct"])

        bad = item(raw_text="初始鲷鱼片2斤", amount=None, finance_amount=None, invoice_quantity=None, price_unit=None)
        bad["row"]["char_end"] = 7
        scored_bad = score_event(control, bad, {"星月站": "c1"}, "初始鲷鱼片2斤")
        self.assertFalse(scored_bad["control_correct"])
        self.assertFalse(scored_bad["control_safe"])

    def test_append_change_cancel_are_scored_for_safety_separately(self):
        for kind, event_text, event_name in (
            ("append", "追加鲷鱼片1斤", "追加"),
            ("change", "改量鲷鱼片", "改量"),
            ("cancel", "取消鲷鱼片", "取消"),
        ):
            control = event(kind, source_line_text=event_text)
            actual = item(raw_text=event_text, status="needs_review", event_state="active" if kind == "append" else "pending", event=event_name, amount=None, finance_amount=None, invoice_quantity=None)
            actual["row"]["char_end"] = len(event_text)
            scored = score_event(control, actual, {"星月站": "c1"}, event_text)
            self.assertTrue(scored["control_safe"], kind)
            self.assertTrue(scored["control_correct"], kind)
            self.assertFalse(scored["automatic"], kind)

    def test_alignment_is_one_to_one_on_exact_span_and_text(self):
        chat = "甲\n鲷鱼片2斤\n乙"
        expected = [
            event(source_line=2, source_line_text="鲷鱼片2斤", event_id="E1"),
            event(source_line=2, source_line_text="鲷鱼片2斤", event_id="E2"),
        ]
        actual = [
            item(raw_text="鲷鱼片2斤", source_line=2, char_start=2, char_end=7),
            item(raw_text="鲷鱼片2斤", source_line=2, char_start=2, char_end=7),
        ]
        pairs, extras = align_events(expected, actual, chat)
        self.assertIsNone(pairs[0][1])
        self.assertEqual(pairs[0][2], "multiple_matching_outputs")
        self.assertIsNone(pairs[1][1])
        self.assertEqual(len(extras), 2)

        expected_one = [event(source_line=2, source_line_text="鲷鱼片2斤", event_id="E1")]
        actual_one = [item(raw_text="鲷鱼片2斤", source_line=2, char_start=2, char_end=7)]
        pairs, extras = align_events(expected_one, actual_one, chat)
        self.assertIsNotNone(pairs[0][1])
        self.assertEqual(extras, [])

    def test_missing_and_unexpected_are_counted_and_matched_unexpected_is_automatic(self):
        missing = score_event(event(), None, {"星月站": "c1"}, "冷冻鲷鱼片2斤")
        self.assertIn("missing_output", missing["errors"])
        unexpected = {
            "kind": "unexpected_output",
            "automatic": True,
            "automatic_correct": False,
            "correct": False,
            "review_correct": False,
            "control_correct": False,
            "control_safe": False,
        }
        summary = metric([missing, unexpected])
        self.assertEqual(summary["automatic"], 1)
        self.assertEqual(summary["automatic_correct"], 0)
        self.assertEqual(summary["events"], 1)


if __name__ == "__main__":
    unittest.main()
