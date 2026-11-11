# v6 共享采购知识：rc8结果与未达原因

状态 **doing / 整体验收未通过**。本阶段知识版本12，包含68条概念规则和2条单位规则；本报告从rc8回放和身份审计读取结果，不把已消费的保留集回归称为独立盲测。该阶段知识与当前工作区一致。

KB7覆盖干豆腐、蒜苔、甘蓝、海天上等耗油、玉米粒罐头和香蕉六类；普通干豆腐先选择未附加厚等形态的最接近层，在同计价基础内比当前价。明确加厚、红帽、牛心、小米蕉等要求仍须由目录证实。原文缺重量或包装依据时，商品身份与数量、金额分别处理。

历史冻结oracle未更改：新规则使更多事件自动输出，旧预期为manual或undefined时不会被自动改判正确。因此自动数上升不保证A/B/C上升；这既不证明所有新增选择错误，也不能忽略冻结预期中的已知错误。

## 1. 全量回放结果

A=全部字段自动正确数/自动完成数；B=同一正确数/全部输入事件；C包括自动正确、可独立核定的人工处理以及正确的控制关系。控制事件留在分母；历史undefined不计正确；异常候选缺穷尽性证据时不算完整人工正确。

| 阶段 | 模式 | 全量事件 | 自动完成 | 自动正确 | A% | B% | C% |
| --- | --- | --- | --- | --- | --- | --- | --- |
| historical_rc8 | catalog_only | 1034 | 276 | 239 | 86.5942 | 23.1141 | 57.2534 |
| historical_rc8 | shared | 1034 | 408 | 293 | 71.8137 | 28.3366 | 66.1509 |
| historical_rc8 | shared_with_company_contract | 1034 | 408 | 293 | 71.8137 | 28.3366 | 66.1509 |
| challenge_rc8 | catalog_only | 387 | 341 | 341 | 100.0 | 88.1137 | 91.9897 |
| challenge_rc8 | shared | 387 | 341 | 341 | 100.0 | 88.1137 | 91.9897 |
| challenge_rc8 | shared_with_company_contract | 387 | 341 | 341 | 100.0 | 88.1137 | 91.9897 |
| extension_rc8 | catalog_only | 243 | 63 | 63 | 100.0 | 25.9259 | 25.9259 |
| extension_rc8 | shared | 243 | 213 | 213 | 100.0 | 87.6543 | 87.6543 |
| extension_rc8 | shared_with_company_contract | 243 | 213 | 213 | 100.0 | 87.6543 | 87.6543 |

同一套冻结输入下，共享知识对全量自动正确覆盖的贡献如下；公司特殊约定当前为空，不能将空配置的零增益解释为已验证所有公司特例。

| 阶段 | 共享新增自动正确 | B提升百分点 | 公司约定额外自动正确 |
| --- | --- | --- | --- |
| historical_rc8 | 54 | 5.2225 | 0 |
| challenge_rc8 | 0 | 0.0 | 0 |
| extension_rc8 | 150 | 61.7284 | 0 |

订单数和商品范围以下表及[覆盖明细](v6/product_coverage.json)为准。目录更新可令同名商品对应不同SKU；这些计数不表示独立真实新订单数量。

| 阶段 | seed | 原文请求 | 导出订单 | 回放事件次数 | 预期商品名数 | 预期SKU数 |
| --- | --- | --- | --- | --- | --- | --- |
| historical_rc8 | 6108,6109 | 120 | 195 | 1034 | 49 | 49 |
| challenge_rc8 | 7201,7202,7291 | 24 | 135 | 387 | 43 | 44 |
| extension_rc8 | 7301,7302,7391 | 3 | 84 | 243 | 31 | 31 |

### 固定159条原始事件的全字段结果

以下取6108同站拆分、shared口径，每条原始事件恰好一次；不含附加目录挑战，不能用1034次混合回放代替该分母。[逐站结构化结果](v6/historical_rc8/fixed_159_full_fields.json)。仍沿用冻结预期，未裁定的自动输出保守不计正确。

| 公司 | 原始事件 | 自动完成 | 自动正确 | A% | B% | C% |
| --- | --- | --- | --- | --- | --- | --- |
| 全部 | 159 | 60 | 41 | 68.3333 | 25.7862 | 64.1509 |
| songbei | 53 | 15 | 11 | 73.3333 | 20.7547 | 62.2642 |
| xingyue | 42 | 19 | 14 | 73.6842 | 33.3333 | 61.9048 |
| xueyuan | 64 | 26 | 16 | 61.5385 | 25.0 | 67.1875 |

