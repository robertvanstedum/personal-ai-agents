import test from 'node:test';
import assert from 'node:assert/strict';
import {toolFactory,runSealer} from '../../services/mc_operations/plugins/minimoi-tools/index.mjs';
const json=body=>new Response(JSON.stringify(body),{status:200,headers:{'content-type':'application/json'}});
const sessionKey='agent:mc-chat:openai-user:guild-mc:'+'a'.repeat(32),runId='chatcmpl_12345678-1234-4234-8234-123456789abc';
// #594, Dev 14 Nov: with only the message, MC saw citations and no passage text, then tried to host-read Vault paths.
test('vault search hands the model each passage text, labelled as untrusted recorded data',async()=>{
 const sealer=runSealer(),calls=[];
 const factory=toolFactory({endpoint:'http://127.0.0.1:5011/internal/mc/v1/tools',token:'t'.repeat(40),tokenFile:'/secure/token',agents:['mc-chat'],tools:['minimoi_vault_search'],instance:'a'.repeat(32),sealer,fetchImpl:async(url,init)=>{
 if(url.endsWith('/instance'))return json({status:'recorded'});
 calls.push(JSON.parse(init.body));
 return json({status:'done',code:'vault_read',message:'From the Vault',data:{count:1,results:[{source:'Codex',date:'2026-11-10',tier:'raw',cite:'sessions-raw/x/main.md:3-5',passage:'the shop probe timed out behind the proxy'}]}});}});
 const tool=factory({agentId:'mc-chat',sessionKey,workspaceDir:'/synthetic',fsPolicy:{workspaceOnly:true}})[0];
 const out=await tool.execute('call1',sealer.seal({query:'larkspur shop down'},{runId,toolCallId:'call1',agentId:'mc-chat',sessionKey}));
 assert.equal(calls.length,1);
 const text=out.content.map(c=>c.text).join('\n');
 assert.match(text,/the shop probe timed out behind the proxy/);
 assert.match(text,/Codex/);assert.match(text,/2026-11-10/);assert.match(text,/untrusted/);
});
