import type {EditorialCopy} from './editorialCopy';
import {CalendarDays,FileCheck2,ArrowRightLeft,ChevronRight} from 'lucide-react';

export default function EditorialSuggestion({copy,compact=false}:{copy:EditorialCopy;compact?:boolean}){
 if(compact)return <div className={`editorial-preview editorial-preview-${copy.currency.toLowerCase()}`}>
  <div className="editorial-preview-left">
   <div className="editorial-preview-kicker">{copy.currency==='USD'?'Action needed':'Payment planning'} <span>·</span> {copy.currency} <span>·</span> {copy.currency==='USD'?'Oct 29':'Nov 18'}</div>
   <h3>{copy.title}</h3><p className="editorial-preview-teaser">{copy.teaser}</p>
   <div className="editorial-preview-metrics">{copy.metrics.map(metric=><div className="editorial-preview-metric" key={metric.label}><span>{metric.label}</span><strong className={metric.negative?'negative':''}>{metric.value}</strong></div>)}</div>
   <div className="editorial-preview-date"><CalendarDays size={16} aria-hidden="true"/><span>{copy.dateLabel}: <strong>{copy.date}</strong></span></div>
  </div>
  <div className="editorial-preview-action">
   <div className="editorial-preview-action-icon">{copy.currency==='USD'?<FileCheck2 size={22} aria-hidden="true"/>:<ArrowRightLeft size={22} aria-hidden="true"/>}</div>
   <div className="editorial-preview-action-body"><strong className="editorial-preview-action-label">Suggested next move</strong><p className="editorial-preview-decision">{copy.decision}</p><p className="editorial-preview-outcome">{copy.outcome}</p></div>
   <ChevronRight className="editorial-preview-arrow" size={21} aria-hidden="true"/>
  </div>
 </div>;
 return <div className="editorial-card-copy">
  <p className="editorial-why">{copy.why}</p>
  <section className="editorial-decision"><strong>Suggested next move · ask Treasury</strong><p>{copy.next}</p><p>{copy.fallback}</p></section>
  {copy.tested&&<section className="editorial-test"><strong>What did the analyst test instead?</strong><p>{copy.tested}</p></section>}<div className="editorial-math"><strong>Why this amount?</strong><span>{copy.math}</span></div>
  <p className="editorial-policy">{copy.policy}</p>
  <small className="editorial-label">Edited display of the saved synthetic analysis · original wording and audit record are preserved</small>
 </div>;
}
