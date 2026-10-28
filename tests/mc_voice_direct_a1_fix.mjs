// The A1 fix round (A1_FIX_BRIEF.md; Spec 165 v0.2 §5) in the shared session module, as regressions that each fail on the
// tree before their commit: identity (item 1), durable admission and four outcomes (2, 4, 5), capture (7), ending (8) and
// playback (9). Fake controller, fetch and timers (tests/js_helpers/direct_voice_rig.mjs); no DOM, network, provider or
// microphone. VOICE_SESSION_MODULE=<file> runs it against another copy of the module.
import assert from 'node:assert/strict';
import { D, SID, assistant, flush, rig, runner, user } from './js_helpers/direct_voice_rig.mjs';
import { fakeDocument } from './js_helpers/fake_dom.mjs';
import { pathToFileURL } from 'node:url';
const T = await import(process.env.VOICE_THREAD_MODULE ? pathToFileURL(process.env.VOICE_THREAD_MODULE)
  : new URL('../minimoi_portal/guild_ui/static/js/mc_voice_direct.js', import.meta.url));

const t = runner('A1 fix-round session regressions');

// ── Item 1: identity is the capture id of the completed owner turn (§5.1 owner_item_id) ────────────────────────────────
await t.check('item 1: a consult names the capture id of the owner turn it acts on, the id its transcript post carries', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('What is stuck on #12?'); await flush();
  await r.c.options.onFunctionCall({ call_id: 'call_1', name: 'consult', arguments: {} });
  const [consult] = r.posts('/consult');
  assert.equal(consult.body.owner_item_id, 't0');
  assert.equal('owner_turn' in consult.body, false);
  assert.equal(r.posts('/transcript')[0].body.events[0].item_id, 't0');
  assert.equal(consult.body.owner_transcript_item, 'What is stuck on #12?');
});
await t.check('item 1: an owner turn that completes after a later item appeared is named by its own place', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.hear([user('Check the kiln', false), assistant('Let me', false), user('and the glaze', false)]);
  r.c.hear([user('Check the kiln', false), assistant('Let me', false), user('and the glaze')]);   // the later one first
  r.c.options.onUserTurn('and the glaze');
  await r.c.options.onFunctionCall({ call_id: 'call_b', name: 'consult', arguments: {} });
  r.c.hear([user('Check the kiln'), assistant('Let me', false), user('and the glaze')]);
  r.c.options.onUserTurn('Check the kiln');
  await r.c.options.onFunctionCall({ call_id: 'call_a', name: 'consult', arguments: {} });
  assert.deepEqual(r.posts('/consult').map((p) => [p.body.owner_item_id, p.body.owner_transcript_item]),
    [['t2', 'and the glaze'], ['t0', 'Check the kiln']]);
});
await t.check('item 1: Check and the model on one turn name one id; the same words said again are a new id', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('Make a note that the kiln needs a new seal');
  await r.s.check();
  await r.c.options.onFunctionCall({ call_id: 'call_m', name: 'save_note', arguments: {} });
  r.c.utter('Make a note that the kiln needs a new seal');
  await r.c.options.onFunctionCall({ call_id: 'call_n', name: 'save_note', arguments: {} });
  assert.deepEqual(r.posts('/consult').map((p) => p.body.owner_item_id), ['t0', 't0', 't1']);
});

