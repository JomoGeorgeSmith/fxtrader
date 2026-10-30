export type ConversionAction={
 assessment_id:string;
 buy:{currency:string;amount:string};
 sell:{currency:string;amount:string};
 reference_rate:{source:string;target:string;target_per_source:string};
 settlement_date?:string|null;
 timing?:{calendar_days_before_first_shortfall:number|null;earliest_modeled_shortfall:string|null;settlement_is_weekday:boolean;bank_delivery_confirmed:false}|null;
 rate_basis:'treasury_sheet';
};
export type ScenarioAction={assessment_id:string;kind:'conversion'|'allocation_plan'|'receipt_match'|'cancel_orders'|'forward'|'portfolio_change';display_rows:{label:string;value:string}[];rate_basis:'treasury_sheet'|'simulated_forward'|'not_applicable';settlement_date?:string|null};
export type RecommendedAction=ConversionAction|ScenarioAction;

const units:Record<string,{symbol:string;name:string}>={
 JMD:{symbol:'J$',name:'Jamaican dollars'},USD:{symbol:'US$',name:'US dollars'},
 GBP:{symbol:'£',name:'British pounds'},CAD:{symbol:'CA$',name:'Canadian dollars'},
};

function money(currency:string,amount:string,maxDigits=2){
 const value=Number(amount);
 if(!Number.isFinite(value)||value<=0||!units[currency])return null;
 return units[currency].symbol+new Intl.NumberFormat('en-GB',{maximumFractionDigits:maxDigits}).format(value);
}

export function actionRows(action?:RecommendedAction|null){
 if(action&&'display_rows' in action){
    if(!['conversion','allocation_plan','receipt_match','cancel_orders','forward','portfolio_change'].includes(action.kind)||!Array.isArray(action.display_rows)||action.display_rows.length<1||action.display_rows.length>5)return null;
   if(action.kind==='forward'&&action.rate_basis!=='simulated_forward')return null;
   if(action.kind==='allocation_plan'&&action.rate_basis!=='treasury_sheet')return null;
    if(action.kind==='portfolio_change'&&action.rate_basis!=='not_applicable')return null;
   return action.display_rows.every(r=>typeof r.label==='string'&&typeof r.value==='string'&&r.label&&r.value)?action.display_rows:null;
 }
 if(!action||action.rate_basis!=='treasury_sheet')return null;
 const buy=money(action.buy.currency,action.buy.amount),sell=money(action.sell.currency,action.sell.amount);
 const rate=money(action.reference_rate.target,action.reference_rate.target_per_source,4);
 const source=units[action.reference_rate.source];
 if(!buy||!sell||!rate||!source||action.reference_rate.source!==action.sell.currency||action.reference_rate.target!==action.buy.currency)return null;
 const rows=[
  {label:'Buy',value:buy},
  {label:'Sell',value:`${sell} in ${units[action.sell.currency].name}`},
  {label:'Treasury reference rate',value:`${source.symbol}1 buys approximately ${rate}`},
 ];
 if(action.settlement_date&&/^\d{4}-\d{2}-\d{2}$/.test(action.settlement_date)){
  const date=new Date(action.settlement_date+'T00:00:00Z');
   if(Number.isFinite(date.getTime())){
    const name=units[action.buy.currency].name;
    rows.push({label:`${name[0].toUpperCase()+name.slice(1)} available by`,value:new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'}).format(date)+' (proposed)'});
   }
 }
 return rows;
}

export function timingNote(action?:RecommendedAction|null){
  if(action&&'display_rows' in action)return action.kind==='forward'?'Illustrative forward terms only; bank pricing and delivery are unconfirmed.':action.kind==='portfolio_change'?'Modeled cancellation or allocation only; Treasury must confirm approvals, actual costs and combined feasibility.':action.kind==='conversion'||action.kind==='allocation_plan'?'Sheet prices and timing are indicative; confirm live prices, bank delivery and the combined plan before trading.':null;
 if(!action?.settlement_date)return null;
 const days=action.timing?.calendar_days_before_first_shortfall;
 let margin='';
 if(days!==null&&days!==undefined){
  margin=days>0?`${days} calendar ${days===1?'day':'days'} before the earliest modeled shortfall. `:
   days===0?'No advance margin before the earliest modeled shortfall. ':'After the earliest modeled shortfall. ';
 }
 const weekend=action.timing?.settlement_is_weekday===false?'The proposed date is on a weekend. ':'';
 return margin+weekend+'Bank delivery timing is unconfirmed.';
}
