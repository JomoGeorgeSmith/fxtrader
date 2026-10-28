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


def run_orchestrator(*,ask,prompt,data,result,rules,handoff,previous,tools,handlers,trace,model,write,progress=None):
    tasks=funding_tasks(data,result)
    # All validated values remain available, without repeated row provenance.
    context=dict(handoff=handoff,workbooks=data,calculations=result,policy=rules,previous_snapshot=previous)
    analyst_prompt=prompt.split('## Output contract')[0]

    def assess(state):
        reports=[]
        warnings=list(state['warnings'])
        for task in state['funding_tasks']:
            currency=task['currency']
            if progress:progress(f'Assessing funding scenarios for {currency}')
            local_trace=[]
            scoped={}
            for name,spec in handlers.items():
                def execute(_name=name,_spec=spec,**args):
                    if _name=='assess_funding_plan' and args.get('obligation_id') not in task['eligible_obligation_ids']:
                        raise ValueError('Choose an eligible payment ID for the assigned funding currency')
                    if _name=='simulate_fx_conversion' and args.get('target')!=currency:
                        raise ValueError('The conversion target must be the assigned funding currency')
                    return _spec['handler'](**args)
                scoped[name]=dict(spec,handler=execute)
            memo=None
            error=None
            try:
                memo=ask(analyst_prompt+'\nYou are the same treasury analyst performing the assigned funding assessment, not writing dashboard cards. Choose a source currency, amount and settlement date yourself. The required calculation tool will execute before you can finish. Size destination funding against the forecast gap, not blindly against the full payment amount; consider funding by the first buffer breach rather than only the selected payment due date. Test receipt uncertainty and both currencies\' buffers. A zero-day delay is identical to baseline, not a receipt-delay stress test. If a tested plan fails a buffer or exposure check, use remaining tool turns to investigate a credible adjustment or materially different source before concluding, when the data supports one. This is bounded investigation, not an exhaustive search or publishing gate. Correct failed tool inputs rather than treating an error as an assessment. Return a short summary of actual results, rejected options and limitations. Never call a plan optimal or say an untested source is inferior; explicitly distinguish tested options from untested ones. A plan is not suitable merely because it pays one loan; distinguish baseline feasibility from robustness, risk and unestablished economics. General planning text is not a tool result.',
                    model_json(dict(context,funding_task=task)),tools,
                    tool_handlers=scoped,required_tools={task['required_tool']},trace=local_trace,
                    trace_stage='funding_assessment',max_tokens=650,model=model)
            except Exception:
                error='Funding assessment interrupted; retained tool results remain available.'
            executed=[t for t in local_trace if t['name']==task['required_tool']]
            successful=[t for t in executed if isinstance(t.get('result'),dict) and 'error' not in t['result']]
            # A tool-returned scenario may be infeasible and is still a valid
            # assessment. It must never be recorded as a successful trade.
            status='assessed' if successful else 'failed'
            if error or not successful:
                warnings.append(f'{currency}: '+(error or 'No funding calculation completed. No tested funding solution was established.'))
            reports.append(dict(task=task,status=status,memo=memo,error=error,tool_calls=local_trace))
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
