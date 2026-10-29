// Codex's integrated attribution probe (review-evidence-codex-A1-last-pass/integrated-attribution.mjs, CC_23), as
// expected-behaviour regressions of A1's binding pass (Spec 165 §5.1 and §7 as amended for Robert's C2 decision, 28 Oct 2026:
// "tie by construction"). The real direct page (direct-voice-session.js), the real controller and the real adapters, with
// fake HTTP, synthetic provider events and a fake clock. No provider, microphone or network; nothing is acted on.
//
// What is asserted is what reaches the server's consult route: on OpenAI (eligible) a model call posts only its own
// response's committed item, with that item's own words, or nothing; on xAI (not eligible until A4) the model's call posts
// nothing at all, and Check with MC still posts the id and words of one capture record.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { DIRECT, SID, flush } from '../js_helpers/direct_voice_rig.mjs';

const root = process.env.VOICE_REVIEW_ROOT ? pathToFileURL(process.env.VOICE_REVIEW_ROOT + '/') : new URL('../../', import.meta.url);
const { RealtimeVoiceController } = await import(new URL('core/realtime_voice/static/realtime-voice-controller.js', root));
const { XAIWebSocketAdapter } = await import(new URL('core/realtime_voice/static/adapters/xai-websocket-adapter.js', root));
const { OpenAIWebRTCAdapter } = await import(new URL('core/realtime_voice/static/adapters/openai-webrtc-adapter.js', root));
const { directVoice } = await import(process.env.VOICE_SESSION_MODULE ? pathToFileURL(process.env.VOICE_SESSION_MODULE)
  : new URL('core/realtime_voice/static/direct-voice-session.js', root));

// The capabilities answer as the core gives it (capabilities.MODEL_ACTIONS), and the bootstrap's page_responses from it.
const MODEL_ACTIONS = { openai: true, xai: false };
const HOST = { ...DIRECT, providers: [{ provider: 'openai', label: 'OpenAI' }, { provider: 'xai', label: 'Grok' }], model_actions: MODEL_ACTIONS };
const failures = [];
let cases = 0;

async function run(provider, scenario) {
  const real = Date.now; let now = 100000; Date.now = () => now;
  const a = provider === 'xai' ? new XAIWebSocketAdapter() : new OpenAIWebRTCAdapter();
  const frames = [];
  if (provider === 'openai') a._dc = { send: (f) => frames.push(JSON.parse(f)) };
  else a._ws = { send: (f) => frames.push(JSON.parse(f)), readyState: 1, close() {} };
  const posts = [], notices = [], timers = new Map(), unbound = [];
  let serial = 0, c = null;
  class C extends RealtimeVoiceController {
    constructor(options) { super(options); c = this; }
    // The bootstrap, as the server answers it: page_responses only where the provider is eligible.
    async startSession() {
      this._adapter = a; this._sessionId = SID; this._state = 'active';
      this._owned = this._pageResponses && MODEL_ACTIONS[provider] === true;
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
      return { ok: true, status: 200, json: async () => (url.endsWith('/consult') ? { ok: true, outcome: 'done', spoken: 'Done' } : { ok: true, kept: body?.events?.length || 0 }) };
    },
    loadController: async () => ({ RealtimeVoiceController: C }), warn: (_words, info) => unbound.push(info),
    setTimeout: (fn, ms) => { timers.set(++serial, { fn, ms }); return serial; }, clearTimeout: (id) => timers.delete(id),
    setInterval: () => ++serial, clearInterval: () => {} });
  const ev = (e) => a._handleServerEvent(e);
  const tagOf = () => frames.filter((f) => f.type === 'response.create').at(-1)?.response?.metadata?.minimoi_tag || null;
  const t = {
    a, c: () => c, s, frames, notices, ev, unbound,
    consults: () => posts.filter((p) => p.url.endsWith('/consult')).map((p) => [p.body.owner_item_id, p.body.owner_transcript_item]),
    creates: () => frames.filter((f) => f.type === 'response.create').length,
    start: (id) => ev({ type: 'input_audio_buffer.speech_started', ...(id ? { item_id: id } : {}) }),
    stop: () => ev({ type: 'input_audio_buffer.speech_stopped' }),
    commit: (id) => ev({ type: 'input_audio_buffer.committed', item_id: id }),
    said: (id, text) => ev({ type: 'conversation.item.input_audio_transcription.completed', item_id: id, transcript: text, event_id: 'ev_' + id }),
    // A response.created the provider made on its own (Codex's probe's created()), or the one the page asked for, echoed.
    created: (id) => ev({ type: 'response.created', response: { id } }),
    asked: (id) => ev({ type: 'response.created', response: { id, ...(tagOf() ? { metadata: { minimoi_tag: tagOf() } } : {}) } }),
    call: (id, name = 'consult') => ev({ type: 'response.done', response: { id, status: 'completed', output: [{ type: 'function_call', call_id: 'call_' + id, name, arguments: '{}' }] } }),
    over: (id) => ev({ type: 'response.done', response: { id, status: 'completed', output: [] } }),
    pass: (ms) => { now += ms; },
  };
  try { await s.start(provider); await scenario(t); } finally { Date.now = real; }
}
async function check(name, fn) { cases++; try { await fn(); } catch (e) { failures.push(`${name}: ${e.message.split('\n')[0]}`); } }

