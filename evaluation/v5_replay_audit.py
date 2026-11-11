#!/usr/bin/env python3
"""Replay generated event-preserving samples through the unmodified CLI.

All artifacts are historical, reference-fitted test evidence, not new-order accuracy.
"""
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from v5_finance_audit import verify_merge

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / 'evaluation'
CLI = ROOT / 'skills/order-mapping/scripts/order_mapping.py'
V5 = EVAL / 'v5'
STATIONS = ('songbei', 'xingyue', 'xueyuan')


def align_events(events, rows):
    """Only equal half-open intervals uniquely identify a source event."""
    event_counts = Counter((x['generated_char_start'], x['generated_char_end']) for x in events)
    row_counts = Counter((x['char_start'], x['char_end']) for x in rows)
    row_by_span = {(x['char_start'], x['char_end']): x['line_id'] for x in rows}
    exact = {}
    for event in events:
        span = (event['generated_char_start'], event['generated_char_end'])
        if event_counts[span] == row_counts[span] == 1:
            exact[event['source_event_id']] = row_by_span[span]
    matched_rows = set(exact.values())
    return {'exact': exact,
            'unmatched_events': [x['source_event_id'] for x in events if x['source_event_id'] not in exact],
            'unmatched_rows': [x['line_id'] for x in rows if x['line_id'] not in matched_rows]}


def comparison_workbook(results, path):
    """Create and then fully reread the per-event review workbook."""
    import math
    import unicodedata
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    overview = wb.active
    overview.title = '概览'
    details = wb.create_sheet('逐事件')
    manual = wb.create_sheet('人工工作簿核验')
    overview.append(['场景', '口径', '事件', '人工对齐', '人工SKU可核', '跨度一致', '原量单位正确', 'SKU相同', '异常候选一致', '异常完整', '逐行复读', '合并财务'])
    details.append(['场景', '口径', '样本', '站点', '原事件ID', '原文', '人工行', '人工商品', '人工规格', '人工配货量', '人工原价', '人工折扣', '人工订单号', '人工核验差异', '人工可核SKU', '实际SKU', '当前目录价', '实际折扣', '需求量', '需求单位', '计价量', '计价单位', '实际金额', '状态', '问题代码', '异常候选一致', '异常处理正确', '输出复读差异'])
    manual.append(['站点', '源表', '表单', 'SHA256', '对齐事件数', '不同人工行数', '事件行核验通过', '不一致事件数', '不一致详情'])
    expected_rows = []
    for scenario, sc in results['scenarios'].items():
        for lane, lane_result in sc['lanes'].items():
            m = lane_result['metrics']
            overview.append([scenario, lane, m['source_event_denominator'], m['manual_aligned_denominator'],
                             m['current_catalog_manual_sku_denominator'], m['parsed_exact_span'],
                             m['review_quantity_unit_correct'], m['manual_identity_correct'],
                             m['exception_candidates_match_v3'], m['exception_acceptance_passed'],
                             m['output_rows_verified'], f"{lane_result['merge_verified']}/{lane_result['merge_attempted']}"])
            for x in lane_result['items']:
                snapshot = x['manual_workbook_row'] or {}
                f = snapshot.get('fields') or {}
                row = [scenario, lane, x['sample_id'], x['source_station'], x['source_event_id'], x['raw_text'],
                       x['manual_row'], f.get('name'), f.get('spec'), f.get('quantity'), f.get('price'),
                       f.get('discount'), f.get('order_number'), ', '.join(snapshot.get('mismatches', [])),
                       x['manual_expected_code'], x['selected_code'], x['selected_price'], x['discount'],
                       x['requested_quantity'], x['requested_unit'], x['invoice_quantity'], x['price_unit'],
                       x['amount'], x['status'], ', '.join(i['code'] for i in x['issues']),
                       x['exception_candidates_match_v3'], x['exception_acceptance'], ', '.join(x['output_row_errors'])]
                expected_rows.append(row)
                details.append(row)
    for station, data in results['manual_workbooks'].items():
        manual.append([station, Path(data['path']).name, data['sheet'], data['sha256'], data['linked_events'],
                       data['distinct_linked_rows'], data['manual_rows_verified'], len(data['manual_row_mismatches']),
                       json.dumps(data['manual_row_mismatches'], ensure_ascii=False)])
    widths = {
        '概览': [28, 25, 12, 14, 16, 16, 18, 14, 18, 16, 16, 16],
        '逐事件': [28, 25, 32, 16, 48, 40, 10, 44, 24, 16, 16, 16, 26, 38,
                  18, 18, 18, 16, 14, 14, 14, 14, 18, 18, 40, 18, 18, 38],
        '人工工作簿核验': [18, 32, 18, 70, 18, 18, 22, 20, 55],
    }
    for sheet in wb:
        sheet.freeze_panes = 'C2'
        sheet.auto_filter.ref = sheet.dimensions
        for index, width in enumerate(widths[sheet.title], 1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        for cell in sheet[1]:
            cell.fill = PatternFill('solid', fgColor='284B63')
            cell.font = Font(bold=True, color='FFFFFF')
            cell.alignment = Alignment(vertical='center', wrap_text=True)
        sheet.row_dimensions[1].height = 34
        for row in sheet.iter_rows(min_row=2):
            line_count = 1
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                if isinstance(cell.value, str) and cell.value.startswith('='):
                    cell.data_type = 's'
                if cell.value is not None:
                    for line in str(cell.value).split('\n'):
                        display_width = sum(2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1 for char in line)
                        line_count = max(line_count, math.ceil(display_width / (widths[sheet.title][cell.column - 1] - 2)))
            sheet.row_dimensions[row[0].row].height = min(409.5, max(18, line_count * 15 + 4))
    wb.save(path)
    saved = load_workbook(path, data_only=False)
    if saved['概览'].max_row != 1 + sum(len(s['lanes']) for s in results['scenarios'].values()):
        raise ValueError('comparison workbook overview row count mismatch')
    if saved['人工工作簿核验'].max_row != len(results['manual_workbooks']) + 1:
        raise ValueError('comparison workbook manual summary row count mismatch')
    if saved['逐事件'].max_row != len(expected_rows) + 1:
        raise ValueError('comparison workbook detail row count mismatch')
    for index, expected in enumerate(expected_rows, 2):
        actual = [saved['逐事件'].cell(index, col).value for col in range(1, len(expected) + 1)]
        # openpyxl round-trips empty strings as blanks.
        if [None if x == '' else x for x in expected] != actual:
            raise ValueError(f'comparison workbook detail mismatch at row {index}')
    return {'path': str(path), 'sha256': sha(path), 'detail_rows_verified': len(expected_rows),
            'summary_rows_verified': len(results['manual_workbooks'])}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def cli(*arguments):
    import os
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, str(CLI), *map(str, arguments)],
                            capture_output=True, text=True, env=env)
    if result.returncode:
        raise RuntimeError(f'CLI {arguments[0]} failed: {result.stderr.strip()}')
    return json.loads(result.stdout)['result']


