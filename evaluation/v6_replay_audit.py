#!/usr/bin/env python3
"""Independent scoring around the real text CLI; no labels enter its process."""
from collections import Counter,defaultdict
from decimal import Decimal
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from v6_simulation_generator import frozen_oracle,generate

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'skills/order-mapping/scripts'
sys.path.insert(0,str(SCRIPTS))
from order_mapping_core.catalog import CatalogStore

MODES=('catalog_only','shared','shared_with_company_contract')
TARGET_MODES=('shared','shared_with_company_contract')
HISTORICAL_SCENARIOS=('single_station_split','single_station_reorder','cross_station_combination')


def write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def original_hashes():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'original').iterdir()) if p.is_file()}


def config(labels):
    return {'schema_version':1,'companies_confirmed':True,
        'companies':[{'company_id':key,'full_name':value,'aliases':[], 'finance_name':value} for key,value in labels.items()],
        'default_discount':'0.95','numbering':{'confirmed':False,'prefix':'OR_','daily_start':0},
        'output_policies':{'standard_order':'sample_exact','finance':'sample_exact','dual_confirmed':False},
        'aliases':{},'unit_rules':{},'finance':{'confirmed':False,'tax_freight_confirmed':False,
        'merchant_names_confirmed':False,'price_source':'discounted_catalog','order_number_mode':'OR'}}


def rate(n,d):return round(100*n/d,4) if d else None


def summarize(rows):
    total=sum('unexpected_output' not in r['errors'] for r in rows)
    auto=sum(r['automatic'] for r in rows);correct=sum(r['automatic_correct'] for r in rows)
    manual=sum(r['manual_correct'] for r in rows)
    control=sum(r.get('control_handled_correct',False) for r in rows)
    known=[r for r in rows if r['expected_action'] in ('auto','manual')]
    known_auto=sum(r['automatic'] for r in known)
    known_correct=sum(r['automatic_correct'] for r in known)
    return {'input_events':total,'automatic':auto,'automatic_correct':correct,'manual_correct':manual,
        'control_handled_correct':control,
        'A':rate(correct,auto),'B':rate(correct,total),'C':rate(correct+manual+control,total),
        # Conservative A keeps every automatically matched row in its
        # denominator.  A_known is a diagnostic for rows whose oracle action
        # is independently adjudicated; undefined rows remain visible via
        # automatic_undefined and are never silently dropped from B/C.
        'automatic_undefined':sum(r['automatic'] and r['expected_action']=='undefined' for r in rows),
        'A_known':rate(known_correct,known_auto),
        'known_automatic':known_auto,'known_automatic_correct':known_correct,
        'undefined_expectations':sum(r['expected_action']=='undefined' and 'unexpected_output' not in r['errors'] for r in rows),
        'unexpected_outputs':sum('unexpected_output' in r['errors'] for r in rows),
        'missing_outputs':sum('missing_output' in r['errors'] for r in rows),
        'failure_categories':dict(Counter(e for r in rows for e in r['errors']))}


