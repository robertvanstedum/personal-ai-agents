// Codex's Chunk B review (REVIEW_codex_chunk_B.md, B1–B7 and the capture edge), as regressions that assert the CORRECT
// behaviour of the shared session module (core/realtime_voice/static/direct-voice-session.js) and the Guild thread. Converted
// from review-evidence-codex/repro_chunk_b.mjs, which asserted the defects. Fake controller, fetch and timers; no DOM,
// network, provider or microphone.
//
// To show a case fails before its fix, point it at an older module: VOICE_SESSION_MODULE=<file> VOICE_THREAD_MODULE=<file>
// (at 93bf00c6 both are minimoi_portal/guild_ui/static/js/mc_voice_direct.js). The fake controller offers the old private
// fields and the new public methods, so either module runs. Every case runs; the failures are listed, then the exit code is 1.
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import { fakeDocument } from './js_helpers/fake_dom.mjs';

const here = (p) => new URL(p, import.meta.url);
const D = await import(process.env.VOICE_SESSION_MODULE ? pathToFileURL(process.env.VOICE_SESSION_MODULE) : here('../core/realtime_voice/static/direct-voice-session.js'));
const T = await import(process.env.VOICE_THREAD_MODULE ? pathToFileURL(process.env.VOICE_THREAD_MODULE) : here('../minimoi_portal/guild_ui/static/js/mc_voice_direct.js'));
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

const SID = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f';
const BASE = '/guild-next/api/v1/mc/voice/direct/api/realtime-voice';
const DIRECT = { path: 'direct', providers: [{ provider: 'openai', label: 'OpenAI' }], default_provider: 'openai',
  bootstrap_url: `${BASE}/bootstrap`, consult_url: `${BASE}/consult`, end_url: `${BASE}/session/{session_id}/end`,
  transcript_url: `${BASE}/session/{session_id}/transcript`, summary_url: `${BASE}/session/{session_id}/summary`,
  summary_decision_url: `${BASE}/session/{session_id}/summary/decision`, heartbeat_seconds: 20, persona: 'master_craftsman',
  locale: 'en-US', card_release: '0.1.18', person: { name: 'Master Craftsman', short: 'MC' } };
const UNKNOWN = 'The answer did not come back, so whether that went through is not known. Check before asking again.';
const INCOMPLETE = 'Voice ended. Some of what was said could not be kept; the rest is in the conversation.';
const PENDING = 'The summary is still being written; it will appear in the conversation.';

function rig({ routes = {} } = {}) {
  const calls = [], said = [], ended = [], reveals = [];
  const timers = new Map(); let nextTimer = 1, now = Date.parse('2026-10-28T10:00:00.000Z');
  const later = (fn, ms, repeat = false) => { const id = nextTimer++; timers.set(id, { fn, ms, repeat, due: now + ms }); return id; };
  const advance = (ms) => {
    const until = now + ms;
    for (;;) {
      const next = [...timers.entries()].filter(([, t]) => t.due <= until).sort((a, b) => a[1].due - b[1].due)[0];
      if (!next) break;
      const [id, t] = next; now = t.due;
      if (t.repeat) t.due += t.ms; else timers.delete(id);
      t.fn();
    }
    now = until;
  };
  const fetch = async (url, opts) => {
    const body = JSON.parse(opts.body);
    calls.push({ url, opts, body });
    const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => url.endsWith(k));
    let out = key ? await routes[key](body, url) : null;
    if (!out) {
      if (url.endsWith('/end')) out = { status: 200, body: { ok: true, ended: true } };
      else if (url.endsWith('/transcript')) out = { status: 200, body: { ok: true, kept: body.events.length } };
      else if (url.endsWith('/consult')) out = { status: 200, body: { ok: true, call_id: body.call_id, answered: true, receipt: 'mc-1', spoken: 'Item 12 is in build.' } };
      else out = { status: 404, body: {} };
    }
    if (out === 'network') throw new Error('offline');
    return { ok: out.status >= 200 && out.status < 300, status: out.status, json: async () => out.body };
  };
  let controller = null;
  class Controller {
    constructor(options) {
      this.options = options; this._state = 'idle'; this._sessionId = null; this.instructions = []; this.ended = []; this.muted = 0;
      this.drain = null;                                     // set by a case: the promise afterPlayback returns
      this._adapter = { sendContinuationInstruction: (t) => this.instructions.push(t) };   // the 93bf00c6 page reads these
      controller = this;
    }
    sessionId() { return this._sessionId; }                 // the public surface (Spec 165 §4.3)
    speak(t) { this.instructions.push(t); return true; }
    afterPlayback() { return this.drain || Promise.resolve(); }
    mute() { this.muted += 1; }
    async startSession(args) {
      this.args = args; this._state = 'connecting'; this._sessionId = SID;
      this.options.onSessionInfo({ provider: args.provider, voice_session_id: SID, voice_notices: [] });
      this._state = 'active'; this.options.onStateChange('active');
    }
    endSession(reason) {
      if (['ending', 'ended'].includes(this._state)) return;
      this.ended.push(reason); this._state = 'ended'; this.options.onStateChange('ended');
      this.options.onFinalize({ turns: this.turns || [], partial_reason: reason, session_id: this._sessionId });
    }
    hear(turns) { this.turns = turns.map((t) => ({ ...t })); this.options.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); }
  }
  const ui = { say: (text, o = {}) => said.push([text, o]), phase() {}, live() {}, elapsed() {}, notice() {}, propose() {},
    ended: (words, o) => ended.push([words, o]), checking() {} };
  const page = { conversation: { id: 'c-1' } };
  const s = D.directVoice({ page, direct: DIRECT, ui, headers: () => ({ 'Content-Type': 'application/json' }), fetch,
    reveal: async () => { reveals.push(true); }, loadController: async () => ({ RealtimeVoiceController: Controller }), clock: () => now,
    setTimeout: (fn, ms) => later(fn, ms), clearTimeout: (id) => timers.delete(id),
    setInterval: (fn, ms) => later(fn, ms, true), clearInterval: (id) => timers.delete(id) });
  return { s, calls, said, ended, reveals, timers, advance, get c() { return controller; },
    posts: (end) => calls.filter((x) => x.url.endsWith(end)) };
}
const user = (text, completed = true) => ({ speaker: 'user', text, completed });
const failures = [];
let cases = 0;
async function check(name, fn) {
  cases++;
  try { await fn(); } catch (e) { failures.push(`${name}: ${e.message.split('\n')[0]}`); }
}

