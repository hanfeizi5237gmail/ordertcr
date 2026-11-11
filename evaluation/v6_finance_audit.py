#!/usr/bin/env python3
"""Independently reread raw-message ledgers and exported Excel workbooks.

``audit_result(result)`` accepts the dictionary returned by
``raw_messages.process_messages``. It is a structural/accounting audit, not a
business SKU oracle. It never calls the production exporter or money helper.
"""

from collections import Counter
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import shutil

from openpyxl import load_workbook

ORDER_HEADERS = ('发货日期', '订单编号', '所属公司', '商品名称', '规格', '配货数量',
                 '原价', '折扣率', '配货单价', '销售金额')
FINANCE_HEADERS = ('收货日期', '订单号', '商户名', '商品名', '单价(基本单位)',
                   '销售单位', '出库数(基本单位)', '销售额(含税)')
CENT = Decimal('0.01')
TOLERANCE = Decimal('0.00000000001')
REVIEW_TMP = Path(__file__).resolve().parent / 'v6/audit_tmp'


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _decimal(value):
    return None if value is None else Decimal(str(value))


def _equal(left, right):
    if left is None or right is None:
        return left is None and right is None
    try:
        return abs(_decimal(left) - _decimal(right)) <= TOLERANCE
    except (ArithmeticError, TypeError, ValueError):
        return False


