#!/usr/bin/env python3
"""Publish KB17 evidence without changing frozen inputs or prior reports."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / 'evaluation/v7'

def read(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))

def write(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    dev = read(E/'kb17_transfer_final_dev/audit/report.json')
    held = read(E/'kb17_transfer_final_holdout/audit/report.json')
    old = read(E/'kb17_oldlane_regression.json')
    identity = read(E/'kb17_final_history_identity.json')
    original = read(E/'original_kb17_final/report.json')
    limits = read(E/'source_fact_limits_review.json')
    before = read(E/'kb17_original_sha_before.json')
    after = {str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT/'original').iterdir()) if p.is_file()}
    write(E/'kb17_original_sha_after.json',after)
    assert len(before)==len(after)==8 and before==after, 'original files changed'
    assert original['total_events']==159 and original['source_equal']
    ks = ROOT/'skills/order-mapping/knowledge/procurement.json'
    kb = read(ks)
    assert kb['knowledge_version']==17
    assert sha(ks)==old['kb_sha256']
    shared_identity = next(v for k,v in identity['groups'].items() if k.endswith('/shared'))
    tables = []
    def compact(m):
        return {k:m.get(k) for k in ('events','input_events','automatic','automatic_correct','A','A_percent','B','B_percent','C','C_percent','business_processing_rate','missing_outputs','unexpected_outputs','error_counts') if k in m}
    metrics = {'new_development':{k:compact(v) for k,v in dev['mode_summaries'].items()},
               'first_holdout':{k:compact(v) for k,v in held['mode_summaries'].items()},
               'old_regression':old['lanes']}
    checks = {'originals_sha_equal':True,'original_events':159,'original_station_counts':{k:v['event_count'] for k,v in original['stations'].items()},
              'core_tests':{'run':200,'passed':200,'log':'evaluation/v7/kb17_core_final.log'},
              'independent_adversarial_probe':read(E/'kb17_independent_boundaries_final.json')['summary'],
              'native_workbook_exception_probe':{'checked_rows_across_three_modes':72,'passed':72,'scope':'missing quantity/unit only; candidate universe unknown'},
              'unmet_all_exception_requirements':{'fresh_candidate_witness_missing':3,'candidate_completeness':'unknown','holdout_split_normal_events':4},
              'actual_agent_semantic_evaluation':'separate lane; see agent report when available',
              'package':read(E/'package_check_kb17_final/report.json')}
    result = {'schema_version':3,'node':'V7-9 / KB17','status':'doing','verified':False,
              'reason':'Held-out normal events 0/24 automatic; full B is 0/32. Development normal 48/48 does not establish generalization; C incomplete.',
              'knowledge_version':17,'knowledge_sha256':sha(ks),'knowledge_rules':{'concept':len(kb['rules']),'unit':len(kb['unit_rules']),'expression':len(kb['expression_rules'])},
              'metrics':metrics,'fixed_history':shared_identity,'denominators':{'original_events':159,'aligned':155,'current_catalog_human_sku':132,'stations':{'songbei':53,'xingyue':42,'xueyuan':64},
              'new_development':64,'first_holdout':32,'new_total':96,'independent_product_concepts':12,'new_development_products':6,'first_holdout_products':6,'new_seeds':[9001,9002,9091]},
              'bounds':{'synthetic_new_challenge':{'events':96,'normal':72,'missing_quantity_or_unit':24,'conditional_full_B_ceiling_percent':75,'achieved_script_auto_correct':48,'achieved_full_B_percent':50,'not_real_business_prevalence':True},'history':limits['summary']},
              'checks':checks,'boundaries':['historical regression is not unseen accuracy','9091 first failure preserved; now consumed','all prices assume provided catalog is active for synthetic orders','C unknown where semantic candidate universe unproven','Qwen Office and WorkBuddy, real new orders, import and formal accounting unverified']}
    agent_path=E/'kb17_agent_semantic_audit/report.json'
    if agent_path.exists():
        result['agent_semantic_forward']=read(agent_path)
        checks['actual_agent_semantic_evaluation']={'report':str(agent_path.relative_to(ROOT)), 'events':32, 'normal_automatic_correct':24, 'A':100, 'B':75, 'C':None, 'attempts':2, 'scope':'local Agent-assisted; executor knows CLI aggregate failure; no oracle read; not strict blind or host acceptance'}
    write(E/'kb17_validation_result.json',result)
    write(E/'kb17_replay_audit.json',{'schema_version':1,'node':'V7-9','status':'doing','metrics':metrics,'source_reports':[
        str(p.relative_to(ROOT)) for p in [E/'kb17_transfer_final_dev/audit/report.json',E/'kb17_transfer_final_holdout/audit/report.json',E/'kb17_oldlane_regression.json',E/'original_kb17_final/report.json',E/'kb17_final_history/replay_audit.json',E/'kb17_final_history_identity.json']],
        'original_sha_equal':True,'scoring':'actual automatic denominator for A; input-only denominator for B; C unknown'})
    sim=E/'kb17_final_simulation';sim.mkdir(exist_ok=True)
    paths=[E/'kb17_transfer_challenge'/str(s)/'manifest.json' for s in (9001,9002,9091)]
    write(sim/'manifest.json',{'schema_version':1,'knowledge_sha256':sha(ks),'new_transfer':{'seeds':[9001,9002,9091],'events_per_seed':32,'normal_per_seed':24,'review_per_seed':8,'concepts':12,'development_products':6,'heldout_products':6,'heldout_first_result':'0/24 normal; now consumed','manifests':{str(p.relative_to(ROOT)):sha(p) for p in paths}},'historical_replay':'evaluation/v7/kb17_final_history/replay_audit.json','fixed_denominators':{'chat':159,'aligned':155,'catalog_human_sku':132},'runtime_input_boundary':'raw chat, ordinary companies/date, current catalog and formal knowledge only; oracle and IDs scoring side only'})
    (sim/'README.md').write_text('''# KB17模拟与运行索引\n\n新挑战9001/9002开发、9091首次保留，各32事件（24正常、8缺量/缺单位），合计12商品概念；开发与保留商品及模板分离。9091首次失败0/24正常自动，现已消费；不能再称未见。三模式重复运行不是新增真实样本。manifest索引原始生成span/来源和冻结文件；运行端只用普通input，不传expected、模拟事件ID和公司元数据。旧历史159/155/132独立回放，不与新挑战合并美化分母。\n''',encoding='utf-8')
    lines=['# V7-9 / KB17 共享采购知识优化与独立验证','',
           '**当前状态：doing，整体95%目标未达成。** 生产为KB17，93概念、2单位、1受限关系表达规则；八份原件只读且前后SHA相同。','',
           '真实原文CLI流程是：聊天 → 公司/日期上下文 → 商品及事件解析 → 共享知识/当前目录 → 数量和金额 → 标准订单/财务草稿。三模式使用同一冻结预期，运行端不接收评测ID或历史逐项答案。当前公司特殊约定为空，shared与合同模式应相同。','',
           '| 验证轨（共享模式） | 全量 | 实际自动 | 可证明自动正确 | A | B | 正常子集 |','|---|---:|---:|---:|---:|---:|---|']
    entries=[('新开发9001/9002',dev['mode_summaries']['shared'],'48/48'),('首次保留9091',held['mode_summaries']['shared'],'0/24')]
    for label,key,normal in [('旧v1严格预期','v1_strict','另见逐项'),('旧v2另版v3预期','v2_v3_oracle','56条件正常另报'),('旧语义回归','semantic','72/72')]:
        entries.append((label,old['lanes'][key]['modes']['shared']['full'],normal))
    entries.append(('目录全名迁移旧回归',old['lanes']['fresh']['shared']['full'],'306/306'))
    if agent_path.exists():
        entries.append(('本地Agent辅助9091（revision2）',read(agent_path)['summary'],'24/24；非严格盲评'))
    for label,m,norm in entries:
        lines.append(f"| {label} | {m.get('events',m.get('input_events'))} | {m['automatic']} | {m['automatic_correct']} | {m.get('A',m.get('A_percent'))} | {m.get('B',m.get('B_percent'))} | {norm} |")
    lines += ['', 'A按实际自动完成分母；B按全量输入分母；正确转人工不计入自动正确。新挑战合计shared自动48/96，A100%、B50%；正常48/72。开发四公司各12/12正常自动，首次保留四公司各0/6；无公司达到保留正常95%。数量前置/后置开发各24/24，保留各0/12。开发订单组168、保留84为三模式累计，不是独立业务订单数。', '',
              '仅目录：新开发正常0/48、B0；加入共享知识：48/48、全量B75；再叠加空公司合同无额外增益。首次保留三模式均B0，说明通用表达迁移仍不足。三个seed共96条是合成变体，实际独立商品概念为12（开发6、保留6），不能冒充96个真实采购样本。', '',
              '本轮修复了需求关系词、已知缺量说明的逗号边界、加工备注与规格中附加属性丢失、字面命中遮蔽歧义，以及两版评分器的分母和控制自证问题。先留红测试与首轮结果再修复；200核心测试及12/12独立反例回归通过。目录改价/删项/规格变更、未知包装和品牌/部位反例仍在核心测试。共享规则动态解析当前目录，不绑定旧SKU、价格和人工实配量。', '',
              '独立保留失败：的干款、要干的某品、要求冷冻、的冰鲜款等结构超出规则覆盖；4条要求冷冻与数量同句被拆分。首次结果保持，不修改冻结预期。9091已转已见回归；下一轮需通用结构修复和新的独立前向验证，不能只反复重跑该fold。详细原因见[准确率下降因素](kb17_accuracy_factors.md)及[首次保留审查](kb17_transfer_final_review.md)。', '',
              '异常独立探针：三模式72条缺量/缺单位行，原文、颜色、原生批注和空金额检查72/72。各运行财务机械审计无金额错误。该检查不证明全部语义候选完整；旧fresh还有3条候选见证未展示，C继续null，不能声称异常验收全部通过。', '',
              '历史固定分母：strict_identity=80/132=60.6061%；相同正确数覆盖155对齐为51.6129%，覆盖159聊天为50.3145%。松北25/44=56.8182%、星月23/35=65.7143%、学院32/53=60.3774%，三站均低于95；微平均60.6061%、宏平均60.9700%，最差松北。undefined=0、缺输出/多输出/错公司=0；未选35、不同历史SKU17；聊天有而人工未对齐4条，人工154行中153行有聊天对应、1行无聊天对应；155个对齐链接到153个人工行，多出2个合并事件链接。这些事件均不从分母删除。', '',
              '原始三站159事件及合并财务159明细复读通过，订单matched82仅表示当前脚本状态，未作为独立全字段正确率。历史540CLI（3seed×3模式×180运行）仅是历史回归。17条经独立支持的硬事实缺口给历史B宽松上界142/159=89.31%，并非已实现结果；新挑战人为24条缺量/缺单位使其B条件上界75%，这一测试配比不是实际客户异常比例。所有逐项依据保留在source_fact_limits_review.json。', '',
              '本地Agent主动语义审阅另轨：仅使用skill、正常原文和当前目录，32事件24正常全字段正确、8缺量/单位安全待核；A100%、B75%，C仍null。28份订单/财务、合并32明细、8异常两表原生批注/颜色/空金额独立复读通过；147个冻结文件哈希一致。执行方已知CLI总体失败且有两次本批尝试，因此这是项目上下文中的Agent辅助试验，不是严格全盲。评分方保管oracle，没有将答案给执行方；审计封装只在内存补source_name和轨道标签，原结果未改。这一轨不能替换规则CLI首次0/24，也不能证明未来真实订单。', '',
              '千问办公、WorkBuddy、真实新增订单、订单系统导入、外部上传或正式财务入账均未执行。portable包仅本机解包、184测试（4跳过）和53条原文CLI通过；不等同所有宿主可用。', '',
              '交付：`skills/order-mapping/`、`dist/order-mapping-v7-kb17-final.zip`、`kb17逐项对比.xlsx`、`kb17_validation_result.json`、`kb17_replay_audit.json`及`outputs/v7/`对应原始订单、财务草稿和来源manifest。']
    (E/'kb17_report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    current='''<!-- KB17_AUTHORITY -->\n## 当前证据：V7-9 / KB17（doing）\n\n生产KB17；最新报告 `evaluation/v7/kb17_report.md`，结构化结果 `evaluation/v7/kb17_validation_result.json`。开发正常48/48但首次独立保留正常0/24，故整体95未达；9091现已消费。200核心与12独立反例通过，原始159及合并财务159行复读/SHA通过；历史身份80/132。旧KB16/KB15标记均为历史。独立Agent语义前向另轨，候选全集/真实宿主/新订单未验证。\n<!-- KB17_AUTHORITY_END -->\n\n'''
    for name in ('task_plan.md','执行清单.md'):
        p=ROOT/name;s=p.read_text()
        if '<!-- KB17_AUTHORITY -->' not in s:p.write_text(current+s,encoding='utf-8')
    out=ROOT/'outputs/v7/README.md';s=out.read_text()
    if '<!-- KB17_AUTHORITY -->' not in s:out.write_text(current+s,encoding='utf-8')
    # The generic entrypoints identify the newest evidence; older versioned reports are untouched.
    write(E/'validation_result.json',result)
    p=ROOT/'evaluation/v7_report.md';s=p.read_text()
    if '<!-- KB17_AUTHORITY -->' not in s:p.write_text(current+s,encoding='utf-8')
    print('KB17 evidence published as doing; first holdout failure preserved')

if __name__=='__main__':
    main()
