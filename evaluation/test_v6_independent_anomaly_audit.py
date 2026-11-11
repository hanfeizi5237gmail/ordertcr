"""Contract checks for the isolated anomaly evaluator; no business artifacts are read."""
import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).with_name('v6_independent_anomaly_audit.py')


class IndependentAuditTests(unittest.TestCase):
    def test_missing_candidate_and_unsafe_amount_are_both_reported(self):
        self.assertTrue(SCRIPT.exists(), 'independent anomaly evaluator must exist')
        spec = importlib.util.spec_from_file_location('independent_audit', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        expected = {'id': 'white_sugar', 'candidates': [
            {'material_code': '1', 'name': '白糖', 'spec': '500g', 'listed_price': '2', 'sheet': 'Sheet0', 'row': 5},
            {'material_code': '2', 'name': '白砂糖', 'spec': '500g', 'listed_price': '3', 'sheet': 'Sheet0', 'row': 6}]}
        actual = {'company_id': 'a', 'shipping_date': '2026-11-03', 'row': {
            'quantity': '2', 'unit': '袋', 'status': 'matched', 'amount': '3.8', 'finance_amount': '3.8',
            'selected': None, 'issues': [], 'candidates': [{'code': '1', 'name': '白糖', 'spec': '500g',
                'price': '2', 'sheet': 'Sheet0', 'row': 5, 'match_reason': '待核袋重'}]}}
        outcome = module.check_case(expected, actual, '2', '袋', 'a', '2026-11-03',
                                    '待核袋重', '待核袋重')
        self.assertIn('candidate_set_mismatch', outcome['errors'])
        self.assertIn('unexpected_automatic_amount', outcome['errors'])
        self.assertEqual(outcome['missing_candidates'], ['2'])

    def test_candidate_reason_must_appear_in_both_native_comments(self):
        self.assertTrue(SCRIPT.exists(), 'independent anomaly evaluator must exist')
        spec = importlib.util.spec_from_file_location('independent_audit', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        expected = {'id': 'white_sugar', 'candidates': [{'material_code': '1', 'name': '白糖',
            'spec': '500g', 'listed_price': '2', 'sheet': 'Sheet0', 'row': 5}]}
        actual = {'company_id': 'a', 'shipping_date': '2026-11-03', 'row': {
            'quantity': '2', 'unit': '袋', 'status': 'needs_review', 'amount': None, 'finance_amount': None,
            'selected': None, 'issues': [], 'candidates': [{'code': '1', 'name': '白糖', 'spec': '500g',
                'price': '2', 'sheet': 'Sheet0', 'row': 5, 'match_reason': '待核袋重'}]}}
        outcome = module.check_case(expected, actual, '2', '袋', 'a', '2026-11-03', '待核袋重', '')
        self.assertIn('candidate_reason_missing_finance_comment', outcome['errors'])
        self.assertNotIn('candidate_set_mismatch', outcome['errors'])


if __name__ == '__main__':
    unittest.main()
