#!/usr/bin/env python3
"""Publish the v6 evidence report from the completed replay audits."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path

from v6_challenge_replay import is_normal_supported, summary
from v6_replay_audit import summarize as historical_summary

ROOT = Path(__file__).resolve().parents[1]
V6 = ROOT / 'evaluation/v6'
BASELINES = ('historical_rc1', 'challenge_rc1', 'extension_rc1')
IDENTITY_GROUP = '6108/single_station_split/shared'
SYNTHETIC_RUNS = V6 / 'kb7_challenge/runs'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fmt(value):
    return '未定义' if value is None else str(value)


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join(['---'] * len(headers)) + ' |'] +
                     ['| ' + ' | '.join(fmt(v).replace('|', '/') for v in row) + ' |' for row in rows])


def old_holdout_table():
    """Use the saved pre-KB7 report, without reinterpreting the spent holdout."""
    lines = (V6 / 'report_before_kb7.md').read_text(encoding='utf-8').splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith('| 首次保留阶段 |'))
    result = []
    for line in lines[header:]:
        if not line.startswith('|'):
            break
        result.append(line)
    if len(result) != 4:
        raise ValueError('预KB7报告的首次保留成绩表不完整')
    return '\n'.join(result)


def normal_by_company(phase, audit):
    rows = [r for r in audit['rows'] if r['mode'] == 'shared' and
            (is_normal_supported(r) if phase.startswith('challenge_') else
             bool(r.get('expected') and r['expected']['outcome'] == 'selected'))]
    overall = summary(rows)
    recorded = audit['normal_supported_metrics']['shared']
    if any(overall[key] != recorded[key] for key in ('input_events', 'automatic', 'automatic_correct', 'A', 'B')):
        raise ValueError(f'{phase} 正常子集分项与审计汇总不一致')
    companies = {name: summary([r for r in rows if r['company'] == name])
                 for name in sorted({r['company'] for r in rows})}
    return overall, companies


def markdown_link(path, start):
    return os.path.relpath(str(path), str(start)).replace(os.sep, '/')


def synthetic_summary(path, knowledge_hash, freeze_hash=None):
    audit = read(path)
    before, after = audit['before_sha256'], audit['after_sha256']
    if (not audit['integrity_passed'] or before != after or
            before['knowledge'] != knowledge_hash or
            (freeze_hash and audit['freeze_sha256'] != freeze_hash) or
            before['freeze'] != audit['freeze_sha256']):
        raise ValueError(f'合成样本审计未绑定冻结集或知识指纹: {path}')
    cases = audit['cases']
    if len({(r['seed'], r['case']) for r in cases}) != len(cases):
        raise ValueError(f'合成样本审计中存在重复案例: {path}')
    passed = sum(not r['errors'] for r in cases)
    if bool(audit['all_checks_passed']) != (passed == len(cases)):
        raise ValueError(f'合成样本审计整体与案例结果不一致: {path}')
    return audit, passed, len(cases)


def historical_knowledge_manifests(audit, knowledge_hash, contract_path):
    counts = {'shared': 0, 'shared_with_company_contract': 0, 'catalog_only': 0}
    without_manifests = 0
    contracts = read(contract_path)['contracts']
    contract_hash = sha(contract_path)
    for run in audit['runs']:
        result = read(Path(run['result_path']))
        manifests = result.get('manifests', [])
        if not manifests:
            without_manifests += 1
        for name in manifests:
            manifest = read(Path(name))
            ledger_file = manifest['files']['ledger']
            ledger_path = Path(ledger_file['path'])
            if sha(ledger_path) != ledger_file['sha256']:
                raise ValueError(f'历史回放台账SHA不一致: {ledger_path}')
            ledger = read(ledger_path)
            mode = run['mode']
            expected = None if mode == 'catalog_only' else knowledge_hash
            if mode == 'shared_with_company_contract':
                company_id = manifest['order']['company_id']
                ids = [rule['id'] for contract in contracts if contract['company_id'] == company_id
                       for rule in contract['rules']]
                encoded = (json.dumps([knowledge_hash, contract_hash, ids], ensure_ascii=False,
                                      sort_keys=True, indent=2, allow_nan=False) + '\n').encode('utf-8')
                expected = hashlib.sha256(encoded).hexdigest()
            if manifest.get('knowledge_sha256') != expected or ledger.get('knowledge_sha256') != expected:
                raise ValueError(f'历史回放知识SHA与模式不一致: {name}')
            counts[mode] += 1
    return counts, without_manifests


def extra_audit_text(path, audit, passed, total, base_freeze):
    failed = [f'{r["label"]}: {", ".join(r["errors"])}' for r in audit['cases'] if r['errors']]
    version = '与KB9首轮同一冻结版本' if audit['freeze_sha256'] == base_freeze else '冻结指纹与KB9首轮不同'
    details = f'；未通过：{"；".join(failed)}' if failed else ''
    return (f'[{audit["phase"]}]({markdown_link(path, ROOT / "evaluation")})'
            f'既定检查通过{passed}/{total}，{version}{details}。'
            f'冻结说明：{audit["freeze_context"].rstrip("。") }。该结果不能作为独立未见验收。')


def synthetic_metrics(path, audit):
    summary_path = path.with_name('summary.json')
    document = read(summary_path)
    m = document['all']
    if (Path(document['report']).resolve() != path.resolve() or
            document['freeze_sha256'] != audit['freeze_sha256'] or
            m['input_events'] != len(audit['cases'])):
        raise ValueError(f'合成审计与汇总未绑定: {summary_path}')
    c_value = '未确定' if m['C_percent'] is None else f'{m["C_percent"]}%'
    return (f'[指标汇总]({markdown_link(summary_path, ROOT / "evaluation")})：'
            f'自动正确/自动={m["automatic_correct"]}/{m["automatic"]}，'
            f'B={m["automatic_correct"]}/{m["input_events"]}；'
            f'C={c_value}，下界{m["C_lower_bound_percent"]}%；'
            f'待人工候选完整性未审定{m["manual_candidate_completeness_unverified"]}条。')


def main(phase_suffix, frozen_audit=None, extra_audits=(), package_phase=None):
    phases = tuple(f'{prefix}_{phase_suffix}' for prefix in ('historical', 'challenge', 'extension'))
    historical, challenge, extension = phases
    audits = {p: read(V6 / p / 'replay_audit.json') for p in phases}
    baseline = {p: read(V6 / p / 'replay_audit.json') for p in BASELINES}
    identity = read(V6 / historical / 'identity_audit.json')
    identity_before = read(V6 / 'historical_rc1/identity_audit.json')
    for phase, audit in {**audits, **baseline}.items():
        if audit.get('phase') != phase:
            raise ValueError(f'回放阶段与路径不一致: {phase}')
    if identity['replay']['sha256'] != sha(V6 / historical / 'replay_audit.json'):
        raise ValueError(f'{phase_suffix}身份审计未绑定{historical}回放')
    if identity_before['replay']['sha256'] != sha(V6 / 'historical_rc1/replay_audit.json'):
        raise ValueError('rc1身份审计未绑定当前historical_rc1回放')

    package_phase = package_phase or f'package_{phase_suffix}'
    if not re.fullmatch(r'package_[A-Za-z0-9_-]+', package_phase):
        raise ValueError('无效的解包验证阶段')
    package_dir = V6 / package_phase
    kb_path = package_dir / 'unpacked/order-mapping/knowledge/procurement.json'
    kb = read(kb_path)
    kb_hash = sha(kb_path)
    if kb['knowledge_version'] != {'rc3': 7, 'rc4': 8, 'rc5': 9, 'rc6': 10, 'rc7': 11, 'rc8': 12}[phase_suffix]:
        raise ValueError(f'{phase_suffix} 对应的解包知识版本不一致')
    for phase, key in ((challenge, 'source_code_sha256'), (extension, 'source_before')):
        fingerprint = audits[phase].get(key, {}).get('skills/order-mapping/knowledge/procurement.json')
        if fingerprint != kb_hash:
            raise ValueError(f'{phase} 运行知识指纹与当前KB不一致')
    contract_path = package_dir / 'unpacked/order-mapping/knowledge/company-contracts.json'
    hist_manifest_counts, hist_without_manifests = historical_knowledge_manifests(
        audits[historical], kb_hash, contract_path)

    package_path = package_dir / 'validation_result.json'
    package = read(package_path)
    archive = ROOT / 'dist/order-mapping-v6.zip'
    package_bound = (package.get('knowledge_sha256') == kb_hash and
                     archive.is_file() and package['package']['sha256'] == sha(archive))
    current_kb = ROOT / 'skills/order-mapping/knowledge/procurement.json'
    current_matches = current_kb.exists() and sha(current_kb) == kb_hash
    iteration_path = V6 / 'kb7_iteration.json'
    iteration = read(iteration_path) if iteration_path.exists() else {}
    current = identity['groups'][IDENTITY_GROUP]
    previous = identity_before['groups'][IDENTITY_GROUP]
    hist_now, hist_before = audits[historical]['metrics']['shared'], baseline['historical_rc1']['metrics']['shared']
    parts = [
        f'# v6 共享采购知识：{phase_suffix}结果与未达原因',
        f'状态 **doing / 整体验收未通过**。本阶段知识版本{kb["knowledge_version"]}，'
        f'包含{len(kb["rules"])}条概念规则和{len(kb.get("unit_rules", []))}条单位规则；'
        f'本报告从{phase_suffix}回放和身份审计读取结果，不把已消费的保留集回归称为独立盲测。'
        + ('该阶段知识与当前工作区一致。' if current_matches else '当前工作区已有不同版本，本文是阶段记录。'),
        'KB7覆盖干豆腐、蒜苔、甘蓝、海天上等耗油、玉米粒罐头和香蕉六类；'
        '普通干豆腐先选择未附加厚等形态的最接近层，在同计价基础内比当前价。'
        '明确加厚、红帽、牛心、小米蕉等要求仍须由目录证实。'
        '原文缺重量或包装依据时，商品身份与数量、金额分别处理。',
        '历史冻结oracle未更改：新规则使更多事件自动输出，旧预期为manual或undefined时不会被自动改判正确。'
        '因此自动数上升不保证A/B/C上升；这既不证明所有新增选择错误，也不能忽略冻结预期中的已知错误。',
        '## 1. 全量回放结果',
        'A=全部字段自动正确数/自动完成数；B=同一正确数/全部输入事件；'
        'C包括自动正确、可独立核定的人工处理以及正确的控制关系。'
        '控制事件留在分母；历史undefined不计正确；异常候选缺穷尽性证据时不算完整人工正确。',
    ]
    metric_rows = []
    for phase in phases:
        for mode, m in audits[phase]['metrics'].items():
            metric_rows.append([phase, mode, m['input_events'], m['automatic'], m['automatic_correct'],
                                m['A'], m['B'], m['C']])
    parts.append(table(['阶段', '模式', '全量事件', '自动完成', '自动正确', 'A%', 'B%', 'C%'], metric_rows))
    contribution = []
    for phase, audit in audits.items():
        catalog, shared, contract = (audit['metrics'][mode] for mode in
                                     ('catalog_only', 'shared', 'shared_with_company_contract'))
        contribution.append([phase, shared['automatic_correct'] - catalog['automatic_correct'],
                             round(shared['B'] - catalog['B'], 4),
                             contract['automatic_correct'] - shared['automatic_correct']])
    parts += ['同一套冻结输入下，共享知识对全量自动正确覆盖的贡献如下；公司特殊约定当前为空，'
              '不能将空配置的零增益解释为已验证所有公司特例。',
              table(['阶段', '共享新增自动正确', 'B提升百分点', '公司约定额外自动正确'], contribution)]
    coverage = {}
    for phase, audit in audits.items():
        rows = [row for row in audit['rows'] if row['mode'] == 'shared']
        records = []
        for row in rows:
            expected = row.get('expected') or {}
            record = expected.get('selected') or expected
            if record.get('sku') or record.get('code'):
                records.append(record)
        runs = [run for run in audit['runs'] if run['mode'] == 'shared']
        coverage[phase] = {
            'audit_sha256': sha(V6 / phase / 'replay_audit.json'),
            'seeds': audit['seeds'], 'raw_message_requests': len(runs),
            'exported_orders': sum(run['audit']['stats'].get('manifest_count', 0) for run in runs),
            'replay_event_occurrences': len(rows),
            'expected_catalog_names': sorted({record['name'] for record in records}),
            'expected_catalog_skus': sorted({str(record.get('sku') or record.get('code')) for record in records}),
            'genuine_new_customer_events': 0,
            'interpretation': '目录预期覆盖；相同事件、名称、SKU的重复变体不是新增真实业务样本。',
        }
    coverage_path = V6 / 'product_coverage.json'
    coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    parts += ['订单数和商品范围以下表及[覆盖明细](v6/product_coverage.json)为准。'
              '目录更新可令同名商品对应不同SKU；这些计数不表示独立真实新订单数量。',
              table(['阶段', 'seed', '原文请求', '导出订单', '回放事件次数', '预期商品名数', '预期SKU数'],
                    [[phase, ','.join(map(str, item['seeds'])), item['raw_message_requests'],
                      item['exported_orders'], item['replay_event_occurrences'],
                      len(item['expected_catalog_names']), len(item['expected_catalog_skus'])]
                     for phase, item in coverage.items()])]
    fixed_rows = [row for row in audits[historical]['rows'] if row['seed'] == 6108 and
                  row['scenario'] == 'single_station_split' and row['mode'] == 'shared']
    if (len(fixed_rows) != 159 or len({row['source_event_id'] for row in fixed_rows}) != 159 or
            {row['source_event_id'] for row in fixed_rows} != {row['event_id'] for row in current['events']}):
        raise ValueError('固定原始159事件不完整或重复')
    fixed_companies = {company: historical_summary([row for row in fixed_rows if row['company'] == company])
                       for company in sorted({row['company'] for row in fixed_rows})}
    if {name: item['input_events'] for name, item in fixed_companies.items()} != {
            'songbei': 53, 'xingyue': 42, 'xueyuan': 64}:
        raise ValueError('固定159事件分站分母不一致')
    fixed = {'audit_sha256': sha(V6 / historical / 'replay_audit.json'),
             'seed': 6108, 'scenario': 'single_station_split', 'mode': 'shared',
             'overall': historical_summary(fixed_rows), 'by_company': fixed_companies,
             'scope': '原159事件各一次的已见历史回归；沿用冻结预期，undefined不算正确，不是独立新订单验收。'}
    fixed_path = V6 / historical / 'fixed_159_full_fields.json'
    fixed_path.write_text(json.dumps(fixed, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    parts += ['### 固定159条原始事件的全字段结果',
              '以下取6108同站拆分、shared口径，每条原始事件恰好一次；'
              '不含附加目录挑战，不能用1034次混合回放代替该分母。'
              f'[逐站结构化结果]({markdown_link(fixed_path, ROOT / "evaluation")})。'
              '仍沿用冻结预期，未裁定的自动输出保守不计正确。',
              table(['公司', '原始事件', '自动完成', '自动正确', 'A%', 'B%', 'C%'],
                    [[name, item['input_events'], item['automatic'], item['automatic_correct'],
                      item['A'], item['B'], item['C']]
                     for name, item in [('全部', fixed['overall']), *fixed_companies.items()]])]
    compare = []
    for old, new in zip(BASELINES, phases):
        for phase, audit in ((old, baseline[old]), (new, audits[new])):
            m = audit['metrics']['shared']
            compare.append([phase, m['input_events'], m['automatic'], m['automatic_correct'], m['A'], m['B'], m['C']])
    parts += ['### 与rc1共享模式对比',
              table(['阶段', '事件', '自动', '自动正确', 'A%', 'B%', 'C%'], compare),
              f'历史共享回放从自动{hist_before["automatic"]}增加到{hist_now["automatic"]}，'
              f'冻结预期下自动正确由{hist_before["automatic_correct"]}变为{hist_now["automatic_correct"]}；'
              f'自动但预期undefined从{hist_before["automatic_undefined"]}增加到{hist_now["automatic_undefined"]}。'
              f'当前已裁定自动{hist_now["known_automatic"]}条中正确{hist_now["known_automatic_correct"]}条、'
              f'未通过{hist_now["known_automatic"] - hist_now["known_automatic_correct"]}条，'
              f'A_known={hist_now["A_known"]}%仅是该子集诊断，不能代替包含undefined的全量A={hist_now["A"]}%。',
              '历史回放有两个seed、多个变形及压力场景；1034是回放事件次数，'
              '不能当成1034条独立真实原文。159条历史原始事件、155条人工对齐事件及132个可核人工SKU分母均保留。',
              '## 2. 分公司、场景与正常子集']
    for phase, audit in audits.items():
        parts += ['### ' + phase,
                  table(['公司', '事件', '自动正确', 'A%', 'B%', 'C%'],
                        [[name, m['input_events'], m['automatic_correct'], m['A'], m['B'], m['C']]
                         for name, m in audit['by_company']['shared'].items()]),
                  table(['场景', '事件', '自动正确', 'A%', 'B%', 'C%'],
                        [[name, m['input_events'], m['automatic_correct'], m['A'], m['B'], m['C']]
                         for name, m in audit['by_scenario']['shared'].items()])]
        if phase != historical:
            normal, companies = normal_by_company(phase, audit)
            parts += ['下表为冻结预期支持的正常子集，不能替代全量分母或整体验收：',
                      table(['公司', '正常事件', '自动正确', 'A%', 'B%'],
                            [[name, m['input_events'], m['automatic_correct'], m['A'], m['B']]
                             for name, m in companies.items()]),
                      f'正常子集微平均B={normal["B"]}%，自动正确{normal["automatic_correct"]}/{normal["input_events"]}。']
    parts += ['## 3. 固定历史人工身份一致率',
              '这是与历史人工表的SKU一致率，不等于A/B/C，也不把人工表作为独立商品语义真值。'
              '以下当前与对照均为6108/single_station_split/shared；分母固定。',
              table(['阶段', '口径', '一致/分母', '比例%'],
                    [[phase, key, f'{metric["correct"]}/{metric["denominator"]}', metric['rate_percent']]
                     for phase, group in (('historical_rc1', previous), (historical, current))
                     for key, metric in group['metrics'].items()
                     if isinstance(metric, dict) and 'denominator' in metric]),
              table(['站点', f'{phase_suffix}可核SKU一致/分母', '严格一致率%'],
                    [[name, f'{m["strict132"]["correct"]}/{m["strict132"]["denominator"]}',
                      m['strict132']['rate_percent']] for name, m in current['per_station'].items()]),
              '各seed和主场景的严格身份结果：',
              table(['seed/主场景', '一致/分母', '比例%', '未选', '不同SKU'],
                    [[name, f'{g["metrics"]["strict132"]["correct"]}/{g["metrics"]["strict132"]["denominator"]}',
                      g['metrics']['strict132']['rate_percent'], g['metrics']['fixed_sku_outcomes']['unselected'],
                      g['metrics']['fixed_sku_outcomes']['different_sku']]
                     for name, g in identity['groups'].items() if name.endswith('/shared')]),
              '未选和不同SKU既可能涉及知识或解析不足，也可能是需要保留的商品属性、包装或换算限制；'
              '应逐项核对，不能因人工单有SKU就把它当作正确答案。'
              f'详情：[{phase_suffix}身份审计](v6/{historical}/identity_audit.json)、'
              '[rc1身份审计](v6/historical_rc1/identity_audit.json)。']
    old_events = {r['event_id']: r for r in previous['events']}
    changed = [r for r in current['events'] if old_events[r['event_id']]['actual_code'] is None and r['actual_code']]
    cabbage = [r for r in current['events'] if r.get('source_raw_text') == '甘蓝3颗']
    if len(cabbage) != 1:
        raise ValueError('固定身份审计中甘蓝3颗事件不唯一')
    cabbage_result = ('仍未选' if cabbage[0]['actual_code'] is None else
                      f'选中SKU {cabbage[0]["actual_code"]}（{cabbage[0]["identity_outcome"]}）')
    parts += [f'在上述6108拆分口径中，{len(changed)}条从未选变为有SKU：'
              f'{sum(r["identity_outcome"] == "correct" for r in changed)}条与历史人工身份一致，'
              f'{sum(r["identity_outcome"] == "different_sku" for r in changed)}条不同；'
              f'“甘蓝3颗”{cabbage_result}；颗到重量不能猜测。'
              '六类九事件是审阅范围，不等于九条历史事件都已选定。',
              '[79条未选审阅](v6/historical_remaining_review.md)及'
              '[六类二次复核](v6/generalization_second_review.md)为静态开发审查；'
              '二次审查曾将加厚干豆腐纳入比较，KB7实施时按普通整品最接近层排除加厚，'
              '这两种口径在当前目录选择相同，但不得误称规则完全一致。',
              '## 4. 首次保留失效与测试隔离',
              '首次7291暴露两条紧邻前置数量消息漏拆；首次7391暴露数字规格80*100与5kg边界。'
              '这些样本已用于修复并成为已见回归，不能用于独立未见验收。'
              '各seed共享商品或表达池，重排、数量变化不等于新词汇或真实客户数据。'
              '首次成绩来自[KB7前报告快照](v6/report_before_kb7.md)，保留原口径：',
              old_holdout_table(),
              '[首次挑战审计](v6/challenge_holdout_final/replay_audit.json) · '
              '[首次扩展审计](v6/extension_holdout_final/replay_audit.json) · '
              '[验收与隔离契约](v6/acceptance_contract.md)。',
              '运行输入是聊天原文、用户确认的公司日期、当前目录和正式知识；'
              '历史人工选择、评测预期和来源事件ID不注入运行时。'
              f'{phase_suffix}在这些已观察样本上的结果只能作为回归，不是独立保留验收。',
              '## 5. 运行完整性与验证范围']
    checks = []
    for phase, audit in audits.items():
        errors = audit.get('execution_errors', [])
        errors = len(errors) if isinstance(errors, list) else errors
        m = audit['metrics']['shared']
        original_unchanged = audit.get('original_sha_unchanged', audit.get('source_sha_unchanged'))
        checks.append([phase, errors, audit.get('audit_error_count'), m.get('missing_outputs'),
                       m.get('unexpected_outputs'), original_unchanged])
    parts += [table(['阶段', '执行错误', '审计错误', '共享漏输出', '共享额外输出', '原件哈希不变'], checks),
              '上表仅描述已运行的回放，不能代表验收覆盖完整。'
              f'{historical}审计的integrity_passed={audits[historical]["verification_gate"]["integrity_passed"]}，'
              f'原因是{", ".join(audits[historical]["verification_gate"]["integrity_reasons"])}；'
              '当前历史回放只有6108和6109，冻结验收口径还要求第三个seed。'
              f'业务预期通过={audits[historical]["verification_gate"]["business_expectations_passed"]}，'
              f'整体验收={audits[historical]["verification_gate"]["acceptance_verified"]}。',
              '台账、标准订单、财务草稿、原生批注与汇总的回读验证是机械一致性证据，'
              '不能证明候选语义穷尽或真实财务验收。',
              f'本阶段知识版本指纹：`{kb_hash}`；挑战和扩展回放及解包验证记录直接绑定该指纹。'
              '历史身份审计绑定历史回放文件SHA；历史回放顶层没有知识SHA，'
              f'但已检查其结果引用的{hist_manifest_counts["shared"]}个shared和'
              f'{hist_manifest_counts["shared_with_company_contract"]}个shared_with_company_contract manifest与台账，'
              '分别与知识SHA、知识和公司约定的组合SHA一致，且台账文件哈希吻合；'
              f'{hist_manifest_counts["catalog_only"]}个catalog_only manifest按模式记录空知识SHA。'
              f'未生成manifest的回放请求数为{hist_without_manifests}。']
    if package_bound:
        test_log = package_dir / 'tests.log'
        match = re.search(r'^Ran (\d+) tests? in [^\n]+\n\n(OK(?: \(skipped=\d+\))?)$',
                          test_log.read_text(encoding='utf-8'), re.MULTILINE)
        test_status = f'解包测试记录{match.group(1)}项，结果{match.group(2)}' if match else '解包测试记录未能解析'
        parts.append(f'当前技能包与[{package_phase}验证](v6/{package_phase}/validation_result.json)'
                     f'的包及知识SHA一致；[解包测试日志](v6/{package_phase}/tests.log){test_status}。'
                     f'原文CLI烟测{package.get("raw_cli_smoke_events", "未记录")}事件；'
                     '这些是本地验证，不代表真实宿主通过。')
    else:
        parts.append(f'当前技能包或知识SHA与{package_phase}验证未绑定，包验证状态需重新确认。')
    if iteration and phase_suffix == 'rc3':
        parts.append(f'新增独立合成样本状态：{iteration.get("new_seeds_status", "未记录")}；'
                     '此项不计入历史分母或rc3成绩，最终结果须以独立审计为准。'
                     '见[KB7迭代记录](v6/kb7_iteration.json)。')
    if phase_suffix in ('rc4', 'rc5', 'rc6', 'rc7', 'rc8'):
        first_path = SYNTHETIC_RUNS / 'kb7_first/20261108T050946Z-138f5e47/report.json'
        kb8_path = SYNTHETIC_RUNS / 'kb8_same_frozen_rerun/20261108T051317Z-b3ddb515/report.json'
        first, first_passed, first_total = synthetic_summary(first_path, sha(V6 / 'procurement_kb7_snapshot.json'))
        kb8, kb8_passed, kb8_total = synthetic_summary(kb8_path, sha(V6 / 'procurement_kb8_snapshot.json'),
                                                       first['freeze_sha256'])
        first_8101 = [r for r in first['cases'] if r['seed'] == '8101']
        register_failed = sum(r['errors'] == ['catalog_register_failed'] for r in first['cases'])
        parts.append('KB8针对独立合成首轮暴露的问题补充普通前缀和合法XLSX缺少dimension的处理；'
                     f'[首轮审计]({markdown_link(first_path, ROOT / "evaluation")})中8101通过'
                     f'{sum(not r["errors"] for r in first_8101)}/{len(first_8101)}，'
                     f'另{register_failed}例目录注册失败；合计通过{first_passed}/{first_total}。'
                     f'[KB8同冻结重跑]({markdown_link(kb8_path, ROOT / "evaluation")})'
                     f'通过{kb8_passed}/{kb8_total}。首次结果已用于修复，重跑属于已消费回归，'
                     '不能作为首次未见验收；三个seed共享表达池，并非真实客户数据。')
        if phase_suffix in ('rc6', 'rc7', 'rc8'):
            kb9_path = SYNTHETIC_RUNS / 'kb9_final_regression/20261108T052123Z-aa133727/report.json'
            kb9_hash = sha(V6 / 'package_rc5/unpacked/order-mapping/knowledge/procurement.json')
            kb9, kb9_passed, kb9_total = synthetic_summary(kb9_path, kb9_hash, first['freeze_sha256'])
            parts.append(f'[KB9同冻结重跑]({markdown_link(kb9_path, ROOT / "evaluation")})'
                         f'通过{kb9_passed}/{kb9_total}，属于已消费回归。')
            extra_first_path = V6 / 'kb9_extra/runs/kb9_extra_first/20261108T052044Z-e24dcb32/report.json'
            extra_first, extra_passed, extra_total = synthetic_summary(extra_first_path, kb9_hash)
            failed = [r for r in extra_first['cases'] if r['errors']]
            if len(failed) != 1 or failed[0]['label'] != '下五花肉2斤（去皮）':
                raise ValueError('KB9额外反例首轮审计结果与引用不一致')
            parts.append(f'[KB9额外反例首轮]({markdown_link(extra_first_path, ROOT / "evaluation")})'
                         f'通过{extra_passed}/{extra_total}；“下五花肉2斤（去皮）”处于待核状态且金额为空，'
                         f'但仍选了普通SKU {failed[0]["selected_code"]}，触发prohibited_selected。'
                         '该结果推动后续修复，此测试不代表真实未见业务验收。')
        if phase_suffix in ('rc7', 'rc8'):
            kb10_hash = sha(V6 / 'package_rc6/unpacked/order-mapping/knowledge/procurement.json')
            kb10_path = SYNTHETIC_RUNS / 'kb10_final_regression/20261108T052504Z-513db6d0/report.json'
            kb10, kb10_passed, kb10_total = synthetic_summary(kb10_path, kb10_hash, first['freeze_sha256'])
            parts.append(f'[KB10同冻结重跑]({markdown_link(kb10_path, ROOT / "evaluation")})'
                         f'通过{kb10_passed}/{kb10_total}，属于已消费回归。')
            for kb10_extra_path in (
                    V6 / 'kb9_extra/runs/kb10_v1_unchanged/20261108T052445Z-0ea890cf/report.json',
                    V6 / 'kb9_extra/v2/runs/kb10_v2_evidence_revision/20261108T052455Z-eee333ab/report.json'):
                extra, passed, total = synthetic_summary(kb10_extra_path, kb10_hash)
                parts.append(extra_audit_text(kb10_extra_path, extra, passed, total,
                                              extra_first['freeze_sha256']))
        if frozen_audit:
            frozen, frozen_passed, frozen_total = synthetic_summary(frozen_audit, kb_hash, first['freeze_sha256'])
            if not frozen['phase'].startswith({'rc5': 'kb9', 'rc6': 'kb10', 'rc7': 'kb11', 'rc8': 'kb12'}.get(phase_suffix, 'INVALID')):
                raise ValueError('最终合成审计阶段与所选回放不一致')
            parts.append(f'[KB{kb["knowledge_version"]}同冻结重跑]({markdown_link(frozen_audit, ROOT / "evaluation")})'
                         f'既定检查通过{frozen_passed}/{frozen_total}；该结果同样是已消费回归。')
            parts.append(synthetic_metrics(frozen_audit, frozen))
        elif phase_suffix in ('rc5', 'rc6', 'rc7', 'rc8'):
            parts.append(f'KB{kb["knowledge_version"]}同冻结重跑结果尚未纳入本报告，需以完成后的审计为准。')
        for extra_path in extra_audits:
            if phase_suffix not in ('rc6', 'rc7', 'rc8'):
                raise ValueError('额外反例重跑仅用于rc6、rc7或rc8')
            extra, passed, total = synthetic_summary(extra_path, kb_hash)
            parts.append(extra_audit_text(extra_path, extra, passed, total, extra_first['freeze_sha256']))
            parts.append(synthetic_metrics(extra_path, extra))
    if phase_suffix in ('rc5', 'rc6', 'rc7', 'rc8'):
        parts.append('KB9增加普通下五花肉、木耳、红肠和粉肠的受约束共享规则；'
                     '普通层不跨越明确品牌、品种或加工备注。'
                     '该阶段仍以对应回放和独立样本的正式审计结果为准。')
    if phase_suffix in ('rc7', 'rc8'):
        if phase_suffix == 'rc7':
            parts.append('否定去皮或要求带皮的处理仅覆盖本次验证到的局部表达，'
                         '不证明所有否定语义均已覆盖。额外发现“不要去皮”误报order_event提示，'
                         '但事件为空、状态active、金额为空；未造成取消或改量。'
                         '这一语义错误未被3/3机械检查覆盖，不计为C的人工正确；'
                         '[反例复核](v6/kb9_extra/review.md)记录了复现和预期修订依据。'
                         '完整宿主Agent语义流程未验收。')
        else:
            parts.append('rc8修复加工备注“不要去皮”被误报为订单控制的问题；'
                         '独立加工备注不再产生order_event，真实取消、追加和改量继续保留。'
                         '仅覆盖已有反例范围，不证明所有否定语义。'
                         '[红测](v6/rc8_parser_red.log)、[核心回归](v6/rc8_parser_core.log)'
                         '和[修复指纹](v6/rc8_parser_fingerprints_after.json)保留前后证据。'
                         'KB12将北极冰虾80*100的过度待核改为有约束自动选择：'
                         '未声明鲜活不等于拒绝目录冷冻项，同品种、同规格、质量计价方可选择；'
                         '明确鲜活、非冷冻、品牌及规格冲突继续阻断，袋重不推定。'
                         '生腰果仁的去壳依据仍缺失，继续待核。'
                         '旧oracle未修改，此轮仍为已消费合成回归，完整宿主Agent语义未验收。')
        limits_path = V6 / 'synthetic_denominator_limits.json'
        limits = read(limits_path)['datasets']
        limits_rows = []
        for name in (challenge, extension):
            limit = limits[name]
            if (limit['sha256'] != sha(V6 / name / 'replay_audit.json') or
                    limit['total'] != audits[name]['metrics']['shared']['input_events'] or
                    limit['actual_B'] != audits[name]['metrics']['shared']['B'] or
                    sum(limit['expected_outcome_counts'].values()) != limit['total'] or
                    limit['loose_max_auto_correct_events'] != limit['expected_outcome_counts']['selected']):
                raise ValueError(f'{name}合成分母上界未绑定回放或数据不一致')
            limits_rows.append([name, limit['total'], limit['loose_max_auto_correct_events'],
                                limit['loose_max_B_percent'], limit['actual_B']])
        parts += ['冻结合成样本有意混合异常和控制事件，在保留原有全分母与预期的前提下，'
                  '仅按非selected排除得到下列宽松B上界；selected仍可能包含inactive控制，'
                  '因此该值不代表可达率。上界低于95%反映测试构造和口径，不能据此改变分母或预期。'
                  f'证据见[合成分母限制]({markdown_link(limits_path, ROOT / "evaluation")})。',
                  table(['数据集', '全量事件', 'selected预期', '宽松B上界%', '实际B%'], limits_rows),
                  '扩展正常子集本身的B也未达95%，不能用正常子集成绩冒充全量成绩。']
    parts += ['## 6. 剩余问题与下一步',
              '历史原文与当前目录的[事实缺口审计](v6/source_fact_limits.md)列出26条已证明缺少完整自动订单所需事实；'
              '在该审计的假设下，133/159是排除已证明缺口后的宽松上界，不是可达率，也不是剩余133条已正确。'
              '不能把旧42/159或92/159分类当作能力上限。',
              '继续复核身份差异和全部合理候选；对已确认的同义与等价规则增加来源、反例及目录变更验证。'
              '不把历史实配量、旧价或人工SKU倒灌为通用规则，也不要求用户重复提供已有材料。'
              '详细因素见[准确率风险](v6/accuracy_risks.md)。',
              '当前尚无真实新增客户订单、真实宿主Agent语义验收或正式财务验收；'
              '正常子集和自动行准确率均不能替代全量B或整体95%目标。',
              '## 7. 交付与证据索引',
              '[技能包](../dist/order-mapping-v6.zip) · [技能入口](../skills/order-mapping/SKILL.md) · '
              '[逐项工作簿](v6逐项对比.xlsx) · [订单与财务草稿](../outputs/v6/README.md)。',
              f'[{historical}](v6/{historical}/replay_audit.json) · '
              f'[{challenge}](v6/{challenge}/replay_audit.json) · '
              f'[{extension}](v6/{extension}/replay_audit.json)。'
              f'[validation_result.json](v6/validation_result.json)为聚合记录，不替代上述{phase_suffix}逐项审计。']
    if phase_suffix == 'rc8':
        parts.append('[KB12迭代记录](v6/kb12_iteration.json) · '
                     '[规则裁定与反例](v6/kb12_knowledge_review.md) · '
                     '[原件交付后SHA](v6/kb12_original_sha_delivery_after.json)。')

    index = ['# v6 当前标准订单与财务草稿',
             f'本阶段证据为KB{kb["knowledge_version"]}及{historical}、{challenge}、{extension}；'
             '整体95%目标尚未通过。7291/7391已消费，当前结果是已见回归。'
             '财务文件是需求预估草稿，不用于正式入账。',
             '[完整报告](../../evaluation/v6_report.md) · '
             f'[{phase_suffix}历史身份审计](../../evaluation/v6/{historical}/identity_audit.json) · '
             '[逐项Excel](../../evaluation/v6逐项对比.xlsx)',
             '## 当前批次与代表性输出',
             '链接指向实际CLI输出；订单包含待核行和原生批注，未把草稿包装成正式财务。']
    for phase, audit in audits.items():
        runs = [r for r in audit['runs'] if r.get('mode') == 'shared']
        if not runs:
            raise ValueError(f'{phase} 缺共享模式运行')
        best = max(runs, key=lambda r: r['audit']['stats'].get('matched', 0))
        result_path = Path(best['result_path'])
        result = read(result_path)
        count = sum(r['audit']['stats'].get('manifest_count', 0) for r in runs)
        index += ['### ' + phase,
                  f'共享模式{len(runs)}个原文请求，导出{count}份分公司/日期订单；'
                  f'回放事件{audit["metrics"]["shared"]["input_events"]}。',
                  f'[结果及来源台账]({markdown_link(result_path, ROOT / "outputs/v6")})']
        for manifest_path in result.get('manifests', [])[:2]:
            manifest_path = Path(manifest_path)
            manifest = read(manifest_path)
            files = manifest['files']
            index.append(f'- [标准订单]({markdown_link(Path(files["standard"]["path"]), ROOT / "outputs/v6")}) · '
                         f'[财务草稿]({markdown_link(Path(files["finance"]["path"]), ROOT / "outputs/v6")}) · '
                         f'[批次manifest]({markdown_link(manifest_path, ROOT / "outputs/v6")})')
        merged = result.get('merged_finance') or {}
        finance_file = merged.get('files', {}).get('finance', {}).get('path')
        if finance_file:
            index.append(f'[合并财务草稿]({markdown_link(Path(finance_file), ROOT / "outputs/v6")})'
                         f'（{merged["order_count"]}单、{merged["row_count"]}行，'
                         f'已确认金额小计{merged["total"]}）')
    index += ['## 证据边界',
              f'固定历史人工SKU一致率为{current["metrics"]["strict132"]["correct"]}/'
              f'{current["metrics"]["strict132"]["denominator"]}；与全字段A/B/C分开报告。'
              '旧v5公司profile拟合结果不等同当前共享流程。'
              '正常子集、合成表达池及已消费保留集均不证明真实客户泛化。',
              '旧阶段和首次失败结果均保留，目录名称本身不证明样本未见。'
              '真实客户、宿主Agent与正式财务未验收。']

    (ROOT / 'evaluation/v6_report.md').write_text('\n\n'.join(parts) + '\n', encoding='utf-8')
    (ROOT / 'outputs/v6/README.md').write_text('\n\n'.join(index) + '\n', encoding='utf-8')
    print(json.dumps({'report': 'evaluation/v6_report.md', 'status': 'doing',
                      'phases': phases, 'knowledge_version': kb['knowledge_version'],
                      'knowledge_sha256': kb_hash, 'package_bound': package_bound}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('rc3', 'rc4', 'rc5', 'rc6', 'rc7', 'rc8'), default='rc3',
                        help='选择一组已完成的回放、身份审计及解包验证')
    parser.add_argument('--frozen-audit', type=Path, help='当前知识版本同冻结样本的正式审计JSON')
    parser.add_argument('--package-phase', help='明确绑定的解包验证阶段；默认package_<phase>')
    parser.add_argument('--extra-audit', type=Path, action='append', default=[], help='当前知识版本额外反例的正式审计JSON')
    args = parser.parse_args()
    main(args.phase, args.frozen_audit, args.extra_audit, args.package_phase)
