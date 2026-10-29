export type LiquidityPosition={currency:string;available:number;minimum:number;projected_low:number};
export type LiquidityForecast={currency:string;date:string;balance:number};
export type LiquiditySnapshot={positions:LiquidityPosition[];forecast:LiquidityForecast[]};

export function liquidityAlerts(current:LiquiditySnapshot,previous?:LiquiditySnapshot|null){
 const old=new Map(previous?.positions.map(p=>[p.currency,Math.max(0,p.minimum-p.projected_low)])??[]);
 return current.positions.flatMap(p=>{
  const gap=Math.max(0,p.minimum-p.projected_low);
  if(!Number.isFinite(gap)||gap<=0)return [];
  const breach=current.forecast.find(row=>row.currency===p.currency&&row.balance<p.minimum)?.date;
  const first=breach??null;
  const prior=old.get(p.currency);
  return [{currency:p.currency,available:p.available,minimum:p.minimum,projectedLow:p.projected_low,gap,firstBreach:first,
   increase:prior===undefined?null:gap-prior,severity:p.projected_low<0?'critical' as const:'warning' as const}];
 }).sort((a,b)=>(a.firstBreach??'9999').localeCompare(b.firstBreach??'9999')||b.gap/b.minimum-a.gap/a.minimum);
}

export function materialLiquidityWorsening(current:LiquiditySnapshot,previous:LiquiditySnapshot){
 const previousGaps=new Map(previous.positions.map(p=>[p.currency,Math.max(0,p.minimum-p.projected_low)]));
 return liquidityAlerts(current,previous).filter(a=>a.increase!==null&&a.increase>=Math.max(1,a.minimum*.1)&&a.gap>(previousGaps.get(a.currency)??0));
}

export function prioritizeSuggestions<T extends {finding_ids?:string[]}>(suggestions:T[],snapshot:LiquiditySnapshot):T[]{
 const rank=new Map(liquidityAlerts(snapshot).map((a,index)=>[`liquidity-${a.currency}`,index]));
 const priority=(card:T)=>Math.min(...(card.finding_ids??[]).map(id=>rank.get(id)??Number.POSITIVE_INFINITY),Number.POSITIVE_INFINITY);
 return suggestions.map((card,index)=>({card,index})).sort((a,b)=>priority(a.card)-priority(b.card)||a.index-b.index).map(item=>item.card);
}
