import test from 'node:test';
import assert from 'node:assert/strict';
import {failureExplanation} from '../src/analystFailure.ts';

test('explains a bad citation without turning an older recommendation into a current one',()=>{
 const explanation=failureExplanation({suggestion_origin:'unavailable',analyst_audit:{validation_attempts:[{error:'Unknown fact reference: plan.fb5ee6e7d19b.scenarios'}]}});
 assert.match(explanation,/citation could not be verified/);
 assert.match(explanation,/no recommendations/);
});
test('does not claim all failures were citation failures',()=>{
 assert.match(failureExplanation({suggestion_origin:'unavailable',analyst_audit:{validation_attempts:[{error:'Model service unavailable'}]}}),/evidence or format checks/);
 assert.match(failureExplanation({suggestion_origin:'unavailable'}),/could not publish/);
 assert.equal(failureExplanation({suggestion_origin:'analyst'}),'');
});
