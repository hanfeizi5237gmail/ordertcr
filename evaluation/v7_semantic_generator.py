#!/usr/bin/env python3
"""Freeze independent, directory-grounded semantic challenge; never invokes product."""
import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'evaluation/v7/semantic_challenge_v1'
CAT = ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx'
KB = ROOT / 'skills/order-mapping/knowledge/procurement.json'
POOLS = [ROOT / p for p in ('evaluation/v6/challenge', 'evaluation/v6/challenge_extension',
    'evaluation/v6/kb7_challenge', 'evaluation/v7/fresh_challenge', 'evaluation/v7/fresh_challenge_v2')]
# Each expression retains distinguishing attributes; it is a reviewer hypothesis, not a catalog alias assertion.
ITEMS = [
 ('玉竹',560,'80002840',['调料玉竹','玉竹调料','调料用玉竹'], '目录标明调料；不得替换成其他药材或改变目录规格'),
 ('桂皮',596,'80002956',['调料桂皮','桂皮调料','调料用桂皮'], '目录标明调料；不能替成肉桂粉等其他形态'),
 ('桑葚',622,'80003020',['桑葚干','干桑葚','干的桑葚'], '干制桑葚；不能换成鲜果或酸奶'),
 ('贡菜',571,'80002901',['干贡菜','贡菜干','干的贡菜'], '干制贡菜；不能换成鲜品或其他菜'),
 ('烤麸',1424,'80005795',['大片干烤麸','烤麸干大片','干烤麸大片'], '干制且大片；不得换为鲜制、其他形态'),
 ('鸭胸',1076,'80004326',['冷冻带皮鸭胸肉','带皮冷冻鸭胸肉','鸭胸肉带皮冷冻'], '鸭胸、带皮、冷冻均是硬约束；不得换去皮或其他部位'),
 ('鲐鲅',1215,'80003217',['大号冷冻鲐鲅鱼','冷冻大号鲐鲅鱼','鲐鲅鱼冷冻大号'], '冷冻大号；不得换中号或其他鱼'),
 ('海带扣',1246,'80005756',['带盐鲜海带扣','鲜海带扣带盐','海带扣鲜的带盐'], '鲜、带盐、扣均需保留；不得替换海带根、丝或干品'),
]
COMPANIES = ['星月站','学院路站','松北大道站','新港站']
SEEDS = (8701,8702,8791)

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,obj):
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
def files():
 return sorted(p for root in POOLS for p in root.rglob('*') if p.is_file()
  and (p.suffix=='.txt' or p.name=='expected.json')
  and not any(part in {'runs','outputs','runtime','logs'} for part in p.parts))
