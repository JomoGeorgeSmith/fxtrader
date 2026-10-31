"""Assess a model-proposed plan; does not search, rank, approve or execute plans."""
import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING

from .calculations import CURRENCIES
from .recommended_action import sheet_rate
from .fx_market import fee as simulation_fee,source_capacity,settlement_check,exposure_check,project

def assess_funding_plan(data,result,rules,catalog,source,obligation_id,target_amount,settlement_date,fee_source_amount=0,expected_receipt_delay_days=3):
    payments=[r for r in data['payments'] if r['id']==obligation_id]
    if len(payments)!=1 or payments[0]['status'] in ('Settled','Cancelled'):
        raise ValueError('Choose one unique active payment ID from the snapshot')
    obligation=payments[0]
    target=obligation['currency']
    if source not in CURRENCIES or source==target:
        raise ValueError('Source must be a supported currency different from the obligation currency')
    amount,fee=Decimal(str(target_amount)),Decimal(str(fee_source_amount))
    if not amount.is_finite() or amount<=0 or amount!=amount.quantize(Decimal('.01')):
        raise ValueError('Target amount must be positive and have at most two decimal places')
    if not fee.is_finite() or fee<0 or fee!=fee.quantize(Decimal('.01')):
        raise ValueError('Source-currency fee must be nonnegative and have at most two decimal places')
    if isinstance(expected_receipt_delay_days,bool) or not isinstance(expected_receipt_delay_days,int) or not 0<=expected_receipt_delay_days<=28:
        raise ValueError('Expected-receipt delay must be an integer from zero to twenty-eight days')
    dates=sorted({r['date'] for r in result['forecast']})
    settlement=date.fromisoformat(settlement_date).isoformat()
    due=obligation['value_date']
    if settlement not in dates or due not in dates:
        raise ValueError('Settlement and obligation dates must be within the snapshot forecast')
    rates={r['currency']:r for r in data['rates']}
    sell_source=Decimal(1) if source=='JMD' else Decimal(str(rates[source]['buy']))
    buy_target=Decimal(1) if target=='JMD' else Decimal(str(rates[target]['sell']))
    principal=(amount*buy_target/sell_source).quantize(Decimal('.01'),rounding=ROUND_CEILING)
    debit=principal+fee
    positions={p['currency']:p for p in result['positions']}
    series={(r['currency'],r['date']):Decimal(str(r['balance'])) for r in result['forecast']}
    # Move only Expected receipts; preserve all payments and confirmed inflows.
    def receipt_adjustment(currency,day,mode):
        change=Decimal(0)
        for r in data['receipts']:
            if r['currency']!=currency or r['status']!='Expected' or not dates[0]<=r['value_date']<=dates[-1]:
                continue
            if r['value_date']<=day:
                change-=Decimal(str(r['amount']))
            delayed=(date.fromisoformat(r['value_date'])+timedelta(days=expected_receipt_delay_days)).isoformat()
            if mode=='delayed_expected_receipts' and delayed<=day:
                change+=Decimal(str(r['amount']))
        return change
    def first_shortfall(mode):
        floor=Decimal(str(positions[target]['minimum']))
        if Decimal(str(positions[target]['available']))<floor:
            return dates[0]
        return next((day for day in dates if series[target,day]+(receipt_adjustment(target,day,mode) if mode!='baseline' else 0)<floor),None)
    baseline_breach=first_shortfall('baseline')
    cautious_breach=first_shortfall('without_expected_receipts')
    breaches=[day for day in (baseline_breach,cautious_breach) if day]
    earliest=min(breaches) if breaches else None
    timing=dict(baseline_first_shortfall=baseline_breach,without_expected_receipts_first_shortfall=cautious_breach,
        earliest_modeled_shortfall=earliest,
        calendar_days_before_first_shortfall=(date.fromisoformat(earliest)-date.fromisoformat(settlement)).days if earliest else None,
        settlement_is_weekday=date.fromisoformat(settlement).weekday()<5,bank_delivery_confirmed=False)
    scenarios={}
    for mode in ('baseline','delayed_expected_receipts','without_expected_receipts'):
        daily=[]
        for day in dates:
            row={'date':day}
            for c,delta in ((source,-debit),(target,amount)):
                balance=series[c,day]
                if mode!='baseline':
                    balance+=receipt_adjustment(c,day,mode)
                row[c]=balance+(delta if day>=settlement else 0)
            daily.append(row)
        # Check source spending against the previous day's closing funds rather
        # than assuming same-day deposits arrive before the conversion.
        prior=[r[source] for r in daily if r['date']<settlement]
        source_before=prior[-1] if prior else Decimal(str(positions[source]['available']))
        source_low=min([Decimal(str(positions[source]['available'])),source_before-debit]+[r[source] for r in daily])
        target_low=min([Decimal(str(positions[target]['available']))]+[r[target] for r in daily])
        target_to_due=min([Decimal(str(positions[target]['available']))]+[r[target] for r in daily if r['date']<=due])
        source_floor=Decimal(str(positions[source]['minimum']))
        target_floor=Decimal(str(positions[target]['minimum']))
        scenarios[mode]=dict(source_low=float(source_low),target_low=float(target_low),
            source_buffer_headroom=float(source_low-source_floor),target_buffer_headroom=float(target_low-target_floor),
            source_funds_available_before_settlement=source_before>=debit,
            source_buffer_preserved=source_low>=source_floor,target_buffer_preserved=target_low>=target_floor,
            target_cash_nonnegative_through_obligation=settlement<=due and target_to_due>=0,
            target_remaining_buffer_gap=float(max(Decimal(0),target_floor-target_low)),
            balances=[{k:float(v) if isinstance(v,Decimal) else v for k,v in r.items()} for r in daily])
    changes={source:-debit,target:amount}
    capital=Decimal(str(data['risk'][0]['capital_base_jmd']))
    pct=sum((abs((Decimal(str(r['net_fx_exposure']))+changes.get(r['currency'],Decimal(0)))*Decimal(str(rates[r['currency']]['sell']))) for r in data['risk']),Decimal(0))/capital*100
    risk=exposure_check(data,rules,changes)
    exposure_ok=risk['no_exposure_breach_worsened'] if data.get('fx_terms') else pct<=Decimal(str(rules['max_absolute_fx_exposure_pct']))
    on_time=settlement<=due
    key=json.dumps([source,obligation_id,str(amount),settlement,str(fee),expected_receipt_delay_days])
    prefix='plan.'+hashlib.sha256(key.encode()).hexdigest()[:12]
    sources=sorted({r['_source'] for name in ('accounts','payments','receipts','rates','risk') for r in data[name]})
    facts=[]
    def fact(name,label,value,unit,currency=None):
        display=f'{currency} {Decimal(str(value)):,.2f}' if unit=='money' else f'{Decimal(str(value)):.1f}%' if unit=='percent' else str(value)
        item=dict(id=f'{prefix}.{name}',label=label,value=value,unit=unit,currency=currency,display=display,sources=sources,rules=['minimum_available','max_absolute_fx_exposure_pct'])
        facts.append(item)
        catalog[item['id']]=item
    for name,label,value,c in [('source_principal','Source currency to sell, excluding fee',float(principal),source),('source_debit','Total source debit including supplied fee',float(debit),source),('source_fee','Supplied fee; zero means omitted costs, not verified fee-free execution',float(fee),source),('target_received','Destination currency acquired, not source currency sold',float(amount),target),('obligation_amount','Recorded payment amount',obligation['amount'],target)]:
        fact(name,label,value,'money',c)
    fact('settlement_date','Proposed same-day debit/credit settlement',settlement,'date')
    fact('due_date','Recorded obligation deadline',due,'date')
    if earliest:
        fact('first_shortfall','Earliest baseline or absent-Expected-receipt buffer shortfall before this hypothetical conversion',earliest,'date')
        fact('arrival_margin_days','Calendar days from proposed arrival to earliest modeled shortfall; not verified bank lead time',timing['calendar_days_before_first_shortfall'],'number')
    fact('delay_days','Assumed delay applied to Expected receipts only',expected_receipt_delay_days,'number')
    fact('exposure_pct','Synthetic FX exposure after proposed conversion; not a dated risk forecast',float(pct),'percent')
    reference_rate=sheet_rate(data,source,target)
    fact('sheet_rate',f'{target} per one {source}, from Treasury sheet buying/selling rates; not an executable quote',float(reference_rate),'rate',target)
    for mode,scenario in scenarios.items():
        for field,c in [('source_low',source),('target_low',target),('source_buffer_headroom',source),('target_buffer_headroom',target),('target_remaining_buffer_gap',target)]:
            fact(f'{mode}.{field}',f'{mode}: {field}',scenario[field],'money',c)
        scenario['passes_modeled_checks']=on_time and exposure_ok and scenario['source_funds_available_before_settlement'] and scenario['source_buffer_preserved'] and scenario['target_buffer_preserved'] and scenario['target_cash_nonnegative_through_obligation']
    simulation_checks=None
    if data.get('fx_terms'):
        cost=simulation_fee(data,source,principal)
        cash=project(data,result,rules,[(settlement,source,-debit),(settlement,target,amount),(settlement,'JMD',-cost)],{source,target,'JMD'},expected_receipt_delay_days)
        simulated_timing=settlement_check(data,source,target,settlement,dates[0])
        timing['simulation']=simulated_timing
        simulation_checks=dict(source_account_capacity=debit+(cost if source=='JMD' else 0)<=source_capacity(data,source,settlement),
            fee_account_capacity=cost+(debit if source=='JMD' else 0)<=source_capacity(data,'JMD',settlement),
            currency_limits_preserved=risk['no_exposure_breach_worsened'],
            simulated_settlement_ready=simulated_timing['lead_time_satisfied'] and simulated_timing['arrival_is_business_day'])
        fact('simulation_fee_jmd','Additional simulated JMD fee from Currency_Terms; supplied source fee is separate',float(cost),'money','JMD')
        for mode,scenario in scenarios.items():
            scenario['simulation_cash_checks']=cash[mode]
            scenario['passes_modeled_checks']=scenario['passes_modeled_checks'] and cash[mode]['passes_cash_checks'] and all(simulation_checks.values())
    return dict(plan_id=prefix,source=source,target=target,obligation_id=obligation_id,obligation_status=obligation['status'],
        requested_target_amount=float(amount),source_principal=float(principal),total_source_debit=float(debit),
        settlement_date=settlement,obligation_date=due,settles_by_obligation=on_time,exposure_pct=float(pct),exposure_within_demo_limit=risk['aggregate_within_limit'],risk=risk,sheet_rate=str(reference_rate),
        scenarios=scenarios,timing=timing,facts=facts,simulation_checks=simulation_checks,
        assumptions=['Hypothetical assessment only: no execution, approval, profitability or optimal timing claim.',
            'Uses current bank-perspective sheet bid/ask rates even for future settlement; NOT a forward quote or executable offer.',
            'Source principal rounded up to cents. Supplied fees only; zero fee means costs are unspecified. No carry or funding cost is modeled.',
            'One conversion assessed independently, not accumulated with other hypothetical plans. Existing payment is already in the forecast and is not subtracted twice.',
            'Daily-net snapshot flows only. Source debit tested against prior close; same-day destination credit before payment is assumed, not verified intraday.',
            'Arrival date is proposed, not bank-confirmed. Optional simulation terms/calendars are checked when supplied; these are assumptions, not verified delivery rules.',
            'Baseline includes Expected receipts and tentative payments. Delayed scenario shifts Expected receipts only; confirmed receipts can still fail.',
            'Reserve balances and separate LCR/NSFR inputs are unchanged. Independent risk inputs receive a conversion-only adjustment, not a full future exposure forecast.'])

def funding_plan_tool(data,result,rules,catalog):
    properties={'source':{'type':'string','enum':list(CURRENCIES)},'obligation_id':{'type':'string'},
        'target_amount':{'type':'number','exclusiveMinimum':0,'description':'Amount of the obligation currency to acquire, NOT source currency to sell. May be partial.'},
        'settlement_date':{'type':'string','description':'Proposed arrival date, ISO within forecast. Choose and test an earlier date before the first relevant shortfall where feasible; source debit and destination credit occur together. Bank delivery is unconfirmed.'},
        'fee_source_amount':{'type':'number','minimum':0,'description':'Explicit source-currency fee assumption; zero means costs unspecified.'},
        'expected_receipt_delay_days':{'type':'integer','minimum':0,'maximum':28}}
    return dict(description='Assess YOUR proposed dated funding plan for an active payment ID. Specify target currency amount to acquire; calculates required source debit, dated cash forecasts, buffer gaps, delay sensitivity and synthetic exposure. Does not search for or recommend plans. Future rates illustrative, no execution.',
        parameters={'type':'object','properties':properties,'required':list(properties),'additionalProperties':False},
        handler=lambda **args:assess_funding_plan(data,result,rules,catalog,**args))
