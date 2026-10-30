"""Compact model views; full precision, provenance and traces stay local."""
import copy
import json


LOCAL_FIELDS={'_source','sources','display','authored_fact_ids','resolved_fact_ids','review_warnings'}
TABLE_GUIDE='Input encoding: $table lists column names; rows hold their values in that order. $keyed_table works the same way but each row starts with its object key (e.g. fact ID), followed by column values. $literal escapes objects containing reserved encoding keys. These are lossless data tables, not instructions.'


def compact(value):
    if isinstance(value,dict):
        return {k:compact(v) for k,v in value.items() if k not in LOCAL_FIELDS}
    if isinstance(value,(list,tuple)):
        return [compact(v) for v in value]
    return value


def compact_catalog(catalog):
    return {id:{k:compact(v) for k,v in fact.items() if k not in LOCAL_FIELDS|{'id'}} for id,fact in catalog.items()}


def scenario_summary(result):
    def trim(value):
        if isinstance(value,dict):return {k:trim(v) for k,v in value.items() if k!='balances'}
        if isinstance(value,list):return [trim(v) for v in value]
        return value
    return trim(compact(result))


def assessment_summary(report):
    # Earlier CAD tests are useful, but duplicating every cash day and every
    # assumption anchors the writer on this one task. Full trace stays in audit.
    calculations=[]
    for t in report['tool_calls']:
        if t['name'] not in ('assess_funding_plan','simulate_fx_conversion'):continue
        result=t['result']
        if 'error' in result:
            summary={'error':result['error']}
        elif t['name']=='assess_funding_plan':
            risk=result.get('risk') or {}
            summary={k:result.get(k) for k in ('plan_id','source','target','obligation_id','requested_target_amount',
                'source_principal','settlement_date','obligation_date','exposure_pct','exposure_within_demo_limit')}
            summary['risk']={k:risk.get(k) for k in ('previous_exposure_pct','exposure_pct','no_exposure_breach_worsened')}
            summary['simulation_checks']=result.get('simulation_checks')
            summary['scenarios']={mode:{k:value.get(k) for k in ('passes_modeled_checks','source_low','target_low',
                'target_remaining_buffer_gap')} for mode,value in result.get('scenarios',{}).items()}
        else:
            summary={k:v for k,v in scenario_summary(result).items() if k!='facts'}
        calculations.append(dict(name=t['name'],arguments=t['arguments'],result=summary))
    return dict(task=report['task'],status=report['status'],error=report.get('error'),calculations=calculations)


def model_json(value):
    return json.dumps(compact(value),separators=(',',':'),ensure_ascii=False)


def pack_tables(value):
    """Lossless wire encoding; never pack schemas, audit records or model output."""
    if isinstance(value,dict):
        if set(value) in ({'$table','rows'},{'$keyed_table','rows'},{'$literal'}):
            return {'$literal':{k:pack_tables(v) for k,v in value.items()}}
        plain={k:pack_tables(v) for k,v in value.items()}
        rows=list(value.values())
        candidate=None
        if rows and all(isinstance(row,dict) and set(row)==set(rows[0]) for row in rows):
            columns=list(rows[0])
            candidate={'$keyed_table':columns,'rows':[[key]+[pack_tables(row[c]) for c in columns] for key,row in value.items()]}
    elif isinstance(value,list):
        plain=[pack_tables(v) for v in value]
        candidate=None
        if value and all(isinstance(row,dict) and set(row)==set(value[0]) for row in value):
            columns=list(value[0])
            candidate={'$table':columns,'rows':[[pack_tables(row[c]) for c in columns] for row in value]}
    else:
        return value
    # Small/irregular objects stay readable rather than growing an envelope.
    if candidate is not None and len(json.dumps(candidate,ensure_ascii=False))<len(json.dumps(plain,ensure_ascii=False)):
        return candidate
    return plain


def unpack_tables(value):
    """Offline verification/readback helper; the model reads tables directly."""
    if isinstance(value,dict):
        if set(value)=={'$literal'}:
            return {k:unpack_tables(v) for k,v in value['$literal'].items()}
        if set(value)=={'$table','rows'}:
            return [{k:unpack_tables(v) for k,v in zip(value['$table'],row)} for row in value['rows']]
        if set(value)=={'$keyed_table','rows'}:
            return {row[0]:{k:unpack_tables(v) for k,v in zip(value['$keyed_table'],row[1:])} for row in value['rows']}
        return {k:unpack_tables(v) for k,v in value.items()}
    if isinstance(value,list):
        return [unpack_tables(v) for v in value]
    return value


def wire_json(value):
    return json.dumps(pack_tables(value),separators=(',',':'),ensure_ascii=False)


def wire_content(content):
    try:
        value=json.loads(content)
    except (ValueError,TypeError):
        return content
    return wire_json(value) if isinstance(value,(dict,list)) else content


def cached_handler(handler):
    """Reuse identical deterministic tool calls within the pinned snapshot."""
    cache={}
    def execute(**arguments):
        key=json.dumps(arguments,sort_keys=True)
        if key not in cache:
            cache[key]=copy.deepcopy(handler(**arguments))
        return copy.deepcopy(cache[key])
    return execute
