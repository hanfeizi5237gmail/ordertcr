#!/usr/bin/env python3
"""Read-only frozen semantic execution audit; output directory must not contain a report."""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evaluation'))
from v6_finance_audit import audit_result


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def equal(a, b):
    if a is None or b is None:
        return a is None and b is None
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return str(a) == str(b)


def metrics(rows):
    n = len(rows); auto = sum(r['automatic'] for r in rows)
    correct = sum(r['automatic_correct'] for r in rows)
    return {'events': n, 'automatic': auto, 'automatic_correct': correct,
            'A': round(100*correct/auto, 4) if auto else None,
            'B': round(100*correct/n, 4) if n else None,
            'C': None, 'C_reason': '候选全集及异常处置未被独立充分裁定'}


def agent_result(seed, index):
    events = []; manifests = []
    for item in index['events']:
        mp = item['manifest_path']
        if mp not in manifests: manifests.append(mp)
        ledger = read(read(mp)['files']['ledger']['path'])
        matches = [x for x in ledger['order']['rows'] if x['line_id'] == item['line_id']]
        if len(matches) != 1: raise ValueError('Agent event ledger link not unique')
        events.append({'row': matches[0], 'company_id': item['company_id'],
                       'shipping_date': item['shipping_date'], 'manifest_path': mp,
                       'excel_row': item['excel_row']})
    first = read(manifests[0]); chat = ROOT / f'evaluation/v7/semantic_challenge_v1/{seed}/input/聊天.txt'
    return {'event_count': len(events), 'events': events, 'manifests': manifests,
            'catalog': first['catalog'], 'matching_mode': 'shared',
            'sources': [{'source_name': '聊天.txt', 'path': str(chat), 'sha256': sha(chat)}]}


def scenario(row):
    import re
    if row['outcome'] != 'conditional_auto':
        return 'exception_' + row['event_id']
    text = row.get('source_text', '')
    return 'quantity_first' if re.match(r'\s*\d', text) else 'quantity_last'


