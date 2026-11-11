"""Auditable concept rules resolved against the current catalog, never old SKU answers."""

import hashlib
import json
from pathlib import Path
import re

from .matching import match_item, _normalized, constraint_conflicts, KNOWN_BRANDS
from .units import price_basis
from .money import decimal_value
from .expressions import match_expression

DEFAULT_KNOWLEDGE = Path(__file__).resolve().parents[2] / 'knowledge' / 'procurement.json'


def _validate_selector(selector):
    if not isinstance(selector, dict):
        raise ValueError('共享概念选择器必须为对象')
    if not selector or set(selector) - {'names', 'name_contains', 'category', 'spec'}:
        raise ValueError('共享概念选择器字段无效，不能绑定SKU、行号、价格或实配量')
    for field, value in selector.items():
        if field in ('category', 'spec'):
            valid = isinstance(value, str) and bool(value.strip())
        elif field == 'name_contains' and isinstance(value, str):
            valid = bool(value.strip())
        else:
            valid = (isinstance(value, list) and bool(value) and
                     all(isinstance(x, str) and bool(x.strip()) for x in value))
        if not valid:
            raise ValueError('共享概念选择器字段类型或筛选值无效')


def validate_knowledge(data):
    if not isinstance(data,dict) or data.get('schema_version') != 1 or not isinstance(data.get('rules'),list):
        raise ValueError('共享知识schema_version/rules无效')
    ids=set()
    for rule in data['rules']:
        if not isinstance(rule,dict) or not rule.get('id') or rule['id'] in ids:
            raise ValueError('共享知识规则ID缺失或重复')
        ids.add(rule['id'])
        for key in ('concept','aliases','selector','evidence','applicable','conflicts','validation_status','kind'):
            if not rule.get(key) and key != 'conflicts':
                raise ValueError(f'共享知识缺少{key}: {rule["id"]}')
        if rule['applicable'] not in ('general','conditional','candidate_only'):
            raise ValueError('共享知识适用范围无效')
        if not isinstance(rule['aliases'],list) or not all(isinstance(x,str) and x for x in rule['aliases']):
            raise ValueError('共享知识aliases须为非空名称数组')
        for selector_key in ('selector', 'selection_selector'):
            if selector_key not in rule:
                continue
            _validate_selector(rule[selector_key])
        if 'reviewed_candidate_names' in rule and (not isinstance(rule['reviewed_candidate_names'], list)
                or not rule['reviewed_candidate_names'] or not all(isinstance(n, str) and n.strip()
                for n in rule['reviewed_candidate_names'])):
            raise ValueError('已审阅候选名称须为非空名称数组')
        if 'excluded_candidate_names' in rule and (not isinstance(rule['excluded_candidate_names'], dict)
                or not all(isinstance(n, str) and n.strip() and isinstance(reason, str) and reason.strip()
                           for n, reason in rule['excluded_candidate_names'].items())):
            raise ValueError('排除候选须记录规范名和具体理由')
    if not isinstance(data.get('unit_rules', []), list):
        raise ValueError('单位规则必须为数组')
    for rule in data.get('unit_rules', []):
        if not isinstance(rule, dict) or not rule.get('id') or rule['id'] in ids:
            raise ValueError('单位规则ID缺失或重复')
        ids.add(rule['id'])
        _validate_selector(rule.get('selector'))
        if rule.get('applicable') not in ('general', 'conditional', 'candidate_only') or not rule.get('evidence'):
            raise ValueError('单位规则须有适用范围和证据')
        conversion = rule.get('conversion')
        if conversion is None:
            if not all(isinstance(rule.get(k), str) and rule[k].strip()
                       for k in ('request_unit', 'price_unit')):
                raise ValueError('单位规则须明确原始单位和计价单位')
            decimal_value(rule.get('factor'), '单位比例', positive=True)
        else:
            if (not isinstance(conversion, dict) or set(conversion) -
                    {'price_unit', 'request_units', 'allow_fractional', 'grams_per_unit', 'ml_per_unit'}):
                raise ValueError('单位换算字段无效')
            ratios = conversion.get('request_units')
            if not isinstance(conversion.get('price_unit'), str) or not conversion['price_unit'].strip() or not isinstance(ratios, dict) or not ratios:
                raise ValueError('单位换算须明确计价单位和比例')
            for unit, factor in ratios.items():
                if not isinstance(unit, str) or not unit.strip():
                    raise ValueError('原始单位无效')
                decimal_value(factor, '单位比例', positive=True)
            for field in ('grams_per_unit', 'ml_per_unit'):
                if field in conversion:
                    decimal_value(conversion[field], field, positive=True)
        if not isinstance((conversion or rule).get('allow_fractional', False), bool):
            raise ValueError('单位规则allow_fractional必须为布尔值')
    if not isinstance(data.get('expression_rules', []), list):
        raise ValueError('共享表达规则必须为数组')
    for rule in data.get('expression_rules', []):
        if not isinstance(rule, dict) or not rule.get('id') or rule['id'] in ids:
            raise ValueError('共享表达规则ID缺失或重复')
        ids.add(rule['id'])
        if rule.get('kind') != 'attribute_order' or not all(rule.get(key) for key in (
                'concept', 'evidence', 'applicable', 'conflicts', 'validation_status', 'attributes')):
            raise ValueError('共享表达规则缺少适用边界或证据')
        if not isinstance(rule['attributes'], dict) or not all(
                isinstance(key, str) and key and isinstance(words, list) and words and
                all(isinstance(word, str) and word for word in words)
                for key, words in rule['attributes'].items()):
            raise ValueError('共享表达属性须为非空词表')
        # A normalized spelling must identify exactly one attribute.  If it is
        # configured under two attributes, extraction order would silently
        # decide which constraint to apply and could merge different goods.
        spellings = {}
        for attribute, words in rule['attributes'].items():
            for word in words:
                normalized = _normalized(word)
                previous = spellings.get(normalized)
                if previous is not None and previous != attribute:
                    raise ValueError(f'共享表达属性词跨属性冲突: {word}')
                spellings[normalized] = attribute
        for field in ('category_labels', 'pricing_labels'):
            if not isinstance(rule.get(field, {}), dict):
                raise ValueError('共享表达标签须为对象')
        if not all(isinstance(labels, list) and labels and all(isinstance(x, str) and x for x in labels)
                   for labels in rule.get('category_labels', {}).values()):
            raise ValueError('共享表达类别标签无效')
        if not all(isinstance(basis, list) and len(basis) == 2 and all(isinstance(x, str) and x for x in basis)
                   for basis in rule.get('pricing_labels', {}).values()):
            raise ValueError('共享表达计价标签无效')
        if not isinstance(rule.get('protected_compounds', []), list) or not all(
                isinstance(word, str) and word for word in rule.get('protected_compounds', [])):
            raise ValueError('共享表达复合词保护须为词表')
        if 'relations' in rule:
            grammar = rule['relations']
            allowed_relation_fields = {
                'connectors', 'markers', 'context_prefixes', 'modifier_aliases',
                'prefix_connectors', 'suffix_connectors',
            }
            if (not isinstance(grammar, dict) or set(grammar) - allowed_relation_fields or
                    not {'connectors', 'markers', 'context_prefixes', 'modifier_aliases'} <= set(grammar)):
                raise ValueError('共享关系表达字段无效')
            for field in ('connectors', 'markers', 'context_prefixes'):
                if not isinstance(grammar[field], list) or not grammar[field] or not all(
                        isinstance(word, str) and word for word in grammar[field]):
                    raise ValueError('共享关系表达须为非空词表')
            for field in ('prefix_connectors', 'suffix_connectors'):
                if field in grammar and (not isinstance(grammar[field], list) or not grammar[field] or
                                         not all(isinstance(word, str) and word for word in grammar[field])):
                    raise ValueError('共享关系边缘连接词须为非空词表')
            if not isinstance(grammar['modifier_aliases'], dict) or not all(
                    isinstance(k, str) and k and isinstance(v, str) and v
                    for k, v in grammar['modifier_aliases'].items()):
                raise ValueError('共享关系表达属性别名须为非空字符串映射')
    return data


