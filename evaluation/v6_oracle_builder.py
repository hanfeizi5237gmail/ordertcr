#!/usr/bin/env python3
"""Freeze evaluation-only order expectations without importing the order engine.

Reads original files and prior human review as evidence. No matcher, profile,
runtime, generated order, or prior replay result is an answer source.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
import re

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evaluation/v6/oracle'
STATIONS = {
    'songbei': ('松北大道站', '松北大道站11.3下单.txt', '松北大道站11.3.xlsx'),
    'xingyue': ('星月站', '星月站11.3下单.txt', '星月站11.3.xlsx'),
    'xueyuan': ('学院路站', '学院路站11.3下单.txt', '学院路站11.3.xlsx'),
}
CATALOG = '食堂食材基准价价格表10.12-10.18.xlsx'
SEEDS = {6108: 'development', 6109: 'development', 6110: 'holdout'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source(name):
    path = ROOT / 'original' / name
    data = path.read_bytes()
    return data, sha(data)


def catalog_records():
    data, fingerprint = source(CATALOG)
    sheet = load_workbook(ROOT / 'original' / CATALOG, read_only=True, data_only=True).active
    records = {}
    for index, (_, code, name, price, spec) in enumerate(sheet.values, 1):
        if index <= 4 or code is None or not name or not spec:
            continue
        key = str(code).strip()
        if key in records:
            raise ValueError(f'duplicate catalog code: {key}')
        records[key] = {'code': key, 'name': str(name).strip(), 'spec': str(spec).strip(),
                        'price': str(price), 'sheet': sheet.title, 'row': index}
    return records, fingerprint


def manual_rows(path):
    sheet = load_workbook(path, read_only=True, data_only=True).active
    result = {}
    for index in range(2, sheet.max_row + 1):
        cells = [sheet.cell(index, col).value for col in range(1, 11)]
        if cells[3] is None:
            continue
        result[index] = {'row': index, 'name': cells[3], 'spec': cells[4],
                         'quantity': str(cells[5]) if cells[5] is not None else None,
                         'price': str(cells[6]) if cells[6] is not None else None,
                         'discount': str(cells[7]) if cells[7] is not None else None,
                         'order_number': str(cells[1]) if cells[1] is not None else None}
    return result, sheet.title


def invoice_quantity(quantity, unit, spec):
    if quantity is None or spec is None:
        return None
    try:
        q = Decimal(str(quantity))
    except (ValueError, ArithmeticError):
        return None
    if spec == '500g' and unit == '斤':
        return str(q)
    if spec == '500g' and unit == '公斤':
        return str(q * 2)
    if unit == spec or unit == '个' and spec == '个':
        return str(q)
    return None


def source_date(text, start):
    """Only a standalone date heading can override the filename's 11.3 date."""
    headings = list(re.finditer(r'(?m)^\s*11月([345])(?:号)?\s*$', text[:start]))
    if headings:
        return f'2026-11-{int(headings[-1].group(1)):02}', 'explicit_chat_heading'
    return '2026-11-03', 'source_filename_11.3'


