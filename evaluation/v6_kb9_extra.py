#!/usr/bin/env python3
"""Replay a frozen KB9 control set using the existing CLI and finance audit."""
import argparse
from pathlib import Path
import v6_kb7_challenge as base

DATA_ROOT = Path(__file__).resolve().parent / 'v6/kb9_extra'

base_score = base.score
base_snapshot = base.snapshot


def score(expected, actual):
    scored = base_score(expected, actual)
    row = actual['row']
    if expected.get('required_note') and expected['required_note'] not in (row.get('notes') or ''):
        scored['errors'].append('processing_note_missing')
    if expected['expected_action'] == 'restricted' and row.get('status') == 'matched':
        # A permitted automatic result must also satisfy the complete price,
        # specification, quantity and amount checks, not just the SKU.
        as_auto = dict(expected, expected_action='auto')
        scored['errors'].extend(base_score(as_auto, actual)['errors'])
    scored['errors'] = sorted(set(scored['errors']))
    return scored


def snapshot(freeze):
    values = base_snapshot(freeze)
    values['kb9_wrapper'] = base.sha(__file__)
    values['builder'] = base.sha(DATA_ROOT / 'freeze_inputs.py')
    if base.DATA == DATA_ROOT / 'v2':
        values['revision_builder'] = base.sha(DATA_ROOT / 'freeze_v2.py')
    elif base.DATA == DATA_ROOT / 'negative':
        values['negative_builder'] = base.sha(DATA_ROOT / 'freeze_negative.py')
    values['review'] = base.sha(base.ROOT / 'evaluation/v6/remaining_ordinary_review.md')
    if values['review'] != freeze['review_sha256']:
        raise ValueError('review evidence fingerprint mismatch')
    return values


base.score = score
base.snapshot = snapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', default='kb9_extra_first')
    parser.add_argument('--dataset', choices=('v1', 'v2', 'negative'), default='v1')
    args = parser.parse_args()
    base.DATA = DATA_ROOT if args.dataset == 'v1' else DATA_ROOT / args.dataset
    base.FREEZE = base.DATA / 'FREEZE.json'
    raise SystemExit(0 if base.run(args.phase)['all_checks_passed'] else 1)
