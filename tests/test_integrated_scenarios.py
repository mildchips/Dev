from fastapi.testclient import TestClient
from app.server import create_app
from app.local_auth import set_password

def logged_in(tmp_path):
    path=tmp_path/'app.db'
    app=create_app(path,test_mode=True)
    set_password(path,'sample-passphrase-very-long')
    client=TestClient(app)
    result=client.post('/api/auth/login',json={'passphrase':'sample-passphrase-very-long'})
    assert result.status_code==200
    client.headers['X-AIPO-CSRF']=result.json()['csrf']
    return client

def scenario(name, units, price):
    return {'label':name,'paid_units':units,'realized_price_rub':str(price),
            'unit_cogs_rub':'500','commission_percent':'15','logistics_unit_rub':'100',
            'storage_unit_rub':'0','advertising_budget_rub':'1000','other_period_costs_rub':'0'}

def test_scenario_only_for_existing_store(tmp_path):
    with logged_in(tmp_path) as client:
        data={'baseline':scenario('baseline',10,1000),'alternative':scenario('alternative',10,1100)}
        assert client.post('/api/stores/missing/scenarios/compare',json=data).status_code==404
        store=client.post('/api/stores',json={'name':'My Shop','platform':'WB'}).json()['id']
        out=client.post(f'/api/stores/{store}/scenarios/compare',json=data)
        assert out.status_code==200,out.text
        obj=out.json()
        assert obj['delta_rub']==850
        assert obj['verified_realized_profit_rub'] is None

def test_cost_revision_blocks_historical_if_not_covered(tmp_path):
    with logged_in(tmp_path) as client:
        store=client.post('/api/stores',json={'name':'My Shop','platform':'WB'}).json()['id']
        content=b'date;sku;category;amount;quantity;operation_id\n2026-09-01;A;sale;1000;1;S1\n2026-10-01;A;sale;1000;1;S2\n'
        assert client.post(f'/api/stores/{store}/imports',files={'file':('ledger.csv',content)}).status_code==200
        p=f'/api/stores/{store}/cost-revisions'
        assert client.put(p,json={'sku':'A','effective_from':'2026-10-01','unit_cost_rub':'800'}).status_code==200
        d=client.get(f'/api/stores/{store}/dashboard').json()
        assert d['summary']['contribution_rub'] is None
        assert d['summary']['missing_cogs_skus']==['A']
        assert client.put(p,json={'sku':'A','effective_from':'2026-09-01','unit_cost_rub':'500'}).status_code==200
        d=client.get(f'/api/stores/{store}/dashboard').json()
        assert d['summary']['cogs_rub']==1300
        assert d['summary']['contribution_rub']==700
        assert len(client.get(p).json()['revisions'])==2

def test_non_authorized_user_cannot_use_cost_revision_or_scenario(tmp_path):
    path=tmp_path/'app.db';app=create_app(path,test_mode=True);set_password(path,'sample-passphrase-very-long')
    with TestClient(app) as client:
        assert client.get('/api/stores/s/cost-revisions').status_code==401
        assert client.put('/api/stores/s/cost-revisions',json={'sku':'A','effective_from':'2026-10-01','unit_cost_rub':'800'}).status_code==401
        assert client.post('/api/stores/s/scenarios/compare',json={'baseline':scenario('a',1,1000),'alternative':scenario('b',2,1000)}).status_code==401

def test_operator_audit_and_confirmation_for_destructive_delete(tmp_path):
    with logged_in(tmp_path) as client:
        store=client.post('/api/stores',json={'name':'Important Shop','platform':'WB'}).json()['id']
        assert client.delete(f'/api/stores/{store}').status_code==422
        assert client.request('DELETE',f'/api/stores/{store}',json={'confirm_name':'Wrong'}).status_code==409
        assert client.get(f'/api/stores/{store}/dashboard').status_code==200
        audit=client.get('/api/local/audit').json()['events']
        assert any(event['action']=='store_created' and event['store_id']==store for event in audit)
        response=client.request('DELETE',f'/api/stores/{store}',json={'confirm_name':'Important Shop'})
        assert response.status_code==200,response.text
        audit=client.get('/api/local/audit').json()['events']
        assert any(event['action']=='store_deleted' and event['store_id']==store for event in audit)

def test_action_plan_does_not_turn_into_fabricated_revenue(tmp_path):
    with logged_in(tmp_path) as client:
        a=client.post('/api/stores',json={'name':'Alpha Shop','platform':'WB'}).json()['id']
        b=client.post('/api/stores',json={'name':'Beta Shop','platform':'Ozon'}).json()['id']
        item=client.post(f'/api/stores/{a}/actions',json={'hypothesis':'Проверить тариф логистики по SKU A'}).json()
        assert item['realized_profit_rub'] is None
        res=client.patch(f"/api/stores/{a}/actions/{item['id']}",json={'status':'implemented'})
        assert res.status_code==200
        assert res.json()['realized_profit_rub'] is None
        assert client.patch(f"/api/stores/{b}/actions/{item['id']}",json={'status':'planned'}).status_code==404
        assert client.get(f'/api/stores/{b}/actions').json()['actions']==[]
        assert client.get(f'/api/stores/{a}/actions').json()['actions'][0]['status']=='implemented'
