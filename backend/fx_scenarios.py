"""Analyst-selected what-ifs for the Excel bank; never select or execute trades."""
import hashlib
import json
from datetime import date,timedelta
from decimal import ROUND_CEILING,DecimalException

from .fx_market import D,CENT,price,mid,terms,fee,source_capacity,settlement_check,exposure_check,project,debit_permissions
from .recommended_action import sheet_rate

CURRENCIES=('JMD','USD','GBP','CAD')


def money(value,positive=True):
    try:
        value=D(value)
        valid=value.is_finite() and value==value.quantize(CENT) and not (value<=0 if positive else value<0)
    except (DecimalException,ValueError,TypeError):valid=False
    if not valid:
        raise ValueError('Amounts must be finite, positive (or zero where permitted), with at most two decimal places')
    return value


def unique(data,key,id):
    rows=[r for r in data.get(key,[]) if r['id']==id]
    if len(rows)!=1:raise ValueError(f'Choose one existing {key} ID')
    return rows[0]


def active(data,key,id):
    row=unique(data,key,id)
    if row['status'] in ('Cancelled','Settled'):raise ValueError('Choose an active cash flow')
    return row


def snapshot_dates(result):return sorted({r['date'] for r in result['forecast']})


def date_in(result,value):
    parsed=date.fromisoformat(value).isoformat()
    if parsed not in snapshot_dates(result):raise ValueError('Choose a date within the snapshot forecast')
    return parsed


def trade_events(row,factor=1):
    day=row['settlement_date'];f=D(factor)
    return [(day,row['source'],-D(row['source_amount'])*f),(day,row['target'],D(row['target_amount'])*f),(day,'JMD',-D(row['fee_jmd'])*f)]


def fmt(currency,value):return f'{currency} {D(value):,.2f}'


def publish(data,rules,catalog,kind,arguments,report,values,action):
    prefix='fx.'+hashlib.sha256(json.dumps([kind,arguments],sort_keys=True,default=str).encode()).hexdigest()[:12]
    sources=sorted({r['_source'] for rows in data.values() for r in rows})
    facts=[]
    for key,label,value,unit,currency in values:
        if hasattr(value,'is_finite'):value=str(value)
        fact=dict(id=f'{prefix}.{key}',label=label,value=value,unit=unit,currency=currency,
            display=f'{currency} {D(value):,.2f}' if unit=='money' else str(value),sources=sources,
            rules=['minimum_available','max_absolute_fx_exposure_pct'])
        catalog[fact['id']]=fact;facts.append(fact)
    return dict(scenario_id=prefix,kind=kind,**report,facts=facts,
        action_fields=action,
        assumptions=['Independent hypothetical plan; its own legs are checked together. No trade, allocation or cancellation is executed.' if kind=='allocation_plan' else 'Independent hypothetical change; no trade, allocation or cancellation is executed.',
            'All new terms, calendars, confirmations and prices are synthetic assumptions, not real bank commitments.',
            'Daily cash checks use prior-close funding, not verified intraday ordering. Protected reserves and LCR/NSFR are unchanged.',
            'Current exposure receives this scenario change only; this is not a complete dated risk/capital forecast.'])


