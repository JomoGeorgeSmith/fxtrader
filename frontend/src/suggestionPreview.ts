import {liquidityAlerts,type LiquiditySnapshot} from './liquidityAlerts.ts';

export type PreviewSuggestion={currency:string|null;severity:string;finding_ids?:string[];next_move?:string|null};
const amount=(currency:string,value:number)=>`${value<0?'−':''}${currency} ${new Intl.NumberFormat('en',{maximumFractionDigits:0}).format(Math.abs(value))}`;
const day=(value:string)=>new Intl.DateTimeFormat('en',{month:'short',day:'numeric',year:'numeric',timeZone:'UTC'}).format(new Date(`${value}T00:00:00Z`));

export function suggestionPreview(snapshot:LiquiditySnapshot,suggestion:PreviewSuggestion){
 const alert=liquidityAlerts(snapshot).find(a=>suggestion.currency===a.currency&&suggestion.finding_ids?.includes(`liquidity-${a.currency}`));
 if(!alert)return {tone:suggestion.severity==='info'?'opportunity':'review',date:null,dateLabel:null,metrics:[]};
 const rows=snapshot.forecast.filter(row=>row.currency===alert.currency);
 const low=rows.find(row=>row.balance===alert.projectedLow)?.date;
 const immediate=alert.available<alert.minimum||alert.firstBreach===rows[0]?.date;
 return {tone:immediate?'urgent':'payment',date:alert.firstBreach?day(alert.firstBreach):null,dateLabel:'First modeled buffer breach',metrics:[
  {label:'Available after holds',value:amount(alert.currency,alert.available),negative:alert.available<0},
  {label:`Forecast low${low?` · ${day(low)}`:''}`,value:amount(alert.currency,alert.projectedLow),negative:alert.projectedLow<0},
  {label:'Demo operating cushion',value:amount(alert.currency,alert.minimum),negative:false}
 ]};
}
