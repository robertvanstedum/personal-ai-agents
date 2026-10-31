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
// v3 (the app acts; the voice only talks): what was the model's call is now Check (a tap, nothing waiting) or a confirmed
// waiting action, and the page posts each finished owner item to the utterance route. The tests below that drove the
// model's call (item 1, R1 P1-2, item 4, item 5, CX A1-C2, F2, F3, G2, L N3, L N6) are adapted to that path; each says so.
const SAY = 'Say this briefly, adding nothing: ';
const decided = (table) => (body) => {
  const x = table[body.text];
  return x ? { status: 200, body: { ok: true, owner_item_id: body.owner_item_id, ...x } } : null;
};
const utterances = (r) => r.posts('/utterance').map((p) => [p.body.owner_item_id, p.body.text]);


// ── Item 1: identity is the capture id of the completed owner turn (§5.1 owner_item_id) ────────────────────────────────
await t.check('item 1: a consult names the capture id of the owner turn it acts on, the id its transcript post carries', async () => {
  // Adapted (v3): a confirmed action names the capture id of its request's owner turn, never his words or the yes.
  const r = rig({ routes: { '/utterance': decided({
    'What is stuck on #12?': { decision: 'ask', kind: 'consult', action_id: 'a1', text_sha256: 'h' },
    'Yes.': { decision: 'confirm', kind: 'consult', action_id: 'a1', owner_item_id: 't0', confirm_item_id: 't1', text_sha256: 'h' } }) } });
  await r.s.start('openai');
  r.c.utter('What is stuck on #12?'); await flush();
  r.c.utter('Yes.'); await flush();
  const [consult] = r.posts('/consult');
  assert.equal(consult.body.owner_item_id, 't0'); assert.equal(consult.body.confirm_item_id, 't1');
  assert.equal('owner_turn' in consult.body, false); assert.equal('owner_transcript_item' in consult.body, false);
  assert.equal(r.posts('/transcript')[0].body.events[0].item_id, 't0');
  assert.deepEqual(utterances(r), [['t0', 'What is stuck on #12?'], ['t1', 'Yes.']]);
});
await t.check('item 1: an owner turn that completes after a later item appeared is named by its own place', async () => {
  // Adapted (v3): each finished owner item is posted to the utterance route under its own place, in the order they finish.
  const r = rig(); await r.s.start('openai');
  r.c.hear([user('Check the kiln', false)]);
  r.c.hear([user('Check the kiln', false), assistant('Let me', false), user('and the glaze', false)]);
  r.c.hear([user('Check the kiln', false), assistant('Let me', false), user('and the glaze')]);   // the later one first
  r.c.hear([user('Check the kiln'), assistant('Let me', false), user('and the glaze')]);
  await flush(12);
  assert.deepEqual(utterances(r), [['t2', 'and the glaze'], ['t0', 'Check the kiln']]);
});
await t.check('item 1: Check and the model on one turn name one id; the same words said again are a new id', async () => {
  // Adapted (v3): Check and his spoken yes on one waiting action send it once, on its id; the same words again are new.
  const r = rig({ routes: { '/utterance': (body) => (body.text === 'Yes.'
    ? { status: 200, body: { ok: true, decision: 'confirm', kind: 'note', action_id: 'a-t0', owner_item_id: 't0', confirm_item_id: 't1', text_sha256: 'h' } }
    : { status: 200, body: { ok: true, decision: 'ask', kind: 'note', action_id: `a-${body.owner_item_id}`, owner_item_id: body.owner_item_id, text_sha256: 'h' } }) } });
  await r.s.start('openai');
  r.c.utter('Make a note that the kiln needs a new seal'); await flush();
  await r.s.check();
  r.c.utter('Yes.'); await flush();
  r.c.utter('Make a note that the kiln needs a new seal'); await flush();
  await r.s.check();
  assert.deepEqual(r.posts('/consult').map((p) => [p.body.action_id, p.body.owner_item_id]), [['a-t0', 't0'], ['a-t2', 't2']]);
});

// ── R1 P1-2, R2 S4: a model call that arrives before its own transcript (repro_a1.mjs R1, R2) ───────────────────────────
await t.check('R1 P1-2: a call before its transcript waits for it, then acts once on the new words and its turn', async () => {
  // Adapted (v3): an utterance is heard only once its own words complete; a commit before them posts nothing yet.
  const r = rig(); await r.s.start('openai');
  r.c.utter('What is stuck on #12?'); await flush();
  r.c.speechStopped(); await flush();                                                       // he says the next thing
  assert.equal(r.posts('/utterance').length, 1, 'heard before its own words were transcribed');
  r.advance(1500);
  r.c.utter('Make a note that the kiln needs a new seal'); await flush();                   // the transcript lands
  assert.deepEqual(utterances(r), [['t0', 'What is stuck on #12?'], ['t1', 'Make a note that the kiln needs a new seal']]);
  assert.equal(r.posts('/consult').length, 0);                                              // nothing acts on hearing
});
await t.check('R1 P1-2: a call whose transcript never comes sends nothing and says it did not catch it', async () => {
  // Adapted (v3): a commit whose words never come is never heard, and nothing acts on other words.
  const r = rig(); await r.s.start('openai');
  r.c.utter('What is stuck on #12?'); await flush();
  r.c.speechStopped();
  r.advance(10000); await flush();
  assert.equal(r.posts('/utterance').length, 1); assert.equal(r.posts('/consult').length, 0);
});
await t.check('R1 P1-2 (repro R2): a call before its transcript, while the earlier consult runs, is not absorbed as pending', async () => {
  // Adapted (v3): an utterance said while a confirmed action runs is still heard on its own, never absorbed by it.
  let release;
  const r = rig({ routes: {
    '/utterance': decided({ 'Check the kiln temperature log.': { decision: 'ask', kind: 'consult', action_id: 'a1', text_sha256: 'h' },
      'Yes.': { decision: 'confirm', kind: 'consult', action_id: 'a1', owner_item_id: 't0', confirm_item_id: 't1', text_sha256: 'h' },
      'Make a note that the kiln is hot': { decision: 'ask', kind: 'note', action_id: 'a2', text_sha256: 'h2' } }),
    '/consult': () => new Promise((res) => { release = () => res({ status: 200, body: { ok: true, outcome: 'done', spoken: 'Logged.' } }); }) } });
  await r.s.start('openai');
  r.c.utter('Check the kiln temperature log.'); await flush();
  r.c.utter('Yes.'); await flush();
  r.c.utter('Make a note that the kiln is hot'); await flush();
  assert.deepEqual(utterances(r).map(([item]) => item), ['t0', 't1', 't2']);
  assert.equal(r.posts('/consult').length, 1);
  release(); await flush();
});
await t.check('R1 P1-2: an empty transcript is not words: nothing is sent', async () => {
  // Adapted (v3): an empty transcript is not an utterance: nothing is posted to be heard.
  const r = rig(); await r.s.start('openai');
  r.c.utter('What is stuck on #12?'); await flush();
  r.c.speechStopped();
  r.c.hear([...r.c.turns, user('')]); r.c.options.onUserTurn(''); await flush();            // transcribed, and empty
  assert.equal(r.posts('/utterance').length, 1);
});