### 与rc1共享模式对比

| 阶段 | 事件 | 自动 | 自动正确 | A% | B% | C% |
| --- | --- | --- | --- | --- | --- | --- |
| historical_rc1 | 1034 | 336 | 293 | 87.2024 | 28.3366 | 67.8917 |
| historical_rc8 | 1034 | 408 | 293 | 71.8137 | 28.3366 | 66.1509 |
| challenge_rc1 | 387 | 341 | 341 | 100.0 | 88.1137 | 91.9897 |
| challenge_rc8 | 387 | 341 | 341 | 100.0 | 88.1137 | 91.9897 |
| extension_rc1 | 243 | 208 | 208 | 100.0 | 85.5967 | 85.5967 |
| extension_rc8 | 243 | 213 | 213 | 100.0 | 87.6543 | 87.6543 |

历史共享回放从自动336增加到408，冻结预期下自动正确由293变为293；自动但预期undefined从43增加到103。当前已裁定自动305条中正确293条、未通过12条，A_known=96.0656%仅是该子集诊断，不能代替包含undefined的全量A=71.8137%。

历史回放有两个seed、多个变形及压力场景；1034是回放事件次数，不能当成1034条独立真实原文。159条历史原始事件、155条人工对齐事件及132个可核人工SKU分母均保留。

## 2. 分公司、场景与正常子集

### historical_rc8

| 公司 | 事件 | 自动正确 | A% | B% | C% |
| --- | --- | --- | --- | --- | --- |
| xingyue | 296 | 125 | 80.6452 | 42.2297 | 67.5676 |
| newco | 2 | 2 | 100.0 | 100.0 | 100.0 |
| xueyuan | 388 | 95 | 61.2903 | 24.4845 | 66.4948 |
| songbei | 348 | 71 | 73.9583 | 20.4023 | 64.3678 |

| 场景 | 事件 | 自动正确 | A% | B% | C% |
| --- | --- | --- | --- | --- | --- |
| brand_part_form_package_interference | 2 | 0 | 未定义 | 0.0 | 0.0 |
| catalog_update | 2 | 2 | 100.0 | 100.0 | 100.0 |
| cross_station_combination | 318 | 82 | 68.3333 | 25.7862 | 64.1509 |
| cross_station_explicit_company | 6 | 3 | 100.0 | 50.0 | 100.0 |
| duplicate_append_change_cancel | 8 | 0 | 未定义 | 0.0 | 100.0 |
| missing_quantity_unit | 2 | 0 | 未定义 | 0.0 | 100.0 |
| multiple_candidates | 2 | 0 | 未定义 | 0.0 | 100.0 |
| new_company_same_product | 2 | 2 | 100.0 | 100.0 | 100.0 |
| quantity_date_variation | 2 | 2 | 100.0 | 100.0 | 100.0 |
| same_quantity_without_explicit_append | 4 | 0 | 未定义 | 0.0 | 50.0 |
| single_station_reorder | 318 | 80 | 67.7966 | 25.1572 | 63.522 |
| single_station_split | 318 | 82 | 68.3333 | 25.7862 | 64.1509 |
| synonym_typo | 2 | 0 | 0.0 | 0.0 | 0.0 |
| unique_predecessor_cancel | 4 | 0 | 未定义 | 0.0 | 100.0 |
| unique_predecessor_change | 4 | 0 | 未定义 | 0.0 | 100.0 |
| unseen_catalog_expression | 40 | 40 | 100.0 | 100.0 | 100.0 |

### challenge_rc8

| 公司 | 事件 | 自动正确 | A% | B% | C% |
| --- | --- | --- | --- | --- | --- |
| 学院路站 | 84 | 80 | 100.0 | 95.2381 | 95.2381 |
| 新港站 | 84 | 84 | 100.0 | 100.0 | 100.0 |
| 星月站 | 135 | 93 | 100.0 | 68.8889 | 80.0 |
| 松北大道站 | 84 | 84 | 100.0 | 100.0 | 100.0 |

