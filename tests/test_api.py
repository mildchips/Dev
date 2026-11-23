import io
import pytest
from fastapi.testclient import TestClient
from app.server import create_app
from app.local_auth import set_password

CSV=b'date;sku;category;amount;quantity;operation_id\n2026-11-01;A;sale;1000,00;1;SALE-1\n2026-11-01;A;commission;-100,00;;COM-1\n'

@pytest.fixture
def client(tmp_path):
    db=tmp_path/'test.db'
    app=create_app(db,test_mode=True)
    set_password(db,'unittest-passphrase-1234')
    with TestClient(app) as c:
        res=c.post('/api/auth/login',json={'passphrase':'unittest-passphrase-1234'})
        assert res.status_code==200,res.text
        c.headers.update({'X-AIPO-CSRF':res.json()['csrf']})
        yield c

def store(client,name='Store'):
    r=client.post('/api/stores',json={'name':name,'platform':'WB'})
    assert r.status_code==201
    return r.json()['id']

def upload(client,id,data=CSV,filename='test.csv',extra=None):
    return client.post(f'/api/stores/{id}/imports',files={'file':(filename,io.BytesIO(data),'text/csv')},data=extra or {})

def test_local_health(client):
    assert client.get('/api/health').json()['marketplace_api'] is False
    assert client.get('/').status_code==200

def test_full_import_cost_audit(client):
    sid=store(client)
    res=upload(client,sid)
    assert res.status_code==200,res.text
    assert res.json()['count_rows']==2
    s=client.get(f'/api/stores/{sid}/dashboard').json()
    assert s['summary']['contribution_rub'] is None
    assert s['summary']['missing_cogs_skus']==['A']
    add=client.put(f'/api/stores/{sid}/costs',json={'sku':'A','unit_cost_rub':'200'})
    assert add.status_code==200
    s=client.get(f'/api/stores/{sid}/dashboard').json()
    assert s['summary']['contribution_rub']==700

def test_repeated_file_prevented(client):
    sid=store(client)
    assert upload(client,sid).status_code==200
    assert upload(client,sid).status_code==409
    assert client.get(f'/api/stores/{sid}/dashboard').json()['summary']['entries']==2

def test_control_total_mismatch_rolls_back(client):
    sid=store(client)
    err=upload(client,sid,extra={'control_sum_rub':'899.99'})
    assert err.status_code==422
    assert client.get(f'/api/stores/{sid}/dashboard').json()['summary']['entries']==0
    assert upload(client,sid,extra={'control_sum_rub':'900.00'}).status_code==200

def test_two_store_isolation(client):
    a=store(client,'Alpha');b=store(client,'Beta')
    upload(client,a)
    assert client.get(f'/api/stores/{b}/dashboard').json()['summary']['entries']==0
    assert client.get(f'/api/stores/{b}/ledger').json()['entries']==[]
    assert client.get(f'/api/stores/{a}/dashboard').json()['summary']['entries']==2

def test_review_is_revision_scoped(client):
    sid=store(client)
    upload(client,sid)
    item=client.get(f'/api/stores/{sid}/dashboard').json()['opportunities'][0]
    r=client.post(f"/api/stores/{sid}/reviews/{item['key']}",json={'status':'confirmed'})
    assert r.status_code==200,r.text
    item2=client.get(f'/api/stores/{sid}/dashboard').json()['opportunities'][0]
    assert item2['status']=='confirmed'
    client.put(f'/api/stores/{sid}/costs',json={'sku':'A','unit_cost_rub':'700'})
    # Old review cannot falsely survive re-calculation.
    findings=client.get(f'/api/stores/{sid}/dashboard').json()['opportunities']
    assert all(f['status']=='detected' for f in findings)

def test_demo_is_marked_synthetic(client):
    sid=client.post('/api/demo').json()['id']
    d=client.get(f'/api/stores/{sid}/dashboard').json()
    assert d['store']['is_demo']==1
    assert d['summary']['entries']>0
    assert 'NEGATIVE_SKU' in {f['code'] for f in d['opportunities']}

def test_cannot_access_foreign_or_missing_store(client):
    assert client.get('/api/stores/missing/dashboard').status_code==404
    assert client.put('/api/stores/missing/costs',json={'sku':'A','unit_cost_rub':'1'}).status_code==404

def test_no_client_status_fabrication(client):
    sid=store(client);upload(client,sid)
    f=client.get(f'/api/stores/{sid}/dashboard').json()['opportunities'][0]
    assert client.post(f"/api/stores/{sid}/reviews/{f['key']}",json={'status':'measured'}).status_code==422

def test_csv_export_is_available(client):
    sid=store(client);upload(client,sid)
    response=client.get(f'/api/stores/{sid}/ledger.csv')
    assert response.status_code==200
    assert 'SALE-1' in response.content.decode('utf-8-sig')
