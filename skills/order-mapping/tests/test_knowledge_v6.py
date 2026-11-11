import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class SharedKnowledgeTests(unittest.TestCase):
    def setUp(self):
        from order_mapping_core.knowledge import match_shared
        self.match = match_shared
        self.records = [
            {'code':'a','name':'土豆（大）','category':'蔬菜','spec':'500g','price':'3'},
            {'code':'b','name':'土豆（小）','category':'蔬菜','spec':'500g','price':'2'},
            {'code':'c','name':'土豆粉','category':'干调','spec':'500g','price':'20'},
        ]
        self.kb = {'schema_version':1,'rules':[{'id':'potato','concept':'鲜土豆','aliases':['土豆','马铃薯'],
            'selector':{'names':['土豆（大）','土豆（小）'],'category':'蔬菜'},'conditions':{},
            'kind':'synonym','applicable':'general','conflicts':['不适用土豆粉'],
            'validation_status':'historical_reviewed','evidence':[{'source':'人工参考','reason':'同种鲜蔬菜'}]}], 'unit_rules':[]}

    def test_shared_concept_reresolves_codes_and_prices(self):
        item={'name':'马铃薯','raw_text':'马铃薯2斤','quantity':'2','unit':'斤','issues':[]}
        first=self.match(item,self.records,self.kb)
        self.assertEqual(first['selected']['code'],'a')
        changed=copy.deepcopy(self.records)
        changed[0]['code']='new';changed[0]['price']='1'
        second=self.match(item,changed,self.kb)
        self.assertEqual(second['selected']['code'],'b')
        self.assertEqual(second['selected']['price'],'2')

    def test_candidate_only_and_missing_selector_do_not_force_selection(self):
        kb=copy.deepcopy(self.kb);kb['rules'][0]['applicable']='candidate_only'
        row=self.match({'name':'马铃薯','quantity':'2','unit':'斤'},self.records,kb)
        self.assertIsNone(row['selected'])
        self.assertEqual({r['code'] for r in row['candidates']},{'a','b'})
        row=self.match({'name':'马铃薯','quantity':'2','unit':'斤'},[],self.kb)
        self.assertIsNone(row['selected'])
        self.assertIn('knowledge_selector_missing',[x['code'] for x in row['issues']])

    def test_conflicting_rules_and_explicit_constraints_stay_manual(self):
        kb=copy.deepcopy(self.kb)
        kb['rules'].append({**kb['rules'][0], 'id':'bad','selector':{'names':['土豆粉']}})
        row=self.match({'name':'马铃薯','quantity':'2','unit':'斤'},self.records,kb)
        self.assertIsNone(row['selected'])
        self.assertIn('knowledge_conflict',[x['code'] for x in row['issues']])
        branded=copy.deepcopy(self.kb);branded['rules'][0]['aliases']=['恒都牛肉']
        row=self.match({'name':'恒都牛肉','quantity':'2','unit':'斤'},self.records,branded)
        self.assertIsNone(row['selected'])

    def test_unresolved_brand_package_requirement_cannot_auto_select(self):
        item={'name':'土豆（大）','quantity':'2','unit':'斤','raw_text':'土豆（大）2斤，换另一种包装',
              'notes':'换另一种包装'}
        row=self.match(item,self.records,self.kb)
        self.assertIsNone(row['selected'])
        self.assertIn('requirement_unresolved',[x['code'] for x in row['issues']])

    def test_box_request_keeps_single_pack_witness_without_pricing_it(self):
        kb = {'schema_version': 1, 'rules': [{
            'id': 'sour_cabbage', 'concept': '酸菜候选', 'aliases': ['酸菜'],
            'selector': {'names': ['翠花酸菜750g', '翠花酸菜750g*10袋'], 'category': '调料类'},
            'conditions': {}, 'kind': 'candidate', 'applicable': 'candidate_only',
            'conflicts': ['箱装件数和净重必须核实'], 'validation_status': 'reviewed',
            'evidence': [{'source': 'test', 'reason': '单袋只能作为人工核对候选'}]
        }], 'unit_rules': []}
        records = [
            {'code': 'single', 'name': '翠花酸菜750g', 'category': '调料类', 'spec': '750g', 'price': '5'},
            {'code': 'case', 'name': '翠花酸菜750g*10袋', 'category': '调料类', 'spec': '750g*10袋', 'price': '50'},
        ]
        row = self.match({'name': '酸菜', 'quantity': '1', 'unit': '箱', 'raw_text': '酸菜1箱', 'issues': []}, records, kb)
        self.assertIsNone(row['selected'])
        self.assertEqual(row['invoice_quantity'], None)
        self.assertEqual({r['code'] for r in row['candidates']}, {'single', 'case'})

    def test_unit_condition_does_not_erase_known_concept_candidates(self):
        kb = copy.deepcopy(self.kb)
        kb['rules'][0]['conditions'] = {'request_units': ['斤', 'kg']}
        row = self.match({'name': '马铃薯', 'quantity': '2', 'unit': '袋',
                          'raw_text': '马铃薯2袋'}, self.records, kb)
        self.assertNotEqual(row['status'], 'matched')
        self.assertIsNone(row['invoice_quantity'])
        self.assertEqual({r['code'] for r in row['candidates']}, {'a', 'b'})

    def test_candidate_context_does_not_override_weight_order(self):
        kb = copy.deepcopy(self.kb)
        kb['rules'] = [{**kb['rules'][0], 'aliases': ['土豆（大）'],
                        'kind': 'candidate', 'applicable': 'candidate_only',
                        'conditions': {'request_units': ['袋']}}]
        ordinary = self.match({'name': '土豆（大）', 'quantity': '2', 'unit': '斤'}, self.records, kb)
        self.assertEqual(ordinary['status'], 'matched')
        packaged = self.match({'name': '土豆（大）', 'quantity': '2', 'unit': '袋'}, self.records, kb)
        self.assertIsNone(packaged['selected'])
        self.assertEqual({r['code'] for r in packaged['candidates']}, {'a', 'b'})

    def test_unknown_brand_shows_unbranded_concept_only_as_conditional(self):
        from order_mapping_core.knowledge import load_knowledge
        kb, _ = load_knowledge()
        records = [{'code': 'plain', 'name': '西兰花', 'category': '蔬菜', 'spec': '500g', 'price': '4'},
                   {'code': 'other', 'name': '海天西兰花', 'category': '蔬菜', 'spec': '500g', 'price': '8'}]
        row = self.match({'name': '臻园牌西蓝花', 'quantity': '2', 'unit': '斤',
                          'raw_text': '臻园牌西蓝花2斤'}, records, kb)
        self.assertIsNone(row['selected'])
        self.assertIsNone(row['invoice_quantity'])
        self.assertEqual({r['code'] for r in row['candidates']}, {'plain'})


