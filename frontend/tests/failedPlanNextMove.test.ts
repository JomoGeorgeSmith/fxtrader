import test from 'node:test';
import assert from 'node:assert/strict';
import {safeNextMove} from '../src/failedPlanNextMove.ts';

test('saved failed plan cannot display an AI-authored execute instruction',()=>{
 const next=safeNextMove({action_id:'plan.failed',recommended_action:null,next_move:'Ask Treasury to execute the USD sale'});
 assert.match(next!,/^Do not execute this tested plan/);
 assert.match(next!,/another funding source/);
});

test('supported and informational next moves remain unchanged',()=>{
 assert.equal(safeNextMove({action_id:'plan.passing',recommended_action:{kind:'conversion'},next_move:'Confirm this proposal with Treasury'}),'Confirm this proposal with Treasury');
 assert.equal(safeNextMove({next_move:'Reconcile USD exposure'}),'Reconcile USD exposure');
});
