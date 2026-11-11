"""Summarize verifiable historical discrepancies with explicit denominators."""
from decimal import Decimal
import json
from pathlib import Path

from audit_xueyuan import EVAL, CATALOG, MANUAL, CHAT, normalized, sha


def main():
    historical = json.loads((EVAL / 'xueyuan_regression.json').read_text(encoding='utf-8'))
    fitted = json.loads((EVAL / 'v2_accuracy.json').read_text(encoding='utf-8'))
    assert (historical['catalog_sha256'], historical['manual_sha256'], historical['chat_sha256']) == (
        sha(CATALOG), sha(MANUAL), sha(CHAT))
    rows = historical['rows']
    aligned = [row for row in rows if row['manual']]
    assert len(rows) == 64 and len(aligned) == 62
    comparable = [row for row in aligned if row['requested_unit'] == '斤'
                  and row['manual']['spec'] in ('500g', '斤')]
    weight_changes = [row for row in comparable
                      if Decimal(row['requested_quantity']) != Decimal(row['manual']['quantity'])]
    raw_number_changes = [row for row in aligned
                          if Decimal(row['requested_quantity']) != Decimal(row['manual']['quantity'])]
    name_changes = [row for row in aligned
                    if normalized(row['parsed_name']) != normalized(row['manual']['name'])]
    catalogue_identified = [row for row in aligned if row['manual_identity_code']]
    price_changes = [row for row in catalogue_identified if not row['catalog_price_matches_manual']]
    assert len(comparable) == 36 and len(weight_changes) == 20
    assert len(raw_number_changes) == 23 and len(name_changes) == 42
    assert len(catalogue_identified) == 53 and len(price_changes) == 2
    assert fitted['metrics']['new_identity_correct'] == 53 and fitted['metrics']['catalog_covered_identity_denominator'] == 53
    result = {
        'purpose': 'historical_discrepancy_and_acceptance_not_independent_accuracy',
        'sources_sha256': {'chat': sha(CHAT), 'manual': sha(MANUAL), 'catalog': sha(CATALOG)},
        'counts': {
            'chat_items': 64, 'manual_detail_rows': 63, 'aligned_pairs': len(aligned),
            'chat_only': 2, 'manual_only': 1,
            'numeric_quantity_difference': {'numerator': len(raw_number_changes), 'denominator': len(aligned),
                                            'percent': round(len(raw_number_changes) / len(aligned) * 100, 2),
                                            'note': '原始数字不同，不保证计价单位可比'},
            'comparable_weight_difference': {'numerator': len(weight_changes), 'denominator': len(comparable),
                                             'percent': round(len(weight_changes) / len(comparable) * 100, 2),
                                             'note': '仅聊天斤与人工500g/斤列；原因未核实，可能实称或改量'},
            'different_display_name': {'numerator': len(name_changes), 'denominator': len(aligned),
                                       'percent': round(len(name_changes) / len(aligned) * 100, 2),
                                       'note': '名称不同不等于品牌错误；含目录规范名称与修饰词'},
            'old_catalog_price_difference': {'numerator': len(price_changes), 'denominator': len(catalogue_identified),
                                             'percent': round(len(price_changes) / len(catalogue_identified) * 100, 2),
                                             'note': '仅人工名称+规格唯一定位当前目录的行'},
            'fitted_sku_match': {'numerator': 53, 'denominator': 53, 'percent': 100.0,
                                 'note': '同一人工样本学习和核验，不能外推未来预测准确率'},
            'catalog_covered_fraction_of_aligned': {'numerator': 53, 'denominator': len(aligned),
                                                    'percent': round(53 / len(aligned) * 100, 2)},
        },
        'examples': {'comparable_weight_changed_chat_lines': [x['chat_line'] for x in weight_changes],
                     'numeric_quantity_changed_chat_lines': [x['chat_line'] for x in raw_number_changes],
                     'price_conflicts': [{'chat_line': x['chat_line'], 'manual_row': x['manual']['row'],
                                          'manual_price': x['manual']['price'],
                                          'catalog_code': x['manual_identity_code']}
                                         for x in price_changes],
                     'chat_only_lines': [x['chat_line'] for x in rows if not x['manual']],
                     'manual_only': historical['manual_only']},
        'evidence_limits': ['人工订单为历史噪声参考，未见逐项称重日志/取消/替代授权，不能归因数量差异',
                            '同表生成profile并同表评价存在样本泄漏，未见独立时段/站点验收',
                            '无当前生产价适用期或真实财务导入回执']}
    (EVAL / 'xueyuan_accuracy_audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['counts'], ensure_ascii=False))


if __name__ == '__main__':
    main()
