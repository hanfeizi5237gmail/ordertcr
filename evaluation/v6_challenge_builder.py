#!/usr/bin/env python3
"""Freeze an independently generated synthetic order-mapping challenge.

Reads only original catalog, three original chats, and no runtime outputs. The
challenge is intentionally immutable: remove neither files nor the guard to rerun.
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / 'original'
DEST = ROOT / 'evaluation' / 'v6' / 'challenge'
CATALOG = ORIGINAL / '食堂食材基准价价格表10.12-10.18.xlsx'
CHAT_NAMES = ['星月站11.3下单.txt', '学院路站11.3下单.txt', '松北大道站11.3下单.txt']
COMPANIES = ['星月站', '学院路站', '松北大道站', '新港站']
SEEDS = {7201: 'dev', 7202: 'dev', 7291: 'holdout'}
NAMES = ['晴王葡萄', '啤梨', '麒麟西瓜', '黄元帅苹果', '红心火龙果', '哈密瓜',
         '徐香猕猴桃', '西柚', '人参果', '木瓜', '蓝莓（大）', '山竹', '新疆吊干杏',
         '西梅', '麻山药', '山东韭菜', '平菇（鲜蘑）', '干桂圆肉', '四粒红花生米',
         '金针菇 称重', '鲜活鲫鱼', '牛腱子（鲜）', '菠菜', '黑芝麻', '西兰花',
         '荷兰豆', '桔梗', '空心菜', '苦苣', '丝瓜', '马蹄', '茼蒿（辽宁）',
         '豌豆苗', '鲜藕', '姜', '荔浦芋头（优质）', '茶树菇', '薏米', '燕麦米',
         '绿豆', '黑豆', '荞麦米', '生腰果']
SYNTHETIC_SKU = '99880001'
DIGITS = '零一二三四五六七八九'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()


def write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def deterministic_xlsx(wb, modified: str) -> bytes:
    wb.properties.created = datetime(2020, 1, 1)
    wb.properties.modified = datetime(2020, 1, 1)
    buf = io.BytesIO()
    wb.save(buf)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(buf.getvalue()), 'r') as z_in, zipfile.ZipFile(out, 'w') as z_out:
        for name in sorted(z_in.namelist()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            data = z_in.read(name)
            if name == 'docProps/core.xml':
                data = re.sub(rb'(<dcterms:modified[^>]*>)[^<]+(</dcterms:modified>)',
                              lambda m: m.group(1) + modified.encode('ascii') + m.group(2), data)
            z_out.writestr(info, data)
    return out.getvalue()


def main():
    selected_seeds = [int(x) for x in sys.argv[1:]] or list(SEEDS)
    if len(set(selected_seeds)) != len(selected_seeds) or any(x not in SEEDS for x in selected_seeds):
        raise SystemExit('Usage: v6_challenge_builder.py [7201] [7202] [7291]')
    if any((DEST / str(x)).exists() for x in selected_seeds):
        raise SystemExit('A selected seed already exists; refusing to overwrite frozen fixtures.')
    source_bytes = CATALOG.read_bytes()
    chat_files = {n: (ORIGINAL / n).read_bytes() for n in CHAT_NAMES}
    history = ''.join(b.decode('utf-8-sig') for b in chat_files.values())
    wb = load_workbook(io.BytesIO(source_bytes), data_only=True)
    sheet = wb.active
    entries = {}
    ids = set()
    for idx, row in enumerate(sheet.iter_rows(min_row=5, values_only=True), 5):
        category, sku, name, price, spec = row[:5]
        if sku:
            ids.add(str(sku).strip())
        if name and str(name).strip() in NAMES:
            key = str(name).strip()
            if key in entries:
                raise ValueError(f'Catalog name not unique: {key}')
            entries[key] = {'sku': str(sku).strip(), 'name': key, 'price': str(price),
                            'spec': str(spec).strip(), 'sheet': sheet.title, 'row': idx}
    if set(entries) != set(NAMES) or SYNTHETIC_SKU in ids:
        raise ValueError('Missing chosen catalog record or synthetic identifier collision')
    if any(n in history for n in NAMES):
        raise ValueError('Selected exact catalog name occurs in original chat')
    if any(e['spec'].lower() != '500g' for e in entries.values()):
        raise ValueError('Chosen records must have a 500g pricing basis')
    if any('蓝翅蝴蝶兰的花瓣' in str(r[2] or '') for r in sheet.iter_rows(min_row=5, values_only=True)):
        raise ValueError('Missing-item challenge unexpectedly appears in source catalog')

    # Exactly equivalent synthetic catalog records make the price comparison
    # unambiguous; the second catalog raises the original SKU above its peer.
    original_broccoli = entries['西兰花']
    sheet.append(['蔬菜', SYNTHETIC_SKU, '西兰花', '5.49', '500g'])
    synthetic_row = sheet.max_row
    # Normalize XLSX core-property modification time and ZIP timestamps.
    catalog_a = deterministic_xlsx(wb, '2020-01-01T00:00:00Z')
    sheet.cell(original_broccoli['row'], 4).value = '6.49'
    catalog_b = deterministic_xlsx(wb, '2020-01-01T00:00:00Z')
    cats = {'catalog_A.xlsx': catalog_a, 'catalog_B.xlsx': catalog_b}

    for seed in selected_seeds:
        split = SEEDS[seed]
        rng = random.Random(seed)
        seed_dir = DEST / str(seed)
        events = []
        input_hashes = {}
        used_names = set()
        for company in COMPANIES:
            for version in ['A', 'B']:
                subdir = f'input/{company}/目录{version}'
                cat_name = f'catalog_{version}.xlsx'
                write(seed_dir / subdir / cat_name, cats[cat_name])
                input_hashes[f'{subdir}/{cat_name}'] = sha(cats[cat_name])
                request = {'company_name': company, 'company_alias': company[:-1],
                           'batch_date': '2026-11-14', 'chat_files': ['聊天.txt'],
                           'catalog_file': cat_name, 'catalog_version': version,
                           'date_policy': '聊天中的配送日期标题覆盖batch_date'}
                request_bytes = dump(request)
                write(seed_dir / subdir / 'request.json', request_bytes)
                input_hashes[f'{subdir}/request.json'] = sha(request_bytes)
                # Dates and event order are actually sampled; action chains remain
                # in temporal order. The fixed shared test is repeated in all firms.
                batches = []
                common_date = '2026-11-14'
                price_row = ({'sku': SYNTHETIC_SKU, 'name': '西兰花', 'price': '5.49',
                              'spec': '500g', 'sheet': sheet.title, 'row': synthetic_row}
                             if version == 'A' else
                             dict(original_broccoli, price='6.49'))
                other = (dict(original_broccoli) if version == 'A' else
                         {'sku': SYNTHETIC_SKU, 'name': '西兰花', 'price': '5.49',
                          'spec': '500g', 'sheet': sheet.title, 'row': synthetic_row})
                shared = {'scenario': 'company_invariance_and_catalog_price', 'requested_name': '西兰花',
                          'action': 'order', 'outcome': 'selected', 'selected': price_row,
                          'candidates': [price_row, other], 'quantity': '2', 'raw_unit': '斤',
                          'quantity_500g': '2', 'selection_basis': 'constraints_then_highest_price'}
                batches.append((common_date, [('西兰花2斤', [shared])]))
                chosen = rng.sample([n for n in NAMES if n not in {'西兰花', '菠菜', '黑芝麻'}], 11)
                variations = ['suffix', 'fullwidth', 'chinese', 'quantity_first', 'kg']
                rng.shuffle(variations)
                for index, name in enumerate(chosen):
                    used_names.add(name)
                    q = rng.randint(1, 9)
                    variation = variations[index] if index < len(variations) else rng.choice(variations)
                    if variation == 'suffix':
                        raw, unit, qty500 = f'{name}{q}斤', '斤', q
                    elif variation == 'fullwidth':
                        raw, unit, qty500 = f'{name}{chr(ord("０") + q)}斤', '斤', q
                    elif variation == 'chinese':
                        raw, unit, qty500 = f'{name}{DIGITS[q]}斤', '斤', q
                    elif variation == 'quantity_first':
                        raw, unit, qty500 = f'{q}斤的{name}', '斤', q
                    else:
                        raw, unit, qty500 = f'{name}{q}kg', 'kg', 2 * q
                    event = {'scenario': f'normal_{variation}', 'requested_name': name,
                             'action': 'order', 'outcome': 'selected', 'selected': entries[name],
                             'candidates': [entries[name]], 'quantity': str(q), 'raw_unit': unit,
                             'quantity_500g': str(qty500), 'selection_basis': 'full_name_and_spec'}
                    date = f'2026-11-{rng.randint(14,18):02d}'
                    batches.append((date, [(raw, [event])]))
                # Guarantee a genuinely contiguous two-item raw line in every seed.
                n1, n2 = rng.sample([n for n in NAMES if n not in {'西兰花', '菠菜', '黑芝麻'}], 2)
                q1, q2 = rng.randint(1, 9), rng.randint(1, 9)
                used_names.update((n1, n2))
                joined = [dict(scenario='continuous_input', requested_name=n, action='order', outcome='selected',
                               selected=entries[n], candidates=[entries[n]], quantity=str(q), raw_unit='斤',
                               quantity_500g=str(q), selection_basis='full_name_and_spec')
                          for n, q in ((n1, q1), (n2, q2))]
                batches.append((f'2026-11-{rng.randint(14,18):02d}', [(f'{n1}{q1}斤{n2}{q2}斤', joined)]))
                if company == '星月站' and version == 'A':
                    def issue(name, text, kind, outcome, selected=None, **extra):
                        ev = {'scenario': kind, 'requested_name': name, 'action': 'order',
                              'outcome': outcome, 'selected': entries[selected] if selected else None,
                              'quantity_500g': None, **extra}
                        return (text, [ev])
                    batches.append(('2026-11-19', [
                        issue('菠菜', '菠菜3斤', 'action_initial', 'selected', '菠菜', quantity='3', raw_unit='斤', quantity_500g='3', action='order', expected_state={'quantity_500g': '3', 'active': True}),
                        issue('菠菜', '菠菜再追加2斤', 'action_append', 'selected', '菠菜', quantity='2', raw_unit='斤', quantity_500g='2', action='append', expected_state={'quantity_500g': '5', 'active': True}),
                        issue('菠菜', '菠菜总量改为4斤', 'action_replace_quantity', 'selected', '菠菜', quantity='4', raw_unit='斤', quantity_500g='4', action='replace_total', expected_state={'quantity_500g': '4', 'active': True}),
                        issue('菠菜', '取消菠菜', 'action_cancel', 'cancelled', '菠菜', action='cancel', expected_state={'quantity_500g': '0', 'active': False}),
                    ]))
                    batches.append(('2026-11-20', [
                        issue('黑芝麻', '黑芝麻3斤', 'duplicate_original', 'selected', '黑芝麻', quantity='3', raw_unit='斤', quantity_500g='3', expected_state={'quantity_500g': '3', 'active': True}),
                        issue('黑芝麻', '重复转发上一条，请勿重复下单：黑芝麻3斤', 'duplicate_explicit', 'duplicate', '黑芝麻', quantity='3', raw_unit='斤', action='duplicate', expected_state={'quantity_500g': '3', 'active': True}),
                    ]))
                    each = [dict(scenario='each_quantity', requested_name=n, action='order', outcome='selected',
                                 selected=entries[n], quantity='3', raw_unit='斤', quantity_500g='3')
                            for n in ('菠菜', '黑豆')]
                    batches.append(('2026-11-21', [('菠菜、黑豆各3斤', each)]))
                    issues = [
                        issue('菠菜', '菠菜', 'quantity_missing', 'review', '菠菜', anomaly='quantity_missing'),
                        issue('黑豆', '黑豆4', 'unit_missing', 'review', '黑豆', quantity='4', anomaly='unit_missing'),
                        issue('黑芝麻', '黑芝麻0斤', 'quantity_zero', 'review', '黑芝麻', quantity='0', raw_unit='斤', anomaly='quantity_zero'),
                        issue('干桂圆肉', '干桂圆肉-2斤', 'quantity_negative', 'review', '干桂圆肉', quantity='-2', raw_unit='斤', anomaly='quantity_negative'),
                        issue('晴王葡萄', '晴王葡萄x斤', 'quantity_invalid', 'review', '晴王葡萄', anomaly='quantity_invalid'),
                        issue('西兰花', '欢乐园牌西兰花2斤', 'brand_conflict', 'review', anomaly='brand_unverified', prohibited_skus=['80003687', SYNTHETIC_SKU]),
                        issue('牛前腱', '牛前腱2斤', 'part_conflict', 'review', anomaly='part_unverified', prohibited_skus=['80000723', '80004828']),
                        issue('西兰花', '西兰花2kg整袋装1袋', 'spec_conflict', 'review', anomaly='packaging_unverified', prohibited_skus=['80003687', SYNTHETIC_SKU]),
                        issue('蓝翅蝴蝶兰的花瓣', '蓝翅蝴蝶兰的花瓣1斤', 'missing_catalog', 'unmatched', anomaly='no_catalog_match'),
                    ]
                    for i, item in enumerate(issues):
                        batches.append((f'2026-11-{22 + i // 3:02d}', [item]))
                # Randomly join compatible singleton normal batches on the same date.
                rng.shuffle(batches)
                lines = [f'公司：{company}', f'本批确认日期均为2026年，当前目录为目录{version}。']
                line_events = []
                cursor = 0
                while cursor < len(batches):
                    date, items = batches[cursor]
                    if (len(items) == 1 and items[0][1][0]['scenario'].startswith('normal_')
                            and cursor + 1 < len(batches)):
                        date2, items2 = batches[cursor + 1]
                        if date == date2 and len(items2) == 1 and items2[0][1][0]['scenario'].startswith('normal_'):
                            separator = rng.choice(['', '，', '、', ' '])
                            items = [(items[0][0] + separator + items2[0][0], items[0][1] + items2[0][1])]
                            cursor += 1
                    lines.append(f'配送日期：{date}')
                    for text, parts in items:
                        lines.append(text)
                        for part in parts:
                            record = dict(part, company=company, date=date, catalog_version=version,
                                          source_file=f'{subdir}/聊天.txt', source_line=len(lines),
                                          source_line_text=text, event_id=f'{seed}-{len(events)+1:04d}')
                            events.append(record)
                    cursor += 1
                txt = ('\n'.join(lines) + '\n').encode('utf-8')
                write(seed_dir / subdir / '聊天.txt', txt)
                input_hashes[f'{subdir}/聊天.txt'] = sha(txt)
        if len(used_names) < 20 or len(events) < 60:
            raise ValueError(f'Insufficient distinct product coverage in {seed}')
        concepts = Counter(e['scenario'] for e in events)
        expected = {'schema': 'ordertcr.synthetic_oracle.v1', 'seed': seed, 'split': split,
                    'source': 'frozen prior to system runs; derived only from input construction and input catalog rows',
                    'events': events,
                    'state_assertions': [
                        {'company': '星月站', 'catalog_version': 'A', 'date': '2026-11-19',
                         'requested_name': '菠菜', 'final_action': 'cancelled', 'final_quantity_500g': '0'},
                        {'company': '星月站', 'catalog_version': 'A', 'date': '2026-11-20',
                         'requested_name': '黑芝麻', 'final_quantity_500g': '3', 'duplicate_events_retained': 1},
                    ],
                    'scoring_notes': ['Each event is one semantic item, including 2 events in 各 quantities and joined lines.',
                                      'Review means no unsupported product, quantity, amount, or silently resolved constraint; retain known SKU where supplied.',
                                      'Score source-text coverage and state transition separately; no silent de-duplication.',
                                      'Exact-name source catalog rows and synthetic equivalent price rows are the entire oracle basis.']}
        expected_bytes = dump(expected)
        write(seed_dir / 'expected.json', expected_bytes)
        manifest = {'schema': 'ordertcr.synthetic_manifest.v1', 'seed': seed, 'split': split,
                    'independence': {'no_outputs_replay_or_shared_knowledge_read': True,
                                     'oracle_not_in_input_tree': True,
                                     'historical_159_is_not_reduced_or_replaced': True,
                                     'selected_catalog_exact_names_absent_from_original_chats': True,
                                     'source_overlap_check': 'exact full catalog name substring against three original chats; not a semantic synonym audit'},
                    'original_catalog': {'file': CATALOG.name, 'sha256': sha(source_bytes)},
                    'original_chats': {n: sha(b) for n, b in chat_files.items()},
                    'selected_catalog_records': [entries[n] for n in NAMES],
                    'synthetic_record': {'sku': SYNTHETIC_SKU, 'name': '西兰花', 'spec': '500g',
                                         'price_A': '5.49', 'price_B': '5.49', 'row': synthetic_row},
                    'catalog_change': {'sku': original_broccoli['sku'], 'name': '西兰花',
                                       'price_A': original_broccoli['price'], 'price_B': '6.49',
                                       'explanation': 'both versions include the synthetic equivalent; B raises the source SKU price'},
                    'companies': COMPANIES, 'event_count': len(events),
                    'normal_random_distinct_catalog_names': sorted(used_names),
                    'scenario_denominators': dict(sorted(concepts.items())),
                    'inputs_sha256': input_hashes, 'expected_sha256': sha(expected_bytes),
                    'source_randomness': 'Python random.Random(seed): sample, randint, choice, shuffle; action-chain relative order kept',
                    'input_contract': 'One directory per company and catalog version: only chat TXT and current catalog XLSX; no expected or manifest.'}
        write(seed_dir / 'manifest.json', dump(manifest))
        seed_hashes = {str(p.relative_to(seed_dir)): sha(p.read_bytes()) for p in sorted(seed_dir.rglob('*')) if p.is_file()}
        seed_hashes['builder'] = sha(Path(__file__).read_bytes())
        seed_freeze = dump({'sha256': seed_hashes, 'note': 'Frozen before any evaluated runs; does not hash itself.'})
        write(seed_dir / 'FREEZE.json', seed_freeze)
        print(f'{seed}: freeze_sha256={sha(seed_freeze)}')
        print(f'{seed}: split={split} events={len(events)} distinct_random_products={len(used_names)} oracle_sha256={sha(expected_bytes)} scenarios={dict(sorted(concepts.items()))}')
    readme = '''# V6 synthetic challenge (frozen before any evaluated run)

Seeds 7201 and 7202 are development; 7291 is held out. Each `input/<company>/目录A|目录B/` is a separate batch: use only its request JSON, chat TXT and catalog XLSX. All files outside input are evaluation-only. Dates and companies are explicit fictional context; 新港站 is a new company. The three original site names are reused as labels, with no company profiles or historic SKU preferences.

All selected source product full names are absent as exact substrings in the original chats. That is not a synonym-level audit. The 43 original catalog products, source SHA-256 values, randomized event counts, scenario denominators, input hashes and expected hash are in each manifest. This synthetic challenge is additional and does not shrink the 159 historical denominator. It provides no evidence of real-account accuracy.

A synthetic `99880001` 西兰花 500g is added to both complete copied catalogs (A 5.49, B 5.49); source `80003687` is 4.49 in A and 6.49 in B. Same-name and same-unit candidates are fully comparable. Every company receives 西兰花2斤 in both versions on the same date, so A selects the synthetic SKU and B selects the original SKU. It tests highest comparable price, company invariance, and catalog replacement. No previous price or preference should survive into B.

Each oracle event denotes one semantic item, not necessarily one source line. Same-line examples include consecutive orders and 各; explicit mutation chains must be evaluated in chronological message order, preserving event provenance. Quantity anomalies preserve established product identity and never authorize unsupported amount. For conflicts, the oracle asserts abstention from prohibited choices, not that every nearby candidate is invalid. Scores must separately report detection, selection, quantity, event/action state, unmatched and review, and source coverage with the scenario counts in manifest. `expected.json` and manifest are not runtime inputs.

Generated with `python3 -B ordertcr/evaluation/v6_challenge_builder.py 7201 7202`, then `python3 -B ordertcr/evaluation/v6_challenge_builder.py 7291`; script refuses to overwrite an existing seed directory. Inputs are synthetic and frozen without invoking the skill or inspecting outputs. Exact-name absence does not imply that related product families are absent from historical data, and the two development seeds and holdout draw from the same 43-product pool. Manual business interpretation and output-based validation have not been performed by this builder.
'''
    if not (DEST / 'README.md').exists():
        write(DEST / 'README.md', readme.encode())


if __name__ == '__main__':
    main()
