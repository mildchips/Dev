"""Fully synthetic, deterministic example. Never used as proof of actual savings."""
from datetime import date,timedelta

def generate_demo():
    rows=[]
    start=date(2026,9,20)
    for day in range(56):
        stamp=(start+timedelta(days=day)).isoformat()
        for sku, price, cost, qty in [('JACKET-01',2500,900,3),('GLOVES-02',1600,700,2),('BAG-03',900,700,2)]:
            base=f'{day:02}-{sku}'
            def add(cat,amount,quantity=None):
                rows.append({'date':stamp,'sku':sku,'category':cat,'amount_kopeks':amount,'quantity':quantity,
                         'operation_id':f'{cat}-{base}','order_id':base,'description':'Синтетический учебный пример'})
            add('sale', price*qty*100,qty)
            add('commission',-round(price*qty*.17*100))
            add('logistics',-(650*qty*100 if day>=42 and sku=='JACKET-01' else 220*qty*100))
            add('advertising',-(430*qty*100 if day>=42 and sku=='JACKET-01' else (210 if sku!='BAG-03' else 190)*qty*100))
    return {'rows':rows,'costs':{'JACKET-01':90000,'GLOVES-02':70000,'BAG-03':70000}}
