"""Validated batch ledger, recoverable exports, and financial consolidation."""

from collections import Counter
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from .catalog import CatalogStore, catalog_lock
from .export import export_standard, export_finance
from .matching import match_item, brand_conflict
from .money import calculate, cents, decimal_value, discount_value, sum_amounts, text_number
from .parsing import parse_chat
from .profiles import current_profile
from .sequence import NumberStore
from .units import invoice_quantity
from .knowledge import load_knowledge, match_shared, catalog_knowledge_audit, company_knowledge, constraint_conflicts

SCHEMA_VERSION = 1


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.json-', dir=path.parent)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(_encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label}必须为对象')
    return value


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 30000:
        raise ValueError(f'{label}必须为非空文本且不超过30000字符')
    return value.strip()


def _boolean(container, field, default=False):
    result = container.get(field, default)
    if not isinstance(result, bool):
        raise ValueError(f'{field}必须为布尔值')
    return result


def _day(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(f'{label}须为已明确年份的YYYY-MM-DD日期')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f'{label}不是有效日期') from exc


def validate_config(config):
    config = _object(config, 'config')
    if type(config.get('schema_version')) is not int or config.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('不支持的配置schema_version')
    _boolean(config, 'companies_confirmed')
    companies = config.get('companies')
    if not isinstance(companies, list) or not companies:
        raise ValueError('首次需初始化公司全称与简称范围')
    lookup = {}
    for company in companies:
        _object(company, 'company')
        company_id = _text(company.get('company_id'), 'company_id')
        full = _text(company.get('full_name'), '公司全称')
        aliases = company.get('aliases', [])
        if not isinstance(aliases, list):
            raise ValueError('公司aliases必须为数组')
        if company.get('finance_name') is not None:
            _text(company['finance_name'], '财务商户名')
        for key in {company_id, full, *[_text(x, '公司简称') for x in aliases]}:
            key = key.casefold()
            if key in lookup:
                raise ValueError('公司编号/全称/简称跨公司冲突')
            lookup[key] = company_id
    discount_value(config.get('default_discount', '0.95'))
    numbering = _object(config.get('numbering', {}), 'numbering')
    _boolean(numbering, 'confirmed')
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,15}', numbering.get('prefix', 'OR_')):
        raise ValueError('订单编号前缀无效')
    for key, start in {'default': numbering.get('daily_start', 0), **_object(numbering.get('daily_starts', {}), 'daily_starts')}.items():
        if key != 'default':
            _day(key, '号段日期')
        if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start < 999999:
            raise ValueError('流水起点须为0到999998的整数')
    policies = _object(config.get('output_policies', {}), 'output_policies')
    for key in ['standard_order', 'finance']:
        if policies.get(key, 'sample_exact') not in {'sample_exact', 'line_cent'}:
            raise ValueError('金额策略只能为sample_exact或line_cent')
    _boolean(policies, 'dual_confirmed')
    if policies.get('standard_order', 'sample_exact') != policies.get('finance', 'sample_exact') and not policies.get('dual_confirmed', False):
        raise ValueError('订单和财务金额策略不同，须明确确认尾差口径')
    for name in ['aliases', 'unit_rules']:
        _object(config.get(name, {}), name)
    for source, target in config.get('aliases', {}).items():
        _text(source, '商品别名')
        _text(target.get('code') if isinstance(target, dict) else target, '别名SKU')
    for code, rule in config.get('unit_rules', {}).items():
        _text(code, '单位规则SKU')
        _object(rule, '单位规则')
        _text(rule.get('price_unit'), '计价单位')
        for unit, factor in _object(rule.get('request_units', {}), 'request_units').items():
            _text(unit, '请求单位')
            decimal_value(factor, '换算比例', positive=True)
        for key in ['grams_per_unit', 'ml_per_unit']:
            if key in rule:
                decimal_value(rule[key], key, positive=True)
        _boolean(rule, 'allow_fractional', True)
    finance = _object(config.get('finance', {}), 'finance')
    for key in ['confirmed', 'tax_freight_confirmed', 'merchant_names_confirmed']:
        _boolean(finance, key)
    if finance.get('price_source', 'discounted_catalog') != 'discounted_catalog':
        raise ValueError('本版本只支持明确的目录折后销售价，其他财务价格请先补充契约')
    if finance.get('order_number_mode', 'OR') != 'OR':
        raise ValueError('PL与OR未有转换依据，本版本只支持沿用OR编号')
    if finance.get('receipt_date'):
        _day(finance['receipt_date'], '收货日期')
    return {'valid': True, 'company_count': len(companies), 'companies_confirmed': config.get('companies_confirmed', False)}


