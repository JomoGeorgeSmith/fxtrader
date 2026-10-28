"""Compact model views; full precision, provenance and traces stay local."""
import copy
import json


LOCAL_FIELDS={'_source','sources','display','authored_fact_ids','resolved_fact_ids','review_warnings'}


def compact(value):
    if isinstance(value,dict):
        return {k:compact(v) for k,v in value.items() if k not in LOCAL_FIELDS}
    if isinstance(value,(list,tuple)):
        return [compact(v) for v in value]
    return value


def compact_catalog(catalog):
    return {id:{k:compact(v) for k,v in fact.items() if k not in LOCAL_FIELDS|{'id'}} for id,fact in catalog.items()}


def scenario_summary(result):
    value=compact(result)
    if isinstance(value,dict) and 'scenarios' in value:
        for scenario in value['scenarios'].values():
            scenario.pop('balances',None)
    return value


def assessment_summary(report):
    # The writer explains actual calculations; no need to resend another AI's
    # memo plus a duplicate full trace and the same facts again in the catalog.
    return dict(task=report['task'],status=report['status'],error=report.get('error'),
        calculations=[dict(name=t['name'],arguments=t['arguments'],result={k:v for k,v in scenario_summary(t['result']).items() if k!='facts'})
            for t in report['tool_calls'] if t['name'] in ('assess_funding_plan','simulate_fx_conversion')])


def model_json(value):
    return json.dumps(compact(value),separators=(',',':'),ensure_ascii=False)


def cached_handler(handler):
    """Reuse identical deterministic tool calls within the pinned snapshot."""
    cache={}
    def execute(**arguments):
        key=json.dumps(arguments,sort_keys=True)
        if key not in cache:
            cache[key]=copy.deepcopy(handler(**arguments))
        return copy.deepcopy(cache[key])
    return execute
