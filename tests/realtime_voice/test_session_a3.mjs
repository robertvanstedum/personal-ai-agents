// What A3 carried from A1 into the shared session module (core/realtime_voice/static/direct-voice-session.js), as regressions:
// Check shows the words it sends (review B N8), the bootstrap is bounded on the direct path (review W N3), and End says why
// each turn of his was not acted on (Gate 1 n2, n3). Fake controller, fetch and timers (tests/js_helpers/direct_voice_rig.mjs);
// no DOM, network, provider or microphone. VOICE_SESSION_MODULE=<file> runs it against another copy of the module. The same
// orders through the real controller and adapters: test_binding_integrated.mjs ("n2", "n3").
import assert from 'node:assert/strict';
import { DIRECT, flush, rig, runner } from '../js_helpers/direct_voice_rig.mjs';

const t = runner('A3 session-module regressions');
const HOST = { ...DIRECT, providers: [{ provider: 'openai', label: 'OpenAI' }, { provider: 'xai', label: 'Grok (xAI)' }],
  model_actions: { openai: true, xai: false } };

// B N8: the page is told the words Check sends, as it sends them and again with the answer: capture's own record of his
// turn, the same words the consult carries. On Grok, where only Check acts, an "okay" said last is what it sends.
for (const provider of ['openai', 'xai']) {
  await t.check(`B N8 (${provider}): Check hands the page the words it sends, then the same words with the answer`, async () => {
    const r = rig({ direct: HOST }); await r.s.start(provider);
    r.c.utter('Check the board.'); r.c.utter('okay'); await flush();
    await r.s.check(); await flush();
    assert.equal(r.posts('/consult')[0].body.owner_transcript_item, 'okay');
    assert.deepEqual(r.checks, [[true, 'okay'], [false, 'okay']]);
  });
}
await t.check('B N8: a Check that sends nothing (nothing said yet) shows no words', async () => {
  const r = rig({ direct: HOST }); await r.s.start('openai');
  await r.s.check(); await flush();
  assert.deepEqual(r.checks, []);
  assert.equal(r.posts('/consult').length, 0);
});
await t.check('B N8 (xai): the notice says only Check acts on this voice', async () => {
  const r = rig({ direct: HOST }); await r.s.start('xai');
  assert.deepEqual(r.notices, ['On this voice, Master Craftsman acts only when you press Check with MC.']);
});

// W N3: the direct path asks the controller to bound its bootstrap (the controller's own bound: realtime_voice_direct_host.mjs).
await t.check('W N3: the session module starts the controller with a bootstrap bound above the portal\'s 30 s', async () => {
  const r = rig({ direct: HOST }); await r.s.start('openai');
  assert.equal(r.c.options.bootstrapTimeoutMs, 36000);
});

// n2, n3: End carries why for each turn named not acted on; the first reason the controller gives stands; a turn consulted
// after all is not named.
await t.check('n2/n3: End names each turn not acted on with why, the first reason standing; one consulted is left out', async () => {
  const r = rig({ direct: HOST }); await r.s.start('openai');
  r.c.utter('Save a note that A.'); r.c.utter('Save a note that B.'); r.c.utter('Save a note that C.'); r.c.utter('Check D.');
  r.c.placeOf = (item) => ({ A: 0, B: 1, C: 2, D: 3 })[item];
  const unbound = r.c.options.onUnbound;
  unbound({ reason: 'superseded_call', owner: 'A', why: 'cut_short' });
  unbound({ reason: 'closed', owner: 'A', kind: 'turn' });                  // a later reason for A: the first stands
  unbound({ reason: 'superseded', owner: 'B', kind: 'turn' });
  unbound({ reason: 'closed', owner: 'C', kind: 'turn' });
  unbound({ reason: 'closed', owner: 'D', kind: 'follow' });               // a follow-up's owner already acted: not named
  unbound({ reason: 'never_started', owner: 'D', kind: 'turn' });
  await r.s.check(); await flush();                                        // Check consults D after all
  await r.s.end('user_ended'); await flush();
  const end = r.posts('/end')[0].body;
  assert.deepEqual(end.not_acted, ['t0', 't1', 't2']);
  assert.deepEqual(end.not_acted_why, { t0: 'cut_short', t1: 'overtaken', t2: 'closed' });
});
await t.check('n2/n3: a page leaving sends the same reasons in its one keepalive End', async () => {
  const r = rig({ direct: HOST }); await r.s.start('openai');
  r.c.utter('Save a note that A.'); await flush();
  r.c.placeOf = () => 0;
  r.c.options.onUnbound({ reason: 'superseded_call', owner: 'A', why: 'overtaken' });
  await r.s.end('pagehide'); await flush();
  const [end] = r.posts('/end');
  assert.equal(end.opts.keepalive, true);
  assert.deepEqual([end.body.not_acted, end.body.not_acted_why], [['t0'], { t0: 'overtaken' }]);
});
await t.check('n2/n3, guard: nothing not acted on: End carries neither field', async () => {
  const r = rig({ direct: HOST }); await r.s.start('openai');
  r.c.utter('Hello.'); await flush();
  await r.s.end('user_ended'); await flush();
  const end = r.posts('/end')[0].body;
  assert.equal('not_acted' in end || 'not_acted_why' in end, false);
});
t.done();
