"""Resolve the analyst's chosen assessment into calculation-backed card fields."""
from decimal import Decimal


def sheet_rate(data,source,target):
    rates={row['currency']:row for row in data['rates']}
    source_price=Decimal(1) if source=='JMD' else Decimal(str(rates[source]['buy']))
    target_price=Decimal(1) if target=='JMD' else Decimal(str(rates[target]['sell']))
    return source_price/target_price


def recommended_action(assessment_id,trace,data):
    """Never choose a trade or prevent other suggestions from publishing."""
    if not assessment_id:
        return None,None
    matches=[t['result'] for t in trace if t['name'] in ('assess_funding_plan','simulate_fx_conversion','assess_fx_trade','assess_receipt_match','assess_round_trip','assess_forward_hedge')
        and isinstance(t.get('result'),dict) and (t['result'].get('plan_id') or t['result'].get('scenario_id'))==assessment_id]
    if not matches:
        return None,'The selected action has no matching calculation result. No recommended action is shown.'
    result=matches[-1]
    if 'action_fields' in result:
        if not result.get('passes_modeled_checks'):
            return None,'The selected scenario fails its modeled checks. The insight is retained, without a recommended action.'
        return dict(assessment_id=assessment_id,kind=result['kind'],display_rows=result['action_fields'],
            rate_basis='simulated_forward' if result['kind']=='forward' else 'treasury_sheet' if result['kind']=='conversion' else 'not_applicable',
            settlement_date=result.get('settlement_date'),fact_ids=[f['id'] for f in result['facts']]),None
    source,target=result['source'],result['target']
    if result.get('plan_id'):
        passes=result.get('scenarios',{}).get('baseline',{}).get('passes_modeled_checks',False)
        sold=result['source_principal']
        bought=result['requested_target_amount']
        fields=('source_principal','target_received','sheet_rate','settlement_date')
        if result.get('timing',{}).get('earliest_modeled_shortfall'):
            fields+=('first_shortfall','arrival_margin_days')
    else:
        position=next((p for p in result.get('positions',[]) if p['currency']==target),None)
        passes=bool(result.get('source_funds_sufficient') and result.get('source_buffer_preserved')
            and result.get('exposure_within_demo_limit') and result.get('passes_simulation_constraints',True) and position and position['projected_low']>=position['minimum'])
        sold=result['source_amount']
        bought=result['target_received']
        fields=('source_amount','target_received','sheet_rate')
    if not passes:
        return None,'The selected plan fails the baseline cash or exposure checks. It is not shown as a recommended action.'
    return dict(assessment_id=assessment_id,buy=dict(currency=target,amount=str(Decimal(str(bought)).quantize(Decimal('.01')))),
        sell=dict(currency=source,amount=str(Decimal(str(sold)).quantize(Decimal('.01')))),
        reference_rate=dict(source=source,target=target,target_per_source=str(sheet_rate(data,source,target))),
        settlement_date=result.get('settlement_date'),
        timing=result.get('timing'),
        rate_basis='treasury_sheet',fact_ids=[f'{assessment_id}.{field}' for field in fields]),None
