import sqlite3
import pytest
import httpx
from app.storage import initialize
from app.integration_security import Vault, initialize_key, Stopped
from app.offline_sync import Gateway, ContractError, ExternalAPIError, CheckpointStore, OfflineSync
from app.httpx_mock_connector import HttpxMockTransport

@pytest.fixture
def env(tmp_path):
    db=tmp_path/'store.db'
    initialize(db)
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO stores(id,name,platform) VALUES('w1','WB','WB')")
        con.execute("INSERT INTO stores(id,name,platform) VALUES('o1','Ozon','Ozon')")
    initialize_key(tmp_path/'vault')
    v=Vault(tmp_path/'vault',db)
    ids={'w1':v.add('w1','WB','wb','SYNTH-WB-SECRET'),
         'o1':v.add('o1','Ozon','oz','SYNTH-OZ-SECRET',client_id='00099')}
    return v,ids,CheckpointStore(tmp_path/'stage.sqlite')


def test_httpx_wire_ozon_auth_path_and_json(env):
    v,ids,stage=env
    inspected=[]
    def handler(request):
        inspected.append((request.url.host, request.url.path, request.headers['api-key'],
                          request.headers['client-id'],request.read()))
        return httpx.Response(200,json={'last_id':'','accruals':[{'accrual_id':'x1'}]})
    tr=HttpxMockTransport(handler)
    assert OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')['events']==1
    assert inspected[0][0]=='api-seller.ozon.ru'
    assert inspected[0][1]=='/v1/finance/accrual/by-day'
    assert inspected[0][2]=='SYNTH-OZ-SECRET'
    assert inspected[0][3]=='00099'
    assert b'2026-11-01' in inspected[0][4]
    assert 'SECRET' not in str(tr.calls)
    tr.close()


def test_httpx_wire_wb_auth_and_204(env):
    v,ids,stage=env
    visited=[]
    def handler(req):
        visited.append((req.url.host,req.headers.get('authorization')))
        if len(visited)==1:
            return httpx.Response(200,json=[{'rrdId':123,'retailAmount':'99.00','currency':'RUB'}])
        return httpx.Response(204)
    tr=HttpxMockTransport(handler)
    assert OfflineSync(v,Gateway(v,tr),stage).wb_period(ids['w1'],'w1','2026-11-01','2026-11-07')['events']==1
    assert visited[0]==('finance-api.wildberries.ru','SYNTH-WB-SECRET')
    assert len(visited)==2


def test_httpx_no_retry_403_and_no_body_leaks(env):
    v,ids,stage=env
    def handler(_): return httpx.Response(403,json={'key':'SYNTH-OZ-SECRET','message':'forbidden'})
    tr=HttpxMockTransport(handler)
    with pytest.raises(ExternalAPIError) as e:
        OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert 'SECRET' not in str(e.value)
    assert len(tr.calls)==1


def test_httpx_429_headers_and_stop_before_retry(env):
    v,ids,stage=env
    count=[]
    def handler(_):
        count.append(1)
        v.set_stop('platform','Ozon',True)
        return httpx.Response(429,headers={'Retry-After':'6'},json={'message':'overlimit'})
    tr=HttpxMockTransport(handler)
    delays=[]
    with pytest.raises(Stopped):
        OfflineSync(v,Gateway(v,tr,sleep=delays.append),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert delays==[6]
    assert len(count)==1


def test_httpx_bad_json_fails_closed(env):
    v,ids,stage=env
    tr=HttpxMockTransport(lambda req: httpx.Response(200,content=b'<html>blocked</html>'))
    with pytest.raises(ContractError): OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')


def test_httpx_redirect_not_followed(env):
    v,ids,stage=env
    tr=HttpxMockTransport(lambda req: httpx.Response(302,headers={'Location':'https://somewhere.example'}))
    with pytest.raises(ExternalAPIError) as e: OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert e.value.status==302
    assert len(tr.calls)==1


def test_httpx_timeout_bounded(env):
    v,ids,stage=env
    tr=HttpxMockTransport(lambda req: (_ for _ in ()).throw(httpx.ReadTimeout('timeout')))
    with pytest.raises(ExternalAPIError) as e: OfflineSync(v,Gateway(v,tr,attempts=2),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert e.value.status==0 and len(tr.calls)==2
