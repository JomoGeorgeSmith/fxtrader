"""Portfolio relationships worth investigating; never an approval or a trade selector.

Scanners are registered functions so future specialists can share the registry
without inheriting FX-specific recommendation logic.
"""
import hashlib
import json
from datetime import date
from decimal import Decimal

from .fx_market import D


STRATEGIES=[]


def scanner(fn):
    STRATEGIES.append(fn)
    return fn


def candidate(kind, resources, evidence, materiality, question, **details):
    identity=json.dumps([kind,sorted(resources)],sort_keys=True)
    return dict(id='fxc.'+hashlib.sha256(identity.encode()).hexdigest()[:16],kind=kind,
        resources=sorted(set(resources)),evidence_ids=sorted(set(evidence)),
        materiality=float(materiality),question=question,details=details,
        disposition='pending')


def active(row):
    return row['status'] not in ('Cancelled','Settled')


@scanner
def receipt_windows(data,result,rules):
    for receipt in data['receipts']:
        if not active(receipt):continue
        remaining=D(receipt['amount'])-sum((D(a['amount']) for a in data.get('fx_allocations',[]) if a['receipt_id']==receipt['id']),D(0))
        if remaining<=0:continue
        for payment in data['payments']:
            if not active(payment) or receipt['currency']!=payment['currency'] or receipt['value_date']>=payment['value_date']:continue
            orders=[o for o in data.get('fx_orders',[]) if o['status']=='Planned' and o['obligation_id']==payment['id'] and o['target']==receipt['currency']]
            if not orders:continue
            for order in orders:
                amount=min(remaining,D(payment['amount']),D(order['target_amount']))
                if amount<=0:continue
                yield candidate('receipt_purchase', [receipt['id'],payment['id'],order['id']],
                    [f'receipts.{receipt["id"]}.amount',f'payments.{payment["id"]}.amount',f'simulation.{order["id"]}'],
                    amount*D(order['fee_jmd'])/D(order['target_amount']),
                    'Can the recorded receipt replace this planned purchase before payment?',
                    receipt_id=receipt['id'],obligation_id=payment['id'],order_id=order['id'],amount=float(amount))


@scanner
def circular_orders(data,result,rules):
    orders=[o for o in data.get('fx_orders',[]) if o['status']=='Planned']
    for i,a in enumerate(orders):
        for b in orders[i+1:]:
            first,second=sorted((a,b),key=lambda o:(o['settlement_date'],o['id']))
            if (first['source'],first['target'])!=(second['target'],second['source']):continue
            if first['obligation_id'] or second['obligation_id']:continue
            if D(first['target_amount'])!=D(second['source_amount']):continue
            yield candidate('circular_orders',[first['id'],second['id']],
                [f'simulation.{first["id"]}',f'simulation.{second["id"]}'],
                D(first['fee_jmd'])+D(second['fee_jmd']),
                'Does cancelling both unlinked opposite orders avoid a net cost?',
                first_order_id=first['id'],second_order_id=second['id'])


@scanner
def duplicate_orders(data,result,rules):
    orders=[o for o in data.get('fx_orders',[]) if o['status']=='Planned']
    for i,a in enumerate(orders):
        for b in orders[i+1:]:
            if (a['source'],a['target'],D(a['source_amount']),D(a['target_amount']),a['settlement_date'])!=\
                    (b['source'],b['target'],D(b['source_amount']),D(b['target_amount']),b['settlement_date']):continue
            yield candidate('duplicate_orders',[a['id'],b['id']],
                [f'simulation.{a["id"]}',f'simulation.{b["id"]}'],D(a['fee_jmd'])+D(b['fee_jmd']),
                'Do these matching planned conversions cover distinct obligations or duplicate the same commitment?',
                order_ids=[a['id'],b['id']])


