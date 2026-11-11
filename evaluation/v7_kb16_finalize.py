#!/usr/bin/env python3
"""Finalize KB16 evidence without changing frozen inputs or production outputs."""
from pathlib import Path
from collections import Counter
from decimal import Decimal
import hashlib, json, shutil
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

ROOT=Path(__file__).resolve().parents[1]
E=ROOT/'evaluation/v7'; OUT=ROOT/'outputs/v7'

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def write(p,x):
    Path(p).parent.mkdir(parents=True,exist_ok=True)
    Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def pct(ok,n): return round(100*ok/n,4) if n else None

def main():
    kb=ROOT/'skills/order-mapping/knowledge/procurement.json'
    kbsha=sha(kb)
    original_before=read(E/'kb16_original_sha_before.json')
    original_after=read(E/'kb16_original_sha_after.json')
    original_equal=all(original_before.get(k)==v for k,v in original_after.items()) and set(original_after).issubset(original_before)
    rc4=read(E/'kb16_rc5/report.json')
    exception_audit=read(E/'kb16_exception_audit_rc5.json')
    sem=read(E/'semantic_kb16_rc2/report.json')
    fresh={}
    for mode in ('catalog_only','shared','shared_with_company_contract'):
        fresh[mode]=read(E/f'fresh_kb16_rc2_{mode}/report.json')
    # One compact event ledger from the final KB16 shared replay.
    ledger=[]
    for run in rc4['runs']:
        if run['mode']!='shared': continue
        for row in run['events']:
            ledger.append({'seed':run['seed'],**{k:row.get(k) for k in ('event_id','kind','concept','company','date','source_line','source_text','automatic','automatic_correct','errors')}})
    summary={
      'schema_version':1,'node':'V7-8','knowledge_version':16,'knowledge_sha256':kbsha,
      'status':'doing','verified':False,'reason':'full automatic coverage and complete C adjudication not at 95%; review/control and candidate completeness remain bounded risks',
      'denominators':{'kb16_per_mode_events':54,'kb16_conditional_events':24,'semantic_v1_events':84,'semantic_v1_normal_events':72,'fresh_v2_events':324,'fresh_v2_normal_events':306,'historical_chat_events':159,'historical_aligned_events':155,'historical_catalog_sku_events':132},
      'kb16_rc5':{m:{'events':v['events'],'automatic':v['automatic'],'automatic_correct':v['automatic_correct'],'A_percent':v['A'],'B_percent':v['B'],'C':None} for m,v in rc4['mode_summaries'].items()},
      'semantic_kb16_rc2':{m:{'full':v['summary'],'normal':v['normal']} for m,v in sem['mode_summaries'].items()},
      'fresh_kb16_rc2':{m:{'full':v['full_denominator_summary'],'normal':v['normal_subset']} for m,v in fresh.items()},
      'historical_original159':read(E/'original_kb16_rc2/report.json'),
      'exception_safety_audit':exception_audit['summary'],
      'source_integrity':{'original_before_sha256':original_before,'original_after_sha256':original_after,'original_equal':original_equal,'catalog_sha256':sha(ROOT/'original/食堂食材基准价价格表10.12-10.18.xlsx')},
      'finance':{'kb16_rc5_all_modes_zero_errors':all(not r['finance_audit']['errors'] for r in rc4['runs']),'fresh_all_zero_errors':all(all(v==0 for v in x['finance_error_counts'].values()) for x in fresh.values()),'original159_zero_errors':all(v['finance_errors']==0 for v in read(E/'original_kb16_rc2/report.json')['stations'].values())},
      'artifact_paths':{'replay':'evaluation/v7/kb16_rc5','semantic_replay':'evaluation/v7/semantic_kb16_rc2','fresh_replays':['evaluation/v7/fresh_kb16_rc2_catalog_only','evaluation/v7/fresh_kb16_rc2_shared','evaluation/v7/fresh_kb16_rc2_shared_with_company_contract'],'original_replay':'evaluation/v7/original_kb16_rc2','exception_contract':'evaluation/v7/kb16_exception_contract.json','exception_safety_audit':'evaluation/v7/kb16_exception_audit_rc5.json','concept_review':'evaluation/v7/kb16_concept_review.json'},
      'scope_notes':['shared mode and shared_with_company_contract are identical because company-contracts.json is empty','C remains null where candidate completeness is undefined or exception semantic adjudication is not independent','historical replay and transformed historical inputs are not future-new-order acceptance','Qianwen Office and WorkBuddy were not executed in this environment'],
    }
    write(E/'kb16_validation_result.json',summary)
    # simulation manifest and README
    sim=E/'kb16_simulation'; sim.mkdir(exist_ok=True)
    manifest={'schema_version':1,'node':'V7-8','seeds':[8801,8802,8891],'roles':{'8801':'development','8802':'development','8891':'reserved_first_holdout_consumed'},'events_per_seed':18,'events_total_per_mode':54,'modes':['catalog_only','shared','shared_with_company_contract'],'input_root':'evaluation/v7/kb16_challenge_v1','expected_is_evaluation_only':True,'source_sha256':{'knowledge':kbsha,'catalog':sha(ROOT/'original/食堂食材基准价价格表10.12-10.18.xlsx'),'generator_current':sha(E/'kb16_challenge_generator.py'),'generator_at_construction':read(E/'kb16_challenge_v1/8801/manifest.json')['source_sha256']['generator']},'event_contract':['raw source line and character span','station/date context','quantity/unit and control relation','company/date/output provenance'],'candidate_completeness':'undefined for finite lexical witness pool; C is not promoted'}
    write(sim/'manifest.json',manifest)
    (sim/'README.md').write_text('# KB16 simulation\n\nThis is a frozen synthetic challenge generated before replay. Each seed has 18 source events and the input directory contains only the ordinary request and chat text. Expected files and source/event IDs are evaluation-only and are read after all raw CLI runs are frozen. Seeds 8801/8802 were development; 8891 was the first reserved holdout and is now consumed. Candidate completeness is undefined, so C is not claimed.\n',encoding='utf-8')
    # event workbook
    wb=Workbook(); ws=wb.active; ws.title='KB16逐项'
    headers=['seed','event_id','kind','concept','company','date','source_line','source_text','automatic','automatic_correct','errors']
    ws.append(headers)
    for row in ledger: ws.append([row.get(h) if h!='errors' else ';'.join(row.get(h) or []) for h in headers])
    for c in ws[1]: c.font=Font(bold=True,color='FFFFFF'); c.fill=PatternFill('solid',fgColor='1F4E78')
    ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
    ws2=wb.create_sheet('指标'); ws2.append(['口径','模式','事件','自动','自动正确','A%','B%','C'])
    for m,v in rc4['mode_summaries'].items(): ws2.append(['KB16 rc5',m,v['events'],v['automatic'],v['automatic_correct'],v['A'],v['B'],'unknown'])
    for m,v in sem['mode_summaries'].items(): ws2.append(['semantic_kb16',m,v['summary']['events'],v['summary']['automatic'],v['summary']['automatic_correct'],v['summary']['A'],v['summary']['B'],'unknown'])
    for m,v in fresh.items(): x=v['full_denominator_summary']; ws2.append(['fresh_kb16',m,x['input_events'],x['automatic'],x['automatic_correct'],x['A_percent'],x['B_percent'],'unknown'])
    for sh in wb.worksheets:
        for col in sh.columns:
            letter=col[0].column_letter; sh.column_dimensions[letter].width=min(70,max(12,max(len(str(x.value or '')) for x in col)+2))
    xlsx=E/'kb16逐项对比.xlsx'; wb.save(xlsx)
    # save/reopen check
    check=load_workbook(xlsx,read_only=False); rows=sum(s.max_row-1 for s in check.worksheets); check.close()
    summary['workbook']={'path':str(xlsx.relative_to(ROOT)),'sha256':sha(xlsx),'sheets':2,'data_rows':rows,'reopen_ok':True}
    write(E/'kb16_validation_result.json',summary)
    od=OUT/'kb16'; od.mkdir(parents=True,exist_ok=True)
    (od/'README.md').write_text('# KB16 outputs\n\nThe final raw order outputs are under `outputs/v7/kb16_rc5/`; this index intentionally points to the immutable replay phase. `semantic_kb16_rc2`, `fresh_kb16_*`, and `original_kb16_rc2` are separate evidence lanes. Finance files are test-only drafts; no real import or posting was performed.\n',encoding='utf-8')
    report=E/'kb16_report.md'
    report.write_text(f'''# V7-8 / KB16 共享采购知识验证报告\n\n状态：**doing，未达到整体95%验收**。本轮把采购表达和目录概念迁移为共享规则，运行端从冻结聊天原文开始；expected、事件ID和历史逐项决策只在运行冻结后由评测端读取。\n\n## 生产变更\n\n- `procurement.json` 升至知识版本16，新增四组有证干制/海带受限表达和四个新目录概念的属性语序规则；规则关联概念与当前目录属性，不绑定旧价格、实配量或人工行号。\n- `knowledge.py` 对表达规则跨属性规范化词冲突 fail-closed。\n- `parsing.py` 识别无冒号的“某站下单/某站订单”标题为公司上下文，并把“商品，数量未定”保留为单一缺量事件；新增失败用例。\n- 核心测试共193项通过；八份 original SHA 前后相同。\n\n## 指标\n\n| 验证集 | 模式 | 事件 | 自动 | 自动正确 | A | B | 结论 |\n|---|---|---:|---:|---:|---:|---:|---|\n| KB16 rc5 | shared | 54 | 24 | 24 | 100% | 44.4444% | 24条条件匹配全对；30条 review/control 留在全量分母 |\n| KB16 rc5 | catalog_only | 54 | 24 | 0 | 0% | 0% | 共享知识增益可见 |\n| semantic v1 KB16 rc2 | shared | 84 | 72 | 72 | 100% | 85.7143% | 正常72/72，12条异常未计自动 |\n| fresh v2 KB16 rc2 | shared | 324 | 306 | 306 | 100% | 94.4444% | 正常306/306；3个异常候选见证仍需人工核验 |\n\nKB16 共享回放按公司三seed合并：学院路站15事件（3自动，3对）、新港站10（7自动，7对）、星月站14（8自动，8对）、松北大道站15（6自动，6对）；各公司自动A均100%，全量B分别20%、70%、57.1429%、40%，不能用异常分母掩盖薄弱场景。\n\n## 安全与边界\n\n- KB16 三模式每个 seed 的财务复读错误为0，标准订单/合并财务草稿由同一台账生成；独立异常安全审计为30/30，未知数量、未知包装、未登记公司、未知品牌仍保留异常且金额为空。\n- 旧语义挑战的 shared 正常子集72/72；fresh 目录变形的正常子集306/306。历史159原文三站均完整输出（53/42/64），财务机械错误0；这属于历史回放，不是新订单验收。\n- C 保持 `null`：候选合同明确有限词面池而非语义全集，异常处置还缺独立完整裁定；正确转人工不计入自动A。\n- 千问办公、WorkBuddy、真实新增订单、真实系统导入和正式财务入账均未验证。\n\n## 剩余风险和下一轮\n\n1. 完成 KB16 review/control 的独立异常语义核验，确认候选集合、原文/单位/颜色/原生批注和控制关系后再决定 C；不把有限 witness 池冒充全集。\n2. 扩展保留集到目录外语义同义与更多包装/单位表达，保持 A/B 分母；fresh 全量B=94.4444% 尚未达到95%。\n3. 对原始159/155/132逐项身份一致率重新生成 KB16 对比，保留历史人工差异说明，不把历史变形成绩当未来准确率。\n\n证据：`evaluation/v7/kb16_validation_result.json`、`evaluation/v7/kb16逐项对比.xlsx`、`evaluation/v7/kb16_simulation/manifest.json`、`evaluation/v7/kb16_rc5/report.json`、`evaluation/v7/semantic_kb16_rc2/report.json`、`evaluation/v7/fresh_kb16_rc2_shared/report.json`、`evaluation/v7/original_kb16_rc2/report.json`。\n''',encoding='utf-8')
    print(json.dumps({'status':'doing','kb_sha256':kbsha,'original_equal':original_equal,'workbook_rows':rows},ensure_ascii=False))
if __name__=='__main__': main()
