import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.analyst import generate_suggestions
from backend.app import read_context
from backend.calculations import evaluate
from backend.calculation_cache import cached_evaluate
from backend.portfolio_signals import portfolio_signals
from backend.seed import ROOT,seed
from backend.synthesize_fx import missing_rows


class PortfolioExplorationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace=tempfile.TemporaryDirectory()
        with patch('backend.seed.SHARED',Path(cls.workspace.name)):seed()
        cls.data=read_context(Path(cls.workspace.name))
        cls.rules=json.loads((ROOT/'policies/demo-rules.json').read_text(encoding='utf-8'))
        cls.prompt=(ROOT/'backend/prompts/fx_analyst.md').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):cls.workspace.cleanup()

    def scenario(self):
        data=copy.deepcopy(self.data)
        data['fx_limits']=missing_rows(data,datetime.fromisoformat(data['accounts'][0]['as_of']).date())['fx_limits']
        for row in data['fx_limits']:row['_source']='Synthetic FX limit for offline test'
        next(r for r in data['risk'] if r['currency']=='USD')['net_fx_exposure']=25000000
        next(a for a in data['accounts'] if a['currency']=='USD')['balance']=20400000
        data['payments'].append(dict(id='SIM-EXTRA-CAD',currency='CAD',amount=50480000,
            value_date='2026-11-18',status='Committed',_source='Offline CAD payment'))
        result=evaluate(data,self.rules,datetime.fromisoformat(data['accounts'][0]['as_of']))
        return data,result

    def test_independent_risk_and_cash_signals_reveal_over_limit_position_without_prescribing_trade(self):
        data,result=self.scenario()
        flags={f['id'] for f in result['findings']}
        self.assertIn('fx-exposure',flags)
        self.assertIn('fx-exposure-USD',flags)
        self.assertIn('liquidity-CAD',flags)
        signals=portfolio_signals(data,result,self.rules)
        usd=next(row for row in signals['currencies'] if row['currency']=='USD')
        self.assertEqual(usd['exposure']['recorded_net_position'],25000000)
        usd_limit=next(row['max_absolute_exposure'] for row in data['fx_limits'] if row['currency']=='USD')
        self.assertAlmostEqual(usd['exposure']['excess_over_synthetic_limit'],25000000-usd_limit,places=2)
        self.assertEqual(usd['operating_available'],20300000)
        self.assertGreater(usd['arithmetic_sale_headroom_baseline'],0)
        self.assertLess(usd['arithmetic_sale_headroom_baseline'],usd['exposure']['excess_over_synthetic_limit'])
        self.assertLessEqual(usd['arithmetic_sale_headroom_without_expected_receipts'],usd['arithmetic_sale_headroom_baseline'])
        self.assertEqual(signals['currencies'][-1]['currency'],'CAD')
        self.assertIn('SIM-EXTRA-CAD',[p['id'] for p in signals['currencies'][-1]['upcoming_payments']])
        self.assertNotIn('recommended_action',json.dumps(signals))

    def test_reloaded_evaluator_restores_missing_usd_finding_from_stale_cache(self):
        data,_=self.scenario()
        now=datetime.fromisoformat(data['accounts'][0]['as_of'])
        with tempfile.TemporaryDirectory() as directory:
            with patch('backend.calculation_cache.loaded_calculation_fingerprint',return_value='stale-import'):
                old,_=cached_evaluate(data,self.rules,directory,now)
                old['findings']=[f for f in old['findings'] if f['id']!='fx-exposure-USD']
                next(Path(directory).glob('*.json')).write_text(json.dumps(old),encoding='utf-8')
            fresh,hit=cached_evaluate(data,self.rules,directory,now)
            self.assertFalse(hit)
            self.assertIn('fx-exposure-USD',{f['id'] for f in fresh['findings']})

    def test_single_writer_can_publish_distinct_risk_and_cash_cards_without_an_extra_review(self):
        data,result=self.scenario();calls=[]
        def model(prompt,content,tools=None,**kwargs):
            calls.append(kwargs['response_schema']['title'])
            self.assertIn('Investigate portfolio candidates',prompt)
            payload=json.loads(content)
            usd=next(x for x in payload['portfolio_map']['currencies'] if x['currency']=='USD')
            self.assertGreater(usd['exposure']['excess_over_synthetic_limit'],10000000)
            self.assertIn('simulate_fx_conversion',kwargs['tool_handlers'])
            return json.dumps(dict(brief='The dollar risk and Canadian-dollar payment are separate concerns.',suggestions=[
                dict(topic='usd_position',currency='USD',severity='warning',title='Investigate the excess dollar position',
                    summary='The recorded USD position exceeds the synthetic currency limit.',
                    detail='Compare the recorded position with cash safely available for sale. A sale of the entire excess would compromise the cash cushion.',
                    assumptions=[],finding_ids=['fx-exposure-USD'],fact_ids=['USD.exposure.current','USD.exposure.limit','USD.projected_low'],rule_ids=[],
                    decision_group=None,action_id=None,expected_result=None,
                    next_move='Ask Treasury to reconcile USD risk and test a limited sale against the upcoming CAD payment; do not sell the full excess from cash.'),
                dict(topic='cad_payment',currency='CAD',severity='critical',title='Cover the November Canadian-dollar payment',
                    summary='The recorded payment leaves a CAD operating cash shortage.',
                    detail='The forecast falls below the demo operating cushion. Fund the payment only after testing cash and settlement.',
                    assumptions=[],finding_ids=['liquidity-CAD'],fact_ids=['CAD.buffer_gap'],rule_ids=['minimum_available'],
                    decision_group=None,action_id=None,expected_result=None,
                    next_move='Ask Treasury to confirm a feasible funding date and alternate approved cover if the plan fails.')]))
        with patch('backend.analyst.run_orchestrator',side_effect=lambda **kw:kw['write'](dict(funding_tasks=[],assessments=[],warnings=[]))):
            output=generate_suggestions(model,self.prompt,data,result,self.rules,'Validated',None)
        self.assertEqual(calls,['AnalystOutput'])
        self.assertEqual([card['topic'] for card in output['suggestions']],['usd_position','cad_payment'])
        self.assertEqual(output['analyst_audit']['evidence_review']['status'],'disabled_for_development')

    def test_shared_choice_label_survives_without_forcing_a_trade_or_deleting_cards(self):
        data,result=self.scenario()
        from backend.analyst import build_evidence,validate_output
        catalog=build_evidence(data,result,self.rules)
        proposals=[dict(topic=topic,currency='USD',severity='warning',title=title,summary='A conditional funding choice for the same obligation.',
            detail='This option uses the same source cash as the other option. Treasury must choose one, not add both.',
            assumptions=[],finding_ids=['fx-exposure-USD'],fact_ids=['USD.exposure.current'],rule_ids=[],
            decision_group='cad_payment_choices',action_id=None,expected_result=None,next_move='Test this option against all cash cushions before approval.')
            for topic,title in [('usd_cad','Use USD to cover CAD'),('usd_jmd','Review another use of USD')]]
        _,cards=validate_output(json.dumps(dict(brief='Choose one of two mutually exclusive options.',suggestions=proposals)),catalog,result,self.rules)
        self.assertEqual(len(cards),2)
        self.assertEqual({c['decision_group'] for c in cards},{'cad_payment_choices'})
