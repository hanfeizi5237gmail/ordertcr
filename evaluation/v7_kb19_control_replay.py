#!/usr/bin/env python3
"""Execute separately frozen control batch without accessing expected values."""
import json,sys,traceback
from pathlib import Path
from v7_kb16_v2_replay import ROOT,KB,CLI,sha,write,config,execute,fingerprint,verify_challenge,MODES
FIX=ROOT/'evaluation/v7/kb19_challenge';EXPECTED_KB='0b71ef20548ee95477d1977a3b6929a0e6b415af87688e2458d4b5de865e1dc2'
def main():
 dest=ROOT/'evaluation/v7/kb19_baseline_control';out=ROOT/'outputs/v7/kb19_baseline_control'
 if dest.exists() or out.exists():raise SystemExit('destination exists')
 dest.mkdir(parents=True);out.mkdir(parents=True); state={'status':'started','executions':[]};before=None
 scripts=(Path(__file__).resolve(),ROOT/'evaluation/v7_kb16_v2_replay.py')
 try:
  if sha(KB)!=EXPECTED_KB:raise ValueError('unexpected KB fingerprint')
  verify_challenge(('control',),FIX);before=fingerprint(FIX,scripts);write(dest/'source_before.json',before)
  source=FIX/'control'; ordinary=json.loads((source/'input/request.json').read_text());chat=(source/'input/聊天.txt').resolve();catalog=(source/'input'/ordinary['catalog_file']).resolve()
  if ordinary['chat_files']!=['聊天.txt'] or catalog.parent!=(ROOT/'original').resolve():raise ValueError('invalid source paths')
  for mode in MODES:
   if fingerprint(FIX,scripts)!=before:raise ValueError('source drift before run')
   runtime=dest/'runtime'/mode;output=out/mode;request=dest/'requests'/mode/'batch.json';write(request,{'schema_version':1,'batch_id':'current','shipping_date':ordinary['batch_date'],'chat_files':[str(chat)],'config':config(ordinary),'matching_mode':mode,'test_only':True})
   entry={'mode':mode,'register_rc':None,'run_rc':None,'result':str(output/'current/messages-result.json')};state['executions'].append(entry);write(dest/'execution_status.json',state)
   entry['register_rc']=execute([sys.executable,'-B',str(CLI),'catalog-register','--runtime',str(runtime),'--file',str(catalog)],dest/'logs'/mode/'catalog.log')
   if entry['register_rc']!=0 or fingerprint(FIX,scripts)!=before:raise ValueError('registration failure or source drift')
   entry['run_rc']=execute([sys.executable,'-B',str(CLI),'run-messages','--runtime',str(runtime),'--batch',str(request),'--output',str(output)],dest/'logs'/mode/'run.log')
   if entry['run_rc']!=0 or fingerprint(FIX,scripts)!=before:raise ValueError('execution failure or source drift')
  state['status']='runs_complete_before_audit';frozen=dict(before)
  for base in (out,dest/'runtime',dest/'requests'):
   for p in base.rglob('*'):
    if p.is_file():frozen[str(p.relative_to(ROOT))]=sha(p)
  write(dest/'input_freeze.json',{'files_sha256':frozen})
 except Exception as exc:
  state.update(status='failed',error=str(exc),traceback=traceback.format_exc());raise
 finally:
  after=fingerprint(FIX,scripts);write(dest/'source_after.json',after);state['source_equal']=before is not None and before==after;write(dest/'execution_status.json',state)
if __name__=='__main__':main()
