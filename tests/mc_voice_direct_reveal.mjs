// Revealed voice records in the thread (UI_CONTRACT_chunk_B.md §6): conversation.js's voiceLine, the records the page adds after
// End. Robert's words and MC's as said, one compact line per consult, MC's full answer only when it says more than was spoken,
// the consult's own note hidden; summaries and outcomes stay ordinary notes. Fake DOM; no network.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { fakeDocument } from './js_helpers/fake_dom.mjs';
const source = fs.readFileSync(new URL('../minimoi_portal/guild_ui/static/js/conversation.js', import.meta.url), 'utf8');
const start = source.indexOf('const VOICE_HIDDEN');
const end = source.indexOf('function noteLine(note) {');
assert.ok(start > 0 && end > start, 'voiceLine block present');
const doc = fakeDocument();
const el = (tag, attrs = {}, text) => {
  const node = doc.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== false && v != null) node.setAttribute(k, v === true ? '' : String(v));
  if (text != null) node.textContent = String(text);
  return node;
};
const sandbox = { el, document: doc, localTime: (iso) => `local(${iso})`, mcName: () => 'Master Craftsman' };
vm.createContext(sandbox);
vm.runInContext(`${source.slice(start, end)}\nthis.voiceLine = voiceLine;`, sandbox);
const SID = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f';
const note = (id, voice, extra = {}) => ({ id, text: 'raw header text', created_at: '2026-10-28T10:00:00Z', voice: { session_id: SID, ...voice }, ...extra });
let n = 0;

