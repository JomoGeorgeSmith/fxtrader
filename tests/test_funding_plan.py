import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app import read_context
from backend.calculations import evaluate
from backend.funding_plan import assess_funding_plan, funding_plan_tool
from backend.seed import seed, ROOT

class FundingPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace=tempfile.TemporaryDirectory()
        with patch('backend.seed.SHARED',Path(cls.workspace.name)):
            seed()
        cls.original=read_context(Path(cls.workspace.name))
        cls.rules=json.loads((ROOT/'policies/demo-rules.json').read_text(encoding='utf-8'))
        cls.now=datetime.fromisoformat(cls.original['accounts'][0]['as_of'])

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.data=copy.deepcopy(self.original)
        self.rules=copy.deepcopy(type(self).rules)
        self.catalog={}

    def day(self,n):
        return (self.now+timedelta(days=n)).date().isoformat()

    def assess(self,**kwargs):
        args=dict(source='JMD',obligation_id='PAY-002',target_amount=1300000,settlement_date=self.day(2))
        args.update(kwargs)
        return assess_funding_plan(self.data,evaluate(self.data,self.rules,self.now),self.rules,self.catalog,**args)

    def test_destination_amount_and_source_cost_are_distinct(self):
        result=self.assess(source='CAD',target_amount=1050000,fee_source_amount=25)
        self.assertEqual(result['requested_target_amount'],1050000)
        self.assertEqual(result['source_principal'],1546119.66)
        self.assertEqual(result['total_source_debit'],1546144.66)
        self.assertTrue(all(f['id'] in self.catalog for f in result['facts']))

    def test_effect_starts_on_settlement_and_existing_obligation_not_double_counted(self):
        result=self.assess()
        self.assertEqual(result['source_principal'],206700000)
        rows=result['scenarios']['baseline']['balances']
        original=evaluate(self.data,self.rules,self.now)
        base={(r['currency'],r['date']):r['balance'] for r in original['forecast']}
        for row in rows:
            delta=1300000 if row['date']>=self.day(2) else 0
            self.assertEqual(row['USD'],base['USD',row['date']]+delta)
            self.assertEqual(row['JMD'],base['JMD',row['date']]-(206700000 if delta else 0))

    def test_partial_funding_does_not_claim_buffer_is_filled(self):
        result=self.assess(target_amount=100000)
        baseline=result['scenarios']['baseline']
        self.assertFalse(baseline['target_buffer_preserved'])
        self.assertTrue(baseline['target_cash_nonnegative_through_obligation'])
        self.assertEqual(baseline['target_remaining_buffer_gap'],1200000)
        self.assertFalse(baseline['passes_modeled_checks'])

    def test_adequate_plan_can_pass_with_synthetic_test_liquidity_and_limit(self):
        next(r for r in self.data['accounts'] if r['currency']=='JMD')['balance']=1000000000
        self.rules['max_absolute_fx_exposure_pct']=100
        result=self.assess()
        self.assertTrue(result['scenarios']['baseline']['passes_modeled_checks'])

    def test_exposure_failure_cannot_be_called_a_passing_plan(self):
        next(r for r in self.data['accounts'] if r['currency']=='JMD')['balance']=1000000000
        result=self.assess()
        self.assertFalse(result['exposure_within_demo_limit'])
        self.assertFalse(result['scenarios']['baseline']['passes_modeled_checks'])

    def test_late_settlement_does_not_cover_the_obligation(self):
        result=self.assess(settlement_date=self.day(4))
        self.assertFalse(result['settles_by_obligation'])
        self.assertFalse(result['scenarios']['baseline']['target_cash_nonnegative_through_obligation'])

    def test_same_day_source_receipt_is_not_assumed_available_before_debit(self):
        self.data['receipts'].append(dict(id='LARGE',currency='JMD',amount=1000000000,value_date=self.day(2),status='Confirmed',_source='test receipt'))
        result=self.assess(target_amount=3000000)
        self.assertFalse(result['scenarios']['baseline']['source_funds_available_before_settlement'])

    def test_expected_deposit_delay_changes_only_the_specified_window(self):
        self.data['receipts'].append(dict(id='UNCERTAIN',currency='USD',amount=800000,value_date=self.day(2),status='Expected',_source='test receipt'))
        result=self.assess(expected_receipt_delay_days=3)
        modes=result['scenarios']
        for base,delayed,without in zip(modes['baseline']['balances'],modes['delayed_expected_receipts']['balances'],modes['without_expected_receipts']['balances']):
            self.assertEqual(base['USD']-delayed['USD'],800000 if self.day(2)<=base['date']<self.day(5) else 0)
            self.assertEqual(base['USD']-without['USD'],800000 if base['date']>=self.day(2) else 0)

    def test_snapshot_is_not_mutated(self):
        before=copy.deepcopy(self.data)
        self.assess()
        self.assertEqual(self.data,before)

    def test_invalid_plans_are_rejected(self):
        for args in [dict(source='USD'),dict(source='EUR'),dict(obligation_id='unknown'),dict(target_amount=-1),dict(target_amount=float('nan')),dict(target_amount=1.001),dict(fee_source_amount=-1),dict(settlement_date=self.day(29)),dict(expected_receipt_delay_days=-1)]:
            with self.subTest(args=args),self.assertRaises(ValueError):
                self.assess(**args)
        self.data['payments'][1]['status']='Settled'
        with self.assertRaises(ValueError):
            self.assess()

    def test_tool_exposes_no_search_or_recommendation_input(self):
        spec=funding_plan_tool(self.data,evaluate(self.data,self.rules,self.now),self.rules,self.catalog)
        self.assertEqual(set(spec['parameters']['required']),{'source','obligation_id','target_amount','settlement_date','fee_source_amount','expected_receipt_delay_days'})
        result=spec['handler'](source='JMD',obligation_id='PAY-002',target_amount=1300000,settlement_date=self.day(2),fee_source_amount=0,expected_receipt_delay_days=3)
        self.assertEqual(result['target'],'USD')

if __name__=='__main__':
    unittest.main()
