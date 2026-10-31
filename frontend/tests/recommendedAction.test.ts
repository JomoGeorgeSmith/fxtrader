import {test} from 'node:test';
import assert from 'node:assert/strict';
import {actionRows,timingNote,type RecommendedAction,type ScenarioAction} from '../src/actionDisplay.ts';
import {newSuggestionSounds} from '../src/sounds.ts';

const action:RecommendedAction={assessment_id:'plan.example',buy:{currency:'USD',amount:'1050000.00'},
 sell:{currency:'GBP',amount:'825749.34'},reference_rate:{source:'GBP',target:'USD',target_per_source:'1.271572327'},rate_basis:'treasury_sheet'};

test('shared portfolio cancellation stays visibly conditional',()=>{
 const scenario:ScenarioAction={assessment_id:'portfolio.example',kind:'portfolio_change',rate_basis:'not_applicable',display_rows:[
  {label:'Proposed change (Treasury approval required)',value:'cancel order TEST-ORDER'},
  {label:'Modeled avoided cost, not profit',value:'JMD 76,030.75'}]};
 assert.equal(actionRows(scenario)?.length,2);
 assert.match(timingNote(scenario)!,/Treasury must confirm approvals/);
 assert.equal(actionRows({...scenario,rate_basis:'treasury_sheet'}),null);
});

test('buy, sell and directional sheet rate are digestible without date or fees',()=>{
 assert.deepEqual(actionRows(action),[
  {label:'Buy',value:'US$1,050,000'},
  {label:'Sell',value:'£825,749.34 in British pounds'},
  {label:'Treasury reference rate',value:'£1 buys approximately US$1.2716'},
 ]);
});
test('only dated plans show proposed dollar arrival, not a rate-sheet timestamp',()=>{
 assert.deepEqual(actionRows({...action,settlement_date:'2026-11-13'})?.at(-1),{label:'US dollars available by',value:'13 November 2026 (proposed)'});
});
test('arrival margins and unconfirmed bank timing are explicit, not guarantees',()=>{
 const dated={...action,settlement_date:'2026-11-13',timing:{calendar_days_before_first_shortfall:1,earliest_modeled_shortfall:'2026-11-14',settlement_is_weekday:true,bank_delivery_confirmed:false as const}};
 assert.equal(timingNote(dated),'1 calendar day before the earliest modeled shortfall. Bank delivery timing is unconfirmed.');
 assert.match(timingNote({...dated,timing:{...dated.timing,calendar_days_before_first_shortfall:0}})!,/No advance margin/);
 assert.match(timingNote({...dated,timing:{...dated.timing,calendar_days_before_first_shortfall:-3}})!,/After the earliest/);
 assert.match(timingNote({...dated,timing:{...dated.timing,settlement_is_weekday:false}})!,/on a weekend/);
 assert.equal(timingNote(action),null);
 assert.equal(timingNote({...action,settlement_date:'2026-11-13'}),'Bank delivery timing is unconfirmed.');
});
test('historical cards without structured actions stay unchanged',()=>{
 assert.equal(actionRows(undefined),null);
 assert.equal(actionRows(null),null);
});
test('JMD and CAD amounts cannot be confused with US dollars',()=>{
 const rows=actionRows({...action,buy:{currency:'CAD',amount:'1000000'},sell:{currency:'JMD',amount:'115000000'},reference_rate:{source:'JMD',target:'CAD',target_per_source:'0.00869565'}});
 assert.equal(rows?.[0].value,'CA$1,000,000');
 assert.equal(rows?.[1].value,'J$115,000,000 in Jamaican dollars');
 assert.equal(rows?.[2].value,'J$1 buys approximately CA$0.0087');
});
test('invalid financial fields are not displayed as valid instructions',()=>{
 assert.equal(actionRows({...action,sell:{currency:'GBP',amount:'NaN'}}),null);
 assert.equal(actionRows({...action,buy:{currency:'USD',amount:'-1'}}),null);
 assert.equal(actionRows({...action,reference_rate:{...action.reference_rate,source:'USD'}}),null);
});
test('a changed recommended amount triggers the severity sound even with unchanged summary',()=>{
 const card={id:'funding-USD',severity:'critical',summary:'Cover the dollar gap.',recommended_action:action};
 assert.deepEqual(newSuggestionSounds([card],[{...card,recommended_action:{...action,sell:{...action.sell,amount:'826000'}}}]),['critical']);
 assert.deepEqual(newSuggestionSounds([card],[structuredClone(card)]),[]);
});

test('receipt matching and order cancellation show their own action, not a fictitious new trade',()=>{
 const receipt:ScenarioAction={assessment_id:'fx.receipt',kind:'receipt_match',rate_basis:'not_applicable',display_rows:[{label:'Use expected deposit',value:'USD 10,000.00 from REC-001'},{label:'Reduce planned purchase',value:'USD 10,000.00 on ORDER-01'}]};
 assert.deepEqual(actionRows(receipt),receipt.display_rows);
 assert.equal(timingNote(receipt),null);
 const cancel:ScenarioAction={...receipt,kind:'cancel_orders',display_rows:[{label:'Cancel planned conversions',value:'ORDER-01 and ORDER-02'}]};
 assert.deepEqual(actionRows(cancel),cancel.display_rows);
 assert.deepEqual(newSuggestionSounds([{id:'cancel',severity:'info',summary:'Avoid the extra conversions.',recommended_action:cancel}],[{id:'cancel',severity:'info',summary:'Avoid the extra conversions.',recommended_action:{...cancel,display_rows:[{label:'Cancel planned conversions',value:'ORDER-01 and ORDER-03'}]}}]),['info']);
});
test('forward terms cannot be presented as Treasury spot prices',()=>{
 const forward:ScenarioAction={assessment_id:'fx.forward',kind:'forward',rate_basis:'simulated_forward',settlement_date:'2026-11-13',display_rows:[{label:'Illustrative forward rate',value:'GBP 1 buys USD 1.2800'}]};
 assert.deepEqual(actionRows(forward),forward.display_rows);
 assert.match(timingNote(forward)!,/Illustrative forward terms only/);
 assert.equal(actionRows({...forward,rate_basis:'treasury_sheet'}),null);
});
test('a joint sale displays both destinations with payment dates and indicative conditions',()=>{
 const plan:ScenarioAction={assessment_id:'fx.allocation',kind:'allocation_plan',rate_basis:'treasury_sheet',display_rows:[
  {label:'Sell USD',value:'USD 1,197,579.30'},
  {label:'For CAD payment',value:'Sell USD 809,229.78 to buy CAD 1,100,000.00 by 2026-11-16 (TEST-CAD)'},
  {label:'For JMD payment',value:'Sell USD 388,349.52 to buy JMD 60,000,000.00 by 2026-11-16 (TEST-JMD)'},
  {label:'Indicative fees',value:'JMD 95,003.00'},
 ]};
 assert.deepEqual(actionRows(plan),plan.display_rows);
 assert.match(timingNote(plan)!,/confirm live prices, bank delivery and the combined plan/);
 assert.equal(actionRows({...plan,rate_basis:'not_applicable'}),null);
});
test('malformed or unrecognized scenario actions are not displayed',()=>{
 assert.equal(actionRows({assessment_id:'x',kind:'forward',rate_basis:'simulated_forward',display_rows:[]}),null);
 assert.equal(actionRows({assessment_id:'x',kind:'conversion',rate_basis:'treasury_sheet',display_rows:[{label:'',value:'x'}]}),null);
});