def assess_fx_trade(data,result,rules,catalog,source,target,target_amount,settlement_date,booking_hour=12,expected_receipt_delay_days=3):
    if source not in CURRENCIES or target not in CURRENCIES or source==target:raise ValueError('Choose two different supported currencies')
    amount=money(target_amount);day=date_in(result,settlement_date)
    if isinstance(booking_hour,bool) or not isinstance(booking_hour,int) or not 0<=booking_hour<=23:raise ValueError('Invalid booking hour')
    if isinstance(expected_receipt_delay_days,bool) or not isinstance(expected_receipt_delay_days,int) or not 1<=expected_receipt_delay_days<=28:raise ValueError('Use a nonzero integer receipt delay up to 28 days')
    principal=(amount/sheet_rate(data,source,target)).quantize(CENT,rounding=ROUND_CEILING)
    cost=fee(data,source,principal)
    events=[(day,source,-principal),(day,target,amount),(day,'JMD',-cost)]
    scenarios=project(data,result,rules,events,{source,target,'JMD'},expected_receipt_delay_days)
    capacity=source_capacity(data,source,day)
    debit=principal+(cost if source=='JMD' else D(0))
    timing=settlement_check(data,source,target,day,snapshot_dates(result)[0],booking_hour)
    risk=exposure_check(data,rules,{source:-principal,target:amount})
    checks=dict(account_permission_and_debit_limit=debit<=capacity,fee_account_capacity=cost+(principal if source=='JMD' else 0)<=source_capacity(data,'JMD',day),simulated_settlement_ready=timing['lead_time_satisfied'] and timing['arrival_is_business_day'],
        all_debit_account_limits=debit_permissions(data,events),exposure_preserved=risk['no_exposure_breach_worsened'])
    for scenario in scenarios.values():scenario['passes_modeled_checks']=scenario['passes_cash_checks'] and all(checks.values())
    values=[('source_principal','Source principal sold',principal,'money',source),('target_received','Destination acquired',amount,'money',target),
        ('fee_jmd','Simulated fee; no carry/opportunity cost included',cost,'money','JMD'),('settlement_date','Proposed arrival',day,'date',target),
        ('exposure_pct','Aggregate exposure after this independent conversion',risk['exposure_pct'],'percent',None),
        ('sheet_rate','Indicative sheet target per source',str(sheet_rate(data,source,target)),'rate',target)]
    action=[dict(label='Buy',value=fmt(target,amount)),dict(label='Sell',value=fmt(source,principal)),
        dict(label='Treasury reference rate',value=f'{source} 1 buys approximately {target} {sheet_rate(data,source,target):.4f}'),dict(label=f'{target} available by',value=day+' (proposed)')]
    return publish(data,rules,catalog,'conversion',dict(source=source,target=target,target_amount=target_amount,settlement_date=day,booking_hour=booking_hour,expected_receipt_delay_days=expected_receipt_delay_days),
        dict(source=source,target=target,source_principal=float(principal),target_received=float(amount),settlement_date=day,fee_jmd=float(cost),
            scenarios=scenarios,checks=checks,timing=timing,risk=risk,passes_modeled_checks=scenarios['baseline']['passes_modeled_checks']),values,action)


