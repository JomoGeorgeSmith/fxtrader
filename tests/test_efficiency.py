import asyncio
import copy
import json
import tempfile
import threading
import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend import app
from backend.analyst import build_evidence, review_suggestions, generate_suggestions
from backend.calculation_cache import cached_evaluate
from backend.calculations import evaluate
from backend.model_context import compact, compact_catalog, scenario_summary, cached_handler
from backend.seed import ROOT, seed


class EfficiencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace=tempfile.TemporaryDirectory()
        cls.shared=Path(cls.workspace.name)
        with patch('backend.seed.SHARED',cls.shared):seed()
        cls.data=app.read_context(cls.shared)
        cls.rules=json.loads((ROOT/'policies/demo-rules.json').read_text(encoding='utf-8'))
        cls.now=datetime.fromisoformat(cls.data['accounts'][0]['as_of'])
        cls.result=evaluate(cls.data,cls.rules,cls.now)
        cls.catalog=build_evidence(cls.data,cls.result,cls.rules)

    @classmethod
    def tearDownClass(cls):cls.workspace.cleanup()

    def setUp(self):
        self.network=patch('backend.app.urllib.request.urlopen',side_effect=AssertionError('No paid network calls in tests'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_compact_views_preserve_values_and_full_local_provenance(self):
        before=copy.deepcopy(self.catalog)
        view=compact_catalog(self.catalog)
        self.assertLess(len(json.dumps(view)),len(json.dumps(self.catalog))*.65)
        for id,fact in self.catalog.items():
            self.assertEqual(view[id]['value'],fact['value'])
            self.assertEqual(view[id]['unit'],fact['unit'])
            self.assertEqual(view[id]['currency'],fact['currency'])
            self.assertNotIn('sources',view[id])
            self.assertNotIn('id',view[id])
        self.assertEqual(before,self.catalog)
        self.assertEqual(compact(self.data)['accounts'][0]['balance'],self.data['accounts'][0]['balance'])

    def test_scenario_summary_keeps_every_check_but_omits_repeated_daily_rows(self):
        result=dict(source='CAD',target='USD',exposure_within_demo_limit=False,
            scenarios={'baseline':dict(source_buffer_preserved=True,target_buffer_preserved=False,
                target_remaining_buffer_gap=250000,passes_modeled_checks=False,balances=[dict(date='2026-11-17',USD=750000)])},
            facts=[dict(id='plan.test.target_low',value=750000,currency='USD',sources=['Workbook row'])])
        value=scenario_summary(result)
        self.assertNotIn('balances',value['scenarios']['baseline'])
        self.assertFalse(value['scenarios']['baseline']['passes_modeled_checks'])
        self.assertEqual(value['scenarios']['baseline']['target_remaining_buffer_gap'],250000)
        self.assertEqual(value['facts'][0]['id'],'plan.test.target_low')
        self.assertIn('balances',result['scenarios']['baseline'])

    def test_provider_gets_compact_tool_result_but_audit_keeps_full_result(self):
        requests=[];trace=[]
        value={'scenarios':{'baseline':{'passes_modeled_checks':False,'balances':[{'USD':1}]}},'facts':[dict(id='plan.x.low',value=1,sources=['Full provenance'])]}
        class Response:
            def __init__(self,message):self.body=json.dumps({'choices':[{'message':message}]}).encode()
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return self.body
        def provider(request,**kwargs):
            body=json.loads(request.data);requests.append(body)
            if len(requests)==1:
                return Response(dict(role='assistant',content=None,tool_calls=[dict(id='tool-1',type='function',function=dict(name='assess_funding_plan',arguments='{}'))]))
            result=json.loads(body['messages'][-1]['content'])
            self.assertNotIn('balances',result['scenarios']['baseline'])
            self.assertNotIn('sources',result['facts'][0])
            return Response(dict(role='assistant',content='Rejected on modeled checks.'))
        with patch.object(app,'key',return_value='fake'),patch('backend.app.urllib.request.urlopen',side_effect=provider):
            app.ask_model('Analyst','Assigned shortfall',tool_handlers={'assess_funding_plan':dict(description='Assess',parameters={},handler=lambda:value)},required_tools={'assess_funding_plan'},trace=trace)
        self.assertEqual(trace[0]['result'],value)

    def test_review_receives_claim_support_not_workbooks_or_full_forecast(self):
        card=dict(id='funding-USD',title='Review funding',summary='Confirm the funding need.',detail='Use approved funding while preserving the operating buffer.',assumptions=[],fact_ids=['USD.buffer_gap'])
        def reviewer(system,content,**kwargs):
            payload=json.loads(content)
            self.assertNotIn('workbooks',payload)
            self.assertNotIn('forecast',payload)
            self.assertIn('USD.buffer_gap',payload['catalog'])
            self.assertNotIn('CAD.rate.previous_sell',payload['catalog'])
            self.assertLess(len(payload['catalog']),len(self.catalog))
            self.assertNotIn('sources',payload['catalog']['USD.buffer_gap'])
            return json.dumps(dict(valid=True,errors=[]))
        self.assertTrue(review_suggestions(reviewer,'same-model','Review current funding.',[card],self.result,self.catalog,self.rules,[]).valid)

    def test_without_shortfalls_only_writer_and_review_are_called(self):
        result=copy.deepcopy(self.result)
        result['findings']=[]
        for p in result['positions']:
            p['projected_low']=p['confirmed_receipts_low']=p['minimum']+1
        calls=[]
        def model(system,content,tools=None,**kwargs):
            title=kwargs['response_schema']['title'];calls.append(title)
            if title=='AnalystOutput':return json.dumps(dict(brief='No additional action is supported by this snapshot.',suggestions=[]))
            return json.dumps(dict(valid=True,errors=[]))
        output=generate_suggestions(model,'Instructions',self.data,result,self.rules,'Python handoff',None)
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertEqual(calls,['AnalystOutput','EvidenceReview'])

    def test_standardization_uses_no_model(self):
        with patch.object(app,'ask_model') as model:
            handoff=app.standardize(self.data)
        self.assertIn('Python-validated',handoff)
        model.assert_not_called()

    def test_calculation_cache_reuses_unchanged_input_and_returns_independent_data(self):
        with tempfile.TemporaryDirectory() as directory,patch('backend.calculation_cache.evaluate',wraps=evaluate) as calculate:
            first,hit=cached_evaluate(self.data,self.rules,directory,self.now)
            self.assertFalse(hit)
            first['positions'][0]['available']=-999
            second,hit=cached_evaluate(self.data,self.rules,directory,self.now)
            self.assertTrue(hit)
            self.assertNotEqual(second['positions'][0]['available'],-999)
            self.assertEqual(calculate.call_count,1)
            data=copy.deepcopy(self.data);data['accounts'][0]['balance']+=1
            self.assertFalse(cached_evaluate(data,self.rules,directory,self.now)[1])
            rules=copy.deepcopy(self.rules);rules['minimum_available']['USD']+=1
            self.assertFalse(cached_evaluate(self.data,rules,directory,self.now)[1])
            self.assertFalse(cached_evaluate(self.data,self.rules,directory,self.now+timedelta(days=1))[1])

    def test_calculation_cache_invalidates_on_quote_age_boundary(self):
        now=datetime(2026,10,28,8)
        data=copy.deepcopy(self.data)
        for rate in data['rates']:rate['as_of']=now.isoformat()
        rules=copy.deepcopy(self.rules);rules['rate_max_age_hours']=6
        with tempfile.TemporaryDirectory() as directory:
            first,_=cached_evaluate(data,rules,directory,now)
            later,hit=cached_evaluate(data,rules,directory,now+timedelta(hours=7))
        self.assertFalse(hit)
        self.assertFalse(any(f['id'].startswith('stale-') for f in first['findings']))
        self.assertTrue(any(f['id'].startswith('stale-') for f in later['findings']))

    def test_corrupt_or_unwritable_cache_does_not_block_calculations(self):
        with tempfile.TemporaryDirectory() as directory:
            first,_=cached_evaluate(self.data,self.rules,directory,self.now)
            next(Path(directory).glob('*.json')).write_text('invalid json',encoding='utf-8')
            second,hit=cached_evaluate(self.data,self.rules,directory,self.now)
            self.assertFalse(hit)
            self.assertEqual(first,second)
        with tempfile.TemporaryDirectory() as directory,patch.object(Path,'mkdir',side_effect=PermissionError):
            result,hit=cached_evaluate(self.data,self.rules,directory,self.now)
        self.assertFalse(hit)
        self.assertEqual(first,result)

    def test_identical_tool_calculation_is_cached_without_shared_mutation(self):
        handler=Mock(side_effect=lambda amount:dict(value=amount))
        cached=cached_handler(handler)
        first=cached(amount=10);first['value']=-1
        self.assertEqual(cached(amount=10),{'value':10})
        self.assertEqual(handler.call_count,1)
        self.assertEqual(cached(amount=20),{'value':20})
        self.assertEqual(handler.call_count,2)

    def test_startup_restores_results_and_does_not_enqueue_or_call_model(self):
        analysis=dict(id='saved',created=self.now.isoformat(),change='Saved analysis',findings=[],suggestions=[])
        db=Mock();db.execute.return_value.fetchall.return_value=[(json.dumps(analysis),)]
        async def start_stop():
            async with app.lifespan(app.app):
                self.assertEqual(app.STATE['analysis']['id'],'saved')
                self.assertEqual(app.STATE['stage'],'ready')
                self.assertIsNone(app.STATE['active_run_id'])
        with patch.object(app,'STATE',dict(app.STATE)),patch.object(app,'STOP',threading.Event()),patch.object(app,'seed'),patch.object(app,'database',return_value=nullcontext(db)),patch.object(app,'initialize_inputs'),patch.object(app.threading,'Thread'),patch.object(app,'queue_analysis') as queue,patch.object(app,'ask_model') as model:
            asyncio.run(start_stop())
        queue.assert_not_called();model.assert_not_called()

    def test_old_pending_event_is_not_replayed_on_startup(self):
        consumer=Mock();consumer.__iter__=Mock(return_value=iter([SimpleNamespace(value=dict(id='old',session='old-boot'))]))
        stop=Mock();stop.is_set.side_effect=[False,False,True,True]
        db=Mock();db.execute.return_value.fetchone.return_value=None
        with patch.object(app,'STOP',stop),patch.object(app,'KafkaConsumer',return_value=consumer),patch.object(app,'database',return_value=nullcontext(db)),patch.object(app,'process') as process:
            app.worker()
        process.assert_not_called();consumer.commit.assert_called_once()

    def test_new_requested_event_runs_once_without_regeneration_by_worker(self):
        event=dict(id='new-request',session=app.BOOT_ID)
        consumer=Mock();consumer.__iter__=Mock(return_value=iter([SimpleNamespace(value=event)]))
        stop=Mock();stop.is_set.side_effect=[False,False,True,True]
        db=Mock();db.execute.return_value.fetchone.return_value=None
        with patch.object(app,'STATE',dict(app.STATE,active_run_id=event['id'])),patch.object(app,'STOP',stop),patch.object(app,'KafkaConsumer',return_value=consumer),patch.object(app,'database',return_value=nullcontext(db)),patch.object(app,'process') as process:
            app.worker()
            self.assertIsNone(app.STATE['active_run_id'])
        process.assert_called_once_with(event)

    def test_manual_request_queues_once_and_duplicate_click_joins_active_run(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'RUNTIME',Path(directory)),patch.object(app,'SHARED',self.shared),patch.object(app,'STATE',dict(app.STATE,active_run_id=None)),patch.object(app,'LAST_INPUT_HASH',None),patch.object(app,'KafkaProducer') as factory,patch.object(app,'ask_model') as model:
            first=app.queue_analysis('Manual analyst run')
            second=app.queue_analysis('Manual analyst run')
            event=factory.return_value.send.call_args.args[1]
            self.assertTrue(first['queued'])
            self.assertFalse(second['queued'])
            self.assertEqual(first['id'],second['id'])
            self.assertEqual(event['session'],app.BOOT_ID)
            factory.return_value.send.assert_called_once()
            model.assert_not_called()
            self.assertEqual(app.read_context(Path(directory)/'snapshots'/first['id']),self.data)

    def test_unchanged_inputs_including_prompt_only_change_do_not_auto_run(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'RUNTIME',Path(directory)),patch.object(app,'SHARED',self.shared),patch.object(app,'LAST_INPUT_HASH',app.business_hash(self.data,self.rules)),patch.object(app,'KafkaProducer') as producer,patch.object(app,'ask_model') as model:
            result=app.queue_analysis('Changed metadata',automatic=True)
        self.assertTrue(result.get('unchanged'),result)
        producer.assert_not_called();model.assert_not_called()

    def test_meaningful_change_queues_once_then_becomes_baseline(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'RUNTIME',Path(directory)),patch.object(app,'SHARED',self.shared),patch.object(app,'STATE',dict(app.STATE,active_run_id=None)),patch.object(app,'LAST_INPUT_HASH','old-business-values'),patch.object(app,'KafkaProducer') as factory:
            self.assertTrue(app.queue_analysis('Changed values',automatic=True)['queued'])
            self.assertTrue(app.queue_analysis('Same values',automatic=True)['unchanged'])
            factory.return_value.send.assert_called_once()

    def test_unstable_snapshot_never_enqueues(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(app,'RUNTIME',Path(directory)),patch.object(app,'SHARED',self.shared),patch.object(app,'STATE',dict(app.STATE,active_run_id=None)),patch.object(app,'signatures',side_effect=['before','after']),patch.object(app,'KafkaProducer') as producer:
            with self.assertRaisesRegex(ValueError,'Inputs changed'):
                app.queue_analysis('Manual analyst run')
        producer.assert_not_called()

    def test_manual_endpoint_submits_one_request_without_calling_a_model(self):
        with patch.object(app,'queue_analysis',return_value={'id':'requested','queued':True,'active':True}) as queue,patch.object(app,'ask_model') as model:
            self.assertTrue(app.analyse()['queued'])
        queue.assert_called_once_with('Manual analyst run')
        model.assert_not_called()


if __name__=='__main__':unittest.main()