// ── R1 P1-2, R2 S4: a model call that arrives before its own transcript (repro_a1.mjs R1, R2) ───────────────────────────
await t.check('R1 P1-2: a call before its transcript waits for it, then acts once on the new words and its turn', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('What is stuck on #12?');
  await r.c.options.onFunctionCall({ call_id: 'a', name: 'consult', arguments: {} });
  r.c.options.onInputState('speech_started'); r.c.options.onInputState('speech_stopped');   // he says the next thing
  const pending = r.c.options.onFunctionCall({ call_id: 'b', name: 'save_note', arguments: { note: 'the kiln needs a new seal' } });
  await flush();
  assert.equal(r.posts('/consult').length, 1, 'sent before its own words were transcribed');
  r.advance(1500);
  r.c.utter('Make a note that the kiln needs a new seal');                                  // the transcript lands
  await pending; await flush();
  const [first, second] = r.posts('/consult').map((p) => p.body);
  assert.equal(r.posts('/consult').length, 2);
  assert.equal(first.owner_item_id, 't0');
  assert.deepEqual([second.owner_item_id, second.owner_transcript_item], ['t1', 'Make a note that the kiln needs a new seal']);
});
await t.check('R1 P1-2: a call whose transcript never comes sends nothing and says it did not catch it', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('What is stuck on #12?');
  await r.c.options.onFunctionCall({ call_id: 'a', name: 'consult', arguments: {} });
  r.c.options.onInputState('speech_started');
  const pending = r.c.options.onFunctionCall({ call_id: 'b', name: 'save_note', arguments: {} });
  await flush(); r.advance(2999); await flush();
  assert.equal(r.posts('/consult').length, 1);
  r.advance(1);
  const out = await pending;
  assert.deepEqual(out, { ok: false, outcome: 'not_done', error: 'not_heard', spoken: D.NOT_HEARD });
  assert.equal(r.posts('/consult').length, 1, 'acted on the older words');
});
await t.check('R1 P1-2 (repro R2): a call before its transcript, while the earlier consult runs, is not absorbed as pending', async () => {
  let release;
  const r = rig({ routes: { '/consult': (body) => (body.call_id === 'a' ? new Promise((res) => { release = () => res({ status: 200, body: { ok: true, outcome: 'done', spoken: 'Logged.' } }); }) : null) } });
  await r.s.start('openai');
  r.c.utter('Check the kiln temperature log.');
  const first = r.c.options.onFunctionCall({ call_id: 'a', name: 'consult', arguments: {} });
  await flush();
  r.c.options.onInputState('speech_started');
  const second = r.c.options.onFunctionCall({ call_id: 'b', name: 'save_note', arguments: {} });
  await flush();
  r.c.utter('Make a note that the kiln is hot');
  const out = await second; release(); await first;
  assert.notEqual(out.outcome, 'pending');
  assert.deepEqual(r.posts('/consult').map((p) => p.body.owner_item_id), ['t0', 't1']);
});
await t.check('R1 P1-2: an empty transcript is not words: nothing is sent', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('What is stuck on #12?');
  await r.c.options.onFunctionCall({ call_id: 'a', name: 'consult', arguments: {} });
  r.c.options.onInputState('speech_started');
  const pending = r.c.options.onFunctionCall({ call_id: 'b', name: 'consult', arguments: {} });
  await flush();
  r.c.options.onUserTurn('');
  assert.equal((await pending).error, 'not_heard');
  assert.equal(r.posts('/consult').length, 1);
});

// ── Item 4: the page's own outcomes use §5.1's words; only done is ok ───────────────────────────────────────────────────
await t.check('item 4: a turn still running is pending, an answer that never came is unknown, a refusal is not done', async () => {
  const answers = { a: { status: 409, body: { ok: false, error: 'in flight' } }, b: 'network', c: { status: 400, body: null } };
  const r = rig({ routes: { '/consult': (body) => answers[body.call_id] } });
  await r.s.start('openai');
  r.c.utter('Run the nightly job.');
  assert.deepEqual(await r.c.options.onFunctionCall({ call_id: 'a', name: 'consult', arguments: {} }),
    { ok: false, error: 'in flight', outcome: 'pending', spoken: "That's taking longer; the answer will be in the conversation." });
  assert.deepEqual(await r.c.options.onFunctionCall({ call_id: 'b', name: 'consult', arguments: {} }),
    { ok: false, error: 'unknown', outcome: 'unknown', spoken: "I didn't get the result back. Check it in writing before asking again." });
  assert.deepEqual(await r.c.options.onFunctionCall({ call_id: 'c', name: 'consult', arguments: {} }),
    { ok: false, error: 'unavailable', outcome: 'not_done', spoken: 'That did not go through. Nothing was done.' });
});

