#!/usr/bin/env python3
"""Build a frozen local portable package and verify extracted tests and raw CLI."""
from pathlib import Path
import hashlib, json, zipfile, subprocess, sys

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/order-mapping'
CHECK = ROOT / 'evaluation/v7/package_check_kb18_final'
PACKAGE = ROOT / 'dist/order-mapping-v7-kb18-final.zip'
EXPECTED_KB = '0b71ef20548ee95477d1977a3b6929a0e6b415af87688e2458d4b5de865e1dc2'

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    if CHECK.exists() or PACKAGE.exists(): raise SystemExit('target exists; refusing overwrite')
    if sha(SKILL/'knowledge/procurement.json') != EXPECTED_KB: raise SystemExit('KB SHA mismatch')
    CHECK.mkdir(parents=True); PACKAGE.parent.mkdir(exist_ok=True)
    inventory={str(p.relative_to(SKILL.parent)):sha(p) for p in sorted(SKILL.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
    with zipfile.ZipFile(PACKAGE,'x',zipfile.ZIP_DEFLATED) as z:
        for name in inventory: z.write(ROOT/'skills'/name,name)
    unpack=CHECK/'unpacked'; unpack.mkdir()
    with zipfile.ZipFile(PACKAGE) as z:
        if z.testzip(): raise RuntimeError('archive CRC failure')
        z.extractall(unpack)
    extracted={str(p.relative_to(unpack)):sha(p) for p in unpack.rglob('*') if p.is_file()}
    if extracted != inventory: raise RuntimeError('archive contents do not match source inventory')
    def run(args,name):
        p=subprocess.run([sys.executable,'-B',*map(str,args)],cwd=ROOT,text=True,capture_output=True)
        (CHECK/(name+'.log')).write_text('stdout:\n'+p.stdout+'\nstderr:\n'+p.stderr)
        if p.returncode: raise RuntimeError(f'{name} exited {p.returncode}')
        return p
    run(['-m','unittest','discover','-s',unpack/'order-mapping/tests','-q'],'core')
    cli=unpack/'order-mapping/scripts/order_mapping.py'; rt=CHECK/'runtime'
    run([cli,'catalog-register','--runtime',rt,'--file',ROOT/'original/食堂食材基准价价格表10.12-10.18.xlsx'],'catalog_register')
    request=json.loads((ROOT/'evaluation/v7/original_kb18_final/requests/songbei.json').read_text()); request['batch_id']='package-kb18-final'
    request_path=CHECK/'request.json'; request_path.write_text(json.dumps(request,ensure_ascii=False,indent=2)+'\n')
    output=ROOT/'outputs/v7/package_check_kb18_final'
    run([cli,'run-messages','--runtime',rt,'--batch',request_path,'--output',output],'raw_run')
    result=output/'package-kb18-final/messages-result.json'; x=json.loads(result.read_text())
    if x['event_count'] != 53 or len(x['events']) != 53: raise RuntimeError('raw 53 event count mismatch')
    from v6_finance_audit import audit_result
    finance=audit_result(x); (CHECK/'finance_audit.json').write_text(json.dumps(finance,ensure_ascii=False,indent=2)+'\n')
    if finance['errors']: raise RuntimeError('raw finance audit failed')
    if sha(SKILL/'knowledge/procurement.json') != EXPECTED_KB: raise RuntimeError('KB changed during packaging')
    report={'package':str(PACKAGE.relative_to(ROOT)),'package_sha256':sha(PACKAGE),'kb_sha256':EXPECTED_KB,
            'inventory_count':len(inventory),'extracted_bytes_equal':True,'core_tests':'passed',
            'raw_event_count':53,'finance_errors':0,'inventory':inventory,
            'limitations':'解包本地测试，不证明其他主机、真实订单或正式结算。'}
    (CHECK/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='inventory'},ensure_ascii=False))
if __name__=='__main__': main()
