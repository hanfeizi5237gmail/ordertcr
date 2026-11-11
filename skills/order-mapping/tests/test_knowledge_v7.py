"""Selection tiers preserve generic demand without hiding review candidates."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class SharedSelectionTierTests(unittest.TestCase):
    def setUp(self):
        from order_mapping_core.knowledge import match_shared
        self.match = match_shared
        self.records = [
            {'code': 'ordinary', 'name': '窝瓜', 'category': '蔬菜', 'spec': '500g', 'price': '2'},
            {'code': 'special', 'name': '贝贝窝瓜', 'category': '蔬菜', 'spec': '500g', 'price': '9'},
        ]
        self.kb = {'schema_version': 1, 'rules': [{
            'id': 'generic_pumpkin', 'concept': '普通窝瓜优先，特殊品种供核对',
            'aliases': ['普通窝瓜'],
            'selector': {'names': ['窝瓜', '贝贝窝瓜'], 'category': '蔬菜'},
            'selection_selector': {'names': ['窝瓜'], 'category': '蔬菜'},
            'conditions': {}, 'kind': 'synonym', 'applicable': 'general',
            'evidence': [{'source': 'test catalog', 'reason': '普通同概念优先于特殊品种'}],
            'conflicts': ['不适用指定贝贝品种'], 'validation_status': 'reviewed',
        }], 'unit_rules': []}

    def test_generic_layer_precedes_higher_price_specialty_and_keeps_candidates(self):
        row = self.match({'name': '普通窝瓜', 'quantity': '2', 'unit': '斤'}, self.records, self.kb)
        self.assertEqual(row['selected']['code'], 'ordinary')
        self.assertEqual({r['code'] for r in row['candidates']}, {'ordinary', 'special'})
        self.assertEqual(row['invoice_quantity'], '2')

    def test_missing_primary_in_updated_catalog_never_falls_back_to_specialty(self):
        row = self.match({'name': '普通窝瓜', 'quantity': '2', 'unit': '斤'}, self.records[1:], self.kb)
        self.assertIsNone(row['selected'])
        self.assertIsNone(row['invoice_quantity'])
        self.assertEqual({r['code'] for r in row['candidates']}, {'special'})

    def test_known_identity_survives_unknown_piece_weight(self):
        row = self.match({'name': '普通窝瓜', 'quantity': '1', 'unit': '个'}, self.records, self.kb)
        self.assertEqual(row['selected']['code'], 'ordinary')
        self.assertEqual(row['product_status'], 'matched')
        self.assertEqual(row['quantity_status'], 'needs_review')
        self.assertIsNone(row['invoice_quantity'])
        self.assertNotEqual(row['status'], 'matched')

    def test_current_codes_and_prices_reresolve_within_primary_layer(self):
        changed = copy.deepcopy(self.records)
        changed[0].update(code='new-ordinary', price='3')
        row = self.match({'name': '普通窝瓜', 'quantity': '2', 'unit': '斤'}, changed, self.kb)
        self.assertEqual(row['selected']['code'], 'new-ordinary')
        self.assertEqual(row['selected']['price'], '3')

    def test_selection_selector_cannot_bind_sku_or_old_price(self):
        from order_mapping_core.knowledge import validate_knowledge
        for key in ('code', 'sku', 'price', 'row', 'invoice_quantity'):
            with self.subTest(key=key):
                kb = copy.deepcopy(self.kb)
                kb['rules'][0]['selection_selector'][key] = 'forbidden'
                with self.assertRaises(ValueError):
                    validate_knowledge(kb)

    def test_malformed_selectors_fail_before_matching(self):
        from order_mapping_core.knowledge import validate_knowledge
        for selector in ({'named': ['窝瓜']}, {'names': []}, {'names': 123},
                         {'names': ['']}, {'category': []}, {'name_contains': []}):
            for selector_key in ('selector', 'selection_selector'):
                with self.subTest(selector=selector, field=selector_key):
                    kb = copy.deepcopy(self.kb)
                    kb['rules'][0][selector_key] = selector
                    with self.assertRaises(ValueError):
                        validate_knowledge(kb)

    def test_unit_rules_reject_unscoped_selectors_and_invalid_factors(self):
        from order_mapping_core.knowledge import validate_knowledge
        for selector, factor in [({}, '1'), ({'named': ['窝瓜']}, '1'),
                                 ({'names': []}, '1'), ({'names': ['窝瓜']}, '0'),
                                 ({'names': ['窝瓜']}, 'NaN')]:
            with self.subTest(selector=selector, factor=factor):
                kb = copy.deepcopy(self.kb)
                kb['unit_rules'] = [{'id': 'bad_pack', 'selector': selector,
                    'request_unit': '袋', 'price_unit': '袋', 'factor': factor,
                    'applicable': 'conditional', 'evidence': [{'source': 'test'}],
                    'validation_status': 'reviewed', 'conflicts': ['需明确包装']}]
                with self.assertRaises(ValueError):
                    validate_knowledge(kb)

    def test_catalog_audit_exposes_missing_selection_layer(self):
        from order_mapping_core.knowledge import catalog_knowledge_audit
        audit = catalog_knowledge_audit(self.kb, self.records[1:])
        self.assertEqual(audit[0]['status'], 'selection_selector_missing')
        self.assertEqual(audit[0]['selection_candidate_codes'], [])

    def test_failed_generic_condition_still_retrieves_review_candidates(self):
        kb = copy.deepcopy(self.kb)
        kb['rules'][0]['kind'] = 'generic_request'
        kb['rules'][0]['conditions'] = {'notes_excludes': ['不要普通']}
        row = self.match({'name': '普通窝瓜', 'quantity': '2', 'unit': '斤',
                          'notes': '不要普通'}, self.records, kb)
        self.assertIsNone(row['selected'])
        self.assertEqual(row.get('knowledge_rule_ids'), ['generic_pumpkin'])
        self.assertEqual({r['code'] for r in row['candidates']}, {'ordinary', 'special'})

    def test_catalog_audit_reports_partially_missing_names(self):
        from order_mapping_core.knowledge import catalog_knowledge_audit
        kb = copy.deepcopy(self.kb)
        kb['rules'][0]['selector']['names'].append('已下架窝瓜')
        audit = catalog_knowledge_audit(kb, self.records)
        self.assertEqual(audit[0]['missing_names'], ['已下架窝瓜'])
        self.assertEqual(audit[0]['status'], 'selector_partially_missing')

    def test_expression_attribute_word_collision_fails_closed(self):
        from order_mapping_core.knowledge import validate_knowledge
        kb = copy.deepcopy(self.kb)
        kb['expression_rules'] = [{
            'id': 'collision', 'kind': 'attribute_order', 'concept': 'test',
            'attributes': {'鲜': ['鲜'], '鲜活': ['鲜']},
            'category_labels': {}, 'pricing_labels': {}, 'protected_compounds': [],
            'evidence': [{'source': 'test', 'locator': 'fixture'}],
            'applicable': 'conditional', 'conflicts': ['must fail closed'],
            'validation_status': 'reviewed',
        }]
        with self.assertRaises(ValueError):
            validate_knowledge(kb)


class CatalogBackedV7KnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from order_mapping_core.catalog import _parse_catalog
        from order_mapping_core.knowledge import load_knowledge, match_shared
        catalog = Path(__file__).resolve().parents[3] / 'original/食堂食材基准价价格表10.12-10.18.xlsx'
        if not catalog.exists():
            raise unittest.SkipTest('业务原目录不随通用skill分发')
        cls.records = _parse_catalog(catalog.read_bytes())
        cls.kb, _ = load_knowledge()
        cls.match = staticmethod(match_shared)

    def test_supported_generic_requests_and_known_identity_unknown_quantity(self):
        cases = [
            ('西红柿', '2', '斤', '', '毛粉柿子', '2'),
            ('豆角', '5', '斤', '', '架豆王（豆角）', '5'),
            ('洋葱', '4', '斤', '', '优质紫洋葱', '4'),
            ('菜花', '6', '斤', '', '青梗菜花', '6'),
            ('茄子', '10', '斤', '', '地产茄子', '10'),
            ('豆芽', '3', '斤', '', '北大荒绿豆芽', '3'),
            ('甘蓝', '3', '颗', '', '大头菜/甘蓝', None),
            ('窝瓜', '1', '个', '', '窝瓜', None),
            ('娃娃菜', '1', '包', '', '娃娃菜袋装', None),
            ('娃娃菜', '2', '袋', '', '娃娃菜袋装', '2'),
            ('豆腐', '7', '盒', '', '北大荒内酯豆腐400G/盒', '7'),
            ('味素', '5', '斤', '', '中粮味素预装（味精）', '5'),
            ('辣椒段', '3', '斤', '', '干辣椒段', '3'),
            ('粉条', '3', '斤', '', '粉条按斤', '3'),
            ('番茄沙司', '2', '袋', '', '亨氏番茄沙司 320g/袋', '2'),
            ('一品鲜酱油', '1', '桶', '', '东古一品鲜酱油5L/桶', '1'),
            ('东古酱油', '2', '瓶', '', '东古一品鲜500ML/瓶', '2'),
            ('鸡柳', '2', '袋', '', '正大雪花鸡柳800g/袋', '2'),
            ('鸡腿', '5', '斤', '切块', '正大鸡腿肉切块', '5'),
            ('松花鸡腿', '2', '个', '', '农大松花鸡腿', '2'),
            ('鲤鱼', '3', '条', '', '鲜活江鲤鱼', None),
            ('血肠', '1', '根', '', '风味血肠', None),
            ('刀鱼', '5', '斤', '', '冷鲜刀鱼350-450', '5'),
            ('猪肉馅', '2', '斤', '', '鲜猪前槽肉馅（金锣）', '2'),
            ('五花肉', '10', '斤', '', '冷鲜精五花肉（金锣）', '10'),
            ('秋林红肠', '2', '斤', '', '秋林里道斯红肠', '2'),
            ('肉丝', '3', '斤', '', '精肉丝称重（金锣）', '3'),
            ('牛奶', '3', '箱', '', '完达山臻醇牛奶250ml', '3'),
        ]
        for name, quantity, unit, notes, expected_name, invoice in cases:
            with self.subTest(name=name, unit=unit):
                row = self.match({'name': name, 'quantity': quantity, 'unit': unit, 'notes': notes,
                                  'raw_text': name + quantity + unit + notes}, self.records, self.kb)
                self.assertEqual((row.get('selected') or {}).get('name'), expected_name)
                self.assertEqual(row['invoice_quantity'], invoice)
                self.assertEqual(row['quantity'], quantity)
                self.assertEqual(row['unit'], unit)

    def test_explicit_variety_brand_form_and_negation_do_not_use_generic_choice(self):
        cases = [
            ('西红柿', '要黄色圣女果'), ('豆角', '要豇豆'), ('洋葱', '要黄洋葱'),
            ('菜花', '不要青梗'), ('茄子', '要小黑茄子'), ('豆芽', '要黄豆芽豆嘴'),
            ('豆腐', '不要内酯，要老豆腐'), ('粉条', '红薯宽粉'), ('辣椒段', '要鲜的'),
            ('番茄沙司', '要味好美340g'), ('一品鲜酱油', '要千禾'),
            ('东古酱油', '不要一品鲜'), ('鸡柳', '要川香'), ('鸡腿', '不要切块'),
            ('五花肉', '要下五花去皮'), ('肉丝', '要牛肉'), ('牛奶', '要脱脂'),
        ]
        for name, notes in cases:
            with self.subTest(name=name, notes=notes):
                unit = '袋' if name in ('鸡柳', '番茄沙司') else '桶' if name == '一品鲜酱油' else '瓶' if name == '东古酱油' else '箱' if name == '牛奶' else '斤'
                row = self.match({'name': name, 'quantity': '2', 'unit': unit, 'notes': notes,
                                  'raw_text': name + '2' + unit + notes}, self.records, self.kb)
                self.assertIsNone(row['selected'])
                self.assertIsNone(row['invoice_quantity'])

    def test_unproven_species_and_explicit_unavailable_attributes_remain_pending(self):
        for name, unit in [('里脊', '斤'), ('护心肉', '斤'), ('小黑茄子', '斤'),
                           ('厚皮鸡蛋', '筐'), ('生腰果仁', '斤')]:
            with self.subTest(name=name):
                row = self.match({'name': name, 'quantity': '2', 'unit': unit}, self.records, self.kb)
                self.assertIsNone(row['selected'])
                self.assertIsNone(row['invoice_quantity'])

    def test_negative_requirements_never_auto_select_the_rejected_brand_or_series(self):
        for name, unit, notes in [
            ('番茄沙司', '袋', '不用亨氏'),
            ('东古酱油', '瓶', '别选一品鲜'),
            ('鸡腿', '斤', '切块但不要正大'),
            ('肉丝', '斤', '不要金锣'),
            ('洋葱', '斤', '拒绝紫色'),
            ('鸡腿', '斤', '不用切块'),
            ('鸡腿', '斤', '不需要切块'),
        ]:
            with self.subTest(name=name, notes=notes):
                row = self.match({'name': name, 'quantity': '2', 'unit': unit, 'notes': notes,
                                  'raw_text': name + '2' + unit + notes}, self.records, self.kb)
                self.assertIsNone(row['selected'])
                self.assertIsNone(row['invoice_quantity'])

    def test_unverified_positive_requirement_does_not_inherit_generic_choice(self):
        for name, unit, notes in [('西红柿', '斤', '要子弹头柿子'),
                                  ('牛奶', '箱', '要无糖'),
                                  ('猪肉馅', '斤', '要瘦肉')]:
            with self.subTest(name=name, notes=notes):
                row = self.match({'name': name, 'quantity': '2', 'unit': unit, 'notes': notes,
                                  'raw_text': name + '2' + unit + notes}, self.records, self.kb)
                self.assertIsNone(row['selected'])
                self.assertIsNone(row['invoice_quantity'])

    def test_new_same_family_name_in_updated_catalog_requires_review(self):
        records = self.records + [{'code': 'new-catalog-brand', 'name': '测试品牌松花鸡腿',
                                  'category': '方便食品', 'spec': '个', 'price': '99'}]
        row = self.match({'name': '松花鸡腿', 'quantity': '2', 'unit': '个',
                          'raw_text': '松花鸡腿2个'}, records, self.kb)
        self.assertIsNone(row['selected'])
        self.assertIsNone(row['invoice_quantity'])
        self.assertIn('new-catalog-brand', {r['code'] for r in row['candidates']})

    def test_unknown_processing_preserves_identity_and_raw_quantity_but_stays_manual(self):
        for name, notes in [('鲤鱼', '收拾好'), ('鸡腿', '切块（去毛）')]:
            with self.subTest(name=name):
                row = self.match({'name': name, 'quantity': '5', 'unit': '斤', 'notes': notes,
                                  'raw_text': name + '5斤' + notes}, self.records, self.kb)
                self.assertIsNotNone(row['selected'])
                self.assertEqual(row['product_status'], 'matched')
                self.assertEqual(row['quantity'], '5')
                self.assertEqual(row['unit'], '斤')
                self.assertNotEqual(row['status'], 'matched')
                self.assertIn('processing_unverified', {i['code'] for i in row['issues']})


if __name__ == '__main__':
    unittest.main()
