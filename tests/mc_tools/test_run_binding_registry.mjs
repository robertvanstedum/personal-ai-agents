import test from 'node:test';
import assert from 'node:assert/strict';
import {runBoundSealer,sealHook,toolFactory,checkArguments,SEAL,backendCallId} from '../../services/mc_operations/plugins/minimoi-tools/index.mjs';
const RUN='chatcmpl_12345678-1234-4234-8234-123456789abc',OTHER='chatcmpl_00000000-0000-4000-8000-000000000000';
const ctx={runId:RUN,toolCallId:'call_1',agentId:'mc-chat',sessionKey:'agent:mc-chat:openai-user:guild-mc:'+'a'.repeat(32),toolName:'minimoi_task_list'};
const args={task_id:'TK-cos-18e9490595d3f256de94245d4abecb32'};
function setup(options={}){const sealer=runBoundSealer(options),hook=sealHook({sealer,tools:[ctx.toolName],agents:['mc-chat']});return {sealer,stage:(params=args,c=ctx)=>hook({toolName:c.toolName,params},c)};}
test('strict post-hook schema passes; real factory sends the host run once',async()=>{
 const f=setup(),sent=[];
 const factory=toolFactory({endpoint:'http://127.0.0.1:5011/internal/mc/v1/tools',token:'r'.repeat(40),tokenFile:'/private/token',agents:['mc-chat'],tools:[ctx.toolName],allowTokenReadableByAgent:true,sealer:f.sealer,instance:'d'.repeat(32),fetchImpl:async(url,init)=>{
  sent.push({url:String(url),body:JSON.parse(init.body),headers:init.headers});return new Response(JSON.stringify(String(url).endsWith('/instance')?{}:{status:'done',code:'task_get',message:'Read complete',data:{state:'done'}}),{status:200,headers:{'content-type':'application/json'}});
 }});
 const tool=factory({agentId:ctx.agentId,sessionKey:ctx.sessionKey})[0],clean=f.stage({...args,[SEAL]:{run:OTHER,call:'forged',mac:'forged'}}).params;
 assert.deepEqual(clean,args);assert.equal(checkArguments(tool.parameters,clean),true);assert.equal(Object.hasOwn(clean,SEAL),false);
 const out=await tool.execute(ctx.toolCallId,clean);assert.equal(out.details.code,'task_get');
 const request=sent.find(x=>x.url.endsWith('/tools/minimoi_task_list'));assert.deepEqual(request.body.arguments,args);assert.equal(request.headers['X-MC-Run'],RUN);
 assert.equal((await tool.execute(ctx.toolCallId,clean)).details.code,'run_identity_missing');assert.equal(sent.filter(x=>x.url.endsWith('/tools/minimoi_task_list')).length,1);
});
test('missing hook, forged seal, changed args and wrong context refuse',()=>{
 assert.equal(setup().sealer.open(args,ctx),null);
 for(const bad of [{...args,[SEAL]:{}},{...args,task_id:'other'}]){const f=setup();f.stage();assert.equal(f.sealer.open(bad,ctx),null);assert.equal(f.sealer.open(args,ctx),null);}
 for(const delta of [{toolName:'minimoi_task_accept'},{agentId:'other'},{sessionKey:'other'},{toolCallId:'other'}]){const f=setup();f.stage();assert.equal(f.sealer.open(args,{...ctx,...delta}),null);assert.deepEqual(f.sealer.open(args,ctx),{run:RUN,args});}
});
test('duplicate hooks are idempotent; conflicts and consumed IDs cannot be reassigned',()=>{
 const f=setup();assert.deepEqual(f.stage(),f.stage());assert.deepEqual(f.sealer.open(args,ctx),{run:RUN,args});assert.equal(f.stage(args,{...ctx,runId:OTHER}).block,true);assert.equal(f.sealer.open(args,ctx),null);
 for(const [a,c] of [[args,{...ctx,runId:OTHER}],[{task_id:'other'},ctx]]){const g=setup();g.stage();assert.equal(g.stage(a,c).block,true);assert.equal(g.sealer.open(args,ctx),null);}
});
test('expiry and capacity preserve identities and pending calls',()=>{
 let at=0;const f=setup({now:()=>at,ttlMs:10,maxEntries:1});f.stage();at=11;assert.equal(f.sealer.open(args,ctx),null);assert.equal(f.stage(args,{...ctx,runId:OTHER}).block,true);assert.equal(f.stage(args,{...ctx,toolCallId:'new'}).block,true);
 const g=setup({maxEntries:1});g.stage();assert.equal(g.stage(args,{...ctx,toolCallId:'new'}).block,true);assert.deepEqual(g.sealer.open(args,ctx),{run:RUN,args});
});
test('canonical JSON tolerates reordered keys but rejects mutations, invalid and oversized values',()=>{
 const f=setup(),a={z:[2,{b:true,a:null}],a:1};f.stage(a);assert.deepEqual(f.sealer.open({a:1,z:[2,{a:null,b:true}]},ctx),{run:RUN,args:a});
 for(const bad of [null,[],{a:undefined},{a:NaN},{a:'x'.repeat(65537)}])assert.equal(setup().stage(bad).block,true);
 const g=setup(),original={nested:{x:1}};g.stage(original);original.nested.x=2;assert.equal(g.sealer.open(original,ctx),null);
 const h=setup(),mutable={nested:{x:1}},clean=h.stage(mutable).params;mutable.nested.x=2;assert.deepEqual(h.sealer.open(clean,ctx),{run:RUN,args:{nested:{x:1}}});
});
test('separate module copies consume the same process registry',async()=>{
 const first=await import('../../services/mc_operations/plugins/minimoi-tools/index.mjs'),second=await import('../../services/mc_operations/plugins/minimoi-tools/index.mjs?registry-copy');
 const entries=globalThis[Symbol.for('minimoi-tools/run-bindings/v1')],key=globalThis[Symbol.for('minimoi-tools/process-state/v1')].sealKey;
 const a=first.runBoundSealer({key,entries}),b=second.runBoundSealer({key,entries}),c={...ctx,toolCallId:'cross-module-unique'};
 const hook=first.sealHook({sealer:a,tools:[c.toolName],agents:[c.agentId]}),clean=hook({toolName:c.toolName,params:args},c).params;
 assert.deepEqual(b.open(clean,c),{run:RUN,args});assert.equal(a.open(clean,c),null);
});

