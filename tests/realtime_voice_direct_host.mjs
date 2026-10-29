/** Shared controller, direct mode, synthetic transport only: a guarded host (MC on the Guild) sends its headers and
 * conversation with the bootstrap; a host that passes neither (German, Portuguese) sends exactly what it sent before. */
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const root = fileURLToPath(new URL('../core/realtime_voice/static/', import.meta.url));
class Adapter {
  constructor(){ this.listeners={}; }
  on(name,fn){ (this.listeners[name] ||= []).push(fn); }
  prepareSession(){} async connect(){} start(){} end(){} sendContinuationInstruction(){}
}
globalThis.OpenAITestAdapter=class extends Adapter {};
globalThis.XAITestAdapter=class extends Adapter {};
let source=fs.readFileSync(root+'realtime-voice-controller.js','utf8').replace(/^import .*;$/gm,'');
source='const OpenAIWebRTCAdapter=globalThis.OpenAITestAdapter,XAIWebSocketAdapter=globalThis.XAITestAdapter;\n'+source;
const {RealtimeVoiceController:Controller}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const saved={fetch:globalThis.fetch,setInterval:globalThis.setInterval,clearInterval:globalThis.clearInterval};
try {
  globalThis.setInterval=()=>1; globalThis.clearInterval=()=>{};
  const sent=[];
  globalThis.fetch=async(url,options)=>{ sent.push([url,options]);
    return {ok:true,json:async()=>({provider:'xai',model:'m',warning_minutes:20,max_minutes:30,voice_session_id:'0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b'})}; };
  const mc=new Controller({bootstrapUrl:'/guild-next/api/v1/mc/voice/direct/api/realtime-voice/bootstrap',
    requestHeaders:()=>({'X-CSRF-Token':'t0k','X-Record-Mode':'on_record'})});
  await mc.startSession({provider:'xai',persona:'master_craftsman',scene:'',conversation_id:'c-123456abcdef'});
  assert.deepEqual(sent[0][1].headers,{'Content-Type':'application/json','X-CSRF-Token':'t0k','X-Record-Mode':'on_record'});
  assert.deepEqual(JSON.parse(sent[0][1].body),{provider:'xai',persona:'master_craftsman',scene:'',conversation_id:'c-123456abcdef'});
  assert.equal(mc._sessionId,'0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b');
  const german=new Controller({bootstrapUrl:'/api/realtime-voice/bootstrap'});
  await german.startSession({provider:'openai',persona:'frau_berger',scene:'bakery',learner_name:'Robert'});
  assert.deepEqual(sent[1][1].headers,{'Content-Type':'application/json'});
  assert.equal(sent[1][1].body,JSON.stringify({provider:'openai',persona:'frau_berger',scene:'bakery',learner_name:'Robert'}));
  // onSessionInfo (opt-in): the host sees the session id and the voice notices, never the credential.
  globalThis.fetch=async()=>({ok:true,json:async()=>({provider:'openai',model:'m',client_secret:'ek_secret',warning_minutes:20,max_minutes:30,
    voice_session_id:'0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b',voice_notices:['openai voice is unset, using marin']})});
  const infos=[];
  const noted=new Controller({bootstrapUrl:'/b',onSessionInfo:info=>infos.push(info)});
  await noted.startSession({provider:'openai',persona:'master_craftsman',scene:'',conversation_id:'c-123456abcdef'});
  assert.deepEqual(infos,[{provider:'openai',model:'m',voice_session_id:'0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b',voice_notices:['openai voice is unset, using marin']}]);
  assert(!JSON.stringify(infos).includes('ek_secret'));
  await new Controller({bootstrapUrl:'/b'}).startSession({provider:'openai',persona:'frau_berger',scene:'bakery'});   // German: no hook, no change
  // Review W N3: the direct-mode bootstrap is bounded only when the host asks (bootstrapTimeoutMs); German and Portuguese
  // ask nothing, so their request carries no signal and no timer is set.
  const realTimeout={set:globalThis.setTimeout,clear:globalThis.clearTimeout};
  const armed=[];
  globalThis.setTimeout=(fn,ms)=>{armed.push({fn,ms});return armed.length;};globalThis.clearTimeout=id=>{if(armed[id-1])armed[id-1].fn=null;};
  try {
    let seen=null;
    globalThis.fetch=async(url,options)=>{seen=options;return {ok:true,json:async()=>({provider:'openai',model:'m',warning_minutes:20,max_minutes:30})};};
    await new Controller({bootstrapUrl:'/api/realtime-voice/bootstrap'}).startSession({provider:'openai',persona:'frau_berger',scene:'bakery',learner_name:'Robert'});
    assert.equal('signal' in seen,false);assert.deepEqual(armed,[]);
    // Never answered: at the bound the request is aborted and the start fails as the bootstrap's network failure, once.
    const fatal=[],states=[];
    globalThis.fetch=(url,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError'))));
    const hung=new Controller({bootstrapUrl:'/b',bootstrapTimeoutMs:36000,onFatalError:e=>fatal.push(e.reason),onStateChange:s=>states.push(s)});
    const starting=hung.startSession({provider:'openai',persona:'chief_of_staff',scene:'',conversation_id:'owner'});
    assert.equal(armed.at(-1).ms,36000);armed.at(-1).fn();await starting;
    assert.deepEqual(fatal,['bootstrap_network_error']);assert.equal(hung._state,'idle');assert.equal(hung._adapter,null);
    // Its headers came, its body never did: the reading of the answer is bounded too.
    fatal.length=0;
    globalThis.fetch=async(url,options)=>({ok:true,json:()=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError'))))});
    const slow=new Controller({bootstrapUrl:'/b',bootstrapTimeoutMs:36000,onFatalError:e=>fatal.push(e.reason)});
    const reading=slow.startSession({provider:'openai',persona:'chief_of_staff',scene:'',conversation_id:'owner'});
    await new Promise(r=>setImmediate(r));armed.at(-1).fn();await reading;
    assert.deepEqual(fatal,['bootstrap_network_error']);assert.equal(slow._adapter,null);
    // Answered in time: the bound is cleared and the session goes on.
    fatal.length=0;
    globalThis.fetch=async()=>({ok:true,json:async()=>({provider:'openai',model:'m',warning_minutes:20,max_minutes:30,voice_session_id:'0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b'})});
    const prompt=new Controller({bootstrapUrl:'/b',bootstrapTimeoutMs:36000,onFatalError:e=>fatal.push(e.reason)});
    await prompt.startSession({provider:'openai',persona:'chief_of_staff',scene:'',conversation_id:'owner'});
    assert.equal(armed.at(-1).fn,null);assert.deepEqual(fatal,[]);assert.equal(prompt._sessionId,'0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b');
  } finally {globalThis.setTimeout=realTimeout.set;globalThis.clearTimeout=realTimeout.clear;}
  // Gate 2 Q S3: a direct start (pageResponses) ended while its bootstrap is in flight discards the late answer: no adapter,
  // no connect (no microphone); the host is told the session it was given, so it can end it. German (no pageResponses) is
  // as before: its late answer still goes on to build its adapter.
  {
    let answer=null;
    globalThis.fetch=()=>new Promise(r=>{answer=()=>r({ok:true,json:async()=>({provider:'openai',model:'m',warning_minutes:20,max_minutes:30,
      voice_session_id:'0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b',page_responses:true})});});
    const infos=[],states=[];
    const direct=new Controller({bootstrapUrl:'/b',pageResponses:true,openingInstruction:null,onUnbound:()=>{},
      onSessionInfo:i=>infos.push(i),onStateChange:s=>states.push(s)});
    const starting=direct.startSession({provider:'openai',persona:'chief_of_staff',scene:'',conversation_id:'owner'});
    await new Promise(r=>setImmediate(r));
    direct.endSession('companion_opened');
    assert.equal(direct.state(),'ended');
    answer();await starting;
    assert.equal(direct._adapter,null);assert.equal(direct.state(),'ended');
    assert.deepEqual(infos,[{provider:'openai',model:'m',voice_session_id:'0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b',voice_notices:[]}]);
    assert.deepEqual(states,['requesting_mic','ending','ended']);
    const german=new Controller({bootstrapUrl:'/api/realtime-voice/bootstrap'});
    const lesson=german.startSession({provider:'openai',persona:'frau_berger',scene:'bakery',learner_name:'Robert'});
    await new Promise(r=>setImmediate(r));
    german.endSession('user_ended');
    answer();await lesson;
    assert.ok(german._adapter,'German\'s late bootstrap no longer builds its adapter (it must be as before)');
  }
  console.log('9 direct-host bootstrap scenarios passed');
} finally { Object.assign(globalThis,saved); }
