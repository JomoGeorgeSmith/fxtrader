import {CalendarDays,FileCheck2,ArrowRightLeft,ChevronRight} from 'lucide-react';
import RecommendedAction from './RecommendedAction';
import type {RecommendedAction as Action} from './actionDisplay';
import {suggestionPreview} from './suggestionPreview';
import type {LiquiditySnapshot} from './liquidityAlerts';
import {safeNextMove} from './failedPlanNextMove';

type Card={title:string;summary:string;severity:string;currency:string|null;finding_ids?:string[];action_id?:string|null;next_move?:string|null;decision_group?:string|null;recommended_action?:Action|null;expected_result?:string|null};
export default function GeneratedSuggestion({analysis,card}:{analysis:LiquiditySnapshot&{suggestions?:{decision_group?:string|null}[]};card:Card}){
 const preview=suggestionPreview(analysis,card);
 const tone=preview.tone;
 const nextMove=safeNextMove(card);
 const competing=card.decision_group&&(analysis.suggestions?.filter(s=>s.decision_group===card.decision_group).length??0)>1;
 return <div className={`editorial-preview generated-preview preview-tone-${tone}`}>
  <div className="editorial-preview-left">
   <div className="editorial-preview-kicker">{tone==='urgent'?'Action needed':tone==='payment'?'Payment planning':tone==='opportunity'?'Opportunity':'Worth a review'} <span>·</span> {card.currency??'Portfolio'} {preview.date&&<><span>·</span> {preview.date}</>}</div>
   <h3>{card.title}</h3><p className="editorial-preview-teaser">{card.summary}</p>
   {!!preview.metrics.length&&<div className="editorial-preview-metrics">{preview.metrics.map(metric=><div className="editorial-preview-metric" key={metric.label}><span>{metric.label}</span><strong className={metric.negative?'negative':''}>{metric.value}</strong></div>)}</div>}
   {preview.date&&<div className="editorial-preview-date"><CalendarDays size={16} aria-hidden="true"/><span>{preview.dateLabel}: <strong>{preview.date}</strong></span></div>}
  </div>
  <div className="editorial-preview-action">
   <div className="editorial-preview-action-icon">{tone==='urgent'?<FileCheck2 size={22} aria-hidden="true"/>:<ArrowRightLeft size={22} aria-hidden="true"/>}</div>
   <div className="editorial-preview-action-body"><strong className="editorial-preview-action-label">{competing?'Alternative · choose one':nextMove?'Suggested next move':'Analyst insight'}</strong><p className="editorial-preview-decision">{nextMove??'No next move was provided with this suggestion. Open the insight for the analyst’s reasoning.'}</p>{competing&&<p className="editorial-preview-outcome">Competes with another {card.decision_group?.replaceAll('_',' ')} option. Do not take both or add separately tested outcomes.</p>}<RecommendedAction action={card.recommended_action} expectedResult={card.expected_result}/></div>
   <ChevronRight className="editorial-preview-arrow" size={21} aria-hidden="true"/>
  </div>
 </div>;
}
