import {ShieldCheck,Clock3,AlertTriangle,Info,ChevronDown} from 'lucide-react';
import './liquidity-metrics.css';

export type LiquidityMetric={id:string;name:string;horizon:string;value_pct:number;status:'good'|'normal'|'at-risk';normal_min_pct:number;good_min_pct:number;numerator:number;denominator:number;explanation:string;as_of:string;basis:string;inputs:{component:string;amount:number;description:string;_source:string}[]};
const format=(n:number)=>new Intl.NumberFormat('en',{maximumFractionDigits:0}).format(n);
const labels={'good':'Good','normal':'Normal','at-risk':'At risk'};

export default function LiquidityMetrics({metrics,updating}:{metrics?:LiquidityMetric[];updating:boolean}){
 return <section className="liquidity-section" aria-label="Liquidity and funding health"><div className="liquidity-heading"><span className="section-label"><ShieldCheck size={13}/> LIQUIDITY & FUNDING HEALTH</span><span>Total JMD equivalent · synthetic · demo bands {updating?'· updating':''}</span></div><div className="liquidity-cards">{['lcr','nsfr'].map(id=>{
  const metric=metrics?.find(m=>m.id===id);
  if(!metric)return <article className="liquidity-card" key={id}><div className="metric-heading"><strong>{id.toUpperCase()}</strong><span className="metric-tag">{id==='lcr'?'30-day stress':'One-year funding structure'}</span></div><div className="metric-value skeleton">—</div><p className="metric-description">Waiting for the new synthetic liquidity-input snapshot.</p></article>;
  const maximum=Math.max(metric.good_min_pct*1.35,metric.value_pct*1.08);
  const normal=metric.normal_min_pct/maximum*100,good=metric.good_min_pct/maximum*100;
  const Icon=metric.status==='good'?ShieldCheck:metric.status==='normal'?Info:AlertTriangle;
   return <details key={id} className={`liquidity-card ${metric.status}`}>
    <summary className="metric-summary" aria-label={`${metric.name}: ${metric.value_pct.toFixed(1)}%, ${labels[metric.status]}. Calculation details`}>
     <span className="metric-identity"><strong>{id.toUpperCase()}</strong><span className="metric-name">{metric.name}</span></span>
     <span className="metric-value">{metric.value_pct.toFixed(1)}<span>%</span></span>
     <span className={`metric-status ${metric.status}`}><Icon size={12}/>{labels[metric.status]}</span>
     <ChevronDown size={14} className="metric-chevron" aria-hidden="true"/>
    </summary>
    <div className="metric-details">
     <span className="metric-tag"><Clock3 size={11}/>{metric.horizon}</span>
     <div className="metric-gauge" role="meter" aria-label={`${id.toUpperCase()} demo status: ${labels[metric.status]}`} aria-valuenow={metric.value_pct} aria-valuemin={0} aria-valuemax={maximum} aria-valuetext={`${metric.value_pct.toFixed(1)} percent, ${labels[metric.status]}, demo thresholds only`} style={{background:`linear-gradient(to right, #e6b3a8 0% ${normal}%, #e8d39d ${normal}% ${good}%, #9bc5b7 ${good}% 100%)`}}><i style={{left:`${metric.value_pct/maximum*100}%`}}/></div>
     <div className="metric-bands"><span>At risk &lt;{metric.normal_min_pct}%</span><span>Normal {metric.normal_min_pct}–&lt;{metric.good_min_pct}%</span><span>Good ≥{metric.good_min_pct}%</span></div>
     <h4>How this is calculated</h4><p>{metric.explanation}</p><div className="metric-equation">JMD {format(metric.numerator)} ÷ JMD {format(metric.denominator)} × 100</div><ul>{metric.inputs.map(input=><li key={input.component}><strong>{input.component.replaceAll('_',' ')}</strong><span>JMD {format(input.amount)}</span><small>{input.description}<br/>{input._source}</small></li>)}</ul><p>{metric.basis}<br/>Inputs as of {new Date(metric.as_of).toLocaleString()}. Not an official regulatory compliance assessment.</p>
    </div>
   </details>
 })}</div><p className="metric-caveat">Synthetic institution-wide ratios, separate from operating cash. Demo bands only · expand a ratio for details.</p></section>
}