// ── Item 4: the page's own outcomes use §5.1's words; only done is ok ───────────────────────────────────────────────────
await t.check('item 4: a turn still running is pending, an answer that never came is unknown, a refusal is not done', async () => {
  // Adapted (v3): through Check; the voice is given the outcome in content-free words.
  const answers = { t0: { status: 409, body: { ok: false, error: 'in flight' } }, t1: 'network', t2: { status: 400, body: null } };
  const r = rig({ routes: { '/consult': (body) => answers[body.owner_item_id] } });
  await r.s.start('openai');
  r.c.utter('Run the nightly job.'); await r.s.check(); await flush();
  assert.equal(r.c.instructions.at(-1), SAY + D.HANDLING);                                  // already being handled (E S1)
  r.c.options.onAssistantTurn(D.HANDLING);                                                  // each line is said at a pause
  r.c.utter('And the other one.'); await r.s.check(); await flush();
  assert.equal(r.c.instructions.at(-1), SAY + D.UNKNOWN);
  r.c.options.onAssistantTurn(D.UNKNOWN);
  r.c.utter('And a third.'); await r.s.check(); await flush();
  assert.equal(r.c.instructions.at(-1), SAY + D.NOT_DONE);
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
  const said = [D.answerLine('The nightly job finished.', 'About what you asked earlier:')];   // data (review S1)
  assert.deepEqual(r.c.instructions, said);
  r.c.options.onAssistantTurn('Good.');
  r.advance(20000); await flush();                                     // reported again: said once
  assert.deepEqual(r.c.instructions, said);
});
await t.check('R2, FR N3: Check answers at a pause, never while a response is under way or he is speaking', async () => {
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
  r.advance(1); assert.equal(r.c.instructions.length, 1);                // he is still speaking: never over his words (FR N3)
  r.advance(20000); assert.equal(r.c.instructions.length, 1);
  r.c.options.onInputState('speech_stopped');                            // a response follows his words
  r.advance(7999); assert.equal(r.c.instructions.length, 1);
  r.advance(1); assert.equal(r.c.instructions.length, 2);                // that response's end was missed: sent at the bound
});
await t.check('FR N3: the spoken summary waits for a pause: not over his words or a response under way, at most 8 s', async () => {
  for (const ending of ['turn', 'bound']) {
    const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We looked at item twelve.' } }) } });
    await r.s.start('openai');
    r.c.utter('Sum it up.');
    r.c.options.onInputState('speech_started'); r.c.options.onInputState('speech_stopped');   // a response to his words follows
    r.s.sumUp(); await flush();
    assert.deepEqual(r.c.tracked, [], `${ending}: the summary was sent while a response was under way`);
    if (ending === 'turn') r.c.options.onAssistantTurn('Sure.');                                // that response ends
    else { r.advance(7999); await flush(); assert.deepEqual(r.c.tracked, []); r.advance(1); }   // its end was missed
    await flush();
    assert.equal(r.c.tracked.length, 1, ending);
    // v3: what waits for the pause is "Your summary is ready" (the summary is shown, and read only when he asks).
    assert.match(r.c.instructions.at(-1), /^Say only this, then stop: Your summary is ready\.$/);
  }
});

// ── Item 5: after an unknown, the page asks once what became of the turn ─────────────────────────────────────────────
await t.check('item 5: an answer lost twice is asked about once, and the stored outcome is what is said', async () => {
  // Adapted (v3): through Check.
  const r = rig({ routes: { '/consult': () => 'network',
    '/t0': () => ({ status: 200, body: { ok: true, outcome: 'done', owner_item_id: 't0', receipt: 'mc-9', spoken: 'Item 12 is in build.' } }) } });
  await r.s.start('openai');
  r.c.utter('What is stuck on #12?');
  await r.s.check(); await flush();
  assert.equal(r.posts('/consult').length, 2);
  const [asked] = r.gets('/consult/');
  assert.equal(asked.url, `/guild-next/api/v1/mc/voice/direct/api/realtime-voice/consult/${SID}/t0?conversation_id=c-1`);
  assert.equal(r.gets('/consult/').length, 1);
  assert.equal(r.c.instructions.at(-1), D.answerLine('Item 12 is in build.'));
});
await t.check('item 5: a read that does not answer, or says unknown, leaves it unknown', async () => {
  // Adapted (v3): through Check.
  for (const answer of ['network', { status: 200, body: { ok: false, outcome: 'unknown', spoken: 'x' } }, { status: 404, body: {} }]) {
    const r = rig({ routes: { '/consult': () => 'network', '/t0': () => answer } });
    await r.s.start('openai');
    r.c.utter('What is stuck on #12?');
    await r.s.check(); await flush();
    assert.equal(r.c.instructions.at(-1), SAY + D.UNKNOWN);
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
  assert.deepEqual(r.posts('/end')[0].body, { conversation_id: 'c-1', last_item_id: 't0', unacknowledged: ['t0'] });   // End names its conversation (S-2)
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
    { session_id: 'other', state: 'dropped', started_at: '2026-10-28T09:00:00.000Z', summary: 'none', capture: 'unconfirmed' }]);   // G4
  assert.equal(thread.querySelector(`[data-voice-line="${SID}"]`)?.textContent,
    'Some of what was said in this voice session was not kept (2 turns). What was kept is in the conversation.');
  assert.match(thread.querySelector('[data-voice-line="other"]')?.textContent || '', /may not have been kept/);
});

