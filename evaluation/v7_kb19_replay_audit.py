#!/usr/bin/env python3
"""Independent KB19 three-mode scorer; expected data are read only for scoring."""
import argparse,hashlib,json,re,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'evaluation'))
from v7_kb16_v2_audit import align_events,score_event,metric,is_automatic
from v6_finance_audit import audit_result
read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
MODES=('catalog_only','shared','shared_with_company_contract')

def review_score(e,item,ids,chat):
 if not item:return {'event_id':e['event_id'],'kind':e['kind'],'company':e['company'],'date':e['date'],'automatic':False,'automatic_correct':False,'correct':False,'review_correct':False,'control_correct':False,'control_safe':False,'errors':['missing_output']}
 row=item.get('row',{});errs=[]
 def c(ok,x):
  if not ok:errs.append(x)
 start,end=row.get('char_start'),row.get('char_end');line=e['source_line'];lines=chat.splitlines(keepends=True);base=sum(map(len,lines[:line-1])) if isinstance(line,int) and line>0 else -1
 c(isinstance(start,int) and isinstance(end,int) and base<=start<=end<=base+len(lines[line-1].rstrip('\r\n')) and chat[start:end]==row.get('raw_text')==e['source_line_text'],'source_span')
 c(item.get('company_id')==ids.get(e['company']),'company');c(item.get('shipping_date')==e['date'],'date')
 c(not is_automatic(row),'review_automatic');c(row.get('status') in {'needs_review','unmapped','ambiguous'},'review_status');c(row.get('amount') is None and row.get('finance_amount') is None,'review_amount');c(row.get('invoice_quantity') is None,'review_invoice_quantity')
 if e['kind']=='missing_quantity':c(row.get('quantity') is None,'quantity_filled')
 if e['kind']=='missing_unit':c(row.get('unit') is None,'unit_filled')
 if e['kind']=='unknown_product':c(not row.get('selected'),'unknown_product_selected')
 return {'event_id':e['event_id'],'kind':e['kind'],'company':e['company'],'date':e['date'],'source_line':e['source_line'],'source_text':e['source_line_text'],'automatic':is_automatic(row),'automatic_correct':False,'correct':not errs,'review_correct':not errs,'control_correct':False,'control_safe':not errs,'errors':errs,'actual':item}

def scenario(e):
 if e['kind']!='conditional_match':return e['kind']
 t=e['source_line_text'];return 'quantity_first' if t[:1].isdigit() else ('embedded_quantity' if re.search(r'\d+(公斤|斤)',t) else 'quantity_last')
def aggregate(rows):
 normal=[x for x in rows if x['kind']=='conditional_match']; keys=lambda k:sorted({x.get(k) for x in rows if x.get(k) is not None})
 return {'all':metric(rows),'normal':metric(normal),'by_company':{k:metric([x for x in rows if x.get('company')==k]) for k in keys('company')},'by_scenario':{k:metric([x for x in rows if x.get('scenario')==k]) for k in keys('scenario')}}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phase',required=True);ap.add_argument('--fixture',type=Path,default=ROOT/'evaluation/v7/kb19_challenge');args=ap.parse_args()
 if not re.fullmatch(r'kb19_[a-z0-9_]+',args.phase):ap.error('phase must start kb19_')
 dest=ROOT/'evaluation/v7'/args.phase;out=dest/'audit';
 if out.exists():raise SystemExit('audit output exists')
 state=read(dest/'execution_status.json');freeze=read(dest/'input_freeze.json')['files_sha256'];mismatch=[p for p,h in freeze.items() if not (ROOT/p).is_file() or sha(ROOT/p)!=h]
 if state.get('status')!='runs_complete_before_audit' or not state.get('source_equal'):raise ValueError('execution incomplete or source drift')
 if any(not p.startswith(('skills/','original/')) for p in mismatch):raise ValueError('frozen artifact drift')
 seeds=(9291,) if 'holdout' in args.phase else (9201,9202);expected_keys={(s,m) for s in seeds for m in MODES};sts=state['executions']
 if {(s['seed'],s['mode']) for s in sts}!=expected_keys:raise ValueError('wrong execution set')
 runs=[];groups=defaultdict(list)
 for st in sts:
  base=args.fixture/str(st['seed']);exp=read(base/'expected.json');chat=(base/'input/聊天.txt').read_text(encoding='utf-8-sig');req=read(base/'input/request.json');res=read(st['result']);ids={c['company_name']:f'c{i}' for i,c in enumerate(req['companies'],1)}
  if len(exp['events'])!=32 or res.get('matching_mode')!=st['mode']:raise ValueError('wrong denominator/mode')
  pairs,extras=align_events(exp['events'],res['events'],chat);scored=[]
  for e,item,why in pairs:
   e=dict(e);e['scenario']=scenario(e)
   if e['kind']=='conditional_match':e['expected_catalog_item']=e['selected'];row=score_event(e,item,ids,chat);row['scenario']=e['scenario']
   else:row=review_score(e,item,ids,chat);row['scenario']=e['scenario']
   if why=='multiple_matching_outputs':row['errors']=['multiple_matching_outputs'];row['correct']=False
   scored.append(row)
  for item in extras:scored.append({'event_id':None,'kind':'unexpected_output','company':None,'scenario':None,'automatic':is_automatic(item.get('row',{})),'automatic_correct':False,'correct':False,'review_correct':False,'control_correct':False,'control_safe':False,'errors':['unexpected_output'],'actual':item})
  finance=audit_result(res);run={'seed':st['seed'],'mode':st['mode'],'aggregate':aggregate(scored),'failure_categories':dict(Counter(z for x in scored for z in x['errors'])),'finance_audit':finance,'events':scored};runs.append(run);groups[st['mode']].extend(scored)
 report={'schema_version':1,'phase':args.phase,'fixture':str(args.fixture),'source_mismatches':mismatch,'mode_aggregates':{k:aggregate(v) for k,v in groups.items()},'runs':runs,'C':None,'C_reason':'candidate completeness unknown; conditional expectations do not establish exhaustive candidates','scope':'KB19 frozen conditional challenge; not real customer acceptance'}
 out.mkdir();(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');lines=['# KB19 replay audit','','C remains unknown because candidate completeness is not established. Review rows are safety-scored separately; they are not automatic successes.','', '| mode | events | automatic | automatic_correct | A | B | normal_correct | review_correct | finance_errors |','|---|---:|---:|---:|---:|---:|---:|---:|---:|']
 for mode,a in report['mode_aggregates'].items(): lines.append(f"| {mode} | {a['all']['events']} | {a['all']['automatic']} | {a['all']['automatic_correct']} | {a['all']['A']} | {a['all']['B']} | {a['normal']['automatic_correct']}/{a['normal']['events']} | {a['all']['review_correct']} | {sum(len(x['finance_audit']['errors']) for x in runs if x['mode']==mode)} |")
 (out/'report.md').write_text('\n'.join(lines)+'\n');print(json.dumps({'phase':args.phase,'mode_aggregates':report['mode_aggregates']},ensure_ascii=False))
if __name__=='__main__':main()
