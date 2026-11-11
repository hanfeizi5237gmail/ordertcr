#!/usr/bin/env python3
"""Rebuild the three raw source orders with before/after implementation hashes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from v6_finance_audit import audit_result

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'skills/order-mapping/scripts/order_mapping.py'
STATIONS = {'songbei': ('松北大道', 53), 'xingyue': ('星月', 42), 'xueyuan': ('学院路', 64)}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def fingerprints():
    paths = [p for folder in ('original', 'skills/order-mapping')
             for p in (ROOT / folder).rglob('*')
             if p.is_file() and '__pycache__' not in p.parts]
    paths += [Path(__file__), ROOT / 'evaluation/v6_finance_audit.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.phase):
        parser.error('invalid phase')
    evidence = ROOT / 'evaluation/v7' / args.phase
    outputs = ROOT / 'outputs/v7' / args.phase
    if evidence.exists() or outputs.exists():
        parser.error('phase already exists; refusing overwrite')
    evidence.mkdir(parents=True)
    outputs.mkdir(parents=True)
    before = fingerprints()
    write(evidence / 'source_before.json', before)
    report = {'scope': 'raw original demand drafts; not settlement or independent accuracy',
              'status': 'started', 'stations': {}}
    write(evidence / 'report.json', report)

    def execute(command, name):
        process = subprocess.run([sys.executable, '-B', str(CLI), *command],
                                 capture_output=True, text=True, cwd=ROOT)
        (evidence / (name + '.log')).write_text(process.stdout + '\nstderr:\n' + process.stderr)
        if process.returncode:
            raise RuntimeError(f'{name}: CLI failed with {process.returncode}')
        if fingerprints() != before:
            raise RuntimeError(f'{name}: implementation or original drifted')

    runtime = evidence / 'runtime'
    execute(['catalog-register', '--runtime', str(runtime), '--file',
             str(ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx')], 'catalog')
    for company, (station, expected_count) in STATIONS.items():
        request = json.loads((ROOT / 'evaluation/v7/original_delivery/requests' / (company + '.json')).read_text())
        request['batch_id'] = f'{args.phase}-{company}'
        batch = evidence / 'requests' / (company + '.json')
        write(batch, request)
        execute(['run-messages', '--runtime', str(runtime), '--batch', str(batch),
                 '--output', str(outputs / company)], company)
        result_path = outputs / company / request['batch_id'] / 'messages-result.json'
        result = json.loads(result_path.read_text())
        finance = audit_result(result)
        write(evidence / (company + '_finance.json'), finance)
        if result['event_count'] != expected_count or len(result['events']) != expected_count or finance['errors']:
            raise RuntimeError(f'{company}: event count or finance audit failed')
        report['stations'][company] = {'station': station, 'event_count': result['event_count'],
            'statuses': dict(Counter(event['row']['status'] for event in result['events'])),
            'order_groups': len(result['manifests']), 'finance_errors': len(finance['errors']),
            'result': str(result_path.relative_to(ROOT))}
        write(evidence / 'report.json', report)
    after = fingerprints()
    write(evidence / 'source_after.json', after)
    report.update(status='completed', source_equal=before == after, total_events=159)
    write(evidence / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
