#!/usr/bin/env python3
"""Run frozen synthetic KB7 requests via CLI; load expected values only afterward."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v6_finance_audit import audit_result

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'evaluation/v6/kb7_challenge'
FREEZE = DATA / 'FREEZE.json'
CLI = ROOT / 'skills/order-mapping/scripts/order_mapping.py'
KNOWLEDGE = ROOT / 'skills/order-mapping/knowledge/procurement.json'
FINANCE = ROOT / 'evaluation/v6_finance_audit.py'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_sha():
    digest = hashlib.sha256()
    for path in [CLI] + sorted((CLI.parent / 'order_mapping_core').glob('*.py')):
        digest.update(str(path.relative_to(ROOT)).encode('utf-8') + b'\0' + path.read_bytes())
    return digest.hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def numeric_equal(left, right):
    try:
        return left is not None and right is not None and Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError, TypeError):
        return False


def score(expected, actual):
    errors = []
    row = actual['row']
    selected = row.get('selected') or {}
    code = str(selected.get('code') or '')
    action = expected['expected_action']
    if row.get('quantity') != expected['quantity'] or row.get('unit') != expected['unit']:
        errors.append('raw_quantity_unit')
    if actual.get('company_id') != expected['company_id'] or actual.get('shipping_date') != expected['shipping_date']:
        errors.append('company_date')
    if str(code) in expected.get('prohibited_selected', []):
        errors.append('prohibited_selected')
    if action == 'auto':
        record = expected['catalog_record']
        if row.get('status') != 'matched':
            errors.append('expected_automatic_match')
        if code != expected['selected_code']:
            errors.append('selected_code')
        for key in ('name', 'spec', 'price'):
            if str(selected.get(key)) != str(record[key]):
                errors.append('catalog_' + key)
        if not numeric_equal(row.get('invoice_quantity'), expected['quantity']):
            errors.append('invoice_quantity')
        expected_unit = {'斤': '500g', '瓶': '瓶', '罐': '罐'}[expected['unit']]
        if row.get('price_unit') != expected_unit:
            errors.append('price_unit')
        expected_amount = Decimal(record['price']) * Decimal('0.95') * Decimal(expected['quantity'])
        if not numeric_equal(row.get('amount'), expected_amount):
            errors.append('amount')
        if not numeric_equal(row.get('finance_amount'), expected_amount):
            errors.append('finance_amount')
        # Ordinary candidates with explicit prohibited variants should not be
        # silently included as if equivalent in the automatic price pool.
        candidate_codes = {str(c.get('code')) for c in row.get('candidates', [])}
        if candidate_codes.intersection(expected.get('prohibited_selected', [])):
            errors.append('prohibited_automatic_candidate')
    elif action in ('manual', 'unassigned'):
        if row.get('status') == 'matched' or row.get('amount') is not None or row.get('finance_amount') is not None:
            errors.append('manual_required')
        if action == 'unassigned' and not any(i.get('code') == 'company_boundary' for i in row.get('issues', [])):
            errors.append('company_boundary_missing')
    elif action == 'restricted':
        if code and code != expected['selected_code']:
            errors.append('restricted_selected')
        if row.get('status') == 'matched' and code != expected['selected_code']:
            errors.append('restricted_automatic')
        if row.get('status') != 'matched' and (row.get('amount') is not None or row.get('finance_amount') is not None):
            errors.append('restricted_unresolved_amount')
    else:
        raise ValueError('unrecognized expected action')
    return {'errors': errors, 'action': action, 'status': row.get('status'), 'selected_code': code or None,
            'amount': row.get('amount'), 'finance_amount': row.get('finance_amount')}


def snapshot(freeze):
    values = {'freeze': sha(FREEZE), 'original_catalog': sha(ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx'),
              'knowledge': sha(KNOWLEDGE), 'sources': source_sha(), 'finance_auditor': sha(FINANCE),
              'runner': sha(__file__)}
    for seed, meta in freeze['seeds'].items():
        manifest_path, expected_path = DATA / seed / 'manifest.json', DATA / seed / 'expected.json'
        values[f'{seed}/manifest'] = sha(manifest_path)
        values[f'{seed}/expected'] = sha(expected_path)
        if values[f'{seed}/manifest'] != meta['manifest_sha256'] or values[f'{seed}/expected'] != meta['expected_sha256']:
            raise ValueError('frozen manifest or expectation fingerprint mismatch: ' + seed)
        # Manifest has only ordinary request paths and hashes; expected content
        # is not opened until every production run exits.
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        values[f'{seed}/catalog'] = sha(DATA / manifest['catalog_file'])
        if values[f'{seed}/catalog'] != meta['catalog_sha256']:
            raise ValueError('catalog fingerprint mismatch: ' + seed)
        for entry in manifest['requests']:
            for kind in ('request', 'chat'):
                key = entry[kind + '_file']
                values[key] = sha(DATA / key)
                if values[key] != entry[kind + '_sha256']:
                    raise ValueError('input fingerprint mismatch: ' + key)
    if values['original_catalog'] != freeze['source_catalog_sha256']:
        raise ValueError('original catalog changed')
    return values


def run(phase):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', phase):
        raise ValueError('phase must contain only letters, numbers, underscore or hyphen')
    freeze = json.loads(FREEZE.read_text(encoding='utf-8'))
    before = snapshot(freeze)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    out = DATA / 'runs' / phase / run_id
    out.mkdir(parents=True, exist_ok=False)
    completed = []
    for seed in freeze['seeds']:
        manifest = json.loads((DATA / seed / 'manifest.json').read_text(encoding='utf-8'))
        runtime = out / seed / 'runtime'
        registration = subprocess.run([sys.executable, '-B', str(CLI), 'catalog-register', '--runtime', str(runtime),
                                       '--file', str(DATA / manifest['catalog_file'])], capture_output=True, text=True)
        log = out / seed / 'catalog.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(registration.stdout + registration.stderr, encoding='utf-8')
        for entry in manifest['requests']:
            req_path = DATA / entry['request_file']
            request = json.loads(req_path.read_text(encoding='utf-8'))
            # Only ordinary request.json, chat and current catalog enter CLI.
            case_dir = req_path.parent.name
            output = out / seed / case_dir / 'outputs'
            if registration.returncode:
                completed.append({'seed': seed, 'case': case_dir, 'error': 'catalog_register_failed', 'log': str(log)})
                continue
            result = subprocess.run([sys.executable, '-B', str(CLI), 'run-messages', '--runtime', str(runtime),
                                     '--batch', str(req_path), '--output', str(output)], capture_output=True, text=True)
            run_log = out / seed / case_dir / 'cli.log'
            run_log.parent.mkdir(parents=True, exist_ok=True)
            run_log.write_text(result.stdout + result.stderr, encoding='utf-8')
            completed.append({'seed': seed, 'case': case_dir, 'returncode': result.returncode, 'log': str(run_log),
                              'result': str(output / request['batch_id'] / 'messages-result.json')})
        print(f'{seed}: {len(manifest["requests"])} CLI runs completed', flush=True)

    # All production CLI runs have exited before this first read of expected.json.
    if snapshot(freeze) != before:
        raise ValueError('inputs or production changed during execution; results are preserved but not scored')
    reports = []
    for item in completed:
        expected_data = json.loads((DATA / item['seed'] / 'expected.json').read_text(encoding='utf-8'))
        expected = next(x for x in expected_data['cases'] if x['case'] == item['case'])
        report = {'seed': item['seed'], 'case': item['case'], 'label': expected['label'], 'runtime': item,
                  'expected_action': expected['expected_action'], 'errors': []}
        if item.get('error') or item.get('returncode'):
            report['errors'].append(item.get('error') or 'cli_failed')
            reports.append(report)
            continue
        try:
            result = json.loads(Path(item['result']).read_text(encoding='utf-8'))
            report['result_sha256'] = sha(item['result'])
            audit = audit_result(result)
            report['finance_audit'] = audit
            if audit['errors']:
                report['errors'].append('finance_audit_failed')
            if result.get('catalog', {}).get('sha256') != before[item['seed'] + '/catalog']:
                report['errors'].append('runtime_catalog_mismatch')
            if result.get('event_count') != 1 or len(result.get('events', [])) != 1:
                report['errors'].append('unexpected_event_count')
            else:
                event = result['events'][0]
                if event['row'].get('raw_text') != expected['input_text']:
                    report['errors'].append('raw_text')
                scored = score(expected, event)
                prior_errors = report['errors'] + scored['errors']
                report.update(scored)
                report['errors'] = sorted(set(prior_errors))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            report['errors'].append('audit_exception')
            report['exception'] = repr(exc)
        reports.append(report)
    after = snapshot(freeze)
    integrity = before == after
    summary = {'phase': phase, 'run_id': run_id, 'freeze_sha256': before['freeze'],
               'interpretation': freeze['limits'], 'freeze_context': freeze['context'],
               'expected_loaded_after_all_cli': True, 'integrity_passed': integrity,
               'before_sha256': before, 'after_sha256': after, 'cases': reports,
               'all_checks_passed': integrity and len(reports) == sum(x['case_count'] for x in freeze['seeds'].values())
                                    and all(not x['errors'] for x in reports)}
    path = out / 'report.json'
    write(path, summary)
    print(json.dumps({'report': str(path), 'integrity_passed': integrity, 'case_count': len(reports),
                      'passed': sum(not x['errors'] for x in reports), 'failed': sum(bool(x['errors']) for x in reports),
                      'all_checks_passed': summary['all_checks_passed']}, ensure_ascii=False, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', default='kb7_first')
    args = parser.parse_args()
    return 0 if run(args.phase)['all_checks_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