// ── Item 8: Keep talking (R1 P3-8), named states (R2 N9), the summary's 45 s bound and its words (R1 P3-7) ─────────────
await t.check('R1 P3-8 (repro R4): Keep talking is retried once with the same body, then said and the voice ended', async () => {
  const r = rig({ routes: { '/keep_talking': () => 'network' } });
  await r.s.start('openai');
  await r.s.keepTalking(); await flush(20);
  assert.deepEqual(r.posts('/keep_talking').map((x) => x.body), [{}, {}]);
  assert.deepEqual(r.said.at(-1), [D.KEEP_TALKING_FAILED, { loud: true, error: true }]);
  assert.deepEqual(r.c.ended, ['keep_talking_failed']);
});
await t.check('item 8: Keep talking goes after the posts queued before it, and a second try that lands is enough', async () => {
  let release, tries = 0;
  const r = rig({ routes: {
    '/transcript': (body) => (body.events.length ? new Promise((res) => { release = () => res({ status: 200, body: { ok: true, kept: 1, propose_end: true } }); }) : null),
    '/keep_talking': () => (++tries === 1 ? { status: 502, body: {} } : { status: 200, body: { ok: true, proposal: 'withdrawn' } }) } });
  await r.s.start('openai');
  r.c.hear([user("We're done.")]); await flush();
  const kept = r.s.keepTalking(); await flush();
  assert.equal(r.posts('/keep_talking').length, 0, 'sent before the post queued before it was answered');
  release(); await kept; await flush();
  assert.equal(r.posts('/keep_talking').length, 2); assert.deepEqual(r.c.ended, []);
});
await t.check('R2 N9: the session names its states, and leaving live or a later ordinary turn hides Sum up', async () => {
  const shown = [];
  // v3: the utterance route decides the end; a later ordinary turn drops it (chat, dropped), which hides Sum up.
  const r = rig({ routes: {
    '/utterance': (body) => (body.text === "We're done." ? { status: 200, body: { ok: true, decision: 'ask', kind: 'end', action_id: 'e1', owner_item_id: 't0' } }
      : { status: 200, body: { ok: true, decision: 'chat', dropped: 'e1', dropped_kind: 'end' } }),
    '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We checked twelve.' } }) } });
  await r.s.start('openai');
  assert.equal(r.s.state(), 'live');
  r.c.hear([user("We're done.")]); await flush();
  assert.equal(r.s.proposing(), true);
  r.c.hear([user("We're done."), user('Actually, the glaze first.')]); await flush();
  assert.equal(r.s.proposing(), false, 'the buttons stayed up after the server withdrew the proposal');
  await r.s.sumUp(); await flush();
  assert.deepEqual(r.lifecycle, ['live', 'closing', 'summarizing', 'summary_ready']);    // v3: shown, the window
  await r.readIt();
  assert.deepEqual(r.lifecycle, ['live', 'closing', 'summarizing', 'summary_ready', 'speaking_summary']);
  await r.s.end('user_ended'); await flush();
  assert.deepEqual(r.lifecycle.at(-1), 'ended'); assert.equal(r.s.state(), 'ended');
  const direct = rig(); await direct.s.start('openai');
  await direct.s.end('user_ended'); await flush();
  assert.deepEqual(direct.lifecycle, ['live', 'ended']);                                  // End goes straight to ended
});
await t.check('item 8, CX V2-5: the summary is awaited 45 s; past it the page says it may still appear, never unavailable', async () => {
  const r = rig({ routes: { '/summary': () => new Promise(() => {}) } });
  await r.s.start('openai');
  r.s.sumUp(); await flush();
  r.advance(44999); await flush(); assert.deepEqual(r.c.ended, []);
  r.advance(1); await flush(20);
  assert.deepEqual(r.c.ended, ['normal_end']); assert.deepEqual(r.ended.at(-1), [D.SUMMARY_MAY_APPEAR, { error: false }]);
});
await t.check('R1 P3-7 (repro R3): a lost summary answer (504, 502, no answer) may still appear; a refusal could not be asked', async () => {
  for (const [answer, words, error] of [[{ status: 504, body: {} }, D.SUMMARY_MAY_APPEAR, false], [{ status: 502, body: {} }, D.SUMMARY_MAY_APPEAR, false],
    ['network', D.SUMMARY_MAY_APPEAR, false], [{ status: 403, body: { message: 'This request could not be verified.' } }, 'This request could not be verified.', true]]) {
    const r = rig({ routes: { '/summary': () => answer } });
    await r.s.start('openai');
    await r.s.sumUp(); await flush(20);
    assert.deepEqual(r.ended.at(-1), [words, { error }], JSON.stringify(answer));
  }
});

// ── Item 9: the spoken summary is one tracked response (onPlaybackDrained), never matched by its words ─────────────────
await t.check('item 9: the summary ends on its own response\'s drain or at barge-in, never on another response or its words', async () => {
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We looked at item twelve.' } }) } });
  await r.s.start('openai');
  await r.s.sumUp(); await flush(); await r.readIt();                            // v3: read because he asked
  assert.deepEqual(r.c.tracked, ['resp_1', 'resp_2']);                           // "ready", then the summary
  r.c.options.onAssistantTurn('We looked at item twelve.'); await flush();      // its words, generated: not heard yet
  r.c.drained('resp_other'); await flush();
  assert.deepEqual(r.c.ended, []);
  r.c.drained('resp_2'); await flush();
  assert.deepEqual(r.c.ended, ['normal_end']);
  const b = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We looked at item twelve.' } }) } });
  await b.s.start('openai');
  await b.s.sumUp(); await flush(); await b.readIt();
  b.c.options.onInputState('speech_started'); await flush();                     // barge-in
  assert.deepEqual(b.c.ended, ['normal_end']);
});

