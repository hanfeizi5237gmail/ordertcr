"""Three-station reference-fitted regression; no manual weights become demand truth."""
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / 'evaluation'
sys.path.insert(0, str(ROOT / 'skills/order-mapping/scripts'))
from order_mapping_core.catalog import CatalogStore  # noqa: E402
from order_mapping_core.profiles import register_profile  # noqa: E402
from order_mapping_core.workflow import process_batch, merge_finance  # noqa: E402

STATIONS = ('songbei', 'xingyue', 'xueyuan')
LABELS = {'songbei': '松北大道站', 'xingyue': '星月站', 'xueyuan': '学院路站'}


def norm(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def original_hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / 'original').iterdir() if p.suffix in {'.txt', '.xlsx'}}


def aligned_reference(station):
    source = EVAL / ('xueyuan_regression.json' if station == 'xueyuan' else f'{station}_alignment.json')
    data = json.loads(source.read_text(encoding='utf-8'))
    result = {}
    for row in data['rows']:
        key = row['line_id']
        assert key not in result, f'{source.name}: duplicate {key}'
        result[key] = row
    return data, result, source


def previous_rows(station):
    path = EVAL / 'improvement_v2' / f'{station}-baseline-manifest.json'
    rows = json.loads(path.read_text(encoding='utf-8'))['order']['rows']
    return {row['line_id']: row for row in rows}


def compare(station, current, baseline, reference, catalog_by_code):
    entries = []
    for row in current:
        line_id = row['line_id']
        assert line_id in baseline and line_id in reference, f'{station}: alignment missing {line_id}'
        old = baseline[line_id]
        ref = reference[line_id]
        manual = ref.get('manual')
        manual_line = ref.get('manual_row') if station != 'xueyuan' else manual['row'] if manual else None
        expected_code = ref.get('manual_identity_code') if station == 'xueyuan' else ref.get('manual_catalog_code')
        if expected_code:
            assert expected_code in catalog_by_code, f'{station}: stale manual catalog SKU {expected_code}'
        target = catalog_by_code.get(expected_code)
        selected = row.get('selected')
        old_selected = old.get('selected')
        new_code = str(selected['code']) if selected else None
        old_code = str(old_selected['code']) if old_selected else None
        product_known = row.get('product_status') == 'matched'
        link = 'chat_only' if manual is None else 'catalog_covered' if target else 'manual_not_current_catalog'
        old_correct = bool(target and old_code == expected_code)
        new_correct = bool(target and product_known and new_code == expected_code)
        price_equal = (Decimal(str(target['price'])) == Decimal(str(manual['price']))) if target and manual else None
        quantity_equal = (Decimal(str(row['invoice_quantity'])) == Decimal(str(manual['quantity']))) \
            if row.get('invoice_quantity') is not None and manual and manual.get('quantity') is not None else None
        raw_quantity_differs = (Decimal(str(row['quantity'])) != Decimal(str(manual['quantity']))) \
            if manual and row.get('quantity') is not None and manual.get('quantity') is not None else None
        comparable_request = bool(target and ((norm(target['spec']) in {'500g', '斤'} and row.get('unit') == '斤')
                                             or norm(target['spec']) == norm(row.get('unit'))))
        demand_preserved = (row.get('invoice_quantity') is not None and
                            Decimal(str(row['invoice_quantity'])) == Decimal(str(row['quantity']))) \
            if comparable_request and row.get('quantity') is not None else None
        merged = ref.get('alignment_kind') == 'possible_merged_manual'
        exclusion = ref.get('exclusion')
        categories = []
        if manual is None:
            categories.append('chat_without_manual_row')
        elif not target:
            categories.append('manual_item_not_uniquely_in_current_catalog')
        if merged:
            categories.append('possible_manual_merge')
        if exclusion:
            categories.append('reference_conflict_or_unproven_substitution')
        if target and not new_correct:
            categories.append('sku_unresolved_or_different')
        if price_equal is False:
            categories.append('manual_price_differs_from_current_catalog')
        if quantity_equal is False:
            categories.append('manual_quantity_differs_or_conversion_unresolved')
        if row.get('product_status') == 'matched' and row.get('quantity_status') != 'matched':
            categories.append('product_known_quantity_blocked')
        item = {'station': station, 'station_name': LABELS[station], 'line_id': line_id,
                'chat_line': row['source_line'], 'date_context': row.get('date_context'),
                'raw_text': row['raw_text'], 'parsed_name': row['name'],
                'requested_quantity': row.get('quantity'), 'requested_unit': row.get('unit'),
                'manual_row': manual_line, 'manual': manual, 'expected_code': expected_code,
                'expected_catalog': target, 'scope': link, 'alignment_kind': ref.get('alignment_kind', ref.get('alignment')),
                'old_selected': old_selected, 'new_selected': selected,
                'old_product_correct': old_correct, 'new_product_correct': new_correct,
                'status': row['status'], 'product_status': row.get('product_status'),
                'quantity_status': row.get('quantity_status'),
                'new_invoice_quantity': row.get('invoice_quantity'), 'new_price_unit': row.get('price_unit'),
                'price_equal': price_equal, 'quantity_equal': quantity_equal,
                'raw_quantity_changed_in_manual': raw_quantity_differs,
                'demand_quantity_preserved': demand_preserved,
                'categories': categories, 'reference_exclusion': exclusion,
                'issues': row.get('issues', [])}
        entries.append(item)
    assert len(entries) == len(current) == len(reference)
    return entries