def load_knowledge(path=None):
    path=Path(path or DEFAULT_KNOWLEDGE)
    raw=path.read_bytes()
    return validate_knowledge(json.loads(raw)), hashlib.sha256(raw).hexdigest()


def company_knowledge(knowledge, company_id):
    """Only explicit company contracts may override a shared alias concept."""
    path=DEFAULT_KNOWLEDGE.with_name('company-contracts.json')
    raw=path.read_bytes();data=json.loads(raw)
    if data.get('schema_version')!=1 or not isinstance(data.get('contracts'),list):
        raise ValueError('公司特殊约定文件无效')
    overlay=[]
    for contract in data['contracts']:
        evidence=contract.get('evidence') or {}
        if evidence.get('kind')!='user_confirmed_contract' or not evidence.get('source') or not evidence.get('locator'):
            raise ValueError('公司特殊约定必须有用户明确约定的可追溯证据')
        validate_knowledge({'schema_version':1,'rules':contract.get('rules')})
        if contract.get('company_id')==company_id:overlay.extend(contract['rules'])
    if not overlay:return knowledge, hashlib.sha256(raw).hexdigest(), []
    aliases={_normalized(a) for r in overlay for a in r['aliases']}
    kept=[]
    for rule in knowledge['rules']:
        remaining=[a for a in rule['aliases'] if _normalized(a) not in aliases]
        if remaining:kept.append({**rule,'aliases':remaining})
    return {**knowledge,'rules':kept+overlay},hashlib.sha256(raw).hexdigest(),[r['id'] for r in overlay]


