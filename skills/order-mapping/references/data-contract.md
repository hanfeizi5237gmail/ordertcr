# 数据与命令契约

本文件说明已实现CLI的结构化输入与输出；运行前用`--help`核对本包命令。样例资产是**演示输入**，不是公司、财务或编号已确认的证明。核心业务规则见[business-rules.md](business-rules.md)。

## 命令

| 命令 | 用途 |
|---|---|
| `catalog-register --runtime PATH --file XLSX [--reference]` | 校验并持久登记目录；`--reference`只存参考，不切当前指针 |
| `catalog-current --runtime PATH` | 读取当前目录及版本来源 |
| `catalog-withdraw --runtime PATH --reason TEXT` | 经有据可查的处置撤销待处理更新错误，不猜旧价有效 |
| `profile-register --runtime PATH --file JSON --source REFERENCE` | 核验真实参考文件SHA、当前目录和SKU后保存同公司偏好；不存历史实重或旧价 |
| `parse --chat TXT [--output JSON]` | 保守拆单，保留原文与问题，不宣称解决语义歧义 |
| `validate-config --config JSON` | 校验配置边界和必填项 |
| `run --runtime PATH --batch JSON --output DIR` | 用当前目录和已确认批次生成订单/复核材料及合条件的财务输出 |
| `merge-finance --runtime PATH --manifests FILE... --output DIR` | 从已导出批次manifest台账合并财务，核对相同目录版本、配置及输出指纹 |

执行器从任意工作目录可运行，本地读写UTF-8，输入不合法时非零退出并给不含堆栈的结构化错误。当前目录的`version_id`、`sha256`、`source_name`、`received_at`与记录快照持久化；记录最少有`code/name/category/spec/price/sheet/row`，价格是十进制字符串、行号为整数。新表校验失败时旧快照仍供追溯，当前处理因待解决错误而阻断；输出发布前重新检查当前指针和文件指纹。

## 项目与批次

拆单项目至少含：`line_id`、`raw_text`、`source_name`、`source_line`、`char_start`、`char_end`、`name`、`quantity`、`unit`、`notes`、`date_context`、`issues`。明确的`quantity`是十进制字符串，否则为`null`；`issues`是含`code/message`的数组。日期标题仅作为上下文；未确定数量或单位均留问题，不用默认值。

匹配后增加整行`status`（`matched`、`unmapped`、`ambiguous`、`needs_review`）、独立的`product_status`（`matched`或`unmapped`）、`quantity_status`（`matched`或`needs_review`）、`selected`、`candidates`、`invoice_quantity`、`price_unit`及疑点。商品已知而数量未知时selected仍存在；只有整行matched才有有效行金额。`candidates`是全部筛过需求约束的合理候选，而不是截断的模糊查询结果。异常也有来源、原文和状态；不能只留模型答案。

`config`当前约定`schema_version:1`，并包含以下字段：

| 字段 | 含义 |
|---|---|
| `companies`、`companies_confirmed` | `[{company_id,full_name,aliases:[],finance_name}]`及公司范围确认布尔值；示例公司不是已确认的真实主体 |
| `default_discount` | 十进制字符串，初始候选`"0.95"`，可经确认按范围调整 |
| `numbering` | `{confirmed,prefix:"OR_",daily_start}`；未确认或未定号段不分配正式订单号 |
| `output_policies` | `{standard_order,finance,dual_confirmed}`；两个策略为`sample_exact`或`line_cent`，不同策略须`dual_confirmed:true` |
| `aliases` | `{原名:SKU}`，仅限已有人工确认且对当前目录仍适用的别名 |
| `unit_rules` | `{SKU:{price_unit,request_units:{原单位:"每单位折合计价数量"},grams_per_unit,ml_per_unit,allow_fractional}}`；字段仅填写有来源的换算依据 |
| `catalog_rules_version` | 当`aliases`或`unit_rules`非空时必填本批当前目录SHA；旧SHA规则不得继续使用，须标阻塞 |
| `finance` | `{confirmed,price_source:"discounted_catalog",tax_freight_confirmed,receipt_date,order_number_mode:"OR",merchant_names_confirmed}`；正式财务需要全部前提有依据 |

批次当前约定`schema_version:1`，含`batch_id`、`shipping_date`、`company`、内嵌`config`、`mode`、`date_policy`、`date_policy_confirmed`、`delivery_confirmed`、`publish`及`items`或`chat_files`，可选`discount`。`company`是配置中的唯一`company_id`、全称或别名；`shipping_date`须明确确认并规范为`YYYY-MM-DD`；`mode`为`order`或`settlement`；`date_policy`为`single`、`consolidated`或`by_date`，若未获确认则`date_policy_confirmed:false`。`by_date`已确认时必须先按日期拆成多个批次；执行器不会静默合并不同日期，未确认时只生成待复核草稿并列出阻塞。`settlement`即使批次标记`delivery_confirmed:true`，也必须对每一行提供用户确认的`decisions[line_id].invoice_quantity`和`quantity_basis:"actual"`；批次布尔值不能把下单需求数量提升为实配数量。`publish:false`为预览/复核，不消耗正式编号。相对`chat_files`路径按**批次JSON所在目录**解析；即使从其他工作目录调用CLI也不变。

