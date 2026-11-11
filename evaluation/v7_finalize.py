#!/usr/bin/env python3
"""Publish current V7 evidence indexes without rewriting frozen run results."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
E = ROOT/'evaluation/v7'


def read(path):
    return json.loads((ROOT/path).read_text())


def write(path, data):
    p=ROOT/path
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')


def sha(path):
    return hashlib.sha256((ROOT/path).read_bytes()).hexdigest()


def archive(path):
    p=ROOT/path
    backup=E/'pre_kb15_index'/path
    if p.exists() and not backup.exists():
        backup.parent.mkdir(parents=True,exist_ok=True)
        backup.write_bytes(p.read_bytes())


def main():
    semantic=read('evaluation/v7/semantic_kb15/report.json')
    fresh={mode:read(f'evaluation/v7/fresh_kb15_{mode}/report.json')
           for mode in ('catalog','shared','contract')}
    historical=read('evaluation/v7/kb15_rc1/replay_audit.json')
    identity=read('evaluation/v7/fixed_identity_kb15.json')
    original=read('evaluation/v7/original_kb15/report.json')
    combined=read('evaluation/v7/original_kb15_combined_r2/report.json')
    package=read('evaluation/v7/package_check_kb15_r2/report.json')
    first=read('evaluation/v7/semantic_audit_v1/recheck/report.json')
    agent=next(run for run in first['runs'] if run['lane']=='agent')
    group=identity['groups']['6108/single_station_split/shared']
    knowledge=read('skills/order-mapping/knowledge/procurement.json')
    frozen=read('evaluation/v7/expression_fix/source_frozen.json')
    current={path:sha(path) for path in frozen}
    before=read('evaluation/v7/expression_fix/source_before.json')
    original_equal=all(current[path]==digest for path,digest in before.items() if path.startswith('original/'))
    if current!=frozen or not original_equal:
        raise ValueError('current skill or original fingerprint differs')
    build=read('evaluation/v7/comparison_build.json')
    # Artifact exports may omit optional worksheet dimension metadata. The
    # normal reader discovers populated cells rather than trusting max_row.
    book=load_workbook(ROOT/'evaluation/v7逐项对比.xlsx',read_only=False,data_only=False)
    expected_sheets={'验收概览','逐项对比','公司与场景','固定分母身份','口径与来源'}
    if set(book.sheetnames)!=expected_sheets or book['逐项对比'].max_row!=build['detailRows']+1:
        raise ValueError('saved comparison row count or sheets differ')
    if any(cell.data_type=='e' for sheet in book for row in sheet for cell in row):
        raise ValueError('saved comparison contains spreadsheet errors')
    if book['验收概览']['F4'].value!=51 or book['验收概览']['H4'].value!=60.7143:
        raise ValueError('saved comparison current score differs')
    comparison_check={'saved_reread_passed':True,'sheets':book.sheetnames,
        'detail_rows':build['detailRows'],'error_cells':0,
        'sha256':sha('evaluation/v7逐项对比.xlsx'),
        'visual_scope':'five sheet previews reviewed; native Excel application not tested'}
    book.close()
    write('evaluation/v7/source_sha_after_current.json',current)
    if historical['execution_errors'] or historical['input_output_mismatches'] or historical['audit_error_count']:
        raise ValueError('historical mechanical audit failed')
    shared_events=[event for run in semantic['runs'] if run['lane']=='shared' for event in run['events']]
    companies={}
    for company in sorted({event['company'] for event in shared_events}):
        rows=[r for r in shared_events if r['company']==company and r['outcome']=='conditional_auto']
        if rows:
            correct=sum(r['automatic_correct'] for r in rows)
            companies[company]={'normal_events':len(rows),'automatic_correct':correct,
                                'B_percent':round(correct/len(rows)*100,4)}
    fresh_skus=set()
    for seed in (7601,7602,7691):
        for event in read(f'evaluation/v7/fresh_challenge_v2/{seed}/expected.json')['events']:
            if event.get('selected'):
                fresh_skus.add(event['selected'].get('sku'))
    fresh_skus.discard(None)
    result={
        'schema_version':2,'status':'doing','acceptance_verified':False,
        'current_phase':'KB15','knowledge':{'version':knowledge['knowledge_version'],
            'concept_rules':len(knowledge['rules']),'unit_rules':len(knowledge['unit_rules']),
            'expression_rules':len(knowledge['expression_rules']),
            'sha256':sha('skills/order-mapping/knowledge/procurement.json')},
        'fixed_denominators':identity['fixed_denominators'],
        'historical_identity':{'metrics':group['metrics'],'per_station':group['per_station'],
            'scope':'历史人工SKU一致率，不是独立A/B/C；跨品牌等历史差异不直接视为业务错误'},
        'historical_frozen_oracle_metrics':historical['metrics'],
        'catalog_name_synthetic':{mode:{'full':r['full_denominator_summary'],'normal':r['normal_subset']}
                                  for mode,r in fresh.items()},
        'semantic_expression_regression':semantic['mode_summaries'],
        'semantic_normal_by_company':companies,
        'initial_agent_assisted_kb14':{'seed':8791,'summary':agent['summary'],
            'normal':agent['normal24'],'scope':'本批Agent独立原文审阅辅助轨；非脚本或KB15保留集成绩；未生成合并财务'},
        'sample_scope':{'historical_source_events':159,'fresh_seeds':[7601,7602,7691],
            'fresh_events_per_mode':324,'fresh_distinct_selected_catalog_skus':len(fresh_skus),
            'semantic_seeds':[8701,8702,8791],'semantic_events_per_mode':84,
            'semantic_distinct_concepts':8,'semantic_executed_order_groups':sum(len({r['actual']['manifest_path'] for r in run['events']}) for run in semantic['runs']),
            'real_new_customer_orders':0,'note':'同概念随机变体不算新增真实业务；开发与首次保留成绩保留，修复后已消费为回归'},
        'mechanical_checks':{'core_tests':188,'core_passed':True,'extracted_tests_run':172,
            'extracted_tests_passed':168,'extracted_tests_skipped':4,'extracted_core_passed':True,
            'historical_cli_runs':len(historical['runs']),'historical_execution_errors':0,
            'historical_export_finance_errors':0,'semantic_runs':9,
            'semantic_export_finance_errors':sum(len(run['finance_audit']['errors']) for run in semantic['runs']),
            'original_output_events':original['total_events'],'original_finance_errors':0,
            'original_sha_unchanged':original_equal,'frozen_current_skill_equal':True},
        'exception_acceptance':{'C':None,'candidate_completeness':'not_proven',
            'native_comments_colors_blank_amounts':'measured mechanical checks passed; see finance audits',
            'old_315_of_324_retracted':'evaluation/v7/postfix_C_errata.json',
            'semantic_exception_checks':36,'semantic_unsafe_fields':sum(
                bool(event['errors']) for run in semantic['runs'] for event in run['events']
                if event['outcome']!='conditional_auto'),
            'scope':'指定安全字段通过不等于全部合理候选或完整C通过'},
        'three_station_combined_finance':combined,
        'source_fact_limits':{'hard_gaps':17,'semantic_undecided':9,
            'optimistic_B_ceiling':'142/159=89.31%','is_achieved_rate':False,
            'evidence':'evaluation/v7/source_fact_limits_review.json'},
        'gates':{'engineering_mechanical_checks_passed':True,'overall_A_and_B_95':False,
            'each_normal_company_95':False,'all_exception_requirements':False,
            'independent_future_customer_accuracy':False,'qwen_workbuddy_tested':False},
        'remaining':['干制品复合词与属性语序需有据概念审阅，不能全局移动单字',
            '候选全集及C尚未独立充分裁定','历史132身份与各站未达95，原文/实配冲突需区别',
            '当前17条事实缺口不能靠模型补造，历史全量B的乐观上界低于95',
            '千问办公/WorkBuddy及真实新增客户订单未验证'],
        'package':{k:v for k,v in package.items() if k!='inventory'},
        'comparison_verification':comparison_check,
        'artifacts':{'report':'evaluation/v7_report.md','comparison':'evaluation/v7逐项对比.xlsx',
            'original_orders':'outputs/v7/original_kb15','semantic_orders':'outputs/v7/semantic_kb15',
            'output_index':'outputs/v7/README.md'},
    }
    for path in ('evaluation/v7/validation_result.json','evaluation/v7/replay_audit.json',
                 'evaluation/v7_report.md','evaluation/v7/simulation/manifest.json',
                 'evaluation/v7/simulation/README.md','outputs/v7/README.md'):
        archive(Path(path))
    write('evaluation/v7/validation_result.json',result)
    write('evaluation/v7/replay_audit.json',{
        'schema_version':2,'status':'doing','current_knowledge':result['knowledge'],
        'runs':{'historical':'evaluation/v7/kb15_rc1/replay_audit.json',
                'fixed_identity':'evaluation/v7/fixed_identity_kb15.json',
                'fresh':{m:f'evaluation/v7/fresh_kb15_{m}/report.json' for m in fresh},
                'semantic':'evaluation/v7/semantic_kb15/report.json',
                'initial_agent':'evaluation/v7/semantic_audit_v1/recheck/report.json',
                'raw_original':'evaluation/v7/original_kb15/report.json'},
        'validation':'evaluation/v7/validation_result.json','historical_decision_replay':'旧v5保持性轨不计本轮共享独立成绩',
        'acceptance_verified':False})
    write('evaluation/v7/simulation/manifest.json',{
        'schema_version':2,'index_only':True,'scope':result['sample_scope'],
        'fixed_denominators':result['fixed_denominators'],
        'historical_generator':'evaluation/v7_simulation_generator.py',
        'fresh_generator':'evaluation/v7_fresh_generator_v2.py',
        'semantic_generator':'evaluation/v7_semantic_generator.py',
        'input_roots':['evaluation/v6/simulation','evaluation/v7/fresh_challenge_v2','evaluation/v7/semantic_challenge_v1'],
        'runtime_boundary':'只传正常请求、原始聊天、当前目录和正式知识；manifest/expected/来源ID仅评测端',
        'current_results':'evaluation/v7/replay_audit.json'})
    (E/'simulation/README.md').write_text('''# v7 模拟输入与冻结边界

本目录是索引。原始历史事件159、对齐155、目录可核132固定不变；真实事件源为三站53/42/64。
历史seed6108/6109/6112覆盖拆分、重排、跨公司明示归属及附加控制/异常。每个历史主场景159条；反事实和无归属输入在负向场景处理。
目录全名新概念集7601/7602为开发，7691原为保留；每seed108事件，共324，修复后均是已见回归。
独立语义集8701/8702开发、8791保留组合，每seed24正常＋4异常，8个概念各3表达；首次KB14脚本/Agent成绩单列，KB15是消费后的回归。
正常跨公司组合均在聊天内给公司和日期；未登记公司为异常，不能依manifest分单。表达集不含修改取消，不借此声称控制流程已验证。
样本源码、输入、expected和FREEZE保留原指纹；源ID/行号/预期只有评分端可见。生成器及结果路径见manifest.json。
''')
    table='\n'.join(f"| {c} | {m['automatic_correct']}/{m['normal_events']} | {m['B_percent']:.2f}% |" for c,m in companies.items())
    report=f'''# v7 共享采购知识与随机表达验证报告

当前为 **KB15；目标doing，整体95%未达成**。工程机械检查及本地包装检查通过，历史业务期望和整体验收未通过。共享知识包含85概念、2单位和1受限表达结构，随skill交付；默认适用于所有已初始化公司及新公司。公司特殊合同当前为空，显式合同模式成绩与共享模式相同。

## 当前结果与共享知识贡献

| 测试范围 | 仅目录 | 目录＋共享知识 | 共享＋明确公司合同 |
|---|---|---|---|
| 目录全名合成324事件 | A100%，B94.44% | A100%，B94.44% | 相同 |
| 较难语义表达84事件 | 自动0，A无分母，B0% | 自动正确51/84，A100%，B60.71% | 相同 |
| 语义正常72事件 | 0/72 | 51/72，B70.83% | 相同 |
| 历史当前目录SKU132 | 39/132 | 80/132（60.61%） | 相同 |

较难表达的共享增益为51条自动全字段正确，B提升60.71个百分点；目录全名集增益为0。历史身份差41条是已参与知识学习材料的回归效果，不是独立新订单准确率。两组各三seed共享商品/表达池，重复变体不算新业务。目录全名集共{len(fresh_skus)}个不同已选目录SKU；语义集8个概念、每seed28事件，当前九次执行共{result['sample_scope']['semantic_executed_order_groups']}个公司/日期订单组。

## 首次保留与Agent辅助轨

语义挑战由独立构造者冻结，执行记录声明Agent未读取expected/manifest/FREEZE/生成器；隔离依据为流程和执行记录，尚无独立文件访问审计证明。首次KB14脚本三seed0/84；8791主动Agent原文/目录语义审阅辅助轨24/28自动正确，A100%、B85.71%，正常24/24；该轨没有合并财务文件，单份订单财务复读通过。
修复后的KB15三模式为已见回归，不能称新的保留验收；Agent本批decisions不计入脚本或KB15成绩。条件性语义预期不是客户已确认真值，真实新增客户订单为0。此前目录全名集首见fresh_rc1和后续rc3均保留，不能只展示最好一次。

## 各公司和主要场景

KB15共享语义正常项如下，四公司均低于95%，不能由总体A100%掩盖：

| 公司 | 正常自动全字段正确 | 正常B |
|---|---:|---:|
{table}

数量前置15/24，后置36/48；玉竹、桂皮、鸭胸、鲐鲅分别9/9，海带扣6/9，桑葚、贡菜、烤麸分别3/9。剩余失败是语义候选待核，没有将其算为自动正确。细分全量公司/场景/概念见semantic_kb15/report.json。
目录全名正常子集306/306，各公司及主要场景通过；故意异常18条仍计入324总账，B仅94.44%。正常子集和含异常全量分别报告。

## 固定159/155/132历史口径

共享：80/132=60.6061%；同一正确数80/155=51.6129%、80/159=50.3145%。松北25/44=56.82%，星月23/35=65.71%，学院32/53=60.38%；micro60.6061%、macro60.9700%，最差松北。固定132中80一致、35未选、17不同历史SKU、形式undefined0。17不同SKU不直接代表业务错选，原文与历史实配替代需分开。
155对齐事件对应153不同人工行，2条额外合并关联；4事件无人工行。27组、4293历史事件记录无缺失/多输出/错误归属，SKU、身份结果、商品状态与KB14均无变化。旧oracle历史共享A53.0193%、B28.3043%，其undefined、人工调整及控制预期仍存在，不能当独立A/B/C。详见fixed_identity_kb15.md/json及kb15_rc1。

## 本轮修复及反例

KB14改进品牌未证参考：陌生品牌只展示当前同名无品牌基础项供核对，不自动替代；排除明确冲突包装和其他形态。尖椒知识文字与现行同层最高价规则统一。
KB15增加有边界的属性语序、调料类别标签和当前500g的括号“斤”标签归一，规则不绑定SKU/旧价。公司只决定归属。完全缺数量/单位时同时提示，已有单位的无效数量不误报缺单位。
独立审查发现并修复“鲜海菇→海鲜菇”和“冷冻干燥→冷冻属性＋其他商品”的假阳性；保留未知词、品牌、部位、包装和复合工艺。没有通用移动单字“干”来凑分；多字属性保护词表仍有限。新增反例、陌生概念、目录改价/下架/规格变更共7项定向测试，核心188项通过。
知识证据13条缺定位已补：5条目录行核实，8条语言推断如实记录原出处未保留；目录候选存在不证明所有同义。默认shared、显式合同模式、legacy_profile及宿主文档已统一。

## 异常、数量与财务安全

本轮原文三站159条全部输出；松北26自动/27待核，星月24/18，学院32/32，共82 matched、77待核。82/159是状态计数，不能称已裁定的B。实际标准订单和财务草稿见outputs/v7/original_kb15。
540次历史CLI、9次语义CLI和三模式目录全名回归均无执行/导出财务机械错误；当前原始三站及解包松北草稿复读通过。三站5个公司/日期订单组另由实际merge-finance合并为159条财务草稿，明细、金额和来源指纹复读0错误（original_kb15_combined_r2）。金额使用Decimal、默认0.95，未知数量/价格/异常金额留空；台账、订单、财务及已有合并明细/来源指纹核对通过。原生批注、颜色等机械检查有证据。
语义四类异常每seed4条、三模式共36条指定安全字段检查通过，**候选全集及必要代码的独立语义穷尽未充分证明，因此C=null，异常整体验收未通过**。目录全名旧预期仍有3个candidate witness差异，冻结预期不追改。旧315/324审计因遗漏血橙公司标题、混淆糙米身份/数量已勘误，不能引用为最终C。

## 事实限制及剩余工作

来源独立复核有17项实称、包装或指代硬缺口、9项语义未决。即使其余142条全自动正确，历史全量B宽松上界为142/159=89.31%，低于95%要求的至少152条。这不是实现率或未来订单上界。逐项证据见source_fact_limits_review.json；不要求重复提供既有材料，不从人工实配量或旧价补造事实。
可继续的工作是干制品/部分海带表达的有据概念审阅、候选全集的独立冻结预期和更多完整Agent新表达执行。每次规则修订须保留首次成绩并作反例/目录更新回归；不能通过重复跑同组或直接抄入历史逐项答案宣布泛化。

## 交付与验证范围

当前包dist/order-mapping-v7-kb15.zip，SHA256 `{package['sha256']}`。正式知识SHA256 `{result['knowledge']['sha256']}`。包内无runtime、原件、输出或宿主私有配置；解包统计172项，实际通过168项、原件相关跳过4项，项目内188项全过；quick_validate、CLI帮助、原文53事件和财务复读通过。第一次包验证因bundled Python缺PyYAML失败，日志保留；改用已有宿主validator Python后通过，没有安装全局依赖。
8原件及冻结生产skill前后SHA一致。千问办公、WorkBuddy尚未实测；Python、文件读写、持久目录状态、Excel原生批注能力见技能可移植说明。未做真实导入、上传、正式入账、Git提交推送或全局安装。
当前权威索引为validation_result.json、replay_audit.json、simulation/manifest.json、v7逐项对比.xlsx和outputs/v7/README.md。历史回放、合成首次保留、已见回归、Agent辅助和真实新增订单验收始终分列。
'''
    (ROOT/'evaluation/v7_report.md').write_text(report)
    (ROOT/'outputs/v7/README.md').write_text('''# KB15 当前订单与财务草稿

当前交付为KB15；95%整体目标doing。所有xlsx仅本地需求复核/财务草稿，未知金额为空。

- `original_kb15/`：三站原文159事件的标准订单及财务；星月保留原文多日分组。各站messages-result.json给出实际xlsx/ledger/manifest路径。
- `semantic_kb15/`：catalog_only/shared/shared_with_company_contract三模式×8701/8702/8791，每组从原始聊天执行；共享51/84全字段自动正确，正常51/72，尚未达95。
- `fresh_kb15_shared/`、`fresh_kb15_catalog/`、`fresh_kb15_contract/`：7601/7602/7691目录全名回归，各324事件、306自动正确，A100/B94.44；三个模式相同，不能证明共享知识有增益。
- `kb15_rc1/`：三seed历史变形三模式，固定132身份80一致；历史冻结oracle结果单列，不能冒充独立准确率。
- `semantic_execution/agent_8791/`：KB14独立Agent语义辅助轨，24/28自动正确，正常24/24，单份财务已核；未生成合并财务。不是KB15脚本或新真实订单成绩。
- `package_check_kb15_r2/`：解包CLI原文53条及财务草稿验证。

旧KB13/14、fresh首次及各rc目录均保留，不能将新目录名复制旧输出当本轮执行。报告、SHA、评分和当前目录/知识指纹见`../../evaluation/v7_report.md`与`../../evaluation/v7/validation_result.json`。
''')
    output_index=ROOT/'outputs/v7/README.md'
    links=['\n## 三站原文当前表格\n',
           '以下年份、公司范围和编号为test_only场景配置，未经用户正式初始化；星月按原文11月3/4/5日分别保留。\n',
           '| 站点与测试批次日期 | 标准订单 | 同批财务草稿 |', '|---|---|---|']
    for st in original['stations'].values():
        part=read(st['result'])
        for manifest_path in part['manifests']:
            manifest=json.loads(Path(manifest_path).read_text())
            date=next(event['shipping_date'] for event in part['events'] if event['manifest_path']==manifest_path)
            files=manifest['files']
            standard=Path(files['standard']['path']).relative_to(ROOT/'outputs/v7')
            finance=Path(files['finance']['path']).relative_to(ROOT/'outputs/v7')
            links.append(f"| {st['station']} {date} | [订单](<{standard}>) | [财务草稿](<{finance}>) |")
    merged_path=Path(combined['merged_finance']['files']['finance']['path']).relative_to(ROOT/'outputs/v7')
    links.append(f'\n[三站原文全部日期合并财务草稿：159条](<{merged_path}>)，5个订单组，来源/明细/金额复读0错误。\n')
    output_index.write_text(output_index.read_text()+'\n'.join(links))
    current_note=f'''## KB15 当前执行结果（目标doing）

V7-6与V7-7工程机械检查verified；历史业务期望及整体95%未通过。当前85概念/2单位/1表达结构，知识SHA `{result['knowledge']['sha256']}`；188核心全过，包内统计172项（168通过、4项原件相关跳过），解包原文53条/财务核对通过。三模式历史540CLI及固定身份80/132无退化；三站原文159全部输出、财务0错误、8原件和冻结skill SHA一致。
语义挑战首次KB14脚本0/84、Agent辅助24/28分别保留；KB15三模式回归：目录0/84，共享51/84（A100/B60.7143），正常51/72（70.8333），四公司均低于95；目录全名三模式各306/324（A100/B94.4444），正常306/306，共享增益0。C因候选全集未充分独立裁定保持null，旧315/324已勘误。17硬事实缺口下历史B宽松上界142/159=89.31%，不是已实现率。剩余干制品和部分海带表达、候选全集及更多Agent语义执行继续doing；千问办公/WorkBuddy、真实新增订单、系统导入/正式入账未验收。
当前证据：evaluation/v7_report.md、evaluation/v7/validation_result.json、fixed_identity_kb15.*、semantic_kb15、v7逐项对比.xlsx、outputs/v7/README.md；旧快照均为历史，不继承其通过状态。

'''
    for name in ('task_plan.md','执行清单.md'):
        p=ROOT/name
        text=p.read_text()
        marker='<!-- KB15_CURRENT -->'
        if marker in text:
            text=text.split('<!-- KB15_CURRENT_END -->',1)[1].lstrip()
        p.write_text(marker+'\n'+current_note+'<!-- KB15_CURRENT_END -->\n\n'+text)
    write('evaluation/v7/finalization_result.json',{'status':'doing','original_sha_unchanged':original_equal,
        'skill_sha_equal':current==frozen,'semantic_normal_by_company':companies,
        'current_outputs':result['artifacts'],'knowledge_sha256':result['knowledge']['sha256']})
    print(json.dumps({'status':'doing','knowledge':result['knowledge'],'semantic_companies':companies},ensure_ascii=False))


if __name__=='__main__':
    main()
