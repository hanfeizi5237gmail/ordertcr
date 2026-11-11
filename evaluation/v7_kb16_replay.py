#!/usr/bin/env python3
"""Run frozen semantic inputs in three modes, then audit only after all runs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'evaluation/v7/kb16_challenge_v1'
CLI = ROOT / 'skills/order-mapping/scripts/order_mapping.py'
KB = ROOT / 'skills/order-mapping/knowledge/procurement.json'
SEEDS = (8801, 8802, 8891)
MODES = ('catalog_only', 'shared', 'shared_with_company_contract')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def config(ordinary):
    companies = ordinary['companies']
    if not companies or any(set(c) != {'company_name', 'company_alias'} for c in companies):
        raise ValueError('invalid ordinary company input')
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


def fingerprint():
    original = sorted(p for p in (ROOT / 'original').iterdir() if p.is_file())
    if len(original) != 8:
        raise ValueError(f'expected eight original files, found {len(original)}')
    paths = original + [p for p in (ROOT / 'skills/order-mapping').rglob('*')
                        if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py', '.json', '.md')]
    paths += [p for p in FIX.rglob('*') if p.is_file()]
    paths += [ROOT / 'evaluation' / n for n in ('v7_semantic_replay.py', 'v7_semantic_audit.py', 'v6_finance_audit.py')]
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(set(paths))}


def verify_challenge(seeds):
    for seed in seeds:
        base = FIX / str(seed)
        frozen = json.loads((base / 'FREEZE.json').read_text(encoding='utf-8'))
        for name, digest in frozen['files_sha256'].items():
            if sha(base / name) != digest:
                raise ValueError(f'challenge freeze mismatch: {seed}/{name}')
        if {p.name for p in (base / 'input').iterdir() if p.is_file()} != {'request.json', '聊天.txt'}:
            raise ValueError(f'unexpected input inventory: {seed}')


def execute(command, log):
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('stdout:\n' + proc.stdout + '\nstderr:\n' + proc.stderr, encoding='utf-8')
    return proc.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--expected-kb-sha256', required=True)
    parser.add_argument('--phase', default='kb16_rc1')
    parser.add_argument('--seeds', type=int, nargs='+', default=list(SEEDS))
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{64}', args.expected_kb_sha256):
        parser.error('expected-kb-sha256 must be 64 lowercase hexadecimal characters')
    if not re.fullmatch(r'kb16_[a-z0-9_]+', args.phase):
        parser.error('phase must start with kb16_ and contain only lowercase letters, digits or underscores')
    dest = ROOT / 'evaluation/v7' / args.phase
    out = ROOT / 'outputs/v7' / args.phase
    if dest.exists() or out.exists():
        raise SystemExit('target exists; refusing overwrite')
    dest.mkdir(parents=True); out.mkdir(parents=True)
    state = {'status': 'started', 'expected_kb_sha256': args.expected_kb_sha256, 'executions': []}
    before = None
    try:
        if sha(KB) != args.expected_kb_sha256:
            raise ValueError('formal KB differs from expected fingerprint')
        seeds = tuple(args.seeds)
        if not seeds or any(seed not in SEEDS for seed in seeds):
            raise ValueError(f'seeds must be selected from {SEEDS}')
        verify_challenge(seeds)
        before = fingerprint()
        write(dest / 'source_before.json', before)
        write(dest / 'execution_status.json', state)
        for mode in MODES:
            for seed in seeds:
                if fingerprint() != before:
                    raise RuntimeError(f'source drift before {mode}/{seed}')
                source = FIX / str(seed)
                ordinary = json.loads((source / 'input/request.json').read_text(encoding='utf-8'))
                chat = [(source / 'input' / x).resolve() for x in ordinary['chat_files']]
                if chat != [(source / 'input/聊天.txt').resolve()]:
                    raise ValueError('unexpected chat path')
                catalog = (source / 'input' / ordinary['catalog_file']).resolve()
                if catalog.parent != (ROOT / 'original').resolve() or not catalog.is_file():
                    raise ValueError('catalog must be an original file')
                runtime = dest / 'runtime' / mode / str(seed)
                output = out / mode / str(seed)
                request = dest / 'requests' / mode / str(seed) / 'batch.json'
                batch = {'schema_version': 1, 'batch_id': 'current', 'shipping_date': ordinary['batch_date'],
                         'chat_files': [str(p) for p in chat], 'config': config(ordinary),
                         'matching_mode': mode, 'test_only': True}
                write(request, batch)
                entry = {'seed': seed, 'mode': mode, 'register_rc': None, 'run_rc': None,
                         'request': str(request), 'result': str(output / 'current/messages-result.json')}
                state['executions'].append(entry)
                write(dest / 'execution_status.json', state)
                entry['register_rc'] = execute([sys.executable, '-B', str(CLI), 'catalog-register', '--runtime', str(runtime), '--file', str(catalog)], dest / 'logs' / mode / str(seed) / 'catalog.log')
                if fingerprint() != before: raise RuntimeError('source drift after registration')
                if entry['register_rc'] != 0: raise RuntimeError(f'registration failed: {mode}/{seed}')
                entry['run_rc'] = execute([sys.executable, '-B', str(CLI), 'run-messages', '--runtime', str(runtime), '--batch', str(request), '--output', str(output)], dest / 'logs' / mode / str(seed) / 'run.log')
                write(dest / 'execution_status.json', state)
                if fingerprint() != before: raise RuntimeError('source drift after execution')
                if entry['run_rc'] != 0 or not Path(entry['result']).is_file():
                    raise RuntimeError(f'execution failed or result missing: {mode}/{seed}')
        state['phase'] = 'all_runs_completed_before_expected_read'
        write(dest / 'script_status.json', state['executions'])
        write(dest / 'execution_status.json', state)
        after = fingerprint()
        write(dest / 'source_after.json', after)
        if after != before: raise RuntimeError('source drift after runs')
        # Include all artifacts consumed by the scorer and workbook auditor in the scoring freeze.
        frozen = dict(before)
        for base in (out, dest / 'requests', dest / 'runtime'):
            for path in base.rglob('*'):
                if path.is_file(): frozen[str(path.relative_to(ROOT))] = sha(path)
        frozen[str((dest / 'script_status.json').relative_to(ROOT))] = sha(dest / 'script_status.json')
        write(dest / 'input_freeze.json', {'files_sha256': frozen})
        # Scoring is intentionally delegated to the KB16 auditor after all runs;
        # this runner never reads expected answers before execution is frozen.
        if fingerprint() != before: raise RuntimeError('source drift before scoring')
        state['status'] = 'runs_complete_before_audit'
    except Exception as exc:
        state['status'] = 'failed'
        state['error'] = str(exc)
        state['traceback'] = traceback.format_exc()
        raise
    finally:
        try:
            after = fingerprint()
            write(dest / 'source_after.json', after)
            state['source_equal'] = before is not None and before == after
        finally:
            write(dest / 'execution_status.json', state)


if __name__ == '__main__':
    main()
