"""Measure the AI-only identity pass separately from reference-fitted profiles."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/order-mapping/scripts'))
from order_mapping_core.catalog import CatalogStore
from order_mapping_core.workflow import process_batch


def main():
    evaluation = ROOT / 'evaluation'
    proposals = json.loads((evaluation / 'xueyuan.semantic-v2-decisions.json').read_text())
    reference = json.loads((evaluation / 'v2_accuracy.json').read_text())
    batch = json.loads((evaluation / 'inputs/xueyuan.json').read_text())
    batch['batch_id'] = 'xueyuan-20261103-semantic-v2'
    batch['decisions'] = proposals['decisions']
    batch['config']['aliases'] = {}
    batch['config']['unit_rules'] = {}
    runtime = evaluation / 'semantic_v2_runtime'
    CatalogStore(runtime).register(ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx')
    assert not (runtime / 'profiles').exists(), 'AI-only evaluation must not load historical preferences'
    manifest = process_batch(runtime, batch, ROOT / 'outputs/semantic-v2', base_dir=ROOT)
    rows = {row['source_line']: row for row in manifest['order']['rows']}
    result = []
    for oracle in reference['rows']:
        row = rows[oracle['chat_line']]
        expected = oracle.get('expected_code')
        selected = row.get('selected')
        result.append({
            'chat_line': oracle['chat_line'], 'raw_text': row['raw_text'],
            'expected_code': expected, 'manual': oracle.get('manual'),
            'selected': selected, 'product_status': row.get('product_status'),
            'identity_correct': bool(expected and selected and selected['code'] == expected),
            'decision': row.get('decision'), 'issues': row['issues'],
        })
    eligible = [row for row in result if row['expected_code']]
    correct = sum(row['identity_correct'] for row in eligible)
    report = {
        'assessment': proposals['assessment'],
        'note': '无历史profile的AI语义复核；评估人员此前读过人工表，非真正盲测。SKU选择后与人工表逐项比较；数量实称不覆盖。',
        'profile_used': False, 'all_chat_items': len(result),
        'ai_decision_count': len(proposals['decisions']),
        'catalog_covered_identity_denominator': len(eligible),
        'identity_correct': correct,
        'identity_percent': round(correct * 100 / len(eligible), 2),
        'all_aligned_identity_percent': round(correct * 100 / reference['metrics']['aligned_pairs'], 2),
        'rows': result, 'manifest_path': manifest['manifest_path'],
    }
    (evaluation / 'semantic_v2_accuracy.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    lines = ['# 无历史偏好的AI语义复核', '', report['note'], '',
             f'可核目录商品身份：{correct}/{len(eligible)}（{report["identity_percent"]}%）；完整62对齐项：{report["all_aligned_identity_percent"]}%。', '',
             '该结果与参考样本拟合回归分开报告，不能证明新真实订单达到95%。', '',
             '|聊天行|原文|人工参考|AI/脚本最终选择|身份一致|', '|---:|---|---|---|---|']
    for row in result:
        lines.append(f'|{row["chat_line"]}|{row["raw_text"]}|{(row["manual"] or {}).get("name", "无人工对应")}|{(row["selected"] or {}).get("name", "未决")}|{row["identity_correct"] if row["expected_code"] else "目录外/无人工对应"}|')
    (evaluation / 'semantic_v2_accuracy.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'rows'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