def assess_allocation_plan(data,result,rules,catalog,source,legs,booking_hour=12,expected_receipt_delay_days=3):
    """Test one analyst-selected source sale into payment-linked destinations as a unit."""
    if source not in CURRENCIES or not isinstance(legs,list) or not 2<=len(legs)<=3:
        raise ValueError('Choose a supported source and two or three allocation legs')
    if isinstance(booking_hour,bool) or not isinstance(booking_hour,int) or not 0<=booking_hour<=23:
        raise ValueError('Invalid booking hour')
    if isinstance(expected_receipt_delay_days,bool) or not isinstance(expected_receipt_delay_days,int) or not 1<=expected_receipt_delay_days<=28:
        raise ValueError('Use a nonzero integer receipt delay up to 28 days')
    first=snapshot_dates(result)[0]
    events=[];changes={};allocation=[];timings=[];targets=set();payments=set();totals={};values=[]
    for leg in legs:
        if not isinstance(leg,dict) or set(leg)!={'target','target_amount','settlement_date','obligation_id'}:
            raise ValueError('Each leg needs a destination, amount, arrival date and existing payment ID')
        target=leg['target']
        if target not in CURRENCIES or target==source or target in targets:
            raise ValueError('Choose distinct destination currencies different from the source')
        targets.add(target)
        payment=active(data,'payments',leg['obligation_id'])
        if payment['id'] in payments or payment['currency']!=target:
            raise ValueError('Choose distinct payments in the matching destination currencies')
        payments.add(payment['id'])
        amount=money(leg['target_amount']);day=date_in(result,leg['settlement_date']);due=date_in(result,payment['value_date'])
        allocated=sum((D(row['amount']) for row in data.get('fx_allocations',[]) if row['obligation_id']==payment['id']),D(0))
        covered=sum((D(row['target_amount']) for key,status in (('fx_orders','Booked'),('fx_hedges','Active')) for row in data.get(key,[])
                     if row['obligation_id']==payment['id'] and row['status']==status),D(0))
        if amount>D(payment['amount'])-allocated-covered:
            raise ValueError('Allocation exceeds remaining uncovered payment')
        principal=(amount/sheet_rate(data,source,target)).quantize(CENT,rounding=ROUND_CEILING)
        cost=fee(data,source,principal)
        timing=settlement_check(data,source,target,day,first,booking_hour)
        timings.append(timing)
        events.extend([(day,source,-principal),(day,target,amount),(day,'JMD',-cost)])
        changes[source]=changes.get(source,D(0))-principal
        changes[target]=changes.get(target,D(0))+amount
        totals[day]=totals.get(day,D(0))+principal
        allocation.append(dict(target=target,target_amount=float(amount),source_principal=float(principal),fee_jmd=float(cost),
            settlement_date=day,obligation_id=payment['id'],payment_date=due,reference_rate=str(sheet_rate(data,source,target))))
        values.extend([(f'leg{len(allocation)}_source','Source sold for '+target,principal,'money',source),
            (f'leg{len(allocation)}_target','Amount allocated to '+payment['id'],amount,'money',target),
            (f'leg{len(allocation)}_arrival','Proposed '+target+' arrival',day,'date',target)])
    risk=exposure_check(data,rules,changes)
    prior=D(next(r['net_fx_exposure'] for r in data['risk'] if r['currency']==source))
    total_sold=-changes[source];total_fee=sum((D(leg['fee_jmd']) for leg in allocation),D(0))
    # Include every currency: another shortfall cannot be hidden by omitting it
    # from the analyst's proposed allocation.
    scenarios=project(data,result,rules,events,set(CURRENCIES),expected_receipt_delay_days)
    checks=dict(source_position_reduced=abs(prior-total_sold)<abs(prior),
        all_payments_coverable=all(leg['settlement_date']<leg['payment_date'] for leg in allocation),
        no_duplicate_planned_cover=all(not any(o['status']=='Planned' and o['obligation_id']==leg['obligation_id'] for o in data.get('fx_orders',[])) for leg in allocation),
        source_account_capacity=all(amount+(sum((D(leg['fee_jmd']) for leg in allocation if leg['settlement_date']==day),D(0)) if source=='JMD' else 0)<=source_capacity(data,source,day) for day,amount in totals.items()),
        fee_account_capacity=all(sum((D(leg['fee_jmd']) for leg in allocation if leg['settlement_date']==day),D(0))+(totals[day] if source=='JMD' else 0)<=source_capacity(data,'JMD',day) for day in totals),
        all_debit_account_limits=debit_permissions(data,events),
        simulated_settlement_ready=all(t['lead_time_satisfied'] and t['arrival_is_business_day'] for t in timings),
        exposure_preserved=risk['no_exposure_breach_worsened'])
    for scenario in scenarios.values():scenario['passes_modeled_checks']=scenario['passes_cash_checks'] and all(checks.values())
    values.extend([('total_sold','Total source currency sold',total_sold,'money',source),
        ('total_fee_jmd','Total simulated conversion fees',total_fee,'money','JMD'),
        ('source_exposure_before','Independent source risk position before plan',prior,'money',source),
        ('source_exposure_after','Independent source risk position after plan',prior-total_sold,'money',source),
        ('source_stressed_low','Source cash low without Expected receipts',scenarios['without_expected_receipts']['lows'][source],'money',source)])
    action=[dict(label='Sell '+source,value=fmt(source,total_sold))]
    action.extend(dict(label='For '+leg['target']+' payment',value='Sell '+fmt(source,leg['source_principal'])+' to buy '+fmt(leg['target'],leg['target_amount'])+' by '+leg['settlement_date']+' ('+leg['obligation_id']+')') for leg in allocation)
    action.append(dict(label='Indicative fees',value=fmt('JMD',total_fee)))
    return publish(data,rules,catalog,'allocation_plan',dict(source=source,legs=[dict(target=leg['target'],target_amount=str(money(leg['target_amount'])),settlement_date=leg['settlement_date'],obligation_id=leg['obligation_id']) for leg in legs],booking_hour=booking_hour,expected_receipt_delay_days=expected_receipt_delay_days),
        dict(source=source,legs=allocation,total_sold=float(total_sold),total_fee_jmd=float(total_fee),checks=checks,timing=timings,risk=risk,scenarios=scenarios,
            passes_modeled_checks=all(s['passes_modeled_checks'] for s in scenarios.values()),
            comparison='All legs are tested together against every currency cash cushion, payment coverage, account limits and exposure. No proven profit or live quote; future settlement at current sheet rates is illustrative.'),values,action)