| 场景 | 事件 | 自动正确 | A% | B% | C% |
| --- | --- | --- | --- | --- | --- |
| action_append | 3 | 0 | 未定义 | 0.0 | 100.0 |
| action_cancel | 3 | 0 | 未定义 | 0.0 | 100.0 |
| action_initial | 3 | 0 | 未定义 | 0.0 | 100.0 |
| action_replace_quantity | 3 | 0 | 未定义 | 0.0 | 100.0 |
| brand_conflict | 3 | 0 | 未定义 | 0.0 | 0.0 |
| company_invariance_and_catalog_price | 24 | 24 | 100.0 | 100.0 | 100.0 |
| continuous_input | 48 | 46 | 100.0 | 95.8333 | 95.8333 |
| duplicate_explicit | 3 | 0 | 未定义 | 0.0 | 100.0 |
| duplicate_original | 3 | 3 | 100.0 | 100.0 | 100.0 |
| each_quantity | 6 | 6 | 100.0 | 100.0 | 100.0 |
| missing_catalog | 3 | 0 | 未定义 | 0.0 | 0.0 |
| normal_chinese | 51 | 51 | 100.0 | 100.0 | 100.0 |
| normal_fullwidth | 59 | 59 | 100.0 | 100.0 | 100.0 |
| normal_kg | 47 | 46 | 100.0 | 97.8723 | 97.8723 |
| normal_quantity_first | 56 | 56 | 100.0 | 100.0 | 100.0 |
| normal_suffix | 51 | 50 | 100.0 | 98.0392 | 98.0392 |
| part_conflict | 3 | 0 | 未定义 | 0.0 | 0.0 |
| quantity_invalid | 3 | 0 | 未定义 | 0.0 | 0.0 |
| quantity_missing | 3 | 0 | 未定义 | 0.0 | 0.0 |
| quantity_negative | 3 | 0 | 未定义 | 0.0 | 0.0 |
| quantity_zero | 3 | 0 | 未定义 | 0.0 | 0.0 |
| spec_conflict | 3 | 0 | 未定义 | 0.0 | 0.0 |
| unit_missing | 3 | 0 | 未定义 | 0.0 | 0.0 |

下表为冻结预期支持的正常子集，不能替代全量分母或整体验收：

| 公司 | 正常事件 | 自动正确 | A% | B% |
| --- | --- | --- | --- | --- |
| 学院路站 | 84 | 80 | 100.0 | 95.2381 |
| 新港站 | 84 | 84 | 100.0 | 100.0 |
| 星月站 | 90 | 90 | 100.0 | 100.0 |
| 松北大道站 | 84 | 84 | 100.0 | 100.0 |

正常子集微平均B=98.8304%，自动正确338/342。

### extension_rc8

| 公司 | 事件 | 自动正确 | A% | B% | C% |
| --- | --- | --- | --- | --- | --- |
| 云溪驿站 | 3 | 0 | 未定义 | 0.0 | 0.0 |
| 学院路站 | 65 | 54 | 100.0 | 83.0769 | 83.0769 |
| 新港站 | 59 | 53 | 100.0 | 89.8305 | 89.8305 |
| 星月站 | 59 | 54 | 100.0 | 91.5254 | 91.5254 |
| 松北大道站 | 57 | 52 | 100.0 | 91.2281 | 91.2281 |

| 场景 | 事件 | 自动正确 | A% | B% | C% |
| --- | --- | --- | --- | --- | --- |
| inline_company_switch | 6 | 6 | 100.0 | 100.0 | 100.0 |
| normal_catalog_name | 63 | 63 | 100.0 | 100.0 | 100.0 |
| normal_semantic_form | 12 | 12 | 100.0 | 100.0 | 100.0 |
| normal_short_name | 33 | 33 | 100.0 | 100.0 | 100.0 |
| normal_synonym | 72 | 60 | 100.0 | 83.3333 | 83.3333 |
| normal_synonym_comparable | 3 | 3 | 100.0 | 100.0 | 100.0 |
| normal_typo | 12 | 12 | 100.0 | 100.0 | 100.0 |
| normal_word_order | 12 | 12 | 100.0 | 100.0 | 100.0 |
| same_demand_company_invariance | 12 | 12 | 100.0 | 100.0 | 100.0 |
| semantic_review | 15 | 0 | 未定义 | 0.0 | 0.0 |
| unknown_company | 3 | 0 | 未定义 | 0.0 | 0.0 |

下表为冻结预期支持的正常子集，不能替代全量分母或整体验收：

