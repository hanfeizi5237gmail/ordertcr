import json
from pathlib import Path

ROOT = Path('/Users/hanfei/Documents/workspace/smartcode/ordertcr')
EVAL = ROOT / 'evaluation/v7'
AUDIT = json.loads((EVAL / 'kb19_current_r4/audit/report.json').read_text(encoding='utf-8'))
STATE = json.loads((EVAL / 'kb19_current_r4/execution_status.json').read_text(encoding='utf-8'))
FREEZE = json.loads((EVAL / 'kb19_current_r4/input_freeze.json').read_text(encoding='utf-8'))
R2 = json.loads((EVAL / 'kb19_current_r2/audit/report.json').read_text(encoding='utf-8'))
OLDLANE = json.loads((EVAL / 'kb19_oldlane_r2/audit/report.json').read_text(encoding='utf-8'))
FIXED = json.loads((EVAL / 'kb19_original_r2/fixed_identity.json').read_text(encoding='utf-8'))
SOURCE_FACT = json.loads((EVAL / 'kb19_source_fact_recheck.json').read_text(encoding='utf-8'))
ORIGINAL = json.loads((EVAL / 'kb19_original_r2/report.json').read_text(encoding='utf-8'))
COMBINED = json.loads((EVAL / 'kb19_original_combined_r2/report.json').read_text(encoding='utf-8'))
PACKAGE = json.loads((EVAL / 'package_check_kb19_final/report.json').read_text(encoding='utf-8'))
LATE = json.loads((EVAL / 'kb19_late_final_r2/audit/report.json').read_text(encoding='utf-8'))
INTEGRITY = json.loads((EVAL / 'kb19_final_integrity.json').read_text(encoding='utf-8'))
MODES = ['catalog_only', 'shared', 'shared_with_company_contract']

def split(seed):
    return 'first_holdout' if seed == 9291 else 'development'

def compact(a):
    return {
        'events': a['events'], 'automatic': a['automatic'],
        'automatic_correct': a['automatic_correct'], 'correct': a['correct'],
        'business_correct': a['business_correct'], 'A': a['A'], 'B': a['B'],
        'business_processing_rate': a['business_processing_rate'],
        'review_correct': a['review_correct'], 'control_correct': a['control_correct'],
        'control_safe': a['control_safe'], 'C': a['C'], 'C_status': 'unknown: complete exception contract/semantic candidate set not verified',
    }

run_rows = []
for run in AUDIT['runs']:
    a = run['aggregate']
    run_rows.append({
        'seed': run['seed'], 'split': split(run['seed']), 'mode': run['mode'],
        'all': compact(a['all']), 'normal': compact(a['normal']),
        'finance_errors': len(run['finance_audit']['errors']),
        'failure_categories': run['failure_categories'],
    })

mode_metrics = {}
for mode in MODES:
    a = AUDIT['mode_aggregates'][mode]
    mode_metrics[mode] = {
        'all': compact(a['all']), 'normal': compact(a['normal']),
        'by_company': {k: compact(v) for k, v in a['by_company'].items()},
        'by_scenario': {k: compact(v) for k, v in a['by_scenario'].items()},
    }

finance_errors = sum(x['finance_errors'] for x in run_rows)
original_before = (EVAL / 'kb19_original_sha_before_round1.txt').read_text(encoding='utf-8')
original_after = (EVAL / 'kb19_original_sha_after_round2.txt').read_text(encoding='utf-8')
original_sha_equal = original_before == original_after

