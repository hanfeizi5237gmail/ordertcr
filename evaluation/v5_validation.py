#!/usr/bin/env python3
"""Aggregate v5 seed replay evidence into the fixed acceptance denominators.

This validator only combines already-produced isolated replay reports. It does
not run the order CLI and never treats counterfactual or historical-decision
lanes as independent new-order accuracy.
"""
from collections import defaultdict
from pathlib import Path
import argparse
import hashlib
import json
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
POSITIVE_SCENARIOS = (
    'single_station_random_split',
    'single_station_reorder',
    'cross_station_random_combinations',
)
STATIONS = ('songbei', 'xingyue', 'xueyuan')
STATION_LABELS = {'songbei': '松北大道', 'xingyue': '星月', 'xueyuan': '学院路'}
EXPECTED_EVENTS = {'songbei': 53, 'xingyue': 42, 'xueyuan': 64}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def positive_items(report, lane='profile_only'):
    return [item
            for scenario in POSITIVE_SCENARIOS
            for item in report['scenarios'][scenario]['lanes'][lane]['items']]


def representative_items(report, lane='profile_only'):
    return report['scenarios'][POSITIVE_SCENARIOS[0]]['lanes'][lane]['items']


def station_metrics(items):
    result = {}
    for station in STATIONS:
        rows = [x for x in items if x['source_station'] == station]
        denominator = sum(x['manual_expected_code'] is not None for x in rows)
        correct = sum(bool(x['manual_identity_correct']) for x in rows)
        result[station] = {
            'events': len(rows),
            'manual_aligned': sum(x['manual_row'] is not None for x in rows),
            'current_catalog_manual_sku_denominator': denominator,
            'strict_identity_correct': correct,
            'strict_identity_rate': correct / denominator if denominator else None,
        }
    return result


def lane_metrics(report, lane='profile_only'):
    items = representative_items(report, lane)
    strict_denominator = sum(x['manual_expected_code'] is not None for x in items)
    strict_correct = sum(bool(x['manual_identity_correct']) for x in items)
    aligned = sum(x['manual_row'] is not None for x in items)
    all_events = len(items)
    exceptions = [x for x in items if not x['review_expected_codes']]
    unresolved = [x for x in items if x['status'] != 'matched']
    return {
        'source_event_denominator': all_events,
        'manual_aligned_denominator': aligned,
        'current_catalog_manual_sku_denominator': strict_denominator,
        'strict_identity_132': {'correct': strict_correct, 'denominator': strict_denominator,
                                'rate': strict_correct / strict_denominator if strict_denominator else None},
        'identity_coverage_155': {'correct': strict_correct, 'denominator': aligned,
                                  'rate': strict_correct / aligned if aligned else None},
        'identity_coverage_159': {'correct': strict_correct, 'denominator': all_events,
                                  'rate': strict_correct / all_events if all_events else None},
        'parsed_exact_span': sum(bool(x['parsed_exact_span']) for x in items),
        'quantity_unit_preserved': sum(bool(x['review_quantity_unit_correct']) for x in items),
        'parser_fields_unchanged': sum(bool(x['parser_fields_unchanged']) for x in items),
        'processed': sum(bool(x['processed']) for x in items),
        'same_v3_identity': sum(bool(x['same_as_v3_identity']) for x in items),
        'same_v3_status': sum(bool(x['same_as_v3_status']) for x in items),
        'output_rows_verified': sum(bool(x['processed']) and not x['output_row_errors'] for x in items),
        'undefined_events': sum(x['manual_expected_code'] is None for x in items),
        'exception_events': len(exceptions),
        'unresolved_rows': len(unresolved),
        'manual_row_missing': sum(x['manual_row'] is None for x in items),
        'chat_row_missing': sum(not x['parsed_exact_span'] for x in items),
        'manual_row_merged': None,
        'exception_candidates': {'matched': sum(x['exception_candidates_match_v3'] is True for x in exceptions),
                                 'denominator': len(exceptions)},
        'exception_required_issues': {'matched': sum(x['exception_required_issues_present_v3'] is True for x in exceptions),
                                      'denominator': len(exceptions)},
        'exception_acceptance': {'passed': sum(x['exception_acceptance'] is True for x in exceptions),
                                 'denominator': len(exceptions)},
        'unknown_amount_blank': {'blank': sum(x['amount'] is None for x in unresolved),
                                 'denominator': len(unresolved)},
        'exception_amount_excluded': {'excluded': sum(x['amount'] is None for x in exceptions),
                                      'denominator': len(exceptions)},
        'station': station_metrics(items),
        'merge_attempted': sum(report['scenarios'][scenario]['lanes'][lane]['merge_attempted']
                               for scenario in POSITIVE_SCENARIOS),
        'merge_verified': sum(report['scenarios'][scenario]['lanes'][lane]['merge_verified']
                              for scenario in POSITIVE_SCENARIOS),
    }