def _validate_item(item):
    _object(item, '下单项')
    for field in ['line_id', 'raw_text', 'name', 'source_name']:
        _text(item.get(field), field)
    for field in ['source_line', 'char_start', 'char_end']:
        value = item.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < (1 if field == 'source_line' else 0):
            raise ValueError(f'{field}须为有效来源整数')
    if item['char_end'] < item['char_start']:
        raise ValueError('来源字符区间顺序错误')
    if item.get('quantity') is not None:
        invalid = any(issue.get('code') == 'invalid_quantity' for issue in item.get('issues', []))
        decimal_value(item['quantity'], '需求数量', positive=not invalid)
    if item.get('unit') is not None:
        _text(item['unit'], '需求单位')
    if not isinstance(item.get('issues', []), list):
        raise ValueError('issues必须为数组')
    for issue in item.get('issues', []):
        _object(issue, 'issue')
        _text(issue.get('code'), 'issue.code')
        _text(issue.get('message'), 'issue.message')


def _load_inputs(batch, base):
    if ('items' in batch) == ('chat_files' in batch):
        raise ValueError('批次必须且只能提供items或chat_files之一')
    sources = []
    if 'items' in batch:
        items = batch['items']
    else:
        files = batch['chat_files']
        if not isinstance(files, list) or not files:
            raise ValueError('chat_files不能为空')
        items = []
        names = set()
        for value in files:
            path = (base / _text(value, '聊天路径')).resolve()
            if path.name in names:
                raise ValueError('多份聊天同名会冲突，请先使用不同文件名')
            names.add(path.name)
            data = path.read_bytes()
            sources.append({'source_name': path.name, 'sha256': _hash(data)})
            items.extend(parse_chat(data.decode('utf-8-sig'), path.name))
    if not isinstance(items, list) or not items or len(items) > 10000:
        raise ValueError('items须含1到10000项')
    for item in items:
        _validate_item(item)
    if len({x['line_id'] for x in items}) != len(items):
        raise ValueError('line_id必须唯一，禁止静默覆盖来源')
    return items, sources


def _issue(row, code, message):
    row['issues'].append({'code': code, 'message': message})
    if row['status'] == 'matched':
        row['status'] = 'needs_review'


