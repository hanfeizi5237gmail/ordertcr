"""Replay real source chats without treating historical manual edits as truth."""
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import time
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/order-mapping/scripts'))
from order_mapping_core.catalog import CatalogStore
from order_mapping_core.workflow import process_batch, merge_finance
from order_mapping_core.money import sum_amounts, text_number


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def verify(manifest, text, decisions):
    order = manifest['order']
    rows = order['rows']
    sheet = load_workbook(manifest['files']['standard']['path']).active
    assert sheet.max_column == 10
    assert sheet.max_row == len(rows) + 2
    assert manifest['catalog']['sha256'] in sheet['D1'].comment.text
    selected_by_line = {row['line_id']: row for row in rows}
    for line_id, decision in decisions.items():
        row = selected_by_line[line_id]
        assert row['selected']['code'] == decision['code']
        assert row['decision'] == decision
        if row['status'] != 'matched':
            assert row['amount'] is None
    checks = 3
    for index, row in enumerate(rows, 2):
        assert text[row['char_start']:row['char_end']] == row['raw_text']
        comments = '\n'.join(sheet.cell(index, col).comment.text for col in range(1, 11) if sheet.cell(index, col).comment)
        assert row['raw_text'] in comments
        if row['status'] == 'matched':
            assert sheet.cell(index, 4).value == row['selected']['name']
            assert sheet.cell(index, 5).value == row['selected']['spec']
            assert abs(Decimal(str(sheet.cell(index, 10).value)) - Decimal(row['amount'])) < Decimal('0.000000001')
        else:
            product_known = row.get('product_status') == 'matched'
            assert sheet.cell(index, 4).value == (row['selected']['name'] if product_known else row['name'])
            assert sheet.cell(index, 5).value == (row['selected']['spec'] if product_known else row.get('unit'))
            assert sheet.cell(index, 10).value is None
            if product_known:
                assert Decimal(str(sheet.cell(index, 7).value)) == Decimal(row['selected']['price'])
            else:
                assert [sheet.cell(index, col).value for col in [7, 9]] == [None, None]
            assert sheet.cell(index, 4).fill.fgColor.rgb[-6:] == ('FCE4D6' if row['status'] == 'unmapped' else 'FFF2CC')
            for candidate in row['candidates']:
                for key, label in [('code', '材料编号'), ('name', '商品名称'), ('spec', '规格'), ('price', '挂牌价')]:
                    assert f'{label}={candidate[key]}' in comments
            assert '来源' in comments and '异常[' in comments
        checks += 7
    if manifest['unresolved_count']:
        assert sheet.cell(len(rows) + 2, 9).value == '已确认金额小计'
        checks += 1
    assert manifest['finance_draft'] is True
    assert order['order_number'] is None
    assert manifest['test_only'] is True
    return checks + 3 + 3 * len(decisions)


