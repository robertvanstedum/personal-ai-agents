// Codex's integrated attribution probe (review-evidence-codex-A1-last-pass/integrated-attribution.mjs, CC_23), as
// expected-behaviour regressions of A1's binding pass (Spec 165 §5.1 and §7 as amended for Robert's C2 decision, 28 Oct 2026:
// "tie by construction"). The real direct page (direct-voice-session.js), the real controller and the real adapters, with
// fake HTTP, synthetic provider events and a fake clock. No provider, microphone or network; nothing is acted on.
//
// What is asserted is what reaches the server's consult route. Adapted for v3 (confirm by voice: the app acts; the voice
// only talks): on both providers a model's call posts nothing at all and is answered NO_ACTIONS with no reply asked; each
// finished owner item is posted to the utterance route under its own id with its own words; Check with MC still posts the
// id and words of one capture record. The binding itself (which response is the page's, superseded and cut-short calls,
// the turns not acted on, LV1–LV4) still runs, and each case below says what it now asserts.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { DIRECT, SID, flush } from '../js_helpers/direct_voice_rig.mjs';

const root = process.env.VOICE_REVIEW_ROOT ? pathToFileURL(process.env.VOICE_REVIEW_ROOT + '/') : new URL('../../', import.meta.url);
const { RealtimeVoiceController } = await import(new URL('core/realtime_voice/static/realtime-voice-controller.js', root));
const { XAIWebSocketAdapter } = await import(new URL('core/realtime_voice/static/adapters/xai-websocket-adapter.js', root));
const { OpenAIWebRTCAdapter } = await import(new URL('core/realtime_voice/static/adapters/openai-webrtc-adapter.js', root));
const { directVoice, answerLine, summaryLine } = await import(process.env.VOICE_SESSION_MODULE ? pathToFileURL(process.env.VOICE_SESSION_MODULE)
  : new URL('core/realtime_voice/static/direct-voice-session.js', root));

// The capabilities answer as the core gives it under v3 (capabilities.MODEL_ACTIONS: none; PAGE_RESPONSES: OpenAI), and the
// bootstrap's page_responses from it.
const MODEL_ACTIONS = { openai: false, xai: false };
const PAGE_RESPONSES = { openai: true, xai: false };
const HOST = { ...DIRECT, providers: [{ provider: 'openai', label: 'OpenAI' }, { provider: 'xai', label: 'Grok' }], model_actions: MODEL_ACTIONS,
  page_responses: PAGE_RESPONSES };
const failures = [];
let cases = 0;

