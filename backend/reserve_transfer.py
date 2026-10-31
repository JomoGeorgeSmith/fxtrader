"""Explore a same-currency reserve transfer without assuming access or delivery."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json

from .calculations import CURRENCIES


def reserve_candidates(data,result,rules):
    """Numerical headroom only; reserve permission and transfer timing are unknown."""
    candidates=[]
    for position in result['positions']:
        currency=position['currency']
        gap=max(0,position['minimum']-position['projected_low'])
        if not gap:
            continue
        reserve=sum(Decimal(str(row['balance'])) for row in data['reserves'] if row['currency']==currency)
        protected=Decimal(str(rules['protected_reserve'][currency]))
        headroom=max(Decimal(0),reserve-protected)
        if headroom:
            candidates.append(dict(currency=currency,reserve_balance=float(reserve),protected_minimum=float(protected),
                arithmetic_headroom=float(headroom),baseline_buffer_gap=gap,
                gap_without_expected_receipts=max(0,position['minimum']-position.get('confirmed_receipts_low',position['projected_low'])),
                note='Numerical headroom only. Reserve release authority and arrival before the shortfall are unknown.'))
    return candidates


def assess_reserve_transfer(data,result,rules,catalog,currency,amount,arrival_date,expected_receipt_delay_days=3):
    """Conditional forecast, never a verified treasury instruction or passing FX trade."""
    if currency not in CURRENCIES:
        raise ValueError('Choose a supported currency')
    try:
        transfer=Decimal(str(amount))
        if not transfer.is_finite() or transfer<=0 or transfer!=transfer.quantize(Decimal('.01')):
            raise ValueError('Transfer amount must be positive with at most two decimals')
    except (InvalidOperation,TypeError) as exc:
        raise ValueError('Transfer amount must be positive with at most two decimals') from exc
    if isinstance(expected_receipt_delay_days,bool) or not isinstance(expected_receipt_delay_days,int) or not 1<=expected_receipt_delay_days<=28:
        raise ValueError('Choose a nonzero Expected-receipt delay up to 28 days')
    dates=sorted({row['date'] for row in result['forecast']})
    if arrival_date not in dates:
        raise ValueError('Arrival must be within the saved forecast')
    position=next(p for p in result['positions'] if p['currency']==currency)
    reserve_rows=[row for row in data['reserves'] if row['currency']==currency]
    reserve=sum((Decimal(str(row['balance'])) for row in reserve_rows),Decimal(0))
    protected=Decimal(str(rules['protected_reserve'][currency]))
    headroom=max(Decimal(0),reserve-protected)
    floor=Decimal(str(position['minimum']))
    rows=[row for row in result['forecast'] if row['currency']==currency]
    first=dates[0] if Decimal(str(position['available']))<floor else next((row['date'] for row in rows if Decimal(str(row['balance']))<floor),None)
    scenarios={}
    for mode in ('baseline','delayed_expected_receipts','without_expected_receipts'):
        balances=[]
        for row in rows:
            day=row['date']
            value=Decimal(str(row['balance']))
            if mode!='baseline':
                for receipt in data['receipts']:
                    if receipt['currency']!=currency or receipt['status']!='Expected' or not dates[0]<=receipt['value_date']<=dates[-1]:
                        continue
                    amount_received=Decimal(str(receipt['amount']))
                    if receipt['value_date']<=day:value-=amount_received
                    delayed=(date.fromisoformat(receipt['value_date'])+timedelta(days=expected_receipt_delay_days)).isoformat()
                    if mode=='delayed_expected_receipts' and delayed<=day:value+=amount_received
            balances.append(value+(transfer if day>=arrival_date else 0))
        # When arrival is on day one, this is explicitly an assumption that
        # credit happens before that day's obligations; no intraday ordering.
        opening=Decimal(str(position['available']))+(transfer if arrival_date==dates[0] else 0)
        low=min([opening]+balances)
        scenarios[mode]=dict(operating_low_if_arrives=float(low),remaining_buffer_gap=float(max(Decimal(0),floor-low)),
            operating_buffer_preserved_if_arrives=low>=floor)
    prefix='reserve.'+hashlib.sha256(json.dumps([currency,str(transfer),arrival_date,expected_receipt_delay_days]).encode()).hexdigest()[:12]
    sources=sorted({row['_source'] for row in reserve_rows+data['accounts'] if row['currency']==currency})
    facts=[]
    def fact(field,label,value,unit):
        item=dict(id=f'{prefix}.{field}',label=label,value=value,unit=unit,currency=currency,sources=sources,rules=['protected_reserve','minimum_available'])
        catalog[item['id']]=item
        facts.append(item)
    for field,label,value in [('amount','Hypothetical reserve transfer',float(transfer)),('reserve_balance','Reserve opening balance',float(reserve)),
                              ('protected_minimum','Synthetic reserve floor',float(protected)),('arithmetic_headroom','Reserve above synthetic floor',float(headroom)),
                              ('reserve_after','Reserve balance if transferred',float(reserve-transfer))]:
        fact(field,label,value,'money')
    fact('arrival_date','Assumed arrival, not verified',arrival_date,'date')
    for mode,scenario in scenarios.items():
        fact(f'{mode}.operating_low','Operating low if transfer arrives before daily cash flows',scenario['operating_low_if_arrives'],'money')
        fact(f'{mode}.remaining_buffer_gap','Remaining operating cushion gap if it arrives',scenario['remaining_buffer_gap'],'money')
    return dict(scenario_id=prefix,currency=currency,amount=float(transfer),arrival_date=arrival_date,
        reserve_balance=float(reserve),protected_minimum=float(protected),arithmetic_headroom=float(headroom),
        reserve_after=float(reserve-transfer),reserve_floor_preserved=transfer<=headroom,
        first_modeled_buffer_breach=first,arrival_before_or_on_breach=first is None or arrival_date<=first,
        scenarios=scenarios,transfer_authority_confirmed=False,delivery_timing_confirmed=False,
        same_day_credit_before_obligations_confirmed=False,executable_transfer_verified=False,
        note='Numerical scenario only. Reserve rights, permitted release, interbank transfer timing and intraday credit are not in the workbooks. No transfer is executed or approved.',
        facts=facts)


def reserve_transfer_tool(data,result,rules,catalog):
    return dict(description='Explore a conditional same-currency transfer from a recorded reserve into operating cash. Checks the synthetic reserve floor and baseline/receipt-stress cushions. Does NOT verify authority, bank delivery or intraday availability and NEVER executes a transfer.',
        parameters={'type':'object','properties':{'currency':{'type':'string','enum':list(CURRENCIES)},
            'amount':{'type':'number','exclusiveMinimum':0},'arrival_date':{'type':'string','description':'Assumed credit date within the snapshot forecast; not bank-confirmed.'},
            'expected_receipt_delay_days':{'type':'integer','minimum':1,'maximum':28}},
            'required':['currency','amount','arrival_date','expected_receipt_delay_days'],'additionalProperties':False},
        handler=lambda **kwargs:assess_reserve_transfer(data,result,rules,catalog,**kwargs))