def assess_receipt_match(data,result,rules,catalog,receipt_id,obligation_id,amount,purchase_order_id):
    receipt=active(data,'receipts',receipt_id);payment=active(data,'payments',obligation_id);amount=money(amount)
    if receipt['currency']!=payment['currency']:raise ValueError('Receipt and payment must share a currency')
    c=receipt['currency'];due=date_in(result,payment['value_date']);date_in(result,receipt['value_date'])
    timing=next((r for r in data.get('fx_receipt_timing',[]) if r['receipt_id']==receipt_id),None)
    if not timing:raise ValueError('Missing simulated receipt timing')
    earmarked=sum((D(r['amount']) for r in data.get('fx_allocations',[]) if r['receipt_id']==receipt_id),D(0))
    allocated=sum((D(r['amount']) for r in data.get('fx_allocations',[]) if r['obligation_id']==obligation_id),D(0))
    hedged=sum((D(r['target_amount']) for key,status in (('fx_hedges','Active'),('fx_orders','Booked')) for r in data.get(key,[]) if r['status']==status and r['obligation_id']==obligation_id),D(0))
    if amount>D(receipt['amount'])-earmarked or amount>D(payment['amount'])-allocated-hedged:raise ValueError('Amount exceeds unallocated receipt or remaining uncovered obligation')
    order=unique(data,'fx_orders',purchase_order_id)
    if order['status']!='Planned' or order['target']!=c or order['obligation_id']!=obligation_id:raise ValueError('Choose a planned purchase explicitly linked to this payment')
    if amount>D(order['target_amount']):raise ValueError('Reduction cannot exceed the planned purchase')
    full=amount==D(order['target_amount'])
    change_allowed=order['cancellable'] if full else order['amendable']
    charge=D(order['cancellation_fee_jmd'] if full else order['amendment_fee_jmd'])
    fraction=amount/D(order['target_amount']);freed=(D(order['source_amount'])*fraction).quantize(CENT)
    avoided_fee=(D(order['fee_jmd'])*fraction).quantize(CENT)
    reduced=trade_events(order)
    day=order['settlement_date'];date_in(result,day)
    reduced.extend([(day,order['source'],freed),(day,c,-amount),(snapshot_dates(result)[0],'JMD',-charge)])
    # Fee schedule may be fixed: retaining the full original fee is conservative.
    if full:reduced.append((day,'JMD',D(order['fee_jmd'])))
    else:avoided_fee=D(0)
    remaining_fee=D(0) if full else D(order['fee_jmd'])
    debits=[(day,order['source'],-(D(order['source_amount'])-freed)),(day,'JMD',-remaining_fee),(snapshot_dates(result)[0],'JMD',-charge)]
    after=project(data,result,rules,reduced,{c,order['source'],'JMD'},debits=debits)
    before=project(data,result,rules,trade_events(order),{c,order['source'],'JMD'})
    stressed=(date.fromisoformat(receipt['value_date'])+timedelta(days=timing['delay_days'])).isoformat()
    expected=receipt['status']=='Expected'
    confirmed=not expected and bool(timing['confirmation_reference'])
    timely=receipt['value_date']<due # Same-day ordering is deliberately not treated as verified.
    stress_timely=stressed<due
    receipt_delta=[(receipt['value_date'],c,-D(receipt['amount']))]
    delayed_delta=receipt_delta+([(stressed,c,D(receipt['amount']))] if stressed in snapshot_dates(result) else [])
    after['matched_receipt_delayed']=project(data,result,rules,reduced+delayed_delta,{c,order['source'],'JMD'},debits=debits)['baseline']
    after['without_matched_receipt']=project(data,result,rules,reduced+receipt_delta,{c,order['source'],'JMD'},debits=debits)['baseline']
    risk=exposure_check(data,rules,{order['source']:-D(order['source_amount'])+freed,c:D(order['target_amount'])-amount})
    capacity=source_capacity(data,order['source'],day)
    checks=dict(order_change_permitted=change_allowed,receipt_arrives_before_payment=timely,delayed_receipt_arrives_before_payment=stress_timely,
        receipt_confirmed_in_simulation=confirmed,source_account_capacity=D(order['source_amount'])-freed<=capacity,
        fee_account_capacity=remaining_fee+charge+(D(order['source_amount'])-freed if order['source']=='JMD' else 0)<=source_capacity(data,'JMD'),
        remaining_order_settlement_ready=full or all(settlement_check(data,order['source'],c,day,snapshot_dates(result)[0])[k] for k in ('lead_time_satisfied','arrival_is_business_day')),
        all_debit_account_limits=debit_permissions(data,debits),
        exposure_preserved=risk['no_exposure_breach_worsened'])
    passes=all(v for k,v in checks.items() if k!='delayed_receipt_arrives_before_payment') and after['baseline']['passes_cash_checks']
    return publish(data,rules,catalog,'receipt_match',dict(receipt_id=receipt_id,obligation_id=obligation_id,amount=str(amount),purchase_order_id=purchase_order_id),
        dict(checks=checks,passes_modeled_checks=passes,scenarios=after,original_order_scenarios=before,risk=risk,
            receipt_id=receipt_id,obligation_id=obligation_id,purchase_order_id=purchase_order_id,settlement_date=due,
            delayed_receipt_date=stressed,receipt_amount_not_added_again=True,
            comparison='Recorded receipt is already in both forecasts; reduce one unapproved purchase. Fees retained on partial amendment; no profit claim.'),
        [('matched_amount','Recorded receipt earmark proposed, not an added inflow',amount,'money',c),('source_purchase_avoided','Source principal no longer exchanged; retained capital, NOT profit',freed,'money',order['source']),
         ('net_fee_reduction','Avoided order fee less amendment/cancellation charge',avoided_fee-charge,'money','JMD'),('due_date','Payment date',due,'date',c)],
        [dict(label='Use expected deposit',value=fmt(c,amount)+' from '+receipt_id),dict(label='Reduce planned purchase',value=fmt(c,amount)+' on '+purchase_order_id),dict(label='Payment due',value=due+' (proposed allocation)')])


