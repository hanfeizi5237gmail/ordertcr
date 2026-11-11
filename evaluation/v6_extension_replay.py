#!/usr/bin/env python3
"""Blind raw-message replay of the frozen 7301/7302 development extension.

The application sees ordinary company config, raw chat and current catalog.
Oracle JSON is loaded only after all six application executions have finished.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from v6_challenge_replay import score, summary
from v6_finance_audit import audit_result
from v6_replay_audit import config, MODES, original_hashes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/order-mapping/scripts'))
from order_mapping_core.catalog import CatalogStore

DEV_FREEZES = {
    7301: '531c19528419608b92b062aba7eae34cabc10925d9cda0eff4070834bae05c9b',
    7302: '3ae3148a933003a8482eed9809d7aedc2f561a7e71e54f7eee61c20c377cee42',
    7391: '6e27deda166ce18698e96a7f11acb5039fd55f378464c1dd2c74a759db84e748',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def source_hashes():
    source = ROOT / 'skills/order-mapping'
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(source.rglob('*'))
            if p.is_file() and p.suffix in ('.py', '.json', '.md') and '__pycache__' not in p.parts}


def verify_frozen(seed):
    source = ROOT / 'evaluation/v6/challenge_extension' / str(seed)
    freeze_path = source / 'FREEZE.json'
    if sha(freeze_path) != DEV_FREEZES[seed]:
        raise ValueError(f'freeze SHA mismatch: {seed}')
    frozen = json.loads(freeze_path.read_text(encoding='utf-8'))
    # The immutable manifest is checked before its contents are trusted.
    for name, digest in frozen['sha256'].items():
        path = ROOT / 'evaluation/v6_challenge_extension_builder.py' if name == 'builder' else source / name
        if sha(path) != digest:
            raise ValueError(f'freeze content mismatch: {seed}/{name}')
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    for name, digest in manifest['input_sha256'].items():
        if sha(source / name) != digest:
            raise ValueError(f'input SHA mismatch: {seed}/{name}')
    if sha(source / 'expected.json') != manifest['expected_sha256']:
        raise ValueError(f'oracle SHA mismatch: {seed}')
    return source, manifest


def make_config(request):
    companies = request['companies']
    if not companies or any(set(c) != {'company_name', 'company_alias'} for c in companies):
        raise ValueError('invalid company initialization scope')
    labels = {f'c{i}': item['company_name'] for i, item in enumerate(companies, 1)}
    result = config(labels)
    for entry, item in zip(result['companies'], companies):
        entry['aliases'] = [item['company_alias']]
    return result, {name: key for key, name in labels.items()}


def line_starts(text):
    starts = []
    for line in text.splitlines(keepends=True):
        starts.append(starts[-1] + len(text.splitlines(keepends=True)[len(starts)-1]) if starts else 0)
    return starts


def align(wanted, actual, text):
    """One-to-one, line-local matching by overlap of frozen and real spans.

    Neither identity, SKU, company nor correctness is used for alignment.
    Any leftover output is independently reported as unexpected.
    """
    starts = []
    position = 0
    for line in text.splitlines(keepends=True):
        starts.append(position)
        position += len(line)
    wanted_groups, actual_groups = defaultdict(list), defaultdict(list)
    for event in wanted:
        span = event['source_span']
        if span.get('unit') != 'unicode_codepoints' or span.get('basis') != 'line':
            raise ValueError('unsupported source span')
        start = starts[event['source_line'] - 1] + span['start']
        end = starts[event['source_line'] - 1] + span['end']
        if text[start:end] != span['text']:
            raise ValueError('frozen source span does not match runtime text')
        wanted_groups[(Path(event['source_file']).name, event['source_line'])].append((event, start, end))
    for item in actual:
        row = item['row']
        actual_groups[(row['source_name'], row['source_line'])].append(item)
    pairs = []
    for key in sorted(wanted_groups.keys() | actual_groups.keys()):
        expected = sorted(wanted_groups[key], key=lambda x: x[1])
        outputs = sorted(actual_groups[key], key=lambda x: x['row']['char_start'])
        unused = set(range(len(outputs)))
        for event, start, end in expected:
            hits = []
            for idx in unused:
                row = outputs[idx]['row']
                overlap = max(0, min(end, row['char_end']) - max(start, row['char_start']))
                if overlap:
                    hits.append((overlap, -row['char_start'], idx))
            actual_item = outputs[max(hits)[2]] if hits else None
            if hits:
                unused.remove(max(hits)[2])
            pairs.append((event, actual_item, (start, end), len(hits)))
        for idx in sorted(unused):
            pairs.append((None, outputs[idx], None, 0))
    return pairs


def run(seeds=(7301, 7302), phase='extension_dev_r1'):
    base = ROOT / 'evaluation/v6' / phase
    output_base = ROOT / 'outputs/v6' / phase
    if base.exists() or output_base.exists():
        raise ValueError('phase exists; refusing to overwrite previous evidence')
    # Everything here is checked before the production process sees a chat.
    verified = {seed: verify_frozen(seed) for seed in seeds}
    before = source_hashes()
    originals_before = original_hashes()
    base.mkdir(parents=True)
    write(base / 'original_sha_before.json', originals_before)
    executions = []
    for seed, (source, manifest) in verified.items():
        ordinary = json.loads((source / 'input/request.json').read_text(encoding='utf-8'))
        cfg, company_ids = make_config(ordinary)
        chat_files = [source / 'input' / name for name in ordinary['chat_files']]
        for mode in MODES:
            case = f's{seed}-{mode}'
            runtime = base / 'runtime' / str(seed) / mode
            store = CatalogStore(runtime)
            store.register(source / 'input' / ordinary['catalog_file'])
            records = {str(r['code']): r for r in store.current()['records']}
            request = {'schema_version': 1, 'batch_id': case, 'shipping_date': ordinary['batch_date'],
                       'config': cfg, 'chat_files': [str(p) for p in chat_files],
                       'test_only': True, 'matching_mode': mode}
            # Intentionally omit `company`: an unknown company cannot inherit a fallback.
            request_path = base / 'requests' / str(seed) / mode / 'batch.json'
            write(request_path, request)
            out = output_base / str(seed) / mode
            proc = subprocess.run([sys.executable, '-B', str(ROOT / 'skills/order-mapping/scripts/order_mapping.py'),
                                   'run-messages', '--runtime', str(runtime), '--batch', str(request_path),
                                   '--output', str(out)], text=True, capture_output=True)
            log = base / 'logs' / str(seed) / mode / 'run.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(proc.stdout + proc.stderr, encoding='utf-8')
            result_path = out / case / 'messages-result.json'
            execution = {'seed': seed, 'mode': mode, 'batch': case, 'returncode': proc.returncode,
                         'request': str(request_path), 'result_path': str(result_path),
                         'log': str(log), 'company_ids': company_ids, 'records': records,
                         'source': source, 'chat_files': chat_files}
            if proc.returncode == 0 and result_path.exists():
                result = json.loads(result_path.read_text(encoding='utf-8'))
                execution['result'] = result
                execution['audit'] = audit_result(result)
            else:
                execution['result'] = {'events': []}
                execution['audit'] = {'errors': [{'code': 'execution_error', 'where': case,
                                                   'detail': proc.stderr[-2000:]}]}
            executions.append(execution)
    # Only now may evaluation read business answers. No execution below.
    after = source_hashes()
    originals_after = original_hashes()
    write(base / 'original_sha_after.json', originals_after)
    oracles = {seed: json.loads((source / 'expected.json').read_text(encoding='utf-8'))
               for seed, (source, _) in verified.items()}
    rows, runs = [], []
    for run_data in executions:
        seed = run_data['seed']; mode = run_data['mode']
        expected = oracles[seed]['events']
        result = run_data['result']
        texts = {p.name: p.read_text(encoding='utf-8-sig') for p in run_data['chat_files']}
        pairs = align(expected, result['events'], texts[run_data['chat_files'][0].name])
        for event, actual, span, hits in pairs:
            expected_company = (run_data['company_ids'].get(event['company'], 'unassigned') if event else '')
            row = score(event, actual, company=expected_company,
                        day=event['date'] if event else '', records=run_data['records'])
            if actual:
                item = actual['row']; text = texts.get(item['source_name'], '')
                if text[item['char_start']:item['char_end']] != item.get('raw_text'):
                    row['errors'].append('source_trace')
                if span and not (item['char_start'] <= span[0] and item['char_end'] >= span[1]):
                    row['errors'].append('incomplete_source_span')
                if hits > 1:
                    row['errors'].append('multiple_outputs_in_expected_span')
                if event and event.get('anomaly') == 'company_unknown':
                    issues = {x.get('code') for x in item.get('issues', []) if isinstance(x, dict)}
                    if actual['company_id'] != 'unassigned' or 'company_boundary' not in issues:
                        row['errors'].append('unknown_company_boundary')
                if event and event.get('anomaly') == 'unit_or_packaging_unverified':
                    issues = {x.get('code') for x in item.get('issues', []) if isinstance(x, dict)}
                    if not issues.intersection({'quantity_conversion','different_specs','semantic_review',
                                                'attribute_conflict','unmapped','unit_unresolved'}):
                        row['errors'].append('packaging_issue_missing')
            if row['errors']:
                row['automatic_correct'] = row['manual_correct'] = row['control_correct'] = False
            row.update(seed=seed, mode=mode, batch=run_data['batch'],
                       company=event['company'] if event else 'unexpected',
                       scenario=event['scenario'] if event else 'unexpected_output',
                       expected_absolute_span=list(span) if span else None)
            rows.append(row)
        runs.append({'seed':seed,'mode':mode,'batch':run_data['batch'],'returncode':run_data['returncode'],
                     'request':run_data['request'],'result_path':run_data['result_path'],'log':run_data['log'],
                     'expected_events':len(expected),'actual_events':len(result['events']),'audit':run_data['audit']})
    modes = list(MODES)
    report = {'schema':'ordertcr.v6_extension_replay.v1','phase':phase,'seeds':list(seeds),
              'source_unchanged':before==after,'source_before':before,'source_after':after,
              'original_sha_unchanged': originals_before == originals_after,
              'oracle_access':'hash verified before execution; JSON loaded only after all six executions',
              'routing':'company ids and aliases from request.json and text; no request fallback company',
              'frozen':{str(s):{'freeze_sha256':DEV_FREEZES[s], 'expected_sha256':m['expected_sha256'],
                                'input_sha256':m['input_sha256']} for s,(_,m) in verified.items()},
              'metrics':{m:summary([r for r in rows if r['mode']==m]) for m in modes},
              'normal_supported_metrics':{m:summary([r for r in rows if r['mode']==m and r.get('expected')
                                                     and r['expected']['outcome']=='selected']) for m in modes},
              'by_company':{m:{c:summary([r for r in rows if r['mode']==m and r['company']==c])
                               for c in sorted({r['company'] for r in rows if r['mode']==m})} for m in modes},
              'by_scenario':{m:{s:summary([r for r in rows if r['mode']==m and r['scenario']==s])
                                for s in sorted({r['scenario'] for r in rows if r['mode']==m})} for m in modes},
              'execution_errors':sum(r['returncode']!=0 for r in runs),
              'audit_error_count':sum(len(r['audit'].get('errors',[])) for r in runs),
              'runs':runs,'rows':rows,
              'limitation':'Synthetic development only; score does not establish exhaustive candidate completeness or held-out acceptance.'}
    write(base / 'replay_audit.json', report)
    print(json.dumps({k:report[k] for k in ('phase','metrics','execution_errors','audit_error_count','source_unchanged')},ensure_ascii=False))


def rescore_reviewed(phase='extension_dev_r1'):
    """Apply documented conservative review corrections without rerunning CLI."""
    base = ROOT / 'evaluation/v6' / phase
    original = base / 'replay_audit.json'
    destination = base / 'replay_audit_reviewed.json'
    if destination.exists():
        raise ValueError('reviewed score already exists; refusing to overwrite')
    report = json.loads(original.read_text(encoding='utf-8'))
    adjusted = []
    for row in report['rows']:
        event, actual = row.get('expected'), row.get('actual')
        if not event or not actual or event.get('candidate_set_status') != 'not_exhaustive':
            continue
        item = actual['row']; selected = item.get('selected') or {}
        selected_code = str(selected.get('code')) if selected else None
        witnesses = {str(x['sku']) for x in event.get('candidates', [])}
        kind = event.get('anomaly')
        reasons = []
        if kind == 'brand_unverified' and 'candidate_witness_missing' in row['errors']:
            row['errors'].remove('candidate_witness_missing')
            reasons.append('unbranded catalog item is a reference, not a proven brand-compatible witness')
        if kind in ('unit_or_packaging_unverified', 'packaging_or_sku_ambiguous') and 'forced_sku' in row['errors']:
            row['errors'].remove('forced_sku')
            if selected_code in witnesses:
                reasons.append('known product identity may remain selected while unit conversion is pending')
            else:
                row['errors'].append('identity_unverified')
                reasons.append('incomplete oracle cannot prove whether selected product identity is unique')
        if reasons:
            row['review_adjustment'] = reasons
            # Incomplete candidate coverage does not become a verified manual success.
            if row['errors'] or row.get('candidate_completeness') == 'unverified':
                row['automatic_correct'] = row['manual_correct'] = row['control_correct'] = False
            adjusted.append({'seed':row['seed'],'mode':row['mode'],
                             'event_id':event['event_id'],'reasons':reasons})
    modes = list(MODES); rows = report['rows']
    report['metrics'] = {m:summary([r for r in rows if r['mode']==m]) for m in modes}
    report['by_company'] = {m:{c:summary([r for r in rows if r['mode']==m and r['company']==c])
                                  for c in sorted({r['company'] for r in rows if r['mode']==m})} for m in modes}
    report['by_scenario'] = {m:{v:summary([r for r in rows if r['mode']==m and r['scenario']==v])
                                   for v in sorted({r['scenario'] for r in rows if r['mode']==m})} for m in modes}
    report['review_adjustments'] = adjusted
    report['reviewed_from'] = {'path':str(original),'sha256':sha(original),
                               'note':'Scoring correction only; frozen oracle and raw CLI outputs unchanged; no production rerun.'}
    write(destination, report)
    print(json.dumps({'reviewed_file':str(destination),'adjusted_rows':len(adjusted),
                      'metrics':report['metrics']},ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rescore-reviewed', action='store_true', help='correct reviewed anomaly semantics without rerunning CLI')
    parser.add_argument('--seeds', type=int, nargs='+', choices=DEV_FREEZES, default=[7301,7302])
    parser.add_argument('--phase', default='extension_dev_r1')
    args=parser.parse_args()
    rescore_reviewed(args.phase) if args.rescore_reviewed else run(args.seeds,args.phase)
