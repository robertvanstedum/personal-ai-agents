import fs from 'node:fs';
import assert from 'node:assert/strict';
const root=new URL('../core/realtime_voice/static/',import.meta.url).pathname;
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve}};
const flush=()=>new Promise(r=>setImmediate(r));
let instances=[],events=[];
class Adapter{
 constructor(){this.handlers={};this.closed=0;this.captured=0;instances.push(this)}
 on(k,f){(this.handlers[k]??=[]).push(f)}emit(k,e){for(const f of this.handlers[k]||[])f(e)}
 prepareSession(){}start(){}sendContinuationInstruction(){}
 async connect(b){if(b.exchangeSdp){const result=await b.exchangeSdp('synthetic-offer');this.emit('session_identity',{session_id:result.session_id})}this.emit('connected',{})}
 stopCapture(){events.push('capture-stopped');this.captured++}
 end(){events.push('transport-closed');this.closed++;this.emit('closed',{})}
}
globalThis.Adapter=Adapter;
let source=fs.readFileSync(root+'realtime-voice-controller.js','utf8').replace(/^import .*;$/gm,'');
source='const OpenAIWebRTCAdapter=globalThis.Adapter,XAIWebSocketAdapter=globalThis.Adapter;\n'+source;
const {RealtimeVoiceController:C}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const ok=()=>({ok:true,json:async()=>({ok:true,session_id:'synthetic-server-id',sdp:'synthetic-answer',provider:'openai',model:'gpt-realtime-2.1',warning_minutes:13,max_minutes:15})});
async function rig(endRequest){
 instances=[];events=[];let endCalls=0,starts=0;const errors=[],finals=[];
 globalThis.fetch=async(url,options)=>{
  if(url.endsWith('/end')){endCalls++;events.push('server-end-request');return endRequest(options)}
  starts++;return ok();
 };
 const c=new C({managedBootstrap:{sessionUrl:'/voice/session'},onFatalError:e=>errors.push(e),onFinalize:e=>finals.push(e)});
 await c.startSession({provider:'openai'});return {c,errors,finals,old:instances[0],calls:()=>endCalls,starts:()=>starts};
}
// End stops capture now, retains peer until receipt, coalesces duplicate End and blocks starts.
{
 const gate=deferred(),r=await rig(()=>gate.promise);const ending=r.c.endSession('user_ended');
 assert.equal(r.old.captured,1);assert.equal(r.old.closed,0);assert.equal(r.c._state,'ending');
 assert.equal(r.c.endSession('user_ended'),ending);await r.c.startSession({provider:'openai'});assert.equal(r.starts(),1);
 await flush();assert.equal(r.calls(),1);assert.equal(r.old.closed,0);
 const decoy={end(){assert.fail('new adapter must not be closed')}};r.c._adapter=decoy;
 gate.resolve({ok:true,json:async()=>({termination_confirmed:true})});await ending;
 assert.equal(r.old.closed,1);assert.equal(r.finals.length,1);assert.equal(r.errors.length,0);
 assert.equal(r.c._state,'ended');assert.equal(r.c.endSession('duplicate'),ending);
 r.c._adapter=r.old;
}
// Explicit404 is uncertainty, never normalized to confirmed; local transport still closes.
{
 const r=await rig(async()=>({ok:false,status:404,json:async()=>({error:'not found'})}));
 const result=await r.c.endSession('user_ended');assert.equal(result.termination_confirmed,false);
 assert.equal(r.calls(),1);assert.equal(r.errors[0].reason,'termination_uncertain');assert.equal(r.old.closed,1);assert.equal(r.finals.length,1);
}
// Network failure still closes locally exactly once, without retry.
{
 const r=await rig(async()=>{throw Error('synthetic private network details')});
 await r.c.endSession('user_ended');assert.equal(r.calls(),1);assert.equal(r.old.closed,1);assert.equal(r.errors.length,1);
 assert(!JSON.stringify(r.errors).includes('private'));
}
// Bounded End timeout aborts the fetch, reports uncertainty and closes the old transport.
{
 const saved=globalThis.setTimeout;
 const r=await rig(options=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Error('aborted')))));
 globalThis.setTimeout=(f,ms,...args)=>saved(f,ms===15000?1:ms,...args);
 try{await r.c.endSession('user_ended')}finally{globalThis.setTimeout=saved}
 assert.equal(r.errors[0].reason,'termination_uncertain');assert.equal(r.old.closed,1);assert.equal(r.calls(),1);
}
// End during a pending actual create waits for its identity and sends only one End.
{
 instances=[];events=[];const create=deferred(),end=deferred();let ends=0;
 globalThis.fetch=async(url)=>url.endsWith('/end')?(ends++,end.promise):create.promise;
 const c=new C({managedBootstrap:{sessionUrl:'/voice/session'}});
 const start=c.startSession({provider:'openai'});await flush();const old=instances[0];
 const ending=c.endSession('user_ended');await flush();assert.equal(old.closed,0);assert.equal(ends,0);
 create.resolve(ok());await flush();assert.equal(ends,1);assert.equal(old.closed,0);
 end.resolve({ok:true,json:async()=>({termination_confirmed:true})});await ending;await start;
 assert.equal(ends,1);assert.equal(old.closed,1);
}
// Legacy flow remains synchronous and does not call the managed End route.
{
 globalThis.fetch=async()=>ok();let finalized=false;
 const c=new C({bootstrapUrl:'/legacy',onFinalize:()=>finalized=true});await c.startSession({provider:'openai'});
 c.endSession('user_ended');assert.equal(finalized,true);assert.equal(c._state,'ended');
}
// A delayed old offer must not create a server session after End has completed.
{
 instances=[];let calls=0,lateExchange;globalThis.fetch=async()=>{calls++;return ok()};
 const savedConnect=Adapter.prototype.connect;
 Adapter.prototype.connect=async function(b){lateExchange=b.exchangeSdp;};
 try{
  const c=new C({managedBootstrap:{sessionUrl:'/voice/session'}});
  await c.startSession({provider:'openai'});
  const receipt=await c.endSession('user_ended');
  assert.equal(receipt.provider_attempted,false);
  await assert.rejects(()=>lateExchange('old-deferred-offer'),/ended before/);
  assert.equal(calls,0);
 }finally{Adapter.prototype.connect=savedConnect;}
}
// Actual adapter stops microphone tracks without closing RTC/datachannel until end.
{
 const {OpenAIWebRTCAdapter:A}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(root+'adapters/openai-webrtc-adapter.js','utf8')).toString('base64'));
 const a=new A();let stopped=0,peerClosed=0,channelClosed=0,paused=0;
 a._micStream={getAudioTracks:()=>[{stop(){stopped++}}]};a._pc={close(){peerClosed++}};a._dc={close(){channelClosed++}};a._remoteAudioEl={pause(){paused++}};
 a.stopCapture();assert.equal(stopped,1);assert.equal(paused,1);assert.equal(peerClosed,0);assert.equal(channelClosed,0);
 a.end('user_ended');assert.equal(peerClosed,1);assert.equal(channelClosed,1);
}
console.log('8 managed/legacy teardown ordering, uncertainty and capture scenarios passed; no provider calls.');
