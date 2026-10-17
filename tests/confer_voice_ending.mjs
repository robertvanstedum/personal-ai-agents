import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {conferVoiceAvailability} from '../core/realtime_voice/static/confer-input-controls.js';
const template=fs.readFileSync(new URL('../domains/cos/templates/cos_ui.html',import.meta.url),'utf8');
const code=template.split('<script type="module">')[1].split('</script>')[0].replace(/^import .*;$/gm,'');
const helper=template.slice(template.indexOf('function cosWriteHeaders() {'),template.indexOf('async function sendMessage() {'));
const flush=()=>new Promise(r=>setImmediate(r));
for(const confirmed of [true,false]){
 const nodes={};let endResolve,realtimeStarts=0,legacyStarts=0,opts;
 const gate=new Promise(r=>endResolve=r);
 const node=id=>nodes[id]??=(id==='voice-mode'?{value:'realtime',options:[{value:'realtime'},{value:'chained'}]}:
  {disabled:false,value:'',textContent:'',classList:{toggle(){}},setAttribute(){},handlers:{},addEventListener(k,fn){this.handlers[k]=fn;}});
 const sandbox={document:{getElementById:node,querySelector:()=>null,addEventListener(){}},window:{addEventListener(){}},
  ConferVoiceController:class{async availableProviders(){return{providers:[{provider:'openai'}]}}async start(){legacyStarts++;}stop(){}},
  RealtimeVoiceController:class{
   constructor(o){opts=o;}
   async startSession(){realtimeStarts++;opts.onStateChange('active');}
   async endSession(){opts.onStateChange('ending');await gate;if(!confirmed)opts.onFatalError({reason:'termination_uncertain',detail:'Unconfirmed; do not retry yet.'});opts.onStateChange('ended');}
  },
  bindConferDictation:()=>({stop:async()=>{},setAvailable(){}}),conferVoiceAvailability,cosWriteHeaders:()=>({}),appendMsg(){},setTimeout,Blob,URL};
 vm.createContext(sandbox);vm.runInContext(helper+code,sandbox);await flush();
 const click=()=>nodes['btn-voice'].handlers.click();
 await click();assert.equal(realtimeStarts,1);
 const end=click();await flush();assert.equal(nodes['voice-mode'].disabled,true);assert.equal(nodes['btn-voice'].disabled,true);
 nodes['voice-mode'].value='chained';await click();assert.equal(legacyStarts,0);
 endResolve();await end;
 assert.equal(nodes['btn-voice'].disabled,!confirmed);assert.equal(nodes['voice-mode'].disabled,!confirmed);
 await click();assert.equal(legacyStarts,confirmed?1:0);
 assert.equal(nodes['send-input']?.disabled||false,false);
}
console.log('2 actual CoS host pending-End/uncertainty gates passed; no calls.');
