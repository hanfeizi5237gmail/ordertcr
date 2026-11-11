#!/usr/bin/env python3
"""Stable V7 simulation-generator entrypoint.

The V7 fixture format is implemented by ``v7_fresh_generator_v2.py``. This
wrapper keeps the requested versioned entrypoint while preserving its
no-overwrite and ``--verify`` behavior.
"""
from v7_fresh_generator_v2 import main

if __name__ == '__main__':
    main()
