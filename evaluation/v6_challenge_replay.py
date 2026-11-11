#!/usr/bin/env python3
"""Run independently frozen synthetic chats through the unmodified public CLI.

Only request.json, TXT and catalog XLSX enter runtime. The expected event ledger
is read by this separate evaluator after execution, never sent to the mapper.
"""
from collections import Counter, defaultdict
from decimal import Decimal
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from v6_finance_audit import audit_result
from v6_replay_audit import config, original_hashes, MODES

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/order-mapping/scripts'))
from order_mapping_core.catalog import CatalogStore


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def rate(n, d):
    return round(100 * n / d, 4) if d else None


def summary(rows):
    total = sum(row['expected'] is not None for row in rows)
    auto = sum(row['automatic'] for row in rows)
    correct = sum(row['automatic_correct'] for row in rows)
    manual = sum(row['manual_correct'] for row in rows)
    control = sum(row['control_correct'] for row in rows)
    return {'input_events': total, 'automatic': auto, 'automatic_correct': correct,
            'manual_correct': manual, 'control_correct': control,
            'A': rate(correct, auto), 'B': rate(correct, total),
            'C': rate(correct + manual + control, total),
            'missing_outputs': sum('missing_output' in r['errors'] for r in rows),
            'unexpected_outputs': sum('unexpected_output' in r['errors'] for r in rows),
            'errors': dict(Counter(error for row in rows for error in row['errors'])),
            'candidate_completeness_unverified': sum(r.get('candidate_completeness') == 'unverified' for r in rows),
            'manual_checks_passed': sum(r.get('manual_checks_passed', False) for r in rows),
            'verified': not any(row['errors'] or row.get('candidate_completeness') == 'unverified' for row in rows)}


def is_normal_supported(row):
    expected = row.get('expected') or {}
    scenario = expected.get('scenario', row.get('scenario', ''))
    return (expected.get('outcome') == 'selected' and
            not scenario.startswith(('action_', 'duplicate_')))


def align_events(expected, actual):
    """Align within each source line by source order, never by product or SKU."""
    groups, outputs = defaultdict(list), defaultdict(list)
    for event in expected:
        groups[(Path(event['source_file']).name, event['source_line'])].append(event)
    for event in actual:
        item = event['row']
        outputs[(item['source_name'], item['source_line'])].append(event)
    pairs = []
    for key in sorted(groups.keys() | outputs.keys()):
        want = groups.get(key, [])
        got = sorted(outputs.get(key, []), key=lambda x: x['row']['char_start'])
        pairs.extend((want[i] if i < len(want) else None,
                      got[i] if i < len(got) else None)
                     for i in range(max(len(want), len(got))))
    return pairs


def _same_number(left, right):
    try:
        return left is not None and right is not None and Decimal(str(left)) == Decimal(str(right))
    except (ArithmeticError, ValueError):
        return False


def _issue_codes(item):
    return {i.get('code') for i in item.get('issues', []) if isinstance(i, dict)}