def score_event(expected,actual,case,records):
    out={'case_id':case['case_id'],'scenario':case['scenario'],'seed':case['seed'],'split':case['split'],
         'source_event_id':expected.get('source_event_id'),'expected_action':expected['action'],
         'company':expected.get('company'),'expected':expected,'actual':actual,
         'automatic':False,'automatic_correct':False,'manual_correct':False,
         'control_handled_correct':False,'errors':[]}
    err=out['errors']
    out['pending_expected'] = bool(expected.get('control_expected_pending'))
    if not actual:err.append('missing_output');return out
    row=actual['row'];selected=row.get('selected') or {}
    state=expected.get('event_state')
    actual_state=row.get('event_state','active')
    actual_control=(row.get('event') in ('改量','取消') or
                    actual_state in ('superseded','inactive','control','cancellation_record','duplicate_event') or
                    (row.get('event')=='追加' and actual_state=='pending'))
    # Eligibility is determined from the actual result alone, never the oracle.
    out['automatic']=row['status']=='matched' and actual_state=='active' and not actual_control
    if row.get('raw_text')!=case['raw_text'][row['char_start']:row['char_end']]:err.append('source_trace')
    # Source labels are used here only, after the CLI returned its output.
    company=expected.get('company')
    if company is not None and actual['company_id']!=company:err.append('company')
    if expected.get('date') and actual['shipping_date']!=expected['date']:err.append('date')
    for key in ('quantity','unit'):
        if key in expected and row.get(key)!=expected.get(key):err.append(key)
    issues={i['code'] for i in row.get('issues',[])}
    if out['pending_expected']:
        # Pending is the expected safe outcome for an ambiguous control
        # relation.  It is reported separately and counts as correct manual
        # handling when the row is retained with blank money and traceable
        # relation evidence; it is not itself an oracle error.
        pending_evidence = {'event_pending','event_predecessor_ambiguous',
                            'unresolved_reference','order_event'}
        if (not (issues & pending_evidence) or row['status']=='matched' or
                row.get('amount') is not None or row.get('finance_amount') is not None):
            err.append('pending_contract')
    cancellation=(state=='control' and actual_state in ('control','cancellation_record')
                  and row.get('event')=='取消' and row.get('event_resolution')=='unique_text_predecessor'
                  and bool(row.get('predecessor_line_id')))
    if state=='active_or_unresolved':
        if actual_state not in ('active','pending','duplicate_event'):err.append('event_state')
    elif state=='duplicate_event':
        if actual_state not in ('active','pending','duplicate_event') or row['status']=='matched':err.append('event_state')
    elif state=='control':
        if not cancellation:err.append('event_state')
    elif state is not None and actual_state!=state:err.append('event_state')
    required=set(expected.get('required_issue_codes') or [])
    required.discard('needs_manual_review')
    if cancellation and 'cancellation' in required:
        required.remove('cancellation');required.add('cancellation_record')
    if required-issues:err.append('required_issues')
    blank_amount=state in ('superseded','inactive','control','duplicate_event') or out['pending_expected']
    if blank_amount:
        if row.get('amount') is not None:err.append('amount')
        if row.get('finance_amount') is not None:err.append('finance_amount')
    if expected['action']=='auto':
        if not blank_amount and not (row['status']=='matched' and actual_state=='active'):err.append('not_automatic')
        for key in ('code','name','spec'):
            if expected.get(key) is not None and str(selected.get(key))!=str(expected[key]):err.append(key)
        if expected.get('invoice_quantity') is not None and row.get('invoice_quantity')!=str(expected['invoice_quantity']):err.append('invoice_quantity')
        if selected:
            current=records.get(str(selected.get('code')))
            if not current or any(str(current[k])!=str(selected.get(k)) for k in ('name','spec','price')):err.append('current_catalog_record')
            if not blank_amount:
                price=Decimal(str(expected.get('catalog_price') or (records.get(str(expected.get('code'))) or {}).get('price') or selected.get('price')))
                discount=Decimal(str(expected.get('discount','0.95')))
                quantity=Decimal(str(expected.get('invoice_quantity') or '0'))
                for key,value in [('discount',discount),('unit_price',price*discount),('amount',price*discount*quantity)]:
                    if row.get(key) is None or Decimal(str(row[key]))!=value:err.append(key)
        out['automatic_correct']=out['automatic'] and not err
    elif expected['action']=='manual':
        if row['status']=='matched':err.append('manual_required')
        candidates={str(r['code']) for r in row.get('candidates',[])}
        expected_candidates={str(c) for c in expected.get('candidate_codes',[])}
        if cancellation and not candidates:
            pass
        elif expected.get('candidate_set_status')=='reviewed_complete':
            if candidates!=expected_candidates:err.append('candidate_set')
        elif not expected_candidates.issubset(candidates):err.append('candidate_witness_missing')
        if expected.get('code') is None and expected.get('candidate_set_status')=='reviewed_complete' and row.get('selected') is not None:
            err.append('forced_sku')
        if row.get('amount') is not None or row.get('finance_amount') is not None:err.append('unknown_amount')
        out['manual_correct']=not err and not actual_control and state!='duplicate_event'
    expected_control=(state in ('superseded','inactive','control','duplicate_event') or
                      out['pending_expected'] or row.get('event') in ('改量','取消'))
    if expected_control and expected['action'] in ('auto','manual') and not err:
        out['control_handled_correct']=(actual_control or out['pending_expected'] or
                                        (state=='duplicate_event' and 'duplicate_event' in issues))
        if out['control_handled_correct']:out['manual_correct']=False
    return out


