"""Offline regression of candidate coverage, joint decisions and typed what-ifs."""
import copy
import json
import unittest
from datetime import datetime

from backend.app import read_context
from backend.calculations import evaluate
from backend.fx_investigation import preflight
from backend.opportunity_registry import discover
from backend.portfolio_decisions import conflict_groups,evaluate_combination
from backend.portfolio_scenario import evaluate_portfolio_scenario
from backend.seed import ROOT,SHARED


class PortfolioInvestigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data=read_context(SHARED)
        cls.rules=json.loads((ROOT/'policies/demo-rules.json').read_text(encoding='utf-8'))
        cls.result=evaluate(cls.data,cls.rules,datetime.fromisoformat(cls.data['accounts'][0]['as_of']))

    def test_generalized_relationships_and_actual_preflight_invocations(self):
        data=copy.deepcopy(self.data)
        for row in data.get('fx_orders',[]):row['id']='NEW-'+row['id']
        for row in data['receipts']+data['payments']:
            if row['id'].startswith('POC-'):row['id']='NEW-'+row['id']
        for row in data.get('fx_orders',[]):
            if row['obligation_id'].startswith('POC-'):row['obligation_id']='NEW-'+row['obligation_id']
        for row in data.get('fx_receipt_timing',[]):
            if row['receipt_id'].startswith('POC-'):row['receipt_id']='NEW-'+row['receipt_id']
        items=discover(data,self.result,self.rules)
        self.assertTrue(any(c['kind']=='receipt_purchase' and 'NEW-POC-GBP-RECEIPT' in c['resources'] for c in items))
        self.assertTrue(any(c['kind']=='circular_orders' and 'NEW-SIM-FX-ORDER-01' in c['resources'] for c in items))
        self.assertTrue(any(c['kind']=='cash_gap' and c['details']['currency']=='CAD' for c in items))
        self.assertTrue(any(c['kind']=='existing_cash' for c in items))
        self.assertTrue(any(c['kind']=='exposure' and c['details']['currency']=='USD' for c in items))
        reports,trace=preflight(data,self.result,self.rules,{},items)
        self.assertTrue(any(t['name']=='assess_receipt_match' for t in trace))
        self.assertTrue(any(t['name']=='assess_round_trip' for t in trace))
        self.assertEqual(sum(t['name']=='assess_fx_trade' and t['candidate_id']==next(c['id'] for c in items if c['kind']=='cash_gap') for t in trace),3)
        self.assertTrue(any(t['name']=='evaluate_portfolio_scenario' and t['result']['passes_modeled_checks'] for t in trace))
        self.assertTrue(any(r['status']=='tested' for r in reports if r['kind']=='exposure'))

    def test_fixture_net_cost_and_usd_cad_are_not_fabricated_profit(self):
        items=discover(self.data,self.result,self.rules)
        _,trace=preflight(self.data,self.result,self.rules,{},items)
        receipt=next(t['result'] for t in trace if t['name']=='evaluate_portfolio_scenario' and
            any(op.get('receipt_id')=='POC-GBP-RECEIPT' for op in t['result']['operations']))
        self.assertTrue(receipt['passes_modeled_checks'])
        self.assertAlmostEqual(receipt['incremental_fee_avoidance_jmd'],76030.75)
        round_trip=next(t['result'] for t in trace if t['name']=='evaluate_portfolio_scenario' and len(t['result']['baseline_orders'])==2)
        self.assertTrue(round_trip['passes_modeled_checks'])
        self.assertAlmostEqual(round_trip['incremental_fee_avoidance_jmd'],933328.3736)
        cad=next(c for c in items if c['kind']=='cash_gap')
        options=[t for t in trace if t['candidate_id']==cad['id']]
        self.assertEqual({t['arguments']['source'] for t in options},{'JMD','USD','GBP'})
        self.assertTrue(next(t for t in options if t['arguments']['source']=='USD')['result']['passes_modeled_checks'])
        self.assertFalse(next(t for t in options if t['arguments']['source']=='GBP')['result']['passes_modeled_checks'])
        self.assertEqual(receipt['exposure_reconciliation'],'unverified')
        self.assertIn('not realized profit',receipt['facts'][0]['label'])
        exposure=next(t for t in trace if t['candidate_id']==next(c['id'] for c in items if c['kind']=='exposure'))
        self.assertTrue(exposure['result']['passes_modeled_checks'])
        self.assertGreater(exposure['result']['source_principal'],1_000_000)
        self.assertGreater(exposure['result']['risk']['currency_breaches_after']['USD'],0)

    def test_shared_receipt_and_order_are_mutually_exclusive(self):
        items=discover(self.data,self.result,self.rules)
        matched=[c for c in items if c['kind']=='receipt_purchase' and 'SIM-FX-PURCHASE-01' in c['resources']]
        groups=conflict_groups(items)
        self.assertEqual(len({groups[c['id']] for c in matched}),1)
        order='SIM-FX-PURCHASE-01'
        a=dict(resources=['POC-GBP-LINKED',order],baseline_orders=[order],operations=[dict(kind='cancel_order',order_id=order)])
        b=dict(resources=['POC-GBP-LINKED',order],baseline_orders=[order],operations=[dict(kind='retain_order',order_id=order)])
        report=evaluate_combination(self.data,self.result,self.rules,[a,b])
        self.assertFalse(report['passes_modeled_checks'])
        self.assertIn(order,report['conflicts'])

    def test_independently_safe_currency_sales_fail_in_combination(self):
        first=dict(kind='trade',source='USD',target='JMD',target_amount=800_000_000,settlement_date='2026-11-06')
        second=dict(first,settlement_date='2026-11-09')
        self.assertTrue(evaluate_portfolio_scenario(self.data,self.result,self.rules,[first])['passes_modeled_checks'])
        self.assertTrue(evaluate_portfolio_scenario(self.data,self.result,self.rules,[second])['passes_modeled_checks'])
        combined=evaluate_combination(self.data,self.result,self.rules,[dict(operations=[first],resources=[]),dict(operations=[second],resources=[])])
        self.assertFalse(combined['passes_modeled_checks'])
        self.assertFalse(combined['checks']['without_expected_receipts_no_new_shortfall'])

    def test_late_receipt_blocked_cancellation_and_duplicate_receipt(self):
        data=copy.deepcopy(self.data)
        order=next(o for o in data['fx_orders'] if o['id']=='SIM-FX-PURCHASE-01')
        ops=[dict(kind='cancel_order',order_id=order['id']),dict(kind='allocate',obligation_id='POC-GBP-LINKED',receipt_id='POC-GBP-RECEIPT',amount=700000)]
        order['cancellable']=False
        blocked=evaluate_portfolio_scenario(data,self.result,self.rules,ops,baseline_orders=[order['id']])
        self.assertFalse(blocked['passes_modeled_checks'])
        self.assertFalse(blocked['checks']['order_0_cancellable'])
        data['receipts']=[dict(r,value_date='2026-11-18') if r['id']=='POC-GBP-RECEIPT' else r for r in data['receipts']]
        late=evaluate_portfolio_scenario(data,self.result,self.rules,ops,baseline_orders=[order['id']])
        self.assertFalse(late['checks']['allocation_1_timely'])
        with self.assertRaisesRegex(ValueError,'(Receipt|Payment allocation) used twice'):
            evaluate_portfolio_scenario(data,self.result,self.rules,ops+[ops[1]],baseline_orders=[order['id']])


if __name__=='__main__':unittest.main()
