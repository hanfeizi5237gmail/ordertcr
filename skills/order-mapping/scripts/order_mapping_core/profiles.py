"""Company-scoped reference preferences, without historical weights or prices."""
import hashlib
import json
from pathlib import Path
import re
import os
import tempfile
from .catalog import CatalogStore, catalog_lock
from .money import decimal_value
from .matching import brand_conflict


def _bytes(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()


def _company(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',value):
        raise ValueError('profile company_id须为1到80位字母数字下划线或连字符')
    return value


def validate_profile(profile,catalog):
    if not isinstance(profile,dict) or type(profile.get('schema_version')) is not int or profile['schema_version']!=1:
        raise ValueError('不支持的profile schema_version')
    if set(profile)-{'schema_version','profile_id','company_id','catalog_version','evidence','aliases','unit_rules','notes'}:
        raise ValueError('profile含不支持字段，禁止历史数量或价格覆盖')
    _company(profile.get('company_id'))
    if not profile.get('profile_id') or profile.get('catalog_version')!=catalog['version_id']:
        raise ValueError('profile必须具名并绑定当前目录版本')
    evidence=profile.get('evidence')
    if not isinstance(evidence,dict) or not evidence.get('source') or not re.fullmatch(r'[0-9a-f]{64}',str(evidence.get('sha256',''))):
        raise ValueError('profile需提供人工参考来源及SHA-256，不得猜测偏好')
    if evidence.get('kind') not in {'user_provided_reference','user_confirmed_preferences'}:
        raise ValueError('profile必须来源于用户提供参考或明确偏好')
    records={str(x['code']):x for x in catalog['records']}
    aliases=profile.get('aliases',{})
    if not isinstance(aliases,dict):raise ValueError('profile aliases必须为对象')
    for source,entry in aliases.items():
        if not isinstance(source,str) or not source or not isinstance(entry,dict):
            raise ValueError('profile alias须为非空原品名与有据条目')
        if set(entry)-{'code','evidence','reason','request_units','notes'}:
            raise ValueError('历史别名不得存储数量或覆盖目录价格')
        if str(entry.get('code')) not in records or not entry.get('evidence'):
            raise ValueError('历史别名SKU须在当前目录，并有来源证据')
        if brand_conflict(source,records[str(entry['code'])]):
            raise ValueError(f'历史偏好与原文明确品牌冲突：{source}')
        if 'request_units' in entry and (not isinstance(entry['request_units'],list) or not all(isinstance(x,str) and x for x in entry['request_units'])):
            raise ValueError('别名适用单位须为字符串数组')
    rules=profile.get('unit_rules',{})
    if not isinstance(rules,dict):raise ValueError('profile unit_rules必须为对象')
    for code,rule in rules.items():
        if code not in records or not isinstance(rule,dict):raise ValueError('profile单位规则需当前SKU')
        if set(rule)-{'price_unit','request_units','grams_per_unit','ml_per_unit','allow_fractional','evidence'}:
            raise ValueError('profile单位规则含不支持字段')
        if not rule.get('evidence') or not isinstance(rule.get('price_unit'),str) or not rule['price_unit']:
            raise ValueError('profile包装规则需计价单位及依据')
        ratios=rule.get('request_units',{})
        if not isinstance(ratios,dict):raise ValueError('request_units须为对象')
        for factor in ratios.values():decimal_value(factor,'包装换算比例',positive=True)
        for key in ['grams_per_unit','ml_per_unit']:
            if key in rule:decimal_value(rule[key],key,positive=True)
        if 'allow_fractional' in rule and not isinstance(rule['allow_fractional'],bool):raise ValueError('allow_fractional须为布尔值')
    return profile


def register_profile(runtime_root,profile,*,source_path=None):
    with catalog_lock(runtime_root):
        catalog=CatalogStore(runtime_root).current()
        validate_profile(profile,catalog)
        if source_path is None:
            raise ValueError('偏好注册必须提供真实参考来源文件source_path/--source以核验指纹')
        source=Path(source_path)
        if not source.is_file():raise ValueError('偏好参考来源文件不存在')
        if hashlib.sha256(source.read_bytes()).hexdigest()!=profile['evidence']['sha256']:
            raise ValueError('偏好参考来源文件指纹与evidence.sha256不符')
        encoded=_bytes(profile);digest=hashlib.sha256(encoded).hexdigest()
        root=Path(runtime_root)/'profiles'/_company(profile['company_id'])
        root.mkdir(parents=True,exist_ok=True)
        snapshot=root/(digest+'.json')
        if snapshot.exists() and snapshot.read_bytes()!=encoded:raise ValueError('偏好快照损坏')
        if not snapshot.exists():snapshot.write_bytes(encoded)
        fd,temp=tempfile.mkstemp(dir=root,prefix='.profile-')
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(_bytes({'sha256':digest,'snapshot':snapshot.name}));stream.flush();os.fsync(stream.fileno())
            os.replace(temp,root/'current.json')
        finally:Path(temp).unlink(missing_ok=True)
        return {'profile_id':profile['profile_id'],'company_id':profile['company_id'],'sha256':digest,
                'alias_count':len(profile.get('aliases',{})),'unit_rule_count':len(profile.get('unit_rules',{}))}


def current_profile(runtime_root,company_id,catalog):
    root=Path(runtime_root)/'profiles'/_company(company_id)
    if not (root/'current.json').exists():return None
    pointer=json.loads((root/'current.json').read_text(encoding='utf-8'))
    digest=pointer['sha256']
    if not re.fullmatch(r'[0-9a-f]{64}',str(digest)):raise ValueError('偏好指针损坏')
    data=(root/(digest+'.json')).read_bytes()
    if hashlib.sha256(data).hexdigest()!=digest:raise ValueError('偏好快照指纹不符')
    profile=json.loads(data)
    if profile.get('catalog_version')!=catalog['version_id']:
        raise ValueError('采购偏好绑定的目录版本已失效，需重新核定；不会沿用旧SKU/价格')
    validate_profile(profile,catalog)
    return profile
