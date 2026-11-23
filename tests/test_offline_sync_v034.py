import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app.storage import initialize
from app.integration_security import Vault, SecurityError, Stopped, initialize_key
from app.offline_sync import (
    Gateway, ScriptedTransport, MockResponse, ContractError,
    ExternalAPIError, CheckpointStore, OfflineSync, OZON_BY_DAY, WB_DETAILS,
    extract_ozon, extract_wb
)

@pytest.fixture
def environment(tmp_path):
    db = tmp_path/'app.db'
    initialize(db)
    with sqlite3.connect(db) as c:
        for sid, platform in [('w1','WB'),('w2','WB'),('o1','Ozon'),('o2','Ozon')]:
            c.execute('INSERT INTO stores(id,name,platform) VALUES(?,?,?)',(sid,sid,platform))
    initialize_key(tmp_path/'keys')
    v = Vault(tmp_path/'keys',db)
    ids = {sid: v.add(sid, ('WB' if sid.startswith('w') else 'Ozon'),sid,'DUMMY_TEST_NOT_REAL',
                          client_id=('test-id' if sid.startswith('o') else None)) for sid in ('w1','w2','o1','o2')}
    return v,ids,CheckpointStore(tmp_path/'staging.db')


def ozone_reply(rows, next_cursor=''):
    return MockResponse(200, {'accruals':rows,'last_id':next_cursor})


def wb_reply(rows):
    return MockResponse(200,rows)


def test_ozon_two_pages_and_replay(environment):
    v, ids, stage = environment
    tr = ScriptedTransport([ozone_reply([{'accrual_id':100,'accrued_category':'ITEM','accrued':'-20.10'}],'p2'),
                           ozone_reply([{'accrual_id':101,'accrued_category':'POSTING'}])])
    runner = OfflineSync(v, Gateway(v,tr),stage)
    assert runner.ozon_day(ids['o1'],'o1','2026-11-01')['events'] == 2
    assert runner.ozon_day(ids['o1'],'o1','2026-11-01')['status'] == 'already_complete'
    assert len(tr.calls) == 2
    assert tr.calls[1][2]['last_id'] == 'p2'


def test_wb_to_204_and_big_rrd(environment):
    v, ids, stage = environment
    big = 2**53+17
    tr = ScriptedTransport([wb_reply([{'rrdId':big,'reportId':88,'retailAmount':'158.50','currency':'RUB'}]),MockResponse(204,None)])
    runner = OfflineSync(v,Gateway(v,tr),stage)
    assert runner.wb_period(ids['w1'],'w1','2026-10-01','2026-10-07')['events'] == 1
    assert tr.calls[1][2]['rrdId'] == big
    assert runner.wb_period(ids['w1'],'w1','2026-10-01','2026-10-07')['status']=='already_complete'


def test_wb_two_stores_isolation(environment):
    v, ids, stage = environment
    for sid in ('w1','w2'):
        tr = ScriptedTransport([wb_reply([{'rrdId':10,'retailAmount':'90.00'}]),MockResponse(204,None)])
        OfflineSync(v,Gateway(v,tr),stage).wb_period(ids[sid],sid,'2026-10-01','2026-10-07')
    assert stage.count('w1') == 1 and stage.count('w2') == 1


def test_cross_store_credential_blocked(environment):
    v, ids, stage = environment
    tr=ScriptedTransport([ozone_reply([])])
    with pytest.raises(SecurityError):
        OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o2','2026-11-01')
    assert tr.calls == []


@pytest.mark.parametrize('scope,target', [('global','*'), ('platform','Ozon'), ('store','o1')])
def test_stop_before_request(environment,scope,target):
    v,ids,stage=environment
    v.set_stop(scope,target,True)
    tr=ScriptedTransport([ozone_reply([])])
    with pytest.raises(Stopped): OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert tr.calls == []


def test_stop_between_pages(environment):
    v,ids,stage=environment
    class Flip(ScriptedTransport):
        def request(self,*args):
            result=super().request(*args)
            v.set_stop('global','*',True)
            return result
    tr=Flip([ozone_reply([{'accrual_id':1}],'another'),ozone_reply([])])
    with pytest.raises(Stopped): OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert len(tr.calls)==1
    assert stage.checkpoint('o1','Ozon','2026-11-01')==('another',False)


