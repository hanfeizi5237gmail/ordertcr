"""Build and smoke-test the portable skill separately from private profiles."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def archive(source, target, prefix):
    entries = []
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(source.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc' or path.name == '.DS_Store':
                continue
            name = str(Path(prefix) / path.relative_to(source))
            info = zipfile.ZipInfo(name, (2026, 11, 7, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, path.read_bytes())
            entries.append(name)
    return {'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'entries': entries}


def main():
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    skill = archive(ROOT / 'skills/order-mapping', dist / 'order-mapping.zip', 'order-mapping')
    assert not any(part in name.split('/') for name in skill['entries'] for part in ['original', 'runtime', 'outputs', 'evaluation'])
    private = dist / 'reference-profiles'
    private.mkdir(exist_ok=True)
    for station in ['songbei', 'xingyue', 'xueyuan']:
        shutil.copyfile(ROOT / f'evaluation/v3/{station}.history-profile.json', private / f'{station}.history-profile.json')
        shutil.copyfile(ROOT / f'evaluation/v3/{station}.decisions.json', private / f'{station}.sample-decisions.json')
    instructions = '''# 本业务私有参考偏好包

这些profile是v3三站采购偏好，由用户提供的11.3人工订单提取，仅供用户在自己的业务环境使用，包含站点采购信息，不属于通用skill。

- 同公司ID分别为songbei（松北）、xingyue（星月）、xueyuan（学院路）。配置需使用相同ID，真实公司全称/简称和日期另由使用者确认。
- 当前只绑定目录SHA `6a0223e4f3db42fe56d6cabb0bd94fdea0e8b123c290eafc395571d48c13f687`。有新目录先切换再重核偏好，禁止只改SHA沿用。
- 不含目录原件、人工原件、实配数量覆盖或旧价覆盖。需要用户自己持有当前目录及相应人工参考文件，用真实文件SHA注册。
- v3完整132项可核人工SKU为127/132（96.21%），学院53/53，均为同一参考样本拟合回归。单独安装通用skill不包含这些公司采购知识；无profile、新聊天和未见公司没有95%保证。
- `*.sample-decisions.json` 只供完全相同聊天文件的回放，包含来源行ID和该次目录选择依据。新聊天必须由宿主Agent重新判断，不能复用行ID答案；这些决策不是通用别名。星月四条和学院八条缺包装证据的单位规则已停用，不能用数量相等推包装关系。

解压通用skill后，在其目录执行（替换文件位置；Python需3.10+及openpyxl）：

```bash
python scripts/order_mapping.py catalog-register --runtime ./runtime --file "用户文件/食堂食材基准价价格表10.12-10.18.xlsx"
python scripts/order_mapping.py profile-register --runtime ./runtime --file "私有偏好/xueyuan.history-profile.json" --source "用户文件/学院路站11.3.xlsx"
```

松北和星月用对应profile及人工表重复第二条。之后按技能references/data-contract.md初始化公司、自然语言日期和批次，主动核对聊天语义，再run导出。目录价与历史价冲突时用当前目录；按条/颗购买但缺实称时商品可确定，计价数量与金额仍待核。
'''
    (private / '使用说明.md').write_text(instructions, encoding='utf-8')
    private_zip = archive(private, dist / 'order-mapping-reference-profiles.zip', 'reference-profiles')
    temporary_root = ROOT / 'evaluation/tmp'
    temporary_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temporary_root) as temporary:
        checkout = Path(temporary)
        with zipfile.ZipFile(skill['path']) as source:
            assert source.testzip() is None
            source.extractall(checkout)
        package = checkout / 'order-mapping'
        command = [sys.executable, str(package / 'scripts/order_mapping.py'), '--help']
        help_result = subprocess.run(command, cwd=checkout, text=True, capture_output=True, check=True)
        assert 'profile-register' in help_result.stdout
        tests = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(package / 'tests'), '-v'],
                               cwd=checkout, text=True, capture_output=True)
        (ROOT / 'evaluation/package_tests.log').write_text(tests.stdout + tests.stderr)
        assert tests.returncode == 0, tests.stderr
        cli = [sys.executable, str(package / 'scripts/order_mapping.py')]
        runtime = checkout / 'runtime'
        def call(*arguments):
            proc = subprocess.run(cli + list(map(str, arguments)), cwd=checkout, text=True, capture_output=True)
            assert proc.returncode == 0, proc.stderr
            return json.loads(proc.stdout)['result']
        call('catalog-register', '--runtime', runtime, '--file', ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx')
        replay = {}
        latest = json.loads((ROOT / 'evaluation/v3_accuracy.json').read_text())
        for station in ['songbei', 'xingyue', 'xueyuan']:
            profile = json.loads((private / f'{station}.history-profile.json').read_text())
            call('profile-register', '--runtime', runtime, '--file', private / f'{station}.history-profile.json',
                 '--source', ROOT / 'original' / profile['evidence']['source'])
            batch = json.loads((ROOT / f'evaluation/inputs/{station}.json').read_text())
            batch['batch_id'] = f'{station}-v3-package-smoke'
            batch['decisions'] = json.loads((private / f'{station}.sample-decisions.json').read_text())['decisions']
            batch['chat_files'] = [str(ROOT / path) for path in batch['chat_files']]
            batch_path = checkout / f'{station}.json'
            batch_path.write_text(json.dumps(batch, ensure_ascii=False))
            outcome = call('run', '--runtime', runtime, '--batch', batch_path, '--output', checkout / 'outputs')
            rows = json.loads(Path(outcome['manifest_path']).read_text())['order']['rows']
            source_output = next(x for x in latest['outputs'] if x['batch_id'].startswith(station + '-'))
            expected = {row['line_id']: row for row in json.loads(Path(source_output['manifest_path']).read_text())['order']['rows']}
            for row in rows:
                target = expected[row['line_id']].get('selected')
                assert (row.get('selected') or {}).get('code') == (target or {}).get('code')
                for field in ['raw_text', 'quantity', 'unit', 'status', 'product_status', 'quantity_status',
                              'invoice_quantity', 'unit_price', 'amount', 'finance_amount']:
                    assert row.get(field) == expected[row['line_id']].get(field), (station, row['line_id'], field)
            replay[station] = {'row_count': len(rows), 'sku_choices_equal_v3': True,
                               'product_matched_count': outcome['product_matched_count'],
                               'quantity_matched_count': outcome['quantity_matched_count']}
    report = {'skill': skill, 'private_profiles': private_zip, 'unpack_cli_passed': True,
              'unpack_tests_passed': True, 'three_station_package_replay': replay,
              'package_test_output': tests.stdout + tests.stderr}
    (dist / 'delivery_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: {k: v for k, v in value.items() if k != 'entries'} for key, value in [('skill', skill), ('private_profiles', private_zip)]}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