def score(seed, lane, result, expected):
    chat = (ROOT / f'evaluation/v7/semantic_challenge_v1/{seed}/input/聊天.txt').read_text(encoding='utf-8-sig')
    actual = defaultdict(list)
    for a in result.get('events', []): actual[a['row'].get('source_line')].append(a)
    companies = {'星月站':'c1', '学院路站':'c2', '松北大道站':'c3', '新港站':'c4', '未登记单位':'unassigned'}
    scored = []
    for e in expected['events']:
        found = actual.get(e['source_line'], []); errors = []
        if len(found) != 1:
            scored.append({'event_id':e['event_id'],'concept':e['concept'],'company':e['company'],
                           'outcome':e['outcome'],'source_line':e['source_line'],
                           'automatic':False,'automatic_correct':False,'errors':['missing_or_multiple_output'],
                           'actual_count':len(found)})
            continue
        a = found[0]; row = a['row']; selected = row.get('selected') or {}
        start,end = row.get('char_start'),row.get('char_end')
        def check(ok, name):
            if not ok: errors.append(name)
        check(isinstance(start,int) and isinstance(end,int) and chat[start:end] == row.get('raw_text') == e['source_line_text'], 'source_span')
        check(a.get('company_id') == companies[e['company']], 'company')
        check(a.get('shipping_date') == e['date'], 'date')
        automatic = row.get('status') == 'matched' and row.get('event_state','active') == 'active' and row.get('event') not in ('改量','取消')
        if e['outcome'] == 'conditional_auto':
            s=e['selected']; check(automatic, 'not_automatic'); check(str(selected.get('code')) == s['sku'], 'sku')
            check(selected.get('name') == s['name'] and selected.get('spec') == s['spec'], 'name_spec')
            check(equal(row.get('quantity'), e['quantity']) and row.get('unit') == e['unit'], 'quantity_unit')
            check(equal(row.get('invoice_quantity'),e['quantity_in_500g_units']) and row.get('price_unit')=='500g', 'invoice_quantity_unit')
            check(equal(selected.get('price'),s['price_yuan_per_500g']), 'catalog_price')
            check(equal(row.get('discount'),e['conditional_discount']), 'discount')
            check(equal(row.get('unit_price'),Decimal(s['price_yuan_per_500g'])*Decimal(e['conditional_discount'])), 'discounted_unit_price')
            check(equal(row.get('amount'),e['conditional_discounted_amount_yuan']) and equal(row.get('finance_amount'),e['conditional_discounted_amount_yuan']), 'decimal_amount')
        else:
            check(not automatic, 'unsafe_automatic'); check(row.get('amount') is None and row.get('finance_amount') is None,'unsafe_amount')
            if e['event_id']=='X01': check(row.get('invoice_quantity') is None, 'unknown_packaging_priced')
            if e['event_id']=='X02': check(row.get('quantity') is None and row.get('invoice_quantity') is None, 'missing_quantity_filled')
            if e['event_id']=='X03': check(not selected, 'unknown_product_selected')
            if e['event_id']=='X04': check(a.get('company_id')=='unassigned','unknown_company_routed')
        if row.get('event') in ('改量','取消'):
            errors.append('unexpected_control_event')
        scored.append({'event_id':e['event_id'],'concept':e['concept'],'company':e['company'],
                       'date':e['date'],'outcome':e['outcome'],'source_line':e['source_line'],
                       'source_text':e['source_line_text'],'expected':e,'actual':a,
                       'automatic':automatic,'automatic_correct':automatic and e['outcome']=='conditional_auto' and not errors,
                       'errors':errors})
    expected_lines={e['source_line'] for e in expected['events']}
    extras=[{'source_line':a['row'].get('source_line'),'raw_text':a['row'].get('raw_text')} for a in result['events'] if a['row'].get('source_line') not in expected_lines]
    finance=audit_result(result)
    if lane=='agent':
        # Structured runs are separate; no merged file was created for this lane.
        finance['errors']=[e for e in finance['errors'] if e['code']!='read_merged']
        finance['merged_status']='not_generated; individual order and finance workbooks audited'
    return {'seed':seed,'lane':lane,'summary':metrics(scored),'normal24':metrics([x for x in scored if x['outcome']=='conditional_auto']),
            'by_company':{k:metrics([r for r in scored if r['company']==k]) for k in sorted({r['company'] for r in scored})},
            'by_concept':{k:metrics([r for r in scored if r['concept']==k]) for k in sorted({r['concept'] for r in scored})},
            'by_scenario':{k:metrics([r for r in scored if scenario(r)==k]) for k in sorted({scenario(r) for r in scored})},
            'failure_categories':dict(Counter(e for row in scored for e in row['errors'])),
            'unexpected_outputs':extras,'finance_audit':finance,'events':scored}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,default=ROOT/'evaluation/v7/semantic_audit_v1'); parser.add_argument('--status',type=Path,default=ROOT/'evaluation/v7/semantic_execution/script_status.json'); parser.add_argument('--include-agent',action='store_true'); args=parser.parse_args()
    target=args.output.resolve(); freeze=read(target/'input_freeze.json')
    if (target/'report.json').exists() or (target/'report.md').exists(): raise FileExistsError('initial report exists; use a new directory and freeze')
    mismatches=[p for p,h in freeze['files_sha256'].items() if not (ROOT/p).is_file() or sha(ROOT/p)!=h]
    if mismatches: raise ValueError('frozen input changed: '+', '.join(mismatches))
    statuses=read(args.status); runs=[]
    keys=[(st['seed'], st.get('mode','script')) for st in statuses]
    if len(set(keys)) != len(keys): raise ValueError('duplicate seed/mode execution')
    for st in statuses:
        seed=st['seed']; base=ROOT/f'evaluation/v7/semantic_challenge_v1/{seed}'; f=read(base/'FREEZE.json')
        if any(sha(base/name)!=h for name,h in f['files_sha256'].items()): raise ValueError(f'challenge freeze mismatch {seed}')
        if st['run_rc'] != 0 or st['register_rc'] != 0: raise ValueError(f'execution failed {seed}')
        e=read(base/'expected.json'); assert len(e['events'])==28 and len({x['source_line'] for x in e['events']})==28
        result=read(st['result']); lane=st.get('mode','script')
        if lane != 'script' and result.get('matching_mode') != lane: raise ValueError(f'result mode mismatch {seed}/{lane}')
        runs.append(score(seed,lane,result,e))
    if args.include_agent or args.status == ROOT/'evaluation/v7/semantic_execution/script_status.json':
        seed=8791; e=read(ROOT/f'evaluation/v7/semantic_challenge_v1/{seed}/expected.json')
        idx=read(ROOT/'evaluation/v7/semantic_execution/agent_8791_events.json'); runs.append(score(seed,'agent',agent_result(seed,idx),e))
    groups={lane:[r for run in runs if run['lane']==lane for r in run['events']] for lane in sorted({run['lane'] for run in runs if run['lane'] != 'agent'})}
    aggregate=groups.get('script',[])
    output={'schema_version':1,'input_freeze_sha256':sha(target/'input_freeze.json'),'evaluator_sha256':sha(__file__),
            'mode_summaries':{lane:{'summary':metrics(rows),'normal':metrics([r for r in rows if r['outcome']=='conditional_auto'])} for lane,rows in groups.items()},
            'runs':runs,'ambiguities':['24 normal expected outcomes are conditional on semantic human judgment; results are conditional, not ground-truth proof.', 'C cannot be established without independent candidate completeness review.', 'No amendment or cancellation cases in frozen 28; these controls are not tested.'],
            'scope':'Frozen known semantic concepts, not real customer acceptance; 0.95 and demand estimates conditional.'}
    if aggregate:
        output['script_3seed_summary']=metrics(aggregate)
        output['script_3seed_normal']=metrics([r for r in aggregate if r['outcome']=='conditional_auto'])
    (target/'report.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# Semantic challenge v1 frozen audit','', '24 条正常与 4 条异常计入每 seed 的 28 分母。A=自动正确/自动，B=自动正确/全部；C 因候选全集缺独立证据未裁定。冻结预期24项本身为条件性语义判断，不能当独立客户验收。','', '| 轨道 | 事件 | 自动 | 自动正确 | A | B | 正常24自动正确 | 工作簿错误 |','|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in runs:
        s=r['summary']; lines.append(f"| {r['lane']} {r['seed']} | {s['events']} | {s['automatic']} | {s['automatic_correct']} | {s['A']} | {s['B']} | {r['normal24']['automatic_correct']}/24 | {len(r['finance_audit']['errors'])} |")
    lines += ['',f"各模式汇总：{ {lane:metrics(rows) for lane,rows in groups.items()} }。详细公司、概念、失败类别、逐项比对和工作簿复读见 report.json。",'', '本集四类异常为包装未知、缺数量、未知商品、未登记公司；不含修改或取消，不能证明这些控制已验证。如包含历史 Agent 轨，该轨没有合并财务文件；其逐份订单与财务工作簿已审阅。对异常安全停留人工只验证指定字段，未把它们自动计入 C。', '', '冻结前原始输出已经执行，本次只读评分，不改预期或生产。']
    (target/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'script':metrics(aggregate),'runs':[{k:r[k] for k in ('seed','lane','summary','failure_categories')} for r in runs]},ensure_ascii=False))

if __name__=='__main__': main()