def equal_number(a, b):
    if a is None or b is None:
        return a is None and b is None
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (ArithmeticError, ValueError, TypeError):
        return False


def manual_snapshot(sheet, row_number, reference, shipping_date, company, order_number=None):
    """Read an original workbook row; quantity and price remain historical facts."""
    raw = [sheet.cell(row_number, col).value for col in range(1, 11)]
    date = raw[0].date().isoformat() if hasattr(raw[0], 'date') else str(raw[0]) if raw[0] is not None else None
    fields = dict(zip(('shipping_date', 'order_number', 'company', 'name', 'spec', 'quantity',
                       'price', 'discount', 'unit_price', 'amount'), [date, *raw[1:]]))
    mismatches = []
    if fields['shipping_date'] != shipping_date:
        mismatches.append('shipping_date_mismatch')
    if fields['company'] != company:
        mismatches.append('company_mismatch')
    if not fields['order_number'] or order_number is not None and fields['order_number'] != order_number:
        mismatches.append('order_number_mismatch')
    for key in ('name', 'spec'):
        if str(fields[key] or '').strip() != str(reference.get(key) or '').strip():
            mismatches.append(f'{key}_mismatch')
    for key in ('quantity', 'price'):
        if not equal_number(fields[key], reference.get(key)):
            mismatches.append(f'{key}_mismatch')
    try:
        if abs(Decimal(str(fields['price'])) * Decimal(str(fields['discount'])) - Decimal(str(fields['unit_price']))) > Decimal('0.0000001'):
            mismatches.append('unit_price_calculation')
        if abs(Decimal(str(fields['quantity'])) * Decimal(str(fields['unit_price'])) - Decimal(str(fields['amount']))) > Decimal('0.0000001'):
            mismatches.append('amount_calculation')
    except (TypeError, ValueError, ArithmeticError):
        mismatches.append('invalid_numeric_fields')
    return {'fields': fields, 'mismatches': mismatches}


