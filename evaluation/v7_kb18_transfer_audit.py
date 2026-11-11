#!/usr/bin/env python3
"""Score frozen KB18 runs with the common audit functions."""
import argparse,hashlib,json,re,sys
from collections import Counter,defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'evaluation'))
from v7_kb16_v2_audit import align_events,score_event,metric,is_automatic
from v6_finance_audit import audit_result
read=lambda p:json.loads(Path(p).read_text(encoding='utf-8'))
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phase',required=True);args=ap.parse_args()
 if not (re.fullmatch(r'kb18_transfer_(baseline|final)_(dev|holdout)',args.phase) or re.fullmatch(r'kb18_transfer_dev_r[1-9][0-9]*',args.phase)):ap.error('invalid phase')
 dest=ROOT/'evaluation/v7'/args.phase;out=dest/'audit'
 if out.exists():raise SystemExit('audit exists')
 state=read(dest/'execution_status.json'); freeze=read(dest/'input_freeze.json')['files_sha256']
 if state['status']!='runs_complete_before_audit' or not state['source_equal']:raise ValueError('run incomplete or drift')
 changed=[p for p,h in freeze.items() if not (ROOT/p).is_file() or sha(ROOT/p)!=h]; bad=[p for p in changed if not p.startswith(('skills/','original/'))]
 if bad:raise ValueError('frozen artifacts changed: '+','.join(bad))
 seeds=(9191,) if args.phase.endswith('holdout') else (9101,9102); modes=('catalog_only','shared','shared_with_company_contract');sts=state['executions']
 if len(sts)!=len(seeds)*3 or {(s['seed'],s['mode']) for s in sts}!={(s,m) for s in seeds for m in modes} or any(s['register_rc']!=0 or s['run_rc']!=0 for s in sts):raise ValueError('invalid runs')
 runs=[];groups=defaultdict(list)
 for st in sts:
  source=ROOT/'evaluation/v7/kb18_transfer_challenge'/str(st['seed']); expected=read(source/'expected.json');chat=(source/'input/聊天.txt').read_text(encoding='utf-8-sig');req=read(source/'input/request.json');result=read(st['result']);ids={c['company_name']:f'c{i}' for i,c in enumerate(req['companies'],1)}
  if len(expected['events'])!=32 or result['matching_mode']!=st['mode']:raise ValueError('invalid event count or mode')
  pairs,extras=align_events(expected['events'],result['events'],chat);scored=[]
  for e,item,alignment in pairs:
   e=dict(e)
   if e['kind']=='conditional_match':e['expected_catalog_item']=e['selected']
   row=score_event(e,item,ids,chat);row['scenario']=('quantity_first' if e['source_line_text'].lstrip()[:1].isdigit() else 'quantity_last') if e['kind']=='conditional_match' else e['kind']
   if alignment=='multiple_matching_outputs':row['errors']=['multiple_matching_outputs']
   scored.append(row)
  for item in extras:scored.append({'event_id':None,'kind':'unexpected_output','company':None,'scenario':None,'automatic':is_automatic(item.get('row',{})),'automatic_correct':False,'correct':False,'review_correct':False,'control_correct':False,'control_safe':False,'errors':['unexpected_output'],'actual':item})
  finance=audit_result(result);runs.append({'seed':st['seed'],'mode':st['mode'],'summary':metric(scored),'failure_categories':dict(Counter(v for x in scored for v in x['errors'])),'finance_audit':finance,'events':scored});groups[st['mode']].extend(scored)
 def agg(rows):
  normal=[x for x in rows if x['kind']=='conditional_match'];return {'all':metric(rows),'normal':metric(normal),'by_company':{v:metric([x for x in rows if x.get('company')==v]) for v in sorted({x['company'] for x in rows if x.get('company')})},'by_scenario':{v:metric([x for x in rows if x.get('scenario')==v]) for v in sorted({x['scenario'] for x in rows if x.get('scenario')})}}
 report={'schema_version':1,'phase':args.phase,'source_mismatches_after_run':changed,'mode_aggregates':{k:agg(v) for k,v in groups.items()},'runs':runs,'C':None,'C_reason':'candidate completeness unknown','scope':'conditional frozen challenge; not real customer acceptance'}
 out.mkdir();(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print('audit complete')
if __name__=='__main__':main()
