// #566 (Robert, 13 Nov 2026, Craft on the phone): the page's half of faults 1 and 3.
// Fault 1: "Check with CoS", confirmed while MC was still answering, was spoken as "That did not go through. Nothing was
// done." Waiting in line is said as "I'll take that next."; a line that gave up is said plainly, never as a failure.
// Fault 3: MC's first answer was said, then said again. One owner turn's answer is said once, whichever path brings it.
// Fake controller, fetch and timers (tests/js_helpers/direct_voice_rig.mjs).
import assert from 'node:assert/strict';
import { D, flush, rig, runner } from '../js_helpers/direct_voice_rig.mjs';

const t = runner('#566 voice layer regressions');
const SAY = 'Say this briefly, adding nothing: ';

function confirmRig(consult) {
  return rig({ routes: {
    '/utterance': (body) => ({ status: 200, body: { ok: true, ...(body.text === 'Ja, so.'
      ? { decision: 'confirm', kind: 'consult', action_id: 'a1', owner_item_id: 't0', confirm_item_id: 't1', text_sha256: 'h' }
      : { decision: 'ask', kind: 'consult', action_id: 'a1', owner_item_id: 't0', text_sha256: 'h' }) } }),
    '/consult': consult } });
}
async function confirmCheckWithCos(r) {
  await r.s.start('openai');
  r.c.utter('Check with CoS.'); await flush();
  r.c.options.onAssistantTurn('Right.');
  r.c.utter('Ja, so.'); await flush();
  r.advance(30000); await flush();
}

await t.check('fault 1: a request waiting in line for MC is said as next, never as did not go through', async () => {
  const r = confirmRig(() => ({ status: 200, body: { ok: false, outcome: 'pending', queued: true, spoken: D.QUEUED } }));
  await confirmCheckWithCos(r);
  assert.ok(r.c.instructions.includes(SAY + D.QUEUED), `not said as next: ${JSON.stringify(r.c.instructions)}`);
  assert.ok(!r.c.instructions.some((line) => line.includes(D.NOT_DONE)), 'said "did not go through"');
  assert.ok(!r.c.instructions.some((line) => line.includes(D.PENDING)), 'said "taking longer" for a turn waiting in line');
});

await t.check('fault 1: a turn the line gave up on is said plainly, never as a failure', async () => {
  const r = confirmRig(() => ({ status: 200, body: { ok: false, outcome: 'not_done', error: 'busy', spoken: D.STILL_BUSY } }));
  await confirmCheckWithCos(r);
  assert.ok(r.c.instructions.includes(SAY + D.STILL_BUSY), `not said plainly: ${JSON.stringify(r.c.instructions)}`);
  assert.ok(!r.c.instructions.some((line) => line.includes(D.NOT_DONE)), 'said "did not go through"');
});

await t.check('fault 3: an answer already said from the finished list is not said again when the retried post returns it', async () => {
  const ASKED = 'Did the deploy last night finish?';
  const ANSWER = "I can't check the delivery record or GitHub right now.";
  const completed = { owner_item_id: 't0', tool: 'consult', outcome: 'done', spoken: ANSWER };
  let first = null, second = null, ready = false;
  const r = rig({ routes: {
    '/utterance': (body) => (body.text === ASKED ? { status: 200, body: { ok: true, decision: 'confirm', kind: 'consult',
      action_id: 'a1', owner_item_id: 't0', confirm_item_id: 't0', text_sha256: 'h', lookup: true } } : null),
    '/consult': () => new Promise((res) => { if (!first) first = res; else second = res; }),
    '/transcript': (body) => (body.events.length || !ready ? null
      : { status: 200, body: { ok: true, kept: 0, completed: [completed] } }) } });
  await r.s.start('openai');
  r.c.utter(ASKED); await flush();
  r.c.options.onAssistantTurn('Checking.');
  first('network'); await flush();                                   // the answer was lost on the way: the post is retried
  assert.equal(r.posts('/consult').length, 2, 'the lost post was not retried');
  ready = true;
  r.advance(20000); await flush();                                   // the heartbeat brings the finished result first
  r.c.options.onAssistantTurn(ANSWER); await flush();
  second({ status: 200, body: { ok: true, outcome: 'done', answered: true, receipt: 'mc-1', spoken: ANSWER } }); await flush();
  r.advance(30000); await flush();
  const said = r.c.instructions.filter((line) => line.includes(ANSWER));
  assert.equal(said.length, 1, `the answer was said ${said.length} times: ${JSON.stringify(r.c.instructions)}`);
});

await t.check('fault 3: an answer said from the post is not said again when the finished list reports it later', async () => {
  const ASKED = 'Did the deploy last night finish?';
  const ANSWER = "I can't check the delivery record or GitHub right now.";
  const completed = { owner_item_id: 't0', tool: 'consult', outcome: 'done', spoken: ANSWER };
  const r = rig({ routes: {
    '/utterance': (body) => (body.text === ASKED ? { status: 200, body: { ok: true, decision: 'confirm', kind: 'consult',
      action_id: 'a1', owner_item_id: 't0', confirm_item_id: 't0', text_sha256: 'h', lookup: true } } : null),
    '/consult': () => ({ status: 200, body: { ok: true, outcome: 'done', answered: true, receipt: 'mc-1', spoken: ANSWER } }),
    '/transcript': (body) => (body.events.length ? null : { status: 200, body: { ok: true, kept: 0, completed: [completed] } }) } });
  await r.s.start('openai');
  r.c.utter(ASKED); await flush();
  r.c.options.onAssistantTurn('Checking.');
  for (let i = 0; i < 4; i++) { r.advance(20000); await flush(); r.c.options.onAssistantTurn('.'); await flush(); }
  assert.equal(r.c.instructions.filter((line) => line.includes(ANSWER)).length, 1);
});

t.done();