| 公司 | 正常事件 | 自动正确 | A% | B% |
| --- | --- | --- | --- | --- |
| 学院路站 | 57 | 54 | 100.0 | 94.7368 |
| 新港站 | 56 | 53 | 100.0 | 94.6429 |
| 星月站 | 57 | 54 | 100.0 | 94.7368 |
| 松北大道站 | 55 | 52 | 100.0 | 94.5455 |

正常子集微平均B=94.6667%，自动正确213/225。

## 3. 固定历史人工身份一致率

这是与历史人工表的SKU一致率，不等于A/B/C，也不把人工表作为独立商品语义真值。以下当前与对照均为6108/single_station_split/shared；分母固定。

| 阶段 | 口径 | 一致/分母 | 比例% |
| --- | --- | --- | --- |
| historical_rc1 | strict132 | 50/132 | 37.8788 |
| historical_rc1 | coverage155 | 50/155 | 32.2581 |
| historical_rc1 | coverage159 | 50/159 | 31.4465 |
| historical_rc8 | strict132 | 58/132 | 43.9394 |
| historical_rc8 | coverage155 | 58/155 | 37.4194 |
| historical_rc8 | coverage159 | 58/159 | 36.478 |

| 站点 | rc8可核SKU一致/分母 | 严格一致率% |
| --- | --- | --- |
| songbei | 14/44 | 31.8182 |
| xingyue | 17/35 | 48.5714 |
| xueyuan | 27/53 | 50.9434 |

各seed和主场景的严格身份结果：

| seed/主场景 | 一致/分母 | 比例% | 未选 | 不同SKU |
| --- | --- | --- | --- | --- |
| 6108/single_station_split/shared | 58/132 | 43.9394 | 67 | 7 |
| 6108/single_station_reorder/shared | 58/132 | 43.9394 | 67 | 7 |
| 6108/cross_station_combination/shared | 58/132 | 43.9394 | 67 | 7 |
| 6109/single_station_split/shared | 58/132 | 43.9394 | 67 | 7 |
| 6109/single_station_reorder/shared | 58/132 | 43.9394 | 67 | 7 |
| 6109/cross_station_combination/shared | 58/132 | 43.9394 | 67 | 7 |

未选和不同SKU既可能涉及知识或解析不足，也可能是需要保留的商品属性、包装或换算限制；应逐项核对，不能因人工单有SKU就把它当作正确答案。详情：[rc8身份审计](v6/historical_rc8/identity_audit.json)、[rc1身份审计](v6/historical_rc1/identity_audit.json)。

在上述6108拆分口径中，12条从未选变为有SKU：8条与历史人工身份一致，4条不同；“甘蓝3颗”仍未选；颗到重量不能猜测。六类九事件是审阅范围，不等于九条历史事件都已选定。

[79条未选审阅](v6/historical_remaining_review.md)及[六类二次复核](v6/generalization_second_review.md)为静态开发审查；二次审查曾将加厚干豆腐纳入比较，KB7实施时按普通整品最接近层排除加厚，这两种口径在当前目录选择相同，但不得误称规则完全一致。

## 4. 首次保留失效与测试隔离

首次7291暴露两条紧邻前置数量消息漏拆；首次7391暴露数字规格80*100与5kg边界。这些样本已用于修复并成为已见回归，不能用于独立未见验收。各seed共享商品或表达池，重排、数量变化不等于新词汇或真实客户数据。首次成绩来自[KB7前报告快照](v6/report_before_kb7.md)，保留原口径：

| 首次保留阶段 | 全量事件 | 自动正确 | A% | B% | C% | 漏项 |
| --- | --- | --- | --- | --- | --- | --- |
| challenge_holdout_final | 129 | 111 | 100.0 | 86.0465 | 89.9225 | 2 |
| extension_holdout_final | 81 | 69 | 100.0 | 85.1852 | 85.1852 | 0 |

[首次挑战审计](v6/challenge_holdout_final/replay_audit.json) · [首次扩展审计](v6/extension_holdout_final/replay_audit.json) · [验收与隔离契约](v6/acceptance_contract.md)。

运行输入是聊天原文、用户确认的公司日期、当前目录和正式知识；历史人工选择、评测预期和来源事件ID不注入运行时。rc8在这些已观察样本上的结果只能作为回归，不是独立保留验收。

## 5. 运行完整性与验证范围