// ── Codex's A1 review, CX A1-C4 (codex_a1_repro.mjs): confirmed fixed by 609a0fac ─────────────────────────────────────
await t.check('CX A1-C4: an earlier response on the same topic never closes a summary that has not been heard', async () => {
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'The agenda is complete.' } }) } });
  await r.s.start('openai'); await r.s.sumUp(); await flush(); await r.readIt();
  r.c.options.onAssistantTurn('The agenda is complete. Now we can discuss tomorrow.'); await flush();   // the older response
  r.c.drained('resp_older'); await flush();
  assert.deepEqual(r.c.ended, [], 'closed before the summary was heard');
  r.c.drained('resp_2'); await flush();
  assert.deepEqual(r.c.ended, ['normal_end']);
});
await t.check('CX A1-C4: a summary response the controller never reports is ended by its bound alone, not by any drain', async () => {
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'The agenda is complete.' } }) } });
  await r.s.start('openai');
  await r.s.sumUp(); await flush();
  r.c.speakTracked = (t) => { r.c.instructions.push(t); return Promise.resolve(null); };   // timed out: no response id
  await r.readIt();                                                      // v3: "ready" played out, then "read it"
  assert.ok(r.c.instructions.at(-1).includes('The agenda is complete.'));
  r.c.drained(null); r.c.drained('resp_2'); await flush();
  assert.deepEqual(r.c.ended, []);
  r.advance(D.summaryBound('The agenda is complete.')); await flush();
  assert.deepEqual(r.c.ended, ['normal_end']);
});

