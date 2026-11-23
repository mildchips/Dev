import json
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

from saas_lab.core import Lab, Denied
from saas_lab.crypto import seal, unseal
from saas_lab.recovery import backup, restore
from saas_lab.server import create_app, COOKIE

PASSWORD='Synthetic-only-Password-037!'


@pytest.fixture
def setup_lab(tmp_path):
    now=[1000.0]
    lab=Lab(tmp_path/'lab.db',clock=lambda:now[0])
    uid,tid=lab.provision('owner-a',PASSWORD,organization=True)
    token,csrf=lab.login('owner-a',PASSWORD)
    a=lab.authenticate(token)
    _,t2=lab.provision('owner-b',PASSWORD,organization=True)
    token2,_=lab.login('owner-b',PASSWORD)
    b=lab.authenticate(token2)
    s1=lab.add_store(a,tid,'SYNTHETIC A1')
    s2=lab.add_store(a,tid,'SYNTHETIC A2')
    sb=lab.add_store(b,t2,'SYNTHETIC B')
    return lab,now,a,tid,s1,s2,b,t2,sb,token,csrf


def member(lab,a,tid,store,role='manager',login='synthetic-manager'):
    uid,_=lab.provision(login,PASSWORD)
    token,_=lab.login(login,PASSWORD)
    actor=lab.authenticate(token)
    invitation=lab.invite(a,tid,login,role,[store])
    lab.accept(actor,invitation)
    return actor,invitation


def test_scoped_membership_and_no_invite_replay(setup_lab):
    lab,_,a,t,s,s2,b,t2,sb,*_=setup_lab
    m,invite=member(lab,a,t,s)
    assert [x['id'] for x in lab.stores(m,t)]==[s]
    assert lab.tenants(m)[0]['role']=='manager'
    for action in (lambda:lab.accept(m,invite),lambda:lab.stores(m,t2),
                   lambda:lab.add_store(m,t,'BAD'),lambda:lab.enqueue(m,t,s2,'x'),
                   lambda:lab.invite(m,t,'x','owner',[s]),lambda:lab.audit(m,t)):
        with pytest.raises(Denied):action()


def test_invite_bound_to_login_expiry_and_role(setup_lab):
    lab,now,a,t,s,_,b,*_=setup_lab
    invite=lab.invite(a,t,'owner-b','auditor',[s],ttl=60)
    with pytest.raises(Denied):lab.accept(a,invite)
    now[0]+=61
    with pytest.raises(Denied):lab.accept(b,invite)
    for role in ('owner','support_limited','system_worker','unknown'):
        with pytest.raises(Denied):lab.invite(a,t,'owner-b',role,[s])


def test_auditor_cannot_write_or_enqueue(setup_lab):
    lab,_,a,t,s,*_=setup_lab
    m,_=member(lab,a,t,s,role='auditor')
    assert lab.stores(m,t)
    with pytest.raises(Denied):lab.enqueue(m,t,s,'audit')
    with pytest.raises(Denied):lab.stop(m,t)


def test_revocation_closes_read_export_jobs_and_old_actor(setup_lab):
    lab,_,a,t,s,*_=setup_lab
    m,_=member(lab,a,t,s)
    jid=lab.enqueue(m,t,s,'idem')
    claim=lab.claim()
    lab.revoke(a,t,m.user_id)
    for fn in (lambda:lab.stores(m,t),lambda:lab.job(m,t,jid),lambda:lab.finish(claim),lambda:lab.enqueue(m,t,s,'new')):
        with pytest.raises(Denied):fn()
    assert lab.job(a,t,jid)['state']=='CANCELLED'
    assert lab.claim() is None


def test_membership_expiry_at_worker_commit(setup_lab):
    lab,now,a,t,s,*_=setup_lab
    m,_=member(lab,a,t,s)
    jid=lab.enqueue(m,t,s,'idem')
    c=lab.claim()
    with lab.db() as db:db.execute('UPDATE members SET expires=? WHERE tenant=? AND user_id=?',(now[0]+1,t,m.user_id))
    now[0]+=2
    with pytest.raises(Denied):lab.finish(c)
    now[0]+=31
    assert lab.claim() is None
    assert lab.job(a,t,jid)['state']=='CANCELLED'


