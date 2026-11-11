"""Contract tests for frozen synthetic challenge scoring."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import v6_challenge_replay as replay


class ChallengeScoreTests(unittest.TestCase):
    def setUp(self):
        self.record = {'code': 'sku1', 'name': '菠菜', 'spec': '500g', 'price': '2'}
        self.expected = {'action': 'order', 'outcome': 'selected', 'selected': {'sku': 'sku1', 'name': '菠菜', 'spec': '500g', 'price': '2'}, 'quantity': '3', 'raw_unit': '斤', 'quantity_500g': '3', 'scenario': 'normal_suffix'}
        self.item = {'line_id': 'one', 'status': 'matched', 'event_state': 'active', 'selected': self.record, 'quantity': '3', 'unit': '斤', 'price_unit': '500g', 'invoice_quantity': '3', 'discount': '0.95', 'unit_price': '1.9', 'amount': '5.7', 'finance_amount': '5.7'}

    def score(self, expected=None, item=None, **kwargs):
        return replay.score(expected or self.expected, {'row': item or self.item, 'company_id': 'c', 'shipping_date': '2026-11-19'}, company='c', day='2026-11-19', records={'sku1': self.record}, **kwargs)

    def test_normal_wrong_price_unit_or_missing_state_rejected(self):
        self.assertTrue(self.score()['automatic_correct'])
        self.assertFalse(self.score(item={**self.item, 'price_unit': '吨'})['automatic_correct'])
        self.assertIn('amount', self.score(item={**self.item, 'amount': 'not-a-number'})['errors'])
        item = dict(self.item); del item['event_state']
        self.assertFalse(self.score(item=item)['automatic_correct'])

    def test_unit_missing_invalid_issue_and_fabricated_quantity_rejected(self):
        expected = {**self.expected, 'outcome': 'review', 'anomaly': 'unit_missing', 'quantity': '4', 'quantity_500g': None}
        expected.pop('raw_unit')
        row = {**self.item, 'quantity': '4', 'unit': None, 'status': 'needs_review', 'invoice_quantity': None, 'amount': None, 'finance_amount': None, 'issues': [{'code': 'missing_unit'}]}
        result = self.score(expected, row)
        self.assertTrue(result['manual_checks_passed'])
        self.assertFalse(result['manual_correct'])
        self.assertEqual(result['candidate_completeness'], 'unverified')
        self.assertFalse(self.score(expected, {**row, 'invoice_quantity': '999'})['manual_correct'])
        self.assertFalse(self.score(expected, {**row, 'issues': [{'code': 'irrelevant'}]})['manual_correct'])

    def test_forbidden_selection_but_candidate_can_be_shown(self):
        expected = {**self.expected, 'outcome': 'review', 'selected': None, 'anomaly': 'brand_unverified', 'prohibited_skus': ['sku1']}
        row = {**self.item, 'selected': None, 'status': 'needs_review', 'invoice_quantity': None, 'amount': None, 'finance_amount': None, 'issues': [{'code': 'brand_conflict'}], 'candidates': [self.record]}
        result = self.score(expected, row)
        self.assertFalse(result['manual_correct'])
        self.assertNotIn('prohibited_candidate', result['errors'])
        self.assertEqual(result['prohibited_skus_shown_as_candidates'], ['sku1'])
        self.assertIn('prohibited_sku', self.score(expected, {**row, 'selected': self.record})['errors'])

    def test_packaging_issue_requires_specific_evidence(self):
        expected = {**self.expected, 'outcome': 'review', 'selected': None, 'anomaly': 'packaging_unverified'}
        row = {**self.item, 'selected': None, 'status': 'needs_review', 'invoice_quantity': None,
               'amount': None, 'finance_amount': None, 'issues': [{'code': 'semantic_review', 'message': '袋装规格有差异'}]}
        self.assertNotIn('required_issue', self.score(expected, row)['errors'])
        row['issues'][0]['message'] = '商品仅有语义候选'
        self.assertIn('required_issue', self.score(expected, row)['errors'])

    def test_control_requires_expected_progression_and_real_predecessor(self):
        expected = {**self.expected, 'action': 'append', 'scenario': 'action_append', 'quantity': '2', 'quantity_500g': '2', 'expected_state': {'active': True, 'quantity_500g': '5'}}
        row = {**self.item, 'event': '追加', 'event_state': 'superseded', 'quantity': '2', 'quantity_after_event': '999', 'predecessor_line_id': 'invented', 'amount': None, 'finance_amount': None}
        result = self.score(expected, row, cancelled_chain=True)
        self.assertFalse(result['control_correct'])
        self.assertIn('progressive_quantity', result['errors'])

    def test_ordered_alignment_never_matches_by_product(self):
        expected = [{'source_file': '聊天.txt', 'source_line': 1, 'requested_name': '菠菜'}, {'source_file': '聊天.txt', 'source_line': 1, 'requested_name': '黑豆'}]
        actual = [{'row': {'source_name': '聊天.txt', 'source_line': 1, 'char_start': 5, 'name': '黑豆'}}, {'row': {'source_name': '聊天.txt', 'source_line': 1, 'char_start': 0, 'name': '菠菜'}}]
        paired = replay.align_events(expected, actual)
        self.assertEqual([(p[0]['requested_name'], p[1]['row']['name']) for p in paired], [('菠菜', '菠菜'), ('黑豆', '黑豆')])
        actual[0]['row']['char_start'], actual[1]['row']['char_start'] = 0, 5
        paired = replay.align_events(expected, actual)
        self.assertEqual(paired[0][1]['row']['name'], '黑豆')

    def test_normal_subsample_excludes_controls_and_duplicates(self):
        items = [{'expected': {'outcome': 'selected', 'scenario': s}, 'scenario': s} for s in ['normal_suffix', 'action_initial', 'duplicate_original', 'duplicate_explicit']]
        self.assertEqual([i['scenario'] for i in items if replay.is_normal_supported(i)], ['normal_suffix'])

    def test_final_state_checks_all_predecessors_and_net(self):
        self.assertTrue(hasattr(replay, 'check_state_assertions'))
        rows = []
        for action, state, q, net, line, pred, preds in [('order', 'superseded','3','3','a',None,[]), ('append','superseded','2','5','b','a',[]), ('replace_total','inactive','4','4','c','b',['a','b']), ('cancel','cancellation_record',None,'0','d','c',['c'])]:
            e = {'action': action, 'company':'站', 'date':'2026-11-19', 'catalog_version':'A', 'requested_name':'菠菜', 'expected_state':{'quantity_500g':net,'active':action!='cancel'}}
            a = {'row': {'line_id':line, 'event_state':state, 'quantity':q, 'quantity_after_event':net, 'predecessor_line_id':pred, 'predecessor_line_ids':preds}, 'company_id':'c', 'shipping_date':'2026-11-19'}
            rows.append({'expected':e, 'actual':a, 'errors':[], 'control_correct':True})
        assertion = {'company':'站','date':'2026-11-19','catalog_version':'A','requested_name':'菠菜','final_action':'cancelled','final_quantity_500g':'0'}
        self.assertEqual(replay.check_state_assertions(rows,[assertion])[0]['errors'], [])
        bad = copy.deepcopy(rows); bad[2]['actual']['row']['predecessor_line_ids']=['b']
        self.assertTrue(replay.check_state_assertions(bad,[assertion])[0]['errors'])
        bad = copy.deepcopy(rows); bad[3]['actual']['row']['quantity_after_event']='4'
        self.assertTrue(replay.check_state_assertions(bad,[assertion])[0]['errors'])

    def test_missing_and_extra_outputs_do_not_remove_expected_denominator(self):
        rows = [{'expected':self.expected,'automatic':False,'automatic_correct':False,'manual_correct':False,'control_correct':False,'errors':['missing_output']}, {'expected':None,'automatic':True,'automatic_correct':False,'manual_correct':False,'control_correct':False,'errors':['unexpected_output']}]
        result = replay.summary(rows)
        self.assertEqual(result['input_events'], 1)
        self.assertEqual(result['missing_outputs'], 1)
        self.assertEqual(result['unexpected_outputs'], 1)
        self.assertFalse(result['verified'])

    def test_batch_without_state_assertions(self):
        rows, chains = replay.score_batch({'events': []}, [], {'events': []}, company_ids={}, records={},
                                          seed=1, mode='shared', case='b', request={'company_name': 'x'},
                                          path=Path('/unused/request.json'))
        self.assertEqual((rows, chains), ([], []))

    def test_frozen_tampering_and_extra_files_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'input').mkdir()
            (root / 'input' / 'chat.txt').write_text('original')
            (root / 'expected.json').write_text('{}')
            manifest = {'expected_sha256': replay.sha(root / 'expected.json'),
                        'inputs_sha256': {'input/chat.txt': replay.sha(root / 'input' / 'chat.txt')}}
            (root / 'manifest.json').write_text(json.dumps(manifest))
            frozen = {'sha256': {'builder': 'historical', 'expected.json': manifest['expected_sha256'],
                                  'manifest.json': replay.sha(root / 'manifest.json'),
                                  **manifest['inputs_sha256']}}
            (root / 'FREEZE.json').write_text(json.dumps(frozen))
            replay.verify_frozen(root)
            (root / 'input' / 'extra.txt').write_text('extra')
            with self.assertRaisesRegex(ValueError, 'inventory'):
                replay.verify_frozen(root)
            (root / 'input' / 'extra.txt').unlink()
            (root / 'expected.json').write_text('{"changed": true}')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                replay.verify_frozen(root)

    def test_saved_result_binds_request_catalog_and_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            chat = root / 'chat.txt'
            chat.write_text('message')
            normal = {'batch_id': 'b', 'matching_mode': 'shared', 'chat_files': [str(chat)]}
            request = root / 'request.json'
            request.write_text(json.dumps(normal))
            result_path = root / 'result.json'
            result = {'batch_id': 'b', 'matching_mode': 'shared', 'catalog': {'sha256': 'catalog'},
                      'sources': [{'path': str(chat), 'sha256': replay.sha(chat)}], 'events': []}
            result_path.write_text(json.dumps(result))
            run = {'request': str(request), 'result_path': str(result_path)}
            _, proof = replay.saved_result(run, normal, 'catalog')
            self.assertFalse(proof['historical_result_hash_authenticated'])
            result['batch_id'] = 'different'
            result_path.write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, 'batch or mode'):
                replay.saved_result(run, normal, 'catalog')
            request.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'request differs'):
                replay.saved_result(run, normal, 'catalog')


if __name__ == '__main__': unittest.main()