// ── CX A1-C3: finalization never awaits an abandoned capture chain; End and the reveal are bounded ─────────────────────
// Through the session's own completion promise (end() resolves once End is sent and the thread is reloaded).
const NEVER = () => new Promise(() => {});
async function settles(r, promise, step = 1000, limit = 60) {
  let done = false;
  promise.then(() => { done = true; });
  for (let i = 0; i < limit && !done; i++) { await flush(); r.advance(step); }
  await flush();
  return done;
}
await t.check('CX A1-C3 (Sum up): after the barrier gives up on a post that never answers, the session still completes', async () => {
  const r = rig({ routes: { '/transcript': (b) => (b.events.length ? NEVER() : null),
    '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'The agenda is complete.' } }) } });
  await r.s.start('openai'); r.c.hear([user('Last detail')]); await flush();
  r.s.sumUp(); await flush(); r.advance(5000); await flush(20);
  assert.deepEqual(r.posts('/summary')[0].body.unacknowledged, ['t0']);
  await r.readIt();                                                        // v3: read because he asked
  r.c.drained('resp_2'); await flush(20);
  assert.deepEqual(r.c.ended, ['normal_end']);
  assert.equal(await settles(r, r.s.end('user_ended')), true, 'the completion promise never settled');
  assert.equal(r.posts('/end').length, 1); assert.deepEqual(r.posts('/end')[0].body.unacknowledged, ['t0']);
  assert.equal(r.reveals.length, 1);
  assert.match(r.ended.at(-1)[0], /was not kept \(1 turn\)/);
});
await t.check('CX A1-C3 (End): a final post and End that never answer still let the session complete', async () => {
  const r = rig({ routes: { '/transcript': (b) => (b.events.length ? NEVER() : null), '/end': NEVER } });
  await r.s.start('openai'); r.c.hear([user('Last detail')]); await flush();
  const finished = r.s.end('user_ended');
  assert.equal(await settles(r, finished), true, 'the completion promise never settled');
  assert.equal(r.posts('/end').length, 1); assert.deepEqual(r.posts('/end')[0].body.unacknowledged, ['t0']);
  assert.equal(r.reveals.length, 1); assert.equal(r.ended.length, 1);
  assert.match(r.ended.at(-1)[0], /server did not confirm/);
});

// ── CX A1-C2: a function call acts on the owner turn its own response answers (codex_a1_repro.mjs) ─────────────────────
await t.check('CX A1-C2: Check on a first request, a second utterance, then the first response\'s delayed call: it acts on the first', async () => {
  // Adapted (v3): Check confirms the waiting action the server holds now; a newer request replaced the first, so Check
  // names the newer one, with its own id, never a mix of the two.
  const r = rig({ routes: { '/utterance': (body) => ({ status: 200, body: { ok: true, decision: 'ask', kind: 'note',
    action_id: `a-${body.owner_item_id}`, owner_item_id: body.owner_item_id, text_sha256: 'h' } }) } });
  await r.s.start('openai');
  r.c.utter('Save a note: first request'); await flush();
  r.c.utter('Save a note: second request'); await flush();
  await r.s.check();
  const p = r.posts('/consult').at(-1).body;
  assert.deepEqual([p.action_id, p.owner_item_id], ['a-t1', 't1']);
});
await t.check('CX A1-C2: overlapping turns: each response\'s call names its own turn, whatever order the calls land in', async () => {
  // Adapted (v3): overlapping utterances are decided one at a time, in order; each names its own item and words.
  let release;
  const r = rig({ routes: { '/utterance': (body) => (body.owner_item_id === 't0'
    ? new Promise((res) => { release = () => res({ status: 200, body: { ok: true, decision: 'chat' } }); }) : null) } });
  await r.s.start('openai');
  r.c.utter('Check the kiln.'); await flush();
  r.c.utter('And the glaze.'); await flush();
  assert.deepEqual(utterances(r), [['t0', 'Check the kiln.']]);                              // the second waits its turn
  release(); await flush(12);
  assert.deepEqual(utterances(r), [['t0', 'Check the kiln.'], ['t1', 'And the glaze.']]);
});
await t.check('CX A1-C2: a call bound to no owner input is refused before anything is sent, never given the latest words', async () => {
  // Adapted (v3): the page passes the controller no function bridge and asks it to refuse every model call, so no call,
  // bound or not, is ever given his words or sent anywhere.
  const r = rig(); await r.s.start('openai');
  r.c.utter('Run the nightly job.'); await flush();
  assert.equal(r.c.options.onFunctionCall, undefined); assert.equal(r.c.options.refuseCalls, true);
  assert.equal(r.posts('/consult').length, 0);
});
await t.check('CX A1-C2: a duplicate completion of the same captured item is not a new turn', async () => {
  // Adapted (v3): a duplicate completion of the same captured item is not a new utterance.
  const r = rig(); await r.s.start('openai');
  assert.equal(r.c.options.pageResponses, true);         // its responses are the page's, one at a time and tagged (C2 by construction)
  r.c.utter('Make a note that the kiln is hot'); await flush();
  r.c.hear(r.c.turns); r.c.options.onUserTurn('Make a note that the kiln is hot'); await flush();   // the same item again
  assert.deepEqual(utterances(r), [['t0', 'Make a note that the kiln is hot']]);
});

// ── CX V2-4 (spec delta review): pagehide is one bounded close envelope, counted in event ids ─────────────────────────
await t.check('CX V2-4: the leaving page\'s End is one envelope within the keepalive budget, with every unacknowledged id', async () => {
  const r = rig({ routes: { '/transcript': (b) => (b.events.length ? new Promise(() => {}) : null) } });
  await r.s.start('openai');
  const many = [];
  for (let i = 0; i < 590; i++) many.push(i % 2 ? assistant(`turn ${i} `.repeat(30)) : user(`turn ${i} `.repeat(30)));
  r.c.hear(many); await flush();
  const before = r.calls.length;
  r.s.end('pagehide');
  const sent = r.calls.slice(before);
  assert.deepEqual(sent.map((x) => x.url.split('/').pop()), ['end']);
  assert.ok(Buffer.byteLength(sent[0].opts.body) <= 64 * 1024, `${Buffer.byteLength(sent[0].opts.body)} bytes`);
  assert.equal(sent[0].body.unacknowledged.length, 590);                 // counted in unique event ids, not batches
  assert.equal(new Set(sent[0].body.unacknowledged).size, 590);
});


await t.check('FR N7: a finished result the server repeats is said once, and every later post acknowledges it', async () => {
  const done = { owner_item_id: 't0', tool: 'consult', outcome: 'done', spoken: 'The nightly job finished.' };
  const acks = [];
  const r = rig({ routes: { '/transcript': (body) => {
    acks.push(body.told || null);
    if (body.events.length) return null;
    return { status: 200, body: { ok: true, kept: 0, ...((body.told || []).includes('t0') ? {} : { completed: [done] }) } };
  } } });
  await r.s.start('openai');
  r.advance(20000); await flush();                                     // reported; the page has not acknowledged it yet
  r.advance(20000); await flush();                                     // reported again (as if the first answer was lost)
  const said = [D.answerLine('The nightly job finished.', 'About what you asked earlier:')];   // data (review S1)
  assert.deepEqual(r.c.instructions, said);                            // said once
  assert.equal(acks[0], null);                                         // nothing told before the first report
  assert.deepEqual(acks.at(-1), ['t0']);                               // the next post acknowledges it
  r.advance(20000); await flush();
  assert.deepEqual(acks.at(-1), ['t0']); assert.deepEqual(r.c.instructions, said);
});

// ── The last pass: Codex's delta review at a17ddc8d (CC_22) and Spec 165 v0.3 ──────────────────────────────────────────
// F2 (P1; Codex's page_cases.mjs, asserting the correct behaviour): Check takes the owner item's id and its words together,
// from capture's own record of that item, never from the last words a turn callback reported. A replayed completion
// changes nothing the page dispatches.
await t.check("F2 (Codex's case): Check after a replayed completion sends the newer item's id with that item's own words", async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('Save a note: first request'); await r.s.check();
  r.advance(1000); r.c.utter('Save a note: second request');
  r.c.hear([user('Save a note: first request'), user('Save a note: second request')]);
  r.c.options.onUserTurn('Save a note: first request');                // a replayed provider completion of the first
  await r.s.check();
  const sent = r.posts('/consult').map((p) => [p.body.owner_item_id, p.body.owner_transcript_item]);
  assert.deepEqual(sent, [['t0', 'Save a note: first request'], ['t1', 'Save a note: second request']]);
});
await t.check('F2: a replayed completion before any Check changes nothing: Check sends the last item with its own words', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('Save a note: the kiln is hot');
  r.c.options.onUserTurn('Delete every note');                         // words no item of capture's holds
  await r.s.check();
  const [p] = r.posts('/consult');
  assert.deepEqual([p.body.owner_item_id, p.body.owner_transcript_item], ['t0', 'Save a note: the kiln is hot']);
});
await t.check('F2: a turn callback with no completed item of capture behind it is not something to Check', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.options.onUserTurn('Save a note: words with no item');           // reported, but capture holds no completed owner item
  await r.s.check();
  assert.equal(r.posts('/consult').length, 0);
  assert.match(r.said.at(-1)[0], /^Say something first/);
});
await t.check("F2: the model's call after a replayed completion acts on its own item and words", async () => {
  // Adapted (v3): a replayed completion is not a new utterance; each item is heard once, with its own words.
  const r = rig(); await r.s.start('openai');
  r.c.utter('Save a note: first request'); await flush();
  r.c.utter('Save a note: second request'); await flush();
  r.c.options.onUserTurn('Save a note: first request'); await flush();                     // replay
  assert.deepEqual(utterances(r), [['t0', 'Save a note: first request'], ['t1', 'Save a note: second request']]);
});