for (const provider of ['xai', 'openai']) {
  const owned = MODEL_ACTIONS[provider];
  // F1, Codex's exact case: a response starts for an untranscribed cough, then new speech 20 s later; the cough's call.
  await check(`F1 original (${provider}): the cough's response posts nothing; his own words act only through his own response`, () => run(provider, async (t) => {
    t.start(); t.stop(); t.created('noise'); t.pass(20000);
    t.start(); t.said('new', 'Save a note: new request'); t.stop(); if (owned) t.commit('new');
    t.call('noise'); await flush();
    assert.deepEqual(t.consults(), []);
    if (owned) assert.deepEqual(t.unbound.map((u) => u.reason), ['unrequested']);        // logged
    if (owned) { t.asked('R_new'); t.call('R_new'); await flush(); assert.deepEqual(t.consults(), [['t0', 'Save a note: new request']]); }
  }));
  // LP1: an expired item-less span's late transcript, then a newer response's call.
  await check(`LP1 (${provider}): at +3.8 s the new response never posts the earlier words`, () => run(provider, async (t) => {
    t.start(); t.stop(); t.created('old'); t.pass(3500); t.start(); t.pass(300); t.said('old_item', 'Save a note: earlier request'); t.stop();
    if (owned) { t.commit('new_item'); t.asked('new'); } else t.created('new');
    t.call('new'); await flush();
    assert.deepEqual(t.consults(), [], 'a consult was posted before its own words came');
    t.said('new_item', 'Check the board'); await flush();
    assert.deepEqual(t.consults(), owned ? [['t1', 'Check the board']] : []);
  }));
  // LP2: both transcripts known (OpenAI: named speech-start and committed ids), then a delayed response to the first.
  await check(`LP2 (${provider}): a delayed response never posts the newer sentence`, () => run(provider, async (t) => {
    t.start(owned ? 'a' : null); t.said('a', 'Save a note: first request'); t.stop(); if (owned) t.commit('a');
    t.pass(500); t.start(owned ? 'b' : null); t.said('b', 'Check the board'); t.stop(); if (owned) t.commit('b');
    t.created('delayed_response_to_a'); t.call('delayed_response_to_a'); await flush();
    assert.deepEqual(t.consults(), [], 'the delayed response posted');
    if (owned) assert.ok(t.unbound.some((u) => u.reason === 'superseded_call' && u.owner === 'a'));
    if (owned) {
      assert.equal(t.creates(), 2, "B's response was not asked for once A's settled");
      t.asked('actual_b_response'); t.call('actual_b_response'); await flush();
      assert.deepEqual(t.consults(), [['t1', 'Check the board']]);
    } else { t.created('actual_b_response'); t.call('actual_b_response'); await flush(); assert.deepEqual(t.consults(), []); }
  }));
  // Controls: an ordinary first response; then a replayed completion before Check: Check sends B's id with B's words.
  await check(`control (${provider}): ordinary speech acts on its own words; a replay does not corrupt Check`, () => run(provider, async (t) => {
    t.start(owned ? 'a' : null); t.said('a', 'Save a note: first request'); t.stop(); if (owned) t.commit('a');
    if (owned) t.asked('a_response'); else t.created('a_response');
    t.call('a_response'); await flush();
    if (owned) { t.asked('follow_a'); t.over('follow_a'); }
    t.pass(1000); t.start(owned ? 'b' : null); t.said('b', 'Check the board'); t.stop(); if (owned) t.commit('b');
    t.said('a', 'Save a note: first request');                           // the identical completion, replayed
    await t.s.check(); await flush();
    assert.deepEqual(t.consults(), owned ? [['t0', 'Save a note: first request'], ['t1', 'Check the board']] : [['t1', 'Check the board']]);
  }));
}
// I6 on the page: xAI direct says Check with MC is how MC acts, and the model's call is answered "say it again".
await check('I6 (xAI): the page says MC acts only through Check, and a model call is answered, never acted on', () => run('xai', async (t) => {
  assert.ok(t.notices.includes('On this voice, Master Craftsman acts only when you press Check with MC.'), JSON.stringify(t.notices));
  t.start(); t.said('a', 'Save a note: the kiln is hot'); t.stop(); t.created('R'); t.call('R', 'save_note'); await flush();
  const out = t.frames.find((f) => f.type === 'conversation.item.create' && f.item.type === 'function_call_output');
  assert.equal(JSON.parse(out.item.output).error, 'not_heard');
  assert.deepEqual(t.consults(), []);
}));
await check('I6 (OpenAI): no check-only notice where MC may act', () => run('openai', async (t) => {
  assert.ok(!t.notices.some((n) => /acts only when you press Check/.test(n)));
}));

if (failures.length) {
  console.log(`${failures.length} of ${cases} integrated binding regressions FAILED:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`${cases} integrated binding regressions passed (Codex's F1, LP1, LP2 and controls through the real page, controller and adapters; fake HTTP).`);
