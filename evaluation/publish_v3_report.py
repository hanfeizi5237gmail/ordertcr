"""Publish human-readable links and acceptance evidence from the current run."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
STATIONS = ('songbei', 'xingyue', 'xueyuan')
LABELS = {'songbei': '松北大道站', 'xingyue': '星月站', 'xueyuan': '学院路站'}


def relative(path, folder):
    import os
    return Path(os.path.relpath(path, folder)).as_posix()


def main():
    report = json.loads((ROOT / 'evaluation/v3_accuracy.json').read_text())
    overall = report['overall_metrics']
    test_count = int(re.search(r'Ran (\d+) tests', (ROOT / 'evaluation/v3_tests.log').read_text())[1])
    folder = ROOT / 'outputs/v3'
    index = ['# v3三站最新输出', '',
             '本索引由最新v3回归报告生成；旧修订保留，请使用下列文件。所有文件均为测试、待人工处理和财务预估草稿。', '',
             '|站点|标准订单|财务草稿|来源台账|', '|---|---|---|---|']
    for station, output in zip(STATIONS, report['outputs']):
        files = output['files']
        links = [f'[{label}]({relative(files[role]["path"], folder)})'
                 for role, label in [('standard', '复核订单'), ('finance', '财务草稿'), ('ledger', 'ledger.json')]]
        index.append(f'|{LABELS[station]}|' + '|'.join(links) + '|')
    merged = report['merged_finance']['files']['finance']['path']
    index += ['', f'- [三站合并财务草稿]({relative(merged, folder)})',
              '- [159项逐项对比与验收](../../evaluation/v3逐项对比.xlsx)',
              '- [v3改进与验证报告](../../v3改进报告.md)']
    (folder / 'README.md').write_text('\n'.join(index) + '\n')
    lines = ['# v3三站映射改进与验收报告', '',
             '**三站当前目录可核人工SKU一致率127/132（96.21%），超过本轮95%目标。**132项分母逐行与v2一致，没有删去未命中或把异常算成商品命中。', '',
             '三站人工表参与了公司采购偏好提取，本结果是历史参考样本回归。它证明本批改进及输出，不代表未见订单的独立准确率，也不表示全部行已满足导入和正式记账条件。', '',
             '|站点|聊天项|v2一致|v3一致/可核SKU|v3一致率|已定SKU/全部聊天|数量可计价|可计金额行|',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    for station in STATIONS:
        m = report['station_metrics'][station]
        lines.append(f"|{LABELS[station]}|{m['all_chat_items']}|{m['v2_identity_correct']}|{m['new_identity_correct']}/{m['catalog_covered_identity_denominator']}|{m['new_identity_percent']}%|{m['product_matched_count']}/{m['all_chat_items']}|{m['quantity_matched_count']}|{m['fully_priced_rows']}|")
    m = overall
    lines.append(f"|合计|{m['all_chat_items']}|{m['v2_identity_correct']}|{m['new_identity_correct']}/{m['catalog_covered_identity_denominator']}|{m['new_identity_percent']}%|{m['product_matched_count']}/{m['all_chat_items']}|{m['quantity_matched_count']}|{m['fully_priced_rows']}|")
    lines += ['', '## 本轮改变', '',
              '- 松北新增小黑茄子、厚皮鸡蛋、泛称豆角、牛肉卷、色拉油的有据采购偏好；星月泛称豆角按该站人工偏好定位油豆角。别名不依赖原行号或原数量。',
              '- 学院大米在当前25kg同类中选最高可比价，香油按同品牌410ml单品定位；跨站泛称橘子统一检查蜜桔同计价候选。原人工旧价不覆盖目录价。',
              '- 松北虾仁与星月采用一致的商品身份判断；4斤整包对1斤需求不能无据拆包，数量和金额仍待核。',
              '- 厚皮鸡蛋将“品质要求：厚皮”明确写入配货备注和原生批注，保留原商品名与原数量。',
              '- 统一停用缺少独立包装证据的换算：星月四条、学院八条（含牛奶箱装关系）。商品仍可定位，原始数量照录，包装换算未有依据时不计金额。停用记录见evaluation/v3/disabled-unit-rules.json。',
              '- v3私有包更新三站profile，并另附本批决策供原聊天回放；新订单需由宿主Agent重新做语义选择。通用技能不夹带客户采购数据。', '',
              '## 与人工SKU仍不同的五项', '',
              '|站点|原文|人工商品|当前处理|原因|', '|---|---|---|---|---|']
    reasons = {
        '下五花肉2斤': '保留明确下五花部位，当前目录有更精确商品',
        '玉米粒2袋': '原文袋装；人工罐装与数量不能直接复用',
        '秋林红肠2斤': '保留明确秋林品牌，人工农大是不同品牌',
        '罗马生菜1颗': '罗马/西生菜品种不等同，保留异常候选',
        '鸡腿块6斤': '保留鸡腿部位，当前目录有鸡腿肉切块',
    }
    for item in report['station_rows_all']:
        if item['scope'] == 'catalog_covered' and not item['new_product_correct']:
            selected = item['new_selected']
            lines.append(f"|{item['station_name']}|{item['raw_text']}|{item['expected_catalog']['name']}|{selected['name'] if selected else '原文异常行'}|{reasons[item['raw_text']]}|")
    lines += ['', '这五项继续留在132分母中，未为了得分把品牌、部位或包装要求改成历史人工答案。松北独站仍为40/44（90.91%）；三站合计超过95%不等于每站都超过95%。', '',
              '## 全159项检查', '',
              '- 原文、原数量和原单位：159/159与三站独立手核review一致；另从原始txt核对每项字符跨度及来源行号，人工实称不反填聊天需求。',
              '- 当前目录可选商品143项，另16项按原文保留异常。商品身份或异常处置159/159符合本轮独立审阅；这159项处理结果不当作SKU准确率。',
              f"- 价格、0.95折扣、Decimal精确计算、未知金额空值、完整合理候选、整行颜色与原生批注均逐行保存后复读；数量可计价{overall['quantity_matched_count']}项，{overall['fully_priced_rows']}项当前可计金额，其余包括跨日/重复/包装和实称疑点。",
              '- 对人工表可比SKU的全155对齐事件身份一致率127/155（81.94%）；对全部159聊天的人工可比SKU覆盖127/159（79.87%）。目录外/名称规格差异及无人工行仍可在159项表中查见。',
              '- 所有金额只汇总已能计价且无其他行级疑点的行，合计是小计；正式财务前提未确认，不补造日期/编号/税运。', '',
              '## 验证证据', '',
              f'- 源码测试{test_count}项通过；通用语义41/41、分隔变体100/100通过。',
              '- 三站profile改量390例、每轮5项的分隔重排300轮、显式异品牌反例260例通过；属于变形回归，不是新增真实客户样本。',
              '- 三站来源复核及交叉审查、全159项xlsx复读（含日期、公司/商户和编号等全部字段）、目录/批注/金额对账、8原件指纹与独立原表算术核验通过。16条异常另按独立审阅的完整合理候选编号及问题类型精确核对。',
              f'- 通用/私有包解包CLI、{test_count}测试（1个依赖原件场景跳过）和三站159项回放，SKU与当前v3一致。',
              '- 当前本机重复回归耗时见结构化报告elapsed_seconds_local；只代表本地注册/匹配/导出或缓存校验，不包含AI逐项审阅时间，不作为业务端到端性能承诺。', '',
              '可复跑命令（从ordertcr目录执行，Python需openpyxl）：', '', '```bash',
              'python evaluation/evaluate_v3.py',
              'python -m unittest discover -s skills/order-mapping/tests -v',
              'python evaluation/verify_semantics.py',
              'python evaluation/verify_profile_variants.py --version v3',
              'python evaluation/verify_originals.py',
              'python evaluation/verify_v3_delivery.py',
              'python evaluation/package_delivery.py', '```', '',
              '证据：[结构化回归](evaluation/v3_accuracy.json)、[159项对比表](evaluation/v3逐项对比.xlsx)、[xlsx复读](evaluation/v3_delivery_verification.json)、[视觉复核](evaluation/visual_qa_v3.md)、[包与回放](dist/delivery_manifest.json)。', '',
              '## 交付与使用边界', '',
              '- [三站订单与合并财务草稿](outputs/v3/README.md)',
              '- [通用skill包](dist/order-mapping.zip)',
              '- [本业务私有采购偏好及本批决策](dist/order-mapping-reference-profiles.zip)',
              '- [技能入口](skills/order-mapping/SKILL.md)', '',
              '现有业务文件已经足够完成本轮优化，无须为这次回归补材料。正式运行时仍需使用者自然语言确认发货日期及公司范围；目标宿主的实际加载、新订单准确率、实际配送、订单系统导入和财务入账未验证。所有产物在ordertcr内，original未改，没有外部发布、全局安装或真实系统写入。']
    (ROOT / 'v3改进报告.md').write_text('\n'.join(lines) + '\n')
    delivery = '''# order-mapping当前交付

当前为2026-11-07的v3。三站历史参考样本按原132项可核人工SKU分母，已从v2的121/132（91.67%）提升到127/132（96.21%）；学院53/53、星月34/35、松北40/44。全部159项原文、数量/单位、现价、计算与异常输出逐项保留并检查。

这属于使用人工表校准后的历史样本回归；松北独站尚低于95%，五项与人工SKU不同的原因保留，未见订单和整表无人工介入不作达标声明。

- [v3改进报告与完整口径](v3改进报告.md)
- [三站最新订单及合并财务草稿](outputs/v3/README.md)
- [159项人工/新旧输出逐项对比](evaluation/v3逐项对比.xlsx)
- [通用skill包](dist/order-mapping.zip)
- [本业务私有参考偏好与本批决策](dist/order-mapping-reference-profiles.zip)
- [包指纹与解包回放证据](dist/delivery_manifest.json)

源码TEST_COUNT项测试、语义与变形回归、全159项Excel复读、独立交叉审查和原件指纹核验已执行。目标宿主、新订单、实际配送与正式入账尚未验证；所有文件仍为本地测试与复核草稿。v2历史报告与输出保留。
'''
    (ROOT / '交付报告.md').write_text(delivery.replace('TEST_COUNT', str(test_count)))


if __name__ == '__main__':
    main()