// F3 (P2; Codex's last page case, asserting the correct behaviour): every POST the live page makes is bounded, with an abort
// and a bound per kind above the server's own, so none can hold the capture chain for ever. Keep talking's whole withdrawal
// (the posts before it, its POST and its one retry) has one total bound; past it, the existing failure path and End, and
// capture never resumes behind the withdrawal that was not confirmed.
const never = () => new Promise(() => {});
await t.check("F3 (Codex's case): a Keep talking request that never answers takes the failure path and End within its bound", async () => {
  const r = rig({ routes: { '/keep_talking': never } });
  await r.s.start('openai');
  let done = false; r.s.keepTalking().then(() => { done = true; }); await flush();
  r.c.utter('A new detail after Keep talking.'); await flush();
  for (let i = 0; i < 25; i++) { r.advance(1000); await flush(); }     // the clock runs as it would: each bound in turn
  assert.equal(done, true, 'Keep talking never settled');
  assert.equal(r.posts('/keep_talking').length, 2);                   // its POST and one retry, each bounded
  assert.deepEqual(r.said.at(-1), [D.KEEP_TALKING_FAILED, { loud: true, error: true }]);
  assert.deepEqual(r.c.ended, ['keep_talking_failed']);
  assert.equal(r.s.state(), 'ended');
  const posted = r.posts('/transcript').flatMap((p) => p.body.events.map((e) => e.text));
  assert.deepEqual(posted, [], 'capture resumed behind a withdrawal that was not confirmed');
  r.advance(60000); await flush(20);
  assert.deepEqual(r.posts('/transcript').flatMap((p) => p.body.events.map((e) => e.text)), []);
  const [end] = r.posts('/end');
  assert.deepEqual(end.body.unacknowledged, ['t0']);                   // End reports it not kept
});
await t.check('F3: Keep talking behind a transcript post that never answers fails at its total bound, never sent late', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.length ? never() : null) } });
  await r.s.start('openai');
  r.c.utter('A detail before Keep talking.'); await flush();
  let done = false; r.s.keepTalking().then(() => { done = true; }); await flush();
  for (let i = 0; i < 24; i++) { r.advance(1000); await flush(); }
  assert.equal(done, false);
  r.advance(1000); await flush(20);                                    // 25 s: the whole withdrawal's bound
  assert.equal(done, true);
  assert.deepEqual(r.c.ended, ['keep_talking_failed']);
  for (let i = 0; i < 60; i++) { r.advance(1000); await flush(); }     // the transcript post's bounds pass behind it
  assert.equal(r.posts('/keep_talking').length, 0, 'the withdrawal was sent after its bound');
});
await t.check('F3: a transcript post that never answers fails after two bounded attempts, and later capture goes on', async () => {
  const r = rig({ routes: { '/transcript': (body) => (body.events.some((e) => e.text === 'A lost detail.') ? never() : null) } });
  await r.s.start('openai');
  r.c.utter('A lost detail.'); await flush();
  r.advance(15000); await flush();                                     // the first attempt's bound: the same ids once more
  assert.equal(r.posts('/transcript').length, 2);
  assert.deepEqual(r.posts('/transcript').map((p) => p.body.events[0].item_id), ['t0', 't0']);
  r.advance(15000); await flush();                                     // the second attempt's bound: failed
  assert.deepEqual(r.said.at(-1), [D.NOT_KEPT, { error: true }]);
  r.c.utter('The next detail.'); await flush();
  assert.equal(r.posts('/transcript').at(-1).body.events[0].text, 'The next detail.');
  r.s.end('user'); await flush(20);
  assert.deepEqual(r.posts('/end')[0].body.unacknowledged, ['t0']);
  assert.match(r.ended.at(-1)[0], /not kept \(1 turn\)/);
});
await t.check('F3: a consult that never answers is unknown at its bound, with one reconciliation read and no second POST', async () => {
  // Adapted (v3): through Check.
  const r = rig({ routes: { '/consult': never } });
  await r.s.start('openai');
  r.c.utter('Run the nightly job.'); await flush();
  let done = false; r.s.check().then(() => { done = true; });
  await flush();
  r.advance(34999); await flush();
  assert.equal(done, false, 'unknown before the bound, which sits above the server\'s 25 s');
  r.advance(1); await flush(20);
  assert.equal(r.posts('/consult').length, 1);
  assert.equal(r.gets('/consult/').length, 1);
  r.c.options.onAssistantTurn('Still working on it.'); await flush();                      // at a pause, after the holding lines
  assert.equal(r.c.instructions.at(-1), SAY + D.UNKNOWN);
});
await t.check('F3: every POST the live page makes carries an abort, except the leaving page\'s keepalive End', async () => {
  // Adapted (v3): Check instead of the model's call, and the utterance and asked posts are bounded too.
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We checked twelve.' } }),
    '/utterance': decided({ 'Make a note that the kiln is hot.': { decision: 'ask', kind: 'note', action_id: 'a1', text_sha256: 'h' } }) } });
  await r.s.start('openai');
  r.c.utter('What is stuck on #12?'); await flush();
  await r.s.check();
  r.c.utter('Make a note that the kiln is hot.'); await flush();
  await r.s.keepTalking(); r.advance(20000); await flush();
  r.s.sumUp(); await flush(); r.advance(8000); await flush(); r.c.drained(r.c.tracked[0]); await flush(20);
  r.advance(15000); await flush(20);                                                         // the reply window: silence
  const posts = r.calls.filter((x) => x.method === 'POST');
  assert.deepEqual([...new Set(posts.map((x) => x.url.split('/').at(-1)))].sort(),
    ['asked', 'consult', 'end', 'keep_talking', 'summary', 'transcript', 'utterance']);
  for (const p of posts) assert.ok(p.opts.signal, `${p.url} is not bounded`);
});

