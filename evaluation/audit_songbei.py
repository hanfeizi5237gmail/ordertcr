"""Rebuild Songbei's reference-fitted alignment and conservative profile."""
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import sys

from openpyxl import load_workbook

from audit_xueyuan import ROOT, EVAL, CATALOG, catalog_rows, decimal, normalized, sha

sys.path.insert(0, str(ROOT / 'skills/order-mapping/scripts'))
from order_mapping_core.parsing import parse_chat
from order_mapping_core.profiles import validate_profile

MANUAL = ROOT / 'original/松北大道站11.3.xlsx'
CHAT = ROOT / 'original/松北大道站11.3下单.txt'

# Manual rows 2–4 belong to later chat additions; 大葱 and 白菜 were moved to the end.
# Source line 3 尖椒 has no corresponding manual detail row.
TARGET = {**{line: line + 4 for line in range(1, 3)},
          **{line: line + 3 for line in range(4, 6)},
          **{line: line + 2 for line in range(7, 18)},
          **{line: line + 1 for line in range(19, 47)},
          6: 52, 18: 53, 48: 48, 50: 2, 51: 3, 52: 4,
          53: 49, 54: 50, 55: 51}

# A manual choice can document a discrepancy without authorizing future substitution.
EXCLUDED = {
    2: '小黑茄子要求特定品种，地产茄子不证明满足“小黑”约束',
    3: '聊天尖椒4斤未见人工对应行，不能推断已取消或选择某SKU',
    11: '下五花肉与人工“精五花肉”部位可能不同',
    21: '厚皮鸡蛋明确品质特征，人工“鲜鸡蛋”不证明厚皮',
    24: '玉米粒2袋被改为罐头3罐，包装和数量均不相符',
    30: '豆角不等于特定架豆王，需确认品种',
    32: '聊天明确秋林，人工农大红肠存在品牌冲突；不可学农大替代',
    33: '罗马生菜与人工西生菜品种不一致',
    38: '牛肉卷与人工肥牛肉片形态不一致',
    41: '色拉油到九三大豆油无明确品牌/原料授权，人工价73.9与目录65.9也不一致',
    48: '聊天五色糙米与人工七色糙米不同，不能学替代及2袋数量',
}

# Only direct one-to-one counts with an explicit package in the catalog SKU.
# 豆腐/酸菜/鸡爪的目录只写重量；同数不能证明“盒/袋”就是一个目录计价单位。
PACK_COUNTS = {27: ('袋', '袋'), 34: ('包', '袋')}
UNPROVEN_PACK = {4: '目录490g未注明每盒，人工7不证明7盒各490g',
                 8: '目录750g未注明每袋，人工4不证明4袋各750g',
                 23: '目录1kg未注明每袋，人工3可能为实称3kg而非3袋'}


def manual_rows():
    sheet = load_workbook(MANUAL, read_only=True, data_only=True).active
    return {i: {'row': i, 'name': sheet.cell(i, 4).value, 'spec': sheet.cell(i, 5).value,
                'quantity': decimal(sheet.cell(i, 6).value), 'price': decimal(sheet.cell(i, 7).value)}
            for i in range(2, 54)}


