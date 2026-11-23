"""Synthetic independent reference tests. No marketplace network/keys."""
import copy
import json
import sqlite3
from datetime import datetime,timezone,timedelta
import pytest
from app.finance import Finance,CONTRACT,kopeks,moscow_day
from app.offline_sync import ContractError
from app.storage import initialize,connect

@pytest.fixture
def finance(tmp_path):
    db=tmp_path/'local.db';initialize(db)
    with connect(db) as c:
        c.executemany('INSERT INTO stores(id,name,platform) VALUES(?,?,?)',
                      [('w1','WB one','WB'),('w2','WB two','WB'),('o1','Ozon','Ozon')])
    return Finance(db)

def event(eid='s1',kind='sale',amount='1000.00',**kwargs):
    return {'event_id':eid,'date':'2026-11-01','sku':'A','type':kind,'amount':amount,
            'quantity':1 if kind=='sale' else None,'posting_id':'p1',**kwargs}

def report(events=None,**kwargs):
    return {'contract':CONTRACT,'report_id':'r1','provider':'WB','start':'2026-11-01','end':'2026-11-07',
            'complete':True,'events':events if events is not None else [event(),event('f1','fee','-100.00')],**kwargs}

def control(total='900.00',cats=None,postings=None):
    return {'reference_id':'INDEPENDENT-REFERENCE-1','total':total,
            'categories':cats if cats is not None else {'sale':'1000.00','fee':'-100.00'},
            'postings':postings if postings is not None else {'p1':'900.00'}}

def batch(**kwargs):
    return {'batch_id':'b1','sku':'A','effective_from':'2026-10-01','purchased_units':10,'unit_cost':'200.00',**kwargs}

def history(finance,stamp='2026-10-01',cost=20000,store='w1'):
    with connect(finance.path) as db:
        db.execute('INSERT INTO cost_revisions(store_id,sku,effective_from,unit_cost_kopeks) VALUES(?,?,?,?)',(store,'A',stamp,cost))

def test_independent_reference_and_cogs(finance):
    finance.ingest('w1',report());history(finance)
    r=finance.control('w1','r1',control())
    assert r['status']=='VERIFIED'
    assert r['actual_kopeks']==90000 and r['difference_kopeks']==0
    assert r['contribution_kopeks']==70000
    assert r['net_profit_kopeks'] is None
    assert len(r['reference_sha256'])==64

@pytest.mark.parametrize('change,expected',[
    ({'complete':False},'PARTIAL'),({},'CHECKING')])
def test_without_independent_reference(finance,change,expected):
    finance.ingest('w1',report(**change))
    r=finance.report('w1','r1'); assert r['status']==expected
    assert r['contribution_kopeks'] is None

def test_stale_status(finance):
    finance.ingest('w1',report());finance.control('w1','r1',control())
    r=finance.report('w1','r1',now=datetime.now(timezone.utc)+timedelta(hours=25))
    assert r['status']=='STALE' and r['contribution_kopeks'] is None

def test_missing_cogs_does_not_crash_ui(finance):
    finance.ingest('w1',report());r=finance.control('w1','r1',control())
    assert r['status']=='VERIFIED' and r['missing_cogs']==['A']
    assert r['sku']['A']['cogs_kopeks'] is None and r['contribution_kopeks'] is None

@pytest.mark.parametrize('total,cats,postings,code',[
 ('899.99',{'sale':'1000.00','fee':'-100.01'},{'p1':'899.99'},'TOTAL_MISMATCH'),
 ('900.00',{'sale':'900.00'},{'p1':'900.00'},'CATEGORY_MISMATCH'),
 ('900.00',{'sale':'1000.00','fee':'-100.00'},{'p2':'900.00'},'POSTING_MISMATCH')])
def test_independent_reconciliation_mismatch(finance,total,cats,postings,code):
    finance.ingest('w1',report());history(finance)
    r=finance.control('w1','r1',control(total,cats,postings))
    assert r['status']=='MISMATCH' and code in r['issues']
    assert r['contribution_kopeks'] is None

