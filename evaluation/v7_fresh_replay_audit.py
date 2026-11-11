#!/usr/bin/env python3
"""Run raw fresh V2 inputs first, then independently score frozen outcomes.

A single invocation is immutable. It never feeds oracle fields to the CLI and
stops immediately if product source, original files, or frozen inputs drift.
"""
from __future__ import annotations
import collections
import hashlib
import json
import re
import subprocess
import sys
import traceback
import argparse
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / 'evaluation/v7/fresh_rc1'
OUT = ROOT / 'outputs/v7/fresh_rc1'
FIX = ROOT / 'evaluation/v7/fresh_challenge_v2'
CLI = ROOT / 'skills/order-mapping/scripts/order_mapping.py'
BUILDER = ROOT / 'evaluation/v7_fresh_generator_v2.py'
KB = ROOT / 'skills/order-mapping/knowledge/procurement.json'
EXPECTED_KB = 'b6340108e9d2feb711a8d078fc9b4a854b02bf3e87420c9a34621f3a0f1a7952'
SEEDS = (7601, 7602, 7691)
sys.path.insert(0, str(ROOT / 'evaluation'))
sys.path.insert(0, str(ROOT / 'skills/order-mapping/scripts'))
from v6_finance_audit import audit_result
from order_mapping_core.catalog import CatalogStore


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def fingerprint():
    files = [p for p in (ROOT / 'skills/order-mapping').rglob('*')
             if p.is_file() and p.suffix in ('.py', '.json', '.md') and '__pycache__' not in p.parts]
    files += [p for p in (ROOT / 'original').iterdir() if p.is_file()]
    files += [p for p in FIX.rglob('*') if p.is_file()]
    files += [Path(__file__).resolve(), BUILDER, ROOT / 'evaluation/v7/source_before/knowledge/procurement.json',
              ROOT / 'evaluation/v7/kb13_draft.json', ROOT / 'evaluation/v7/kb13_proposal_freeze.json']
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(set(files))}