await t.check('item 4: a finished result the heartbeat reports is said once, at a pause', async () => {
  let beats = 0;
  const done = { owner_item_id: 't0', tool: 'consult', outcome: 'done', spoken: 'The nightly job finished.' };
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? null : { status: 200, body: { ok: true, kept: 0, ...(++beats <= 2 ? { completed: [done] } : {}) } }) } });
  await r.s.start('openai');
  r.c.options.onInputState('speech_started');                          // Robert is talking: no line now
  r.advance(20000); await flush();                                     // the heartbeat reports the finished result
  assert.deepEqual(r.c.instructions, []);
  r.c.options.onInputState('speech_stopped');                          // a response to his words follows
  r.advance(3000); await flush();
  assert.deepEqual(r.c.instructions, []);
  r.c.options.onAssistantTurn('Sure.'); await flush();                 // the pause
  const said = ['Say this briefly, adding nothing: About what you asked earlier: The nightly job finished.'];
  assert.deepEqual(r.c.instructions, said);
  r.c.options.onAssistantTurn('Good.');
  r.advance(20000); await flush();                                     // reported again: said once
  assert.deepEqual(r.c.instructions, said);
});
await t.check('R2: Check answers at a pause, never while a response is under way, and at most 8 s later', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('Is the kiln job done?');
  r.c.options.onInputState('speech_started'); r.c.options.onInputState('speech_stopped');
  r.c.utter('Is the kiln job done?');
  await r.s.check(); await flush();
  assert.deepEqual(r.c.instructions, [], 'spoken while a response was under way');
  r.c.options.onAssistantTurn('Let me look.'); await flush();
  assert.equal(r.c.instructions.length, 1);
  r.c.options.onInputState('speech_started');
  r.c.utter('And the glaze?');
  await r.s.check(); await flush();
  assert.equal(r.c.instructions.length, 1);
  r.advance(7999); assert.equal(r.c.instructions.length, 1);
  r.advance(1); assert.equal(r.c.instructions.length, 2);                // the pause was missed: sent at the bound
});

// ── Item 5: after an unknown, the page asks once what became of the turn ─────────────────────────────────────────────
await t.check('item 5: an answer lost twice is asked about once, and the stored outcome is what is said', async () => {
  const r = rig({ routes: { '/consult': () => 'network',
    '/t0': () => ({ status: 200, body: { ok: true, outcome: 'done', owner_item_id: 't0', receipt: 'mc-9', spoken: 'Item 12 is in build.' } }) } });
  await r.s.start('openai');
  r.c.utter('What is stuck on #12?');
  const out = await r.c.options.onFunctionCall({ call_id: 'call_1', name: 'consult', arguments: {} });
  assert.equal(r.posts('/consult').length, 2);
  const [asked] = r.gets('/consult/');
  assert.equal(asked.url, `/guild-next/api/v1/mc/voice/direct/api/realtime-voice/consult/${SID}/t0?conversation_id=c-1`);
  assert.equal(r.gets('/consult/').length, 1);
  assert.equal(out.outcome, 'done'); assert.equal(out.spoken, 'Item 12 is in build.');
});
await t.check('item 5: a read that does not answer, or says unknown, leaves it unknown', async () => {
  for (const answer of ['network', { status: 200, body: { ok: false, outcome: 'unknown', spoken: 'x' } }, { status: 404, body: {} }]) {
    const r = rig({ routes: { '/consult': () => 'network', '/t0': () => answer } });
    await r.s.start('openai');
    r.c.utter('What is stuck on #12?');
    const out = await r.c.options.onFunctionCall({ call_id: 'call_1', name: 'consult', arguments: {} });
    assert.equal(out.outcome, 'unknown'); assert.equal(out.spoken, D.UNKNOWN);
  }
});