def verify_row(row, standard, finance, index, catalog):
    """Compare saved sheets, ledger, and current catalog; return failed checks."""
    errors = []
    def check(condition, label):
        if not condition:
            errors.append(label)
    selected = row.get('selected') if row.get('product_status') == 'matched' else None
    notes = '\n'.join(cell.comment.text for cell in standard[index] if cell.comment)
    check(row['raw_text'] in notes and row['source_name'] in notes, 'source_comment')
    check(all(part in notes for part in row.get('notes', '').split('、') if part), 'notes_comment')
    check(standard.cell(index, 4).value == (selected['name'] if selected else row['name']), 'standard_name')
    check(finance.cell(index, 4).value == (selected['name'] if selected else row['name']), 'finance_name')
    check(equal_number(standard.cell(index, 8).value, row['discount']), 'discount')
    if selected:
        reference = catalog.get(str(selected['code']))
        check(reference == selected, 'current_catalog_record')
        if reference:
            check(equal_number(standard.cell(index, 7).value, reference['price']), 'catalog_price')
            check(standard.cell(index, 5).value == reference['spec'], 'catalog_spec')
            expected_price = Decimal(str(reference['price'])) * Decimal(str(row['discount']))
            check(equal_number(row['unit_price'], expected_price), 'unit_price_calculation')
            check(equal_number(standard.cell(index, 9).value, expected_price), 'standard_unit_price')
    else:
        check(standard.cell(index, 7).value is None and standard.cell(index, 9).value is None, 'unresolved_price_blank')
    if row['status'] == 'matched':
        expected = Decimal(str(row['unit_price'])) * Decimal(str(row['invoice_quantity']))
        check(equal_number(row['amount'], expected), 'ledger_amount_calculation')
        check(equal_number(row['finance_amount'], expected), 'ledger_finance_calculation')
        check(equal_number(standard.cell(index, 6).value, row['invoice_quantity']), 'standard_quantity')
        check(equal_number(standard.cell(index, 10).value, expected), 'standard_amount')
        check(equal_number(finance.cell(index, 5).value, row['unit_price']), 'finance_unit_price')
        check(equal_number(finance.cell(index, 7).value, row['invoice_quantity']), 'finance_quantity')
        check(equal_number(finance.cell(index, 8).value, expected), 'finance_amount')
        check(finance.cell(index, 6).value == row['price_unit'], 'finance_unit')
    else:
        check(row['amount'] is None and row['finance_amount'] is None, 'ledger_amount_blank')
        check(standard.cell(index, 10).value is None, 'standard_amount_blank')
        check(finance.cell(index, 5).value is None and finance.cell(index, 8).value is None, 'finance_amount_blank')
        expected_qty = row.get('invoice_quantity') if selected and row.get('quantity_status') == 'matched' else row.get('quantity')
        check(equal_number(standard.cell(index, 6).value, expected_qty), 'standard_unresolved_quantity')
        check(equal_number(finance.cell(index, 7).value, row.get('quantity')), 'finance_unresolved_quantity')
        check(finance.cell(index, 6).value == row.get('unit'), 'finance_unresolved_unit')
        color = 'FCE4D6' if row['status'] == 'unmapped' else 'FFF2CC'
        check(all(standard.cell(index, c).fill.fgColor.rgb[-6:] == color for c in range(1, 11)), 'standard_exception_color')
        check(all(finance.cell(index, c).fill.fgColor.rgb[-6:] == color for c in range(1, 9)), 'finance_exception_color')
        for issue in row.get('issues', []):
            check(issue['code'] in notes and issue['message'] in notes, 'issue_comment')
        for candidate in row.get('candidates', []):
            check(all(str(candidate[k]) in notes for k in ('code', 'name', 'spec', 'price', 'sheet'))
                  and f"第{candidate['row']}行" in notes, 'candidate_comment')
        if selected and row.get('quantity_status') != 'matched':
            comment = standard.cell(index, 6).comment
            check(bool(comment and '原始数量' in comment.text), 'quantity_comment')
    return sorted(set(errors))


def verify_export(manifest, catalog):
    from openpyxl import load_workbook
    failures = []
    for kind, artifact in manifest['files'].items():
        if sha(artifact['path']) != artifact['sha256']:
            failures.append(f'{kind}_sha256')
    ledger = read(manifest['files']['ledger']['path'])
    rows = ledger['order']['rows']
    standard = load_workbook(manifest['files']['standard']['path']).active
    book = load_workbook(manifest['files']['finance']['path'])
    finance = book['商品明细']
    if standard.max_column != 10 or standard.max_row != len(rows) + 2 or finance.max_row != len(rows) + 1:
        failures.append('sheet_dimensions')
    if not (manifest['finance_draft'] and manifest['test_only'] and ledger['order']['order_number'] is None):
        failures.append('draft_test_number')
    comment = book['订单明细']['A1'].comment
    if not comment or '预估' not in comment.text:
        failures.append('finance_draft_comment')
    total_standard = sum((Decimal(str(r['amount'])) for r in rows if r.get('amount') is not None), Decimal(0))
    total_finance = sum((Decimal(str(r['finance_amount'])) for r in rows if r.get('finance_amount') is not None), Decimal(0))
    if not equal_number(total_standard, manifest['totals']['standard']) or not equal_number(total_finance, manifest['totals']['finance']):
        failures.append('manifest_totals')
    if not equal_number(standard.cell(len(rows) + 2, 10).value, total_standard):
        failures.append('standard_total')
    if not equal_number(book['订单明细']['D2'].value, total_finance):
        failures.append('finance_total')
    return ledger, failures, {row['line_id']: verify_row(row, standard, finance, i, catalog)
                              for i, row in enumerate(rows, 2)}


