#!/usr/bin/env python3
"""Generate isolated semantic and company-routing challenges; never read outputs."""
from __future__ import annotations
import hashlib, io, json, random, re, sys, zipfile
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'evaluation/v6/challenge_extension'
CATALOG = ROOT / 'original/食堂食材基准价价格表10.12-10.18.xlsx'
SEEDS = {7301:'dev', 7302:'dev', 7391:'holdout'}
COMPANIES = [
 {'company_name':'星月站', 'company_alias':'星月'},
 {'company_name':'学院路站', 'company_alias':'学院路'},
 {'company_name':'松北大道站', 'company_alias':'松北大道'},
 {'company_name':'新港站', 'company_alias':'新港'},
]
# Each mapping is judged against the present catalog only, not a historical answer.
PRODUCTS = [
 ('西蓝花','西兰花','typo'), ('黑芝麻粒','黑芝麻','semantic_form'),
 ('去皮青笋','去皮莴笋（青笋）','synonym'), ('铁棍山药','铁杆山药/铁棍山药','synonym'),
 ('干金针菜','干黄花菜','synonym'), ('薏仁米','薏米','synonym'),
 ('生腰果仁','生腰果','synonym'), ('一级库尔勒香梨','库尔勒香梨一级','word_order'),
 ('黄元帅','黄元帅苹果','short_name'), ('紫菜','紫菜（斤）','short_name'),
 ('绿豆','绿豆','catalog_name'), ('黑豆','黑豆','catalog_name'),
 ('茼蒿（辽宁）','茼蒿（辽宁）','catalog_name'), ('荷兰豆','荷兰豆','catalog_name'),
 ('木瓜','木瓜','catalog_name'), ('空心菜','空心菜','catalog_name'),
 ('马蹄','马蹄','catalog_name'), ('丝瓜','丝瓜','catalog_name'),
 ('姜','姜','catalog_name'), ('南瓜','南瓜','catalog_name'), ('西柚','西柚','catalog_name'),
 ('平菇（鲜蘑）','平菇（鲜蘑）','catalog_name'), ('鲜活鲫鱼','鲜活鲫鱼','catalog_name'),
 ('水晶梨','水晶梨','catalog_name'), ('茶树菇','茶树菇','catalog_name'),
 ('荔浦芋头（优质）','荔浦芋头（优质）','catalog_name'), ('豌豆苗','豌豆苗','catalog_name'),
 ('金针菇 称重','金针菇 称重','catalog_name'), ('生猪肥肠','生猪肥肠','catalog_name'),
 ('北极冰虾80*100','冷冻北极冰虾80*100','short_name'),
 ('干桂园肉','干桂圆肉','typo'), ('马铃薯（大885）','土豆（大885）','synonym'),
 ('平菇','平菇（鲜蘑）','short_name'),
]

