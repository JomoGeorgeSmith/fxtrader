"""Canonical read-only snapshot manifest, shared by future specialist analysts."""
from .fx_market import D


def normalized_state(data,result,rules,snapshot_id=None):
    def rows(key,date_field=None):
        return [dict(id=row.get('id',row.get('account_id',row.get('currency'))),
            currency=row.get('currency'),value_date=row.get(date_field) if date_field else None,
            status=row.get('status'),source_category=key,source=row.get('_source'),
            confirmation='confirmed' if row.get('status') in ('Confirmed','Committed','Booked','Active') else 'expected_or_unverified',
            synthetic=key.startswith('fx_') or 'SYNTHETIC' in str(row.get('description','')).upper(),
            amount=row.get('amount',row.get('balance')))
            for row in data.get(key,[])]
    accounts=rows('accounts')
    for item,row in zip(accounts,data['accounts']):
        item['available_after_recorded_holds']=float(D(row['balance'])-D(row['holds']))
        item['unknown_encumbrances']='not supplied'
    return dict(snapshot_id=snapshot_id,snapshot_as_of=data['accounts'][0]['as_of'],
        operating_balances=accounts,protected_reserves=rows('reserves'),payments=rows('payments','value_date'),
        receipts=rows('receipts','value_date'),planned_fx_orders=rows('fx_orders','settlement_date'),
        executed_or_booked_fx=[o for o in rows('fx_orders','settlement_date') if o['status']=='Booked'],
        allocations=rows('fx_allocations'),hedges=rows('fx_hedges','settlement_date'),
        settlement_terms=rows('fx_terms'),settlement_holidays=rows('fx_holidays','value_date'),
        permissions=rows('fx_permissions'),risk_positions=rows('risk'),policies=rules,
        market_quotes=rows('rates'),quote_type='Treasury reference sheet, not verified executable market quotes',
        exposure_reconciled=False,exposure_reconciliation='Risk_Inputs has no auditable link to operating cash or offsets.',
        cash_forecast_basis='Recorded active flows and Booked FX only; Planned orders are modeled as explicit what-ifs.')