replay = {
    'schema_version': 1,
    'node': 'V7-11 / KB19',
    'stage': 'current_r4',
    'status': 'doing',
    'scope': 'KB19 frozen conditional challenge; current_r4 replay only, not real customer acceptance',
    'fixture': 'evaluation/v7/kb19_challenge',
    'seeds': [9201, 9202, 9291],
    'events_per_seed': 32,
    'event_split': {'normal_conditional': 24, 'review_or_exception': 8},
    'business_order_groups': {'current_r4_shared_three_seeds': 84, 'late_9391_shared': 32, 'counting_rule': 'company_id + shipping_date groups from frozen manifests; repeated modes/seeds are not new business orders'},
    'independent_concepts': {'current_r4': 12, 'late_9391': 6, 'total_distinct_directory_concepts': 18, 'counting_rule': 'concept reuse across modes/seeds and message variants is not a new concept'},
    'modes': MODES,
    'mode_metrics': mode_metrics,
    'prior_r2': {mode: {'all': compact(R2['mode_aggregates'][mode]['all']), 'normal': compact(R2['mode_aggregates'][mode]['normal'])} for mode in MODES},
    'runs': run_rows,
    'conditional_undefined_events': AUDIT['conditional_undefined_events'],
    'C': None,
    'C_definition': '全量业务处理正确率 = 自动正确 + 确应人工且正确保留/标色/批注，再除以全量输入',
    'C_reason': '异常完整合同与候选语义全集未证，当前 C 保持 null',
    'source_mismatches': AUDIT['source_mismatches'],
    'execution_status': STATE['status'],
    'source_equal': STATE['source_equal'],
    'original_sha_equal': original_sha_equal,
    'finance_errors_total': finance_errors,
    'evidence': {
        'audit_report': 'evaluation/v7/kb19_current_r4/audit/report.json',
        'audit_report_md': 'evaluation/v7/kb19_current_r4/audit/report.md',
        'freeze': 'evaluation/v7/kb19_current_r4/input_freeze.json',
        'execution_status': 'evaluation/v7/kb19_current_r4/execution_status.json',
        'prior_r2_audit': 'evaluation/v7/kb19_current_r2/audit/report.json',
        'control_boundary_review': 'evaluation/v7/kb19_control_boundary_review.json',
        'control_boundary_green_r2': 'evaluation/v7/kb19_control_boundary_green_r2.log',
        'expression_green_r2': 'evaluation/v7/kb19_expression_green_r2.log',
        'core_tests_final': 'evaluation/v7/kb19_r3_gate/core_tests_final.log',
        'final_integrity': 'evaluation/v7/kb19_final_integrity.json',
        'risk_analysis': 'evaluation/v7/kb19_accuracy_risk_analysis.md',
    },
    'limitations': [
        'A uses actual automatic output; review rows are not automatic successes.',
        'B keeps all frozen events in the denominator, including 8 review/exception events per seed.',
        'C is the full business-processing correctness rate; it remains unknown because the complete exception contract and semantic candidate set are not verified.',
        'Per-station and per-scenario metrics are synthetic frozen challenge evidence, not production accuracy.',
        'The separate control fixture and 8-case boundary test are safety evidence and are not blended into the 96-event A/B denominator.',
        'The prior current_r2 result is retained for before/after comparison; it cannot replace current_r4 or be overwritten.',
    ],
}
(EVAL / 'kb19_replay_audit.json').write_text(json.dumps(replay, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

validation = {
    'schema_version': 1,
    'node': 'V7-11 / KB19',
    'stage': 'current_r4',
    'status': 'doing',
    'verified': False,
    'knowledge_version': 19,
    'knowledge_sha256': STATE['expected_kb_sha256'],
    'knowledge_rules': {
        'concept': '继承 KB18 当前目录与商品概念；未新增逐商品答案',
        'unit': '继承既有计量与包装边界',
        'expression': 'KB19 受限关系结构：选属性的商品、商品请按属性供货、属性前置及属性商品要；未知词、否定、加工和包装不删除',
        'control': 'KB18 控制链边界：同批身份需可证明，公司与日期隔离，关系不唯一时 pending、计价量和金额为空',
    },
    'metrics': {'current_r4': mode_metrics, 'prior_current_r2': replay['prior_r2'], 'late_9391_r2': {mode: {'all': compact(a['all']), 'normal': compact(a['normal']), 'by_company': {k: compact(v) for k, v in a['by_company'].items()}} for mode, a in LATE['mode_aggregates'].items()}},
    'runs': run_rows,
    'denominators': {
        'seeds': [9201, 9202, 9291], 'events_per_seed': 32,
        'total_events_per_mode': 96, 'normal_events_per_mode': 72,
        'review_or_exception_events_per_mode': 24,
        'development_seeds': [9201, 9202], 'first_holdout_seed': 9291,
        'companies': ['学院路站', '新港站', '星月站', '松北大道站'],
        'scenarios': ['embedded_quantity', 'conflicting_form', 'conflicting_part', 'missing_quantity', 'missing_unit', 'negation', 'unknown_brand', 'unknown_pack', 'unknown_product'],
        'business_order_groups': {'current_r4_shared_three_seeds': 84, 'late_9391_shared': 32, 'counting_basis': 'company_id + shipping_date from messages-result manifests; do not multiply by three modes'},
        'independent_directory_concepts': {'current_r4': 12, 'late_9391': 6, 'total': 18, 'counting_basis': 'deduplicated concept identities across seeds/modes and message variants'},
    },
    'C': None,
    'C_definition': '全量业务处理正确率 = 自动正确 + 确应人工且正确保留/标色/批注，再除以全量输入',
    'reason': 'current_r4 的 shared 与 shared_with_company_contract 为 60/96 自动且正确，A=100%、B=62.5%；正常子集60/72，catalog_only 无自动分母且 B=0%。C 是全量业务处理正确率，但异常完整合同与候选语义全集未证，保持 null；整体95%目标保持 doing。',
    'checks': {
        'source_mismatches': AUDIT['source_mismatches'],
        'execution_status': STATE['status'],
        'execution_source_equal': STATE['source_equal'],
        'original_sha_equal_before_after_round2': original_sha_equal,
        'core_tests_final': '256 tests, OK',
        'expression_tests_r3': '4 tests, OK',
        'control_boundary_baseline': '6/8 passed; two known boundary failures retained as baseline evidence',
        'control_boundary_green_r1': '8/8 passed',
        'control_boundary_green_r3': '8/8 passed',
        'control_kb18_green_r3': '40/40 passed',
        'finance_audit_errors_all_runs': finance_errors,
        'quick_validate': 'Skill is valid!',
        'package': {'sha256': PACKAGE['package_sha256'], 'inventory_count': PACKAGE['inventory_count'], 'extracted_bytes_equal': PACKAGE['extracted_bytes_equal'], 'core_tests': '240 run, 236 passed, 4 skipped', 'raw_event_count': PACKAGE['raw_event_count'], 'finance_errors': PACKAGE['finance_errors']},
        'late_9391_r2': {'shared_all': compact(LATE['mode_aggregates']['shared']['all']), 'shared_normal': compact(LATE['mode_aggregates']['shared']['normal']), 'contract_all': compact(LATE['mode_aggregates']['shared_with_company_contract']['all']), 'finance_errors': 0, 'independent_concepts': 6, 'normal_events': 24, 'review_events': 8, 'r1_status': 'rejected_source_drift_after_execution'},
        'anomaly_mechanical': {'path': 'evaluation/v7/kb19_current_r4_anomaly_mechanical/report.json', 'status': 'all_true', 'color_comment_blank_amount': '77/77', 'finance_errors': 0},
        'final_integrity': INTEGRITY,
        'original': {'events': ORIGINAL['total_events'], 'finance_errors': sum(s['finance_errors'] for s in ORIGINAL['stations'].values()), 'merged_rows': COMBINED['merged_finance']['row_count'], 'merged_finance_errors': COMBINED['finance_errors'], 'fixed_identity': FIXED['assessment']['metrics'], 'source_quantity_preserved': SOURCE_FACT['quantity_preserved'], 'source_unit_preserved': SOURCE_FACT['unit_preserved'], 'hard_fact_blockers': SOURCE_FACT['hard_blockers_count'], 'optimistic_upper_bound': SOURCE_FACT['optimistic_upper_bound']},
        'oldlane_r2': {'shared': compact(OLDLANE['mode_aggregates']['shared']['all']), 'shared_normal': compact(OLDLANE['mode_aggregates']['shared']['normal'])},
        'xlsx_rows': 288,
    },
    'boundaries': [
        'shared 与 shared_with_company_contract 的 60/96 是冻结条件性挑战结果，正常子集为60/72，不等于真实新增订单覆盖。',
        'catalog_only 只证明没有共享知识时的保守行为，A 无分母；其 24 条正确处理是异常安全转人工，不是自动成功。',
        'A 绑定自动输出逐项正确率，B 绑定全量事件分母，C 绑定全量业务处理正确率；不得用 review_correct 或 business_processing_rate 替代 B/C。',
        '各分站和场景指标均须保留，不能用合并值掩盖最差分站/场景；r4 四站同为 21/24 正确处理、15/24 自动。',
        '原始八份材料只读；本阶段未验证千问办公、WorkBuddy、真实宿主、真实新增订单、系统导入、实际配送或正式财务入账。',
    ],
    'next_stage': {'name': '9391 new holdout', 'status': 'r1_rejected_r2_verified_independent', 'r1': {'status': 'rejected', 'reason': 'freeze 后补 contract_version 造成 source_drift_after_execution；原始消息未变化，失败证据保留', 'blind_first_claim': False}, 'r2': {'status': 'verified_independent', 'scorer': 'evaluation/v7/kb19_late_replay_audit.py', 'result': 'shared/contract 24/32，B=75%，正常24/24，finance_errors=0'}, 'required_updates': ['保留现有 current_r4 与先前 r2 证据', 'late9391 独立列示，不回填本轮固定96条']},
    'evidence': {
        'replay_audit': 'evaluation/v7/kb19_replay_audit.json',
        'replay_report': 'evaluation/v7/kb19_current_r4/audit/report.json',
        'report_md': 'evaluation/v7/kb19_report.md',
        'comparison_workbook': 'evaluation/v7/kb19逐项对比.xlsx',
        'delivery_index': 'outputs/v7/kb19/README.md',
        'final_integrity': 'evaluation/v7/kb19_final_integrity.json',
        'risk_analysis': 'evaluation/v7/kb19_accuracy_risk_analysis.md',
    },
}
(EVAL / 'kb19_validation_result.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

report_md = '''# KB19 未见属性关系与控制范围审计（current_r4）

状态：**doing**。本报告主结论对应 `current_r4`，不能宣称整体 95% 已完成。`current_r2` 的原始审计保留为前后对比；新增 9391 轨道的 `late_final_r1` 因冻结后补 `contract_version` 触发 source drift，拒绝评分，失败证据保留；稳定 `late_final_r2` 已独立完成并单列。

## 1. 固定验收口径

- **A**：实际输出中自动行逐项字段全正确的比例。没有自动输出时 A 为无分母，不把待核行算作自动成功。
- **B**：自动且正确事件数 / 全部冻结事件数。每个 seed 固定 32 条，其中 24 条条件性正常事件和 8 条异常/待核事件；异常转人工仍在 B 分母中。
- **C**：全量业务处理正确率，即“自动正确 + 确应人工且正确保留/标色/批注”除以全量输入。当前异常完整合同和候选语义全集尚未证实，C 保持 null。
- **95%**：整体目标要求固定分母下的全量 A/B、主要分站和主要场景同时满足；本轮未达，不用局部子集替代。

## 2. current_r4 三模式结果

| 模式 | 全量事件 | 正常事件 | 自动 | 自动且正确 | A | B | 正常自动且正确 | 异常/待核安全 | C |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| catalog_only | 96 | 72 | 0 | 0 | 无分母 | 0.0000% | 0/72 | 24 | 未知 |
| shared | 96 | 72 | 60 | 60 | 100.0000% | 62.5000% | 60/72 | 24 | 未知 |
| shared_with_company_contract | 96 | 72 | 60 | 60 | 100.0000% | 62.5000% | 60/72 | 24 | 未知 |

`shared` 与 `shared_with_company_contract` 的 60/96 是当前冻结条件性挑战结果；正常子集为 60/72=83.3333%，不能把自动输出的 60/60=100% 写成正常全覆盖。`catalog_only` 的 24/24 是异常安全转人工，不属于自动正确。

前后对比：`current_r2` 的 shared/contract 为 56/96 自动且正确、A=100%、B=58.3333%，正常 56/72；`current_r4` 为 60/96、B=62.5000%，正常 60/72。增加的 4 条是 r2 已见缺口修复后的回归增益，不是新盲测成绩。

## 3. seed、分站与场景

三 seed 为 9201、9202（development）和 9291（first_holdout，当前已消费，不能再称为未见盲测）。`current_r4` 每个 seed 的 shared/contract 均为 20/32 自动且正确、B=62.5000%，正常 20/24=83.3333%。三模式、逐 seed 详情见 `kb19_replay_audit.json` 和逐项对比表。

四个分站均保留：学院路站、新港站、星月站、松北大道站。在合并三 seed 的 shared/contract 结果中，各站都是 24 条事件、15 条自动且正确、A=100.0000%、B=62.5000%、正确处理 21 条、异常安全 6 条。该对称性只描述冻结挑战，不推断真实分站泛化。

场景分布为：`embedded_quantity` 72 条；`conflicting_form`、`conflicting_part`、`missing_quantity`、`missing_unit`、`negation`、`unknown_brand`、`unknown_pack`、`unknown_product` 各 3 条。shared/contract 的 `embedded_quantity` 为 60/72 自动且正确（A=100.0000%，B=83.3333%）；其余异常场景均按待核安全计数，不进入自动成功。

## 4. 条件性候选与异常边界

固定挑战中有 12 个独特正常事件（跨三模式计为 36 条审计行）被标为 `conditional_candidate_insufficient`：原文只给基础名 + 500g，缺少鲜冻、去皮等区分属性，不能由当前目录证明唯一完整商品组合。r2 的 9291 另有 4 条已见备注语法拒绝；r4 修复后为 20/24，但该 9291 已消费，不构成新的独立保留通过。

异常规则保持安全边界：未知品牌、未知包装、未知商品、否定、部位/形态冲突、缺量和缺单位均转待核；金额、计价量和身份字段不得凭空补齐。原文、完整 span、候选和批注保留在输出证据中。

控制链是独立安全证据，不混入 96 条 A/B 分母。`kb19_control_boundary_baseline.log` 的基线为 6/8，r1 与 r2 绿测均为 8/8；它证明同批身份、公司/日期隔离和未知约束不留可计价的已证同义行，但不证明整体自动覆盖率。

## 5. 订单组、独立概念与 late 轨道

current_r4 的 shared 三个 seed 按 `messages-result` manifest 的 `company_id + shipping_date` 去重后为 **84 个业务订单组、96 条事件**；三模式复跑不扩成新的业务订单。9391 late_final_r2 为 **32 个业务订单组、32 条事件**（含异常），同样不按模式重复计数。目录概念去重后，current_r4 覆盖 12 个概念，9391 覆盖 6 个独立迁移概念，合计 **18 个独立目录概念**；同一概念的 seed、模式和消息变体不算新增业务样本。

## 5.1 9391 独立 late 轨道

`kb19_late_final_r2` 是独立轨道：1 个 seed（9391）、6 个独立目录概念、四公司 24 条正常事件和 8 条异常事件。`shared` 与 `shared_with_company_contract` 均为全量 24/32 自动且正确（A=100.0000%、B=75.0000%），正常 24/24=100.0000%，异常 8/8 安全转人工，finance audit 0 错；四公司各 6/8 全量、6/6 正常。`catalog_only` 为 0/32 自动，8 条异常安全。C 仍为 null，原因同样是异常完整合同和候选语义全集未证。

该轨道不能抬高 current_r4 的固定 96 条主指标。9391 的 6 个概念与 9201/9202 复用关系已单独标记，不把事件变体当新增业务样本。`late_final_r1` 曾因冻结后补 `contract_version` 触发 `source_drift_after_execution` 而拒绝评分，原始消息未变化；因此不能称无中断首次盲评，r1 失败证据保留，r2 使用独立评分器和 full-demand 冻结口径。

## 5.2 旧集、原始三站与工程复读

- 旧 KB18 挑战回归 `kb19_oldlane_r2`：shared/contract 72/96 自动且正确、A=100%、B=75%；正常 72/72，24 异常安全。该已见旧集不替代 KB19 的 60/96。
- 原始三站 `kb19_original_r2`：53+42+64=159 事件，缺失/路由错 0，订单与财务复读错误 0；合并 159 明细、5 组订单、财务错误 0。这是历史复读和草稿机械检查，不能替代独立自动准确率。
- 固定人工 SKU 身份 80/132=60.6061%；对齐分母 80/155=51.6129%，全部聊天 80/159=50.3145%。松北 25/44、星月 23/35、学院路 32/53，未达 95%。
- 159 条原文量和单位均 159/159 保留；先前独立审阅的 17 条硬事实缺口当前仍无计价量与金额。即使乐观假设其余全部成功，上界 142/159=89.3082%，不是当前实现准确率。
- `kb19_r3_gate/core_tests_final.log`：256 tests，全部通过。
- `kb19_current_r3_expression.log`：4 tests，全部通过。
- `kb19_control_boundary_green_r2.log`：8 tests，全部通过；基线失败仍保留以显示修复前边界。
- 9 个 seed×模式运行的 finance audit 错误数为 0；订单/财务草稿、异常颜色、批注和空金额由独立审计检查。
- `execution_status.json` 标记 `runs_complete_before_audit` 且 `source_equal=true`；冻结输入无 source mismatch。
- 八份 `original/` 原始材料 round1 前与 round2 后 SHA 完全一致，原始目录未改写。
- `kb19_quick_validate.log`：Skill is valid；正式包 `order-mapping-v7-kb19-final.zip` SHA-256 `9c8c36b359f8a62286702386b611f3ec9c753c3df7766f78218fdbd044bead19`，51 文件解包字节一致，包内 240 项（236 通过、4 跳过），解包原始松北 53 条、财务 0 错。仅为本地包验证。
- `kb19_current_r4_anomaly_mechanical/report.json`：异常机械检查全 true；原始两阶段 77/77 颜色、批注、空金额与财务错误 0。
- `kb19_final_integrity.json`：8 份原件、current_r4 83 文件、9391 r2 74 文件、原文 r2 61 文件均与冻结指纹一致；技能包 51 文件与当前文件逐字节一致，包 SHA 与报告一致。该复核只证明版本与证据对应，不新增准确率样本。
- `kb19_accuracy_risk_analysis.md`：保留 17 条硬事实缺口逐项人类可读说明及 18 概念边界；风险分析不改变 current_r4 或 late9391 的固定分母。

## 6. 未验证项与下一阶段

本阶段未验证千问办公、WorkBuddy、真实宿主、实际真实订单、实际配送、系统导入或正式财务入账；C 所需的异常完整合同和候选语义全集也未验证。`late_final_r1` 因冻结后补字段触发 source drift 被拒绝评分，不能称无中断首次盲评；稳定 `late_final_r2` 已完成但保持独立，不回填 current_r4 固定 96 条。

证据索引：`evaluation/v7/kb19_current_r4/audit/report.json`、`evaluation/v7/kb19_current_r2/audit/report.json`、`evaluation/v7/kb19_validation_result.json`、`evaluation/v7/kb19_replay_audit.json`、`evaluation/v7/kb19逐项对比.xlsx`、`evaluation/v7/kb19_final_integrity.json`、`evaluation/v7/kb19_accuracy_risk_analysis.md`、`outputs/v7/kb19/README.md`。
'''
(EVAL / 'kb19_report.md').write_text(report_md, encoding='utf-8')

readme = '''# KB19 输出索引（current_r4）

状态：**doing**。本索引主结果是 KB19 `current_r4` 三模式冻结挑战，`current_r2` 仅作前后对比。所有订单/财务文件均为测试草稿，不代表已导入订单系统、实际配送或正式入账。整体 95% 目标、C 候选语义全集、真实宿主和真实新增订单仍未验证。

- 三模式运行证据：[`evaluation/v7/kb19_current_r4/`](../../../evaluation/v7/kb19_current_r4/)
  - `catalog_only/`：无共享知识的保守基线。
  - `shared/`：共享知识模式，r4 合并 60/96 自动且正确，A=100.0000%、B=62.5000%，正常 60/72。
  - `shared_with_company_contract/`：共享知识加公司合同模式，r4 合并 60/96，A=100.0000%、B=62.5000%，正常 60/72。
  - 9201、9202 为 development；9291 为 first_holdout，当前已消费，不再称未见盲测。
- 业务订单组与独立概念计数：current_r4 shared 三个 seed 按 `company_id + shipping_date` 去重为 84 个业务订单组 / 96 条事件；9391 late_final_r2 为 32 个业务订单组 / 32 条事件；三模式重复、seed 复用和消息变体不扩成新业务订单。去重后 current_r4 12 个目录概念 + 9391 6 个独立迁移概念 = 18 个独立目录概念。
- 回放审计：[`evaluation/v7/kb19_current_r4/audit/report.json`](../../../evaluation/v7/kb19_current_r4/audit/report.json)
- r2 保留前后对比：[`evaluation/v7/kb19_current_r2/audit/report.json`](../../../evaluation/v7/kb19_current_r2/audit/report.json)，shared/contract 56/96、B=58.3333%。
- 旧 KB18 挑战已见回归：[`evaluation/v7/kb19_oldlane_r2/audit/report.json`](../../../evaluation/v7/kb19_oldlane_r2/audit/report.json)，shared/contract 72/96、正常 72/72，不能代替 KB19 新挑战。
- 原始三站 159 事件与合并财务：[`evaluation/v7/kb19_original_r2/report.json`](../../../evaluation/v7/kb19_original_r2/report.json)、[`evaluation/v7/kb19_original_combined_r2/report.json`](../../../evaluation/v7/kb19_original_combined_r2/report.json)，财务错误 0。
- 固定人工 SKU 身份：[`evaluation/v7/kb19_original_r2/fixed_identity.json`](../../../evaluation/v7/kb19_original_r2/fixed_identity.json)，80/132；另见 80/155、80/159 分母。
- 原始源事实复核：[`evaluation/v7/kb19_source_fact_recheck.json`](../../../evaluation/v7/kb19_source_fact_recheck.json)，159/159 原量与单位保留，17 条硬事实缺口、乐观上界 142/159 不是实现率。
- 原始站点标准订单与财务草稿：[`outputs/v7/kb19_original_r2/songbei/kb19_original_r2-songbei-g1/47ddc0111c3a1920-6cf8cdb1/测试_下单需求_待配送_待人工处理.xlsx`](../../../outputs/v7/kb19_original_r2/songbei/kb19_original_r2-songbei-g1/47ddc0111c3a1920-6cf8cdb1/测试_下单需求_待配送_待人工处理.xlsx)、[`测试_财务记账_预估草稿.xlsx`](../../../outputs/v7/kb19_original_r2/songbei/kb19_original_r2-songbei-g1/47ddc0111c3a1920-6cf8cdb1/测试_财务记账_预估草稿.xlsx)；星月 g1：[`标准订单`](../../../outputs/v7/kb19_original_r2/xingyue/kb19_original_r2-xingyue-g1/a731037ac0fe7ed7-3815628c/测试_下单需求_待配送_待人工处理.xlsx)、[`单站财务`](../../../outputs/v7/kb19_original_r2/xingyue/kb19_original_r2-xingyue-g1/a731037ac0fe7ed7-3815628c/测试_财务记账_预估草稿.xlsx)；学院路 g1：[`标准订单`](../../../outputs/v7/kb19_original_r2/xueyuan/kb19_original_r2-xueyuan-g1/c7c973fa433486c6-21511e60/测试_下单需求_待配送_待人工处理.xlsx)、[`单站财务`](../../../outputs/v7/kb19_original_r2/xueyuan/kb19_original_r2-xueyuan-g1/c7c973fa433486c6-21511e60/测试_财务记账_预估草稿.xlsx)。
- 原始三站合并财务：[`总合并草稿`](../../../outputs/v7/kb19_original_r2/kb19_original_combined_r2/merged-09b385b38e068c0e-71acfb04/测试_合并财务记账_预估草稿.xlsx)；站点合并草稿：[`松北`](../../../outputs/v7/kb19_original_r2/songbei/merged/merged-36101dc5f71b2547-09d31e44/测试_合并财务记账_预估草稿.xlsx)、[`星月`](../../../outputs/v7/kb19_original_r2/xingyue/merged/merged-1017d639266fa125-7db26ad6/测试_合并财务记账_预估草稿.xlsx)、[`学院路`](../../../outputs/v7/kb19_original_r2/xueyuan/merged/merged-3fbcf57dfb5fdf74-79f7e1c6/测试_合并财务记账_预估草稿.xlsx)。
- 9391 late 独立轨道：[`evaluation/v7/kb19_late_final_r2/audit/report.json`](../../../evaluation/v7/kb19_late_final_r2/audit/report.json)，shared/contract 24/32、B=75.0000%，正常 24/24，四公司各 6/8；该轨道不并入 current_r4 固定96。r1 因冻结后补 `contract_version` 触发 source drift 被拒绝评分，不能称无中断首次盲评。
- 正式报告：[`evaluation/v7/kb19_report.md`](../../../evaluation/v7/kb19_report.md)
- 结构化验收结果：[`evaluation/v7/kb19_validation_result.json`](../../../evaluation/v7/kb19_validation_result.json)
- 三模式回放摘要：[`evaluation/v7/kb19_replay_audit.json`](../../../evaluation/v7/kb19_replay_audit.json)
- 逐项对比（含汇总、seed/模式、分站、场景和 288 条逐项行）：[`evaluation/v7/kb19逐项对比.xlsx`](../../../evaluation/v7/kb19逐项对比.xlsx)
- 控制链边界绿测：[`evaluation/v7/kb19_current_r3_control_boundary.log`](../../../evaluation/v7/kb19_current_r3_control_boundary.log)，8/8；KB18 控制回归 [`evaluation/v7/kb19_current_r3_control_kb18.log`](../../../evaluation/v7/kb19_current_r3_control_kb18.log)，40/40；这些证据不混入 96 条 A/B 分母。
- 表达关系绿测：[`evaluation/v7/kb19_current_r3_expression.log`](../../../evaluation/v7/kb19_current_r3_expression.log)，4/4。
- 核心测试：[`evaluation/v7/kb19_r3_gate/core_tests_final.log`](../../../evaluation/v7/kb19_r3_gate/core_tests_final.log)，256/256。
- 包验证：[`evaluation/v7/package_check_kb19_final/report.json`](../../../evaluation/v7/package_check_kb19_final/report.json)，51 文件、解包字节一致、包内 240 项运行（236 通过、4 跳过）、原始松北 53 条/财务 0 错；[`dist/order-mapping-v7-kb19-final.zip`](../../../dist/order-mapping-v7-kb19-final.zip)，SHA-256 `9c8c36b359f8a62286702386b611f3ec9c753c3df7766f78218fdbd044bead19`。
- 最终完整性复核：[`evaluation/v7/kb19_final_integrity.json`](../../../evaluation/v7/kb19_final_integrity.json)，8 份原件与 83/74/61 阶段文件指纹一致，包内 51 文件逐字节一致；不新增准确率样本。风险分析：[`evaluation/v7/kb19_accuracy_risk_analysis.md`](../../../evaluation/v7/kb19_accuracy_risk_analysis.md)，含 17 条硬缺口逐项说明与 18 概念边界。

异常/待核事件保留原文、span、候选、颜色和批注；未知金额保持空白。C 定义为全量业务处理正确率，但异常完整合同与候选语义全集未证，当前保持 null。9391 轨道 r1 因 source drift 拒绝评分，r2 已完成但独立列示；不能称无中断首次盲评，也不纳入本轮固定 96 条。
'''
out = ROOT / 'outputs/v7/kb19'
out.mkdir(parents=True, exist_ok=True)
(out / 'README.md').write_text(readme, encoding='utf-8')
print(json.dumps({'validation': str(EVAL / 'kb19_validation_result.json'), 'report': str(EVAL / 'kb19_report.md'), 'replay': str(EVAL / 'kb19_replay_audit.json'), 'readme': str(out / 'README.md')}, ensure_ascii=False))
