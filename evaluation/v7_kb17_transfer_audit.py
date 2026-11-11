#!/usr/bin/env python3
"""Score KB17 raw runs with the shared semantic scoring and finance auditor."""
import argparse, hashlib, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'evaluation'))
from v7_kb16_v2_audit import align_events, score_event, metric, is_automatic
from v6_finance_audit import audit_result
FIX=ROOT/'evaluation/v7/kb17_transfer_challenge'
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def scenario(event):
 if event['kind']!='conditional_match':return event['kind']
 return 'quantity_first' if event['source_line_text'].lstrip()[:1].isdigit() else 'quantity_last'
def grouped(rows, key):
 values=sorted({row.get(key) for row in rows if row.get(key) is not None})
 return {value:metric([row for row in rows if row.get(key)==value]) for value in values}
def aggregates(rows):
 normal=[row for row in rows if row.get('kind')=='conditional_match']
 return {'summary':metric(rows),'normal_subset':metric(normal),
         'per_company':grouped(rows,'company'),'normal_per_company':grouped(normal,'company'),
         'per_scenario':grouped(rows,'scenario'),'normal_per_scenario':grouped(normal,'scenario')}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phase',required=True);args=ap.parse_args()
 if args.phase not in ('kb17_transfer_baseline_dev','kb17_transfer_baseline_holdout','kb17_transfer_final_dev','kb17_transfer_final_holdout') and not re.fullmatch(r'kb17_transfer_dev_r[1-9][0-9]*',args.phase):ap.error('invalid phase')
 dest=ROOT/'evaluation/v7'/args.phase; out=dest/'audit';
 if out.exists():raise SystemExit('audit exists; refusing overwrite')
 status=read(dest/'execution_status.json'); freeze=read(dest/'input_freeze.json')
 if status['status']!='runs_complete_before_audit' or not status['source_equal']:raise ValueError('run incomplete or source drift')
 mismatches=[p for p,h in freeze['files_sha256'].items() if not (ROOT/p).is_file() or sha(ROOT/p)!=h]
 if mismatches:raise ValueError('frozen input mismatch: '+','.join(mismatches))
 expected_seeds=(9091,) if 'holdout' in args.phase else (9001,9002)
 expected_keys={(seed,mode) for seed in expected_seeds for mode in ('catalog_only','shared','shared_with_company_contract')}
 sts=status['executions']; keys=[(x['seed'],x['mode']) for x in sts]
 if set(keys)!=expected_keys or len(keys)!=len(expected_keys) or any(x['register_rc']!=0 or x['run_rc']!=0 for x in sts):raise ValueError('execution set incomplete')
 scorer=ROOT/'evaluation/v7_kb16_v2_audit.py'; scoring_hash=sha(scorer); own_hash=sha(__file__)
 runs=[];modes=defaultdict(list)
 for st in sts:
  seed=st['seed'];base=FIX/str(seed); expected=read(base/'expected.json');chat=(base/'input/聊天.txt').read_text(encoding='utf-8-sig');request=read(base/'input/request.json');result=read(st['result'])
  if result.get('matching_mode')!=st['mode'] or len(expected['events'])!=32:raise ValueError('invalid mode or denominator')
  ids={x['company_name']:f'c{i}' for i,x in enumerate(request['companies'],1)}
  events=[]
  for raw in expected['events']:
   event=dict(raw)
   if event['kind']=='conditional_match':event['expected_catalog_item']=event['selected']
   events.append(event)
  pairs,extras=align_events(events,result.get('events',[]),chat)
  scored=[]
  for e,item,alignment in pairs:
   row=score_event(e,item,ids,chat)
   row['scenario']=scenario(e)
   if alignment=='multiple_matching_outputs':row['errors']=['multiple_matching_outputs']
   scored.append(row)
  for item in extras:
   scored.append({'event_id':None,'kind':'unexpected_output','company':None,'concept':None,'automatic':is_automatic(item.get('row',{})),'automatic_correct':False,'correct':False,'review_correct':False,'control_correct':False,'control_safe':False,'errors':['unexpected_output'],'actual':item})
  finance=audit_result(result)
  run={'seed':seed,'mode':st['mode'],**aggregates(scored),'failure_categories':dict(Counter(e for x in scored for e in x['errors'])),'finance_audit':finance,'events':scored}
  runs.append(run);modes[st['mode']].extend(scored)
 if sha(scorer)!=scoring_hash or sha(__file__)!=own_hash:raise ValueError('scorer changed during scoring')
 if any(not (ROOT/p).is_file() or sha(ROOT/p)!=h for p,h in freeze['files_sha256'].items()):raise ValueError('frozen input changed during scoring')
 report={'schema_version':1,'scope':'KB17 conditional expression transfer; not customer acceptance','shared_scorer_sha256':scoring_hash,'adapter_sha256':own_hash,'input_freeze_sha256':sha(dest/'input_freeze.json'),'mode_summaries':{k:metric(v) for k,v in modes.items()},'mode_aggregates':{k:aggregates(v) for k,v in modes.items()},'runs':runs,'candidate_completeness':'unknown; C=null'}
 out.mkdir();(out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print('Audit complete; details saved in separate phase directory')
if __name__=='__main__':main()
