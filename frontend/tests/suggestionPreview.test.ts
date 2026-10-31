import test from 'node:test';
import assert from 'node:assert/strict';
import {suggestionPreview} from '../src/suggestionPreview.ts';

const snapshot=(usdAvailable:number)=>({positions:[
 {currency:'USD',available:usdAvailable,minimum:1000000,projected_low:usdAvailable-3400000},
 {currency:'CAD',available:49980000,minimum:200000,projected_low:-850000}],forecast:[
 {currency:'USD',date:'2026-10-29',balance:usdAvailable-200000},
 {currency:'USD',date:'2026-11-17',balance:usdAvailable-3400000},
 {currency:'CAD',date:'2026-10-29',balance:49830000},
 {currency:'CAD',date:'2026-11-18',balance:-850000}]});

test('new cards derive their numbers and dates from their own saved run',()=>{
 const card={currency:'USD',severity:'critical',finding_ids:['liquidity-USD'],next_move:'Ask Treasury to confirm funding.'};
 const first=suggestionPreview(snapshot(300000),card);
 const changed=suggestionPreview(snapshot(20300000),card);
 assert.equal(first.tone,'urgent');
 assert.equal(first.date,'Oct 29, 2026');
 assert.equal(first.metrics[1].label,'Forecast low · Nov 17, 2026');
 assert.equal(first.metrics[1].value,'−USD 3,100,000');
 assert.equal(changed.metrics.length,0);
 const cad=suggestionPreview(snapshot(20300000),{currency:'CAD',severity:'critical',finding_ids:['liquidity-CAD']});
 assert.equal(cad.tone,'payment');
 assert.equal(cad.metrics[1].value,'−CAD 850,000');
 assert.equal(cad.date,'Nov 18, 2026');
});
test('portfolio and uncited cards do not borrow another currency’s cash figures',()=>{
 const data=snapshot(300000);
 assert.equal(suggestionPreview(data,{currency:null,severity:'warning',finding_ids:['stale-USD']}).metrics.length,0);
 assert.equal(suggestionPreview(data,{currency:'CAD',severity:'warning',finding_ids:['stale-CAD']}).metrics.length,0);
});
test('an opening balance below the demo cushion is urgent even before daily flows',()=>{
 const data={positions:[{currency:'USD',available:300000,minimum:1000000,projected_low:300000}],forecast:[{currency:'USD',date:'2026-10-29',balance:1400000}]};
 assert.equal(suggestionPreview(data,{currency:'USD',severity:'critical',finding_ids:['liquidity-USD']}).tone,'urgent');
});
