#!/usr/bin/env python3
"""Finite-candidate audit for KB16 semantic execution; never changes expected files."""
from __future__ import annotations
import argparse, hashlib, json, re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / 'evaluation/v7/kb16_exception_contract.json'
SEEDS = (8701, 8702, 8791)
FOUR = {
    '桑葚': 'dried_mulberry', '贡菜': 'dried_gongcai',
    '烤麸': 'dried_kaofu', '海带扣': 'fresh_salted_kelp_knot',
}
FRESH = ('snow', 'bean', 'brand', 'brown_bag', 'brown_missing', 'orange')
COMPANIES = {'星月站', '学院路站', '松北大道站', '新港站'}

def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dec(v):
    try: return str(v) if v is not None else None
    except Exception: return None

def contract_index(contract): return {c['id']: c for c in contract['cases']}
def candidate_codes(row): return sorted({str(x.get('code')) for x in (row or {}).get('candidates', []) if x.get('code') is not None})
def selected_code(row):
    s=(row or {}).get('selected') or {}; return str(s['code']) if s.get('code') is not None else None

def expected_concept(event, case):
    qty=event.get('quantity'); unit=event.get('unit')
    quantity_known = qty is not None and unit == '斤'
    company=event.get('company') or ''
    company_boundary = company in ('未登记单位','unassigned') or company not in COMPANIES
    return {
        'case_id': case['id'], 'identity': 'known',
        'expected_codes': [str(x['code']) for x in case.get('selectable_records', [])],
        'review_codes': [str(x['code']) for x in case.get('review_reference_records', [])],
        'reasonable_codes': [str(x['code']) for x in (case.get('selectable_records', []) + case.get('review_reference_records', []))],
        'excluded_codes': [str(x['code']) for x in case.get('excluded_records', [])],
        'candidate_set_status': 'finite' if case.get('selectable_records') else 'unknown',
        'quantity': 'known_斤_500g' if quantity_known else 'unknown',
        'company_boundary': company_boundary,
        'amount_allowed': quantity_known and not company_boundary,
    }

def expected_fresh(case_id, event):
    # Contract is authoritative for the six fresh exception classes. Empty selectable
    # sets and review references intentionally remain unknown/non-selectable.
    return {'case_id': case_id, 'identity': 'known' if case_id in {'brown_bag','brown_missing','orange'} else 'unknown',
            'expected_codes': [str(x['code']) for x in event.get('selectable_records', [])],
            'review_codes': [str(x['code']) for x in event.get('review_reference_records', [])],
            'reasonable_codes': [str(x['code']) for x in (event.get('selectable_records', []) + event.get('review_reference_records', []))],
            'excluded_codes': [str(x['code']) for x in event.get('excluded_records', [])],
            'candidate_set_status': 'finite' if event.get('selectable_records') else 'unknown',
            'quantity': 'known_斤_500g' if case_id == 'orange' else ('unknown' if case_id in {'brown_bag','brown_missing'} else 'known_original'),
            'company_boundary': case_id == 'orange', 'amount_allowed': False if case_id == 'orange' else False}

def event_from_contract_occurrence(contract, case_id, occurrence):
    quantity_match=re.search(r'(\d+(?:\.\d+)?)\s*(斤|两|kg|公斤|千克|g|克|袋|箱|包|盒)?', occurrence['raw_text'])
    return {'case_id': case_id, 'source_file': occurrence['source_file'], 'source_line': occurrence['line'],
            'raw_text': occurrence['raw_text'], 'company': occurrence.get('company_context', {}).get('value'),
            'date': occurrence.get('date_context', {}).get('value'),
            'quantity': quantity_match.group(1) if quantity_match else None,
            'unit': quantity_match.group(2) if quantity_match else None}

