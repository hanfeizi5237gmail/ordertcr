#!/usr/bin/env python3
"""Generate reproducible, source-preserving random combinations of historical chats.

Each seed is a separate historical metamorphic regression, not a new customer
order or an independent accuracy sample. Source-company attribution is retained.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import random

from v4_simulation_generator import (ROOT, SEPARATORS, SOURCES, load_events,
                                     parse_chat, render_sample, sha256)

DEFAULT_SEEDS = (20261108, 20261109)
DEFAULT_OUTPUT = ROOT / 'evaluation/v5/simulation'
SCENARIOS = ('single_station_random_split', 'single_station_reorder',
             'cross_station_random_combinations', 'counterfactual_company',
             'negative_unpartitioned')


def combination_groups(events, rng):
    """Partition the complete event set into varied small and large mixtures."""
    pool = events[:]
    rng.shuffle(pool)
    groups = []
    while pool:
        size = min(len(pool), rng.randint(5, 18))
        groups.append(pool[:size])
        del pool[:size]
    return groups


def mixed_groups(events, rng, count=12):
    """Put all three source stations in every group, then distribute the rest."""
    groups = [[] for _ in range(count)]
    leftovers = []
    for station, _, _ in SOURCES:
        station_events = [event for event in events if event['source_station'] == station]
        rng.shuffle(station_events)
        if len(station_events) < count:
            raise ValueError(f'not enough source events for {count} mixed groups: {station}')
        for index, event in enumerate(station_events[:count]):
            groups[index].append(event)
        leftovers.extend(station_events[count:])
    rng.shuffle(leftovers)
    capacities = [rng.randint(9, 18) for _ in groups]
    for event in leftovers:
        available = [index for index, group in enumerate(groups) if len(group) < capacities[index]]
        if not available:
            available = list(range(count))
        index = rng.choice(available)
        groups[index].append(event)
    for group in groups:
        rng.shuffle(group)
    return groups


def scenario_groups(events, rng):
    by_station = {station: [event for event in events if event['source_station'] == station]
                  for station, _, _ in SOURCES}
    split, reorder = [], []
    for station, _, _ in SOURCES:
        split.extend(combination_groups(by_station[station], rng))
        pool = by_station[station][:]
        rng.shuffle(pool)
        reorder.append(pool)
    counterfactual = mixed_groups(events, rng)
    unpartitioned = events[:]
    rng.shuffle(unpartitioned)
    return {SCENARIOS[0]: split, SCENARIOS[1]: reorder,
            SCENARIOS[2]: mixed_groups(events, rng),
            SCENARIOS[3]: counterfactual, SCENARIOS[4]: [unpartitioned]}


def attribution(event, scenario):
    source = event['source_station']
    if scenario == 'counterfactual_company':
        stations = [station for station, _, _ in SOURCES]
        target = stations[(stations.index(source) + 1) % len(stations)]
        return {'evaluation_company_id': target, 'routing_status': 'counterfactual',
                'manual_sku_expectation': 'undefined_no_target_company_annotation'}
    if scenario == 'negative_unpartitioned':
        return {'evaluation_company_id': None, 'routing_status': 'needs_manual_partition',
                'manual_sku_expectation': 'undefined_until_partitioned'}
    return {'evaluation_company_id': source, 'routing_status': 'source_company_partition',
            'manual_sku_expectation': 'historical_reference_only'}


def relation_context(event):
    """Expose source relation cues without guessing a missing antecedent."""
    return {'source_event_marker': event['event'],
            'source_target_station': event['target_station'],
            'original_date_context': event['date_context'],
            'antecedent_source_event_id': None,
            'antecedent_status': 'needs_manual_review' if event['event'] else 'none_asserted'}


def generate(seed):
    sources, events = load_events()
    source_hashes = {source['source_name']: source['sha256'] for source in sources}
    rng = random.Random(seed)
    scenarios, texts = {}, {}
    for scenario, groups in scenario_groups(events, rng).items():
        samples = []
        for index, group in enumerate(groups, 1):
            sample_id = f'{scenario}-{index:03d}'
            relative = f'{scenario}/{sample_id}.txt'
            text, records = render_sample(group, rng)
            records = [dict(record, canonical_event_id=record['source_event_id'],
                            source_sha256=source_hashes[record['source_name']],
                            relation_context=relation_context(record),
                            **attribution(record, scenario)) for record in records]
            texts[relative] = text
            samples.append({'sample_id': sample_id, 'text_file': relative,
                            'text_sha256': sha256(text.encode('utf-8')),
                            'event_count': len(records),
                            'scenario_expectation': (
                                'no_target_company_oracle_and_no_inherited_manual_sku' if scenario == 'counterfactual_company'
                                else 'unpartitioned_cross_company_input_must_wait_for_manual_routing' if scenario == 'negative_unpartitioned'
                                else 'preserve_original_company_provenance_and_event_boundaries'),
                            'source_station_counts': dict(sorted(Counter(
                                record['source_station'] for record in records).items())),
                            'events': records})
        scenarios[scenario] = samples
    manifest = {'schema_version': 1, 'simulation_version': 5,
                'evaluation_type': 'historical_reference_fitted_metamorphic_regression',
                'seed': seed, 'sources': sources, 'source_event_count': len(events),
                'scenarios': scenarios,
                'semantic_fields_basis': 'original_parse_chat_observation_not_independent_truth',
                'generated_offsets': '0-based half-open Unicode character offsets',
                'company_attribution': 'source_station is immutable provenance; evaluation_company_id is a scenario-specific target, or null when intentionally unpartitioned',
                'relation_boundary': 'event and target_station are source parser observations; '
                                     'a split/reorder can separate a correction, cancellation, '
                                     'or repetition from its antecedent, so no cross-sample '
                                     'relation is inferred or scored as resolved',
                'negative_scenarios': [
                    {'id': 'cross_station_profile_leakage',
                     'trigger': 'a synthetic text contains events from different source stations',
                     'required_behavior': 'route each event to its source company; never apply another station profile or inherit its manual SKU'},
                    {'id': 'lost_date_or_relation_context',
                     'trigger': 'random grouping detaches an event from original date heading or prior correction/append/cancel event',
                     'required_behavior': 'preserve the manifest source date and event relation fields; mark unresolved context for review, never invent a resolved cross-batch event chain'},
                    {'id': 'quantity_or_price_guess',
                     'trigger': 'count/packaging conversion or current catalog identity remains unknown',
                     'required_behavior': 'preserve unresolved order rows, candidate notes, and blank unknown amounts; never substitute old delivered weight or price'},
                    {'id': 'counterfactual_company',
                     'trigger': 'evaluation_company_id is deliberately changed from source_station',
                     'required_behavior': 'do not inherit source company manual SKU, profile or historical decision as target company truth; identity expectation is undefined'},
                    {'id': 'negative_unpartitioned',
                     'trigger': 'all 159 events from three stations appear in a single mixed text without a trusted partition',
                     'required_behavior': 'stop at routing boundary and ask for manual company attribution; do not run an order or report SKU accuracy'},
                ],
                'limitations': ['源于同一三站历史聊天，不是未见订单准确率',
                                '原始日期上下文随事件重建；跨批修订链不证明仍可执行',
                                '原站人工SKU不作为反事实改公司的正确答案']}
    return manifest, texts


def validate(manifest, texts):
    """Check full coverage, source spans, generated spans, and reparsed fields."""
    sources, originals = load_events()
    source_by_id = {event['source_event_id']: event for event in originals}
    source_hashes = {source['source_name']: source['sha256'] for source in sources}
    if len(source_by_id) != 159 or len(originals) != 159:
        raise AssertionError('source event IDs or expected count changed')
    if manifest['sources'] != sources or manifest['source_event_count'] != 159:
        raise AssertionError('source provenance changed')
    observed = []
    if set(manifest['scenarios']) != set(SCENARIOS):
        raise AssertionError('scenario types changed')
    for scenario, samples in manifest['scenarios'].items():
        for sample in samples:
            text = texts[sample['text_file']]
            if sha256(text.encode('utf-8')) != sample['text_sha256']:
                raise AssertionError(f"sample fingerprint differs: {sample['sample_id']}")
            records = sample['events']
            if len(records) != sample['event_count'] or len(records) < 1:
                raise AssertionError(f"sample count differs: {sample['sample_id']}")
            if dict(sorted(Counter(r['source_station'] for r in records).items())) != sample['source_station_counts']:
                raise AssertionError(f"station counts differ: {sample['sample_id']}")
            if scenario in SCENARIOS[:2] and len(sample['source_station_counts']) != 1:
                raise AssertionError(f"single-station sample mixed stations: {sample['sample_id']}")
            if scenario in SCENARIOS[2:] and len(sample['source_station_counts']) != 3:
                raise AssertionError(f"mixed sample omits source station: {sample['sample_id']}")
            parsed = parse_chat(text, sample['text_file'])
            if len(parsed) != len(records):
                raise AssertionError(f"reparse count differs: {sample['sample_id']}: {len(parsed)} vs {len(records)}")
            for expected, actual in zip(records, parsed):
                source_id = expected['source_event_id']
                observed.append(source_id)
                original = source_by_id[source_id]
                if any(expected[key] != value for key, value in original.items()):
                    raise AssertionError(f'original provenance differs: {source_id}')
                if expected['canonical_event_id'] != source_id or expected['source_sha256'] != source_hashes[expected['source_name']]:
                    raise AssertionError(f'source identity differs: {source_id}')
                if {k: expected[k] for k in ('evaluation_company_id', 'routing_status', 'manual_sku_expectation')} != attribution(expected, scenario):
                    raise AssertionError(f'company attribution differs: {source_id}')
                if expected['relation_context'] != relation_context(original):
                    raise AssertionError(f'relation context differs: {source_id}')
                start, end = expected['generated_char_start'], expected['generated_char_end']
                if text[start:end] != original['raw_text'] or (actual['char_start'], actual['char_end']) != (start, end):
                    raise AssertionError(f'generated span differs: {source_id}')
                for key in ('raw_text', 'name', 'quantity', 'unit', 'notes', 'date_context',
                            'event', 'target_station', 'issues'):
                    if actual[key] != expected[key]:
                        raise AssertionError(f'reparsed {key} differs: {source_id}')
            for left, right in zip(records, records[1:]):
                gap = text[left['generated_char_end']:right['generated_char_start']]
                if gap and gap not in SEPARATORS and not any(date in gap for date in
                                                             {r['date_context'] for r in records if r['date_context']}):
                    raise AssertionError(f"unexpected separator: {sample['sample_id']}")
        if len(observed) != 159 or set(observed) != set(source_by_id):
            raise AssertionError('source event coverage or duplication differs')
        observed.clear()


def self_test():
    outputs = {}
    for seed in DEFAULT_SEEDS:
        manifest, texts = generate(seed)
        validate(manifest, texts)
        if (manifest, texts) != generate(seed):
            raise AssertionError(f'seed {seed} is not reproducible')
        outputs[seed] = {'samples': {name: len(samples) for name, samples in manifest['scenarios'].items()},
                         'source_events': manifest['source_event_count'],
                         'sha256': sha256(json.dumps(manifest, ensure_ascii=False,
                                                    sort_keys=True).encode('utf-8'))}
    if generate(DEFAULT_SEEDS[0])[1] == generate(DEFAULT_SEEDS[1])[1]:
        raise AssertionError('distinct seeds produced the same samples')
    return outputs


def write_seed(seed, base):
    manifest, texts = generate(seed)
    validate(manifest, texts)
    output = base / str(seed)
    output.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        folder = output / scenario
        if folder.exists():
            for old in folder.glob(f'{scenario}-[0-9][0-9][0-9].txt'):
                if old.relative_to(output).as_posix() not in texts:
                    old.unlink()
    for relative, text in texts.items():
        path = output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
    manifest_path = output / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for relative, text in texts.items():
        if (output / relative).read_text(encoding='utf-8') != text:
            raise AssertionError(f'written text differs: {relative}')
    if json.loads(manifest_path.read_text(encoding='utf-8')) != manifest:
        raise AssertionError('written manifest differs')
    return {'manifest': str(manifest_path), 'seed': seed,
            'samples': {name: len(samples) for name, samples in manifest['scenarios'].items()},
            'events': manifest['source_event_count'],
            'sha256': sha256(manifest_path.read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, nargs='+', default=DEFAULT_SEEDS)
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--self-test', action='store_true', help='不写文件，验证两个固定种子')
    args = parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds):
        parser.error('seed must be unique')
    result = self_test() if args.self_test else [write_seed(seed, args.output_dir) for seed in args.seeds]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
