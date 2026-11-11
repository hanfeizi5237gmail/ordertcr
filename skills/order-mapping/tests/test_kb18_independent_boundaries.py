"""Independent synthetic KB18 probe; stdout JSON, exit 1 on failed contracts."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
SKILL = ROOT / 'ordertcr/skills/order-mapping'
sys.path.insert(0, str(SKILL / 'scripts'))
from order_mapping_core.parsing import parse_chat
from order_mapping_core.knowledge import match_shared

# Each catalog is a deliberately fictional current catalog, not an alias list.
# Candidate completeness cannot be established from these positive witnesses.
CASES = [
    ('suffix_form', '名称的属性款', '松茸菇的干款2斤', ['干松茸菇'], 'one_exact', 2),
    ('prefix_form', '要属性的名称', '2斤要冷冻的紫河鱼', ['冷冻紫河鱼'], 'one_exact', 2),
    ('comma_form', '逗号要求属性', '紫河鱼，要求冷冻3斤', ['冷冻紫河鱼'], 'one_exact', 3),
    ('quantity_unit_kg', '数量与单位', '彩椒的进口款2公斤', ['进口彩椒'], 'one_exact', 2),
    ('unknown_attribute', '未知属性', '彩椒的星光款2斤', ['彩椒'], 'no_auto', 2),
    ('unknown_brand', '未知品牌', '彩椒的蓝岛牌款2斤', ['彩椒'], 'no_auto', 2),
    ('negative', '否定', '紫河鱼不要冷冻款2斤', ['冷冻紫河鱼', '紫河鱼'], 'no_auto', 2),
    ('processing', '加工', '紫河鱼的切片款2斤', ['紫河鱼'], 'no_auto', 2),
    ('two_meanings', '同词形态歧义', '豆腐要干形态2斤', ['干豆腐', '豆腐干'], 'ambiguous_two', 2),
    ('two_comma_items', '两个商品', '彩椒2斤，南瓜3斤', ['彩椒', '南瓜'], 'two_items', None),
    ('bare_comma_qualifier', '逗号未明备注', '彩椒2斤，别要软的', ['彩椒'], 'no_auto', 2),
    ('multi_number', '数量不能吸走', '紫河鱼2斤要求每袋500g', ['紫河鱼'], 'no_auto_or_quantity_two', 2),
]


def run():
    files = [SKILL / 'scripts/order_mapping_core' / (n + '.py') for n in ('expressions', 'parsing', 'matching', 'knowledge')]
    files.append(SKILL / 'knowledge/procurement.json')
    hashes = lambda: {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    before = hashes()
    kb = json.loads(files[-1].read_text())
    results = []
    for case_id, dimension, text, names, expectation, quantity in CASES:
        records = [{'code': 'fixture-' + str(i), 'name': n, 'category': '蔬菜',
                    'spec': '500g', 'price': str(i + 3)} for i, n in enumerate(names)]
        parsed = parse_chat(text, source_name='independent.txt')
        observed = []
        for item in parsed:
            result = match_shared(item, records, kb)
            observed.append({'raw_text': item['raw_text'], 'span': [item['char_start'], item['char_end']],
                'name': item['name'], 'notes': item['notes'], 'quantity': item['quantity'], 'unit': item['unit'],
                'status': result.get('status'), 'selected': (result.get('selected') or {}).get('code'),
                'candidates': [r['code'] for r in result.get('candidates', [])],
                'issues': [x['code'] for x in result.get('issues', [])]})
        spans_ok = all(text[x['span'][0]:x['span'][1]] == x['raw_text'] for x in observed)
        first = observed[0] if observed else {}
        quantity_ok = quantity is None or first.get('quantity') == str(quantity)
        if expectation == 'one_exact':
            passed = len(observed) == 1 and first.get('status') == 'matched' and first.get('selected') == 'fixture-0' and first.get('raw_text') == text and quantity_ok
        elif expectation == 'no_auto':
            passed = bool(observed) and all(x['status'] != 'matched' for x in observed) and quantity_ok
        elif expectation == 'ambiguous_two':
            passed = len(observed) == 1 and first.get('status') != 'matched' and set(first.get('candidates', [])) == {'fixture-0', 'fixture-1'} and quantity_ok
        elif expectation == 'two_items':
            passed = len(observed) == 2 and [x['quantity'] for x in observed] == ['2', '3'] and [x['selected'] for x in observed] == ['fixture-0', 'fixture-1']
        else:
            passed = quantity_ok and all(x['status'] != 'matched' for x in observed)
        results.append({'id': case_id, 'dimension': dimension, 'text': text, 'catalog': records,
                        'contract': expectation, 'expected_quantity': quantity,
                        'span_round_trip': spans_ok, 'passed': bool(passed and spans_ok), 'observed': observed})
    after = hashes()
    data = {'scope': 'independent synthetic parse_chat + match_shared; no claim of candidate semantic completeness',
            'source_sha256_before': before, 'source_sha256_after': after, 'stable': before == after,
            'summary': {'total': len(results), 'passed': sum(r['passed'] for r in results),
                        'failed': sum(not r['passed'] for r in results)}, 'cases': results}
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return int(not data['stable'] or data['summary']['failed'] > 0)


if __name__ == '__main__':
    raise SystemExit(run())