def test_inconsistent_control_rejected(finance):
    finance.ingest('w1',report())
    with pytest.raises(ContractError): finance.control('w1','r1',control('1.00'))

@pytest.mark.parametrize('mutation',[
 {'type':'NEW_FEE'}, {'amount':'NaN'}, {'amount':'1.001'}, {'amount':1.5},
 {'amount':True}, {'quantity':True}, {'date':'2026-12-01'}, {'customer':'PRIVATE'},
 {'type':'sale','amount':'-1.00'}, {'type':'return','amount':'-1.00','quantity':-1},
 {'type':'fee','quantity':1}, {'type':'refund','amount':'-1.00'}])
def test_bad_row_quarantine_no_money(finance,mutation):
    result=finance.ingest('w1',report([event(**mutation)]))
    r=finance.report('w1','r1')
    assert result['quarantined']==1 and r['status']=='MISMATCH'
    assert r['actual_kopeks']==0 and r['contribution_kopeks'] is None
    with connect(finance.path) as db:
        assert 'PRIVATE' not in str([tuple(r) for r in db.execute('SELECT * FROM finance_quarantine')])

@pytest.mark.parametrize('events',[[],[event()]])
def test_unknown_contract(finance,events):
    finance.ingest('w1',report(events,contract='UNKNOWN/99'))
    r=finance.report('w1','r1')
    assert r['status']=='MISMATCH' and r['event_count']==0

def test_overlap_reimport_restart_deduplicated(finance):
    assert finance.ingest('w1',report())['accepted']==2
    assert finance.ingest('w1',report())['duplicates']==2
    finance=Finance(finance.path)
    assert finance.ingest('w1',report(report_id='r2'))['duplicates']==2
    f=finance.overview('w1')
    assert f['unique_events']==2 and f['unique_accruals_kopeks']==90000
    assert [r['event_count'] for r in f['reports']]==[2,2]

def test_same_id_changed_source_quarantine(finance):
    finance.ingest('w1',report())
    finance.ingest('w1',report([event(amount='2000.00')]))
    r=finance.report('w1','r1')
    assert r['actual_kopeks']==90000 and r['status']=='MISMATCH'
    assert r['quarantine'][0]['reason']=='SOURCE_EVENT_CONFLICT'

def test_sealed_report_no_new_events(finance):
    finance.ingest('w1',report())
    result=finance.ingest('w1',report([event('new')]))
    assert result['quarantined']==1
    assert finance.overview('w1')['unique_events']==2

def test_partial_pages_then_complete(finance):
    finance.ingest('w1',report([event()],complete=False))
    finance.ingest('w1',report([event('f1','fee','-100.00')],complete=True))
    assert finance.control('w1','r1',control())['status']=='VERIFIED'
    finance.ingest('w1',report([event()],complete=False))
    assert finance.report('w1','r1')['status']=='VERIFIED'

def test_store_provider_and_report_scope_isolation(finance):
    finance.ingest('w1',report()); finance.ingest('w2',report([event(amount='500.00')]))
    assert finance.report('w2','r1')['actual_kopeks']==50000
    with pytest.raises(ContractError): finance.ingest('o1',report())
    with pytest.raises(ContractError): finance.control('o1','r1',control())
    with pytest.raises(ContractError): finance.ingest('w1',report(start='2026-10-31'))
    assert finance.overview('o1')['unique_events']==0

def test_batch_date_quantity_immutable(finance):
    finance.ingest('w1',report([event(batch_id='b1')]))
    finance.add_batch('w1',batch()); finance.add_batch('w1',batch())
    r=finance.control('w1','r1',control('1000.00',{'sale':'1000.00'},{'p1':'1000.00'}))
    assert r['contribution_kopeks']==80000
    with pytest.raises(ContractError): finance.add_batch('w1',batch(unit_cost='250.00'))

