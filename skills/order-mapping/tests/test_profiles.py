import copy
import hashlib
import importlib
import sys
import tempfile
from pathlib import Path
import unittest
from openpyxl import Workbook
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
TMP=Path(__file__).resolve().parents[3]/'evaluation/tmp'
TMP.mkdir(exist_ok=True,parents=True)

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=TMP);self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.api=importlib.import_module('order_mapping_core.profiles')
        from order_mapping_core.catalog import CatalogStore
        b=Workbook();b.active.append(['类别名称','材料编号','材料名称','挂牌价','规格']);b.active.append(['蔬菜','1','蒜苔（绿帽）','8.99','500g'])
        self.path=self.root/'price.xlsx';b.save(self.path)
        self.store=CatalogStore(self.root/'runtime');self.catalog=self.store.register(self.path)
        self.reference=self.root/'人工订单.txt'
        self.reference.write_text('用户提供的人工参考，蒜薹对应绿帽蒜苔。',encoding='utf-8')
        self.profile={'schema_version':1,'profile_id':'station-v1','company_id':'station',
                      'catalog_version':self.catalog['version_id'],'evidence':{'kind':'user_provided_reference','source':self.reference.name,'sha256':hashlib.sha256(self.reference.read_bytes()).hexdigest()},
                      'aliases':{'蒜薹':{'code':'1','evidence':'人工订单第41行，原文蒜薹6斤'}},'unit_rules':{}}

    def test_profile_persists_scoped_company_and_invalidates_with_new_catalog(self):
        self.api.register_profile(self.root/'runtime',self.profile,source_path=self.reference)
        p=self.api.current_profile(self.root/'runtime','station',self.catalog)
        self.assertEqual(p['aliases']['蒜薹']['code'],'1')
        self.assertIsNone(self.api.current_profile(self.root/'runtime','other',self.catalog))
        b=Workbook();b.active.append(['类别名称','材料编号','材料名称','挂牌价','规格']);b.active.append(['蔬菜','1','蒜苔（绿帽）','9','500g']);b.save(self.path)
        new=self.store.register(self.path)
        with self.assertRaises(ValueError):self.api.current_profile(self.root/'runtime','station',new)

    def test_profile_cannot_contain_historical_weight_or_price_and_sku_must_exist(self):
        for extra in [{'actual_quantity':'6.24'},{'price':'9'},{'invoice_quantity':'6'}]:
            p=copy.deepcopy(self.profile);p['aliases']['蒜薹'].update(extra)
            with self.assertRaises(ValueError):self.api.register_profile(self.root/'runtime',p)
        p=copy.deepcopy(self.profile);p['aliases']['蒜薹']['code']='deleted'
        with self.assertRaises(ValueError):self.api.register_profile(self.root/'runtime',p)

    def test_profile_requires_source_and_conflicting_versions_are_rejected(self):
        p=copy.deepcopy(self.profile);p.pop('evidence')
        with self.assertRaises(ValueError):self.api.register_profile(self.root/'runtime',p)
        p=copy.deepcopy(self.profile);p['catalog_version']='old'
        with self.assertRaises(ValueError):self.api.register_profile(self.root/'runtime',p)

    def test_profile_does_not_override_explicit_unit_scope(self):
        from order_mapping_core.matching import match_item
        r=self.catalog['records'][0]
        profile=self.profile['aliases']['蒜薹']
        profile['request_units']=['斤']
        row=match_item({'name':'蒜薹','quantity':'1','unit':'箱','issues':[]},[r],aliases={'蒜薹':profile})
        self.assertNotEqual(row['product_status'],'matched')

    def test_alias_accepts_spacing_and_common_orthographic_correction(self):
        from order_mapping_core.matching import match_item
        rec={'code':'oil','name':'海天上等蚝油1kg/瓶','spec':'1kg','price':'14.90'}
        for name in ['海天上等耗油','海天上等蚝油','海天 上等 蚝油']:
            row=match_item({'name':name,'quantity':'2','unit':'瓶','issues':[]},[rec],aliases={'海天上等耗油':{'code':'oil'}})
            self.assertEqual(row['selected']['code'],'oil')
        row=match_item({'name':'海天蚝油','quantity':'1','unit':'瓶','issues':[]},[rec],aliases={'海天耗油':'oil','海天蚝油':'different'})
        self.assertEqual(row['status'],'ambiguous')

    def test_registration_verifies_actual_reference_bytes(self):
        with self.assertRaisesRegex(ValueError,'来源'):
            self.api.register_profile(self.root/'runtime',self.profile)
        p=copy.deepcopy(self.profile);p['evidence']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'指纹'):
            self.api.register_profile(self.root/'runtime',p,source_path=self.reference)

    def test_historical_alias_cannot_replace_an_explicit_brand(self):
        from order_mapping_core.matching import match_item
        rec={'code':'sausage','name':'农大红肠','spec':'500g','price':'46.47'}
        row=match_item({'name':'秋林红肠','quantity':'2','unit':'斤','issues':[]},[rec],aliases={'秋林红肠':'sausage'})
        self.assertIsNone(row['selected'])
        self.assertTrue(any(issue['code']=='brand_conflict' for issue in row['issues']))
        catalog={'version_id':self.catalog['version_id'],'records':[rec]}
        p=copy.deepcopy(self.profile)
        p['aliases']={'秋林红肠':{'code':'sausage','evidence':'人工表误映射'}}
        with self.assertRaisesRegex(ValueError,'品牌'):
            self.api.validate_profile(p,catalog)
