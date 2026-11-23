import json
import pytest
from fastapi.testclient import TestClient
from app.server import create_app
from app.local_auth import set_password
from test_finance_v036 import report,control,batch,event

@pytest.fixture
def client(tmp_path):
    path=tmp_path/'api.db';app=create_app(path,test_mode=True)
    set_password(path,'TEST_ONLY_LOCAL_PASSPHRASE')
    with TestClient(app) as c:
        login=c.post('/api/auth/login',json={'passphrase':'TEST_ONLY_LOCAL_PASSPHRASE'})
        c.headers['X-AIPO-CSRF']=login.json()['csrf']
        yield c

def store(client,provider='WB'):
    return client.post('/api/stores',json={'name':'Synthetic store','platform':provider}).json()['id']

def post(client,sid,route,payload,data=None):
    return client.post(f'/api/stores/{sid}/finance/{route}',files={'file':('report.json',json.dumps(payload),'application/json')},data=data)

def test_api_report_control_batch_and_journal(client):
    sid=store(client)
    assert post(client,sid,'reports',report([event(batch_id='b1')])).status_code==200
    assert post(client,sid,'batches',batch()).status_code==200
    result=post(client,sid,'controls',control('1000.00',{'sale':'1000.00'},{'p1':'1000.00'}),{'report_id':'r1'})
    assert result.status_code==200 and result.json()['contribution_kopeks']=='80000'
    rows=client.get(f'/api/stores/{sid}/finance/journal').json()['events']
    assert rows[0]['event_id']=='s1' and len(rows[0]['source_hash'])==64
    assert 'payload' not in rows[0] and rows[0]['amount_kopeks']=='100000'
    assert client.get(f'/api/stores/{sid}/finance').json()['unique_events']==1

def test_api_auth_csrf_and_scope(client):
    sid=store(client);other=store(client)
    post(client,sid,'reports',report())
    assert client.get(f'/api/stores/{other}/finance/journal').json()['events']==[]
    assert post(client,other,'controls',control(),{'report_id':'r1'}).status_code==422
    assert client.get('/api/stores/unknown/finance').status_code==404
    client.headers.pop('X-AIPO-CSRF')
    assert post(client,sid,'reports',report()).status_code==403
    client.cookies.clear()
    assert client.get(f'/api/stores/{sid}/finance').status_code==401

def test_api_invalid_duplicate_and_large_json(client):
    sid=store(client)
    route=f'/api/stores/{sid}/finance/reports'
    for blob in [b'{"x":1,"x":2}',b'not json',b'{"x":NaN}']:
        assert client.post(route,files={'file':('x.json',blob)}).status_code==422
    assert client.post(route,files={'file':('x.json',b' '*(5*1024*1024+1))}).status_code==413

def test_unknown_type_visible_while_other_ui_works(client):
    sid=store(client)
    post(client,sid,'reports',report([event(kind='NEW_TYPE')]))
    r=client.get(f'/api/stores/{sid}/finance').json()['reports'][0]
    assert r['label']=='Расхождение' and len(r['quarantine'])==1
    assert client.get(f'/api/stores/{sid}/dashboard').status_code==200
    assert client.get('/api/health').json()['version']=='0.3.7-security-foundation'

def test_exact_browser_totals_above_safe_integer(client):
    sid=store(client)
    events=[event(str(i),amount='1000000000000.00') for i in range(100)]
    assert post(client,sid,'reports',report(events)).status_code==200
    r=client.get(f'/api/stores/{sid}/finance').json()
    assert r['unique_accruals_kopeks']=='10000000000000000'
    assert r['reports'][0]['category_kopeks']['sale']=='10000000000000000'
