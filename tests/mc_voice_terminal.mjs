import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../minimoi_portal/guild_ui/static/js/mc_voice.js',import.meta.url),'utf8')
 .replace(/^import .*;$/gm,'').replace('export function initMcVoice','function initMcVoice')
 .replace('await import(`${api}/mc/voice/static/realtime-voice-controller.js`)','await importControlled()');
const flush=()=>new Promise(r=>setImmediate(r));
async function rig({endGate=null}={}){
 const nodes={},instances=[],events={},windowEvents={};
 const node=s=>nodes[s]??={textContent:'',value:s.includes('provider')?'openai':'',disabled:false,hidden:false,checked:false,attrs:{},setAttribute(k,v){this.attrs[k]=v},addEventListener(k,f){this[k]=f}};
 const document={body:{dataset:{mcVoice:'true'}},dispatchEvent(){},addEventListener(k,f){events[k]=f}};
 class Controller{constructor(options){this.options=options;instances.push(this)}async startSession(){this.options.onStateChange('active')}endSession(){if(endGate)return endGate.then(()=>this.options.onStateChange('ended'));this.options.onStateChange('ended')}}
 const sandbox={$:node,announce(){},live:{known:true,off:false},onChange(){},recordMode:()=> 'on',submitVoiceUtterance(){throw Error('no submit')},panelMode:()=> 'open',
 document,CustomEvent:class{},MutationObserver:class{observe(){}},window:{addEventListener(k,f){windowEvents[k]=f}},fetch:async()=>({ok:true,json:async()=>({ok:true,providers:[{provider:'openai'}],managed_realtime:{session_url:'/fake'}})}),importControlled:async()=>({RealtimeVoiceController:Controller})};
 vm.createContext(sandbox);vm.runInContext(source,sandbox);sandbox.initMcVoice({urls:{api:'/api'},csrf_token:'synthetic',conversation:{id:'synthetic-conversation'}});await flush();
 return {nodes,instances,node,async click(){node('[data-mc-voice-btn]').click();await flush()},status:()=>node('[data-mc-voice-status]').textContent};
}
// Terminal failure must never return to Listening from late input/state/warning events.
{
 const r=await rig();await r.click();const c=r.instances[0];assert.equal(r.status(),'Listening…');
 c.options.onFatalError({reason:'provider_failure'});const error=r.status();
 c.options.onInputState('speech_started');c.options.onStateChange('active');c.options.onWarning();
 assert.equal(r.status(),error);c.options.onStateChange('ended');assert.equal(r.status(),error);
 assert.equal(r.node('[data-mc-voice-btn]').textContent,'Voice');
}
// Ended attempt does not repaint itself Listening; a genuinely new attempt can.
{
 const r=await rig();await r.click();const c=r.instances[0];c.options.onStateChange('ended');const ended=r.status();
 c.options.onInputState('speech_started');c.options.onStateChange('active');assert.equal(r.status(),ended);
 await r.click();assert.equal(r.instances.length,2);assert.equal(r.status(),'Listening…');
 c.options.onFatalError({reason:'termination_uncertain'});assert.equal(r.status(),'Listening…');assert.equal(r.node('[data-mc-voice-btn]').disabled,false);
}
// Unconfirmed termination blocks new starts without resetting or retrying anything.
{
 const r=await rig();await r.click();const c=r.instances[0];await r.click();
 c.options.onFatalError({reason:'termination_uncertain'});assert.match(r.status(),/termination is unconfirmed/);
 assert.equal(r.node('[data-mc-voice-btn]').disabled,true);await r.click();assert.equal(r.instances.length,1);
 c.options.onFatalError({reason:'provider_failure'});assert.match(r.status(),/termination is unconfirmed/);
}
// Known stages have truthful bounded labels rather than pretending every stage is Listening.
{
 const r=await rig();await r.click();const c=r.instances[0];
 for(const [phase,text] of [['speech_stopped','Understanding your speech…'],['understood','Preparing a reply…'],['consulting','Working on that…']]){c.options.onInputState(phase);assert.equal(r.status(),text)}
}
console.log('4 MC terminal/error/late-event/restart/stage scenarios passed; no network/provider/microphone calls.');

// MC host keeps End busy until the shared asynchronous termination resolves.
{
 let release;const gate=new Promise(r=>release=r);const r=await rig({endGate:gate});
 await r.click();await r.click();
 assert.equal(r.node('[data-mc-voice-btn]').textContent,'Ending voice…');
 assert.equal(r.node('[data-mc-voice-btn]').disabled,true);
 await r.click();assert.equal(r.instances.length,1);
 release();await flush();assert.equal(r.node('[data-mc-voice-btn]').textContent,'Voice');
 assert.equal(r.node('[data-mc-voice-btn]').disabled,false);
 await r.click();assert.equal(r.instances.length,2);
}
console.log('MC host asynchronous End regression passed; no calls.');

for(const reason of ['voice_session_unresolved','agent_action_unresolved']){
 const r=await rig();await r.click();const c=r.instances[0];c.options.onFatalError({reason,detail:'ignored private detail'});const message=r.status();assert.match(message,/earlier/);assert.match(message,/Typing still works/);assert(!message.includes('private detail'));assert.equal(r.node('[data-mc-voice-btn]').disabled,true);c.options.onStateChange('ended');assert.equal(r.status(),message);await r.click();assert.equal(r.instances.length,1);
}
console.log('2 MC admission refusal/late-ended/no-retry cases passed.');
