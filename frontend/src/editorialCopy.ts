/** User-approved wording for one saved synthetic snapshot. Never rewrites its audit or future runs. */
export type EditorialCopy={title:string;teaser:string;currency:'USD'|'CAD';dateLabel:string;date:string;metrics:{label:string;value:string;negative?:boolean}[];decision:string;outcome:string;titleDetail?:string;why:string;next:string;fallback:string;tested?:string;testSummary?:string;math:string;policy:string};

const RUN='fe158f659600487aaabe104893d8a198';
const approved:Record<string,EditorialCopy>={
 'usd_cash_shortfall-USD':{
  title:'Resolve USD 4.1M operating-cash cushion gap',
  teaser:'Cash is already below our app’s demo operating cushion. Payments deepen the shortfall by Nov 17.',
  currency:'USD',dateLabel:'First modeled breach',date:'Oct 29, 2026',
  metrics:[{label:'Available after holds',value:'USD 300,000'},{label:'Forecast low · Nov 17',value:'−USD 3,100,000',negative:true},{label:'Demo operating cushion',value:'USD 1,000,000'}],
  decision:'Ask Treasury whether USD 4.1M can be released from the USD reserve into operating cash before the Oct 29 payments—and who can authorize it.',
  outcome:'If credited in time and Expected receipts arrive, the modeled low reaches the USD 1M demo cushion. If not, seek another approved USD source or renegotiate payment timing; without Expected receipts, a USD 1.4M cushion gap remains.',
  why:'The USD account has USD 300,000 available after holds. Our app’s demo operating-cash cushion is USD 1 million, so we start USD 700,000 below it. After the recorded flows on Oct 29, cash is forecast at USD 100,000. By Nov 17, a committed USD 2.2 million payment helps push the forecast low to negative USD 3.1 million.',
  next:'Ask Treasury now whether it is allowed to move USD 4.1 million from the USD reserve into the operating account, and whether it can arrive before the Oct 29 payments. If both are confirmed and Expected receipts arrive, the modeled low would rise from negative USD 3.1 million to the USD 1 million demo operating-cash cushion.',
  fallback:'If it cannot arrive in time, seek another approved source of USD or negotiate which payments can move. Confirm the Expected receipts too: if none arrive, this transfer still leaves a USD 1.4 million gap below the demo cushion.',
  tested:'The analyst tested buying USD 4.1 million with about JMD 651.9 million, assuming the dollars arrived Oct 29. This was not a workable plan in the simulation: earliest simulated arrival was Nov 2, after the first shortfall; it breached a synthetic currency-exposure limit; and operating cash starts USD 700,000 below the demo cushion before any trade arrives. These are simulated checks, not a bank quote or confirmed settlement.',
  testSummary:'Why not buy USD with JMD? The tested USD 4.1m purchase assumed Oct 29 arrival, but simulated delivery is no earlier than Nov 2 and a synthetic currency-exposure limit fails.',
  math:'USD 1m demo operating cushion − (negative USD 3.1m forecast low) = USD 4.1m. A hypothetical transfer would leave USD 1.36m of the recorded USD 5.46m reserve.',
  policy:'The USD 1m operating cushion and USD 0.5m protected-reserve floor are app-defined demo rules, not BOJ requirements or verified JN policy. Reserve-release permission and same-day credit have not been established.'
 },
 'cad_cash_shortfall-CAD':{
  title:'The Nov 18 CAD payment could drain cash below our demo cushion',
  teaser:'A committed CAD 50.48M payment and other flows put the forecast CAD balance below the app’s demo cushion.',
  currency:'CAD',dateLabel:'Payment and first breach',date:'Nov 18, 2026',
  metrics:[{label:'Available after holds',value:'CAD 49.98M'},{label:'Committed payment · Nov 18',value:'CAD 50.48M'},{label:'Forecast low · Nov 18',value:'−CAD 850,000',negative:true},{label:'Demo operating cushion',value:'CAD 200,000'}],
  decision:'Ask Treasury and the FX dealer whether buying CAD 1.05M with JMD can be approved, priced and delivered before the Nov 18 payment.',
  outcome:'If delivered in time, the modeled CAD low reaches the CAD 200k demo cushion. If not, arrange an approved CAD source or negotiate payment timing. The tested Nov 14 date is a Saturday and failed the simulated business-day check.',
  why:'The CAD account has CAD 49.98 million available after holds. A committed CAD 50.48 million payment on Nov 18, together with other recorded flows, produces a forecast low of negative CAD 850,000. Our app’s demo CAD operating-cash cushion is CAD 200,000, making the modeled gap CAD 1.05 million.',
  next:'Ask Treasury and the FX dealer whether a JMD-to-CAD purchase of CAD 1.05 million can be approved, priced and delivered before the Nov 18 payment. If it arrives in time at the modeled amount, the forecast CAD low rises to the CAD 200,000 demo cushion, including the modeled receipt stresses.',
  fallback:'If delivery cannot be confirmed, arrange another approved CAD source or negotiate the payment date. The tested Nov 14 settlement date is a Saturday and failed the simulated business-day check; do not treat that test as a ready-to-execute trade.',
  math:'CAD 200k demo operating cushion − (negative CAD 850k forecast low) = CAD 1.05m.',
  policy:'The CAD 200k operating-cash cushion is an app-defined demo rule, not a BOJ requirement or verified JN policy. Live price, approval and actual delivery remain unconfirmed.'
 }
};

export function editorialCopy(run:{id:string;suggestion_origin?:string},suggestion:{id:string}):EditorialCopy|null{
 return run.id===RUN&&run.suggestion_origin==='analyst'?approved[suggestion.id]??null:null;
}
