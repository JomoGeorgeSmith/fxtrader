"""Compact portfolio-wide facts for an open-ended analyst, not a trade selector."""
from decimal import Decimal
from datetime import date


def portfolio_signals(data,result,rules):
    risk={row['currency']:row for row in data['risk']}
    limits={row['currency']:row for row in data.get('fx_limits',[])}
    positions={row['currency']:row for row in result['positions']}
    dates=sorted({row['date'] for row in result['forecast']})
    currencies=[]
    for currency,position in positions.items():
        minimum=Decimal(str(position['minimum']))
        baseline=Decimal(str(position['projected_low']))
        without_expected=Decimal(str(position.get('confirmed_receipts_low',position['projected_low'])))
        row=risk.get(currency)
        limit=limits.get(currency)
        exposure=None
        if row:
            value=Decimal(str(row['net_fx_exposure']))
            ceiling=Decimal(str(limit['max_absolute_exposure'])) if limit else None
            exposure=dict(recorded_net_position=float(value),synthetic_absolute_limit=float(ceiling) if ceiling is not None else None,
                excess_over_synthetic_limit=float(max(Decimal(0),abs(value)-ceiling)) if ceiling is not None else None,
                note='Risk_Inputs is separate from operating cash; offsets are not reconciled in this snapshot.')
        payments=[p for p in data['payments'] if p['currency']==currency and p['status'] not in ('Cancelled','Settled') and dates[0]<=p['value_date']<=dates[-1]]
        receipts=[r for r in data['receipts'] if r['currency']==currency and r['status'] not in ('Cancelled','Settled') and dates[0]<=r['value_date']<=dates[-1]]
        expected=[r for r in receipts if r['status']=='Expected']
        currencies.append(dict(currency=currency,exposure=exposure,
            operating_available=position['available'],demo_operating_cushion=position['minimum'],
            baseline_low=position['projected_low'],low_without_expected_receipts=float(without_expected),
            baseline_cushion_gap=float(max(Decimal(0),minimum-baseline)),
            cushion_gap_without_expected_receipts=float(max(Decimal(0),minimum-without_expected)),
            arithmetic_sale_headroom_baseline=float(max(Decimal(0),baseline-minimum)),
            arithmetic_sale_headroom_without_expected_receipts=float(max(Decimal(0),without_expected-minimum)),
            upcoming_payment_count=len(payments),
            upcoming_payments=[dict(id=p['id'],amount=p['amount'],date=p['value_date'],status=p['status'])
                for p in sorted(payments,key=lambda row:(row['value_date'],row['id']))],
            incoming_receipts=[dict(id=r['id'],amount=r['amount'],date=r['value_date'],status=r['status'])
                for r in sorted(receipts,key=lambda row:(row['value_date'],row['id']))],
            expected_receipts_total=float(sum((Decimal(str(r['amount'])) for r in expected),Decimal(0)))))
    # Join recorded flows and existing plans by date rather than handing the
    # writer four unrelated sheets. No scenario is selected by this map.
    timeline={}
    for key,kind in (('payments','payment'),('receipts','receipt')):
        for flow in data[key]:
            if flow['status'] in ('Cancelled','Settled') or not dates[0]<=flow['value_date']<=dates[-1]:continue
            timeline.setdefault(flow['value_date'],[]).append(dict(kind=kind,id=flow['id'],currency=flow['currency'],
                amount=flow['amount'],status=flow['status'],description=flow.get('description','')))
    for order in data.get('fx_orders',[]):
        if order['status'] not in ('Planned','Booked') or not dates[0]<=order['settlement_date']<=dates[-1]:continue
        timeline.setdefault(order['settlement_date'],[]).append(dict(kind='fx_order',id=order['id'],source=order['source'],
            source_amount=order['source_amount'],target=order['target'],target_amount=order['target_amount'],
            status=order['status'],obligation_id=order['obligation_id'],fee_jmd=order['fee_jmd']))
    daily={(r['date'],r['currency']):r for r in result['forecast']}
    cash_timeline=[]
    for day,events in sorted(timeline.items()):
        affected={c for event in events for c in (event.get('currency'),event.get('source'),event.get('target')) if c}
        cash_timeline.append(dict(date=day,events=events,
            operating_close={c:daily[day,c]['balance'] for c in sorted(affected)},
            close_without_expected={c:daily[day,c]['confirmed_receipts_balance'] for c in sorted(affected)}))
    # Nearby flow pairs are dated facts, not automatic allocations or trades.
    # A surplus receipt, payment or order may participate in multiple choices.
    receipt_payment_windows=[]
    for r in data['receipts']:
        if r['status'] in ('Cancelled','Settled') or not dates[0]<=r['value_date']<=dates[-1]:continue
        candidates=sorted((p for p in data['payments'] if p['currency']==r['currency'] and p['status'] not in ('Cancelled','Settled')
            and r['value_date']<=p['value_date']<=dates[-1]),key=lambda p:(p['value_date'],p['id']))
        for p in candidates[:2]:
            receipt_payment_windows.append(dict(currency=r['currency'],receipt_id=r['id'],payment_id=p['id'],
                days_before=(date.fromisoformat(p['value_date'])-date.fromisoformat(r['value_date'])).days,
                linked_planned_purchases=[o['id'] for o in data.get('fx_orders',[])
                    if o['status']=='Planned' and o['target']==p['currency'] and o['obligation_id']==p['id']]))
    orders=[o for o in data.get('fx_orders',[]) if o['status'] in ('Planned','Booked')]
    opposing_order_pairs=[dict(first=a['id'],second=b['id'],intermediate_amounts_match=a['target_amount']==b['source_amount'],
        either_order_linked_to_payment=bool(a['obligation_id'] or b['obligation_id']))
        for a in orders for b in orders if a['id']!=b['id'] and a['settlement_date']<=b['settlement_date']
        and (a['source'],a['target'])==(b['target'],b['source'])]
    return dict(forecast_start=dates[0],forecast_end=dates[-1],aggregate_exposure_pct=result['exposure_pct'],synthetic_aggregate_limit_pct=rules['max_absolute_fx_exposure_pct'],
        currencies=currencies,
        dated_relationships=cash_timeline,
        receipt_payment_windows=receipt_payment_windows,opposing_order_pairs=opposing_order_pairs,
        outstanding_fx_orders=[dict(id=o['id'],source=o['source'],source_amount=o['source_amount'],target=o['target'],
            target_amount=o['target_amount'],settlement_date=o['settlement_date'],status=o['status'],obligation_id=o['obligation_id'],
            cancellable=o['cancellable'],amendable=o['amendable'],cancellation_fee_jmd=o['cancellation_fee_jmd'])
            for o in data.get('fx_orders',[]) if o['status'] in ('Planned','Booked')],
        forward_inputs_present=bool(data.get('fx_hedge_policy') and data.get('fx_counterparties')),
        caveat='Arithmetic sale headroom is not a verified trading capacity or combined-plan test. Independent trades can compete for the same cash and payment. Choose hypotheses yourself; test timing, costs, buffers and exposure before proposing any trade.')