def _date(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _comments(sheet, index):
    return '\n'.join(cell.comment.text for cell in sheet[index] if cell.comment)


def _candidate_tuples(notes):
    return [(code, name, spec, price, sheet, int(row))
            for code, name, spec, price, sheet, row in re.findall(
                r'(?m)^\d+\. 材料编号=([^；]+)；商品名称=([^；]+)；规格=([^；]+)；挂牌价=([^；]+)；目录=([^!\n]+)!第(\d+)行',
                notes)]


def _expected_candidates(row):
    return [(str(c.get('code')), str(c.get('name')), str(c.get('spec')),
             str(c.get('price')), str(c.get('sheet')), int(c.get('row')))
            for c in row.get('candidates', [])]


def _policy_amount(unit_price, quantity, policy):
    exact = _decimal(unit_price) * _decimal(quantity)
    return exact.quantize(CENT, rounding=ROUND_HALF_UP) if policy == 'line_cent' else exact


def audit_result(result: dict) -> dict:
    """Return {checks, errors, stats}; every saved row remains in the count.

    A failed check adds a structured error rather than silently dropping a row.
    Missing/corrupt files become errors and leave the expected denominator in
    ``stats``; the function does not mutate its input or generated artifacts.
    """
    checks = Counter()
    errors = []
    stats = {'event_count': result.get('event_count'), 'result_events': len(result.get('events', [])),
             'manifest_count': len(result.get('manifests', [])), 'ledger_rows': 0,
             'standard_rows': 0, 'finance_rows': 0, 'merged_rows': 0,
             'matched': 0, 'unresolved': 0, 'candidate_rows': 0,
             'raw_chat_bytes_verified': False}

    def check(ok, code, where, detail=None):
        checks[code] += 1
        if not ok:
            errors.append({'code': code, 'where': str(where), 'detail': detail or ''})

    manifests = []
    ledgers = []
    source_detail = []
    source_summary = []
    observed_events = {}
    source_names = {source.get('source_name') for source in result.get('sources', [])}
    check(bool(source_names) and len(source_names) == len(result.get('sources', []))
          and all(re.fullmatch(r'[a-f0-9]{64}', str(source.get('sha256') or ''))
                  for source in result.get('sources', [])), 'source_fingerprint_record', 'result')
    source_texts = {}
    sources_with_path = [source for source in result.get('sources', []) if source.get('path')]
    if sources_with_path:
        check(len(sources_with_path) == len(result['sources']), 'source_paths_complete', 'result')
        for source in sources_with_path:
            try:
                path = Path(source['path'])
                raw = path.read_bytes()
                check(_sha(path) == source['sha256'], 'raw_chat_fingerprint', path)
                source_texts[source['source_name']] = raw.decode('utf-8-sig')
            except (OSError, ValueError, UnicodeError) as exc:
                check(False, 'raw_chat_read', source.get('path'), repr(exc))
        stats['raw_chat_bytes_verified'] = len(source_texts) == len(result['sources']) and not any(
            error['code'] in ('raw_chat_fingerprint', 'raw_chat_read', 'source_paths_complete') for error in errors)
    for path_value in result.get('manifests', []):
        path = Path(path_value)
        try:
            manifest = _read(path)
            ledger_path = manifest['files']['ledger']['path']
            ledger = _read(ledger_path)
            standard_path = manifest['files']['standard']['path']
            finance_path = manifest['files']['finance']['path']
            standard = load_workbook(standard_path, data_only=False).active
            finance_book = load_workbook(finance_path, data_only=False)
            detail = finance_book['商品明细']
            summary = finance_book['订单明细']
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            check(False, 'read_source', path, repr(exc))
            continue
        manifests.append(manifest)
        ledgers.append(ledger)
        order = ledger['order']
        rows = order['rows']
        stats['ledger_rows'] += len(rows)
        for role, entry in manifest['files'].items():
            check(_sha(entry['path']) == entry['sha256'], 'source_fingerprint', f'{path}:{role}')
        check(manifest['catalog'] == ledger['catalog'] == result['catalog'], 'catalog_identity', path)
        check(manifest['row_count'] == len(rows), 'source_count', path)
        check(manifest['matching_mode'] == ledger['matching_mode'] == result['matching_mode'], 'matching_mode', path)
        input_items = {item['line_id']: item for item in ledger['batch']['items']}
        check(len(input_items) == len(rows), 'source_input_count', path)
        check(ledger.get('profile') is None and manifest.get('profile_sha256') is None,
              'no_legacy_profile', path)
        check(tuple(standard.cell(1, col).value for col in range(1, 11)) == ORDER_HEADERS,
              'standard_headers', path)
        check(tuple(detail.cell(1, col).value for col in range(1, 9)) == FINANCE_HEADERS,
              'finance_headers', path)
        check(standard.max_column == 10 and standard.max_row == len(rows) + 2,
              'standard_dimensions', path)
        check(detail.max_column == 8 and detail.max_row == len(rows) + 1,
              'finance_dimensions', path)
        check(summary.max_column == 4 and summary.max_row == 3,
              'summary_dimensions', path)
        catalog_note = standard['D1'].comment.text if standard['D1'].comment else ''
        check(all(str(ledger['catalog'][key]) in catalog_note for key in ('version_id', 'source_name', 'sha256')),
              'catalog_comment', path)
        standard_total = Decimal(0)
        finance_total = Decimal(0)
        policy = ledger['batch']['config'].get('output_policies', {})
        for index, row in enumerate(rows, 2):
            where = f"{path}:{row['line_id']}"
            original = input_items.get(row['line_id'])
            check(original is not None and row['source_name'] in source_names
                  and all(original.get(key) == row.get(key) for key in
                          ('raw_text', 'name', 'quantity', 'unit', 'source_name', 'source_line',
                           'char_start', 'char_end', 'original_date_context')),
                  'source_input_preserved', where)
            if source_texts:
                text = source_texts.get(row['source_name'])
                start, end = row.get('char_start'), row.get('char_end')
                check(text is not None and isinstance(start, int) and isinstance(end, int)
                      and 0 <= start < end <= len(text) and text[start:end] == row['raw_text'],
                      'raw_chat_span', where)
            stats['standard_rows'] += 1
            stats['finance_rows'] += 1
            matched = row['status'] == 'matched'
            stats['matched' if matched else 'unresolved'] += 1
            selected = row.get('selected') if row.get('product_status') == 'matched' or matched else None
            qty_known = row.get('quantity_status') == 'matched' or matched
            expected_qty = row.get('invoice_quantity') if selected and qty_known else row.get('quantity')
            expected_name = selected['name'] if selected else row.get('name', row.get('raw_text'))
            expected_spec = selected['spec'] if selected else row.get('unit') or row.get('raw_spec')
            expected_price = selected['price'] if selected else None
            check(_date(standard.cell(index, 1).value) == order['shipping_date']
                  and standard.cell(index, 2).value == order['order_number']
                  and standard.cell(index, 3).value == order['company_name'], 'standard_routing', where)
            check(standard.cell(index, 4).value == expected_name and standard.cell(index, 5).value == expected_spec,
                  'standard_identity', where)
            check(_equal(standard.cell(index, 6).value, expected_qty)
                  and _equal(standard.cell(index, 7).value, expected_price)
                  and _equal(standard.cell(index, 8).value, row['discount']), 'standard_quantity_price', where)
            check(_equal(standard.cell(index, 9).value, row.get('unit_price') if selected else None)
                  and _equal(standard.cell(index, 10).value, row.get('amount') if matched else None),
                  'standard_amount_cells', where)
            check(row['raw_text'] in _comments(standard, index)
                  and row['source_name'] in _comments(standard, index), 'standard_source_comment', where)
            if not matched:
                expected_color = 'FCE4D6' if row['status'] == 'unmapped' else 'FFF2CC'
                check(all(standard.cell(index, col).fill.fgColor.rgb[-6:] == expected_color
                          for col in range(1, 11)), 'standard_exception_color', where)
                notes = _comments(standard, index)
                check(all(issue['code'] in notes and issue['message'] in notes for issue in row.get('issues', [])),
                      'standard_issue_comment', where)
                candidates = _expected_candidates(row)
                stats['candidate_rows'] += bool(candidates)
                check(_candidate_tuples(notes) == candidates, 'standard_candidates_complete', where)
                check(row.get('amount') is None and row.get('finance_amount') is None
                      and standard.cell(index, 10).value is None, 'unresolved_amount_blank', where)
                if not selected:
                    check(all(standard.cell(index, col).value is None for col in (7, 9, 10)),
                          'unresolved_identity_gij_blank', where)
                elif not qty_known:
                    check(_equal(standard.cell(index, 6).value, row.get('quantity'))
                          and standard.cell(index, 10).value is None, 'known_identity_raw_quantity', where)
            else:
                unit_price = _decimal(selected['price']) * _decimal(row['discount'])
                amount = _policy_amount(unit_price, row['invoice_quantity'],
                                        policy.get('standard_order', 'sample_exact'))
                finance_amount = _policy_amount(unit_price, row['invoice_quantity'],
                                                policy.get('finance', 'sample_exact'))
                check(_equal(row['unit_price'], unit_price)
                      and _equal(row['amount'], amount)
                      and _equal(row['finance_amount'], finance_amount)
                      and _equal(standard.cell(index, 9).value, unit_price)
                      and _equal(standard.cell(index, 10).value, amount), 'decimal_recalculation', where)
                standard_total += amount
                finance_total += finance_amount
            expected_detail = (
                order.get('receipt_date'), order.get('order_number'),
                order.get('finance_name') or order['company_name'], expected_name,
                row.get('unit_price') if matched else None,
                row.get('price_unit') if matched else row.get('unit'),
                row.get('invoice_quantity') if matched else row.get('quantity'),
                row.get('finance_amount') if matched else None)
            actual_detail = tuple(cell.value for cell in detail[index])
            check(_date(actual_detail[0]) == expected_detail[0]
                  and actual_detail[1:4] == expected_detail[1:4]
                  and _equal(actual_detail[4], expected_detail[4])
                  and actual_detail[5] == expected_detail[5]
                  and _equal(actual_detail[6], expected_detail[6])
                  and _equal(actual_detail[7], expected_detail[7]), 'finance_detail', where)
            if not matched:
                expected_color = 'FCE4D6' if row['status'] == 'unmapped' else 'FFF2CC'
                check(all(detail.cell(index, col).fill.fgColor.rgb[-6:] == expected_color
                          for col in range(1, 9)), 'finance_exception_color', where)
                check(row['raw_text'] in _comments(detail, index)
                      and _candidate_tuples(_comments(detail, index)) == _expected_candidates(row),
                      'finance_exception_comment', where)
                check(detail.cell(index, 5).value is None and detail.cell(index, 8).value is None,
                      'finance_unresolved_blank', where)
            source_detail.append(actual_detail)
            observed_events[row['line_id']] = (row, order, str(path), index)
        check(_equal(standard.cell(len(rows) + 2, 10).value, standard_total)
              and _equal(manifest['totals']['standard'], standard_total), 'standard_subtotal', path)
        merchant = order.get('finance_name') or order['company_name']
        actual_summary = tuple(cell.value for cell in summary[2])
        check(_date(actual_summary[0]) == order.get('receipt_date')
              and actual_summary[1:3] == (order.get('order_number'), merchant)
              and _equal(actual_summary[3], finance_total), 'finance_summary', path)
        check(_equal(summary.cell(3, 4).value, finance_total)
              and _equal(manifest['totals']['finance'], finance_total), 'finance_subtotal', path)
        source_summary.append(actual_summary)
    check(result.get('event_count') == len(result.get('events', [])) == stats['ledger_rows']
          and len(observed_events) == stats['ledger_rows'], 'total_event_count', 'result')
    for event in result.get('events', []):
        row = event['row']
        saved = observed_events.get(row['line_id'])
        check(saved is not None and row == saved[0] and event['company_id'] == saved[1]['company_id']
              and event['shipping_date'] == saved[1]['shipping_date']
              and event['manifest_path'] == saved[2] and event['excel_row'] == saved[3],
              'result_event_link', row['line_id'])
    merge = result.get('merged_finance') or {}
    try:
        merged_path = merge['files']['finance']['path']
        merged_book = load_workbook(merged_path, data_only=False)
        merged_detail = merged_book['商品明细']
        merged_summary = merged_book['订单明细']
        check(_sha(merged_path) == merge['files']['finance']['sha256'],
              'merged_fingerprint', merged_path)
        check([str(Path(x).resolve()) for x in result['manifests']] == merge['source_manifests'],
              'merge_sources', merged_path)
        check(merge['catalog'] == result['catalog'] and merge['row_count'] == stats['ledger_rows']
              and merge['order_count'] == len(ledgers), 'merge_denominators', merged_path)
        check(merged_detail.max_row == stats['ledger_rows'] + 1 and merged_summary.max_row == len(ledgers) + 2,
              'merge_dimensions', merged_path)
        for index, source in enumerate(source_detail, 2):
            actual = tuple(cell.value for cell in merged_detail[index])
            check(_date(actual[0]) == _date(source[0]) and actual[1:4] == source[1:4]
                  and _equal(actual[4], source[4]) and actual[5] == source[5]
                  and _equal(actual[6], source[6]) and _equal(actual[7], source[7]),
                  'merge_detail', f'{merged_path}:{index}')
            stats['merged_rows'] += 1
        total = Decimal(0)
        for index, source in enumerate(source_summary, 2):
            actual = tuple(cell.value for cell in merged_summary[index])
            check(_date(actual[0]) == _date(source[0]) and actual[1:3] == source[1:3]
                  and _equal(actual[3], source[3]), 'merge_summary', f'{merged_path}:{index}')
            total += _decimal(source[3])
        check(_equal(merged_summary.cell(len(ledgers) + 2, 4).value, total)
              and _equal(merge['total'], total), 'merge_subtotal', merged_path)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        check(False, 'read_merged', 'merged_finance', repr(exc))
    return {'checks': dict(checks), 'errors': errors, 'stats': stats}


def _self_test():
    """A changed workbook plus a refreshed SHA must still fail semantic checks."""
    import sys
    from openpyxl import Workbook
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/order-mapping/scripts'))
    from order_mapping_core.catalog import CatalogStore
    from order_mapping_core.raw_messages import process_messages
    REVIEW_TMP.mkdir(parents=True, exist_ok=True)
    case = REVIEW_TMP / 'self_test'
    if case.exists():
        if case.is_symlink():
            raise ValueError('self test fixture directory must not be a symlink')
        shutil.rmtree(case)
    case.mkdir(exist_ok=True)
    catalog = case / 'catalog.xlsx'
    book = Workbook()
    sheet = book.active
    sheet.append(['类别名称', '材料编号', '材料名称', '挂牌价', '规格'])
    sheet.append(['蔬菜', 'one', '青萝卜', '2', '500g'])
    book.save(catalog)
    CatalogStore(case / 'runtime').register(catalog)
    chat = case / 'chat.txt'
    chat.write_text('【甲站】\n青萝卜2斤，萝卜1筐\n【乙站】\n青萝卜1斤', encoding='utf-8')
    config = {'schema_version': 1, 'companies_confirmed': True,
              'companies': [{'company_id': 'a', 'full_name': '甲站', 'aliases': [], 'finance_name': '甲站'},
                            {'company_id': 'b', 'full_name': '乙站', 'aliases': [], 'finance_name': '乙站'}],
              'numbering': {'prefix': 'OR_', 'confirmed': False},
              'output_policies': {}, 'finance': {}}
    request = {'schema_version': 1, 'batch_id': 'self_test', 'chat_files': [str(chat)],
               'config': config, 'shipping_date': '2026-11-03',
               'matching_mode': 'shared', 'test_only': True}
    result = process_messages(case / 'runtime', request, case / 'outputs')
    # The runtime in older versions omitted the path. Simulate the new
    # boundary field without changing the runtime implementation.
    if not result['sources'][0].get('path'):
        result['sources'][0]['path'] = str(chat.resolve())
    baseline = audit_result(result)
    assert not baseline['errors'], baseline['errors']
    assert baseline['stats']['candidate_rows'] == 1, baseline['stats']
    assert baseline['stats']['raw_chat_bytes_verified'], baseline['stats']
    manifest_path = Path(result['manifests'][0])
    manifest = _read(manifest_path)
    finance_path = Path(manifest['files']['finance']['path'])
    book = load_workbook(finance_path)
    book['商品明细'].cell(2, 8).value = 999
    book.save(finance_path)
    manifest['files']['finance']['sha256'] = _sha(finance_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tampered = audit_result(result)
    assert any(x['code'] == 'finance_detail' for x in tampered['errors']), tampered['errors']
    assert not any(x['code'] == 'source_fingerprint' for x in tampered['errors']), tampered['errors']
    wrong_chat = chat.read_text(encoding='utf-8')
    chat.write_text(wrong_chat + '\n篡改', encoding='utf-8')
    tampered_source = audit_result(result)
    assert any(x['code'] == 'raw_chat_fingerprint' for x in tampered_source['errors']), tampered_source['errors']
    return {'baseline_checks': sum(baseline['checks'].values()), 'baseline_errors': 0,
            'tampered_errors': tampered['errors'], 'baseline_stats': baseline['stats']}


if __name__ == '__main__':
    print(json.dumps(_self_test(), ensure_ascii=False, indent=2))
