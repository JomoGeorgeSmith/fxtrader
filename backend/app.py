import asyncio
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import urllib.request
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from kafka import KafkaConsumer, KafkaProducer
from openpyxl import load_workbook
from pydantic import BaseModel, Field

from .seed import ROOT, SHARED, seed
from .calculations import evaluate, CURRENCIES
from .liquidity_metrics import COMPONENTS
from .analyst import generate_suggestions, build_evidence, simulate_conversion
from .funding_plan import funding_plan_tool
from .model_context import compact, compact_catalog, scenario_summary, model_json, cached_handler, wire_content, wire_json, TABLE_GUIDE
from .calculation_cache import cached_evaluate
from .simulation_inputs import SIMULATION_FILE, read_simulation
from .fx_scenarios import scenario_tools

RUNTIME = ROOT/'runtime'
RUNTIME.mkdir(exist_ok=True)
RULES_PATH = ROOT/'policies'/'demo-rules.json'
PROMPT_PATH = ROOT/'backend'/'prompts'/'fx_analyst.md'
TOPIC = 'jn.workbook.changed'
STATE = {'stage':'starting','message':'Connecting to Kafka','kafka':False,'revision':0,'analysis':None,'runs':[], 'ai_mode':'Checking','active_run_id':None}
LOCK = threading.RLock()
RUN_LOCK = threading.Lock()
STOP = threading.Event()
BOOT_ID = uuid.uuid4().hex
LAST_INPUT_HASH = None
WATCH_SIGNATURE = None
FILES = ['Bank_Balances.xlsx','Daily_Rate_Sheet.xlsx','Weekly_Disbursements.xlsx',SIMULATION_FILE]
SHEETS = {'accounts':(FILES[0],'Accounts'),'reserves':(FILES[0],'Reserves'),'risk':(FILES[0],'Risk_Inputs'),'rates':(FILES[1],'Rates'),'payments':(FILES[2],'Payments'),'receipts':(FILES[2],'Expected_Receipts'),'liquidity_inputs':(FILES[0],'Liquidity_Inputs')}
REQUIRED = {'accounts':['account_id','bank','currency','balance','holds','as_of'],'reserves':['reserve_id','currency','balance','as_of'],'risk':['currency','net_fx_exposure','capital_base_jmd','as_of'],'rates':['currency','buy','sell','previous_sell','as_of','quote'],'payments':['id','currency','amount','value_date','status'],'receipts':['id','currency','amount','value_date','status'],'liquidity_inputs':['component','currency','amount','as_of','description']}

def update(**values):
    with LOCK:
        STATE.update(values)
        STATE['revision'] += 1

def database():
    db = sqlite3.connect(RUNTIME/'audit.sqlite')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created TEXT, payload TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY, run_id TEXT, suggestion_id TEXT, role TEXT, content TEXT)')
    return db

