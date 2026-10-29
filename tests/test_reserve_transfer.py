import unittest

from backend.reserve_transfer import assess_reserve_transfer, reserve_candidates, reserve_transfer_tool
from backend.recommended_action import recommended_action


class ReserveTransferTests(unittest.TestCase):
    def setUp(self):
        self.data=dict(accounts=[dict(currency='USD',balance=400000,holds=100000,_source='synthetic account')],
            reserves=[dict(currency='USD',balance=5460000,_source='synthetic reserve')],
            receipts=[dict(currency='USD',status='Expected',value_date='2026-11-01',amount=1400000)])
        self.rules=dict(protected_reserve={'USD':500000})
        self.result=dict(positions=[dict(currency='USD',available=300000,minimum=1000000,
            projected_low=-3100000,confirmed_receipts_low=-4500000)],forecast=[
            dict(currency='USD',date='2026-10-29',balance=100000),
            dict(currency='USD',date='2026-10-30',balance=-1050000),
            dict(currency='USD',date='2026-11-01',balance=-3100000)])
        self.catalog={}

    def assess(self,amount=4100000,arrival='2026-10-29'):
        return assess_reserve_transfer(self.data,self.result,self.rules,self.catalog,'USD',amount,arrival,3)

    def test_400k_usd_case_exposes_conditional_baseline_and_receipt_risk(self):
        candidate=reserve_candidates(self.data,self.result,self.rules)[0]
        self.assertEqual(candidate['arithmetic_headroom'],4960000)
        self.assertEqual(candidate['baseline_buffer_gap'],4100000)
        self.assertEqual(candidate['gap_without_expected_receipts'],5500000)
        result=self.assess()
        self.assertEqual(result['reserve_after'],1360000)
        self.assertTrue(result['reserve_floor_preserved'])
        self.assertEqual(result['scenarios']['baseline']['operating_low_if_arrives'],1000000)
        self.assertEqual(result['scenarios']['without_expected_receipts']['operating_low_if_arrives'],-400000)
        self.assertEqual(result['scenarios']['without_expected_receipts']['remaining_buffer_gap'],1400000)
        self.assertFalse(result['transfer_authority_confirmed'])
        self.assertFalse(result['delivery_timing_confirmed'])
        self.assertFalse(result['executable_transfer_verified'])
        self.assertTrue(set(f['id'] for f in result['facts'])<=self.catalog.keys())
        self.assertIsNone(recommended_action(result['scenario_id'],[dict(name='assess_reserve_transfer',result=result)],self.data)[0])

    def test_10k_balance_requires_more_but_still_only_conditional(self):
        self.result['positions'][0].update(available=-90000,projected_low=-3490000,confirmed_receipts_low=-4890000)
        self.result['forecast']=[dict(row,balance=row['balance']-390000) for row in self.result['forecast']]
        self.assertEqual(reserve_candidates(self.data,self.result,self.rules)[0]['baseline_buffer_gap'],4490000)
        result=self.assess(4490000)
        self.assertEqual(result['reserve_after'],970000)
        self.assertEqual(result['scenarios']['baseline']['operating_low_if_arrives'],1000000)
        self.assertFalse(result['executable_transfer_verified'])

    def test_arrival_after_breach_or_above_headroom_is_not_described_as_safe(self):
        late=self.assess(arrival='2026-10-30')
        self.assertFalse(late['arrival_before_or_on_breach'])
        self.assertFalse(late['scenarios']['baseline']['operating_buffer_preserved_if_arrives'])
        excess=self.assess(5000000)
        self.assertFalse(excess['reserve_floor_preserved'])
        for amount in (0,-1,float('nan')):
            with self.subTest(amount=amount),self.assertRaises(ValueError):self.assess(amount)
        with self.assertRaises(ValueError):self.assess(arrival='2026-10-28')

    def test_tool_contract_is_available_without_an_execution_claim(self):
        spec=reserve_transfer_tool(self.data,self.result,self.rules,self.catalog)
        self.assertIn('NOT verify authority',spec['description'])
        self.assertIn('arrival_date',spec['parameters']['required'])
        self.assertFalse(spec['handler'](currency='USD',amount=4100000,arrival_date='2026-10-29',expected_receipt_delay_days=3)['executable_transfer_verified'])
