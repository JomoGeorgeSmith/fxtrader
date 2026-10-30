"""Shared-state, read-only FX what-if evaluator. No source workbook is modified.

Baseline cash excludes Planned orders. The explicit comparison includes only
orders mentioned by the proposal; other planned orders are not silently spent.
Allocations earmark existing cash/receipts and never add a second cash inflow.
"""
import hashlib
import json
from datetime import date,timedelta
from decimal import ROUND_CEILING

from .fx_market import D,CENT,fee,price,project,exposure_check,source_capacity,settlement_check
from .recommended_action import sheet_rate


def evaluate_portfolio_scenario(data,result,rules,operations,*,baseline_orders=(),expected_delay_days=3):
    if not isinstance(operations,list) or not operations:
        raise ValueError('Provide at least one proposed operation')
    dates={r['date'] for r in result['forecast']}
    orders={r['id']:r for r in data.get('fx_orders',[])}
    payments={r['id']:r for r in data['payments']}
    receipts={r['id']:r for r in data['receipts']}
    currencies={p['currency'] for p in result['positions']}
    baseline_orders=tuple(baseline_orders)
    if len(set(baseline_orders))!=len(baseline_orders):raise ValueError('Duplicate baseline order')
    if any(i not in orders or orders[i]['status']!='Planned' for i in baseline_orders):
        raise ValueError('Comparison orders must exist and be Planned')
    flows=[];baseline=[];charges=D(0);avoided=D(0);risk_changes={};used_orders=set();allocations={};receipt_uses={};
    evidence=set();limitations=['Indicative Treasury sheet prices, not executable quotes or confirmed bank delivery.',
        'Risk_Inputs is not reconciled to operating cash. Exposure change is provisional, not verified policy remediation.',
        'Available-to-trade cash is limited by recorded holds and simulated permissions; unknown encumbrances need Treasury confirmation.']
    checks={};settlements=[]
    def add(day,c,delta):
        if day not in dates:raise ValueError('Operation date outside forecast')
        flows.append((day,c,D(delta)))
    def leg(order,target):
        day=order['settlement_date']
        if day not in dates:raise ValueError('Order settles outside forecast')
        target.extend([(day,order['source'],-D(order['source_amount'])),
            (day,order['target'],D(order['target_amount'])),(day,'JMD',-D(order['fee_jmd']))])
    for id in baseline_orders:leg(orders[id],baseline)
    for index,op in enumerate(operations):
        if not isinstance(op,dict) or 'kind' not in op:raise ValueError('Invalid portfolio operation')
        kind=op['kind']
        if kind in ('cancel_order','retain_order','amend_order'):
            id=op.get('order_id');order=orders.get(id)
            if not order or order['status']!='Planned' or id in used_orders or id not in baseline_orders:
                raise ValueError('Order must be a unique Planned comparison order')
            used_orders.add(id);evidence.add('simulation.'+id)
            if kind=='retain_order':leg(order,flows)
            elif kind=='amend_order':
                reduction=D(op['target_reduction']);original=D(order['target_amount'])
                if not reduction.is_finite() or reduction<=0 or reduction>=original or reduction!=reduction.quantize(CENT):
                    raise ValueError('Amendment must leave a positive order amount')
                checks[f'order_{index}_amendable']=bool(order['amendable'])
                fraction=(original-reduction)/original
                day=order['settlement_date']
                add(day,order['source'],-(D(order['source_amount'])*fraction).quantize(CENT))
                add(day,order['target'],original-reduction)
                # Preserve fixed/unknown fee rather than claim partial savings.
                add(day,'JMD',-D(order['fee_jmd']))
                cost=D(order['amendment_fee_jmd']);charges+=cost
                if cost:add(min(dates),'JMD',-cost)
            else:
                checks[f'order_{index}_cancellable']=bool(order['cancellable'])
                cost=D(order['cancellation_fee_jmd']);charges+=cost
                if cost:add(min(dates),'JMD',-cost)
                avoided+=D(order['fee_jmd'])
        elif kind=='trade':
            source,target=op.get('source'),op.get('target')
            if source not in currencies or target not in currencies or source==target:raise ValueError('Invalid currency pair')
            amount=D(op['target_amount']);day=op['settlement_date']
            if not amount.is_finite() or amount<=0 or amount!=amount.quantize(CENT):raise ValueError('Invalid trade amount')
            principal=(amount/sheet_rate(data,source,target)).quantize(CENT,rounding=ROUND_CEILING)
            cost=fee(data,source,principal)
            add(day,source,-principal);add(day,target,amount);add(day,'JMD',-cost)
            charges+=cost
            timing=settlement_check(data,source,target,day,min(dates),op.get('booking_hour',12))
            settlements.append(timing)
            checks[f'trade_{index}_settlement']=timing['lead_time_satisfied'] and timing['arrival_is_business_day']
            risk_changes[source]=risk_changes.get(source,D(0))-principal
            risk_changes[target]=risk_changes.get(target,D(0))+amount
            evidence.update((f'{source}.rate.buy',f'{target}.rate.sell'))
        elif kind=='allocate':
            payment=payments.get(op.get('obligation_id'))
            if not payment or payment['status'] in ('Cancelled','Settled'):raise ValueError('Unknown active payment')
            amount=D(op['amount']);id=payment['id']
            if not amount.is_finite() or amount<=0 or amount!=amount.quantize(CENT):raise ValueError('Invalid allocation amount')
            allocations[id]=allocations.get(id,D(0))+amount
            if op.get('receipt_id'):
                receipt=receipts.get(op['receipt_id'])
                if not receipt or receipt['status'] in ('Cancelled','Settled') or receipt['currency']!=payment['currency']:
                    raise ValueError('Receipt unavailable for payment')
                receipt_uses[receipt['id']]=receipt_uses.get(receipt['id'],D(0))+amount
                checks[f'allocation_{index}_timely']=receipt['value_date']<payment['value_date']
                checks[f'allocation_{index}_confirmed']=receipt['status'] in ('Confirmed','Committed')
                evidence.update((f'receipts.{receipt["id"]}.amount',f'receipts.{receipt["id"]}.date'))
            evidence.add(f'payments.{id}.amount')
        else:raise ValueError('Unknown portfolio operation: '+str(kind))
    if set(baseline_orders)!=used_orders:raise ValueError('Every comparison order must be retained or cancelled exactly once')
    for id,amount in allocations.items():
        existing=sum((D(a['amount']) for a in data.get('fx_allocations',[]) if a['obligation_id']==id),D(0))
        if amount+existing>D(payments[id]['amount']):raise ValueError('Payment allocation used twice')
    for id,amount in receipt_uses.items():
        existing=sum((D(a['amount']) for a in data.get('fx_allocations',[]) if a['receipt_id']==id),D(0))
        if amount+existing>D(receipts[id]['amount']):raise ValueError('Receipt used twice')
    # Cash projections are against the same immutable recorded ledger; planned
    # trades are modeled only when explicitly retained in the scenario.
    before=project(data,result,rules,baseline,currencies,expected_delay_days)
    after=project(data,result,rules,flows,currencies,expected_delay_days)
    delayed_flows=list(flows)
    for id in receipt_uses:
        receipt=receipts[id]
        timing=next((row for row in data.get('fx_receipt_timing',[]) if row['receipt_id']==id),None)
        checks['receipt_'+id+'_delay_known']=timing is not None
        if timing and receipt['value_date'] in dates:
            shifted=(date.fromisoformat(receipt['value_date'])+timedelta(days=timing['delay_days'])).isoformat()
            delayed_flows.append((receipt['value_date'],receipt['currency'],-D(receipt['amount'])))
            if shifted in dates:delayed_flows.append((shifted,receipt['currency'],D(receipt['amount'])))
            checks['receipt_'+id+'_delayed_before_payment']=all(shifted<payments[pid]['value_date'] for pid in allocations
                if any(op.get('receipt_id')==id and op.get('obligation_id')==pid for op in operations))
    delayed=project(data,result,rules,delayed_flows,currencies,expected_delay_days)
    for mode in after:
        checks[mode+'_no_new_shortfall']=all(
            D(after[mode]['lows'][c])>=min(D(before[mode]['lows'][c]),D(next(p['minimum'] for p in result['positions'] if p['currency']==c)))
            for c in currencies)
        checks[mode+'_funded_before_debit']=after[mode]['source_funds_before_debit']
    checks['allocated_receipt_delay_no_new_shortfall']=all(D(delayed['baseline']['lows'][c])>=min(
        D(before['baseline']['lows'][c]),D(next(p['minimum'] for p in result['positions'] if p['currency']==c))) for c in currencies)
    debits={}
    for day,c,delta in flows:
        if delta<0:debits[day,c]=debits.get((day,c),D(0))-delta
    checks['account_debit_limits']=all(amount<=source_capacity(data,c,day) for (day,c),amount in debits.items())
    checks['no_double_allocation']=True
    risk=exposure_check(data,rules,risk_changes)
    checks['exposure_not_worsened']=risk['no_exposure_breach_worsened']
    # A purchase is an expense, not a profit. Only avoided recorded order fees
    # net of amendment/cancellation charges have a directly priced comparison.
    net_avoided=avoided-charges
    if len(baseline_orders)==2 and all(op['kind']=='cancel_order' for op in operations):
        a,b=(orders[i] for i in baseline_orders)
        if (a['source'],a['target'])==(b['target'],b['source']) and D(a['target_amount'])==D(b['source_amount']):
            net_avoided+=(D(a['source_amount'])-D(b['target_amount']))*price(data,a['source'],'buy')
    identity=json.dumps(dict(operations=operations,baseline_orders=baseline_orders),sort_keys=True,default=str)
    return dict(scenario_id='portfolio.'+hashlib.sha256(identity.encode()).hexdigest()[:16],operations=operations,
        baseline_orders=list(baseline_orders),baseline_comparison='Complete referenced planned orders versus proposed operations; all other planned orders remain excluded from both forecasts.',
        impact_by_currency_date=after['baseline']['balances'],cash_before={m:v['lows'] for m,v in before.items()},
        cash_after={m:v['lows'] for m,v in after.items()},checks=checks,passes_modeled_checks=all(checks.values()),
        risk=risk,exposure_reconciliation='unverified',exposure_change_provisional=True,
        incremental_fee_avoidance_jmd=float(net_avoided),valuation_assumptions=['Recorded synthetic fees; principal retained is not profit. No live executable quote or future P&L.'],
        settlement=settlements,limitations=limitations,evidence_ids=sorted(evidence),
        resources=sorted(set(baseline_orders)|set(allocations)|set(receipt_uses)),
        scenarios={**{m:{k:v for k,v in row.items() if k!='balances'} for m,row in after.items()},
            'delayed_allocated_receipts':{k:v for k,v in delayed['baseline'].items() if k!='balances'}})


