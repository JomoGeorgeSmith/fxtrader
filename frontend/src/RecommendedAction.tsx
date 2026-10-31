import {actionRows,timingNote,type RecommendedAction as Action} from './actionDisplay';

export default function RecommendedAction({action,expectedResult}:{action?:Action|null;expectedResult?:string|null}){
 const rows=actionRows(action);
 if(!rows)return null;
 return <>
  <div className="recommended-action">
   <strong className="action-heading">Recommended action</strong>
   <dl>{rows.map(row=><div className="action-row" key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
    {timingNote(action)&&<p className="action-timing">{timingNote(action)}</p>}
  </div>
  {expectedResult&&<div className="action-result"><strong className="action-heading">Expected result</strong><p>{expectedResult}</p></div>}
 </>;
}
