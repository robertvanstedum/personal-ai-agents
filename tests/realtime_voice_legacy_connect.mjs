/** Actual shared controller, synthetic transport only: no fetch/network/microphone/provider. */
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import assert from 'node:assert/strict';
const root = fileURLToPath(new URL('../core/realtime_voice/static/', import.meta.url));
let instances = [], failConnect = false;
class Adapter {
  constructor(provider) { this.provider=provider; this.listeners={}; this.startCalls=0; this.endCalls=[]; instances.push(this); }
  on(name,fn) { (this.listeners[name] ||= []).push(fn); }
  emit(name,value) { for (const fn of this.listeners[name] || []) fn(value); }
  prepareSession(config) { this.config=config; }
  async connect() { if(failConnect) throw new Error('SYNTHETIC_PRIVATE_TRANSPORT_DETAIL'); this.emit('connected',{}); }
  start() { this.startCalls++; }
  end(reason) { this.endCalls.push(reason); this.emit('closed',{}); }
  sendContinuationInstruction() {}
}
globalThis.OpenAITestAdapter=class extends Adapter { constructor(){super('openai');} };
globalThis.XAITestAdapter=class extends Adapter { constructor(){super('xai');} };
let source=fs.readFileSync(root+'realtime-voice-controller.js','utf8').replace(/^import .*;$/gm,'');
source='const OpenAIWebRTCAdapter=globalThis.OpenAITestAdapter,XAIWebSocketAdapter=globalThis.XAITestAdapter;\n'+source;
const {RealtimeVoiceController:Controller}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const saved={fetch:globalThis.fetch,setInterval:globalThis.setInterval,clearInterval:globalThis.clearInterval};
try {
  for(const provider of ['openai','xai']) {
    for(const failure of [true,false]) {
      instances=[]; failConnect=failure;
      let requests=[],timers=new Set(),timerStarts=0,states=[],fatals=[],finals=[];
      globalThis.setInterval=()=>{ const id=++timerStarts; timers.add(id); return id; };
      globalThis.clearInterval=id=>timers.delete(id);
      globalThis.fetch=async(url,options)=>{
        requests.push([url,options]); assert.equal(url,'/synthetic/legacy/bootstrap');
        assert.deepEqual(JSON.parse(options.body),{provider,persona:'synthetic',scene:'synthetic',learner_name:'Test'});
        return {ok:true,json:async()=>({provider,model:'synthetic-model',warning_minutes:20,max_minutes:30,session_config:{synthetic:true}})};
      };
      const controller=new Controller({bootstrapUrl:'/synthetic/legacy/bootstrap',onStateChange:s=>states.push(s),onFatalError:f=>fatals.push(f),onFinalize:f=>finals.push(f)});
      await controller.startSession({provider,persona:'synthetic',scene:'synthetic',learner_name:'Test'});
      assert.equal(instances.length,1); const adapter=instances[0]; assert.equal(adapter.provider,provider);
      // The only request is mocked legacy bootstrap. No managed session/create/end request exists.
      assert.equal(requests.length,1); assert.equal(controller._managedCreate,null);
      if(failure) {
        assert.deepEqual(fatals,[{reason:'connection_failed',detail:'Voice could not connect'}]);
        assert.equal(JSON.stringify(fatals).includes('PRIVATE_TRANSPORT'),false);
        assert.equal(adapter.startCalls,0); assert.deepEqual(adapter.endCalls,['connection_failed']);
        assert.deepEqual(states,['requesting_mic','connecting','ending','ended']);
        assert.equal(timerStarts,0); assert.equal(timers.size,0);
        assert.equal(finals.length,1); assert.equal(finals[0].partial,true); assert.equal(finals[0].partial_reason,'connection_failed');
      } else {
        assert.deepEqual(fatals,[]); assert.equal(adapter.startCalls,1); assert.equal(controller._state,'active');
        assert.equal(timerStarts,1); assert.equal(timers.size,1);
        // Legacy End must remain synchronous; closed event cannot double-finalize.
        assert.equal(controller.endSession('user_ended'),undefined); assert.equal(controller._state,'ended');
        assert.deepEqual(adapter.endCalls,['user_ended']); assert.equal(timers.size,0);
        assert.equal(finals.length,1); assert.equal(finals[0].partial,false);
      }
      assert.equal(finals[0].provider,provider); assert.equal(finals[0].model,'synthetic-model');
      assert.match(finals[0].session_id,/^sess-/); assert.deepEqual(finals[0].turns,[]);
      controller.endSession('duplicate_end'); assert.equal(adapter.endCalls.length,1); assert.equal(finals.length,1);
    }
  }
  console.log('4 legacy OpenAI/XAI connect-failure/success functional scenarios passed');
} finally { Object.assign(globalThis,saved); }