| 阶段 | 执行错误 | 审计错误 | 共享漏输出 | 共享额外输出 | 原件哈希不变 |
| --- | --- | --- | --- | --- | --- |
| historical_rc8 | 0 | 0 | 0 | 0 | True |
| challenge_rc8 | 0 | 0 | 0 | 0 | True |
| extension_rc8 | 0 | 0 | 0 | 0 | True |

上表仅描述已运行的回放，不能代表验收覆盖完整。historical_rc8审计的integrity_passed=False，原因是coverage_incomplete；当前历史回放只有6108和6109，冻结验收口径还要求第三个seed。业务预期通过=False，整体验收=False。

台账、标准订单、财务草稿、原生批注与汇总的回读验证是机械一致性证据，不能证明候选语义穷尽或真实财务验收。

本阶段知识版本指纹：`419be6295ea35f1705596c857c7ae999813a9fd105f07ff08e2c166534f0d42e`；挑战和扩展回放及解包验证记录直接绑定该指纹。历史身份审计绑定历史回放文件SHA；历史回放顶层没有知识SHA，但已检查其结果引用的195个shared和195个shared_with_company_contract manifest与台账，分别与知识SHA、知识和公司约定的组合SHA一致，且台账文件哈希吻合；195个catalog_only manifest按模式记录空知识SHA。未生成manifest的回放请求数为0。

当前技能包与[package_rc8_docs验证](v6/package_rc8_docs/validation_result.json)的包及知识SHA一致；[解包测试日志](v6/package_rc8_docs/tests.log)解包测试记录144项，结果OK (skipped=2)。原文CLI烟测5事件；这些是本地验证，不代表真实宿主通过。

KB8针对独立合成首轮暴露的问题补充普通前缀和合法XLSX缺少dimension的处理；[首轮审计](v6/kb7_challenge/runs/kb7_first/20261108T050946Z-138f5e47/report.json)中8101通过17/19，另38例目录注册失败；合计通过17/57。[KB8同冻结重跑](v6/kb7_challenge/runs/kb8_same_frozen_rerun/20261108T051317Z-b3ddb515/report.json)通过57/57。首次结果已用于修复，重跑属于已消费回归，不能作为首次未见验收；三个seed共享表达池，并非真实客户数据。

[KB9同冻结重跑](v6/kb7_challenge/runs/kb9_final_regression/20261108T052123Z-aa133727/report.json)通过57/57，属于已消费回归。

[KB9额外反例首轮](v6/kb9_extra/runs/kb9_extra_first/20261108T052044Z-e24dcb32/report.json)通过13/14；“下五花肉2斤（去皮）”处于待核状态且金额为空，但仍选了普通SKU 80005146，触发prohibited_selected。该结果推动后续修复，此测试不代表真实未见业务验收。

[KB10同冻结重跑](v6/kb7_challenge/runs/kb10_final_regression/20261108T052504Z-513db6d0/report.json)通过57/57，属于已消费回归。

[kb10_v1_unchanged](v6/kb9_extra/runs/kb10_v1_unchanged/20261108T052445Z-0ea890cf/report.json)既定检查通过13/14，与KB9首轮同一冻结版本；未通过：下五花肉2斤（去皮）: manual_required。冻结说明：依据独立目录审阅与业务规则生成；在本额外测试的任何 CLI 回放前冻结，可能已受 KB9 生产变更影响。该结果不能作为独立未见验收。

[kb10_v2_evidence_revision](v6/kb9_extra/v2/runs/kb10_v2_evidence_revision/20261108T052455Z-eee333ab/report.json)既定检查通过14/14，冻结指纹与KB9首轮不同。冻结说明：v2修订在看到v1 KB9回放后、KB10本测试回放前冻结；仅修正case_13的旧manual预期，依据当前目录明确去皮下五花和500g计价基础。该结果不能作为独立未见验收。

[KB12同冻结重跑](v6/kb7_challenge/runs/kb12_current/20261108T054334Z-e45b0920/report.json)既定检查通过57/57；该结果同样是已消费回归。

[指标汇总](v6/kb7_challenge/runs/kb12_current/20261108T054334Z-e45b0920/summary.json)：自动正确/自动=31/31，B=31/57；C=未确定，下界54.386%；待人工候选完整性未审定26条。

