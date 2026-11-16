import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
const source=fs.readFileSync('minimoi_portal/guild_ui/static/js/tasks.js','utf8').replace(/^import .*$/m,'').replace(/export /g,'');
class El {
 constructor(tag){this.tag=tag;this.children=[];this.textContent='';this.disabled=false;this.events={};this.value='';}
 append(...items){this.children.push(...items);} replaceChildren(...items){this.children=items;}
 addEventListener(name,fn){this.events[name]=fn;} focus(){}
 all(){return [this,...this.children.flatMap(c=>c.all())];}
 querySelectorAll(){return this.all().filter(e=>['button','textarea'].includes(e.tag));}
}
const proposal={interpretation_id:'IN-test-0001',proposal_digest:'sha256:'+'a'.repeat(64),proposed:{outcome:'Request outcome',scope:'personal',recipients:['principal'],continuing:true,task:{outcome:'Actual task outcome',lead:'mc',holder:'cos',done_when:'Reviewed',return_window:'tomorrow'}},sources:[{content:'<img onerror=bad()>',source_event_id:'SE-test-0001'}]};
const task={task_id:'TK-test-0001',revision:1,state:'working',outcome:'Actual task outcome',lead:'mc',holder:'cos',done_when:'Reviewed'};
const settle=()=>new Promise(r=>setImmediate(r));
async function rig({search='',get,post,storage=new Map()}={}){
 const root=new El('main'), fields={};
 for(const name of ['status','proposals','open','detail','record','refresh']){fields[name]=new El(name==='refresh'?'button':'div');root.append(fields[name]);}
 root.querySelector=s=>fields[s.match(/data-task-(.+)\]/)[1]];
 const calls=[];let url=new URL('https://synthetic.test/tasks'+search);
 const context={document:{createElement:t=>new El(t),querySelector:()=>root},crypto:webcrypto,TextEncoder,URL,URLSearchParams,window:{location:{href:url.href}},location:{search:url.search},history:{replaceState:(_,__,v)=>{url=new URL(v);context.window.location.href=url.href;context.location.search=url.search;}},sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},apiGet:async p=>get?get(p):({ok:true,body:p.startsWith('/tasks/item')?{task}:{proposals:[proposal],work:[]}}),apiPost:async(p,b)=>{calls.push({p,b});return post?post(p,b):{ok:false,status:0};}};
 vm.createContext(context);vm.runInContext(source+'\ninitTasks({task_owner:"owner:synthetic",urls:{api:"/api/v1"}});',context);
 for(let i=0;i<6;i++)await settle();
 return {root,fields,calls,storage,click:async text=>{const el=root.all().find(e=>e.tag==='button'&&e.textContent===text);assert.ok(el,text);await el.events.click();for(let i=0;i<6;i++)await settle();},text:()=>root.all().map(e=>e.textContent).join('\n')};
}
{
 const r=await rig();assert.equal(r.calls.length,0);for(const text of ['Actual task outcome','Request outcome','tomorrow','personal','principal','Yes','<img onerror=bad()>'])assert.ok(r.text().includes(text),text);
 await r.click('Confirm this work');assert.equal(r.calls[0].b.proposal_digest,proposal.proposal_digest);assert.match(r.fields.status.textContent,/not confirmed/);
 await r.click('Confirm this work');assert.equal(r.calls[0].b.idempotency_key,r.calls[1].b.idempotency_key);
}
{
 const storage=new Map();const a=await rig({search:'?task='+task.task_id,storage});a.root.all().find(e=>e.tag==='textarea').value='same correction';await a.click('Record correction');
 const b=await rig({search:'?task='+task.task_id,storage,get:p=>({ok:true,body:p.startsWith('/tasks/item')?{task:{...task,revision:2}}:{proposals:[],work:[]}})});b.root.all().find(e=>e.tag==='textarea').value='same correction';await b.click('Record correction');assert.equal(a.calls[0].b.idempotency_key,b.calls[0].b.idempotency_key);assert.ok(!JSON.stringify([...storage]).includes('same correction'));
}
{
 let confirmed=false;const r=await rig({get:p=>p.startsWith('/tasks/item')?{ok:false,body:{message:'Task read unavailable'}}:{ok:true,body:{proposals:confirmed?[]:[proposal],work:[]}},post:()=>{confirmed=true;return {ok:true,body:{state:'confirmed',task_id:task.task_id}};}});await r.click('Confirm this work');assert.equal(r.fields.status.textContent,'Task read unavailable');
}
{
 const r=await rig({search:'?task='+task.task_id,get:p=>({ok:true,body:p.startsWith('/tasks/item')?{task:{...task,state:'canceled'}}:{proposals:[],work:[]}})});assert.ok(!r.root.all().some(e=>e.textContent==='Cancel task'));
}
{
 const work=[{type:'task',id:'TK-hc-day1',state:'working',outcome:'Daily job-health note',lead:'mc',holder:'apprentice',done_when:'{"date": "2026-11-14"}',created_at:'2026-11-14T15:00:00+00:00',updated_at:'2026-11-14T15:10:00+00:00'},{type:'task',id:'TK-hc-day2',state:'working',outcome:'Daily job-health note',lead:'mc',holder:'apprentice',done_when:'{"date": "2026-11-15"}',created_at:'2026-11-15T15:00:00+00:00',updated_at:'2026-11-15T15:10:00+00:00'}];
 const taskFor=id=>({task_id:id,revision:2,state:'working',outcome:'Daily job-health note',lead:'mc',holder:'apprentice',done_when:id.endsWith('day1')?'{"date": "2026-11-14"}':'{"date": "2026-11-15"}',created_at:'2026-11-15T15:00:00+00:00',updated_at:'2026-11-15T16:00:00+00:00'});
 const get=p=>p.startsWith('/tasks/item/')?{ok:true,body:{task:taskFor(decodeURIComponent(p.split('/').pop()))}}:{ok:true,body:{proposals:[],work}};
 const r=await rig({get});
 const text=r.text();
 assert.ok(text.includes('TK-hc-day1')&&text.includes('TK-hc-day2')&&text.includes('2026-11-14')&&text.includes('2026-11-15'),text);
 assert.ok(text.includes('With')&&!text.includes('Next step'));
 const buttons=r.root.all().filter(e=>e.tag==='button'&&e.textContent==='Open task');
 assert.equal(buttons.length,2);
 await buttons[1].events.click();
 for(let i=0;i<8;i++)await settle();
 assert.equal(r.fields.status.textContent,'Showing task TK-hc-day2.');
 const card=r.fields.open.children[1];
 assert.equal(card.className,'task-card is-open');
 assert.equal(r.fields.open.children[0].className,'task-card');
 assert.ok(card.detailSlot.all().some(e=>e.tag==='textarea'));
 assert.ok(card.detailSlot.all().map(e=>e.textContent).join('\n').includes('TK-hc-day2'));
 assert.ok(card.detailSlot.all().map(e=>e.textContent).join('\n').includes('2026-11-15T16:00:00+00:00'));
 assert.equal(r.fields.record.children.length,0);
 assert.equal(r.fields.detail.hidden,true);
}
{
 const work=[{type:'task',id:'TK-hc-day2',state:'working',outcome:'Daily job-health note',lead:'mc',holder:'apprentice',done_when:'{"date": "2026-11-15"}',created_at:'2026-11-15T15:00:00+00:00',updated_at:'2026-11-15T15:10:00+00:00'}];
 const get=p=>p.startsWith('/tasks/item/')?{ok:true,body:{task:{task_id:'TK-hc-day2',revision:1,state:'working',outcome:'Daily job-health note',lead:'mc',holder:'apprentice',done_when:'{"date": "2026-11-15"}',created_at:'2026-11-15T15:00:00+00:00',updated_at:'2026-11-15T16:00:00+00:00'}}}:{ok:true,body:{proposals:[],work}};
 const r=await rig({search:'?task=TK-hc-day2',get});
 assert.equal(r.fields.status.textContent,'Showing task TK-hc-day2.');
 assert.equal(r.fields.open.children[0].className,'task-card is-open');
 assert.ok(r.fields.open.children[0].detailSlot.all().some(e=>e.textContent==='Id'));
}
{
 const r=await rig({search:'?task=not-a-task',get:()=>({ok:true,body:{proposals:[],work:[]}})});
 assert.equal(r.fields.status.textContent,'Choose a tracked task.');
 assert.ok(!r.fields.status.textContent.startsWith('Showing task'));
}
{
 const r=await rig({get:()=>({ok:true,body:{proposals:[proposal],work:[]}})});
 assert.ok(r.text().includes('With')&&!r.text().includes('Next step'));
}
console.log('task UI behavioral checks passed');
