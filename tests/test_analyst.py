import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend.analyst import build_evidence, validate_output, generate_suggestions, simulate_conversion
from backend.app import read_context, ask_model
from backend.calculations import evaluate
from backend.seed import seed, ROOT

class AnalystTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace=tempfile.TemporaryDirectory()
        with patch('backend.seed.SHARED',Path(cls.workspace.name)):
            seed()
        cls.data=read_context(Path(cls.workspace.name))
        cls.rules=json.loads((ROOT/'policies/demo-rules.json').read_text(encoding='utf-8'))
        cls.result=evaluate(cls.data,cls.rules,datetime.fromisoformat(cls.data['accounts'][0]['as_of']))
        cls.catalog=build_evidence(cls.data,cls.result,cls.rules)
        cls.prompt=(ROOT/'backend/prompts/fx_analyst.md').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def response(self):
        return dict(brief='Prioritize the dollar funding gap while protecting reserves and confirming receipt timing.',suggestions=[dict(
            topic='funding',currency='USD',severity='critical',title='Confirm dollar funding before settlement',
            summary=f'Review USD 1,300,000 of funding before {self.catalog["USD.first_breach"]["display"]}.',
            detail='The forecast falls below the operating buffer. Confirm receipts and review approved funding; protected reserves must remain untouched.',
            assumptions=['Receipt timing remains uncertain.'],finding_ids=['liquidity-USD'],fact_ids=['USD.buffer_gap','USD.first_breach'],rule_ids=['minimum_available'])])

    def test_valid_model_authored_output_retains_its_wording(self):
        body=self.response()
        brief,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertEqual(cards[0]['summary'],body['suggestions'][0]['summary'])
        self.assertEqual(cards[0]['author'],'fx-analyst')
        self.assertTrue(cards[0]['sources'])

    def test_numbers_are_not_matched_against_catalog(self):
        body=self.response()
        body['suggestions'][0]['summary']='Arrange USD 9,876,543 before settlement.'
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertEqual(cards[0]['summary'],body['suggestions'][0]['summary'])

    def test_numeric_text_does_not_auto_attach_citations(self):
        body=self.response()
        body['suggestions'][0]['fact_ids'].remove('USD.first_breach')
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertEqual(cards[0]['summary'],body['suggestions'][0]['summary'])
        self.assertEqual(cards[0]['resolved_fact_ids'],[])
        self.assertEqual(cards[0]['fact_ids'],body['suggestions'][0]['fact_ids'])

    def test_percentage_wording_is_preserved(self):
        body=self.response()
        body['suggestions'][0]['detail']='The demo exposure limit is 15%. Confirm funding without using protected reserves.'
        body['suggestions'][0]['fact_ids'].append('exposure.limit')
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertIn('15%',cards[0]['detail'])

    def test_currency_wording_is_left_for_factual_review(self):
        body=self.response()
        body['suggestions'][0]['summary']='Arrange CAD 1,300,000 before settlement.'
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertEqual(cards[0]['summary'],body['suggestions'][0]['summary'])

    def test_abbreviated_overview_and_card_numbers_do_not_block_publishing(self):
        body=self.response()
        body['brief']='Arrange USD 1.3 million to protect the operating buffer.'
        body['suggestions'][0]['summary']='Review USD 1.3 million before settlement.'
        brief,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertEqual(brief,body['brief'])
        self.assertEqual(cards[0]['summary'],body['suggestions'][0]['summary'])

    def test_numeric_wording_produces_no_warnings_or_retry(self):
        body=self.response()
        body['brief']='Arrange USD 1.3 million to protect the operating buffer.'
        body['suggestions'][0]['summary']='Review USD 1.3 million by November 17, 2026.'
        body['suggestions'][0]['assumptions']=['1. Confirm receipts; 2. Review the 111% ratio.']
        calls=[]
        def model(*args,**kwargs):
            calls.append(kwargs['response_schema']['title'])
            return json.dumps(body if calls[-1]=='AnalystOutput' else {'valid':True,'errors':[]})
        output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(output['brief'],body['brief'])
        self.assertEqual(calls,['AnalystOutput','EvidenceReview'])
        self.assertEqual(output['validation_mode'],'narrative_warnings')
        self.assertEqual(output['validation_warnings'],[])
        self.assertFalse(output['analyst_audit']['numerical_wording_check'])
        self.assertEqual(output['suggestions'][0]['summary'],body['suggestions'][0]['summary'])
        self.assertEqual(output['validation_warnings'],output['analyst_audit']['validation_warnings'])

    def test_unknown_fact_finding_and_rule_rejected(self):
        for field,value in [('fact_ids',['invented']),('finding_ids',['invented']),('rule_ids',['invented'])]:
            body=self.response()
            body['suggestions'][0][field]=value
            with self.assertRaises(ValueError):
                validate_output(json.dumps(body),self.catalog,self.result,self.rules)

    def test_critical_omission_or_downgrade_rejected(self):
        for mode in ('omit','downgrade'):
            body=self.response()
            if mode=='omit':body['suggestions']=[]
            else:body['suggestions'][0]['severity']='info'
            with self.assertRaises(ValueError):
                validate_output(json.dumps(body),self.catalog,self.result,self.rules)

    def test_copied_template_rejected(self):
        body=self.response()
        body['suggestions'][0]['summary']=self.result['findings'][0]['summary']
        with self.assertRaises(ValueError):
            validate_output(json.dumps(body),self.catalog,self.result,self.rules)

    def test_model_writer_never_receives_engine_suggestion_templates(self):
        def writer(prompt,content,tools=None,**kwargs):
            schema=kwargs.get('response_schema',{}).get('title')
            if schema=='EvidenceReview':return json.dumps({'valid':True,'errors':[]})
            if schema=='AnalystOutput':
                topic_schema=kwargs['response_schema']['$defs']['Card']['properties']['topic']
                self.assertNotIn('enum',topic_schema)
            self.assertEqual(schema,'AnalystOutput')
            self.assertNotIn('CURRENT STAGE:',prompt)
            self.assertIn('read_treasury_context',kwargs['required_tools'])
            self.assertIn('assess_funding_plan',kwargs['tool_handlers'])
            self.assertNotIn('assess_funding_plan',kwargs['required_tools'])
            self.assertEqual(tools['read_treasury_context'][1],self.data)
            for finding in tools['evaluate_treasury_limits'][1]['findings']:
                self.assertNotIn('summary',finding)
                self.assertNotIn('title',finding)
                self.assertNotIn('detail',finding)
            return json.dumps(self.response())
        result=generate_suggestions(writer,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(result['suggestion_origin'],'analyst')

    def test_duplicate_cards_and_more_than_five_rejected(self):
        for count in (2,6):
            body=self.response()
            body['suggestions']*=count
            with self.assertRaises(ValueError):
                validate_output(json.dumps(body),self.catalog,self.result,self.rules)

    def test_opportunity_without_a_threshold_breach_is_allowed(self):
        body=self.response()
        body['suggestions'].append(dict(topic='idle_cash',currency='CAD',severity='info',title='Review a short-term Canadian-dollar placement',summary='Consider reviewing idle cash after confirming obligations and retaining the operating buffer.',detail='The projected daily minimum remains above the demo buffer. Confirm obligations beyond the forecast and receipt certainty before reviewing any approved placement.',assumptions=['No investment yield or availability is assumed.'],finding_ids=[],fact_ids=['CAD.potential_surplus'],rule_ids=['minimum_available']))
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertEqual(len(cards),2)

    def test_independent_topic_without_a_finding_or_rule_is_allowed(self):
        body=self.response()
        body['suggestions'].append(dict(topic='receipt_confirmation_process',currency=None,severity='warning',title='Confirm the reliability of expected receipts',summary='Review receipt confirmation and escalation before relying on forecast balances.',detail='The forecast depends on scheduled cash flows. Ask Treasury to confirm receipt certainty and escalation arrangements rather than assume all receipts will settle as planned.',assumptions=['The snapshot does not establish the effectiveness of the confirmation process.'],finding_ids=[],fact_ids=['USD.projected_low'],rule_ids=[]))
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertEqual(cards[1]['topic'],'receipt_confirmation_process')
        self.assertEqual(cards[1]['finding_ids'],[])
        self.assertEqual(cards[1]['rule_ids'],[])
        self.assertIn('no specific demo rule',cards[1]['rule'])
        self.assertTrue(cards[1]['sources'])

    def test_open_ended_topics_still_need_workbook_evidence(self):
        body=self.response()
        body['suggestions'][0]['topic']='receipt_confirmation_process'
        body['suggestions'][0]['fact_ids']=[]
        with self.assertRaises(ValueError):
            validate_output(json.dumps(body),self.catalog,self.result,self.rules)

    def test_outage_never_substitutes_calculated_cards(self):
        def unavailable(*args,**kwargs):raise RuntimeError('Service unavailable')
        output=generate_suggestions(unavailable,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(output['suggestions'],[])
        self.assertEqual(output['suggestion_origin'],'unavailable')

    def test_invalid_output_can_be_retried_without_rewriting(self):
        responses=iter(['{}',json.dumps(self.response())])
        def model(*args,**kwargs):
            schema=kwargs.get('response_schema',{}).get('title')
            if schema=='EvidenceReview':return json.dumps({'valid':True,'errors':[]})
            if schema=='AnalystOutput':return next(responses)
            return 'Investigation record.'
        output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertEqual(len(output['analyst_audit']['validation_attempts']),1)

    def test_single_stage_generation_then_factual_review(self):
        calls=[]
        def model(prompt,content,tools=None,**kwargs):
            schema=kwargs['response_schema']['title']
            calls.append(schema)
            self.assertNotIn('investigation',json.loads(content))
            if schema=='AnalystOutput':
                self.assertIn('read_treasury_context',tools)
                return json.dumps(self.response())
            self.assertIsNone(tools)
            return json.dumps({'valid':True,'errors':[]})
        output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertEqual(calls,['AnalystOutput','EvidenceReview'])
        self.assertNotIn('investigation',output['analyst_audit'])
        self.assertNotIn('workflow_version',output['analyst_audit'])

    def test_independent_review_concerns_warn_without_suppressing_advice(self):
        def wrong(*args,**kwargs):
            schema=kwargs.get('response_schema',{}).get('title')
            if schema=='AnalystOutput':return json.dumps(self.response())
            if schema=='EvidenceReview':return json.dumps({'valid':False,'errors':['Reserve balance has been confused with reserve shortfall.']})
            return 'Investigation record.'
        output=generate_suggestions(wrong,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(output['suggestions'][0]['summary'],self.response()['suggestions'][0]['summary'])
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertTrue(any('Reserve balance' in w for w in output['validation_warnings']))
        self.assertFalse(output['analyst_audit']['evidence_review']['valid'])
        self.assertEqual(output['analyst_audit']['validation_attempts'],[])

    def test_review_outage_or_invalid_review_does_not_hide_authored_cards(self):
        for failure in ('invalid json',RuntimeError('private provider body')):
            def model(*args,**kwargs):
                if kwargs['response_schema']['title']=='AnalystOutput':return json.dumps(self.response())
                if isinstance(failure,Exception):raise failure
                return failure
            with self.subTest(failure=type(failure).__name__):
                output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Validated',None)
            self.assertEqual(output['suggestion_origin'],'analyst')
            self.assertTrue(any('review unavailable' in w for w in output['validation_warnings']))
            self.assertNotIn('private provider body',json.dumps(output))

    def test_conversion_uses_buy_sell_rates_and_never_mutates_snapshot(self):
        before=copy.deepcopy(self.data)
        catalog=copy.deepcopy(self.catalog)
        scenario=simulate_conversion(self.data,self.result,self.rules,catalog,'CAD','USD',150000)
        received=next(f for f in scenario['facts'] if f['id'].endswith('target_received'))
        self.assertEqual(received['value'],101867.92)
        self.assertTrue(scenario['source_buffer_preserved'])
        self.assertEqual(self.data,before)
        self.assertTrue(all(f['id'] in catalog for f in scenario['facts']))

    def test_insufficient_conversion_is_reported_not_executed(self):
        scenario=simulate_conversion(self.data,self.result,self.rules,copy.deepcopy(self.catalog),'CAD','USD',10000000)
        self.assertFalse(scenario['source_funds_sufficient'])
        self.assertFalse(scenario['source_buffer_preserved'])

    def test_client_enforces_required_tools_and_records_copied_results(self):
        results={'get_demo_policy':('Rules',{'version':'demo'}),'evaluate_treasury_limits':('Facts',{'findings':[]})}
        class Response:
            def __init__(self,body):self.body=json.dumps(body).encode()
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return self.body
        requests=[]
        def urlopen(request,**kwargs):
            body=json.loads(request.data)
            requests.append(body)
            choice=body['tool_choice']
            if isinstance(choice,dict):
                name=choice['function']['name']
                message={'role':'assistant','content':None,'tool_calls':[{'id':name,'type':'function','function':{'name':name,'arguments':'{}'}}]}
            else:message={'role':'assistant','content':'Done'}
            return Response({'choices':[{'message':message}]})
        trace=[]
        with patch('backend.app.key',return_value='test'),patch('backend.app.urllib.request.urlopen',side_effect=urlopen):
            answer=ask_model('instructions','context',results,required_tools=set(results),trace=trace)
        self.assertEqual(answer,'Done')
        self.assertEqual({entry['name'] for entry in trace},set(results))
        results['evaluate_treasury_limits'][1]['findings'].append('later')
        self.assertEqual(trace[0]['result']['findings'],[])

if __name__=='__main__':unittest.main()
