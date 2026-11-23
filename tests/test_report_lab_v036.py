import httpx
import pytest
from app.report_lab import ReportLab,WB_LIST,WB_DETAILS,OZ_TYPES,OZ_POSTINGS,OZ_LIST,OZ_REPORT,OZ_POSTING_REPORT
from app.integration_security import Vault,initialize_key,SecurityError,Stopped
from app.offline_sync import ContractError
from app.live_connector import _strict_body
from app.storage import initialize,connect

@pytest.fixture
def env(tmp_path):
    db=tmp_path/'stores.db';initialize(db)
    with connect(db) as c:
        c.executemany('INSERT INTO stores(id,name,platform) VALUES(?,?,?)',[('w1','One','WB'),('w2','Two','WB'),('o1','Ozon','Ozon')])
    initialize_key(tmp_path/'vault');v=Vault(tmp_path/'vault',db)
    return v,{'w1':v.add('w1','WB','WB','FAKE_WB_KEY'),'o1':v.add('o1','Ozon','OZ','FAKE_OZ_KEY',client_id='123')}

def reply(**kwargs):return httpx.Response(200,json={'lab_contract':'aipo.report-lab/1',**kwargs})

def test_wb_catalog_offset_and_64bit_details(env):
    v,ids=env;calls=[];big=2**53+5
    def handler(r):
        body=__import__('json').loads(r.content);calls.append((r.url.path,body))
        assert r.headers['Authorization']=='FAKE_WB_KEY'
        if r.url.path==WB_LIST:return reply(reports=[{'reportId':big}]) if body['offset']==0 else reply(reports=[])
        return reply(rows=[{'rrdId':big,'reportId':big,'forPay':'99.99'}]) if body['rrdId']==0 else httpx.Response(204)
    lab=ReportLab(v,httpx.MockTransport(handler))
    assert lab.wb_reports(ids['w1'],'w1','2026-11-01','2026-11-07',limit=1)==[{'report_id':str(big)}]
    assert lab.wb_details(ids['w1'],'w1','2026-11-01','2026-11-07',report_id=str(big))[0]['rrdId']==str(big)
    assert calls[1][1]['offset']==1 and calls[-1][1]['rrdId']==big
    lab.close()

def test_wb_period_detail_staged_only(env):
    v,ids=env
    lab=ReportLab(v,httpx.MockTransport(lambda r:reply(rows=[{'rrdId':1,'forPay':'1.00'}]) if len(lab.calls)==1 else httpx.Response(204)))
    assert len(lab.wb_details(ids['w1'],'w1','2026-11-01','2026-11-07'))==1
    lab.close()

def test_ozon_new_report_sequence_and_not_formed(env):
    v,ids=env;paths=[]
    def handler(r):
        paths.append(r.url.path)
        assert r.headers['Api-Key']=='FAKE_OZ_KEY' and r.headers['Client-Id']=='123'
        if r.url.path==OZ_LIST:return reply(numbers=['R-001','R-002'])
        if r.url.path==OZ_REPORT:return reply(report_code='standard-code')
        return reply(report_code=None)
    lab=ReportLab(v,httpx.MockTransport(handler));result=lab.ozon_realization(ids['o1'],'o1','2026-11-01','2026-11-07')
    assert paths==[OZ_LIST,OZ_REPORT,OZ_POSTING_REPORT,OZ_REPORT,OZ_POSTING_REPORT]
    assert result['reports'][0]['posting'] is None and result['recognized_financial_events']==0
    lab.close()

def test_ozon_types_no_automatic_mapping_and_postings(env):
    v,ids=env
    lab=ReportLab(v,httpx.MockTransport(lambda r:reply(types=[{'type_id':'UNRECOGNIZED'}]) if r.url.path==OZ_TYPES else reply(links=[{'accrual_id':'a1','posting_id':'p1'}])))
    assert lab.ozon_types(ids['o1'],'o1')['automatic_financial_mapping'] is False
    assert lab.ozon_postings(ids['o1'],'o1',['a1'])=={'a1':'p1'}
    lab.close()

@pytest.mark.parametrize('path,provider',[(WB_LIST,'WB'),(WB_DETAILS+'/123','WB'),(OZ_TYPES,'Ozon'),(OZ_LIST,'Ozon'),(OZ_REPORT,'Ozon'),(OZ_POSTING_REPORT,'Ozon')])
def test_new_routes_cannot_enter_live_transport(path,provider):
    with pytest.raises(SecurityError):_strict_body(provider,path,{})

def test_report_lab_rejects_real_transport(env):
    with pytest.raises(SecurityError):ReportLab(env[0],httpx.HTTPTransport())

def test_scope_and_kill_between_pages(env):
    v,ids=env
    def handler(r):
        v.set_stop('store','w1',True,'TEST')
        return reply(reports=[{'reportId':1}])
    lab=ReportLab(v,httpx.MockTransport(handler))
    with pytest.raises(SecurityError):lab.wb_reports(ids['w1'],'w2','2026-11-01','2026-11-07')
    assert lab.calls==[]
    with pytest.raises(Stopped):lab.wb_reports(ids['w1'],'w1','2026-11-01','2026-11-07')
    assert len(lab.calls)==1;lab.close()

def test_retry_checks_stop(env):
    v,ids=env
    lab=ReportLab(v,httpx.MockTransport(lambda r:httpx.Response(503)),sleep=lambda _:v.set_stop('global','*',True,'TEST'))
    with pytest.raises(Stopped):lab.ozon_types(ids['o1'],'o1')
    assert len(lab.calls)==1;lab.close()

@pytest.mark.parametrize('response',[httpx.Response(200,json={}),reply(reports=[{'reportId':True}]),reply(reports=[{'reportId':1}])])
def test_unknown_schema_ids_and_cursor_loop(env,response):
    v,ids=env;lab=ReportLab(v,httpx.MockTransport(lambda _:response))
    with pytest.raises(ContractError):lab.wb_reports(ids['w1'],'w1','2026-11-01','2026-11-07',max_pages=2)
    lab.close()

def test_incomplete_posting_links(env):
    v,ids=env;lab=ReportLab(v,httpx.MockTransport(lambda _:reply(links=[])))
    with pytest.raises(ContractError):lab.ozon_postings(ids['o1'],'o1',['a1'])
    lab.close()