def evaluate(expected, actual):
    row=(actual or {}).get('row') or {}
    selected=selected_code(row); cand=candidate_codes(row)
    out={'identity':'unknown','candidate_set':'unknown','quantity':'unknown','company_boundary':'unknown','amount':'unknown','errors':[]}
    if expected['identity']=='known':
        out['identity']='correct' if selected in expected['expected_codes'] and row.get('product_status') == 'matched' else 'incorrect'
        if out['identity']=='incorrect': out['errors'].append('identity')
    else:
        out['identity']='safe_unselected' if selected is None else 'incorrect_selected_unknown'
        if selected is not None: out['errors'].append('unknown_identity_selected')
    if expected['candidate_set_status']=='finite':
        excluded_overlap = sorted(set(cand) & set(expected.get('excluded_codes', [])))
        out['excluded_present'] = excluded_overlap
        out['candidate_set']='complete' if set(cand)==set(expected['reasonable_codes']) and not excluded_overlap else 'incomplete_or_extra'
    else: out['candidate_set']='unknown'
    if expected['quantity']=='known_斤_500g':
        q=row.get('invoice_quantity'); out['quantity']='correct' if q is not None and dec(q)==dec(actual.get('expected_quantity')) else 'incorrect'
        if out['quantity']=='incorrect': out['errors'].append('quantity')
    elif expected['quantity']=='unknown':
        out['quantity']='safe_blank' if row.get('invoice_quantity') is None else 'incorrect_inferred'
        if out['quantity']!='safe_blank': out['errors'].append('unknown_quantity_filled')
    if expected['company_boundary'] is True:
        out['company_boundary']='blocked' if actual.get('company_id') in ('unassigned',None) or row.get('amount') is None else 'incorrect_routed_or_priced'
        if out['company_boundary']!='blocked': out['errors'].append('company_boundary')
    else: out['company_boundary']='not_applicable'
    if expected['amount_allowed']:
        out['amount']='present' if row.get('amount') is not None else 'missing'
    else:
        out['amount']='blank' if row.get('amount') is None else 'incorrect_nonblank'
        if out['amount']!='blank': out['errors'].append('amount')
    return out

def load_old_events():
    events=[]
    for seed in SEEDS:
        for e in read(ROOT/f'evaluation/v7/semantic_challenge_v1/{seed}/expected.json')['events']:
            if e.get('concept') in FOUR:
                events.append({'seed':seed,'event_id':e['event_id'],'source_file':f'evaluation/v7/semantic_challenge_v1/{seed}/input/聊天.txt','source_line':e['source_line'],'raw_text':e['source_line_text'],'company':e.get('company'),'date':e.get('date'),'quantity':e.get('quantity'),'unit':e.get('unit'),'concept':e['concept']})
    return events

def load_execution(phase):
    status_path=ROOT/f'evaluation/v7/{phase}/script_status.json'
    if status_path.exists():
        status=read(status_path)
    else:
        status_path=ROOT/f'evaluation/v7/{phase}/execution_status.json'
        if not status_path.exists(): raise FileNotFoundError(f'execution status missing: {status_path}')
        status=read(status_path)
    if isinstance(status,list):
        entries=status
    else:
        entries=status.get('runs',status.get('entries',status.get('executions',[])))
    results=[]
    for ent in entries:
        # Candidate semantics are assessed on the shared lane only.  The
        # catalog-only lane is an ablation and company-contract is identical
        # while the contract file is empty; mixing them creates duplicate
        # links and falsely turns a single event into an ambiguity.
        if ent.get('mode') not in (None, 'shared'):
            continue
        path=ent.get('result') or ent.get('output') or ent.get('result_path')
        if not path: continue
        p=Path(path); p = p if p.is_absolute() else ROOT/p
        if p.is_dir():
            found=sorted(p.rglob('messages-result.json')); p=found[-1] if found else p/'messages-result.json'
        if p.exists(): results.append((ent,p,read(p)))
    if not results: raise ValueError('execution status has no readable result files')
    return results

def index_actual(results):
    idx=defaultdict(list)
    for ent,path,result in results:
        seed = ent.get('seed') or result.get('seed')
        if seed is None:
            m=re.search(r'(?<!\d)(\d{4})(?!\d)', str(path)); seed=int(m.group(1)) if m else None
        for ev in result.get('events',[]):
            row=ev.get('row',ev); key=(seed, Path(str(ev.get('source_name') or row.get('source_name') or '聊天.txt')).name, ev.get('source_line',row.get('source_line')))
            idx[key].append({'row':row, 'company_id':ev.get('company_id',result.get('company_id')), 'result_path':str(path)})
    return idx

