"""Measured in-process synthetic HTTP benchmark, not public/production load proof."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import socket
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from saas_lab.server import create_app, COOKIE


def blocked(*a,**kw):raise AssertionError('benchmark forbids external network')


def run(tenants=100,requests=2000,workers=16):
    socket.socket.connect=blocked
    socket.socket.connect_ex=blocked
    socket.getaddrinfo=blocked
    socket.create_connection=blocked
    with tempfile.TemporaryDirectory(prefix='aipo-load-') as d:
        app=create_app(Path(d)/'lab.db',test_mode=True)
        lab=app.state.lab
        accounts=[]
        for i in range(tenants):
            _,tid=lab.provision(f'synthetic-{i}','Synthetic-only-password-037',organization=True)
            token,csrf=lab.login(f'synthetic-{i}','Synthetic-only-password-037')
            a=lab.authenticate(token)
            sid=lab.add_store(a,tid,f'SYNTHETIC-{i}')
            accounts.append((tid,sid,token,csrf))
        def request(i):
            tid,sid,token,csrf=accounts[i%tenants]
            foreign=accounts[(i+1)%tenants][0]
            start=time.perf_counter()
            with TestClient(app) as c:
                c.cookies.set(COOKIE,token)
                if i%4==0:
                    r=c.get(f'/api/tenants/{foreign}/stores');expected=403
                elif i%4==1:
                    r=c.post(f'/api/tenants/{tid}/jobs',json={'store':sid,'idempotency_key':f'request-{i}'},headers={'x-aipo-csrf':csrf});expected=200
                else:
                    r=c.get(f'/api/tenants/{tid}/stores');expected=200
            return {'ms':(time.perf_counter()-start)*1000,'ok':r.status_code==expected,'status':r.status_code}
        started=time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            observations=list(pool.map(request,range(requests)))
        elapsed=time.perf_counter()-started
        latencies=sorted(o['ms'] for o in observations)
        processed=0
        while claim:=lab.claim():lab.finish(claim);processed+=1
        def percentile(p):return round(latencies[min(len(latencies)-1,int((len(latencies)-1)*p))],3)
        return {'at_utc':datetime.now(timezone.utc).isoformat(),'version':'0.3.7','scope':'in-process FastAPI TestClient + SQLite, synthetic only',
                'python':platform.python_version(),'platform':platform.platform(),'logical_cpus':__import__('os').cpu_count(),
                'tenants':tenants,'requests':requests,'concurrent_threads':workers,'seconds':round(elapsed,3),
                'rps':round(requests/elapsed,2),'latency_ms':{'p50':percentile(.5),'p95':percentile(.95),'p99':percentile(.99)},
                'unexpected_results':sum(not o['ok'] for o in observations),'expected_denials':sum(o['status']==403 for o in observations),
                'synthetic_jobs_completed':processed,'expected_jobs':len([i for i in range(requests) if i%4==1]),
                'production_capacity_verified':False,'excluded':['TCP/TLS','PostgreSQL/RLS','cloud KMS','WB/Ozon','HA/failover','1000 concurrent users','soak/chaos']}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--tenants',type=int,default=100);p.add_argument('--requests',type=int,default=2000);p.add_argument('--workers',type=int,default=16)
    a=p.parse_args()
    if min(a.tenants,a.requests,a.workers)<2:raise ValueError('parameters must be >=2')
    result=run(a.tenants,a.requests,a.workers)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(1 if result['unexpected_results'] or result['synthetic_jobs_completed']!=result['expected_jobs'] else 0)