// Code mode's nested id: the runtime's real shape, from a 91-character parent id. 131 characters for minimoi_issue_create.
const NESTED='tool_search_code:'+'c'.repeat(37)+'_'+'f'.repeat(53)+':minimoi_issue_create:1';
test('a nested code-mode call longer than the backend id is sealed, opened once, and sent as a stable digest',()=>{
 assert.equal(NESTED.length,131);
 const {sealer}=setup(),args={title:'A real title',body:'Body'},c={...ctx,toolName:'minimoi_issue_create',toolCallId:NESTED};
 assert.deepEqual(sealer.seal(args,c),args);
 assert.equal(sealer.open(args,c).run,c.runId);
 assert.equal(sealer.open(args,c),null);                             // still one shot
 assert.equal(sealer.seal(args,c),null);                             // and still no replay
 assert.match(backendCallId(NESTED),/^sha256:[0-9a-f]{64}$/);
 assert.equal(backendCallId(NESTED),backendCallId(NESTED));
 assert.equal(backendCallId('call_short'),'call_short');
 assert.notEqual(backendCallId(NESTED),backendCallId(NESTED.slice(0,-1)+'2'));
});
test('every refusal names its branch and shows no arguments or id values',()=>{
 const seen=[],at={now:1000},sealer=runBoundSealer({onRefuse:d=>seen.push(d),maxEntries:1,now:()=>at.now,ttlMs:50}),c={agentId:'mc-chat',sessionKey:'agent:mc-chat:x',toolName:'minimoi_issue_create',runId:RUN,toolCallId:'call_1'};
 const reason=()=>seen.at(-1).reason;
 assert.equal(sealer.seal({a:1},{...c,runId:'no'}),null);assert.equal(reason(),'run_id_invalid');
 assert.equal(sealer.seal({a:1},{...c,toolCallId:undefined}),null);assert.equal(reason(),'call_id_missing');
 assert.equal(sealer.seal({a:1},{...c,toolCallId:'bad|id'}),null);assert.equal(reason(),'call_id_invalid');
 assert.equal(sealer.seal({a:1},{...c,toolCallId:'c'.repeat(257)}),null);assert.equal(reason(),'call_id_invalid');
 assert.equal(sealer.seal({a:1},{...c,sessionKey:''}),null);assert.equal(reason(),'session_missing');
 assert.equal(sealer.seal({a:undefined},c),null);assert.equal(reason(),'arguments_not_canonical');
 assert.equal(sealer.seal([],c),null);assert.equal(reason(),'arguments_not_plain');
 assert.equal(sealer.seal({a:'SECRET-BODY'},{...c,toolName:'other'}),null);assert.equal(reason(),'tool_name_invalid');
 assert.deepEqual(sealer.seal({a:'SECRET-BODY'},c),{a:'SECRET-BODY'});
 assert.equal(sealer.seal({a:'SECRET-BODY'},{...c,toolCallId:'call_2'}),null);assert.equal(reason(),'registry_full');
 assert.equal(sealer.seal({a:'other'},c),null);assert.equal(reason(),'call_arguments_changed');
 assert.equal(sealer.seal({a:'SECRET-BODY'},c),null);assert.equal(reason(),'call_already_used');
 assert.ok(!JSON.stringify(seen).includes('SECRET-BODY')&&!JSON.stringify(seen).includes('call_1')&&!JSON.stringify(seen).includes(RUN));
});

