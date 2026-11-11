#!/usr/bin/env python3
"""Generate and verify frozen V7 catalog-transfer fixtures, without invoking the CLI.

Requires the already available openpyxl runtime. Existing seeds are never overwritten;
``--verify`` reconstructs them in memory and compares all bytes.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
from collections import Counter
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'evaluation/v7/fresh_challenge'
CATALOG = ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx'
KNOWLEDGE = ROOT / 'skills/order-mapping/knowledge/procurement.json'
OLD_BUILDERS = [ROOT / 'evaluation/v6_challenge_builder.py',
                ROOT / 'evaluation/v6_challenge_extension_builder.py']
SEEDS = {7501: 'development', 7502: 'development', 7591: 'reserved'}
COMPANIES = [
    {'company_name': '星月站', 'company_alias': '星月'},
    {'company_name': '学院路站', 'company_alias': '学院路'},
    {'company_name': '松北大道站', 'company_alias': '松北大道'},
    {'company_name': '新港站', 'company_alias': '新港'},
]

# Exact catalog identities with explicit varietal, form, origin, or simple name.
# A slash in one catalog record explicitly supplies two additional expressions.
NAMES = [
    '黄岩蜜桔', '云南蜜桔', '玉菇蜜瓜', '血橙', '小台芒', '南果梨',
    '久保桃', '皇冠梨', '花牛苹果', '春见粑粑柑', '网纹瓜', '小青柠',
    '黄油桃', '巨峰葡萄', '鸡毛菜', '韭黄', '韭苔', '螺丝椒',
    '绿茄子', '紫茄子', '奶白菜（斤）', '水果萝卜', '塔菜', '苏子叶',
    '香椿芽', '冰鲜多宝鱼', '冰冻鱿鱼须', '鲜活乳山生蚝', '红曲米',
    '三色藜麦', '糙米', '高粱米', '花芸豆', '白芸豆', '去皮鹌鹑蛋',
    '风味五香干豆腐丝', '红丁萝卜/红萝卜丁',
    '丹丹郫县豆瓣酱（250克/袋）',
]
SYNONYMS = {'红丁萝卜/红萝卜丁': ['红丁萝卜', '红萝卜丁']}
CHINESE = {'1': '一', '2': '二', '3': '三', '4': '四', '5': '五'}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f'No literal {name} in {path}')


def load_sources():
    # Only read the formal rule's actual aliases and selectors, never evidence,
    # past expected answers, evaluated output, or human order documents.
    kb_bytes = KNOWLEDGE.read_bytes()
    rules = json.loads(kb_bytes)['rules']
    known = {name for rule in rules for name in rule.get('aliases', [])}
    known.update(name for rule in rules for name in rule.get('selector', {}).get('names', []))
    old_names = set(assignment(OLD_BUILDERS[0], 'NAMES'))
    old_products = assignment(OLD_BUILDERS[1], 'PRODUCTS')
    old_names.update(name for p in old_products for name in p[:2])
    expressions = set(NAMES) | {n for ns in SYNONYMS.values() for n in ns}
    if collision := expressions & (known | old_names):
        raise ValueError(f'Product or expression already in rules or old pool: {sorted(collision)}')
    catalog_bytes = CATALOG.read_bytes()
    sheet = load_workbook(CATALOG, read_only=True, data_only=True)['Sheet0']
    found = {}
    all_names = []
    for row in sheet.iter_rows(min_row=5, values_only=False):
        category, sku, name, price, spec = [c.value for c in row[:5]]
        if name:
            all_names.append(str(name))
        if name in NAMES:
            if name in found:
                raise ValueError(f'Exact catalog name occurs twice: {name}')
            found[name] = {'sku': str(sku), 'name': name, 'category': category,
                           'price': str(price), 'spec': str(spec),
                           'sheet': sheet.title, 'row': row[0].row}
    if set(found) != set(NAMES) or len(found) < 18:
        raise ValueError('Missing catalog identities')
    if any(found[n]['spec'] != '500g' for n in NAMES[:-1]):
        raise ValueError('A weight-product price basis changed')
    if found[NAMES[-1]]['spec'] != '250g' or '250克/袋' not in NAMES[-1]:
        raise ValueError('The specified bag product changed')
    if any('山月牌' in n for n in all_names) or any('雪雾星果' in n for n in all_names):
        raise ValueError('An artificial unknown is present in current catalog')
    return found, {'catalog_sha256': digest(catalog_bytes),
                   'formal_knowledge_sha256': digest(kb_bytes),
                   'old_builder_sha256': {p.name: digest(p.read_bytes()) for p in OLD_BUILDERS}}


def normal_event(name, raw_name, quantity, raw_quantity, unit, company, date, record, kind):
    base = Decimal(quantity) * (Decimal(2) if unit == 'kg' else Decimal(1))
    if record['spec'] == '250g':
        base = Decimal(quantity)
    amount = base * Decimal(record['price'])
    return {
        'scenario': kind, 'requested_name': raw_name, 'company': company,
        'date': date, 'raw_quantity': raw_quantity, 'raw_unit': unit,
        'quantity': quantity, 'outcome': 'auto', 'review_reason': None,
        'selected': record, 'known_identity_record': record, 'candidates': [record],
        'candidate_set_status': 'unknown',
        'candidate_scope_note': '列出名称明确的目录记录；尚未证明可适配语义候选完整，不能将例示集合视为穷举。',
        'quantity_in_price_units': str(base),
        'conditional_nominal_amount_yuan': str(amount.quantize(Decimal('.01'))),
        'evidence': '目录精确完整品名' if raw_name == name else '目录单行斜杠互标的名称',
    }


def manual_event(raw_name, raw_q, unit, company, date, reason, reference=None):
    return {'scenario': 'manual_' + reason, 'requested_name': raw_name,
            'company': company, 'date': date, 'raw_quantity': raw_q, 'raw_unit': unit,
            'quantity': raw_q if raw_q and raw_q.isdigit() else None,
            'outcome': 'manual', 'review_reason': reason,
            'selected': None,
            'known_identity_record': reference if reason in {'missing_quantity', 'package_weight_unknown', 'unknown_company'} else None,
            'candidates': [reference] if reference else [],
            'candidate_set_status': 'unknown',
            'candidate_scope_note': '仅列举近似参考，完整可适配集合未知；不得作为选中商品。',
            'quantity_in_price_units': None, 'conditional_nominal_amount_yuan': None,
            'evidence': '原文明示约束与目录中可证事实不足以完成该项'}


def generate(seed, records, sources):
    rng = random.Random(seed)
    blocks = []
    q_choices = ['1', '2', '3', '4', '5', '1.5']
    for index, name in enumerate(NAMES):
        # Each product is requested twice by distinct companies; no duplicate
        # for the same product/company can imply a cancellation or replacement.
        for repetition in range(2):
            co = COMPANIES[(index + repetition) % len(COMPANIES)]
            date = f'2026-12-{rng.randint(1, 8):02d}'
            raw_name = rng.choice([name] + SYNONYMS.get(name, []))
            quantity = rng.choice(q_choices if records[name]['spec'] == '500g' else ['1', '2', '3'])
            raw_q = CHINESE[quantity] if quantity in CHINESE and rng.randrange(2) else quantity
            unit = rng.choice(['斤', 'kg']) if records[name]['spec'] == '500g' else '袋'
            fmt = rng.randrange(3)
            fragment = (f'{raw_q}{unit}{raw_name}' if fmt == 0 else
                        f'{raw_name}：{raw_q}{unit}' if fmt == 1 else f'{raw_name}{raw_q}{unit}')
            kind = ('catalog_synonym' if raw_name != name else 'catalog_exact')
            if fmt == 0:
                kind += '_quantity_first'
            e = normal_event(name, raw_name, quantity, raw_q, unit, co['company_name'], date, records[name], kind)
            blocks.append((co, date, [(fragment, e)]))

    # Same demand and date across the four companies tests routing without
    # incorporating any company-specific historical product preference.
    shared_name = '三色藜麦'
    for co in COMPANIES:
        date = '2026-12-10'
        fragment = f'{shared_name}2斤'
        e = normal_event(shared_name, shared_name, '2', '2', '斤', co['company_name'], date,
                         records[shared_name], 'same_demand_four_companies')
        blocks.append((co, date, [(fragment, e)]))

    # Two different companies in one source line; no evaluation ID appears there.
    for pair in [(0, 1), (2, 3)]:
        date = '2026-12-11'
        pieces = []
        for company_index, name, q in [(pair[0], '黄岩蜜桔', '3'), (pair[1], '云南蜜桔', '4')]:
            co = COMPANIES[company_index]
            fragment = f'{name}{q}斤'
            e = normal_event(name, name, q, q, '斤', co['company_name'], date,
                             records[name], 'inline_company_switch')
            pieces.append((f"{co['company_name']}：", fragment, e))
        blocks.append((COMPANIES[pair[0]], date, pieces))

    review_cases = [
        ('糙米', None, None, 'missing_quantity', '糙米'),
        ('糙米3袋', '3', '袋', 'package_weight_unknown', '糙米'),
        ('山月牌高粱米2斤', '2', '斤', 'brand_unverified', '高粱米'),
        ('丹丹郫县豆瓣酱（700克/袋）2袋', '2', '袋', 'specified_package_not_verified', '丹丹郫县豆瓣酱（250克/袋）'),
        ('雪雾星果2斤', '2', '斤', 'unknown_product', None),
    ]
    for i, (fragment, q, unit, reason, reference) in enumerate(review_cases):
        co = COMPANIES[i % len(COMPANIES)]
        date = f'2026-12-{12+i:02d}'
        name = fragment[:-len(q + unit)] if q and unit else fragment
        e = manual_event(name, q, unit, co['company_name'], date, reason,
                         records[reference] if reference else None)
        blocks.append((co, date, [(fragment, e)]))
    date = '2026-12-18'
    fragment = '血橙2斤'
    e = manual_event('血橙', '2', '斤', None, date, 'unknown_company', records['血橙'])
    e['raw_company'] = '云溪驿站'
    blocks.append(({'company_name': '云溪驿站', 'company_alias': '云溪驿站'}, date, [(fragment, e)]))
    rng.shuffle(blocks)

    lines = ['各单位分开记账，日期与公司以文本为准。']
    events = []
    for co, date, pieces in blocks:
        label = rng.choice([co['company_name'], co['company_alias']])
        lines.extend([f'公司：{label}', f'配送日期：{date}'])
        if len(pieces[0]) == 2:
            fragment, e = pieces[0]
            line = fragment
            spans = [(fragment, e, 0)]
        else:
            line = ''
            spans = []
            for prefix, fragment, e in pieces:
                if line:
                    line += '；'
                line += prefix
                start = len(line)
                line += fragment
                spans.append((fragment, e, start))
        lines.append(line)
        for fragment, item, start in spans:
            event = dict(item)
            event.update(event_id=f'{seed}-{len(events)+1:04d}', source_file='input/聊天.txt',
                         source_line=len(lines), source_line_text=line,
                         source_span={'start': start, 'end': start + len(fragment),
                                      'basis': 'line', 'unit': 'unicode_codepoints', 'text': fragment})
            events.append(event)

    request = {'companies': COMPANIES, 'batch_date': '2026-12-01', 'chat_files': ['聊天.txt'],
               'catalog_file': '../../../../../original/食堂食材基准价价格表10.12-10.18.xlsx',
               'company_policy': '按聊天公司标记切换；未登记公司保留异常',
               'date_policy': '聊天日期标题覆盖批次日期'}
    chat = ('\n'.join(lines) + '\n').encode('utf-8')
    request_bytes = dump(request)
    expected = {'schema': 'ordertcr.v7.fresh_expected.v1', 'seed': seed, 'split': SEEDS[seed],
                'events': events, 'notes': [
                    '候选集合标记 unknown 时，列出的参考不代表完整候选。',
                    'auto 是目录语义及计价条件下的预期，不代表实际成交。',
                    '保留种子共享商品池和生成代码，不构成全新商品分布或盲测。',
                    '单行偏移为 Unicode code point，左闭右开。',
                ]}
    expected_bytes = dump(expected)
    counts = Counter(e['scenario'] for e in events)
    auto_products = {e['selected']['sku'] for e in events if e['outcome'] == 'auto'}
    assert len(events) >= 50 and len(auto_products) >= 18
    assert {e['company'] for e in events} >= {c['company_name'] for c in COMPANIES}
    assert 'event_id' not in request
    manifest = {'schema': 'ordertcr.v7.fresh_manifest.v1', 'seed': seed, 'split': SEEDS[seed],
                'source_hashes': sources, 'event_count': len(events),
                'distinct_auto_catalog_records': len(auto_products),
                'scenario_denominators': dict(sorted(counts.items())),
                'input_sha256': {'input/request.json': digest(request_bytes), 'input/聊天.txt': digest(chat)},
                'expected_sha256': digest(expected_bytes),
                'input_contract': '仅提供 input/request.json 和 input/聊天.txt；价目表相对路径引用只读原件。expected/manifest/FREEZE仅供评测。',
                'separation': '7501/7502开发，7591保留；共享商品池，不称盲测或全新品种分布。',
                'exclusion_check': '每个完整品名和斜杠互标表达均与正式 aliases、selector.names、V6 challenge/extension 商品池做精确交集检查；不宣称全历史语义不重叠。'}
    manifest_bytes = dump(manifest)
    files = {'input/request.json': request_bytes, 'input/聊天.txt': chat,
             'expected.json': expected_bytes, 'manifest.json': manifest_bytes}
    freeze = {'schema': 'ordertcr.v7.fresh_freeze.v1',
              'sha256': {**{p: digest(b) for p, b in sorted(files.items())},
                         'builder': digest(Path(__file__).read_bytes()),
                         'source_catalog': sources['catalog_sha256']},
              'note': '构造完成后冻结；开发规则与样本此前已消费，非盲测。'}
    files['FREEZE.json'] = dump(freeze)
    return files, manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', action='store_true', help='reconstruct in memory and compare frozen bytes')
    args = parser.parse_args()
    records, sources = load_sources()
    all_files = {seed: generate(seed, records, sources) for seed in SEEDS}
    if args.verify:
        for seed, (files, manifest) in all_files.items():
            for relative, content in files.items():
                path = DEST / str(seed) / relative
                if not path.is_file() or path.read_bytes() != content:
                    raise ValueError(f'Frozen fixture differs: {seed}/{relative}')
            print(f"{seed} {manifest['split']}: bytes stable, {manifest['event_count']} events, {manifest['distinct_auto_catalog_records']} catalog records")
        return
    existing = [str(seed) for seed in SEEDS if (DEST / str(seed)).exists()]
    if existing:
        raise SystemExit('Refusing to overwrite frozen seed directories: ' + ', '.join(existing))
    for seed, (files, manifest) in all_files.items():
        for relative, content in files.items():
            path = DEST / str(seed) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        print(f"{seed} {manifest['split']}: {manifest['event_count']} events, {manifest['distinct_auto_catalog_records']} catalog records")


if __name__ == '__main__':
    main()
