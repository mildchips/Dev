from app.engine import summary,analyze
from app.demo_data import generate_demo

def row(date,sku,category,amount,quantity=None,import_id='file-one',source_row=2,operation_id=''):
    return {'date':date,'sku':sku,'category':category,'amount_kopeks':amount,'quantity':quantity,'import_id':import_id,'source_row':source_row,'operation_id':operation_id}

def test_revenue_is_not_payout_and_cogs_is_not_optional():
    rows=[row('2026-11-01','A','sale',10000,1),row('2026-11-01','A','commission',-1000)]
    assert summary(rows,{})['contribution_rub'] is None
    s=summary(rows,{'A':3000})
    assert s['gross_sales_rub']==100
    assert s['marketplace_costs_rub']==-10
    assert s['cogs_rub']==30
    assert s['contribution_rub']==60

def test_return_adjusts_inventory_and_revenue():
    rows=[row('2026-11-01','A','sale',10000,2),row('2026-11-02','A','return',-5000,-1)]
    assert summary(rows,{'A':3000})['contribution_rub']==20

def test_missing_quantity_blocks_true_contribution():
    r=[row('2026-11-01','A','sale',10000)]
    assert summary(r,{'A':3000})['data_status']=='incomplete'
    assert any(f['code']=='MISSING_COGS' for f in analyze(r,{'A':3000}))

def test_negative_sku_is_historical_exposure_not_savings():
    rows=[row('2026-11-01','A','sale',10000,1),row('2026-11-01','A','commission',-2000)]
    item=next(f for f in analyze(rows,{'A':10000}) if f['code']=='NEGATIVE_SKU')
    assert item['amount_rub']==20
    assert 'не подтверждённая экономия' in item['note']
    assert item['evidence_count']==2

def test_duplicate_operation_id_does_not_claim_refund():
    r=[row('2026-11-01','A','commission',-300,operation_id='ONE'),row('2026-11-02','A','commission',-300,operation_id='ONE')]
    f=next(f for f in analyze(r,{}) if f['code']=='REPEATED_OPERATION_ID')
    assert f['amount_rub'] is None

def test_demo_has_explainable_opportunities():
    d=generate_demo()
    rows=[]
    for i,r in enumerate(d['rows']):
        rows.append({**r,'source_row':i+2,'import_id':'demo'})
    findings=analyze(rows,d['costs'])
    codes={f['code'] for f in findings}
    assert 'NEGATIVE_SKU' in codes
    assert 'LOGISTICS_RATE_SPIKE' in codes
    assert summary(rows,d['costs'])['data_status']=='complete_for_modeled_sources'

def test_unallocated_costs_are_in_total_not_false_sku():
    rows=[row('2026-11-01','A','sale',10000,1),row('2026-11-01','','other',-1000)]
    assert summary(rows,{'A':3000})['contribution_rub']==60
    assert summary(rows,{'A':3000})['unallocated_costs_rub']==-10

def test_full_period_and_category_totals_are_not_truncated():
    rows=[row('2026-09-01','A','sale',10000,1),row('2026-11-15','A','commission',-3000)]
    s=summary(rows,{'A':3000})
    assert s['period_start']=='2026-09-01'
    assert s['period_end']=='2026-11-15'
    assert s['category_totals_rub']=={'sale':100,'commission':-30}
