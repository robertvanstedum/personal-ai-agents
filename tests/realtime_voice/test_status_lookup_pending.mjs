// #561 (Robert, 13 Nov 2026, voice on the phone after 1.2.1): "did the deploy finish?" was routed to MC as a lookup, but the
// voice guessed "finished" while the lookup ran and then read out MC's opposite answer. While a spoken lookup is pending the
// voice says once that it is checking and nothing else about it; the answer is said once when it returns; the same result
// reported again later is never said twice. Fake controller, fetch and timers (tests/js_helpers/direct_voice_rig.mjs).
import assert from 'node:assert/strict';
import { D, flush, rig, runner } from '../js_helpers/direct_voice_rig.mjs';

const t = runner('#561 status-lookup pending regressions');
const SAY = 'Say this briefly, adding nothing: ';
const ASKED = 'Did the deploy finish?';
const LOOKUP = { decision: 'confirm', kind: 'consult', action_id: 'a1', owner_item_id: 't0', confirm_item_id: 't0',
  text_sha256: 'h', lookup: true };
const ANSWER = 'Yes. Release 1.2.1 is verified in production since 05:24 UTC, per the delivery record.';

function lookupRig({ consult, transcript } = {}) {
  let release = null;
  const routes = {
    '/utterance': (body) => (body.text === ASKED ? { status: 200, body: { ok: true, ...LOOKUP } } : null),
    '/consult': consult || (() => new Promise((res) => { release = (answer) => res(answer); })),
  };
  if (transcript) routes['/transcript'] = transcript;
  const r = rig({ routes });
  return { r, release: (answer) => release(answer) };
}
const done = (spoken = ANSWER) => ({ status: 200, body: { ok: true, outcome: 'done', answered: true, receipt: 'mc-1', spoken } });

await t.check('a pending lookup says once that it is checking, then nothing until its one answer, even after 55 s', async () => {
  const { r, release } = lookupRig();
  await r.s.start('openai');
  r.c.utter(ASKED); await flush();
  assert.deepEqual(r.c.instructions, [SAY + D.CHECKING], 'the lookup did not say once that it is checking');
  assert.equal(r.posts('/consult').length, 1);
  r.c.options.onAssistantTurn('Checking.');
  // A real MC turn with GitHub reads (production, 12-13 Nov: median 20 s, p90 45 s, max 56 s): no holding line, no
  // "couldn't get it", no reconciliation, while the lookup still runs.
  for (let i = 0; i < 11; i++) { r.advance(5000); await flush(); }
  assert.deepEqual(r.c.instructions, [SAY + D.CHECKING], 'the voice was given something to say while the lookup ran');
  assert.equal(r.gets('/consult/').length, 0, 'the page gave up on the lookup and reconciled before its answer');
  release(done()); await flush();
  r.advance(30000); await flush();
  assert.deepEqual(r.c.instructions, [SAY + D.CHECKING, D.answerLine(ANSWER)]);
  assert.equal(r.c.instructions.filter((line) => /\bcheck/i.test(line)).length, 1, 'said it was checking more than once');
  assert.ok(!r.c.instructions.some((line) => /couldn.?t get|unknown|didn.?t get the result/i.test(line)));
});

await t.check('a lookup that answers quickly gets the checking line and the answer, nothing in between', async () => {
  const { r, release } = lookupRig();
  await r.s.start('openai');
  r.c.utter(ASKED); await flush();
  r.c.options.onAssistantTurn('Checking.');
  r.advance(3000); release(done()); await flush();
  r.advance(30000); await flush();
  assert.deepEqual(r.c.instructions, [SAY + D.CHECKING, D.answerLine(ANSWER)]);
});

await t.check('the same result reported later as finished is acknowledged, never said again', async () => {
  const completed = { owner_item_id: 't0', tool: 'consult', outcome: 'done', spoken: ANSWER };
  const told = [];
  const { r, release } = lookupRig({ transcript: (body) => {
    told.push(body.told || []);
    return body.events.length ? null : { status: 200, body: { ok: true, kept: 0, completed: [completed] } };
  } });
  await r.s.start('openai');
  r.c.utter(ASKED); await flush();
  r.c.options.onAssistantTurn('Checking.');
  r.advance(2000); release(done()); await flush();
  r.c.options.onAssistantTurn(ANSWER);
  r.advance(60000); await flush();                                     // heartbeats report it again, three times
  for (let i = 0; i < 3; i++) { r.c.options.onAssistantTurn('.'); r.advance(20000); await flush(); }
  assert.deepEqual(r.c.instructions, [SAY + D.CHECKING, D.answerLine(ANSWER)], 'the answer was said twice');
  assert.ok(told.some((ids) => ids.includes('t0')), 'the page never acknowledged the answer it had already said');
});

await t.check('a lookup the host answers pending (past its own wait) says so once, then its finished answer once', async () => {
  const completed = { owner_item_id: 't0', tool: 'consult', outcome: 'done', spoken: ANSWER };
  let ready = false;
  const { r, release } = lookupRig({ transcript: (body) => (body.events.length || !ready ? null
    : { status: 200, body: { ok: true, kept: 0, completed: [completed] } }) });
  await r.s.start('openai');
  r.c.utter(ASKED); await flush();
  r.c.options.onAssistantTurn('Checking.');
  r.advance(75000); release({ status: 200, body: { ok: false, outcome: 'pending', spoken: D.PENDING } }); await flush();
  r.c.options.onAssistantTurn(D.PENDING);
  ready = true;
  for (let i = 0; i < 4; i++) { r.advance(20000); await flush(); r.c.options.onAssistantTurn('.'); await flush(); }
  const late = D.answerLine(ANSWER, 'About what you asked earlier:');
  assert.deepEqual(r.c.instructions, [SAY + D.CHECKING, SAY + D.PENDING, late]);
});

await t.check('a confirmed request (his yes, not a lookup) is unchanged: no checking line, the usual holding lines', async () => {
  let release = null;
  const r = rig({ routes: {
    '/utterance': (body) => ({ status: 200, body: { ok: true, ...(body.text === 'Yes.'
      ? { decision: 'confirm', kind: 'consult', action_id: 'a1', owner_item_id: 't0', confirm_item_id: 't1', text_sha256: 'h' }
      : { decision: 'ask', kind: 'consult', action_id: 'a1', text_sha256: 'h' }) } }),
    '/consult': () => new Promise((res) => { release = () => res(done('Item 12 is in build.')); }) } });
  await r.s.start('openai');
  r.c.utter('Close item 12.'); await flush();
  r.c.options.onAssistantTurn('Shall I go ahead?');
  r.c.utter('Yes.'); await flush();
  assert.ok(!r.c.instructions.includes(SAY + D.CHECKING));
  r.advance(8000); await flush();
  assert.equal(r.c.instructions.at(-1), 'Still checking.');
  release(); await flush();
});

t.done();