def check_state_assertions(rows, assertions):
    """Verify frozen progressive states and final chain against actual line links.

    Progressive state is checked against the observed event's
    quantity_after_event; final state is separately reconstructed from active
    rows.  Frozen expectations are never supplied to the production process.
    """
    results = []
    for assertion in assertions:
        members = [r for r in rows if r.get('expected') and all(
            r['expected'].get(key) == assertion[key]
            for key in ('company', 'date', 'catalog_version', 'requested_name'))]
        members.sort(key=lambda r: r['expected']['source_line'] if 'source_line' in r['expected'] else 0)
        issues, active, last, seen_ids = [], [], None, set()
        duplicate_count = 0
        for member in members:
            expected = member['expected']
            actual = member.get('actual')
            if not actual:
                issues.append('chain_missing_event')
                continue
            item = actual['row']
            line = item.get('line_id')
            if not line or line in seen_ids:
                issues.append('chain_invalid_line_id')
            seen_ids.add(line)
            action = expected['action']
            if action in ('append', 'duplicate'):
                if last is None or item.get('predecessor_line_id') != last:
                    issues.append('chain_wrong_predecessor')
            if action in ('replace_total', 'cancel'):
                prior_ids = [a['row'].get('line_id') for a in active]
                if not prior_ids or item.get('predecessor_line_id') != prior_ids[-1]:
                    issues.append('chain_wrong_predecessor')
                if item.get('predecessor_line_ids') != prior_ids:
                    issues.append('chain_predecessor_set')
                active = []
            if action == 'duplicate':
                duplicate_count += 1
            elif action != 'cancel':
                active.append(actual)
                last = line
            else:
                last = None
            progress = (expected.get('expected_state') or {}).get('quantity_500g')
            if progress is not None and not _same_number(item.get('quantity_after_event'), progress):
                issues.append('chain_progressive_quantity')
            quantities = [a['row'].get('quantity') for a in active]
            try:
                computed = sum((Decimal(str(x)) for x in quantities), Decimal(0))
            except (ArithmeticError, ValueError):
                computed = None
            if progress is not None and not _same_number(computed, progress):
                issues.append('chain_progressive_net')
        final_rows = [r['actual']['row'] for r in members if r.get('actual')
                      and r['actual']['row'].get('event_state') == 'active'
                      and r['expected']['action'] != 'duplicate']
        try:
            final_quantity = sum((Decimal(str(r['quantity'])) for r in final_rows), Decimal(0))
        except (ArithmeticError, ValueError, KeyError):
            final_quantity = None
        if not _same_number(final_quantity, assertion['final_quantity_500g']):
            issues.append('chain_final_quantity')
        if assertion.get('final_action') == 'cancelled' and final_rows:
            issues.append('chain_not_cancelled')
        if 'duplicate_events_retained' in assertion and duplicate_count != assertion['duplicate_events_retained']:
            issues.append('chain_duplicate_count')
        if not members:
            issues.append('chain_not_found')
        issues = sorted(set(issues))
        for member in members:
            if issues:
                member['errors'].extend(x for x in issues if x not in member['errors'])
                member['automatic_correct'] = member['manual_correct'] = member['control_correct'] = False
            elif member.get('control_needs_chain_check'):
                member['control_correct'] = not member['errors']
        results.append({'assertion': assertion, 'errors': issues, 'member_count': len(members),
                        'actual_final_quantity_500g': str(final_quantity) if final_quantity is not None else None})
    return results