def _select_records(selector, records):
    names={_normalized(x) for x in selector.get('names',[])}
    contains=selector.get('name_contains',[])
    if isinstance(contains,str):contains=[contains]
    return [r for r in records
            if (not names or _normalized(r['name']) in names)
            and (not selector.get('category') or r.get('category')==selector['category'])
            and all(_normalized(x) in _normalized(r['name']) for x in contains)
            and (not selector.get('spec') or _normalized(r.get('spec'))==_normalized(selector['spec']))]


def _applies(rule,item):
    if _normalized(item.get('name')) not in {_normalized(a) for a in rule['aliases']}:
        return False
    conditions=rule.get('conditions') or {}
    if conditions.get('request_units') and item.get('unit') not in conditions['request_units']:
        return False
    notes=str(item.get('notes') or '')+' '+str(item.get('raw_text') or '')
    contains=conditions.get('notes_contains',[])
    if isinstance(contains,str):contains=[contains]
    excludes=conditions.get('notes_excludes',[])
    if isinstance(excludes,str):excludes=[excludes]
    return all(word in notes for word in contains) and not any(word in notes for word in excludes)


def catalog_knowledge_audit(data,records):
    """Re-resolve all selectors on activation; missing/changed concepts are visible."""
    result=[]
    for rule in data['rules']:
        found=_select_records(rule['selector'],records)
        selection = _select_records(rule.get('selection_selector', rule['selector']), found)
        present = {_normalized(r['name']) for r in found}
        missing = [name for name in rule['selector'].get('names', []) if _normalized(name) not in present]
        selection_present = {_normalized(r['name']) for r in selection}
        selection_missing = [name for name in rule.get('selection_selector', rule['selector']).get('names', [])
                             if _normalized(name) not in selection_present]
        result.append({'rule_id':rule['id'],'candidate_codes':[r['code'] for r in found],
                       'selection_candidate_codes':[r['code'] for r in selection],
                       'missing_names':missing, 'selection_missing_names':selection_missing,
                       'status':('selector_missing' if not found else
                                 'selection_selector_missing' if not selection else
                                 'selector_partially_missing' if missing else
                                 'selection_selector_partially_missing' if selection_missing else 'available'),
                       'specs':sorted({r['spec'] for r in found})})
    return result