def read_context(folder,include_simulation=True):
    data = {}
    books = {}
    try:
        for key,(filename,sheet) in SHEETS.items():
            if filename not in books:
                books[filename] = load_workbook(folder/filename,data_only=True)
            ws = books[filename][sheet]
            rows = list(ws.values)
            headers = list(rows[0])
            if not set(REQUIRED[key]).issubset(headers):
                raise ValueError(f'{filename} / {sheet}: required columns are missing')
            data[key] = []
            for index,row in enumerate(rows[1:],2):
                if all(v is None for v in row):
                    continue
                item = dict(zip(headers,row))
                item['_source'] = f'{filename} / {sheet} / row {index}'
                if any(item.get(field) is None for field in REQUIRED[key]):
                    raise ValueError(f'{item["_source"]}: a required value is blank')
                if item['currency'] not in CURRENCIES:
                    raise ValueError(f'{item["_source"]}: unsupported currency')
                for field in ('balance','holds','amount','capital_base_jmd','buy','sell','previous_sell','net_fx_exposure'):
                    if field in item:
                        import math
                        number = float(item[field])
                        if not math.isfinite(number) or (field!='net_fx_exposure' and number<0):
                            raise ValueError(f'{item["_source"]}: invalid {field}')
                        if field in ('capital_base_jmd','buy','sell','previous_sell') and number==0:
                            raise ValueError(f'{item["_source"]}: {field} must be positive')
                for field in ('as_of','value_date'):
                    if field in item:
                        value = item[field]
                        item[field] = value.isoformat() if hasattr(value,'isoformat') else str(value)
                        datetime.fromisoformat(item[field])
                        if field=='value_date':
                            item[field] = item[field][:10]
                if 'status' in item and item['status'] not in ('Committed','Confirmed','Expected','Cancelled','Settled'):
                    raise ValueError(f'{item["_source"]}: unsupported status')
                data[key].append(item)
        for key in ('accounts','reserves'):
            if {r['currency'] for r in data[key]} != set(CURRENCIES):
                raise ValueError(f'{key}: all four currencies are required')
        for key in ('rates','risk'):
            if sorted(r['currency'] for r in data[key]) != ['CAD','GBP','USD']:
                raise ValueError(f'{key}: exactly one USD, GBP and CAD row is required')
        if len({str(r['capital_base_jmd']) for r in data['risk']}) != 1:
            raise ValueError('Capital base must agree across risk rows')
        for rate in data['rates']:
            if rate['quote'] != f'JMD per {rate["currency"]}':
                raise ValueError('Rate quote must explicitly be JMD per foreign currency')
        inputs = data['liquidity_inputs']
        if sorted(r['component'] for r in inputs)!=sorted(COMPONENTS):
            raise ValueError('Liquidity_Inputs: exactly one row per supported component is required')
        if any(r['currency']!='JMD' for r in inputs):
            raise ValueError('Liquidity_Inputs: all amounts must be total JMD equivalent')
        if any(float(r['amount'])<=0 for r in inputs if r['component'] in ('stressed_outflows_30d','required_stable_funding')):
            raise ValueError('Liquidity_Inputs: stressed outflows and required stable funding must be positive')
        if include_simulation:data.update(read_simulation(folder,data))
        return data
    finally:
        for book in books.values():
            book.close()

def digest(data):
    normalized = {key:sorted([{k:v for k,v in r.items() if k!='_source'} for r in rows],key=lambda r:json.dumps(r,sort_keys=True,default=str)) for key,rows in data.items()}
    return hashlib.sha256(json.dumps(normalized,sort_keys=True,default=str).encode()).hexdigest()

def key():
    value = os.getenv('OPENAI_API_KEY','').strip()
    if not value and (ROOT/'apikey.txt').exists():
        value = (ROOT/'apikey.txt').read_text().strip()
    return value