def score(expected, actual, *, company, day, records, cancelled_chain=False):
    row = {'expected': expected, 'actual': actual, 'automatic': False,
           'automatic_correct': False, 'manual_correct': False, 'control_correct': False,
           'errors': []}
    errors = row['errors']
    if actual:
        item = actual['row']
        row['automatic'] = (item.get('status') == 'matched' and
                            item.get('event_state') == 'active' and
                            item.get('event') not in ('改量', '取消', '追加', '重复'))
    if expected is None:
        errors.append('unexpected_output')
        return row
    if actual is None:
        errors.append('missing_output')
        return row
    item = actual['row']
    selected = item.get('selected') or {}
    state = item.get('event_state')
    if actual['company_id'] != company:
        errors.append('company')
    if actual['shipping_date'] != day:
        errors.append('date')
    if 'quantity' in expected and item.get('quantity') != expected['quantity']:
        errors.append('quantity')
    if 'raw_unit' in expected and item.get('unit') != expected['raw_unit']:
        errors.append('unit')
    target = expected.get('selected') or {}
    if selected:
        current = records.get(str(selected.get('code')))
        if not current or any(str(selected.get(k)) != str(current.get(k)) for k in ('name', 'spec', 'price', 'sheet', 'row')):
            errors.append('current_catalog_record')
    is_control = cancelled_chain or expected['outcome'] in ('cancelled', 'duplicate')
    if is_control:
        if cancelled_chain:
            required_state = {'order': 'superseded', 'append': 'superseded',
                              'replace_total': 'inactive', 'cancel': 'cancellation_record'}[expected['action']]
            if state != required_state:
                errors.append('event_state')
        elif expected['outcome'] == 'duplicate' and state != 'duplicate_event':
            errors.append('event_state')
        expected_event = {'order': None, 'append': '追加', 'replace_total': '改量',
                          'cancel': '取消', 'duplicate': '重复'}[expected['action']]
        if item.get('event') != expected_event:
            errors.append('event_action')
        progressive = (expected.get('expected_state') or {}).get('quantity_500g')
        if progressive is not None and not _same_number(item.get('quantity_after_event'), progressive):
            errors.append('progressive_quantity')
        if expected['action'] not in ('cancel',) and target:
            for field, key in (('code', 'sku'), ('name', 'name'), ('spec', 'spec'), ('price', 'price')):
                if str(selected.get(field)) != str(target.get(key)):
                    errors.append(field)
        if item.get('amount') is not None or item.get('finance_amount') is not None:
            errors.append('control_money')
        if not item.get('predecessor_line_id') and expected['action'] != 'order':
            errors.append('control_relation')
        # A non-empty ID is not sufficient: check_state_assertions must prove
        # it points to the correct prior row and verify final net state.
        row['control_correct'] = False
        row['control_needs_chain_check'] = True
        return row
    if expected['outcome'] == 'selected':
        for field, key in (('code', 'sku'), ('name', 'name'), ('spec', 'spec'), ('price', 'price')):
            if str(selected.get(field)) != str(target.get(key)):
                errors.append(field)
        if not row['automatic']:
            errors.append('not_automatic')
        quantity = expected.get('quantity_500g')
        if not _same_number(item.get('invoice_quantity'), quantity):
            errors.append('invoice_quantity')
        if item.get('price_unit') != target.get('spec'):
            errors.append('price_unit')
        if quantity is not None:
            price = Decimal(target['price'])
            discount = Decimal('0.95')
            for field, value in (('discount', discount), ('unit_price', price * discount),
                                 ('amount', price * discount * Decimal(quantity)),
                                 ('finance_amount', price * discount * Decimal(quantity))):
                if not _same_number(item.get(field), value):
                    errors.append(field)
        row['automatic_correct'] = row['automatic'] and not errors
    else:
        if item.get('status') == 'matched':
            errors.append('manual_required')
        if target:
            if str(selected.get('code')) != target['sku']:
                errors.append('known_identity_lost')
        elif selected:
            errors.append('forced_sku')
        if item.get('amount') is not None or item.get('finance_amount') is not None:
            errors.append('unknown_money')
        if item.get('invoice_quantity') is not None:
            errors.append('unknown_invoice_quantity')
        if expected.get('anomaly') == 'unit_missing' and item.get('unit') is not None:
            errors.append('unit_should_be_unknown')
        if expected.get('anomaly') in ('quantity_missing', 'quantity_invalid') and item.get('quantity') is not None:
            errors.append('quantity_should_be_unknown')
        required = {
            'unit_missing': {'missing_unit'}, 'quantity_missing': {'missing_quantity'},
            'quantity_zero': {'invalid_quantity'}, 'quantity_negative': {'invalid_quantity'},
            'quantity_invalid': {'invalid_quantity'}, 'brand_unverified': {'brand_conflict', 'brand_unverified'},
            'part_unverified': {'attribute_conflict', 'part_unverified'},
            'packaging_unverified': {'attribute_conflict', 'different_specs', 'packaging_unverified'},
            'no_catalog_match': {'unmapped', 'knowledge_selector_missing'},
        }.get(expected.get('anomaly'), set())
        issue_codes = _issue_codes(item)
        issue_text = ' '.join(str(i.get('message', '')) for i in item.get('issues', []) if isinstance(i, dict))
        semantic_issue = ('semantic_review' in issue_codes and
                          expected.get('anomaly') == 'packaging_unverified' and
                          any(word in issue_text for word in ('袋', '包', '规格')) and
                          any(word in issue_text for word in ('差异', '缺', '不一致', '未知', '确认')))
        if required and not required.intersection(issue_codes) and not semantic_issue:
            errors.append('required_issue')
        expected_candidates = {str(r['sku']) for r in expected.get('candidates', [])}
        actual_candidates = {str(r['code']) for r in item.get('candidates', [])}
        if not expected_candidates.issubset(actual_candidates):
            errors.append('candidate_witness_missing')
        prohibited = set(map(str, expected.get('prohibited_skus', [])))
        if str(selected.get('code')) in prohibited:
            errors.append('prohibited_sku')
        # The frozen builder specifies abstention from these selections; it
        # explicitly permits nearby SKUs as candidates for human review.
        row['prohibited_skus_shown_as_candidates'] = sorted(actual_candidates & prohibited)
        if expected.get('anomaly') == 'no_catalog_match' and actual_candidates:
            errors.append('unexpected_candidates')
        for candidate in item.get('candidates', []):
            current = records.get(str(candidate.get('code')))
            if not current:
                errors.append('candidate_not_in_catalog')
            elif any(str(candidate.get(k)) != str(current.get(k)) for k in ('name', 'spec', 'price', 'sheet', 'row')):
                errors.append('candidate_catalog_record')
        if expected['outcome'] == 'unmatched' and item.get('status') != 'unmapped':
            errors.append('unmatched_status')
        row['candidate_completeness'] = ('verified' if expected.get('candidate_set_status') == 'reviewed_complete'
                                         else 'unverified')
        if expected.get('candidate_set_status') == 'reviewed_complete' and actual_candidates != expected_candidates:
            errors.append('candidate_set_mismatch')
        row['manual_checks_passed'] = not errors
        row['manual_correct'] = row['manual_checks_passed'] and row['candidate_completeness'] == 'verified'
    return row


