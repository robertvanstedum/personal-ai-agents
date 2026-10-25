import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,copyFileSync,readFileSync,existsSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {toolFactory,runSealer} from '../../services/mc_operations/plugins/minimoi-tools/index.mjs';
const json=x=>new Response(JSON.stringify(x),{status:200,headers:{'content-type':'application/json'}});
const kit=process.env.MC_CAREER_TEST_KIT;
for(const mode of ['ok','cancel','refused','lost_callback','unconfigured'])test(`synthetic career ${mode}`,{skip:!kit},async t=>{
 const root=mkdtempSync('/private/tmp/mc-career-plugin-');t.after(()=>rmSync(root,{recursive:true,force:true}));
 for(const f of ['career_trial.py','career_trial_recipe.py','artifact.py'])copyFileSync(new URL('../../scripts/mc_host/'+f,import.meta.url),join(root,f));
 // Staged public kit may be inside _working for tests; copy it to disposable fixed operator path.
 const {cpSync}=await import('node:fs');cpSync(kit,join(root,'kit'),{recursive:true});
 const manifest=JSON.parse(readFileSync(new URL('../../core/mc_tools/career_trial_manifest.json',import.meta.url)));
 const payload=Buffer.from(JSON.stringify({career_trial:manifest})),sha=createHash('sha256').update(payload).digest('hex');
 const op='12345678-1234-4234-8234-123456789abc',challenge='22345678-1234-4234-8234-123456789abc',nonce='b'.repeat(64),sealer=runSealer(),control=new AbortController(),phases=[];
 const sessionKey='agent:mc-chat:openai-user:guild-mc:'+'a'.repeat(32),name='minimoi_career_trial';
 const factory=toolFactory({endpoint:'http://127.0.0.1:5011/internal/mc/v1/tools',token:'v'.repeat(40),tokenFile:join(root,'token'),agents:['mc-chat'],tools:[name],sealer,
 hostPython:process.env.MC_HOST_TEST_PYTHON,hostArtifactHelper:join(root,'artifact.py'),hostArtifactRoot:root,
 hostCareerHelper:mode==='unconfigured'?undefined:join(root,'career_trial.py'),hostCareerKit:join(root,'kit'),
 fetchImpl:async(url,init)=>{
  if(url.endsWith('/instance'))return json({status:'recorded'});
  const r=JSON.parse(init.body).artifact_request;
  if(!r)return json({status:'done',code:'artifact_prepared',message:'private',data:{artifact_descriptor:{kind:'career_trial',challenge_id:challenge,operation_id:op,nonce,expires_at:Date.now()/1000+100,mode:'create',root,sha256:sha,bytes:payload.length,chunk_bytes:4096}}});
  phases.push(r.phase);
  if(r.phase==='chunk'){
   if(mode==='cancel')control.abort();
   return json({status:'done',code:'artifact_chunk',message:'private',data:{offset:r.offset,bytes:payload.length,sha256:sha,chunk:payload.subarray(r.offset,r.offset+4096).toString('base64')}});
  }
  if(r.phase==='start'){
   if(mode==='refused')return json({status:'refused',code:'cancelled',message:'Stopped',data:{}});
   assert.equal(existsSync(join(root,'mc-artifacts')),false);
   return json({status:'done',code:'artifact_execute',message:'private',data:{mode:'create',sha256:sha,expires_at:Date.now()/1000+20}});
  }
  assert.equal(r.phase,'complete');assert.equal(r.result.verified,true);assert.deepEqual(r.result.files,manifest.expected);
  if(mode==='lost_callback')throw Error('reply lost');
  return json({status:'done',code:'artifact_verified',message:'Synthetic trial verified',data:{operation_id:op}});
 }});
 const tools=factory({agentId:'mc-chat',sessionKey,workspaceDir:join(root,'workspace'),fsPolicy:{workspaceOnly:true}});
 if(mode==='unconfigured'){assert.equal(tools.length,0);return;}
 const result=await tools[0].execute('career_call',sealer.seal({fixture:'synthetic-v1'},{runId:'chatcmpl_12345678-1234-4234-8234-123456789abc',toolCallId:'career_call',agentId:'mc-chat',sessionKey}),control.signal);
 if(mode==='ok')assert.equal(result.details.code,'artifact_verified');
 if(mode==='lost_callback')assert.equal(result.details.status,'unknown');
 if(['cancel','refused'].includes(mode))assert.equal(existsSync(join(root,'mc-artifacts')),false);
 if(['ok','lost_callback'].includes(mode))assert.equal(JSON.parse(readFileSync(join(root,'mc-artifacts',op,'checks.json'))).fixture,'synthetic-v1');
 assert.ok(!JSON.stringify(result).includes(nonce));assert.ok(phases.filter(p=>p==='start').length<=1);
});
