# v2 正式技能只读可用性与一致性复核（2026-11-07）

范围：`skills/order-mapping/SKILL.md`、`references/business-rules.md`、`references/data-contract.md`、`references/portability.md`、`assets/profile.example.json`；以当前 CLI、profile、匹配、工作流及导出源码和已有测试作契约对照。未改正式技能或 core，未运行真实批次或在其他宿主验收。

## 发现

- **非阻断，文档旧时态**：`references/data-contract.md:3` 仍写“CLI与执行器实现后再用公开校验核对一致性”，但当前 CLI 与执行器已经存在。建议后续将其改为“本文件描述当前 CLI 与执行器的数据契约；变更后以 CLI 帮助、测试和样本复核”，以免首次使用者误以为命令尚未交付。本轮按只读范围不改。
- 没有发现本次重点核对项的阻断性契约冲突。以下是源文件和静态核验的证据，而不是新的端到端准确率证明。

## 契约核对

| 核对项 | 证据与结论 |
|---|---|
| profile 注册 | `SKILL.md:13,17`、`references/business-rules.md:24-27`、`references/data-contract.md:12,45-63` 均写必需 `--source`；CLI `scripts/order_mapping.py:20-23,42-44` 将其设为必填并传 `source_path`。实际执行 `profile-register --help` 显示 `--runtime`、`--file`、`--source` 均为必需。`profiles.py:67-76` 读取实际源文件字节并核对 `evidence.sha256`，且 `:44-47` 对已识别品牌冲突拒绝注册；旧提案所称“仅校验来源指纹形状”已不适用于当前源码。指纹保证文件内容一致，别名证据与未列举品牌/品种仍须业务核查，这点 `business-rules.md:25,27` 已明确。 |
| 商品身份与计价数量 | `SKILL.md:13-14`、`business-rules.md:32-34,39-42`、`data-contract.md:24,67-69` 均要求先保留已知SKU，单独标数量未决。`matching.py:185-190` 先置 `product_status=matched`，换算失败时保留商品；`workflow.py:350-365` 仅在整行 matched 计金额，未决金额清空；`export.py:158-183` 的标准订单保留目录 D/E/G/I、原量 F 与原单位批注、空 J 和黄色；`:240-259` 财务草稿保留已知品名与原量，单价及金额留空。`tests/test_identity.py:14-38` 覆盖鲤鱼按条的预期，但本轮未重跑该测试。 |
| AI主动语义选品 | `SKILL.md:12` 明确不能把脚本的“语义待复核”当成Agent已完成判断，要求过滤品牌/品种/鲜冻/包装，按等价可比最高价给出逐行 `decisions[line_id]`。`data-contract.md:41` 列明 `code/catalog_version/reason/confirmed_by/selection_basis`，`workflow.py:208-222` 校验并保存 Agent 决策。不同包装决定商品时保留未决；这与身份已知而仅数量未知的场景分开。 |
| profile与成绩边界 | `SKILL.md:15` 和 `business-rules.md:28` 明确同表拟合不得称未见订单准确率；无 profile 仍主动审阅、报告选择和未决，不承诺与已校准公司同样成绩。`portability.md` 末段说明通用包不含私有profile，执行器不会自动调用模型API。示例JSON是合法JSON，但SKU与SHA显式占位；`data-contract.md:47,61` 明确不能直接注册。 |
| 本地引用 | 对上述四份Markdown的相对链接逐一检查：目标文件均存在、带片段的目标标题均对应；`references/data-contract.md` 到 `../assets/profile.example.json` 也有效。`quick_validate.py` 对正式技能返回 `Skill is valid!`，JSON语法检查通过。 |

本轮是只读文档/源码复核与 CLI 帮助查询，没有执行导出测试、真实参考文件注册、千问办公/WorkBuddy 使用或实际订单/财务导入。材料语义和历史采购偏好的真实正确率须在冻结profile后用未见订单独立验证；文档与当前代码一致并不等于95%验收达成。

主代理处置记录：上述data-contract.md旧时态已改为“已实现CLI”，并再次通过正式skill quick_validate及本地链接检查。
