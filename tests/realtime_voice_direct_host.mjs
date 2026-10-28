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
  console.log('2 direct-host bootstrap scenarios passed');
} finally { Object.assign(globalThis,saved); }
