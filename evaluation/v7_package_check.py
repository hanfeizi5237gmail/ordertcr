#!/usr/bin/env python3
"""Package only the portable skill and verify an extracted raw-message execution."""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess
import sys
import zipfile
import shutil
import re

from v6_finance_audit import audit_result

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/order-mapping'
CHECK = ROOT / 'evaluation/v7/package_check_kb15'
PACKAGE = ROOT / 'dist/order-mapping-v7-kb15.zip'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def snapshot():
    return {str(p.relative_to(ROOT)): sha(p) for folder in (ROOT/'original', SKILL)
            for p in sorted(folder.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def main():
    global CHECK
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', default='package_check_kb15_r2')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.phase):
        parser.error('invalid phase')
    CHECK = ROOT/'evaluation/v7'/args.phase
    if CHECK.exists():
        raise FileExistsError('check phase exists; refusing overwrite')
    CHECK.mkdir(parents=True)
    before = snapshot()
    write(CHECK/'source_before.json', before)
    frozen = json.loads((ROOT/'evaluation/v7/expression_fix/source_frozen.json').read_text())
    if before != frozen:
        raise ValueError('frozen production/original fingerprint differs')
    inventory = {}
    for p in sorted(SKILL.rglob('*')):
        if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
            inventory[str(p.relative_to(SKILL.parent))] = sha(p)
    reused = PACKAGE.exists()
    if not reused:
      with zipfile.ZipFile(PACKAGE, 'w', zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(SKILL.rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts or p.suffix == '.pyc':
                continue
            name = str(p.relative_to(SKILL.parent))
            archive.write(p, name)
    unpacked = CHECK/'unpacked'
    with zipfile.ZipFile(PACKAGE) as archive:
        if set(archive.namelist()) != set(inventory):
            raise ValueError('package inventory differs')
        if archive.testzip():
            raise ValueError('zip CRC check failed')
        archive.extractall(unpacked)
    if any(sha(unpacked/name) != digest for name,digest in inventory.items()):
        raise ValueError('extracted bytes differ')
    if any(part in {'runtime','original','outputs','.env','node_modules'}
           for name in inventory for part in Path(name).parts):
        raise ValueError('private/runtime directory in package')
    extracted = unpacked/'order-mapping'

    def run(command, label, executable=None):
        process = subprocess.run([executable or sys.executable, '-B', *map(str,command)],
                                 capture_output=True, text=True)
        (CHECK/(label+'.log')).write_text(process.stdout+'\nstderr:\n'+process.stderr)
        if process.returncode:
            raise RuntimeError(f'{label} returned {process.returncode}')

    # The host validator requires PyYAML; the business runtime does not.
    validator_python = shutil.which('python3')
    if not validator_python:
        raise RuntimeError('host validator Python unavailable')
    run(['/Users/hanfei/.codex/skills/.system/skill-creator/scripts/quick_validate.py',extracted], 'quick_validate', validator_python)
    run(['-m','unittest','discover','-s',extracted/'tests','-v'], 'core')
    cli = extracted/'scripts/order_mapping.py'
    run([cli,'--help'], 'cli_help')
    runtime = CHECK/'runtime'
    run([cli,'catalog-register','--runtime',runtime,'--file',
         ROOT/'original/食堂食材基准价价格表10.12-10.18.xlsx'], 'catalog_register')
    request=json.loads((ROOT/'evaluation/v7/original_kb15/requests/songbei.json').read_text())
    request['batch_id']='package-kb15'
    write(CHECK/'request.json',request)
    output=ROOT/'outputs/v7'/args.phase
    run([cli,'run-messages','--runtime',runtime,'--batch',CHECK/'request.json','--output',output], 'raw_run')
    result=json.loads((output/'package-kb15/messages-result.json').read_text())
    finance=audit_result(result)
    write(CHECK/'finance_audit.json',finance)
    if result['event_count'] != 53 or finance['errors']:
        raise ValueError('extracted raw order or finance check failed')
    after=snapshot()
    write(CHECK/'source_after.json',after)
    if before != after:
        raise ValueError('source/original changed during packaging')
    report={'package':str(PACKAGE.relative_to(ROOT)),'sha256':sha(PACKAGE),
            'inventory':inventory,'extracted_bytes_equal':True,'source_equal':True,
            'quick_validate':'passed','validator_python':validator_python,'package_reused':reused,
            'cli_help':'passed','raw_events':53,
            'finance_errors':0,'core_log':str((CHECK/'core.log').relative_to(ROOT)),
            'scope':'local extracted Python CLI; no external host or production acceptance'}
    write(CHECK/'report.json',report)
    write(ROOT/'dist/order-mapping-v7-kb15.manifest.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='inventory'},ensure_ascii=False))


if __name__ == '__main__':
    main()