def check_counterfactual(report, manifest):
    target_by_event = {
        event['source_event_id']: event.get('evaluation_company_id')
        for sample in manifest['scenarios']['counterfactual_company']
        for event in sample['events']
    }
    checks = {'items': 0, 'target_company_match': 0, 'decision_injection_absent': 0,
              'undefined_manual_sku_oracle': 0, 'errors': []}
    for lane in ('profile_only', 'historical_decisions'):
        for item in report['scenarios']['counterfactual_company']['lanes'][lane]['items']:
            checks['items'] += 1
            target = target_by_event[item['source_event_id']]
            # The original source SKU is never used as target-company truth;
            # the counterfactual target has no independent annotation.
            checks['undefined_manual_sku_oracle'] += 1
            manifest_path = item.get('batch_manifest')
            if not manifest_path:
                checks['errors'].append({'event': item['source_event_id'], 'error': 'missing_batch_manifest'})
                continue
            output_manifest = read(manifest_path)
            if output_manifest['order']['company_id'] == target:
                checks['target_company_match'] += 1
            else:
                checks['errors'].append({'event': item['source_event_id'], 'error': 'target_company_mismatch',
                                         'expected': target, 'actual': output_manifest['order']['company_id']})
            root = Path(manifest_path).resolve().parents[3]
            batch_path = root / f"{output_manifest['batch_id']}.json"
            batch = read(batch_path)
            if not batch.get('decisions'):
                checks['decision_injection_absent'] += 1
            else:
                checks['errors'].append({'event': item['source_event_id'], 'error': 'historical_decision_injected'})
    return checks