async function run(provider, scenario, { routes = {} } = {}) {
  const real = Date.now; let now = 100000; Date.now = () => now;
  const a = provider === 'xai' ? new XAIWebSocketAdapter() : new OpenAIWebRTCAdapter();
  const frames = [];
  if (provider === 'openai') a._dc = { send: (f) => frames.push(JSON.parse(f)), close() {} };
  else a._ws = { send: (f) => frames.push(JSON.parse(f)), readyState: 1, close() {} };
  const posts = [], notices = [], timers = new Map(), unbound = [];
  let serial = 0, c = null;
  class C extends RealtimeVoiceController {
    constructor(options) { super(options); c = this; }
    // The bootstrap, as the server answers it: page_responses only where the provider is eligible.
    async startSession() {
      this._adapter = a; this._sessionId = SID; this._state = 'active';
      this._owned = this._pageResponses && PAGE_RESPONSES[provider] === true;
      this._wireAdapterEvents();
      this._onSessionInfo({ voice_session_id: SID, provider, voice_notices: [] });
      this._onStateChange('active');
    }
  }
  const ui = new Proxy({ notice: (t) => notices.push(t) }, { get: (o, k) => o[k] || (() => {}) });
  const s = directVoice({ page: { conversation: { id: 'c-1' } }, direct: HOST, ui, headers: () => ({}), clock: () => now,
    fetch: async (url, opts = {}) => {
      const body = opts.body ? JSON.parse(opts.body) : null;
      if (opts.method === 'POST') posts.push({ url, body });
      const route = Object.keys(routes).find((k) => url.split('?')[0].endsWith(k));
      const out = route ? await routes[route](body) : url.endsWith('/consult') ? { ok: true, outcome: 'done', spoken: 'Done' }
        : url.endsWith('/end') ? { ok: true, ended: true, capture: 'complete' } : { ok: true, kept: body?.events?.length || 0 };
      // A route may answer with a status of its own: {__status, body}.
      const status = out && Number.isInteger(out.__status) ? out.__status : 200, answer = status === 200 && !out?.__status ? out : out.body;
      return { ok: status >= 200 && status < 300, status, json: async () => answer };
    },
    loadController: async () => ({ RealtimeVoiceController: C }), warn: (_words, info) => unbound.push(info),
    setTimeout: (fn, ms) => { timers.set(++serial, { fn, due: now + ms }); return serial; }, clearTimeout: (id) => timers.delete(id),
    setInterval: () => ++serial, clearInterval: () => {} });
  const ev = (e) => a._handleServerEvent(e);
  const tagOf = () => frames.filter((f) => f.type === 'response.create').at(-1)?.response?.metadata?.minimoi_tag || null;
  const t = {
    a, c: () => c, s, frames, notices, ev, unbound,
    consults: () => posts.filter((p) => p.url.endsWith('/consult')).map((p) => [p.body.owner_item_id, p.body.owner_transcript_item]),
    creates: () => frames.filter((f) => f.type === 'response.create').length,
    start: (id) => ev({ type: 'input_audio_buffer.speech_started', ...(id ? { item_id: id } : {}) }),
    stop: () => ev({ type: 'input_audio_buffer.speech_stopped' }),
    // v3: a committed item's response waits for its decision (the utterance route, fake HTTP): settled before going on.
    commit: async (id) => { ev({ type: 'input_audio_buffer.committed', item_id: id }); await flush(); await flush(); },
    utterances: () => posts.filter((p) => p.url.endsWith('/utterance')).map((p) => [p.body.owner_item_id, p.body.text]),
    said: (id, text) => ev({ type: 'conversation.item.input_audio_transcription.completed', item_id: id, transcript: text, event_id: 'ev_' + id }),
    // A response.created the provider made on its own (Codex's probe's created()), or the one the page asked for, echoed.
    created: (id) => ev({ type: 'response.created', response: { id } }),
    asked: (id) => ev({ type: 'response.created', response: { id, ...(tagOf() ? { metadata: { minimoi_tag: tagOf() } } : {}) } }),
    call: (id, name = 'consult') => ev({ type: 'response.done', response: { id, status: 'completed', output: [{ type: 'function_call', call_id: 'call_' + id, name, arguments: '{}' }] } }),
    over: (id) => ev({ type: 'response.done', response: { id, status: 'completed', output: [] } }),
    pass: (ms) => { now += ms; },
    // The page's own timers, run in order up to ms from now (the controller's are on Date.now, read at its next event).
    async advance(ms) {
      const until = now + ms;
      for (;;) {
        const next = [...timers].filter(([, x]) => x.due <= until).sort((p, q) => p[1].due - q[1].due)[0];
        if (!next) break;
        timers.delete(next[0]); now = next[1].due; next[1].fn(); await flush();
      }
      now = until; await flush();
    },
    drained: (id, outcome = 'drained') => a._emit('playback_drained', { response_id: id, outcome }),
    posts: (end) => posts.filter((p) => p.url.split('?')[0].endsWith(end)).map((p) => p.body),
    refusedCalls: () => frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output')
      .filter((f) => JSON.parse(f.item.output).error === 'no_model_actions').map((f) => f.item.call_id),
    outputs: () => frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output')
      .map((f) => [f.item.call_id, JSON.parse(f.item.output)]),
    lines: () => frames.filter((f) => f.type === 'conversation.item.create' && f.item.type === 'message').map((f) => f.item.content[0].text),
  };
  try { await s.start(provider); await scenario(t); } finally { Date.now = real; }
}
async function check(name, fn) { cases++; try { await fn(); } catch (e) { failures.push(`${name}: ${e.message.split('\n')[0]}`); } }