def assess_round_trip(data,result,rules,catalog,first_order_id,second_order_id):
    a=unique(data,'fx_orders',first_order_id);b=unique(data,'fx_orders',second_order_id)
    if a['id']==b['id'] or (a['source'],a['target'])!=(b['target'],b['source']):raise ValueError('Choose two different opposite-direction orders')
    if a['status'] not in ('Planned','Booked') or b['status'] not in ('Planned','Booked'):raise ValueError('Only outstanding orders can be assessed')
    if a['obligation_id'] or b['obligation_id']:raise ValueError('Linked payment orders need a separate coverage assessment; no automatic cancellation')
    if a['settlement_date']>b['settlement_date'] or D(a['target_amount'])!=D(b['source_amount']):raise ValueError('Second leg must exactly return the first target amount on or after its date')
    for row in (a,b):date_in(result,row['settlement_date'])
    current=[];cancel=[]
    for row in (a,b):
        legs=trade_events(row)
        if row['status']=='Planned':current+=legs
        else:cancel+=trade_events(row,-1)
    charge=D(a['cancellation_fee_jmd'])+D(b['cancellation_fee_jmd'])
    cancel.append((snapshot_dates(result)[0],'JMD',-charge))
    affected={a['source'],a['target'],'JMD'}
    before=project(data,result,rules,current,affected);after=project(data,result,rules,cancel,affected,debits=[(snapshot_dates(result)[0],'JMD',-charge)])
    loss=D(a['source_amount'])-D(b['target_amount'])
    net=loss*price(data,a['source'],'buy')+D(a['fee_jmd'])+D(b['fee_jmd'])-charge
    # Independent exposure deltas remove only booked future flows; current risk
    # positions are not reconstructed from historical trades.
    changes={c:sum((v for _,cc,v in cancel if cc==c),D(0)) for c in affected}
    risk=exposure_check(data,rules,changes)
    checks=dict(cancellation_permitted=a['cancellable'] and b['cancellable'],no_linked_payment_needs=True,
        fee_account_permission=debit_permissions(data,[(snapshot_dates(result)[0],'JMD',-charge)]),
        exposure_preserved=risk['no_exposure_breach_worsened'],modeled_cost_reduction=net>0)
    return publish(data,rules,catalog,'cancel_orders',dict(first_order_id=first_order_id,second_order_id=second_order_id),
        dict(checks=checks,passes_modeled_checks=all(checks.values()) and after['baseline']['passes_cash_checks'],scenarios=after,original_order_scenarios=before,risk=risk,
            comparison='Complete both recorded orders versus cancel both; spread loss valued at current source buying price, not a realized return. Intermediate currency availability is rechecked.'),
        [('source_round_trip_loss','Source principal lost by completing both orders',loss,'money',a['source']),('net_cost_avoided_jmd','Indicative principal/spread loss plus order fees less cancellation charges',net,'money','JMD')],
        [dict(label='Cancel planned conversions',value=a['id']+' and '+b['id']),dict(label='Keep instead',value=fmt(a['source'],a['source_amount'])),dict(label='Review before',value=a['settlement_date']+' (proposed)')])


