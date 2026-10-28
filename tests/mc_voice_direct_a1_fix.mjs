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
  await r.c.fn({ call_id: 'call_1', name: 'consult', arguments: {} });
  const [consult] = r.posts('/consult');
  assert.equal(consult.body.owner_item_id, 't0');
  assert.equal('owner_turn' in consult.body, false);
  assert.equal(r.posts('/transcript')[0].body.events[0].item_id, 't0');
  assert.equal(consult.body.owner_transcript_item, 'What is stuck on #12?');
});
await t.check('item 1: an owner turn that completes after a later item appeared is named by its own place', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.hear([user('Check the kiln', false)]);
  const first = r.c.respond();                                                                // answers t0, still being transcribed
  r.c.hear([user('Check the kiln', false), assistant('Let me', false), user('and the glaze', false)]);
  r.c.hear([user('Check the kiln', false), assistant('Let me', false), user('and the glaze')]);   // the later one first
  r.c.options.onUserTurn('and the glaze');
  await r.c.fn({ call_id: 'call_b', name: 'consult', arguments: {} });
  r.c.hear([user('Check the kiln'), assistant('Let me', false), user('and the glaze')]);
  r.c.options.onUserTurn('Check the kiln');
  await r.c.callFrom(first, { call_id: 'call_a', name: 'consult', arguments: {} });
  assert.deepEqual(r.posts('/consult').map((p) => [p.body.owner_item_id, p.body.owner_transcript_item]),
    [['t2', 'and the glaze'], ['t0', 'Check the kiln']]);
});
await t.check('item 1: Check and the model on one turn name one id; the same words said again are a new id', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('Make a note that the kiln needs a new seal');
  await r.s.check();
  await r.c.fn({ call_id: 'call_m', name: 'save_note', arguments: {} });
  r.c.utter('Make a note that the kiln needs a new seal');
  await r.c.fn({ call_id: 'call_n', name: 'save_note', arguments: {} });
  assert.deepEqual(r.posts('/consult').map((p) => p.body.owner_item_id), ['t0', 't0', 't1']);
});

// ── R1 P1-2, R2 S4: a model call that arrives before its own transcript (repro_a1.mjs R1, R2) ───────────────────────────
await t.check('R1 P1-2: a call before its transcript waits for it, then acts once on the new words and its turn', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('What is stuck on #12?');
  await r.c.fn({ call_id: 'a', name: 'consult', arguments: {} });
  r.c.speechStopped();                                                                      // he says the next thing
  const pending = r.c.fn({ call_id: 'b', name: 'save_note', arguments: { note: 'the kiln needs a new seal' } });
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
  await r.c.fn({ call_id: 'a', name: 'consult', arguments: {} });
  r.c.speechStopped();
  const pending = r.c.fn({ call_id: 'b', name: 'save_note', arguments: {} });
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
  const first = r.c.fn({ call_id: 'a', name: 'consult', arguments: {} });
  await flush();
  r.c.speechStopped();
  const second = r.c.fn({ call_id: 'b', name: 'save_note', arguments: {} });
  await flush();
  r.c.utter('Make a note that the kiln is hot');
  const out = await second; release(); await first;
  assert.notEqual(out.outcome, 'pending');
  assert.deepEqual(r.posts('/consult').map((p) => p.body.owner_item_id), ['t0', 't1']);
});
await t.check('R1 P1-2: an empty transcript is not words: nothing is sent', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('What is stuck on #12?');
  await r.c.fn({ call_id: 'a', name: 'consult', arguments: {} });
  r.c.speechStopped();
  const pending = r.c.fn({ call_id: 'b', name: 'consult', arguments: {} });
  await flush();
  r.c.hear([...r.c.turns, user('')]); r.c.options.onUserTurn('');                          // transcribed, and empty
  assert.equal((await pending).error, 'not_heard');
  assert.equal(r.posts('/consult').length, 1);
});

// ── Item 4: the page's own outcomes use §5.1's words; only done is ok ───────────────────────────────────────────────────
await t.check('item 4: a turn still running is pending, an answer that never came is unknown, a refusal is not done', async () => {
  const answers = { a: { status: 409, body: { ok: false, error: 'in flight' } }, b: 'network', c: { status: 400, body: null } };
  const r = rig({ routes: { '/consult': (body) => answers[body.call_id] } });
  await r.s.start('openai');
  r.c.utter('Run the nightly job.');
  const first = r.c.respond(), second = r.c.follow({ response_id: first }), third = r.c.follow({ response_id: second });
  assert.deepEqual(await r.c.callFrom(first, { call_id: 'a', name: 'consult', arguments: {} }),
    { ok: false, error: 'in flight', outcome: 'pending', spoken: "That's taking longer; the answer will be in the conversation." });
  assert.deepEqual(await r.c.callFrom(second, { call_id: 'b', name: 'consult', arguments: {} }),   // chained: the same turn
    { ok: false, error: 'unknown', outcome: 'unknown', spoken: "I didn't get the result back. Check it in writing before asking again." });
  assert.deepEqual(await r.c.callFrom(third, { call_id: 'c', name: 'consult', arguments: {} }),
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
    assert.match(r.c.instructions.at(-1), /^Say only this, then stop: We looked at item twelve\.$/);
  }
});

