"""Adversarial evaluator contract checks for both KB16 scorer versions."""
import unittest

import v7_kb16_audit as v1
import v7_kb16_v2_audit as v2
from test_v7_kb16_audit import event, item


class ScorerFailurePaths(unittest.TestCase):
    def test_unexpected_output_does_not_expand_input_denominator(self):
        expected = {"kind": "conditional_match", "automatic": True, "automatic_correct": True,
                    "correct": True, "review_correct": False, "control_correct": False, "control_safe": False}
        extra = {"kind": "unexpected_output", "automatic": True, "automatic_correct": False,
                 "correct": False, "review_correct": False, "control_correct": False, "control_safe": False}
        for scorer in (v1, v2):
            with self.subTest(scorer=scorer.__name__):
                summary = scorer.metric([expected, extra])
                self.assertEqual(summary["events"], 1)
                self.assertEqual(summary["automatic"], 2)
                self.assertEqual(summary["A"], 50.0)
                self.assertEqual(summary["B"], 100.0)

    def test_initial_missing_independent_quantity_and_price_cannot_self_certify(self):
        for scorer in (v1, v2):
            with self.subTest(scorer=scorer.__name__):
                expected = event("initial", assumed_selected_sku="S1")
                # V2 initial events contain no assumed_active_total_500g.
                expected.pop("expected_catalog_item")
                actual = item(invoice_quantity=None, price_unit="500g", amount=None, finance_amount=None)
                scored = scorer.score_event(expected, actual, {"星月站": "c1"}, "冷冻鲷鱼片2斤")
                self.assertTrue(scored["automatic"])
                self.assertFalse(scored["automatic_correct"])
                self.assertFalse(scored["control_correct"])

    def test_initial_price_cannot_be_derived_from_actual_selected(self):
        for scorer in (v1, v2):
            with self.subTest(scorer=scorer.__name__):
                expected = event("initial", assumed_selected_sku="S1", assumed_active_total_500g="2")
                expected.pop("expected_catalog_item")
                actual = item(selected={"code":"S1", "name":"错", "spec":"500g", "price":"1"},
                              unit_price="0.95", amount="1.90", finance_amount="1.90")
                scored = scorer.score_event(expected, actual, {"星月站": "c1"}, "冷冻鲷鱼片2斤")
                self.assertFalse(scored["automatic_correct"])

    def test_matched_active_append_and_wrong_control_are_counted_in_a(self):
        for scorer in (v1, v2):
            for kind, action in (("append", "追加"), ("change", "改量"), ("cancel", "取消")):
                with self.subTest(scorer=scorer.__name__, kind=kind):
                    row = item(event=action)["row"]
                    self.assertTrue(scorer.is_automatic(row))

    def test_independently_proven_append_can_be_automatic(self):
        for scorer in (v1, v2):
            with self.subTest(scorer=scorer.__name__):
                expected = event("append", assumed_selected_sku="S1", expected_predecessor_id="P1")
                actual = item(event="追加", predecessor_id="P1")
                scored = scorer.score_event(expected, actual, {"星月站": "c1"}, "冷冻鲷鱼片2斤")
                self.assertTrue(scored["automatic"])
                self.assertTrue(scored["automatic_correct"], scored["errors"])
                actual["row"]["predecessor_id"] = "wrong"
                self.assertFalse(scorer.score_event(expected, actual, {"星月站": "c1"}, "冷冻鲷鱼片2斤")["automatic_correct"])

    def test_control_review_with_invoice_quantity_is_not_safe(self):
        for scorer in (v1, v2):
            expected = event("cancel")
            actual = item(status="needs_review", event="取消", event_state="pending", amount=None, finance_amount=None, invoice_quantity="2")
            self.assertFalse(scorer.score_event(expected, actual, {"星月站": "c1"}, "冷冻鲷鱼片2斤")["control_safe"])

    def test_repeated_text_cannot_align_to_wrong_source_line(self):
        chat = "冷冻鲷鱼片2斤\n冷冻鲷鱼片2斤"
        expected = event(source_line=1)
        actual = item(source_line=1, char_start=8, char_end=15)
        for scorer in (v1, v2):
            pairs, extras = scorer.align_events([expected], [actual], chat)
            self.assertIsNone(pairs[0][1])
            self.assertEqual(len(extras), 1)

    def test_incomplete_or_duplicated_execution_inventory_rejected(self):
        for scorer in (v1, v2):
            bad = {"status": "runs_complete_before_audit", "phase": "all_runs_completed_before_expected_read", "source_equal": True,
                   "executions": [{"seed": 8801, "mode": "shared", "register_rc": 0, "run_rc": 0}] * 9}
            with self.subTest(scorer=scorer.__name__):
                with self.assertRaises(ValueError):
                    scorer.validate_execution_inventory(bad, {"files_sha256": {}})

    def test_wrong_control_event_type_is_not_safe(self):
        for scorer in (v1, v2):
            with self.subTest(scorer=scorer.__name__):
                expected = event("cancel")
                actual = item(status="needs_review", event="追加", event_state="pending", amount=None,
                              finance_amount=None, invoice_quantity=None)
                scored = scorer.score_event(expected, actual, {"星月站": "c1"}, "冷冻鲷鱼片2斤")
                self.assertFalse(scored["control_safe"])

    def test_review_status_must_be_a_review_or_unmapped_state(self):
        for scorer in (v1, v2):
            with self.subTest(scorer=scorer.__name__):
                expected = event("unknown_brand")
                actual = item(status="corrupt", amount=None, finance_amount=None, invoice_quantity=None)
                scored = scorer.score_event(expected, actual, {"星月站": "c1"}, "冷冻鲷鱼片2斤")
                self.assertFalse(scored["review_correct"])


if __name__ == "__main__":
    unittest.main()