def assess_forward_hedge(data,result,rules,catalog,source,obligation_id,target_amount,settlement_date,counterparty_id):
    payment=active(data,'payments',obligation_id);target=payment['currency']
    if source not in CURRENCIES or source==target:raise ValueError('Choose a different supported source currency')
    amount=money(target_amount);day=date_in(result,settlement_date);due=date_in(result,payment['value_date'])
    first=snapshot_dates(result)[0];tenor=(date.fromisoformat(day)-date.fromisoformat(first)).days
    if tenor<=0:raise ValueError('A forward needs a future settlement date')
    policy=next((r for r in data.get('fx_hedge_policy',[]) if r['currency']==target),None)
    if not policy:raise ValueError('Missing hedge policy for target currency')
    cp=unique(data,'fx_counterparties',counterparty_id)
    # Covered-interest-parity illustration, with explicit dealer spread. Neither
    # future spot predictions nor real counterparty offers are used.
    s,t=terms(data,source),terms(data,target)
    theoretical=mid(data,source)/mid(data,target)*(1+D(t['annual_interest_rate'])*D(tenor)/365)/(1+D(s['annual_interest_rate'])*D(tenor)/365)
    rate=theoretical/(1+D(policy['forward_spread_bps'])/10000)
    principal=(amount/rate).quantize(CENT,rounding=ROUND_CEILING)
    notional=amount*price(data,target)
    collateral=(notional*D(cp['collateral_pct'])/100).quantize(CENT,rounding=ROUND_CEILING)
    cost=D(cp['fee_jmd'])
    existing=[r for r in data.get('fx_hedges',[]) if r['status']=='Active']
    covered=sum((D(r['target_amount']) for r in existing if r['obligation_id']==obligation_id),D(0))
    covered+=sum((D(r['target_amount']) for r in data.get('fx_orders',[]) if r['status']=='Booked' and r['obligation_id']==obligation_id),D(0))
    allocated=sum((D(r['amount']) for r in data.get('fx_allocations',[]) if r['obligation_id']==obligation_id),D(0))
    used=sum((D(r['target_amount'])*price(data,r['target']) for r in existing if r['counterparty_id']==counterparty_id),D(0))
    # Existing collateral is assumed already held in Accounts.holds. New margin
    # is reserved at booking and released on settlement, not free liquidity.
    events=[(first,'JMD',-collateral-cost),(day,'JMD',collateral),(day,source,-principal),(day,target,amount)]
    scenarios=project(data,result,rules,events,{source,target,'JMD'})
    timing=settlement_check(data,source,target,day,first,forward=True)
    capacity=source_capacity(data,source,day)
    risk=exposure_check(data,rules,{source:-principal,target:amount})
    checks=dict(instrument_permitted=policy['allow_deliverable_forward'],counterparty_approved=cp['approved'],tenor_permitted=tenor<=policy['max_tenor_days'],
        amount_within_hedge_policy=covered+amount<=D(payment['amount'])*D(policy['max_hedge_pct'])/100,
        no_overhedging=covered+allocated+amount<=D(payment['amount']),counterparty_capacity=used+notional<=D(cp['forward_limit_jmd']),
        arrives_before_payment=day<due,source_account_capacity=principal<=capacity,collateral_and_fee_account_capacity=collateral+cost<=source_capacity(data,'JMD'),
        all_debit_account_limits=debit_permissions(data,events),
        simulated_settlement_ready=timing['arrival_is_business_day'] and timing['lead_time_satisfied'],
        exposure_preserved=risk['no_exposure_breach_worsened'])
    for scenario in scenarios.values():scenario['passes_modeled_checks']=all(checks.values()) and scenario['passes_cash_checks']
    # Transparent future-spot sensitivities are not forecasts or guaranteed P&L.
    sensitivity=[]
    for factor in (D('.95'),D(1),D('1.05')):
        assumed_spot=sheet_rate(data,source,target)*factor
        unhedged=(amount/assumed_spot).quantize(CENT,rounding=ROUND_CEILING)
        sensitivity.append(dict(assumed_target_per_source=str(assumed_spot),unhedged_source_cost=str(unhedged),forward_source_cost=str(principal),
            forward_source_cost_difference=str(principal-unhedged),includes_fees_carry_or_collateral_cost=False))
    return publish(data,rules,catalog,'forward',dict(source=source,obligation_id=obligation_id,target_amount=str(amount),settlement_date=day,counterparty_id=counterparty_id),
        dict(source=source,target=target,source_principal=float(principal),target_received=float(amount),settlement_date=day,passes_modeled_checks=scenarios['baseline']['passes_modeled_checks'],
            checks=checks,scenarios=scenarios,risk=risk,timing=timing,simulated_forward_rate=str(rate),spot_sensitivity=sensitivity,
            comparison='Illustrative price certainty versus remaining unhedged, not expected savings. Fee included; carry, margin interest/opportunity cost and market availability are not established.'),
        [('source_principal','Forward source principal at maturity',principal,'money',source),('target_received','Forward target proceeds',amount,'money',target),
         ('forward_rate','Illustrative forward target per source, not Treasury spot/executable price',str(rate),'rate',target),('collateral_jmd','Additional JMD reserved until settlement',collateral,'money','JMD'),
         ('fee_jmd','Assumed counterparty fee at booking',cost,'money','JMD'),('settlement_date','Proposed forward maturity',day,'date',target)],
        [dict(label='Propose forward purchase',value=fmt(target,amount)),dict(label='Sell at maturity',value=fmt(source,principal)),dict(label='Illustrative forward rate',value=f'{source} 1 buys {target} {rate:.4f}'),dict(label=f'{target} available by',value=day+' (proposed)')])


