import test from 'node:test';
import assert from 'node:assert/strict';
import {reviewNotes} from '../src/reviewNotes.ts';

test('only selected insight warnings are shown as insight-specific',()=>{
 const analysis={analysis_review_warnings:['Overview wording'],validation_warnings:['CAD exposure','Overview wording']};
 assert.deepEqual(reviewNotes(analysis,{review_warnings:['CAD exposure']}),{
  insight:['CAD exposure'],snapshot:['Overview wording'],legacy:false,
 });
 assert.deepEqual(reviewNotes(analysis,{review_warnings:[]}).insight,[]);
});

test('empty scoped overview does not fall back to all warnings',()=>{
 assert.deepEqual(reviewNotes({analysis_review_warnings:[],validation_warnings:['Other insight issue']},{}),{
  insight:[],snapshot:[],legacy:false,
 });
});

test('legacy warnings are retained without guessing which insight they affect',()=>{
 assert.deepEqual(reviewNotes({validation_warnings:['Earlier unscoped warning']},{}),{
  insight:[],snapshot:['Earlier unscoped warning'],legacy:true,
 });
 assert.deepEqual(reviewNotes({}),{insight:[],snapshot:[],legacy:true});
});

test('pinned snapshot notes do not leak from a newer analysis',()=>{
 const old={analysis_review_warnings:['Old overview'],validation_warnings:['Old insight','Old overview']};
 const newer={analysis_review_warnings:['New overview']};
 reviewNotes(newer,{review_warnings:['New insight']});
 assert.deepEqual(reviewNotes(old,{review_warnings:['Old insight']}),{
  insight:['Old insight'],snapshot:['Old overview'],legacy:false,
 });
});
