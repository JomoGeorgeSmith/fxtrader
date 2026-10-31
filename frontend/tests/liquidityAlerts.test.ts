import test from 'node:test';
import assert from 'node:assert/strict';
import {liquidityAlerts,materialLiquidityWorsening,prioritizeSuggestions} from '../src/liquidityAlerts.ts';

const snapshot=(balance:number)=>({positions:[{currency:'USD',available:balance-100000,minimum:1000000,projected_low:balance-3500000},{currency:'CAD',available:49980000,minimum:200000,projected_low:-850000}],forecast:[{currency:'USD',date:'2026-10-29',balance:balance-100000},{currency:'CAD',date:'2026-11-18',balance:-850000}]});
test('USD deterioration from 5.4m through 400k to 10k is visible ahead of CAD',()=>{
 const first=snapshot(5400000),middle=snapshot(400000),last=snapshot(10000);
 assert.equal(liquidityAlerts(first).find(a=>a.currency==='USD'),undefined);
 const alert=liquidityAlerts(last,middle)[0];
 assert.equal(alert.currency,'USD');assert.equal(alert.available,-90000);
 assert.equal(alert.gap,4490000);assert.equal(alert.increase,390000);
 assert.equal(alert.firstBreach,'2026-10-29');
 assert.deepEqual(materialLiquidityWorsening(last,middle).map(a=>a.currency),['USD']);
 assert.deepEqual(materialLiquidityWorsening(middle,first).map(a=>a.currency),['USD']);
 assert.deepEqual(materialLiquidityWorsening(last,last),[]);
});
test('failed analyst output does not affect calculated alerts',()=>{
 assert.equal(liquidityAlerts(snapshot(10000)).length,2);
});
test('imminent USD card leads CAD without creating or rewriting an analyst suggestion',()=>{
 const cards=[{id:'cad',finding_ids:['liquidity-CAD']},{id:'rates',finding_ids:[]},{id:'usd',finding_ids:['liquidity-USD']}];
 assert.deepEqual(prioritizeSuggestions(cards,snapshot(10000)).map(c=>c.id),['usd','cad','rates']);
 assert.deepEqual(prioritizeSuggestions(cards,snapshot(5400000)).map(c=>c.id),['cad','rates','usd']);
 assert.deepEqual(prioritizeSuggestions([{id:'cad',finding_ids:['liquidity-CAD']}],snapshot(10000)).map(c=>c.id),['cad']);
});