def _decision(item, decision, catalog, unit_rules):
    _object(decision, '逐项决策')
    row = match_item(item, catalog['records'], unit_rules=unit_rules)
    if decision.get('catalog_version') != catalog['version_id']:
        _issue(row, 'stale_decision', '逐项决策未绑定当前目录版本，更新后必须重新核定')
        return row
    reason = _text(decision.get('reason'), '逐项决策依据')
    authority = decision.get('confirmed_by')
    if authority not in {'user', 'agent'}:
        raise ValueError('逐项决策须注明confirmed_by:user或agent及依据')
    if authority == 'agent' and decision.get('selection_basis') != 'constraints_then_highest_price':
        raise ValueError('模型语义选择须声明已筛需求约束并按等价最高价规则选择')
    matches = [x for x in catalog['records'] if str(x['code']) == str(decision.get('code'))]
    if len(matches) != 1:
        _issue(row, 'decision_sku_missing', '决策SKU不在当前目录中')
        return row
    selected = matches[0]
    conflict = brand_conflict(item.get('name'), selected)
    if conflict:
        _issue(row, 'brand_conflict', f'逐项决策与原文明确品牌{conflict}冲突，请补充真实替换需求而非沿用错误映射')
        return row
    conflicts = constraint_conflicts(item, selected)
    if conflicts:
        _issue(row,'attribute_conflict','逐项选择与原文属性冲突：'+','.join(conflicts))
        return row
    row.update(selected=selected, candidates=[selected], status='matched', product_status='matched', match_basis='agent_semantic' if authority == 'agent' else 'user_decision')
    row['decision'] = decision
    row['decision_reason'] = reason
    row['issues'] = [x for x in row['issues'] if x['code'] not in {'semantic_review', 'unmapped', 'different_specs', 'tied_candidates', 'quantity_conversion'}]
    try:
        if 'invoice_quantity' in decision:
            if authority != 'user':
                raise ValueError('数量覆盖必须有用户确认，模型不能推测实重/装箱数')
            quantity = text_number(decimal_value(decision['invoice_quantity'], '核定数量', positive=True))
            price_unit = _text(decision.get('price_unit'), '核定计价单位')
            # Check the nominated invoice unit against the SKU using its own rule.
            check = invoice_quantity(quantity, price_unit, selected, unit_rules)
            if check['quantity'] != quantity:
                raise ValueError('覆盖数量必须已换算为此SKU的计价单位')
            row['issues'] = [x for x in row['issues'] if x['code'] not in {'missing_quantity', 'missing_unit'}]
            converted = check
        else:
            converted = invoice_quantity(item.get('quantity'), item.get('unit'), selected, unit_rules)
        row.update(invoice_quantity=converted['quantity'], price_unit=converted['price_unit'], quantity_status='matched',
                   quantity_basis=decision.get('quantity_basis', 'requested'), decision_reason=reason)
    except ValueError as exc:
        _issue(row, 'quantity_conversion', str(exc))
    return row


def _rules_hash():
    files = sorted(Path(__file__).parent.glob('*.py'))
    return _hash(b''.join(x.name.encode() + x.read_bytes() for x in files))


def _candidate_review(item, row, review, catalog):
    """A current Agent can veto selection and supply an audited complete pool.

    This never promotes a SKU or invents quantities; it is distinct from a
    decision. The input text and current catalog bind the review to this run.
    """
    _object(review,'候选审阅')
    if (review.get('catalog_version')!=catalog['version_id'] or
            review.get('raw_text_sha256')!=_hash(item['raw_text'].encode('utf-8'))):
        raise ValueError('候选审阅须绑定本次原文SHA和当前目录版本')
    if review.get('confirmed_by') not in ('agent','user'):
        raise ValueError('候选审阅须注明审阅者')
    reason=_text(review.get('reason'),'候选审阅理由')
    codes=review.get('candidate_codes')
    if not isinstance(codes,list) or len(set(codes))!=len(codes):raise ValueError('候选编号须为去重数组')
    records={str(r['code']):r for r in catalog['records']}
    if any(str(code) not in records for code in codes):raise ValueError('候选审阅含非当前目录SKU')
    candidates=[]
    for code in codes:
        record=records[str(code)]
        if constraint_conflicts(item,record):raise ValueError('候选审阅与原文明确属性冲突')
        candidates.append({**record,'match_reason':str((review.get('candidate_reasons') or {}).get(str(code),reason))})
    row.update(selected=None,product_status='needs_review' if candidates else 'unmapped',quantity_status='needs_review',invoice_quantity=None,
               candidates=candidates,status='ambiguous' if len(candidates)>1 else 'needs_review' if candidates else 'unmapped',
               candidate_review=review,match_basis='agent_candidate_review',issues=list(item.get('issues') or []))
    row['issues'].append({'code':'semantic_review' if candidates else 'unmapped','message':reason})
    return row


