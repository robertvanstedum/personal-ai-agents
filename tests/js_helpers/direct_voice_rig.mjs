// A rig for the shared direct-voice session module (core/realtime_voice/static/direct-voice-session.js): a fake controller
// with only the public surface, a fake fetch with per-route answers, and fake timers. Used by the A1 fix round's
// regressions (tests/mc_voice_direct_a1_fix.mjs). No DOM, network, provider or microphone.
import { pathToFileURL } from 'node:url';

const here = (p) => new URL(p, import.meta.url);
export const D = await import(process.env.VOICE_SESSION_MODULE ? pathToFileURL(process.env.VOICE_SESSION_MODULE)
  : here('../../core/realtime_voice/static/direct-voice-session.js'));
export const flush = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise((r) => setImmediate(r)); };

export const SID = '7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f';
const BASE = '/guild-next/api/v1/mc/voice/direct/api/realtime-voice';
export const DIRECT = { path: 'direct', providers: [{ provider: 'openai', label: 'OpenAI' }], default_provider: 'openai',
  bootstrap_url: `${BASE}/bootstrap`, consult_url: `${BASE}/consult`, end_url: `${BASE}/session/{session_id}/end`,
  transcript_url: `${BASE}/session/{session_id}/transcript`, summary_url: `${BASE}/session/{session_id}/summary`,
  summary_decision_url: `${BASE}/session/{session_id}/summary/decision`,
  reconcile_url: `${BASE}/consult/{session_id}/{owner_item_id}`, keep_talking_url: `${BASE}/session/{session_id}/keep_talking`,
  heartbeat_seconds: 20, persona: 'master_craftsman', locale: 'en-US', card_release: '0.1.18',
  person: { name: 'Master Craftsman', short: 'MC' } };

export const user = (text, completed = true) => ({ speaker: 'user', text, completed });
export const assistant = (text, completed = true) => ({ speaker: 'assistant', text, completed });

// routes: {'/suffix': (body, url, opts) => {status, body} | 'network' | null (the default answer) | a promise of one}.
export function rig({ routes = {}, direct = DIRECT } = {}) {
  const calls = [], said = [], ended = [], reveals = [], lifecycle = [];
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
  const fetch = async (url, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url, opts, body, method: opts.method || 'GET' });
    const path = url.split('?')[0];
    const key = Object.keys(routes).sort((a, b) => b.length - a.length).find((k) => path.endsWith(k));
    let out = key ? await routes[key](body, url, opts) : null;
    if (!out) {
      if (path.endsWith('/end')) out = { status: 200, body: { ok: true, ended: true } };
      else if (path.endsWith('/transcript')) out = { status: 200, body: { ok: true, kept: body.events.length } };
      else if (path.endsWith('/consult')) out = { status: 200, body: { ok: true, outcome: 'done', call_id: body.call_id, answered: true, receipt: 'mc-1', spoken: 'Item 12 is in build.' } };
      else if (path.endsWith('/keep_talking')) out = { status: 200, body: { ok: true, proposal: 'withdrawn' } };
      else out = { status: 404, body: {} };
    }
    if (out === 'network') throw new Error('offline');
    return { ok: out.status >= 200 && out.status < 300, status: out.status, json: async () => out.body };
  };
  let controller = null;
  class Controller {
    constructor(options) {
      this.options = options; this._state = 'idle'; this._sid = null; this.instructions = []; this.ended = []; this.muted = 0;
      this.turns = []; this.responses = 0; this.tracked = [];
      controller = this;
    }
    sessionId() { return this._sid; }
    state() { return this._state; }
    speak(t) { this.instructions.push(t); return true; }
    // The tracked form (Spec 165 §5.3): the instruction, and the id of the response it starts.
    speakTracked(t) { this.instructions.push(t); const id = `resp_${++this.responses}`; this.tracked.push(id); return Promise.resolve(id); }
    mute() { this.muted += 1; }
    async startSession(args) {
      this.args = args; this._state = 'connecting'; this._sid = SID;
      this.options.onSessionInfo({ provider: args.provider, voice_session_id: SID, voice_notices: [] });
      this._state = 'active'; this.options.onStateChange('active');
    }
    endSession(reason) {
      if (['ending', 'ended'].includes(this._state)) return;
      this.ended.push(reason); this._state = 'ended'; this.options.onStateChange('ended');
      this.options.onFinalize({ turns: this.turns.map((t) => ({ ...t })), partial_reason: reason, session_id: this._sid });
    }
    hear(turns) { this.turns = turns.map((t) => ({ ...t })); this.options.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); }
    utter(text) {                                          // a completed owner turn: the transcript update, then the turn
      this.turns = [...this.turns, user(text)];
      this.options.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); this.options.onUserTurn(text);
    }
    drained(id) { this.options.onPlaybackDrained?.(id); }
  }
  const ui = { say: (text, o = {}) => said.push([text, o]), phase() {}, live() {}, elapsed() {}, notice() {}, propose() {},
    ended: (words, o) => ended.push([words, o]), checking() {}, lifecycle: (s) => lifecycle.push(s) };
  const page = { conversation: { id: 'c-1' } };
  const s = D.directVoice({ page, direct, ui, headers: () => ({ 'Content-Type': 'application/json' }), fetch,
    reveal: async () => { reveals.push(true); }, loadController: async () => ({ RealtimeVoiceController: Controller }), clock: () => now,
    setTimeout: (fn, ms) => later(fn, ms), clearTimeout: (id) => timers.delete(id),
    setInterval: (fn, ms) => later(fn, ms, true), clearInterval: (id) => timers.delete(id) });
  return { s, calls, said, ended, reveals, lifecycle, timers, advance, get c() { return controller; }, now: () => now,
    posts: (end) => calls.filter((x) => x.url.split('?')[0].endsWith(end) && x.method === 'POST'),
    gets: (part) => calls.filter((x) => x.method === 'GET' && x.url.includes(part)) };
}

// Every case runs; failures are listed, then the exit code is 1.
export function runner(title) {
  const failures = [];
  let cases = 0;
  return {
    async check(name, fn) { cases++; try { await fn(); } catch (e) { failures.push(`${name}: ${e.message.split('\n')[0]}`); } },
    done() {
      if (failures.length) {
        console.log(`${failures.length} of ${cases} ${title} FAILED:\n  ${failures.join('\n  ')}`);
        process.exit(1);
      }
      console.log(`${cases} ${title} passed; fake controller, fetch and timers only.`);
    },
  };
}
