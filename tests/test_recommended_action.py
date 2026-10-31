import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from backend.analyst import analyst_schema, simulate_conversion
from backend.app import read_context
from backend.calculations import evaluate
from backend.funding_plan import assess_funding_plan
from backend.recommended_action import recommended_action, sheet_rate
from backend.seed import ROOT, seed


class RecommendedActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace=tempfile.TemporaryDirectory()
        with patch('backend.seed.SHARED',Path(cls.workspace.name)):seed()
        cls.original=read_context(Path(cls.workspace.name))
        cls.policy=json.loads((ROOT/'policies/demo-rules.json').read_text(encoding='utf-8'))
        cls.now=datetime.fromisoformat(cls.original['accounts'][0]['as_of'])

    @classmethod
    def tearDownClass(cls):cls.workspace.cleanup()

    def setUp(self):
        self.data=copy.deepcopy(self.original)
        self.rules=copy.deepcopy(self.policy)
        # Explicitly synthetic test liquidity; never edit live shared workbooks.
        for account in self.data['accounts']:account['balance']=1000000000
        self.rules['max_absolute_fx_exposure_pct']=100000
        self.result=evaluate(self.data,self.rules,self.now)
        self.catalog={}

    def plan(self,**overrides):
        args=dict(source='GBP',obligation_id='PAY-002',target_amount=1050000,
            settlement_date=(self.now+timedelta(days=1)).date().isoformat())
        args.update(overrides)
        return assess_funding_plan(self.data,self.result,self.rules,self.catalog,**args)

    def resolve(self,result,name='assess_funding_plan'):
        id=result.get('plan_id') or result['scenario_id']
        return recommended_action(id,[dict(name=name,result=result)],self.data)

    def test_correct_rate_sides_in_every_supported_direction(self):
        rates={r['currency']:r for r in self.data['rates']}
        for source in ('JMD','USD','GBP','CAD'):
            for target in ('JMD','USD','GBP','CAD'):
                if source==target:continue
                with self.subTest(source=source,target=target):
                    buy=Decimal(1) if source=='JMD' else Decimal(str(rates[source]['buy']))
                    sell=Decimal(1) if target=='JMD' else Decimal(str(rates[target]['sell']))
                    self.assertEqual(sheet_rate(self.data,source,target),buy/sell)

    def test_plan_fields_come_from_exact_selected_calculation(self):
        result=self.plan()
        action,warning=self.resolve(result)
        self.assertIsNone(warning)
        self.assertEqual(action['buy'],dict(currency='USD',amount='1050000.00'))
        self.assertEqual(Decimal(action['sell']['amount']),Decimal(str(result['source_principal'])))
        self.assertEqual(Decimal(action['reference_rate']['target_per_source']),sheet_rate(self.data,'GBP','USD'))
        self.assertEqual(action['settlement_date'],result['settlement_date'])
        self.assertTrue(set(action['fact_ids'])<=self.catalog.keys())
        self.assertNotIn('fees',action)
        self.assertNotIn('rate_sheet_date',action)

    def test_no_trade_is_inferred_from_absent_or_invented_selection(self):
        result=self.plan()
        trace=[dict(name='assess_funding_plan',result=result)]
        self.assertEqual(recommended_action(None,trace,self.data),(None,None))
        action,warning=recommended_action('invented',trace,self.data)
        self.assertIsNone(action)
        self.assertIn('no matching',warning)

    def test_failed_baseline_does_not_become_a_recommended_action(self):
        self.rules['max_absolute_fx_exposure_pct']=0
        action,warning=self.resolve(self.plan())
        self.assertIsNone(action)
        self.assertIn('fails',warning)

    def test_receipt_dependency_does_not_invent_a_robustness_guarantee(self):
        result=self.plan()
        result['scenarios']['without_expected_receipts']['passes_modeled_checks']=False
        action,warning=self.resolve(result)
        self.assertIsNotNone(action)
        self.assertIsNone(warning)
        self.assertNotIn('verified',json.dumps(action))

    def test_immediate_conversion_uses_source_amount_not_destination_amount(self):
        result=simulate_conversion(self.data,self.result,self.rules,self.catalog,'CAD','USD',150000)
        action,warning=self.resolve(result,'simulate_fx_conversion')
        self.assertIsNone(warning)
        self.assertEqual(action['sell']['amount'],'150000.00')
        self.assertEqual(action['buy']['amount'],'101867.92')
        self.assertIsNone(action['settlement_date'])

    def test_immediate_conversion_must_preserve_destination_buffer(self):
        self.data=copy.deepcopy(self.original)
        self.result=evaluate(self.data,self.rules,self.now)
        result=simulate_conversion(self.data,self.result,self.rules,self.catalog,'CAD','USD',1)
        action,warning=self.resolve(result,'simulate_fx_conversion')
        self.assertIsNone(action)
        self.assertIn('fails',warning)

    def test_live_response_schema_is_strict_but_allows_no_action(self):
        card=analyst_schema()['$defs']['Card']
        self.assertEqual(set(card['required']),set(card['properties']))
        for field in ('action_id','expected_result'):
            self.assertNotIn('default',card['properties'][field])
            self.assertIn({'type':'null'},card['properties'][field]['anyOf'])


if __name__=='__main__':unittest.main()
