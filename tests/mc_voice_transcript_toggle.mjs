import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(process.argv[2]||new URL('../minimoi_portal/guild_ui/static/js/mc_voice.js',import.meta.url),'utf8')
 .replace(/^import .*;$/gm,'').replace('export function initMcVoice','function initMcVoice')
 .replace('await import(`${api}/mc/voice/static/realtime-voice-controller.js`)','await importControlled()');
const flush=()=>new Promise(r=>setImmediate(r));
async function rig({endGate=null,savedGate=null,delayedFinalize=false,savedFailure=false,stall=null}={}){
 const nodes={},instances=[],events={},windowEvents={},changes=[],savedRequests=[];
 const node=s=>nodes[s]??={textContent:'',value:s.includes('provider')?'openai':'',disabled:false,hidden:false,open:false,checked:false,attrs:{},setAttribute(k,v){this.attrs[k]=v},addEventListener(k,f){this[k]=f}};
 const document={body:{dataset:{mcVoice:'true'}},dispatchEvent(){},addEventListener(k,f){events[k]=f}};
 class Controller{constructor(options){this.options=options;instances.push(this)}async startSession(){this.options.onStateChange('active')}endSession(){if(!delayedFinalize)this.options.onFinalize({turns:this.finalTurns||[]});if(endGate)return endGate.then(()=>this.options.onStateChange('ended'));this.options.onStateChange('ended')}}
 const sandbox={$:node,announce(){},live:{known:true,off:false},onChange(fn){changes.push(fn)},recordMode:()=> 'on',submitVoiceUtterance(){throw Error('no submit')},panelMode:()=> 'open',
 document,AbortController,setTimeout:stall?fn=>{stall.deadline=fn;return 1}:setTimeout,clearTimeout:stall?()=>{}:clearTimeout,CustomEvent:class{},MutationObserver:class{observe(){}},window:{addEventListener(k,f){windowEvents[k]=f}},fetch:async(url,options)=>{if(url.endsWith('/voice-transcript')){savedRequests.push({url,options});if(stall?.kind==='fetch')await new Promise(r=>stall.release=r);if(savedGate)await savedGate;return {ok:!savedFailure,headers:{get:()=> 'text/plain; charset=utf-8'},text:async()=>{if(stall?.kind==='body')await new Promise(r=>stall.release=r);return `Saved same-conversation voice transcript ${savedRequests.length}.`}}}return {ok:true,json:async()=>({ok:true,providers:[{provider:'openai'}],managed_realtime:{session_url:'/fake'}})}},importControlled:async()=>({RealtimeVoiceController:Controller})};
 vm.createContext(sandbox);vm.runInContext(source,sandbox);const page={urls:{api:'/api'},csrf_token:'synthetic',conversation:{id:'synthetic-conversation'}};sandbox.initMcVoice(page);await flush();
 return {nodes,instances,node,savedRequests,live:sandbox.live,page,changes,async click(){node('[data-mc-voice-btn]').click();await flush()},status:()=>node('[data-mc-voice-status]').textContent};
}

// Opt in while active: show already-observed turns without restarting any session.
{
 const r=await rig();await r.click();const c=r.instances[0],choice=r.node('[data-mc-voice-transcript-choice]');
 assert.equal(choice.disabled,false);
 c.options.onTranscriptUpdate([{speaker:'user',text:'Synthetic first utterance',completed:true}]);
 assert.equal(r.node('[data-mc-voice-transcript]').hidden,true);
 assert.equal(r.savedRequests.length,0);c.options.onInputState('speaking');assert.equal(r.status(),'Speaking…');
 choice.checked=true;choice.change();
 assert.equal(r.node('[data-mc-voice-transcript]').open,true);
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/You: Synthetic first utterance/);
 assert.equal(r.node('[data-mc-voice-transcript]').hidden,false);
 assert.equal(r.instances.length,1);
}
// Switching display off and on follows live choice; partial output is labelled.
{
 const r=await rig();await r.click();const c=r.instances[0],choice=r.node('[data-mc-voice-transcript-choice]');
 choice.checked=true;choice.change();
 c.options.onTranscriptUpdate([{speaker:'assistant',text:'Synthetic reply',completed:false}]);
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/Voice: Synthetic reply.*partial/);
 choice.checked=false;choice.change();assert.equal(r.node('[data-mc-voice-transcript]').hidden,true);
 assert.equal(r.node('[data-mc-voice-transcript-text]').textContent,'');
 choice.checked=true;choice.change();assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/Synthetic reply/);
}
// End displays the real saved export with live display OFF; checkbox never hides it.
{
 const r=await rig();await r.click();const c=r.instances[0];assert.equal(c.options.includeTranscriptAfterEnd,true);
 c.finalTurns=[{speaker:'user',text:'Synthetic final turn',completed:true}];
 await r.click();const choice=r.node('[data-mc-voice-transcript-choice]');assert.equal(choice.checked,false);
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/Saved same-conversation voice transcript/);
 assert.equal(r.node('[data-mc-voice-transcript]').hidden,false);
 assert.equal(r.node('[data-mc-voice-transcript]').open,true);
 choice.change();assert.equal(r.node('[data-mc-voice-transcript]').hidden,false);
 assert.equal(r.savedRequests.length,1);assert.equal(r.savedRequests[0].options.method,undefined);
}
// Private clears display authority; returning on record cannot resurrect old text.
{
 const r=await rig();await r.click();const c=r.instances[0],choice=r.node('[data-mc-voice-transcript-choice]');choice.checked=true;
 c.options.onTranscriptUpdate([{speaker:'user',text:'Synthetic private-boundary sample',completed:true}]);
 r.live.off=true;r.changes.forEach(fn=>fn());
 assert.equal(r.node('[data-mc-voice-transcript]').hidden,true);assert.equal(r.node('[data-mc-voice-transcript-text]').textContent,'');
 r.live.off=false;choice.change();c.options.onTranscriptUpdate([{speaker:'user',text:'Stale callback',completed:true}]);
 assert.equal(r.node('[data-mc-voice-transcript-text]').textContent,'');
 await r.click();r.instances[1].options.onTranscriptUpdate([{speaker:'assistant',text:'Fresh attempt',completed:true}]);
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/Fresh attempt/);
 c.options.onTranscriptUpdate([{speaker:'assistant',text:'Stale replacement',completed:true}]);
 assert(!r.node('[data-mc-voice-transcript-text]').textContent.includes('Stale'));
}

