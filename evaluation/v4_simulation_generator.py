"""Create repeatable metamorphic chat samples with event-level provenance.

The semantic fields in the manifest are parser observations, not independent
annotations. Historical human reviews can be joined using source_event_id.
"""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / 'evaluation/v4/simulation'
SOURCES = (
    ('xueyuan', '学院路站', '学院路站11.3下单.txt'),
    ('xingyue', '星月站', '星月站11.3下单.txt'),
    ('songbei', '松北大道站', '松北大道站11.3下单.txt'),
)
SEPARATORS = ('\n', '，', ',', '、', '；', ';')
DEFAULT_SEED = 20261108
sys.path.insert(0, str(ROOT / 'skills/order-mapping/scripts'))
from order_mapping_core.parsing import parse_chat  # noqa: E402


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_events():
    sources, events = [], []
    for station, station_name, filename in SOURCES:
        data = (ROOT / 'original' / filename).read_bytes()
        text = data.decode('utf-8-sig')
        rows = parse_chat(text, filename)
        sources.append({'source_station': station, 'station_name': station_name,
                        'source_name': filename, 'sha256': sha256(data), 'event_count': len(rows)})
        for item in rows:
            assert text[item['char_start']:item['char_end']] == item['raw_text']
            events.append({
                'source_event_id': item['line_id'], 'source_station': station,
                'station_name': station_name, 'source_name': filename,
                'source_line': item['source_line'], 'source_char_start': item['char_start'],
                'source_char_end': item['char_end'], 'raw_text': item['raw_text'],
                'name': item['name'], 'quantity': item['quantity'], 'unit': item['unit'],
                'date_context': item['date_context'], 'event': item['event'],
                'target_station': item['target_station'], 'notes': item['notes'],
                'issues': item['issues'],
            })
    ids = [event['source_event_id'] for event in events]
    if len(ids) != len(set(ids)):
        raise ValueError('原始事件标识不唯一')
    return sources, events


def group_samples(events, randomizer):
    """Split and shuffle per station; distribute each station across mixed samples."""
    by_station = {station: [event for event in events if event['source_station'] == station]
                  for station, _, _ in SOURCES}
    split, reordered = [], []
    for station, _, _ in SOURCES:
        pool = by_station[station][:]
        randomizer.shuffle(pool)
        while pool:
            count = randomizer.randint(5, 13)
            split.append(pool[:count])
            del pool[:count]
        pool = by_station[station][:]
        randomizer.shuffle(pool)
        reordered.append(pool)
    # Round-robin assignment ensures every mixed sample contains every station.
    combined = [[] for _ in range(10)]
    for station, _, _ in SOURCES:
        pool = by_station[station][:]
        randomizer.shuffle(pool)
        start = randomizer.randrange(len(combined))
        for index, event in enumerate(pool):
            combined[(start + index) % len(combined)].append(event)
    for sample in combined:
        randomizer.shuffle(sample)
    return {'single_station_split': split, 'single_station_reorder': reordered,
            'cross_station': combined}


def render_sample(events, randomizer):
    # Start with no-date events. Once a date heading is emitted, parse_chat has
    # no reset to None. Keeping date groups separate prevents context bleed.
    groups = defaultdict(list)
    for event in events:
        groups[event['date_context']].append(event)
    dates = [None] if None in groups else []
    dated = [value for value in groups if value is not None]
    randomizer.shuffle(dated)
    dates.extend(dated)
    parts, positioned = [], []
    position = 0

    def append(value):
        nonlocal position
        parts.append(value)
        position += len(value)

    for group_index, date in enumerate(dates):
        if group_index:
            append('\n')
        if date is not None:
            append(date + '\n')
        for index, event in enumerate(groups[date]):
            if index:
                append(randomizer.choice(SEPARATORS))
            start = position
            append(event['raw_text'])
            positioned.append({**event, 'generated_char_start': start,
                               'generated_char_end': position,
                               'generated_date_context': date})
    append('\n')
    return ''.join(parts), positioned


