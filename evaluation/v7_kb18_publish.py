#!/usr/bin/env python3
"""Index KB18 evidence without overwriting immutable phase results."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'evaluation/v7'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def main():
    phases = {
        'baseline_development': 'kb18_transfer_baseline_dev',
        'final_development': 'kb18_transfer_final_dev',
        'first_holdout': 'kb18_transfer_final_holdout',
    }
    reports = {key: read(E / value / 'audit/report.json') for key, value in phases.items()}
    before = read(E / 'kb18_original_sha_before.json')
    after = {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT / 'original').iterdir()) if p.is_file()}
    assert before == after and len(after) == 8, 'original drift'
    write(E / 'kb18_original_sha_after.json', after)
    knowledge_path = ROOT / 'skills/order-mapping/knowledge/procurement.json'
    knowledge = read(knowledge_path)
    assert knowledge['knowledge_version'] == 18
    metrics = {key: report['mode_aggregates'] for key, report in reports.items()}
    automatic_correct = sum(metrics[key]['shared']['all']['automatic_correct']
                            for key in ('final_development', 'first_holdout'))
    checks = {
        'original_sha_equal': True,
        'raw_originals': read(E / 'original_kb18_final/report.json'),
        'merged_originals': read(E / 'original_kb18_final_combined/report.json'),
        'core_log': 'evaluation/v7/kb18_core_final.log',
        'scorer_log': 'evaluation/v7/kb18_scorer_tests.log',
        'independent_probe': read(E / 'kb18_independent_final.json'),
        'original_exception_probe': read(E / 'kb18_original_exception_probe.json'),
        'package': read(E / 'package_check_kb18_final/report.json'),
    }
    result = {
        'schema_version': 4, 'node': 'V7-10 / KB18', 'status': 'doing', 'verified': False,
        'knowledge_version': 18, 'knowledge_sha256': sha(knowledge_path),
        'knowledge_rules': {key: len(knowledge[field]) for key, field in
                            (('concept', 'rules'), ('unit', 'unit_rules'), ('expression', 'expression_rules'))},
        'metrics': metrics, 'group_metrics': {key: value['mode_aggregates'] for key, value in reports.items()},
        'old_regression': read(E / 'kb18_oldlane_regression.json'),
        'fixed_history': read(E / 'kb18_final_history_identity.json'),
        'denominators': {'original_events': 159, 'aligned': 155, 'current_catalog_human_sku': 132,
                         'stations': {'songbei': 53, 'xingyue': 42, 'xueyuan': 64},
                         'development': 64, 'first_holdout': 32, 'new_total': 96,
                         'independent_product_concepts': 12, 'development_products': 6,
                         'heldout_products': 6, 'seeds': [9101, 9102, 9191]},
        'checks': checks,
        'bounds': {'synthetic': {'events': 96, 'missing_quantity_or_unit': 24,
                                  'conditional_B_ceiling_percent': 75,
                                  'automatic_correct': automatic_correct,
                                  'B_percent': 100 * automatic_correct / 96,
                                  'not_real_business_prevalence': True},
                   'history': read(E / 'source_fact_limits_review.json')['summary']},
        'C': None,
        'reason': 'Frozen full B is below95%; missing facts are not guessed. Semantic candidate completeness and real new orders remain unverified.',
        'boundaries': ['A uses actual automatic outputs; extras enter A',
                       'B uses all frozen input events; failures and exceptions stay',
                       'Old holdouts are consumed; later executions are seen regression',
                       'Rule CLI evidence does not establish complete Agent semantics or host compatibility',
                       'Qwen Office, WorkBuddy, real new orders, imports and formal accounting unverified'],
    }
    agent_report = E / 'kb18_agent_semantic_audit/report.json'
    if agent_report.exists():
        result['agent_semantic_forward'] = read(agent_report)
    write(E / 'kb18_validation_result.json', result)
    write(E / 'validation_result.json', result)
    write(E / 'kb18_replay_audit.json', {'node': result['node'], 'status': 'doing', 'metrics': metrics,
                                       'source_reports': phases, 'C': None, 'original_sha_equal': True})
    simulation = E / 'kb18_final_simulation'
    write(simulation / 'manifest.json', {'node': result['node'], 'seeds': [9101, 9102, 9191],
                                       'development': [9101, 9102], 'first_holdout': 9191,
                                       'events': 96, 'product_concepts': 12,
                                       'knowledge_sha256': result['knowledge_sha256'],
                                       'manifests': [f'evaluation/v7/kb18_transfer_challenge/{s}/manifest.json'
                                                     for s in (9101, 9102, 9191)]})
    (simulation / 'README.md').write_text('KB18三seed索引。每seed32事件，开发与保留商品及模板分离。'
        '普通input只含公司、日期和聊天；expected、事件ID与来源manifest仅供评分。'
        '96合成事件含12商品概念，重复表达不是新增真实业务样本。首次保留消费后只作已见回归。\n')
    lines = ['# KB18共享采购知识优化与独立验证', '', '状态：doing，整体95%目标未达。', '',
             '| 轨道 | 模式 | 输入 | 自动 | 自动全正确 | A | B |', '|---|---|---:|---:|---:|---:|---:|']
    for key, values in metrics.items():
        for mode, value in values.items():
            value = value.get('all', value)
            lines.append(f"|{key}|{mode}|{value['events']}|{value['automatic']}|{value['automatic_correct']}|{value['A']}|{value['B']}|")
    lines.extend(['', 'A按实际自动输出，B按全部输入；正确转人工不计自动正确。'
        '各公司、场景和正常子集指标均保存在validation的group_metrics及final审计report.json中。'
        'C因语义候选全集尚未完整独立证明保持null。', '',
        '本轮修复完整属性关系、数量前置规格保护、显式刚才改/取消、同批当前目录商品身份关联，'
        '以及pending行计价量清空。包装异常不再把原文明示净重说成用户缺失。未加入逐商品历史SKU答案。', '',
        f'新挑战固定96条，其中24条人为缺数量或单位，条件性B上限75%；实现自动正确{automatic_correct}/96。'
        '该上限只适用于此组合，不能当成真实业务比例。历史17条硬事实缺口及142/159宽松上界详见source_fact_limits_review，'
        '它不是已实现率。', '',
        '首次新保留结果永不覆盖。旧9091首次失败保留，后续修复只算已见回归。'
        '历史SKU、人工实配量、旧价和人工行号不进入正常原文运行端。'
        '本轮以正式规则CLI、保存复读及独立评分为主；上轮本地Agent辅助另轨保留。', '',
        '本轮首次9191条件正常项0/24，四公司均0/6正常自动正确；全量0/32、A无分母、B=0%。'
        '开发48/48不能证明陌生表达泛化。“选干的”“请按…供货”“商品…要”等仍超出规则覆盖。'
        '相同的六商品需求在四公司下结果一致，公司不是失败原因。', '',
        '旧v2回归中的同SKU异名重复项因同批身份关联转待核：须区分控制链真实安全修复与额外自动覆盖下降。'
        '旧预期不因输出而改写，详细数量、逐项原因及潜在批次范围过大问题见oldlane报告。', '',
        '历史当前目录可核人工SKU132、全部对齐155、全部聊天159分母同时保留，见kb18_final_history_identity.json。'
        '当前原始三站需求草稿与合并财务另有159事件复读，不能把匹配状态当成独立自动正确。', '',
        '千问办公、WorkBuddy、真实新增订单、真实配送、系统导入及正式入账尚未验证。'
        '完整异常候选语义、C和全量A/B同时95未达。', '',
        '证据：kb18_validation_result.json、kb18_replay_audit.json、kb18_oldlane_regression.json、'
        'kb18逐项对比.xlsx、outputs/v7/kb18/README.md。'])
    (E / 'kb18_report.md').write_text('\n'.join(lines) + '\n')
    authority = ('<!-- KB18_AUTHORITY -->\n## 当前证据：V7-10 / KB18（doing）\n\n'
        '当前生产KB18；报告 `evaluation/v7/kb18_report.md`、结构化结果 `evaluation/v7/kb18_validation_result.json`。'
        '新三seed开发与首次保留分别报告，A/B以全量固定口径统计，C候选全集及整体95仍未证。'
        '交付索引 `outputs/v7/kb18/README.md`；KB17及之前为历史证据，不继承通过状态。\n'
        '<!-- KB18_AUTHORITY_END -->\n\n')
    for path in (ROOT / 'task_plan.md', ROOT / '执行清单.md', ROOT / 'evaluation/v7_report.md'):
        text = path.read_text()
        if '<!-- KB18_AUTHORITY -->' not in text:
            path.write_text(authority + text)
    out = ROOT / 'outputs/v7/kb18'
    out.mkdir(exist_ok=True)
    (out / 'README.md').write_text('# KB18订单及财务草稿索引\n\n状态doing。均为需求及财务草稿。\n\n'
        '- 新开发：../kb18_transfer_final_dev/；新首次保留：../kb18_transfer_final_holdout/。\n'
        '- 原始三站：../original_kb18_final/，含标准订单、财务和合并财务。\n'
        '- 旧seed具体输出路径：../../../evaluation/v7/kb18_oldlane_regression.json。\n'
        '- 逐项表：../../../evaluation/v7/kb18逐项对比.xlsx。\n'
        '- 报告：../../../evaluation/v7/kb18_report.md。\n'
        '- 技能包：../../../dist/order-mapping-v7-kb18-final.zip。\n\n'
        '首次保留消费后只作已见回归；真实宿主与真实新增订单尚未验证。\n')
    print(json.dumps({'status': 'doing', 'new_automatic_correct': automatic_correct,
                      'new_events': 96, 'knowledge_sha256': result['knowledge_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
