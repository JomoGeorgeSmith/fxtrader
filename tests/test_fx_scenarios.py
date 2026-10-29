import copy
import json
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch,Mock,MagicMock

from openpyxl import load_workbook

from backend import app
from backend.calculations import evaluate
from backend.analyst import build_evidence,generate_suggestions
from backend.funding_plan import assess_funding_plan
from backend.fx_market import settlement_check,business_day,source_capacity
from backend.fx_scenarios import assess_fx_trade,assess_allocation_plan,assess_receipt_match,assess_round_trip,assess_forward_hedge,scenario_tools
from backend.recommended_action import recommended_action
from backend.seed import ROOT,seed
from backend.simulation_inputs import TABLES,SIMULATION_FILE,read_simulation,validate_links
from backend.synthesize_fx import apply_missing,digest_file
from backend.model_context import scenario_summary


class FXScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace=tempfile.TemporaryDirectory();cls.folder=Path(cls.workspace.name)
        with patch('backend.seed.SHARED',cls.folder):seed()
        cls.now=datetime.fromisoformat(app.read_context(cls.folder)['accounts'][0]['as_of'])
        cls.manifest=apply_missing(cls.folder,cls.folder/'runtime',cls.now.date())
        cls.original=app.read_context(cls.folder)
        cls.original_rules=json.loads((ROOT/'policies/demo-rules.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):cls.workspace.cleanup()

    def setUp(self):
        self.network=patch('backend.app.urllib.request.urlopen',side_effect=AssertionError('Offline tests only'));self.network.start();self.addCleanup(self.network.stop)
        self.data=copy.deepcopy(self.original);self.rules=copy.deepcopy(self.original_rules);self.catalog={}
        for a in self.data['accounts']:a['balance']=1000000000
        for p in self.data['fx_permissions']:p['daily_debit_limit']=1000000000
        for r in self.data['fx_limits']:r['max_absolute_exposure']=1000000000
        self.rules['max_absolute_fx_exposure_pct']=10000

    def day(self,n):return (self.now+timedelta(days=n)).date().isoformat()

    def business(self,n):
        while not business_day(self.data,self.day(n),('JMD','USD','GBP','CAD')):n+=1
        return self.day(n)

    def result(self):return evaluate(self.data,self.rules,self.now)

    def trade(self,**args):
        params=dict(source='GBP',target='USD',target_amount=1000,settlement_date=self.business(5),booking_hour=12,expected_receipt_delay_days=3);params.update(args)
        return assess_fx_trade(self.data,self.result(),self.rules,self.catalog,**params)

    def allocation_fixture(self):
        next(a for a in self.data['accounts'] if a['currency']=='USD')['balance']=5000000
        next(r for r in self.data['risk'] if r['currency']=='USD')['net_fx_exposure']=6200000
        next(r for r in self.data['fx_limits'] if r['currency']=='USD')['max_absolute_exposure']=5031446.54
        for id,currency,amount,day in [('TEST-CAD','CAD',1100000,20),('TEST-JMD','JMD',60000000,21)]:
            self.data['payments'].append(dict(id=id,currency=currency,amount=amount,value_date=self.day(day),status='Committed',_source='test'))
        return [dict(target='CAD',target_amount=1100000,settlement_date=self.business(9),obligation_id='TEST-CAD'),
                dict(target='JMD',target_amount=60000000,settlement_date=self.business(9),obligation_id='TEST-JMD')]

    def allocation(self,**args):
        legs=self.allocation_fixture()
        params=dict(source='USD',legs=legs,booking_hour=12,expected_receipt_delay_days=3);params.update(args)
        return assess_allocation_plan(self.data,self.result(),self.rules,self.catalog,**params)

    def hedge(self,**args):
        p=next(p for p in self.data['payments'] if p['id']=='PAY-003');p['value_date']=self.day(20)
        params=dict(source='GBP',obligation_id=p['id'],target_amount=1000,settlement_date=self.business(10),counterparty_id='SIM-FX-DEALER-01');params.update(args)
        return assess_forward_hedge(self.data,self.result(),self.rules,self.catalog,**params)

    def receipt(self,**args):
        p=next(p for p in self.data['payments'] if p['id']=='PAY-003');p['value_date']=self.day(20)
        order=next(o for o in self.data['fx_orders'] if o['obligation_id']==p['id']);order['settlement_date']=self.business(8)
        params=dict(receipt_id='REC-001',obligation_id=p['id'],amount=10000,purchase_order_id=order['id']);params.update(args)
        return assess_receipt_match(self.data,self.result(),self.rules,self.catalog,**params)

    def test_extension_backs_up_and_preserves_all_existing_workbooks(self):
        m=self.manifest;self.assertTrue(m['preserved_base_files'])
        for name,sha in m['original_hashes'].items():
            self.assertEqual(digest_file(Path(m['backup'])/name),sha)
            self.assertEqual(digest_file(self.folder/name),sha)
        self.assertEqual(len(m['added_sheets']),len(TABLES))
        self.assertTrue(all(r['synthetic'] for key in TABLES for r in self.original[key]))

    def test_second_apply_is_noop_preserving_user_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            with patch('backend.seed.SHARED',folder):seed()
            apply_missing(folder,folder/'runtime',self.now.date())
            p=folder/SIMULATION_FILE;wb=load_workbook(p);wb['Currency_Terms']['E2']=.07123;wb.save(p);wb.close()
            before=digest_file(p);self.assertTrue(apply_missing(folder,folder/'runtime')['unchanged']);self.assertEqual(digest_file(p),before)

    def test_old_snapshots_without_extension_remain_readable(self):
        self.assertTrue(all(not v for v in read_simulation(self.folder/'not-created',self.data).values()))
        old={k:v for k,v in self.data.items() if k not in TABLES};self.assertEqual(scenario_tools(old,self.result(),self.rules,{}),{})

    def test_input_links_reject_wrong_currency_and_duplicate_cash_legs(self):
        self.data['fx_permissions'][0]['currency']='USD'
        with self.assertRaises(ValueError):validate_links(self.data)

    def test_confirmation_does_not_promote_an_expected_receipt(self):
        self.data['receipts'][0]['status']='Expected'
        with self.assertRaisesRegex(ValueError,'Expected receipts'):validate_links(self.data)

    def test_receipt_cannot_be_allocated_twice_over_its_amount(self):
        self.data['fx_allocations']=[dict(id='X',currency='USD',receipt_id='REC-001',obligation_id='PAY-003',amount=300000)]
        with self.assertRaisesRegex(ValueError,'allocations exceed'):validate_links(self.data)

    def test_current_positions_and_aggregate_limits_are_reused(self):
        catalog=build_evidence(self.data,self.result(),self.rules)
        self.assertEqual(catalog['USD.exposure.current']['value'],self.data['risk'][0]['net_fx_exposure'])
        self.assertIn('USD.exposure.limit',catalog);self.assertEqual(catalog['exposure.limit']['value'],self.rules['max_absolute_fx_exposure_pct'])

    def test_planned_orders_do_not_change_baseline_forecasts(self):
        before=self.result();self.data['fx_orders']=[];after=self.result();self.assertEqual(before['forecast'],after['forecast'])

    def test_booked_order_adds_cash_once(self):
        order=self.data['fx_orders'][0];before=self.result();order['status']='Booked';after=self.result()
        lookup={(r['currency'],r['date']):r['balance'] for r in before['forecast']}
        row=next(r for r in after['forecast'] if r['currency']==order['target'] and r['date']==order['settlement_date'])
        self.assertAlmostEqual(row['balance']-lookup[row['currency'],row['date']],order['target_amount'])

    def test_exact_linked_order_not_double_counted(self):
        order=self.data['fx_orders'][0];order['status']='Booked';day=order['settlement_date']
        baseline=self.result();order.update(cash_in_forecast=True,payment_id='LEG-P',receipt_id='LEG-R')
        self.data['payments'].append(dict(id='LEG-P',currency=order['source'],amount=order['source_amount'],value_date=day,status='Committed',_source='test'))
        self.data['receipts'].append(dict(id='LEG-R',currency=order['target'],amount=order['target_amount'],value_date=day,status='Confirmed',_source='test'))
        validate_links(self.data);self.assertEqual(baseline['forecast'],self.result()['forecast'])
        second=copy.deepcopy(order);second['id']='duplicate-legs';self.data['fx_orders'].append(second)
        with self.assertRaisesRegex(ValueError,'multiple trades'):validate_links(self.data)

    def test_trade_uses_correct_sheet_sides_and_explicit_fees(self):
        r=self.trade();self.assertEqual(r['source_principal'],786.43);self.assertGreater(r['fee_jmd'],1000)
        self.assertTrue(r['passes_modeled_checks']);self.assertTrue(all(f['id'] in self.catalog for f in r['facts']))

    def test_trade_rejects_bad_amount_currency_and_stress(self):
        for args in (dict(target_amount=-1),dict(target_amount=float('nan')),dict(target_amount=1.001),dict(target='GBP'),dict(expected_receipt_delay_days=0)):
            with self.subTest(args=args),self.assertRaises(ValueError):self.trade(**args)

    def test_combined_sale_allocates_proceeds_to_payments_and_preserves_stressed_cash(self):
        r=self.allocation()
        self.assertTrue(r['passes_modeled_checks'],r['checks'])
        self.assertEqual([leg['obligation_id'] for leg in r['legs']],['TEST-CAD','TEST-JMD'])
        self.assertAlmostEqual(sum(leg['source_principal'] for leg in r['legs']),r['total_sold'])
        self.assertLess(r['risk']['currency_exposures']['USD'],5031446.54)
        self.assertGreaterEqual(r['scenarios']['without_expected_receipts']['lows']['USD'],self.rules['minimum_available']['USD'])
        action,warning=recommended_action(r['scenario_id'],[dict(name='assess_allocation_plan',result=r)],self.data)
        self.assertIsNone(warning);self.assertEqual(action['kind'],'allocation_plan')
        self.assertIn('TEST-CAD',action['display_rows'][1]['value'])
        self.assertIn('TEST-JMD',action['display_rows'][2]['value'])

    def test_combined_sale_is_not_recommended_when_source_cash_is_needed(self):
        r=self.allocation()
        next(a for a in self.data['accounts'] if a['currency']=='USD')['balance']=3000000
        r=assess_allocation_plan(self.data,self.result(),self.rules,self.catalog,'USD',[
            dict(target=leg['target'],target_amount=leg['target_amount'],settlement_date=leg['settlement_date'],obligation_id=leg['obligation_id']) for leg in r['legs']])
        self.assertFalse(r['passes_modeled_checks'])
        self.assertFalse(r['scenarios']['without_expected_receipts']['cash_cushions_preserved'])
        self.assertIsNone(recommended_action(r['scenario_id'],[dict(name='assess_allocation_plan',result=r)],self.data)[0])

    def test_combined_sale_checks_all_currencies_and_joint_daily_limits(self):
        r=self.allocation();legs=[dict(target=leg['target'],target_amount=leg['target_amount'],settlement_date=leg['settlement_date'],obligation_id=leg['obligation_id']) for leg in r['legs']]
        next(p for p in self.data['fx_permissions'] if p['currency']=='USD')['daily_debit_limit']=r['total_sold']-1
        failed=assess_allocation_plan(self.data,self.result(),self.rules,self.catalog,'USD',legs)
        self.assertFalse(failed['checks']['source_account_capacity']);self.assertFalse(failed['passes_modeled_checks'])
        next(p for p in self.data['fx_permissions'] if p['currency']=='USD')['daily_debit_limit']=1000000000
        next(p for p in self.data['payments'] if p['id']=='PAY-004')['amount']=1000000001
        failed=assess_allocation_plan(self.data,self.result(),self.rules,self.catalog,'USD',legs)
        self.assertFalse(failed['scenarios']['baseline']['cash_cushions_preserved'])

    def test_combined_sale_must_really_resolve_currency_exposure_limit(self):
        legs=self.allocation_fixture()
        legs[0]['target_amount']=1000;legs[1]['target_amount']=1000
        r=assess_allocation_plan(self.data,self.result(),self.rules,self.catalog,'USD',legs)
        self.assertFalse(r['checks']['exposure_preserved'])
        self.assertFalse(r['passes_modeled_checks'])
        self.assertIsNone(recommended_action(r['scenario_id'],[dict(name='assess_allocation_plan',result=r)],self.data)[0])

    def test_combined_sale_rejects_invented_or_duplicate_payment_cover(self):
        legs=self.allocation_fixture()
        with self.assertRaises(ValueError):assess_allocation_plan(self.data,self.result(),self.rules,self.catalog,'USD',[dict(legs[0],obligation_id='NO-PAYMENT'),legs[1]])
        with self.assertRaises(ValueError):assess_allocation_plan(self.data,self.result(),self.rules,self.catalog,'USD',[dict(legs[0],target_amount=1100001),legs[1]])
        with self.assertRaises(ValueError):assess_allocation_plan(self.data,self.result(),self.rules,self.catalog,'USD',[legs[0],dict(legs[1],target='CAD')])
        with self.assertRaises(ValueError):assess_allocation_plan(self.data,self.result(),self.rules,self.catalog,'USD',[legs[0],legs[1]],expected_receipt_delay_days=0)
        self.data['fx_orders'].append(dict(id='PLANNED-CAD',status='Planned',obligation_id='TEST-CAD',_source='test'))
        self.assertFalse(assess_allocation_plan(self.data,self.result(),self.rules,self.catalog,'USD',legs)['checks']['no_duplicate_planned_cover'])

    def test_currency_limit_breach_is_not_hidden_by_aggregate_headroom(self):
        next(r for r in self.data['fx_limits'] if r['currency']=='USD')['max_absolute_exposure']=1
        r=self.trade();self.assertFalse(r['checks']['exposure_preserved']);self.assertFalse(r['passes_modeled_checks'])

    def test_account_permission_and_daily_limit_are_enforced(self):
        p=next(r for r in self.data['fx_permissions'] if r['currency']=='GBP');p['fx_enabled']=False
        self.assertFalse(self.trade()['passes_modeled_checks']);p['fx_enabled']=True;p['daily_debit_limit']=1
        self.assertFalse(self.trade()['passes_modeled_checks'])

    def test_fee_account_permission_is_enforced(self):
        next(r for r in self.data['fx_permissions'] if r['currency']=='JMD')['fx_enabled']=False
        self.assertFalse(self.trade()['passes_modeled_checks'])

    def test_settlement_lead_time_cutoff_and_holiday_are_enforced(self):
        first=self.business(0);base=settlement_check(self.data,'GBP','USD',self.day(20),first,12)
        late=settlement_check(self.data,'GBP','USD',self.day(20),first,18)
        self.assertGreater(late['earliest_simulated_arrival'],base['earliest_simulated_arrival'])
        self.data['fx_holidays'].append(dict(currency='USD',value_date=base['earliest_simulated_arrival'],_source='test'))
        closed=settlement_check(self.data,'GBP','USD',base['earliest_simulated_arrival'],first)
        self.assertFalse(closed['arrival_is_business_day']);self.assertGreater(closed['earliest_simulated_arrival'],base['earliest_simulated_arrival'])
        self.assertFalse(self.trade(settlement_date=first)['passes_modeled_checks'])

    def test_new_funding_checks_apply_to_existing_required_tool(self):
        r=assess_funding_plan(self.data,self.result(),self.rules,self.catalog,'GBP','PAY-003',1000,self.business(0))
        self.assertFalse(r['simulation_checks']['simulated_settlement_ready']);self.assertFalse(r['scenarios']['baseline']['passes_modeled_checks'])
        self.assertIn('simulation_fee_jmd',{f['id'].split('.')[-1] for f in r['facts']})

    def test_receipt_match_does_not_add_existing_receipt_again(self):
        r=self.receipt();self.assertTrue(r['receipt_amount_not_added_again']);self.assertTrue(r['passes_modeled_checks'])
        order=next(o for o in self.data['fx_orders'] if o['id']==r['purchase_order_id'])
        day=self.day(20);base=next(f['balance'] for f in self.result()['forecast'] if f['currency']=='USD' and f['date']==day)
        row=next(f for f in r['scenarios']['baseline']['balances'] if f['date']==day)
        self.assertAlmostEqual(row['USD'],base+order['target_amount']-10000)
        self.assertIn('without_matched_receipt',r['scenarios'])

    def test_expected_receipt_not_a_confirmed_purchase_reduction(self):
        self.data['receipts'][0]['status']='Expected';self.data['fx_receipt_timing'][0]['confirmation_reference']=''
        self.assertFalse(self.receipt()['passes_modeled_checks'])

    def test_receipt_matching_requires_actual_linked_planned_purchase(self):
        with self.assertRaises(ValueError):self.receipt(purchase_order_id='SIM-FX-ORDER-01')
        with self.assertRaises(ValueError):self.receipt(amount=250001)

    def test_same_day_receipt_is_not_verified_payment_readiness(self):
        self.data['receipts'][0]['value_date']=self.day(20)
        self.assertFalse(self.receipt()['checks']['receipt_arrives_before_payment'])

    def test_cancellation_round_trip_values_spread_fees_and_penalty(self):
        a,b=self.data['fx_orders'][:2];r=assess_round_trip(self.data,self.result(),self.rules,self.catalog,a['id'],b['id'])
        self.assertTrue(r['passes_modeled_checks']);self.assertGreater(float(next(f['value'] for f in r['facts'] if f['id'].endswith('net_cost_avoided_jmd'))),0)
        b['cancellation_fee_jmd']=100000000
        self.assertFalse(assess_round_trip(self.data,self.result(),self.rules,self.catalog,a['id'],b['id'])['passes_modeled_checks'])

    def test_round_trip_rejects_obligation_links_partial_amounts_and_no_permission(self):
        a,b=self.data['fx_orders'][:2];a['cancellable']=False
        self.assertFalse(assess_round_trip(self.data,self.result(),self.rules,self.catalog,a['id'],b['id'])['passes_modeled_checks'])
        b['source_amount']+=1
        with self.assertRaises(ValueError):assess_round_trip(self.data,self.result(),self.rules,self.catalog,a['id'],b['id'])

    def test_forward_is_not_a_spot_quote_or_a_market_prediction(self):
        r=self.hedge();self.assertTrue(r['passes_modeled_checks']);self.assertEqual(len(r['spot_sensitivity']),3)
        self.assertNotEqual(r['simulated_forward_rate'],str(202.18/159))
        self.assertIn('NOT',scenario_tools(self.data,self.result(),self.rules,{})['assess_forward_hedge']['description'])

    def test_forward_collateral_is_reserved_then_released(self):
        r=self.hedge();values={f['id'].split('.')[-1]:f['value'] for f in r['facts']};base={(x['currency'],x['date']):x['balance'] for x in self.result()['forecast']}
        first=r['scenarios']['baseline']['balances'][0];last=r['scenarios']['baseline']['balances'][-1]
        self.assertAlmostEqual(first['JMD'],base['JMD',first['date']]-float(values['collateral_jmd'])-float(values['fee_jmd']))
        self.assertAlmostEqual(last['JMD'],base['JMD',last['date']]-float(values['fee_jmd']))

    def test_forward_policy_counterparty_and_collateral_failures(self):
        cp=self.data['fx_counterparties'][0];cp['approved']=False
        self.assertFalse(self.hedge()['passes_modeled_checks']);cp['approved']=True;cp['forward_limit_jmd']=1
        self.assertFalse(self.hedge()['passes_modeled_checks']);cp['forward_limit_jmd']=1e12
        next(p for p in self.data['fx_permissions'] if p['currency']=='JMD')['daily_debit_limit']=1
        self.assertFalse(self.hedge()['passes_modeled_checks'])

    def test_forward_overhedging_existing_cover_and_horizon(self):
        self.assertFalse(self.hedge(target_amount=500000)['checks']['amount_within_hedge_policy'])
        with self.assertRaises(ValueError):self.hedge(settlement_date=self.day(400))
        self.assertFalse(self.hedge(settlement_date=self.day(20))['checks']['arrives_before_payment'])

    def existing_hedge(self):
        row=dict(id='SIM-FX-EXISTING',currency='USD',synthetic=True,description='Synthetic existing hedge test',obligation_id='PAY-003',
            source='GBP',target='USD',source_amount=1000,target_amount=1250,settlement_date=self.business(9),status='Active',
            counterparty_id='SIM-FX-DEALER-01',collateral_jmd=10000,collateral_release_receipt_id='',cash_in_forecast=False,payment_id='',receipt_id='',fee_jmd=100,fee_payment_id='',_source='test')
        self.data['fx_hedges'].append(row)
        return row

    def test_existing_hedge_collateral_release_counted_exactly_once(self):
        before=self.result();hedge=self.existing_hedge();validate_links(self.data);after=self.result()
        day=hedge['settlement_date'];base=next(r['balance'] for r in before['forecast'] if r['date']==day and r['currency']=='JMD')
        value=next(r['balance'] for r in after['forecast'] if r['date']==day and r['currency']=='JMD')
        self.assertEqual(value,base+9900)
        hedge['collateral_release_receipt_id']='RELEASE'
        self.data['receipts'].append(dict(id='RELEASE',currency='JMD',amount=10000,value_date=day,status='Confirmed',_source='test'))
        validate_links(self.data);self.assertEqual(after['forecast'],self.result()['forecast'])

    def test_existing_hedge_coverage_consumes_policy_and_counterparty_headroom(self):
        self.existing_hedge()
        cp=self.data['fx_counterparties'][0];cp['forward_limit_jmd']=200000
        self.assertFalse(self.hedge()['checks']['counterparty_capacity'])
        cp['forward_limit_jmd']=1e12
        policy=next(r for r in self.data['fx_hedge_policy'] if r['currency']=='USD');policy['max_hedge_pct']=.3
        self.assertFalse(self.hedge()['checks']['amount_within_hedge_policy'])

    def test_existing_collateral_cannot_exceed_recorded_jmd_holds(self):
        self.existing_hedge()['collateral_jmd']=1e12
        with self.assertRaisesRegex(ValueError,'collateral'):validate_links(self.data)

    def test_same_day_booked_commitments_reduce_account_fx_capacity(self):
        order=self.data['fx_orders'][0];order['status']='Booked'
        p=next(p for p in self.data['fx_permissions'] if p['currency']==order['source']);p['daily_debit_limit']=order['source_amount']+1
        self.assertEqual(float(source_capacity(self.data,order['source'],order['settlement_date'])),1)
        self.assertFalse(self.trade(settlement_date=order['settlement_date'])['checks']['account_permission_and_debit_limit'])

    def test_selected_confirmed_receipt_is_removed_in_explicit_absence_stress(self):
        r=self.receipt();day=self.day(20)
        base=next(row for row in r['scenarios']['baseline']['balances'] if row['date']==day)
        absent=next(row for row in r['scenarios']['without_matched_receipt']['balances'] if row['date']==day)
        self.assertEqual(base['USD']-absent['USD'],250000)

    def test_passing_scenario_supplies_action_rows_but_failed_does_not(self):
        r=self.trade();trace=[dict(name='assess_fx_trade',result=r)]
        action,warning=recommended_action(r['scenario_id'],trace,self.data)
        self.assertIsNone(warning);self.assertEqual(action['kind'],'conversion');self.assertEqual(action['display_rows'][0]['value'],'USD 1,000.00')
        r['passes_modeled_checks']=False
        self.assertIsNone(recommended_action(r['scenario_id'],trace,self.data)[0])

    def test_new_tools_available_in_writer_without_forced_investigations(self):
        result=self.result();result['findings']=[]
        calls=[]
        def model(prompt,content,tools=None,**kwargs):
            title=kwargs['response_schema']['title'];calls.append(title)
            if title=='EvidenceReview':return json.dumps(dict(valid=True,errors=[]))
            self.assertTrue(set(scenario_tools(self.data,result,self.rules,{}))<=kwargs['tool_handlers'].keys())
            return json.dumps(dict(brief='No supported additional action is needed in this fixture.',suggestions=[]))
        output=generate_suggestions(model,(ROOT/'backend/prompts/fx_analyst.md').read_text(encoding='utf-8'),self.data,result,self.rules,'Validated',None)
        self.assertEqual(calls,['AnalystOutput']);self.assertEqual(output['suggestion_origin'],'analyst')

    def test_selected_new_action_reaches_card_without_independent_reviewer(self):
        result=self.result();result['findings']=[];calls=[]
        def model(prompt,content,tools=None,**kwargs):
            title=kwargs['response_schema']['title'];calls.append(title)
            if title=='EvidenceReview':
                payload=json.loads(content);self.assertEqual(payload['proposed']['suggestions'][0]['recommended_action']['kind'],'conversion')
                self.assertEqual(payload['scenario_results'][0]['kind'],'conversion')
                return json.dumps(dict(valid=True,errors=[]))
            scenario=kwargs['tool_handlers']['assess_fx_trade']['handler'](source='GBP',target='USD',target_amount=1000,settlement_date=self.business(5),booking_hour=12,expected_receipt_delay_days=3)
            kwargs['trace'].append(dict(name='assess_fx_trade',arguments={},result=scenario))
            return json.dumps(dict(brief='One small, independently tested option is available.',suggestions=[dict(topic='test_option',currency='USD',severity='info',title='Consider the small purchase',summary='This is a hypothetical cash purchase, not a completed trade.',detail='The test preserves the modeled cash cushions but does not establish profit or real bank delivery.',assumptions=[],finding_ids=[],fact_ids=[scenario['facts'][0]['id']],rule_ids=[],action_id=scenario['scenario_id'],expected_result='This would provide the tested dollars under the simulation assumptions.')]))
        out=generate_suggestions(model,(ROOT/'backend/prompts/fx_analyst.md').read_text(encoding='utf-8'),self.data,result,self.rules,'Validated',None)
        self.assertEqual(calls,['AnalystOutput']);self.assertEqual(out['suggestions'][0]['recommended_action']['kind'],'conversion')

    def test_writer_can_choose_one_full_allocation_card_without_forcing_a_trade(self):
        legs=self.allocation_fixture();result=self.result();result['findings']=[];reviewed=[]
        def model(prompt,content,tools=None,**kwargs):
            if kwargs['response_schema']['title']=='EvidenceReview':
                payload=json.loads(content);reviewed.append(payload['scenario_results'][0]['kind'])
                self.assertEqual(payload['proposed']['suggestions'][0]['recommended_action']['kind'],'allocation_plan')
                return json.dumps(dict(valid=True,errors=[]))
            scenario=kwargs['tool_handlers']['assess_allocation_plan']['handler'](source='USD',legs=legs,booking_hour=12,expected_receipt_delay_days=3)
            kwargs['trace'].append(dict(name='assess_allocation_plan',arguments=dict(source='USD',legs=legs),result=scenario))
            return json.dumps(dict(brief='The two-payment allocation is an optional, jointly tested synthetic plan.',suggestions=[dict(
                topic='reduce_oversized_position',currency='USD',severity='warning',title='Reduce our oversized USD position',
                summary='Sell excess USD into CAD and JMD for upcoming payments. Both allocations preserve modeled cash and exposure limits.',
                detail='The source position starts above its synthetic limit; live prices and delivery still need confirmation before trading.',
                assumptions=[],finding_ids=[],fact_ids=[scenario['facts'][0]['id'],scenario['facts'][-2]['id']],rule_ids=[],
                action_id=scenario['scenario_id'],expected_result='USD exposure falls below its simulated limit while the payment currencies are funded.')]))
        out=generate_suggestions(model,(ROOT/'backend/prompts/fx_analyst.md').read_text(encoding='utf-8'),self.data,result,self.rules,'Validated',None)
        self.assertEqual(reviewed,[])
        self.assertEqual(out['suggestions'][0]['recommended_action']['kind'],'allocation_plan')
        self.assertEqual(len(out['suggestions'][0]['recommended_action']['display_rows']),4)

    def test_chat_uses_new_tools_only_for_its_pinned_snapshot(self):
        saved=dict(id='saved',context=self.data,rules=self.rules,suggestions=[dict(id='selected',fact_ids=[])],**self.result())
        def answer(prompt,content,tools,**kwargs):
            self.assertIn('assess_forward_hedge',kwargs['tool_handlers'])
            self.assertIs(tools['read_treasury_context'][1],saved['context'])
            return 'These are the saved simulation assumptions; no transaction was executed.'
        with patch.object(app,'history',return_value=[]),patch.object(app,'database',return_value=MagicMock()),patch.object(app,'run',return_value=saved),patch.object(app,'STATE',dict(analysis=dict(id='newer'))),patch.object(app,'ask_model',side_effect=answer):
            self.assertEqual(app.chat(app.Chat(run_id='saved',suggestion_id='selected',message='Explain the terms'))['mode'],'Live AI')

    def test_compact_provider_view_removes_nested_forecasts_not_audit(self):
        r=self.receipt();view=scenario_summary(r)
        self.assertNotIn('balances',view['original_order_scenarios']['baseline']);self.assertIn('balances',r['original_order_scenarios']['baseline'])

    def test_simulation_edits_affect_semantic_hash_and_snapshot_contains_extension(self):
        original=app.business_hash(self.data,self.rules);self.data['fx_terms'][0]['fee_bps']+=1
        self.assertNotEqual(app.business_hash(self.data,self.rules),original)
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'SHARED',self.folder),patch.object(app,'RUNTIME',Path(tmp)),patch.object(app,'STATE',dict(active_run_id=None,revision=0)),patch.object(app,'KafkaProducer',return_value=Mock()):
            queued=app.queue_analysis('Offline snapshot test')
            saved=Path(tmp)/'snapshots'/queued['id']
            self.assertTrue((saved/SIMULATION_FILE).exists());self.assertEqual(app.digest(app.read_context(saved)),app.digest(self.original))
