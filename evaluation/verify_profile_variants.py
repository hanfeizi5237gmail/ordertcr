"""Metamorphic checks: historical preferences must generalize beyond line IDs/counts."""
from pathlib import Path
import argparse
import json
import random
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/order-mapping/scripts'))
from order_mapping_core.catalog import CatalogStore
from order_mapping_core.parsing import parse_chat
from order_mapping_core.matching import match_item


def verify(profile, catalog, station):
    randomizer=random.Random(20261107)
    checks=[]
    entries=list(profile['aliases'].items())
    for name,alias in entries:
        unit=(alias.get('request_units',['斤']) or [''])[0]
        for quantity in [1,7,19]:
            raw=name+str(quantity)+unit
            item=parse_chat(raw,'never_seen_chat.txt')[0]
            row=match_item(item,catalog['records'],aliases=profile['aliases'],unit_rules=profile['unit_rules'])
            assert row['product_status']=='matched' and row['selected']['code']==alias['code'],(raw,row)
            assert row['quantity']==str(quantity) and row['raw_text']==raw
            checks.append({'raw':raw,'selected_code':row['selected']['code'],'passed':True})
    separations=[]
    for trial in range(100):
        chosen=randomizer.sample(entries,5)
        texts=[name+str(randomizer.randint(1,20))+(alias.get('request_units',['斤']) or [''])[0] for name,alias in chosen]
        separator=randomizer.choice([',','，','、',';','；','\n',' '])
        if separator==' ' and any(not alias.get('request_units',['斤']) for _,alias in chosen):
            separator='，'  # Missing-unit phrases require a real boundary.
        rows=parse_chat(separator.join(texts),'new_chat.txt')
        assert len(rows)==len(texts),(separator,texts,rows)
        for item,(_,alias) in zip(rows,chosen):
            row=match_item(item,catalog['records'],aliases=profile['aliases'],unit_rules=profile['unit_rules'])
            assert row['product_status']=='matched' and row['selected']['code']==alias['code']
        separations.append({'trial':trial,'separator':repr(separator),'item_count':5,'passed':True})
    negative=[]
    for name,alias in entries:
        record=next(x for x in catalog['records'] if x['code']==alias['code'])
        for foreign in ['秋林'+name,'其他品牌'+name]:
            row=match_item({'name':foreign,'quantity':'2','unit':(alias.get('request_units',['斤']) or [None])[0],'issues':[]},catalog['records'],aliases=profile['aliases'],unit_rules=profile['unit_rules'])
            assert row.get('match_basis')!='historical_preference'
            negative.append(foreign)
    result={'passed':True,'scope':'metamorphic_profile_generalization_not_unseen_customer_accuracy',
            'quantity_variants':len(checks),'five_item_separator_trials':len(separations),
            'negative_explicit_brand_checks':len(negative),'seed':20261107,'quantity_checks':checks,
            'separator_trials':separations,'limitations':['采购偏好源自同一人工样本，变体不是新的独立人工标注订单']}
    result['station']=station
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--version',choices=['v2','v3'],default='v2')
    args=parser.parse_args()
    version=args.version
    catalog=CatalogStore(ROOT/('evaluation/v3/runtime' if version=='v3' else 'evaluation/v2_runtime')).current()
    stations=['songbei','xingyue','xueyuan'] if version=='v3' else ['xueyuan']
    results=[]
    for station in stations:
        path=ROOT/f'evaluation/{"v3/" if version=="v3" else ""}{station}.history-profile.json'
        results.append(verify(json.loads(path.read_text()),catalog,station))
    result={'passed':True,'scope':'metamorphic_profile_generalization_not_unseen_customer_accuracy',
            'stations':results}
    for key in ['quantity_variants','five_item_separator_trials','negative_explicit_brand_checks']:
        result[key]=sum(x[key] for x in results)
    prefix='v3_profile_variants' if version=='v3' else 'profile_variants'
    p=ROOT/f'evaluation/{prefix}.json';p.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (ROOT/f'evaluation/{prefix}.md').write_text(f'# 采购偏好变体验证\n\n{result["quantity_variants"]}个改量品名用例、{result["five_item_separator_trials"]}轮每轮5项随机分隔重排、{result["negative_explicit_brand_checks"]}个显式品牌否定用例通过。所有选择按品名/原单位及目录版本，不依赖原样例行号、原数量、原文文件名。\n\n这是变形回归，未计作新客户独立准确率。\n')
    print(json.dumps({k:v for k,v in result.items() if k!='stations'},ensure_ascii=False))
if __name__=='__main__':main()
