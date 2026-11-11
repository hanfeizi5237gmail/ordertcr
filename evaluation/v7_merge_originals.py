#!/usr/bin/env python3
"""Merge all current original-message drafts through the actual skill CLI."""
import hashlib
import argparse
import json
from pathlib import Path
import subprocess
import sys
import re

from v6_finance_audit import audit_result

ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'evaluation/v7/original_kb15_combined'


def write(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def source_hashes():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('original','skills/order-mapping')
            for p in sorted((ROOT/folder).rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def main():
    global E
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',default='original_kb15_combined_r2')
    parser.add_argument('--original-phase',default='original_kb15',
                        help='Immutable raw-original replay phase to merge')
    args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.phase):
        parser.error('invalid phase')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',args.original_phase):
        parser.error('invalid original phase')
    E=ROOT/'evaluation/v7'/args.phase
    if E.exists():
        raise FileExistsError('immutable merge phase exists')
    E.mkdir(parents=True)
    before=source_hashes()
    write(E/'source_before.json',before)
    report=json.loads((ROOT/'evaluation/v7'/args.original_phase/'report.json').read_text())
    parts=[json.loads((ROOT/st['result']).read_text()) for st in report['stations'].values()]
    manifests=[path for part in parts for path in part['manifests']]
    command=[sys.executable,'-B',str(ROOT/'skills/order-mapping/scripts/order_mapping.py'),
             'merge-finance','--runtime',str(ROOT/'evaluation/v7'/args.original_phase/'runtime'),
             '--manifests',*manifests,'--output',str(ROOT/'outputs/v7'/args.original_phase/args.phase)]
    process=subprocess.run(command,capture_output=True,text=True)
    (E/'merge.log').write_text(process.stdout+'\nstderr:\n'+process.stderr)
    if process.returncode:
        raise RuntimeError('merge CLI failed')
    response=json.loads(process.stdout)
    if not response.get('ok'):
        raise ValueError('merge returned unsuccessful response')
    merged=response['result']
    if len({part['matching_mode'] for part in parts})!=1:
        raise ValueError('cannot audit mixed matching modes as one result')
    combined={'catalog':parts[0]['catalog'],'event_count':159,
              'matching_mode':parts[0]['matching_mode'],
              'events':[event for part in parts for event in part['events']],
              'sources':[source for part in parts for source in part['sources']],
              'manifests':manifests,'merged_finance':merged}
    write(E/'result.json',combined)
    audit=audit_result(combined)
    write(E/'finance_audit.json',audit)
    after=source_hashes()
    write(E/'source_after.json',after)
    if audit['errors'] or before!=after or len(combined['events'])!=159 or merged['row_count']!=159:
        raise ValueError('merge finance or source verification failed')
    write(E/'report.json',{'order_groups':len(manifests),'events':159,
          'finance_errors':0,'source_equal':True,'merged_finance':merged,
          'scope':'three-company demand estimate draft; unresolved amounts blank, not settlement'})
    print(json.dumps({'groups':len(manifests),'rows':159,'finance_errors':0,'source_equal':True},ensure_ascii=False))


if __name__=='__main__':
    main()
