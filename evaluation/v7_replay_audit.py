#!/usr/bin/env python3
"""Reuse the frozen historical scorer while keeping V7 evidence separate."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v6_replay_audit import MODES, ROOT, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True)
    parser.add_argument('--seeds', type=int, nargs='+', default=[6108, 6109, 6112])
    parser.add_argument('--modes', nargs='+', choices=MODES, default=list(MODES))
    args = parser.parse_args()
    if not args.phase.replace('_', '').replace('-', '').isalnum():
        parser.error('phase must be alphanumeric with underscore or hyphen')
    run(ROOT/'evaluation/v6/oracle/oracle_v4.json', 'all', args.phase,
        modes=args.modes, seeds=args.seeds, evidence_root=ROOT/'evaluation/v7',
        output_root=ROOT/'outputs/v7', update_index=False)


if __name__ == '__main__':
    main()
