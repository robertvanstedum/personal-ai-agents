import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../minimoi_portal/guild_ui/static/js/mc_interruption.js',import.meta.url),'utf8');
const {interruptions}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const row=(state,extra={})=>({note_request_id:'n1',conversation_id:'c1',state,...extra});
test('disconnect is unknown then authoritative running and saved, never a fabricated failure',()=>{
 const events=[];const r=interruptions((...e)=>events.push(e));r.lost('n1','c1','t1');
 r.reconcile({running:row('claimed')});r.reconcile({recent:[row('answered',{outcome:{reply_note_id:42}})]});
 assert.equal(r.recovered({id:42}),'n1');
 assert.deepEqual(events.map(e=>e[1]),['unknown','running','saved','recovered']);
 r.reconcile({running:row('claimed')});assert.equal(events.length,4);
});
test('other scope and older turn cannot settle this request',()=>{
 const events=[];const r=interruptions((...e)=>events.push(e));r.lost('n1','c1','t1');
 r.reconcile({running:row('claimed',{conversation_id:'other'})});
 assert.equal(r.recovered({id:3,turn:{turn_id:'older'}}),null);assert.equal(events.length,1);
 assert.equal(r.recovered({id:4,turn:{turn_id:'t1'}}),'n1');
});
test('repeated loss keeps original identity; terminal execution failure remains visible',()=>{
 const events=[];const r=interruptions((...e)=>events.push(e));r.lost('n1','c1','t1');r.lost('n1','c1');
 r.reconcile({recent:[row('failed')]});assert.equal(events.at(-1)[1],'failed');
 assert.equal(r.recovered({turn:{turn_id:'t1'}}),'n1');
});
test('cancellation and unknown never become success; a later request is not cleared',()=>{
 const events=[];const r=interruptions((...e)=>events.push(e));r.lost('n1','c1','t1');r.lost('n2','c1','t2');
 r.reconcile({recent:[row('cancelled')]});assert.equal(events.at(-1)[1],'cancelled');
 r.reconcile({recent:[row('unknown')]});assert.equal(events.at(-1)[1],'unknown');
 r.recovered({turn:{turn_id:'t1'}});r.reconcile({running:row('claimed',{note_request_id:'n2'})});
 assert.deepEqual(events.at(-1).slice(0,2),['n2','running']);
});

test('saved note arrives before ledger finalization after lost ack',()=>{
 const events=[];const r=interruptions((...e)=>events.push(e));r.lost('n1','c1');
 r.reconcile({running:row('claimed')});assert.equal(r.recovered({id:42}),null);
 r.reconcile({recent:[row('answered',{outcome:{reply_note_id:42}})]});
 assert.equal(events.at(-1)[1],'recovered');
});

// Exercise the production stream controller, not just the recovery reducer.
const conversation = await readFile(new URL('../minimoi_portal/guild_ui/static/js/conversation.js', import.meta.url), 'utf8');
const {runInNewContext} = await import('node:vm');
async function disconnected(events, throwsBeforeAck=false) {
 const states=[], lost=[], failures=[];
 const control={addEventListener(){}};
 const li={dataset:{},querySelector:()=>control,remove(){},stopTicking(){}};
 const data=events.map(e=>JSON.stringify(e)+'\n').join('');let read=false;
 const context={latestStreamRequest:null, AbortController,TextDecoder,Date,STREAM_LINE_CAP:100000,
  window:{addEventListener(){},removeEventListener(){}},streamLine:()=>li,
  apiStream:async()=>{if(throwsBeforeAck)throw Error('network');return {headers:{get:()=> 'application/x-ndjson'},body:{getReader:()=>({read:async()=>read?{done:true}:(read=true,{value:Buffer.from(data),done:false})})}}},
  turnBody:()=>({}),endLine(){},thread:{append(){}},platformLine(){},announce(){},follow(){},
  interrupted:{lost:(...args)=>lost.push(args)},answerFailed:x=>failures.push(x),
  mcSay:x=>x,slot:()=>({textContent:''}),setSlot:(li,k,v)=>states.push(v),showFiles(){},
  applyHeader(){},confirmedStop:()=>false,failureLine:x=>x,
 };
 const start=conversation.indexOf('async function streamMasterCraftsman(');
 const end=conversation.indexOf('function appendOffRecord(',start);
 runInNewContext(conversation.slice(start,end)+';globalThis.run=streamMasterCraftsman',context);
 await context.run({note:{request_id:'n1'},conversationId:'c1'});
 return {lost,failures,latest:context.latestStreamRequest};
}
test('real stream loss after ack does not report execution failure',async()=>{
 const r=await disconnected([{t:'ack',turn_id:'t1'}]);
 assert.deepEqual(r.failures,[]);assert.deepEqual(r.lost,[['n1','c1','t1']]);assert.equal(r.latest,'n1');
});
test('real transport failure before ack remains unknown without retry',async()=>{
 const r=await disconnected([],true);assert.deepEqual(r.failures,[]);assert.deepEqual(r.lost,[['n1','c1']]);
});

test('reload binds current ledger run before saved reply readback',()=>{
 const events=[];const r=interruptions((...e)=>events.push(e));
 r.track('n1','c1');r.reconcile({running:row('claimed')});
 assert.equal(r.recovered({id:12,turn:{turn_id:'historical'}}),null);
 r.reconcile({recent:[row('answered',{outcome:{reply_note_id:42}})]});
 assert.equal(r.recovered({id:42}),'n1');assert.equal(events.at(-1)[1],'recovered');
});
