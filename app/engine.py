"""Deterministic financial diagnostics; money in signed kopeks.

STRICTLY separate actual historical contribution, abnormal cost indicators,
user-entered scenarios and independently verified realized benefit.
No candidate may be sold as a proved monthly profit uplift.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from hashlib import sha256
from typing import Any

VERSION='0.3.7-security-foundation'
COST_CATEGORIES={'commission','logistics','advertising','storage','adjustment','other'}


def money(kopeks: int | None) -> float | None:
    return None if kopeks is None else round(kopeks/100,2)


def unit_cost_at(costs:dict[str,Any], sku:str, stamp:str) -> int|None:
    """Supports static exploratory cost OR effective-dated revisions; no future lookahead."""
    c=costs.get(sku)
    if isinstance(c,int):
        return c if c>=0 else None
    if isinstance(c,list):
        eligible=[r for r in c if r['effective_from']<=stamp]
        return max(eligible,key=lambda r:r['effective_from'])['unit_cost_kopeks'] if eligible else None
    return None


def probable_overlap(entries:list[dict])->list[dict]:
    """Cross-import duplicates that would otherwise overstate money.
    Only a WARNING/BLOCK: identical operations can occasionally be legitimate.
    """
    seen={}
    duplicates=[]
    for r in entries:
        op=(r.get('operation_id') or '').strip()
        if not op:
            continue
        key=(op,r['date'],r['sku'],r['category'],r['amount_kopeks'],r.get('quantity'))
        before=seen.get(key)
        if before and before['import_id'] != r['import_id']:
            duplicates.append(r)
        else:
            seen[key]=r
    return duplicates


def _rows_sales(entries):
    return [r for r in entries if r['category'] in ('sale','return')]


def _missing_costs(entries, costs):
    missing=set()
    for r in _rows_sales(entries):
        if r.get('quantity') is None or unit_cost_at(costs,r['sku'],r['date']) is None:
            missing.add(r['sku'])
    return sorted(missing)


def _bad_quantities(entries):
    sold=Counter()
    for r in _rows_sales(entries):
        if r.get('quantity') is not None:
            sold[r['sku']]+=r['quantity']
    return sorted(s for s,q in sold.items() if q<0)


def summary(entries:list[dict],costs:dict[str,Any])->dict[str,Any]:
    income=sum(r['amount_kopeks'] for r in entries if r['category']=='sale')
    returns=sum(r['amount_kopeks'] for r in entries if r['category']=='return')
    expenses=sum(r['amount_kopeks'] for r in entries if r['category'] in COST_CATEGORIES)
    net_sales=income+returns
    sales=_rows_sales(entries)
    sold_skus={r['sku'] for r in sales}
    missing=_missing_costs(entries,costs)
    badqty=_bad_quantities(entries)
    overlaps=probable_overlap(entries)
    # A complete contribution requires a valid quantity, cost basis and no
    # suspected overlapping imports. No inferred values or fake zeroes.
    cogs_known=bool(sales) and not missing and not badqty and not overlaps
    cogs=sum(r['quantity']*unit_cost_at(costs,r['sku'],r['date']) for r in sales) if cogs_known else None
    contrib=net_sales+expenses-cogs if cogs is not None else None
    unallocated=sum(r['amount_kopeks'] for r in entries if not r['sku'] and r['category'] in COST_CATEGORIES)
    bycat=defaultdict(int)
    for r in entries:
        bycat[r['category']]+=r['amount_kopeks']
    starts=[r['date'] for r in entries]
    is_static=any(isinstance(costs.get(sku),int) for sku in sold_skus)
    return {
      'gross_sales_rub':money(income),'returns_rub':money(returns),'net_sales_rub':money(net_sales),
      'marketplace_costs_rub':money(expenses),'cogs_rub':money(cogs),'contribution_rub':money(contrib),
      'missing_cogs_skus':missing,'bad_quantity_skus':badqty,'cross_import_overlap_count':len(overlaps),
      'unallocated_costs_rub':money(unallocated),'entries':len(entries),'sku_count':len(sold_skus),
      'period_start':min(starts,default=None),'period_end':max(starts,default=None),
      'category_totals_rub':{k:money(v) for k,v in bycat.items()},
      'data_status':'complete_for_modeled_sources' if cogs_known else 'incomplete',
      'cost_basis':'static_user_assumption' if is_static else 'effective_dated_or_unavailable',
      'verified_realized_profit_rub':None,'verified_savings_rub':None,'claimable_monthly_gain_rub':None,
      'warnings':(
         ['Операции из разных файлов могут дублировать друг друга; прибыль заблокирована до сверки.'] if overlaps else []
       )+(['Возвратов больше продаж за загруженный период по отдельным SKU; нужна история заказов.'] if badqty else [])
        +(['Себестоимость одинакова для всех дат: изменение цен закупки и партии не учтены.'] if is_static else [])
        +['Не бухгалтерская чистая прибыль: внеплощадочные расходы и налоги не охвачены автоматически.',
           'Начисления и выплаты не сверены независимо с площадкой/банком.',
           'Возможности роста прибыли требуют контрфактической проверки и не выводятся из одной суммы убытка.'],
      'caveat':'Не является бухгалтерской чистой прибылью: налоги, зарплата, аренда и прочие внешние расходы не представлены, если не внесены отдельными строками.'}


def analyze(entries:list[dict],costs:dict[str,Any])->list[dict[str,Any]]:
    signals=[]
    span=(min((r['date'] for r in entries),default='none'),max((r['date'] for r in entries),default='none'))
    # Review keys include period bounds; review must not leak to another date window.
    period_key=sha256('|'.join(span).encode()).hexdigest()[:10]
    def signal(code,name,sku,amount,unit,confidence,explanation,formula,action,risk,evidence,importance=1):
        signals.append({
          'key':f'{code}:{sku or "_"}:{period_key}','code':code,'title':name,'sku':sku,
          'amount_rub':money(amount),'amount_basis':unit,'confidence':confidence,
          'status':'detected','explanation':explanation,'formula':formula,'action':action,'risk':risk,
          'evidence':[{'import_id':r['import_id'],'source_row':r['source_row'],'date':r['date'],
             'sku':r['sku'],'category':r['category'],'amount_rub':money(r['amount_kopeks'])} for r in evidence[:12]],
          'evidence_count':len(evidence),'priority':importance,'rule_version':VERSION,
          'verified_savings_rub':None,'estimated_incremental_profit_rub':None,
          'benefit_class':'historical_exposure' if code=='NEGATIVE_SKU' else 'diagnostic_indicator',
          'note':'Сигнал для проверки, не подтверждённая экономия и не требование к маркетплейсу.'})
    overlap=probable_overlap(entries)
    if overlap:
        signal('OVERLAPPING_IMPORTS','Возможный двойной учёт между файлами',None,None,'блокировка достоверности','high',
            'Те же идентификаторы, суммы, дата и SKU обнаружены в разных импортах.',
            'Совпадает идентификатор операции, дата, SKU, категория, сумма, количество.',
            'Сверьте версии выгрузок; отмените повторные строки либо загрузите недублирующиеся периоды.',
            'Без сверки все итоговые суммы и показатели могут быть завышены.',overlap,importance=10)
    if _bad_quantities(entries):
        for sku in _bad_quantities(entries):
            evidence=[r for r in _rows_sales(entries) if r['sku']==sku]
            signal('RETURN_OVERHANG','Возвратов больше, чем продаж в периоде',sku,None,'качество данных','high',
              'Количество возвратов превышает продажи в текущей выгрузке; возможно, возврат относится к прошлому периоду.',
              'Σ sale.quantity + Σ return.quantity < 0',
              'Расширьте период, сверьте заказ/возврат, определите первоначальную себестоимость.',
              'Маржа за период без исторических данных может быть искажена.',evidence,importance=9)
    by_sku=defaultdict(list)
    for r in entries:
        if r['sku']:
            by_sku[r['sku']].append(r)
    for sku,rows in by_sku.items():
        sales=_rows_sales(rows)
        if not sales:
            continue
        if any(r['quantity'] is None or unit_cost_at(costs,sku,r['date']) is None for r in sales):
            signal('MISSING_COGS','Недостаточно данных для расчёта маржи',sku,None,'качество данных','high',
                'Невозможно определить себестоимость на дату операции и/или количество.',
                'Исторический вклад = продажи + возвраты + расходы − COGS по дате операции.',
                'Укажите себестоимость и её даты действия; проверьте корректность количеств.',
                'Нельзя считать приблизительную маржу фактической.',sales,importance=9)
            continue
        if overlap or sum(r['quantity'] for r in sales)<0:
            continue
        revenue=sum(r['amount_kopeks'] for r in sales)
        exp=sum(r['amount_kopeks'] for r in rows if r['category'] in COST_CATEGORIES)
        cogs=sum(r['quantity']*unit_cost_at(costs,sku,r['date']) for r in sales)
        result=revenue+exp-cogs
        if result<0:
            signal('NEGATIVE_SKU','Товар приносит отрицательный вклад',sku,-result,
                'исторический отрицательный вклад за загруженный период','high',
                'Сумма выручки после возвратов, назначенных расходов и указанной себестоимости отрицательная.',
                'Продажи + возвраты + назначенные расходы − Σ(quantity × unit_cost на дату операции).',
                'Проверьте рекламные расходы, комиссии, ценовую и закупочную политику; отдельно смоделируйте результат действий.',
                'Исторический убыток не является достижимой экономией. Общие расходы не распределены.',rows,importance=8)
        dates=sorted(set(r['date'] for r in sales))
        if len(dates)<2:
            continue
        end=max(r['date'] for r in rows)
        curr_start=(date.fromisoformat(end)-timedelta(days=13)).isoformat()
        prev_start=(date.fromisoformat(end)-timedelta(days=27)).isoformat()
        curr=[r for r in rows if curr_start<=r['date']<=end]
        prev=[r for r in rows if prev_start<=r['date']<curr_start]
        for cat,code,title in [('logistics','LOGISTICS_RATE_SPIKE','Доля логистики выросла'),
                               ('advertising','ADS_RATE_SPIKE','Рекламные расходы выросли относительно продаж')]:
            # Net revenue denominator includes returns. Still a ratio, not causality.
            curr_rev=sum(r['amount_kopeks'] for r in curr if r['category'] in ('sale','return'))
            prev_rev=sum(r['amount_kopeks'] for r in prev if r['category'] in ('sale','return'))
            curr_spend=-sum(r['amount_kopeks'] for r in curr if r['category']==cat)
            prev_spend=-sum(r['amount_kopeks'] for r in prev if r['category']==cat)
            curr_sales=sum(r['quantity'] or 0 for r in curr if r['category']=='sale')
            prev_sales=sum(r['quantity'] or 0 for r in prev if r['category']=='sale')
            if curr_rev<=0 or prev_rev<=0 or min(curr_sales,prev_sales)<3 or curr_spend<=0 or prev_spend<=0:
                continue
            current_ratio=curr_spend/curr_rev
            previous_ratio=prev_spend/prev_rev
            delta=current_ratio-previous_ratio
            if delta>=0.035:
                equivalent=round(delta*curr_rev)
                signal(code,title,sku,equivalent,'условный эквивалент отклонения за последние 14 дней','low',
                  f'Доля расходов: {previous_ratio*100:.1f}% → {current_ratio*100:.1f}% за два периода по 14 дней. Сравнение не доказывает потери.',
                  '(текущая доля − предыдущая доля) × текущая выручка после возвратов.',
                  'Проверьте тарифы, логистику, рекламу, сезонность, цены и эффект на объём заказов.',
                  'Снижение рекламных затрат может снизить заказы. Эквивалент отклонения не является экономией.',curr+prev,importance=5 if cat=='logistics' else 4)
    grouped=defaultdict(list)
    for r in entries:
        if r.get('operation_id'):
            grouped[(r['operation_id'],r['category'],r['amount_kopeks'])].append(r)
    for (op,cat,amount),rows in grouped.items():
        if len(rows)>1:
            signal('REPEATED_OPERATION_ID','Повторяющийся идентификатор операции',rows[0]['sku'],None,
                'контроль качества импорта','medium',
                f'Идентификатор {op[:100]} встречается {len(rows)} раза. Это может быть законное повторение.',
                'Идентификатор + категория + сумма совпадают, но не обязательно уникальны.',
                'Проверьте первичные документы, не направляйте претензии автоматически.',
                'Нет оснований заявлять сумму к возврату.',rows,importance=3)
    signals.sort(key=lambda r:(-r['priority'],r['sku'] or '',r['code']))
    return signals