`decisions`可选，按`line_id`逐行记录`code`、`catalog_version`、必填的`reason`、`confirmed_by`，以及按需的`invoice_quantity`、`price_unit`、`quantity_basis`。当前`catalog_version`必须与本批目录一致。`confirmed_by:"agent"`还须`selection_basis:"constraints_then_highest_price"`，仅能对明确品牌/形态/包装/单位约束均满足且唯一适配或等价可比最高价唯一首选的项目使用，不写入长期`aliases`。用户覆盖数量时必须`confirmed_by:"user"`，同时提供`price_unit`和`quantity_basis`（`requested`或`actual`）及明确理由；不能由Agent擅自替用户确认实称。这些决策只适用当前具体行和目录版本。`test_only:true`只用于标明演示批次，导出也须标识测试；不能将样例的主体、日期、号段或财务状态当成真实用户授权。未确认的公司等前提可以产出标识明确的复核文件，manifest和Excel均须说明未确认内容。

每批台账保存原始文件指纹、目录版本及指纹、配置和规则版本、来源、确认与修订、订单号状态和输出指纹。状态流为`draft → needs_confirmation → ready → exported`；生成有异常行的复核订单不等于`ready`或正式入账。内容或目录版本变化后重验并生成修订，历史文件及其来源保持可查。

## 公司偏好注册

示例结构见[profile.example.json](../assets/profile.example.json)，占位SKU和SHA须替换为真实值，不能直接注册。字段如下：

```json
{
  "schema_version": 1,
  "profile_id": "company-preferences-v1",
  "company_id": "company_a",
  "catalog_version": "当前目录SHA256",
  "evidence": {"kind": "user_provided_reference", "source": "人工参考.xlsx", "sha256": "真实参考文件SHA256"},
  "aliases": {"原始品名": {"code": "当前目录材料编号", "request_units": ["袋"], "evidence": "人工表行及目录行的对应依据"}},
  "unit_rules": {}
}
```

`company_id`限1—80位字母数字、下划线、连字符；`evidence.kind`也可为`user_confirmed_preferences`，其source是保存用户明确偏好的文件。注册的`--source`须指向与SHA一致的实际文件。aliases的可选request_units限定原单位；unit_rules结构与config一致，另须非空evidence。历史重量/价格/行号答案字段会被拒绝。profile以不可变快照持久化于`runtime/profiles/<company_id>/<sha256>.json`及`current.json`；原参考文件无需复制到通用技能包。

批次自动载入同公司有效profile，显式config规则覆盖对应偏好。新目录使旧profile失效并列阻塞，仍可导出新目录复核件。manifest和ledger保存`profile_sha256`，ledger另含所用profile；缓存内容摘要包括此指纹。manifest的`product_matched_count`、`quantity_matched_count`分别表示身份/数量状态数，不等于可结算行数，更不是对人工表的准确率。

## 标准订单与财务输出

标准订单固定十列：A发货日期、B订单编号、C所属公司、D商品名称、E规格、F配货数量、G原价、H折扣率、I配货单价、J销售金额。确定行D/E/G来自同一有效SKU；`order`模式F是已确认的需求换算量，`settlement`模式F是实配计价量。异常D/E/F用原文可确定的品名、原始规格/单位与原始数量；无依据的G/I/J留真正空单元格。完整原文、来源、原因、全部合理候选与待决事项写在D列原生批注；异常颜色按[业务规则](business-rules.md#异常处理)。D1表头另有原生批注注明目录来源、版本及指纹。所有非空异常行必须进入**同一张表**，不能只放独立问题表。

商品身份已知、数量未决是上段异常原文规则的特例：D/E/G/I使用当前有效SKU，F保留原数值并批注明确原单位和尚未换算，J为空，整行浅黄。manifest金额与两本Excel小计只汇总整行`status=matched`项目；未决行的`amount/finance_amount`均为null，不把已知单价乘以未换算计件量。

`order`的订单文件名含“下单需求_待配送”，工作表标记“下单需求”；`settlement`含“配货结算”。任一模式有未解决异常时文件名另含“待人工处理”。未知金额时末尾仅为“已确认金额小计”并批注遗漏，不表示应付总额。预览和异常复核件不消耗正式编号，B列保持空值并以原生批注说明。`publish:true`且商品、数量、公司、日期和编号前提全部明确后，`order`或`settlement`均可保留正式OR号；正式`order`号只代表已确认需求单，仍非实配/结算。正式编号写为文本，输入品名不能被Excel当作公式执行。订单数据区不添加版本字段，版本在批次/核对材料和D1批注中记录。

财务工作簿包含`订单明细`与`商品明细`，两者来自同一台账。`商品明细`至少表达收货日期、订单号、商户名、商品名、与销售单位同口径的单价、销售单位、最终出库数及销售额；`订单明细`按日期、订单号、商户汇总。正式财务同时要求`settlement`、`delivery_confirmed:true`、所有商品和计价阻塞已解决，且`finance.confirmed`、`tax_freight_confirmed`、`merchant_names_confirmed`、收货日期、编号策略均有已确认依据；否则只能输出显著标识的财务草稿及已确认小计。未解决异常不写入正式财务商品行，也不填零金额；草稿说明未计原文及原因。财务价、税运、收货日期、PL号若有特定业务意义，应由使用者补充来源并确认，不能使用历史工作簿推断。

`sample_exact`保持精确行金额再求订单合计；`line_cent`逐行舍入到分后求和。两输出用不同策略时，核对材料另存订单精确合计、到分合计、财务行分位合计与尾差。每张表的数字结果应能直接复读，不依赖打开Excel才计算。
