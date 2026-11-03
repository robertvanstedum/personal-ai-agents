"""Offline execution of the Rooms picker/send/recovery UI, with no model calls."""
import shutil
import subprocess
from pathlib import Path
import pytest

JS = Path(__file__).resolve().parents[3] / "minimoi_portal/guild_ui/static/js/rooms.js"

SCRIPT = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
class Element {
  constructor(tag, attrs = {}, text = '') { this.tag = tag; this.attrs = attrs; this.textContent = text; this.children = []; this.handlers = {}; this.dataset = {}; for (const [k,v] of Object.entries(attrs)) if (k.startsWith('data-')) this.dataset[k.slice(5).replace(/-([a-z])/g, (_,c)=>c.toUpperCase())] = v; }
  append(...xs) { this.children.push(...xs); }
  replaceChildren(...xs) { this.children = xs; }
  querySelector(s) { return this.find(s)[0] || null; }
  find(s) { const key = s.slice(1,-1); return this.children.flatMap(x => x instanceof Element ? [...(key in x.attrs ? [x] : []), ...x.find(s)] : []); }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  hasAttribute(key) { return key in this.attrs; }
  closest() { return this; }
  focus() { context.document.activeElement = this; }
  setSelectionRange(start,end) { this.selectionStart=start; this.selectionEnd=end; }
}
const nodes = new Map();
const storage = new Map();
const $ = s => { if (!nodes.has(s)) nodes.set(s, new Element('div')); return nodes.get(s); };
const $$ = s => $('[data-rm-to]').find(s);
const context = vm.createContext({ $, $$, el: (t,a,v) => new Element(t,a,v), assert, console, URLSearchParams, announce(){},
  window: { location: { search: "" }, setInterval(){}, clearInterval(){}, clearTimeout(){}, sessionStorage: {getItem(k){return storage.get(k)},setItem(k,v){storage.set(k,v)},removeItem(k){storage.delete(k)}} },
  document: { addEventListener(){}, createTextNode(text){return new Element("#text",{},text);} }, newKey: () => 'test-key' });
