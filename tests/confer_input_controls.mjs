import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {bindConferDictation,conferVoiceAvailability} from '../core/realtime_voice/static/confer-input-controls.js';
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
function rig({beforeStart=async()=>{}}={}){
  const events={},instances=[];
  const button={disabled:true,textContent:'',setAttribute(){},addEventListener:(k,fn)=>events[k]=fn};
  const input={value:'Existing draft',readOnly:false,disabled:false};
  const send={disabled:false};
  class Memo{
    constructor(options){this.options=options;this.finish=deferred();instances.push(this);}
    async start(){this.options.onStateChange('requesting');this.options.onStateChange('listening');}
    async stop(){this.options.onStateChange('finalizing');await this.finish.promise;this.options.onTranscript(this.options.initialText+' final words');this.options.onStateChange('stopped');}
    cancel(){this.cancelled=true;this.options.onStateChange('stopped');}
  }
  const binding=bindConferDictation({button,input,bootstrapUrl:'/synthetic',MemoController:Memo,beforeStart,onBusy:v=>send.disabled=v});
  binding.setAvailable(true);
  return {binding,button,input,send,instances,click:()=>events.click()};
}
// Dictation starts with the old draft, inserts text, never invokes a submit callback.
{
 const r=rig();await r.click();const m=r.instances[0];
 assert.equal(m.options.initialText,'Existing draft');assert.equal(r.input.readOnly,true);assert.equal(r.send.disabled,true);
 m.options.onTranscript('Existing draft provisional');
 const stopping=r.binding.stop();assert.equal(r.input.readOnly,true);assert.equal(r.send.disabled,true);
 m.finish.resolve();await stopping;
 assert.equal(r.input.value,'Existing draft final words');assert.equal(r.input.readOnly,false);assert.equal(r.send.disabled,false);
 m.options.onTranscript('late duplicate');assert.equal(r.input.value,'Existing draft final words');
}
// Page exit cancels immediately, even if graceful finalization is pending.
{
 const r=rig();await r.click();const m=r.instances[0];m.options.onTranscript('Keep these words');
 const finishing=r.binding.stop();await r.binding.stop('pagehide');
 assert.equal(m.cancelled,true);assert.equal(r.input.readOnly,false);
 m.options.onTranscript('late after exit');assert.equal(r.input.value,'Keep these words');
 m.finish.resolve();await finishing;assert.equal(r.input.value,'Keep these words');
}
// Stop during a preceding microphone handoff cancels the pending start.
{
 const gate=deferred();const r=rig({beforeStart:()=>gate.promise});const start=r.click();
 assert.equal(r.send.disabled,true);await r.binding.stop('voice_started');gate.resolve();await start;
 assert.equal(r.instances.length,0);assert.equal(r.input.readOnly,false);assert.equal(r.input.value,'Existing draft');
}
// Failure of chained voice cannot disable available realtime.
{
 const mode={value:'chained',options:[{value:'chained'},{value:'realtime'}]};
 assert.deepEqual(conferVoiceAvailability({legacyReady:false,realtimeReady:true,mode}),{voiceReady:true,dictationReady:false});
 assert.equal(mode.value,'realtime');assert.equal(mode.options[0].disabled,true);
}
// Run the actual host Send handler against a recording/finalizing draft.
{
 const template=fs.readFileSync(new URL('../domains/cos/templates/cos_ui.html',import.meta.url),'utf8');
 const handler=template.slice(template.indexOf('async function sendMessage()'),template.indexOf('function setInputDisabled'));
 for(const state of ['recording','finalizing']){
  const r=rig();await r.click();const m=r.instances[0];let stopping;
  if(state==='finalizing')stopping=r.binding.stop();
  let fetches=0;
  // The slice reaches past sendMessage to the Files from CoS wiring (c13e0fba), which looks its toggle up by id at load:
  // a page node that takes listeners is all it needs (Spec 165 §12.1's missing DOM mock).
  const sandbox={sendInput:r.input,btnSend:r.send,fetch:()=>{fetches++;},crypto:{randomUUID:()=> 'synthetic'},appendMsg:()=>assert.fail('must not send'),appendThinking:()=>assert.fail('must not send'),setInputDisabled:()=>{},
   document:{getElementById:()=>({addEventListener(){}})}};
  vm.createContext(sandbox);vm.runInContext(handler,sandbox);await sandbox.sendMessage();
  assert.equal(fetches,0);assert.equal(r.input.value,'Existing draft');
  if(!stopping)stopping=r.binding.stop();m.finish.resolve();await stopping;
  assert.equal(r.input.value,'Existing draft final words');
 }
}
// A cancelled old finalization cannot tear down a new session after BFCache return.
{
 const r=rig();await r.click();const old=r.instances[0];
 const oldStop=r.binding.stop();await r.binding.stop('pagehide');
 await r.click();const fresh=r.instances[1];assert.ok(fresh);assert.equal(r.input.readOnly,true);
 old.finish.resolve();await oldStop;
 assert.equal(r.input.readOnly,true);assert.equal(r.send.disabled,true);
 fresh.options.onTranscript('New session draft');assert.equal(r.input.value,'New session draft');
 const newStop=r.binding.stop();fresh.finish.resolve();await newStop;
 assert.equal(r.input.readOnly,false);assert.equal(r.send.disabled,false);
}
// Actual host wiring remains clickable while the optional legacy fetch hangs.
{
 const template=fs.readFileSync(new URL('../domains/cos/templates/cos_ui.html',import.meta.url),'utf8');
 const code=template.split('<script type="module">')[1].split('</script>')[0].replace(/^import .*;$/gm,'');
 const gate=deferred(),nodes={};let starts=0;
 const node=id=>nodes[id]??=(id==='voice-mode'?{value:'realtime',options:[{value:'realtime'},{value:'chained'}]}:
   {disabled:false,value:'',classList:{toggle(){}},setAttribute(){},handlers:{},addEventListener(k,fn){this.handlers[k]=fn;}});
 const sandbox={document:{getElementById:node,querySelector:()=>null,addEventListener(){}},window:{addEventListener(){}},
   ConferVoiceController:class{availableProviders(){return gate.promise;}stop(){}},
   RealtimeVoiceController:class{async startSession(){starts++;}endSession(){}},
   bindConferDictation:()=>({stop:async()=>{},setAvailable(){}}),conferVoiceAvailability,appendMsg(){},setTimeout,Blob,URL};
 vm.createContext(sandbox);
 const helper=template.slice(template.indexOf('function cosWriteHeaders() {'),template.indexOf('async function sendMessage() {'));
 vm.runInContext(helper,sandbox);vm.runInContext(code,sandbox);
 assert.equal(nodes['btn-voice'].disabled,false);assert.equal(typeof nodes['btn-voice'].handlers.click,'function');
 await nodes['btn-voice'].handlers.click();assert.equal(starts,1);
 gate.resolve({providers:[]});await Promise.resolve();await Promise.resolve();
 assert.equal(nodes['btn-voice'].disabled,false);
}
console.log('8 functional dictation/readiness scenarios passed; no network or microphone calls.');
