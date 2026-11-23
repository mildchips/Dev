"""Real encrypted backup/restore drill of temporary synthetic LAB data only."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from saas_lab.core import Lab,Denied
from saas_lab.recovery import backup,restore


with tempfile.TemporaryDirectory(prefix='aipo-drill-') as folder:
    root=Path(folder)
    lab=Lab(root/'source.db')
    _,tid=lab.provision('synthetic-drill','Synthetic-only-password-037',organization=True)
    token,_=lab.login('synthetic-drill','Synthetic-only-password-037');actor=lab.authenticate(token)
    sid=lab.add_store(actor,tid,'SYNTHETIC RECOVERY SHOP')
    jid=lab.enqueue(actor,tid,sid,'restore-1');claim=lab.claim()
    start=time.perf_counter();meta=backup(lab,root/'snapshot.aipobak','Synthetic backup password 037');backup_s=time.perf_counter()-start
    start=time.perf_counter();restored=restore(root/'snapshot.aipobak',root/'restored.db','Synthetic backup password 037');restore_s=time.perf_counter()-start
    recovered=Lab(root/'restored.db')
    try:recovered.authenticate(token);raise AssertionError('old session resurrected')
    except Denied:pass
    token,_=recovered.login('synthetic-drill','Synthetic-only-password-037');a=recovered.authenticate(token)
    assert recovered.job(a,tid,jid)['state']=='PAUSED'
    assert recovered.stores(a,tid)[0]['id']==sid
    assert recovered.claim() is None
    try:recovered.finish(claim);raise AssertionError('old lease resurrected')
    except Denied:pass
    recovered.stop(a,tid,False);recovered.finish(recovered.claim())
    assert recovered.job(a,tid,jid)['state']=='DONE'
    result={'at_utc':datetime.now(timezone.utc).isoformat(),'scope':'temporary synthetic SQLite lab only','backup_seconds':round(backup_s,4),
            'restore_seconds':round(restore_s,4),'sha256':meta['sha256'],'bytes':meta['bytes'],'integrity':restored['integrity'],
            'sessions_revoked':True,'inflight_job_fenced':True,'jobs_paused':True,'manual_resume_completed':True,
            'production_RPO_RTO_verified':False,'PITR_HA_verified':False}
    output=Path(sys.argv[1]);output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False,indent=2))