const source = fs.readFileSync(process.argv[1], 'utf8').replace(/^import .*;$/mg,'').replace(/^export /mg,'');
vm.runInContext(source, context);
vm.runInContext(String.raw`
(async () => {
  // Availability, speaking eligibility, and actual work are separate states.
  meeting={turns:[]};
  const participant={id:'mc',label:'Master Craftsman',rsvp:'accepted',reach:{state:'reachable'}};
  assert.equal(participantState(participant),'Available');
  assert.equal(participantState({...participant,waiting:true}),'Waiting');
  meeting.turns=[{id:'working',addressee:'mc',state:'running'}];
  assert.equal(participantState({...participant,waiting:true}),'Working');
  assert.deepEqual([...turnLine(meeting.turns[0]).actions],['stop']);
  meeting.turns[0].state='cancel_requested';
  assert.equal(participantState({...participant,waiting:true}),'Stopping reply');
  meeting.turns[0].state='recovering';
  assert.ok(turnLine(meeting.turns[0]).text.includes('saved reply'));
  // Fold agent learning metadata without modifying or interpreting the record.
  const capture='Correction received.\n\n'+String.fromCharCode(96).repeat(3)+'capture\nowner-said: <img src=x onerror=alert(1)>\n'+String.fromCharCode(96).repeat(3)+'\nCaptured 1 correction pairs\nMore prose';
  const event={id:'capture-event',actor_kind:'agent',body:capture};
  const folded=eventBody(event);
  assert.equal(event.body,capture);
  assert.equal(folded.children[0].textContent,'Correction received.\n\n');
  const detail=folded.children[1]; assert.equal(detail.tag,'details'); assert.equal(detail.open,false);
  assert.equal(detail.children[0].textContent,'Learning note');
  assert.ok(detail.children[1].textContent.includes('<img src=x onerror=alert(1)>'));
  assert.equal(detail.children[1].children.length,0); // text, never executable HTML
  assert.equal(folded.children[2].textContent,'\nMore prose');
  detail.open=true; detail.handlers.toggle(); assert.equal(eventBody(event).children[1].open,true);
  detail.open=false; detail.handlers.toggle(); assert.equal(eventBody(event).children[1].open,false);
  assert.equal(eventBody({...event,actor_kind:'human'}).textContent,capture);
  const incomplete='Hello\n'+String.fromCharCode(96).repeat(3)+'capture\nunfinished';
  assert.equal(eventBody({...event,body:incomplete}).textContent,incomplete);
  const approval = eventBody({actor_kind:'agent',body:'Approve at https://dev.minimoi.ai/guild-next/guild/build?c=c-123'});
  assert.equal(approval.children[1].tag,'a');
  assert.equal(approval.children[1].attrs.href,'https://dev.minimoi.ai/guild-next/guild/build?c=c-123');
  current = 'room-a'; room = { id: current, state: 'active', events: [{actor:'robert',kind:'message',seq:20}] };
  page = { storage_ns: 'test', urls:{floor:'/guild-next/guild/build'} };
  const people = ['codex','claude-code','mc'];
  meeting = {room:current, participants:people.map(id=>({id,label:id,teammate:true,rsvp:'accepted'})), turns:[], routing_notes:[], meeting:{facilitator:'mc'}};
  let resolveOld;
  records=async()=>new Promise(resolve=>{resolveOld=resolve;});
  const oldRead=loadMeeting(); current='room-b';
  const newer={room:'room-b',participants:[],turns:[]}; meeting=newer;
  resolveOld({status:200,body:{room:'room-a',participants:[],turns:[]}}); await oldRead;
  assert.equal(meeting,newer); // a slow old room cannot replace the newly selected one
  current='room-a'; room.id=current;
  meeting={room:current,participants:people.map(id=>({id,label:id,teammate:true,rsvp:'accepted'})),turns:[],routing_notes:[],meeting:{facilitator:'mc'}};
  records=async()=>({status:503,body:{}}); await loadMeeting();
  assert.equal(meeting.room,current); assert.equal(meetingStale,true);
  assert.equal($('[data-rm-connection]').hidden,false);
  assert.ok($('[data-rm-participation]').find('[data-rm-participation-actor]').every(b=>b.disabled));
  meetingStale=false;
  let changed=0;
  records=async()=>{changed++;return {status:200,body:{}};};
  await changeParticipation(el('button',{'data-rm-participation-actor':'mc','data-waiting':'true','data-participation-room':'old-room'}));
  assert.equal(changed,0); // a stale control never mutates the selected room
  let completeFirst,completeSecond;
  records=async()=>new Promise(resolve=>{if(!completeFirst)completeFirst=resolve;else completeSecond=resolve;});
  const firstRead=loadMeeting(), secondRead=loadMeeting();
  completeSecond({status:503,body:{}}); await secondRead;
  const held=meeting;
  completeFirst({status:200,body:{room:current,participants:[],turns:[]}}); await firstRead;
  assert.equal(meeting,held); assert.equal(meetingStale,true);
  // A pending newer poll must not hide a completed timeout (poll15s, timeout20s).
  completeFirst=null; completeSecond=null;
  const timeoutRead=loadMeeting(), pendingNewerRead=loadMeeting();
  meetingStale=false;
  completeFirst({status:503,body:{}}); await timeoutRead;
  assert.equal(meetingStale,true);
  completeSecond({status:503,body:{}}); await pendingNewerRead;
  completeFirst=null; completeSecond=null;
  const roomTimeout=loadRoom(current), roomPending=loadRoom(current);
  meetingStale=false;
  completeFirst({status:503,body:{}}); await roomTimeout;
  assert.equal(meetingStale,true);
  completeSecond({status:503,body:{}}); await roomPending;
  meetingStale=false;
  let sent;
  records = async (url, options) => { sent = options; return {status:201,body:{}}; };
  loadRoom = async () => true;
  bindMeeting();
  renderTo();
  const click = attrs => $('[data-rm-to]').handlers.click({target:el('button',attrs)});
  click({'data-rm-to-chip':'codex'}); click({'data-rm-to-chip':'claude-code'});
  assert.equal(targets.size,2);
  $('[data-rm-input]').value = 'Confirm attendance'; await send();
  assert.equal(sent.body.body,'Confirm attendance');
  assert.equal(sent.body.target,undefined);
  assert.deepEqual([...sent.body.recipients],['codex','claude-code']);
  click({'data-rm-to-chip':'codex'});
  $('[data-rm-input]').value = 'Confirm attendance'; await send();
  assert.equal(sent.body.target,'claude-code');
  click({'data-rm-to-all':true}); assert.equal(targets.size,3);
  click({'data-rm-to-clear':true}); assert.equal(targets.size,0);
  $('[data-rm-input]').value = '@everyone hello'; await send();
  assert.equal(sent.body.body,'@everyone hello');
  meeting.participants.push(...['a','b','c'].map(id=>({id,label:id,teammate:true,rsvp:'accepted'})));
  renderTo(); assert.ok($('[data-rm-to]').querySelector('[data-rm-picker]'));
  recipientFilter='claude'; filterRecipients();
  const search=$('[data-rm-to]').querySelector('[data-rm-to-search]');
  search.focus(); search.setSelectionRange(2,4); renderTo();
  assert.equal(document.activeElement,$('[data-rm-to]').querySelector('[data-rm-to-search]'));
  assert.equal(document.activeElement.selectionStart,2); assert.equal(document.activeElement.selectionEnd,4);
  assert.equal($$('[data-rm-to-chip]').filter(n=>!n.hidden).length,1);
  targets.add('codex'); meeting.participants[0].rsvp='declined'; renderTo(); assert.ok(!targets.has('codex'));
  meeting.turns = [{id:'old',addressee:'codex',state:'committed',trigger_seq:19,created:'1',round:{round_id:'r1',position:0}}];
  assert.equal(roundLine(),null); // prior round must not describe the latest single message
  meeting.turns = [{id:'expired',addressee:'codex',state:'cancelled',disposition:'window_expired',trigger_seq:20,created:'2',round:{round_id:'r2',position:0}}];
  renderTurns();
  assert.ok($('[data-rm-turns]').find('[data-rm-turn-action]').some(n=>n.attrs['data-rm-turn-action']==='renew'));
  meeting.meeting = {kind:'meeting',facilitator:'mc',remaining:20,max_turns:20,window_expires:'2099-01-01T00:00:00Z',cursor:20};
  dismissed.clear(); renderTurns();
  assert.equal($('[data-rm-turns]').find('[data-rm-turn-action]').length,0);
  meeting.turns = Array.from({length:20},(_,i)=>({...meeting.turns[0],id:String(i),round:{round_id:'r2',position:i}}));
  assert.ok(roundLine().text.includes('earlier turns may be omitted'));
  meeting.turns = [{id:'paused',addressee:'mc',state:'failed',disposition:'mc_paused'}];
  renderTurns();
  assert.ok(failureText('MC','mc_paused','mc').includes('earlier action is not replayed'));
  assert.ok($('[data-rm-turns]').find('[href]').some(n=>n.attrs.href===page.urls.floor));
  meeting.turns = [
    {id:'retry',addressee:'mc',state:'committed',trigger_seq:20,attempt:1},
    {id:'original',addressee:'mc',state:'failed',trigger_seq:20,attempt:0,round:{round_id:'round',position:0}}];
  assert.ok(roundLine().text.includes('answered'));
  assert.ok(!roundLine().text.includes('not answered'));
  // Continue without is a recipient decision, not just a disappearing warning.
  meeting.participants = ['mc','cos'].map(id=>({id,label:id,teammate:true,rsvp:'accepted'}));
  meeting.turns = [
    {id:'mc-new',addressee:'mc',state:'uncertain'},
    {id:'mc-old',addressee:'mc',state:'uncertain',disposition:'unresolved_started'},
    {id:'cos-old',addressee:'cos',state:'failed'}];
  targets.clear(); dismissed.clear();
  const proceed = id => turnAction(el('button',{'data-turn':id,'data-rm-turn-action':'continue'}));
  sent = null; await proceed('mc-new');
  assert.equal(sent,null); // no retry, provider call or new meeting
  assert.deepEqual([...targets],['cos']);
  assert.ok(dismissed.has('mc-new') && dismissed.has('mc-old'));
  assert.ok(!dismissed.has('cos-old'));
  targets.clear(); dismissed.clear(); skippedRecipients.clear(); requireRecipient=false;
  restoreRecovery(); // same choices after reload
  assert.deepEqual([...targets],['cos']);
  assert.ok(dismissed.has('mc-old') && requireRecipient);
  $('[data-rm-input]').value='Can you help me, CoS?'; await send();
  assert.equal(sent.body.target,'cos');
  await proceed('cos-old');
  assert.equal(targets.size,0); // never loop back to the previously skipped MC
  sent=null; $('[data-rm-input]').value='Keep this draft'; await send();
  assert.equal(sent,null); assert.equal($('[data-rm-input]').value,'Keep this draft');
  current='another-room'; restoreRecovery(); assert.equal(dismissed.size,0); assert.equal(requireRecipient,false);
  current='room-a'; restoreRecovery(); assert.ok(requireRecipient);
  click({'data-rm-to-chip':'cos'}); assert.equal(requireRecipient,false);
  $('[data-rm-input]').value='Try CoS again'; await send(); assert.equal(sent.body.target,'cos');
  // Typing a correction during the POST must survive the first receipt.
  let finish; let posts=0;
  records=async()=>{posts++;return new Promise(resolve=>{finish=resolve;});};
  $('[data-rm-input]').value='First message';
  const pending=send();
  $('[data-rm-input]').value='Actually, change direction';
  await send(); assert.equal(posts,1);
  finish({status:201,body:{}}); await pending;
  assert.equal($('[data-rm-input]').value,'Actually, change direction');
  // A receipt for the previous room must not clear the new room's draft.
  $('[data-rm-input]').value='Old room message';
  const switching=send(); current='room-b'; $('[data-rm-input]').value='New room draft';
  finish({status:201,body:{}}); await switching;
  assert.equal($('[data-rm-input]').value,'New room draft'); current='room-a';
  meeting.turns=[{id:'active',addressee:'mc',state:'running'}];
  const before=[...targets]; records=async()=>({status:503,body:{}});
  await proceed('active'); assert.deepEqual([...targets],before); assert.ok(!dismissed.has('active'));

})().catch(e=>{console.error(e);process.exitCode=1;});
`, vm.createContext(Object.assign(context, {process})));
"""


def test_recipient_selection_send_and_round_recovery():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for executable UI checks")
    result = subprocess.run([node, "-e", SCRIPT, str(JS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