def main():
    catalog = catalog_rows()
    current = {'version_id': sha(CATALOG), 'records': catalog}
    by_name = defaultdict(list)
    for record in catalog:
        by_name[normalized(record['name'])].append(record)
    manual = manual_rows()
    parsed = parse_chat(CHAT.read_text(encoding='utf-8-sig'), CHAT.name)
    assert len(parsed) == 53 and len(manual) == 52
    assert set(TARGET.values()) == set(manual) and len(TARGET) == 52
    records = []
    aliases = defaultdict(list)
    units = {}
    for item in parsed:
        line = item['source_line']
        human = manual.get(TARGET.get(line))
        row = {'line_id': item['line_id'], 'chat_line': line, 'raw_text': item['raw_text'],
               'parsed_name': item['name'], 'requested_quantity': item.get('quantity'),
               'requested_unit': item.get('unit'), 'manual_row': TARGET.get(line), 'manual': human,
               'alignment_kind': 'ordered_name_anchor' if human else 'manual_missing',
               'manual_catalog_code': None, 'catalog_price_matches_manual': False,
               'profile_eligible': False, 'exclusion': EXCLUDED.get(line),
               'unit_rule_exclusion': UNPROVEN_PACK.get(line)}
        if human:
            candidates = [c for c in by_name[normalized(human['name'])]
                          if normalized(c['spec']) == normalized(human['spec'])]
            priced = [c for c in candidates if Decimal(c['price']) == Decimal(human['price'])]
            chosen = priced[0] if len(priced) == 1 else candidates[0] if len(candidates) == 1 else None
            row['catalog_candidates_same_name_spec'] = [c['code'] for c in candidates]
            row['catalog_price_candidates'] = [c['code'] for c in priced]
            if chosen:
                row['manual_catalog_code'] = chosen['code']
                row['catalog_price_matches_manual'] = Decimal(chosen['price']) == Decimal(human['price'])
            if not chosen:
                row['exclusion'] = row['exclusion'] or '人工商品无法按名称、规格和挂牌价唯一绑定当前目录SKU'
            elif not row['catalog_price_matches_manual']:
                row['exclusion'] = row['exclusion'] or '人工价格不同于当前目录挂牌价，须单独核定价格来源'
            if chosen and not row['exclusion']:
                row['profile_eligible'] = True
                aliases[item['name']].append((row, chosen))
                if line in PACK_COUNTS:
                    source_unit, price_unit = PACK_COUNTS[line]
                    assert item['unit'] == source_unit and Decimal(item['quantity']) == Decimal(human['quantity'])
                    assert '袋' in chosen['name'] or '袋' in chosen['spec']
                    code = chosen['code']
                    assert code not in units
                    units[code] = {'price_unit': price_unit, 'request_units': {source_unit: '1'},
                                   'allow_fractional': False,
                                   'evidence': f'松北参考样本聊天{line}行“{item["raw_text"]}”与人工表{human["row"]}行'
                                               f'SKU {code}数量同为{human["quantity"]}；目录名称/规格含袋装证据；'
                                               '仅包装计件1:1，不代表实称或历史售价。'}
        records.append(row)

    profile_aliases = {}
    for name, events in sorted(aliases.items()):
        codes = {record['code'] for _, record in events}
        if len(codes) != 1:
            for row, _ in events:
                row['profile_eligible'] = False
                row['exclusion'] = '同一原品名在人工记录中指向不同SKU'
            continue
        profile_aliases[name] = {
            'code': next(iter(codes)),
            'request_units': sorted({row['requested_unit'] for row, _ in events if row['requested_unit']}),
            'evidence': '；'.join(
                f'聊天{row["chat_line"]}行“{row["raw_text"]}”→人工表{row["manual_row"]}行“{row["manual"]["name"]}”'
                f'→目录{catalog_record["catalog_row"]}行SKU {catalog_record["code"]}；'
                '仅原品名/原单位范围的SKU偏好，不导入实配数量或旧价'
                for row, catalog_record in events)}
    profile = {'schema_version': 1, 'profile_id': 'songbei-20261103-reference-fitted-v1',
               'company_id': 'songbei', 'catalog_version': sha(CATALOG),
               'evidence': {'kind': 'user_provided_reference', 'source': MANUAL.name, 'sha256': sha(MANUAL)},
               'aliases': profile_aliases, 'unit_rules': units}
    validate_profile(profile, current)
    assert '秋林红肠' not in profile_aliases and '玉米粒' not in profile_aliases
    output = {'purpose': 'reference_fitted_alignment_not_independent_accuracy',
              'company_id': 'songbei', 'catalog_version': sha(CATALOG),
              'manual_sha256': sha(MANUAL), 'chat_sha256': sha(CHAT),
              'counts': {'chat_items': len(parsed), 'manual_detail_rows': len(manual),
                         'chat_events_with_manual_link': sum(r['manual_row'] is not None for r in records),
                         'profile_eligible_events': sum(r['profile_eligible'] for r in records),
                         'profile_aliases': len(profile_aliases), 'package_rules': len(units)},
              'rows': records,
              'limitations': ['人工表可能反映实称、替代和人工增删，按顺序/名称对齐只代表参考，不是独立正确率',
                              '未向profile复制任何订单数量、实际称重或价格',
                              '品种、品牌、包装冲突仅作为差异留在alignment中，不复用为别名']}
    (EVAL / 'songbei_alignment.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (EVAL / 'songbei.history-profile.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(output['counts'], ensure_ascii=False))


if __name__ == '__main__':
    main()
