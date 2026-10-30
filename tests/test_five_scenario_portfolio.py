"""Offline coverage of one coherent workbook, not model-card quotas or scripted advice."""
import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from backend.app import read_context
from backend.calculations import evaluate
from backend.five_scenario_portfolio import extend
from backend.funding_plan import assess_funding_plan
from backend.fx_scenarios import assess_forward_hedge,assess_fx_trade,assess_receipt_match,assess_round_trip
from backend.portfolio_signals import portfolio_signals
from backend.seed import ROOT,SHARED
from backend.simulation_inputs import validate_links


class FiveScenarioPortfolioTests(unittest.TestCase):
    def test_portfolio_exposes_cross_currency_choices_and_preserves_cad_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            for path in SHARED.glob('*.xlsx'):shutil.copy2(path,folder/path.name)
            # Works on both the untouched fixture and an already extended checkout.
            extend(folder)
            self.assertFalse(extend(folder))
            data=read_context(folder);validate_links(data)
            rules=json.loads((ROOT/'policies/demo-rules.json').read_text(encoding='utf-8'))
            result=evaluate(data,rules,datetime.fromisoformat(data['accounts'][0]['as_of']))
            flags={f['id'] for f in result['findings']}
            self.assertIn('fx-exposure-USD',flags)
            self.assertIn('liquidity-CAD',flags)
            self.assertEqual(next(p for p in result['positions'] if p['currency']=='CAD')['projected_low'],-850000)
            signals=portfolio_signals(data,result,rules)
            self.assertIn('POC-GBP-RECEIPT',[p['id'] for p in next(c for c in signals['currencies'] if c['currency']=='GBP')['incoming_receipts']])
            self.assertIn('SIM-FX-ORDER-02',[o['id'] for o in signals['outstanding_fx_orders']])
            dated={event['id']:day for day in signals['dated_relationships'] for event in day['events']}
            self.assertLess(dated['POC-GBP-RECEIPT']['date'],dated['POC-GBP-LINKED']['date'])
            self.assertIn('POC-USD-FUTURE',dated)
            self.assertLess(dated['SIM-FX-ORDER-01']['date'],dated['SIM-FX-ORDER-02']['date'])
            self.assertTrue(signals['forward_inputs_present'])
            self.assertTrue(any(window['receipt_id']=='POC-GBP-RECEIPT' and window['payment_id']=='POC-GBP-LINKED'
                and 'SIM-FX-PURCHASE-01' in window['linked_planned_purchases'] for window in signals['receipt_payment_windows']))
            self.assertTrue(any(pair['first']=='SIM-FX-ORDER-01' and pair['second']=='SIM-FX-ORDER-02'
                for pair in signals['opposing_order_pairs']))
            cad=assess_funding_plan(data,result,rules,{},'USD','SIM-CAD-SHORTFALL-P01',1100000,'2026-11-13')
            self.assertTrue(cad['scenarios']['without_expected_receipts']['passes_modeled_checks'])
            self.assertGreater(cad['risk']['currency_breaches_after']['USD'],0)
            self.assertLess(cad['risk']['currency_breaches_after']['USD'],cad['risk']['currency_breaches_before']['USD'])
            receipt=assess_receipt_match(data,result,rules,{},'POC-GBP-RECEIPT','POC-GBP-LINKED',700000,'SIM-FX-PURCHASE-01')
            self.assertTrue(receipt['passes_modeled_checks'])
            self.assertTrue(receipt['checks']['delayed_receipt_arrives_before_payment'])
            self.assertTrue(assess_round_trip(data,result,rules,{},'SIM-FX-ORDER-01','SIM-FX-ORDER-02')['passes_modeled_checks'])
            gbp=assess_fx_trade(data,result,rules,{},'GBP','USD',350000,'2026-11-24')
            cad_source=assess_fx_trade(data,result,rules,{},'CAD','USD',350000,'2026-11-24')
            self.assertLess(gbp['scenarios']['without_expected_receipts']['lows']['GBP'],rules['minimum_available']['GBP'])
            self.assertGreater(cad_source['scenarios']['without_expected_receipts']['balances'][-1]['CAD'],rules['minimum_available']['CAD'])
            self.assertFalse(cad_source['passes_modeled_checks'])  # Earlier CAD gap and USD risk remain unresolved independently.
            self.assertGreater(cad_source['risk']['currency_breaches_after']['USD'],cad_source['risk']['currency_breaches_before']['USD'])
            hedge=assess_forward_hedge(data,result,rules,{},'GBP','POC-USD-FUTURE',150000,'2026-11-20','SIM-FX-DEALER-01')
            self.assertTrue(hedge['checks']['amount_within_hedge_policy'])
            self.assertEqual(len(hedge['spot_sensitivity']),3)
            self.assertFalse(hedge['passes_modeled_checks'])  # Future USD need is not a dated risk offset in the current inputs.


if __name__=='__main__':unittest.main()