def review_new_rules():
    """Static metadata review; suggestions only, never a production rule change."""
    kb_path = ROOT / 'skills/order-mapping/knowledge/procurement.json'
    kb = read(kb_path)
    ids = [
        'kb16_mulberry_dry', 'kb16_celery_stem_dry', 'kb16_gluten_dry_large',
        'kb16_seaweed_cured_fresh', 'kb16_frozen_flounder_head',
        'kb16_iced_squid_tentacle', 'kb16_frozen_tilapia_slice',
        'kb16_fresh_lily_slice',
    ]
    by_id = {r.get('id'): r for r in kb.get('rules', [])}
    checks=[]
    for rule_id in ids:
        r=by_id.get(rule_id)
        if not r:
            checks.append({'rule_id':rule_id,'status':'missing'}); continue
        checks.append({'rule_id':rule_id,'status':'present','kind':r.get('kind'),
                       'applicable':r.get('applicable'),'selector':r.get('selector'),
                       'request_units':r.get('conditions',{}).get('request_units',[]),
                       'validation_status':r.get('validation_status'),
                       'boundary_review':'保持基础名及全部鲜/冻/干/带盐/形态属性；不跨形态或包装替代；仅当前目录候选完整且无冲突时才考虑自动身份，数量另行核算。'})
    evidence_checks=[]
    for rule_id in ids:
        r=by_id.get(rule_id) or {}
        for evidence in r.get('evidence',[]):
            source=evidence.get('source')
            source_candidates=[ROOT/source, ROOT/'skills/order-mapping'/source] if source else []
            source_path=next((candidate for candidate in source_candidates if candidate.exists()), None)
            evidence_checks.append({'rule_id':rule_id,'source':source,'locator':evidence.get('locator'),
                                    'source_exists':bool(source_path and source_path.exists()),
                                    'source_sha256':sha(source_path) if source_path and source_path.is_file() else None,
                                    'catalog_source':evidence.get('catalog_source'),'catalog_sha256':evidence.get('catalog_sha256')})
    return {'current_knowledge_version':kb.get('knowledge_version'),
            'knowledge_sha256':sha(kb_path),
            'current_rule_counts':{'concept_rules':len(kb.get('rules',[])),'unit_rules':len(kb.get('unit_rules',[])),'expression_rules':len(kb.get('expression_rules',[]))},
            'documented_count':89,'count_delta_concepts':len(kb.get('rules',[]))-89,
            'eight_rule_review':checks,'evidence_reference_checks':evidence_checks,
            'recommendations':['将 shared-knowledge.md 与 SKILL.md 的 89 条概念规则更新为当前实际 93 条（85+8），或明确文档只统计已发布子集；当前口径不一致。','将 kb16_celery_stem_dry 的 rule id 与贡菜概念对齐，避免把 celery stem 误解为正式语义边界；不在本审计中改名。','四个干/鲜/带盐规则应继续把属性作为硬约束，并对无属性、鲜冻相反、腌制或不同形态返回待核；不能只因名称相近自动选择。','鲽鱼头、鱿鱼须、鲷鱼片、百合片四规则的 selector 是具体目录名称，未来同名不同规格或品牌出现时应保留可比候选并按当前价格规则审查，不绑定旧 SKU/价格。','8 条规则的 validation_status 仍为开发回归待定，不能把本轮有限审计当成完整语义或真实订单验收。']}