def build_verification_gate(rows,runs,execution_errors,audit_errors,original_sha_unchanged,*,oracle=None,split='all'):
    """Keep execution integrity, known expectations and whole-run acceptance distinct."""
    integrity_reasons=[]; business_reasons=[]; acceptance_reasons=[]; coverage_errors=[]
    if execution_errors:integrity_reasons.append('execution_error')
    if any(r['input_events']!=r['output_events'] for r in runs):integrity_reasons.append('input_output_mismatch')
    if audit_errors:integrity_reasons.append('audit_error')
    if not original_sha_unchanged:integrity_reasons.append('original_sha_changed')
    integrity={'missing_output','unexpected_output','source_trace','multiple_outputs_in_source_span'}
    if any(integrity.intersection(r['errors']) for r in rows):integrity_reasons.append('input_output_integrity')
    target_rows=[r for r in rows if r.get('mode') in TARGET_MODES or r.get('mode') is None]
    if any('pending_contract' in r['errors'] for r in target_rows):business_reasons.append('pending_contract')
    if any(set(r['errors'])-integrity for r in target_rows):business_reasons.append('oracle_expected_case_mismatch')

    cases=[c for c in (oracle or {}).get('cases',[]) if split=='all' or c['split']==split]
    history=(oracle or {}).get('historical_events',[])
    history_ids=[e['event_id'] for e in history]
    history_companies={e['event_id']:e.get('source',{}).get('station') for e in history}
    all_cases=(oracle or {}).get('cases',[])
    seeds={c['seed'] for c in all_cases}
    if (not cases or not rows or not runs or len(history_ids)!=159 or len(set(history_ids))!=159 or
            Counter(history_companies.values())!={'songbei':53,'xingyue':42,'xueyuan':64}):
        coverage_errors.append('empty_or_invalid_baseline')
    case_by_id={c['case_id']:c for c in cases}
    if len(case_by_id)!=len(cases):coverage_errors.append('duplicate_case_ids')
    expected_runs={(mode,c['case_id']) for mode in MODES for c in cases}
    run_keys=Counter((r.get('mode'),r.get('case_id')) for r in runs)
    if set(run_keys)!=expected_runs or any(n!=1 for n in run_keys.values()):
        coverage_errors.append('mode_case_runs')
    rows_by_case=defaultdict(list)
    for r in rows:
        if 'unexpected_output' not in r['errors']:rows_by_case[(r.get('mode'),r.get('case_id'))].append(r)
    for mode,case_id in expected_runs:
        case=case_by_id[case_id]
        case_runs=[r for r in runs if r.get('mode')==mode and r.get('case_id')==case_id]
        if len(case_runs)!=1 or case_runs[0]['input_events']!=len(case['expected_events']):
            coverage_errors.append(f'run_input:{mode}:{case_id}');continue
        observed=rows_by_case.get((mode,case_id),[])
        def event_key(e):return (e.get('source_event_id'),tuple(e.get('source_span',())))
        wanted=Counter(event_key(e) for e in case['expected_events'])
        got=Counter(event_key(r.get('expected',{})) for r in observed)
        if wanted!=got or any(r.get('seed')!=case['seed'] or r.get('scenario')!=case['scenario']
                              or r.get('source_event_id')!=r.get('expected',{}).get('source_event_id')
                              or r.get('company')!=r.get('expected',{}).get('company') for r in observed):
            coverage_errors.append(f'events:{mode}:{case_id}')
        if case['scenario'] in HISTORICAL_SCENARIOS and any(
                r.get('company')!=history_companies.get(r.get('source_event_id')) for r in observed):
            coverage_errors.append(f'history_company:{mode}:{case_id}')
    for mode in MODES:
        for seed in {c['seed'] for c in cases}:
            for scenario in HISTORICAL_SCENARIOS:
                event_ids=[r.get('source_event_id') for r in rows if r.get('mode')==mode and r.get('seed')==seed
                           and r.get('scenario')==scenario and 'unexpected_output' not in r['errors']]
                if Counter(event_ids)!=Counter(history_ids):
                    coverage_errors.append(f'history:{mode}:{seed}:{scenario}')
    if coverage_errors:integrity_reasons.append('coverage_incomplete')

    thresholds={}
    for mode in TARGET_MODES:
        mode_rows=[r for r in rows if r.get('mode')==mode]
        companies=sorted({e.get('expected',{}).get('company') for e in history if e.get('expected',{}).get('company')})
        groups={'overall':mode_rows}
        groups.update({f'company:{company}':[r for r in mode_rows if r.get('company')==company] for company in companies})
        groups.update({f'scenario:{scenario}':[r for r in mode_rows if r.get('scenario')==scenario] for scenario in HISTORICAL_SCENARIOS})
        groups.update({f'company_scenario:{company}:{scenario}':[
            r for r in mode_rows if r.get('company')==company and r.get('scenario')==scenario]
            for company in companies for scenario in HISTORICAL_SCENARIOS})
        thresholds[mode]={}
        for name,group in groups.items():
            metric=summarize(group)
            passed=(metric['automatic']>0 and metric['input_events']>0 and
                    100*metric['automatic_correct']>=95*metric['automatic'] and
                    100*metric['automatic_correct']>=95*metric['input_events'])
            thresholds[mode][name]={'A':metric['A'],'B':metric['B'],'input_events':metric['input_events'],
                                     'automatic':metric['automatic'],'passed':passed}
    if any(not g['passed'] for groups in thresholds.values() for g in groups.values()):
        acceptance_reasons.append('threshold_not_met')
    if split!='all' or len(seeds)!=3 or len({r.get('seed') for r in rows})!=3:
        acceptance_reasons.append('aggregate_three_seeds_required')
    if any(r.get('pending_expected') for r in rows):acceptance_reasons.append('control_expectation_pending')
    # The v6 history-derived seeds have been inspected; the independent challenge
    # is a separate format and has not yet been connected with verifiable evidence.
    acceptance_reasons.append('independent_holdout_unverified')
    integrity_passed=not integrity_reasons
    business_passed=bool(target_rows) and not business_reasons
    acceptance_verified=integrity_passed and business_passed and not acceptance_reasons
    reasons=list(dict.fromkeys(integrity_reasons+business_reasons+acceptance_reasons))
    return {'integrity_passed':integrity_passed,'business_expectations_passed':business_passed,
            'acceptance_verified':acceptance_verified,'verified':acceptance_verified,'reasons':reasons,
            'integrity_reasons':integrity_reasons,'business_reasons':business_reasons,
            'acceptance_reasons':acceptance_reasons,'coverage_errors':coverage_errors,'thresholds':thresholds}