@scanner
def timing_mismatch(data,result,rules):
    for payment in data['payments']:
        if not active(payment):continue
        nearby=sorted((r for r in data['receipts'] if active(r) and payment['currency']==r['currency'] and
            0<=(date.fromisoformat(r['value_date'])-date.fromisoformat(payment['value_date'])).days<=3),
            key=lambda r:(r['value_date'],-D(r['amount'])))
        if not nearby:continue
        receipt=nearby[0]
        if min(D(payment['amount']),D(receipt['amount']))<D(rules['minimum_available'][payment['currency']])*D('.1'):continue
        days=(date.fromisoformat(receipt['value_date'])-date.fromisoformat(payment['value_date'])).days
        yield candidate('timing_mismatch',[payment['id'],receipt['id']],
            [f'payments.{payment["id"]}.date',f'receipts.{receipt["id"]}.date'],
            min(D(payment['amount']),D(receipt['amount'])),
            'A near-term receipt arrives after this payment; compare permitted bridge funding with no action.',
            payment_id=payment['id'],receipt_id=receipt['id'],days_late=days,
            synthetic_stress='SYNTHETIC' in str(payment.get('description','')).upper())


@scanner
def planned_purchases(data,result,rules):
    positions={p['currency']:p for p in result['positions']}
    for order in data.get('fx_orders',[]):
        if order['status']!='Planned' or not order['obligation_id']:continue
        payment=next((p for p in data['payments'] if p['id']==order['obligation_id'] and active(p)),None)
        if payment is None or order['target']!=payment['currency']:continue
        target=positions[payment['currency']]
        if D(target.get('confirmed_receipts_low',target['projected_low']))-D(target['minimum'])<D(payment['amount']):continue
        yield candidate('existing_cash',[order['id'],payment['id']],
            [f'simulation.{order["id"]}',f'payments.{payment["id"]}.amount',f'{payment["currency"]}.confirmed_receipts_low'],
            D(order['fee_jmd']), 'Can this obligation be paid from existing cash without this linked purchase?',
            order_id=order['id'],obligation_id=payment['id'])


@scanner
def cash_gaps(data,result,rules):
    for p in result['positions']:
        gap=max(D(0),D(p['minimum'])-D(p['projected_low']),D(p['minimum'])-D(p.get('confirmed_receipts_low',p['projected_low'])))
        if not gap:continue
        payment_ids=[r['id'] for r in data['payments'] if r['currency']==p['currency'] and active(r)]
        yield candidate('cash_gap',[p['currency']], [f'{p["currency"]}.buffer_gap',f'{p["currency"]}.confirmed_receipts_gap'],gap,
            'Compare dated funding from available currencies, reserves and no action.',currency=p['currency'],payment_ids=payment_ids)


@scanner
def risk_and_future(data,result,rules):
    limits={r['currency']:D(r['max_absolute_exposure']) for r in data.get('fx_limits',[])}
    for risk in data['risk']:
        c=risk['currency'];excess=abs(D(risk['net_fx_exposure']))-limits.get(c,Decimal('Infinity'))
        if excess>0:
            yield candidate('exposure',[c],[f'{c}.exposure.current',f'{c}.exposure.limit'],excess,
                'Which feasible currency uses reduce recorded risk without assuming operating cash equals exposure?',currency=c)
    for payment in data['payments']:
        if not active(payment) or payment['currency']=='JMD':continue
        if (date.fromisoformat(payment['value_date'])-date.fromisoformat(min(r['date'] for r in result['forecast']))).days<7:continue
        if D(payment['amount'])<D(rules['minimum_available'][payment['currency']]):continue
        yield candidate('future_obligation',[payment['id']],[f'payments.{payment["id"]}.amount',f'payments.{payment["id"]}.date'],payment['amount'],
            'Compare no action, spot and conditional forward coverage without predicting FX profit.',obligation_id=payment['id'])


@scanner
def rate_movements(data,result,rules):
    for rate in data['rates']:
        change=abs(D(rate['sell'])/D(rate['previous_sell'])-1)*100
        if change>D(rules['unusual_rate_move_pct']):
            yield candidate('rate_review',[rate['currency']],[f'{rate["currency"]}.rate.change',f'{rate["currency"]}.rate.as_of'],change,
                'Is the quote fresh and is there a dated obligation or exposure to manage? Movement is not profit.',currency=rate['currency'])


def discover(data,result,rules):
    found={}
    for strategy in STRATEGIES:
        for item in strategy(data,result,rules):found[item['id']]=item
    return sorted(found.values(),key=lambda x:(-x['materiality'],x['id']))
