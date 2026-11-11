#!/usr/bin/env python3
"""Build the portable shared-knowledge package and exercise its extracted CLI."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from v6_finance_audit import audit_result
from v6_replay_audit import config, original_hashes

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build_archive(target):
    source = ROOT / 'skills/order-mapping'
    entries = []
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob('*')):
            if (not path.is_file() or '__pycache__' in path.parts
                    or path.suffix == '.pyc' or path.name == '.DS_Store'
                    or path.name == 'procurement.v1.json'):
                continue
            name = str(Path('order-mapping') / path.relative_to(source))
            info = zipfile.ZipInfo(name, (2026, 11, 8, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
            entries.append(name)
    assert not any(part in name.split('/') for name in entries
                   for part in ('original', 'runtime', 'outputs', 'evaluation'))
    assert not any('history-profile' in name or '.decisions.' in name for name in entries)
    assert 'order-mapping/knowledge/procurement.json' in entries
    return {'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'entries': entries, 'historical_profiles_included': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', default='package_r1')
    args = parser.parse_args()
    if not args.phase.replace('_', '').replace('-', '').isalnum():
        raise ValueError('invalid phase')
    base = ROOT / 'evaluation/v6' / args.phase
    if base.exists():
        raise ValueError('phase already exists; use a new phase to preserve evidence')
    base.mkdir(parents=True)
    before = original_hashes()
    write(base / 'original_sha_before.json', before)
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    # Preserve the exact archive named by every validation receipt. The short
    # filename remains a convenient current copy, not historical evidence.
    package = build_archive(dist / f'order-mapping-v6-{args.phase}.zip')
    shutil.copyfile(package['path'], dist / 'order-mapping-v6.zip')
    checkout = base / 'unpacked'
    with zipfile.ZipFile(package['path']) as archive:
        assert archive.testzip() is None
        archive.extractall(checkout)
    skill = checkout / 'order-mapping'
    tests = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s',
                            str(skill / 'tests'), '-v'], cwd=checkout, text=True, capture_output=True)
    (base / 'tests.log').write_text(tests.stdout + tests.stderr)
    if tests.returncode:
        raise AssertionError(tests.stderr[-4000:])
    cli = [sys.executable, '-B', str(skill / 'scripts/order_mapping.py')]

    def call(*arguments):
        result = subprocess.run(cli + list(map(str, arguments)), cwd=checkout,
                                text=True, capture_output=True)
        with (base / 'cli.log').open('a', encoding='utf-8') as log:
            log.write(result.stdout + result.stderr)
        if result.returncode:
            raise AssertionError(result.stderr[-4000:])
        return json.loads(result.stdout)['result']

    runtime = base / 'runtime'
    call('catalog-register', '--runtime', runtime, '--file',
         ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx')
    chat = base / '本批聊天.txt'
    chat.write_text('【测试采购公司】\n2026-11-14\n胡萝卜２斤，马铃薯3斤\n'
                    '目录不存在的测试原料1包\n取消胡萝卜\n'
                    '【第二采购公司】\n2026-11-14\n马铃薯3斤\n', encoding='utf-8')
    request = {'schema_version': 1, 'batch_id': 'v6-package-shared-smoke',
               'shipping_date': '2026-11-14', 'chat_files': [str(chat)],
               'matching_mode': 'shared', 'test_only': True,
               'config': config({'new_a': '测试采购公司', 'new_b': '第二采购公司'})}
    request_path = base / 'request.json'
    write(request_path, request)
    call('run-messages', '--runtime', runtime, '--batch', request_path,
         '--output', base / 'outputs')
    result = json.loads((base / 'outputs/v6-package-shared-smoke/messages-result.json').read_text())
    assert result['event_count'] == 5
    rows = [event['row'] for event in result['events']]
    potatoes = [row for row in rows if row['name'] == '马铃薯']
    assert len(potatoes) == 2
    assert potatoes[0]['selected']['code'] == potatoes[1]['selected']['code']
    assert all(row['invoice_quantity'] == '3' and row['status'] == 'matched' for row in potatoes)
    unknown = next(row for row in rows if '目录不存在' in row['name'])
    assert unknown['selected'] is None and unknown['amount'] is None
    assert any(row['event_state'] == 'cancellation_record' for row in rows)
    audit = audit_result(result)
    write(base / 'finance_audit.json', audit)
    assert not audit['errors'], audit['errors'][:3]
    after = original_hashes()
    write(base / 'original_sha_after.json', after)
    assert before == after
    report = {'schema_version': 1, 'package': package, 'unpacked_core_tests': True,
              'raw_cli_smoke_events': 5, 'new_company_invariance': True,
              'native_comments_and_finance_audit': audit['stats'],
              'audit_errors': audit['errors'], 'original_sha_unchanged': before == after,
              'verification_scope': 'extracted deterministic CLI; no target host or unseen business accuracy claim',
              'knowledge_sha256': hashlib.sha256((skill / 'knowledge/procurement.json').read_bytes()).hexdigest()}
    write(base / 'validation_result.json', report)
    write(dist / 'v6_delivery_manifest.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'package'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