def station_metrics(station, entries, alignment, profile):
    linked = [x for x in entries if x['manual']]
    covered = [x for x in linked if x['scope'] == 'catalog_covered']
    compatible = [x for x in covered if not x['reference_exclusion']]
    exact = [x for x in covered if x['price_equal']]
    correct = sum(x['new_product_correct'] for x in covered)
    rules = set(profile.get('unit_rules', {}))
    quantity_reference = [x for x in covered if x['raw_quantity_changed_in_manual'] is False
                          and (x['requested_unit'] in {'斤', '个'} or
                               x['new_selected'] and str(x['new_selected']['code']) in rules)]
    aligned_manual = {x['manual_row'] for x in linked}
    manual_rows = alignment['counts']['manual_detail_rows']
    metrics = {'all_chat_items': len(entries), 'all_manual_rows': manual_rows,
               'aligned_pairs': len(linked), 'distinct_manual_rows_linked': len(aligned_manual),
               'manual_only': manual_rows - len(aligned_manual), 'chat_only': len(entries) - len(linked),
               'manual_rows_with_multiple_chat_events': sum(Counter(x['manual_row'] for x in linked)[number] > 1
                                                          for number in aligned_manual),
               'catalog_covered_identity_denominator': len(covered),
               'old_identity_correct': sum(x['old_product_correct'] for x in covered),
               'new_identity_correct': correct,
               'new_identity_percent': round(100 * correct / len(covered), 2) if covered else None,
               'source_compatible_catalog_denominator': len(compatible),
               'source_compatible_identity_correct': sum(x['new_product_correct'] for x in compatible),
               'source_conflicted_catalog_count': len(covered) - len(compatible),
               'catalog_name_spec_price_denominator': len(exact),
               'exact_price_identity_correct': sum(x['new_product_correct'] for x in exact),
               'source_unchanged_reference_qty_denominator': len(quantity_reference),
               'source_unchanged_reference_qty_correct': sum(x['quantity_equal'] is True for x in quantity_reference),
               'raw_quantity_differs_from_manual': sum(x['raw_quantity_changed_in_manual'] is True for x in linked),
               'manual_outside_catalog_aligned_count': sum(x['scope'] == 'manual_not_current_catalog' for x in linked),
               'known_identity_price_conflicts': sum(x['price_equal'] is False for x in covered),
               'all_aligned_identity_percent': round(100 * correct / len(linked), 2) if linked else None,
               'all_chat_identity_percent': round(100 * correct / len(entries), 2) if entries else None,
               'product_matched_count': sum(x['product_status'] == 'matched' for x in entries),
               'quantity_matched_count': sum(x['quantity_status'] == 'matched' for x in entries),
               'catalog_gap_or_nonunique': sum(x['scope'] == 'manual_not_current_catalog' for x in linked),
               'reference_conflict_or_unproven_substitution': sum(bool(x['reference_exclusion']) for x in entries),
               'possible_manual_merge_events': sum('possible_manual_merge' in x['categories'] for x in entries)}
    return metrics