def test_concurrent_idempotency_and_worker_fencing(setup_lab):
    lab,_,a,t,s,*_=setup_lab
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids=list(pool.map(lambda _:lab.enqueue(a,t,s,'same'),range(24)))
    assert len(set(ids))==1
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims=list(pool.map(lambda _:lab.claim(),range(16)))
    assert len([c for c in claims if c])==1
    claim=next(c for c in claims if c)
    lab.finish(claim)
    with pytest.raises(Denied):lab.finish(claim)
    assert lab.job(a,t,ids[0])['state']=='DONE'


def test_crash_reopen_and_stale_worker(setup_lab):
    lab,now,a,t,s,*_=setup_lab
    jid=lab.enqueue(a,t,s,'crash')
    stale=lab.claim()
    now[0]+=31
    reopened=Lab(lab.path,clock=lambda:now[0])
    fresh=reopened.claim()
    assert fresh['id']==stale['id'] and fresh['lease']!=stale['lease']
    with pytest.raises(Denied):reopened.finish(stale)
    reopened.finish(fresh)
    assert reopened.job(a,t,jid)['attempts']==2


def test_retry_dead_letter_and_backoff(setup_lab):
    lab,now,a,t,s,*_=setup_lab
    jid=lab.enqueue(a,t,s,'retry')
    for _ in range(3):
        c=lab.claim();assert c
        lab.finish(c,success=False)
        assert lab.claim() is None
        now[0]+=10
    assert lab.job(a,t,jid)['state']=='DEAD'


def test_expired_lease_eventually_dead(setup_lab):
    lab,now,a,t,s,*_=setup_lab
    jid=lab.enqueue(a,t,s,'crash-loop')
    for _ in range(3):
        assert lab.claim()
        now[0]+=31
    assert lab.claim() is None
    assert lab.job(a,t,jid)['state']=='DEAD'


def test_stop_mid_task_and_tenant_independence(setup_lab):
    lab,_,a,t,s,_,b,t2,sb,*_=setup_lab
    jid=lab.enqueue(a,t,s,'stop')
    claim=lab.claim()
    lab.stop(a,t)
    with pytest.raises(Denied):lab.finish(claim)
    with pytest.raises(Denied):lab.enqueue(a,t,s,'new')
    other=lab.enqueue(b,t2,sb,'other')
    c=lab.claim();assert c['id']==other
    lab.finish(c)
    lab.stop(a,t,False)
    c=lab.claim();assert c['id']==jid
    with pytest.raises(Denied):lab.finish(claim)
    lab.finish(c)


def test_csrf_logout_expiry_and_password_lockout(setup_lab):
    lab,now,a,t,s,_,b,t2,sb,token,csrf=setup_lab
    with pytest.raises(Denied):lab.authenticate(token,'bad',mutation=True)
    assert lab.authenticate(token,csrf,mutation=True)==a
    lab.logout(a)
    with pytest.raises(Denied):lab.stores(a,t)
    for _ in range(5):
        with pytest.raises(Denied):lab.login('owner-a','wrong')
    with pytest.raises(Denied):lab.login('owner-a',PASSWORD)
    now[0]+=301
    tok,_=lab.login('owner-a',PASSWORD)
    now[0]+=1801
    with pytest.raises(Denied):lab.authenticate(tok)


def test_no_password_tokens_in_storage_and_append_only_audit(setup_lab):
    lab,_,a,t,*rest=setup_lab
    token=rest[-2]
    with lab.db() as db:
        assert db.execute('SELECT password FROM users').fetchone()[0]!=PASSWORD.encode()
        assert db.execute('SELECT hash FROM sessions WHERE hash=?',(token,)).fetchone() is None
        for query in ('UPDATE audit SET event="bad"','DELETE FROM audit'):
            with pytest.raises(Exception):db.execute(query)
    assert lab.audit(a,t)