// G2 (Spec 165 v0.3 §12.4): a slow consult gets a holding line by 8 s ("Still checking.") and a second by 17 s ("Still
// working on it."), and the server answers pending at 25 s, so there is never more than 10 s of silence. Each line still
// waits for a pause, is never sent while he speaks, and is let go once the consult has answered: it says only what is true.
const held = (answer) => {
  let release = null;
  const r = rig({ routes: { '/consult': () => new Promise((res) => { release = () => res(answer); }) } });
  return { r, release: () => release() };
};
const PENDING_ANSWER = { status: 200, body: { ok: false, outcome: 'pending', spoken: D.PENDING } };
await t.check('G2: a slow consult hears "Still checking." at 8 s and "Still working on it." at 17 s, then pending at 25 s', async () => {
  // Adapted (v3): through Check; the pending outcome is then said in its own content-free words.
  const { r, release } = held(PENDING_ANSWER);
  await r.s.start('openai');
  r.c.utter('Run the nightly job.'); await flush();
  r.s.check(); await flush();
  r.advance(7999); assert.deepEqual(r.c.instructions, []);
  r.advance(1); assert.deepEqual(r.c.instructions, ['Still checking.']);
  r.c.options.onAssistantTurn('Still checking.');                      // that line's response is done
  r.advance(8999); assert.deepEqual(r.c.instructions, ['Still checking.']);
  r.advance(1); assert.deepEqual(r.c.instructions, ['Still checking.', 'Still working on it.']);
  r.c.options.onAssistantTurn('Still working on it.');
  r.advance(8000); release(); await flush();                           // 25 s: the server's own pending answer
  assert.deepEqual(r.c.instructions, ['Still checking.', 'Still working on it.', SAY + D.PENDING]);
  r.c.options.onAssistantTurn(D.PENDING);
  r.advance(30000); await flush();
  assert.deepEqual(r.c.instructions, ['Still checking.', 'Still working on it.', SAY + D.PENDING]);   // the lines are let go
});
await t.check('G2: a consult answered at 9 s gets one holding line only', async () => {
  // Adapted (v3): through Check.
  const { r, release } = held(null);
  await r.s.start('openai');
  r.c.utter('What is stuck on #12?'); await flush();
  r.s.check(); await flush();
  r.advance(8000); r.c.options.onAssistantTurn('Still checking.');
  r.advance(1000); release(); await flush();
  r.advance(30000); await flush();
  assert.deepEqual(r.c.instructions, ['Still checking.', D.answerLine('Item 12 is in build.')]);
});
await t.check('G2: a holding line due while he speaks waits for the pause, and one whose consult answered first is never said', async () => {
  // Adapted (v3): through Check.
  const { r, release } = held(null);
  await r.s.start('openai');
  r.c.utter('Check the kiln.'); await flush();
  r.s.check(); await flush();
  r.c.options.onInputState('speech_started');                          // he speaks over the wait
  r.advance(8000); assert.deepEqual(r.c.instructions, [], 'a line was sent while he spoke');
  r.c.options.onInputState('speech_stopped');                          // his words' response follows
  r.advance(1000); assert.deepEqual(r.c.instructions, []);
  r.c.options.onAssistantTurn('Sure.');                                // a pause: the line is still true, so it is said
  assert.deepEqual(r.c.instructions, ['Still checking.']);
  r.c.options.onAssistantTurn('Still checking.');
  r.c.options.onInputState('speech_started');                          // he speaks again; the second line comes due
  r.advance(8000); assert.deepEqual(r.c.instructions, ['Still checking.']);
  release(); await flush();                                            // answered before the pause
  r.c.options.onInputState('speech_stopped'); r.c.options.onAssistantTurn('Right.');
  r.advance(30000); await flush();
  assert.deepEqual(r.c.instructions, ['Still checking.', D.answerLine('Item 12 is in build.')], 'a holding line was said after its consult had answered');
});

// G3 (Spec 165 v0.3 §5.3): how the spoken summary's response ended (the controller's drained, barged_in, cancelled, cleared,
// no_audio, or the page's own bound: timeout) ends the session every time, and End carries it so the record says it; only
// drained is heard.
const summarized = () => rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We checked twelve.' } }) } });
for (const [label, finish, expected] of [
  ['its audio heard to the end', (r) => r.c.drained(r.c.tracked.at(-1), 'drained'), 'drained'],
  ['the provider cleared it', (r) => r.c.drained(r.c.tracked.at(-1), 'cleared'), 'cleared'],
  ['it had no audio', (r) => r.c.drained(r.c.tracked.at(-1), 'no_audio'), 'no_audio'],
  ['he spoke over it', (r) => r.c.options.onInputState('speech_started'), 'barged_in'],
  ['its bound passed', (r) => r.advance(D.summaryBound('We checked twelve.')), 'timeout']]) {
  await t.check(`G3: the summary ends when ${label}, and End says so (${expected})`, async () => {
    const r = summarized();
    await r.s.start('openai'); await r.s.sumUp(); await flush(); await r.readIt();   // v3: read because he asked
    assert.equal(r.s.state(), 'speaking_summary');
    finish(r); await flush(20);
    assert.deepEqual(r.c.ended, ['normal_end']);
    assert.equal(r.posts('/end')[0].body.summary_playback, expected);
  });
}
await t.check('G3: a drain that names no outcome ends the summary, and End claims nothing about it', async () => {
  const r = summarized();
  await r.s.start('openai'); await r.s.sumUp(); await flush(); await r.readIt();
  r.c.drained(r.c.tracked.at(-1), null); await flush(20);
  assert.deepEqual(r.c.ended, ['normal_end']);
  assert.equal('summary_playback' in r.posts('/end')[0].body, false);
});

// L N3 (the last pass's verification): the holding clock starts when the model's call arrives, not after its wait for its
// words, so the first line comes by 8 s from the call even when the transcript follows the call by 2.5 s.
await t.check('L N3: a call that arrives before its words still hears "Still checking." 8 s after the call', async () => {
  // Adapted (v3): a confirmed action hears "Still checking." 8 s after it is sent, whatever was said before.
  const { r, release } = held(null);
  await r.s.start('openai');
  r.c.utter('Run the nightly job.'); await flush();
  r.s.check(); await flush();                                          // nothing waiting: Check sends his words
  r.advance(7999); assert.deepEqual(r.c.instructions, []);
  r.advance(1); assert.deepEqual(r.c.instructions, ['Still checking.'], 'the first line came later than 8 s after the call');
  release(); await flush();
});

