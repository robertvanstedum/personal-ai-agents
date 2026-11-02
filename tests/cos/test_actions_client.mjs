import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {executeActions} from '../../docker/cos-agent-a/plugins/cos-actions/client.mjs';
test('replay keeps operation identity/time across calls and timeout; changed arguments refuse',async()=>{
 const cacheRoot=await mkdtemp(join(tmpdir(),'actions-'));const sent=[];
 const deps={cacheRoot,readToken:async()=>'test-token',fetch:async(url,options)=>{sent.push(options.body);if(sent.length===1)throw new Error('timeout');return {json:async()=>({result:{outcome:'accepted'}})}}};
 try {
  const args={operation:'open',title:'Example',kind:'briefing'};
  await assert.rejects(executeActions('call-1',args,deps),/timeout/);
  await executeActions('call-1',args,deps);
  assert.equal(sent[0],sent[1]);
  await assert.rejects(executeActions('call-1',{...args,title:'Changed'},deps),/reused/);
  assert.equal(sent.length,2);
  await executeActions('call-2',args,deps);assert.notEqual(sent[1],sent[2]);
 }finally{await rm(cacheRoot,{recursive:true,force:true});}
});
