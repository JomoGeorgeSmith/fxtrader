"""Deterministic demo checks. All monetary arithmetic uses Decimal."""
from datetime import datetime, timedelta
from decimal import Decimal
from .liquidity_metrics import calculate_metrics
from .fx_market import ledger_events

CURRENCIES = ('JMD','USD','GBP','CAD')
def dec(value):
    return Decimal(str(value))
def money(value, currency):
    return f'{currency} {value:,.0f}'

def evaluate(data, rules, now=None):
    now = now or datetime.now()
    findings, positions, forecast = [], [], []
    rates = {r['currency']: r for r in data['rates']}
    booked_events=ledger_events(data)
    def add(id, severity, title, summary, detail, rule, sources, currency=None):
        findings.append(dict(id=id,severity=severity,title=title,summary=summary,detail=detail,rule=rule,sources=sources,currency=currency))
    for currency in CURRENCIES:
        accounts = [r for r in data['accounts'] if r['currency']==currency]
        available = sum((dec(r['balance'])-dec(r['holds']) for r in accounts), Decimal(0))
        reserve_rows = [r for r in data['reserves'] if r['currency']==currency]
        reserve = sum((dec(r['balance']) for r in reserve_rows), Decimal(0))
        floor = dec(rules['minimum_available'][currency])
        required = dec(rules['protected_reserve'][currency])
        sources = [r['_source'] for r in accounts]
        running, low = available, available
        cautious_running, cautious_low = available, available
        low_date = now.date().isoformat()
        first_breach = low_date if available < floor else None
        for day in range(rules['forecast_days']+1):
            date = (now+timedelta(days=day)).date().isoformat()
            def flows(kind):
                return [r for r in data[kind] if r['currency']==currency and r['value_date']==date and r['status'] not in ('Cancelled','Settled')]
            ins, outs = flows('receipts'), flows('payments')
            booked=sum((amount for when,c,amount in booked_events if when==date and c==currency),Decimal(0))
            running += sum((dec(r['amount']) for r in ins),Decimal(0))-sum((dec(r['amount']) for r in outs),Decimal(0))+booked
            cautious_running += sum((dec(r['amount']) for r in ins if r['status'] in ('Confirmed','Committed')),Decimal(0))-sum((dec(r['amount']) for r in outs),Decimal(0))+booked
            cautious_low = min(cautious_low,cautious_running)
            forecast.append(dict(currency=currency,date=date,balance=float(running),confirmed_receipts_balance=float(cautious_running)))
            if running < floor and first_breach is None:
                first_breach = date
            if running < low:
                low, low_date = running,date
        positions.append(dict(currency=currency,available=float(available),reserve=float(reserve),minimum=float(floor),projected_low=float(low),ending=float(running),confirmed_receipts_low=float(cautious_low)))
        if low < floor:
            gap = floor-low
            add(f'liquidity-{currency}', 'critical' if low<0 else 'warning', f'{currency} funding needs attention',
                f'Arrange {money(gap,currency)} by {first_breach} to maintain the demo cash buffer.',
                f'Available funds are {money(available,currency)} after holds. The lowest projected balance is {money(low,currency)} on {low_date}, against a {money(floor,currency)} minimum. Funding needed is minimum minus projected low. Confirm receipts and arrange approved funding; this is not a trade instruction.',
                'minimum_available',sources+[r['_source'] for r in data['payments']+data['receipts'] if r['currency']==currency],currency)
        if reserve < required:
            add(f'reserve-{currency}','warning',f'Protect the {currency} reserve',f'The demo reserve is {money(required-reserve,currency)} below its protected minimum.',
                f'Reserve balance: {money(reserve,currency)}. Demo requirement: {money(required,currency)}. Operational balances exclude these separate reserve balances. Review a transfer without compromising scheduled payments.',
                'protected_reserve',[r['_source'] for r in reserve_rows],currency)
    exposure = sum((abs(dec(r['net_fx_exposure'])*dec(rates[r['currency']]['sell'])) for r in data['risk']),Decimal(0))
    capital = dec(data['risk'][0]['capital_base_jmd'])
    pct = exposure/capital*100
    if pct > dec(rules['max_absolute_fx_exposure_pct']):
        add('fx-exposure','warning','FX exposure is above the demo limit',f'Aggregate absolute exposure is {pct:.1f}% of capital; the demo limit is {rules["max_absolute_fx_exposure_pct"]}%.',
            f'Sum of absolute currency exposures translated at sheet selling rates: {money(exposure,"JMD")}. Capital: {money(capital,"JMD")}. This simplified gross measure is not regulatory NOP. Review exposure-reduction options with Treasury.',
            'max_absolute_fx_exposure_pct',[r['_source'] for r in data['risk']+data['rates']])
    for currency, rate in rates.items():
        age = (now-datetime.fromisoformat(str(rate['as_of']))).total_seconds()/3600
        change = (dec(rate['sell'])/dec(rate['previous_sell'])-1)*100
        if age>rules['rate_max_age_hours']:
            add(f'stale-{currency}','warning',f'Refresh the {currency} rate',f'The {currency} quote is more than {rules["rate_max_age_hours"]} hours old.',f'Rate timestamp: {rate["as_of"]}. Confirm the rate sheet before considering a conversion.','rate_max_age_hours',[rate['_source']],currency)
        if abs(change)>dec(rules['unusual_rate_move_pct']):
            add(f'rate-{currency}','info',f'Review the {currency} rate movement',f'The selling rate moved {change:+.1f}% from the previous reference.',f'Previous: {rate["previous_sell"]}; current: {rate["sell"]} JMD per {currency}. Check the quote before using it; a rate-sheet movement is not a market forecast.','unusual_rate_move_pct',[rate['_source']],currency)
    metrics = calculate_metrics(data['liquidity_inputs'],rules)
    for metric in metrics:
        if metric['status']=='at-risk':
            add(f'metric-{metric["id"]}','critical',f'{metric["id"].upper()} is below the demo floor',
                f'{metric["name"]} is {metric["value_pct"]:.1f}%, below the {metric["normal_min_pct"]:g}% demo floor.',
                f'{metric["explanation"]} Numerator: JMD {metric["numerator"]:,.0f}. Denominator: JMD {metric["denominator"]:,.0f}. Review the underlying funding and liquidity assumptions. This is a demo status, not a regulatory breach assessment.',
                f'liquidity_metrics.{metric["id"]}',metric['sources'])
    order = {'critical':0,'warning':1,'info':2}
    findings.sort(key=lambda f:order[f['severity']])
    return dict(calculation_version='demo-calculations-2.0',metrics=metrics,positions=positions,forecast=forecast,findings=findings,exposure_pct=float(pct))
