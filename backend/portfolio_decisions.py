"""Conflict grouping and joint feasibility, independent of the FX writer/UI."""
import hashlib

from .portfolio_scenario import evaluate_portfolio_scenario


def conflict_groups(candidates):
    """Connected resources define alternatives, not an instruction to execute all."""
    parent={c['id']:c['id'] for c in candidates}
    def root(id):
        while parent[id]!=id:
            parent[id]=parent[parent[id]];id=parent[id]
        return id
    resources={}
    for c in candidates:
        for resource in c['resources']:
            if resource in resources:parent[root(c['id'])]=root(resources[resource])
            else:resources[resource]=c['id']
    members={}
    for c in candidates:members.setdefault(root(c['id']),[]).append(c['id'])
    return {id:'decision.'+hashlib.sha256('|'.join(sorted(members[root(id)])).encode()).hexdigest()[:12]
        for id in parent if len(members[root(id)])>1}


def evaluate_combination(data,result,rules,strategies):
    """Recompute on ONE cash ledger; never sum independent modeled benefits."""
    operations=[];orders=[];claims=set();conflicts=[]
    for strategy in strategies:
        for resource in strategy.get('resources',[]):
            if resource in claims:conflicts.append(resource)
            claims.add(resource)
        operations.extend(strategy['operations'])
        orders.extend(strategy.get('baseline_orders',[]))
    if conflicts:return dict(passes_modeled_checks=False,conflicts=sorted(set(conflicts)),reason='Alternatives reuse a payment, receipt or order')
    try:
        report=evaluate_portfolio_scenario(data,result,rules,operations,baseline_orders=orders)
    except ValueError as exc:
        return dict(passes_modeled_checks=False,conflicts=[],reason=str(exc))
    return dict(report,conflicts=[])


def resolve_competing_cards(cards,trace):
    """At most one executable-looking action per decision group.

    Retain unrelated risk observations; record suppressed alternatives for audit.
    This is a safety arbitration, not evidence that a trade is executable.
    """
    grouped={};rejected=[]
    results={v.get('scenario_id',v.get('plan_id')):v for t in trace for v in [t.get('result') or {}] if isinstance(v,dict)}
    for card in cards:
        if card.get('decision_group') and card.get('recommended_action'):
            grouped.setdefault(card['decision_group'],[]).append(card)
    for group,options in grouped.items():
        if len(options)<2:continue
        def score(card):
            result=results.get(card['action_id'],{})
            return (float(result.get('incremental_fee_avoidance_jmd') or 0),card['id'])
        winner=max(options,key=score)
        for card in options:
            if card is winner:continue
            card['recommended_action']=None;card['expected_result']=None
            card['next_move']='Alternative to '+winner['title']+'. Do not execute both; ask Treasury to compare these options together before choosing.'
            rejected.append(dict(id=card['id'],decision_group=group,reason='Conflicting action: '+winner['id']+' retained as the provisional option'))
        winner['detail']+=' Alternatives considered: '+', '.join(card['title'] for card in options if card is not winner)+'. Do not execute competing actions together.'
    return rejected


def resolve_joint_portfolio(cards,trace,data,result,rules,audit=None):
    """Gate simultaneously displayed actions on a single shared-state check."""
    by_id={v.get('scenario_id',v.get('plan_id')):(t,v) for t in trace for v in [t.get('result') or {}] if isinstance(v,dict)}
    proposals=[];rejected=[]
    for card in cards:
        if not card.get('recommended_action'):continue
        pair=by_id.get(card.get('action_id'))
        if not pair:continue
        tool,value=pair
        if tool['name']=='evaluate_portfolio_scenario':
            proposal=dict(operations=value['operations'],baseline_orders=value['baseline_orders'],resources=value['resources'])
        elif tool['name']=='assess_fx_trade':
            args=tool.get('arguments') or {}
            if not {'source','target','target_amount','settlement_date'}<=args.keys():
                continue  # Historical/fake traces lack sufficient joint inputs.
            proposal=dict(operations=[dict(kind='trade',source=args['source'],target=args['target'],
                target_amount=args['target_amount'],settlement_date=args['settlement_date'])],resources=[])
        else:
            # Other legacy tool kinds cannot be translated safely to this
            # joint engine. They remain conditional, not jointly verified.
            continue
        priority=(card['currency']==next((p['currency'] for p in result['positions'] if p['projected_low']<p['minimum']),None),
            float(value.get('incremental_fee_avoidance_jmd') or 0))
        proposals.append((priority,card,proposal))
    selected=[];selected_cards=[]
    for _,card,proposal in sorted(proposals,key=lambda row:row[0],reverse=True):
        report=evaluate_combination(data,result,rules,selected+[proposal])
        if audit is not None:
            audit.append(dict(card_id=card['id'],combined_with=[c['id'] for c in selected_cards],
                scenario_id=report.get('scenario_id'),passes_modeled_checks=report['passes_modeled_checks'],
                checks=report.get('checks'),conflicts=report.get('conflicts'),reason=report.get('reason'),
                baseline_orders=report.get('baseline_orders'),operations=report.get('operations'),
                cash_after=report.get('cash_after'),incremental_fee_avoidance_jmd=report.get('incremental_fee_avoidance_jmd'),
                exposure_change_provisional=report.get('exposure_change_provisional')))
        if report['passes_modeled_checks']:
            selected.append(proposal);selected_cards.append(card)
            continue
        card['recommended_action']=None;card['expected_result']=None
        card['next_move']='Do not execute this option alongside the other displayed actions. Ask Treasury to compare funding, cash and order commitments on one approved portfolio plan.'
        rejected.append(dict(id=card['id'],reason='Combined portfolio checks failed: '+', '.join(
            k for k,v in report.get('checks',{}).items() if v is False)[:200] if report.get('checks') else report.get('reason','Shared resources conflict')))
    return rejected


def evaluation_metrics(candidates,reports,trace,cards,expected_resources=()):
    """Repeatable coverage metrics; expected_resources is a labeled test set."""
    discovered={resource for c in candidates for resource in c['resources']}
    expected=set(expected_resources)
    calculated={r['id'] for r in reports if r['status']=='tested'}
    actions=[c for c in cards if c.get('action_id')]
    groups=[c['decision_group'] for c in cards if c.get('recommended_action') and c.get('decision_group')]
    valid=sum(c.get('recommended_action') is not None for c in actions)
    return dict(material_candidate_recall=None if not expected else len(expected&discovered)/len(expected),
        investigation_coverage=len(calculated)/len(candidates) if candidates else 1,
        tool_invocations=len(trace),valid_action_rate=valid/len(actions) if actions else 1,
        duplicate_conflict_rate=(len(groups)-len(set(groups)))/len(actions) if actions else 0,
        net_benefits_require_common_baseline=True)
