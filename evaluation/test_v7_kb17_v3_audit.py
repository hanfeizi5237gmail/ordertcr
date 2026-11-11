import copy
import unittest

from test_v7_kb16_audit import event, item
from v7_kb17_v3_audit import score_pairs


def fixtures():
    text = '冷冻鲷鱼片2斤\n冷冻鲷鱼片2斤'
    first = event('initial', event_id='K29', assumed_selected_sku='S1', source={'derived_line_span': {'start': 0, 'end': 7}})
    second = event('append', event_id='K30', source_line=2, assumed_selected_sku='S1', expected_predecessor_id='K29', source={'derived_line_span': {'start': 8, 'end': 15}})
    prior = item(line_id='actual:1')
    append = item(source_line=2, char_start=8, char_end=15, line_id='actual:2', event='追加', predecessor_id='actual:1')
    return text, [first, second], [prior, append]


class V3OracleAdapterTests(unittest.TestCase):
    def test_manual_review_allows_selected_but_not_auto_or_billable_quantity(self):
        text, events, outputs = fixtures()
        expected = dict(events[0], kind='manual_review')
        outputs[0]['row'].update(status='needs_review', invoice_quantity=None, amount=None, finance_amount=None)
        row = score_pairs([expected], outputs[:1], {'星月站': 'c1'}, text)[0]
        self.assertTrue(row['review_correct'])
        self.assertEqual(row['kind'], 'manual_review')
        self.assertIsNotNone(row['actual']['row']['selected'])
        outputs[0]['row']['status'] = 'matched'
        row = score_pairs([expected], outputs[:1], {'星月站': 'c1'}, text)[0]
        self.assertTrue(row['automatic'])
        self.assertFalse(row['automatic_correct'])
        outputs[0]['row'].update(status='needs_review', invoice_quantity='2')
        self.assertFalse(score_pairs([expected], outputs[:1], {'星月站': 'c1'}, text)[0]['review_correct'])

    def test_append_resolves_expected_id_to_unique_actual_identity(self):
        text, events, outputs = fixtures()
        scores = score_pairs(events, outputs, {'星月站': 'c1'}, text)
        self.assertTrue(scores[1]['automatic_correct'], scores[1]['errors'])
        outputs[1]['row']['predecessor_id'] = 'K29'
        scores = score_pairs(events, outputs, {'星月站': 'c1'}, text)
        self.assertFalse(scores[1]['automatic_correct'])
        self.assertIn('control_relation_unproven', scores[1]['errors'])

    def test_duplicate_predecessor_identity_cannot_prove_relationship(self):
        text, events, outputs = fixtures()
        extra = copy.deepcopy(outputs[0])
        extra['row'].update(source_line=77, raw_text='extra')
        scores = score_pairs(events, outputs + [extra], {'星月站': 'c1'}, text)
        self.assertFalse(scores[1]['automatic_correct'])
        self.assertIn('control_relation_unproven', scores[1]['errors'])

    def test_explicit_frozen_span_is_checked(self):
        text, events, outputs = fixtures()
        events[0]['source']['derived_line_span']['end'] -= 1
        result = score_pairs(events[:1], outputs[:1], {'星月站': 'c1'}, text)[0]
        self.assertFalse(result['automatic_correct'])
        self.assertIn('oracle_span_mismatch', result['errors'])


if __name__ == '__main__':
    unittest.main()