@pytest.mark.parametrize('b',[
 {'effective_from':'2026-11-02'}, {'sku':'OTHER'}, {'purchased_units':1}])
def test_invalid_batch_blocks_cogs(finance,b):
    finance.ingest('w1',report([event(batch_id='b1',quantity=2)]))
    finance.add_batch('w1',batch(**b))
    r=finance.control('w1','r1',control('1000.00',{'sale':'1000.00'},{'p1':'1000.00'}))
    assert r['missing_cogs']==['A'] and r['contribution_kopeks'] is None

def test_batch_capacity_across_overlapping_reports(finance):
    finance.add_batch('w1',batch(purchased_units=1))
    finance.ingest('w1',report([event(batch_id='b1')]))
    finance.ingest('w1',report([event(batch_id='b1')],report_id='overlap'))
    assert finance.report('w1','r1')['missing_cogs']==[]
    finance.ingest('w1',report([event('s2',batch_id='b1')],report_id='extra'))
    assert finance.report('w1','r1')['missing_cogs']==['A']

def test_return_cogs_uses_original_sale_date(finance):
    history(finance); history(finance,'2026-11-02',90000)
    finance.ingest('w1',report([event(),event('r','return','-1000.00',date='2026-11-03',quantity=-1,reverses='s1')]))
    r=finance.control('w1','r1',control('0.00',{'sale':'1000.00','return':'-1000.00'},{'p1':'0.00'}))
    assert r['contribution_kopeks']==0 and r['sku']['A']['cogs_kopeks']==0

@pytest.mark.parametrize('reverse',['missing','s1'])
def test_return_invalid_quantity_or_reference(finance,reverse):
    history(finance)
    finance.ingest('w1',report([event(),event('r','return','-2000.00',quantity=-2,reverses=reverse)]))
    assert finance.report('w1','r1')['missing_cogs']==['A']

@pytest.mark.parametrize('kind,quantity,amount,cats,total',[
 ('fee',None,'100.00',{'sale':'1000.00','fee':'-100.00','storno':'100.00'},'1000.00'),
 ('sale',-1,'-1000.00',{'sale':'1000.00','storno':'-1000.00'},'0.00')])
def test_storno_exact_reversal(finance,kind,quantity,amount,cats,total):
    history(finance)
    events=[event()]
    target='s1'
    if kind=='fee':
        events.append(event('f1','fee','-100.00'));target='f1'
    events.append(event('st','storno',amount,quantity=quantity,reverses=target))
    finance.ingest('w1',report(events))
    r=finance.control('w1','r1',control(total,cats,{'p1':total}))
    assert r['status']=='VERIFIED' and r['contribution_kopeks']==(80000 if kind=='fee' else 0)

def test_invalid_storno_is_mismatch(finance):
    finance.ingest('w1',report([event(),event('st','storno','-1.00',quantity=-1,reverses='s1')]))
    assert 'INVALID_REVERSAL' in finance.report('w1','r1')['issues']

def test_negative_fee_adjustment_preserves_sign(finance):
    events=[event(),event('f','fee','-100.00'),event('c','fee','20.00'),event('d','delivery','-10.00')]
    finance.ingest('w1',report(events))
    r=finance.control('w1','r1',control('910.00',{'sale':'1000.00','fee':'-80.00','delivery':'-10.00'},{'p1':'910.00'}))
    assert r['actual_kopeks']==91000 and r['status']=='VERIFIED'

def test_historical_cost_before_first_revision_blocks(finance):
    finance.ingest('w1',report());history(finance,'2026-11-02')
    assert finance.report('w1','r1')['missing_cogs']==['A']

def test_general_cost_not_arbitrarily_allocated(finance):
    history(finance)
    finance.ingest('w1',report([event(),event('fee','fee','-100.00',sku='')]))
    r=finance.control('w1','r1',control())
    assert r['sku']['A']['contribution_kopeks']==80000
    assert r['sku']['']['contribution_kopeks'] is None
    assert r['contribution_kopeks']==70000