def historic_events(records):
    result, hashes = [], {}
    exceptions = json.loads((ROOT / 'evaluation/v3/exception-expectations.json').read_text())
    for station, (label, chat_name, manual_name) in STATIONS.items():
        data, chat_hash = source(chat_name)
        text = data.decode('utf-8-sig')
        _, manual_hash = source(manual_name)
        human, sheet = manual_rows(ROOT / 'original' / manual_name)
        review_path = ROOT / f'evaluation/v3/{station}.review.json'
        review = json.loads(review_path.read_text(encoding='utf-8'))
        recorded_hashes = review.get('source_sha256', {})
        if (recorded_hashes.get('chat', chat_hash) != chat_hash
                or recorded_hashes.get('manual', manual_hash) != manual_hash):
            raise ValueError(f'stale review source for {station}')
        hashes.update({chat_name: chat_hash, manual_name: manual_hash})
        cursor = 0
        for row in review['rows']:
            raw = row['raw_text']
            start = text.find(raw, cursor)
            if start < 0:
                raise ValueError(f'cannot locate source event {row["line_id"]}')
            end = start + len(raw)
            cursor = end
            manual = human.get(row['manual_row']) if row['manual_row'] else None
            codes = [str(code) for code in row['expected_codes']]
            selected = records.get(codes[0]) if len(codes) == 1 else None
            if any(code not in records for code in codes):
                raise ValueError(f'unknown review SKU {row["line_id"]}')
            exc = exceptions.get(row['line_id'])
            quantity, unit = row['quantity'], row['unit']
            shipping_date, date_basis = source_date(text, start)
            # An old human answer is evidence, not a transferable order truth.
            direct = bool(selected and selected['name'] in raw and str(quantity or '') in raw
                          and (unit is None or unit in raw))
            amount_basis = invoice_quantity(quantity, unit, selected['spec'] if selected else None)
            tier = 'direct' if direct and amount_basis is not None else 'conditional' if selected else 'uncertain'
            action = 'auto' if tier == 'direct' else 'manual' if selected or exc else 'undefined'
            discrepancies = []
            if manual:
                if selected and (manual['name'] != selected['name'] or manual['spec'] != selected['spec']):
                    discrepancies.append('manual_identity_differs_from_current_catalog_record')
                if quantity is not None and manual['quantity'] != str(quantity):
                    discrepancies.append('manual_delivered_quantity_is_not_requested_quantity')
            else:
                discrepancies.append('no_manual_row')
            if discrepancies and tier == 'direct':
                tier, action = 'conditional', 'manual'
            candidates = exc['expected_candidate_codes'] if exc else ([] if action == 'auto' else codes)
            missing = [code for code in candidates if code not in records]
            if missing:
                raise ValueError(f'unknown candidate SKU {missing}')
            reason = (exc['reason'] if exc else row['reason']) + ('；需人工确认旧人工答案与当前原文/目录关系' if action != 'auto' else '')
            control_pending = bool(re.search(r'再加|追加|改为|改量|取消|这样的|同上', raw))
            result.append({
                'event_id': row['line_id'],
                'source': {'station': station, 'file': chat_name, 'line': text.count('\n', 0, start) + 1,
                           'start': start, 'end': end, 'raw_text': raw, 'sha256': chat_hash},
                'expected': {'tier': tier, 'action': action, 'name': selected['name'] if action == 'auto' else None,
                             'spec': selected['spec'] if action == 'auto' else None, 'code': selected['code'] if action == 'auto' else None,
                             'quantity': quantity, 'unit': unit, 'company': station, 'date': shipping_date,
                             'date_basis': date_basis,
                             'invoice_quantity': amount_basis if action == 'auto' else None,
                             'candidate_codes': sorted(map(str, candidates)),
                             'candidate_set_status': 'reviewed_complete' if exc else 'human_witness_not_exhaustive' if action != 'auto' else 'not_applicable',
                             'control_expected_pending': control_pending,
                             'event_state': None if control_pending else 'active_or_unresolved',
                             'required_issue_codes': exc['required_issue_codes'] if exc else ([] if action == 'auto' else ['needs_manual_review']),
                             'reason': reason},
                'manual': {'file': manual_name, 'sheet': sheet, 'sha256': manual_hash,
                           'observed': manual, 'discrepancies': discrepancies},
                'evidence': ['original_chat_span', 'current_catalog_record' if selected else 'no_unique_catalog_identity',
                             'historical_human_review', 'original_manual_workbook' if manual else 'no_manual_workbook_row']})
    if len(result) != 159 or len({x['event_id'] for x in result}) != 159:
        raise ValueError('historic denominator must retain all 159 events')
    return result, hashes


def case(case_id, split, seed, scenario, lines, *, note=None, catalog_sha256=None):
    parts, expected = [], []
    position = 0
    for text, answer in lines:
        if parts:
            parts.append('\n')
            position += 1
        start = position
        parts.append(text)
        position += len(text)
        item_start = len(text) - len(answer.get('item_text', text))
        if item_start < 0 or text[item_start:] != answer.get('item_text', text):
            raise ValueError(f'item_text must be the suffix of generated line: {case_id}')
        expected.append({'source_span': [start + item_start, position],
                         'raw_text': answer.get('item_text', text),
                         **{k: v for k, v in answer.items() if k != 'item_text'}})
    return {'case_id': case_id, 'split': split, 'seed': seed, 'scenario': scenario,
            'raw_text': ''.join(parts) + '\n', 'expected_events': expected,
            'catalog_sha256': catalog_sha256, 'note': note}


def synth_answer(record, *, quantity='2', unit='catalog_spec', company='songbei', date='2026-11-10', action='auto', reason='目录同条记录完整品名和规格直接确定'):
    unit = record['spec'] if unit == 'catalog_spec' else unit
    invoice = invoice_quantity(quantity, unit, record['spec']) if action == 'auto' else None
    unit_price = Decimal(record['price']) * Decimal('0.95') if invoice is not None else None
    return {'tier': 'direct' if action == 'auto' else 'uncertain', 'action': action,
            'name': record['name'] if action == 'auto' else None,
            'spec': record['spec'] if action == 'auto' else None,
            'code': record['code'] if action == 'auto' else None,
            'quantity': quantity, 'unit': unit, 'company': company, 'date': date,
            'invoice_quantity': invoice, 'discount': '0.95' if unit_price is not None else None,
            'unit_price': str(unit_price) if unit_price is not None else None,
            'amount_exact': str(unit_price * Decimal(invoice)) if unit_price is not None else None,
            'candidate_codes': [] if action == 'auto' else [record['code']],
            'required_issue_codes': [] if action == 'auto' else ['needs_manual_review'],
            'reason': reason}