def match_shared(item, records, knowledge, *, unit_rules=None):
    records=list(records)
    # Conditions gate automatic use, not retrieval. An unknown package unit
    # must still expose the concept's catalog candidates for human review.
    name = _normalized(item.get('name'))
    brand_prefix = re.match(r'[\u4e00-\u9fff]{2,10}牌(.+)', name)
    concept_names = {name, brand_prefix[1]} if brand_prefix else {name}
    rules=[r for r in knowledge['rules'] if concept_names.intersection(
           {_normalized(a) for a in r['aliases']}) and
           (_applies(r,item) or r['kind'] in ('synonym', 'generic_request') or bool(brand_prefix))]
    base=match_item(item,records,unit_rules=unit_rules)
    if re.search(r'其他品牌|别的品牌|换另一种包装|换个包装',str(item.get('raw_text') or item.get('name') or '')):
        base.update(selected=None,product_status='needs_review',status='needs_review',invoice_quantity=None,
                    quantity_status='needs_review')
        base['issues'].append({'code':'requirement_unresolved','message':'原文要求其他品牌/不同包装，但未指定目标，不可沿用原商品'})
        return base
    if not rules:
        grammars = [r.get('relations', {}) for r in knowledge.get('expression_rules', [])]
        relation_syntax = any(word in name for g in grammars
                              for word in (g.get('connectors', []) + g.get('markers', []) +
                                           g.get('prefix_connectors', []) + g.get('suffix_connectors', [])))
        if not base.get('selected') or relation_syntax:
            expression = match_expression(item, records, knowledge.get('expression_rules', []),
                                          unit_rules=unit_rules)
            if expression is not None:
                return expression
        if base.get('selected') and constraint_conflicts(item,base['selected']):
            base.update(selected=None,product_status='needs_review',status='needs_review',invoice_quantity=None)
            base['issues'].append({'code':'attribute_conflict','message':'当前需求属性与目录结果冲突'})
        return base
    pools=[_select_records(rule['selector'],records) for rule in rules]
    unreviewed = set()
    for rule, pool in zip(rules, pools):
        reviewed = {_normalized(n) for n in rule.get('reviewed_candidate_names', [])}
        if rule['kind'] != 'generic_request' or not reviewed:
            continue
        excluded = {_normalized(n) for n in rule.get('excluded_candidate_names', {})}
        categories = ({rule['selector']['category']} if rule['selector'].get('category')
                      else {r.get('category') for r in pool})
        codes = {str(r['code']) for r in pool}
        # Catalog changes must be checked across the concept's spellings,
        # not just the spelling used by this one message. Restrict retrieval
        # to the already reviewed categories before applying the new-name gate.
        same_category = [r for r in records if r.get('category') in categories]
        retrieved = list(base.get('candidates', []))
        for alias in rule['aliases']:
            if _normalized(alias) == name:
                continue
            alternative = {**item, 'name': alias}
            retrieved.extend(match_item(alternative, same_category, unit_rules=unit_rules)['candidates'])
        for candidate in retrieved:
            if (candidate.get('category') in categories and
                    _normalized(candidate['name']) not in reviewed | excluded and
                    str(candidate['code']) not in codes):
                pool.append(candidate)
                codes.add(str(candidate['code']))
        unreviewed.update(str(r['code']) for r in pool if _normalized(r['name']) not in reviewed | excluded)

    def review_compatible(record):
        """Keep packaging-only mismatches as review witnesses.

        A request for one box may still need to show a matching single-pack
        SKU in the Excel comment so the operator can verify case count and
        net weight.  It must never become selectable for automatic pricing;
        only non-packaging conflicts remain hard exclusions.
        """
        conflicts = constraint_conflicts(item, record)
        if not conflicts:
            return True
        if (brand_prefix and all(c.startswith('品牌:') for c in conflicts) and
                not any(brand in str(record.get('name')) for brand in KNOWN_BRANDS)):
            return True  # Unbranded is a conditional reference, never a brand match.
        return (all(c.startswith('属性未证实:') for c in conflicts) or
                (item.get('unit') == '箱' and conflicts == ['包装不符合箱装要求']))

    candidates = {}
    for rule, pool in zip(rules, pools):
        for record in pool:
            if not review_compatible(record):
                continue
            reason = rule.get('candidate_reasons', {}).get(record['name'])
            if str(record['code']) in unreviewed:
                reason = '当前目录新增或未审阅的同类名称；仅供语义核对，未证明与自动选择层等价'
            if not reason:
                reason = '名称关联依据：' + rule['concept']
                if not _applies(rule, item):
                    reason += ('；当前自动适用条件未满足，原文单位' + str(item.get('unit') or '缺失') +
                               '与目录规格' + str(record.get('spec') or '缺失') + '的计价关系仍需核定')
                else:
                    reason += '；数量、包装和公司归属须分别核定'
            if brand_prefix:
                reason = '品名相近，但当前目录未证明原文指定品牌；仅供核对，不可跨品牌替代'
            candidates[str(record['code'])] = ({**record, 'match_reason': reason}
                                               if reason else record)
    result=dict(base)
    result.update(selected=None,product_status='needs_review',status='needs_review',invoice_quantity=None,
                  quantity_status='needs_review',candidates=list(candidates.values()),
                  knowledge_rule_ids=[r['id'] for r in rules],
                  mapping_evidence=[r['evidence'] for r in rules],match_basis='shared_knowledge')
    result['issues']=[i for i in base['issues'] if i['code'] not in ('unmapped','semantic_review','different_specs','tied_candidates','quantity_conversion')]
    def pending(code,message):
        result['issues'].append({'code':code,'message':message})
        return result
    if not candidates:
        result['status']='unmapped'
        return pending('knowledge_selector_missing','共享概念在当前目录无满足原文约束的条目；不会沿用旧SKU或旧价')
    if unreviewed.intersection(candidates):
        return pending('knowledge_new_candidates','当前目录出现未审阅的同类名称，须重新核定候选层；不静默套用旧选择')
    auto=[set(str(r['code']) for r in _select_records(
                  rule.get('selection_selector', rule['selector']), pool)
              if not constraint_conflicts(item,r))
          for rule,pool in zip(rules,pools)
          if rule['applicable']!='candidate_only' and _applies(rule,item)]
    if not auto:
        return pending('semantic_review','共享经验仅供候选检索，品种/品牌/形态/包装仍需人工核定')
    if any(pool != auto[0] for pool in auto[1:]):
        return pending('knowledge_conflict','共享规则候选不一致，保留全部候选，不能合并历史选择')
    eligible=[candidates[code] for code in auto[0]]
    if not eligible:
        return pending('attribute_conflict','当前目录缺少满足原文属性的自动选择层；不回退其他品种或包装')
    if len({price_basis(r) for r in eligible})!=1:
        return pending('different_specs','概念对应多个不可直接比较的规格/包装，须人工选择')
    highest=max(decimal_value(r['price'],'挂牌价',positive=True) for r in eligible)
    top=[r for r in eligible if decimal_value(r['price'],'挂牌价',positive=True)==highest]
    if len(top)!=1:
        return pending('tied_candidates','等价候选最高价相同，不能按目录行号决定')
    selected=top[0]
    dynamic_units=dict(unit_rules or {})
    # A package conversion is an auditable rule, not a side effect of a
    # matching selector.  Apply it only when its declared request unit and
    # applicability cover this event; a rule for another spelling or a
    # candidate-only concept must leave the row in quantity review.
    for rule in knowledge.get('unit_rules',[]):
        if rule.get('applicable') == 'candidate_only':
            continue
        request_unit = rule.get('request_unit')
        if request_unit and item.get('unit') != request_unit:
            continue
        selector = rule.get('selector') or {}
        if selected not in _select_records(selector,[selected]):
            continue
        conversion = rule.get('conversion') or {
            'price_unit':rule['price_unit'],
            'request_units':{rule['request_unit']:rule['factor']},
            'allow_fractional':rule.get('allow_fractional',False)}
        dynamic_units[str(selected['code'])]=conversion
    chosen=match_item(item,records,aliases={item['name']:{'code':selected['code']}},unit_rules=dynamic_units)
    chosen.update(candidates=list(candidates.values()),knowledge_rule_ids=result['knowledge_rule_ids'],
                  match_basis='shared_knowledge',mapping_evidence=result['mapping_evidence'])
    return chosen