def sha(b): return hashlib.sha256(b).hexdigest()
def jdump(o): return (json.dumps(o,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
def write(p,b): p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b)

def catalog_bytes(w):
 w.properties.created = w.properties.modified = datetime(2020,1,1)
 buf=io.BytesIO(); w.save(buf); out=io.BytesIO()
 with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as src, zipfile.ZipFile(out,'w') as dst:
  for n in sorted(src.namelist()):
   z=zipfile.ZipInfo(n,(2020,1,1,0,0,0)); z.compress_type=zipfile.ZIP_DEFLATED; z.external_attr=0o600<<16
   b=src.read(n)
   if n=='docProps/core.xml':
    b=re.sub(rb'(<dcterms:modified[^>]*>)[^<]+(</dcterms:modified>)',lambda m:m.group(1)+b'2020-01-01T00:00:00Z'+m.group(2),b)
   dst.writestr(z,b)
 return out.getvalue()

def main():
 seeds=[int(s) for s in sys.argv[1:]] or list(SEEDS)
 if len(set(seeds))!=len(seeds) or any(s not in SEEDS for s in seeds): raise SystemExit('usage: extension_builder.py [7301] [7302] [7391]')
 if any((DEST/str(s)).exists() for s in seeds): raise SystemExit('seed exists; refusing overwrite')
 src=CATALOG.read_bytes(); wb=load_workbook(io.BytesIO(src),data_only=True); sh=wb.active
 names={target for _,target,_ in PRODUCTS}; records={}
 potato_ids={'80003671','80003741','80004321'}; potato_records={}
 for rownum,row in enumerate(sh.iter_rows(min_row=5,values_only=True),5):
  cat,sku,name,price,spec=row[:5]
  if str(sku).strip() in potato_ids:
   potato_records[str(sku).strip()]={'sku':str(sku).strip(),'name':str(name).strip(),'price':str(price),'spec':str(spec).strip(),'sheet':sh.title,'row':rownum}
  if name and str(name).strip() in names:
   name=str(name).strip()
   if name in records: raise ValueError('ambiguous exact catalog name '+name)
   records[name]={'sku':str(sku).strip(),'name':name,'price':str(price),'spec':str(spec).strip(),'sheet':sh.title,'row':rownum}
 if set(records)!=names or any(r['spec'].lower()!='500g' for r in records.values()) or set(potato_records)!=potato_ids or any(r['spec'].lower()!='500g' for r in potato_records.values()): raise ValueError('missing or non-500g product')
 catalog=catalog_bytes(wb)
 for seed in seeds:
  rng=random.Random(seed); dest=DEST/str(seed); inputs=dest/'input'; split=SEEDS[seed]
  request={'companies':COMPANIES,'batch_date':'2026-12-01','chat_files':['聊天.txt'],
           'catalog_file':'catalog.xlsx','company_policy':'按聊天公司标记切换；未登记公司保留异常',
           'date_policy':'聊天日期标题覆盖批次日期'}
  # Build one natural TXT containing all company switches. A request lists the
  # allowed identities; individual expected companies are oracle only.
  blocks=[]
  for co in COMPANIES:
   alias=co['company_alias']
   # Same semantic demand at all four companies, with quantity and date held fixed.
   same={'requested_name':'西蓝花','scenario':'same_demand_company_invariance',
         'selected':records['西兰花'],'outcome':'selected','quantity':'2','raw_unit':'斤','quantity_500g':'2'}
   blocks.append((co['company_name'],'2026-12-01', [('西蓝花2斤',[same])]))
   must={'黑芝麻粒','去皮青笋','铁棍山药','干金针菜','薏仁米','生腰果仁',
         '一级库尔勒香梨','黄元帅','紫菜','干桂园肉','马铃薯（大885）'}
   forced=[p for p in PRODUCTS if p[0] in must]
   used_targets={p[1] for p in forced}|{'西兰花'}
   options=[p for p in PRODUCTS if p[1] not in used_targets]
   rng.shuffle(options); remaining=[]
   for candidate in options:
    if candidate[1] not in used_targets:
     remaining.append(candidate); used_targets.add(candidate[1])
     if len(remaining)==6: break
   chosen=forced+remaining; rng.shuffle(chosen)
   for raw_name,target,kind in chosen:
    q=rng.randint(1,8); unit=rng.choice(['斤','kg']); n=str(q) if rng.random()<.6 else '零一二三四五六七八'[q]
    text=f'{raw_name}{n}{unit}'
    event={'requested_name':raw_name,'scenario':'normal_'+kind,'selected':records[target],
           'outcome':'selected','quantity':str(q),'raw_unit':unit,
           'quantity_500g':str(q*(2 if unit=='kg' else 1))}
    # A real switch on most blocks: shorthand and full name alternate.
    company_label=rng.choice([co['company_name'],alias])
    blocks.append((company_label,f'2026-12-{rng.randint(1,5):02d}',[(text,[event])]))
  # Generic potato synonyms are genuinely comparable on 500g basis.
  candidates=sorted(potato_records.values(),key=lambda r:r['sku'])
  best=max(candidates,key=lambda r:Decimal(r['price']))
  assert best['sku']=='80003671' and sum(Decimal(r['price'])==Decimal(best['price']) for r in candidates)==1
  q=rng.randint(1,8)
  potato={'requested_name':'马铃薯','scenario':'normal_synonym_comparable','selected':best,
          'candidates':candidates,'outcome':'selected','quantity':str(q),'raw_unit':'斤',
          'quantity_500g':str(q),'selection_basis':'constraints_then_highest_price'}
  blocks.append((rng.choice(COMPANIES)['company_name'],'2026-12-06',[(f'马铃薯{q}斤',[potato])]))
  # Clear switch inside a single line: two different companies and two items.
  route1={'requested_name':'西蓝花','scenario':'inline_company_switch','selected':records['西兰花'],
          'outcome':'selected','quantity':'3','raw_unit':'斤','quantity_500g':'3','company_override':'星月站'}
  route2={'requested_name':'薏仁米','scenario':'inline_company_switch','selected':records['薏米'],
          'outcome':'selected','quantity':'4','raw_unit':'斤','quantity_500g':'4','company_override':'学院路站'}
  blocks.append(('星月站','2026-12-08', [('星月站：西蓝花3斤；学院路站：薏仁米4斤',[route1,route2])]))
  # Unknown textual company is a blocker, not a seventh spelling of a known site.
  unknown={'requested_name':'西蓝花','scenario':'unknown_company','selected':records['西兰花'],
           'outcome':'review','anomaly':'company_unknown','quantity':'2','raw_unit':'斤','quantity_500g':'2'}
  blocks.append(('云溪驿站','2026-12-07',[('西蓝花2斤',[unknown])]))
  # True unit/spec ambiguity or unconstrained alias: explicitly incomplete candidate sets.
  for text,requested,kind,near in [
    ('白糖2袋','白糖','packaging_or_sku_ambiguous',[]),
    ('黑芝麻1盒','黑芝麻','unit_or_packaging_unverified',[records['黑芝麻']]),
    ('西蓝花1罐','西蓝花','unit_or_packaging_unverified',[records['西兰花']]),
    ('薏仁米2袋','薏仁米','unit_or_packaging_unverified',[records['薏米']]),
    ('臻园牌西蓝花2斤','臻园牌西蓝花','brand_unverified',[records['西兰花']]),
  ]:
   event={'requested_name':requested,'scenario':'semantic_review','selected':None,'outcome':'review',
          'anomaly':kind,'candidates':near,'candidate_set_status':'not_exhaustive',
          'quantity_500g':None}
   blocks.append((rng.choice(COMPANIES)['company_name'],f'2026-12-{rng.randint(9,12):02d}',[(text,[event])]))
  rng.shuffle(blocks)
  lines=['各单位分开记账，下面公司名和配送日期适用于其后订单。']
  events=[]; aliasmap={v:c['company_name'] for c in COMPANIES for v in [c['company_name'],c['company_alias']]}
  for company_label,date,items in blocks:
   lines.append(f'公司：{company_label}'); lines.append(f'配送日期：{date}')
   for text,parts in items:
    lines.append(text); line_no=len(lines)
    for event in parts:
     ev=dict(event)
     ev['company']=ev.pop('company_override',aliasmap.get(company_label,company_label))
     ev.update(event_id=f'{seed}-{len(events)+1:04d}',catalog_version='current',date=date,action='order',
               source_file='input/聊天.txt',source_line=line_no,source_line_text=text)
     # Product request is a unique literal in each source line. The entire
     # line is retained too; source_span uses Unicode code-point offsets.
     start=text.index(ev['requested_name'])
     token_end=start+len(ev['requested_name'])
     # Include immediately following quantity and unit in the event span.
     match=re.match(r'[0-9一二三四五六七八]+(?:斤|kg|袋|盒|罐)',text[token_end:])
     if match: token_end += len(match.group())
     ev['source_span']={'start':start,'end':token_end,'unit':'unicode_codepoints','basis':'line','text':text[start:token_end]}
     if 'candidates' not in ev and ev['selected']: ev['candidates']=[ev['selected']]
     if 'candidate_set_status' not in ev: ev['candidate_set_status']='selected_exact_or_justified_semantic_match'
     events.append(ev)
  text=('\n'.join(lines)+'\n').encode()
  write(inputs/'聊天.txt',text); write(inputs/'request.json',jdump(request)); write(inputs/'catalog.xlsx',catalog)
  counts=Counter(x['scenario'] for x in events)
  source_names={e['selected']['name'] for e in events if e.get('selected')}
  if len(events)<60 or len(source_names)<20: raise ValueError('coverage too small')
  expected={'schema':'ordertcr.synthetic_oracle.v1','seed':seed,'split':split,'events':events,
            'source':'frozen before evaluated run; directly derived from catalog records and authored semantics',
            'scoring_notes':['source_span uses zero-based Unicode code points, end exclusive, relative to source_line_text.',
              'company is the expected text-derived assignment, never an input routing field.',
              'unknown company retains item and known SKU but cannot publish for a known company.',
              'candidate_set_status not_exhaustive prohibits interpreting the listed examples as the entire candidate set.',
              '各公司同需求一致场景不依赖公司历史采购偏好。']}
  eb=jdump(expected); write(dest/'expected.json',eb)
  input_hashes={str(p.relative_to(dest)):sha(p.read_bytes()) for p in sorted(inputs.rglob('*')) if p.is_file()}
  manifest={'schema':'ordertcr.synthetic_manifest.v1','seed':seed,'split':split,'original_catalog_sha256':sha(src),
            'input_sha256':input_hashes,'expected_sha256':sha(eb),'event_count':len(events),
            'scenario_denominators':dict(sorted(counts.items())), 'distinct_catalog_products':len(source_names),
            'randomness':'random.Random(seed) sample, randint, choice, shuffle',
            'independence':'No outputs, replay results, historical answer files, or knowledge base read; source catalog only.',
            'boundary':'selected SKU follows explicit name/constraint and known 500g pricing basis; unknown packaging is review.',
            'candidate_policy':'not_exhaustive when no exhaustive semantic candidate search is certified',
            'historical_159_denominator':'unchanged', 'input_contract':'Only input/request.json, input/聊天.txt and input/catalog.xlsx are runtime inputs.'}
  write(dest/'manifest.json',jdump(manifest))
  hashes={str(p.relative_to(dest)):sha(p.read_bytes()) for p in sorted(dest.rglob('*')) if p.is_file()}
  hashes['builder']=sha(Path(__file__).read_bytes())
  frozen=jdump({'sha256':hashes,'note':'Frozen before any system run.'}); write(dest/'FREEZE.json',frozen)
  print(seed,split,'events',len(events),'distinct_products',len(source_names),'oracle_sha',sha(eb),'freeze_sha',sha(frozen),'scenarios',dict(sorted(counts.items())))
 if not (DEST/'README.md').exists():
  write(DEST/'README.md', '''# V6 semantic/company extension\n\n7301 and 7302 are development seeds; 7391 is holdout. The runtime must only access each seed's `input/request.json`, `input/聊天.txt` and `input/catalog.xlsx`. `request.json` is the allowed company initialization scope, and company changes are read from the text, including within a single line; no routing from expected events. An unknown company remains a review item. All dates are explicit synthetic test data.\n\nThe original catalog is copied and normalized without new SKUs or price edits. Normal rows use current, explicitly compatible catalog records and 500g pricing basis. Aliases are constructed independently of past outputs and reference orders. Review cases without exhaustive catalog candidate proof explicitly declare `candidate_set_status: not_exhaustive`; no consumer may treat an example list as exhaustive. Source spans are line-relative zero-based Unicode code point offsets, end exclusive. Scenario denominators and input hashes are in each manifest. A single oracle event corresponds to one intended item; company switching, identity and quantity should be scored separately.\n\nFrozen synthetic cases do not prove real-order accuracy. Three seeds reuse a shared product and expression pool, and there is no claim of disjoint product distribution from earlier inputs. Historical 159 remains the original denominator. Builder reproducibility uses the same source catalog and Python/openpyxl environment; it refuses overwriting a seed.\n'''.encode())
if __name__=='__main__': main()
