import {test} from 'node:test';
import assert from 'node:assert/strict';
import {cutoffMessage,wrapCutoffStream,EMPTY_NOTICE} from '../docker/cos-agent-a/plugins/mc-cutoff-delivery/stream.mjs';
const model={provider:'minimoi-gateway',id:'minimoi-cos-agent',api:'openai-completions'};
function stream() {
 const q=[];let ended=false,wake;
 return {push(x){q.push(x);wake?.();},end(){ended=true;wake?.();},async *[Symbol.asyncIterator](){while(!ended||q.length){if(q.length)yield q.shift();else await new Promise(r=>wake=r);}}};
}
async function run(events, options={}) {
 let calls=0;const context={messages:[]};
 const wrapped=wrapCutoffStream((m,c,o)=>{calls++;assert.equal(m,model);assert.equal(c,context);assert.equal(o,options);return (async function*(){yield* events;})();},stream);
 const result=[];for await(const e of wrapped(model,context,options))result.push(e);
 assert.equal(calls,1);return result;
}
test('empty length becomes explicitly platform-authored notice, retaining usage and length',async()=>{
 const usage={input:12,output:4096};const original={role:'assistant',content:[],stopReason:'length',usage};
 const result=await run([{type:'done',reason:'length',message:original}]);const done=result.at(-1);
 assert.equal(done.reason,'length');assert.equal(done.message.stopReason,'length');assert.equal(done.message.usage,usage);
 assert.deepEqual(done.message.content,[{type:'text',text:EMPTY_NOTICE}]);assert.deepEqual(original.content,[]);
 assert.equal(result.filter(e=>e.type==='text_delta')[0].delta,EMPTY_NOTICE);
});
test('original partial text is preserved and incomplete tool calls cannot be executed',async()=>{
 const text={type:'text',text:'Original partial analysis'};
 const result=await run([{type:'done',reason:'length',message:{role:'assistant',content:[text,{type:'toolCall',id:'unfinished',name:'write',arguments:{}}],stopReason:'length'}}]);
 assert.deepEqual(result.at(-1).message.content,[text,{type:'text',text:''}]);assert.equal(result.length,1);
});
test('normal stop and tool use remain unchanged',async()=>{
 for(const reason of ['stop','toolUse']){
  const original={role:'assistant',content:[{type:'text',text:'done'}],stopReason:reason};
  const result=await run([{type:'done',reason,message:original}]);assert.equal(result.length,1);assert.equal(result[0].message,original);
 }
});
test('provider error and cancellation remain errors, never notices',async()=>{
 const abort=new AbortController();abort.abort();
 const event={type:'error',reason:'aborted',error:{stopReason:'aborted',content:[]}};
 assert.deepEqual(await run([event],{signal:abort.signal}),[event]);
});
test('thrown cancellation is not turned into a successful completion',async()=>{
 const controller=new AbortController();controller.abort();
 const wrapped=wrapCutoffStream(()=>({async *[Symbol.asyncIterator](){throw Error('cancelled');}}),stream);
 const events=[];for await(const e of wrapped(model,{}, {signal:controller.signal}))events.push(e);
 assert.equal(events.length,1);assert.equal(events[0].reason,'aborted');assert.equal(events[0].error.stopReason,'aborted');
});
test('other providers and aliases bypass wrapper',()=>{
 for(const m of [{provider:'other',id:model.id},{...model,id:'other'}]){const value={};assert.equal(wrapCutoffStream(()=>value,stream)(m,{},{}),value);}
});
test('non-length messages preserve identity and thinking-only length gets notice',()=>{
 const m={stopReason:'error',content:[]};assert.equal(cutoffMessage(m),m);
 const reasoning={type:'thinking',thinking:'unexposed'};const out=cutoffMessage({stopReason:'length',content:[reasoning]});
 assert.deepEqual(out.content,[reasoning,{type:'text',text:EMPTY_NOTICE}]);
});

test('notice index does not reuse a streamed incomplete tool call index',async()=>{
 const partial={role:'assistant',content:[{type:'toolCall',id:'bad',name:'read',arguments:{}}],stopReason:'length'};
 const result=await run([{type:'toolcall_start',contentIndex:0,partial},{type:'done',reason:'length',message:partial}]);
 assert.equal(result.find(e=>e.type==='text_start').contentIndex,1);
 assert.ok(result.at(-1).message.content.every(p=>p.type!=='toolCall'));
});
test('unterminated stream returns error rather than successful answer',async()=>{
 const result=await run([]);assert.equal(result.length,1);assert.equal(result[0].reason,'error');
});