// ── Item 5: after an unknown, the page asks once what became of the turn ─────────────────────────────────────────────
await t.check('item 5: an answer lost twice is asked about once, and the stored outcome is what is said', async () => {
  const r = rig({ routes: { '/consult': () => 'network',
    '/t0': () => ({ status: 200, body: { ok: true, outcome: 'done', owner_item_id: 't0', receipt: 'mc-9', spoken: 'Item 12 is in build.' } }) } });
  await r.s.start('openai');
  r.c.utter('What is stuck on #12?');
  const out = await r.c.fn({ call_id: 'call_1', name: 'consult', arguments: {} });
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
    const out = await r.c.fn({ call_id: 'call_1', name: 'consult', arguments: {} });
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
  const r = rig({ routes: {
    '/transcript': (body) => (body.events.some((e) => e.text === "We're done.") ? { status: 200, body: { ok: true, kept: 1, propose_end: true, proposing: true } }
      : body.events.length ? { status: 200, body: { ok: true, kept: 1, proposing: false } } : null),
    '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We checked twelve.' } }) } });
  await r.s.start('openai');
  assert.equal(r.s.state(), 'live');
  r.c.hear([user("We're done.")]); await flush();
  assert.equal(r.s.proposing(), true);
  r.c.hear([user("We're done."), user('Actually, the glaze first.')]); await flush();
  assert.equal(r.s.proposing(), false, 'the buttons stayed up after the server withdrew the proposal');
  await r.s.sumUp(); await flush();
  assert.deepEqual(r.lifecycle, ['live', 'closing', 'summarizing', 'speaking_summary']);
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
  await r.s.sumUp(); await flush();
  assert.deepEqual(r.c.tracked, ['resp_1']);
  r.c.options.onAssistantTurn('We looked at item twelve.'); await flush();      // its words, generated: not heard yet
  r.c.drained('resp_other'); await flush();
  assert.deepEqual(r.c.ended, []);
  r.c.drained('resp_1'); await flush();
  assert.deepEqual(r.c.ended, ['normal_end']);
  const b = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We looked at item twelve.' } }) } });
  await b.s.start('openai');
  await b.s.sumUp(); await flush();
  b.c.options.onInputState('speech_started'); await flush();                     // barge-in
  assert.deepEqual(b.c.ended, ['normal_end']);
});

