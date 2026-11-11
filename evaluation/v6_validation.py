#!/usr/bin/env python3
"""Aggregate explicitly named v6 replay audits without running the mapper.

Examples (run only after the corresponding audits exist and are authorized):
  python3 -B ordertcr/evaluation/v6_validation.py \
    --challenge challenge_dev_final --extension extension_dev_final \
    --historical development_r3 --package-validation ordertcr/dist/v6_delivery_manifest.json
  python3 -B ordertcr/evaluation/v6_validation.py --self-test

Each phase is reported separately; metrics from different datasets are never
merged. Input arguments identify audit outputs, never challenge input/oracles.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
V6 = ROOT / 'evaluation/v6'
FACTS = V6 / 'source_fact_limits.json'
OUTPUTS = (V6 / 'validation_result.json', ROOT / 'evaluation/v6_validation_result.json')
MODES = ('catalog_only', 'shared', 'shared_with_company_contract')
COUNTS = {'source_events': 159, 'proven_blocked_events': 26,
          'not_proven_blocked': 133, 'upper_bound_possible_events': 133}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path):
    with path.open(encoding='utf-8') as stream:
        return json.load(stream)


def allowed_path(value, *, phase=False):
    """Resolve an explicitly named local audit without touching input trees."""
    raw = Path(value).expanduser()
    if phase and len(raw.parts) == 1 and raw.suffix != '.json':
        raw = V6 / raw / 'replay_audit.json'
    elif not raw.is_absolute():
        # Accept workspace-relative ordertcr/... or paths relative to ordertcr.
        raw = ROOT.parent / raw if raw.parts and raw.parts[0] == ROOT.name else ROOT / raw
    if raw.is_dir():
        raw = raw / 'replay_audit.json'
    path = raw.resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError(f'path outside ordertcr: {value}')
    if phase and (path.name != 'replay_audit.json' or 'input' in path.parts or 'challenge' in path.parts):
        raise ValueError(f'phase must identify an audit output: {value}')
    if path in (p.resolve() for p in OUTPUTS):
        raise ValueError('output file cannot be an input')
    return path


def ratio(numerator, denominator):
    return round(numerator * 100 / denominator, 4) if denominator else None


def fact_limits(document, path=None):
    counts = document['counts']
    for name, expected in COUNTS.items():
        if counts.get(name) != expected:
            raise ValueError(f'source_fact_limits unexpected {name}: {counts.get(name)!r}')
    if counts.get('station_counts') != {'学院路站': 64, '星月站': 42, '松北大道站': 53}:
        raise ValueError('source_fact_limits station counts mismatch')
    events = document.get('events', [])
    if len(events) != counts['source_events']:
        raise ValueError('source_fact_limits event count mismatch')
    if Counter(e.get('source_status') for e in events) != {'proven_blocked': 26, 'not_proven_hard': 133}:
        raise ValueError('source_fact_limits event classifications mismatch')
    if Counter(e.get('station') for e in events) != counts['station_counts']:
        raise ValueError('source_fact_limits event station counts mismatch')
    event_ids = [e.get('event_id') for e in events]
    if None in event_ids or len(set(event_ids)) != len(event_ids):
        raise ValueError('source_fact_limits event identifiers invalid')
    return {
        'path': str(path) if path else None, 'sha256': digest(path) if path else None,
        'scope': document['scope'], 'counts': counts,
        'proven_hard_fact_gaps': 26,
        'loose_upper_bound': {'numerator': 133, 'denominator': 159,
                              'percent': ratio(133, 159), 'is_achievable_rate': False,
                              'interpretation': document['upper_bound_interpretation']},
    }


def audit_integrity(data):
    """Check reported arithmetic/partitions, keeping discrepancies visible."""
    issues = []
    before, after = data.get('scorer_sha256_before'), data.get('scorer_sha256_after')
    if before and after and before != after:
        issues.append('scorer hash changed during evaluation')
    metrics = data.get('metrics')
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError('audit has no metrics')
    for mode, metric in metrics.items():
        if not isinstance(metric, dict):
            raise ValueError(f'{mode}: invalid metrics')
        for key in ('input_events', 'automatic', 'automatic_correct', 'manual_correct', 'A', 'B', 'C'):
            if key not in metric:
                raise ValueError(f'{mode}: missing {key}')
        n, auto, correct, manual = (metric[k] for k in ('input_events', 'automatic', 'automatic_correct', 'manual_correct'))
        # Historical replay reports this count as control_handled_correct;
        # challenge/extension reports call it control_correct.
        if ('control_correct' in metric and 'control_handled_correct' in metric and
                metric['control_correct'] != metric['control_handled_correct']):
            issues.append(f'{mode}: conflicting control count fields')
        control = metric.get('control_correct', metric.get('control_handled_correct', 0))
        if any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in (n, auto, correct, manual, control)):
            raise ValueError(f'{mode}: invalid counts')
        if correct > auto or correct + manual + control > n:
            issues.append(f'{mode}: impossible correctness counts')
        for key, calc in (('A', ratio(correct, auto)), ('B', ratio(correct, n)),
                          ('C', ratio(correct + manual + control, n))):
            value = metric[key]
            if value is None and calc is None:
                continue
            if not isinstance(value, (int, float)) or calc is None or abs(value - calc) > 0.00011:
                issues.append(f'{mode}: {key} does not match counts')
        for section in ('by_company', 'by_scenario'):
            grouped = data.get(section, {}).get(mode)
            if not isinstance(grouped, dict):
                issues.append(f'{mode}: missing {section}')
            elif sum(v.get('input_events', 0) for v in grouped.values()) != n:
                issues.append(f'{mode}: {section} denominator mismatch')
        normal = data.get('normal_supported_metrics', {}).get(mode)
        if normal is not None and normal.get('input_events', 0) > n:
            issues.append(f'{mode}: normal denominator exceeds full denominator')
    return issues


def describe_audit(data, kind, path=None):
    integrity = audit_integrity(data)
    metrics = data['metrics']
    missing_modes = [mode for mode in MODES if mode not in metrics]
    legacy = any('candidate_completeness_unverified' not in item for item in metrics.values())
    return {
        'kind': kind, 'path': str(path) if path else None, 'sha256': digest(path) if path else None,
        'phase': data.get('phase'), 'split': data.get('split'), 'seeds': data.get('seeds'),
        'verification_scope': data.get('verification_scope', data.get('scope', data.get('limitation'))),
        'scorer_sha256_before': data.get('scorer_sha256_before'),
        'scorer_sha256_after': data.get('scorer_sha256_after'),
        'rescore_from': data.get('rescore_from'),
        'full_metrics': metrics,
        'normal_supported_metrics': data.get('normal_supported_metrics'),
        'normal_supported_note': ('未提供独立 normal_supported 指标，不以场景猜测代替。'
                                  if data.get('normal_supported_metrics') is None else
                                  '子集与全量分开报告，不替代全量分母。'),
        'by_company': data.get('by_company'), 'by_scenario': data.get('by_scenario'),
        'ablation': {mode: metrics.get(mode) for mode in MODES},
        'ablation_missing_modes': missing_modes,
        'execution_errors': data.get('execution_errors'),
        'input_output_mismatches': data.get('input_output_mismatches'),
        'audit_error_count': data.get('audit_error_count'),
        'original_sha_unchanged': data.get('original_sha_unchanged', data.get('source_sha_unchanged')),
        'verification_gate': data.get('verification_gate'),
        'aggregation_integrity_issues': integrity,
        'candidate_completeness': ('历史或未标识独立完整候选审计；C 中人工正确项不得视为候选集合已独立穷尽。'
                                   if legacy else '按原审计 candidate_completeness_unverified 单列；机械一致性不证明语义穷尽。'),
        'candidate_completeness_independently_proven': False,
    }


def package_evidence(document, path=None):
    if not isinstance(document, dict):
        raise ValueError('package validation must be a JSON object')
    return {'path': str(path) if path else None, 'sha256': digest(path) if path else None,
            'report': document,
            'interpretation': '仅转录此包验证报告；不得推断目标宿主、真实订单或入账通过。'}


def aggregate(facts, sources, packages):
    return {
        'schema_version': 2, 'status': 'doing', 'acceptance_verified': False,
        'acceptance_note': '全量A/B、各公司正常场景及异常候选全量独立验收未同时达标。',
        'scope': '离线审计聚合，各审计口径和数据集独立，非生产或真实宿主验收。',
        'fixed_denominators': {'all_chat_events': 159, 'chat_manual_aligned_events': 155,
                               'catalog_verifiable_manual_sku_events': 132,
                               'station_events': {'松北大道站': 53, '星月站': 42, '学院路站': 64}},
        'source_fact_limits': facts,
        'audits': sources,
        'package_validation': packages,
        'host_verification': {'千问办公': '未实测', 'WorkBuddy': '未实测',
                              '真实订单系统': '未连接', '正式财务入账': '未执行'},
        'limitations': ['各阶段、各模式和不同数据集不合并分数。',
                        '历史变形结果不能充当独立新订单准确率。',
                        '候选与批注一致不证明独立完整候选集合。',
                        '固定上界是排除已证实硬缺口后的宽松上界，并非可达率或系统准确率。'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('challenge', 'extension', 'historical'):
        parser.add_argument('--' + flag, action='append', default=[], metavar='AUDIT_OR_PHASE')
    parser.add_argument('--package-validation', action='append', default=[], metavar='JSON')
    parser.add_argument('--source-facts', default=str(FACTS), metavar='JSON')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args(argv)
    if args.self_test:
        if any((args.challenge, args.extension, args.historical, args.package_validation)):
            parser.error('--self-test cannot be combined with inputs')
        run_self_test()
        return
    if not any((args.challenge, args.extension, args.historical)):
        parser.error('specify at least one --challenge, --extension or --historical audit')
    source_path = allowed_path(args.source_facts)
    facts = fact_limits(load_json(source_path), source_path)
    audits = []
    seen = set()
    for kind in ('challenge', 'extension', 'historical'):
        for value in getattr(args, kind):
            path = allowed_path(value, phase=True)
            if path in seen:
                parser.error(f'duplicate audit: {path}')
            seen.add(path)
            audits.append(describe_audit(load_json(path), kind, path))
    packages = []
    for value in args.package_validation:
        path = allowed_path(value)
        packages.append(package_evidence(load_json(path), path))
    report = aggregate(facts, audits, packages)
    content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    for path in OUTPUTS:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    print(json.dumps({'outputs': [str(p) for p in OUTPUTS], 'audits': len(audits),
                      'aggregation_integrity_issues': sum(len(a['aggregation_integrity_issues']) for a in audits)},
                     ensure_ascii=False))


class AggregationTests(unittest.TestCase):
    def sample(self):
        metric = {'input_events': 4, 'automatic': 2, 'automatic_correct': 2, 'manual_correct': 1,
                  'control_correct': 0, 'A': 100.0, 'B': 50.0, 'C': 75.0,
                  'candidate_completeness_unverified': 1}
        return {'phase': 'dev', 'metrics': {'shared': metric},
                'normal_supported_metrics': {'shared': {**metric, 'input_events': 3}},
                'by_company': {'shared': {'company': {'input_events': 4}}},
                'by_scenario': {'shared': {'scenario': {'input_events': 4}}}}

    def test_full_and_subset_stay_separate_with_missing_ablation_visible(self):
        result = describe_audit(self.sample(), 'challenge')
        self.assertEqual(result['full_metrics']['shared']['input_events'], 4)
        self.assertEqual(result['normal_supported_metrics']['shared']['input_events'], 3)
        self.assertEqual(result['ablation_missing_modes'], ['catalog_only', 'shared_with_company_contract'])
        self.assertFalse(result['candidate_completeness_independently_proven'])
        self.assertEqual(result['aggregation_integrity_issues'], [])

    def test_bad_metrics_and_denominator_are_detected(self):
        sample = self.sample()
        sample['metrics']['shared']['B'] = 100.0
        sample['by_company']['shared']['company']['input_events'] = 3
        result = describe_audit(sample, 'extension')
        self.assertEqual(len(result['aggregation_integrity_issues']), 2)

    def test_legacy_control_handled_is_included_in_c(self):
        sample = self.sample()
        sample['metrics']['shared'] = {'input_events': 1034, 'automatic': 336,
            'automatic_correct': 293, 'manual_correct': 385, 'control_handled_correct': 24,
            'A': 87.2024, 'B': 28.3366, 'C': 67.8917}
        sample['by_company']['shared']['company']['input_events'] = 1034
        sample['by_scenario']['shared']['scenario']['input_events'] = 1034
        self.assertEqual(audit_integrity(sample), [])
        sample['metrics']['shared']['control_correct'] = 0
        self.assertIn('shared: conflicting control count fields', audit_integrity(sample))

    def test_historical_not_upgraded_to_candidate_proof(self):
        sample = self.sample()
        del sample['metrics']['shared']['candidate_completeness_unverified']
        del sample['normal_supported_metrics']
        result = describe_audit(sample, 'historical')
        self.assertIsNone(result['normal_supported_metrics'])
        self.assertIn('未标识', result['candidate_completeness'])
        self.assertFalse(result['candidate_completeness_independently_proven'])

    def test_fixed_source_limits_and_not_achievable(self):
        data = {'counts': {**COUNTS, 'station_counts': {'学院路站': 64, '星月站': 42, '松北大道站': 53}},
                'events': [{'event_id': str(i), 'station': station,
                            'source_status': 'proven_blocked' if i < 26 else 'not_proven_hard'}
                           for i, station in enumerate(['学院路站'] * 64 + ['星月站'] * 42 + ['松北大道站'] * 53)],
                'scope': 'scope', 'upper_bound_interpretation': 'not a rate'}
        result = fact_limits(data)
        self.assertEqual(result['loose_upper_bound']['percent'], 83.6478)
        self.assertFalse(result['loose_upper_bound']['is_achievable_rate'])
        data['counts']['proven_blocked_events'] = 25
        with self.assertRaises(ValueError):
            fact_limits(data)

    def test_zero_automatic_is_undefined_and_outside_paths_rejected(self):
        sample = self.sample()
        sample['metrics']['shared'].update(automatic=0, automatic_correct=0, manual_correct=0, A=None, B=0, C=0)
        self.assertEqual(audit_integrity(sample), [])
        with self.assertRaises(ValueError):
            allowed_path('/tmp/audit.json', phase=True)
        with self.assertRaises(ValueError):
            allowed_path('evaluation/v6/challenge/7201/input/replay_audit.json', phase=True)


def run_self_test():
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AggregationTests))
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