def order_phrase(record, quantity='2', unit=None):
    """Put the order quantity after a separator; catalog digits remain product name."""
    spec = record['spec']
    unit = unit if unit is not None else ('斤' if spec == '500g' else '个' if spec == '个' else '袋' if spec == '袋' else '盒' if spec == '盒' else '瓶' if spec == '瓶' else '包' if spec == '包' else '件')
    if unit == '件':
        return f'{record["name"]}，数量{quantity}件', unit, False
    return f'{record["name"]}，数量{quantity}{unit}', unit, unit == spec or spec == '500g' and unit == '斤'


def fixture_line(record, *, quantity='2', company='songbei', date='2026-11-10', action='auto', reason=None):
    text, unit, supported = order_phrase(record, quantity)
    if action == 'auto' and not supported:
        action = 'manual'
        reason = '目录规格无法从订购计数直接换算；品名同条记录可核，计价量待确认'
    expected = synth_answer(record, quantity=quantity, unit=unit, company=company,
                            date=date, action=action, reason=reason or '目录同条记录完整品名和规格直接确定')
    expected['item_text'] = text
    return f'{STATIONS.get(company, ("新公司",))[0]} {date}：{text}', expected


def build_cases(events, records, catalog_hash):
    cases = []
    original_words = ''.join(e['source']['raw_text'] for e in events)
    used_codes = {code for event in events
                  for code in ([event['expected']['code']] + event['expected']['candidate_codes']) if code}
    manual_names = {event['manual']['observed']['name'] for event in events
                    if event['manual']['observed']}
    # Stable catalog selection; no matcher, profile, or replay output enters this selection.
    unique = Counter(r['name'] for r in records.values())
    reserved = [r for r in sorted(records.values(), key=lambda x: x['code'])
                if unique[r['name']] == 1 and r['name'] not in original_words
                and r['name'] not in manual_names and r['code'] not in used_codes
                and len(r['name']) >= 3 and r['spec'] in ('500g', '个', '袋', '盒', '瓶', '包')]
    if len(reserved) < 20:
        raise ValueError('fewer than 20 independent catalog reserve products')
    reserved = reserved[::max(1, len(reserved) // 40)][:20]
    by_station = defaultdict(list)
    for item in events:
        by_station[item['source']['station']].append(item)
    for seed, split in SEEDS.items():
        rng = random.Random(seed)
        for station in STATIONS:
            shuffled = by_station[station][:]
            rng.shuffle(shuffled)
            for batch, begin in enumerate(range(0, len(shuffled), 11), 1):
                lines = []
                for item in shuffled[begin:begin + 11]:
                    expected = dict(item['expected'])
                    expected['source_event_id'] = item['event_id']
                    expected['date_basis'] = 'source_provenance_not_reordered_text'
                    # Reordering can detach date and amendment context; retain ambiguity.
                    if expected['action'] != 'auto':
                        expected['invoice_quantity'] = None
                    lines.append((item['source']['raw_text'], expected))
                cases.append(case(f'{seed}-{station}-split-{batch:02}', split, seed,
                                  'single_station_split', lines, catalog_sha256=catalog_hash,
                                  note='同站原始事件各出现一次；跨事件追加/改量/取消不推断已解决'))
            reordered = []
            for item in shuffled:
                answer = {**item['expected'], 'source_event_id': item['event_id'],
                          'date_basis': 'source_provenance_not_reordered_text'}
                reordered.append((f'{STATIONS[station][0]} {answer["date"]}：{item["source"]["raw_text"]}', answer))
            cases.append(case(f'{seed}-{station}-reorder-all', split, seed,
                              'single_station_reorder', reordered, catalog_sha256=catalog_hash,
                              note='整站全事件重排；每事件显式公司和原日期，关系控制待核不被臆造'))
        # Ten disjoint cross-station groups collectively contain all 159 events once.
        mixed_groups = [[] for _ in range(10)]
        for station in STATIONS:
            station_events = by_station[station][:]
            rng.shuffle(station_events)
            for index, item in enumerate(station_events):
                mixed_groups[index % len(mixed_groups)].append(item)
        for index, group in enumerate(mixed_groups, 1):
            rng.shuffle(group)
            lines = [(f'{STATIONS[item["source"]["station"]][0]} {item["expected"]["date"]}：{item["source"]["raw_text"]}',
                      {**item['expected'], 'source_event_id': item['event_id'],
                       'date_basis': 'source_provenance_explicit_in_generated_text'}) for item in group]
            cases.append(case(f'{seed}-cross-all-{index:02}', split, seed,
                              'cross_station_combination', lines, catalog_sha256=catalog_hash,
                              note='跨站10组共完整159事件，文本逐行带公司和原日期；不能串用原站人工SKU'))
        # Cross-station text carries explicit company labels, never applies a source SKU to another company.
        mixed = [rng.choice(by_station[station]) for station in STATIONS]
        cases.append(case(f'{seed}-cross-explicit', split, seed, 'cross_station_explicit_company',
                          [(f'{STATIONS[e["source"]["station"]][0]} {e["expected"]["date"]}：{e["source"]["raw_text"]}',
                            {**e['expected'], 'source_event_id': e['event_id']}) for e in mixed],
                          catalog_sha256=catalog_hash, note='公司由文本显式提供；无法独立确定身份仍保留manual/undefined'))
        chosen = reserved[seed % len(reserved)]
        cases.append(case(f'{seed}-newco', split, seed, 'new_company_same_product',
                          [(f'新公司 2026-11-10：{chosen["name"]}2{chosen["spec"]}',
                            synth_answer(chosen, company='newco'))], catalog_sha256=catalog_hash))
        cases.append(case(f'{seed}-quantity-date', split, seed, 'quantity_date_variation',
                          [(f'松北大道站 2026-11-11：{chosen["name"]}3{chosen["spec"]}',
                            synth_answer(chosen, quantity='3', date='2026-11-11'))], catalog_sha256=catalog_hash))
        for index, product in enumerate(reserved, 1):
            cases.append(case(f'{seed}-reserved-{index:02}', split, seed, 'unseen_catalog_expression',
                              [(f'星月站 2026-11-10：{product["name"]}2{product["spec"]}',
                                synth_answer(product, company='xingyue'))], catalog_sha256=catalog_hash,
                              note='原聊天未出现该完整目录名；当前目录名称和规格从同一记录读取'))
        cases.append(case(f'{seed}-brand-package', split, seed, 'brand_part_form_package_interference',
                          [(f'学院路站 2026-11-10：其他品牌{chosen["name"]}2{chosen["spec"]}，换另一种包装',
                            synth_answer(chosen, company='xueyuan', action='manual', reason='明确品牌及包装冲突；候选仅为同名原目录记录，不能自动选择'))],
                          catalog_sha256=catalog_hash))
        cases.append(case(f'{seed}-missing-quantity', split, seed, 'missing_quantity_unit',
                          [(f'松北大道站 2026-11-10：{chosen["name"]}',
                            synth_answer(chosen, quantity=None, unit=None, action='manual', reason='缺数量和单位；商品身份候选可核，金额不可定'))],
                          catalog_sha256=catalog_hash))
        names = [r for r in records.values() if '蒜蓉辣酱' in r['name']]
        candidates = sorted(r['code'] for r in names)
        cases.append(case(f'{seed}-multi-candidate', split, seed, 'multiple_candidates',
                          [('星月站 2026-11-10：蒜蓉辣酱2瓶',
                            {'tier': 'uncertain', 'action': 'manual', 'name': None, 'spec': None, 'code': None,
                             'quantity': '2', 'unit': '瓶', 'company': 'xingyue', 'date': '2026-11-10',
                             'invoice_quantity': None, 'candidate_codes': candidates,
                             'required_issue_codes': ['needs_manual_review'],
                             'reason': '当前目录所有名称含蒜蓉辣酱的候选；品牌和规格未知，不以人工旧表反推'})],
                          catalog_sha256=catalog_hash))
        cases.append(case(f'{seed}-amendment', split, seed, 'duplicate_append_change_cancel',
                          [(f'松北大道站 2026-11-10：{chosen["name"]}2{chosen["spec"]}', synth_answer(chosen)),
                           (f'再加{chosen["name"]}1{chosen["spec"]}', synth_answer(chosen, quantity='1', action='manual', reason='追加关系需确认前件')),
                           (f'{chosen["name"]}改为3{chosen["spec"]}', synth_answer(chosen, quantity='3', action='manual', reason='改量关系需确认前件')),
                           (f'取消{chosen["name"]}', synth_answer(chosen, quantity=None, action='manual', reason='取消关系需确认前件'))],
                          catalog_sha256=catalog_hash,
                          note='同商品有多个前驱；控制状态期望待独立人工标注，不宣称C完整通过'))
        cases[-1]['control_expected_pending'] = True
        for event in cases[-1]['expected_events']:
            event['control_expected_pending'] = True
            event['event_state'] = None
        single_change = case(f'{seed}-unique-change', split, seed, 'unique_predecessor_change',
                             [(f'松北大道站 2026-11-10：{chosen["name"]}2{chosen["spec"]}',
                               {**synth_answer(chosen), 'event_state': 'superseded',
                                'amount_exact': None, 'amount_in_subtotal': False, 'control_expected_pending': False}),
                              (f'{chosen["name"]}改为3{chosen["spec"]}',
                               {**synth_answer(chosen, quantity='3'), 'event_state': 'active',
                                'amount_in_subtotal': True, 'control_expected_pending': False})],
                             catalog_sha256=catalog_hash,
                             note='唯一前驱改量；原行金额空，新行按3个规格计价；两个事件都留总账')
        cases.append(single_change)
        cases.append(case(f'{seed}-unique-cancel', split, seed, 'unique_predecessor_cancel',
                          [(f'松北大道站 2026-11-10：{chosen["name"]}2{chosen["spec"]}',
                            {**synth_answer(chosen), 'event_state': 'inactive',
                             'amount_exact': None, 'amount_in_subtotal': False, 'control_expected_pending': False}),
                           (f'取消{chosen["name"]}',
                            {**synth_answer(chosen, quantity=None, action='manual', reason='唯一前驱取消；控制行保留在事件总账与异常订单'),
                             'event_state': 'control', 'amount_in_subtotal': False,
                             'control_expected_pending': False, 'required_issue_codes': ['cancellation']})],
                          catalog_sha256=catalog_hash,
                          note='取消控制行和旧行金额均空；不得从分母剔除'))
        duplicate_line = f'松北大道站 2026-11-10：{chosen["name"]}2{chosen["spec"]}'
        cases.append(case(f'{seed}-duplicate', split, seed, 'same_quantity_without_explicit_append',
                          [(duplicate_line, {**synth_answer(chosen), 'event_state': 'active',
                                             'control_expected_pending': False}),
                           (duplicate_line, {**synth_answer(chosen, action='manual', reason='同量重复但无明确追加，保留事件并标注待核'),
                                             'event_state': 'duplicate_event', 'amount_in_subtotal': False,
                                             'control_expected_pending': False,
                                             'required_issue_codes': ['duplicate_event']})],
                          catalog_sha256=catalog_hash,
                          note='同量重复保留两条来源span，第二条不可静默去重或当明确追加'))
        cases.append(case(f'{seed}-typo', split, seed, 'synonym_typo',
                          [(f'松北大道站 2026-11-10：{chosen["name"][:-1]}2{chosen["spec"]}',
                            synth_answer(chosen, action='manual', reason='截短商品名模拟错字；无独立同义证据，不能猜SKU'))],
                          catalog_sha256=catalog_hash))
        # A declared catalog update changes only price in the fixture; no historic ledger is reused.
        updated_price = Decimal(chosen['price']) + Decimal('1.00')
        update = case(f'{seed}-catalog-update', split, seed, 'catalog_update',
                      [(f'松北大道站 2026-11-11：{chosen["name"]}2{chosen["spec"]}',
                        {**synth_answer(chosen, date='2026-11-11'),
                         'catalog_price': str(updated_price), 'discount': '0.95',
                         'unit_price': str(updated_price * Decimal('0.95')),
                         'amount_exact': str(updated_price * Decimal('0.95') * 2)})],
                      catalog_sha256=catalog_hash,
                      note='仅测试声明的目录版本切换；必须先注册新表，再按其价格计算，不用旧价')
        update['catalog_override'] = {'base_catalog_sha256': catalog_hash, 'code': chosen['code'],
                                      'old_price': chosen['price'], 'new_price': str(updated_price),
                                      'same_record_name': chosen['name'], 'same_record_spec': chosen['spec']}
        cases.append(update)
    return cases, reserved


def build():
    records, catalog_hash = catalog_records()
    events, hashes = historic_events(records)
    cases, reserved = build_cases(events, records, catalog_hash)
    result = {'schema_version': 1, 'frozen_before_runtime': True,
              'evaluation_type': 'independent_original_and_catalog_oracle',
              'source_hashes': {**hashes, CATALOG: catalog_hash}, 'catalog_sha256': catalog_hash,
              'catalog_records': {r['code']: r for r in reserved},
              'company_labels': {k: v[0] for k, v in STATIONS.items()} | {'newco': '新公司'},
              'event_count': len(events), 'historical_events': events, 'cases': cases,
              'seed_splits': {str(k): v for k, v in SEEDS.items()},
              'case_count': len(cases),
              'limits': ['历史人工答案只是证据，非独立SKU或实重真值',
                         'conditional/uncertain与manual/undefined均纳入完整159事件分母',
                         '保留集按原目录未参与原聊天的完整名称与规格生成，不是未见真实订单',
                         '评测侧文件禁止作为执行器输入或运行时profile']}
    return result


def corrected_history(v1, records):
    """Correct v1's unsupported manual verdicts before reading any runtime result."""
    import copy
    events = copy.deepcopy(v1['historical_events'])
    semantic_rules = {
        '鸡块5斤': ('80004195', '鸡块与鸡肉块同一切块形态，未指定腿部'),
        '红色尖椒4斤': ('80003500', '红色尖椒与红尖椒同色同品种'),
        '海天上等耗油1瓶': ('80004513', '耗油为蚝油常见错写，品牌及上等等属性相同'),
        '土豆15斤': ('80003671', '普通土豆不等于新土豆；目录土豆（大885）为可比当前项'),
        '土豆30斤': ('80003671', '普通土豆不等于新土豆；目录土豆（大885）为可比当前项'),
        '尖椒4斤': ('80003528', '普通与地产尖椒500g可比，按最高挂牌价选择；红尖椒是颜色不同品种'),
        '尖椒20斤': ('80003528', '普通与地产尖椒500g可比，按最高挂牌价选择；红尖椒是颜色不同品种'),
        '油豆角6斤': ('80003749', '明确油豆角品种，目录同名500g计价'),
        '豇豆角6斤': ('80003532', '明确豇豆角品种，目录同名500g计价'),
        '架豆王5斤': ('80003525', '明确架豆王品种，目录架豆王（豆角）500g计价'),
        '护心肉10斤': ('80000759', '生猪护心肉与原文部位一致，目录500g计价'),
        '青萝卜6斤': ('80003609', '目录同名青萝卜500g计价'),
        '紫皮洋葱7斤': ('80004785', '紫皮洋葱与目录优质紫洋葱同品，优质非冲突品种'),
        '红色尖椒4斤': ('80003500', '红色尖椒与红尖椒同色同品种'),
    }
    counts = Counter()
    for event in events:
        expected = event['expected']
        if expected['tier'] != 'conditional':
            continue
        witness = expected['candidate_codes']
        selected = records[witness[0]] if len(witness) == 1 else None
        quantity, unit = expected['quantity'], expected['unit']
        invoice = invoice_quantity(quantity, unit, selected['spec'] if selected else None)
        if quantity is None:
            gap = 'missing_quantity'
        elif unit is None:
            gap = 'missing_unit'
        elif invoice is None:
            gap = 'quantity_to_catalog_unit_unproven'
        else:
            gap = None
        if gap:
            counts[gap] += 1
            expected.update(action='manual', code=None, name=None, spec=None,
                            invoice_quantity=None, required_issue_codes=[],
                            status_predicate='requires_human_for_missing_fact',
                            reason=f'{gap}；原文数量/单位仍保留，旧人工实重不能回填。')
            continue
        raw = event['source']['raw_text']
        semantic = semantic_rules.get(raw)
        if semantic and selected and selected['code'] == semantic[0]:
            proof = semantic[1]
        elif selected and selected['name'] in raw:
            proof = '原文直接包含当前目录同条品名；斤与500g计价量可换算'
        else:
            proof = None
        if proof:
            expected.update(tier='direct', action='auto', code=selected['code'],
                            name=selected['name'], spec=selected['spec'],
                            invoice_quantity=invoice, candidate_codes=[],
                            candidate_set_status='not_applicable', required_issue_codes=[],
                            status_predicate='catalog_identity_and_quantity_direct',
                            reason=proof + '；旧人工配货量和历史价不作答案')
            counts['independently_auto'] += 1
        else:
            expected.update(action='undefined', code=None, name=None, spec=None,
                            invoice_quantity=None, required_issue_codes=[],
                            status_predicate='identity_not_independently_determined',
                            reason='当前原文与目录不足以独立确定旧人工所选SKU；该项仍计入历史分母，不将人工猜测当自动或应人工真值')
            counts['identity_undefined'] += 1
    return events, dict(counts)


def corrected_cases(events, records, catalog_hash):
    """Rebuild v2 text from original spans and explicit source routing."""
    cases = []
    by_station = defaultdict(list)
    for event in events:
        by_station[event['source']['station']].append(event)
    old_words = ''.join(e['source']['raw_text'] for e in events)
    old_names = {e['manual']['observed']['name'] for e in events if e['manual']['observed']}
    used_codes = {code for e in events for code in
                  ([e['expected']['code']] + e['expected']['candidate_codes']) if code}
    unique = Counter(r['name'] for r in records.values())
    reserve = [r for r in sorted(records.values(), key=lambda r: r['code'])
               if r['spec'] == '500g' and unique[r['name']] == 1
               and r['name'] not in old_words and r['name'] not in old_names
               and r['code'] not in used_codes and len(r['name']) >= 3]
    reserve = reserve[::max(1, len(reserve) // 40)][:20]
    if len(reserve) != 20:
        raise ValueError('v2 needs 20 unused, unambiguous 500g catalog identities')

    def historical_line(item):
        raw = item['source']['raw_text']
        answer = {**item['expected'], 'source_event_id': item['event_id'],
                  'item_text': raw, 'date_basis': 'source_date_explicitly_rendered'}
        return f'{STATIONS[item["source"]["station"]][0]} {answer["date"]}：{raw}', answer

    for seed, split in SEEDS.items():
        rng = random.Random(seed)
        for station in STATIONS:
            shuffled = by_station[station][:]
            rng.shuffle(shuffled)
            for batch, begin in enumerate(range(0, len(shuffled), 11), 1):
                lines = [historical_line(item) for item in shuffled[begin:begin + 11]]
                cases.append(case(f'{seed}-{station}-split-{batch:02}', split, seed,
                                  'single_station_split', lines, catalog_sha256=catalog_hash))
            cases.append(case(f'{seed}-{station}-reorder-all', split, seed,
                              'single_station_reorder', [historical_line(item) for item in shuffled],
                              catalog_sha256=catalog_hash))
        groups = [[] for _ in range(10)]
        for station in STATIONS:
            pool = by_station[station][:]
            rng.shuffle(pool)
            for index, item in enumerate(pool):
                groups[index % len(groups)].append(item)
        for index, group in enumerate(groups, 1):
            rng.shuffle(group)
            cases.append(case(f'{seed}-cross-all-{index:02}', split, seed,
                              'cross_station_combination', [historical_line(item) for item in group],
                              catalog_sha256=catalog_hash))
        chosen = reserve[seed % len(reserve)]
        cases.append(case(f'{seed}-cross-explicit', split, seed, 'cross_station_explicit_company',
                          [historical_line(rng.choice(by_station[station])) for station in STATIONS],
                          catalog_sha256=catalog_hash))
        cases.append(case(f'{seed}-newco', split, seed, 'new_company_same_product',
                          [fixture_line(chosen, company='newco')], catalog_sha256=catalog_hash))
        cases.append(case(f'{seed}-quantity-date', split, seed, 'quantity_date_variation',
                          [fixture_line(chosen, quantity='3', date='2026-11-11')], catalog_sha256=catalog_hash))
        for index, product in enumerate(reserve, 1):
            cases.append(case(f'{seed}-reserved-{index:02}', split, seed, 'unseen_catalog_expression',
                              [fixture_line(product, company='xingyue')], catalog_sha256=catalog_hash))
        item, _, _ = order_phrase(chosen)
        cases.append(case(f'{seed}-brand-package', split, seed, 'brand_part_form_package_interference',
                          [(f'学院路站 2026-11-10：其他品牌{item}，换另一种包装',
                            {**synth_answer(chosen, quantity='2', unit='斤', company='xueyuan', action='manual',
                                            reason='明确其他品牌和包装冲突，不能选当前目录原记录'),
                             'item_text': f'其他品牌{item}，换另一种包装'})], catalog_sha256=catalog_hash))
        cases.append(case(f'{seed}-missing-quantity', split, seed, 'missing_quantity_unit',
                          [(f'松北大道站 2026-11-10：{chosen["name"]}',
                            {**synth_answer(chosen, quantity=None, unit=None, action='manual',
                                            reason='缺数量和单位，不能计算金额'), 'item_text': chosen['name']})],
                          catalog_sha256=catalog_hash))
        candidates = sorted(r['code'] for r in records.values() if '蒜蓉辣酱' in r['name'])
        cases.append(case(f'{seed}-multi-candidate', split, seed, 'multiple_candidates',
                          [('星月站 2026-11-10：蒜蓉辣酱，数量2瓶',
                            {'tier': 'uncertain', 'action': 'manual', 'name': None, 'spec': None, 'code': None,
                             'quantity': '2', 'unit': '瓶', 'company': 'xingyue', 'date': '2026-11-10',
                             'invoice_quantity': None, 'candidate_codes': candidates,
                             'candidate_set_status': 'exact_name_contains_concept',
                             'required_issue_codes': [], 'status_predicate': 'multiple_catalog_identities',
                             'reason': '品牌和包装未知，列出目录所有名称包含蒜蓉辣酱的记录',
                             'item_text': '蒜蓉辣酱，数量2瓶'})], catalog_sha256=catalog_hash))
        # Each control line carries routing and date; item span excludes its prefix.
        first = fixture_line(chosen)
        changed = fixture_line(chosen, quantity='3')
        add_text = f'再加{chosen["name"]}，数量1斤'
        change_text = f'{chosen["name"]}改为数量3斤'
        cancel_text = f'取消{chosen["name"]}'
        cases.append(case(f'{seed}-amendment', split, seed, 'duplicate_append_change_cancel',
                          [first,
                           (f'松北大道站 2026-11-10：{add_text}',
                            {**synth_answer(chosen, quantity='1', unit='斤', action='manual', reason='多个前驱时追加关系待核'),
                             'item_text': add_text}),
                           (f'松北大道站 2026-11-10：{change_text}',
                            {**synth_answer(chosen, quantity='3', unit='斤', action='manual', reason='多个前驱时改量关系待核'),
                             'item_text': change_text}),
                           (f'松北大道站 2026-11-10：{cancel_text}',
                            {**synth_answer(chosen, quantity=None, unit=None, action='manual', reason='多个前驱时取消关系待核'),
                             'item_text': cancel_text})], catalog_sha256=catalog_hash,
                          note='多前驱控制待独立标注；不宣称完整C通过'))
        cases[-1]['control_expected_pending'] = True
        for item_expected in cases[-1]['expected_events']:
            item_expected.update(control_expected_pending=True, event_state=None)
        cases.append(case(f'{seed}-unique-change', split, seed, 'unique_predecessor_change',
                          [(first[0], {**first[1], 'event_state': 'superseded',
                                       'amount_exact': None, 'amount_in_subtotal': False}),
                           (f'松北大道站 2026-11-10：{change_text}',
                            {**changed[1], 'item_text': change_text, 'event_state': 'active',
                             'amount_in_subtotal': True})], catalog_sha256=catalog_hash))
        cases.append(case(f'{seed}-unique-cancel', split, seed, 'unique_predecessor_cancel',
                          [(first[0], {**first[1], 'event_state': 'inactive',
                                       'amount_exact': None, 'amount_in_subtotal': False}),
                           (f'松北大道站 2026-11-10：{cancel_text}',
                            {**synth_answer(chosen, quantity=None, unit=None, action='manual', reason='唯一前驱取消；控制行保留'),
                             'item_text': cancel_text, 'event_state': 'control',
                             'amount_in_subtotal': False, 'required_issue_codes': ['cancellation']})],
                          catalog_sha256=catalog_hash))
        cases.append(case(f'{seed}-duplicate', split, seed, 'same_quantity_without_explicit_append',
                          [first, (first[0], {**first[1], 'event_state': 'duplicate_event',
                                             'amount_exact': None, 'amount_in_subtotal': False,
                                             'required_issue_codes': ['duplicate_event']})],
                          catalog_sha256=catalog_hash))
        typo = chosen['name'][:-1] + '，数量2斤'
        cases.append(case(f'{seed}-typo', split, seed, 'synonym_typo',
                          [(f'松北大道站 2026-11-10：{typo}',
                            {**synth_answer(chosen, quantity='2', unit='斤', action='undefined',
                                            reason='截短品名没有独立同义证据，不能猜唯一SKU'),
                             'item_text': typo, 'candidate_codes': [], 'required_issue_codes': []})],
                          catalog_sha256=catalog_hash))
        updated_price = Decimal(chosen['price']) + Decimal('1.00')
        update_line = fixture_line(chosen, date='2026-11-11')
        update = case(f'{seed}-catalog-update', split, seed, 'catalog_update',
                      [(update_line[0], {**update_line[1], 'catalog_price': str(updated_price),
                                         'unit_price': str(updated_price * Decimal('0.95')),
                                         'amount_exact': str(updated_price * Decimal('0.95') * 2)})],
                      catalog_sha256=catalog_hash)
        update['catalog_override'] = {'base_catalog_sha256': catalog_hash, 'code': chosen['code'],
                                      'old_price': chosen['price'], 'new_price': str(updated_price),
                                      'same_record_name': chosen['name'], 'same_record_spec': chosen['spec']}
        cases.append(update)
    return cases, reserve


def build_v2():
    v1_path = OUT / 'oracle.json'
    v1_bytes = v1_path.read_bytes()
    v1 = json.loads(v1_bytes)
    records, catalog_hash = catalog_records()
    if catalog_hash != v1['catalog_sha256']:
        raise ValueError('current original catalog differs from frozen v1 source')
    events, corrections = corrected_history(v1, records)
    cases, reserve = corrected_cases(events, records, catalog_hash)
    result = {**v1, 'schema_version': 2, 'parent_oracle_sha256': sha(v1_bytes),
              'historical_events': events, 'cases': cases, 'case_count': len(cases),
              'catalog_records': {r['code']: r for r in reserve},
              'pre_run_corrections': corrections,
              'evaluation_type': 'pre_run_independent_oracle_corrected_schema_and_inputs',
              'limits': v1['limits'] + ['v1保留为无效初版：将未知身份误判应人工，合成数量与目录规格粘连，拆分缺公司日期',
                                       'v2先冻结再运行；undefined留原始分母，不作为机器问题代码判负']}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--v2', action='store_true', help='write a separate corrected oracle_v2.json; preserve v1')
    args = parser.parse_args()
    result = build_v2() if args.v2 else build()
    OUT.mkdir(parents=True, exist_ok=True)
    name = 'oracle_v2.json' if args.v2 else 'oracle.json'
    path = OUT / name
    encoded = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    if path.exists() and path.read_bytes() != encoded:
        raise SystemExit('oracle already frozen with different content; do not overwrite')
    path.write_bytes(encoded)
    fingerprint = sha(encoded)
    sidecar = OUT / ('oracle_v2.sha256' if args.v2 else 'oracle.sha256')
    sidecar.write_text(f'{fingerprint}  {name}\n', encoding='ascii')
    print(json.dumps({'events': result['event_count'], 'cases': result['case_count'],
                      'sha256': fingerprint, 'path': str(path)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
