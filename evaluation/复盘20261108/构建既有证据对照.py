#!/usr/bin/env python3
"""只读取已保存证据，构建复盘对照；不运行映射器、不修改预期。"""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def read(relative):
    return json.loads((ROOT / relative).read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    old_path = 'evaluation/v5/replay_audit_20261108.json'
    new_path = 'evaluation/v7/kb19_original_r2/fixed_identity.json'
    old_items = read(old_path)['scenarios']['single_station_random_split']['lanes']['profile_only']['items']
    old = {x['source_event_id']: x for x in old_items}
    current = read(new_path)['assessment']['events']
    assert len(old) == len(current) == 159
    assert set(old) == {x['event_id'] for x in current}
    rows = {}
    for station in read('evaluation/v7/kb19_original_r2/report.json')['stations'].values():
        for event in read(station['result'])['events']:
            rows[event['row']['line_id']] = event['row']
    assert set(rows) == set(old)
    records_path = next((ROOT / 'evaluation/v7/kb19_original_r2/runtime/catalogs').glob('*/records.json'))
    records = {x['code']: x for x in json.loads(records_path.read_text())['records']}
    transition = Counter()
    detail = []
    for event in current:
        key = event['event_id']
        before, after = old[key], rows[key]
        old_outcome = ('correct' if before['manual_identity_correct'] else
                       'unselected' if before['selected_code'] is None else 'different_sku')
        if event['sku_in_fixed_132']:
            transition[(old_outcome, event['identity_outcome'])] += 1
        detail.append({
            'canonical_event_id': key, 'station': event['station'],
            'raw_text': event['source_raw_text'], 'in_fixed_132': event['sku_in_fixed_132'],
            'manual_code': event['expected_code'], 'manual_name': event['manual_name'],
            'manual_spec': event['manual_spec'], 'v5_code': before['selected_code'],
            'v5_name': (records.get(before['selected_code']) or {}).get('name'),
            'v5_status': before['status'], 'v5_product_status': before['product_status'],
            'v5_quantity_status': before['quantity_status'], 'v5_invoice_quantity': before['invoice_quantity'],
            'v5_historical_identity_outcome': old_outcome,
            'kb19_code': event['actual_code'], 'kb19_name': (after.get('selected') or {}).get('name'),
            'kb19_status': after['status'], 'kb19_product_status': after['product_status'],
            'kb19_quantity_status': after['quantity_status'], 'kb19_invoice_quantity': after.get('invoice_quantity'),
            'kb19_historical_identity_outcome': event['identity_outcome'],
            'source_quantity': after.get('quantity'), 'source_unit': after.get('unit'),
            'kb19_issue_codes': '|'.join(i['code'] for i in after['issues']),
            'kb19_candidates': json.dumps(after.get('candidates'), ensure_ascii=False),
            'semantic_adjudication': 'not_established_by_historical_sku_agreement',
        })
    with (OUT / '159事件版本逐项对照.csv').open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(detail[0]))
        writer.writeheader()
        writer.writerows(detail)
    versions = []
    paths = ['v6/historical_rc1/identity_audit.json', 'v6/historical_rc3/identity_audit.json',
             'v6/historical_rc8/identity_audit.json', 'v7/fixed_identity_rc2.json',
             'v7/fixed_identity_kb15.json', 'v7/kb16_final_history_identity.json',
             'v7/kb17_final_history_identity.json', 'v7/kb18_final_history_identity.json']
    for relative in paths:
        source = read('evaluation/' + relative)
        key = next(k for k in source['groups'] if k.startswith('6108/single_station_split/') and k.endswith('/shared'))
        group = source['groups'][key]
        versions.append({'path': 'evaluation/' + relative,
                         'sha256': digest(ROOT / 'evaluation' / relative),
                         'catalog_sha256': source['catalog']['sha256'],
                         'v5_reference_shas': [v['sha256'] for v in source['v5_sources']],
                         'fixed_denominators': source['fixed_denominators'],
                         'outcomes': group['metrics']['fixed_sku_outcomes'],
                         'per_station': {s: v['strict132'] for s, v in group['per_station'].items()}})
    old_history = read('evaluation/v7/kb17_final_history/replay_audit.json')
    new_history = read('evaluation/v7/kb19_final_history/replay_audit.json')
    assert old_history['oracle_sha256'] == new_history['oracle_sha256']
    assert len(old_history['rows']) == len(new_history['rows'])
    added_quantity_diagnostics = []
    for before, after in zip(old_history['rows'], new_history['rows']):
        assert (before['mode'], before['case_id'], before.get('source_event_id')) == (
            after['mode'], after['case_id'], after.get('source_event_id'))
        if 'invoice_quantity' not in before['errors'] and 'invoice_quantity' in after['errors']:
            previous, actual = before['actual']['row'], after['actual']['row']
            added_quantity_diagnostics.append({
                'mode': after['mode'], 'case_id': after['case_id'], 'scenario': after['scenario'],
                'raw_text': actual['raw_text'], 'quantity_before': previous.get('quantity'),
                'quantity_after': actual.get('quantity'), 'unit_before': previous.get('unit'),
                'unit_after': actual.get('unit'), 'invoice_before': previous.get('invoice_quantity'),
                'invoice_after': actual.get('invoice_quantity'), 'state_before': previous.get('event_state'),
                'state_after': actual.get('event_state'), 'amount_before': previous.get('amount'),
                'amount_after': actual.get('amount'), 'issue_codes': [i['code'] for i in actual['issues']],
            })
    summary = {
        'scope': 'saved_evidence_read_only_retrospective_no_new_mapping_execution',
        'status': 'paused_awaiting_user_review', 'events': 159, 'aligned_events': 155, 'fixed_sku_events': 132,
        'identity_metric_boundary': 'same historical manual SKU, not independently adjudicated current-demand accuracy',
        'versions': versions,
        'v5_to_kb19_transition132': [{'v5': a, 'kb19': b, 'events': n} for (a, b), n in transition.items()],
        'v5_full159': {'selected': sum(x['selected_code'] is not None for x in old.values()),
                       'status': dict(Counter(x['status'] for x in old.values())),
                       'quantity_status': dict(Counter(x['quantity_status'] for x in old.values()))},
        'kb19_full159': {'selected': sum(bool(x.get('selected')) for x in rows.values()),
                         'selected_quantity_pending': sum(bool(x.get('selected')) and x['quantity_status'] != 'matched' for x in rows.values()),
                         'status': dict(Counter(x['status'] for x in rows.values())),
                         'quantity_status': dict(Counter(x['quantity_status'] for x in rows.values()))},
        'same_oracle_history': {'oracle_sha256': old_history['oracle_sha256'],
                               'kb17_metrics': old_history['metrics'], 'kb19_metrics': new_history['metrics'],
                               'official_C': None, 'C_boundary': 'legacy score is finite old-oracle diagnostic, not exhaustive business accuracy',
                               'added_invoice_diagnostics_by_mode': dict(Counter(x['mode'] for x in added_quantity_diagnostics)),
                               'added_invoice_diagnostic_details': added_quantity_diagnostics},
        'sources': {relative: digest(ROOT / relative) for relative in [old_path, new_path]},
    }
    (OUT / '既有证据对照.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'events': len(detail), 'transition132': summary['v5_to_kb19_transition132'],
                      'added_invoice_diagnostics_by_mode': summary['same_oracle_history']['added_invoice_diagnostics_by_mode']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