def verify_frozen():
    result = {}
    for seed in SEEDS:
        source = FIX / str(seed)
        freeze = json.loads((source / 'FREEZE.json').read_text())
        manifest = json.loads((source / 'manifest.json').read_text())
        paths = {'builder': BUILDER, 'source_catalog': ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx'}
        paths.update({name: source / name for name in ('input/request.json', 'input/聊天.txt', 'manifest.json', 'expected.json')})
        if set(freeze['sha256']) != set(paths):
            raise ValueError(f'{seed}: frozen inventory differs')
        mismatches = [key for key, path in paths.items() if sha(path) != freeze['sha256'][key]]
        if mismatches:
            raise ValueError(f'{seed}: frozen content mismatch: {mismatches}')
        if manifest['expected_sha256'] != freeze['sha256']['expected.json']:
            raise ValueError(f'{seed}: expected hash differs from manifest')
        if any(manifest['input_sha256'][x] != freeze['sha256'][x] for x in ('input/request.json','input/聊天.txt')):
            raise ValueError(f'{seed}: input hash differs from manifest')
        actual_inputs = {str(p.relative_to(source)) for p in (source/'input').rglob('*') if p.is_file()}
        if actual_inputs != {'input/request.json', 'input/聊天.txt'}:
            raise ValueError(f'{seed}: extra input files')
        result[str(seed)] = {'freeze_sha256': sha(source/'FREEZE.json'),
                             'expected_sha256': freeze['sha256']['expected.json'],
                             'input_sha256': manifest['input_sha256'],
                             'construction_sources': manifest['source_hashes']}
    return result


def same_number(a, b):
    if a is None or b is None:
        return a is None and b is None
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except Exception:
        return False


def config(ordinary):
    companies = ordinary['companies']
    if not companies or any(set(c) != {'company_name', 'company_alias'} for c in companies):
        raise ValueError('Invalid ordinary company input')
    return {'schema_version': 1, 'companies_confirmed': True,
            'companies': [{'company_id': f'c{i}', 'full_name': c['company_name'],
                           'aliases': [c['company_alias']], 'finance_name': c['company_name']}
                          for i, c in enumerate(companies, 1)],
            'default_discount': '0.95',
            'numbering': {'confirmed': False, 'prefix': 'OR_', 'daily_start': 0},
            'output_policies': {'standard_order': 'sample_exact', 'finance': 'sample_exact', 'dual_confirmed': False},
            'aliases': {}, 'unit_rules': {},
            'finance': {'confirmed': False, 'tax_freight_confirmed': False,
                        'merchant_names_confirmed': False, 'price_source': 'discounted_catalog', 'order_number_mode': 'OR'}}


def execute(args, logpath):
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    logpath.parent.mkdir(parents=True, exist_ok=True)
    logpath.write_text('stdout:\n' + proc.stdout + '\nstderr:\n' + proc.stderr, encoding='utf-8')
    return proc.returncode


def line_starts(text):
    starts, n = [], 0
    for line in text.splitlines(keepends=True):
        starts.append(n)
        n += len(line)
    return starts


def align(expected, actual, text):
    starts = line_starts(text)
    groups, outputs = collections.defaultdict(list), collections.defaultdict(list)
    for e in expected:
        span = e['source_span']; start = starts[e['source_line']-1] + span['start']; end = starts[e['source_line']-1] + span['end']
        if text[start:end] != span['text']:
            raise ValueError('Frozen source span does not match chat')
        groups[(Path(e['source_file']).name, e['source_line'])].append((e, start, end))
    for a in actual:
        row = a['row']
        outputs[(row['source_name'], row['source_line'])].append(a)
    out = []
    for key in sorted(groups.keys() | outputs.keys()):
        want = sorted(groups[key], key=lambda x:x[1]); got = outputs[key]; available = set(range(len(got)))
        for e, start, end in want:
            hits = [(max(0, min(end, got[i]['row']['char_end']) - max(start, got[i]['row']['char_start'])), i) for i in available]
            hits = [x for x in hits if x[0] > 0]
            if hits:
                _, i = max(hits, key=lambda x:(x[0], -got[x[1]]['row']['char_start']))
                available.remove(i); out.append((e, got[i], start, end))
            else:
                out.append((e, None, start, end))
        out.extend((None, got[i], None, None) for i in sorted(available))
    return out


def candidate_current(record, records):
    entry = records.get(str(record.get('code')))
    return bool(entry) and all(str(record.get(k)) == str(entry.get(k)) for k in ('code', 'name', 'spec', 'price', 'sheet', 'row'))


def expected_price_unit(selected):
    """Derive the catalog sales unit, separate from a net-weight spec."""
    name = str(selected.get('name') or '')
    match = re.search(r'\d+(?:\.\d+)?\s*(?:公斤|千克|毫升|kg|ml|克|斤|两|升|g|l)\s*/\s*([袋包盒瓶罐桶箱件个支块副捆筐条颗根])', name, re.I)
    if match:
        return match.group(1)
    spec = str(selected.get('spec') or '')
    if spec in {'袋','包','盒','瓶','罐','桶','箱','件','个','支','块','副','捆','筐','条','颗','根'}:
        return spec
    return spec


def score(e, a, start, end, text, company_ids, records):
    item = a['row'] if a else {}
    automatic = bool(a and item.get('status') == 'matched' and item.get('event_state') == 'active')
    row = {'expected': e, 'actual': a, 'alignment': {'expected_start': start, 'expected_end': end,
           'actual_start': item.get('char_start'), 'actual_end': item.get('char_end')},
           'automatic': automatic, 'automatic_correct': False, 'manual_checks_passed': False,
           'candidate_completeness': 'unknown', 'errors': []}
    errors = row['errors']
    if e is None:
        errors.append('unexpected_output'); return row
    if a is None:
        errors.append('missing_output'); return row
    actual_start, actual_end = item.get('char_start'), item.get('char_end')
    if not (isinstance(actual_start,int) and isinstance(actual_end,int) and 0 <= actual_start < actual_end <= len(text)
            and text[actual_start:actual_end] == item.get('raw_text')):
        errors.append('actual_source_span')
    if not (isinstance(actual_start,int) and isinstance(actual_end,int) and actual_start <= start and actual_end >= end):
        errors.append('expected_span_not_covered')
    if a.get('company_id') != company_ids.get(e.get('company'), 'unassigned'): errors.append('company')
    if a.get('shipping_date') != e['date']: errors.append('date')
    if not same_number(item.get('quantity'), e.get('quantity')): errors.append('raw_quantity')
    if item.get('unit') != e.get('raw_unit'): errors.append('raw_unit')
    if e.get('raw_quantity') and e['raw_quantity'] not in text[start:end]: errors.append('oracle_raw_quantity_span')
    if e.get('raw_unit') and e['raw_unit'] not in text[start:end]: errors.append('oracle_raw_unit_span')
    selected = item.get('selected') or {}
    if selected and not candidate_current(selected, records): errors.append('current_selected_record')
    if any(not candidate_current(c, records) for c in item.get('candidates', [])): errors.append('current_candidate_record')
    if e['outcome'] == 'auto':
        target = e['selected']
        if not automatic: errors.append('not_automatic')
        for field,key in (('code','sku'),('name','name'),('spec','spec'),('price','price'),('sheet','sheet'),('row','row')):
            if str(selected.get(field)) != str(target.get(key)): errors.append('selected_'+field)
        if not same_number(item.get('invoice_quantity'), e['quantity_in_price_units']): errors.append('invoice_quantity')
        if item.get('price_unit') != expected_price_unit(target): errors.append('price_unit')
        expected_unit_price = Decimal(target['price']) * Decimal('0.95')
        expected_amount = expected_unit_price * Decimal(e['quantity_in_price_units'])
        for field,value in (('discount',Decimal('0.95')), ('unit_price',expected_unit_price),
                            ('amount',expected_amount),('finance_amount',expected_amount)):
            if not same_number(item.get(field), value): errors.append(field)
        row['automatic_correct'] = automatic and not errors
    else:
        if automatic or item.get('status') == 'matched': errors.append('manual_required')
        known = e.get('known_identity_record')
        if known and str(selected.get('code')) != known['sku']: errors.append('known_identity_lost')
        if not known and selected: errors.append('forced_sku')
        if item.get('amount') is not None or item.get('finance_amount') is not None: errors.append('manual_amount_not_blank')
        if e['review_reason'] in ('missing_quantity','package_weight_unknown') and item.get('invoice_quantity') is not None:
            errors.append('manual_invoice_quantity')
        witness = {str(c['sku']) for c in e.get('candidates', [])}
        actual_candidates = {str(c['code']) for c in item.get('candidates', [])}
        if witness and not witness.issubset(actual_candidates): errors.append('candidate_witness_missing')
        row['manual_checks_passed'] = not errors
    return row


def rate(n,d): return round(100*n/d, 4) if d else None


def summary(rows):
    expected = [r for r in rows if r['expected'] is not None]
    auto = [r for r in rows if r['automatic']]
    passed = sum(r['automatic_correct'] for r in rows)
    manual = sum(r['manual_checks_passed'] for r in rows)
    return {'input_events':len(expected), 'observed_outputs':sum(r['actual'] is not None for r in rows),
            'automatic':len(auto), 'automatic_correct':passed,
            'A_percent':rate(passed,len(auto)), 'B_percent':rate(passed,len(expected)),
            'manual_observed_checks_passed':manual,
            'C_percent':None, 'C_status':'not_proven: candidate_set_status unknown; manual candidate completeness unverified',
            'missing_outputs':sum('missing_output' in r['errors'] for r in rows),
            'unexpected_outputs':sum('unexpected_output' in r['errors'] for r in rows),
            'error_counts':dict(sorted(collections.Counter(x for r in rows for x in r['errors']).items()))}


def groups(rows, field):
    grouped = collections.defaultdict(list)
    for row in rows:
        e = row['expected']
        if e: grouped[str(e.get(field) or 'unknown')].append(row)
    return {name:summary(group) for name,group in sorted(grouped.items())}


def rescore_reference_diagnostics():
    """Correct witness accounting from saved first-run data; never run the CLI.

    The original frozen oracle calls its candidate examples non-exhaustive
    references, not mandatory candidates. Keep the original report and ledger;
    remove this one unsupported assertion only in separate adjudicated files.
    """
    source = EVAL/'event_ledger.json'
    output = EVAL/'event_ledger_adjudicated.json'
    if not source.is_file() or output.exists():
        raise ValueError('First ledger missing or adjudication already exists')
    ledgers = json.loads(source.read_text())
    aggregate=[]
    for ledger in ledgers.values():
        for row in ledger['rows']:
            if 'candidate_witness_missing' in row['errors']:
                row['errors'].remove('candidate_witness_missing')
                row['diagnostics'] = ['reference_candidate_not_shown; oracle did not require exhaustive candidate set']
                if row['expected'] and row['expected']['outcome'] == 'manual':
                    row['manual_checks_passed'] = not row['errors']
        rows=ledger['rows']
        ledger['summary']=summary(rows)
        ledger['by_company']=groups(rows,'company')
        ledger['by_scenario']=groups(rows,'scenario')
        ledger['normal_subset']=summary([r for r in rows if r['expected'] and r['expected']['outcome']=='auto'])
        aggregate.extend(rows)
    write(output,ledgers)
    report={'source_ledger_sha256':sha(source),
            'note':'原报告保留；仅将未穷举的参考候选未显示从失败改为诊断，未修改预期或运行输出。C仍未证。',
            'full_denominator_summary':summary(aggregate),
            'normal_subset':summary([r for r in aggregate if r['expected'] and r['expected']['outcome']=='auto']),
            'by_company':groups(aggregate,'company'), 'by_scenario':groups(aggregate,'scenario'),
            'by_seed':{seed:{k:v for k,v in entry.items() if k!='rows'} for seed,entry in ledgers.items()}}
    write(EVAL/'adjudication_v1.json',report)
    print(json.dumps(report['full_denominator_summary'],ensure_ascii=False,indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', default='fresh_rc1',
                        help='独立输出目录名；已有目录拒绝覆盖')
    parser.add_argument('--mode', choices=('catalog_only', 'shared', 'shared_with_company_contract'),
                        default='shared', help='商品匹配模式')
    parser.add_argument('--kb-sha256', default=EXPECTED_KB,
                        help='本轮事前冻结的正式知识SHA；不改变样本或预期')
    parser.add_argument('--rescore-reference', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.phase):
        parser.error('phase must be alphanumeric with underscore or hyphen')
    if not re.fullmatch(r'[0-9a-f]{64}', args.kb_sha256):
        parser.error('kb-sha256 must contain 64 lowercase hexadecimal characters')
    expected_kb = args.kb_sha256
    global EVAL, OUT
    EVAL = ROOT / 'evaluation/v7' / args.phase
    OUT = ROOT / 'outputs/v7' / args.phase
    if args.rescore_reference:
        return rescore_reference_diagnostics()
    if EVAL.exists() or OUT.exists():
        raise SystemExit('RC1 path already exists; refusing overwrite')
    EVAL.mkdir(parents=True); OUT.mkdir(parents=True)
    state = {'status':'started', 'mode':args.mode, 'expected_kb_sha256':expected_kb, 'executions':[], 'problems':[]}
    try:
        if sha(KB) != expected_kb: raise ValueError('Current formal KB differs from authorized initial fingerprint')
        state['frozen_inputs'] = verify_frozen()
        before = fingerprint(); write(EVAL/'source_before.json',before)
        state['runtime_kb_sha256'] = sha(KB)
        state['construction_draft_sha256'] = state['frozen_inputs']['7601']['construction_sources']['kb13_frozen_draft']['sha256']
        state['formal_vs_construction_draft'] = 'different hashes are expected: formal runtime KB and frozen construction draft are separate sources'
        write(EVAL/'execution_status.json',state)
        for seed in SEEDS:
            if fingerprint()!=before or sha(KB)!=expected_kb: raise RuntimeError(f'Source drift before seed {seed}; stopped')
            source = FIX / str(seed); ordinary = json.loads((source/'input/request.json').read_text())
            runtime = EVAL/'runtime'/str(seed); output = OUT/str(seed)
            catalog = (source/'input'/ordinary['catalog_file']).resolve()
            chat_paths = [(source/'input'/v).resolve() for v in ordinary['chat_files']]
            batch = {'schema_version':1, 'batch_id':'current', 'shipping_date':ordinary['batch_date'],
                     'chat_files':[str(p) for p in chat_paths], 'config':config(ordinary),
                     'matching_mode':args.mode, 'test_only':True}
            request_path = EVAL/'requests'/str(seed)/'batch.json'; write(request_path,batch)
            register = execute([sys.executable,'-B',str(CLI),'catalog-register','--runtime',str(runtime),'--file',str(catalog)], EVAL/'logs'/str(seed)/'catalog.log')
            entry={'seed':seed,'register_rc':register,'run_rc':None,'request_path':str(request_path),
                   'catalog_path':str(catalog),'result_path':str(output/'current/messages-result.json')}
            state['executions'].append(entry); write(EVAL/'execution_status.json',state)
            if fingerprint()!=before or sha(KB)!=expected_kb: raise RuntimeError(f'Source drift after catalog registration {seed}; stopped')
            if register == 0:
                entry['run_rc']=execute([sys.executable,'-B',str(CLI),'run-messages','--runtime',str(runtime),
                                         '--batch',str(request_path),'--output',str(output)],EVAL/'logs'/str(seed)/'run.log')
            if fingerprint()!=before or sha(KB)!=expected_kb: raise RuntimeError(f'Source drift after CLI {seed}; stopped')
            write(EVAL/'execution_status.json',state)
        after = fingerprint(); write(EVAL/'source_after.json',after)
        state['source_equal'] = before == after
        state['phase'] = 'all_runs_completed_before_expected_read'; write(EVAL/'execution_status.json',state)
        # Only after all CLI runs: reconstruct frozen fixtures and read expected.
        reconstructed = execute([sys.executable,'-B',str(BUILDER),'--verify'], EVAL/'generator_verify.log')
        state['generator_reconstruction_rc'] = reconstructed
        ledgers = {}; aggregate=[]; finance={}
        for execution in state['executions']:
            seed=execution['seed']; source=FIX/str(seed)
            expected=json.loads((source/'expected.json').read_text())['events']
            ordinary=json.loads((source/'input/request.json').read_text())
            company_ids={c['company_name']:f'c{i}' for i,c in enumerate(ordinary['companies'],1)}
            chat=(source/'input/聊天.txt').read_text(); result_path=Path(execution['result_path'])
            result=json.loads(result_path.read_text()) if result_path.exists() else {'events':[]}
            runtime=EVAL/'runtime'/str(seed)
            try:
                current=CatalogStore(runtime).current()
                records={str(r['code']):r for r in current['records']}
                if current['sha256'] != sha(Path(execution['catalog_path'])): raise ValueError('Current catalog SHA mismatch')
            except Exception as exc:
                records={};state['problems'].append({'seed':seed,'catalog':repr(exc)})
            try: finance[str(seed)] = audit_result(result)
            except Exception as exc: finance[str(seed)]={'errors':[{'code':'finance_audit_exception','detail':repr(exc)}]}
            rows=[score(e,a,start,end,chat,company_ids,records) for e,a,start,end in align(expected,result.get('events',[]),chat)]
            for row in rows:row['seed']=seed
            ledgers[str(seed)]={'summary':summary(rows),'by_company':groups(rows,'company'),
                                'by_scenario':groups(rows,'scenario'),
                                'normal_subset':summary([r for r in rows if r['expected'] and r['expected']['outcome']=='auto']),
                                'rows':rows}
            aggregate.extend(rows)
        write(EVAL/'finance_audit.json',finance)
        write(EVAL/'event_ledger.json',ledgers)
        report={'status':'completed', 'mode':args.mode, 'seeds':list(SEEDS), 'full_denominator_summary':summary(aggregate),
                'normal_subset':summary([r for r in aggregate if r['expected'] and r['expected']['outcome']=='auto']),
                'by_company':groups(aggregate,'company'), 'by_scenario':groups(aggregate,'scenario'),
                'by_seed':{seed:{k:v for k,v in obj.items() if k!='rows'} for seed,obj in ledgers.items()},
                'finance_error_counts':{seed:len(v.get('errors',[])) for seed,v in finance.items()},
                'source_equal':state['source_equal'],'frozen_reconstruction_rc':reconstructed,
                'runtime_kb_sha256':state['runtime_kb_sha256'],
                'construction_draft_sha256':state['construction_draft_sha256'],
                'executions':state['executions'], 'problems':state['problems'],
                'notes':['C不宣称已证：候选语义集合均标记unknown。',
                         'A/B按冻结的精确目录商品和规则条件评分，不证明真实采购准确性。',
                         '金额按请求中 sample_exact 及0.95计算；冻结名义金额未折扣，不用于对比实际折后金额。',
                         '正常子集为预期auto事件，完整分母始终单列。',
                         '保留种子首次消费；开发和保留种子共用商品池。']}
        if fingerprint()!=before:
            state['source_equal']=False;report['source_equal']=False;report['problems'].append('source drift during audit')
        write(EVAL/'report.json',report)
        state['status']='completed';write(EVAL/'execution_status.json',state)
        print(json.dumps({'summary':report['full_denominator_summary'],'normal_subset':report['normal_subset'],
                          'finance_error_counts':report['finance_error_counts'],'source_equal':report['source_equal'],
                          'reconstruction_rc':reconstructed},ensure_ascii=False,indent=2))
    except BaseException as exc:
        state['status']='stopped';state['problems'].append({'type':type(exc).__name__,'message':str(exc)})
        try:write(EVAL/'source_after.json',fingerprint())
        except Exception:pass
        write(EVAL/'execution_status.json',state)
        (EVAL/'failure.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise


if __name__=='__main__': main()
