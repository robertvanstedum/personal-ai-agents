import fs from 'node:fs';
import assert from 'node:assert/strict';
const root=new URL('../core/realtime_voice/static/',import.meta.url).pathname;
class Adapter {
  constructor(){this.listeners={};this.instructions=[];this.results=[];globalThis.lastAdapter=this;}
  on(n,fn){(this.listeners[n] ||= []).push(fn)}
  emit(n,e){for(const fn of this.listeners[n]||[])fn(e)}
  prepareSession(){}
  async connect(b){if(b.exchangeSdp){const a=await b.exchangeSdp('offer');this.emit('session_identity',{session_id:a.session_id})}this.emit('connected',{})}
  start(){} mute(){} unmute(){} stopCapture(){}
  end(){this.emit('closed',{})}
  sendContinuationInstruction(t){this.instructions.push(t)}
  sendFunctionResult(c,r){this.results.push([c,r])}
}
globalThis.Adapter=Adapter;
let source=fs.readFileSync(root+'realtime-voice-controller.js','utf8').replace(/^import .*;$/gm,'');
source='const OpenAIWebRTCAdapter=globalThis.Adapter,XAIWebSocketAdapter=globalThis.Adapter;\n'+source;
const {RealtimeVoiceController:C}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
let requests=[];
globalThis.fetch=async(url,opts)=>{requests.push([url,opts]);return {ok:true,json:async()=>url.endsWith('/end')?{termination_confirmed:true}:{ok:true,session_id:'real-server-id',sdp:'answer',provider:'openai',model:'gpt-realtime-2.1',warning_minutes:13,max_minutes:15}}};
let final;
const legacy=new C({bootstrapUrl:'/legacy',onFinalize:r=>final=r});
await legacy.startSession({provider:'openai'});assert(lastAdapter.instructions[0].startsWith('Begin in character'));legacy.endSession('user_ended');assert(final.session_id.startsWith('sess-'));
let toolRuns=0,finals=[];
const managed=new C({managedBootstrap:{sessionUrl:'/voice/session'},requestHeaders:()=>({'X-CSRF':'valid'}),onFunctionCall:()=>toolRuns++,onFinalize:r=>finals.push(r),includeTranscriptAfterEnd:false});
await managed.startSession({provider:'openai',conversation_id:'c1'});
const old=lastAdapter;
assert.equal(managed._sessionId,'real-server-id');assert.equal(old.instructions.length,0);
old.emit('function_call',{call_id:'call1',arguments:'{"approve":true}'});assert.equal(toolRuns,0);
old.emit('input_transcript',{item_id:'user1',text:'synthetic',completed:true});
await managed.endSession('user_ended');assert.deepEqual(finals[0].turns,[]);
assert(requests.some(([u,o])=>u==='/voice/session/real-server-id/end' && o.headers['X-CSRF']==='valid' && o.headers['Content-Type']==='application/json' && o.body==='{}'));
await managed.startSession({provider:'openai',conversation_id:'c1'});
old.emit('input_transcript',{item_id:'stale',text:'stale',completed:true});assert.equal(managed._items.size,0);
await managed.endSession('user_ended');
await assert.rejects(()=>managed.startSession({provider:'xai'}),/not verified/);
// Actual shared adapter: stale microphone acquisition stops newly returned tracks.
let stopCount=0,finishMic;
globalThis.document={createElement:()=>({})};
globalThis.RTCPeerConnection=class{close(){}createDataChannel(){return {close(){}}}addTrack(){throw Error('stale track attached')}};
Object.defineProperty(globalThis,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia:()=>new Promise(resolve=>finishMic=resolve)}}});
const adapterSource=fs.readFileSync(root+'adapters/openai-webrtc-adapter.js','utf8');
const {OpenAIWebRTCAdapter:A}=await import('data:text/javascript;base64,'+Buffer.from(adapterSource).toString('base64'));
const a=new A();const connecting=a.connect({isCurrent:()=>false,exchangeSdp:()=>{throw Error('stale provider request')}});
finishMic({getTracks:()=>[{stop:()=>stopCount++}],getAudioTracks:()=>[]});await connecting;assert.equal(stopCount,1);
// Live transcript callback follows normalized provider events and never dispatches.
const liveTurns=[];
const live=new C({managedBootstrap:{sessionUrl:'/voice/session'},onTranscriptUpdate:t=>liveTurns.push(t)});
await live.startSession({provider:'openai',conversation_id:'c1'});
lastAdapter.emit('input_transcript',{item_id:'u1',provider_event_id:'e1',text:'Grüß dich',completed:true});
lastAdapter.emit('output_transcript',{item_id:'a1',provider_event_id:'e2',text:'Guten ',is_delta:true,completed:false});
lastAdapter.emit('output_transcript',{item_id:'a1',provider_event_id:'e3',text:'Morgen',is_delta:true,completed:true});
assert.equal(liveTurns.at(-1)[1].text,'Guten Morgen');
assert.equal(liveTurns[1][1].text,'Guten '); // immutable earlier view
lastAdapter.emit('output_transcript',{item_id:'a1',provider_event_id:'e3',text:'duplicate',completed:true});
assert.equal(liveTurns.length,3);
await live.endSession('user_ended');
console.log('8 synthetic controller/adapter functional scenarios passed');