def test_resume_midway(environment):
    v, ids, stage=environment
    tr=ScriptedTransport([ozone_reply([{'accrual_id':1}],'c2'),TimeoutError('temporary')])
    with pytest.raises(ExternalAPIError): OfflineSync(v,Gateway(v,tr,attempts=1),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert stage.count('o1')==1
    tr2=ScriptedTransport([ozone_reply([{'accrual_id':2}])])
    assert OfflineSync(v,Gateway(v,tr2),stage).ozon_day(ids['o1'],'o1','2026-11-01')['events']==2
    assert tr2.calls[0][2]['last_id']=='c2'


def test_retry_429_then_503_then_200(environment):
    v, ids, stage=environment
    delays=[]
    tr=ScriptedTransport([MockResponse(429,{'secret':'untrusted'},retry_after=2),MockResponse(503,None),ozone_reply([])])
    assert OfflineSync(v,Gateway(v,tr,sleep=delays.append,attempts=3),stage).ozon_day(ids['o1'],'o1','2026-11-01')['status']=='complete'
    assert delays == [2,2]


@pytest.mark.parametrize('status',[400,401,402,403,404])
def test_permanent_errors_no_retry(environment,status):
    v,ids,stage=environment
    tr=ScriptedTransport([MockResponse(status,{'token':'must-not-print'}),ozone_reply([])])
    with pytest.raises(ExternalAPIError) as e:
        OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert len(tr.calls)==1
    assert 'must-not-print' not in str(e.value)


@pytest.mark.parametrize('status',[429,500,502,503,504])
def test_retry_exhausted(environment,status):
    v,ids,stage=environment
    tr=ScriptedTransport([MockResponse(status,None) for _ in range(3)])
    with pytest.raises(ExternalAPIError): OfflineSync(v,Gateway(v,tr,attempts=3),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert len(tr.calls)==3


def test_timeout_retry(environment):
    v,ids,stage=environment
    tr=ScriptedTransport([TimeoutError('test'),ozone_reply([])])
    assert OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')['status']=='complete'


def test_schema_drift_ozon(environment):
    v,ids,stage=environment
    for response in [MockResponse(200,{'result':{'data':[]}}),ozone_reply([{'wrong_id':1}]),ozone_reply([{'accrual_id':1,'accrued':'nan'}])]:
        with pytest.raises(ContractError): OfflineSync(v,Gateway(v,ScriptedTransport([response])),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert stage.count('o1')==0


def test_ozon_cursor_loop(environment):
    v,ids,stage=environment
    tr=ScriptedTransport([ozone_reply([{'accrual_id':1}],'c1'),ozone_reply([{'accrual_id':2}],'c1')])
    with pytest.raises(ContractError, match='cursor loop'): OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    assert stage.count('o1')==1


def test_empty_nonterminal_ozon_continues(environment):
    v,ids,stage=environment
    tr=ScriptedTransport([ozone_reply([], 'next'),ozone_reply([{'accrual_id':1}])])
    assert OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')['events']==1


def test_wb_failclosed_unknown_schema(environment):
    v,ids,stage=environment
    for response in (MockResponse(200,{'data': []}),wb_reply([{'rrd_id':1}]),wb_reply([])):
        with pytest.raises(ContractError): OfflineSync(v,Gateway(v,ScriptedTransport([response])),stage).wb_period(ids['w1'],'w1','2026-10-01','2026-10-07')


def test_wb_duplicate_cursor(environment):
    v,ids,stage=environment
    tr=ScriptedTransport([wb_reply([{'rrdId':1}]),wb_reply([{'rrdId':1}])])
    with pytest.raises(ContractError): OfflineSync(v,Gateway(v,tr),stage).wb_period(ids['w1'],'w1','2026-10-01','2026-10-07')


def test_wb_max_page_guard(environment):
    v,ids,stage=environment
    tr=ScriptedTransport([wb_reply([{'rrdId':1}])])
    with pytest.raises(ContractError,match='max pages'):
        OfflineSync(v,Gateway(v,tr),stage,max_pages=1).wb_period(ids['w1'],'w1','2026-10-01','2026-10-07')
    assert stage.checkpoint('w1','WB','2026-10-01:2026-10-07')==('1',False)


def test_conflicting_event_does_not_advance(environment):
    v,ids,stage=environment
    # First day: source item recorded. Second day: changed source id; should block.
    first=ScriptedTransport([ozone_reply([{'accrual_id':10,'accrued':'10.00'}])])
    OfflineSync(v,Gateway(v,first),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    second=ScriptedTransport([ozone_reply([{'accrual_id':10,'accrued':'20.00'}])])
    with pytest.raises(ContractError,match='Conflicting'):
        OfflineSync(v,Gateway(v,second),stage).ozon_day(ids['o1'],'o1','2026-11-02')
    assert stage.checkpoint('o1','Ozon','2026-11-02')==('',False)


def test_disallow_obsolete_or_write_routes(environment):
    v,ids,_=environment
    tr=ScriptedTransport([])
    gw=Gateway(v,tr)
    for path in ('/v3/finance/transaction/list','/v1/product/import/prices','https://evil.com','/api/finance/v1/sales-reports/../DELETE'):
        with pytest.raises(SecurityError): gw.request(ids['o1'],'o1','Ozon',path,{})
    assert not tr.calls


def test_invalid_retry_after_fails(environment):
    v,ids,stage=environment
    tr=ScriptedTransport([MockResponse(429,None,retry_after=float('nan'))])
    with pytest.raises(ContractError): OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')


def test_no_secrets_in_staging(environment):
    v,ids,stage=environment
    tr=ScriptedTransport([ozone_reply([{'accrual_id':1,'posting':{'buyer_name':'PRIVATE'}, 'accrued':'1.00'}])])
    OfflineSync(v,Gateway(v,tr),stage).ozon_day(ids['o1'],'o1','2026-11-01')
    raw=stage.path.read_bytes()
    assert b'PRIVATE' not in raw and b'DUMMY_TEST_NOT_REAL' not in raw


def test_separate_mock_transport_prohibits_network(environment):
    v,ids,_=environment
    with pytest.raises(SecurityError): Gateway(v,object())


def test_ozon_finite_money_and_wb_strings():
    assert extract_ozon({'accruals':[{'accrual_id':1,'accrued':'-10.50'}],'last_id':''})[0][0][1]['accrued']=='-10.50'
    assert extract_wb([{'rrdId':123,'retailAmount':'123.45'}])[0][0][1]['retailAmount']=='123.45'
    for bad in ('NaN', 'Infinity', '0.001', '10,50', True):
        with pytest.raises(ContractError): extract_wb([{'rrdId':123,'retailAmount':bad}])


def test_virtual_quota_per_key_and_provider(environment):
    from app.offline_sync import SimulatedPacer
    delays=[]
    pace=SimulatedPacer({'WB':60.0,'Ozon':2.0},sleep=delays.append)
    pace.wait('one','WB')
    pace.wait('one','WB')
    pace.wait('other','WB')
    pace.wait('one','Ozon')
    pace.wait('one','Ozon')
    assert delays == [60,2]


def test_gateway_uses_virtual_pacer(environment):
    from app.offline_sync import SimulatedPacer
    v,ids,stage=environment
    delays=[]
    pacer=SimulatedPacer({'WB':60},sleep=delays.append)
    mocked=ScriptedTransport([wb_reply([{'rrdId':1}]),MockResponse(204,None)])
    OfflineSync(v,Gateway(v,mocked,pacer=pacer),stage).wb_period(ids['w1'],'w1','2026-11-01','2026-11-07')
    assert delays == [60]


def test_1000_tenants_staging_isolation(tmp_path):
    """Synthetic storage smoke ONLY, not an end-to-end 1000-client performance benchmark."""
    stage=CheckpointStore(tmp_path/'capacity_smoke.sqlite')
    for index in range(1000):
        sid=f'tenant-{index}'
        stage.commit_page(sid,'Ozon','2026-11-01','',[(str(index),{'accrual_id':str(index)})],True)
    assert stage.count('tenant-0')==1
    assert stage.count('tenant-999')==1
    with stage._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 1000
