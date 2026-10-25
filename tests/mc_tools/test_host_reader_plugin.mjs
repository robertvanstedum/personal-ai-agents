import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, copyFileSync, writeFileSync, rmSync} from 'node:fs';
import {join} from 'node:path';
import {toolFactory, runSealer} from '../../services/mc_operations/plugins/minimoi-tools/index.mjs';

const TOKEN='z'.repeat(40), RUN='chatcmpl_12345678-1234-4234-8234-123456789abc';
const sessionKey='agent:mc-chat:openai-user:guild-mc:'+'a'.repeat(32);
const json = body => new Response(JSON.stringify(body), {status:200,headers:{'content-type':'application/json'}});
let count=0;
function fixture(t, mode='ok') {
  const dir=mkdtempSync('/private/tmp/mc-host-plugin-');t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const helper=join(dir,'reader.py');copyFileSync(new URL('../../scripts/mc_host/inspect.py',import.meta.url),helper);
  const source=join(dir,'ordinary.txt');writeFileSync(source,'ordinary host text outside workspace');
  const args={path:source,limit:10};const sealer=runSealer();const calls=[];
  const controller=new AbortController();
  const options={endpoint:'http://127.0.0.1:5011/internal/mc/v1/tools',token:TOKEN,tokenFile:join(dir,'executor.token'),
    agents:['mc-chat'],tools:['minimoi_host_read'],sealer,instance:(++count).toString(16).padStart(32,'d'),
    hostPython:process.env.MC_HOST_TEST_PYTHON || '/usr/bin/python3',hostHelper:helper,
    fetchImpl:async(url,init)=>{
      if(url.endsWith('/instance'))return json({status:'recorded'});
      const body=JSON.parse(init.body);calls.push(body);
      if(body.host_completion){
        assert.equal(body.host_completion.nonce,'a'.repeat(64));
        assert.equal(body.host_completion.result.status,'done');
        assert.equal(body.host_completion.result.data.text,'ordinary h');
        return json({status:'done',code:'host_read',message:body.host_completion.result.data.text,data:{}});
      }
      if(mode==='refused')return json({status:'refused',code:'tool_turn_not_live',message:'Refused',data:{}});
      if(mode==='cancel')controller.abort();
      return json({status:'done',code:'host_read_admitted',message:'pending',data:{host_descriptor:{
        arguments:args,operation_id:'12345678-1234-4234-8234-123456789abc',nonce:'a'.repeat(64),
        expires_at:Date.now()/1000+(mode==='expired'?-1:20),operation:'read'}}});
    }};
  const factory=toolFactory(options);
  const context={agentId:'mc-chat',sessionKey,workspaceDir:join(dir,'workspace'),fsPolicy:{workspaceOnly:true}};
  const execute=()=>factory(context)[0].execute('host_call',sealer.seal(args,{runId:RUN,toolCallId:'host_call',agentId:'mc-chat',sessionKey}),controller.signal);
  return {options,context,helper,execute,calls};
}

test('admitted host read uses real isolated helper and keeps nonce private',async t=>{
  const f=fixture(t);const answer=await f.execute();
  assert.equal(answer.details.status,'done');assert.equal(answer.content[0].text,'ordinary h');
  assert.equal(f.calls.length,2);assert.deepEqual(f.calls[0].offered_tools,['minimoi_host_read']);
  assert.ok(!JSON.stringify(answer).includes('a'.repeat(64)));
});

for(const mode of ['refused','cancel','expired'])test(`${mode} admission never invokes helper`,async t=>{
  const f=fixture(t,mode);
  // A helper that would always fail proves it isn't needed on these paths; no completion must be sent.
  writeFileSync(f.helper,'raise RuntimeError("should not run")');
  const answer=await f.execute();assert.equal(answer.details.status,'refused');assert.equal(f.calls.length,1);
});

test('host reads remain absent without fixed helper or workspaceOnly', t=>{
  const f=fixture(t);assert.equal(toolFactory({...f.options,hostHelper:undefined})(f.context).length,0);
  assert.equal(toolFactory(f.options)({...f.context,fsPolicy:{workspaceOnly:false}}),null);
  assert.equal(toolFactory({...f.options,hostHelper:'/some/_working/reader.py'})(f.context).length,0);
  assert.equal(toolFactory({...f.options,hostHelper:f.context.workspaceDir+'/reader.py'})(f.context).length,0);
});
