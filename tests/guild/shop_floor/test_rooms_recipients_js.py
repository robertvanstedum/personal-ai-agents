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
  document: { addEventListener(){} }, newKey: () => 'test-key' });
const source = fs.readFileSync(process.argv[1], 'utf8').replace(/^import .*;$/mg,'').replace(/^export /mg,'');
vm.runInContext(source, context);
vm.runInContext(String.raw`
(async () => {
  current = 'room-a'; room = { id: current, state: 'active', events: [{actor:'robert',kind:'message',seq:20}] };
  page = { storage_ns: 'test' };
  const people = ['codex','claude-code','mc'];
  meeting = {room:current, participants:people.map(id=>({id,label:id,teammate:true,rsvp:'accepted'})), turns:[], routing_notes:[], meeting:{facilitator:'mc'}};
  let sent;
  records = async (url, options) => { sent = options; return {status:201,body:{}}; };
  loadRoom = async () => true;
  bindMeeting();
  renderTo();
  const click = attrs => $('[data-rm-to]').handlers.click({target:el('button',attrs)});
  click({'data-rm-to-chip':'codex'}); click({'data-rm-to-chip':'claude-code'});
  assert.equal(targets.size,2);
  $('[data-rm-input]').value = 'Confirm attendance'; await send();
  assert.equal(sent.body.body,'@codex @claude-code\nConfirm attendance');
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