def scenario_tools(data,result,rules,catalog):
    if not data.get('fx_terms'):return {}
    def text(description=''):return dict(type='string',description=description)
    currency=dict(type='string',enum=list(CURRENCIES))
    amount=dict(type='number',exclusiveMinimum=0,description='Destination/receipt amount, at most two decimals.')
    definitions={
        'assess_fx_trade':(assess_fx_trade,'Test your dated currency sale/purchase for concentration reduction or funding-source comparison; reports before/after currency and aggregate exposure breaches, fees, account permissions, cash cushions and simulated settlement. A smaller breach remains a breach. Compare alternatives yourself; no ranking/execution.',
             dict(source=currency,target=currency,target_amount=amount,settlement_date=text('ISO arrival date inside forecast; you choose it.'),booking_hour=dict(type='integer',minimum=0,maximum=23),expected_receipt_delay_days=dict(type='integer',minimum=1,maximum=28))),
        'assess_allocation_plan':(assess_allocation_plan,'Optionally test YOUR proposed sale of one currency into two or three distinct currencies needed for recorded payments. No destination, amount or trade is selected by code. All legs, fees, dates, cash cushions and exposure are checked together; failed or receipt-dependent plans cannot become action rows.',
            dict(source=currency,legs=dict(type='array',minItems=2,maxItems=3,items=dict(type='object',properties=dict(target=currency,target_amount=amount,settlement_date=text('ISO date within forecast'),obligation_id=text('Existing payment ID')),required=['target','target_amount','settlement_date','obligation_id'],additionalProperties=False)),booking_hour=dict(type='integer',minimum=0,maximum=23),expected_receipt_delay_days=dict(type='integer',minimum=1,maximum=28))),
        'assess_receipt_match':(assess_receipt_match,'Test using an existing receipt instead of part/all of a linked planned FX purchase; no receipt is counted twice. Checks earmarks, coverage, timing, confirmations and cash. Requires a real planned purchase ID linked to payment, not an invented avoided trade.',
            dict(receipt_id=text(),obligation_id=text(),amount=amount,purchase_order_id=text())),
        'assess_round_trip':(assess_round_trip,'Compare completing two opposite outstanding FX orders with cancelling both; test intermediate liquidity, fees, cancellation terms and exposure. Only exact target/source matches without linked obligations; no invented trade savings.',
            dict(first_order_id=text(),second_order_id=text())),
        'assess_forward_hedge':(assess_forward_hedge,'Test a deliverable forward for a recorded future payment using assumed interest, dealer spread, hedge policy, existing cover, counterparty limit and reserved collateral. Shows spot sensitivities, NOT exchange-rate predictions or guaranteed savings. Within forecast only.',
            dict(source=currency,obligation_id=text(),target_amount=amount,settlement_date=text(),counterparty_id=text())),
    }
    return {name:dict(description=description,parameters=dict(type='object',properties=props,required=list(props),additionalProperties=False),
        handler=lambda _fn=fn,**args:_fn(data,result,rules,catalog,**args)) for name,(fn,description,props) in definitions.items()}
