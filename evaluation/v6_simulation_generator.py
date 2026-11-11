#!/usr/bin/env python3
"""Materialize frozen evaluation text; labels stay outside runtime requests."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def frozen_oracle(path=None):
    path=Path(path or ROOT/'evaluation/v6/oracle/oracle_v2.json')
    raw=path.read_bytes()
    expected=path.with_suffix('.sha256').read_text().split()[0]
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('冻结评测指纹不符')
    return json.loads(raw),expected


def generate(path=None):
    oracle,digest=frozen_oracle(path)
    base=ROOT/'evaluation/v6/simulation';base.mkdir(parents=True,exist_ok=True)
    cases=[]
    for case in oracle['cases']:
        target=base/str(case['seed'])/(case['case_id']+'.txt')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(case['raw_text'],encoding='utf-8')
        for event in case['expected_events']:
            start,end=event['source_span']
            if case['raw_text'][start:end]!=event['raw_text']:
                raise ValueError(f'模拟来源span不符: {case["case_id"]}')
        cases.append({'case_id':case['case_id'],'seed':case['seed'],'scenario':case['scenario'],
            'split':case['split'],'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'expected_event_count':len(case['expected_events'])})
    manifest={'schema_version':1,'oracle_sha256':digest,'case_count':len(cases),'cases':cases,
        'boundary':'公司与日期须由文本解析；manifest标签仅供评测，绝不传入运行端'}
    (base/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (base/'README.md').write_text('# v6 原始模拟消息\n\n固定开发seed6108、6109；原保留seed6110（已消费，不再是独立保留验收）。每seed三类历史主场景各完整159事件；这些是历史变体，不能当成新增真实业务样本。独立目录商品的合成表达、异常及目录更新另外报告。manifest及oracle仅供评测，CLI只读取txt和正常业务配置。\n')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--oracle');args=parser.parse_args()
    print(json.dumps({k:v for k,v in generate(args.oracle).items() if k!='cases'},ensure_ascii=False))
