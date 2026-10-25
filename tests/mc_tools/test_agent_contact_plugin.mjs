import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {toolFactory,runSealer} from '../../services/mc_operations/plugins/minimoi-tools/index.mjs';
const token='t'.repeat(40), sessionKey='agent:mc-chat:openai-user:guild-mc:'+'a'.repeat(32);
const runId='chatcmpl_12345678-1234-4234-8234-123456789abc';
const tools=['minimoi_agent_contact','minimoi_agent_status','minimoi_agent_watch'];
const json=body=>new Response(JSON.stringify(body),{status:200,headers:{'content-type':'application/json'}});
let serial=0;
function fixture(){
  const sealer=runSealer(),calls=[];
  const make=toolFactory({endpoint:'http://127.0.0.1:5011/internal/mc/v1/tools',token,tokenFile:'/secure/executor.json',
    agents:['mc-chat'],tools,sealer,instance:(++serial).toString(16).padStart(32,'e'),fetchImpl:async(url,init)=>{
      if(url.endsWith('/instance'))return json({status:'recorded'});
      calls.push({url,body:JSON.parse(init.body),headers:init.headers});
      return json({status:'unknown',code:'agent_contact_receipt',message:'Request dispatched; pickup unverified.',
                   data:{request_ref:'11111111-1111-4111-8111-111111111111',state:'dispatched',automatic_retry:false}});
    }});
  const ctx={agentId:'mc-chat',sessionKey,workspaceDir:'/synthetic/workspace',fsPolicy:{workspaceOnly:true}};
  return {make,ctx,calls,sealer};
}
test('existing chat request uses sealed admission and displays honest receipt',async()=>{
  const f=fixture(),tool=f.make(f.ctx).find(t=>t.name==='minimoi_agent_contact');
  const args={target:'codex-owner',mode:'status_request'};
  const out=await tool.execute('call1',f.sealer.seal(args,{runId,toolCallId:'call1',agentId:'mc-chat',sessionKey}));
  assert.equal(f.calls.length,1);
  assert.deepEqual(f.calls[0].body,{arguments:args,offered_tools:tools});
  assert.equal(out.details.status,'unknown');
  assert.match(out.content.map(c=>c.text).join("\n"),/request_ref/);
  assert.match(out.content.map(c=>c.text).join("\n"),/untrusted data/);
  assert.ok(!JSON.stringify(out).includes(token));
});
test('freeform relay or unsealed request cannot reach the listener',async()=>{
  const f=fixture(),tool=f.make(f.ctx).find(t=>t.name==='minimoi_agent_contact');
  for(const args of [{target:'codex-owner',mode:'relay_owner_note'},{target:'codex-owner',mode:'status_request',message:'build X'}]){
    const out=await tool.execute('call1',f.sealer.seal(args,{runId,toolCallId:'call1',agentId:'mc-chat',sessionKey}));
    assert.equal(out.details.status,'refused');
  }
  const out=await tool.execute('call1',{target:'codex-owner',mode:'status_request'});
  assert.equal(out.details.status,'refused');assert.equal(f.calls.length,0);
  assert.equal(f.make({...f.ctx,fsPolicy:{workspaceOnly:false}}),null);
});
test('approved handoff seals only fixed registry selector, never caller message',async()=>{
  const f=fixture(),tool=f.make(f.ctx).find(t=>t.name==='minimoi_agent_contact');
  const args={target:'claude-career',mode:'approved_owner_handoff',approved_handoff_ref:'career-original'};
  await tool.execute('fixed',f.sealer.seal(args,{runId,toolCallId:'fixed',agentId:'mc-chat',sessionKey}));
  assert.deepEqual(f.calls[0].body.arguments,args);
  for(const invalid of [{...args,message:'new work'},{...args,approved_handoff_ref:'../../path'}]){
    const result=await tool.execute('bad',f.sealer.seal(invalid,{runId,toolCallId:'bad',agentId:'mc-chat',sessionKey}));
    assert.equal(result.details.status,'refused');
  }
  assert.equal(f.calls.length,1);
});

test('provider contact schema is an object without root combinators; catalogue stays canonical',()=>{
  const canonical=JSON.parse(readFileSync(new URL('../../services/mc_operations/plugins/minimoi-tools/tools.json',import.meta.url),'utf8'));
  const source=canonical.tools.find(t=>t.name==='minimoi_agent_contact').parameters;
  assert.equal(source.allOf.length,1);
  const f=fixture(),emitted=f.make(f.ctx).find(t=>t.name==='minimoi_agent_contact').parameters;
  assert.equal(emitted.type,'object');
  for(const key of ['allOf','oneOf','anyOf']) assert.ok(!Object.hasOwn(emitted,key));
  const {allOf,...object}=source;assert.deepEqual(emitted,object);
  // Projection is detached; a consumer cannot weaken execution's canonical schema.
  emitted.properties.mode.enum.push('relay_owner_note');
  assert.ok(!f.make(f.ctx).find(t=>t.name==='minimoi_agent_contact').parameters.properties.mode.enum.includes('relay_owner_note'));
  for(const tool of f.make(f.ctx))for(const key of ['allOf','oneOf','anyOf'])assert.ok(!Object.hasOwn(tool.parameters,key));
});
test('contact discriminator is enforced before HTTP despite provider projection',async()=>{
  const f=fixture(),tool=f.make(f.ctx).find(t=>t.name==='minimoi_agent_contact');
  for(const args of [
    {target:'claude-career',mode:'approved_owner_handoff'},
    {target:'codex-owner',mode:'status_request',approved_handoff_ref:'career-original'},
    {target:'codex-owner',mode:'clarification_request',approved_handoff_ref:'career-original'},
    {target:'claude-career',mode:'approved_owner_handoff',approved_handoff_ref:null},
    {target:'claude-career',mode:'approved_owner_handoff',approved_handoff_ref:''}
  ]){
    const out=await tool.execute('invalid',f.sealer.seal(args,{runId,toolCallId:'invalid',agentId:'mc-chat',sessionKey}));
    assert.equal(out.details.status,'refused');
  }
  assert.equal(f.calls.length,0);
  for(const args of [
    {target:'codex-owner',mode:'status_request'},
    {target:'codex-owner',mode:'clarification_request'},
    {target:'claude-career',mode:'approved_owner_handoff',approved_handoff_ref:'career-original'}
  ]) await tool.execute('valid',f.sealer.seal(args,{runId,toolCallId:'valid',agentId:'mc-chat',sessionKey}));
  assert.equal(f.calls.length,3);
});
