// The A1 fix round (A1_FIX_BRIEF.md; Spec 165 v0.2 §5) in the shared session module, as regressions that each fail on the
// tree before their commit: identity (item 1), durable admission and four outcomes (2, 4, 5), capture (7), ending (8) and
// playback (9). Fake controller, fetch and timers (tests/js_helpers/direct_voice_rig.mjs); no DOM, network, provider or
// microphone. VOICE_SESSION_MODULE=<file> runs it against another copy of the module.
import assert from 'node:assert/strict';
import { D, SID, assistant, flush, rig, runner, user } from './js_helpers/direct_voice_rig.mjs';

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

t.done();
