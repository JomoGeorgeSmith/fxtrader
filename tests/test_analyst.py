import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend.analyst import build_evidence, validate_output, generate_suggestions, simulate_conversion, material_liquidity_changes, EvidenceReview, attach_review_warnings
from backend.app import read_context, ask_model
from backend.calculations import evaluate
from backend.seed import seed, ROOT
from backend.model_context import compact

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

    def setUp(self):
        # These tests isolate card writing/review. The real LangGraph routing
        # and forced financial-tool execution are exercised in test_orchestrator.
        route=patch('backend.analyst.run_orchestrator',side_effect=lambda **kw:kw['write'](dict(plan=None,funding_tasks=[],assessments=[],warnings=[])))
        route.start()
        self.addCleanup(route.stop)

    def test_valid_model_authored_output_retains_its_wording(self):
        body=self.response()
        brief,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertEqual(cards[0]['summary'],body['suggestions'][0]['summary'])
        self.assertEqual(cards[0]['author'],'fx-analyst')
        self.assertTrue(cards[0]['sources'])

    def test_material_usd_deterioration_is_supplied_to_writer_without_gating_cards(self):
        previous={'id':'previous','positions':[{'currency':'USD','available':300000,'minimum':1000000,'projected_low':-3100000}],
                  'suggestion_origin':'analyst','suggestions':[]}
        current=copy.deepcopy(self.result)
        usd=next(p for p in current['positions'] if p['currency']=='USD')
        usd.update(available=-90000,minimum=1000000,projected_low=-3490000)
        current['forecast']=[{'currency':'USD','date':'2026-10-29','balance':-90000}]
        changes=material_liquidity_changes(current,previous)
        self.assertEqual(changes[0]['current_gap'],4490000)
        self.assertEqual(changes[0]['increase'],390000)
        self.assertEqual(changes[0]['first_breach'],'2026-10-29')
        self.assertEqual(material_liquidity_changes(current,{'positions':current['positions']}),[])
        calls=[]
        def model(prompt,content,tools=None,**kwargs):
            if kwargs['response_schema']['title']=='AnalystOutput':
                changes=json.loads(content)['material_liquidity_changes']
                self.assertEqual(changes[0]['increase'],390000)
                self.assertIn('Do not merely repeat a prior card',prompt)
                calls.append('writer')
                return json.dumps(self.response())
            return json.dumps({'valid':True,'errors':[]})
        output=generate_suggestions(model,self.prompt,self.data,current,self.rules,'Validated',previous)
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertEqual(calls,['writer'])

    def test_writer_can_offer_a_conditional_reserve_next_move_without_claiming_an_executable_trade(self):
        data=copy.deepcopy(self.data)
        next(a for a in data['accounts'] if a['currency']=='USD').update(balance=400000,holds=100000)
        next(r for r in data['reserves'] if r['currency']=='USD')['balance']=5460000
        result=evaluate(data,self.rules,datetime.fromisoformat(data['accounts'][0]['as_of']))
        def model(prompt,content,tools=None,**kwargs):
            self.assertEqual(kwargs['response_schema']['title'],'AnalystOutput')
            self.assertIn('next_move',kwargs['response_schema']['$defs']['Card']['required'])
            self.assertIn('assess_reserve_transfer',kwargs['tool_handlers'])
            candidate=next(c for c in json.loads(content)['conditional_reserve_options'] if c['currency']=='USD')
            self.assertGreater(candidate['arithmetic_headroom'],0)
            day=result['forecast'][0]['date']
            transfer=kwargs['tool_handlers']['assess_reserve_transfer']['handler'](currency='USD',
                amount=min(round(candidate['baseline_buffer_gap'],2),candidate['arithmetic_headroom']),arrival_date=day,expected_receipt_delay_days=3)
            kwargs['trace'].append(dict(name='assess_reserve_transfer',result=transfer))
            body=self.response()
            body['suggestions'][0]['fact_ids'].append(transfer['facts'][0]['id'])
            body['suggestions'][0]['next_move']='Ask Treasury to confirm whether the USD reserve can transfer before today\'s obligations; if not, escalate a same-day facility or payment timing decision.'
            body['suggestions'][0]['action_id']=None
            return json.dumps(body)
        output=generate_suggestions(model,self.prompt,data,result,self.rules,'Validated',None)
        card=output['suggestions'][0]
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertIn('same-day facility',card['next_move'])
        self.assertIsNone(card['recommended_action'])
        self.assertTrue(any(t['name']=='assess_reserve_transfer' for t in output['analyst_audit']['tool_calls']))

    def test_friendly_voice_uses_existing_writer_without_an_extra_rewrite(self):
        body=self.response()
        body['suggestions'][0]['title']="Let's get ahead of the cash gap"
        body['suggestions'][0]['summary']="Our US dollar cash could fall below its safety cushion. Let's confirm the expected deposits before relying on them."
        calls=[]
        def model(prompt,content,tools=None,**kwargs):
            title=kwargs['response_schema']['title'];calls.append(title)
            if title=='AnalystOutput':
                self.assertIn('Writing voice: a helpful colleague',prompt)
                self.assertIn('two or three short everyday sentences',prompt)
                self.assertIn('Keep the real risks and conditions clear.',prompt)
                self.assertNotIn('Keep summaries to one short sentence',prompt)
                return json.dumps(body)
            return json.dumps(dict(valid=True,errors=[]))
        output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Python validation',None)
        self.assertEqual(calls,['AnalystOutput'])
        self.assertEqual(output['suggestions'][0]['summary'],body['suggestions'][0]['summary'])
        self.assertEqual(output['suggestions'][0]['severity'],'critical')
        self.assertEqual(output['analyst_audit']['validation_attempts'],[])

    def test_numbers_are_not_matched_against_catalog(self):
        body=self.response()
        body['suggestions'][0]['summary']='Arrange USD 9,876,543 before settlement.'
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertEqual(cards[0]['summary'],body['suggestions'][0]['summary'])

    def test_decision_value_guidance_reaches_existing_writer_without_a_new_gate(self):
        calls=[]
        def model(prompt,content,tools=None,**kwargs):
            title=kwargs['response_schema']['title'];calls.append(title)
            if title=='AnalystOutput':
                self.assertIn('Decision value: an opportunity is not a status update',prompt)
                self.assertIn('what to do, with how much, by when, and what benefit it provides',prompt)
                self.assertIn('Put routine status observations in the snapshot brief',prompt)
                self.assertIn('Important risk warnings can still be cards without a feasible trade.',prompt)
                self.assertIn('not an extra review stage, completeness gate or automatic rewrite',prompt)
                self.assertIn('Treasury reference rate',prompt)
                self.assertIn('Writing voice: a helpful colleague',prompt)
                return json.dumps(self.response())
            return json.dumps(dict(valid=True,errors=[]))
        output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(calls,['AnalystOutput'])
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertEqual(output['suggestions'][0]['severity'],'critical')
        self.assertIsNone(output['suggestions'][0]['recommended_action'])
        self.assertFalse(output['analyst_audit']['automatic_rewrites'])

    def test_selected_action_reaches_card_without_paid_review(self):
        body=self.response()
        data=copy.deepcopy(self.data)
        next(a for a in data['accounts'] if a['currency']=='GBP')['balance']=1000000000
        rules=copy.deepcopy(self.rules);rules['max_absolute_fx_exposure_pct']=100000
        result=evaluate(data,rules,datetime.fromisoformat(data['accounts'][0]['as_of']))
        calls=[]
        def model(prompt,content,tools=None,**kwargs):
            title=kwargs['response_schema']['title'];calls.append(title)
            if title=='AnalystOutput':
                plan=kwargs['tool_handlers']['assess_funding_plan']['handler'](source='GBP',obligation_id='PAY-002',
                    target_amount=1300000,settlement_date=self.catalog['analysis.forecast_start']['value'])
                self.assertTrue(plan['scenarios']['baseline']['passes_modeled_checks'])
                kwargs['trace'].append(dict(name='assess_funding_plan',result=plan))
                body['suggestions'][0].update(action_id=plan['plan_id'],expected_result='This could cover the dollar gap if the expected deposits arrive.')
                return json.dumps(body)
            payload=json.loads(content)
            action=payload['proposed']['suggestions'][0]['recommended_action']
            self.assertEqual(action['buy']['amount'],'1300000.00')
            self.assertTrue(set(action['fact_ids'])<=payload['catalog'].keys())
            self.assertIn('expected deposits',payload['proposed']['suggestions'][0]['expected_result'])
            return json.dumps(dict(valid=True,errors=[]))
        output=generate_suggestions(model,self.prompt,data,result,rules,'Validated',None)
        self.assertEqual(calls,['AnalystOutput'])
        self.assertIsNotNone(output['suggestions'][0]['recommended_action'])
        self.assertEqual(output['suggestions'][0]['severity'],'critical')
        self.assertEqual(output['validation_warnings'],[])

    def test_unknown_or_broken_action_keeps_other_suggestions_and_scopes_warning(self):
        for selected in ('unknown','broken'):
            body=self.response()
            body['suggestions'][0].update(action_id=selected,expected_result='This would cover the dollar gap.')
            def model(prompt,content,tools=None,**kwargs):
                if kwargs['response_schema']['title']=='AnalystOutput':
                    if selected=='broken':kwargs['trace'].append(dict(name='assess_funding_plan',result=dict(plan_id='broken')))
                    return json.dumps(body)
                return json.dumps(dict(valid=True,errors=[]))
            with self.subTest(selected=selected):
                output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Validated',None)
                card=output['suggestions'][0]
                self.assertIsNone(card['recommended_action'])
                self.assertIsNone(card['expected_result'])
                self.assertTrue(card['review_warnings'])
                self.assertEqual(output['analysis_review_warnings'],[])
                self.assertEqual(output['suggestion_origin'],'analyst')

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
        self.assertEqual(calls,['AnalystOutput'])
        self.assertEqual(output['validation_mode'],'narrative_warnings')
        self.assertEqual(output['validation_warnings'],[])
        self.assertFalse(output['analyst_audit']['numerical_wording_check'])
        self.assertEqual(output['suggestions'][0]['summary'],body['suggestions'][0]['summary'])
        self.assertEqual(output['validation_warnings'],output['analyst_audit']['validation_warnings'])

    def test_unknown_references_skip_only_the_affected_card(self):
        for field,value in [('fact_ids',['invented']),('finding_ids',['invented']),('rule_ids',['invented'])]:
            body=self.response()
            body['suggestions'][0][field]=value
            warnings=[]
            _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules,warnings)
            self.assertEqual(cards,[])
            self.assertTrue(any('Skipped' in warning for warning in warnings))

    def test_unknown_fact_ids_do_not_publish_a_fabricated_card_or_retry(self):
        body=self.response()
        body['suggestions'][0]['fact_ids']=['USD.buffer_gap','invented.fact','also.invented']
        warnings=[]
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules,warnings)
        self.assertEqual(cards,[])
        self.assertIn('also.invented, invented.fact',warnings[0])
        calls=[]
        def model(prompt,content,tools=None,**kwargs):
            calls.append(kwargs['response_schema']['title'])
            payload=json.loads(content)
            if kwargs['response_schema']['title']=='AnalystOutput':
                self.assertNotIn('citation_fact_ids',payload)
                self.assertIn('USD.buffer_gap',payload['evidence_catalog'])
                self.assertIn('Copy citation IDs verbatim',prompt)
                return json.dumps(body)
            return json.dumps({'valid':True,'errors':[]})
        output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(calls,['AnalystOutput'])
        self.assertEqual(output['suggestion_origin'],'unavailable')
        self.assertEqual(output['suggestions'],[])
        self.assertIn('also.invented, invented.fact',output['analyst_audit']['validation_warnings'][0])
        self.assertEqual(output['analyst_audit']['validation_attempts'][0]['error'],'No verifiable analyst cards remained after evidence checks')

    def test_critical_omission_and_downgrade_warn_instead_of_rejecting(self):
        for mode in ('omit','downgrade'):
            body=self.response()
            if mode=='omit':body['suggestions']=[]
            else:body['suggestions'][0]['severity']='info'
            warnings=[]
            _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules,warnings)
            self.assertTrue(cards or warnings)
            if mode=='omit':self.assertIn('critical finding',warnings[0])
            else:self.assertIn('critical calculated finding',cards[0]['validation_warnings'][0])

    def test_copied_template_warns(self):
        body=self.response()
        body['suggestions'][0]['summary']=self.result['findings'][0]['summary']
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules)
        self.assertTrue(any('repeats' in w for w in cards[0]['validation_warnings']))

    def test_model_writer_never_receives_engine_suggestion_templates(self):
        def writer(prompt,content,tools=None,**kwargs):
            schema=kwargs.get('response_schema',{}).get('title')
            if schema=='EvidenceReview':return json.dumps({'valid':True,'errors':[]})
            if schema=='AnalystOutput':
                topic_schema=kwargs['response_schema']['$defs']['Card']['properties']['topic']
                self.assertNotIn('enum',topic_schema)
            self.assertEqual(schema,'AnalystOutput')
            self.assertNotIn('CURRENT STAGE:',prompt)
            self.assertEqual(kwargs['required_tools'],set())
            self.assertEqual(json.loads(content)['workbooks'],compact(self.data))
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

    def test_duplicate_cards_skipped_and_more_than_five_rejected(self):
        body=self.response();body['suggestions']*=2
        warnings=[]
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,self.rules,warnings)
        self.assertEqual(len(cards),1)
        self.assertTrue(any('duplicate' in w for w in warnings))
        body['suggestions']*=3
        with self.assertRaises(ValueError):
            validate_output(json.dumps(body),self.catalog,self.result,self.rules)

    def test_demo_card_limit_warns_without_blocking(self):
        body=self.response()
        body['suggestions'].append(dict(topic='receipt_risk',currency='USD',severity='warning',title='Confirm receipt timing',
            summary='Check whether expected deposits will settle before the dollar obligations.',
            detail='A delayed deposit would change the forecast; verify its arrival with Treasury.',
            assumptions=[],finding_ids=[],fact_ids=['USD.projected_low'],rule_ids=[]))
        rules=copy.deepcopy(self.rules);rules['max_suggestions']=1
        warnings=[]
        _,cards=validate_output(json.dumps(body),self.catalog,self.result,rules,warnings)
        self.assertEqual(len(cards),2)
        self.assertTrue(any('demo policy' in warning for warning in warnings))

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

    def test_open_ended_topics_without_workbook_evidence_are_skipped(self):
        body=self.response()
        body['suggestions'][0]['topic']='receipt_confirmation_process'
        body['suggestions'][0]['fact_ids']=[]
        with self.assertRaises(ValueError):
            validate_output(json.dumps(body),self.catalog,self.result,self.rules)

    def test_saved_usd_drop_severity_mismatch_keeps_cad_card_with_warning(self):
        body=self.response()
        body['suggestions'].append(dict(topic='cad_buffer_gap_solved_by_jmd_conversion',currency='CAD',severity='info',
            title='Explore JMD funding for CAD',summary='A hypothetical conversion could cover the Canadian-dollar gap.',
            detail='Treasury should review available JMD and the settlement date before relying on this scenario.',
            assumptions=[],finding_ids=['liquidity-CAD'],fact_ids=['CAD.buffer_gap','CAD.projected_low'],rule_ids=['minimum_available']))
        data=copy.deepcopy(self.data)
        next(a for a in data['accounts'] if a['currency']=='USD')['balance']=400000
        result=evaluate(data,self.rules,datetime.fromisoformat(data['accounts'][0]['as_of']))
        # The saved failed run also contained a separate critical CAD finding.
        result['findings'].append(dict(id='liquidity-CAD',severity='critical',currency='CAD',
            rule='minimum_available',summary='Arrange CAD funding to maintain the buffer.',
            sources=['Bank_Balances.xlsx / Accounts / row 5']))
        catalog=build_evidence(data,result,self.rules)
        def model(*args,**kwargs):
            return json.dumps(body if kwargs['response_schema']['title']=='AnalystOutput' else {'valid':True,'errors':[]})
        output=generate_suggestions(model,self.prompt,data,result,self.rules,'Validated',None)
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertEqual(len(output['suggestions']),2,output['validation_warnings'])
        cad=next(c for c in output['suggestions'] if c['currency']=='CAD')
        self.assertEqual(cad['severity'],'info')
        self.assertTrue(any('critical calculated finding' in w for w in cad['review_warnings']))
        self.assertEqual(output['analyst_audit']['validation_attempts'],[])

    def test_invalid_card_is_skipped_without_losing_valid_card(self):
        body=self.response()
        bad=copy.deepcopy(body['suggestions'][0]);bad['topic']='unsupported';bad['fact_ids']=['invented']
        body['suggestions'].append(bad)
        def model(*args,**kwargs):
            return json.dumps(body if kwargs['response_schema']['title']=='AnalystOutput' else {'valid':True,'errors':[]})
        output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(len(output['suggestions']),1)
        self.assertEqual(output['suggestions'][0]['topic'],'funding')
        self.assertTrue(any('Skipped unsupported' in w for w in output['analysis_review_warnings']))

    def test_outage_never_substitutes_calculated_cards(self):
        def unavailable(*args,**kwargs):raise RuntimeError('Service unavailable')
        output=generate_suggestions(unavailable,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(output['suggestions'],[])
        self.assertEqual(output['suggestion_origin'],'unavailable')

    def test_invalid_output_is_reported_once_without_a_paid_rewrite(self):
        calls=[]
        def model(*args,**kwargs):
            schema=kwargs.get('response_schema',{}).get('title')
            calls.append(schema)
            return '{}'
        output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(output['suggestion_origin'],'unavailable')
        self.assertEqual(calls,['AnalystOutput'])
        self.assertEqual(len(output['analyst_audit']['validation_attempts']),1)
        self.assertFalse(output['analyst_audit']['automatic_rewrites'])

    def test_writer_receives_routed_research_without_paid_review(self):
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
        self.assertEqual(calls,['AnalystOutput'])
        self.assertEqual(output['analyst_audit']['evidence_review']['status'],'disabled_for_development')
        self.assertNotIn('investigation',output['analyst_audit'])
        self.assertEqual(output['analyst_audit']['workflow_version'],'compact-routing-3')

    def test_independent_review_is_not_requested_during_development(self):
        def wrong(*args,**kwargs):
            schema=kwargs.get('response_schema',{}).get('title')
            if schema=='AnalystOutput':return json.dumps(self.response())
            if schema=='EvidenceReview':return json.dumps({'valid':False,'errors':[dict(message='Reserve balance has been confused with reserve shortfall.',suggestion_ids=['funding-USD'],affects_overview=False)]})
            return 'Investigation record.'
        output=generate_suggestions(wrong,self.prompt,self.data,self.result,self.rules,'Validated',None)
        self.assertEqual(output['suggestions'][0]['summary'],self.response()['suggestions'][0]['summary'])
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertNotIn('Reserve balance',json.dumps(output))
        self.assertEqual(output['analyst_audit']['evidence_review']['status'],'disabled_for_development')
        self.assertEqual(output['analyst_audit']['validation_attempts'],[])
        self.assertEqual(output['suggestions'][0]['review_warnings'],[])
        self.assertEqual(output['analysis_review_warnings'],[])

    def test_review_warnings_attach_only_to_explicitly_referenced_cards(self):
        cards=[dict(id=id) for id in ('funding-USD','exposure-portfolio','liquidity-portfolio','rate-GBP')]
        review=EvidenceReview.model_validate(dict(valid=False,errors=[
            dict(message='The conversion exceeds the demo exposure cap.',suggestion_ids=['funding-USD','exposure-portfolio'],affects_overview=False),
            dict(message='Ratios do not resolve a currency shortage.',suggestion_ids=['liquidity-portfolio'],affects_overview=True),
            dict(message='The overview overstates the tested solution.',suggestion_ids=[],affects_overview=True)]))
        all_warnings,overview=attach_review_warnings(cards,review,['Planning unavailable.'])
        self.assertEqual(len(all_warnings),4)
        self.assertEqual(cards[0]['review_warnings'],cards[1]['review_warnings'])
        self.assertEqual(len(cards[2]['review_warnings']),1)
        self.assertEqual(cards[3]['review_warnings'],[])
        self.assertEqual(len(overview),3)
        self.assertNotIn('Factual review: The conversion exceeds the demo exposure cap.',overview)

    def test_unknown_warning_reference_is_retained_without_a_publishing_gate(self):
        cards=[dict(id='funding-USD')]
        review=EvidenceReview.model_validate(dict(valid=False,errors=[
            dict(message='The claim needs review.',suggestion_ids=['unknown-card'],affects_overview=False)]))
        all_warnings,overview=attach_review_warnings(cards,review,[])
        self.assertEqual(all_warnings,overview)
        self.assertEqual(len(overview),1)
        self.assertEqual(cards[0]['review_warnings'],[])

    def test_no_review_request_even_when_review_service_would_fail(self):
        for failure in ('invalid json',RuntimeError('private provider body')):
            def model(*args,**kwargs):
                if kwargs['response_schema']['title']=='AnalystOutput':return json.dumps(self.response())
                if isinstance(failure,Exception):raise failure
                return failure
            with self.subTest(failure=type(failure).__name__):
                output=generate_suggestions(model,self.prompt,self.data,self.result,self.rules,'Validated',None)
            self.assertEqual(output['suggestion_origin'],'analyst')
            self.assertEqual(output['validation_warnings'],[])
            self.assertEqual(output['analysis_review_warnings'],[])
            self.assertEqual(output['suggestions'][0]['review_warnings'],[])
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
