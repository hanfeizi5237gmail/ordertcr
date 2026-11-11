#!/usr/bin/env python3
"""Audit current original-chat outputs against the fixed historical 132/155/159.

This scorer runs only after production outputs exist. Historical manual labels
are never passed to the production CLI and do not define new-order A/B/C.
"""
import argparse
import json
from pathlib import Path
import re

from v6_identity_audit import (ROOT, ORACLE, CATALOG, V5_REPORTS, read, sha,
                               load_catalog, frozen_universe, assess_group)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'kb19_original_r[1-9][0-9]*', args.phase):
        parser.error('invalid original replay phase')
    phase = ROOT / 'evaluation/v7' / args.phase
    output = phase / 'fixed_identity.json'
    if output.exists():
        raise FileExistsError('refusing to overwrite identity evidence')
    oracle = read(ORACLE)
    universe = frozen_universe(oracle, [read(p) for p in V5_REPORTS],
                               load_catalog(CATALOG, oracle['catalog_sha256']))
    source_lookup = {(e['source']['file'], e['source']['start'], e['source']['end'],
                      e['source']['raw_text']): e['event_id']
                     for e in oracle['historical_events']}
    replay = read(phase / 'report.json')
    if replay.get('status') != 'completed' or not replay.get('source_equal'):
        raise ValueError('raw replay is not complete or source hashes drifted')
    scored_inputs = []
    for station in replay['stations'].values():
        result_path = ROOT / station['result']
        result = read(result_path)
        for event in result['events']:
            row = event['row']
            key = (row.get('source_name'), row.get('char_start'),
                   row.get('char_end'), row.get('raw_text'))
            scored_inputs.append({'source_event_id': source_lookup.get(key),
                                  'actual': event, 'errors': []})
    assessment = assess_group(universe, scored_inputs)
    report = {'phase': args.phase, 'scope': 'fixed historical manual SKU identity only; not A/B/C',
              'fixed_denominators': {'events': 159, 'aligned': 155, 'sku': 132},
              'oracle_sha256': sha(ORACLE), 'catalog_sha256': sha(CATALOG),
              'assessment': assessment}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in assessment.items() if k != 'events'},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