def verify_frozen(source):
    """Reject inconsistent frozen files even when Python assertions are disabled."""
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    freeze = json.loads((source / 'FREEZE.json').read_text(encoding='utf-8'))
    frozen_hashes = freeze['sha256']
    declared = set(manifest['inputs_sha256'])
    actual_inputs = {str(p.relative_to(source)) for p in (source / 'input').rglob('*') if p.is_file()}
    if declared != actual_inputs or set(frozen_hashes) != declared | {'builder', 'manifest.json', 'expected.json'}:
        raise ValueError('frozen input inventory mismatch')
    if any(not name.startswith('input/') or '..' in Path(name).parts for name in declared):
        raise ValueError('invalid frozen input path')
    required = {'manifest.json': sha(source / 'manifest.json'),
                'expected.json': sha(source / 'expected.json')}
    required.update({name: sha(source / name) for name in manifest['inputs_sha256']})
    for name, fingerprint in required.items():
        if frozen_hashes.get(name) != fingerprint:
            raise ValueError(f'frozen hash mismatch: {name}')
    if manifest['expected_sha256'] != required['expected.json']:
        raise ValueError('manifest expected hash mismatch')
    for name, fingerprint in manifest['inputs_sha256'].items():
        if required[name] != fingerprint:
            raise ValueError(f'manifest input hash mismatch: {name}')
    builder_actual = sha(ROOT / 'evaluation/v6_challenge_builder.py')
    return {'expected_sha256': required['expected.json'], 'freeze_sha256': sha(source / 'FREEZE.json'),
            'input_hashes': manifest['inputs_sha256'],
            'builder_sha256_at_freeze': frozen_hashes['builder'], 'builder_sha256_current': builder_actual,
            'builder_matches_freeze': frozen_hashes['builder'] == builder_actual}


def score_batch(expected, wanted, result, *, company_ids, records, seed, mode, case, request, path):
    batch_rows = []
    assertions = [state for state in expected.get('state_assertions', []) if any(
        all(e.get(key) == state[key] for key in ('company', 'date', 'catalog_version', 'requested_name'))
        for e in wanted)]
    for event, actual in align_events(wanted, result['events']):
        cancelled = bool(event and any(state.get('final_action') == 'cancelled' and all(
            state[key] == event[key] for key in ('company', 'date', 'catalog_version', 'requested_name'))
            for state in assertions))
        scored = score(event, actual, company=company_ids[event['company']] if event else '',
                       day=event['date'] if event else '', records=records, cancelled_chain=cancelled)
        if actual:
            item = actual['row']
            text_path = path.parent / item['source_name']
            if not text_path.is_file():
                scored['errors'].append('source_trace')
            else:
                text = text_path.read_text(encoding='utf-8-sig')
                if text[item['char_start']:item['char_end']] != item['raw_text']:
                    scored['errors'].append('source_trace')
            if scored['errors']:
                scored['automatic_correct'] = scored['manual_correct'] = scored['control_correct'] = False
                if 'manual_checks_passed' in scored:
                    scored['manual_checks_passed'] = False
        scored.update(seed=seed, mode=mode, batch=case,
                      company=event['company'] if event else request['company_name'],
                      scenario=event['scenario'] if event else 'unexpected_output')
        batch_rows.append(scored)
    chain_audit = check_state_assertions(batch_rows, assertions)
    return batch_rows, chain_audit


