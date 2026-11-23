"""Financial correctness and no fabricated-profit regressions."""
from app.engine import summary,analyze,unit_cost_at
from app.scenarios import ScenarioCase,ScenarioRequest,compare
from app.ingest import IngestError
import pytest

def line(d,sku,cat,amt,qty=None,imp='a',op='',row=2):
    return dict(date=d,sku=sku,category=cat,amount_kopeks=amt,quantity=qty,
                operation_id=op,import_id=imp,source_row=row)

def case(label,units,price,cogs,ads='0',commission='15',logistics='100',storage='0',other='0'):
    return ScenarioCase(label=label,paid_units=units,realized_price_rub=price,
            unit_cogs_rub=cogs,commission_percent=commission,logistics_unit_rub=logistics,
            storage_unit_rub=storage,advertising_budget_rub=ads,other_period_costs_rub=other)

def test_scenario_is_explicit_counterfactual_not_guarantee():
    out=compare(ScenarioRequest(baseline=case('before',100,'1000','600',ads='5000'),
                                alternative=case('after',95,'1050','600',ads='4500')))
    assert out['baseline']['contribution_rub']==10000
    assert out['alternative']['contribution_rub']==13787.50
    assert out['delta_rub']==3787.50
    assert out['verified_realized_profit_rub'] is None
    assert out['guaranteed_savings_rub'] is None
    assert out['benefit_class']=='counterfactual_user_assumptions'

def test_scenario_cuts_ad_but_could_destroy_profit():
    out=compare(ScenarioRequest(baseline=case('before',100,'1000','600',ads='5000'),
                                alternative=case('after',50,'1000','600',ads='1000')))
    assert out['delta_rub']<0

def test_scenario_commission_bounds_and_negative_cost_rejected():
    with pytest.raises(IngestError):
        compare(ScenarioRequest(baseline=case('a',1,'1000','600',commission='101'),alternative=case('b',1,'1000','600')))
    with pytest.raises(IngestError):
        compare(ScenarioRequest(baseline=case('a',1,'1000','-1'),alternative=case('b',1,'1000','600')))

def test_cogs_effective_dated_no_future_lookahead():
    rows=[line('2026-09-01','SKU','sale',100000,1),line('2026-10-01','SKU','sale',100000,1)]
    revisions={'SKU':[dict(effective_from='2026-09-01',unit_cost_kopeks=50000),
                      dict(effective_from='2026-10-01',unit_cost_kopeks=80000)]}
    out=summary(rows,revisions)
    assert out['cogs_rub']==1300
    assert out['contribution_rub']==700
    assert out['cost_basis']=='effective_dated_or_unavailable'
    assert unit_cost_at(revisions,'SKU','2026-08-01') is None

def test_unknown_earlier_purchase_price_blocks_profit():
    rows=[line('2026-09-01','SKU','sale',100000,1)]
    revisions={'SKU':[dict(effective_from='2026-10-01',unit_cost_kopeks=80000)]}
    s=summary(rows,revisions)
    assert s['contribution_rub'] is None
    assert s['missing_cogs_skus']==['SKU']

def test_overlap_imports_blocks_profit_without_fabricating_refund():
    base=line('2026-11-01','S','sale',100000,1,imp='file1',op='OP-1')
    duplicate={**base,'import_id':'file2','source_row':200}
    s=summary([base,duplicate],{'S':10000})
    assert s['contribution_rub'] is None
    assert s['cross_import_overlap_count']==1
    items=analyze([base,duplicate],{'S':10000})
    o=next(f for f in items if f['code']=='OVERLAPPING_IMPORTS')
    assert o['amount_rub'] is None
    assert o['verified_savings_rub'] is None

def test_return_more_than_sales_is_not_false_profit():
    r=[line('2026-11-01','S','return',-15000,-2)]
    s=summary(r,{'S':10000})
    assert s['contribution_rub'] is None
    assert 'S' in s['bad_quantity_skus']
    assert any(i['code']=='RETURN_OVERHANG' for i in analyze(r,{'S':10000}))

def test_historical_loss_and_spike_are_not_achievable_savings():
    r=[line('2026-11-01','S','sale',10000,1),line('2026-11-01','S','commission',-9000)]
    d=summary(r,{'S':2000})
    f=analyze(r,{'S':2000})
    assert d['contribution_rub']==-10
    assert d['verified_realized_profit_rub'] is None
    assert d['claimable_monthly_gain_rub'] is None
    assert next(x for x in f if x['code']=='NEGATIVE_SKU')['estimated_incremental_profit_rub'] is None

def test_period_specific_review_keys():
    a=[line('2026-10-01','S','sale',10000,1)]
    b=[line('2026-11-01','S','sale',10000,1)]
    akey=analyze(a,{})[0]['key']; bkey=analyze(b,{})[0]['key']
    assert akey!=bkey
