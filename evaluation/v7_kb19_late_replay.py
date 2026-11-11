#!/usr/bin/env python3
"""Execute the independently frozen late KB19 challenge from ordinary messages."""
from pathlib import Path
from v7_kb16_v2_replay import main

if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    main(fixture=root / 'evaluation/v7/kb19_late_holdout', seeds=(9391,),
         phase_prefix='kb19_late_',
         extra_scripts=(Path(__file__).resolve(), root / 'evaluation/v7_kb16_v2_replay.py',
                        root / 'evaluation/v7/kb19_late_replay_audit.py'))