def main():
 if OUT.exists() and any(OUT.iterdir()): raise SystemExit('Target exists; refusing to overwrite frozen challenge')
 kb=json.loads(KB.read_text()); assert len(kb['rules']) == 85
 names=[]
 for rule in kb['rules']:
  names.extend(rule.get('aliases',[])); names.extend(rule.get('selector',{}).get('names',[]))
  names.extend(rule.get('selection_selector',{}).get('names',[]))
 pool=files(); assert len(pool)>0
 texts=[p.read_text(encoding='utf-8') for p in pool]
 sheet=openpyxl.load_workbook(CAT,read_only=True,data_only=True).active
 records=[]
 for term,row,sku,expressions,boundary in ITEMS:
  assert not any(term in str(n) for n in names), (term,'knowledge intersection')
  assert not any(term in text for text in texts), (term,'sample intersection')
  category,code,name,price,spec=[sheet.cell(row,i).value for i in range(1,6)]
  assert str(code)==sku and spec=='500g' and len(set(expressions))==3
  assert all(name not in expression for expression in expressions)
  records.append(dict(concept=term,catalog={'source':'original/食堂食材基准价价格表10.12-10.18.xlsx',
   'sheet':'Sheet0','row':row,'sku':sku,'category':category,'name':name,'spec':spec,'price_yuan_per_500g':str(price)},
   expressions=expressions, positive_negative_constraints=boundary,
   evidence_type='目录名称直接约束与人工语言判断', reviewer_boundary='表达与目录品名的语义对应需人工复核；不推断未写明的包装或品牌'))
 source_hashes={str(p.relative_to(ROOT)):sha(p) for p in [CAT,KB,*pool]}
 for seed_index,seed in enumerate(SEEDS):
  rng=random.Random(seed); events=[]
  for i,rec in enumerate(records):
   for j in range(3):
    qty=j+1; phrase=rec['expressions'][(j+seed_index)%3]
    # Quantity always in 斤; 500g catalog unit is exactly one 斤.
    render=[f'{phrase}{qty}斤', f'{qty}斤{phrase}', f'{phrase}  {qty} 斤'][(i+j+seed_index)%3]
    price=Decimal(rec['catalog']['price_yuan_per_500g']); amount=price*qty
    events.append(dict(event_id=f'C{i+1:02d}-{j+1}',concept=rec['concept'],raw=render,
       outcome='conditional_auto', quantity=str(qty),unit='斤',quantity_in_500g_units=str(qty),
       selected=rec['catalog'], nominal_amount_yuan=format(amount,'.2f'),
       conditional_discount='0.95',conditional_discounted_amount_yuan=format(amount*Decimal('.95'),'.4f'),
       evidence=rec['positive_negative_constraints'],reviewer_boundary=rec['reviewer_boundary']))
  controls=[
   ('X01','调料桂皮1箱','包装换算未知，不得将箱数当斤数或填金额'),
   ('X02','带盐鲜海带扣','缺数量，不得填数量或金额'),
   ('X03','月球香草2斤','虚构商品名；无目录身份凭据，不得猜商品及金额'),
   ('X04','干桑葚2斤','公司未登记，不得继承上家公司或生成正式归属')]
  for eid,raw,why in controls: events.append(dict(event_id=eid,concept='控制',raw=raw,outcome='review',reason=why,nominal_amount_yuan=None,conditional_discounted_amount_yuan=None))
  rng.shuffle(events); lines=['各单位分开记账，日期与公司以文本为准。']
  for n,event in enumerate(events):
   company='未登记单位' if event['event_id']=='X04' else COMPANIES[(n+seed)%len(COMPANIES)]
   date=f'2027-01-{1+seed_index*8+n%8:02d}'
   lines.extend((f'公司：{company}',f'配送日期：{date}',event['raw']))
   event.update(company=company,date=date,source_file='input/聊天.txt',source_line=len(lines),source_line_text=event.pop('raw'))
  dest=OUT/str(seed); inp=dest/'input'; inp.mkdir(parents=True)
  (inp/'聊天.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
  request={'batch_date':'2027-01-01','catalog_file':'../../../../../original/食堂食材基准价价格表10.12-10.18.xlsx',
    'chat_files':['聊天.txt'],'companies':[{'company_alias':x.removesuffix('站'),'company_name':x} for x in COMPANIES],
    'company_policy':'按聊天公司标记切换；未登记公司保留异常','date_policy':'聊天日期标题覆盖批次日期'}
  write(inp/'request.json',request)
  write(dest/'expected.json',{'schema':'semantic_challenge_expected_v1','seed':seed,'events':events,
    'evaluation_note':'24 正常事件对应 8 个概念各 3 条；4 控制事件独立计入分母。相同概念表达变体不能计为新商品概念。金额为明确假设下的需求预估，非实配结算。'})
  manifest={'schema':'semantic_challenge_manifest_v1','seed':seed,'split':'holdout' if seed==8791 else 'development',
    'counts':{'events':28,'normal':24,'controls':4,'distinct_concepts':8},'concepts':records,
    'exclusion':'精确可见字符串核对：8 个核心名称均未出现在当前 85 规则 aliases、selector.names、selection_selector.names 或列明的五组 v6/v7 样本输入 txt 及 expected.json；各候选完整目录名包含核心名称，因此也未出现。未搜索其他历史、隐藏别名或语义同义词，不能宣称全历史独立。',
    'sample_pool_files': [str(p.relative_to(ROOT)) for p in pool], 'source_sha256':source_hashes,
    'independence':'各 seed 共享 28 个逻辑事件和 8 概念，改变顺序、公司、日期及表达；8791 为保留组合，不是全新概念盲测。',
    'input_contract':'执行者仅读 input 和 skill；不得向执行端提供预期、事件编号、来源行或评测文件。',
    'amount_basis':'目录标价每 500g，原文单位斤；名义金额=标价×斤数；0.95 为独立条件示例，不视为已确认折扣或实配。',
    'input_sha256':{str(p.relative_to(dest)):sha(p) for p in sorted(inp.iterdir())},'expected_sha256':sha(dest/'expected.json')}
  write(dest/'manifest.json',manifest)
  write(dest/'FREEZE.json',{'schema':'semantic_challenge_freeze_v1','seed':seed,'files_sha256':{
    str(p.relative_to(dest)):sha(p) for p in [inp/'request.json',inp/'聊天.txt',dest/'expected.json',dest/'manifest.json']},
    'generator_sha256':sha(Path(__file__).resolve()),'source_sha256':source_hashes})
 print('Frozen', ', '.join(map(str,SEEDS)))
if __name__=='__main__': main()