{
  const li = sandbox.voiceLine(note(1, { kind: 'owner_turn', text: 'What is stuck on #12?', started_at: '2026-10-28T10:00:01.000Z', part: 1, parts: 1 }));
  assert.equal(li.getAttribute('data-author-kind'), 'owner');
  assert.equal(li.getAttribute('data-voice-kind'), 'owner_turn');
  assert.equal(li.getAttribute('data-note'), '1');
  assert.equal(li.getAttribute('data-at'), '2026-10-28T10:00:01.000Z');
  assert.match(li.textContent, /^You · spoken · local\(2026-10-28T10:00:01\.000Z\)What is stuck on #12\?$/);
  assert(!li.textContent.includes('raw header'));
  n++;
}
{
  // The binding pass's review (B S3, N2): a turn of his not acted on says so on its meta line.
  const li = sandbox.voiceLine(note(9, { kind: 'owner_turn', text: 'Save a note that A.', started_at: '2026-10-28T10:00:01.000Z', part: 1, parts: 1, not_acted: true }));
  assert.match(li.textContent, /^You · spoken · local\(2026-10-28T10:00:01\.000Z\) · Not acted on: say it again if you still want itSave a note that A\.$/);
  n++;
}
{
  const li = sandbox.voiceLine(note(2, { kind: 'assistant_turn', text: 'Let me check', interrupted: true, partial: true, part: 1 }));
  assert.match(li.textContent, /^Master Craftsman · spoken · .* · interrupted/);
  assert.equal(li.getAttribute('data-author-kind'), 'agent');
  const part = sandbox.voiceLine(note(3, { kind: 'assistant_turn', text: 'second part', part: 2, parts: 2 }));
  assert.equal(part.getAttribute('data-voice-part'), '2');
  assert.equal(part.textContent, 'second part');                                      // joined to the turn above: no new heading
  n++;
}
{
  const li = sandbox.voiceLine(note(4, { kind: 'consult_receipt', text: 'Answered. Reply mc-1. Spoken: Item 12 is in build.' }));
  assert.equal(li.textContent, 'Answered. Reply mc-1. Spoken: Item 12 is in build.');    // a line the server could not read: as kept
  assert.match(li.className, /msg-voice-receipt/);
  n++;
}
// UI review D1: the receipt reads "Checked with MC · <outcome>", then what was spoken; the id is in the title and a data
// attribute, never in the visible text. The same for every receipt kind.
for (const [outcome, refKind, ref, spoken] of [['answered', 'Reply', 'mc-e46f0123456789abcdef0123456789abcaf171', 'Item 12 is in build.'],
  ['note saved', 'Operation', 'op-7', 'Saved: the kiln needs a seal.'], ['job started', 'Job', 'j1', 'Started the nightly job.'],
  ['not done', null, null, 'That did not go through. Nothing is claimed.']]) {
  const text = `whatever ${ref || ''} the record keeps`;
  const li = sandbox.voiceLine(note(8, { kind: 'consult_receipt', text, outcome, ref_kind: refKind, ref, spoken }));
  assert.equal(li.textContent, `Checked with MC · ${outcome}${spoken}`, outcome);
  if (ref) {
    assert(!li.textContent.includes(ref), outcome);
    assert.equal(li.getAttribute('data-voice-ref'), ref);
    assert.equal(li.getAttribute('title'), `${refKind} ${ref}`);
  } else assert.equal(li.getAttribute('title'), null);
  assert.equal(li.getAttribute('data-author-kind'), 'platform');
  n++;
}
for (const voice of [{ kind: 'consult_note' }, { kind: 'consult_request', text: 'consult: x' }, { kind: 'consult_reply', full_differs: false }]) {
  const li = sandbox.voiceLine(note(5, voice));
  assert.equal(li.hidden, true, voice.kind);                                          // present (never re-added), not shown
  assert.equal(li.textContent, '');
  n++;
}
{
  const li = sandbox.voiceLine(note(6, { kind: 'consult_reply', full_differs: true }, { text: 'Full answer with a code block.' }));
  assert.equal(li.hidden, false);
  assert.match(li.textContent, /^Master Craftsman's full answer · .*Full answer with a code block\.$/);
  n++;
}
for (const kind of ['summary_proposal', 'summary_decision', 'summary_unavailable', 'consult_outcome', 'voice_note']) {
  assert.equal(sandbox.voiceLine(note(7, { kind })), null, kind);                      // rendered as ordinary notes
  n++;
}
// UI review #6: the reveal adds only notes newer than the oldest shown (older history the page never showed stays unloaded),
// each in its place by id and never twice.
{
  const mStart = source.indexOf('export function mergeNotes');
  const mEnd = source.indexOf('function appendNote(note) {');
  assert.ok(mStart > 0 && mEnd > mStart, 'mergeNotes block present');
  const thread = doc.createElement('ol');
  const row = (id) => { const li = doc.createElement('li'); li.setAttribute('data-note', String(id)); return li; };
  thread.append(row(10), row(12));
  const box = { thread, noteLine: (note) => row(note.id), follow: () => { box.followed += 1; }, followed: 0 };
  vm.createContext(box);
  vm.runInContext(`${source.slice(mStart, mEnd).replace('export function', 'function')}\nthis.mergeNotes = mergeNotes;`, box);
  assert.equal(box.mergeNotes([{ id: 5 }, { id: 13 }, { id: 11 }, { id: 12 }, { id: 9 }, { id: 10 }]), 2);
  assert.deepEqual(thread.elements.map((li) => li.getAttribute('data-note')), ['10', '11', '12', '13']);
  assert.equal(box.followed, 1);
  assert.equal(box.mergeNotes([{ id: 11 }, { id: 13 }]), 0);                            // never twice
  const empty = { thread: doc.createElement('ol'), noteLine: (note) => row(note.id), follow: () => {} };
  vm.createContext(empty);
  vm.runInContext(`${source.slice(mStart, mEnd).replace('export function', 'function')}\nthis.mergeNotes = mergeNotes;`, empty);
  assert.equal(empty.mergeNotes([{ id: 2 }, { id: 1 }]), 2);                            // an empty thread takes them all
  n++;
}
// UI review #7: conversation.js announces each first page of notes it reads (with voice_sessions) for the voice thread.
{
  const rStart = source.indexOf('function notesRead(');
  const rEnd = source.indexOf('// The assistant response is not the platform');
  assert.ok(rStart > 0 && rEnd > rStart, 'notesRead block present');
  assert.equal((source.match(/return notesRead\(/g) || []).length, 2);                    // both of the page's notes sweeps
  const events = [];
  class CustomEvent { constructor(type, init) { this.type = type; this.detail = init.detail; } }
  const box = { CustomEvent, document: { dispatchEvent: (e) => events.push(e) } };
  vm.createContext(box);
  vm.runInContext(`${source.slice(rStart, rEnd)}\nthis.notesRead = notesRead;`, box);
  const answer = { ok: true, body: { notes: [{ id: 1 }], voice_sessions: [{ session_id: SID, state: 'live' }] } };
  assert.equal(box.notesRead('c-1', answer, undefined), answer);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'guild:notes-read');
  assert.equal(events[0].detail.conversationId, 'c-1');
  assert.equal(events[0].detail.voice_sessions[0].session_id, SID);
  box.notesRead('c-1', answer, 40);                                                     // an older page: not announced
  box.notesRead('c-1', { ok: false, body: {} });
  box.notesRead('c-1', { ok: true, body: { notes: [] } });                               // no voice_sessions: not announced
  assert.equal(events.length, 1);
  n++;
}
console.log(`${n} revealed-voice-record scenarios passed; fake DOM only.`);