def aggregate_reports(oracle,oracle_sha,reports):
    """Combine independent split reports only after validating each one's scope."""
    rows=[];runs=[];issues=[]
    scorer_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for report in reports:
        split=report.get('split')
        if split not in ('development','holdout') or report.get('oracle_sha256')!=oracle_sha:
            issues.append('aggregate_report_identity');continue
        recorded_scorer=report.get('score_only',{}).get('scorer_sha256') or report.get('scorer_sha256')
        if recorded_scorer!=scorer_sha:
            issues.append('aggregate_scorer_mismatch');continue
        part=build_verification_gate(report.get('rows',[]),report.get('runs',[]),
             report.get('execution_errors',[]),report.get('audit_error_count',0),
             report.get('original_sha_unchanged') is True,oracle=oracle,split=split)
        if not part['integrity_passed']:issues.append('aggregate_split_integrity')
        rows.extend(report.get('rows',[]));runs.extend(report.get('runs',[]))
    gate=build_verification_gate(rows,runs,[],0,True,oracle=oracle,split='all')
    if issues:
        gate['integrity_passed']=False;gate['acceptance_verified']=False;gate['verified']=False
        for reason in dict.fromkeys(issues):
            gate['integrity_reasons'].append(reason);gate['reasons'].append(reason)
    return gate