def main():
    start = time.perf_counter()
    original = ROOT / 'original'
    runtime = ROOT / 'evaluation/business_runtime'
    price = original / '食堂食材基准价价格表10.12-10.18.xlsx'
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in original.iterdir() if p.is_file() and p.suffix in {'.txt', '.xlsx'}}
    catalog = CatalogStore(runtime).register(price)
    config = {'schema_version': 1, 'companies_confirmed': False, 'companies': [
        {'company_id': 'songbei', 'full_name': '松北大道消防救援站', 'aliases': ['松北大道站'], 'finance_name': '消防队松北大道站'},
        {'company_id': 'xingyue', 'full_name': '星月街消防站', 'aliases': ['星月站'], 'finance_name': '消防队星月街站'},
        {'company_id': 'xueyuan', 'full_name': '学院路消防救援', 'aliases': ['学院路站'], 'finance_name': '消防队学院路站'}],
        'default_discount': '0.95', 'numbering': {'confirmed': False, 'prefix': 'OR_', 'daily_start': 0},
        'output_policies': {'standard_order': 'sample_exact', 'finance': 'sample_exact', 'dual_confirmed': False},
        'aliases': {}, 'unit_rules': {}, 'finance': {'confirmed': False, 'price_source': 'discounted_catalog',
        'tax_freight_confirmed': False, 'receipt_date': None, 'order_number_mode': 'OR', 'merchant_names_confirmed': False}}
    decision_file = ROOT / 'evaluation/semantic_decisions.json'
    decisions_bundle = json.loads(decision_file.read_text(encoding='utf-8'))
    # A decision bundle is keyed by global line_id; split it per chat source.
    decisions = {}
    if 'decisions_by_source' in decisions_bundle:
        decisions = decisions_bundle['decisions_by_source']
    elif isinstance(decisions_bundle.get('decisions'), dict):
        for line_id, decision in decisions_bundle['decisions'].items():
            source = line_id.rsplit(':', 2)[0] if line_id.count(':') >= 2 else ''
            decisions.setdefault(source, {})[line_id] = decision
    if decisions_bundle.get('catalog_version') and decisions_bundle['catalog_version'] != catalog['version_id']:
        raise RuntimeError('语义决策目录版本与本次当前目录不一致，请重新生成决策')
    if len(decisions_bundle['decisions']) != 18 or sum(map(len, decisions.values())) != 18:
        raise RuntimeError('前向逐项决策缺失或重复，不能继续生成回放结论')
    summaries, manifests = [], []
    for station, company_id, count in [('松北大道站', 'songbei', 53), ('星月站', 'xingyue', 42), ('学院路站', 'xueyuan', 64)]:
        chat = original / f'{station}11.3下单.txt'
        batch = {'schema_version': 1, 'batch_id': company_id + '-20261103-review', 'company': company_id,
                 'shipping_date': '2026-11-03', 'mode': 'order', 'date_policy': 'single', 'date_policy_confirmed': False,
                 'delivery_confirmed': False, 'publish': False, 'test_only': True, 'config': config,
                 'chat_files': [str(chat.relative_to(ROOT))]}
        if chat.name in decisions:
            batch['decisions'] = decisions[chat.name]
        dump(ROOT / f'evaluation/inputs/{company_id}.json', batch)
        tick = time.perf_counter()
        manifest = process_batch(runtime, batch, ROOT / 'outputs', base_dir=ROOT)
        elapsed = time.perf_counter() - tick
        assert manifest['row_count'] == count
        checks = verify(manifest, chat.read_text(encoding='utf-8-sig'), decisions.get(chat.name, {}))
        repeated = process_batch(runtime, batch, ROOT / 'outputs', base_dir=ROOT)
        assert repeated['manifest_path'] == manifest['manifest_path']
        manifests.append(manifest)
        summaries.append({'station': station, 'source_items': count, 'exported_rows': manifest['row_count'],
            'status_counts': dict(Counter(r['status'] for r in manifest['order']['rows'])), 'checks': checks,
            'agent_decisions': len(decisions.get(chat.name, {})),
            'seconds': round(elapsed, 4), 'manifest': manifest['manifest_path'],
            'standard': manifest['files']['standard']['path'], 'subtotal': manifest['totals']['standard'],
            'candidate_count': sum(len(r['candidates']) for r in manifest['order']['rows'] if r['status'] != 'matched')})
    merged = merge_finance(runtime, [m['manifest_path'] for m in manifests], ROOT / 'outputs/merged')
    book = load_workbook(merged['files']['finance']['path'])
    assert book.sheetnames == ['订单明细', '商品明细']
    assert book['商品明细'].max_row == 160
    assert book['订单明细'].max_row == 5
    assert merged['finance_draft'] is True
    expected = sum_amounts(m['totals']['finance'] for m in manifests)
    assert Decimal(merged['total']) == expected
    for row in book['商品明细'].iter_rows(min_row=2):
        if row[7].value is None:
            assert row[3].comment and row[3].fill.fgColor.rgb[-6:] in {'FFF2CC', 'FCE4D6'}
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in original.iterdir() if p.is_file() and p.suffix in {'.txt', '.xlsx'}}
    assert before == after
    result = {'passed': True, 'source_item_count': 159, 'exported_order_rows': 159, 'finance_detail_rows': 159,
        'catalog_record_count': len(catalog['records']), 'catalog_sha256': catalog['sha256'],
        'semantic_decision_bundle': str(decision_file), 'agent_decision_count': 18,
        'scenarios': summaries, 'merged_finance': merged, 'original_hashes_unchanged': before == after,
        'original_sha256': after, 'total_seconds': round(time.perf_counter() - start, 4),
        'limitations': ['示例公司、日期、实配、财务与编号未由真实使用者初始化，均为标记测试的需求复核件/预估财务草稿',
            '匹配覆盖率不是业务准确率；历史人工表的未记录改量/漏项不能作为原文自动转换真值',
            '未在千问办公、WorkBuddy、实际订单系统或财务系统验收']}
    dump(ROOT / 'evaluation/business_validation.json', result)
    lines = ['# 真实业务样例回放验证', '', '通过：159条原文全部进入标准订单及合并财务草稿。18条前向SKU决策逐项核对目录编号与证据；8份原件SHA-256未变。', '',
             '|站点|原文项|前向SKU决策|输出行|已匹配|待处理|候选数|导出秒数|', '|---|---:|---:|---:|---:|---:|---:|']
    for s in summaries:
        matched = s['status_counts'].get('matched', 0)
        lines.append(f"|{s['station']}|{s['source_items']}|{s['agent_decisions']}|{s['exported_rows']}|{matched}|{s['exported_rows']-matched}|{s['candidate_count']}|{s['seconds']}|")
    lines.extend(['', '逐行复读检查原文跨度、10列、目录来源、商品规格与价格、异常原始品名/单位、空金额、红黄颜色和完整候选原生批注；重复运行复用同一修订，未分配正式流水。', '',
        f"合并财务含3个站点、159条明细，当前已确认/预估小计{merged['total']}，不表示11月3日完整应付或已入账总额。", '', *result['limitations']])
    (ROOT / 'evaluation/business_validation.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'passed': True, 'source_items': 159, 'status_counts': [s['status_counts'] for s in summaries], 'seconds': result['total_seconds']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
