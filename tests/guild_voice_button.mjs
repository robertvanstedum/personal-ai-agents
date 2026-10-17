import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const root=process.env.GUILD_VOICE_REPO||new URL('../',import.meta.url).pathname;
const helperPath=process.env.GUILD_VOICE_HELPER||`${root}/minimoi_portal/guild_ui/static/js/voice_button.js`;
const hostPath=process.env.GUILD_VOICE_HOST||`${root}/minimoi_portal/guild_ui/static/js/mc_voice.js`;
const {paintVoiceButton}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(helperPath,'utf8')).toString('base64'));
const host=fs.readFileSync(hostPath,'utf8').replace(/^import .*;$/gm,'').replace('export function initMcVoice','function initMcVoice').replace('await import(`${api}/mc/voice/static/realtime-voice-controller.js`)','await importControlled()');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function button(rich=true){
 const children={'[data-voice-label]':{textContent:'Voice'},'[data-voice-icon="mic"]':{hidden:false},'[data-voice-icon="stop"]':{hidden:true}};
 return {attrs:{},children,textContent:'Voice',hidden:true,disabled:false,setAttribute(k,v){this.attrs[k]=v},addEventListener(k,f){this[k]=f},
 ...(rich?{querySelector:s=>children[s]||null}:{})};
}
for(const state of ['idle','connecting','listening','working','speaking','ending','error']){
 const b=button(),active=['connecting','listening','working','speaking'].includes(state);
 paintVoiceButton(b,{state,active});
 assert.equal(b.attrs['data-voice-state'],state);assert(b.attrs['aria-label']);
 assert.equal(b.children['[data-voice-icon="mic"]'].hidden,active||state==='ending');
 assert.equal(b.children['[data-voice-icon="stop"]'].hidden,!(active||state==='ending'));
 assert(b.children['[data-voice-label]'].textContent.length>0);
 assert.equal(b.hidden,true); // helper cannot activate the feature
}
{
 const b=button(false);paintVoiceButton(b,{state:'ending',active:true});assert.equal(b.textContent,'Ending voice…');
 paintVoiceButton(b,{state:'idle'});assert.equal(b.textContent,'Voice');
}
async function rig({rich=true}={}){
 const nodes={},instances=[];let releaseStart,releaseEnd;
 const start=new Promise(r=>releaseStart=r),end=new Promise(r=>releaseEnd=r);
 const b=button(rich);nodes['[data-mc-voice-btn]']=b;
 const node=s=>nodes[s]??={textContent:'',hidden:false,disabled:false,checked:false,value:s.includes('provider')?'openai':'',setAttribute(){},addEventListener(){}};
 class Controller{constructor(options){this.options=options;instances.push(this)}async startSession(){await start;this.options.onStateChange('active')}endSession(){this.options.onStateChange('ending');return end.then(()=>this.options.onStateChange('ended'))}}
 const sandbox={$:node,paintVoiceButton,announce(){},live:{known:true,off:false},onChange(){},recordMode:()=> 'on',submitVoiceUtterance(){throw Error('no request')},panelMode:()=> 'open',
 document:{body:{dataset:{mcVoice:'true'}},addEventListener(){},dispatchEvent(){}},CustomEvent:class{},MutationObserver:class{observe(){}},window:{addEventListener(){}},
 fetch:async()=>({ok:true,json:async()=>({ok:true,providers:[{provider:'openai'}],managed_realtime:{session_url:'/synthetic'}})}),importControlled:async()=>({RealtimeVoiceController:Controller})};
 vm.createContext(sandbox);vm.runInContext(host,sandbox);sandbox.initMcVoice({urls:{api:'/api'},csrf_token:'synthetic',conversation:{id:'synthetic'}});await flush();
 return{b,node,instances,releaseStart,releaseEnd,async click(){b.click();await flush()}};
}
{
 const r=await rig();assert.equal(r.b.hidden,false);await r.click();assert.equal(r.b.attrs['data-voice-state'],'connecting');assert.equal(r.b.children['[data-voice-icon="stop"]'].hidden,false);
 r.releaseStart();await flush();assert.equal(r.b.attrs['data-voice-state'],'listening');
 const c=r.instances[0];c.options.onInputState('consulting');assert.equal(r.b.attrs['data-voice-state'],'working');
 c.options.onInputState('speaking');assert.equal(r.b.attrs['data-voice-state'],'speaking');
 assert.equal(r.node('[data-mc-voice-status]').textContent,'Speaking…');
 c.options.onInputState('speech_started');assert.equal(r.b.attrs['data-voice-state'],'listening');
 await r.click();assert.equal(r.b.attrs['data-voice-state'],'ending');assert.equal(r.b.disabled,true);assert.equal(r.b.children['[data-voice-icon="stop"]'].hidden,false);
 r.releaseEnd();await flush();assert.equal(r.b.attrs['data-voice-state'],'idle');assert.equal(r.b.disabled,false);
 assert.equal(r.node('[data-dictate]').textContent,''); // voice never changes separate dictation control
}
{
 const r=await rig();await r.click();r.releaseStart();await flush();const c=r.instances[0];
 c.options.onFatalError({reason:'voice_session_unresolved',detail:'private detail'});assert.equal(r.b.attrs['data-voice-state'],'error');assert.equal(r.b.disabled,true);
 c.options.onStateChange('ended');c.options.onInputState('speaking');assert.equal(r.b.attrs['data-voice-state'],'error');assert(!r.node('[data-mc-voice-status]').textContent.includes('private detail'));
}
{
 const r=await rig({rich:false});await r.click();assert.equal(r.b.textContent,'Connecting…');r.releaseStart();await flush();assert.equal(r.b.textContent,'Stop voice');
 await r.click();r.releaseEnd();await flush();assert.equal(r.b.textContent,'Voice');
}
console.log('11 functional voice-button state/fallback/host lifecycle/privacy scenarios passed; no real mic or provider.');
