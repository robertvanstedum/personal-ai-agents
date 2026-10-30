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
  utterance_url: `${BASE}/session/{session_id}/utterance`, asked_url: `${BASE}/session/{session_id}/utterance/{action_id}/asked`,
  heartbeat_seconds: 20, persona: 'master_craftsman', locale: 'en-US', card_release: '0.1.18',
  person: { name: 'Master Craftsman', short: 'MC' } };

export const user = (text, completed = true) => ({ speaker: 'user', text, completed });
export const assistant = (text, completed = true) => ({ speaker: 'assistant', text, completed });

// routes: {'/suffix': (body, url, opts) => {status, body} | 'network' | null (the default answer) | a promise of one}.
export function rig({ routes = {}, direct = DIRECT } = {}) {
  const calls = [], said = [], ended = [], reveals = [], lifecycle = [], checks = [], notices = [];
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
      else if (path.endsWith('/asked')) out = { status: 200, body: { ok: true, state: 'asked', expires_in: 30 } };
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
      // Response construction, as the real controller does it where the page creates every response (Spec 165 §5.1 as
      // amended for Robert's C2 decision, 28 Oct 2026): each response that may act was created by the page for one committed
      // owner item, named by the provider's commit; a function result's follow-up is owned by the item of the call it
      // continues; any other response answers no one. Here an owner item is committed when its words appear (utter, hear),
      // or before them when he stops speaking (speechStopped); the words of an item committed before they were transcribed
      // land on it (in the real controller by the provider's item id; here, the earliest committed item still without words).
      this.commits = []; this.answered = 0; this.answers = new Map(); this.calls = 0;
      controller = this;
    }
    saw(i) {
      if (this.commits.some((c) => c.index === i)) return;
      const waiting = this.commits.find((c) => c.index === null);
      if (waiting) waiting.index = i; else this.commits.push({ index: i });
    }
    speechStopped() {                                      // he spoke and his item was committed; its words are not known yet
      this.commits.push({ index: null });
      this.options.onInputState('speech_started'); this.options.onInputState('speech_stopped');
    }
    respond() {                                            // the page's response for his newest committed item starts now
      const id = `call_resp_${++this.calls}`;
      const owner = this.commits.length > this.answered ? this.commits[this.commits.length - 1] : null;
      this.answered = this.commits.length;
      this.answers.set(id, owner);
      return id;
    }
    follow(call) {                                         // a function result's follow-up starts: owned as the call it continues
      const id = `call_resp_${++this.calls}`;
      this.answers.set(id, call && this.answers.has(call.response_id) ? this.answers.get(call.response_id) : null);
      return id;
    }
    callFrom(responseId, call) { return this.options.onFunctionCall({ ...call, response_id: responseId }); }
    fn(call) { return this.callFrom(this.respond(), call); }             // a response that starts now and calls a function
    inputOf(call) {
      const owner = call && call.response_id ? this.answers.get(call.response_id) : undefined;
      if (!owner) return null;
      if (owner.index === null || !this.turns[owner.index]) return { state: 'pending' };
      const turn = this.turns[owner.index];
      return turn.completed ? { state: 'bound', index: owner.index, text: turn.text } : { state: 'pending' };
    }
    sessionId() { return this._sid; }
    state() { return this._state; }
    speak(t) { this.instructions.push(t); return true; }
    // The tracked form (Spec 165 §5.3): the instruction, and the id of the response it starts.
    // The v3 fix round (review M2): its first audio is reported a moment later, as both adapters now report it.
    speakTracked(t) {
      this.instructions.push(t); const id = `resp_${++this.responses}`; this.tracked.push(id);
      setImmediate(() => this.options.onAudioStarted?.(id));
      return Promise.resolve(id);
    }
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
      this.committed = this.committed || new Set();             // v3: each of his items, once completed, is committed once
      this.turns.forEach((t, i) => { if (t.speaker === 'user' && t.completed && !this.committed.has(i)) { this.committed.add(i); this.commit(i); } });
    }
    utter(text) {                                          // a completed owner turn: the transcript update, then the turn
      this.turns = [...this.turns, user(text)];
      this.saw(this.turns.length - 1);
      this.options.onTranscriptUpdate(this.turns.map((t) => ({ ...t }))); this.options.onUserTurn(text);
      this.committed = this.committed || new Set();
      this.committed.add(this.turns.length - 1);
      this.commit(this.turns.length - 1);
    }
    // v3, as the real controller does where the page makes every response (OpenAI): his committed item's one response waits
    // for the page's word on it (respondFor, its server decision), then starts (onResponseStarted, owned by the item), with
    // the page's instruction if it gave one; {skip} makes none. The provider's item id here is "i" + its place.
    ownsResponses() { return this.owns !== false; }
    placeOf(id) { return typeof id === 'string' && /^i\d+$/.test(id) ? Number(id.slice(1)) : null; }
    commit(i) {
      if (typeof this.options.respondFor !== 'function') return;
      Promise.resolve(this.options.respondFor(`i${i}`)).then((word) => {
        if (word && word.skip) return;
        if (word && word.instruction) this.instructions.push(word.instruction);
        const id = `turn_resp_${i}`;
        this.options.onResponseStarted?.({ response_id: id, owner: `i${i}`, page: true, kind: 'turn' });
        this.options.onAudioStarted?.(id);                // its first audio, as the transport reports it (review M2)
      });
    }
    drained(id, outcome = 'drained') { this.options.onPlaybackDrained?.(id, outcome); }   // how it ended (v0.3 G3)
  }
  const ui = { say: (text, o = {}) => said.push([text, o]), phase() {}, live() {}, elapsed() {}, notice: (text) => notices.push(text),
    propose() {}, ended: (words, o) => ended.push([words, o]), checking: (...args) => checks.push(args),
    lifecycle: (s) => lifecycle.push(s) };
  const page = { conversation: { id: 'c-1' } };
  const s = D.directVoice({ page, direct, ui, headers: () => ({ 'Content-Type': 'application/json' }), fetch,
    reveal: async () => { reveals.push(true); }, loadController: async () => ({ RealtimeVoiceController: Controller }), clock: () => now,
    setTimeout: (fn, ms) => later(fn, ms), clearTimeout: (id) => timers.delete(id),
    setInterval: (fn, ms) => later(fn, ms, true), clearInterval: (id) => timers.delete(id) });
  // v3: the summary is shown and the voice says only "Your summary is ready" (the first tracked line); readIt() lets that
  // line play out and answers the one reply window with "read it", so the summary itself is read (the next tracked line).
  const readIt = async (words = 'Read it.') => {
    controller.drained(controller.tracked[0]);
    for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r));
    controller.options.onUserTurn(words);
    for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r));
  };
  return { s, calls, said, ended, reveals, lifecycle, checks, notices, timers, advance, readIt, get c() { return controller; }, now: () => now,
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
