"""All tests use httpx.MockTransport. NO network or real credentials."""
import json
import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from app.storage import initialize
from app.integration_security import Vault, Stopped, SecurityError, initialize_key
from app.offline_sync import (
    CheckpointStore, OfflineSync, ExternalAPIError, ContractError, OZON_BY_DAY, WB_DETAILS
)
from app.live_connector import (
    LiveDisabled, QuotaDeferred, LiveGateway, DurableQuota, ReadOnlyHTTPTransport,
    LIVE_OPT_IN_ENV, LIVE_OPT_IN_VALUE, _retry_after, _strict_body,
)
from app import live_cli


class Clock:
    def __init__(self): self.t = 5000.0
    def now(self): return self.t
    def sleep(self, value): self.t += value


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_OPT_IN_ENV, raising=False)
    db = tmp_path/'store.db'
    initialize(db)
    with sqlite3.connect(db) as c:
        for store, platform in [('wb1','WB'),('wb2','WB'),('oz1','Ozon')]:
            c.execute('INSERT INTO stores(id,name,platform) VALUES(?,?,?)', (store,store,platform))
    initialize_key(tmp_path/'vault')
    v = Vault(tmp_path/'vault',db)
    ids = {
        'oz1':v.add('oz1','Ozon','o','TEST_SECRET_NOT_REAL',client_id='123456', expires_on='2099-12-31'),
        'wb1':v.add('wb1','WB','w1','WB_FAKE_NOT_REAL'),
        'wb2':v.add('wb2','WB','w2','WB_FAKE_NOT_REAL_2')
    }
    stage=CheckpointStore(tmp_path/'staging.db')
    return v,ids,stage


def gw(env, handler, *, attempts=1, clock=None, max_wait=180):
    v, _, _ = env
    c = httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=True)
    tr = ReadOnlyHTTPTransport(client=c)
    fake = clock or Clock()
    quota = DurableQuota(v.dir/'quota.db',{'WB':60.0,'Ozon':2.0},clock=fake.now)
    gateway = LiveGateway(v,tr,quota,attempts=attempts,sleeper=fake.sleep,clock=fake.now,max_wait=max_wait)
    return gateway,tr,fake


def arm(v,cid,store,platform,path,n=3):
    v.grant_live_permit(cid,store,platform,path,confirmation='I_AUTHORIZE_READ_ONLY_CANARY',max_calls=n)


def ozon_response():
    return httpx.Response(200,json={'accruals':[{'accrual_id':'a1','accrued':'-9.00'}],'last_id':''})


def test_disabled_environment_never_calls_http(env):
    v,ids,stage=env
    calls=[]
    gateway,tr,_=gw(env,lambda request: calls.append(1) or ozon_response())
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    with pytest.raises(LiveDisabled):
        OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    assert calls==[]
    tr.client.close()