// ── Item 7: capture states, the close's report, incomplete(n); R2 S2: the barrier admits nothing new ──────────────────
await t.check('R2 S2: a turn that completes during Sum up\'s flush is not admitted, so a fully kept session is complete', async () => {
  let release;
  const r = rig({ routes: {
    '/transcript': (body) => (body.events.some((e) => e.text === 'Last words.') ? new Promise((res) => { release = () => res({ status: 200, body: { ok: true, kept: 1 } }); }) : null),
    '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'Done.' } }) } });
  await r.s.start('openai');
  r.c.hear([user('Last words.')]); await flush();
  const asked = r.s.sumUp(); await flush();
  r.c.hear([user('Last words.'), assistant('Sure, summing up.')]); await flush();     // the voice answers during the flush
  release(); await asked; await flush();
  const report = r.posts('/summary')[0].body;
  assert.deepEqual([report.last_item_id, report.unacknowledged], ['t0', []]);
  assert.equal(r.posts('/transcript').filter((x) => x.body.events.some((e) => e.item_id === 't1')).length, 0);
});
await t.check('item 7: an event ends failed after two attempts under one id, and End reports it', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? 'network' : null),
    '/end': () => ({ status: 200, body: { ok: true, ended: true, capture: 'incomplete', missing: 1 } }) } });
  await r.s.start('openai');
  r.c.hear([user('Lost twice.')]); await flush();
  await r.s.end('user_ended'); await flush();
  const sent = r.posts('/transcript').filter((x) => x.body.events.length);
  assert.deepEqual(sent.map((x) => x.body.events[0].item_id), ['t0', 't0']);         // no third attempt at End
  assert.deepEqual(r.posts('/end')[0].body, { last_item_id: 't0', unacknowledged: ['t0'] });
  assert.deepEqual(r.ended.at(-1), ['Voice ended. Some of what was said was not kept (1 turn). What was kept is in the conversation.', { error: true }]);
});
await t.check('item 7: End\'s line takes the server\'s count (it holds a turn whose answer was lost)', async () => {
  let n = 0;
  const r = rig({ routes: { '/transcript': (body) => (body.events.length && n++ < 2 ? 'network' : null),
    '/end': () => ({ status: 200, body: { ok: true, ended: true, capture: 'complete' } }) } });
  await r.s.start('openai');
  r.c.hear([user('Kept, though the answers were lost.')]); await flush();
  await r.s.end('user_ended'); await flush();
  assert.deepEqual(r.posts('/end')[0].body.unacknowledged, ['t0']);
  assert.deepEqual(r.ended.at(-1), [D.ENDED, { error: false }]);
});
await t.check('item 7 (pagehide, single End): End carries one batch and the report, with no posts beside it', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? new Promise(() => {}) : null) } });
  await r.s.start('openai');
  const many = [];
  for (let i = 0; i < 25; i++) many.push(i % 2 ? assistant(`turn ${i}`) : user(`turn ${i}`));
  r.c.hear(many); await flush();
  const before = r.calls.length;
  r.s.end('pagehide');
  const sent = r.calls.slice(before);
  assert.deepEqual(sent.map((x) => x.url.split('/').pop()), ['end']);
  assert.deepEqual([sent[0].body.events.length, sent[0].body.last_item_id, sent[0].body.unacknowledged.length], [20, 't24', 25]);
});
await t.check('item 7, R2 N6: the thread says incomplete with its count, and a dropped session may not be whole', async () => {
  const doc = fakeDocument(), thread = doc.createElement('ol');
  const view = T.voiceThread({ doc, direct: {}, thread: () => thread, mine: () => null, conversationId: () => 'c-1', post: async () => ({}), reveal: async () => {} });
  view.paint([{ session_id: SID, state: 'ended', started_at: '2026-10-28T10:00:00.000Z', summary: 'none', capture: 'incomplete', missing: 2 },
    { session_id: 'other', state: 'dropped', started_at: '2026-10-28T09:00:00.000Z', summary: 'none', capture: 'unknown' }]);
  assert.equal(thread.querySelector(`[data-voice-line="${SID}"]`)?.textContent,
    'Some of what was said in this voice session was not kept (2 turns). What was kept is in the conversation.');
  assert.match(thread.querySelector('[data-voice-line="other"]')?.textContent || '', /may not have been kept/);
});

t.done();
