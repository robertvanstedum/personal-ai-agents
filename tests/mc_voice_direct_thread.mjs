// MC voice on the direct path, the thread (UI_CONTRACT_chunk_B.md §6, §7, §10): the recovering line sits where the session
// started, a session live elsewhere says so, and a waiting summary proposal carries Confirm, Edit and Decline. Fake DOM and
// fake POST; no network.
import assert from 'node:assert/strict';
import { fakeDocument } from './js_helpers/fake_dom.mjs';
const D = await import(new URL('../minimoi_portal/guild_ui/static/js/mc_voice_direct.js', import.meta.url));

const SID = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f', OTHER = '0f1e2d3c-4b5a-4968-8776-655443322110';
const DIRECT = { summary_decision_url: '/api/v1/mc/voice/direct/api/realtime-voice/session/{session_id}/summary/decision' };
const PROPOSAL = '[Voice summary proposal] For this voice session only. Not confirmed. Covers all 4 retained records of this session.\n\nWe looked at #12: in build.';

function rig({ answer = () => ({ status: 200, body: { ok: true, state: 'confirmed', repeated: false, authorizes: 'nothing beyond the summary' } }), mine = null } = {}) {
  const doc = fakeDocument(), thread = doc.createElement('ol'), posts = [], reveals = [];
  thread.setAttribute('data-mc-thread', '');
  const row = (id, iso, attrs = {}) => {
    const li = doc.createElement('li');
    li.setAttribute('data-note', String(id));
    for (const [k, v] of Object.entries(attrs)) li.setAttribute(k, v);
    const meta = doc.createElement('p'), when = doc.createElement('span');
    when.setAttribute('data-iso', iso); meta.append(when); li.append(meta);
    thread.append(li);
    return li;
  };
  const t = D.voiceThread({ doc, direct: DIRECT, thread: () => thread, mine: () => mine, conversationId: () => 'c-1',
    post: async (url, body) => { posts.push({ url, body }); return answer(body); }, reveal: async () => { reveals.push(true); } });
  return { doc, thread, row, t, posts, reveals, order: () => thread.elements.map((n) => n.getAttribute('data-note') || `line:${n.getAttribute('data-voice-line')}`) };
}
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };
let n = 0;