def main():
    import argparse
    from datetime import datetime, timezone
    import os
    from openpyxl import load_workbook
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=V5 / 'simulation/20261108/manifest.json')
    args = parser.parse_args()
    source_manifest = args.manifest.resolve()
    simulation = read(source_manifest)
    originals = sorted(p for p in (ROOT / 'original').iterdir() if p.suffix.lower() in ('.txt', '.xlsx'))
    before = {p.name: sha(p) for p in originals}
    for source in simulation['sources']:
        if before.get(source['source_name']) != source['sha256']:
            raise ValueError(f"source fingerprint mismatch: {source['source_name']}")
    # New isolated test state for each run; no existing runtime or outputs are reused.
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + f'-{os.getpid()}'
    output = ROOT / 'outputs/v5' / run_id
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / 'runtime'
    registered = cli('catalog-register', '--runtime', runtime, '--file', ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx')
    version = registered['version_id']
    catalog = {str(x['code']): x for x in read(runtime / 'catalogs' / version / 'records.json')['records']}
    profiles, reviews, historical_decisions, baselines, templates = {}, {}, {}, {}, {}
    manual_records, manual_workbooks = {}, {}
    v3 = read(EVAL / 'v3_accuracy.json')
    exception_expectations = read(EVAL / 'v3/exception-expectations.json')
    for station in STATIONS:
        profile_path = EVAL / 'v3' / f'{station}.history-profile.json'
        profile = read(profile_path)
        cli('profile-register', '--runtime', runtime, '--file', profile_path,
            '--source', ROOT / 'original' / profile['evidence']['source'])
        profiles[station] = sha(profile_path)
        reviews[station] = {x['line_id']: x for x in read(EVAL / 'v3' / f'{station}.review.json')['rows']}
        historical_decisions[station] = read(EVAL / 'v3' / f'{station}.decisions.json')['decisions']
        baselines[station] = {x['line_id']: x for x in v3['station_rows'][station]}
        templates[station] = read(EVAL / 'inputs' / f'{station}.json')
        manual_path = ROOT / 'original' / profile['evidence']['source']
        if sha(manual_path) != profile['evidence']['sha256']:
            raise ValueError(f'manual workbook fingerprint mismatch: {station}')
        book = load_workbook(manual_path, data_only=True)
        if len(book.worksheets) != 1:
            raise ValueError(f'manual workbook sheet is ambiguous: {station}')
        sheet = book.active
        headers = [sheet.cell(1, col).value for col in range(1, 11)]
        expected_headers = ['发货日期', '订单编号', '所属公司', '商品名称', '规格', None, '原价', '折扣率', '配货单价', '销售金额']
        if any(expected and value != expected for value, expected in zip(headers, expected_headers)) or headers[5] not in ('配货数量', '配送数量'):
            raise ValueError(f'manual workbook columns differ: {station}')
        company = next(c['full_name'] for c in templates[station]['config']['companies'] if c['company_id'] == station)
        order_number = sheet.cell(2, 2).value
        records = {}
        for source_id, review in reviews[station].items():
            baseline = baselines[station][source_id]
            review_row = review.get('manual_row')
            baseline_row = baseline.get('manual_row')
            if review_row is None and baseline_row is None:
                records[source_id] = None
                continue
            if review_row != baseline_row or not isinstance(review_row, int) or not 2 <= review_row <= sheet.max_row or not baseline.get('manual'):
                records[source_id] = {'row': review_row, 'fields': None, 'mismatches': ['review_alignment_row_mismatch']}
                continue
            snapshot = manual_snapshot(sheet, review_row, baseline['manual'], templates[station]['shipping_date'], company, order_number)
            expected_code = baseline.get('expected_code')
            expected_record = catalog.get(str(expected_code)) if expected_code else None
            if expected_code and expected_record is None:
                snapshot['mismatches'].append('expected_sku_missing_from_catalog')
            elif expected_record:
                import unicodedata
                def normal(value):
                    return ''.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()
                if normal(snapshot['fields']['name']) != normal(expected_record['name']) or normal(snapshot['fields']['spec']) != normal(expected_record['spec']):
                    snapshot['mismatches'].append('manual_to_expected_catalog_name_spec')
            records[source_id] = {'row': review_row, **snapshot}
        manual_records[station] = records
        linked = [(sid, value) for sid, value in records.items() if value is not None]
        mismatches = [{'source_event_id': sid, 'manual_row': value['row'], 'mismatches': value['mismatches']}
                      for sid, value in linked if value['mismatches']]
        manual_workbooks[station] = {'path': str(manual_path), 'sheet': sheet.title, 'sha256': sha(manual_path),
                                     'linked_events': len(linked),
                                     'distinct_linked_rows': len({value['row'] for _, value in linked}),
                                     'manual_rows_verified': sum(not value['mismatches'] for _, value in linked),
                                     'manual_row_mismatches': mismatches}
    results = {'schema_version': 1, 'evaluation_type': 'historical_reference_fitted_random_combination_regression',
               'seed': simulation.get('seed'),
               'simulation_manifest': str(source_manifest), 'simulation_sha256': sha(source_manifest),
               'catalog_version': version, 'profile_sha256': profiles, 'output_root': str(output),
               'manual_workbooks': manual_workbooks,
               'manual_workbook_sha': {station: value['sha256'] for station, value in manual_workbooks.items()},
               'manual_rows_verified': sum(value['manual_rows_verified'] for value in manual_workbooks.values()),
               'manual_row_mismatches': [dict(station=station, **item) for station, value in manual_workbooks.items() for item in value['manual_row_mismatches']],
               'lanes': {'profile_only': '注册原公司历史profile，不注入逐项决策',
                         'historical_decisions': '仅同公司、原文及位置一一对应时复用v3历史逐项决策；非独立真值'},
               'limitations': ['仅历史样本拆分与重排，不代表未见订单准确率',
                               '没有运行Agent语义判断；历史决策模式复用既有拟合结果',
                               '跨站分区依赖manifest的来源公司元数据，非从聊天自动识别',
                               '人工SKU和profile来自同一历史参考；人工实配数量/旧价不作为需求真值',
                               '反事实改公司无独立标注，不继承历史SKU，本回放未覆盖此测试',
                               '拆分可能使重复、改量事件不在同一批次，不能据此证明跨批事件处理'],
               'scenarios': {}, 'errors': []}
    for scenario, samples in simulation['scenarios'].items():
        sc = {'sample_count': len(samples), 'event_count': sum(len(s['events']) for s in samples),
              'samples': [], 'lanes': {k: {'items': [], 'merge_attempted': 0, 'merge_verified': 0} for k in results['lanes']}}
        source_ids = [e['source_event_id'] for s in samples for e in s['events']]
        expected_ids = set().union(*(set(reviews[station]) for station in STATIONS))
        if len(source_ids) != len(set(source_ids)) or set(source_ids) != expected_ids:
            results['errors'].append({'scenario': scenario, 'error': 'source_event_coverage_or_duplicates'})
        for sample in samples:
            path = (source_manifest.parent / sample['text_file']).resolve()
            if not path.is_relative_to(source_manifest.parent) or sha(path) != sample['text_sha256']:
                raise ValueError(f"sample path or hash invalid: {sample['sample_id']}")
            text = path.read_text(encoding='utf-8-sig')
            for event in sample['events']:
                if text[event['generated_char_start']:event['generated_char_end']] != event['raw_text']:
                    raise ValueError(f"generated source span mismatch: {event['source_event_id']}")
                source_text = (ROOT / 'original' / event['source_name']).read_text(encoding='utf-8-sig')
                if source_text[event['source_char_start']:event['source_char_end']] != event['raw_text']:
                    raise ValueError(f"original source span mismatch: {event['source_event_id']}")
            parsed = cli('parse', '--chat', path)
            alignment = align_events(sample['events'], parsed)
            if alignment['unmatched_events'] or alignment['unmatched_rows']:
                results['errors'].append({'scenario': scenario, 'sample': sample['sample_id'], 'parser_alignment': alignment})
            by_id = {r['line_id']: r for r in parsed}
            source_by_id = {e['source_event_id']: e for e in sample['events']}
            reverse = {row_id: source_by_id[source_id] for source_id, row_id in alignment['exact'].items()}
            # Source event interval overlap permits attribution only when all overlaps belong to one station.
            assigned = {station: [] for station in STATIONS}
            quarantined = []
            for row in parsed:
                overlapping = [e for e in sample['events']
                               if row['char_start'] < e['generated_char_end'] and e['generated_char_start'] < row['char_end']]
                stations = {(e.get('evaluation_company_id') or e['source_station'])
                            for e in overlapping}
                if len(stations) == 1:
                    # The negative unpartitioned lane deliberately refuses to
                    # route mixed-company text. Other lanes use source-company
                    # routing, or the declared counterfactual target company.
                    if scenario != 'negative_unpartitioned':
                        assigned[next(iter(stations))].append(row)
                    else:
                        quarantined.append({'line_id': row['line_id'], 'raw_text': row['raw_text'],
                                            'stations': sorted(stations), 'reason': 'manual_partition_required'})
                else:
                    quarantined.append({'line_id': row['line_id'], 'raw_text': row['raw_text'],
                                        'stations': sorted(stations), 'reason': 'manual_partition_required'})
            if scenario == 'negative_unpartitioned':
                # Even if parser spans one source event, no trusted company
                # partition exists. Keep every event in the denominator while
                # preventing an order/finance run from inventing ownership.
                assigned = {station: [] for station in STATIONS}
            sc['samples'].append({'sample_id': sample['sample_id'], 'input_sha256': sample['text_sha256'],
                                  'parser_rows': len(parsed), 'aligned_events': len(alignment['exact']),
                                  'unmatched_events': alignment['unmatched_events'],
                                  'unmatched_rows': alignment['unmatched_rows'], 'quarantined_rows': quarantined})
            for lane in results['lanes']:
                processed = {}
                manifests = []
                for station, rows in assigned.items():
                    if not rows:
                        continue
                    batch = dict(templates[station])
                    batch.pop('chat_files', None)
                    batch['items'] = rows
                    batch['batch_id'] = f"v5-{lane}-{sample['sample_id']}-{station}"
                    batch['decisions'] = {}
                    if lane == 'historical_decisions' and scenario != 'counterfactual_company':
                        for row in rows:
                            event = reverse.get(row['line_id'])
                            if event and row['raw_text'] == event['raw_text']:
                                decision = historical_decisions[station].get(event['source_event_id'])
                                if decision:
                                    batch['decisions'][row['line_id']] = decision
                    batch_path = output / f"{batch['batch_id']}.json"
                    dump(batch_path, batch)
                    try:
                        result = cli('run', '--runtime', runtime, '--batch', batch_path, '--output', output / 'batches')
                        manifest = read(result['manifest_path'])
                        ledger, failures, row_errors = verify_export(manifest, catalog)
                        if ledger['order']['company_id'] != station or ledger.get('profile') != read(EVAL / 'v3' / f'{station}.history-profile.json'):
                            failures.append('company_profile_mismatch')
                        if failures:
                            results['errors'].append({'scenario': scenario, 'sample': sample['sample_id'],
                                                      'station': station, 'lane': lane, 'export_errors': failures})
                        failed_rows = {key: value for key, value in row_errors.items() if value}
                        if failed_rows:
                            results['errors'].append({'scenario': scenario, 'sample': sample['sample_id'],
                                                      'station': station, 'lane': lane, 'output_row_errors': failed_rows})
                        manifests.append(result['manifest_path'])
                        for row in ledger['order']['rows']:
                            processed[row['line_id']] = (row, row_errors[row['line_id']], result['manifest_path'])
                    except (RuntimeError, OSError, ValueError, KeyError) as exc:
                        results['errors'].append({'scenario': scenario, 'sample': sample['sample_id'],
                                                  'station': station, 'lane': lane, 'error': str(exc)})
                if manifests:
                    sc['lanes'][lane]['merge_attempted'] += 1
                    try:
                        merge = cli('merge-finance', '--runtime', runtime, '--manifests', *manifests,
                                    '--output', output / 'merged' / lane / sample['sample_id'])
                        finance_failures = verify_merge(merge, manifests)
                        if finance_failures:
                            raise ValueError('finance audit: ' + ', '.join(finance_failures))
                        fin = merge['files']['finance']
                        if sha(fin['path']) != fin['sha256'] or not merge['test_only'] or not merge['finance_draft']:
                            raise ValueError('merged finance fingerprint or draft/test status mismatch')
                        book = load_workbook(fin['path'])
                        if book['商品明细'].max_row != merge['row_count'] + 1 or book['订单明细'].max_row != merge['order_count'] + 2:
                            raise ValueError('merged finance dimensions mismatch')
                        input_details = []
                        expected_total = Decimal(0)
                        for path_value in manifests:
                            source = read(path_value)
                            expected_total += Decimal(str(source['totals']['finance']))
                            sheet = load_workbook(source['files']['finance']['path'], data_only=True)['商品明细']
                            input_details.extend(tuple(cell.value for cell in row) for row in sheet.iter_rows(min_row=2))
                        merged_details = [tuple(cell.value for cell in row) for row in book['商品明细'].iter_rows(min_row=2)]
                        if input_details != merged_details or not equal_number(expected_total, merge['total']):
                            raise ValueError('merged finance details or manifest total differ from source batches')
                        if not equal_number(book['订单明细'].cell(merge['order_count'] + 2, 4).value, expected_total):
                            raise ValueError('merged finance subtotal mismatch')
                        sc['lanes'][lane]['merge_verified'] += 1
                    except (RuntimeError, OSError, ValueError, KeyError) as exc:
                        results['errors'].append({'scenario': scenario, 'sample': sample['sample_id'],
                                                  'lane': lane, 'merge_error': str(exc)})
                for event in sample['events']:
                    station = event['source_station']
                    source_id = event['source_event_id']
                    review = reviews[station][source_id]
                    baseline = baselines[station][source_id]
                    row_id = alignment['exact'].get(source_id)
                    observed = processed.get(row_id)
                    row = observed[0] if observed else None
                    parsed_row = by_id.get(row_id)
                    actual_code = str(row['selected']['code']) if row and row.get('product_status') == 'matched' else None
                    expected_code = baseline['expected_code']
                    old_code = str(baseline['new_selected']['code']) if baseline.get('new_selected') and baseline.get('product_status') == 'matched' else None
                    is_exception = not review['expected_codes']
                    exception = exception_expectations.get(source_id) if is_exception else None
                    manual = manual_records[station][source_id]
                    exception_candidates_match = None
                    exception_issues_match = None
                    if row and exception:
                        exception_candidates_match = set(exception['expected_candidate_codes']) == {str(c['code']) for c in row['candidates']}
                        exception_issues_match = set(exception['required_issue_codes']) <= {issue['code'] for issue in row['issues']}
                    exception_acceptance = None
                    if is_exception:
                        exception_acceptance = bool(exception and row and actual_code is None and row['status'] != 'matched'
                            and exception_candidates_match and exception_issues_match
                            and not any(error in observed[1] for error in ('ledger_amount_blank', 'standard_amount_blank',
                                'finance_amount_blank', 'standard_exception_color', 'finance_exception_color')))
                    sc['lanes'][lane]['items'].append({
                        'sample_id': sample['sample_id'], 'source_station': station, 'source_event_id': source_id,
                        'source_name': event['source_name'], 'source_line': event['source_line'],
                        'source_char_start': event['source_char_start'], 'source_char_end': event['source_char_end'],
                        'raw_text': event['raw_text'], 'generated_line_id': row_id,
                        'generated_char_start': event['generated_char_start'], 'generated_char_end': event['generated_char_end'],
                        'manual_row': review.get('manual_row'), 'manual_expected_code': expected_code,
                        'manual_workbook_row': manual,
                        'manual_discount_matches_batch': equal_number(manual['fields']['discount'], row['discount']) if manual and manual['fields'] and row else None,
                        'review_expected_codes': review['expected_codes'], 'reference_exclusion': baseline.get('reference_exclusion'),
                        'requested_quantity': parsed_row.get('quantity') if parsed_row else None,
                        'requested_unit': parsed_row.get('unit') if parsed_row else None,
                        'original_source_verified': event['raw_text'] == review['raw_text'],
                        'parsed_exact_span': row_id is not None,
                        'review_quantity_unit_correct': bool(parsed_row and equal_number(parsed_row.get('quantity'), review.get('quantity')) and parsed_row.get('unit') == review.get('unit')),
                        'parser_fields_unchanged': bool(parsed_row and all(parsed_row.get(k) == event.get(k) for k in ('raw_text', 'name', 'quantity', 'unit', 'notes', 'date_context', 'event', 'target_station', 'issues'))),
                        'processed': bool(row), 'selected_code': actual_code, 'v3_selected_code': old_code,
                        'same_as_v3_identity': bool(row and actual_code == old_code),
                        'same_as_v3_status': bool(row and row['status'] == baseline['status']),
                        'manual_identity_correct': bool(row and expected_code and actual_code == expected_code),
                        'review_identity_or_exception_correct': bool(row and (actual_code in review['expected_codes'] if review['expected_codes'] else actual_code is None and row['status'] != 'matched' and row['issues'])),
                        'exception_candidates_match_v3': exception_candidates_match,
                        'exception_required_issues_present_v3': exception_issues_match,
                        'exception_acceptance': exception_acceptance,
                        'selected_price': row['selected']['price'] if row and row.get('selected') and row.get('product_status') == 'matched' else None,
                        'discount': row.get('discount') if row else None,
                        'invoice_quantity': row.get('invoice_quantity') if row else None,
                        'price_unit': row.get('price_unit') if row else None,
                        'amount': row.get('amount') if row else None,
                        'status': row['status'] if row else ('needs_manual_partition' if scenario == 'negative_unpartitioned' else None),
                        'product_status': row.get('product_status') if row else ('unresolved_company' if scenario == 'negative_unpartitioned' else None),
                        'quantity_status': row.get('quantity_status') if row else None,
                        'issues': row.get('issues', []) if row else ([{'code': 'company_partition_required', 'message': '混合文本缺少可信所属公司分区，需人工分区'}] if scenario == 'negative_unpartitioned' else []),
                        'output_row_errors': observed[1] if observed else (['company_partition_required'] if scenario == 'negative_unpartitioned' else ['not_processed']),
                        'batch_manifest': observed[2] if observed else None,
                    })
        for lane, lane_result in sc['lanes'].items():
            entries = lane_result['items']
            lane_result['metrics'] = {
                'source_event_denominator': len(entries),
                'manual_aligned_denominator': sum(x['manual_row'] is not None for x in entries),
                'current_catalog_manual_sku_denominator': sum(x['manual_expected_code'] is not None for x in entries),
                'parsed_exact_span': sum(x['parsed_exact_span'] for x in entries),
                'review_quantity_unit_correct': sum(x['review_quantity_unit_correct'] for x in entries),
                'parser_fields_unchanged_same_parser': sum(x['parser_fields_unchanged'] for x in entries),
                'processed': sum(x['processed'] for x in entries),
                'same_v3_identity': sum(x['same_as_v3_identity'] for x in entries),
                'same_v3_status': sum(x['same_as_v3_status'] for x in entries),
                'manual_identity_correct': sum(x['manual_identity_correct'] for x in entries),
                'review_identity_or_exception_correct': sum(x['review_identity_or_exception_correct'] for x in entries),
                'expected_exception_denominator': sum(not x['review_expected_codes'] for x in entries),
                'exception_candidates_match_v3': sum(x['exception_candidates_match_v3'] is True for x in entries),
                'exception_candidates_mismatch_v3': sum(not x['review_expected_codes'] and x['exception_candidates_match_v3'] is not True for x in entries),
                'exception_required_issues_present_v3': sum(x['exception_required_issues_present_v3'] is True for x in entries),
                'exception_acceptance_passed': sum(x['exception_acceptance'] is True for x in entries),
                'exception_acceptance_failed': sum(x['exception_acceptance'] is False for x in entries),
                'output_rows_verified': sum(x['processed'] and not x['output_row_errors'] for x in entries),
                'unresolved': sum(x['status'] not in ('matched', None) for x in entries),
                'company_partition_required': sum(x['status'] == 'needs_manual_partition' for x in entries),
            }
            lane_result['exception_failures'] = [
                {'source_event_id': x['source_event_id'], 'candidate_match': x['exception_candidates_match_v3'],
                 'required_issues_present': x['exception_required_issues_present_v3'],
                 'selected_code': x['selected_code'], 'status': x['status'], 'output_row_errors': x['output_row_errors']}
                for x in entries if x['exception_acceptance'] is False]
        results['scenarios'][scenario] = sc
    results['original_hashes_before'] = before
    results['original_hashes_after'] = {p.name: sha(p) for p in originals}
    results['original_hashes_unchanged'] = before == results['original_hashes_after']
    results['comparison_workbook'] = comparison_workbook(results, EVAL / 'v5逐项对比.xlsx')
    report_path = V5 / 'replay_audit.json'
    dump(report_path, results)
    lines = ['# v5 回放审计', '', '历史参考拟合变形回归；不是未见订单准确率。', '',
             '|场景|回放|事件|人工对齐|人工SKU可核|解析跨度一致|需求数量/单位|当前人工SKU相同|异常候选一致|异常完整|输出逐行复读|',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for scenario, sc in results['scenarios'].items():
        for lane, value in sc['lanes'].items():
            m = value['metrics']
            lines.append(f"|{scenario}|{lane}|{m['source_event_denominator']}|{m['manual_aligned_denominator']}|{m['current_catalog_manual_sku_denominator']}|{m['parsed_exact_span']}|{m['review_quantity_unit_correct']}|{m['manual_identity_correct']}|{m['exception_candidates_match_v3']}/{m['expected_exception_denominator']}|{m['exception_acceptance_passed']}/{m['expected_exception_denominator']}|{m['output_rows_verified']}|")
    lines.extend(['', f"错误记录：{len(results['errors'])}；原件指纹未变：{results['original_hashes_unchanged']}。",
                  f"人工表对齐事件行核验：{results['manual_rows_verified']}；行不一致：{len(results['manual_row_mismatches'])}。",
                  f"人工表 SHA-256：{json.dumps(results['manual_workbook_sha'], ensure_ascii=False)}。",
                  f"逐项对比工作簿复读：{results['comparison_workbook']['detail_rows_verified']} 行，SHA-256 `{results['comparison_workbook']['sha256']}`。",
                  '', '事件、人工对齐、可核SKU分母均保留；错误和未处理行保留在JSON逐项记录中。',
                  '', *[f'- {item}' for item in results['limitations']],
                  '', f'结果：`{report_path}`', f'独立测试runtime和输出：`{output}`'])
    (V5 / 'replay_audit.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'report': str(report_path), 'errors': len(results['errors']),
                      'original_hashes_unchanged': results['original_hashes_unchanged'],
                      'metrics': {s: {lane: data['metrics'] for lane, data in sc['lanes'].items()}
                                  for s, sc in results['scenarios'].items()}}, ensure_ascii=False, indent=2))
    return int(bool(results['errors']) or bool(results['manual_row_mismatches']) or not results['original_hashes_unchanged'])


if __name__ == '__main__':
    raise SystemExit(main())