// Delayed finalize and delayed End acknowledgement do not lose the saved display.
{
 let finish;const endGate=new Promise(resolve=>finish=resolve);
 const r=await rig({endGate,delayedFinalize:true});await r.click();const c=r.instances[0];
 c.options.onTranscriptUpdate([{speaker:'assistant',text:'Observed only',completed:true}]);
 await r.click();assert.equal(r.savedRequests.length,0);
 assert.equal(r.node('[data-mc-voice-transcript]').hidden,true);
 finish();await flush();await flush();
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/Saved same-conversation/);
 c.options.onFinalize({turns:[{speaker:'assistant',text:'Late browser finalize',completed:true}]});
 assert(!r.node('[data-mc-voice-transcript-text]').textContent.includes('Late browser'));
 assert.equal(r.savedRequests.length,1);
}
// Old GET/finalize cannot repaint a newer active voice attempt or another conversation.
{
 let finish;const savedGate=new Promise(resolve=>finish=resolve);
 const r=await rig({savedGate});await r.click();const old=r.instances[0];await r.click();
 assert.equal(r.savedRequests.length,1);await r.click();
 finish();await flush();await flush();
 old.options.onFinalize({turns:[{speaker:'assistant',text:'STALE',completed:true}]});
 assert.equal(r.node('[data-mc-voice-transcript]').hidden,true);
 assert.equal(r.node('[data-mc-voice-transcript-text]').textContent,'');
 r.page.conversation.id='new-conversation';
 r.instances[1].options.onTranscriptUpdate([{speaker:'assistant',text:'WRONG CONVERSATION',completed:true}]);
 assert.equal(r.node('[data-mc-voice-transcript-text]').textContent,'');
}
// Private aborts outstanding saved read, and returning on record grants no stale display.
{
 let finish;const savedGate=new Promise(resolve=>finish=resolve);
 const r=await rig({savedGate});await r.click();const old=r.instances[0];await r.click();
 r.live.off=true;r.changes.forEach(fn=>fn());assert.equal(r.savedRequests[0].options.signal.aborted,true);
 r.live.off=false;finish();await flush();await flush();
 old.options.onFinalize({turns:[{speaker:'assistant',text:'PRIVATE STALE',completed:true}]});
 assert.equal(r.node('[data-mc-voice-transcript]').hidden,true);
 assert.equal(r.node('[data-mc-voice-transcript-text]').textContent,'');
}
// Each End reloads this persistent conversation's saved history, never POSTS browser text.
{
 const r=await rig();await r.click();await r.click();
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/transcript 1/);
 await r.click();assert.equal(r.node('[data-mc-voice-transcript]').hidden,true);await r.click();
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/transcript 2/);
 assert.equal(r.savedRequests[0].url,r.savedRequests[1].url);
}
// An unavailable saved export must not relabel the observed browser buffer as saved.
{
 const r=await rig({savedFailure:true});await r.click();
 r.instances[0].finalTurns=[{speaker:'assistant',text:'OBSERVED NOT PROVEN SAVED',completed:true}];await r.click();
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/Saved voice transcript is unavailable/);
 assert(!r.node('[data-mc-voice-transcript-text]').textContent.includes('OBSERVED NOT PROVEN SAVED'));
}
console.log('9 live-only-toggle/saved-after-End/delayed-finalize/privacy/stale/persistent-conversation scenarios passed; synthetic controller/GET only.');


for(const kind of ['fetch','body']){
 const stall={kind},r=await rig({stall});await r.click();await r.click();
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/Loading saved/);
 stall.deadline();await flush();
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/unavailable/);
 assert.equal(r.savedRequests[0].options.signal.aborted,true);
 stall.release();await flush();
 assert.match(r.node('[data-mc-voice-transcript-text]').textContent,/unavailable/);
}
// Ordinary incoming updates respect the owner's manual disclosure collapse.
{
 const r=await rig();await r.click();const choice=r.node('[data-mc-voice-transcript-choice]');
 choice.checked=true;choice.change();r.node('[data-mc-voice-transcript]').open=false;
 r.instances[0].options.onTranscriptUpdate([{speaker:'user',text:'Still collapsed',completed:true}]);
 assert.equal(r.node('[data-mc-voice-transcript]').open,false);
}
console.log('3 saved-read deadline/late-result/manual-collapse scenarios passed.');