def test_sources_and_controls_immutable_and_store_delete_supported(finance):
    finance.ingest('w1',report());finance.control('w1','r1',control())
    for table in ('finance_sources','finance_controls','finance_contracts'):
        with pytest.raises(sqlite3.IntegrityError),connect(finance.path) as db:
            db.execute(f'UPDATE {table} SET '+ ('event_id=event_id' if table=='finance_sources' else 'id=id' if table=='finance_controls' else 'version=version'))
    with connect(finance.path) as db: db.execute('DELETE FROM stores WHERE id=?',('w1',))
    with connect(finance.path) as db: assert db.execute('SELECT COUNT(*) FROM finance_sources').fetchone()[0]==0

def test_ozon_same_normalized_contract_no_inference(finance):
    finance.ingest('o1',report(provider='Ozon'))
    assert finance.control('o1','r1',control())['status']=='VERIFIED'

@pytest.mark.parametrize('value',[1.1,True,'NaN','Infinity','1.001','1e2','1000000000001','-0.001'])
def test_money_rejects_ambiguous_values(value):
    with pytest.raises(ContractError):kopeks(value)

def test_moscow_midnight_and_big_ids(finance):
    assert moscow_day('2026-10-31T21:30:00Z')=='2026-11-01'
    with pytest.raises(ContractError):moscow_day('2026-11-01T00:00:00')
    finance.ingest('w1',report([event(eid=str(2**63-1))]))
    assert finance.overview('w1')['unique_events']==1

def test_migration_preserves_legacy_rows_and_schema_history(finance):
    with connect(finance.path) as db:
        db.execute("INSERT INTO costs(store_id,sku,unit_cost_kopeks) VALUES('w1','A',123)")
    Finance(finance.path)
    with connect(finance.path) as db:
        assert db.execute('SELECT unit_cost_kopeks FROM costs').fetchone()[0]==123
        assert db.execute('SELECT COUNT(*) FROM finance_contracts').fetchone()[0]==1

def test_observed_schema_history_and_original_checksum(finance):
    finance.ingest('w1',report([event()]))
    finance.ingest('w1',report([event('new',amount=1000)],report_id='r2'))
    with connect(finance.path) as db:
        assert db.execute('SELECT COUNT(*) FROM finance_schema_observations').fetchone()[0]==2
        row=db.execute('SELECT original_hash,source_hash FROM finance_sources LIMIT 1').fetchone()
        assert len(row['original_hash'])==64 and len(row['source_hash'])==64


def test_invalid_json_rolls_back_whole_file(finance):
    with pytest.raises(ContractError):
        finance.ingest('w1',report([event(),event('bad',amount=float('nan'))]))
    assert finance.overview('w1')['unique_events']==0
    assert finance.overview('w1')['reports']==[]


def test_store_batch_and_history_do_not_cross_store(finance):
    finance.add_batch('w2',batch())
    history(finance,store='w2')
    finance.ingest('w1',report([event(batch_id='b1')]))
    assert finance.report('w1','r1')['missing_cogs']==['A']


def test_full_example_matches_independent_fixture(finance):
    from pathlib import Path
    examples=Path(__file__).resolve().parents[1]/'examples'/'finance_v036'
    load=lambda name:json.loads((examples/name).read_text())
    finance.ingest('w1',load('report_wb.json'));finance.add_batch('w1',load('batch.json'))
    result=finance.control('w1','SYN-REPORT-1',load('independent_control.json'))
    assert result['actual_kopeks']==73000 and result['contribution_kopeks']==33000
    assert result['status']=='VERIFIED' and result['difference_kopeks']==0


@pytest.mark.parametrize('provider',[None,[],{'bad':'provider'}])
def test_invalid_provider_fails_closed(finance,provider):
    with pytest.raises(ContractError):finance.ingest('w1',report(provider=provider))
    assert finance.overview('w1')['unique_events']==0
