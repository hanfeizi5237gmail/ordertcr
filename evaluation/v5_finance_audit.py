"""Read back test finance exports against batch ledgers and merge manifests.

Historical replay evidence only; a passing audit does not authorize accounting.
"""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from v4_replay_audit import read, sha, verify_export


def _number(value):
    return None if value is None else Decimal(str(value))


def _same(actual, expected):
    if isinstance(actual, datetime) and isinstance(expected, date):
        actual = actual.date()
    if isinstance(expected, Decimal) or isinstance(actual, (float, int)):
        try:
            return _number(actual) == _number(expected)
        except (ValueError, TypeError, ArithmeticError):
            return False
    return actual == expected


def _row_matches(actual, expected):
    return all(_same(cell.value, value) and cell.data_type != 'f'
               for cell, value in zip(actual, expected))


def verify_merge(merge, source_manifests):
    """Return all observed merge mismatches, preserving original source order.

    source_manifests may be manifest paths or already loaded manifests. The
    ledger is the independent source of amounts, names, and batch boundaries;
    source XLSX readback detects discrepancies hidden by a copied merge file.
    """
    failures = []
    sources = [read(value) if isinstance(value, (str, Path)) else value for value in source_manifests]
    paths = [str(Path(source.get('manifest_path', '')).resolve()) for source in sources]
    if list(merge.get('source_manifests', [])) != paths:
        failures.append('source_manifest_order')
    if not merge.get('test_only') or not merge.get('finance_draft'):
        failures.append('merged_draft_test_status')
    artifact = merge['files']['finance']
    if sha(artifact['path']) != artifact['sha256']:
        failures.append('merged_finance_sha256')
    book = load_workbook(artifact['path'], data_only=False)
    summary, detail = book['订单明细'], book['商品明细']
    expected_details, expected_summaries = [], []
    source_detail_styles = []
    expected_total = Decimal(0)
    for source_index, source in enumerate(sources, 1):
        if not source.get('test_only') or not source.get('finance_draft'):
            failures.append(f'source_{source_index}_draft_test_status')
        for kind in ('ledger', 'finance'):
            file = source['files'][kind]
            if sha(file['path']) != file['sha256']:
                failures.append(f'source_{source_index}_{kind}_sha256')
        ledger = read(source['files']['ledger']['path'])
        order = ledger['order']
        receipt = date.fromisoformat(order['receipt_date']) if order.get('receipt_date') else None
        merchant = order.get('finance_name') or order['company_name']
        order_number = order.get('order_number')
        subtotal = Decimal(0)
        for row in order['rows']:
            matched = row['status'] == 'matched'
            selected = row.get('selected') if row.get('product_status') == 'matched' or matched else None
            amount = _number(row.get('finance_amount')) if matched else None
            if amount is not None:
                subtotal += amount
            expected_details.append((receipt, order_number, merchant,
                                     selected['name'] if selected else row.get('name', row.get('raw_text')),
                                     _number(row.get('unit_price')) if matched else None,
                                     row.get('price_unit') if matched else row.get('unit'),
                                     _number(row.get('invoice_quantity') if matched else row.get('quantity')),
                                     amount))
        expected_summaries.append((receipt, order_number, merchant, subtotal))
        expected_total += subtotal
        source_book = load_workbook(source['files']['finance']['path'], data_only=False)
        source_detail = source_book['商品明细']
        source_summary = source_book['订单明细']
        offset = len(expected_details) - len(order['rows'])
        for line, expected in enumerate(expected_details[offset:], 2):
            if not _row_matches(source_detail[line][:8], expected):
                failures.append(f'source_{source_index}_detail_{line}')
            source_detail_styles.append(tuple((cell.fill.fgColor.rgb,
                                               cell.comment.text if cell.comment else None)
                                              for cell in source_detail[line][:8]))
        if not _row_matches(source_summary[2][:4], expected_summaries[-1]):
            failures.append(f'source_{source_index}_subtotal')
        if _number(source['totals']['finance']) != subtotal:
            failures.append(f'source_{source_index}_manifest_total')
    if merge.get('row_count') != len(expected_details) or detail.max_row != len(expected_details) + 1:
        failures.append('merged_detail_dimensions')
    if merge.get('order_count') != len(expected_summaries) or summary.max_row != len(expected_summaries) + 2:
        failures.append('merged_summary_dimensions')
    for line, expected in enumerate(expected_details, 2):
        if not _row_matches(detail[line][:8], expected):
            failures.append(f'merged_detail_{line}')
        actual_style = tuple((cell.fill.fgColor.rgb,
                              cell.comment.text if cell.comment else None)
                             for cell in detail[line][:8])
        if actual_style != source_detail_styles[line - 2]:
            failures.append(f'merged_detail_style_{line}')
    for line, expected in enumerate(expected_summaries, 2):
        if not _row_matches(summary[line][:4], expected):
            failures.append(f'merged_subtotal_{line}')
        if not summary.cell(line, 4).comment or '预估' not in summary.cell(line, 4).comment.text:
            failures.append(f'merged_subtotal_draft_comment_{line}')
    footer = len(expected_summaries) + 2
    if not str(summary.cell(footer, 3).value or '').startswith('预估'):
        failures.append('merged_footer_draft_label')
    if not _same(summary.cell(footer, 4).value, expected_total):
        failures.append('merged_footer_total')
    if _number(merge.get('total')) != expected_total:
        failures.append('merged_manifest_total')
    if not summary['A1'].comment or '预估' not in summary['A1'].comment.text:
        failures.append('merged_draft_comment')
    return failures


def main():
    """Audit every merge in one isolated replay output tree, retaining failures."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    paths = sorted(args.output_root.resolve().rglob('merge-manifest.json'))
    if not paths:
        parser.error('no merge-manifest.json files found')
    entries = []
    for path in paths:
        merge = read(path)
        try:
            errors = verify_merge(merge, merge['source_manifests'])
        except (OSError, KeyError, ValueError, TypeError) as exc:
            errors = [f'audit_unreadable: {exc}']
        entries.append({'manifest': str(path), 'failures': errors})
    result = {'evaluation_type': 'historical_test_finance_replay',
              'output_root': str(args.output_root.resolve()),
              'attempted': len(entries), 'verified': sum(not e['failures'] for e in entries),
              'passed': all(not e['failures'] for e in entries), 'entries': entries,
              'limits': 'Test finance drafts only; not actual delivery or formal accounting.'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'attempted': result['attempted'], 'verified': result['verified'],
                      'passed': result['passed'], 'report': str(args.report)}, ensure_ascii=False))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
