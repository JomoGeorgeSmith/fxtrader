"""Deterministic simulation mechanics shared by FX assessments and forecasts."""
from datetime import date,timedelta
from decimal import Decimal,ROUND_CEILING

D=lambda v:Decimal(str(v))
CENT=Decimal('.01')


def price(data,currency,side='sell'):
    return D(1) if currency=='JMD' else D(next(r[side] for r in data['rates'] if r['currency']==currency))


def mid(data,currency):return (price(data,currency,'buy')+price(data,currency,'sell'))/2


def terms(data,currency):
    rows=[r for r in data.get('fx_terms',[]) if r['currency']==currency]
    if len(rows)!=1:raise ValueError('Missing unique simulated Currency_Terms for '+currency)
    return rows[0]


def business_day(data,day,currencies):
    day=date.fromisoformat(day) if isinstance(day,str) else day
    return day.weekday()<5 and not any(r['currency'] in currencies and r['value_date']==day.isoformat() for r in data.get('fx_holidays',[]))


def settlement_check(data,source,target,settlement,trade_date,booking_hour=12,forward=False):
    s,t=terms(data,source),terms(data,target)
    day=date.fromisoformat(trade_date)
    if booking_hour>=min(s['cutoff_hour'],t['cutoff_hour']):day+=timedelta(days=1)
    while not business_day(data,day,(source,target)):day+=timedelta(days=1)
    for _ in range(0 if forward else max(s['spot_business_days'],t['spot_business_days'])):
        day+=timedelta(days=1)
        while not business_day(data,day,(source,target)):day+=timedelta(days=1)
    return dict(earliest_simulated_arrival=day.isoformat(),arrival_is_business_day=business_day(data,settlement,(source,target)),
        lead_time_satisfied=settlement>=day.isoformat(),trade_date=trade_date,assumed_booking_hour=booking_hour,
        simulated_rules_only=True,bank_delivery_confirmed=False)


def source_capacity(data,source,day=None):
    policies={r['account_id']:r for r in data.get('fx_permissions',[])}
    if not policies:raise ValueError('Missing simulated account permissions')
    capacity=sum((min(max(D(0),D(a['balance'])-D(a['holds'])),D(policies[a['account_id']]['daily_debit_limit']))
        for a in data['accounts'] if a['currency']==source and a['account_id'] in policies and policies[a['account_id']]['fx_enabled']),D(0))
    committed=D(0)
    if day:
        for key,status in (('fx_orders','Booked'),('fx_hedges','Active')):
            for row in data.get(key,[]):
                if row['status']!=status or row['settlement_date']!=day:continue
                if row['source']==source:committed+=D(row['source_amount'])
                if source=='JMD':committed+=D(row['fee_jmd'])
    return max(D(0),capacity-committed)


def fee(data,source,source_amount):
    row=terms(data,source)
    return (D(source_amount)*price(data,source,'buy')*D(row['fee_bps'])/10000+D(row['fixed_fee_jmd'])).quantize(CENT,rounding=ROUND_CEILING)


def debit_permissions(data,events):
    grouped={}
    for day,currency,delta in events:
        if delta<0:grouped[day,currency]=grouped.get((day,currency),D(0))-D(delta)
    return all(amount<=source_capacity(data,currency,day) for (day,currency),amount in grouped.items())


def ledger_events(data):
    """Booked cash only; planned orders do not change the bank's current forecast."""
    events=[]
    for key,status in (('fx_orders','Booked'),('fx_hedges','Active')):
        for row in data.get(key,[]):
            if row['status']!=status:continue
            if not row['cash_in_forecast']:
                events.extend([(row['settlement_date'],row['source'],-D(row['source_amount'])),(row['settlement_date'],row['target'],D(row['target_amount']))])
            if not row['fee_payment_id'] and row['fee_jmd']:
                events.append((row['settlement_date'],'JMD',-D(row['fee_jmd'])))
            if key=='fx_hedges' and row['collateral_jmd'] and not row['collateral_release_receipt_id']:
                events.append((row['settlement_date'],'JMD',D(row['collateral_jmd'])))
    return events


def exposure_check(data,rules,changes):
    exposures={r['currency']:D(r['net_fx_exposure'])+D(changes.get(r['currency'],0)) for r in data['risk']}
    limits={r['currency']:D(r['max_absolute_exposure']) for r in data.get('fx_limits',[])}
    pct=sum((abs(amount*price(data,c)) for c,amount in exposures.items()),D(0))/D(data['risk'][0]['capital_base_jmd'])*100
    return dict(exposure_pct=float(pct),aggregate_within_limit=pct<=D(rules['max_absolute_fx_exposure_pct']),
        currency_exposures={c:float(v) for c,v in exposures.items()},currency_limits_preserved=all(abs(v)<=limits[c] for c,v in exposures.items() if c in limits),
        note='Current Risk_Inputs plus this independent scenario; not a full dated regulatory risk position.')


def project(data,result,rules,events,affected,expected_delay_days=3,debits=None):
    positions={r['currency']:r for r in result['positions']}
    dates=sorted({r['date'] for r in result['forecast']})
    base={(r['currency'],r['date']):D(r['balance']) for r in result['forecast']}
    if any(day not in dates for day,_,_ in events):raise ValueError('Scenario cash legs must fall within the snapshot forecast')
    actual_debits=[(day,c,delta) for day,c,delta in events if delta<0] if debits is None else debits
    scenarios={}
    for mode in ('baseline','delayed_expected_receipts','without_expected_receipts'):
        lows={c:D(positions[c]['available']) for c in affected};prior=dict(lows);rows=[];liquid=True
        for day in dates:
            balances={}
            for c in affected:
                balance=base[c,day]
                if mode!='baseline':
                    for receipt in data['receipts']:
                        if receipt['currency']!=c or receipt['status']!='Expected' or not dates[0]<=receipt['value_date']<=dates[-1]:continue
                        if receipt['value_date']<=day:balance-=D(receipt['amount'])
                        delayed=(date.fromisoformat(receipt['value_date'])+timedelta(days=expected_delay_days)).isoformat()
                        if mode=='delayed_expected_receipts' and delayed<=day:balance+=D(receipt['amount'])
                balance+=sum((D(delta) for when,ccy,delta in events if ccy==c and when<=day),D(0))
                debit=-sum((D(delta) for when,ccy,delta in actual_debits if ccy==c and when==day and delta<0),D(0))
                if debit:
                    liquid=liquid and prior[c]>=debit
                    lows[c]=min(lows[c],prior[c]-debit)
                lows[c]=min(lows[c],balance);balances[c]=float(balance);prior[c]=balance
            rows.append(dict(date=day,**balances))
        preserved=all(v>=D(positions[c]['minimum']) for c,v in lows.items())
        scenarios[mode]=dict(lows={c:float(v) for c,v in lows.items()},cash_cushions_preserved=preserved,
            source_funds_before_debit=liquid,passes_cash_checks=preserved and liquid,balances=rows)
    return scenarios
