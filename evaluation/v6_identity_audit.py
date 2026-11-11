#!/usr/bin/env python3
"""Score fixed historical manual SKU agreement from saved v6 replay events.

This evaluation-only comparison uses the v5 frozen 132-event universe, the
v4 historical oracle and the original manual names/specifications. It does not
send oracle/manual data to production and never treats manual quantities as
requested quantities. It is a historical identity comparison, not A/B/C or
new-order accuracy.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import unicodedata
import unittest

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / 'evaluation/v6'
ORACLE = EVAL / 'oracle/oracle_v4.json'
V5_REPORTS = (ROOT / 'evaluation/v5/replay_audit_20261108.json',
              ROOT / 'evaluation/v5/replay_audit_20261109.json')
CATALOG = ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx'
V5_SCENARIOS = ('single_station_random_split', 'single_station_reorder', 'cross_station_random_combinations')
SCENARIOS = ('single_station_split', 'single_station_reorder', 'cross_station_combination')
STATIONS = {'songbei': 53, 'xingyue': 42, 'xueyuan': 64}
EXPECTED_SKU_COUNTS = {'songbei': 44, 'xingyue': 35, 'xueyuan': 53}
MODES = ('catalog_only', 'shared', 'shared_with_company_contract')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def norm(value):
    return ''.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()


def pct(n, d):
    return round(100 * n / d, 4) if d else None


def load_catalog(path, expected_sha):
    if sha(path) != expected_sha:
        raise ValueError('current catalog fingerprint differs from frozen oracle')
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = book['Sheet0']
        headers = [sheet.cell(4, i).value for i in range(1, 6)]
        if headers != ['类别名称', '材料编号', '材料名称', '挂牌价', '规格']:
            raise ValueError('catalog columns changed')
        records = defaultdict(list)
        for category, code, name, price, spec in sheet.iter_rows(min_row=5, max_col=5, values_only=True):
            if code is not None:
                records[str(code)].append({'code': str(code), 'name': name, 'spec': spec})
        return records
    finally:
        book.close()


def frozen_universe(oracle, reports, catalog):
    """Determine IDs solely from fixed v5 manual labels, independently verify them."""
    history = oracle['historical_events']
    source = {event['event_id']: event for event in history}
    if len(source) != 159 or len(history) != 159:
        raise ValueError('historical oracle must contain 159 unique source events')
    if Counter(e['source']['station'] for e in history) != STATIONS:
        raise ValueError('historical station denominator mismatch')
    baselines = []
    for report in reports:
        if report.get('catalog_version') != oracle['catalog_sha256']:
            raise ValueError('v5 baseline catalog differs from oracle')
        for scenario in V5_SCENARIOS:
            items = report['scenarios'][scenario]['lanes']['profile_only']['items']
            by_id = {r['source_event_id']: r for r in items}
            if len(items) != 159 or len(by_id) != 159 or set(by_id) != set(source):
                raise ValueError(f'v5 fixed 159-event universe differs: {scenario}')
            baselines.append(by_id)
    baseline = baselines[0]
    for other in baselines[1:]:
        for event_id, item in baseline.items():
            if any(item.get(k) != other[event_id].get(k) for k in ('source_station', 'manual_row', 'manual_expected_code')):
                raise ValueError(f'v5 frozen universe differs across seeds/scenarios: {event_id}')
    workbooks = {}
    items = {}
    try:
        for event_id, event in source.items():
            row = baseline[event_id]
            manual = event['manual']
            observed = manual.get('observed') or {}
            station = event['source']['station']
            if row['source_station'] != station:
                raise ValueError(f'v5 station differs from oracle: {event_id}')
            if row.get('manual_row') != observed.get('row'):
                raise ValueError(f'v5 manual alignment differs from oracle: {event_id}')
            filename = manual['file']
            if Path(filename).name != filename:
                raise ValueError('invalid manual workbook path')
            manual_path = ROOT / 'original' / filename
            if manual_path not in workbooks:
                if sha(manual_path) != manual['sha256'] or sha(manual_path) != oracle['source_hashes'][filename]:
                    raise ValueError(f'manual workbook hash mismatch: {filename}')
                for report in reports:
                    if report['manual_workbook_sha'][station] != manual['sha256']:
                        raise ValueError(f'v5 manual workbook hash mismatch: {filename}')
                workbooks[manual_path] = load_workbook(manual_path, read_only=True, data_only=True)
            code = row.get('manual_expected_code')
            expected_record = None
            undefined = []
            if observed:
                sheet = workbooks[manual_path][manual['sheet']]
                row_number = observed['row']
                if norm(sheet.cell(row_number, 4).value) != norm(observed['name']) or norm(sheet.cell(row_number, 5).value) != norm(observed['spec']):
                    undefined.append('manual_name_spec_mismatch')
                v5manual = row.get('manual_workbook_row') or {}
                fields = v5manual.get('fields') or {}
                if (v5manual.get('row') != row_number or v5manual.get('mismatches') or
                    norm(fields.get('name')) != norm(observed.get('name')) or
                    norm(fields.get('spec')) != norm(observed.get('spec'))):
                    undefined.append('v5_manual_snapshot_mismatch')
            if code is not None:
                records = catalog.get(str(code), [])
                if len(records) != 1:
                    undefined.append('catalog_code_not_unique')
                elif not observed or norm(records[0]['name']) != norm(observed.get('name')) or norm(records[0]['spec']) != norm(observed.get('spec')):
                    undefined.append('manual_catalog_identity_mismatch')
                else:
                    expected_record = records[0]
            items[event_id] = {'event_id': event_id, 'station': station,
                               'manual_row': row.get('manual_row'), 'manual_file': filename,
                               'manual_name': observed.get('name'), 'manual_spec': observed.get('spec'),
                               'sku_in_fixed_132': code is not None, 'expected_code': str(code) if code is not None else None,
                               'catalog_record': expected_record, 'identity_undefined_reasons': undefined,
                               'source_raw_text': event['source']['raw_text']}
    finally:
        for book in workbooks.values():
            book.close()
    fixed = [x for x in items.values() if x['sku_in_fixed_132']]
    aligned = [x for x in items.values() if x['manual_row'] is not None]
    if len(fixed) != 132 or len(aligned) != 155 or Counter(x['station'] for x in fixed) != EXPECTED_SKU_COUNTS:
        raise ValueError('v5 132/155 fixed universe cannot be independently established')
    return items


def score_event(item, rows):
    result = {**item, 'output_count': len(rows), 'identity_outcome': 'outside_132',
              'actual_code': None, 'actual_status': None, 'product_status': None,
              'routing_company_mismatch': False, 'replay_errors': []}
    if not rows:
        result['identity_outcome'] = 'missing_output' if item['sku_in_fixed_132'] else 'outside_132_missing_output'
        return result
    if len(rows) != 1:
        result['identity_outcome'] = 'multiple_outputs' if item['sku_in_fixed_132'] else 'outside_132_multiple_outputs'
        return result
    saved = rows[0]
    actual = saved.get('actual')
    result['replay_errors'] = saved.get('errors', [])
    if not actual:
        result['identity_outcome'] = 'missing_output' if item['sku_in_fixed_132'] else 'outside_132_missing_output'
        return result
    row = actual['row']
    selected = row.get('selected') or {}
    result.update(actual_code=str(selected['code']) if selected.get('code') is not None else None,
                  actual_status=row.get('status'), product_status=row.get('product_status'),
                  routing_company_mismatch=actual.get('company_id') != item['station'])
    if not item['sku_in_fixed_132']:
        return result
    if item['identity_undefined_reasons'] or item['catalog_record'] is None:
        result['identity_outcome'] = 'undefined_reference'
    elif ('multiple_outputs_in_source_span' in result['replay_errors']):
        result['identity_outcome'] = 'multiple_outputs'
    elif ('source_trace' in result['replay_errors'] or row.get('raw_text') != item['source_raw_text']):
        result['identity_outcome'] = 'undefined_provenance'
    elif row.get('product_status') != 'matched' or not selected.get('code'):
        result['identity_outcome'] = 'unselected'
    elif str(selected['code']) != item['expected_code']:
        result['identity_outcome'] = 'different_sku'
    elif norm(selected.get('name')) != norm(item['catalog_record']['name']) or norm(selected.get('spec')) != norm(item['catalog_record']['spec']):
        result['identity_outcome'] = 'undefined_selected_identity'
    else:
        result['identity_outcome'] = 'correct'
    return result


def metrics(rows, expected_events, expected_manual, expected_skus):
    counts = Counter(r['identity_outcome'] for r in rows if r['sku_in_fixed_132'])
    correct = counts['correct']
    # A reference can be undefined even when its output is missing or split;
    # never let the output outcome mask uncertainty in the fixed universe.
    undefined = sum(bool(r['identity_undefined_reasons']) or r['identity_outcome'] in
                    ('undefined_reference', 'undefined_provenance', 'undefined_selected_identity')
                    for r in rows if r['sku_in_fixed_132'])
    def rate(denominator):
        return {'correct': correct, 'denominator': denominator,
                'rate_percent': None if undefined else pct(correct, denominator),
                'conservative_percent_if_undefined_counted_incorrect': pct(correct, denominator)}
    return {'strict132': rate(expected_skus), 'coverage155': rate(expected_manual), 'coverage159': rate(expected_events),
            'undefined_fixed_sku': undefined, 'fixed_sku_outcomes': dict(counts),
            'outside_fixed_sku': sum(not r['sku_in_fixed_132'] for r in rows),
            'manual_row_missing': sum(r['manual_row'] is None for r in rows),
            'output_missing_events': sum(r['identity_outcome'] in ('missing_output', 'outside_132_missing_output') for r in rows),
            'multiple_output_events': sum(r['identity_outcome'] in ('multiple_outputs', 'outside_132_multiple_outputs') for r in rows),
            'routing_company_mismatches': sum(r['routing_company_mismatch'] for r in rows)}


def assess_group(universe, rows):
    by_id = defaultdict(list)
    unexpected = []
    for row in rows:
        event_id = row.get('source_event_id')
        if event_id in universe:
            by_id[event_id].append(row)
        else:
            unexpected.append(event_id)
    results = [score_event(item, by_id[event_id]) for event_id, item in universe.items()]
    total = metrics(results, 159, 155, 132)
    stations = {}
    for station, events in STATIONS.items():
        subset = [r for r in results if r['station'] == station]
        stations[station] = metrics(subset, events, sum(r['manual_row'] is not None for r in subset), EXPECTED_SKU_COUNTS[station])
    rates = {k: value['strict132']['rate_percent'] for k, value in stations.items()}
    known = all(value is not None for value in rates.values())
    macro = round(sum(rates.values()) / len(rates), 4) if known else None
    worst = min(rates, key=rates.get) if known else None
    linked = [r for r in results if r['manual_row'] is not None]
    keys = [(r['manual_file'], r['manual_row']) for r in linked]
    return {'metrics': total, 'per_station': stations,
            'micro_strict_rate_percent': total['strict132']['rate_percent'],
            'macro_strict_rate_percent': macro, 'worst_station': worst,
            'worst_station_rate_percent': rates[worst] if worst else None,
            'manual_row_links': len(keys), 'distinct_manual_rows': len(set(keys)),
            'manual_merged_extra_event_links': len(keys) - len(set(keys)),
            'unexpected_source_event_ids': unexpected, 'events': results}


def run(replay_path, output):
    oracle = read(ORACLE)
    replay = read(replay_path)
    oracle_hash = sha(ORACLE)
    if replay.get('oracle_sha256') != oracle_hash:
        raise ValueError('replay is not bound to this oracle SHA-256')
    reports = [read(path) for path in V5_REPORTS]
    catalog = load_catalog(CATALOG, oracle['catalog_sha256'])
    universe = frozen_universe(oracle, reports, catalog)
    grouped = {}
    for seed in replay['seeds']:
        for scenario in SCENARIOS:
            for mode in MODES:
                selected = [r for r in replay['rows'] if r.get('seed') == seed and r.get('scenario') == scenario and r.get('mode') == mode]
                grouped[f'{seed}/{scenario}/{mode}'] = assess_group(universe, selected)
    out = {'schema_version': 1, 'phase': replay.get('phase'), 'scope': '历史人工SKU一致率；不是A/B/C、新订单准确率或实际生产验收',
           'replay': {'path': str(replay_path), 'sha256': sha(replay_path), 'scorer_sha256': replay.get('scorer_sha256'),
                      'seeds': replay['seeds'], 'verification_gate': replay.get('verification_gate')},
           'oracle': {'path': str(ORACLE), 'sha256': oracle_hash},
           'v5_sources': [{'path': str(p), 'sha256': sha(p)} for p in V5_REPORTS],
           'catalog': {'path': str(CATALOG), 'sha256': sha(CATALOG)},
           'fixed_denominators': {'events': 159, 'manual_aligned': 155, 'current_catalog_manual_sku': 132,
                                  'station_events': STATIONS, 'station_sku_denominators': EXPECTED_SKU_COUNTS},
           'reference_undefined_events': [r['event_id'] for r in universe.values() if r['sku_in_fixed_132'] and r['identity_undefined_reasons']],
           'groups': grouped,
           'limitations': ['人工表身份为历史参照，存在与原文约束冲突，不能当新订单真值或A/B/C。',
                           '未向映射器提供人工表或预期；不使用人工实配数量作为需求。',
                           '仅统计实际 product_status=matched 的选中SKU；未选择记为不一致。',
                           '源证据或参考未裁定时不输出确定比率，保留固定分母与保守数。']}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'output': str(output), 'replay_sha256': out['replay']['sha256'],
                      'reference_undefined': len(out['reference_undefined_events']),
                      'groups': {k: v['metrics']['strict132'] for k,v in grouped.items()}}, ensure_ascii=False))


class Tests(unittest.TestCase):
    def item(self):
        return {'event_id': 'x', 'station': 'songbei', 'manual_row': 2, 'manual_file': 'm.xlsx',
                'sku_in_fixed_132': True, 'expected_code': '1', 'catalog_record': {'name': '米', 'spec': '500g'},
                'identity_undefined_reasons': [], 'source_raw_text': '米2斤'}

    def row(self):
        return {'errors': [], 'actual': {'company_id': 'songbei', 'row': {'raw_text': '米2斤',
                'status': 'needs_review', 'product_status': 'matched', 'selected': {'code': '1', 'name': '米', 'spec': '500g'}}}}

    def test_missing_and_duplicate_preserve_outcomes(self):
        self.assertEqual(score_event(self.item(), [])['identity_outcome'], 'missing_output')
        self.assertEqual(score_event(self.item(), [self.row(), self.row()])['identity_outcome'], 'multiple_outputs')

    def test_matched_product_even_when_quantity_review_and_wrong_sku(self):
        row = self.row()
        self.assertEqual(score_event(self.item(), [row])['identity_outcome'], 'correct')
        row['actual']['row']['selected']['code'] = '2'
        self.assertEqual(score_event(self.item(), [row])['identity_outcome'], 'different_sku')

    def test_unresolved_and_source_mismatch_do_not_become_correct(self):
        row = self.row()
        row['actual']['row']['product_status'] = 'ambiguous'
        self.assertEqual(score_event(self.item(), [row])['identity_outcome'], 'unselected')
        row = self.row(); row['actual']['row']['raw_text'] = '米3斤'
        scored = score_event(self.item(), [row])
        self.assertEqual(scored['identity_outcome'], 'undefined_provenance')
        self.assertIsNone(metrics([scored], 159, 155, 132)['strict132']['rate_percent'])
        self.assertEqual(metrics([scored], 159, 155, 132)['strict132']['denominator'], 132)

    def test_manual_reference_mismatch_remains_undefined(self):
        item = self.item(); item['identity_undefined_reasons'] = ['manual_catalog_identity_mismatch']
        self.assertEqual(score_event(item, [self.row()])['identity_outcome'], 'undefined_reference')
        missing = score_event(item, [])
        self.assertEqual(missing['identity_outcome'], 'missing_output')
        self.assertIsNone(metrics([missing], 159, 155, 132)['strict132']['rate_percent'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
        if not result.wasSuccessful():
            raise SystemExit(1)
        return
    if args.replay is None:
        parser.error('--replay is required')
    replay = args.replay.resolve()
    if not replay.is_relative_to(EVAL.resolve()) or replay.name != 'replay_audit.json':
        parser.error('--replay must be an evaluation/v6 replay_audit.json')
    output = (args.output or replay.parent / 'identity_audit.json').resolve()
    if not output.is_relative_to(EVAL.resolve()):
        parser.error('--output must stay within evaluation/v6')
    run(replay, output)


if __name__ == '__main__':
    main()
