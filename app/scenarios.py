"""Assumption-driven, deterministic scenario comparison (not predictions).

The operator specifies BOTH the baseline and the alternative. This engine never
invents demand elasticity, supplier rebates, counterfactuals or recovered money.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any
from pydantic import BaseModel, Field
from .ingest import parse_money, IngestError

class ScenarioCase(BaseModel):
    label: str = Field(min_length=1,max_length=80)
    paid_units: int = Field(ge=0,le=5_000_000,description='Net paid units after returns, not orders placed')
    realized_price_rub: str = Field(max_length=25,description='Realized revenue per paid unit after discounts/returns')
    unit_cogs_rub: str = Field(max_length=25)
    commission_percent: str = Field(max_length=20,description='Percentage of realized sales')
    logistics_unit_rub: str = Field(max_length=25)
    storage_unit_rub: str = Field(default='0',max_length=25)
    advertising_budget_rub: str = Field(default='0',max_length=25)
    other_period_costs_rub: str = Field(default='0',max_length=25)

class ScenarioRequest(BaseModel):
    baseline: ScenarioCase
    alternative: ScenarioCase
    notes: str = Field(default='',max_length=800)
    period: str = Field(default='calendar month',max_length=80)


def _to_percent(value: str) -> Decimal:
    try:
        p=Decimal(str(value).replace(',','.').strip())
    except InvalidOperation as exc:
        raise IngestError('Commission percent must be numeric') from exc
    if not p.is_finite() or p<0 or p>100:
        raise IngestError('Commission percent must be between 0 and 100')
    return p


def calc(case: ScenarioCase) -> dict[str,Any]:
    fields=['realized_price_rub','unit_cogs_rub','logistics_unit_rub','storage_unit_rub',
            'advertising_budget_rub','other_period_costs_rub']
    values={f:parse_money(getattr(case,f)) for f in fields}
    if any(v<0 for v in values.values()):
        raise IngestError('Scenario costs and realized price cannot be negative')
    q=case.paid_units
    commission=_to_percent(case.commission_percent)
    revenue=q*values['realized_price_rub']
    commission_total=int((Decimal(revenue)*commission/Decimal('100')).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    cogs=q*values['unit_cogs_rub']
    logistics=q*values['logistics_unit_rub']
    storage=q*values['storage_unit_rub']
    ad=values['advertising_budget_rub']
    other=values['other_period_costs_rub']
    contribution=revenue-commission_total-cogs-logistics-storage-ad-other
    unit_contrib=(Decimal(values['realized_price_rub'])*(Decimal('1')-commission/100)
                  -values['unit_cogs_rub']-values['logistics_unit_rub']-values['storage_unit_rub'])
    fixed=ad+other
    if unit_contrib<=0:
        breakeven=None
    else:
        breakeven=int((Decimal(fixed)/unit_contrib).to_integral_value(rounding='ROUND_CEILING'))
    rub=lambda k:round(k/100,2)
    return {'label':case.label,'paid_units':q,'revenue_rub':rub(revenue),
            'commission_rub':rub(commission_total),'cogs_rub':rub(cogs),
            'logistics_rub':rub(logistics),'storage_rub':rub(storage),
            'advertising_rub':rub(ad),'other_period_costs_rub':rub(other),
            'contribution_rub':rub(contribution),
            'break_even_paid_units':breakeven,
            'margin_on_revenue_percent':round(contribution/revenue*100,2) if revenue>0 else None}


def compare(payload:ScenarioRequest) -> dict[str,Any]:
    a=calc(payload.baseline)
    b=calc(payload.alternative)
    delta=int(round((b['contribution_rub']-a['contribution_rub'])*100))
    return {
        'baseline':a,'alternative':b,'delta_rub':round(delta/100,2),
        'benefit_class':'counterfactual_user_assumptions',
        'verified_realized_profit_rub':None,
        'guaranteed_savings_rub':None,
        'confidence':'not_estimated',
        'period':payload.period,
        'notes':payload.notes,
        'method':'Units × realized price − commission × revenue − units × (COGS + logistics + storage) − ads − other period costs',
        'limitations':[
            'Все значения — допущения пользователя. Это не прогноз и не подтвержденная экономия.',
            'Paid units means fulfilled, kept units after returns. The original return/refund logistics needs separate accounting.',
            'Реакция спроса на цену, каннибализация, сезонность и динамика позиций не моделируются автоматически.',
            'Не включены налоги, финансирование, зарплаты и прочие расходы, если не заданы в other_period_costs.',
            'Для атрибуции дополнительной прибыли нужен период после внедрения и обоснованная контрольная база.'
        ]}