for (const provider of ['xai', 'openai']) {
  const owned = PAGE_RESPONSES[provider];
  // F1, Codex's exact case (adapted: no model call acts): a response starts for an untranscribed cough, then new speech
  // 20 s later. Neither the cough's call nor his own response's call posts anything; his words are heard as his own item.
  await check(`F1 original (${provider}): the cough's response posts nothing; his own words act only through his own response`, () => run(provider, async (t) => {
    t.start(); t.stop(); t.created('noise'); t.pass(20000);
    t.start(); t.said('new', 'Save a note: new request'); t.stop(); if (owned) await t.commit('new');
    t.call('noise'); await flush();
    assert.deepEqual(t.consults(), []);
    if (owned) assert.deepEqual(t.unbound.map((u) => u.reason).filter((r) => r !== 'model_call'), ['unrequested']);   // logged
    if (owned) { t.asked('R_new'); t.call('R_new'); await flush(); assert.deepEqual(t.consults(), []); assert.ok(t.refusedCalls().includes('call_R_new')); }
    assert.deepEqual(t.utterances(), [['t0', 'Save a note: new request']]);
  }));
  // LP1 (adapted): an expired item-less span's late transcript, then a newer response's call: nothing posted; each item's
  // words are heard under its own id.
  await check(`LP1 (${provider}): at +3.8 s the new response never posts the earlier words`, () => run(provider, async (t) => {
    t.start(); t.stop(); t.created('old'); t.pass(3500); t.start(); t.pass(300); t.said('old_item', 'Save a note: earlier request'); t.stop();
    if (owned) { await t.commit('new_item'); t.asked('new'); } else t.created('new');
    t.call('new'); await flush();
    assert.deepEqual(t.consults(), [], 'a consult was posted for a model call');
    t.said('new_item', 'Check the board'); await flush(); await flush();
    assert.deepEqual(t.consults(), []);
    assert.deepEqual(t.utterances(), [['t0', 'Save a note: earlier request'], ['t1', 'Check the board']]);
  }));
  // LP2 (adapted): both transcripts known, then a delayed response to the first: nothing posted, by any call.
  await check(`LP2 (${provider}): a delayed response never posts the newer sentence`, () => run(provider, async (t) => {
    t.start(owned ? 'a' : null); t.said('a', 'Save a note: first request'); t.stop(); if (owned) await t.commit('a');
    t.pass(500); t.start(owned ? 'b' : null); t.said('b', 'Check the board'); t.stop(); if (owned) await t.commit('b');
    t.created('delayed_response_to_a'); t.call('delayed_response_to_a'); await flush();
    assert.deepEqual(t.consults(), [], 'the delayed response posted');
    if (owned) { t.asked('actual_b_response'); t.call('actual_b_response'); await flush(); }
    else { t.created('actual_b_response'); t.call('actual_b_response'); await flush(); }
    assert.deepEqual(t.consults(), []);
    assert.deepEqual(t.utterances(), [['t0', 'Save a note: first request'], ['t1', 'Check the board']]);
  }));
  // Controls: an ordinary first response; then a replayed completion before Check: Check sends B's id with B's words.
  await check(`control (${provider}): ordinary speech acts on its own words; a replay does not corrupt Check`, () => run(provider, async (t) => {
    t.start(owned ? 'a' : null); t.said('a', 'Save a note: first request'); t.stop(); if (owned) await t.commit('a');
    if (owned) t.asked('a_response'); else t.created('a_response');
    t.call('a_response'); await flush();
    t.pass(1000); t.start(owned ? 'b' : null); t.said('b', 'Check the board'); t.stop(); if (owned) await t.commit('b');
    t.said('a', 'Save a note: first request');                           // the identical completion, replayed
    await t.s.check(); await flush();
    assert.deepEqual(t.consults(), [['t1', 'Check the board']]);         // v3: only Check posts (adapted)
  }));
}
// I6 on the page, as v3 makes it (adapted): no Check-only notice on either provider, and the model's call is answered
// NO_ACTIONS, never acted on.
await check('I6 (xAI): the page says MC acts only through Check, and a model call is answered, never acted on', () => run('xai', async (t) => {
  assert.ok(!t.notices.some((n) => /acts only/.test(n)), JSON.stringify(t.notices));
  t.start(); t.said('a', 'Save a note: the kiln is hot'); t.stop(); t.created('R'); t.call('R', 'save_note'); await flush();
  const out = t.frames.find((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output');
  assert.equal(JSON.parse(out.item.output).error, 'no_model_actions');
  assert.deepEqual(t.consults(), []);
}));
await check('I6 (OpenAI): no check-only notice where MC may act', () => run('openai', async (t) => {
  assert.ok(!t.notices.some((n) => /acts only when you press Check/.test(n)));
}));

// ── The binding pass's independent review at 8eb30a10 (REVIEW_independent_A1_binding.md), through the real page ─────────
// B S1, the reviewer's probe (kept): a lost request's late reply posts nothing (no call acts under v3), and the lost request
// is logged as never started.
await check('B S1 (openai, no tag echoed): a lost request\'s late reply posts nothing, never C\'s words', () => run('openai', async (t) => {
  t.start(); t.said('a', 'Save a note that the kiln is hot.'); t.stop(); await t.commit('a'); t.pass(10500);
  t.start(); t.said('b', 'Hello again.'); t.stop(); await t.commit('b');
  t.created('RB'); t.over('RB');                                        // #2's own start, untagged
  t.start(); t.said('c', 'Check the board.'); t.stop(); await t.commit('c');
  t.created('RA_late'); t.call('RA_late', 'save_note'); await flush();   // #1's reply, at last
  assert.deepEqual(t.consults(), [], 'a consult was posted for the lost request\'s reply');
  assert.ok(t.unbound.some((u) => u.reason === 'never_started'));
}));
// B S2 (C4, G3 on xAI), adapted for v3: "Yes." ends, the summary is asked for; the voice says only "Your summary is ready"
// (once); xAI's late reply to "Yes." starting and draining closes nothing and is never heard as the summary; with no reply
// in the window the session ends, and End claims no playback (the summary was never spoken).
const SUMMARY = { '/summary': () => ({ ok: true, state: 'proposed', spoken: 'We checked the board.' }) };
await check('B S2 (xai): another reply starting after the summary is asked for never closes it or is heard', () => run('xai', async (t) => {
  t.start(); t.said('i0', "We're done."); t.stop(); t.created('r0'); t.over('r0');
  t.start(); t.said('i1', 'Yes.'); t.stop();
  t.s.sumUp(); await flush(); await t.advance(8000);                    // the page's pause bound: his reply has not reported
  t.pass(2000); t.c()._pump(); await flush();                          // the hold's bound passes: its timer, run now
  assert.equal(t.lines().filter((l) => l.startsWith('Say only this')).length, 1, 'the ready line was not sent');
  assert.ok(!t.lines().some((l) => l.includes('We checked the board.')), 'the summary was spoken unasked');
  t.created('auto_yes'); await flush(); t.drained('auto_yes'); await flush();
  assert.equal(t.posts('/end').length, 0, 'another reply\'s drain closed the session');
  await t.advance(8000); await t.advance(15000);                       // "ready" let go at its bound, then a silent window
  assert.equal(t.posts('/end').length, 1);
  assert.equal(t.posts('/end')[0].summary_playback, undefined);         // nothing claimed heard
}, { routes: SUMMARY }));
await check('B S2 (xai), the usual order: his reply starts first, then the summary\'s own drain ends it, heard', () => run('xai', async (t) => {
  t.start(); t.said('i1', 'Yes.'); t.stop(); t.created('auto_yes'); t.over('auto_yes');
  t.s.sumUp(); await flush(); await t.advance(8000);
  t.created('r_ready'); await flush(); t.drained('r_ready'); await flush();   // "Your summary is ready" played out
  t.start(); t.said('i2', 'Read it.'); t.stop(); t.created('their_reply'); t.over('their_reply'); await flush();
  assert.ok(t.lines().includes(summaryLine('We checked the board.')), 'the summary was not read when asked');
  t.created('r_summary'); await flush(); t.drained('r_summary'); await flush();   // its audio played out
  assert.equal(t.posts('/end')[0].summary_playback, 'drained');
}, { routes: SUMMARY }));
// G-S1 (Gate 1 at b0c3518c), adapted for v3: Check's answer is being said when he says "Yes."; the page sums up; the
// voice says only "ready"; the provider's late reply to "Yes." starts and drains: it closes nothing and is never heard.
await check('G-S1 (xai): a Check answer\'s own start never uses up the reply owed to "Yes.": the summary is not heard', () => run('xai', async (t) => {
  t.start(); t.said('i0', 'What is stuck on twelve?'); t.stop(); t.created('r0'); t.over('r0');
  t.s.check(); await flush(); await t.advance(8000);                     // Check with the person: its answer, at a pause
  assert.equal(t.lines().filter((l) => l.startsWith('Say this briefly') || l.startsWith('Say the words between the markers')).length, 1,
    'the answer line was not sent');
  t.start(); t.said('i1', 'Yes.'); t.stop();                             // "Yes." ends while the answer is in flight
  t.created('L1_or_theirs'); await flush(); t.over('L1_or_theirs'); await flush();
  t.s.sumUp(); await flush(); await t.advance(8000);
  assert.equal(t.lines().filter((l) => l.startsWith('Say only this')).length, 1, 'the ready line was not sent');
  t.created('auto_yes'); await flush(); t.drained('auto_yes'); await flush();
  assert.equal(t.posts('/end').length, 0, 'the provider\'s late reply to "Yes." closed the session');
  await t.advance(8000); await t.advance(15000);
  assert.equal(t.posts('/end').length, 1);
  assert.equal(t.posts('/end')[0].summary_playback, undefined);
}, { routes: SUMMARY }));
// LV1 (live, oa2), adapted: the call is refused once, never posted; its repeat in the second response.done is not answered
// or followed up again.
await check('LV1 (openai): the repeated call in a second response.done is not posted, answered or followed up again', () => run('openai', async (t) => {
  t.start(); t.said('a', 'Please use the consult tool to check the status of the build board.'); t.stop(); await t.commit('a');
  t.asked('RA'); t.call('RA'); await flush();
  assert.equal(t.consults().length, 0);
  const creates = t.creates();
  t.start();                                                              // "Hm", while RA still plays
  t.ev({ type: 'response.done', response: { id: 'RA', status: 'cancelled', status_details: { type: 'cancelled', reason: 'turn_detected' },
    output: [{ type: 'function_call', call_id: 'call_RA', name: 'consult', arguments: '{}' }] } });
  await flush();
  assert.equal(t.consults().length, 0, 'the repeated call was posted');
  assert.equal(t.outputs().filter(([id]) => id === 'call_RA').length, 1, 'the call was answered twice');
  assert.equal(t.creates(), creates, 'the repeated call was followed up');
}));
// LV2 (live, xa3), through the real page: Check sends his final words, never the first interim fragment xAI marked
// completed; the utterance heard is the final words too.
await check('LV2 (xai): Check sends his final words, not the first interim fragment', () => run('xai', async (t) => {
  const done = (status, transcript, n) => t.ev({ type: 'conversation.item.input_audio_transcription.completed', item_id: 'city',
    status, transcript, event_id: `city_${n}` });
  t.start('city'); done('in_progress', 'Name a large', 1); done('in_progress', 'Name a large city.', 2); t.stop();
  done('in_progress', 'Name a large city.', 3); t.created('1deaf10b'); await t.commit('city'); done('completed', 'Name a large city.', 4);
  await flush();
  await t.s.check(); await flush();
  assert.deepEqual(t.consults(), [['t0', 'Name a large city.']]);
  assert.deepEqual(t.utterances(), [['t0', 'Name a large city.']]);
  assert.deepEqual(t.posts('/transcript').flatMap((b) => b.events || []).filter((e) => e.role === 'user').map((e) => e.text),
    ['Name a large city.']);
}));
// LV4 (live, oa2), adapted: no model call is ever left without an output (it is refused at once), so the page never needs
// a pending output before his next reply; Check's result, landing while he talks, is said as a line once.
let releaseLV4 = null;
await check('LV4 (openai): a running consult is answered pending before his next reply; its result is said as a line', () => run('openai', async (t) => {
  t.start(); t.said('a', 'Please use the consult tool to check the status of the build board.'); t.stop(); await t.commit('a');
  t.asked('RA'); t.call('RA'); await flush();
  assert.deepEqual(t.outputs().map(([id, out]) => [id, out.error]), [['call_RA', 'no_model_actions']]);   // answered at once
  t.over('RA');
  t.s.check(); await flush();
  assert.equal(t.consults().length, 1);
  t.start(); t.said('hm', 'Hm'); t.stop(); await t.commit('hm');
  t.asked('RH'); t.over('RH'); await flush();
  releaseLV4(); await flush(); await flush(); await t.advance(8000);   // said at a pause (a reply with no words reports no turn)
  assert.equal(t.outputs().length, 1, 'a call was answered twice');
  assert.ok(t.lines().includes(answerLine('The board is green.')), 'its result was not said');
}, { routes: { '/consult': () => new Promise((r) => { releaseLV4 = () => r({ ok: true, outcome: 'done', spoken: 'The board is green.' }); }) } }));
// B S3 + N2 (kept, with v3's refusal): a call from a barged-in reply is answered not done, with no reply asked for it; End
// names his turn not acted on; the next reply's call is refused (no model action), never posted.
await check('B S3 + N2 (openai): a barged-in call is answered not done, and End names his turn not acted on', () => run('openai', async (t) => {
  t.start(); t.said('a', 'Save a note that A.'); t.stop(); await t.commit('a'); t.asked('RA');
  t.start();                                                              // he goes on: the provider's barge-in
  t.ev({ type: 'response.done', response: { id: 'RA', status: 'cancelled', output: [{ type: 'function_call', call_id: 'call_RA', name: 'save_note', arguments: '{}' }] } });
  await flush();
  assert.equal(t.creates(), 1, 'a reply was asked for the refused call');
  t.said('b', 'And check the board.'); t.stop(); await t.commit('b'); t.asked('RB'); t.call('RB'); await flush();
  assert.deepEqual(t.consults(), []);
  assert.deepEqual(t.outputs()[0], ['call_RA', { ok: false, outcome: 'not_done', error: 'superseded',
    spoken: 'Not run: superseded by what he said next. Nothing was done.' }]);
  await t.s.end('user_ended'); await flush();
  assert.deepEqual(t.posts('/end')[0].not_acted, ['t0']);
}));
await check('B N2 (openai): an earlier commit let go while a reply is in flight is named not acted on; a consulted one is not', () => run('openai', async (t) => {
  t.start(); t.said('a', 'What is stuck on twelve?'); t.stop(); await t.commit('a'); t.asked('RA');   // RA in flight
  t.s.check(); await flush();                                             // Check consults a (v3: the model cannot)
  t.start(); t.said('b', 'Save a note that the kiln is hot.'); t.stop(); await t.commit('b');
  t.start(); t.said('c', 'Check the board.'); t.stop(); await t.commit('c');   // b is let go: c is his newest
  t.over('RA'); await flush(); t.asked('RC'); t.over('RC'); await flush();
  assert.deepEqual(t.consults(), [['t0', 'What is stuck on twelve?']]);
  await t.s.end('user_ended'); await flush();
  assert.deepEqual(t.posts('/end')[0].not_acted, ['t1']);
}));
// B N4 (adapted: through Check): a holding line already handed on, waiting behind a reply in flight, is withdrawn when its
// consult answers.
await check('B N4 (openai): a queued holding line is withdrawn when its result arrives, and never said', () => {
  let release;
  return run('openai', async (t) => {
    t.start(); t.said('a', 'Run the nightly job.'); t.stop(); await t.commit('a'); t.asked('RA'); t.over('RA'); await flush();
    t.s.check(); await flush();
    t.start(); t.said('b', 'And the glaze?'); t.stop(); await t.commit('b'); t.asked('RB');   // his next reply, in flight
    await t.advance(16000);                                               // "Still checking." at 8 s, handed on after its pause bound
    assert.ok(!t.lines().includes('Still checking.'), 'a line was sent while a reply was in flight');
    release(); await flush();                                             // the consult answers: the line is not true now
    t.over('RB'); await flush();
    assert.ok(!t.lines().includes('Still checking.'), 'the holding line played after its result');
  }, { routes: { '/consult': () => new Promise((r) => { release = () => r({ ok: true, outcome: 'done', spoken: 'Started.' }); }) } });
});

// A3 (Gate 1 n2, n3), kept: End says why each of his turns was not acted on, so the reveal can say which; a request still
// in flight when End is pressed is one of them.
await check('n2 (openai): a call cut short is answered so; End names both turns not acted on, each with why', () => run('openai', async (t) => {
  t.start(); t.said('a', 'Save a note that A.'); t.stop(); await t.commit('a'); t.asked('RA');
  t.ev({ type: 'response.done', response: { id: 'RA', status: 'incomplete', output: [{ type: 'function_call', call_id: 'call_RA', name: 'save_note', arguments: '{}' }] } });
  await flush();
  assert.deepEqual(t.outputs()[0], ['call_RA', { ok: false, outcome: 'not_done', error: 'cut_short',
    spoken: 'Not run: the reply was cut short before it could run. Nothing was done.' }]);
  t.start(); t.said('b', 'Save a note that B.'); t.stop(); await t.commit('b'); t.asked('RB');
  t.start();                                                              // he goes on: the provider's barge-in
  t.ev({ type: 'response.done', response: { id: 'RB', status: 'cancelled', output: [{ type: 'function_call', call_id: 'call_RB', name: 'save_note', arguments: '{}' }] } });
  await flush();
  t.said('c', 'Thanks.'); t.stop(); await t.commit('c'); t.asked('RC'); t.over('RC'); await flush();
  await t.s.end('user_ended'); await flush();
  const end = t.posts('/end')[0];
  assert.deepEqual(end.not_acted, ['t0', 't1']);
  assert.deepEqual(end.not_acted_why, { t0: 'cut_short', t1: 'overtaken' });
  assert.deepEqual(t.consults(), []);
}));
await check('n3 (openai): his request whose reply is still under way when End is pressed is named: the voice ended first', () => run('openai', async (t) => {
  t.start(); t.said('a', 'What is stuck on twelve?'); t.stop(); await t.commit('a'); t.asked('RA');   // its reply still speaking
  await t.s.end('user_ended'); await flush();
  const end = t.posts('/end')[0];
  assert.deepEqual(end.not_acted, ['t0']);
  assert.deepEqual(end.not_acted_why, { t0: 'closed' });
}));
await check('n3, guard (openai): a turn already answered is not named at End; nor is one Check consulted', () => run('openai', async (t) => {
  t.start(); t.said('a', 'Hello.'); t.stop(); await t.commit('a'); t.asked('RA'); t.over('RA'); await flush();
  t.start(); t.said('b', 'Check the board.'); t.stop(); await t.commit('b'); t.asked('RB');    // under way at End
  await t.s.check(); await flush();                                                            // Check sent b
  await t.s.end('user_ended'); await flush();
  const end = t.posts('/end')[0];
  assert.equal('not_acted' in end, false);
  assert.equal('not_acted_why' in end, false);
}));
await check('n3, guard (xai): on Grok nothing is named at End (no reply of the page\'s can act there)', () => run('xai', async (t) => {
  t.start(); t.said('a', 'What is stuck on twelve?'); t.stop(); t.created('RA');
  await t.s.end('user_ended'); await flush();
  assert.equal('not_acted' in t.posts('/end')[0], false);
}));

// Gate 2 E S1 (the tester's E2), adapted for v3: two calls in one OpenAI reply are both refused (no model action), never
// posted; Check on that turn acts once, and its outcome is said once when it lands.
const HANDLING_SERVER = 'That turn is already being handled; its answer will be said once it is back. Ask anything else in it on its own.';
let releaseNote = null;
await check('E S1 (openai): a second call in one reply is told the turn is already being handled; the first is said once', () => run('openai', async (t) => {
  t.start(); t.said('a', 'Save a note that the glaze is ready and check the kiln.'); t.stop(); await t.commit('a'); t.asked('RA');
  t.ev({ type: 'response.done', response: { id: 'RA', status: 'completed', output: [
    { type: 'function_call', call_id: 'call_note', name: 'save_note', arguments: '{}' },
    { type: 'function_call', call_id: 'call_kiln', name: 'consult', arguments: '{}' }] } });
  await flush(); await flush();
  assert.deepEqual(t.consults(), []);
  assert.deepEqual(t.refusedCalls().sort(), ['call_kiln', 'call_note']);
  t.s.check(); await flush();
  assert.equal(t.consults().length, 1);
  releaseNote(); await flush(); await flush(); await t.advance(8000);
  assert.equal(t.lines().filter((l) => l === answerLine('Saved.')).length, 1, "Check's result was not said once");   // a consult's words
}, { routes: { '/consult': () => new Promise((r) => { releaseNote = () => r({ ok: true, outcome: 'done', saved: true, spoken: 'Saved.' }); }) } }));
await check('E S1 (openai): a 409 in flight with no words of its own is still told the turn is being handled', () => run('openai', async (t) => {
  t.start(); t.said('a', 'Check the kiln.'); t.stop(); await t.commit('a'); t.asked('RA'); t.over('RA'); await flush();
  await t.s.check(); await flush(); await flush(); await t.advance(8000);
  assert.ok(t.lines().includes(`Say this briefly, adding nothing: ${HANDLING_SERVER}`));
}, { routes: { '/consult': () => ({ __status: 409, body: { ok: false, error: 'in flight' } }) } }));

if (failures.length) {
  console.log(`${failures.length} of ${cases} integrated binding regressions FAILED:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`${cases} integrated binding regressions passed (Codex's F1, LP1, LP2 and controls through the real page, controller and adapters; fake HTTP).`);
