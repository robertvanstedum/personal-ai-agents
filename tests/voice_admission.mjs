import fs from 'node:fs';import assert from 'node:assert/strict';
const root=new URL('../',import.meta.url).pathname;
class Adapter{constructor(){this.handlers={};this.closed=0;globalThis.lastAdapter=this}on(k,f){(this.handlers[k]??=[]).push(f)}emit(k,v){for(const f of this.handlers[k]||[])f(v)}prepareSession(){}start(){}stopCapture(){}end(){this.closed++;this.emit('closed',{})}async connect(b){await b.exchangeSdp('synthetic')}}
globalThis.Adapter=Adapter;let src=fs.readFileSync(root+'core/realtime_voice/static/realtime-voice-controller.js','utf8').replace(/^import .*;$/gm,'');src='const OpenAIWebRTCAdapter=globalThis.Adapter,XAIWebSocketAdapter=globalThis.Adapter;\n'+src;const{RealtimeVoiceController:C}=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));const flush=()=>new Promise(r=>setImmediate(r));
for(const code of ['voice_session_unresolved','agent_action_unresolved']){
 const errors=[];let requests=0;globalThis.fetch=async()=>{requests++;return{status:409,ok:false,json:async()=>({ok:false,code,error:'UNTRUSTED PRIVATE DETAIL'})}};
 const c=new C({managedBootstrap:{sessionUrl:'/managed/session'},onFatalError:e=>errors.push(e)});await c.startSession({provider:'openai'});await flush();assert.equal(c._state,'ended');assert.equal(requests,1);assert.equal(errors.length,1);assert.equal(errors[0].reason,code);assert.match(errors[0].detail,/Typing still works/);assert(!JSON.stringify(errors).includes('PRIVATE'));assert.equal(globalThis.lastAdapter.closed,1);assert.deepEqual(await c.endSession('duplicate'),{provider_attempted:false,admission_refused:code});assert.equal(requests,1);
}
for(const [status,code] of [[503,'voice_session_unresolved'],[409,'unknown_code']]){
 const errors=[];let requests=0;globalThis.fetch=async()=>{requests++;return{status,ok:false,json:async()=>({ok:false,code,error:'PRIVATE'})}};const c=new C({managedBootstrap:{sessionUrl:'/managed/session'},onFatalError:e=>errors.push(e)});await c.startSession({provider:'openai'});await flush();assert.deepEqual(errors.map(e=>e.reason),['connection_failed','termination_uncertain']);assert.equal(requests,1);assert(!JSON.stringify(errors).includes('PRIVATE'));
}
// Execute the actual CoS fatal handler; no unrelated input or authority changes.
const html=fs.readFileSync(root+'domains/cos/templates/cos_ui.html','utf8');const body=html.split('  onFatalError:error=>{')[1].split('\n  },')[0];
for(const reason of ['voice_session_unresolved','agent_action_unresolved']){
 const button={disabled:false},mode={disabled:false},transcript={textContent:''};const result=Function('error','button','mode','transcript','let voiceRestartBlocked=false;'+body+';return voiceRestartBlocked;')({reason,detail:'Safe fixed message. Typing still works.'},button,mode,transcript);assert(result&&button.disabled&&mode.disabled);assert.match(transcript.textContent,/Typing still works/);
}
console.log('6 admission/controller/CoS-handler cases passed; zero actual provider, microphone or runtime calls.');
