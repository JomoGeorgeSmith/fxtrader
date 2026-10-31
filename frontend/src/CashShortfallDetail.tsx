import {liquidityAlerts,type LiquiditySnapshot} from './liquidityAlerts';

const number=(value:number)=>new Intl.NumberFormat('en',{maximumFractionDigits:0}).format(value);
const money=(currency:string,value:number)=>`${currency} ${number(value)}`;
const day=(value:string)=>new Intl.DateTimeFormat('en',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'}).format(new Date(`${value}T00:00:00Z`));

export default function CashShortfallDetail({analysis,previous,findingIds}:{analysis:LiquiditySnapshot;previous?:LiquiditySnapshot|null;findingIds?:string[]}){
 const alert=liquidityAlerts(analysis,previous).find(a=>findingIds?.includes(`liquidity-${a.currency}`));
 if(!alert)return null;
  return <div className="suggestion-cash-facts" aria-label={`Calculated ${alert.currency} cash position`}>
   <div className="cash-facts-lead"><div><span className="cash-facts-eyebrow">Calculated cash position · {alert.currency}</span><strong className="cash-facts-value">{money(alert.currency,alert.gap)}</strong><span className="cash-facts-caption">needed to restore the modeled cushion</span></div><span className="cash-facts-status">Cushion at risk</span></div>
   <div className="cash-facts-grid">
    <div><span>Available after holds</span><strong>{money(alert.currency,alert.available)}</strong></div>
    <div><span>Minimum cushion</span><strong>{money(alert.currency,alert.minimum)}</strong></div>
    <div><span>Forecast low</span><strong>{money(alert.currency,alert.projectedLow)}</strong></div>
   </div>
   <div className="cash-facts-timing"><span className="cash-facts-date"><span className="cash-facts-dot"/>First buffer breach <strong>{alert.firstBreach?day(alert.firstBreach):'Already below minimum'}</strong></span>{alert.increase!==null&&alert.increase>0&&<span className="cash-facts-change">↑ Gap widened by {money(alert.currency,alert.increase)} since the previous saved run</span>}</div>
   <small>Modeled scenario, not approved funding. Confirm balances, holds and payment timing.</small>
  </div>;
}
