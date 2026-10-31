"""Synthetic end-to-end plumbing with a stub author; no paid model request."""
import json
import unittest
from datetime import datetime

from backend.analyst import generate_suggestions
from backend.app import read_context
from backend.calculations import evaluate
from backend.seed import SHARED,ROOT


class OfflinePipelineTests(unittest.TestCase):
    def test_candidate_evidence_reaches_publication_without_live_model(self):
        data=read_context(SHARED)
        rules=json.loads((ROOT/'policies/demo-rules.json').read_text(encoding='utf-8'))
        result=evaluate(data,rules,datetime.fromisoformat(data['accounts'][0]['as_of']))
        seen=[]
        def stub_writer(system,content,tools=None,**kwargs):
            payload=json.loads(content)
            reports=payload['candidate_preflight'];registry={c['id']:c for c in payload['candidate_registry']}
            by_kind={}
            for report in reports:
                c=registry[report['id']]
                if report['kind']=='receipt_purchase' and 'POC-GBP-RECEIPT' not in c['resources']:continue
                by_kind[report['kind']]=report
            def option(kind,tool,source=None):
                return next(o for o in by_kind[kind]['alternatives'] if o['tool']==tool and
                    (source is None or o['arguments'].get('source')==source))
            actions=[('receipt_match','GBP',option('receipt_purchase','evaluate_portfolio_scenario')['scenario_id'],['receipts.POC-GBP-RECEIPT.amount'],[]),
                ('circular_orders','GBP',option('circular_orders','evaluate_portfolio_scenario')['scenario_id'],['simulation.SIM-FX-ORDER-01'],[]),
                ('fund_cad','CAD',option('cash_gap','assess_fx_trade','USD')['scenario_id'],['CAD.buffer_gap'],['liquidity-CAD']),
                ('existing_cash','USD',option('existing_cash','evaluate_portfolio_scenario')['scenario_id'],['simulation.SIM-FX-PURCHASE-02'],[]),
                ('reconcile_risk','USD',None,['USD.exposure.current'],['fx-exposure-USD'])]
            cards=[]
            for topic,currency,action_id,facts,findings in actions:
                cards.append(dict(topic=topic,currency=currency,severity='warning',title='Review '+topic.replace('_',' '),
                    summary='Investigate the recorded portfolio relationship before deciding.',
                    detail='Preliminary synthetic evidence exists; reconcile restrictions, exposure and executable terms before any action.',
                    assumptions=['No action is approved or executed.'],finding_ids=findings,fact_ids=facts,rule_ids=[],
                    action_id=action_id,expected_result='Indicative outcome only, conditional on Treasury approval.' if action_id else None,
                    next_move='Ask Treasury to validate available cash, dealer terms and risk before deciding.',decision_group=None))
            seen.extend(actions)
            dispositions=[dict(candidate_id=c['id'],status='tested' if next(r for r in reports if r['id']==c['id'])['status']=='tested' else 'deferred',
                reason='Compared recorded alternatives; additional validation remains.' if next(r for r in reports if r['id']==c['id'])['status']=='tested' else 'No complete funding terms supplied yet.') for c in registry.values()]
            return json.dumps(dict(brief='Portfolio alternatives are indicative and require Treasury approval.',suggestions=cards,candidate_dispositions=dispositions))
        output=generate_suggestions(stub_writer,'Synthetic instructions',data,result,rules,'Offline',None)
        self.assertEqual(output['suggestion_origin'],'analyst')
        self.assertEqual(len(output['suggestions']),5)
        self.assertEqual(len(output['candidate_dispositions']),len(output['analyst_audit']['candidates']))
        self.assertEqual(sum(s['recommended_action'] is not None for s in output['suggestions']),4)
        self.assertTrue(any(t['name']=='assess_receipt_match' for t in output['analyst_audit']['tool_calls']))
        self.assertTrue(any(t['name']=='assess_round_trip' for t in output['analyst_audit']['tool_calls']))
        self.assertIsNone(next(s for s in output['suggestions'] if s['topic']=='reconcile_risk')['recommended_action'])


if __name__=='__main__':unittest.main()
