import copy
import importlib
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
TMP = Path(__file__).resolve().parents[3] / 'evaluation' / 'tmp'
TMP.mkdir(parents=True, exist_ok=True)


def config():
    return {'schema_version': 1, 'companies_confirmed': True,
            'companies': [{'company_id': 'c', 'full_name': '测试公司', 'aliases': ['简称'], 'finance_name': '测试商户'}],
            'default_discount': '0.95', 'numbering': {'confirmed': True, 'prefix': 'OR_', 'daily_start': 0},
            'output_policies': {'standard_order': 'sample_exact', 'finance': 'line_cent', 'dual_confirmed': True},
            'aliases': {}, 'unit_rules': {},
            'finance': {'confirmed': True, 'price_source': 'discounted_catalog', 'tax_freight_confirmed': True,
                        'receipt_date': '2026-11-03', 'order_number_mode': 'OR', 'merchant_names_confirmed': True}}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        try:
            self.api = importlib.import_module('order_mapping_core.workflow')
        except ModuleNotFoundError as exc:
            self.fail(f'完整批次执行尚未实现: {exc}')
        from order_mapping_core.catalog import CatalogStore
        self.store = CatalogStore(self.root / 'runtime')
        self.catalog('1.005')

    def catalog(self, price):
        path = self.root / 'price.xlsx'
        book = Workbook()
        book.active.append(['类别名称', '材料编号', '材料名称', '挂牌价', '规格'])
        book.active.append(['蔬菜', '1', '青萝卜', price, '500g'])
        book.save(path)
        return self.store.register(path)

    def batch(self):
        from order_mapping_core.parsing import parse_chat
        return {'schema_version': 1, 'batch_id': 'b1', 'shipping_date': '2026-11-03', 'company': '简称',
                'config': config(), 'items': parse_chat('青萝卜2斤', 'chat.txt'), 'mode': 'settlement',
                'date_policy': 'single', 'date_policy_confirmed': True, 'delivery_confirmed': True,
                'publish': True, 'test_only': True}

    def actual_batch(self, batch):
        line = batch['items'][0]['line_id']
        batch['decisions'] = {line: {'code': '1', 'invoice_quantity': '2', 'price_unit': '斤',
                                     'quantity_basis': 'actual', 'reason': '逐项实配确认', 'confirmed_by': 'user',
                                     'catalog_version': self.store.current()['version_id']}}
        return batch

    def run_batch(self, batch):
        return self.api.process_batch(self.root / 'runtime', batch, self.root / 'outputs')

    def test_final_export_reuses_number_revision_and_integrity(self):
        first = self.run_batch(self.actual_batch(self.batch()))
        self.assertEqual(first['status'], 'exported')
        self.assertFalse(first['finance_draft'])
        self.assertEqual(first['order']['order_number'], 'OR_20261103000001')
        self.assertEqual(first['totals']['standard_exact'], '1.9095')
        self.assertEqual(first['totals']['finance'], '1.91')
        second = self.run_batch(self.actual_batch(self.batch()))
        self.assertEqual(first['manifest_path'], second['manifest_path'])
        self.catalog('2')
        third = self.run_batch(self.actual_batch(self.batch()))
        self.assertNotEqual(first['manifest_path'], third['manifest_path'])
        self.assertTrue(Path(first['manifest_path']).exists())
        self.assertEqual(first['order']['order_number'], third['order']['order_number'])
        self.assertEqual(third['totals']['standard_exact'], '3.8')
        Path(third['files']['standard']['path']).write_bytes(b'damaged')
        fixed = self.run_batch(self.actual_batch(self.batch()))
        self.assertNotEqual(fixed['manifest_path'], third['manifest_path'])
        self.assertEqual(fixed['order']['order_number'], first['order']['order_number'])

    def test_unknown_rows_stay_in_order_and_block_allocation(self):
        from order_mapping_core.parsing import parse_chat
        batch = self.batch()
        batch['items'] = parse_chat('鲤鱼3条，青萝卜2斤')
        result = self.run_batch(batch)
        self.assertEqual(result['status'], 'needs_confirmation')
        self.assertIsNone(result['order']['order_number'])
        self.assertTrue(result['finance_draft'])
        sheet = load_workbook(result['files']['standard']['path']).active
        self.assertEqual(sheet['D2'].value, '鲤鱼')
        self.assertIsNone(sheet['J2'].value)
        self.assertEqual(result['row_count'], 2)

    def test_preview_date_company_and_stale_rules(self):
        batch = self.batch()
        batch['config']['companies_confirmed'] = False
        batch['items'][0]['date_context'] = '11月4日'
        result = self.run_batch(batch)
        self.assertIsNone(result['order']['order_number'])
        self.assertTrue(any('日期' in x for x in result['blockers']))
        batch = self.batch()
        batch['config']['aliases'] = {'青萝卜': '1'}
        batch['config']['catalog_rules_version'] = 'old'
        result = self.run_batch(batch)
        self.assertTrue(any('规则' in x for x in result['blockers']))
        self.assertIsNone(result['order']['order_number'])

    def test_decisions_require_source_and_current_catalog(self):
        from order_mapping_core.parsing import parse_chat
        batch = self.batch()
        batch['items'] = parse_chat('萝卜2斤')
        batch['mode'] = 'order'
        batch['delivery_confirmed'] = False
        line = batch['items'][0]['line_id']
        batch['decisions'] = {line: {'code': '1', 'reason': '已核定', 'confirmed_by': 'user',
                                     'catalog_version': self.store.current()['version_id']}}
        result = self.run_batch(batch)
        self.assertEqual(result['status'], 'exported')
        self.catalog('4')
        stale = self.run_batch(batch)
        self.assertIsNone(stale['order']['order_number'])
        self.assertTrue(any(i['code'] == 'stale_decision' for i in stale['order']['rows'][0]['issues']))

    def test_current_agent_can_veto_identity_but_stale_candidate_review_is_rejected(self):
        batch=self.batch();batch['mode']='order'
        item=batch['items'][0]
        batch['candidate_reviews']={item['line_id']:{'catalog_version':self.store.current()['version_id'],
            'raw_text_sha256':hashlib.sha256(item['raw_text'].encode()).hexdigest(),
            'confirmed_by':'agent','candidate_codes':['1'],'reason':'原文品种要求仍未证实，候选不能充当确认'}}
        result=self.run_batch(batch);row=result['order']['rows'][0]
        self.assertIsNone(row['selected']);self.assertIsNone(row['amount'])
        sheet=load_workbook(result['files']['standard']['path']).active
        self.assertIsNone(sheet['G2'].value);self.assertIsNone(sheet['I2'].value)
        self.assertIn('材料编号=1',sheet['D2'].comment.text)
        batch['candidate_reviews'][item['line_id']]['raw_text_sha256']='0'*64
        with self.assertRaises(ValueError):self.run_batch(batch)

    def test_failed_save_retry_and_no_false_success(self):
        batch = self.batch()
        batch = self.actual_batch(batch)
        with patch.object(self.api, 'export_finance', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.run_batch(batch)
        self.assertEqual(list((self.root / 'outputs').rglob('manifest.json')), [])
        result = self.run_batch(batch)
        self.assertEqual(result['order']['order_number'], 'OR_20261103000001')

    def test_settlement_requires_per_line_actual_quantity(self):
        result = self.run_batch(self.batch())
        self.assertEqual(result['status'], 'needs_confirmation')
        self.assertTrue(any('逐项实配' in x for x in result['blockers']))
        self.assertTrue(result['finance_draft'])
        row = result['order']['rows'][0]
        self.assertIsNone(row['amount'])
        self.assertIsNone(row['finance_amount'])
        self.assertEqual(result['totals']['standard'], '0')
        self.assertEqual(result['totals']['finance'], '0')
        standard = load_workbook(result['files']['standard']['path']).active
        finance = load_workbook(result['files']['finance']['path'])['商品明细']
        self.assertIsNone(standard['J2'].value)
        self.assertIsNone(finance['H2'].value)
        self.assertEqual(standard['J3'].value, 0)

    def test_validate_boundaries_and_finance_merge(self):
        bad = config()
        bad['companies'].append(dict(bad['companies'][0], company_id='b'))
        with self.assertRaises(ValueError):
            self.api.validate_config(bad)
        batch = self.batch()
        batch['publish'] = 'false'
        with self.assertRaises(ValueError):
            self.run_batch(batch)
        first = self.run_batch(self.actual_batch(self.batch()))
        batch = self.batch()
        batch['batch_id'] = 'b2'
        second = self.run_batch(self.actual_batch(batch))
        merged = self.api.merge_finance(self.root / 'runtime', [first['manifest_path'], second['manifest_path']], self.root / 'merged')
        self.assertFalse(merged['finance_draft'])
        self.assertEqual(merged['row_count'], 2)
        self.assertEqual(merged['total'], '3.82')
        with self.assertRaises(ValueError):
            self.api.merge_finance(self.root / 'runtime', [first['manifest_path'], first['manifest_path']], self.root / 'merged')

    def test_stale_company_profile_is_not_used_after_catalog_update(self):
        from order_mapping_core.profiles import register_profile
        register_profile(self.root / 'runtime',{'schema_version':1,'profile_id':'hist','company_id':'c',
            'catalog_version':self.store.current()['version_id'],
            'evidence':{'kind':'user_provided_reference','source':'price.xlsx','sha256':hashlib.sha256((self.root/'price.xlsx').read_bytes()).hexdigest()},
            'aliases':{'青萝卜':{'code':'1','evidence':'用户提供人工参考'}},'unit_rules':{}},source_path=self.root/'price.xlsx')
        self.catalog('9')
        batch=self.batch();batch['mode']='order'
        batch['matching_mode']='legacy_profile'
        result=self.run_batch(batch)
        self.assertEqual(result['catalog']['version_id'],self.store.current()['version_id'])
        self.assertEqual(result['order']['rows'][0]['selected']['price'],'9')
        self.assertIsNone(result['order']['order_number'])
        self.assertTrue(any('偏好' in message for message in result['blockers']))


if __name__ == '__main__':
    unittest.main()
