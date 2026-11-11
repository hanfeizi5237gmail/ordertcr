from pathlib import Path
import copy
import json
import sys
import tempfile
import unittest
from openpyxl import Workbook,load_workbook

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
TMP=Path(__file__).resolve().parents[3]/'evaluation'/'tmp'
TMP.mkdir(exist_ok=True,parents=True)


class RawMessageTests(unittest.TestCase):
    def setUp(self):
        from order_mapping_core.catalog import CatalogStore
        from order_mapping_core.raw_messages import process_messages
        self.run=process_messages
        self.tmp=tempfile.TemporaryDirectory(dir=TMP);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        path=self.root/'catalog.xlsx';wb=Workbook();ws=wb.active
        ws.append(['类别名称','材料编号','材料名称','挂牌价','规格'])
        ws.append(['蔬菜','1','青萝卜','2','500g']);wb.save(path)
        CatalogStore(self.root/'runtime').register(path)
        self.config={'schema_version':1,'companies_confirmed':True,'companies':[
            {'company_id':'a','full_name':'甲站','aliases':['甲公司'],'finance_name':'甲站'},
            {'company_id':'b','full_name':'乙站','aliases':['乙公司'],'finance_name':'乙站'}],
            'numbering':{'prefix':'OR_','confirmed':False},'output_policies':{},'finance':{}}

    def run_text(self,text,**kwargs):
        chat=self.root/'chat.txt';chat.write_text(text)
        request={'schema_version':1,'batch_id':'raw','chat_files':[str(chat)],'config':self.config,
                 'shipping_date':'2026-11-03','matching_mode':'shared','test_only':True,**kwargs}
        return self.run(self.root/'runtime',request,self.root/'output')

    def test_text_routes_companies_and_dates_without_metadata(self):
        result=self.run_text('2026-11-03\n甲站：青萝卜2斤\n2026-11-04\n乙站：青萝卜3斤')
        self.assertEqual(result['event_count'],2)
        self.assertEqual([(r['company_id'],r['shipping_date']) for r in result['events']],
                         [('a','2026-11-03'),('b','2026-11-04')])
        self.assertEqual([r['row']['selected']['code'] for r in result['events']],['1','1'])
        self.assertTrue(result['merged_finance']['finance_draft'])

    def test_attached_qualifiers_block_money_in_real_message_workflow(self):
        for suffix in ('，切片', '，去骨', '，别要肥的', '要求：有机500g', '要鲜的500g', '磨成粉'):
            with self.subTest(suffix=suffix):
                result = self.run_text('【甲站】\n青萝卜2斤' + suffix)
                self.assertEqual(result['event_count'], 1)
                row = result['events'][0]['row']
                self.assertEqual((row['quantity'], row['unit']), ('2', '斤'))
                self.assertNotEqual(row['status'], 'matched')
                self.assertIsNone(row['invoice_quantity'])
                self.assertIsNone(row['amount'])
                manifest = json.loads(Path(result['manifests'][0]).read_text())
                sheet = load_workbook(manifest['files']['standard']['path']).active
                self.assertIsNone(sheet['J2'].value)
                self.assertIsNotNone(sheet['D2'].comment)

    def test_invalid_quantity_is_one_review_row_not_a_failed_batch(self):
        result = self.run_text('公司：甲站\n配送日期：2026-11-03\n青萝卜0斤\n青萝卜-2斤\n青萝卜x斤')
        self.assertEqual(result['event_count'], 3)
        for event in result['events']:
            row = event['row']
            self.assertEqual(row['selected']['code'], '1')
            self.assertIsNone(row['amount'])
            self.assertNotEqual(row['status'], 'matched')

    def test_explicit_total_change_resolves_append_chain_and_cancel(self):
        result = self.run_text('【甲站】\n青萝卜3斤\n青萝卜再追加2斤\n青萝卜总量改为4斤\n取消青萝卜')
        rows = [event['row'] for event in result['events']]
        self.assertEqual([r['event_state'] for r in rows],
                         ['superseded', 'superseded', 'inactive', 'cancellation_record'])
        self.assertEqual([r['quantity_after_event'] for r in rows], ['3', '5', '4', '0'])
        self.assertTrue(all(r['amount'] is None for r in rows))
        self.assertEqual(set(rows[2]['predecessor_line_ids']), {rows[0]['line_id'], rows[1]['line_id']})

    def test_explicit_retransmission_keeps_original_amount_and_duplicate_record(self):
        result = self.run_text('【甲站】\n青萝卜3斤\n重复转发上一条，请勿重复下单：青萝卜3斤')
        original, duplicate = [event['row'] for event in result['events']]
        self.assertEqual(original['status'], 'matched')
        self.assertEqual(original['amount'], '5.7')
        self.assertEqual(duplicate['event_state'], 'duplicate_event')
        self.assertEqual(duplicate['predecessor_line_id'], original['line_id'])
        self.assertIsNone(duplicate['amount'])

    def test_repeated_product_different_amounts_need_an_explicit_relation(self):
        result = self.run_text('【甲站】\n青萝卜3斤\n青萝卜5斤')
        rows = [event['row'] for event in result['events']]
        self.assertEqual([r['quantity'] for r in rows], ['3', '5'])
        self.assertTrue(all(r['event_state'] == 'pending' for r in rows))
        self.assertTrue(all(r['amount'] is None for r in rows))
        self.assertTrue(all(r['quantity_after_event'] is None for r in rows))

    def test_partial_cancel_and_invalid_change_do_not_silently_replace_prior(self):
        for change in ('取消青萝卜3斤', '青萝卜总量改为-2斤', '取消青萝卜1/0斤',
                       '取消青萝卜二二斤', '取消青萝卜x斤', '青萝卜总量改为4',
                       '青萝卜总量改为4斤或者5斤', '青萝卜总量改为4至5斤', '取消青萝卜3斤或者2斤'):
            with self.subTest(change=change):
                result = self.run_text('【甲站】\n青萝卜5斤\n' + change)
                rows = [event['row'] for event in result['events']]
                self.assertTrue(all(r['event_state'] == 'pending' for r in rows))
                self.assertTrue(all(r['amount'] is None for r in rows))

    def test_retransmission_must_refer_to_the_actual_previous_event(self):
        result = self.run_text('【甲站】\n青萝卜3斤\n其他食材2斤\n'
                               '重复转发上一条，请勿重复下单：青萝卜3斤')
        duplicate = result['events'][-1]['row']
        self.assertEqual(duplicate['event_state'], 'pending')
        self.assertIsNone(duplicate.get('predecessor_line_id'))
        self.assertIsNone(duplicate['amount'])

    def test_processing_note_is_not_dropped_from_quantity_first_item(self):
        result = self.run_text('【甲站】\n2斤青萝卜(去皮)')
        row = result['events'][0]['row']
        self.assertEqual(row['name'], '青萝卜')
        self.assertIn('去皮', row['notes'])
        self.assertIsNone(row['amount'])
        self.assertIn('processing_unverified', {i['code'] for i in row['issues']})

    def test_do_not_peel_note_is_not_an_order_cancellation(self):
        text = '【甲站】\n下五花肉2斤（不要去皮）'
        row = self.run_text(text)['events'][0]['row']
        self.assertEqual(row['raw_text'], '下五花肉2斤（不要去皮）')
        self.assertIn('不要去皮', row['notes'])
        self.assertIsNone(row['event'])
        self.assertEqual(row['event_state'], 'active')
        self.assertNotIn('order_event', {i['code'] for i in row['issues']})
        self.assertIsNone(row['amount'])

    def test_real_cancellation_after_processing_note_still_warns(self):
        row = self.run_text('【甲站】\n青萝卜2斤（不要去皮）取消')['events'][0]['row']
        self.assertIn('不要去皮', row['notes'])
        self.assertIn('order_event', {i['code'] for i in row['issues']})
        self.assertIsNone(row['amount'])

    def test_no_company_boundary_or_oracle_fields_can_auto_complete(self):
        result=self.run_text('青萝卜2斤')
        row=result['events'][0]['row']
        self.assertNotEqual(row['status'],'matched')
        self.assertIn('company_boundary',[i['code'] for i in row['issues']])
        self.assertIsNone(row['amount'])
        with self.assertRaises(ValueError):self.run_text('青萝卜2斤',decisions={'x':{}})
        with self.assertRaises(ValueError):self.run_text('青萝卜2斤',event_id='oracle')

    def test_unknown_company_heading_blocks_default_company_and_cross_year_needs_review(self):
        result=self.run_text('【丙公司】\n青萝卜2斤',company='a')
        row=result['events'][0]['row']
        self.assertEqual(result['events'][0]['company_id'],'unassigned')
        self.assertIsNone(row['amount'])
        result=self.run_text('【甲站】\n12月31日\n青萝卜2斤',shipping_date='2026-01-01')
        row=result['events'][0]['row']
        self.assertIn('date_context',[i['code'] for i in row['issues']])
        self.assertIsNone(row['amount'])

    def test_unique_cancellation_is_control_not_new_sku_quantity(self):
        result=self.run_text('【甲站】\n青萝卜2斤\n取消青萝卜')
        old,control=[e['row'] for e in result['events']]
        self.assertEqual(old['event_state'],'inactive')
        self.assertIsNone(old['amount'])
        self.assertIsNone(control['selected'])
        self.assertNotIn('quantity_conversion',[i['code'] for i in control['issues']])

    def test_modification_cancellation_keep_total_ledger_and_blank_amounts(self):
        result=self.run_text('【甲站】\n青萝卜2斤，再加青萝卜1斤，青萝卜改成3斤，取消青萝卜')
        self.assertEqual(result['event_count'],4)
        self.assertEqual(len(result['events']),4)
        # Two active predecessors make an unspecified modification ambiguous.
        self.assertTrue(any(i['code']=='event_predecessor_ambiguous'
                            for e in result['events'] for i in e['row']['issues']))
        for event in result['events'][2:]:self.assertIsNone(event['row']['amount'])

    def test_catalog_update_uses_new_price_and_keeps_source_trace(self):
        first=self.run_text('【甲站】\n青萝卜2斤')
        from order_mapping_core.catalog import CatalogStore
        path=self.root/'new.xlsx';wb=Workbook();ws=wb.active
        ws.append(['类别名称','材料编号','材料名称','挂牌价','规格'])
        ws.append(['蔬菜','new','青萝卜','4','500g']);wb.save(path)
        CatalogStore(self.root/'runtime').register(path)
        second=self.run_text('【乙站】\n青萝卜2斤')
        self.assertEqual(second['events'][0]['row']['selected']['code'],'new')
        self.assertEqual(second['events'][0]['row']['amount'],'7.6')
        self.assertNotEqual(first['catalog']['version_id'],second['catalog']['version_id'])
        row=second['events'][0]['row']
        self.assertEqual('青萝卜2斤',('【乙站】\n青萝卜2斤')[row['char_start']:row['char_end']])


if __name__=='__main__':unittest.main()