class VersionFiveLanguageRulesTests(unittest.TestCase):
    def setUp(self):
        from order_mapping_core.knowledge import load_knowledge, match_shared
        self.kb, _ = load_knowledge()
        self.match = match_shared
        self.pairs = [
            ('去皮青笋', '去皮莴笋（青笋）', '蔬菜'),
            ('铁棍山药', '铁杆山药/铁棍山药', '蔬菜'),
            ('一级库尔勒香梨', '库尔勒香梨一级', '水果类'),
            ('紫菜', '紫菜（斤）', '调料类'),
            ('平菇', '平菇（鲜蘑）', '蔬菜'),
            ('西蓝花', '西兰花', '蔬菜'),
            ('薏仁米', '薏米', '米类'),
            ('干金针菜', '干黄花菜', '调料类'),
            ('黄元帅', '黄元帅苹果', '水果类'),
            ('干桂园肉', '干桂圆肉', '调料类'),
            ('黑芝麻粒', '黑芝麻', '调料类'),
            ('马铃薯（大885）', '土豆（大885）', '蔬菜'),
        ]
        self.records = [{'code':f'test-{i}', 'name':name, 'category':category,
                         'spec':'500g', 'price':str(i+2)}
                        for i, (_, name, category) in enumerate(self.pairs)]

    def test_rules_requery_current_catalog_without_storing_answers(self):
        self.assertGreaterEqual(self.kb['knowledge_version'], 6)
        for index, (alias, canonical, _) in enumerate(self.pairs):
            with self.subTest(alias=alias):
                matching = [r for r in self.kb['rules'] if alias in r['aliases']]
                self.assertEqual(len(matching), 1)
                rule = matching[0]
                self.assertEqual(rule['selector']['names'], [canonical])
                self.assertFalse({'sku','code','price','row'} & set(rule['selector']))
                self.assertIn(rule['evidence'][0]['kind'],
                              {'catalog_explicit_synonym','linguistic_inference'})
                item = {'name':alias, 'quantity':'2', 'unit':'斤',
                        'raw_text':alias+'2斤', 'issues':[]}
                first = self.match(item, self.records, self.kb)
                self.assertEqual(first['selected']['code'], f'test-{index}')
                self.assertEqual(first['invoice_quantity'], '2')
                changed = copy.deepcopy(self.records)
                changed[index]['code'] = 'new-current-code'
                changed[index]['price'] = '101'
                second = self.match(item, changed, self.kb)
                self.assertEqual(second['selected']['code'], 'new-current-code')
                self.assertEqual(second['selected']['price'], '101')
                removed = [r for r in changed if r['name'] != canonical]
                self.assertIsNone(self.match(item, removed, self.kb)['selected'])

    def test_weight_conversion_and_unknown_packaging(self):
        kg = self.match({'name':'西蓝花','quantity':'2','unit':'kg',
                         'raw_text':'西蓝花2kg','issues':[]},self.records,self.kb)
        self.assertEqual(kg['invoice_quantity'], '4')
        packed = self.match({'name':'西蓝花','quantity':'1','unit':'罐',
                             'raw_text':'西蓝花1罐','issues':[]},self.records,self.kb)
        self.assertNotEqual(packed['status'], 'matched')
        self.assertIsNone(packed['invoice_quantity'])

    def test_unproven_cashew_is_candidate_only(self):
        candidates = [
            {'code':'cashew', 'name':'生腰果', 'category':'米类', 'spec':'500g', 'price':'4'},
            {'code':'shrimp', 'name':'冷冻北极冰虾80*100', 'category':'水产品', 'spec':'500g', 'price':'8'},
        ]
        for alias, expected in [('生腰果仁','cashew')]:
            with self.subTest(alias=alias):
                row = self.match({'name':alias,'quantity':'2','unit':'斤','issues':[]},candidates,self.kb)
                self.assertIsNone(row['selected'])
                self.assertIsNone(row['invoice_quantity'])
                self.assertIn(expected, {r['code'] for r in row['candidates']})
                rule = next(r for r in self.kb['rules'] if alias in r['aliases'])
                self.assertEqual(rule['applicable'], 'candidate_only')


    def test_explicit_post_quantity_attributes_require_catalog_evidence(self):
        for alias, raw, notes in [
            ('西蓝花', '西蓝花2斤冷冻', '冷冻'),
            ('黑芝麻粒', '黑芝麻粒2斤要熟制', '熟制'),
        ]:
            with self.subTest(alias=alias, notes=notes):
                row = self.match({'name':alias,'quantity':'2','unit':'斤',
                                  'raw_text':raw,'notes':notes,'issues':[]},self.records,self.kb)
                self.assertNotEqual(row['status'], 'matched')
                self.assertIsNone(row['invoice_quantity'])

    def test_new_explicit_brand_is_not_silently_dropped(self):
        row = self.match({'name':'臻园牌西蓝花','quantity':'2','unit':'斤',
                          'raw_text':'臻园牌西蓝花2斤','issues':[]},self.records,self.kb)
        self.assertNotEqual(row['status'],'matched')
        self.assertIsNone(row['selected'])