@pytest.mark.parametrize('operation',['stores','jobs','export','invite','revoke','audit','stop'])
def test_http_cross_tenant_and_store_idor(tmp_path,operation):
    app=create_app(tmp_path/'api.db',test_mode=True)
    lab=app.state.lab
    uid,t=lab.provision('owner-a',PASSWORD,organization=True)
    _,tb=lab.provision('owner-b',PASSWORD,organization=True)
    bt,_=lab.login('owner-b',PASSWORD);b=lab.authenticate(bt)
    sb=lab.add_store(b,tb,'SYNTHETIC B');jid=lab.enqueue(b,tb,sb,'b')
    with TestClient(app) as c:
        csrf=c.post('/api/login',json={'login':'owner-a','password':PASSWORD}).json()['csrf']
        routes={
            'stores':('GET',f'/api/tenants/{tb}/stores',None),
            'jobs':('POST',f'/api/tenants/{t}/jobs',{'store':sb,'idempotency_key':'bad'}),
            'export':('GET',f'/api/tenants/{tb}/jobs/{jid}/export',None),
            'invite':('POST',f'/api/tenants/{tb}/invites',{'login':'owner-a','role':'manager','stores':[sb]}),
            'revoke':('DELETE',f'/api/tenants/{tb}/members/{uid}',None),
            'audit':('GET',f'/api/tenants/{tb}/audit',None),
            'stop':('POST',f'/api/tenants/{tb}/stop',None)}
        method,path,data=routes[operation]
        r=c.request(method,path,json=data,headers={'x-aipo-csrf':csrf})
        assert r.status_code==403
        assert 'SYNTHETIC B' not in r.text


def test_http_anonymous_csrf_host_origin_remote_denial(tmp_path):
    app=create_app(tmp_path/'api.db',test_mode=True)
    _,t=app.state.lab.provision('owner-a',PASSWORD,organization=True)
    with TestClient(app) as c:
        assert c.get('/api/me').status_code==401
        response=c.post('/api/login',json={'login':'owner-a','password':PASSWORD})
        assert 'httponly' in response.headers['set-cookie'].lower()
        csrf=response.json()['csrf']
        assert c.post(f'/api/tenants/{t}/stores',json={'label':'X'}).status_code==401
        assert c.get('/api/me',headers={'Host':'evil.example'}).status_code==400
        assert c.post(f'/api/tenants/{t}/stop',headers={'x-aipo-csrf':csrf,'origin':'https://evil.example'}).status_code==403
        assert c.get('/api/me',headers={'sec-fetch-site':'cross-site'}).status_code==403
        assert c.post(f'/api/tenants/{t}/stores',json={'label':'X'},headers={'x-aipo-csrf':csrf}).status_code==200
        assert c.post('/api/logout',headers={'x-aipo-csrf':csrf}).status_code==200
        assert c.get('/api/me').status_code==401
    actual=create_app(tmp_path/'remote.db')
    with TestClient(actual,client=('203.0.113.1',123)) as c:
        assert c.get('/',headers={'Host':'localhost'}).status_code==403


class FakeKMS:
    def __init__(self):self.keys={'test-v1':secrets.token_bytes(32),'test-v2':secrets.token_bytes(32)};self.available=True
    def wrap(self,key_id,plaintext_key,context):
        if not self.available:raise RuntimeError('KMS unavailable')
        nonce=secrets.token_bytes(12)
        return nonce+AESGCM(self.keys[key_id]).encrypt(nonce,plaintext_key,context)
    def unwrap(self,key_id,wrapped_key,context):
        if not self.available:raise RuntimeError('KMS unavailable')
        return AESGCM(self.keys[key_id]).decrypt(wrapped_key[:12],wrapped_key[12:],context)


@pytest.mark.parametrize('field,value',[('tenant','other'),('store','other'),('provider','Ozon'),('version',2),('key_id','test-v2')])
def test_envelope_rejects_scope_substitution(field,value):
    kms=FakeKMS();scope=dict(tenant='A',store='S',provider='WB',version=1,key_id='test-v1')
    envelope=seal(kms,**scope,plaintext=b'SYNTHETIC-ONLY-TOKEN')
    assert b'SYNTHETIC-ONLY-TOKEN' not in json.dumps(envelope).encode()
    assert unseal(kms,envelope,**scope)==b'SYNTHETIC-ONLY-TOKEN'
    with pytest.raises(Exception):unseal(kms,envelope,**(scope|{field:value}))


