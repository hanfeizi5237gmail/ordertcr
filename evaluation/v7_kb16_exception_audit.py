#!/usr/bin/env python3
"""Independent safety audit for KB16 review/control rows after raw replay."""
from pathlib import Path
from collections import Counter
import argparse,json,hashlib
ROOT=Path(__file__).resolve().parents[1]
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--report',type=Path,default=ROOT/'evaluation/v7/kb16_rc4/report.json');ap.add_argument('--output',type=Path,default=ROOT/'evaluation/v7/kb16_exception_audit.json');a=ap.parse_args()
 report=read(a.report); rows=[]
 for run in report['runs']:
  if run['mode']!='shared':continue
  for e in run['events']:
   if e['kind']=='conditional_match':continue
   actual=e.get('actual') or {}; row=actual.get('row') or {}; errors=[]
   # An initial control row is a real first demand and may be automatically
   # matched; append/change/cancel must retain relation state and review rows
   # must remain non-priced.
   if e['kind'] not in ('initial',) and row.get('status')=='matched':errors.append('review_or_control_auto_matched')
   if e['kind'] not in ('initial',) and (row.get('amount') is not None or row.get('finance_amount') is not None):errors.append('unknown_amount_nonblank')
   if not isinstance(row.get('char_start'),int) or not isinstance(row.get('char_end'),int):errors.append('source_span_missing')
   if not row.get('raw_text'):errors.append('raw_text_missing')
   if e.get('company') and actual.get('company_id') in (None,'unassigned') and e.get('company')!='未登记单位':errors.append('company_not_routed')
   if row.get('event') in ('追加','改量','取消') and not row.get('predecessor_clue') and e['kind'] in ('append','change','cancel'):errors.append('control_predecessor_missing')
   rows.append({'seed':run['seed'],'event_id':e['event_id'],'kind':e['kind'],'source_text':e.get('source_text'),'status':row.get('status'),'event_state':row.get('event_state'),'errors':errors})
 summary={'events':len(rows),'safe':sum(not x['errors'] for x in rows),'error_count':sum(len(x['errors']) for x in rows),'by_kind':{},'C':'unknown: candidate completeness undefined; safety does not equal semantic candidate completeness'}
 for k in sorted({x['kind'] for x in rows}):
  xs=[x for x in rows if x['kind']==k];summary['by_kind'][k]={'events':len(xs),'safe':sum(not x['errors'] for x in xs),'errors':sum(len(x['errors']) for x in xs)}
 out={'schema_version':1,'scope':'KB16 rc4 shared review/control rows only','summary':summary,'rows':rows,'source_report_sha256':hashlib.sha256(a.report.read_bytes()).hexdigest(),'limitations':['颜色与原生批注由保存后工作簿审计覆盖；本审计不证明候选全集。','安全转人工不计入自动A/B。']}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