// Recovering: one quiet line where the session started, never an empty gap; it goes once the session is revealed.
{
  const r = rig();
  r.row(1, '2026-10-28T09:00:00.000Z'); r.row(2, '2026-10-28T10:30:00.000Z');
  r.t.paint([{ session_id: SID, state: 'recovering', started_at: '2026-10-28T10:00:00.000Z', line: D.RECOVERING, summary: null }]);
  assert.deepEqual(r.order(), ['1', `line:${SID}`, '2']);
  assert.equal(r.thread.querySelector(`[data-voice-line="${SID}"]`).textContent, D.RECOVERING);
  r.t.paint([{ session_id: SID, state: 'recovering', started_at: '2026-10-28T10:00:00.000Z', line: D.RECOVERING }]);
  assert.equal(r.thread.querySelectorAll('[data-voice-line]').length, 1);                // painted again: still one line
  r.t.paint([{ session_id: SID, state: 'ended', started_at: '2026-10-28T10:00:00.000Z', line: null, summary: 'none' }]);
  assert.deepEqual(r.order(), ['1', '2']);
  n++;
}
// An unknown state shows the same line; a session started after every row goes last.
{
  const r = rig();
  r.row(1, '2026-10-28T09:00:00.000Z');
  r.t.paint([{ session_id: SID, state: 'unknown', started_at: '2026-10-28T11:00:00.000Z', line: D.RECOVERING }]);
  assert.deepEqual(r.order(), ['1', `line:${SID}`]);
  n++;
}
// Live in another tab or device: it says so. Live here: nothing (the live state is in the composer, not the thread).
{
  const r = rig({ mine: SID });
  r.t.paint([{ session_id: SID, state: 'live', started_at: '2026-10-28T10:00:00.000Z', line: null },
    { session_id: OTHER, state: 'live', started_at: '2026-10-28T10:05:00.000Z', line: null }]);
  assert.equal(r.thread.querySelector(`[data-voice-line="${SID}"]`), null);
  assert.equal(r.thread.querySelector(`[data-voice-line="${OTHER}"]`).textContent, D.LIVE_ELSEWHERE);
  n++;
}
// §7 / §10: the proposal is a card in the thread: Confirm, Edit, Decline, and what confirming means. Confirm authorizes nothing else.
{
  const r = rig();
  const proposal = r.row(5, '2026-10-28T10:20:00.000Z', { 'data-voice-kind': 'summary_proposal', 'data-voice-session': SID });
  const sessions = [{ session_id: SID, state: 'ended', started_at: '2026-10-28T10:00:00.000Z', summary: 'proposed' }];
  r.t.paint(sessions, [{ id: 5, text: PROPOSAL, voice: { session_id: SID, kind: 'summary_proposal' } }]);
  r.t.paint(sessions);
  assert.equal(proposal.querySelectorAll('[data-voice-summary-card]').length, 1);       // never twice
  const card = proposal.querySelector('[data-voice-summary-card]');
  for (const b of ['confirm', 'edit', 'decline']) assert(card.querySelector(`[data-voice-summary-${b}]`));
  assert.equal(card.querySelector('[data-voice-summary-confirm]').textContent, 'Confirm');
  assert.match(card.textContent, /Confirming confirms the summary only\. Anything it mentions still needs its own request\./);
  card.querySelector('[data-voice-summary-confirm]').click();
  await flush();
  assert.deepEqual(r.posts, [{ url: `/api/v1/mc/voice/direct/api/realtime-voice/session/${SID}/summary/decision`, body: { conversation_id: 'c-1', decision: 'confirm' } }]);
  assert.equal(card.querySelector('[data-voice-summary-result]').textContent, 'Confirmed. It confirms the summary only; it authorizes nothing it mentions.');
  assert.equal(card.querySelector('[data-voice-summary-confirm]'), null);                // decided: the buttons go
  assert.equal(r.reveals.length, 1);                                                    // the decision note is shown
  n++;
}
// Edit opens the written text in place; Save sends Robert's wording; Cancel puts the buttons back.
{
  const r = rig({ answer: (body) => ({ status: 200, body: { ok: true, state: body.decision === 'edit' ? 'edited' : 'declined' } }) });
  const proposal = r.row(5, '2026-10-28T10:20:00.000Z', { 'data-voice-kind': 'summary_proposal', 'data-voice-session': SID });
  r.t.paint([{ session_id: SID, state: 'ended', summary: 'proposed' }], [{ id: 5, text: PROPOSAL, voice: { session_id: SID, kind: 'summary_proposal' } }]);
  const card = proposal.querySelector('[data-voice-summary-card]');
  card.querySelector('[data-voice-summary-edit]').click();
  const editor = card.querySelector('[data-voice-summary-editor]');
  const area = editor.elements[0];
  assert.equal(area.tagName, 'TEXTAREA');
  assert.equal(area.value, 'We looked at #12: in build.');
  assert.equal(area.getAttribute('maxlength'), String(D.EDIT_MAX));
  card.querySelector('[data-voice-summary-edit]').click();
  assert.equal(card.querySelectorAll('[data-voice-summary-editor]').length, 1);
  card.querySelector('[data-voice-summary-cancel]').click();
  assert.equal(card.querySelector('[data-voice-summary-editor]'), null);
  card.querySelector('[data-voice-summary-edit]').click();
  const again = card.querySelector('[data-voice-summary-editor]').elements[0];
  again.value = '   ';
  card.querySelector('[data-voice-summary-save]').click();
  await flush();
  assert.equal(r.posts.length, 0);                                                      // an empty edit is not sent
  again.value = 'Item 12 is in build; nothing else was decided.';
  card.querySelector('[data-voice-summary-save]').click();
  await flush();
  assert.deepEqual(r.posts.at(-1).body, { conversation_id: 'c-1', decision: 'edit', text: 'Item 12 is in build; nothing else was decided.' });
  assert.match(card.querySelector('[data-voice-summary-result]').textContent, /Your wording replaces the proposal\. It confirms the summary only/);
  assert.equal(card.querySelector('[data-voice-summary-editor]'), null);
  n++;
}
// Decline; the first decision wins (already decided); a refused decision keeps the buttons.
{
  const r = rig({ answer: () => ({ status: 200, body: { ok: true, state: 'declined' } }) });
  const proposal = r.row(5, '2026-10-28T10:20:00.000Z', { 'data-voice-kind': 'summary_proposal', 'data-voice-session': SID });
  r.t.paint([{ session_id: SID, state: 'ended', summary: 'proposed' }]);
  proposal.querySelector('[data-voice-summary-decline]').click();
  await flush();
  assert.deepEqual(r.posts[0].body, { conversation_id: 'c-1', decision: 'decline' });
  assert.equal(proposal.querySelector('[data-voice-summary-result]').textContent, 'Declined. The proposal is not your record of the session.');
  const late = rig({ answer: () => ({ status: 409, body: { error: 'already_decided', state: 'confirmed' } }) });
  const p2 = late.row(5, '2026-10-28T10:20:00.000Z', { 'data-voice-kind': 'summary_proposal', 'data-voice-session': SID });
  late.t.paint([{ session_id: SID, state: 'ended', summary: 'proposed' }]);
  p2.querySelector('[data-voice-summary-confirm]').click();
  await flush();
  assert.equal(p2.querySelector('[data-voice-summary-result]').textContent, 'Already decided (confirmed). Nothing was changed.');
  const bad = rig({ answer: () => ({ status: 422, body: { error: 'invalid', message: 'Say confirm, edit (with the text) or decline. Nothing was changed.' } }) });
  const p3 = bad.row(5, '2026-10-28T10:20:00.000Z', { 'data-voice-kind': 'summary_proposal', 'data-voice-session': SID });
  bad.t.paint([{ session_id: SID, state: 'ended', summary: 'proposed' }]);
  p3.querySelector('[data-voice-summary-confirm]').click();
  await flush();
  assert.equal(p3.querySelector('[data-voice-summary-confirm]').disabled, false);
  assert.match(p3.querySelector('[data-voice-summary-result]').textContent, /Nothing was changed/);
  n++;
}
// No card for a summary already decided, or for a session with no proposal row.
{
  const r = rig();
  const proposal = r.row(5, '2026-10-28T10:20:00.000Z', { 'data-voice-kind': 'summary_proposal', 'data-voice-session': SID });
  r.t.paint([{ session_id: SID, state: 'ended', summary: 'confirmed' }, { session_id: OTHER, state: 'ended', summary: 'proposed' }]);
  assert.equal(proposal.querySelector('[data-voice-summary-card]'), null);
  n++;
}
console.log(`${n} direct-voice thread scenarios passed (recovering line, live elsewhere, summary card Confirm/Edit/Decline); fake DOM only.`);
