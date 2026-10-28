"""Simplified POC ratios from separately supplied, pre-adjusted synthetic aggregates.

Not a regulatory return engine: eligibility, haircuts, caps on HQLA composition,
runoff factors and ASF/RSF weights must be applied upstream to these inputs.
"""
from decimal import Decimal

COMPONENTS = {
    'eligible_hqla': (1250000000, 'Synthetic eligible HQLA after assumed haircuts and composition limits'),
    'stressed_outflows_30d': (1800000000, 'Synthetic 30-day stressed outflows after assumed runoff factors'),
    'eligible_inflows_30d': (900000000, 'Synthetic eligible 30-day stressed inflows before the aggregate inflow cap'),
    'maturity_mismatch_addon': (0, 'Synthetic LCR denominator maturity-mismatch add-on'),
    'available_stable_funding': (10000000000, 'Synthetic available stable funding after assumed ASF weights'),
    'required_stable_funding': (9000000000, 'Synthetic required stable funding after assumed RSF weights'),
}

def calculate_metrics(rows, rules):
    values = {r['component']: Decimal(str(r['amount'])) for r in rows}
    config = rules['liquidity_metrics']
    if not 0<=Decimal(str(config['lcr']['inflow_cap_pct']))<=100:
        raise ValueError('LCR inflow cap must be between zero and 100 percent')
    outflows = values['stressed_outflows_30d']
    allowed_inflows = min(values['eligible_inflows_30d'],outflows*Decimal(str(config['lcr']['inflow_cap_pct']))/100)
    net_outflows = outflows-allowed_inflows+values['maturity_mismatch_addon']
    def metric(id,name,horizon,numerator,denominator,components,explanation):
        if denominator<=0:
            raise ValueError(f'{name}: denominator must be positive')
        pct = numerator/denominator*100
        normal = Decimal(str(config[id]['normal_min_pct']))
        good = Decimal(str(config[id]['good_min_pct']))
        if normal<=0 or good<=normal:
            raise ValueError(f'{name}: invalid demo status thresholds')
        status = 'good' if pct>=good else 'normal' if pct>=normal else 'at-risk'
        relevant = [r for r in rows if r['component'] in components]
        return dict(id=id,name=name,horizon=horizon,value_pct=float(pct),status=status,
                    normal_min_pct=float(normal),good_min_pct=float(good),numerator=float(numerator),denominator=float(denominator),
                    explanation=explanation,inputs=relevant,sources=[r['_source'] for r in relevant],
                    as_of=min(r['as_of'] for r in relevant),basis='Total JMD equivalent · synthetic pre-adjusted aggregates')
    return [
        metric('lcr','Liquidity Coverage Ratio','30-day stress',values['eligible_hqla'],net_outflows,
               ['eligible_hqla','stressed_outflows_30d','eligible_inflows_30d','maturity_mismatch_addon'],
               f'Eligible HQLA ÷ (stressed outflows − capped eligible inflows + maturity-mismatch add-on) × 100. Inflows are capped at {config["lcr"]["inflow_cap_pct"]}% of outflows. These are independent synthetic 30-day inputs, not the seven-day payment forecast.'),
        metric('nsfr','Net Stable Funding Ratio','One-year funding structure',values['available_stable_funding'],values['required_stable_funding'],
               ['available_stable_funding','required_stable_funding'],
               'Available stable funding ÷ required stable funding × 100. Inputs are synthetic aggregates after assumed funding-stability and asset-funding weights. This is a structural one-year measure, not a one-year cash-flow forecast.'),
    ]