def test_enabled_without_permit_does_not_dispatch(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    calls=[]
    gateway,tr,_=gw(env,lambda request: calls.append(1) or ozon_response())
    with pytest.raises(SecurityError,match='permit'):
        OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    assert calls==[]
    tr.client.close()


def test_ozon_guarded_canonical_post_request(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    requests=[]
    def handler(req):
        requests.append(req)
        return ozon_response()
    gateway,tr,_=gw(env,handler)
    res=OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    assert res['status']=='complete' and res['events']==1
    assert requests[0].method=='POST'
    assert requests[0].url.host=='api-seller.ozon.ru'
    assert requests[0].url.path==OZON_BY_DAY
    assert requests[0].headers['Api-Key']=='TEST_SECRET_NOT_REAL'
    assert requests[0].headers['Client-Id']=='123456'
    assert json.loads(requests[0].content)=={'date':'2026-11-01','last_id':''}
    assert b'TEST_SECRET_NOT_REAL' not in stage.path.read_bytes()
    assert v.live_permit_status()[0]['used']==1
    tr.client.close()


def test_wb_separate_store_tokens_and_204(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    requests=[]
    def handler(req):
        requests.append(req)
        return httpx.Response(200,json=[{'rrdId':123,'retailAmount':'12.00'}]) if len(requests)%2 else httpx.Response(204)
    gateway,tr,clock=gw(env,handler)
    for sid in ('wb1','wb2'):
        arm(v,ids[sid],sid,'WB',WB_DETAILS,n=2)
        result=OfflineSync(v,gateway,stage).wb_period(ids[sid],sid,'2026-11-01','2026-11-07')
        assert result['status']=='complete'
    assert len(requests)==4
    assert all(r.url.host=='finance-api.wildberries.ru' for r in requests)
    assert requests[0].headers['Authorization']=='WB_FAKE_NOT_REAL'
    assert requests[2].headers['Authorization']=='WB_FAKE_NOT_REAL_2'
    assert all(r.url.path==WB_DETAILS for r in requests)
    assert clock.t>=5060
    tr.client.close()


@pytest.mark.parametrize('scope,target', [('global','*'),('platform','Ozon'),('store','oz1')])
def test_emergency_stops_always_block(env,monkeypatch,scope,target):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    v.set_stop(scope,target,True)
    calls=[]
    gateway,tr,_=gw(env,lambda req: calls.append(1) or ozon_response())
    with pytest.raises(Stopped): OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    assert calls==[]
    tr.client.close()


def test_emergency_stop_between_pages(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY,n=3)
    calls=[]
    def handler(req):
        calls.append(1)
        v.set_stop('global','*',True)
        return httpx.Response(200,json={'accruals':[{'accrual_id':'a1'}],'last_id':'another'})
    gateway,tr,_=gw(env,handler)
    with pytest.raises(Stopped): OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    assert len(calls)==1
    assert stage.checkpoint('oz1','Ozon','2026-11-01')==('another',False)
    tr.client.close()


@pytest.mark.parametrize('code',[401,403])
def test_auth_error_quarantines_credential(env,monkeypatch,code):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    calls=[]
    gateway,tr,_=gw(env,lambda req: calls.append(1) or httpx.Response(code,json={'token':'PRIVATE_SECRET'}))
    with pytest.raises(ExternalAPIError) as ex:
        OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    assert str(ex.value).find('PRIVATE_SECRET')<0
    assert len(calls)==1
    with pytest.raises(Stopped): v.check(ids['oz1'])
    assert stage.count('oz1')==0
    tr.client.close()


def test_429_retry_after_and_attempt_budget(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY,n=2)
    calls=[]
    def handler(req):
        calls.append(1)
        if len(calls)==1:
            return httpx.Response(429,headers={'Retry-After':'4'},json={'secret':'PRIVATE'})
        return ozon_response()
    gateway,tr,clock=gw(env,handler,attempts=2)
    assert OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')['events']==1
    assert len(calls)==2 and clock.t>=5004
    assert v.live_permit_status()[0]['used']==2
    tr.client.close()


def test_exhausted_permit_blocks_retry(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY,n=1)
    calls=[]
    gateway,tr,_=gw(env,lambda req: calls.append(1) or httpx.Response(429),attempts=3)
    with pytest.raises(SecurityError,match='permit'):
        OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    assert len(calls)==1
    tr.client.close()


def test_redirect_never_followed_even_custom_client_with_redirects(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    called=[]
    def handler(req):
        called.append(req.url.host)
        return httpx.Response(302,headers={'Location':'https://attacker.example/steal'})
    gateway,tr,_=gw(env,handler)
    with pytest.raises(ExternalAPIError) as ex:
        OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    assert ex.value.status==302 and called==['api-seller.ozon.ru']
    tr.client.close()


def test_wrong_store_cannot_access_credential_or_permit(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['wb1'],'wb1','WB',WB_DETAILS)
    calls=[]
    gateway,tr,_=gw(env,lambda req: calls.append(1) or httpx.Response(204))
    with pytest.raises(SecurityError):
        OfflineSync(v,gateway,stage).wb_period(ids['wb1'],'wb2','2026-11-01','2026-11-07')
    assert calls==[]
    tr.client.close()


def test_unknown_or_write_endpoint_blocked_without_network(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    calls=[]
    gateway,tr,_=gw(env,lambda req: calls.append(1) or ozon_response())
    for path in ('https://evil.example','/v1/product/import/prices','/v3/finance/transaction/list',
                 '/v1/finance/accrual/by-day/../delete'):
        with pytest.raises(SecurityError):gateway.request(ids['oz1'],'oz1','Ozon',path,{'date':'2026-11-01','last_id':''})
    assert calls==[]
    tr.client.close()


@pytest.mark.parametrize('status',[400,402,404,409,422])
def test_nonretryable_errors(env,monkeypatch,status):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    calls=[]
    gateway,tr,_=gw(env,lambda req: calls.append(1) or httpx.Response(status,json={'secret':'PRIVATE'}),attempts=3)
    with pytest.raises(ExternalAPIError): OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    assert len(calls)==1
    tr.client.close()


def test_provider_error_too_large_response_rejected(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    gateway,tr,_=gw(env,lambda req:httpx.Response(200,headers={'Content-Length':'20000000'},content=b'{}'))
    with pytest.raises(ContractError,match='maximum size'):
        OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    tr.client.close()


def test_expiry_unknown_rejected_for_live(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    other=v.add('oz1','Ozon','missing_exp','FAKE',client_id='99')
    with pytest.raises(SecurityError,match='expiration date'):
        v.grant_live_permit(other,'oz1','Ozon',OZON_BY_DAY,confirmation='I_AUTHORIZE_READ_ONLY_CANARY')
    v.set_expiry(other,'2099-12-31')
    assert any(x['credential_id']==other and x['expires_on']=='2099-12-31' for x in v.expiry_notifications())


def test_expired_key_is_blocked_offline_too(env):
    v,ids,stage=env
    v.set_expiry(ids['oz1'],'2000-01-01')
    with pytest.raises(Stopped): v.check(ids['oz1'])
    assert any(x['status']=='expired' for x in v.expiry_notifications())


def test_rotate_requires_new_expiry_before_next_ozon_live(env):
    v,ids,stage=env
    v.rotate(ids['oz1'],'ANOTHER_SYNTH_TOKEN')
    with pytest.raises(SecurityError,match='expiration'):
        v.require_live_expiry(ids['oz1'])
    v.set_expiry(ids['oz1'],'2099-01-01')
    v.require_live_expiry(ids['oz1'])


def test_live_disarm_blocks_next_call(env,monkeypatch):
    v,ids,stage=env
    monkeypatch.setenv(LIVE_OPT_IN_ENV,LIVE_OPT_IN_VALUE)
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    v.disable_live_permit(ids['oz1'])
    calls=[]
    gateway,tr,_=gw(env,lambda req:calls.append(1) or ozon_response())
    with pytest.raises(SecurityError):OfflineSync(v,gateway,stage).ozon_day(ids['oz1'],'oz1','2026-11-01')
    assert not calls
    tr.client.close()


def test_quota_shared_by_store_including_across_instances(tmp_path):
    clock=Clock()
    q1=DurableQuota(tmp_path/'quota.db',{'WB':60.0,'Ozon':2.0},clock=clock.now)
    q2=DurableQuota(tmp_path/'quota.db',{'WB':60.0,'Ozon':2.0},clock=clock.now)
    assert q1.reserve('WB','a')==0
    assert q2.reserve('WB','a')==60
    assert q1.reserve('WB','b')==0
    with pytest.raises(QuotaDeferred):q2.reserve('WB','a',max_wait=3)
    # No phantom reservation on rejection; clock advances 120s, the original slots expire.
    clock.sleep(121)
    assert q2.reserve('WB','a')==0


def test_safe_body_schema_fail_closed():
    with pytest.raises(ContractError):_strict_body('Ozon',OZON_BY_DAY,{'date':'20261101','last_id':''})
    with pytest.raises(ContractError):_strict_body('WB',WB_DETAILS,{'dateFrom':'2026-11-01','dateTo':'2026-11-07','rrdId':0})
    with pytest.raises(ContractError):_strict_body('WB',WB_DETAILS,{'dateFrom':'2026-11-01','dateTo':'2026-11-07','rrdId':0,'limit':1,'period':'daily','evil':True})


def test_retry_after_parses_float_and_http_date():
    assert _retry_after('12.5')==12.5
    assert _retry_after('not a date') is None
    assert _retry_after('Thu, 01 Jan 1970 00:00:10 GMT', now=lambda: __import__('datetime').datetime(1970,1,1,tzinfo=__import__('datetime').timezone.utc))==10


def test_cli_preflight_offline_without_network(env,monkeypatch,capsys):
    v,ids,stage=env
    monkeypatch.delenv(LIVE_OPT_IN_ENV,raising=False)
    args=['--db',str(v.store_db),'--vault',str(v.dir),'--staging',str(stage.path),
          'preflight','--credential',ids['oz1'],'--store','oz1','--provider','Ozon']
    assert live_cli.run(args)==0
    stdout=capsys.readouterr().out
    assert 'OFFLINE_PREFLIGHT_PASS' in stdout and 'TEST_SECRET_NOT_REAL' not in stdout
    assert '"network_used": false' in stdout


def test_vault_security_log_not_containing_secrets(env):
    v,ids,stage=env
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    assert 'TEST_SECRET_NOT_REAL' not in repr(v.audit(100))
    assert 'TEST_SECRET_NOT_REAL' not in repr(v.live_permit_status())

@pytest.mark.parametrize('scope,target', [('global','*'),('platform','Ozon'),('store','oz1')])
def test_emergency_disarms_permit_even_after_resume(env,scope,target):
    v,ids,stage=env
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    v.set_stop(scope,target,True)
    v.set_stop(scope,target,False)
    assert v.live_permit_status()==[]


def test_rotation_disarms_old_permit(env):
    v,ids,stage=env
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    v.rotate(ids['oz1'],'NEW_FAKE_KEY',expires_on='2099-12-31')
    assert v.live_permit_status()==[]


def test_expiry_edit_disarms_existing_permit(env):
    v,ids,stage=env
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    v.set_expiry(ids['oz1'],'2099-12-31')
    assert v.live_permit_status()==[]


def test_revoked_key_clears_permit_and_ciphertext(env):
    v,ids,stage=env
    arm(v,ids['oz1'],'oz1','Ozon',OZON_BY_DAY)
    v.revoke_and_purge(ids['oz1'])
    assert v.live_permit_status()==[]
    with pytest.raises(Stopped):v.check(ids['oz1'])