// ── B1: one owner turn, one effect, whoever dispatches it ───────────────────────────────────────────────────────────────
await check('B1 manual Check then the model on the same words name the same owner turn', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.options.onUserTurn('Save a note: the kiln needs a new seal');
  await r.s.check();
  await r.c.options.onFunctionCall({ call_id: 'provider-repeat', name: 'save_note', arguments: {} });
  const turns = r.posts('/consult').map((p) => p.body.owner_turn);
  assert.equal(turns.length, 2); assert.ok(turns[0]); assert.equal(turns[0], turns[1]);
});
await check('B1 the same words said again are a new owner turn', async () => {
  const r = rig(); await r.s.start('openai');
  r.c.options.onUserTurn('What is stuck on #12?');
  await r.c.options.onFunctionCall({ call_id: 'a', name: 'consult', arguments: {} });
  r.c.options.onUserTurn('What is stuck on #12?');
  await r.c.options.onFunctionCall({ call_id: 'b', name: 'consult', arguments: {} });
  const [first, second] = r.posts('/consult').map((p) => p.body.owner_turn);
  assert.ok(first && second); assert.notEqual(first, second);
});
await check('B1 a second dispatch while the first runs is told it is still being checked, not that it failed', async () => {
  const r = rig({ routes: { '/consult': () => ({ status: 409, body: { ok: false, error: 'in flight', spoken: 'Still checking.' } }) } });
  await r.s.start('openai');
  r.c.options.onUserTurn('Run the nightly job.');
  const out = await r.c.options.onFunctionCall({ call_id: 'provider-2', name: 'consult', arguments: {} });
  assert.equal(out.ok, true); assert.equal(out.outcome, 'pending'); assert.doesNotMatch(out.spoken, /did not go through/);
});

// ── B7: a lost answer is not a failed action ────────────────────────────────────────────────────────────────────────────
await check('B7 an answer lost on the way back is asked again as the same call and its real result spoken', async () => {
  let n = 0;
  const r = rig({ routes: { '/consult': (body) => (++n === 1 ? 'network' : { status: 200, body: { ok: true, call_id: body.call_id, answered: true, saved: true, receipt: 'op-1', spoken: 'Saved.' } }) } });
  await r.s.start('openai');
  r.c.options.onUserTurn('Save a note: the kiln is hot');
  const out = await r.c.options.onFunctionCall({ call_id: 'call_1', name: 'save_note', arguments: {} });
  assert.equal(out.saved, true); assert.equal(out.spoken, 'Saved.');
  assert.deepEqual(r.posts('/consult').map((p) => p.body.call_id), ['call_1', 'call_1']);
});
await check('B7 a retry the server no longer knows (a restart) leaves the first attempt unknown, not failed', async () => {
  let n = 0;
  const r = rig({ routes: { '/consult': () => (++n === 1 ? 'network' : { status: 409, body: { ok: false, error: 'session ended' } }) } });
  await r.s.start('openai');
  r.c.options.onUserTurn('Run the nightly job.');
  const out = await r.c.options.onFunctionCall({ call_id: 'call_1', name: 'consult', arguments: {} });
  assert.equal(out.outcome, 'unknown'); assert.equal(out.spoken, UNKNOWN);
});
await check('B7 an answer that never comes back is unknown, never "did not go through"', async () => {
  const r = rig({ routes: { '/consult': () => 'network' } });
  await r.s.start('openai');
  r.c.options.onUserTurn('Run the nightly job.');
  const out = await r.c.options.onFunctionCall({ call_id: 'call_1', name: 'consult', arguments: {} });
  assert.equal(out.outcome, 'unknown'); assert.equal(out.spoken, UNKNOWN); assert.doesNotMatch(out.spoken, /did not go through/);
});

if (failures.length) {
  console.log(`${failures.length} of ${cases} review regressions FAILED:\n  ${failures.join('\n  ')}`);
  process.exit(1);
}
console.log(`${cases} Chunk B review regressions passed (B1–B7, capture edge); fake controller, fetch and timers only.`);
