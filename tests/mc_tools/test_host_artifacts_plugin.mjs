import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,copyFileSync,readFileSync,existsSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {toolFactory,runSealer} from '../../services/mc_operations/plugins/minimoi-tools/index.mjs';
const TOKEN='v'.repeat(40),RUN='chatcmpl_12345678-1234-4234-8234-123456789abc';
let sequence=0;
const json=b=>new Response(JSON.stringify(b),{status:200,headers:{'content-type':'application/json'}});
function fixture(t,mode='ok'){
 const root=mkdtempSync('/private/tmp/mc-artifact-plugin-');t.after(()=>rmSync(root,{recursive:true,force:true}));
 const helper=join(root,'artifact.py');copyFileSync(new URL('../../scripts/mc_host/artifact.py',import.meta.url),helper);
 const op='12345678-1234-4234-8234-123456789abc', nonce='b'.repeat(64),challenge='22345678-1234-4234-8234-123456789abc';
 const text='Unicode🙂\n'.repeat(800),payload=Buffer.from(JSON.stringify({files:[{name:'artifact.txt',text}]}));
 const sha=createHash('sha256').update(payload).digest('hex');const phases=[],sealer=runSealer(),control=new AbortController();
 const sessionKey='agent:mc-chat:openai-user:guild-mc:'+'a'.repeat(32);
 const args={};
 const factory=toolFactory({endpoint:'http://127.0.0.1:5011/internal/mc/v1/tools',token:TOKEN,tokenFile:join(root,'token'),
  agents:['mc-chat'],tools:['minimoi_conversation_export'],sealer,instance:(++sequence).toString(16).padStart(32,'e'),
  hostPython:process.env.MC_HOST_TEST_PYTHON||'/usr/bin/python3',hostArtifactHelper:helper,hostArtifactRoot:root,
  fetchImpl:async(url,init)=>{
   if(url.endsWith('/instance'))return json({status:'recorded'});
   assert.ok(Buffer.byteLength(init.body)<=16384);
   const b=JSON.parse(init.body), r=b.artifact_request;
   if(!r)return json({status:'done',code:'artifact_prepared',message:'private',data:{artifact_descriptor:{
    challenge_id:challenge,operation_id:op,nonce,expires_at:Date.now()/1000+100,mode:'create',root,
    sha256:sha,bytes:payload.length,chunk_bytes:4096}}});
   assert.equal(r.nonce,nonce);phases.push(r.phase);
   if(r.phase==='chunk'){
    if(mode==='cancel')control.abort();
    return json({status:'done',code:'artifact_chunk',message:'private',data:{offset:r.offset,bytes:payload.length,
      sha256:sha,chunk:(mode==='corrupt'?Buffer.alloc(Math.min(4096,payload.length-r.offset),42):payload.subarray(r.offset,r.offset+4096)).toString('base64')}});
   }
   if(r.phase==='start'){
    assert.equal(r.sha256,sha);assert.ok(phases.filter(x=>x==='chunk').length>1);
    assert.equal(existsSync(join(root,'mc-artifacts')),false);
    if(mode==='start_refused')return json({status:'refused',code:'tool_turn_not_live',message:'Stopped',data:{}});
    if(mode==='lost_start')throw Error('lost response');
    return json({status:'done',code:'artifact_execute',message:'private',data:{expires_at:Date.now()/1000+20,mode:'create',sha256:sha}});
   }
   assert.equal(r.phase,'complete');assert.equal(r.result.verified,true);
   if(mode==='completion_refused')return json({status:'refused',code:'tool_turn_stopped',message:'Nothing was done.',data:{}});
   return json({status:'done',code:'artifact_verified',message:'Artifact saved.',data:{operation_id:op,path:join(root,'mc-artifacts',op)}});
  }});
 const tool=factory({agentId:'mc-chat',sessionKey,workspaceDir:join(root,'workspace'),fsPolicy:{workspaceOnly:true}})[0];
 return {root,op,text,phases,execute:()=>tool.execute('artifact_call',sealer.seal(args,{runId:RUN,toolCallId:'artifact_call',agentId:'mc-chat',sessionKey}),control.signal)};
}

test('complete chunked payload verified before lease/start and one real create',async t=>{
 const f=fixture(t);const result=await f.execute();assert.equal(result.details.code,'artifact_verified');
 assert.equal(readFileSync(join(f.root,'mc-artifacts',f.op,'artifact.txt'),'utf8'),f.text);
 assert.ok(result.content.some(c=>c.text.includes(f.op)));assert.ok(!JSON.stringify(result).includes('b'.repeat(64)));
 assert.ok(!JSON.stringify(result).includes('Unicode'));
});
for(const mode of ['corrupt','cancel','start_refused','lost_start'])test(`${mode} does not invoke writer`,async t=>{
 const f=fixture(t,mode);const result=await f.execute();assert.notEqual(result.details.code,'artifact_verified');
 assert.equal(existsSync(join(f.root,'mc-artifacts')),false);
 if(mode==='start_refused')assert.equal(result.details.status,'refused');
 assert.ok(!f.phases.includes('complete'));
 if(['corrupt','cancel'].includes(mode))assert.ok(!f.phases.includes('start'));
});

test('completion refusal after actual create reports uncertainty and recovery, never no effect',async t=>{
 const f=fixture(t,'completion_refused');const result=await f.execute();
 assert.equal(readFileSync(join(f.root,'mc-artifacts',f.op,'artifact.txt'),'utf8'),f.text);
 assert.equal(result.details.status,'unknown');assert.equal(result.details.code,'artifact_unconfirmed');
 assert.ok(result.content[0].text.includes('minimoi_artifact_recover'));
 assert.ok(result.content[0].text.includes(f.op));assert.ok(!JSON.stringify(result).includes('Nothing was done'));
 assert.equal(f.phases.filter(p=>p==='start').length,1);
});