// ── Codex's A1 review, CX A1-C4 (codex_a1_repro.mjs): confirmed fixed by 609a0fac ─────────────────────────────────────
await t.check('CX A1-C4: an earlier response on the same topic never closes a summary that has not been heard', async () => {
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'The agenda is complete.' } }) } });
  await r.s.start('openai'); await r.s.sumUp(); await flush();
  r.c.options.onAssistantTurn('The agenda is complete. Now we can discuss tomorrow.'); await flush();   // the older response
  r.c.drained('resp_older'); await flush();
  assert.deepEqual(r.c.ended, [], 'closed before the summary was heard');
  r.c.drained('resp_1'); await flush();
  assert.deepEqual(r.c.ended, ['normal_end']);
});
await t.check('CX A1-C4: a summary response the controller never reports is ended by its bound alone, not by any drain', async () => {
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'The agenda is complete.' } }) } });
  await r.s.start('openai');
  r.c.speakTracked = (t) => { r.c.instructions.push(t); return Promise.resolve(null); };   // timed out: no response id
  await r.s.sumUp(); await flush();
  r.c.drained(null); r.c.drained('resp_1'); await flush();
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
  r.c.drained('resp_1'); await flush(20);
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
  const r = rig(); await r.s.start('openai');
  r.c.utter('Save a note: first request');
  const first = r.c.respond();                                                // the response to the first request starts
  await r.s.check();
  r.c.utter('Save a note: second request');
  await r.c.callFrom(first, { call_id: 'late-first-response', name: 'save_note', arguments: { note: 'first request' } });
  const p = r.posts('/consult').at(-1).body;
  assert.deepEqual([p.owner_item_id, p.owner_transcript_item], ['t0', 'Save a note: first request'], 'rebound to the newer turn');
});
await t.check('CX A1-C2: overlapping turns: each response\'s call names its own turn, whatever order the calls land in', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('Check the kiln.'); const a = r.c.respond();
  r.c.utter('And the glaze.'); const b = r.c.respond();
  await r.c.callFrom(b, { call_id: 'b', name: 'consult', arguments: {} });
  await r.c.callFrom(a, { call_id: 'a', name: 'consult', arguments: {} });
  assert.deepEqual(r.posts('/consult').map((p) => [p.body.call_id, p.body.owner_item_id, p.body.owner_transcript_item]),
    [['b', 't1', 'And the glaze.'], ['a', 't0', 'Check the kiln.']]);
});
await t.check('CX A1-C2: a call bound to no owner input is refused before anything is sent, never given the latest words', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.utter('Run the nightly job.');
  const out = await r.c.options.onFunctionCall({ call_id: 'orphan', name: 'consult', arguments: {} });   // no response id
  const unknown = await r.c.callFrom('resp_never_started', { call_id: 'stray', name: 'consult', arguments: {} });
  assert.equal(out.outcome, 'not_done'); assert.equal(unknown.outcome, 'not_done');
  assert.equal(r.posts('/consult').length, 0);
});
await t.check('CX A1-C2: a duplicate completion of the same captured item is not a new turn', async () => {
  const r = rig(); await r.s.start('openai');
  assert.equal(r.c.options.trackResponses, true);                                  // each response it asks for is tagged (V R2-M1)
  r.c.utter('Make a note that the kiln is hot');
  const first = r.c.respond();
  await r.c.callFrom(first, { call_id: 'a', name: 'save_note', arguments: {} });
  r.c.hear(r.c.turns); r.c.options.onUserTurn('Make a note that the kiln is hot');        // the same item completes again
  await r.c.callFrom(r.c.follow({ response_id: first }), { call_id: 'b', name: 'save_note', arguments: {} });   // its follow-up's call
  await r.s.check();
  assert.deepEqual(r.posts('/consult').map((p) => p.body.owner_item_id), ['t0', 't0']);
  assert.deepEqual(r.said.at(-1), [D.ALREADY_CHECKED, { transient: true }]);
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
  const said = ['Say this briefly, adding nothing: About what you asked earlier: The nightly job finished.'];
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
  const r = rig(); await r.s.start('openai');
  r.c.utter('Save a note: first request'); await flush();
  r.c.utter('Save a note: second request'); await flush();
  r.c.options.onUserTurn('Save a note: first request');                // replay
  await r.c.fn({ call_id: 'call_b', name: 'save_note', arguments: {} });
  const [p] = r.posts('/consult');
  assert.deepEqual([p.body.owner_item_id, p.body.owner_transcript_item], ['t1', 'Save a note: second request']);
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
  const r = rig({ routes: { '/consult': never } });
  await r.s.start('openai');
  r.c.utter('Run the nightly job.'); await flush();
  let out = null; r.c.fn({ call_id: 'call_1', name: 'consult', arguments: {} }).then((o) => { out = o; });
  await flush();
  r.advance(34999); await flush();
  assert.equal(out, null, 'unknown before the bound, which sits above the server\'s 25 s');
  r.advance(1); await flush(20);
  assert.equal(r.posts('/consult').length, 1);
  assert.equal(r.gets('/consult/').length, 1);
  assert.equal(out?.outcome, 'unknown');
  assert.equal(out?.spoken, D.UNKNOWN);
});
await t.check('F3: every POST the live page makes carries an abort, except the leaving page\'s keepalive End', async () => {
  const r = rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We checked twelve.' } }) } });
  await r.s.start('openai');
  r.c.utter('What is stuck on #12?'); await flush();
  await r.c.fn({ call_id: 'call_1', name: 'consult', arguments: {} });
  await r.s.keepTalking(); r.advance(20000); await flush();
  r.s.sumUp(); await flush(); r.advance(8000); await flush(); r.c.drained(r.c.tracked[0]); await flush(20);
  const posts = r.calls.filter((x) => x.method === 'POST');
  assert.deepEqual([...new Set(posts.map((x) => x.url.split('/').at(-1)))].sort(), ['consult', 'end', 'keep_talking', 'summary', 'transcript']);
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
  const { r, release } = held(PENDING_ANSWER);
  await r.s.start('openai');
  r.c.utter('Run the nightly job.'); await flush();
  let out = null; r.c.fn({ call_id: 'call_1', name: 'consult', arguments: {} }).then((o) => { out = o; }); await flush();
  r.advance(7999); assert.deepEqual(r.c.instructions, []);
  r.advance(1); assert.deepEqual(r.c.instructions, ['Still checking.']);
  r.c.options.onAssistantTurn('Still checking.');                      // that line's response is done
  r.advance(8999); assert.deepEqual(r.c.instructions, ['Still checking.']);
  r.advance(1); assert.deepEqual(r.c.instructions, ['Still checking.', 'Still working on it.']);
  r.c.options.onAssistantTurn('Still working on it.');
  r.advance(8000); release(); await flush();                           // 25 s: the server's own pending answer
  assert.equal(out?.outcome, 'pending');
  r.advance(30000); await flush();
  assert.deepEqual(r.c.instructions, ['Still checking.', 'Still working on it.']);    // nothing more: the lines are let go
});
await t.check('G2: a consult answered at 9 s gets one holding line only', async () => {
  const { r, release } = held(null);
  await r.s.start('openai');
  r.c.utter('What is stuck on #12?'); await flush();
  const out = r.c.fn({ call_id: 'call_1', name: 'consult', arguments: {} }); await flush();
  r.advance(8000); r.c.options.onAssistantTurn('Still checking.');
  r.advance(1000); release(); assert.equal((await out).outcome, 'done');
  r.advance(30000); await flush();
  assert.deepEqual(r.c.instructions, ['Still checking.']);
});
await t.check('G2: a holding line due while he speaks waits for the pause, and one whose consult answered first is never said', async () => {
  const { r, release } = held(null);
  await r.s.start('openai');
  r.c.utter('Check the kiln.'); await flush();
  const out = r.c.fn({ call_id: 'call_1', name: 'consult', arguments: {} }); await flush();
  r.c.options.onInputState('speech_started');                          // he speaks over the wait
  r.advance(8000); assert.deepEqual(r.c.instructions, [], 'a line was sent while he spoke');
  r.c.options.onInputState('speech_stopped');                          // his words' response follows
  r.advance(1000); assert.deepEqual(r.c.instructions, []);
  r.c.options.onAssistantTurn('Sure.');                                // a pause: the line is still true, so it is said
  assert.deepEqual(r.c.instructions, ['Still checking.']);
  r.c.options.onAssistantTurn('Still checking.');
  r.c.options.onInputState('speech_started');                          // he speaks again; the second line comes due
  r.advance(8000); assert.deepEqual(r.c.instructions, ['Still checking.']);
  release(); assert.equal((await out).outcome, 'done');                // answered before the pause
  r.c.options.onInputState('speech_stopped'); r.c.options.onAssistantTurn('Right.');
  r.advance(30000); await flush();
  assert.deepEqual(r.c.instructions, ['Still checking.'], 'a holding line was said after its consult had answered');
});