def publish_scenario(data,catalog,report):
    """Expose a passing *conditional* portfolio action to the existing card UI."""
    id=report['scenario_id']
    sources=sorted({source for evidence_id in report['evidence_ids'] if evidence_id in catalog
        for source in catalog[evidence_id].get('sources',[])})
    if not sources:
        ids=set(report['resources'])
        sources=sorted({row['_source'] for rows in data.values() for row in rows
            if row.get('id') in ids and '_source' in row})
    value=report['incremental_fee_avoidance_jmd']
    fact=dict(id=id+'.fee_avoidance',label='Indicative net fee / round-trip cost avoided versus explicitly compared Planned orders; not realized profit',
        value=value,unit='money',currency='JMD',display=f'JMD {value:,.2f}',sources=sources,rules=[])
    catalog[fact['id']]=fact
    report['facts']=[fact];report['kind']='portfolio_change'
    report['action_fields']=[dict(label='Proposed change (Treasury approval required)',value=', '.join(
        op['kind'].replace('_',' ')+' '+op.get('order_id',op.get('obligation_id',op.get('source','')))
        for op in report['operations'])),dict(label='Modeled avoided cost, not profit',value=f'JMD {value:,.2f}'),
        dict(label='Pricing / risk',value='Indicative synthetic fees; obtain executable quote and reconcile exposure')]
    return report