def generate(seed=DEFAULT_SEED):
    sources, events = load_events()
    randomizer = random.Random(seed)
    scenarios, texts = {}, {}
    for scenario, groups in group_samples(events, randomizer).items():
        scenarios[scenario] = []
        for index, group in enumerate(groups, 1):
            sample_id = f'{scenario}-{index:03d}'
            path = f'{scenario}/{sample_id}.txt'
            text, records = render_sample(group, randomizer)
            texts[path] = text
            scenarios[scenario].append({
                'sample_id': sample_id, 'text_file': path,
                'text_sha256': sha256(text.encode('utf-8')),
                'event_count': len(records), 'events': records,
            })
    return ({'schema_version': 1, 'seed': seed, 'sources': sources,
             'source_event_count': len(events), 'scenarios': scenarios,
             'semantic_fields_basis': 'original_parse_chat_observation_not_independent_truth',
             'generated_offsets': '0-based half-open Unicode character offsets'}, texts)


def self_test():
    manifest, texts = generate(DEFAULT_SEED)
    again, again_texts = generate(DEFAULT_SEED)
    assert (manifest, texts) == (again, again_texts), 'same seed must reproduce the output'
    assert generate(DEFAULT_SEED + 1)[1] != texts, 'changing seed must change the simulated texts'
    _, originals = load_events()
    source_by_id = {event['source_event_id']: event for event in originals}
    source_ids = set(source_by_id)
    assert len(source_ids) == 159
    used_separators = set()
    for scenario, samples in manifest['scenarios'].items():
        events = [event for sample in samples for event in sample['events']]
        ids = [event['source_event_id'] for event in events]
        assert len(ids) == len(set(ids)) == 159 and set(ids) == source_ids, scenario
        for sample in samples:
            text = texts[sample['text_file']]
            assert sha256(text.encode('utf-8')) == sample['text_sha256']
            parsed = parse_chat(text, sample['text_file'])
            # Test source spans and independently reparse the generated samples.
            assert len(parsed) == len(sample['events']), (sample['sample_id'], len(parsed), sample['event_count'])
            for expected, actual in zip(sample['events'], parsed):
                original = source_by_id[expected['source_event_id']]
                for key, value in original.items():
                    assert expected[key] == value, (sample['sample_id'], key)
                assert text[expected['generated_char_start']:expected['generated_char_end']] == expected['raw_text']
                assert (actual['char_start'], actual['char_end']) == (expected['generated_char_start'], expected['generated_char_end'])
                for key in ('raw_text', 'name', 'quantity', 'unit', 'notes', 'date_context', 'event', 'target_station', 'issues'):
                    assert actual[key] == expected[key], (sample['sample_id'], expected['source_event_id'], key)
            for left, right in zip(sample['events'], sample['events'][1:]):
                between = text[left['generated_char_end']:right['generated_char_start']]
                if between in SEPARATORS:
                    used_separators.add(between)
            if scenario == 'cross_station':
                assert len({event['source_station'] for event in sample['events']}) == 3
            else:
                assert len({event['source_station'] for event in sample['events']}) == 1
    assert used_separators == set(SEPARATORS), used_separators
    return {'passed': True, 'seed': DEFAULT_SEED, 'source_events': len(source_ids),
            'scenarios': {name: {'samples': len(samples), 'events': sum(s['event_count'] for s in samples)}
                          for name, samples in manifest['scenarios'].items()},
            'separators': list(SEPARATORS)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--self-test', action='store_true', help='验证生成、来源和重解析，不写文件')
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
        return
    manifest, texts = generate(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for relative, text in texts.items():
        path = args.output_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    (args.output_dir / 'manifest.json').write_bytes(manifest_text.encode('utf-8'))
    print(json.dumps({'output': str(args.output_dir / 'manifest.json'),
                      'seed': args.seed, 'source_events': manifest['source_event_count'],
                      'samples': {key: len(value) for key, value in manifest['scenarios'].items()}},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
