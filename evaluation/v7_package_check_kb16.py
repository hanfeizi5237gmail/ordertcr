#!/usr/bin/env python3
"""Build and verify the portable KB16 skill package from extracted bytes."""
from pathlib import Path
import hashlib,json,zipfile,shutil,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]; SKILL=ROOT/'skills/order-mapping'; DIST=ROOT/'dist'; PACKAGE=DIST/'order-mapping-v7-kb16-final.zip'; CHECK=ROOT/'evaluation/v7/package_check_kb16_final'
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
 if CHECK.exists(): raise SystemExit('check exists; refusing overwrite')
 CHECK.mkdir(parents=True); inventory={}
 for p in sorted(SKILL.rglob('*')):
  if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc': inventory[str(p.relative_to(SKILL.parent))]=sha(p)
 DIST.mkdir(exist_ok=True)
 with zipfile.ZipFile(PACKAGE,'w',zipfile.ZIP_DEFLATED) as z:
  for n in inventory: z.write(ROOT/'skills'/n,n)
 unpack=CHECK/'unpacked'; unpack.mkdir()
 with zipfile.ZipFile(PACKAGE) as z:
  if z.testzip(): raise RuntimeError('zip CRC')
  z.extractall(unpack)
 if {n:sha(unpack/n) for n in inventory}!=inventory: raise RuntimeError('extracted bytes mismatch')
 cli=unpack/'order-mapping/scripts/order_mapping.py'; py=sys.executable
 def run(args,name):
  p=subprocess.run([py,'-B',*map(str,args)],cwd=ROOT,text=True,capture_output=True);(CHECK/(name+'.log')).write_text(p.stdout+'\nstderr:\n'+p.stderr);return p
 p=run([unpack/'tests'], 'inventory')
 test=subprocess.run([py,'-B','-m','unittest','discover','-s',str(unpack/'order-mapping/tests'),'-q'],cwd=ROOT,text=True,capture_output=True);(CHECK/'core.log').write_text(test.stdout+'\nstderr:\n'+test.stderr)
 if test.returncode: raise RuntimeError('core tests failed')
 rt=CHECK/'runtime'; out=ROOT/'outputs/v7/package_check_kb16_final'; req=ROOT/'evaluation/v7/original_kb16_final/requests/songbei.json'
 p=subprocess.run([py,'-B',str(cli),'catalog-register','--runtime',str(rt),'--file',str(ROOT/'original/食堂食材基准价价格表10.12-10.18.xlsx')],cwd=ROOT,text=True,capture_output=True);(CHECK/'catalog_register.log').write_text(p.stdout+'\nstderr:\n'+p.stderr)
 if p.returncode: raise RuntimeError('catalog register failed')
 reqj=json.loads(req.read_text());reqj['batch_id']='package-kb16';(CHECK/'request.json').write_text(json.dumps(reqj,ensure_ascii=False,indent=2))
 p=subprocess.run([py,'-B',str(cli),'run-messages','--runtime',str(rt),'--batch',str(CHECK/'request.json'),'--output',str(out)],cwd=ROOT,text=True,capture_output=True);(CHECK/'raw_run.log').write_text(p.stdout+'\nstderr:\n'+p.stderr)
 if p.returncode: raise RuntimeError('raw run failed')
 result=out/'package-kb16/messages-result.json'; x=json.loads(result.read_text())
 report={'package':str(PACKAGE.relative_to(ROOT)),'sha256':sha(PACKAGE),'inventory_count':len(inventory),'extracted_bytes_equal':True,'core_tests':'passed','raw_event_count':x.get('event_count'),'source_scope':'portable skill only; no external host or production acceptance'}
 (CHECK/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');(DIST/'order-mapping-v7-kb16-final.manifest.json').write_text(json.dumps({**report,'inventory':inventory},ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__': main()