// G3 (Spec 165 v0.3 §5.3): how the spoken summary's response ended (the controller's drained, barged_in, cancelled, cleared,
// no_audio, or the page's own bound: timeout) ends the session every time, and End carries it so the record says it; only
// drained is heard.
const summarized = () => rig({ routes: { '/summary': () => ({ status: 200, body: { ok: true, state: 'proposed', spoken: 'We checked twelve.' } }) } });
for (const [label, finish, expected] of [
  ['its audio heard to the end', (r) => r.c.drained(r.c.tracked[0], 'drained'), 'drained'],
  ['the provider cleared it', (r) => r.c.drained(r.c.tracked[0], 'cleared'), 'cleared'],
  ['it had no audio', (r) => r.c.drained(r.c.tracked[0], 'no_audio'), 'no_audio'],
  ['he spoke over it', (r) => r.c.options.onInputState('speech_started'), 'barged_in'],
  ['its bound passed', (r) => r.advance(D.summaryBound('We checked twelve.')), 'timeout']]) {
  await t.check(`G3: the summary ends when ${label}, and End says so (${expected})`, async () => {
    const r = summarized();
    await r.s.start('openai'); await r.s.sumUp(); await flush();
    assert.equal(r.s.state(), 'speaking_summary');
    finish(r); await flush(20);
    assert.deepEqual(r.c.ended, ['normal_end']);
    assert.equal(r.posts('/end')[0].body.summary_playback, expected);
  });
}
await t.check('G3: a drain that names no outcome ends the summary, and End claims nothing about it', async () => {
  const r = summarized();
  await r.s.start('openai'); await r.s.sumUp(); await flush();
  r.c.drained(r.c.tracked[0], null); await flush(20);
  assert.deepEqual(r.c.ended, ['normal_end']);
  assert.equal('summary_playback' in r.posts('/end')[0].body, false);
});

t.done();
