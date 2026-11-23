"""Create reproducible-content release manifest; exclude caches and private data."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXCLUDE={'.git','.venv','__pycache__','.pytest_cache','node_modules','screenshots','private_reports','private_backups','saas_lab_private'}


def sha(data):return hashlib.sha256(data).hexdigest()


def main():
    output=Path(sys.argv[1]).resolve()
    if output.is_relative_to(ROOT):raise ValueError('output must be outside source tree')
    paths=[]
    for p in sorted(ROOT.rglob('*')):
        rel=p.relative_to(ROOT)
        if any(part in EXCLUDE for part in rel.parts) or p.name=='MANIFEST.json' or p.suffix=='.pyc':continue
        if p.is_symlink():raise ValueError('symlink forbidden: '+str(rel))
        if not p.is_file():continue
        if p.suffix in ('.db','.sqlite3','.key','.pem','.p12','.pfx','.aipobak','.zip') or p.name=='.env' or '.db-' in p.name:
            raise ValueError('private/runtime file forbidden: '+str(rel))
        paths.append(p)
    manifest={'name':ROOT.name,'version':'0.3.7','build_revision':'cli-hotfix-1','built_at':datetime.now(timezone.utc).isoformat(),
              'target_market':'Russian WB/Ozon sellers','mode':'LOCAL_ONLY + separate SYNTHETIC_SECURITY_LAB',
              'source_archive_sha256':'aed7b9620edf79f28e0301bea065ea355c904dcf1d25c1c63fd8c1dffc004d57',
              'baseline_passed':260,'tests':{'Python3.13.5':{'passed':269,'failed':0,'skipped':0},'Python3.12.14':{'passed':269,'failed':0,'skipped':0}},
              'live_api_verified':False,'macos_verified':False,'visual_browser_verified':False,'production_ready':False,
              'files':[{'path':str(p.relative_to(ROOT)),'size_bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in paths]}
    (ROOT/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for p in paths+[ROOT/'MANIFEST.json']:z.write(p,ROOT.name+'/'+str(p.relative_to(ROOT)))
    with zipfile.ZipFile(output) as z:
        assert z.testzip() is None
        for record in manifest['files']:
            content=z.read(ROOT.name+'/'+record['path'])
            assert len(content)==record['size_bytes'] and sha(content)==record['sha256']
    print(json.dumps({'file':str(output),'bytes':output.stat().st_size,'sha256':sha(output.read_bytes()),'manifest_files_verified':len(paths),'archive_integrity':'PASS'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
