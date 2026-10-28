import {test} from 'node:test';
import assert from 'node:assert/strict';
import {melodies,newSuggestionSounds} from '../src/sounds.ts';

const info={id:'rate-GBP',severity:'info',summary:'Rate moved 3%'};
const warning={id:'reserve-USD',severity:'warning',summary:'Reserve short by 40K'};
test('unchanged suggestions and reordered cards stay silent',()=>{
 assert.deepEqual(newSuggestionSounds([info,warning],[warning,info]),[]);
});
test('new cards use the matching severity sound',()=>{
 assert.deepEqual(newSuggestionSounds([info],[info,warning]),['warning']);
});
test('changed severity or amount plays a fresh sound',()=>{
 assert.deepEqual(newSuggestionSounds([warning],[{...warning,severity:'critical'}]),['critical']);
 assert.deepEqual(newSuggestionSounds([warning],[{...warning,summary:'Reserve short by 50K'}]),['warning']);
});
test('removed cards and unsupported severity do not make sound',()=>{
 assert.deepEqual(newSuggestionSounds([info],[]),[]);
 assert.deepEqual(newSuggestionSounds([],[{...info,severity:'unknown'}]),[]);
});
test('each level has a distinct short melody with safe audible notes',()=>{
 assert.equal(new Set(Object.values(melodies).map(m=>JSON.stringify(m))).size,3);
 for(const melody of Object.values(melodies))for(const tone of melody){
  assert(tone.note>200&&tone.note<4000);
  assert(tone.at+tone.length<1);
 }
});
