import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const html=fs.readFileSync(new URL('../domains/cos/templates/cos_ui.html',import.meta.url),'utf8');
const helper=html.slice(html.indexOf('function cosWriteHeaders() {'),html.indexOf('async function sendMessage() {'));
const send=html.slice(html.indexOf('async function sendMessage() {'),html.indexOf('function setInputDisabled('));
// The page's nodes as its attachment and Files from CoS wiring (c13e0fba) looks them up at load: they take listeners, text
// and children, nothing more (Spec 165 §12.1's missing DOM mock). The token is read through querySelector, as the page does.
const historyWindow=()=>({cosHistory:{ready:true,conversationId:'cosc-synthetic',device:'synthetic',requestId:()=> 'synthetic-request',persistDraft(){},confirm(){},uncertain(){}}});
const pageDocument=(token)=>({querySelector:()=>({content:token()}),
 getElementById:()=>({textContent:'',value:'',files:[],addEventListener(){},appendChild(){},click(){}})});
// Actual typed handler must send the current portal token and preserve a rejected draft without retry.
{
 let token='synthetic-token-1',status=403;const sent=[],messages=[];
 const input={value:'Retain this dictated draft',disabled:false,readOnly:false,focus(){}};
 const sandbox={window:historyWindow(),sendInput:input,document:pageDocument(()=>token),crypto:{randomUUID:()=> 'synthetic-request'},
   setInputDisabled:value=>{input.disabled=value},appendMsg:(role,text)=>messages.push([role,text]),appendThinking:()=>({remove(){}}),
   fetch:async(url,options)=>{sent.push({url,options});return {status,ok:status===200,json:async()=>status===200?{reply:'Actual accepted response'}:{error:'csrf',message:'Request refused'}}}};
 vm.createContext(sandbox);vm.runInContext(helper+send,sandbox);
 await sandbox.sendMessage();assert.equal(sent.length,1);assert.equal(sent[0].options.headers['X-CSRF-Token'],token);
 assert.equal(input.value,'Retain this dictated draft');assert.match(messages.at(-1)[1],/Message was not sent/);
 token='synthetic-token-2';status=200;await sandbox.sendMessage();assert.equal(sent.length,2);
 assert.equal(sent[1].options.headers['X-CSRF-Token'],token);assert.equal(messages.at(-1)[1],'Actual accepted response');
}
// Actual chained controller must forward headers on BOTH bootstrap and submitted turns.
{
 const source=fs.readFileSync(new URL('../core/realtime_voice/static/realtime-confer-controller.js',import.meta.url),'utf8')
   .replace(/^import .*;$/gm,'').replace('export class ConferVoiceController','class ConferVoiceController')+'\nthis.Controller=ConferVoiceController;';
 const calls=[];const sandbox={fetch:async(url,options)=>{calls.push({url,options});return{ok:false,json:async()=>({error:'synthetic refusal'})}}};
 vm.createContext(sandbox);vm.runInContext(source,sandbox);
 const c=new sandbox.Controller({bootstrapUrl:'/synthetic/bootstrap',turnUrl:'/synthetic/send',requestHeaders:()=>({'X-CSRF-Token':'synthetic-current-token'})});
 c._active=true;
 await assert.rejects(()=>c._openSession(),/synthetic refusal/);
 await assert.rejects(()=>c._submitTurn('synthetic words','synthetic-id'),/synthetic refusal/);
 assert.equal(calls.length,2);assert.ok(calls.every(x=>x.options.headers['X-CSRF-Token']==='synthetic-current-token'));
}
// Non-JSON and uncertain network failures preserve the draft, without redispatch.
for(const fail of ['html403','network','html200','null200','array200','empty200','number200','missing200']) {
 let calls=0;const messages=[],input={value:'Do not lose this draft',disabled:false,readOnly:false,focus(){}};
 const sandbox={window:historyWindow(),sendInput:input,document:pageDocument(()=>'synthetic'),crypto:{randomUUID:()=> 'synthetic'},
   setInputDisabled:v=>{input.disabled=v},appendMsg:(role,text)=>messages.push(text),appendThinking:()=>({remove(){}}),
   fetch:async()=>{calls++;if(fail==='network')throw Error('network unavailable');const accepted=fail.endsWith('200');return{status:accepted?200:403,ok:accepted,json:async()=>{if(fail.startsWith('html'))throw Error('HTML response');return {null200:null,array200:[],empty200:{reply:'  '},number200:{reply:42},missing200:{}}[fail]}}}};
 vm.createContext(sandbox);vm.runInContext(helper+send,sandbox);await sandbox.sendMessage();
 assert.equal(calls,1);assert.equal(input.value,'Do not lose this draft');assert.match(messages.at(-1),/unconfirmed/);
}
console.log('10 actual Confer typed/chained authentication and malformed-response scenarios passed; no network/provider calls.');
