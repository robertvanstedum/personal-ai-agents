import test from 'node:test';
import assert from 'node:assert/strict';
import {toolFactory,runSealer} from '../../services/mc_operations/plugins/minimoi-tools/index.mjs';
const json=body=>new Response(JSON.stringify(body),{status:200,headers:{'content-type':'application/json'}});
const sessionKey='agent:mc-chat:openai-user:guild-mc:'+'a'.repeat(32),runId='chatcmpl_12345678-1234-4234-8234-123456789abc';
test('recall is sealed, offered, validated and visible as untrusted data to the model',async()=>{
 const sealer=runSealer(),calls=[];
 const factory=toolFactory({endpoint:'http://127.0.0.1:5011/internal/mc/v1/tools',token:'t'.repeat(40),tokenFile:'/secure/token',agents:['mc-chat'],tools:['minimoi_recall'],instance:'a'.repeat(32),sealer,fetchImpl:async(url,init)=>{
 if(url.endsWith('/instance'))return json({status:'recorded'});
 calls.push(JSON.parse(init.body));return json({status:'done',code:'recall_read',message:'Read',data:{items:[{text:'historical synthetic marker'}],untrusted_data:true,complete:false,continuation:'next'}});}});
 const tool=factory({agentId:'mc-chat',sessionKey,workspaceDir:'/synthetic',fsPolicy:{workspaceOnly:true}})[0];
 const invoke=args=>tool.execute('call1',sealer.seal(args,{runId,toolCallId:'call1',agentId:'mc-chat',sessionKey}));
 for(const args of [{action:'find'},{action:'open',query:'x'},{action:'find',query:'x',conversation_ref:'c-aaaaaaaaaaaa'}])assert.equal((await invoke(args)).details.status,'refused');
 assert.equal(calls.length,0);
 const out=await invoke({action:'find',query:'marker'});
 assert.deepEqual(calls[0].offered_tools,['minimoi_recall']);
 const text=out.content.map(c=>c.text).join('\n');assert.match(text,/historical synthetic marker/);assert.match(text,/untrusted/);assert.match(text,/continuation/);
});
