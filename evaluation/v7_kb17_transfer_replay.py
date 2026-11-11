#!/usr/bin/env python3
"""Configure the shared raw-input replay for the KB17 transfer challenge."""
from pathlib import Path
from v7_kb16_v2_replay import main

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    main(fixture=root / 'evaluation/v7/kb17_transfer_challenge',
         seeds=(9001, 9002, 9091), phase_prefix='kb17_transfer_',
         extra_scripts=(Path(__file__).resolve(), root / 'evaluation/v7_kb16_v2_replay.py'))
