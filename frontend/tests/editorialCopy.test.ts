import test from 'node:test';
import assert from 'node:assert/strict';
import {editorialCopy} from '../src/editorialCopy.ts';

const saved={id:'fe158f659600487aaabe104893d8a198',suggestion_origin:'analyst'};
test('approved display copy explains demo thresholds and rejected USD test',()=>{
 const usd=editorialCopy(saved,{id:'usd_cash_shortfall-USD'});
 assert.match(usd!.why,/demo operating-cash cushion is USD 1 million/);
 assert.match(usd!.tested!,/earliest simulated arrival was Nov 2/);
 assert.match(usd!.testSummary!,/synthetic currency-exposure limit fails/);
 assert.match(usd!.tested!,/synthetic currency-exposure limit/);
 assert.match(usd!.policy,/not BOJ requirements/);
 assert.match(usd!.fallback,/USD 1.4 million gap/);
 assert.equal(usd!.metrics[1].label,'Forecast low · Nov 17');
 assert.equal(usd!.date,'Oct 29, 2026');
 assert.match(usd!.outcome,/modeled low reaches the USD 1M demo cushion/);
 assert.doesNotMatch(usd!.outcome,/1\.1M/);
});
test('CAD copy flags the weekend test rather than presenting an approved trade',()=>{
 const cad=editorialCopy(saved,{id:'cad_cash_shortfall-CAD'});
 assert.match(cad!.title,/Nov 18 CAD payment/);
 assert.match(cad!.fallback,/Saturday/);
 assert.match(cad!.policy,/not a BOJ requirement/);
 assert.equal(cad!.metrics[1].value,'CAD 50.48M');
 assert.equal(cad!.date,'Nov 18, 2026');
});
test('no saved copy is applied to newer, failed, or unrelated runs',()=>{
 assert.equal(editorialCopy({...saved,id:'new-run'},{id:'usd_cash_shortfall-USD'}),null);
 assert.equal(editorialCopy({...saved,suggestion_origin:'unavailable'},{id:'usd_cash_shortfall-USD'}),null);
 assert.equal(editorialCopy(saved,{id:'rate_sheet_staleness-portfolio'}),null);
});
