# 独立首次使用者运行记录

范围：只按`skills/order-mapping/SKILL.md`及两个资产示例运行CLI，状态和产物均放在`evaluation/forward_runtime/`。Python使用`/Users/hanfei/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`，CLI为`ordertcr/skills/order-mapping/scripts/order_mapping.py`，以下命令均从`/tmp`执行，没有调用实现测试。完整绝对路径参数、退出码和产物见[JSON记录](forward_use_report.json)。

| 顺序 | 实际命令与关键参数 | 退出码 | 结果 |
|---|---|---:|---|
| 1 | `order_mapping.py --help`，再分别查`catalog-register/run/validate-config --help` | 0 | 帮助列出目录注册、当前版本、撤销、解析、配置校验、运行及财务合并命令；三个子命令参数与文档一致。 |
| 2 | `catalog-register --runtime evaluation/forward_runtime/state --file original/食堂食材基准价价格表10.12-10.18.xlsx` | 0 | 注册1647条，版本和SHA均为`6a0223e4f3db42fe56d6cabb0bd94fdea0e8b123c290eafc395571d48c13f687`。 |
| 3 | `validate-config --config skills/order-mapping/assets/company-config.example.json` | 0 | `valid=true`、1家公司、`companies_confirmed=false`。 |
| 4 | `catalog-current --runtime evaluation/forward_runtime/state` | 0 | 从独立状态读回相同版本和1647条。 |
| 5 | `run --runtime …/state --batch …/assets/batch.example.json --output …/example_output` | 0 | 1行演示需求，`status=needs_confirmation`、`test_only=true`、`finance_draft=true`；公司/日期/编号未确认，预览B2为空且有原生批注。 |
| 6 | 从示例复制配置到`forward_runtime/batch.chat_files.json`，把`items`替换为相对路径`../../original/星月站11.3下单.txt`，然后`run --runtime …/state --batch …/batch.chat_files.json --output …/chat_output` | 0 | 从`/tmp`正确读取批次JSON所在目录的相对聊天文件；输出42行、33个待解决、测试财务草稿。 |

保存后用openpyxl复读了两份订单。示例文件名和工作表都标明测试、下单需求和待复核；A1批注说明公司、日期和编号未确认，D1批注含目录来源、版本和SHA。星月输出42条订单行，D列原生批注42条，黄色32行、红色1行、无异常色9行；33行异常的A:J整行着色，G/I/J在无依据时为空。所有B列订单号为空，尾行写“已确认金额小计”`232.9685`，批注列出33条未计项目。两个“菜花6斤”都保留并写`duplicate_event`；缺单位的“孜然粉2”、无数量的萝卜丁咸菜、饺子皮个数换算及11月4/5日期问题均在行批注中出现。

## 发现的候选约束缺陷

| 位置 | 观察 | 影响 |
|---|---|---|
| 星月原文17行“酸菜1箱”→输出`下单需求_待复核!D15` | 批注所称7个“合理候选”含750g单袋、500g单袋和鱼酸菜；箱装与品类约束没有筛净。 | 用户选择时会看到不满足需求的候选；不能按最高标价跨袋/箱比较。 |
| 星月原文31行“鸡丸1袋”→`D29` | 候选含“安井鸡肉丸 称重”500g，目录没有其为袋装的依据。 | 包装约束没有完整作用。 |
| 星月原文49行“萝卜丁咸菜大袋的”→`D43` | 原文无数量，目录无可确认的萝卜丁咸菜；却将雪里红咸菜、芥菜疙瘩等不同品种列为“合理候选”并标`ambiguous`。 | 候选过宽，可能误导人工选品；目前金额仍为空，没有实际误记账。 |

文档的“全部合理候选”要求是正确的，实际导出实现还需严格应用包装/品类及明确食材约束；这里没有修改主实现。两次运行的manifest分别记录`rules_sha256=4b664eff…`及`a821494c…`，表明这段时间主实现发生变化，不能用两次产物之间的差异推断规则回归。

本轮没有真实公司、号段、实称或财务确认，不验证正式发布和记账；也未在千问办公、WorkBuddy或真实系统导入。当前证据证明本地预览、相对路径、持久目录和异常批注机制可运行，但候选质量仍有上述缺陷。

## 复跑修正记录（2026-11-07）

在独立运行记录之后重新运行当前实现，候选筛选已收窄：酸菜候选排除鱼酸菜、调料、馅及缺少整件/多包装依据的单袋项；星月“酸菜1箱”现只保留`80001926`、`80004109`两条十袋规格候选，仍缺每箱件数/净重而不自动计价。鸡丸只保留有明确袋装规格的目录项；“萝卜丁咸菜大袋的”因无同品种目录项改为`unmapped`，不再列其他咸菜品种。最新三站回放的星月仍为42行、30行待处理、测试草稿；全部未决候选均未自动确认。日期策略为`by_date`但尚未确认时只生成待复核草稿并写入阻塞，确认后需拆批处理。上表记录的是首次使用时的历史缺陷，不代表当前输出仍含该问题。
