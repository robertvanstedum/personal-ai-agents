import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here=path.dirname(fileURLToPath(import.meta.url));
const source=fs.readFileSync(path.join(here,'../minimoi_portal/guild_ui/static/js/agent_requests.js'),'utf8').replace(/^import .*;\n/gm,'').replace('export function','function');
async function fixture(responses){
 const timers=[];let status=null,calls=0;
 const window={setTimeout(fn,ms){const t={fn,ms,live:true};timers.push(t);return t;},clearTimeout(t){if(t)t.live=false;},addEventListener(){}};
 const document={hidden:false,addEventListener(){}};
 const thread={children:[],after(s){status=s;},querySelector(){return null;},append(){}};
 const start=new Function('window','document','apiPost','announce','el','live','onChange',source+';return initAgentRequests;');
 const init=start(window,document,async()=>{calls++;return responses[Math.min(calls-1,responses.length-1)];},()=>{},()=>({hidden:true,textContent:''}),{off:false},()=>{});
 init({page:{agent_requests:{enabled:true},conversation:{id:'c-synthetic'}},thread,noteLine(){throw Error('unexpected note');},follow(){}});
 const flush=()=>new Promise(r=>setImmediate(r));await flush();
 return {timers,flush,get status(){return status;},get calls(){return calls;}};
}
const unkept={ok:true,body:{notes:[],outstanding:0,unkept:1}};
{
 const f=await fixture([unkept]);assert.match(f.status.textContent,/kept in the request ledger/);assert.equal(f.status.hidden,false);
 for(let i=0;i<5;i++){const t=f.timers.find(t=>t.live);assert(t);assert.deepEqual(t.ms,[3000,6000,12000,30000,60000][i]);t.live=false;t.fn();await f.flush();}
 assert.equal(f.calls,6);assert.equal(f.timers.filter(t=>t.live).length,0);assert.match(f.status.textContent,/Could not refresh agent replies/);
}
{
 const f=await fixture([unkept,{ok:true,body:{notes:[],outstanding:0,unkept:0}}]);const t=f.timers.find(t=>t.live);t.live=false;t.fn();await f.flush();assert.equal(f.calls,2);assert.equal(f.status.hidden,true);assert.equal(f.timers.filter(t=>t.live).length,0);
}
console.log('2 agent-report sync browser fixtures passed; no requests or live ledger');
