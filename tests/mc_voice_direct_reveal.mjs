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
  assert.equal(li.textContent, 'Answered. Reply mc-1. Spoken: Item 12 is in build.');
  assert.match(li.className, /msg-voice-receipt/);
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
console.log(`${n} revealed-voice-record scenarios passed; fake DOM only.`);