def test_kms_outage_and_rotation_no_plaintext_fallback():
    kms=FakeKMS();scope=dict(tenant='A',store='S',provider='WB',version=1,key_id='test-v1')
    old=seal(kms,**scope,plaintext=b'SYNTHETIC')
    new_scope=scope|{'version':2,'key_id':'test-v2'}
    new=seal(kms,**new_scope,plaintext=unseal(kms,old,**scope))
    assert unseal(kms,new,**new_scope)==b'SYNTHETIC'
    kms.available=False
    with pytest.raises(RuntimeError):seal(kms,**scope,plaintext=b'SYNTHETIC')
    with pytest.raises(RuntimeError):unseal(kms,old,**scope)


def test_encrypted_backup_recovery_and_session_job_fencing(setup_lab,tmp_path):
    lab,now,a,t,s,*rest=setup_lab
    token=rest[-2]
    jid=lab.enqueue(a,t,s,'restore');claim=lab.claim()
    path=tmp_path/'lab.aipobak';password='Synthetic backup password 037'
    meta=backup(lab,path,password)
    assert meta['bytes']>0 and path.stat().st_mode&0o777==0o600
    assert b'SYNTHETIC A1' not in path.read_bytes()
    with pytest.raises(Exception):restore(path,tmp_path/'wrong.db','wrong-password-long')
    assert not (tmp_path/'wrong.db').exists()
    target=tmp_path/'restored.db'
    assert restore(path,target,password)['sessions_revoked']
    recovered=Lab(target,clock=lambda:now[0])
    with pytest.raises(Denied):recovered.authenticate(token)
    token,_=recovered.login('owner-a',PASSWORD);actor=recovered.authenticate(token)
    assert recovered.stores(actor,t)
    assert recovered.job(actor,t,jid)['state']=='PAUSED'
    assert recovered.claim() is None
    with pytest.raises(Denied):recovered.finish(claim)
    with pytest.raises(FileExistsError):restore(path,target,password)
    recovered.stop(actor,t,False)
    recovered.finish(recovered.claim())
    assert recovered.job(actor,t,jid)['state']=='DONE'


def test_backup_tampering_no_restore_and_no_overwrite(setup_lab,tmp_path):
    lab,*_=setup_lab
    path=tmp_path/'lab.aipobak';password='Synthetic backup password 037'
    backup(lab,path,password)
    with pytest.raises(FileExistsError):backup(lab,path,password)
    damaged=bytearray(path.read_bytes());damaged[-1]^=1;path.write_bytes(damaged)
    with pytest.raises(Exception):restore(path,tmp_path/'restored.db',password)
    assert not (tmp_path/'restored.db').exists()


def test_old_snapshot_never_resurrects_delegated_membership(setup_lab,tmp_path):
    lab,now,a,t,s,*_=setup_lab
    m,_=member(lab,a,t,s)
    snapshot=tmp_path/'old.aipobak';password='Synthetic backup password 037'
    backup(lab,snapshot,password)
    lab.revoke(a,t,m.user_id)
    target=tmp_path/'old-restored.db'
    assert restore(snapshot,target,password)['delegates_disabled']
    recovered=Lab(target,clock=lambda:now[0])
    token,_=recovered.login('synthetic-manager',PASSWORD)
    delegate=recovered.authenticate(token)
    assert recovered.tenants(delegate)==[]
    with pytest.raises(Denied):recovered.stores(delegate,t)


def test_stop_resume_cannot_bypass_max_attempts(setup_lab):
    lab,_,a,t,s,*_=setup_lab
    jid=lab.enqueue(a,t,s,'stop-loop')
    for _ in range(3):
        assert lab.claim()
        lab.stop(a,t);lab.stop(a,t,False)
    assert lab.claim() is None
    assert lab.job(a,t,jid)['state']=='DEAD'