def workbook(report, path):
    book = Workbook()
    summary = book.active
    summary.title = '概览'
    summary.append(['站点', '聊天项', '人工行', '关联聊天项', '人工仅有行', '聊天仅有项', '可核目录SKU',
                    '旧SKU一致', '新SKU一致', '新SKU准确率', '全对齐SKU覆盖率', '全聊天SKU覆盖率',
                    '目录缺口/非唯一', '人工价与目录不同', '可能合并事件', '人工差异/约束冲突',
                    '原文未标冲突SKU', '其中新SKU一致', '未标冲突SKU准确率'])
    for station in STATIONS:
        m = report['station_metrics'][station]
        summary.append([LABELS[station], m['all_chat_items'], m['all_manual_rows'], m['aligned_pairs'],
                        m['manual_only'], m['chat_only'], m['catalog_covered_identity_denominator'],
                        m['old_identity_correct'], m['new_identity_correct'], m['new_identity_percent'],
                        m['all_aligned_identity_percent'], m['all_chat_identity_percent'],
                        m['catalog_gap_or_nonunique'], m['known_identity_price_conflicts'],
                        m['possible_manual_merge_events'], m['reference_conflict_or_unproven_substitution'],
                        m['source_compatible_catalog_denominator'], m['source_compatible_identity_correct'],
                        m['source_compatible_identity_percent']])
    total = report['overall_metrics']
    summary.append(['三站合计', total['all_chat_items'], total['all_manual_rows'], total['aligned_pairs'],
                    total['manual_only'], total['chat_only'], total['catalog_covered_identity_denominator'],
                    total['old_identity_correct'], total['new_identity_correct'], total['new_identity_percent'],
                    total['all_aligned_identity_percent'], total['all_chat_identity_percent'],
                    total['catalog_gap_or_nonunique'], total['known_identity_price_conflicts'],
                    total['possible_manual_merge_events'], total['reference_conflict_or_unproven_substitution'],
                    total['source_compatible_catalog_denominator'], total['source_compatible_identity_correct'],
                    total['source_compatible_identity_percent']])
    summary.append([])
    summary.append(['口径', '人工样本参与profile拟合；准确率仅为参考样本回归，不是未见样本准确率。'])
    summary.append(['分母', '可核SKU使用当前目录中可唯一定位的人工商品身份；全对齐/全聊天覆盖率保留目录外及未决。'])
    summary.append(['原文兼容口径', '仅排除人工对齐明确标记原文约束冲突/缺少替代依据的可核SKU；其余未标记不等于业务确认可替代。'])
    summary.append(['数量', '聊天需求数量与人工实称、合并、增项分开记录；数量差异不自动判为识别错误。'])
    summary.append(['日期', '星月跨11月3/4/5日上下文保留，人工统一日期不证明已经合并发货。'])

    detail = book.create_sheet('逐项对比')
    headers = ['站点', '聊天行', '日期上下文', '原文', '原品名', '需求数', '原单位', '人工表行', '人工商品', '人工规格',
               '人工数量', '人工原价', '人工目录SKU', '当前目录价', '旧选SKU', '旧选商品', '新选SKU', '新选商品',
               '新选规格', '新挂牌价', '新计价数', '新计价单位', '旧商品一致', '新商品一致', '规格一致', '人工价=当前目录价',
               '新数=人工数', '商品状态', '数量状态', '对齐类型', '差异类别', '异常原因/人工差异']
    detail.append(headers)
    for entry in report['station_rows_all']:
        human = entry['manual'] or {}
        previous = entry['old_selected'] or {}
        selected = entry['new_selected'] or {}
        target = entry['expected_catalog'] or {}
        spec_equal = norm(selected.get('spec')) == norm(human.get('spec')) if selected and human else None
        remarks = [x.get('message', '') for x in entry['issues']]
        if entry['reference_exclusion']:
            remarks.append('人工参考冲突/缺依据：' + entry['reference_exclusion'])
        if entry['raw_quantity_changed_in_manual'] is True:
            remarks.append('人工数量与聊天需求不同，可能实称/调整，不能反填需求')
        if entry['alignment_kind'] == 'possible_merged_manual':
            remarks.append('人工可能合并多条聊天；不删除原始事件或分配实称')
        detail.append([entry['station_name'], entry['chat_line'], entry['date_context'], entry['raw_text'],
                       entry['parsed_name'], entry['requested_quantity'], entry['requested_unit'], entry['manual_row'],
                       human.get('name'), human.get('spec'), human.get('quantity'), human.get('price'),
                       entry['expected_code'], target.get('price'), previous.get('code'), previous.get('name'),
                       selected.get('code'), selected.get('name'), selected.get('spec'), selected.get('price'),
                       entry['new_invoice_quantity'], entry['new_price_unit'], entry['old_product_correct'],
                       entry['new_product_correct'], spec_equal, entry['price_equal'], entry['quantity_equal'],
                       entry['product_status'], entry['quantity_status'], entry['alignment_kind'],
                       '；'.join(entry['categories']), '；'.join(remarks)])
    for sheet, widths in [(summary, [16] + [15] * 18),
                          (detail, [14, 10, 15, 35, 18, 10, 11, 11, 34, 17, 11, 12, 17, 12,
                                    17, 28, 17, 32, 18, 12, 12, 14, 12, 12, 12, 18, 13, 16, 16,
                                    23, 38, 72])]:
        sheet.freeze_panes = 'D2' if sheet is detail else 'B2'
        sheet.auto_filter.ref = f'A1:{sheet.cell(1, len(widths)).column_letter}{4 if sheet is summary else sheet.max_row}'
        sheet.row_dimensions[1].height = 34
        for index, width in enumerate(widths, 1):
            sheet.column_dimensions[sheet.cell(1, index).column_letter].width = width
            cell = sheet.cell(1, index)
            cell.fill = PatternFill('solid', fgColor='284B63')
            cell.font = Font(name='宋体', size=10, color='FFFFFF', bold=True)
            cell.alignment = Alignment(vertical='center', horizontal='center', wrap_text=True)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical='center', wrap_text=True)
                if cell.data_type == 's':
                    cell.data_type = 's'
            if sheet is detail:
                row[23].fill = PatternFill('solid', fgColor='E2F0D9' if row[23].value is True else 'FFF2CC' if row[12].value else 'FCE4D6')
    for row in detail.iter_rows(min_row=2):
        for col in (13, 15, 17):
            row[col - 1].number_format = '@'
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    saved = load_workbook(path, read_only=True, data_only=True)
    assert saved.sheetnames == ['概览', '逐项对比']
    assert saved['逐项对比'].max_row == 1 + report['overall_metrics']['all_chat_items']
    saved.close()


