#!/usr/bin/env python3
"""Re-score frozen KB16 v2 executions against the post-observation v3 oracle."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import v7_kb16_v2_audit as common

ROOT = Path(__file__).resolve().parents[1]
ORACLE = ROOT / 'evaluation/v7/kb17_v2_oracle_v3'
CHALLENGE = ROOT / 'evaluation/v7/kb16_challenge_v2'


def verify_oracle():
    freeze = common.read(ORACLE / 'FREEZE.json')
    for path, digest in freeze['file_sha256'].items():
        if common.sha(ORACLE / path) != digest:
            raise ValueError(f'oracle freeze mismatch: {path}')
    for seed, paths in freeze['original_frozen_sha256'].items():
        for path, digest in paths.items():
            if common.sha(CHALLENGE / seed / path) != digest:
                raise ValueError(f'original challenge mismatch: {seed}/{path}')
    return freeze


def score_pairs(expected, actual, company_ids, chat):
    pairs, extras = common.align_events(expected, actual, chat)
    # A relation is provable only when the expected predecessor aligns to one
    # real row with a unique nonempty actual source identity in this run.
    identity_counts = Counter(x.get('row', {}).get('line_id') for x in actual if x.get('row', {}).get('line_id'))
    aligned = {event['event_id']: item for event, item, error in pairs if item is not None and error is None}
    output = []
    for event, item, reason in pairs:
        effective = dict(event)
        if event['kind'] == 'manual_review':
            effective['kind'] = 'unknown_pack'  # same status, amount and invoice safety contract
        if event['kind'] == 'append':
            prior = aligned.get(event.get('expected_predecessor_id'))
            row = prior.get('row', {}) if prior else {}
            identity = row.get('line_id')
            if identity and identity_counts[identity] == 1 and prior.get('company_id') == (item or {}).get('company_id') and prior.get('shipping_date') == (item or {}).get('shipping_date'):
                effective['expected_predecessor_id'] = identity
            else:
                effective['expected_predecessor_id'] = None
        scored = common.score_event(effective, item, company_ids, chat)
        scored['kind'] = event['kind']
        scored['expected'] = event
        if item is not None:
            span = event['source']['derived_line_span']
            row = item.get('row', {})
            if row.get('char_start') != span['start'] or row.get('char_end') != span['end']:
                scored['errors'].append('oracle_span_mismatch')
                for key in ('automatic_correct', 'correct', 'review_correct', 'control_correct', 'control_safe'):
                    scored[key] = False
        if reason == 'multiple_matching_outputs':
            scored['errors'].append(reason)
        output.append(scored)
    for extra in extras:
        row = extra.get('row', {})
        output.append({'event_id': None, 'kind': 'unexpected_output', 'company': None, 'concept': None,
                       'source_line': row.get('source_line'), 'source_text': row.get('raw_text'),
                       'automatic': common.is_automatic(row), 'automatic_correct': False, 'correct': False,
                       'review_correct': False, 'control_correct': False, 'control_safe': False,
                       'errors': ['unexpected_output'], 'actual': extra})
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--status', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    dest = args.output.resolve()
    if dest.exists():
        raise ValueError('output must be a new directory')
    oracle_freeze = verify_oracle()
    status = common.read(args.status)
    source_freeze = common.read(args.status.parent / 'input_freeze.json')
    executions = common.validate_execution_inventory(status, source_freeze)
    mismatches = [path for path, digest in source_freeze['files_sha256'].items()
                  if not (ROOT / path).is_file() or common.sha(ROOT / path) != digest]
    fatal = [p for p in mismatches if not p.startswith(('skills/', 'original/'))]
    if fatal:
        raise ValueError('frozen execution artifact changed: ' + ', '.join(fatal))
    runs, modes = [], defaultdict(list)
    for run in executions:
        seed, mode = str(run['seed']), run['mode']
        expected_doc = common.read(ORACLE / seed / 'expected.json')
        expected = expected_doc['events']
        if len(expected) != 32 or len({e['event_id'] for e in expected}) != 32:
            raise ValueError('oracle events must be 32 unique events per seed')
        base = CHALLENGE / seed
        chat = (base / 'input/聊天.txt').read_text(encoding='utf-8-sig')
        request = common.read(base / 'input/request.json')
        companies = {x['company_name']: f'c{i}' for i, x in enumerate(request['companies'], 1)}
        companies['未登记单位'] = 'unassigned'
        result = common.read(run['result'])
        scored = score_pairs(expected, result.get('events', []), companies, chat)
        finance = common.audit_result(result)
        modes[mode].extend(scored)
        runs.append({'seed': int(seed), 'mode': mode, 'summary': common.metric(scored),
                     'by_kind': {k: common.metric([e for e in scored if e['kind'] == k]) for k in sorted({e['kind'] for e in scored})},
                     'failure_categories': dict(Counter(x for e in scored for x in e['errors'])),
                     'missing_outputs': sum('missing_output' in e['errors'] for e in scored),
                     'unexpected_outputs': sum('unexpected_output' in e['errors'] for e in scored),
                     'finance_audit': finance, 'events': scored})
    summaries = {mode: common.metric(rows) for mode, rows in modes.items()}
    if any(summary['events'] != oracle_freeze['denominator'] for summary in summaries.values()):
        raise ValueError('oracle denominator mismatch')
    report = {'schema_version': 1, 'scope': 'post-observation development regression; not unseen accuracy',
              'oracle_freeze_sha256': common.sha(ORACLE / 'FREEZE.json'), 'status_sha256': common.sha(args.status),
              'execution_freeze_sha256': common.sha(args.status.parent / 'input_freeze.json'),
              'scorer_sha256': common.sha(Path(__file__)), 'common_scorer_sha256': common.sha(Path(common.__file__)),
              'source_mismatches': mismatches, 'mode_summaries': summaries, 'runs': runs,
              'C_policy': {'status': 'unknown', 'reason': 'semantic candidate completeness is undefined or unknown'},
              'relation_policy': 'expected predecessor ID is resolved through alignment to unique actual line_id; row predecessor_id must match'}
    dest.mkdir(parents=True)
    (dest / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    lines = ['# KB17 v3 oracle audit', '', 'Post-observation development regression; not an unseen holdout. C remains unknown.', '',
             '| mode | input | auto | proven auto correct | A | B | business processing | missing | extra | finance errors |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for mode, m in summaries.items():
        rs = [r for r in runs if r['mode'] == mode]
        lines.append(f"| {mode} | {m['events']} | {m['automatic']} | {m['automatic_correct']} | {m['A']} | {m['B']} | {m['business_processing_rate']} | {sum(r['missing_outputs'] for r in rs)} | {sum(r['unexpected_outputs'] for r in rs)} | {sum(len(r['finance_audit']['errors']) for r in rs)} |")
    lines.extend(['', 'Automatic append requires a verified predecessor identity. Safe pending control handling does not prove relationship resolution.', 'Detailed results, provenance and control error categories are in report.json.'])
    (dest / 'report.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'mode_summaries': summaries}, ensure_ascii=False))


if __name__ == '__main__':
    main()