def portfolio_scenario_tool(data,result,rules,catalog):
    nullable_string=dict(type=['string','null'])
    nullable_number=dict(type=['number','null'])
    properties=dict(kind=dict(type='string',enum=['trade','cancel_order','amend_order','retain_order','allocate']),
        order_id=nullable_string,target_reduction=nullable_number,source=nullable_string,target=nullable_string,
        target_amount=nullable_number,settlement_date=nullable_string,booking_hour=dict(type=['integer','null']),
        obligation_id=nullable_string,receipt_id=nullable_string,amount=nullable_number)
    def run(operations,baseline_orders):
        return publish_scenario(data,catalog,evaluate_portfolio_scenario(data,result,rules,
            [{k:v for k,v in op.items() if v is not None} for op in operations],baseline_orders=baseline_orders))
    return dict(description='Evaluate a set of proposed FX trades, planned order cancellations/amendments, and payment/receipt allocations on ONE shared dated ledger. Include each compared Planned order ID in baseline_orders. No execution or verified exposure reconciliation.',
        parameters=dict(type='object',properties=dict(operations=dict(type='array',items=dict(type='object',
            properties=properties,required=list(properties),additionalProperties=False)),
            baseline_orders=dict(type='array',items=dict(type='string'))),required=['operations','baseline_orders'],additionalProperties=False),
        handler=run)