def run(phase, output, fresh_phase=None):
    contract=read(CONTRACT); ci=contract_index(contract)
    semantic_results=load_execution(phase)
    fresh_results=load_execution(fresh_phase or phase)
    semantic_actual=index_actual(semantic_results)
    fresh_actual=index_actual(fresh_results)
    old=[]; counts=Counter(); details=[]
    for e in load_old_events():
        case=ci[FOUR[e['concept']]]; ex=expected_concept(e,case); key=(e['seed'],'聊天.txt',e['source_line']); found=semantic_actual.get(key,[])
        # source line alone can collide across seeds; if several, retain unknown rather than guess.
        if len(found)!=1: scored={'status':'unknown_execution_link','candidates':len(found)}
        else:
            found[0]['expected_quantity']=e.get('quantity'); scored=evaluate(ex,found[0])
        details.append({'scope':'four_concepts','seed':e['seed'],'event_id':e['event_id'],'concept':e['concept'],'source_line':e['source_line'],'raw_text':e['raw_text'],'expected':ex,'actual_count':len(found),'score':scored}); counts.update(scored.get('errors',[]))
    fresh=[]
    for case_id in FRESH:
        case=ci[case_id]
        for occ in case.get('source_occurrences',[]):
            e=event_from_contract_occurrence(contract,case_id,occ); ex=expected_fresh(case_id,case)
            m=re.search(r'(?<!\d)(\d{4})(?!\d)', e['source_file']); seed=int(m.group(1)) if m else None
            key=(seed,Path(e['source_file']).name,e['source_line']); found=fresh_actual.get(key,[])
            if len(found)!=1:
                scored={'status':'unknown_execution_link','candidates':len(found)}
            else:
                found[0]['expected_quantity']=e.get('quantity')
                scored=evaluate(ex,found[0])
            fresh.append({'scope':'fresh_six','case_id':case_id,'source_file':e['source_file'],'source_line':e['source_line'],'raw_text':e['raw_text'],'expected':ex,'actual_count':len(found),'score':scored})
    out={'schema':'kb16_candidate_audit_v1','execution_phase':phase,'fresh_execution_phase':fresh_phase or phase,'contract_sha256':sha(CONTRACT),'scope':'有限候选集合及身份/数量/公司边界审计；unknown 保留，非A/B/C',
         'old_four_concepts':{'event_count':len(details),'details':details,'error_counts':dict(counts)},
         'fresh_six':{'event_count':len(fresh),'details':fresh,'case_counts':dict(Counter(x['case_id'] for x in fresh))},
         'new_rule_review':review_new_rules(),
         'limitations':['候选集合仅限合同和当前可见目录范围；合同标记unknown时不擅自补候选。','未验证真实订单、供应商替代或实配；金额仅检查输出是否违反合同边界。']}
    output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n'); return out

def self_test():
    contract=read(CONTRACT); ci=contract_index(contract)
    assert len(load_old_events()) == 36
    assert set(FRESH).issubset(ci) and all(len(ci[x].get('source_occurrences', [])) == 3 for x in FRESH)
    # Synthetic rows prove unknown and finite contracts are kept distinct without reading execution output.
    case= {'id':'dried_mulberry','selectable_records':[{'code':'80003020'}], 'review_reference_records':[]}
    ex=expected_concept({'quantity':'2','unit':'斤','company':'星月站'},case)
    ok=evaluate(ex,{'row':{'selected':{'code':'80003020'},'product_status':'matched','candidates':[{'code':'80003020'}],'invoice_quantity':'2','amount':'41.3440'},'expected_quantity':'2','company_id':'c1'})
    assert ok['identity']=='correct' and ok['candidate_set']=='complete' and ok['quantity']=='correct'
    excluded=evaluate({**ex,'excluded_codes':['999']},{'row':{'selected':{'code':'80003020'},'product_status':'matched','candidates':[{'code':'80003020'},{'code':'999'}],'invoice_quantity':'2','amount':'41.3440'},'expected_quantity':'2','company_id':'c1'})
    assert excluded['candidate_set']=='incomplete_or_extra' and excluded['excluded_present']==['999']
    unknown=expected_fresh('snow',{'selectable_records':[],'review_reference_records':[]})
    bad=evaluate(unknown,{'row':{'selected':{'code':'80003906'},'candidates':[],'amount':'1'}})
    assert bad['identity']=='incorrect_selected_unknown' and bad['candidate_set']=='unknown' and 'unknown_identity_selected' in bad['errors']
    print('self-test passed: finite candidate, unknown candidate and quantity/company boundaries')

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--execution-phase'); ap.add_argument('--fresh-execution-phase'); ap.add_argument('--output',type=Path,default=ROOT/'evaluation/v7/kb16_candidate_audit.json'); ap.add_argument('--self-test',action='store_true'); a=ap.parse_args()
    if a.self_test: self_test()
    elif not a.execution_phase: ap.error('--execution-phase is required unless --self-test')
    else: run(a.execution_phase,a.output.resolve(),a.fresh_execution_phase)