// L N6 (the last pass's verification): an item's words are frozen at its first completion. A second completion of the same
// item with other words (xAI's .updated with completed, then .completed) changes nothing: the transcript post, Check and the
// model's call all carry the first words, so what is kept is what is acted on.
await t.check('L N6: Check acts on the words kept at the first completion, not on a later completion of the same item', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('Save a note: the kiln is hot'); await flush();
  r.c.hear([user('Save a note: the glaze is hot')]); await flush();    // the same item, completed again with other words
  await r.s.check();
  const [p] = r.posts('/consult');
  assert.deepEqual([p.body.owner_item_id, p.body.owner_transcript_item], ['t0', 'Save a note: the kiln is hot']);
  assert.deepEqual(r.posts('/transcript').flatMap((x) => x.body.events.map((e) => e.text)), ['Save a note: the kiln is hot']);
});
await t.check("L N6: the model's call acts on the words kept at the first completion", async () => {
  // Adapted (v3): the utterance heard is the words kept at the first completion, not a later completion's.
  const r = rig(); await r.s.start('openai');
  r.c.utter('Save a note: the kiln is hot'); await flush();
  r.c.hear([user('Save a note: the glaze is hot')]); await flush();
  assert.deepEqual(utterances(r), [['t0', 'Save a note: the kiln is hot']]);
});

// L N7 (the last pass's verification): the Guild thread's posts after the session (the summary decision, "Ask for a summary")
// are bounded too, with the session module's jsonPost, so a request that never answers ends in words that are true.
function threadWithHangingPosts() {
  const doc = fakeDocument(), thread = doc.createElement('ol'), timers = new Map(), sent = [];
  let next = 1, now = 0;
  thread.setAttribute('data-mc-thread', '');
  const later = (fn, ms) => { const id = next++; timers.set(id, { fn, due: now + ms }); return id; };
  const advance = (ms) => { now += ms; for (const [id, t] of [...timers]) if (t.due <= now) { timers.delete(id); t.fn(); } };
  const fetchNever = (url, opts) => { sent.push({ url, signal: opts.signal }); return new Promise(() => {}); };
  const post = D.jsonPost(fetchNever, () => ({}), { setTimeout: later, clearTimeout: (id) => timers.delete(id) });
  const DIRECT = { summary_decision_url: '/x/session/{session_id}/summary/decision', summary_url: '/x/session/{session_id}/summary' };
  const view = T.voiceThread({ doc, direct: DIRECT, thread: () => thread, mine: () => null, conversationId: () => 'c-1', post, reveal: async () => {} });
  const row = (attrs) => { const li = doc.createElement('li'); for (const [k, v] of Object.entries(attrs)) li.setAttribute(k, v);
    const meta = doc.createElement('p'), when = doc.createElement('span'); when.setAttribute('data-iso', '2026-10-28T10:20:00.000Z');
    meta.append(when); li.append(meta); thread.append(li); return li; };
  return { view, row, advance, sent };
}
await t.check('L N7: a summary decision that never answers is aborted at its bound and says it may have been kept', async () => {
  const h = threadWithHangingPosts();
  const proposal = h.row({ 'data-note': '5', 'data-voice-kind': 'summary_proposal', 'data-voice-session': SID });
  const sessions = [{ session_id: SID, state: 'ended', started_at: '2026-10-28T10:00:00.000Z', summary: 'proposed' }];
  h.view.paint(sessions, [{ id: 5, text: '[Voice summary proposal] x\n\nWe looked at #12.', voice: { session_id: SID, kind: 'summary_proposal' } }]);
  const card = proposal.querySelector('[data-voice-summary-card]');
  card.querySelector('[data-voice-summary-confirm]').click(); await flush();
  assert.ok(h.sent[0].signal, 'the decision post is not bounded');
  h.advance(15000); await flush();
  assert.equal(h.sent[0].signal.aborted, true);
  assert.equal(card.querySelector('[data-voice-summary-result]').textContent, T.DECISION_NO_ANSWER);
  assert.equal(card.querySelector('[data-voice-summary-confirm]').disabled, false);
});
await t.check('L N7: "Ask for a summary" that never answers is aborted at its bound and says the summary may still appear', async () => {
  const h = threadWithHangingPosts();
  const row = h.row({ 'data-note': '6', 'data-voice-kind': 'summary_unavailable', 'data-voice-session': SID });
  h.view.paint([{ session_id: SID, state: 'ended', summary: 'unavailable' }]);
  const ask = row.querySelector('[data-voice-summary-retry]');
  ask.click(); await flush();
  assert.ok(h.sent[0].signal, 'the ask is not bounded');
  h.advance(45000); await flush();
  assert.equal(h.sent[0].signal.aborted, true);
  assert.equal(row.querySelector('[data-voice-summary-result]').textContent, T.ASK_NO_ANSWER);
  assert.equal(ask.disabled, false);
});

// W N2 (the verification at d886bbf0): after the transcript cap capture stops observing, so its last owner turn is no longer
// his newest words. Check then says it is unavailable, instead of sending an older turn.
await t.check('W N2: after the transcript cap, Check is unavailable and sends nothing', async () => {
  let posts = 0;
  const r = rig({ routes: { '/transcript': (body) => (body.events.length && ++posts >= 2
    ? { status: 429, body: { ok: false, error: 'cap', message: 'This voice session has kept all it may.' } } : null) } });
  await r.s.start('openai');
  r.c.utter('First request.'); await flush();
  r.c.utter('Second request.'); await flush();                        // its post meets the cap: capture stops
  r.c.utter('Third request.'); await flush();
  await r.s.check();
  assert.equal(r.posts('/consult').length, 0, 'Check sent an older turn after capture stopped');
  assert.deepEqual(r.said.at(-1), [D.CHECK_FULL, { transient: true }]);
});

t.done();