def ask_model(system, content, tool_values=None, *, tool_handlers=None, response_schema=None, required_tools=None, trace=None, max_tokens=1400, model=None, trace_stage=None, stop_after_required_tools=False):
    if not key():
        raise RuntimeError('No model key')
    body = {'model':model or os.getenv('OPENAI_MODEL','gpt-4.1-mini'),'messages':[{'role':'system','content':system+'\n'+TABLE_GUIDE},{'role':'user','content':wire_content(content)}], 'max_tokens':max_tokens}
    if response_schema:
        body['response_format']={'type':'json_schema','json_schema':{'name':'treasury_analyst_output','strict':True,'schema':response_schema}}
    needed=set(required_tools or [])
    called=set()
    if tool_values:
        body['tools'] = [{'type':'function','function':{'name':name,'description':description,'parameters':{'type':'object','properties':{},'additionalProperties':False},'strict':True}} for name,(description,_) in tool_values.items()]
        body['tool_choice'] = 'required' if required_tools is None else 'auto'
    if tool_handlers:
        body.setdefault('tools',[]).extend({'type':'function','function':{'name':name,'description':spec['description'],'parameters':spec['parameters'],'strict':True}} for name,spec in tool_handlers.items())
    for turn in range(8):
        missing=needed-called
        if missing:
            body['tool_choice']={'type':'function','function':{'name':sorted(missing)[0]}}
        req = urllib.request.Request('https://api.openai.com/v1/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':f'Bearer {key()}','Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=40) as response:
            message = json.load(response)['choices'][0]['message']
        if not message.get('tool_calls'):
            if missing:
                raise ValueError('Required evidence tools were not called')
            if message.get('refusal'):
                raise ValueError('The model declined the analysis')
            return message.get('content') or 'No additional explanation was returned.'
        body['messages'].append(message)
        for call in message['tool_calls']:
            name = call['function']['name']
            arguments={}
            try:
                arguments=json.loads(call['function'].get('arguments') or '{}')
                if not isinstance(arguments,dict):
                    raise ValueError('Tool arguments must be an object')
                if tool_handlers and name in tool_handlers:
                    value=tool_handlers[name]['handler'](**arguments)
                elif tool_values and name in tool_values and not arguments:
                    value=tool_values[name][1]
                else:
                    raise ValueError('Unknown tool or unexpected arguments')
                if not isinstance(value,dict) or 'error' not in value:
                    called.add(name)
            except (ValueError,TypeError,KeyError) as exc:
                value={'error':str(exc)[:200]}
            if trace is not None:
                trace.append(dict(name=name,arguments=arguments,result=json.loads(json.dumps(value)),call_id=call['id'],stage=trace_stage))
            visible=compact_catalog(value) if name=='get_evidence_catalog' and isinstance(value,dict) else scenario_summary(value) if name.startswith('assess_') or name=='simulate_fx_conversion' else compact(value)
            body['messages'].append({'role':'tool','tool_call_id':call['id'],'content':wire_json(visible)})
        if stop_after_required_tools and needed and needed<=called:
            # The writer receives the full calculations and performs judgment.
            # Do not buy another response just to narrate an unused tool memo.
            return ''
        body['tool_choice'] = 'none' if turn>=6 else 'auto'
    raise RuntimeError('Agent tool-call budget exhausted')

def standardize(data):
    """Deterministic handoff; workbook parsing already validates the values."""
    return f'Python-validated synthetic treasury data for JMD, USD, GBP and CAD: {len(data["accounts"])} operating accounts, {len(data["payments"])} payments and {len(data["receipts"])} receipts. Separate protected reserves; bank-perspective JMD-per-foreign-currency quotes. Numeric context is unchanged.'

def process(event):
    folder = RUNTIME/'snapshots'/event['id']
    update(stage='gathering',message='Reading the saved workbook snapshot')
    data = read_context(folder)
    rules = json.loads((folder/'demo-rules.json').read_text(encoding='utf-8'))
    update(stage='standardizing',message='Validating data and preparing the agent handoff')
    handoff = standardize(data)
    update(stage='checking',message='Calculating cash buffers, reserves and FX exposure')
    result,cache_hit = cached_evaluate(data,rules,RUNTIME/'calculation-cache')
    update(stage='analysing',message='The FX analyst is reviewing calculated findings')
    prompt_file=folder/'fx_analyst.md'
    prompt=prompt_file.read_text(encoding='utf-8') if prompt_file.exists() else PROMPT_PATH.read_text(encoding='utf-8')
    with LOCK:
        previous=STATE['analysis']
    authored=generate_suggestions(ask_model,prompt,data,result,rules,handoff,previous,progress=lambda message:update(stage='analysing',message=message))
    result.update(authored)
    result.update(id=event['id'],created=datetime.now().isoformat(),rules=rules,context=data,handoff=handoff,standardizer_mode='Python validation',calculation_cache_hit=cache_hit,change=event['change'],analyst_model=os.getenv('FX_ANALYST_MODEL','gpt-4.1'))
    (folder/'analysis.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    with database() as db:
        db.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?)',(result['id'],result['created'],json.dumps(result)))
    with LOCK:
        runs = ([{'id':result['id'],'created':result['created'],'change':event['change'],'count':len(result['findings'])}]+STATE['runs'])[:20]
    update(stage='ready',message=('Analysis updated · POC review warnings' if result.get('validation_warnings') else 'Analysis is up to date') if result['suggestion_origin']=='analyst' else 'Calculations updated · analyst suggestions unavailable',analysis=result,runs=runs,ai_mode=result['ai_mode'])

def worker():
    while not STOP.is_set():
        try:
            consumer = KafkaConsumer(TOPIC,bootstrap_servers='localhost:9092',group_id='jn-fx-analyst-v1',auto_offset_reset='earliest',enable_auto_commit=False,value_deserializer=lambda b:json.loads(b),consumer_timeout_ms=1000)
            update(kafka=True)
            while not STOP.is_set():
                for message in consumer:
                    event = message.value
                    with database() as db:
                        exists = db.execute('SELECT 1 FROM runs WHERE id=?',(event['id'],)).fetchone()
                    # Never resume an uncompleted paid job just because the app
                    # restarted. Only events explicitly queued this boot run.
                    if not exists and event.get('session')==BOOT_ID:
                        try:
                            process(event)
                        except Exception as exc:
                            update(stage='error',message=f'Analysis stopped ({type(exc).__name__}). Saved analysis is preserved; no automatic retry.')
                        finally:
                            with LOCK:
                                if STATE.get('active_run_id')==event['id']:
                                    update(active_run_id=None)
                    consumer.commit()
            consumer.close()
        except Exception:
            update(kafka=False,stage='connecting',message='Waiting for Kafka on localhost:9092')
            STOP.wait(4)

def signatures():
    return tuple((p.name,p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else (p.name,None,None) for p in [*(SHARED/f for f in FILES),RULES_PATH,PROMPT_PATH])

def business_hash(data,rules):
    return digest(data)+hashlib.sha256(json.dumps(rules,sort_keys=True).encode()).hexdigest()


def initialize_inputs():
    """Establish a baseline on startup, never enqueue an analysis."""
    global LAST_INPUT_HASH,WATCH_SIGNATURE
    signature=signatures()
    data=read_context(SHARED)
    rules=json.loads(RULES_PATH.read_text(encoding='utf-8'))
    if signatures()!=signature:
        raise ValueError('Workbook save is still in progress')
    LAST_INPUT_HASH=business_hash(data,rules)
    WATCH_SIGNATURE=signature


def queue_analysis(change,*,automatic=False,expected_signature=None):
    global LAST_INPUT_HASH
    with RUN_LOCK:
        with LOCK:
            active=STATE.get('active_run_id')
        if active and not automatic:
            return dict(id=active,queued=False,in_progress=True,active=True)
        signature=signatures()
        if expected_signature is not None and signature!=expected_signature:
            raise ValueError('Workbook save is still in progress')
        data=read_context(SHARED)
        rule_text=RULES_PATH.read_text(encoding='utf-8')
        content_hash=business_hash(data,json.loads(rule_text))
        if automatic and content_hash==LAST_INPUT_HASH:
            return dict(queued=False,unchanged=True)
        prompt_text=PROMPT_PATH.read_text(encoding='utf-8')
        run_id=uuid.uuid4().hex
        folder=RUNTIME/'snapshots'/run_id
        folder.mkdir(parents=True)
        for filename in FILES:
            if (SHARED/filename).exists():shutil.copy2(SHARED/filename,folder/filename)
        (folder/'demo-rules.json').write_text(rule_text,encoding='utf-8')
        (folder/'fx_analyst.md').write_text(prompt_text,encoding='utf-8')
        if signatures()!=signature or digest(read_context(folder))!=digest(data):
            raise ValueError('Inputs changed while snapshotting; no analysis was queued')
        event=dict(id=run_id,hash=content_hash,change=change,session=BOOT_ID)
        producer=None
        try:
            producer=KafkaProducer(bootstrap_servers='localhost:9092',value_serializer=lambda v:json.dumps(v).encode(),retries=0,request_timeout_ms=10000,max_block_ms=10000)
            update(stage='queued',message='Analysis requested',active_run_id=run_id)
            producer.send(TOPIC,event).get(timeout=15)
            LAST_INPUT_HASH=content_hash
            update(kafka=True)
        except Exception:
            update(stage='error',message='Could not confirm analysis delivery. No automatic retry.',active_run_id=None)
            raise
        finally:
            if producer is not None:producer.close(timeout=5)
        with LOCK:
            active=STATE.get('active_run_id')==run_id
        return dict(id=run_id,queued=True,active=active)


def watcher():
    global LAST_INPUT_HASH
    last_signature = WATCH_SIGNATURE
    while not STOP.wait(2):
        try:
            signature = signatures()
            if signature == last_signature:
                continue
            if STOP.wait(2):
                break
            if signatures()!=signature:
                continue
            if LAST_INPUT_HASH is None:
                initialize_inputs()
                last_signature = signature
                continue
            last_signature=signature
            queue_analysis('Workbook or demo-rule values changed',automatic=True,expected_signature=signature)
        except Exception as exc:
            update(stage='error',message=f'Input/event error: {str(exc)[:180]}. Last successful analysis is preserved.')

@asynccontextmanager
async def lifespan(app):
    STOP.clear()
    seed()
    with database() as db:
        rows = db.execute('SELECT payload FROM runs ORDER BY created DESC LIMIT 20').fetchall()
    if rows:
        results = [json.loads(r[0]) for r in rows]
        update(analysis=results[0],runs=[dict(id=r['id'],created=r['created'],change=r['change'],count=len(r['findings'])) for r in results])
    try:
        initialize_inputs()
        update(stage='ready',message='Saved analysis restored · no new analysis started' if rows else 'Ready · select Run analyst',active_run_id=None)
    except Exception:
        update(stage='error',message='Waiting for valid, stable workbook inputs. No analysis started.')
    threads = [threading.Thread(target=worker,daemon=True),threading.Thread(target=watcher,daemon=True)]
    for thread in threads:
        thread.start()
    yield
    STOP.set()

app = FastAPI(title='JN Treasury FX Analyst — synthetic POC',lifespan=lifespan)

@app.get('/health')
def health():
    return {'status':'ok','kafka':STATE['kafka'],'stage':STATE['stage']}

@app.get('/api/state')
def state():
    with LOCK:
        return json.loads(json.dumps(STATE))


@app.post('/api/analyse',status_code=202)
def analyse():
    try:
        return queue_analysis('Manual analyst run')
    except ValueError as exc:
        raise HTTPException(409,str(exc)) from None
    except Exception:
        raise HTTPException(503,'Could not confirm analysis delivery. No automatic retry.') from None

@app.get('/api/events')
async def events():
    async def stream():
        revision = -1
        while True:
            with LOCK:
                current = STATE['revision']
                payload = json.dumps(STATE)
            if revision!=current:
                yield f'data: {payload}\n\n'
                revision=current
            else:
                yield ': heartbeat\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})

@app.get('/api/runs/{run_id}')
def run(run_id:str):
    with database() as db:
        row = db.execute('SELECT payload FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row:
        raise HTTPException(404,'Analysis not found')
    return json.loads(row[0])

class Chat(BaseModel):
    run_id:str
    suggestion_id:str
    message:str = Field(min_length=1,max_length=3000)

@app.get('/api/chat/{run_id}/{suggestion_id}')
def history(run_id:str,suggestion_id:str):
    with database() as db:
        rows=db.execute('SELECT role,content FROM chats WHERE run_id=? AND suggestion_id=? ORDER BY id',(run_id,suggestion_id)).fetchall()
    return [dict(role=r[0],content=r[1]) for r in rows]

@app.post('/api/chat')
def chat(body:Chat):
    analysis = run(body.run_id)
    suggestion = next((s for s in analysis['suggestions'] if s['id']==body.suggestion_id),None)
    if not suggestion:
        raise HTTPException(404,'Suggestion not found')
    messages=history(body.run_id,body.suggestion_id)[-10:]
    try:
        catalog=build_evidence(analysis['context'],analysis,analysis['rules'])
        catalog.update(analysis.get('analyst_audit',{}).get('evidence_catalog',{}))
        snapshot_prompt=RUNTIME/'snapshots'/body.run_id/'fx_analyst.md'
        prompt=(snapshot_prompt if snapshot_prompt.exists() else PROMPT_PATH).read_text(encoding='utf-8')
        prompt+='\nFor THIS follow-up conversation only: return plain English, not the suggestion JSON schema. Answer the selected suggestion using its original snapshot. Ground figures in evidence and use calculation tools for new transaction amounts. Readable abbreviations and rounding are acceptable; numerical-wording matching is not performed. All output remains synthetic and advisory, never executed.'
        with LOCK:
            latest=STATE['analysis']
        handlers={'simulate_fx_conversion':dict(description='Compute an illustrative conversion, cash-buffer impact and exposure; no execution.',parameters={'type':'object','properties':{'source':{'type':'string','enum':list(CURRENCIES)},'target':{'type':'string','enum':list(CURRENCIES)},'amount':{'type':'number','exclusiveMinimum':0}},'required':['source','target','amount'],'additionalProperties':False},handler=lambda **args:simulate_conversion(analysis['context'],analysis,analysis['rules'],catalog,**args))}
        handlers['assess_funding_plan']=funding_plan_tool(analysis['context'],analysis,analysis['rules'],catalog)
        handlers.update(scenario_tools(analysis['context'],analysis,analysis['rules'],catalog))
        for spec in handlers.values():
            if 'handler' in spec:spec['handler']=cached_handler(spec['handler'])
        action_facts=(suggestion.get('recommended_action') or {}).get('fact_ids',[])
        cited={id:catalog[id] for id in suggestion.get('fact_ids',[])+action_facts if id in catalog}
        content={'suggestion':suggestion,'facts':compact_catalog(cited),'positions':analysis.get('positions',[]),'policy':analysis['rules'],'snapshot_id':analysis['id'],'newer_snapshot_available':bool(latest and latest['id']!=analysis['id']),'history':messages,'question':body.message}
        tools={'read_treasury_context':('Read this suggestion’s original workbook snapshot, including simulation terms and order IDs.',analysis['context']),'get_demo_policy':('Read the demo-rule version used in this suggestion.',analysis['rules']),'evaluate_treasury_limits':('Read immutable calculated positions, forecasts and findings.',{k:analysis[k] for k in ('positions','forecast','findings','exposure_pct','metrics') if k in analysis}),'get_evidence_catalog':('Read supported fact IDs, values, units and rules; provenance stays local.',catalog)}
        answer=ask_model(prompt,model_json(content),tools,tool_handlers=handlers,required_tools=set(),max_tokens=800,model=os.getenv('FX_ANALYST_MODEL','gpt-4.1'))
        mode='Live AI'
    except Exception as exc:
        # Do not log provider bodies, prompts, workbook data or credentials.
        logging.getLogger(__name__).warning('Chat answer unavailable (%s)',type(exc).__name__)
        answer='The analyst could not return a validated answer. Please try again; no transaction has been executed.'
        mode='Analyst unavailable'
    with database() as db:
        db.executemany('INSERT INTO chats(run_id,suggestion_id,role,content) VALUES (?,?,?,?)',[(body.run_id,body.suggestion_id,'user',body.message),(body.run_id,body.suggestion_id,'assistant',answer)])
    return dict(answer=answer,mode=mode)

@app.get('/api/logo')
def logo():
    from fastapi.responses import FileResponse
    return FileResponse(ROOT/'jn-logo.png')

dist = ROOT/'frontend'/'dist'
if dist.exists():
    app.mount('/',StaticFiles(directory=dist,html=True),name='ui')
