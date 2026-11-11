#!/usr/bin/env python3
"""Run KB18 frozen raw inputs against KB19 without rewriting prior evidence."""
from pathlib import Path
from v7_kb16_v2_replay import main

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    main(fixture=root / 'evaluation/v7/kb18_transfer_challenge',
         seeds=(9101, 9102, 9191), phase_prefix='kb19_oldlane_',
         extra_scripts=(Path(__file__).resolve(), root / 'evaluation/v7_kb16_v2_replay.py'))