def score_saved_rows(stored_rows,cases,records_by_case):
    case_map={case['case_id']:case for case in cases}
    scored=[]
    for saved in stored_rows:
        case=case_map[saved['case_id']];mode=saved['mode'];actual=saved.get('actual')
        if 'unexpected_output' in saved['errors']:
            row=actual['row']
            automatic=(row['status']=='matched' and row.get('event_state','active')=='active' and
                       row.get('event') not in ('改量','取消'))
            scored.append({**saved,'automatic':automatic,'automatic_correct':False,
                           'manual_correct':False,'control_handled_correct':False})
            continue
        expected=saved['expected']
        matches=[event for event in case['expected_events'] if event.get('source_span')==expected.get('source_span')]
        if len(matches)!=1 or matches[0]!=expected:
            raise ValueError(f'saved expectation differs from frozen oracle: {mode} {case["case_id"]}')
        event=score_event(matches[0],actual,case,records_by_case[(mode,case['case_id'])]);event['mode']=mode
        if 'multiple_outputs_in_source_span' in saved['errors']:
            event['errors'].append('multiple_outputs_in_source_span')
            event['automatic_correct']=event['manual_correct']=event['control_handled_correct']=False
        scored.append(event)
    return scored


def rescore_report(replay_path,oracle_path):
    """Rescore stored evidence without running the CLI or rebuilding workbooks."""
    replay_path=Path(replay_path)
    raw=replay_path.read_bytes();replay=json.loads(raw)
    oracle,oracle_sha=frozen_oracle(oracle_path)
    if replay.get('oracle_sha256')!=oracle_sha:raise ValueError('replay oracle hash differs')
    case_map={c['case_id']:c for c in oracle['cases']};records_by_case={};catalog_hashes={}
    for run_item in replay['runs']:
        mode=run_item['mode'];case_id=run_item['case_id'];case=case_map[case_id]
        result_path=Path(run_item['result_path'])
        if not result_path.is_file():raise ValueError(f'missing saved result: {result_path}')
        result_raw=result_path.read_bytes();result=json.loads(result_raw)
        saved_actuals=[r['actual'] for r in replay['rows'] if r['mode']==mode and r['case_id']==case_id and r.get('actual')]
        def fingerprint(item):return json.dumps(item,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        if Counter(map(fingerprint,saved_actuals))!=Counter(map(fingerprint,result['events'])):
            raise ValueError(f'saved events differ from result: {mode} {case_id}')
        version=result['catalog']['version_id']
        runtime=replay_path.parent/'runtime'/mode
        if case.get('catalog_override'):runtime=runtime/case_id
        registry=json.loads((runtime/'catalog-registry.json').read_text())
        record_bytes=(runtime/'catalogs'/version/'records.json').read_bytes()
        record_sha=hashlib.sha256(record_bytes).hexdigest()
        if record_sha!=registry['versions'][version]['records_sha256']:
            raise ValueError(f'catalog records fingerprint mismatch: {mode} {case_id}')
        records_by_case[(mode,case_id)]={str(r['code']):r for r in json.loads(record_bytes)['records']}
        catalog_hashes[f'{mode}:{case_id}']={'version':version,'records_sha256':record_sha,
                                             'saved_result_sha256':hashlib.sha256(result_raw).hexdigest()}
    rows=score_saved_rows(replay['rows'],oracle['cases'],records_by_case)
    modes=sorted({r['mode'] for r in rows})
    metrics={mode:summarize([r for r in rows if r['mode']==mode]) for mode in modes}
    gate=build_verification_gate(rows,replay['runs'],replay['execution_errors'],replay['audit_error_count'],
                                 replay['original_sha_unchanged'],oracle=oracle,split=replay['split'])
    scorer_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return {**replay,'rows':rows,'metrics':metrics,'verification_gate':gate,'scorer_sha256':scorer_sha,
            'by_company':{mode:{str(company):summarize([r for r in rows if r['mode']==mode and r['company']==company])
                                for company in {r['company'] for r in rows if r['mode']==mode}} for mode in modes},
            'by_scenario':{mode:{scenario:summarize([r for r in rows if r['mode']==mode and r['scenario']==scenario])
                                 for scenario in sorted({r['scenario'] for r in rows if r['mode']==mode})} for mode in modes},
            'score_only':{'original_replay_path':str(replay_path.resolve()),
                          'original_replay_sha256':hashlib.sha256(raw).hexdigest(),
                          'scorer_sha256':scorer_sha,
                          'catalog_and_result_hashes':catalog_hashes,
                          'new_cli_execution':False,'new_finance_audit':False}}


def run(oracle_path,split,phase,modes=MODES,seeds=None,*,evidence_root=None,output_root=None,update_index=True):
    oracle,oracle_sha=frozen_oracle(oracle_path)
    manifest=generate(oracle_path)
    cases=[c for c in oracle['cases'] if (split=='all' or c['split']==split) and (not seeds or c['seed'] in seeds)]
    base=Path(evidence_root or ROOT/'evaluation/v6')/phase;base.mkdir(parents=True,exist_ok=True)
    output=Path(output_root or ROOT/'outputs/v6')/phase
    before=original_hashes();write(base/'original_sha_before.json',before)
    catalog_path=ROOT/'original/食堂食材基准价价格表10.12-10.18.xlsx'
    # Use the activated current catalog for scoring. oracle.catalog_records is
    # intentionally limited to holdout concepts and is never the runtime input.
    records=None
    all_rows=[];runs=[];execution_errors=[];start=time.monotonic()
    for mode in modes:
        runtime=base/'runtime'/mode
        CatalogStore(runtime).register(catalog_path)
        if records is None:
            records={str(r['code']):r for r in CatalogStore(runtime).current()['records']}
        for index,case in enumerate(cases):
            case_records=records;active_runtime=runtime
            if case.get('catalog_override'):
                from openpyxl import load_workbook
                override=case['catalog_override'];book=load_workbook(catalog_path);ws=book.active
                for cells in ws.iter_rows():
                    if str(cells[1].value)==str(override['code']):cells[3].value=Decimal(override['new_price'])
                path=base/'catalog_updates'/(case['case_id']+'.xlsx');path.parent.mkdir(parents=True,exist_ok=True);book.save(path)
                active_runtime=base/'runtime'/mode/case['case_id']
                store=CatalogStore(active_runtime);store.register(catalog_path);store.register(path)
                case_records={str(r['code']):r for r in store.current()['records']}
            text_path=Path(next(c['path'] for c in manifest['cases'] if c['case_id']==case['case_id']))
            # Exactly these ordinary input keys cross into the child process.
            request={'schema_version':1,'batch_id':case['case_id'],'chat_files':[str(text_path)],
                'config':config(oracle['company_labels']),'shipping_date':'2026-11-03',
                'matching_mode':mode,'test_only':True}
            request_path=base/'requests'/mode/(case['case_id']+'.json');write(request_path,request)
            command=[sys.executable,'-B',str(SCRIPTS/'order_mapping.py'),'run-messages','--runtime',str(active_runtime),
                     '--batch',str(request_path),'--output',str(output/mode)]
            process=subprocess.run(command,text=True,capture_output=True)
            logfile=base/'cli_logs'/mode/(case['case_id']+'.log');logfile.parent.mkdir(parents=True,exist_ok=True)
            logfile.write_text(process.stdout+process.stderr)
            result=None;audits={}
            if process.returncode:
                execution_errors.append({'mode':mode,'case_id':case['case_id'],'error':process.stderr[-2000:]})
            else:
                result=json.loads((output/mode/case['case_id']/'messages-result.json').read_text())
                try:
                    from v6_finance_audit import audit_result
                    audits=audit_result(result)
                except ImportError:audits={'errors':['audit_module_unavailable']}
            actuals=result['events'] if result else []
            used=set();case_rows=[]
            for expected in case['expected_events']:
                lo,hi=expected['source_span'];found=[]
                for number,actual in enumerate(actuals):
                    row=actual['row']
                    if number not in used and lo<=row['char_start'] and row['char_end']<=hi:found.append((number,actual))
                actual=None
                if len(found)==1:used.add(found[0][0]);actual=found[0][1]
                event=score_event(expected,actual,case,case_records);event['mode']=mode
                if len(found)>1:event['errors'].append('multiple_outputs_in_source_span')
                case_rows.append(event)
            for number,actual in enumerate(actuals):
                if number not in used:
                    case_rows.append({'mode':mode,'case_id':case['case_id'],'scenario':case['scenario'],'seed':case['seed'],
                        'split':case['split'],'company':actual['company_id'],'expected_action':'undefined',
                        'automatic':actual['row']['status']=='matched' and actual['row'].get('event_state','active')=='active'
                                    and actual['row'].get('event') not in ('改量','取消'),
                        'automatic_correct':False,'manual_correct':False,
                        'errors':['unexpected_output'],'actual':actual,'expected':{}})
            all_rows.extend(case_rows)
            runs.append({'mode':mode,'case_id':case['case_id'],'input_events':len(case['expected_events']),
                'output_events':len(actuals),'cli_returncode':process.returncode,'audit':audits,
                'result_path':str(output/mode/case['case_id']/'messages-result.json')})
            print(f'{phase} {mode} {index+1}/{len(cases)} {case["case_id"]}',flush=True)
    after=original_hashes();write(base/'original_sha_after.json',after)
    metrics={mode:summarize([r for r in all_rows if r['mode']==mode]) for mode in modes}
    by_scenario={};by_company={}
    for mode in modes:
        selected=[r for r in all_rows if r['mode']==mode]
        by_scenario[mode]={key:summarize([r for r in selected if r['scenario']==key]) for key in sorted({r['scenario'] for r in selected})}
        by_company[mode]={str(key):summarize([r for r in selected if r['company']==key]) for key in {r['company'] for r in selected}}
    input_output_mismatches=[r for r in runs if r['input_events']!=r['output_events']]
    audit_errors=sum(len((r.get('audit') or {}).get('errors',[])) for r in runs)
    report={'schema_version':1,'phase':phase,'split':split,'oracle_sha256':oracle_sha,
        'scorer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'seed_count':len({c['seed'] for c in cases}),'seeds':sorted({c['seed'] for c in cases}),
        'case_count':len(cases),'unique_original_events':len({r.get('source_event_id') for r in all_rows if r.get('source_event_id')}),
        'metrics':metrics,'by_company':by_company,'by_scenario':by_scenario,'runs':runs,'rows':all_rows,
        'execution_errors':execution_errors,'input_output_mismatches':input_output_mismatches,
        'audit_error_count':audit_errors,'original_sha_unchanged':before==after,
        'verification_gate':build_verification_gate(all_rows,runs,execution_errors,audit_errors,before==after,oracle=oracle,split=split),
        'duration_seconds':round(time.monotonic()-start,2),
        'scope':'确定性原文CLI测试；历史变体/保留合成分别报告；不等于完整Agent或真实新增订单验收'}
    write(base/'replay_audit.json',report)
    if update_index:
        write(ROOT/'evaluation/v6/replay_audit.json',{'latest':str(base/'replay_audit.json'),'phase':phase})
    print(json.dumps({'phase':phase,'metrics':metrics,'execution_errors':len(execution_errors),'sha_unchanged':before==after},ensure_ascii=False),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--oracle',default=str(ROOT/'evaluation/v6/oracle/oracle_v2.json'))
    parser.add_argument('--split',choices=['development','holdout','all'],default='development')
    parser.add_argument('--phase');parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--aggregate-reports',nargs='+',metavar='REPORT')
    parser.add_argument('--score-only',metavar='REPLAY');parser.add_argument('--score-output',metavar='PATH')
    parser.add_argument('--modes',nargs='+',choices=MODES,default=list(MODES))
    parser.add_argument('--seeds',nargs='+',type=int)
    args=parser.parse_args()
    if args.self_test:
        import unittest
        suite=unittest.defaultTestLoader.discover(str(Path(__file__).parent),pattern='test_v6_replay_audit.py')
        raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
    if args.aggregate_reports:
        oracle,oracle_sha=frozen_oracle(args.oracle)
        reports=[json.loads(Path(path).read_text()) for path in args.aggregate_reports]
        print(json.dumps(aggregate_reports(oracle,oracle_sha,reports),ensure_ascii=False,indent=2))
        raise SystemExit(0)
    if args.score_only:
        if not args.score_output:parser.error('--score-output is required with --score-only')
        output_path=Path(args.score_output)
        if output_path.exists():parser.error('--score-output must be a new file')
        resolved=output_path.resolve()
        if any(resolved.is_relative_to(protected.resolve()) for protected in
               (ROOT/'original',ROOT/'evaluation/v6/oracle')):
            parser.error('--score-output must not be under original or frozen oracle')
        write(output_path,rescore_report(args.score_only,args.oracle))
        print(json.dumps({'score_output':str(output_path),'new_cli_execution':False},ensure_ascii=False))
        raise SystemExit(0)
    if not args.phase:parser.error('--phase is required unless --self-test is used')
    run(args.oracle,args.split,args.phase,args.modes,args.seeds)