def saved_result(old_run, normal, catalog_sha):
    """Read a previous result and bind its metadata to the current frozen inputs."""
    if not old_run:
        raise ValueError('missing previous run')
    request_path = Path(old_run['request'])
    if json.loads(request_path.read_text(encoding='utf-8')) != normal:
        raise ValueError('previous request differs from frozen request')
    result_path = Path(old_run['result_path'])
    result = json.loads(result_path.read_text(encoding='utf-8'))
    if result.get('batch_id') != normal['batch_id'] or result.get('matching_mode') != normal['matching_mode']:
        raise ValueError('previous result batch or mode mismatch')
    if result.get('catalog', {}).get('sha256') != catalog_sha:
        raise ValueError('previous result catalog mismatch')
    sources = {s['path']: s['sha256'] for s in result.get('sources', [])}
    if sources != {name: sha(name) for name in normal['chat_files']}:
        raise ValueError('previous result source mismatch')
    return result, {'source_result_sha256_now': sha(result_path),
                    'source_request_sha256_now': sha(request_path),
                    'historical_result_hash_authenticated': False}


def run(seeds, phase, modes, rescore_from=None):
    scorer_sha_before = sha(Path(__file__))
    base = ROOT / 'evaluation/v6' / phase
    if base.exists():
        raise ValueError('phase exists; preserve old evidence and select a new phase')
    base.mkdir(parents=True)
    before = original_hashes()
    write(base / 'original_sha_before.json', before)
    rows, runs, errors = [], [], []
    frozen = {}
    old_runs = {}
    if rescore_from:
        old_path = ROOT / 'evaluation/v6' / rescore_from / 'replay_audit.json'
        old = json.loads(old_path.read_text(encoding='utf-8'))
        for old_run in old['runs']:
            key = (old_run['seed'], old_run['mode'], old_run['batch'])
            if key in old_runs:
                raise ValueError(f'duplicate previous run: {key}')
            old_runs[key] = old_run
    for seed in seeds:
        source = ROOT / 'evaluation/v6/challenge' / str(seed)
        # Hash proof is checked before the process reads any raw chats.
        frozen[str(seed)] = verify_frozen(source)
        requests = sorted((source / 'input').rglob('request.json'))
        # Company initialization is derived from ordinary request inputs only.
        ordinary = [(path, json.loads(path.read_text())) for path in requests]
        names = sorted({request['company_name'] for path, request in ordinary})
        labels = {f'c{i}': name for i, name in enumerate(names, 1)}
        company_ids = {name: identifier for identifier, name in labels.items()}
        for mode in modes:
            for index, (path, request) in enumerate(ordinary):
                case = f's{seed}-b{index + 1}'
                # Reuse this company's store so A -> B is a real activation.
                runtime = base / 'runtime' / str(seed) / mode / company_ids[request['company_name']]
                store = CatalogStore(runtime)
                store.register(path.parent / request['catalog_file'])
                records = {str(r['code']): r for r in store.current()['records']}
                normal = {'schema_version': 1, 'batch_id': case, 'shipping_date': request['batch_date'],
                          'company': request['company_name'], 'config': config(labels),
                          'chat_files': [str(path.parent / name) for name in request['chat_files']],
                          'test_only': True, 'matching_mode': mode}
                input_path = base / 'requests' / str(seed) / mode / f'{case}.json'
                write(input_path, normal)
                output = ROOT / 'outputs/v6' / phase / str(seed) / mode
                result_path = output / case / 'messages-result.json'
                saved_evidence = None
                if rescore_from:
                    old_run = old_runs.get((seed, mode, case))
                    try:
                        result, saved_evidence = saved_result(old_run, normal, sha(path.parent / request['catalog_file']))
                        result_path = Path(old_run['result_path'])
                        audit = {'errors': list(old_run['audit']['errors']),
                                 'provenance': 'historical audit, not re-executed',
                                 'historical_audit': old_run['audit']}
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        errors.append({'seed': seed, 'mode': mode, 'batch': case, 'error': str(exc)})
                        result = {'events': []}
                        audit = {'errors': ['saved_result_error']}
                else:
                    proc = subprocess.run([sys.executable, '-B', str(ROOT / 'skills/order-mapping/scripts/order_mapping.py'),
                                           'run-messages', '--runtime', str(runtime), '--batch', str(input_path),
                                           '--output', str(output)], text=True, capture_output=True)
                    log = base / 'logs' / str(seed) / mode / f'{case}.log'
                    log.parent.mkdir(parents=True, exist_ok=True)
                    log.write_text(proc.stdout + proc.stderr)
                    if proc.returncode:
                        errors.append({'seed': seed, 'mode': mode, 'batch': case, 'error': proc.stderr[-2000:]})
                        result = {'events': []}
                        audit = {'errors': ['execution_error']}
                    else:
                        result = json.loads(result_path.read_text())
                        audit = audit_result(result)
                # Expected business answers are first loaded after execution.
                expected = json.loads((source / 'expected.json').read_text())
                source_names = {str((path.parent / name).relative_to(source)) for name in request['chat_files']}
                wanted = [e for e in expected['events'] if e['source_file'] in source_names]
                batch_rows, chain_audit = score_batch(expected, wanted, result, company_ids=company_ids,
                                                      records=records, seed=seed, mode=mode, case=case,
                                                      request=request, path=path)
                rows.extend(batch_rows)
                runs.append({'seed': seed, 'mode': mode, 'batch': case, 'request': str(input_path),
                             'result_path': str(result_path), 'expected_events': len(wanted),
                             'actual_events': len(result['events']), 'audit': audit, 'chain_audit': chain_audit,
                             'saved_evidence': saved_evidence})
    after = original_hashes()
    write(base / 'original_sha_after.json', after)
    metrics = {mode: summary([r for r in rows if r['mode'] == mode]) for mode in modes}
    companies = {mode: {company: summary([r for r in rows if r['mode'] == mode and r['company'] == company])
                        for company in sorted({r['company'] for r in rows})} for mode in modes}
    scenarios = {mode: {scenario: summary([r for r in rows if r['mode'] == mode and r['scenario'] == scenario])
                        for scenario in sorted({r['scenario'] for r in rows})} for mode in modes}
    normal_metrics = {mode: summary([r for r in rows if r['mode'] == mode and is_normal_supported(r)])
                      for mode in modes}
    audit_errors = sum(len(run['audit']['errors']) for run in runs)
    result = {'schema_version': 1, 'phase': phase, 'seeds': seeds, 'modes': list(modes),
              'rescore_from': rescore_from,
              'rescore_source_audit_sha256_now': sha(old_path) if rescore_from else None,
              'scorer_sha256_before': scorer_sha_before, 'scorer_sha256_after': sha(Path(__file__)),
              'verification_scope': 'deterministic raw-message CLI, independently frozen synthetic business answers; not host Agent semantics',
              'frozen': frozen, 'source_sha_unchanged': before == after, 'execution_errors': errors,
              'metrics': metrics, 'normal_supported_metrics': normal_metrics,
              'by_company': companies, 'by_scenario': scenarios, 'audit_error_count': audit_errors,
              'runs': runs, 'rows': rows, 'status': 'doing',
              'source_code_sha256': {str(p.relative_to(ROOT)): sha(p)
                                     for p in (ROOT / 'skills/order-mapping').rglob('*')
                                     if p.is_file() and p.suffix in ('.py', '.json', '.md')
                                     and '__pycache__' not in p.parts}}
    write(base / 'replay_audit.json', result)
    print(json.dumps({key: result[key] for key in ('phase', 'seeds', 'metrics', 'normal_supported_metrics',
                                                  'execution_errors', 'audit_error_count', 'source_sha_unchanged')},
                     ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seeds', type=int, nargs='+', required=True)
    parser.add_argument('--phase', required=True)
    parser.add_argument('--modes', nargs='+', choices=MODES, default=MODES)
    parser.add_argument('--rescore-from', help='Read saved result JSON from an existing phase; do not rerun the mapper')
    args = parser.parse_args()
    run(args.seeds, args.phase, args.modes, args.rescore_from)
