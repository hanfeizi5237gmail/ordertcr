#!/usr/bin/env python3
from pathlib import Path
from v7_kb16_v2_replay import main
if __name__=='__main__':
 root=Path(__file__).resolve().parents[1]
 main(fixture=root/'evaluation/v7/kb19_challenge',seeds=(9201,9202,9291),phase_prefix='kb19_',extra_scripts=(Path(__file__).resolve(),root/'evaluation/v7_kb16_v2_replay.py'))
