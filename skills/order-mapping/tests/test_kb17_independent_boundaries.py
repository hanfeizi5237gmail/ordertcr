"""Independent adversarial probe. Exits 1 on unsafe results; output is JSON evidence."""
import hashlib
import json
from pathlib import Path
import sys

SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parent
sys.path.insert(0, str(SKILL / 'scripts'))
from order_mapping_core.parsing import parse_chat
from order_mapping_core.knowledge import match_shared

CASES = [
    ('brand_ambiguous', '品牌', '葡萄要蓝天品牌2斤', ['葡萄蓝天品牌', '蓝天品牌葡萄'], 'no_auto'),
    ('negation_attached', '否定', '牛肉2斤不要冷冻', ['牛肉', '冷冻牛肉'], 'no_auto'),
    ('negation_comma', '否定', '牛肉2斤，别要肥的', ['牛肉'], 'no_auto'),
    ('part_attached', '部位', '猪肉2斤要后腿', ['猪肉', '后腿猪肉'], 'no_auto'),
    ('form_collision', '形态/拼接歧义', '豆腐要干形态2斤', ['干豆腐', '豆腐干'], 'retain_two_no_auto'),
    ('form_exact_short_circuit', '形态/拼接歧义', '豆腐要干形态2斤', ['豆腐要干形态', '干豆腐', '豆腐干'], 'retain_three_no_auto'),
    ('size_followed_note', '规格/备注', '大米2斤要求：有机500g', ['大米'], 'no_auto'),
    ('prep_comma', '加工', '牛肉2斤，切片', ['牛肉'], 'no_auto'),
    ('prep_attached', '加工', '鸡肉2斤去骨', ['鸡肉'], 'no_auto'),
    ('prep_comma_bone', '加工', '鸡肉2斤，去骨', ['鸡肉'], 'no_auto'),
    ('notes_attached', '备注', '牛肉2斤备注：切丝', ['牛肉'], 'no_auto'),
    ('fresh_size', '规格/形态', '牛肉2斤要鲜的500g', ['牛肉'], 'no_auto'),
]


def run():
    paths = [SKILL / 'scripts/order_mapping_core' / (n + '.py') for n in ('expressions', 'parsing', 'matching', 'knowledge')]
    paths += [SKILL / 'knowledge/procurement.json']
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    kb = json.loads(paths[-1].read_text())
    results = []
    for case_id, category, text, names, expected in CASES:
        records = [{'code': 'fixture-' + str(i), 'name': name, 'category': '蔬菜',
                    'spec': '500g', 'price': str(3 + i)} for i, name in enumerate(names)]
        parsed = parse_chat(text, source_name='synthetic.txt')
        rows = []
        for item in parsed:
            matched = match_shared(item, records, kb)
            rows.append({'raw_text': item['raw_text'], 'span': [item['char_start'], item['char_end']],
                         'name': item['name'], 'notes': item['notes'], 'quantity': item['quantity'],
                         'unit': item['unit'], 'status': matched.get('status'),
                         'selected': (matched.get('selected') or {}).get('code'),
                         'candidates': [r['code'] for r in matched.get('candidates', [])],
                         'issues': [x['code'] for x in matched.get('issues', [])]})
        first = rows[0]
        no_auto = first['status'] != 'matched'
        need = 3 if expected == 'retain_three_no_auto' else 2
        passed = no_auto and (not expected.startswith('retain_') or len(first['candidates']) == need)
        results.append({'case_id': case_id, 'category': category, 'input': text,
                        'catalog': records, 'expected': expected, 'observed': rows,
                        'passed': passed, 'boundary': 'single-line fragment association is ambiguous; do not silently confirm first row' if len(rows) > 1 else 'preserve all explicitly plausible interpretations and constraints'})
    after = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    result = {'schema': 'kb17_independent_boundaries_v1', 'scope': 'synthetic direct parse_chat + match_shared only; no execution workflow, no 9091 data',
              'source_sha256_before': hashes, 'source_sha256_after': after,
              'source_stable_during_probe': hashes == after, 'cases': results,
              'summary': {'total': len(results), 'passed': sum(x['passed'] for x in results),
                          'failed': sum(not x['passed'] for x in results)}}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['summary']['failed'] or hashes != after else 0

if __name__ == '__main__':
    raise SystemExit(run())
