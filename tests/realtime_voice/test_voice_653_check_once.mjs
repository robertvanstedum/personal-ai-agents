// #653 (Robert, 16 Nov 2026, CoS by phone): while one lookup ran, the voice said "Let me check with Master Craftsman" four
// times in two minutes (a phantom line, #651, drew more), and the lookup's answer was said only after the pending line, as
// "That's taking longer…" then "About what you asked earlier: …". The page's half: while a check is out, each chat response
// is told to say nothing about it; an answer in hand before its pending line was said replaces that line and is said
// plainly. Nothing here waits on the audio path: the lines still go at the next pause. Fake controller, fetch and timers
// (tests/js_helpers/direct_voice_rig.mjs); the server's half is tests/realtime_voice/test_voice_offer_652.py.
import assert from 'node:assert/strict';
import { D, flush, rig, runner } from '../js_helpers/direct_voice_rig.mjs';

const t = runner('#653 check-once page regressions');
const SAY = 'Say this briefly, adding nothing: ';
const ASKED = 'Did it release to production?';
const PHANTOM = 'Die Reise war ein voller Erfolg.';
const LOOKUP = { decision: 'confirm', kind: 'consult', action_id: 'a1', owner_item_id: 't0', confirm_item_id: 't0',
  text_sha256: 'h', lookup: true };
const ANSWER = "I can't confirm a production release from here. That sits with Master Craftsman.";
const COMPLETED = { owner_item_id: 't0', tool: 'consult', outcome: 'done', spoken: ANSWER };

function checkRig() {
  let release = null, ready = false;
  const r = rig({ routes: {
    '/utterance': (body) => ({ status: 200, body: { ok: true, ...(body.text === ASKED ? LOOKUP : { decision: 'chat' }) } }),
    '/consult': () => new Promise((res) => { release = (answer) => res(answer); }),
    '/transcript': (body) => (body.events.length || !ready ? null
      : { status: 200, body: { ok: true, kept: 0, completed: [COMPLETED] } }),
  } });
  return { r, release: (answer) => release(answer), finished: () => { ready = true; } };
}
const PENDING_ANSWER = { status: 200, body: { ok: false, outcome: 'pending', spoken: D.PENDING } };
const DONE = { status: 200, body: { ok: true, outcome: 'done', answered: true, receipt: 'turn-1', spoken: ANSWER } };

await t.check('an answer in hand before its pending line was said replaces that line and is said plainly', async () => {
  const { r, release, finished } = checkRig();
  await r.s.start('openai');
  r.c.utter(ASKED); await flush();
  r.c.options.onAssistantTurn('Let me check with Master Craftsman.');
  r.c.options.onInputState('speech_started');            // he (or a phantom line) talks over the wait: no pause yet
  release(PENDING_ANSWER); await flush();
  finished();
  r.advance(20000); await flush();                         // the next heartbeat brings the finished answer
  r.c.options.onInputState('speech_stopped'); await flush();
  r.c.options.onAssistantTurn('Sorry?'); await flush();    // the response to his words ends: the pause
  assert.deepEqual(r.c.instructions, [SAY + D.CHECKING, D.answerLine(ANSWER)], JSON.stringify(r.c.instructions));
  assert.ok(!r.c.instructions.some((line) => line.includes(D.PENDING)), 'the pending line was said with the answer in hand');
});

await t.check('a pending line already said keeps its answer\'s lead, and the answer is said once', async () => {
  const { r, release, finished } = checkRig();
  await r.s.start('openai');
  r.c.utter(ASKED); await flush();
  r.c.options.onAssistantTurn('Checking.');
  release(PENDING_ANSWER); await flush();
  r.c.options.onAssistantTurn(D.PENDING);
  finished();
  for (let i = 0; i < 3; i++) { r.advance(20000); await flush(); r.c.options.onAssistantTurn('.'); await flush(); }
  assert.deepEqual(r.c.instructions, [SAY + D.CHECKING, SAY + D.PENDING, D.answerLine(ANSWER, 'About what you asked earlier:')]);
});

await t.check('while a check is out, a chat response is told to say nothing about it; after its answer, not', async () => {
  const { r, release } = checkRig();
  await r.s.start('openai');
  r.c.utter(ASKED); await flush();
  r.c.options.onAssistantTurn('Checking.');
  r.c.utter(PHANTOM); await flush();                       // #651's phantom line, heard as chat
  assert.equal(r.c.chats.at(-1), `${D.CHAT_LINE} ${D.CHECK_RUNNING}`);
  r.c.options.onAssistantTurn('Sorry, say that again?');
  r.c.utter('Yeah, you can talk a little faster.'); await flush();
  assert.equal(r.c.chats.at(-1), `${D.CHAT_LINE} ${D.CHECK_RUNNING}`);
  r.c.options.onAssistantTurn('Sure.');
  release(DONE); await flush();
  r.c.options.onAssistantTurn(ANSWER);
  r.c.utter('Okay.'); await flush();
  assert.equal(r.c.chats.at(-1), D.CHAT_LINE, 'the check was still treated as out after its answer');
  // The page itself said one checking line and the answer, nothing more, however many lines he said meanwhile.
  assert.deepEqual(r.c.instructions, [SAY + D.CHECKING, D.answerLine(ANSWER)]);
  assert.match(D.CHECK_RUNNING, /say nothing about it, about checking, or about who is being asked/);
  assert.match(D.CHECK_RUNNING, /ask him briefly to say them again/);
});

await t.check('a check answered pending is still out until its finished answer is said', async () => {
  const { r, release, finished } = checkRig();
  await r.s.start('openai');
  r.c.utter(ASKED); await flush();
  r.c.options.onAssistantTurn('Checking.');
  release(PENDING_ANSWER); await flush();
  r.c.options.onAssistantTurn(D.PENDING);
  r.c.utter(PHANTOM); await flush();
  assert.equal(r.c.chats.at(-1), `${D.CHAT_LINE} ${D.CHECK_RUNNING}`);
  r.c.options.onAssistantTurn('Sorry?');
  finished();
  r.advance(20000); await flush();
  r.c.options.onAssistantTurn(ANSWER); await flush();
  r.c.utter('Okay.'); await flush();
  assert.equal(r.c.chats.at(-1), D.CHAT_LINE);
});

t.done();