class OrdinaryDemandRulesTests(unittest.TestCase):
    """Unspecified brands are not missing facts when ordinary items are comparable."""

    def setUp(self):
        from order_mapping_core.knowledge import load_knowledge, match_shared
        self.kb, _ = load_knowledge()
        self.match = match_shared

    def row(self, name, records, unit='斤', notes=''):
        return self.match({'name': name, 'quantity': '2', 'unit': unit,
                           'notes': notes, 'raw_text': name + '2' + unit + notes}, records, self.kb)

    @staticmethod
    def record(code, name, category, price, spec='500g'):
        return {'code': code, 'name': name, 'category': category, 'spec': spec, 'price': price}

    def test_plain_dry_tofu_excludes_thick_and_processed_records(self):
        records = [self.record('a', '豆骅堂干豆腐（称重）', '豆制品', '6'),
                   self.record('b', '北大荒干豆腐', '豆制品', '7'),
                   self.record('thick', '加厚干豆腐', '豆制品', '99'),
                   self.record('strips', '干豆腐丝', '豆制品', '100')]
        row = self.row('干豆腐', records)
        self.assertEqual(row['selected']['code'], 'b')
        self.assertEqual(self.row('普通干豆腐', records)['selected']['code'], 'b')
        self.assertEqual(row['invoice_quantity'], '2')
        records[0]['price'] = '8'
        self.assertEqual(self.row('干豆腐', records)['selected']['code'], 'a')
        records[0]['spec'] = '1kg'
        self.assertIsNone(self.row('干豆腐', records)['selected'])

    def test_plain_vegetable_concepts_do_not_select_different_varieties(self):
        records = [self.record('sprout', '蒜苔（绿帽）', '蔬菜', '8'),
                   self.record('cabbage', '大头菜/甘蓝', '蔬菜', '2'),
                   self.record('purple', '紫甘蓝', '蔬菜', '99'),
                   self.record('heart', '牛心甘蓝', '蔬菜', '100')]
        for alias, code in [('蒜苔', 'sprout'), ('蒜薹', 'sprout'), ('甘蓝', 'cabbage')]:
            with self.subTest(alias=alias):
                self.assertEqual(self.row(alias, records)['selected']['code'], code)
        self.assertEqual(self.row('紫甘蓝', records)['selected']['code'], 'purple')
        self.assertIsNone(self.row('甘蓝', records, notes='冷冻')['selected'])

    def test_full_oyster_series_and_explicit_bottle_resolve_current_size(self):
        records = [self.record('series', '海天上等蚝油 1kg/瓶', '调料类', '15', '1kg'),
                   self.record('other', '海天蚝油700g/瓶', '调料类', '99', '700g')]
        row = self.row('海天上等耗油', records, '瓶')
        self.assertEqual(row['selected']['code'], 'series')
        self.assertEqual(row['price_unit'], '瓶')
        self.assertEqual(row['invoice_quantity'], '2')
        self.assertIsNone(self.row('海天上等耗油', records, '箱')['selected'])
        self.assertIsNone(self.row('海天上等耗油', records, '瓶', '545g')['selected'])

    def test_canned_corn_never_crosses_frozen_or_different_pack_sizes(self):
        records = [self.record('a', '美国厨师原味玉米粒425g/罐', '调料类', '10', '425g'),
                   self.record('b', '甘竹玉米粒罐头', '方便食品', '8', '425g'),
                   self.record('frozen', '冻玉米粒嘉利得0.9kg*10袋/件', '调料类', '99', '0.9kg')]
        row = self.row('玉米粒罐头', records, '罐')
        self.assertEqual(row['selected']['code'], 'a')
        self.assertEqual(row['invoice_quantity'], '2')
        self.assertIsNone(self.row('玉米粒罐头', records, '袋')['selected'])
        records[1]['spec'] = '800g'
        self.assertIsNone(self.row('玉米粒罐头', records, '罐')['selected'])

    def test_generic_banana_prefers_current_price_but_respects_explicit_brand(self):
        records = [self.record('a', '优质香蕉', '水果类', '4'),
                   self.record('b', '优选佳农香蕉', '水果类', '6')]
        self.assertEqual(self.row('香蕉', records)['selected']['code'], 'b')
        self.assertEqual(self.row('普通香蕉', records)['selected']['code'], 'b')
        self.assertIsNone(self.row('香蕉', records, notes='臻园牌')['selected'])
        records[0]['code'] = 'new-current-code'
        records[0]['price'] = '7'
        self.assertEqual(self.row('香蕉', records)['selected']['code'], 'new-current-code')

    def test_unknown_units_keep_identity_candidates_without_pricing(self):
        records = [self.record('sprout', '蒜苔（绿帽）', '蔬菜', '8')]
        row = self.row('蒜苔', records, '捆')
        self.assertIsNone(row['invoice_quantity'])
        self.assertEqual({r['code'] for r in row['candidates']}, {'sprout'})
        self.assertIsNone(self.row('蒜苔', [], '斤')['selected'])

    def test_trailing_variety_and_thickness_cannot_be_dropped(self):
        records = [self.record('tofu', '北大荒干豆腐', '豆制品', '7'),
                   self.record('sprout', '蒜苔（绿帽）', '蔬菜', '8'),
                   self.record('cabbage', '大头菜/甘蓝', '蔬菜', '2'),
                   self.record('banana', '优选佳农香蕉', '水果类', '6')]
        for alias, notes in [('干豆腐', '加厚'), ('蒜苔', '红帽'),
                             ('甘蓝', '牛心'), ('香蕉', '小米蕉')]:
            with self.subTest(alias=alias, notes=notes):
                row = self.row(alias, records, notes=notes)
                self.assertIsNone(row['selected'])
                self.assertIsNone(row['invoice_quantity'])

    def test_lower_pork_belly_retains_explicit_cut_and_processing(self):
        records = [self.record('lower', '云宴三花下五花肉', '肉制品', '20'),
                   self.record('peeled', '冷鲜去皮下五花（金锣）', '肉制品', '99'),
                   self.record('general', '鲜猪五花肉（金锣）', '肉制品', '100')]
        row = self.row('下五花肉', records)
        self.assertEqual(row['selected']['code'], 'lower')
        peeled = self.row('下五花肉', records, notes='去皮')
        self.assertEqual(peeled['selected']['code'], 'peeled')
        self.assertEqual(peeled['invoice_quantity'], '2')
        self.assertIsNone(self.row('下五花肉', records[:1], notes='去皮')['selected'])
        self.assertIsNone(self.row('下五花肉', records, notes='去皮切片')['invoice_quantity'])
        self.assertIsNone(self.row('下五花肉', records, notes='金锣')['selected'])
        for notes in ['不要去皮', '不去皮', '带皮']:
            with self.subTest(notes=notes):
                result = self.row('下五花肉', records, notes=notes)
                self.assertIsNone(result['selected'])
                self.assertIsNone(result['invoice_quantity'])

    def test_red_sausage_compares_plain_current_records_and_respects_brand(self):
        records = [self.record('a', '一手店红肠', '方便食品', '40'),
                   self.record('b', '农大红肠', '方便食品', '46'),
                   self.record('c', '秋林里道斯红肠', '方便食品', '41'),
                   self.record('flavored', '风味红肠', '方便食品', '99')]
        self.assertEqual(self.row('红肠', records)['selected']['code'], 'b')
        self.assertIsNone(self.row('红肠', records, notes='安井')['selected'])
        records[0]['code'] = 'current-new'
        records[0]['price'] = '47'
        self.assertEqual(self.row('红肠', records)['selected']['code'], 'current-new')
        records[0]['spec'] = '1kg'
        self.assertIsNone(self.row('红肠', records)['selected'])

    def test_starch_sausage_does_not_cross_to_other_sausage_or_sliced_form(self):
        records = [self.record('a', '裕昌粉肠', '方便食品', '23'),
                   self.record('b', '农大粉肠', '方便食品', '22'),
                   self.record('c', '一手店粉肠', '方便食品', '34'),
                   self.record('d', '哈信诚粉肠', '方便食品', '14'),
                   self.record('flavored', '风味粉肠', '方便食品', '99')]
        self.assertEqual(self.row('普通粉肠', records)['selected']['code'], 'c')
        self.assertIsNone(self.row('粉肠', records, notes='切片')['invoice_quantity'])
        self.assertIsNone(self.row('粉肠', [], '斤')['selected'])

    def test_plain_wood_ear_does_not_promote_small_variety(self):
        records = [self.record('ordinary', '优质木耳', '调料类', '40'),
                   self.record('small', '木耳黑山秋木耳/小木耳（好）', '调料类', '99')]
        self.assertEqual(self.row('木耳', records)['selected']['code'], 'ordinary')
        self.assertIsNone(self.row('木耳', records, notes='熟制')['selected'])
        self.assertIsNone(self.row('木耳', records, notes='小木耳')['selected'])

    def test_arctic_shrimp_weight_selects_current_same_concept(self):
        records = [self.record('current', '冷冻北极冰虾80*100', '水产品', '39.99'),
                   self.record('other', '冷冻青虾4050', '水产品', '99')]
        for unit, expected in [('斤', '2'), ('kg', '4')]:
            with self.subTest(unit=unit):
                row = self.row('北极冰虾80*100', records, unit)
                self.assertEqual(row['status'], 'matched')
                self.assertEqual(row['selected']['code'], 'current')
                self.assertEqual(row['invoice_quantity'], expected)
        changed = copy.deepcopy(records)
        changed[0].update(code='new-code', price='50')
        updated = self.row('北极冰虾80*100', changed)
        self.assertIsNotNone(updated['selected'])
        self.assertEqual(updated['selected']['code'], 'new-code')
        self.assertEqual(updated['selected']['price'], '50')
        changed[0]['spec'] = '1kg'
        row = self.row('北极冰虾80*100', changed)
        self.assertEqual(row['invoice_quantity'], '1')
        self.assertEqual(row['price_unit'], '1kg')
        changed[0]['spec'] = '250g'
        row = self.row('北极冰虾80*100', changed)
        self.assertEqual(row['invoice_quantity'], '4')
        self.assertEqual(row['price_unit'], '250g')
        for spec in ('袋', '箱', '规格不明'):
            with self.subTest(spec=spec):
                changed[0]['spec'] = spec
                self.assertNotEqual(self.row('北极冰虾80*100', changed)['status'], 'matched')
        self.assertIsNone(self.row('北极冰虾80*100', changed[1:])['selected'])
        self.assertIsNone(self.row('北极冰虾80*100', records, '袋')['invoice_quantity'])

    def test_arctic_shrimp_explicit_fresh_live_and_nonfrozen_never_select_frozen(self):
        from order_mapping_core.parsing import parse_chat
        records = [self.record('frozen', '冷冻北极冰虾80*100', '水产品', '39.99')]
        aliases = ['北极冰虾80*100', '冷冻北极冰虾80*100']
        raw_cases = [
            *(f'北极冰虾80*100 2斤（{suffix}）' for suffix in
              ('鲜', '鲜虾', '活虾', '鲜活', '不要冷冻', '非冷冻', '不要冻的')),
            *(f'北极冰虾80*100（{suffix}）2斤' for suffix in
              ('鲜', '鲜虾', '活虾', '鲜活', '不要冷冻', '非冷冻', '不要冻的')),
            *(f'2斤北极冰虾80*100（{suffix}）' for suffix in
              ('鲜', '鲜虾', '活虾', '鲜活', '不要冷冻', '非冷冻', '不要冻的')),
            *(f'冷冻北极冰虾80*100 2斤（{suffix}）' for suffix in
              ('鲜', '鲜虾', '活虾', '鲜活', '不要冷冻', '非冷冻', '不要冻的')),
            '鲜北极冰虾80*100 2斤', '活北极冰虾80*100 2斤',
            '2斤北极冰虾80*100不要冷冻', '冷冻北极冰虾80*100 2斤（非冷冻）',
            '臻园牌北极冰虾80*100 2斤', '北极冰虾80*100 2斤（臻园牌）',
            '北极冰虾80*120 2斤', '北极冰虾80*100 2斤（80*120）',
        ]
        for raw in raw_cases:
            with self.subTest(raw=raw):
                rows = parse_chat(raw, product_names=aliases)
                self.assertEqual(len(rows), 1)
                row = self.match(rows[0], records, self.kb)
                self.assertIsNone(row['selected'])
                self.assertIsNone(row['invoice_quantity'])

    def test_arctic_shrimp_explicit_frozen_is_allowed(self):
        records = [self.record('frozen', '冷冻北极冰虾80*100', '水产品', '39.99')]
        row = self.row('北极冰虾80*100', records, notes='冷冻')
        self.assertIsNotNone(row['selected'])
        self.assertEqual(row['selected']['code'], 'frozen')
        self.assertEqual(row['invoice_quantity'], '2')

if __name__=='__main__':unittest.main()