[kb12_current](v6/kb9_extra/v2/runs/kb12_current/20261108T054400Z-6692c0c4/report.json)既定检查通过14/14，冻结指纹与KB9首轮不同。冻结说明：v2修订在看到v1 KB9回放后、KB10本测试回放前冻结；仅修正case_13的旧manual预期，依据当前目录明确去皮下五花和500g计价基础。该结果不能作为独立未见验收。

[指标汇总](v6/kb9_extra/v2/runs/kb12_current/20261108T054400Z-6692c0c4/summary.json)：自动正确/自动=12/12，B=12/14；C=未确定，下界85.7143%；待人工候选完整性未审定2条。

[kb12_current](v6/kb9_extra/negative/runs/kb12_current/20261108T054421Z-c63c96a9/report.json)既定检查通过3/3，冻结指纹与KB9首轮不同。冻结说明：KB11已实现的否定/带皮条件开发回归，测试回放前冻结，不是盲测。该结果不能作为独立未见验收。

[指标汇总](v6/kb9_extra/negative/runs/kb12_current/20261108T054421Z-c63c96a9/summary.json)：自动正确/自动=0/0，B=0/3；C=未确定，下界0.0%；待人工候选完整性未审定3条。

KB9增加普通下五花肉、木耳、红肠和粉肠的受约束共享规则；普通层不跨越明确品牌、品种或加工备注。该阶段仍以对应回放和独立样本的正式审计结果为准。

rc8修复加工备注“不要去皮”被误报为订单控制的问题；独立加工备注不再产生order_event，真实取消、追加和改量继续保留。仅覆盖已有反例范围，不证明所有否定语义。[红测](v6/rc8_parser_red.log)、[核心回归](v6/rc8_parser_core.log)和[修复指纹](v6/rc8_parser_fingerprints_after.json)保留前后证据。KB12将北极冰虾80*100的过度待核改为有约束自动选择：未声明鲜活不等于拒绝目录冷冻项，同品种、同规格、质量计价方可选择；明确鲜活、非冷冻、品牌及规格冲突继续阻断，袋重不推定。生腰果仁的去壳依据仍缺失，继续待核。旧oracle未修改，此轮仍为已消费合成回归，完整宿主Agent语义未验收。

冻结合成样本有意混合异常和控制事件，在保留原有全分母与预期的前提下，仅按非selected排除得到下列宽松B上界；selected仍可能包含inactive控制，因此该值不代表可达率。上界低于95%反映测试构造和口径，不能据此改变分母或预期。证据见[合成分母限制](v6/synthetic_denominator_limits.json)。

| 数据集 | 全量事件 | selected预期 | 宽松B上界% | 实际B% |
| --- | --- | --- | --- | --- |
| challenge_rc8 | 387 | 354 | 91.4729 | 88.1137 |
| extension_rc8 | 243 | 225 | 92.5926 | 87.6543 |

扩展正常子集本身的B也未达95%，不能用正常子集成绩冒充全量成绩。

## 6. 剩余问题与下一步

历史原文与当前目录的[事实缺口审计](v6/source_fact_limits.md)列出26条已证明缺少完整自动订单所需事实；在该审计的假设下，133/159是排除已证明缺口后的宽松上界，不是可达率，也不是剩余133条已正确。不能把旧42/159或92/159分类当作能力上限。

继续复核身份差异和全部合理候选；对已确认的同义与等价规则增加来源、反例及目录变更验证。不把历史实配量、旧价或人工SKU倒灌为通用规则，也不要求用户重复提供已有材料。详细因素见[准确率风险](v6/accuracy_risks.md)。

当前尚无真实新增客户订单、真实宿主Agent语义验收或正式财务验收；正常子集和自动行准确率均不能替代全量B或整体95%目标。

## 7. 交付与证据索引

[技能包](../dist/order-mapping-v6.zip) · [技能入口](../skills/order-mapping/SKILL.md) · [逐项工作簿](v6逐项对比.xlsx) · [订单与财务草稿](../outputs/v6/README.md)。

[historical_rc8](v6/historical_rc8/replay_audit.json) · [challenge_rc8](v6/challenge_rc8/replay_audit.json) · [extension_rc8](v6/extension_rc8/replay_audit.json)。[validation_result.json](v6/validation_result.json)为聚合记录，不替代上述rc8逐项审计。

[KB12迭代记录](v6/kb12_iteration.json) · [规则裁定与反例](v6/kb12_knowledge_review.md) · [原件交付后SHA](v6/kb12_original_sha_delivery_after.json)。
