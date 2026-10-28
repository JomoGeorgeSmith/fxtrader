import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.analyst import build_evidence, generate_suggestions, simulate_conversion
from backend.app import ask_model, read_context
from backend.calculations import evaluate
from backend.funding_plan import funding_plan_tool
from backend.orchestrator import funding_tasks, run_orchestrator
from backend.seed import ROOT, seed
from backend.model_context import compact


class OrchestratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace=tempfile.TemporaryDirectory()
        with patch('backend.seed.SHARED',Path(cls.workspace.name)):seed()
        cls.data=read_context(Path(cls.workspace.name))
        cls.rules=json.loads((ROOT/'policies/demo-rules.json').read_text())
        cls.now=datetime.fromisoformat(cls.data['accounts'][0]['as_of'])
        cls.prompt=(ROOT/'backend/prompts/fx_analyst.md').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):cls.workspace.cleanup()

    def setUp(self):
        self.data=copy.deepcopy(type(self).data)
        self.result=evaluate(self.data,self.rules,self.now)
        self.trace=[]
        self.catalog=build_evidence(self.data,self.result,self.rules)

    def run_graph(self,ask):
        handlers={'assess_funding_plan':funding_plan_tool(self.data,self.result,self.rules,self.catalog),
            'simulate_fx_conversion':dict(handler=lambda **args:simulate_conversion(self.data,self.result,self.rules,self.catalog,**args))}
        return run_orchestrator(ask=ask,prompt=self.prompt,data=self.data,result=self.result,rules=self.rules,
            handoff='Full standardized context',previous=None,tools={},handlers=handlers,trace=self.trace,
            model='same-model',write=lambda state:state)

    def fake_ask(self,prompt,content,tools=None,**kwargs):
        payload=json.loads(content)
        self.assertEqual(payload['workbooks'],compact(self.data))
        self.assertEqual(payload['calculations']['positions'],self.result['positions'])
        self.assertEqual(payload['policy'],self.rules)
        task=payload['funding_task']
        name=task['required_tool']
        self.assertEqual(kwargs['required_tools'],{name})
        self.assertTrue(kwargs['stop_after_required_tools'])
        self.assertTrue(kwargs['model'])
        args=dict(source='CAD',target=task['currency'],amount=1000) if name=='simulate_fx_conversion' else dict(
            source='CAD',obligation_id=task['eligible_obligation_ids'][0],
            target_amount=task['baseline_buffer_gap'] or task['buffer_gap_without_expected_receipts'],
            settlement_date=self.result['forecast'][0]['date'],fee_source_amount=0,expected_receipt_delay_days=3)
        value=kwargs['tool_handlers'][name]['handler'](**args)
        kwargs['trace'].append(dict(name=name,arguments=args,result=value,stage=kwargs['trace_stage']))
        return 'Actual calculation returned; check the returned constraints before recommending.'

    def test_code_routes_required_assessment_without_an_ai_plan(self):
        before=copy.deepcopy(self.data)
        state=self.run_graph(self.fake_ask)
        self.assertNotIn('plan',state)
        self.assertEqual(state['funding_tasks'][0]['currency'],'USD')
        self.assertEqual(state['assessments'][0]['status'],'assessed')
        self.assertEqual(self.trace[0]['name'],'assess_funding_plan')
        self.assertIn('scenarios',self.trace[0]['result'])
        self.assertEqual(self.data,before)

    def test_no_planner_request_is_made(self):
        calls=[]
        def ask(*args,**kwargs):
            calls.append(kwargs.get('response_schema'))
            self.assertNotIn('response_schema',kwargs)
            return self.fake_ask(*args,**kwargs)
        state=self.run_graph(ask)
        self.assertEqual(state['assessments'][0]['status'],'assessed')
        self.assertEqual(calls,[None])
        self.assertEqual(state['warnings'],[])
        self.assertNotIn('memo',state['assessments'][0])

    def test_failed_worker_does_not_block_publish(self):
        def ask(*args,**kwargs):
            if kwargs.get('response_schema'):return json.dumps({'investigations':[]})
            kwargs['trace'].append(dict(name='assess_funding_plan',arguments={},result={'error':'Invalid inputs'}))
            raise RuntimeError('private details')
        state=self.run_graph(ask)
        self.assertEqual(state['assessments'][0]['status'],'failed')
        self.assertEqual(state['assessments'][0]['tool_calls'][0]['result']['error'],'Invalid inputs')
        self.assertTrue(state['warnings'])
        self.assertNotIn('private details',json.dumps(state))

    def test_interruption_after_calculation_retains_results(self):
        def ask(*args,**kwargs):
            reply=self.fake_ask(*args,**kwargs)
            if not kwargs.get('response_schema'):raise RuntimeError('late outage')
            return reply
        state=self.run_graph(ask)
        self.assertEqual(state['assessments'][0]['status'],'assessed')
        self.assertTrue(state['assessments'][0]['error'])
        self.assertIn('scenarios',state['assessments'][0]['tool_calls'][0]['result'])

    def test_no_shortfall_does_not_force_artificial_trade(self):
        for p in self.result['positions']:
            p['projected_low']=p['confirmed_receipts_low']=p['minimum']+1000000
        state=self.run_graph(self.fake_ask)
        self.assertEqual(state['funding_tasks'],[])
        self.assertEqual(state['assessments'],[])
        self.assertEqual(self.trace,[])

    def test_receipt_dependent_shortfall_is_routed(self):
        p=next(p for p in self.result['positions'] if p['currency']=='USD')
        p['projected_low']=p['minimum']+100
        p['confirmed_receipts_low']=p['minimum']-100
        tasks=funding_tasks(self.data,self.result)
        self.assertEqual(tasks[0]['baseline_buffer_gap'],0)
        self.assertEqual(tasks[0]['buffer_gap_without_expected_receipts'],100)

    def test_frozen_usd_shortfall_routes_both_deadlines_without_workbook_edits(self):
        # Fixed synthetic regression fixture, independent of today's date and
        # the user's live workbooks. No source currency is prescribed by routing.
        now=datetime(2026,10,28,12)
        for row in self.data['accounts']:
            row['balance']=5400000 if row['currency']=='USD' else 1000000000
            row['holds']=100000 if row['currency']=='USD' else 0
        self.data['payments']=[dict(id=id,currency='USD',amount=amount,value_date=day,status='Committed',_source='Frozen synthetic test')
            for id,amount,day in [('FROZEN-P1',4550000,'2026-11-14'),('FROZEN-P2',2200000,'2026-11-17')]]
        self.data['receipts']=[dict(id='FROZEN-R1',currency='USD',amount=1400000,value_date='2026-11-01',status='Expected',_source='Frozen synthetic test')]
        self.result=evaluate(self.data,self.rules,now)
        self.catalog=build_evidence(self.data,self.result,self.rules)
        state=self.run_graph(self.fake_ask)
        self.assertEqual(len(state['funding_tasks']),1)
        task=state['funding_tasks'][0]
        self.assertEqual(task['baseline_buffer_gap'],1050000)
        self.assertEqual(task['buffer_gap_without_expected_receipts'],2450000)
        self.assertEqual(task['baseline_deadline'],'2026-11-17')
        self.assertEqual(task['deadline_without_expected_receipts'],'2026-11-14')
        self.assertNotIn('source',task)
        self.assertEqual(state['assessments'][0]['status'],'assessed')

    def test_assessment_cannot_substitute_another_currency_obligation(self):
        def ask(prompt,content,tools=None,**kwargs):
            if kwargs.get('response_schema'):return json.dumps({'investigations':[]})
            wrong=next(r['id'] for r in self.data['payments'] if r['currency']=='GBP')
            with self.assertRaisesRegex(ValueError,'eligible payment'):
                kwargs['tool_handlers']['assess_funding_plan']['handler'](source='JMD',obligation_id=wrong,
                    target_amount=1000,settlement_date=self.result['forecast'][0]['date'])
            return self.fake_ask(prompt,content,tools,**kwargs)
        self.assertEqual(self.run_graph(ask)['assessments'][0]['status'],'assessed')

    def test_multiple_currencies_each_get_assessment(self):
        self.data['accounts'][2]['balance']=10000
        self.result=evaluate(self.data,self.rules,self.now)
        self.catalog=build_evidence(self.data,self.result,self.rules)
        state=self.run_graph(self.fake_ask)
        self.assertEqual({r['task']['currency'] for r in state['assessments']},{'USD','GBP'})
        self.assertTrue(all(r['status']=='assessed' for r in state['assessments']))

    def test_no_recorded_payment_uses_conversion_tool(self):
        self.data['payments']=[r for r in self.data['payments'] if r['currency']!='USD']
        self.data['accounts'][1]['balance']=200000
        self.result=evaluate(self.data,self.rules,self.now)
        self.catalog=build_evidence(self.data,self.result,self.rules)
        state=self.run_graph(self.fake_ask)
        self.assertEqual(state['funding_tasks'][0]['required_tool'],'simulate_fx_conversion')
        self.assertEqual(self.trace[0]['arguments']['target'],'USD')

    def test_infeasible_plan_is_assessed_not_claimed_suitable(self):
        state=self.run_graph(self.fake_ask)
        self.assertEqual(state['assessments'][0]['status'],'assessed')
        self.assertFalse(self.trace[0]['result']['scenarios']['baseline']['passes_modeled_checks'])

    def test_client_requires_financial_tool_and_returns_results_to_model(self):
        requests=[]
        class Response:
            def __init__(self,msg):self.body=json.dumps({'choices':[{'message':msg}]}).encode()
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return self.body
        def urlopen(req,**kwargs):
            body=json.loads(req.data);requests.append(body)
            if len(requests)<=2:
                self.assertEqual(body['tool_choice']['function']['name'],'assess_funding_plan')
                args='{"amount":1000}' if len(requests)==2 else 'not json'
                return Response(dict(role='assistant',content=None,tool_calls=[dict(id=f'call{len(requests)}',type='function',function=dict(name='assess_funding_plan',arguments=args))]))
            self.assertEqual(body['tool_choice'],'auto')
            self.assertIn('computed',body['messages'][-1]['content'])
            return Response(dict(role='assistant',content='A tested result is available.'))
        handlers={'assess_funding_plan':dict(description='Calculate',parameters={},handler=lambda amount:{'computed':amount})}
        with patch('backend.app.key',return_value='test'),patch('backend.app.urllib.request.urlopen',side_effect=urlopen):
            answer=ask_model('Analyst','Funding task',tool_handlers=handlers,required_tools={'assess_funding_plan'},trace=self.trace,trace_stage='funding_assessment')
        self.assertIn('tested',answer)
        self.assertIn('error',self.trace[0]['result'])
        self.assertEqual(self.trace[1]['result'],{'computed':1000})

    def test_graph_results_reach_writer_as_compact_evidence(self):
        writes=[]
        def ask(prompt,content,tools=None,**kwargs):
            schema=kwargs.get('response_schema',{}).get('title')
            if schema is None:return self.fake_ask(prompt,content,tools,**kwargs)
            if schema=='EvidenceReview':return json.dumps({'valid':True,'errors':[]})
            payload=json.loads(content)
            writes.append(payload)
            reports=payload['funding_assessments']
            self.assertEqual(reports[0]['status'],'assessed')
            self.assertIn('scenarios',reports[0]['calculations'][0]['result'])
            self.assertNotIn('balances',reports[0]['calculations'][0]['result']['scenarios']['baseline'])
            self.assertNotIn('memo',reports[0])
            fact=next(id for id in payload['evidence_catalog'] if id.startswith('plan.'))
            return json.dumps(dict(brief='Funding options have been tested against the recorded forecast.',suggestions=[dict(topic='tested_funding',currency='USD',severity='critical',title='Assess the modeled funding trade-off',summary='The tested plan has constraints that must be resolved before proceeding.',detail='The calculation checks cash buffers and exposure. Treat its failed checks as constraints, not an approved trade.',assumptions=['No transaction has been executed.'],finding_ids=['liquidity-USD'],fact_ids=[fact],rule_ids=[])]))
        output=generate_suggestions(ask,self.prompt,self.data,self.result,self.rules,'Full context',None)
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertEqual(len(writes),1)
        self.assertEqual(output['analyst_audit']['orchestration']['assessments'][0]['status'],'assessed')


if __name__=='__main__':unittest.main()