// Code mode hands the model's code only a tool's details, so its words must be there as well as in its text.
function answering(reply){
 const f=setup(),calls=[];
 const factory=toolFactory({endpoint:'http://127.0.0.1:5011/internal/mc/v1/tools',token:'r'.repeat(40),tokenFile:'/private/token',agents:['mc-chat'],tools:[ctx.toolName],allowTokenReadableByAgent:true,sealer:f.sealer,fetchImpl:async(url,init)=>{
  calls.push(String(url));if(String(url).endsWith('/instance'))return new Response('{}',{status:200,headers:{'content-type':'application/json'}});
  return reply();
 }});
 const [tool]=factory({agentId:ctx.agentId,sessionKey:ctx.sessionKey,workspaceDir:'/private/ws'}).filter(t=>t.name===ctx.toolName);
 return {tool,f,calls};
}
const json=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'content-type':'application/json'}});
test('a successful answer keeps its status, code and data, and adds its words',async()=>{
 const {tool,f}=answering(()=>json({status:'done',code:'task',message:'Read completely.',data:{state:'completed'}}));
 assert.deepEqual(f.stage().params,args);
 const out=await tool.execute(ctx.toolCallId,args);
 assert.deepEqual(out.details,{message:'Read completely.',status:'done',code:'task',data:{state:'completed'}});
 assert.equal(out.content[0].text,'Read completely.');
});
test('a refusal from the listener reaches details with its explanation',async()=>{
 const {tool,f}=answering(()=>json({status:'refused',code:'name_in_public_text',message:'It names someone. Nothing was proposed.',data:{}}));
 f.stage();
 const out=await tool.execute(ctx.toolCallId,args);
 assert.equal(out.details.code,'name_in_public_text');assert.equal(out.details.status,'refused');
 assert.equal(out.details.message,'It names someone. Nothing was proposed.');
});
test('a refusal the plugin makes itself says why, and sends nothing',async()=>{
 const {tool,calls}=answering(()=>{throw Error('must not be called');});
 const out=await tool.execute(ctx.toolCallId,args);                     // never sealed
 assert.equal(out.details.code,'run_identity_missing');assert.match(out.details.message,/nothing was sent/);
 assert.ok(!calls.some(u=>u.endsWith(ctx.toolName)));
});
test('an answer that fails the existing checks is still unknown, and adds nothing from that answer',async()=>{
 const {tool,f}=answering(()=>json({status:'done',code:'x',message:'SERVER WORDS',data:{},extra:1}));
 f.stage();
 const out=await tool.execute(ctx.toolCallId,args);
 assert.equal(out.details.status,'unknown');assert.ok(!JSON.stringify(out).includes('SERVER WORDS'));
});
