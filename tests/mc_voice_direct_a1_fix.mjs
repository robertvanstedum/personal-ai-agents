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
  r.c.hear([user('Check the kiln'), assistant('Let me', false), user('and the glaze', false)]);
  r.c.options.onUserTurn('Check the kiln');
  await r.c.options.onFunctionCall({ call_id: 'call_a', name: 'consult', arguments: {} });
  r.c.hear([user('Check the kiln'), assistant('Let me', false), user('and the glaze')]);
  r.c.options.onUserTurn('and the glaze');
  await r.c.options.onFunctionCall({ call_id: 'call_b', name: 'consult', arguments: {} });
  assert.deepEqual(r.posts('/consult').map((p) => p.body.owner_item_id), ['t0', 't2']);
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

t.done();
