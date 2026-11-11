#!/usr/bin/env python3
"""Replay six ordinary requests, then compare to a separately frozen review.

The review file is fingerprinted before execution but is only opened after all
six subprocesses finish. It is never passed to the runtime or CLI requests.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone

from openpyxl import load_workbook

from v6_finance_audit import audit_result

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx'
FROZEN = ROOT / 'evaluation/v6/independent_anomaly_candidates.json'
KNOWLEDGE = ROOT / 'skills/order-mapping/knowledge/procurement.json'
CLI = ROOT / 'skills/order-mapping/scripts/order_mapping.py'
FINANCE_AUDITOR = ROOT / 'evaluation/v6_finance_audit.py'
DAY = '2026-11-03'

# These are ordinary user requests, not candidate codes or expected outcomes.
REQUESTS = [
    ('brand_broccoli', '臻园牌西蓝花2斤', '2', '斤', 'a', '甲站'),
    ('canned_broccoli', '西蓝花1罐', '1', '罐', 'a', '甲站'),
    ('coix', '薏仁米2袋', '2', '袋', 'a', '甲站'),
    ('black_sesame', '黑芝麻1盒', '1', '盒', 'a', '甲站'),
    ('white_sugar', '白糖2袋', '2', '袋', 'a', '甲站'),
    ('uninitialized_company_broccoli', '西蓝花2斤', '2', '斤', 'unassigned', '云溪驿站'),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def production_sha():
    files = [CLI] + sorted((CLI.parent / 'order_mapping_core').glob('*.py'))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(ROOT)).encode('utf-8') + b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def comments(sheet, row):
    return '\n'.join(cell.comment.text for cell in sheet[row] if cell.comment)


def check_case(expected, actual, quantity, unit, company, day, standard_notes, finance_notes):
    """Compare runtime candidates with the independently reviewed catalog set."""
    errors = []
    row = actual['row']
    expected_by_code = {str(c['material_code']): c for c in expected['candidates']}
    candidates = row.get('candidates') or []
    actual_by_code = {str(c.get('code')): c for c in candidates}
    missing = sorted(expected_by_code.keys() - actual_by_code.keys())
    unexpected = sorted(actual_by_code.keys() - expected_by_code.keys())
    if missing or unexpected or len(actual_by_code) != len(candidates):
        errors.append('candidate_set_mismatch')
    if row.get('quantity') != quantity or row.get('unit') != unit:
        errors.append('raw_quantity_or_unit')
    if actual.get('company_id') != company or actual.get('shipping_date') != day:
        errors.append('company_or_date')
    if row.get('status') == 'matched' or row.get('amount') is not None or row.get('finance_amount') is not None:
        errors.append('unexpected_automatic_amount')
    if expected['id'] == 'brand_broccoli' and row.get('selected') is not None:
        errors.append('unverified_brand_selected')
    if expected['id'] in {'brand_broccoli', 'canned_broccoli', 'coix', 'black_sesame', 'white_sugar'} and row.get('invoice_quantity') is not None:
        errors.append('unsupported_invoice_quantity')
    if expected['id'] == 'uninitialized_company_broccoli' and not any(
            issue.get('code') == 'company_boundary' for issue in row.get('issues', [])):
        errors.append('uninitialized_company_not_flagged')
    issue_text = ' '.join(str(x.get('message', '')) for x in row.get('issues', []))
    if expected['id'] == 'brand_broccoli' and '品牌' not in issue_text:
        errors.append('brand_reason_missing')
    if expected['id'] in {'canned_broccoli', 'coix', 'black_sesame', 'white_sugar'} and not re.search(
            r'包装|单位|净重|规格|计价|数量|装箱', issue_text):
        errors.append('quantity_packaging_reason_missing')
    for code in sorted(expected_by_code.keys() & actual_by_code.keys()):
        target, candidate = expected_by_code[code], actual_by_code[code]
        for actual_field, expected_field in (('name', 'name'), ('spec', 'spec'), ('price', 'listed_price'),
                                              ('sheet', 'sheet'), ('row', 'row')):
            if str(candidate.get(actual_field)) != str(target[expected_field]):
                errors.append(f'candidate_record_mismatch:{code}:{actual_field}')
        reason = str(candidate.get('match_reason') or '').strip()
        if not reason:
            errors.append(f'candidate_reason_missing:{code}')
        else:
            if reason not in standard_notes:
                errors.append('candidate_reason_missing_standard_comment')
            if reason not in finance_notes:
                errors.append('candidate_reason_missing_finance_comment')
            if expected['id'] == 'brand_broccoli' and not ('品牌' in reason and ('仅供' in reason or '参考' in reason)):
                errors.append('brand_reference_not_labelled')
            if expected['id'] in {'canned_broccoli', 'coix', 'black_sesame', 'white_sugar'} and not (
                    unit in reason and re.search(r'未|需|待|不能|不明|未知', reason)):
                errors.append(f'candidate_packaging_uncertainty_missing:{code}')
            if ((expected['id'] == 'black_sesame' and target['name'] == '熟芝麻') or
                    (expected['id'] == 'white_sugar' and target['name'] in ('韩国细幼砂糖', '砂糖'))) and not re.search(r'颜色|黑白|白色', reason):
                errors.append(f'candidate_color_uncertainty_missing:{code}')
    return {'errors': sorted(set(errors)), 'missing_candidates': missing, 'unexpected_candidates': unexpected,
            'actual_candidate_codes': sorted(actual_by_code), 'expected_candidate_codes': sorted(expected_by_code),
            'status': row.get('status'), 'quantity': row.get('quantity'), 'unit': row.get('unit'),
            'amount': row.get('amount'), 'finance_amount': row.get('finance_amount')}


def run(phase):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,60}', phase):
        raise ValueError('phase must contain only letters, numbers, underscore or hyphen')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    base = ROOT / 'evaluation/v6' / phase / run_id
    output = ROOT / 'outputs/v6' / phase / run_id
    base.mkdir(parents=True, exist_ok=False)
    before = {'catalog': sha(CATALOG), 'frozen_review': sha(FROZEN), 'knowledge': sha(KNOWLEDGE),
              'production_sources': production_sha(), 'finance_auditor': sha(FINANCE_AUDITOR), 'script': sha(__file__)}
    runtime = base / 'runtime'
    registration = subprocess.run([sys.executable, '-B', str(CLI), 'catalog-register', '--runtime', str(runtime),
                                   '--file', str(CATALOG)], text=True, capture_output=True)
    (base / 'catalog_register.log').write_text(registration.stdout + registration.stderr, encoding='utf-8')
    if registration.returncode:
        raise RuntimeError('catalog registration failed; inspect ' + str(base / 'catalog_register.log'))
    config = {'schema_version': 1, 'companies_confirmed': True,
              'companies': [{'company_id': 'a', 'full_name': '甲站', 'aliases': [], 'finance_name': '甲站'}],
              'numbering': {'prefix': 'OR_', 'confirmed': False}, 'output_policies': {}, 'finance': {}}
    completed = []
    for case_id, text, quantity, unit, company, heading in REQUESTS:
        chat = base / 'inputs' / f'{case_id}.txt'
        chat.parent.mkdir(parents=True, exist_ok=True)
        chat.write_text(f'【{heading}】\n{text}\n', encoding='utf-8')
        batch_id = 'anomaly_' + case_id
        request = {'schema_version': 1, 'batch_id': batch_id, 'chat_files': [str(chat)],
                   'config': config, 'shipping_date': DAY, 'matching_mode': 'shared', 'test_only': True}
        request_path = base / 'inputs' / f'{case_id}.json'
        write(request_path, request)
        result = subprocess.run([sys.executable, '-B', str(CLI), 'run-messages', '--runtime', str(runtime),
                                 '--batch', str(request_path), '--output', str(output)], text=True, capture_output=True)
        log = base / 'logs' / f'{case_id}.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.stdout + result.stderr, encoding='utf-8')
        completed.append({'id': case_id, 'quantity': quantity, 'unit': unit, 'company': company,
                          'chat': str(chat), 'chat_sha256': sha(chat), 'request': str(request_path),
                          'request_sha256': sha(request_path), 'returncode': result.returncode,
                          'result_path': str(output / batch_id / 'messages-result.json'), 'log': str(log)})

    # No review content enters the mapper: the file is first opened only after
    # every CLI subprocess above has exited, including failures.
    if sha(FROZEN) != before['frozen_review']:
        raise ValueError('frozen review changed during replay; refusing to score')
    frozen = json.loads(FROZEN.read_text(encoding='utf-8'))
    if frozen['catalog']['sha256'] != before['catalog']:
        raise ValueError('frozen candidate review refers to a different catalog')
    expected = {item['id']: item for item in frozen['cases']}
    if set(expected) != {item[0] for item in REQUESTS}:
        raise ValueError('frozen review has missing or unexpected cases')
    reports = []
    for run_info in completed:
        case_id = run_info['id']
        report = {'id': case_id, 'runtime': run_info, 'errors': []}
        if run_info['returncode']:
            report['errors'].append('cli_failed')
            reports.append(report)
            continue
        try:
            result = json.loads(Path(run_info['result_path']).read_text(encoding='utf-8'))
            report['result_sha256'] = sha(run_info['result_path'])
            if result.get('catalog', {}).get('sha256') != before['catalog']:
                report['errors'].append('runtime_catalog_mismatch')
            finance_audit = audit_result(result)
            report['finance_audit'] = finance_audit
            if finance_audit['errors']:
                report['errors'].append('finance_audit_failed')
            if result.get('event_count') != 1 or len(result.get('events', [])) != 1:
                report['errors'].append('unexpected_event_count')
                reports.append(report)
                continue
            event = result['events'][0]
            row = event['row']
            chat = Path(run_info['chat']).read_text(encoding='utf-8')
            if chat[row['char_start']:row['char_end']] != row['raw_text']:
                report['errors'].append('source_span_mismatch')
            if row['raw_text'] != chat.splitlines()[-1]:
                report['errors'].append('request_text_not_fully_preserved')
            manifest = json.loads(Path(event['manifest_path']).read_text(encoding='utf-8'))
            if manifest.get('knowledge_sha256') != before['knowledge']:
                report['errors'].append('runtime_knowledge_mismatch')
            standard = load_workbook(manifest['files']['standard']['path'], data_only=False).active
            finance = load_workbook(manifest['files']['finance']['path'], data_only=False)['商品明细']
            assessment = check_case(expected[case_id], event, run_info['quantity'], run_info['unit'],
                                    run_info['company'], DAY, comments(standard, event['excel_row']),
                                    comments(finance, event['excel_row']))
            errors = report['errors'] + assessment['errors']
            report.update(assessment)
            report['errors'] = sorted(set(errors))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            report['errors'].append('audit_exception')
            report['exception'] = repr(exc)
        reports.append(report)
    after = {'catalog': sha(CATALOG), 'frozen_review': sha(FROZEN), 'knowledge': sha(KNOWLEDGE),
             'production_sources': production_sha(), 'finance_auditor': sha(FINANCE_AUDITOR), 'script': sha(__file__)}
    integrity = before == after and all(sha(x['chat']) == x['chat_sha256'] and sha(x['request']) == x['request_sha256'] for x in completed)
    report = {'phase': phase, 'run_id': run_id, 'type': 'development_supplement_not_holdout_or_accuracy',
              'interpretation': 'KB6部分候选规则采用同一冻结目录审阅，属于已见目录与需求的回归，不是盲测、独立泛化或95%验收。',
              'frozen_review_loaded_after_all_cli': True, 'before_sha256': before, 'after_sha256': after,
              'integrity_passed': integrity, 'cases': reports,
              'all_checks_passed': integrity and len(reports) == 6 and all(not r['errors'] for r in reports)}
    report_path = base / 'report.json'
    write(report_path, report)
    print(json.dumps({'report': str(report_path), 'all_checks_passed': report['all_checks_passed'],
                      'integrity_passed': integrity, 'cases': [{'id': x['id'], 'errors': x['errors']} for x in reports]},
                     ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', default='anomaly_final')
    args = parser.parse_args()
    report = run(args.phase)
    return 0 if report['all_checks_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