def _verified_manifest(path):
    manifest = json.loads(Path(path).read_text(encoding='utf-8'))
    for entry in manifest['files'].values():
        if _hash(Path(entry['path']).read_bytes()) != entry['sha256']:
            raise ValueError('历史输出指纹不符，需重新生成修订')
    return manifest


def process_batch(runtime_root, batch, output_root, *, base_dir=None):
    """Publish one company/date batch. The catalog lock also serializes exports.

    No number is consumed for review copies. Revisions keep prior directories;
    a reserved number survives failures and is reused on retry.
    """
    batch = json.loads(_encoded(_object(batch, 'batch')))
    if type(batch.get('schema_version')) is not int or batch.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('不支持的批次schema_version')
    batch_id = _text(batch.get('batch_id'), 'batch_id')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', batch_id):
        raise ValueError('batch_id只允许1到80位字母、数字、下划线、连字符')
    for field in ['publish', 'test_only', 'delivery_confirmed', 'date_policy_confirmed']:
        _boolean(batch, field)
    shipping = _day(batch.get('shipping_date'), '发货日期')
    mode = batch.get('mode', 'order')
    if mode not in {'order', 'settlement'}:
        raise ValueError('mode只能为order或settlement')
    date_policy = batch.get('date_policy', 'single')
    if date_policy not in {'single', 'consolidated', 'by_date'}:
        raise ValueError('date_policy无效')
    if date_policy == 'by_date' and batch.get('date_policy_confirmed'):
        raise ValueError('date_policy=by_date当前需按日期拆分为多个批次后分别运行；不会静默合并不同日期')
    root = Path(runtime_root).resolve()
    output = Path(output_root).resolve()
    base = Path(base_dir or '.').resolve()
    config = batch.get('config')
    with catalog_lock(root):
        if config is None:
            config = json.loads((root / 'company-config.json').read_text(encoding='utf-8'))
        validate_config(config)
        batch['config'] = config
        company_name = _text(batch.get('company'), '所属公司')
        companies = [c for c in config['companies'] if company_name.casefold() in {str(x).casefold() for x in [c['company_id'], c['full_name'], *c.get('aliases', [])]}]
        if len(companies) != 1:
            raise ValueError('所属公司不在已初始化的唯一全称/简称范围内')
        company = companies[0]
        catalog_store = CatalogStore(root)
        catalog = catalog_store.current()
        items, sources = _load_inputs(batch, base)
        decisions = _object(batch.get('decisions', {}), 'decisions')
        if set(decisions) - {x['line_id'] for x in items}:
            raise ValueError('decisions含不存在的line_id')
        candidate_reviews = _object(batch.get('candidate_reviews',{}),'candidate_reviews')
        if set(candidate_reviews)-{x['line_id'] for x in items}:
            raise ValueError('candidate_reviews含不存在的line_id')
        blockers = []
        if date_policy == 'by_date':
            blockers.append('date_policy=by_date尚未按日期拆分并确认；本次仅生成待复核草稿')
        if not config.get('companies_confirmed', False):
            blockers.append('公司全称和简称范围尚未确认')
        if not batch.get('date_policy_confirmed', False):
            blockers.append('发货日期及日期语义尚未确认')
        valid_rules = config.get('catalog_rules_version') == catalog['version_id']
        if (config.get('aliases') or config.get('unit_rules')) and not valid_rules:
            blockers.append('商品别名/单位规则未绑定当前目录版本，旧规则已停用')
        aliases = config.get('aliases', {}) if valid_rules else {}
        unit_rules = config.get('unit_rules', {}) if valid_rules else {}
        matching_mode = batch.get('matching_mode', 'shared')
        if matching_mode not in {'legacy_profile','catalog_only','shared','shared_with_company_contract'}:
            raise ValueError('matching_mode无效')
        # Normal raw-message runs never load reference-fitted per-company answers.
        profile = None
        if matching_mode == 'legacy_profile':
            try:
                profile = current_profile(root, company['company_id'], catalog)
            except ValueError as exc:
                blockers.append(str(exc))
        if profile:
            aliases = {**profile.get('aliases', {}), **aliases}
            unit_rules = {**profile.get('unit_rules', {}), **unit_rules}
        profile_hash = _hash(_encoded(profile)) if profile else None
        knowledge, knowledge_hash, knowledge_audit = None, None, []
        if matching_mode in {'shared','shared_with_company_contract'}:
            knowledge, knowledge_hash = load_knowledge()
            if matching_mode == 'shared_with_company_contract':
                knowledge, contract_hash, contract_ids = company_knowledge(knowledge,company['company_id'])
                knowledge_hash = _hash(_encoded([knowledge_hash,contract_hash,contract_ids]))
            knowledge_audit = catalog_knowledge_audit(knowledge, catalog['records'])
        discount = discount_value(batch.get('discount', config.get('default_discount', '0.95')))
        counts = Counter((x['name'], x.get('quantity'), x.get('unit'), x.get('date_context'))
                         for x in items if x.get('event') not in ('追加', '重复'))
        rows = []
        for item in items:
            decision = decisions.get(item['line_id'])
            row = (_decision(item, decision, catalog, unit_rules) if decision else
                   match_shared(item, catalog['records'], knowledge, unit_rules=unit_rules) if knowledge else
                   match_item(item, catalog['records'], aliases=aliases, unit_rules=unit_rules))
            if item['line_id'] in candidate_reviews:
                row=_candidate_review(item,row,candidate_reviews[item['line_id']],catalog)
            if item.get('event_state') == 'cancellation_record':
                row.update(selected=None,product_status='needs_review',quantity_status='needs_review',
                           invoice_quantity=None,candidates=[],status='needs_review',issues=list(item['issues']))
            context = str(item.get('date_context') or '')
            if context:
                match = re.fullmatch(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})(?:日|号)?', context)
                same_day = bool(match and (not match[1] or int(match[1]) == int(shipping[:4])) and
                                int(match[2]) == int(shipping[5:7]) and int(match[3]) == int(shipping[8:]))
                same_day = same_day or context == shipping
                consolidated = date_policy == 'consolidated' and batch.get('date_policy_confirmed')
                if not same_day and not consolidated:
                    _issue(row, 'date_context', '聊天日期上下文与发货日不同或是日期范围；请明确合并发货或拆分批次')
            target = item.get('target_station')
            if target and target.casefold() not in {str(x).casefold() for x in [company['company_id'],company['full_name'], *company.get('aliases', [])]}:
                _issue(row, 'target_company', '原文明示站点与本批公司范围不一致，请先分配到正确公司')
            if item.get('event') and '这样的' in str(item.get('notes') or '') and not (decision and decision.get('confirmed_by') == 'user' and decision.get('event_resolved') is True):
                _issue(row, 'unresolved_reference', '追加中的“这样的”指代未核定，须确认品种/规格与前单关系')
            identity = (item['name'], item.get('quantity'), item.get('unit'), item.get('date_context'))
            if item.get('event') not in ('追加', '重复') and counts[identity] > 1 and not (decision and decision.get('confirmed_by') == 'user' and decision.get('allow_duplicate') is True):
                _issue(row, 'duplicate_event', '同日同品同数量重复原文均保留，请确认是重复发送还是追加下单')
            event_text = item['raw_text']
            notes = str(item.get('notes') or '')
            if notes and event_text.endswith(notes):
                # Exclude only the explicit processing instruction. A separate
                # cancellation in the same note must still trigger review.
                event_notes = re.sub(r'[（(]\s*不要\s*去皮\s*[）)]|^不要\s*去皮$', '', notes)
                event_text = event_text[:-len(notes)] + event_notes
            if re.search(r'取消|改为|改成|不要|增加|减少', event_text) and not item.get('event_resolution') and not (decision and decision.get('confirmed_by') == 'user' and decision.get('event_resolved') is True):
                _issue(row, 'order_event', '发现改量/取消事件，须与前单核定后再发布')
            if item.get('event_state') == 'pending':
                row.update(invoice_quantity=None, quantity_status='needs_review', status='needs_review')
            row.update(discount=discount, unit_price=None, amount=None, finance_amount=None)
            if row.get('product_status') == 'matched':
                row['unit_price'] = calculate(row['selected']['price'], discount, '1', 'sample_exact')['unit_price']
            # Parse issues remain unresolved unless explicitly corrected by a decision.
            if row['issues'] and row['status'] == 'matched':
                row['status'] = 'needs_review'
            if row['status'] == 'matched':
                policy = config.get('output_policies', {})
                money = calculate(row['selected']['price'], discount, row['invoice_quantity'], policy.get('standard_order', 'sample_exact'))
                row.update(money)
                row['finance_amount'] = calculate(row['selected']['price'], discount, row['invoice_quantity'], policy.get('finance', 'sample_exact'))['amount']
                row.setdefault('quantity_basis', 'requested')
                if mode == 'settlement' and batch.get('delivery_confirmed') and row.get('quantity_basis') != 'actual':
                    _issue(row, 'actual_quantity_missing', '正式配货结算必须有逐项实配数量和用户确认来源；批次delivery_confirmed不能替代逐项实称')
            if row['status'] != 'matched':
                row.update(amount=None, finance_amount=None)
            rows.append(row)
        unknown = sum(row['status'] != 'matched' for row in rows)
        if mode == 'settlement' and batch.get('delivery_confirmed') and any(
                row.get('status') == 'needs_review' and any(i.get('code') == 'actual_quantity_missing' for i in row.get('issues', []))
                for row in rows):
            blockers.append('存在未提供逐项实配数量的行；批次delivery_confirmed不能替代逐项实称确认')
        if unknown:
            blockers.append(f'{unknown}条商品/数量/单位/日期或重复事件仍待确认')
        if not config.get('numbering', {}).get('confirmed', False):
            blockers.append('订单编号策略和起点尚未确认')
        if mode == 'settlement' and not batch.get('delivery_confirmed', False):
            blockers.append('实配数量和配送实绩尚未确认')
        finance = config.get('finance', {})
        finance_reasons = list(blockers)
        if mode != 'settlement' or not batch.get('delivery_confirmed', False):
            finance_reasons.append('当前是需求/待配送，缺少已确认实配依据')
        for field, label in [('confirmed', '财务价格来源'), ('tax_freight_confirmed', '税运口径'), ('merchant_names_confirmed', '财务商户名')]:
            if not finance.get(field, False):
                finance_reasons.append(label + '尚未确认')
        if not finance.get('receipt_date'):
            finance_reasons.append('收货日期尚未确认')
        if not company.get('finance_name'):
            finance_reasons.append('此公司财务商户名缺失')
        if not batch.get('publish', False):
            finance_reasons.append('当前为预览/复核，未发布正式流水')
        finance_draft = bool(finance_reasons)
        identity = _hash(_encoded([batch_id, company['company_id'], shipping, str(output)]))
        rules_hash = _rules_hash()
        digest = _hash(_encoded({'batch': batch, 'items': items, 'sources': sources,
                                'catalog': catalog['version_id'], 'rules': rules_hash, 'profile': profile_hash,
                                'knowledge':knowledge_hash}))
        number_store = NumberStore(root, config.get('numbering', {}))
        previous = number_store.publication(identity)
        if previous and previous['digest'] == digest:
            try:
                cached = _verified_manifest(previous['payload']['manifest_path'])
                if previous['status'] == 'prepared':
                    number_store.commit(identity, digest, previous['payload'])
                catalog_store.assert_current(catalog['version_id'], catalog['sha256'])
                return cached
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        order_number = None
        if batch.get('publish', False) and not blockers:
            order_number = number_store.reserve(batch_id, company['company_id'], shipping)
        order = {'company_id': company['company_id'], 'company_name': company['full_name'],
                 'finance_name': company.get('finance_name'), 'shipping_date': shipping,
                 'receipt_date': finance.get('receipt_date'), 'order_number': order_number,
                 'mode': mode, 'rows': rows}
        revision = f'{digest[:16]}-{uuid.uuid4().hex[:8]}'
        final = output / batch_id / revision
        final.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=final.parent))
        payload = {'manifest_path': str(final / 'manifest.json')}
        number_store.prepare(identity, digest, payload)
        try:
            test_label = '测试_' if batch.get('test_only', False) else ''
            label = '下单需求_待配送' if mode == 'order' else '配货结算'
            if blockers:
                label += '_待人工处理'
            elif not batch.get('publish', False):
                label += '_预览'
            order_file = test_label + label + '.xlsx'
            finance_file = test_label + ('财务记账_预估草稿' if finance_draft else '财务记账单') + '.xlsx'
            export_standard(stage / order_file, rows, company_name=company['full_name'], shipping_date=shipping,
                            order_number=order_number, catalog=catalog, mode=mode, pending=bool(blockers))
            # All prerequisites are recorded visibly, including test-only intent.
            from openpyxl import load_workbook
            from openpyxl.comments import Comment
            wb = load_workbook(stage / order_file)
            wb.active['A1'].comment = Comment(('仅供测试。\n' if batch.get('test_only') else '') +
                ('下单需求，尚未配送。\n' if mode == 'order' else '配货结算。\n') +
                ('\n'.join(blockers) if blockers else '本批输入前提已确认。') +
                ('\n预览文件，无正式流水。' if not batch.get('publish') else ''), 'User')
            wb.save(stage / order_file)
            export_finance(stage / finance_file, [order], catalog=catalog, draft=finance_draft,
                           reasons=(['仅供测试，不是实际业务凭证'] if batch.get('test_only') else []) + finance_reasons)
            ledger = {'schema_version': SCHEMA_VERSION, 'batch': batch, 'sources': sources,
                      'catalog': {k: v for k, v in catalog.items() if k != 'records'},
                      'rules_sha256': rules_hash, 'profile': profile, 'profile_sha256': profile_hash,
                      'knowledge_sha256':knowledge_hash,'knowledge_audit':knowledge_audit,
                      'matching_mode':matching_mode,'order': order, 'finance_draft': finance_draft,
                      'blockers': blockers, 'finance_reasons': finance_reasons}
            _write_json(stage / 'ledger.json', ledger)
            files = {}
            for role, name in [('standard', order_file), ('finance', finance_file), ('ledger', 'ledger.json')]:
                files[role] = {'path': str(final / name), 'sha256': _hash((stage / name).read_bytes())}
            standard = sum_amounts(r.get('amount') for r in rows if r['status'] == 'matched')
            exact = sum_amounts(calculate(r['selected']['price'], discount, r['invoice_quantity'], 'sample_exact')['amount'] for r in rows if r['status'] == 'matched')
            finance_total = sum_amounts(r.get('finance_amount') for r in rows if r['status'] == 'matched')
            manifest = {'schema_version': SCHEMA_VERSION, 'batch_id': batch_id, 'identity': identity,
                        'digest': digest, 'status': 'needs_confirmation' if blockers else ('exported' if order_number else 'ready'),
                        'test_only': batch.get('test_only', False), 'row_count': len(rows), 'unresolved_count': unknown,
                        'product_matched_count': sum(r.get('product_status') == 'matched' for r in rows),
                        'quantity_matched_count': sum(r.get('quantity_status') == 'matched' for r in rows),
                        'catalog': ledger['catalog'], 'rules_sha256': rules_hash, 'profile_sha256': profile_hash,
                        'knowledge_sha256':knowledge_hash,'matching_mode':matching_mode,'config_sha256': _hash(_encoded(config)),
                        'finance_draft': finance_draft, 'blockers': blockers, 'finance_reasons': finance_reasons,
                        'order': order, 'totals': {'standard': text_number(standard), 'standard_exact': text_number(exact),
                        'standard_cent': text_number(cents(standard)), 'finance': text_number(finance_total),
                        'finance_minus_standard': text_number(finance_total - standard)},
                        'manifest_path': str(final / 'manifest.json'), 'files': files}
            _write_json(stage / 'manifest.json', manifest)
            catalog_store.assert_current(catalog['version_id'], catalog['sha256'])
            os.replace(stage, final)
            number_store.commit(identity, digest, payload)
            _write_json(root / 'company-config.json', config)
            _write_json(root / 'batches' / batch_id / 'latest.json', {'manifest_path': str(final / 'manifest.json'), 'digest': digest})
            return manifest
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def merge_finance(runtime_root, manifest_paths, output_root):
    if not isinstance(manifest_paths, list) or not manifest_paths:
        raise ValueError('合并需至少一份批次manifest')
    with catalog_lock(runtime_root):
        store = CatalogStore(runtime_root)
        catalog = store.current()
        manifests = [_verified_manifest(path) for path in manifest_paths]
        identities = [m['identity'] for m in manifests]
        if len(set(identities)) != len(identities):
            raise ValueError('同一批次的重复或不同修订不能重复入账')
        if any(m['catalog']['version_id'] != catalog['version_id'] for m in manifests):
            raise ValueError('合并批次不是当前同一目录版本，请用当前目录重新生成')
        if len({m['config_sha256'] for m in manifests}) != 1:
            raise ValueError('合并批次配置口径不同，须统一后重导')
        orders = []
        ledgers = []
        for manifest in manifests:
            latest = NumberStore(runtime_root, {}).publication(manifest['identity'])
            if not latest or latest['status'] != 'committed' or latest['payload'].get('manifest_path') != manifest.get('manifest_path'):
                raise ValueError('合并包含已被修订或未提交的批次，请使用最新manifest')
            ledger = json.loads(Path(manifest['files']['ledger']['path']).read_text(encoding='utf-8'))
            if ledger['catalog']['version_id'] != catalog['version_id']:
                raise ValueError('台账目录与当前版本不一致')
            ledgers.append(ledger)
            orders.append(ledger['order'])
        numbers = [o['order_number'] for o in orders if o.get('order_number')]
        if len(numbers) != len(set(numbers)):
            raise ValueError('合并发现重复正式订单号')
        draft = any(ledger['finance_draft'] for ledger in ledgers)
        reasons = sorted({reason for ledger in ledgers for reason in ledger['finance_reasons']})
        test_only = any(ledger['batch'].get('test_only') for ledger in ledgers)
        digest = _hash(_encoded([m['digest'] for m in manifests]))
        final = Path(output_root).resolve() / ('merged-' + digest[:16] + '-' + uuid.uuid4().hex[:8])
        final.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=final.parent))
        try:
            name = ('测试_' if test_only else '') + ('合并财务记账_预估草稿.xlsx' if draft else '合并财务记账单.xlsx')
            export_finance(stage / name, orders, catalog=catalog, draft=draft,
                           reasons=(['仅供测试'] if test_only else []) + reasons)
            result = {'schema_version': 1, 'finance_draft': draft, 'test_only': test_only,
                      'catalog': {k: v for k, v in catalog.items() if k != 'records'},
                      'row_count': sum(len(o['rows']) for o in orders), 'order_count': len(orders),
                      'total': text_number(sum_amounts(row.get('finance_amount') for o in orders for row in o['rows'])),
                      'source_manifests': [str(Path(x).resolve()) for x in manifest_paths],
                      'files': {'finance': {'path': str(final / name), 'sha256': _hash((stage / name).read_bytes())}}}
            _write_json(stage / 'merge-manifest.json', result)
            store.assert_current(catalog['version_id'], catalog['sha256'])
            os.replace(stage, final)
            return result
        finally:
            if stage.exists():
                shutil.rmtree(stage)
