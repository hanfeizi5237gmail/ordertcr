#!/usr/bin/env python3
"""Configure the frozen raw-input replay for KB18 transfer."""
from pathlib import Path
from v7_kb16_v2_replay import main
if __name__=='__main__':
 root=Path(__file__).resolve().parents[1]
 main(fixture=root/'evaluation/v7/kb18_transfer_challenge',seeds=(9101,9102,9191),phase_prefix='kb18_transfer_',extra_scripts=(Path(__file__).resolve(),root/'evaluation/v7_kb16_v2_replay.py'))