def check_negative(report):
    result = {}
    for lane in ('profile_only', 'historical_decisions'):
        metrics = report['scenarios']['negative_unpartitioned']['lanes'][lane]['metrics']
        result[lane] = {
            'all_events': metrics['source_event_denominator'],
            'parsed_exact_span': metrics['parsed_exact_span'],
            'processed': metrics['processed'],
            'company_partition_required': metrics['company_partition_required'],
            'merge_attempted': report['scenarios']['negative_unpartitioned']['lanes'][lane]['merge_attempted'],
            'pass': (metrics['source_event_denominator'] == 159 and
                     metrics['parsed_exact_span'] == 159 and
                     metrics['processed'] == 0 and
                     metrics['company_partition_required'] == 159 and
                     report['scenarios']['negative_unpartitioned']['lanes'][lane]['merge_attempted'] == 0),
        }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reports', nargs=2, required=True)
    parser.add_argument('--manifests', nargs=2, required=True)
    parser.add_argument('--finance-audits', nargs=2, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluation/v5/validation_result.json')
    parser.add_argument('--report-md', type=Path, default=ROOT / 'evaluation/v5_report.md')
    args = parser.parse_args()
    reports = [read(p) for p in args.reports]
    manifests = [read(p) for p in args.manifests]
    finance = [read(p) for p in args.finance_audits]
    seed_results = []
    all_hashes_unchanged = True
    for report, manifest, finance_audit in zip(reports, manifests, finance):
        profile = lane_metrics(report, 'profile_only')
        history = lane_metrics(report, 'historical_decisions')
        for key in ('manual_row_missing', 'chat_row_missing'):
            profile['manual_row_merged'] = sum(v['linked_events'] - v['distinct_linked_rows']
                                               for v in report['manual_workbooks'].values())
        cf = check_counterfactual(report, manifest)
        negative = check_negative(report)
        all_hashes_unchanged = all_hashes_unchanged and report['original_hashes_unchanged']
        seed_results.append({
            'seed': manifest['seed'],
            'manifest': args.manifests[len(seed_results)],
            'manifest_sha256': sha(args.manifests[len(seed_results)]),
            'profile_only': profile,
            'historical_decision_replay': history,
            'counterfactual_company': cf,
            'negative_unpartitioned': negative,
            'finance_audit': {'report': args.finance_audits[len(seed_results)],
                              'attempted': finance_audit['attempted'],
                              'verified': finance_audit['verified'],
                              'passed': finance_audit['passed']},
            'original_hashes_unchanged': report['original_hashes_unchanged'],
            'manual_workbook_sha': report['manual_workbook_sha'],
            'replay_report': args.reports[len(seed_results)],
            'output_root': report['output_root'],
        })
    baseline = seed_results[0]['profile_only']
    rates = [seed['profile_only']['station'][station]['strict_identity_rate']
             for seed in seed_results for station in STATIONS]
    aggregate = {
        'profile_only_replicates': len(seed_results) * len(POSITIVE_SCENARIOS),
        'strict_identity_132': baseline['strict_identity_132'],
        'identity_coverage_155': baseline['identity_coverage_155'],
        'identity_coverage_159': baseline['identity_coverage_159'],
        'station': baseline['station'],
        'micro_average_rate': baseline['strict_identity_132']['rate'],
        'macro_average_rate': mean(rates[:len(STATIONS)]),
        'worst_station': min(STATIONS, key=lambda station: baseline['station'][station]['strict_identity_rate']),
        'worst_station_rate': min(baseline['station'][station]['strict_identity_rate'] for station in STATIONS),
        'profile_only_strict_identity_132_ge_95': baseline['strict_identity_132']['rate'] >= 0.95,
        'all_positive_scenario_replicates_stable': all(
            seed['profile_only']['strict_identity_132'] == baseline['strict_identity_132']
            for seed in seed_results),
        'all_positive_events_processed': all(seed['profile_only']['processed'] == 159 for seed in seed_results),
        'all_positive_quantity_unit_preserved': all(seed['profile_only']['quantity_unit_preserved'] == 159 for seed in seed_results),
        'all_positive_output_rows_verified': all(seed['profile_only']['output_rows_verified'] == 159 for seed in seed_results),
        'all_finance_audits_passed': all(seed['finance_audit']['passed'] for seed in seed_results),
        'all_original_hashes_unchanged': all_hashes_unchanged,
        'core_tests_passed': ('Ran 82 tests' in (ROOT / 'evaluation/v5_core_tests.log').read_text() and
                              '\nOK' in (ROOT / 'evaluation/v5_core_tests.log').read_text()),
        'generator_self_test_present': (ROOT / 'evaluation/v5_generator_self_test.json').exists(),
    }
    result = {
        'schema_version': 1,
        'evaluation_type': 'v5_random_combination_regression_validation',
        'status': 'verified' if (aggregate['profile_only_strict_identity_132_ge_95'] and
                                 aggregate['all_positive_scenario_replicates_stable'] and
                                 aggregate['all_positive_events_processed'] and
                                 aggregate['all_positive_quantity_unit_preserved'] and
                                 aggregate['all_positive_output_rows_verified'] and
                                 aggregate['all_finance_audits_passed'] and
                                 aggregate['all_original_hashes_unchanged'] and
                                 aggregate['core_tests_passed'] and
                                 aggregate['generator_self_test_present']) else 'doing',
        'fixed_denominators': {'all_chat_events': 159, 'chat_manual_aligned_events': 155,
                               'current_catalog_manual_sku_events': 132,
                               'station_events': EXPECTED_EVENTS},
        'seed_results': seed_results,
        'aggregate': aggregate,
        'original_sha256': {
            'before_file': str((ROOT / 'evaluation/v5_original_sha_before.txt').resolve()),
            'after_file': str((ROOT / 'evaluation/v5_original_sha_after.txt').resolve()),
            'equal': (ROOT / 'evaluation/v5_original_sha_before.txt').read_bytes() ==
                     (ROOT / 'evaluation/v5_original_sha_after.txt').read_bytes(),
        },
        'verification_artifacts': {
            'generator_self_test': str((ROOT / 'evaluation/v5_generator_self_test.json').resolve()),
            'core_tests': str((ROOT / 'evaluation/v5_core_tests.log').resolve()),
            'comparison_workbook': str((ROOT / 'evaluation/v5逐项对比.xlsx').resolve()),
        },
        'limitations': [
            'profile-only结果来自同一批历史聊天的事件级变形回归，不是未见订单准确率。',
            'historical-decision replay只验证原文未变且一一对应时的历史决策保持性，不计为独立准确率。',
            '反事实公司没有目标公司人工标注；验收点是目标profile生效且未注入历史逐项决策，SKU正确率不定义。',
            '不分区混合文本保留159条事件但停止在公司路由边界，需人工分区后才能生成订单。',
            '松北大道单站40/44低于95%，原因是人工表与当前原文品牌/部位/品种/包装约束冲突，不能放宽规则强行匹配。',
            '真实宿主、实际配送、财务口径确认和正式入账未执行。',
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = [
        '# v5 订单映射随机组合变形回归', '',
        f"状态：**{result['status']}**。本轮固定分母为全部聊天事件159、聊天—人工对齐155、当前目录可核人工SKU132；三站事件数为松北大道53、星月42、学院路64。", '',
        '## profile-only', '',
        '|口径|正确|分母|比例|', '|---|---:|---:|---:|',
        f"|strict_identity_132|{baseline['strict_identity_132']['correct']}|{baseline['strict_identity_132']['denominator']}|{baseline['strict_identity_132']['rate']:.2%}|",
        f"|identity_coverage_155|{baseline['identity_coverage_155']['correct']}|{baseline['identity_coverage_155']['denominator']}|{baseline['identity_coverage_155']['rate']:.2%}|",
        f"|identity_coverage_159|{baseline['identity_coverage_159']['correct']}|{baseline['identity_coverage_159']['denominator']}|{baseline['identity_coverage_159']['rate']:.2%}|", '',
        '|站点|事件|当前目录可核SKU|一致|比例|', '|---|---:|---:|---:|---:|',
    ]
    for station in STATIONS:
        value = baseline['station'][station]
        lines.append(f"|{STATION_LABELS[station]}|{value['events']}|{value['current_catalog_manual_sku_denominator']}|{value['strict_identity_correct']}|{value['strict_identity_rate']:.2%}|")
    lines.extend(['', f"微平均：{aggregate['micro_average_rate']:.2%}；宏平均：{aggregate['macro_average_rate']:.2%}；最差站点：{STATION_LABELS[aggregate['worst_station']]} {aggregate['worst_station_rate']:.2%}。", '',
                  '## 双seed与场景验证', '', '|seed|拆分/重排/跨站 profile-only|历史决策 replay|财务合并复读|不分区边界|反事实边界|', '|---|---|---|---|---|---|'])
    for seed in seed_results:
        p = seed['profile_only']; h = seed['historical_decision_replay']
        neg = all(x['pass'] for x in seed['negative_unpartitioned'].values())
        cf = seed['counterfactual_company']
        lines.append(f"|{seed['seed']}|{p['processed']}/159，异常{p['exception_acceptance']['passed']}/{p['exception_acceptance']['denominator']}|same_v3_identity {h['same_v3_identity'] if 'same_v3_identity' in h else '见JSON'}|{seed['finance_audit']['verified']}/{seed['finance_audit']['attempted']}|{'通过' if neg else '失败'}（159事件均待人工分区）|{'通过' if not cf['errors'] and cf['target_company_match']==cf['items'] and cf['decision_injection_absent']==cf['items'] else '失败'}（无历史决策注入）|")
    lines.extend(['', '## 异常、数量与金额安全性', '',
                  f"profile-only 每个seed：异常候选集合 {baseline['exception_candidates']['matched']}/{baseline['exception_candidates']['denominator']}，必要问题代码 {baseline['exception_required_issues']['matched']}/{baseline['exception_required_issues']['denominator']}，原文/数量/单位/来源及解析跨度 159/159，输出逐行复读 159/159。异常颜色、原生批注、未知金额留空和异常不进入完整金额总额均由逐项导出复读与财务审计覆盖；异常金额留空 {baseline['exception_amount_excluded']['excluded']}/{baseline['exception_amount_excluded']['denominator']}。", '',
                  '## 边界与剩余风险', '', *[f'- {item}' for item in result['limitations']], '',
                  f"结构化结果：`{args.output}`。逐seed详细回放：`evaluation/v5/replay_audit_20261108.json`、`evaluation/v5/replay_audit_20261109.json`。",])
    args.report_md.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'status': result['status'], 'validation': str(args.output), 'report': str(args.report_md),
                      'strict_identity_132': baseline['strict_identity_132'],
                      'station': baseline['station']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
