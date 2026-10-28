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
      // Response attribution, as the real controller does it (Spec 165 §5.1, V R2-M1): owner inputs in the order they became
      // known, his utterance that ended and awaits the provider's response, and each response's owner input. A response the
      // provider makes (respond) answers his ended utterance; a function result's follow-up (follow) answers what the call
      // it continues answered. Here a new owner input with no ended utterance pending stands for an utterance whose speech
      // events the test does not spell out.
      // A response bound before its utterance's item is known holds that utterance (review F1): the next input settles it;
      // new speech first voids it.
      this.inputs = []; this.heard = null; this.answers = new Map(); this.calls = 0; this.holds = [];
      controller = this;
    }
    saw(i) {
      if (this.inputs.includes(i)) return;
      if (this.holds.length) { for (const hold of this.holds.splice(0)) hold.item = i; }       // a late item: no new utterance
      else if (this.heard === null) this.heard = { index: this.inputs.length };
      this.inputs.push(i);
    }
    speechStopped() {                                      // he spoke (an utterance began and ended), its words not known yet
      for (const hold of this.holds.splice(0)) hold.item = null;                            // new speech voids a hold
      if (this.heard === null) this.heard = { index: this.inputs.length };
      this.options.onInputState('speech_started'); this.options.onInputState('speech_stopped');
    }
    respond() {                                            // the provider's response starts now, bound as the controller binds it
      const id = `call_resp_${++this.calls}`, ended = this.heard;
      this.heard = null;
      let owner = null;
      if (ended && this.inputs.length > ended.index) owner = this.inputs[this.inputs.length - 1];
      else if (ended) { owner = { item: undefined }; this.holds.push(owner); }
      this.answers.set(id, owner);
      return id;
    }
    follow(call) {                                         // a function result's follow-up starts: it answers what the call did
      const id = `call_resp_${++this.calls}`;
      this.answers.set(id, call && this.answers.has(call.response_id) ? this.answers.get(call.response_id) : null);
      return id;
    }
    callFrom(responseId, call) { return this.options.onFunctionCall({ ...call, response_id: responseId }); }
    fn(call) { return this.callFrom(this.respond(), call); }             // a response that starts now and calls a function
    inputOf(call) {
      let owner = call && call.response_id ? this.answers.get(call.response_id) : undefined;
      if (owner === undefined || owner === null) return null;
      if (typeof owner === 'object') owner = owner.item;
      if (owner === null) return null;
      if (owner === undefined || !this.turns[owner]) return { state: 'pending' };
      return this.turns[owner].completed ? { state: 'bound', index: owner, text: this.turns[owner].text } : { state: 'pending' };
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
    hear(turns) {
      this.turns = turns.map((t) => ({ ...t }));
      this.turns.forEach((t, i) => { if (t.speaker === 'user') this.saw(i); });
      this.options.onTranscriptUpdate(this.turns.map((t) => ({ ...t })));
    }
    utter(text) {                                          // a completed owner turn: the transcript update, then the turn
      this.turns = [...this.turns, user(text)];
      this.saw(this.turns.length - 1);
      this.options.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); this.options.onUserTurn(text);
    }
    drained(id, outcome = 'drained') { this.options.onPlaybackDrained?.(id, outcome); }   // how it ended (v0.3 G3)
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
