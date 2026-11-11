"""Focused regression checks for v6 replay control scoring and integrity gates."""
import unittest
import json
import hashlib
from pathlib import Path

import v6_replay_audit as audit


class ReplayAuditTests(unittest.TestCase):
    def setUp(self):
        self.case = {'case_id': 'test', 'scenario': 'control', 'seed': 1, 'split': 'development',
                     'raw_text': '面2斤'}
        self.record = {'code': '1', 'name': '面', 'spec': '500g', 'price': '5'}
        self.expected = {'action': 'auto', 'company': 'x', 'quantity': '2', 'unit': '斤',
                         'code': '1', 'name': '面', 'spec': '500g', 'invoice_quantity': '2',
                         'event_state': 'superseded', 'amount_exact': None, 'amount_in_subtotal': False}
        self.row = {'raw_text': '面2斤', 'char_start': 0, 'char_end': 3, 'status': 'needs_review',
                    'event_state': 'superseded', 'quantity': '2', 'unit': '斤',
                    'selected': self.record, 'invoice_quantity': '2', 'amount': None,
                    'finance_amount': None, 'issues': []}

    def score(self, expected=None, row=None):
        return audit.score_event(expected or self.expected,
                                 {'company_id': 'x', 'row': row or self.row},
                                 self.case, {'1': self.record})

    def test_superseded_and_inactive_are_retained_with_blank_amount(self):
        for state in ('superseded', 'inactive'):
            with self.subTest(state=state):
                expected = {**self.expected, 'event_state': state}
                row = {**self.row, 'event_state': state}
                result = self.score(expected, row)
                self.assertEqual([], result['errors'])
                self.assertFalse(result['automatic_correct'])
                self.assertTrue(result['control_handled_correct'])
                self.assertIn('amount', self.score(expected, {**row, 'amount': '9'})['errors'])
                self.assertIn('finance_amount', self.score(expected, {**row, 'finance_amount': '9'})['errors'])

    def test_resolved_cancellation_accepts_control_record_without_candidates(self):
        expected = {'action': 'manual', 'company': 'x', 'quantity': None, 'unit': None,
                    'event_state': 'control', 'candidate_codes': ['1'],
                    'required_issue_codes': ['cancellation'], 'amount_exact': None,
                    'amount_in_subtotal': False}
        row = {**self.row, 'quantity': None, 'unit': None, 'selected': None,
               'event_state': 'cancellation_record', 'event': '取消',
               'event_resolution': 'unique_text_predecessor', 'predecessor_line_id': 'line-1',
               'issues': [{'code': 'cancellation_record'}], 'candidates': []}
        scored = self.score(expected, row)
        self.assertEqual([], scored['errors'])
        self.assertTrue(scored['control_handled_correct'])
        self.assertFalse(scored['manual_correct'])
        self.assertTrue(self.score(expected, {**row, 'predecessor_line_id': None})['errors'])

    def test_duplicate_is_unresolved_with_issue_and_blank_amount(self):
        expected = {**self.expected, 'event_state': 'duplicate_event',
                    'required_issue_codes': ['duplicate_event']}
        row = {**self.row, 'event_state': 'active', 'issues': [{'code': 'duplicate_event'}]}
        self.assertEqual([], self.score(expected, row)['errors'])
        self.assertTrue(self.score(expected, {**row, 'issues': []})['errors'])
        self.assertTrue(self.score(expected, {**row, 'amount': '9'})['errors'])

    def test_pending_is_reported_for_all_actions_even_when_output_missing(self):
        for action in ('auto', 'manual', 'undefined'):
            expected = {**self.expected, 'action': action, 'control_expected_pending': True}
            result = audit.score_event(expected, None, self.case, {'1': self.record})
            with self.subTest(action=action):
                self.assertIn('missing_output', result['errors'])

    def test_gate_rejects_all_integrity_errors_with_equal_counts(self):
        self.assertTrue(hasattr(audit, 'build_verification_gate'))
        runs = [{'input_events': 1, 'output_events': 1}]
        for error in ('unexpected_output', 'missing_output', 'source_trace',
                      'multiple_outputs_in_source_span', 'pending_contract'):
            row = {'expected_action': 'undefined', 'errors': [error]}
            with self.subTest(error=error):
                result = audit.build_verification_gate([row], runs, [], 0, True)
                self.assertFalse(result['verified'])
                self.assertTrue(result['reasons'])

    def test_expected_pending_control_counts_as_manual_handling(self):
        expected = {**self.expected, 'action': 'manual', 'control_expected_pending': True,
                    'event_state': 'active_or_unresolved', 'candidate_codes': ['1'],
                    'candidate_set_status': 'human_witness_not_exhaustive'}
        row = {**self.row, 'status': 'needs_review', 'issues': [{'code': 'event_pending'}],
               'event': '追加', 'event_state': 'pending', 'candidates': [self.record],
               'selected': None, 'amount': None, 'finance_amount': None}
        result = self.score(expected, row)
        self.assertEqual([], result['errors'])
        self.assertFalse(result['manual_correct'])
        self.assertTrue(result['control_handled_correct'])

    def test_pending_auto_retains_known_identity_without_unsafe_amount(self):
        expected = {**self.expected, 'control_expected_pending': True, 'event_state': None}
        row = {**self.row, 'event_state': 'active', 'issues': [{'code': 'event_pending'}]}
        result = self.score(expected, row)
        self.assertEqual([], result['errors'])
        self.assertTrue(result['control_handled_correct'])
        self.assertFalse(result['automatic_correct'])
        self.assertIn('pending_contract', self.score(expected, {**row, 'issues': []})['errors'])
        self.assertIn('pending_contract', self.score(expected, {**row, 'amount': '9'})['errors'])

    def test_active_change_is_control_not_automatic_output(self):
        expected = {**self.expected, 'event_state': 'active', 'amount_exact': '9.5',
                    'amount_in_subtotal': True}
        row = {**self.row, 'event_state': 'active', 'status': 'matched', 'event': '改量',
               'discount': '0.95', 'unit_price': '4.75', 'amount': '9.5', 'finance_amount': '9.5'}
        result = self.score(expected, row)
        self.assertEqual([], result['errors'])
        self.assertFalse(result['automatic'])
        self.assertFalse(result['automatic_correct'])

    def test_score_saved_rows_uses_frozen_expectation_and_preserves_integrity_error(self):
        expected = {**self.expected, 'source_span': [0, 3]}
        case = {**self.case, 'expected_events': [expected]}
        stored = {'mode': 'shared', 'case_id': 'test', 'expected': expected,
                  'actual': {'company_id': 'x', 'row': self.row},
                  'errors': ['multiple_outputs_in_source_span']}
        result = audit.score_saved_rows([stored], [case], {('shared', 'test'): {'1': self.record}})
        self.assertIn('multiple_outputs_in_source_span', result[0]['errors'])
        altered = {**stored, 'expected': {**expected, 'quantity': '999'}}
        with self.assertRaises(ValueError):
            audit.score_saved_rows([altered], [case], {('shared', 'test'): {'1': self.record}})

    def test_automatic_uses_actual_state_not_oracle_state(self):
        row = {**self.row, 'status': 'matched', 'event_state': 'active',
               'discount': '0.95', 'unit_price': '4.75', 'amount': '9.5'}
        result = self.score(self.expected, row)
        self.assertTrue(result['automatic'])
        self.assertFalse(result['automatic_correct'])
        self.assertFalse(result['control_handled_correct'])

    def test_correct_control_enters_c_without_double_counting(self):
        control = self.score()
        ordinary = {'errors': [], 'automatic': True, 'automatic_correct': True,
                    'manual_correct': False, 'control_handled_correct': False,
                    'expected_action': 'auto'}
        result = audit.summarize([control, ordinary])
        self.assertEqual(result['control_handled_correct'], 1)
        self.assertEqual(result['C'], 100.0)
        self.assertEqual(result['automatic'], 1)
        self.assertEqual(result['B'], 50.0)

    def test_active_or_unresolved_does_not_accept_inactive(self):
        expected = {**self.expected, 'action': 'undefined', 'event_state': 'active_or_unresolved'}
        self.assertIn('event_state', self.score(expected, {**self.row, 'event_state': 'inactive'})['errors'])

    def test_summary_keeps_undefined_automatic_rows_visible(self):
        rows = [
            {'errors': [], 'automatic': True, 'automatic_correct': True,
             'manual_correct': False, 'expected_action': 'auto'},
            {'errors': [], 'automatic': True, 'automatic_correct': False,
             'manual_correct': False, 'expected_action': 'undefined'},
        ]
        result = audit.summarize(rows)
        self.assertEqual(result['automatic_undefined'], 1)
        self.assertEqual(result['known_automatic'], 1)
        self.assertEqual(result['A_known'], 100.0)
        self.assertEqual(result['A'], 50.0)


class AcceptanceGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.oracle = json.loads((Path(__file__).parent / 'v6/oracle/oracle_v2.json').read_text())
        cls.rows, cls.runs = [], []
        for mode in audit.MODES:
            for case in cls.oracle['cases']:
                cls.runs.append({'mode': mode, 'case_id': case['case_id'],
                                 'input_events': len(case['expected_events']),
                                 'output_events': len(case['expected_events'])})
                for expected in case['expected_events']:
                    cls.rows.append({'mode': mode, 'case_id': case['case_id'],
                                     'seed': case['seed'], 'scenario': case['scenario'],
                                     'company': expected.get('company'),
                                     'source_event_id': expected.get('source_event_id'),
                                     'expected_action': 'auto', 'expected': expected,
                                     'automatic': True, 'automatic_correct': True,
                                     'manual_correct': False, 'control_handled_correct': False,
                                     'pending_expected': False, 'errors': []})

    def gate(self, rows=None, runs=None, **kwargs):
        return audit.build_verification_gate(self.rows if rows is None else rows,
                                             self.runs if runs is None else runs,
                                             [], 0, True, oracle=self.oracle, **kwargs)

    def test_empty_and_partial_coverage_never_verify(self):
        self.assertFalse(self.gate([], [])['acceptance_verified'])
        for name, predicate in (
            ('one_mode', lambda x: x['mode'] == audit.MODES[0]),
            ('one_seed', lambda x: x.get('seed') == 6108),
            ('missing_scenario', lambda x: x['scenario'] != 'single_station_reorder'),
        ):
            rows = [r for r in self.rows if predicate(r)]
            with self.subTest(name=name):
                self.assertFalse(self.gate(rows=rows)['acceptance_verified'])

    def test_split_integrity_passes_without_other_split(self):
        for split in ('development', 'holdout'):
            cases = {c['case_id'] for c in self.oracle['cases'] if c['split'] == split}
            rows = [r for r in self.rows if r['case_id'] in cases]
            runs = [r for r in self.runs if r['case_id'] in cases]
            result = self.gate(rows=rows, runs=runs, split=split)
            self.assertTrue(result['integrity_passed'], result['coverage_errors'])
            self.assertFalse(result['verified'])
            self.assertIn('aggregate_three_seeds_required', result['reasons'])

    def test_aggregate_validates_each_split_and_oracle_hash(self):
        reports = []
        for split in ('development', 'holdout'):
            ids = {c['case_id'] for c in self.oracle['cases'] if c['split'] == split}
            reports.append({'split': split, 'oracle_sha256': 'same',
                            'scorer_sha256': hashlib.sha256(Path(audit.__file__).read_bytes()).hexdigest(),
                            'rows': [r for r in self.rows if r['case_id'] in ids],
                            'runs': [r for r in self.runs if r['case_id'] in ids],
                            'execution_errors': [], 'audit_error_count': 0,
                            'original_sha_unchanged': True})
        gate = audit.aggregate_reports(self.oracle, 'same', reports)
        self.assertTrue(gate['integrity_passed'])
        self.assertNotIn('aggregate_three_seeds_required', gate['reasons'])
        self.assertFalse(gate['verified'])
        changed = [{**reports[0], 'oracle_sha256': 'different'}, reports[1]]
        self.assertFalse(audit.aggregate_reports(self.oracle, 'same', changed)['integrity_passed'])
        self.assertFalse(audit.aggregate_reports(self.oracle, 'same', reports[:1])['verified'])

    def test_baseline_performance_does_not_block_target_thresholds(self):
        rows = [dict(r) for r in self.rows]
        for row in rows:
            if row['mode'] == 'catalog_only':
                row['automatic_correct'] = False
                row['errors'] = ['not_automatic']
        result = self.gate(rows=rows)
        self.assertNotIn('threshold_not_met', result['reasons'])
        self.assertTrue(result['business_expectations_passed'])
        self.assertFalse(result['verified'])

    def test_omitted_case_run_blocks_integrity(self):
        result = self.gate(runs=self.runs[:-1])
        self.assertFalse(result['integrity_passed'])
        self.assertIn('mode_case_runs', result['coverage_errors'])

    def test_damaged_history_coverage_never_verifies(self):
        rows = [dict(r) for r in self.rows]
        index = next(i for i, r in enumerate(rows) if r['mode']=='shared' and r['seed']==6108
                     and r['scenario']=='single_station_split' and r['source_event_id'])
        rows[index]['source_event_id'] = 'forged-duplicate'
        result = self.gate(rows=rows)
        self.assertFalse(result['acceptance_verified'])
        self.assertIn('coverage_incomplete', result['reasons'])

    def test_threshold_failure_and_undefined_denominator_block(self):
        rows = [dict(r) for r in self.rows]
        chosen = [r for r in rows if r['mode']=='shared' and r['company']=='songbei'
                  and r['scenario']=='single_station_split']
        for row in chosen[:40]:
            row['automatic_correct'] = False
            row['expected_action'] = 'undefined'
        result = self.gate(rows=rows)
        self.assertFalse(result['acceptance_verified'])
        self.assertIn('threshold_not_met', result['reasons'])
        self.assertLess(result['thresholds']['shared']['company:songbei']['B'], 95)

    def test_target_company_scenario_and_zero_auto_denominator_block(self):
        rows = [dict(r) for r in self.rows]
        for row in rows:
            if (row['mode']=='shared' and row['company']=='songbei'
                    and row['scenario']=='single_station_reorder'):
                row['automatic'] = False
                row['automatic_correct'] = False
        result = self.gate(rows=rows)
        key = 'company_scenario:songbei:single_station_reorder'
        self.assertIsNone(result['thresholds']['shared'][key]['A'])
        self.assertFalse(result['thresholds']['shared'][key]['passed'])
        self.assertIn('threshold_not_met', result['reasons'])

    def test_independent_holdout_evidence_is_required(self):
        result = self.gate()
        self.assertTrue(result['integrity_passed'])
        self.assertTrue(result['business_expectations_passed'])
        self.assertFalse(result['acceptance_verified'])
        self.assertFalse(result['verified'])
        self.assertIn('independent_holdout_unverified', result['reasons'])

    def test_pending_prevents_full_control_acceptance(self):
        rows = [dict(r) for r in self.rows]
        rows[0]['pending_expected'] = True
        result = self.gate(rows=rows)
        self.assertFalse(result['acceptance_verified'])
        self.assertIn('control_expectation_pending', result['reasons'])


if __name__ == '__main__':
    unittest.main()
