"""Fresh XLSX readback, manifest reconciliation, links, and local thumbnails."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import subprocess
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]


def main():
    result = json.loads((ROOT / 'evaluation/v2_accuracy.json').read_text())
    checks = 0
    previews = []
    for output in result['outputs']:
        manifest = json.loads(Path(output['manifest_path']).read_text())
        for artifact in manifest['files'].values():
            path = Path(artifact['path'])
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256']
        standard = load_workbook(output['files']['standard']['path']).active
        finance = load_workbook(output['files']['finance']['path'])['商品明细']
        total = Decimal(0)
        for index, row in enumerate(manifest['order']['rows'], 2):
            assert row['raw_text'] in standard.cell(index, 4).comment.text
            if row['status'] != 'matched':
                assert standard.cell(index, 10).value is None
                assert finance.cell(index, 8).value is None
                assert row['amount'] is None and row['finance_amount'] is None
            else:
                total += Decimal(str(standard.cell(index, 10).value))
            if row.get('product_status') == 'matched' and row.get('quantity_status') != 'matched':
                assert '原始数量' in standard.cell(index, 6).comment.text
                assert standard.cell(index, 4).value == row['selected']['name']
            checks += 1
        assert total == Decimal(manifest['totals']['standard'])
        assert Decimal(str(standard.cell(len(manifest['order']['rows']) + 2, 10).value)) == total
        folder = ROOT / 'evaluation/rendered-v2' / output['batch_id']
        folder.mkdir(parents=True, exist_ok=True)
        render = subprocess.run(['/usr/bin/qlmanage', '-t', '-s', '2200', '-o', str(folder), output['files']['standard']['path']], capture_output=True, text=True)
        previews.append({'batch_id': output['batch_id'], 'returncode': render.returncode,
                         'images': [str(path) for path in folder.glob('*.png')]})
    broken = []
    for path in (ROOT / 'skills/order-mapping').rglob('*.md'):
        for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
            if '://' in target or target.startswith('#'):
                continue
            if not (path.parent / target.split('#')[0]).exists():
                broken.append([str(path), target])
    assert not broken, broken
    report = {'passed': True, 'xlsx_rows_verified': checks, 'money_totals_match_manifest': True,
              'native_comments_verified': True, 'skill_links_broken': broken, 'previews': previews,
              'limitations': ['缩略图不能验证原生批注浮窗；批注由openpyxl逐项复读', '未执行真实宿主或订单/财务系统导入']}
    (ROOT / 'evaluation/v2_delivery_verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
