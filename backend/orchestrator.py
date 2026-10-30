"""Code-routed assessments and AI-authored advice; no separate AI planner."""
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from .model_context import model_json


class WorkflowState(TypedDict, total=False):
    funding_tasks: list[dict]
    assessments: list[dict]
    warnings: list[str]
    output: dict


def funding_tasks(data, result):
    """Select problems, never funding currencies, trades, or winning plans."""
    dates=sorted({r['date'] for r in result['forecast']})
    tasks=[]
    for p in result['positions']:
        baseline_gap=max(0,p['minimum']-p['projected_low'])
        cautious_gap=max(0,p['minimum']-p.get('confirmed_receipts_low',p['projected_low']))
        if not max(baseline_gap,cautious_gap):
            continue
        daily=[r for r in result['forecast'] if r['currency']==p['currency']]
        baseline_deadline=next((r['date'] for r in daily if r['balance']<p['minimum']),None)
        cautious_deadline=next((r['date'] for r in daily if r.get('confirmed_receipts_balance',r['balance'])<p['minimum']),None)
        if p['available']<p['minimum']:
            baseline_deadline=cautious_deadline=dates[0]
        obligations=[r['id'] for r in data['payments'] if r['currency']==p['currency'] and r['status'] not in ('Cancelled','Settled') and dates[0]<=r['value_date']<=dates[-1]]
        tasks.append(dict(currency=p['currency'],baseline_buffer_gap=baseline_gap,
            buffer_gap_without_expected_receipts=cautious_gap,baseline_deadline=baseline_deadline,
            deadline_without_expected_receipts=cautious_deadline,eligible_obligation_ids=obligations,
            required_tool='assess_funding_plan' if obligations else 'simulate_fx_conversion'))
    return tasks


def run_orchestrator(*,ask,prompt,data,result,rules,handoff,previous,tools,handlers,trace,model,write,progress=None,candidate_driven=False):
    if candidate_driven:
        if progress:progress('Investigating portfolio candidates and alternatives')
        return write(dict(funding_tasks=[],assessments=[],warnings=[]))
    tasks=funding_tasks(data,result)
    # All validated values remain available, without repeated row provenance.
    dates=sorted({r['date'] for r in result['forecast']})
    # The writer receives the complete prompt, forecast and previous snapshot.
    # Funding selection only needs its assigned currency's daily path plus all
    # positions/workbook flows to choose a source and test it with the tool.
    funding_prompt=('You are choosing a synthetic FX funding scenario, not recommending a trade. '
        'Use the assigned calculation tool for the assigned currency and an eligible obligation. '
        'Choose the source, destination amount sized to the cushion gap (not automatically the full payment), '
        'a proposed arrival with margin before the earliest baseline or receipt-sensitive breach where possible, '
        'and a nonzero Expected-receipt delay. Choose a settlement_date only from the tool schema: '
        'all dates are within the saved forecast, from '+dates[0]+' through '+dates[-1]+'. '
        'If the earliest shortfall is today or settlement is infeasible, still test a scenario and report its failed checks; '
        'do not pretend same-day settlement, a weekday, or an unconfirmed bank cutoff is safe. '
        'No execution, invented prices, approval or guaranteed delivery. Tool failures can be corrected. '
        'After the calculation, the full-context writer will judge the result and can test alternatives.')

    def assess(state):
        reports=[]
        warnings=list(state['warnings'])
        for task in state['funding_tasks']:
            currency=task['currency']
            if progress:progress(f'Assessing funding scenarios for {currency}')
            local_trace=[]
            name=task['required_tool']
            spec=handlers[name]
            def execute(_name=name,_spec=spec,**args):
                if _name=='assess_funding_plan' and args.get('obligation_id') not in task['eligible_obligation_ids']:
                    raise ValueError('Choose an eligible payment ID for the assigned funding currency')
                if _name=='assess_funding_plan' and args.get('settlement_date') not in dates:
                    raise ValueError('Settlement must be within the saved forecast')
                if _name=='simulate_fx_conversion' and args.get('target')!=currency:
                    raise ValueError('The conversion target must be the assigned funding currency')
                return _spec['handler'](**args)
            scoped={name:dict(spec,handler=execute)}
            if name=='assess_funding_plan':
                parameters=dict(spec['parameters'],properties=dict(spec['parameters']['properties']))
                parameters['properties']['settlement_date']=dict(parameters['properties']['settlement_date'],enum=dates)
                scoped[name]['parameters']=parameters
            scoped_context=dict(workbooks=data,policy=rules,funding_task=task,
                calculations=dict(positions=result['positions'],exposure_pct=result['exposure_pct'],
                    forecast=[r for r in result['forecast'] if r['currency']==currency],
                    findings=[f for f in result['findings'] if f['currency']==currency],
                    forecast_start=dates[0],forecast_end=dates[-1]))
            error=None
            try:
                ask(funding_prompt,
                    model_json(scoped_context),
                    tool_handlers=scoped,required_tools={task['required_tool']},trace=local_trace,
                    trace_stage='funding_assessment',max_tokens=650,model=model,stop_after_required_tools=True)
            except Exception:
                error='Funding assessment interrupted; retained tool results remain available.'
            executed=[t for t in local_trace if t['name']==task['required_tool']]
            successful=[t for t in executed if isinstance(t.get('result'),dict) and 'error' not in t['result']]
            # A tool-returned scenario may be infeasible and is still a valid
            # assessment. It must never be recorded as a successful trade.
            status='assessed' if successful else 'failed'
            if error or not successful:
                warnings.append(f'{currency}: '+(error or 'No funding calculation completed. No tested funding solution was established.'))
            reports.append(dict(task=task,status=status,error=error,tool_calls=local_trace))
            trace.extend(local_trace)
        return dict(assessments=reports,warnings=warnings)

    def publish(state):
        if progress:progress('Writing suggestions from portfolio evidence and assessment results')
        return dict(output=write(state))

    graph=StateGraph(WorkflowState)
    graph.add_node('funding_assessment',assess)
    graph.add_node('publish',publish)
    graph.add_conditional_edges(START,lambda s:'funding_assessment' if s['funding_tasks'] else 'publish')
    graph.add_edge('funding_assessment','publish')
    graph.add_edge('publish',END)
    return graph.compile().invoke(dict(funding_tasks=tasks,assessments=[],warnings=[]))['output']
