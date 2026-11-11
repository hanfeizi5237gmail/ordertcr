# KB19 未见属性关系与控制范围审计（current_r4）

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
