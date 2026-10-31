"""Deterministic candidate preflight; facts for the FX Analyst, not authored advice.

The analyst can test new hypotheses with the ordinary what-if tools. Preflight
ensures obvious recorded relationships are actually evaluated before writing.
"""
from .fx_scenarios import assess_receipt_match,assess_round_trip,assess_fx_trade
from .portfolio_scenario import evaluate_portfolio_scenario,publish_scenario
from .portfolio_decisions import conflict_groups
from .fx_market import D,price


def preflight(data,result,rules,catalog,candidates):
    groups=conflict_groups(candidates)
    reports=[];trace=[]
    for item in candidates:
        detail=item['details'];kind=item['kind'];options=[]
        try:
            if kind=='receipt_purchase':
                order=next(o for o in data['fx_orders'] if o['id']==detail['order_id'])
                args={k:detail[k] for k in ('receipt_id','obligation_id','amount')}
                args['purchase_order_id']=detail['order_id']
                value=assess_receipt_match(data,result,rules,catalog,**args)
                ops=[dict(kind='allocate',obligation_id=detail['obligation_id'],receipt_id=detail['receipt_id'],amount=detail['amount']),
                    dict(kind='cancel_order',order_id=order['id']) if D(detail['amount'])==D(order['target_amount']) else
                    dict(kind='amend_order',order_id=order['id'],target_reduction=detail['amount'])]
                combined=publish_scenario(data,catalog,evaluate_portfolio_scenario(data,result,rules,ops,baseline_orders=[order['id']]))
                options=[('assess_receipt_match',args,value),('evaluate_portfolio_scenario',dict(operations=ops,baseline_orders=[order['id']]),combined)]
            elif kind=='circular_orders':
                args=detail
                value=assess_round_trip(data,result,rules,catalog,**args)
                ids=[detail['first_order_id'],detail['second_order_id']]
                ops=[dict(kind='cancel_order',order_id=i) for i in ids]
                combined=publish_scenario(data,catalog,evaluate_portfolio_scenario(data,result,rules,ops,baseline_orders=ids))
                options=[('assess_round_trip',args,value),('evaluate_portfolio_scenario',dict(operations=ops,baseline_orders=ids),combined)]
            elif kind=='existing_cash':
                order=next(o for o in data['fx_orders'] if o['id']==detail['order_id'])
                payment=next(p for p in data['payments'] if p['id']==detail['obligation_id'])
                ops=[dict(kind='cancel_order',order_id=order['id']),
                    dict(kind='allocate',obligation_id=payment['id'],amount=payment['amount'])]
                value=publish_scenario(data,catalog,evaluate_portfolio_scenario(data,result,rules,ops,baseline_orders=[order['id']]))
                options=[('evaluate_portfolio_scenario',dict(operations=ops,baseline_orders=[order['id']]),value)]
            elif kind=='cash_gap':
                target=detail['currency'];position=next(p for p in result['positions'] if p['currency']==target)
                amount=max(D(0),D(position['minimum'])-D(position['projected_low']))
                # The writer remains responsible for amounts, timing and strategy.
                # Preliminary comparisons never select an executable source.
                for source in (p['currency'] for p in result['positions'] if p['currency']!=target):
                    days=[r['date'] for r in result['forecast'] if r['currency']==target]
                    day=days[min(7,len(days)-1)]
                    args=dict(source=source,target=target,target_amount=float(amount),settlement_date=day)
                    value=assess_fx_trade(data,result,rules,catalog,**args)
                    options.append(('assess_fx_trade',args,value))
            elif kind=='exposure':
                source=detail['currency'];position=next(p for p in result['positions'] if p['currency']==source)
                headroom=max(D(0),D(position['confirmed_receipts_low'])-D(position['minimum']))
                excess=D(item['materiality'])
                # Test a substantial but buffer-limited sale. This does NOT
                # reconcile Risk_Inputs to cash or select a real trade.
                principal=min(headroom/D(2),excess/D(4)).quantize(D('.01'))
                if principal>0:
                    days=[r['date'] for r in result['forecast'] if r['currency']==source]
                    args=dict(source=source,target='JMD',target_amount=float((principal*price(data,source,'buy')).quantize(D('.01'))),
                        settlement_date=days[min(7,len(days)-1)])
                    value=assess_fx_trade(data,result,rules,catalog,**args)
                    options=[('assess_fx_trade',args,value)]
        except (ValueError,KeyError,TypeError) as exc:
            reports.append(dict(id=item['id'],kind=kind,decision_group=groups.get(item['id']),status='blocked',
                reason=str(exc)[:240],alternatives=[]))
            continue
        for name,args,value in options:
            trace.append(dict(stage='candidate_preflight',candidate_id=item['id'],name=name,arguments=args,result=value))
        reports.append(dict(id=item['id'],kind=kind,decision_group=groups.get(item['id']),
            status='tested' if options else 'awaiting_analyst',
            reason='Preliminary calculation, not approval; compare alternatives and verify missing facts.' if options else 'Analyst must investigate or document deferral.',
            alternatives=[dict(tool=name,arguments=args,scenario_id=value.get('scenario_id'),
                passes_modeled_checks=value.get('passes_modeled_checks'),checks=value.get('checks'),
                indicative_fee_avoidance_jmd=value.get('incremental_fee_avoidance_jmd'),
                limitations=value.get('limitations')) for name,args,value in options]))
    return reports,trace