def main():
    runtime = EVAL / 'v2_runtime'
    before = original_hashes()
    catalog = CatalogStore(runtime).register(ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx')
    by_code = {str(row['code']): row for row in catalog['records']}
    profiles = {}
    references = {}
    for station in STATIONS:
        profile_path = EVAL / f'{station}.history-profile.json'
        profile = json.loads(profile_path.read_text(encoding='utf-8'))
        assert profile['company_id'] == station and profile['catalog_version'] == catalog['version_id']
        register_profile(runtime, profile, source_path=ROOT / 'original' / profile['evidence']['source'])
        profiles[station] = {'path': str(profile_path), 'sha256': hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                             'alias_count': len(profile.get('aliases', {})), 'unit_rule_count': len(profile.get('unit_rules', {}))}
        references[station] = aligned_reference(station)
    results, station_rows, station_stats = [], {}, {}
    for station in STATIONS:
        batch = json.loads((EVAL / f'inputs/{station}.json').read_text(encoding='utf-8'))
        batch['batch_id'] = station + '-20261103-v2'
        batch.pop('decisions', None)  # The profile is the reference-fitted source for this comparison.
        if station == 'xueyuan':
            semantic = EVAL / 'xueyuan.semantic-v2-decisions.json'
            if semantic.exists():
                decisions = json.loads(semantic.read_text(encoding='utf-8')).get('decisions', {})
                batch['decisions'] = {k: v for k, v in decisions.items() if k.rsplit(':', 2)[1] == '64'}
        manifest = process_batch(runtime, batch, ROOT / 'outputs/v2', base_dir=ROOT)
        repeat = process_batch(runtime, batch, ROOT / 'outputs/v2', base_dir=ROOT)
        assert repeat['manifest_path'] == manifest['manifest_path']
        sheet = load_workbook(manifest['files']['standard']['path']).active
        for index, row in enumerate(manifest['order']['rows'], 2):
            product_known = row.get('product_status') == 'matched'
            assert sheet.cell(index, 4).value == (row['selected']['name'] if product_known else row['name'])
            if row['status'] != 'matched':
                assert sheet.cell(index, 10).value is None
                assert sheet.cell(index, 4).fill.fgColor.rgb[-6:] in {'FFF2CC', 'FCE4D6'}
            assert Decimal(str(sheet.cell(index, 7).value)) == Decimal(row['selected']['price']) if product_known else sheet.cell(index, 7).value is None
        alignment, by_line, _ = references[station]
        entries = compare(station, manifest['order']['rows'], previous_rows(station), by_line, by_code)
        station_rows[station] = entries
        station_stats[station] = station_metrics(station, entries, alignment, json.loads((EVAL / f'{station}.history-profile.json').read_text(encoding='utf-8')))
        results.append(manifest)
    xueyuan = station_stats['xueyuan']
    assert (xueyuan['catalog_covered_identity_denominator'], xueyuan['new_identity_correct']) == (53, 53), xueyuan
    assert (xueyuan['source_unchanged_reference_qty_denominator'], xueyuan['source_unchanged_reference_qty_correct']) == (25, 25), xueyuan
    overall = {}
    additive = [key for key, value in xueyuan.items() if isinstance(value, int) and not isinstance(value, bool)]
    for key in additive:
        overall[key] = sum(station_stats[station][key] for station in STATIONS)
    overall['new_identity_percent'] = round(100 * overall['new_identity_correct'] / overall['catalog_covered_identity_denominator'], 2)
    for metrics in (*station_stats.values(), overall):
        denominator = metrics['source_compatible_catalog_denominator']
        metrics['source_compatible_identity_percent'] = round(100 * metrics['source_compatible_identity_correct'] / denominator, 2) if denominator else None
    overall['all_aligned_identity_percent'] = round(100 * overall['new_identity_correct'] / overall['aligned_pairs'], 2)
    overall['all_chat_identity_percent'] = round(100 * overall['new_identity_correct'] / overall['all_chat_items'], 2)
    merged = merge_finance(runtime, [manifest['manifest_path'] for manifest in results], ROOT / 'outputs/v2/merged')
    assert before == original_hashes()
    report = {'passed': True, 'evaluation_type': 'historical_reference_fitted_regression_not_unseen_accuracy',
              'metrics': xueyuan, 'station_metrics': station_stats, 'overall_metrics': overall,
              'profiles': profiles, 'rows': station_rows['xueyuan'], 'station_rows': station_rows,
              'station_rows_all': [row for station in STATIONS for row in station_rows[station]],
              'outputs': [{key: m[key] for key in ('batch_id', 'row_count', 'product_matched_count',
                         'quantity_matched_count', 'unresolved_count', 'manifest_path', 'files')} for m in results],
              'merged_finance': merged, 'original_hashes_unchanged': True}
    book_path = EVAL / 'v2逐项对比.xlsx'
    workbook(report, book_path)
    report['comparison_workbook'] = {'path': str(book_path), 'sha256': hashlib.sha256(book_path.read_bytes()).hexdigest()}
    dump(EVAL / 'v2_accuracy.json', report)
    lines = ['# 三站v2人工样本回归', '', '三站人工表已参与公司/目录版本绑定的历史profile拟合。以下为参考样本回归，不是未见样本的独立准确率；人工实称、合并及增项不充当聊天数量真值。',
             '', '|站点|全部聊天项|关联人工项|可核目录SKU|旧SKU一致|新SKU一致|可核SKU准确率|未标原文冲突SKU一致|未标原文冲突准确率|全对齐SKU覆盖率|全聊天SKU覆盖率|目录缺口/非唯一|旧价差|合并事件|',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for station in STATIONS:
        m = station_stats[station]
        lines.append(f"|{LABELS[station]}|{m['all_chat_items']}|{m['aligned_pairs']}|{m['catalog_covered_identity_denominator']}|{m['old_identity_correct']}|{m['new_identity_correct']}|{m['new_identity_percent']}%|{m['source_compatible_identity_correct']}/{m['source_compatible_catalog_denominator']}|{m['source_compatible_identity_percent']}%|{m['all_aligned_identity_percent']}%|{m['all_chat_identity_percent']}%|{m['catalog_gap_or_nonunique']}|{m['known_identity_price_conflicts']}|{m['possible_manual_merge_events']}|")
    m = overall
    lines.append(f"|合计|{m['all_chat_items']}|{m['aligned_pairs']}|{m['catalog_covered_identity_denominator']}|{m['old_identity_correct']}|{m['new_identity_correct']}|{m['new_identity_percent']}%|{m['source_compatible_identity_correct']}/{m['source_compatible_catalog_denominator']}|{m['source_compatible_identity_percent']}%|{m['all_aligned_identity_percent']}%|{m['all_chat_identity_percent']}%|{m['catalog_gap_or_nonunique']}|{m['known_identity_price_conflicts']}|{m['possible_manual_merge_events']}|")
    lines += ['', '学院路既有口径：可核目录SKU 53/53；数量不变且有来源的参考比较 25/25；严格名称+规格+挂牌价分母51。',
              '', f"可核SKU准确率只用目录可唯一定位的人工商品作分母，并保留人工商品与原文冲突的{m['source_conflicted_catalog_count']}项样本。另列的“未标原文冲突”口径仅排除人工对齐已明确记载约束冲突或缺乏替代依据的可核SKU；未标记不代表业务已确认可替代。全对齐与全聊天覆盖率把目录外、未决及无人工行继续留在分母中。品牌/品种冲突、跨日和人工可能合并均在逐项表中保留；不能因参考样本拟合成功声称真实出货数量或目标系统已验收。",
              '', '逐项人工商品、旧/新SKU、规格/挂牌价/数量差异和异常原因见 [v2逐项对比.xlsx](v2逐项对比.xlsx) 与 [v2_accuracy.json](v2_accuracy.json)。']
    (EVAL / 'v2_accuracy.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'station_metrics': station_stats, 'overall_metrics': overall,
                      'comparison_workbook': str(book_path)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
