"""Full three-station regression, retaining the v2 132-SKU denominator.

The independent review files contain hand-transcribed demand quantities, not
invoice weights copied from the manual workbooks. Reference fitting is explicit.
"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from evaluate_v2 import (
    ROOT, EVAL, STATIONS, LABELS, CatalogStore, register_profile,
    process_batch, merge_finance, aligned_reference, previous_rows,
    compare, station_metrics, workbook, original_hashes, dump,
)

V3 = EVAL / 'v3'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def same_number(left, right):
    return left is None and right is None or (left is not None and right is not None
                                            and Decimal(str(left)) == Decimal(str(right)))


def audit_rows(station, manifest, reviews, catalog):
    """Assert source, current-price, monetary and native Excel contracts."""
    rows = manifest['order']['rows']
    by_id = {r['line_id']: r for r in reviews['rows']}
    exception_expectations = read(V3 / 'exception-expectations.json')
    assert len(by_id) == len(rows) and set(by_id) == {r['line_id'] for r in rows}
    standard = load_workbook(manifest['files']['standard']['path']).active
    finance_book = load_workbook(manifest['files']['finance']['path'])
    finance = finance_book['商品明细']
    assert standard.max_column == 10 and standard.max_row == len(rows) + 2
    assert finance.max_row == len(rows) + 1
    assert manifest['finance_draft'] and manifest['test_only']
    assert manifest['order']['order_number'] is None
    assert '预估' in finance_book['订单明细']['A1'].comment.text
    order = manifest['order']
    summary = finance_book['订单明细']
    def day(value):
        return value.date().isoformat() if hasattr(value, 'date') else value.isoformat() if hasattr(value, 'isoformat') else value
    assert day(summary.cell(2, 1).value) == order.get('receipt_date')
    assert summary.cell(2, 2).value == order.get('order_number')
    assert summary.cell(2, 3).value == (order.get('finance_name') or order['company_name'])
    assertions = []
    subtotal = Decimal(0)
    for index, row in enumerate(rows, 2):
        expected = by_id[row['line_id']]
        source = (ROOT / 'original' / row['source_name']).read_text(encoding='utf-8-sig')
        assert source[row['char_start']:row['char_end']] == row['raw_text'], row['line_id']
        assert source[:row['char_start']].count('\n') + 1 == row['source_line'], row['line_id']
        assert row['raw_text'] == expected['raw_text'], row['line_id']
        assert same_number(row.get('quantity'), expected['quantity']), row['line_id']
        assert row.get('unit') == expected['unit'], row['line_id']
        selected = row.get('selected') if row.get('product_status') == 'matched' else None
        code = str(selected['code']) if selected else None
        expected_codes = expected['expected_codes']
        product_ok = code in expected_codes if expected_codes else selected is None
        assert product_ok, (station, row['line_id'], code, expected_codes)
        assert day(standard.cell(index, 1).value) == order['shipping_date']
        assert standard.cell(index, 2).value == order['order_number']
        assert standard.cell(index, 3).value == order['company_name']
        assert standard.cell(index, 4).value == (selected['name'] if selected else row['name'])
        assert day(finance.cell(index, 1).value) == order.get('receipt_date')
        assert finance.cell(index, 2).value == order['order_number']
        assert finance.cell(index, 3).value == (order.get('finance_name') or order['company_name'])
        assert finance.cell(index, 4).value == (selected['name'] if selected else row['name'])
        assert finance.cell(index, 6).value == (row['price_unit'] if row['status'] == 'matched' else row.get('unit'))
        notes = '\n'.join(cell.comment.text for cell in standard[index] if cell.comment)
        assert row['raw_text'] in notes and row['source_name'] in notes
        assert all(part in notes for part in row.get('notes', '').split('、') if part)
        assert same_number(standard.cell(index, 8).value, row['discount'])
        assert Decimal(row['discount']) == Decimal('0.95')
        if selected:
            assert selected == catalog[code]
            price = Decimal(catalog[code]['price'])
            unit_price = price * Decimal(row['discount'])
            assert same_number(standard.cell(index, 7).value, price)
            assert same_number(standard.cell(index, 9).value, unit_price)
            assert same_number(row['unit_price'], unit_price)
            assert standard.cell(index, 5).value == catalog[code]['spec']
        else:
            assert standard.cell(index, 7).value is None and standard.cell(index, 9).value is None
            assert standard.cell(index, 5).value == (row.get('unit') or row.get('raw_spec'))
            assert expected.get('expected_exception_reason')
            assert row['status'] != 'matched' and row['issues']
            exception = exception_expectations[row['line_id']]
            assert set(exception['expected_candidate_codes']) == {str(c['code']) for c in row['candidates']}, row['line_id']
            assert set(exception['required_issue_codes']) <= {issue['code'] for issue in row['issues']}, row['line_id']
        if row['status'] == 'matched':
            expected_amount = Decimal(selected['price']) * Decimal(row['discount']) * Decimal(row['invoice_quantity'])
            assert same_number(row['amount'], expected_amount)
            assert same_number(row['finance_amount'], expected_amount)
            assert same_number(standard.cell(index, 6).value, row['invoice_quantity'])
            assert same_number(standard.cell(index, 10).value, expected_amount)
            assert same_number(finance.cell(index, 5).value, row['unit_price'])
            assert same_number(finance.cell(index, 7).value, row['invoice_quantity'])
            assert same_number(finance.cell(index, 8).value, expected_amount)
            subtotal += expected_amount
        else:
            assert row['amount'] is None and row['finance_amount'] is None
            assert standard.cell(index, 10).value is None
            assert finance.cell(index, 5).value is None and finance.cell(index, 8).value is None
            expected_qty = row.get('invoice_quantity') if selected and row.get('quantity_status') == 'matched' else row.get('quantity')
            assert same_number(standard.cell(index, 6).value, expected_qty)
            assert same_number(finance.cell(index, 7).value, row.get('quantity'))
            assert finance.cell(index, 6).value == row.get('unit')
            color = 'FCE4D6' if row['status'] == 'unmapped' else 'FFF2CC'
            assert all(standard.cell(index, c).fill.fgColor.rgb[-6:] == color for c in range(1, 11))
            assert all(finance.cell(index, c).fill.fgColor.rgb[-6:] == color for c in range(1, 9))
            for issue in row.get('issues', []):
                assert issue['code'] in notes and issue['message'] in notes
            for candidate in row.get('candidates', []):
                for key in ['code', 'name', 'spec', 'price', 'sheet']:
                    assert str(candidate[key]) in notes, (row['line_id'], key, candidate)
                assert f"第{candidate['row']}行" in notes
            if selected and row.get('quantity_status') != 'matched':
                assert '原始数量' in standard.cell(index, 6).comment.text
        assertions.append({
            'station': station, 'station_name': LABELS[station], 'line_id': row['line_id'],
            'raw_text': row['raw_text'], 'manual_row': expected.get('manual_row'),
            'requested_quantity': row.get('quantity'), 'requested_unit': row.get('unit'),
            'expected_codes': expected_codes, 'selected_code': code,
            'selected_name': selected['name'] if selected else None,
            'review_reason': expected['reason'], 'source_quantity_correct': True,
            'source_span_correct': True, 'xlsx_metadata_correct': True,
            'exception_candidates_and_issues_verified': True if not selected else None,
            'review_identity_or_exception_correct': product_ok,
            'product_status': row['product_status'], 'quantity_status': row['quantity_status'],
            'invoice_quantity': row.get('invoice_quantity'), 'price_unit': row.get('price_unit'),
            'notes': row.get('notes'), 'issues': row['issues'],
            'current_price_correct': True, 'calculation_or_blank_correct': True,
            'source_comments_and_exception_color_correct': True,
            'expected_exception_reason': expected.get('expected_exception_reason'),
            'amount': row.get('amount'),
        })
    assert subtotal == Decimal(manifest['totals']['standard']) == Decimal(manifest['totals']['finance'])
    assert same_number(standard.cell(len(rows) + 2, 10).value, subtotal)
    assert same_number(finance_book['订单明细']['D2'].value, subtotal)
    for artifact in manifest['files'].values():
        assert hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest() == artifact['sha256']
    return assertions


def add_audit_sheet(path, audits):
    book = load_workbook(path)
    sheet = book.create_sheet('全部159项验收')
    sheet.append(['站点', '行ID', '下单原文', '人工表行', '原数量', '原单位', '审阅允许SKU',
                  '本次SKU', '本次商品', '商品/异常正确', '商品状态', '数量状态', '计价数量', '计价单位',
                  '当前价格正确', '金额或空值正确', '批注标色正确', '品质加工备注', '问题', '三方审阅依据'])
    for item in audits:
        sheet.append([item['station_name'], item['line_id'], item['raw_text'], item['manual_row'],
                      item['requested_quantity'], item['requested_unit'], ', '.join(item['expected_codes']),
                      item['selected_code'], item['selected_name'], '通过', item['product_status'],
                      item['quantity_status'], item['invoice_quantity'], item['price_unit'],
                      '通过', '通过', '通过', item['notes'],
                      '\n'.join(i['message'] for i in item['issues']), item['review_reason']])
    sheet.freeze_panes = 'D2'
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in zip('ABCDEFGHIJKLMNOPQRST', [16, 44, 32, 12, 12, 12, 20, 18, 38, 16, 18, 18, 14, 14, 16, 16, 16, 24, 60, 90]):
        sheet.column_dimensions[column].width = width
    for cell in sheet[1]:
        cell.fill = PatternFill('solid', fgColor='284B63')
        cell.font = Font(name='宋体', color='FFFFFF', bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical='center')
    sheet.row_dimensions[1].height = 34
    for index, item in enumerate(audits, 2):
        for cell in sheet[index]:
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            cell.font = Font(name='宋体', size=11)
            if not item['expected_codes']:
                cell.fill = PatternFill('solid', fgColor='FFF2CC')
        sheet.row_dimensions[index].height = 76
    book.save(path)


def main():
    start = time.monotonic()
    before = original_hashes()
    runtime = V3 / 'runtime'
    catalog = CatalogStore(runtime).register(ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx')
    by_code = {str(r['code']): r for r in catalog['records']}
    v2 = read(EVAL / 'v2_accuracy.json')
    results, station_rows, stats, audits, profiles, review_inputs = [], {}, {}, [], {}, {}
    for station in STATIONS:
        profile_path = V3 / f'{station}.history-profile.json'
        profile = read(profile_path)
        register_profile(runtime, profile, source_path=ROOT / 'original' / profile['evidence']['source'])
        profiles[station] = {'path': str(profile_path), 'sha256': hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                             'alias_count': len(profile['aliases']), 'unit_rule_count': len(profile['unit_rules'])}
        review_inputs[station] = {role: {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                                  for role, path in [('review', V3 / f'{station}.review.json'),
                                                     ('decisions', V3 / f'{station}.decisions.json')]}
        batch = read(EVAL / f'inputs/{station}.json')
        batch['batch_id'] = station + '-20261103-v3'
        # The reviewed execution inputs are separate from the test oracle.
        # v3 supersedes every earlier batch decision, including the earlier
        # generic 粉肠 choice now handled by a company procurement preference.
        batch['decisions'] = read(V3 / f'{station}.decisions.json')['decisions']
        result = process_batch(runtime, batch, ROOT / 'outputs/v3', base_dir=ROOT)
        repeated = process_batch(runtime, batch, ROOT / 'outputs/v3', base_dir=ROOT)
        assert repeated['manifest_path'] == result['manifest_path']
        alignment, reference, _ = aligned_reference(station)
        entries = compare(station, result['order']['rows'], previous_rows(station), reference, by_code)
        old_rows = {row['line_id']: row for row in v2['station_rows'][station]}
        for entry in entries:
            old = old_rows[entry['line_id']]
            assert entry['expected_code'] == old['expected_code'] and entry['scope'] == old['scope']
            entry['v2_selected'] = old['new_selected']
            entry['v2_product_correct'] = old['new_product_correct']
        station_rows[station] = entries
        metrics = station_metrics(station, entries, alignment, profile)
        metrics['v2_identity_correct'] = sum(x['v2_product_correct'] for x in entries)
        reviewed = audit_rows(station, result, read(V3 / f'{station}.review.json'), by_code)
        metrics['source_quantity_correct'] = sum(x['source_quantity_correct'] for x in reviewed)
        metrics['review_expected_sku_count'] = sum(bool(x['expected_codes']) for x in reviewed)
        metrics['review_expected_exception_count'] = len(reviewed) - metrics['review_expected_sku_count']
        metrics['review_identity_or_exception_correct'] = sum(x['review_identity_or_exception_correct'] for x in reviewed)
        metrics['fully_priced_rows'] = sum(x['status'] == 'matched' for x in result['order']['rows'])
        stats[station] = metrics
        audits.extend(reviewed)
        results.append(result)
    overall = {key: sum(stats[s][key] for s in STATIONS) for key, value in stats['xueyuan'].items()
               if isinstance(value, int) and not isinstance(value, bool)}
    for metrics in (*stats.values(), overall):
        for target, numerator, denominator in [
            ('new_identity_percent', 'new_identity_correct', 'catalog_covered_identity_denominator'),
            ('all_aligned_identity_percent', 'new_identity_correct', 'aligned_pairs'),
            ('all_chat_identity_percent', 'new_identity_correct', 'all_chat_items'),
            ('source_compatible_identity_percent', 'source_compatible_identity_correct', 'source_compatible_catalog_denominator'),
        ]:
            metrics[target] = round(100 * metrics[numerator] / metrics[denominator], 2)
    assert overall['catalog_covered_identity_denominator'] == 132
    assert overall['all_chat_items'] == len(audits) == 159
    assert sum(a['exception_candidates_and_issues_verified'] is True for a in audits) == 16
    assert overall['new_identity_correct'] / 132 >= .95, overall
    merged = merge_finance(runtime, [m['manifest_path'] for m in results], ROOT / 'outputs/v3/merged')
    assert before == original_hashes()
    report = {'passed': True, 'evaluation_type': 'historical_reference_fitted_regression_not_unseen_accuracy',
              'station_metrics': stats, 'overall_metrics': overall, 'profiles': profiles,
              'review_inputs': review_inputs,
              'exception_expectations': {'path': str(V3 / 'exception-expectations.json'),
                                         'sha256': hashlib.sha256((V3 / 'exception-expectations.json').read_bytes()).hexdigest()},
              'review_scope': 'Hand-transcribed source demand reviewed against the same historical references; cross-reviewed locally. Not blind or unseen evaluation.',
              'station_rows': station_rows, 'station_rows_all': [x for s in STATIONS for x in station_rows[s]],
              'all_item_audits': audits,
              'outputs': [{key: m[key] for key in ('batch_id', 'row_count', 'product_matched_count',
                         'quantity_matched_count', 'unresolved_count', 'manifest_path', 'files')} for m in results],
              'merged_finance': merged, 'original_hashes_unchanged': True,
              'elapsed_seconds_local': round(time.monotonic() - start, 3)}
    path = EVAL / 'v3逐项对比.xlsx'
    workbook(report, path)
    add_audit_sheet(path, audits)
    report['comparison_workbook'] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    dump(EVAL / 'v3_accuracy.json', report)
    print(json.dumps({'station_metrics': stats, 'overall_metrics': overall,
                      'elapsed_seconds_local': report['elapsed_seconds_local']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
