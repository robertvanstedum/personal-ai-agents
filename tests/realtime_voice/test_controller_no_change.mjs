// The shared controller and adapters after Spec 165 A1 and its fix round, for German and Portuguese: byte-for-byte the files
// they ran before, plus additive parts they never use: the controller's public methods (sessionId(), state(), speak(),
// speakTracked(), inputOf()), its optional onPlaybackDrained callback and the adapter events it listens to (response_started,
// playback_drained, input_item: nothing reaches a page that passes no callback), its response attribution (review CX A1-C2:
// which owner input each response answers), the adapters' response tracking that emits them (Spec 165 §5.1, §5.3), the
// response id on each function call, and new adapter cache keys. Pinned two ways: each source with exactly those additions taken out hashes to
// its 93bf00c6 file (the Chunk B baseline; what production German, Portuguese and CoS receive at merge from origin/main is
// listed in A1_FIX_RESULT.md); and the methods, driven on a synthetic transport, send nothing unless a host calls them, and
// nothing on the managed path. No provider, microphone or network.
import fs from 'node:fs';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';

const PATH = new URL('../../core/realtime_voice/static/realtime-voice-controller.js', import.meta.url);
const BEFORE = '682906489b0b3f9f02cd3df951da6fbaaef74c2aa49927d5905827ea3b8c7f05';     // 93bf00c6, as German loads it today
const CONTROLLER = [
  [`// Both adapters changed for the playback events and response attribution: new keys so browsers fetch them (Spec 165 §4.3, §5.3).
import { OpenAIWebRTCAdapter } from "./adapters/openai-webrtc-adapter.js?v=20261028-attrib1";
import { XAIWebSocketAdapter } from "./adapters/xai-websocket-adapter.js?v=20261028-attrib1";`,
   `import { OpenAIWebRTCAdapter } from "./adapters/openai-webrtc-adapter.js?v=20261015-managed-end1";
import { XAIWebSocketAdapter } from "./adapters/xai-websocket-adapter.js?v=20260725-transcript1";`],
  [`    onSessionInfo, onPlaybackDrained = null,
`,
   `    onSessionInfo,
`],
  [`    this._onPlaybackDrained = onPlaybackDrained;     // optional (Spec 165 §5.3): a host that waits for a response's audio
    this._responseWaiters = [];
    this._inputs = [];               // owner input items (provider ids), in the order they became known (Spec 165 §5.1)
    this._inputOpen = false;         // owner speech has ended and its input item is not known yet
    this._answers = new Map();       // response id -> the owner input it answers: a provider id, null, or {after: id}
`,
   ``],
  [`    on("response_started", (evt) => { for (const resolve of this._responseWaiters.splice(0)) resolve(evt.response_id || null); });
    on("playback_drained", (evt) => { if (this._onPlaybackDrained) this._onPlaybackDrained(evt.response_id || null); });
`,
   ``],
  [`  /** The session's id (the server's voice_session_id when it issued one), or null. For a host page (Spec 165 §4.3);
   *  German and Portuguese do not call it. */
  sessionId() { return this._sessionId; }

  /** The session's state (idle, requesting_mic, connecting, active, reconnecting, ending, ended). For a host page (Spec 165
   *  §4.3), so it never reads a private field; German and Portuguese do not call it. */
  state() { return this._state; }

  /** A host's one-off instruction to the voice on the direct path (a holding line, "say this briefly"): true when the
   *  session's transport took it. Never on the managed path. German and Portuguese do not call it. */
  speak(instruction) {
    if (this._managedBootstrap || !this._adapter) return false;
    try { this._adapter.sendContinuationInstruction(instruction); return true; } catch (_) { return false; }
  }

  /** speak(), tracked (Spec 165 §5.3): resolves with the id of the response the instruction starts (the adapter's next
   *  response_started), or null when none is reported within maxMs or the transport refused it. A host passes
   *  onPlaybackDrained to learn when that response's audio has played out. German and Portuguese do not call it. */
  speakTracked(instruction, maxMs = 5000) {
    return new Promise((resolve) => {
      let timer = null;
      const done = (id) => { clearTimeout(timer); resolve(id); };
      this._responseWaiters.push(done);
      timer = setTimeout(() => { const i = this._responseWaiters.indexOf(done); if (i >= 0) this._responseWaiters.splice(i, 1); resolve(null); }, maxMs);
      if (!this.speak(instruction)) { const i = this._responseWaiters.indexOf(done); if (i >= 0) this._responseWaiters.splice(i, 1); done(null); }
    });
  }

`,
   ``],
  [`    // Response attribution (Spec 165 §5.1), first, so a host asking inputOf() from any callback below sees it current.
    on("speech_stopped", () => { this._inputOpen = true; });
    on("input_item", (evt) => this._sawInput(evt.item_id));
    on("input_transcript", (evt) => this._sawInput(evt.item_id));
    on("response_started", (evt) => { if (evt.response_id) this._answers.set(evt.response_id, this._inputOpen ? { after: this._inputs.at(-1) ?? null } : (this._inputs.at(-1) ?? null)); });
`,
   ``],
  [`  /** The owner input item a function call answers (Spec 165 §5.1; review CX A1-C2): each response is bound when it starts to
   *  the owner input current then (or, while his speech has ended without its item known yet, to the next one to appear),
   *  and the call to its response by response_id. {state: "bound", index, text} once that item's transcript has completed
   *  (index: its place in the transcript list the host is given), {state: "pending"} until then, or null when the call is
   *  bound to no owner input. Never the latest input by default. German and Portuguese do not call it. */
  inputOf(call) {
    const owner = call && call.response_id ? this._answers.get(call.response_id) : undefined;
    if (owner === undefined || owner === null) return null;
    if (typeof owner === "object") return { state: "pending" };
    const order = [...this._items.entries()].sort((a, b) => a[1].firstSeq - b[1].firstSeq);
    const index = order.findIndex(([id]) => id === owner);
    if (index < 0) return { state: "pending" };
    const item = order[index][1];
    return item.completed ? { state: "bound", index, text: item.text || "" } : { state: "pending" };
  }

  _sawInput(id) {
    if (!id || this._inputs.includes(id)) return;
    const before = this._inputs.at(-1) ?? null;
    this._inputs.push(id);
    this._inputOpen = false;
    for (const [rid, owner] of this._answers) if (owner && typeof owner === "object" && owner.after === before) this._answers.set(rid, id);
  }

`,
   ``]
];
let n = 0;

const source = fs.readFileSync(PATH, 'utf8');
{
  let before = source;
  for (const [now, then] of CONTROLLER) { assert.equal(before.split(now).length, 2, now.slice(0, 60)); before = before.replace(now, then); }
  assert.equal(crypto.createHash('sha256').update(before).digest('hex'), BEFORE);   // nothing else changed
  n++;
}
// The adapters: exactly the additions below, nothing else (each taken out, the file is its 93bf00c6 bytes).
const ADAPTERS = {
  'adapters/xai-websocket-adapter.js': ['afe83aa8aae73e4b47d6eaa920f8893932cf4bf04ee225e0918db06d5db3afbf', [
  [`    this._responses = new Map();     // response id -> {sources, done}: which response each chunk of audio belongs to
`,
   ``],
  [`  // Spec 165 §5.3: playback_drained {response_id} once a response is done and every chunk of its audio has played out (or
  // was stopped at barge-in); a response with no audio drains at its end. Nothing listens unless a host passes
  // onPlaybackDrained to the controller; German and Portuguese do not.
  _response(id) {
    const key = id || this._responseId || null;
    if (!this._responses.has(key)) this._responses.set(key, { sources: 0, done: false });
    return [key, this._responses.get(key)];
  }

  _drainedIf(key) {
    const r = this._responses.get(key);
    if (r && r.done && r.sources <= 0) { this._responses.delete(key); this._emit("playback_drained", { response_id: key }); }
  }

`,
   ``],
  [`      case "response.created":
        this._responseId = event.response?.id || null;
        this._emit("response_started", { response_id: this._responseId });
        break;
`,
   ``],
  [`this._playAudioChunk(event.delta || event.audio, event.response_id);`,
   `this._playAudioChunk(event.delta || event.audio);`],
  [`        {
          const [key, r] = this._response(event.response?.id);
          r.done = true;
          this._drainedIf(key);
        }
`,
   ``],
  [`  _playAudioChunk(base64Audio, responseId) {`,
   `  _playAudioChunk(base64Audio) {`],
  [`    const [key, owner] = this._response(responseId);
    owner.sources += 1;
    source.addEventListener("ended", () => {
      this._playbackSources.delete(source);
      if (owner.sources > 0) { owner.sources -= 1; this._drainedIf(key); }
    }, { once: true });`,
   `    source.addEventListener("ended", () => this._playbackSources.delete(source), { once: true });`],
  [`    const cut = [...this._responses].filter(([, r]) => r.sources > 0).map(([key]) => key);
`,
   ``],
  [`    for (const key of cut) {                                // stopped at barge-in: what was queued is not going to play
      const r = this._responses.get(key);
      if (r) { r.sources = 0; r.done = true; this._drainedIf(key); }
    }
`,
   ``],
  [`      // Spec 165 §5.1: the owner input item a response answers (if xAI sends this OpenAI-shaped event; not verified).
      case "input_audio_buffer.committed":
        this._emit("input_item", { item_id: event.item_id || null });
        break;
`,
   ``],
  [`              response_id: event.response?.id || this._responseId || null,
`,
   ``]],
  ],
  'adapters/openai-webrtc-adapter.js': ['dae118fd252add9a80755a363d64ce6b4ef377b004d177e8dc10bba5a3c274a7', [
  [`      // Spec 165 §5.1: the owner input item a response answers is the one committed before it starts.
      case "input_audio_buffer.committed":
        this._emit("input_item", { item_id: event.item_id || null });
        break;
`,
   ``],
  [`    this._audible = new Set();       // responses whose audio the provider says is playing (output_audio_buffer.started)
`,
   ``],
  [`      // Spec 165 §5.3: response_started on response.created; playback_drained {response_id} when the provider says that
      // response's audio buffer stopped (played out) or was cleared (barge-in), or at response.done when it had no audio.
      // That output_audio_buffer.stopped means audible playback has drained in the browser is not verified against the
      // provider. Nothing listens unless a host passes onPlaybackDrained; German and Portuguese do not.
      case "response.created":
        this._emit("response_started", { response_id: event.response?.id || null });
        break;
      case "output_audio_buffer.started":
        this._audible.add(event.response_id || null);
        break;
      case "output_audio_buffer.stopped":
      case "output_audio_buffer.cleared":
        for (const id of event.response_id ? [event.response_id] : [...this._audible]) {
          this._audible.delete(id);
          this._emit("playback_drained", { response_id: id });
        }
        break;
`,
   ``],
  [`        if (!this._audible.has(event.response?.id || null) && !_hasAudio(event.response)) {
          this._emit("playback_drained", { response_id: event.response?.id || null });
        }
`,
   ``],
  [`
// Whether a finished response carried audio (its output has an audio content part): its drain is then the provider's
// output_audio_buffer.stopped, not its end.
function _hasAudio(response) {
  return (response?.output || []).some((item) => (item?.content || []).some((part) => ["audio", "output_audio"].includes(part?.type)));
}
`,
   ``],
  [`              response_id: event.response?.id || null,
`,
   ``]],
  ],
};
for (const [name, [hash, edits]] of Object.entries(ADAPTERS)) {
  let text = fs.readFileSync(new URL(name, PATH), 'utf8');
  for (const [now, then] of edits) { assert.equal(text.split(now).length, 2, `${name}: ${now.slice(0, 60)}`); text = text.replace(now, then); }
  assert.equal(crypto.createHash('sha256').update(text).digest('hex'), hash, name);
  n++;
}

// The methods on a synthetic transport: the German start sends what it sent; speak() reaches the transport only when called.
class Adapter {
  constructor() { this.listeners = {}; this.sent = []; }
  on(name, fn) { (this.listeners[name] ||= []).push(fn); }
  emit(name, e) { for (const fn of this.listeners[name] || []) fn(e); }
  prepareSession() {} async connect() {} start() { this.emit('connected', {}); } end() {} stopCapture() {}
  sendContinuationInstruction(text) { this.sent.push(text); }
}
const adapters = [];
globalThis.OpenAITestAdapter = class extends Adapter { constructor() { super(); adapters.push(this); } };
globalThis.XAITestAdapter = class extends Adapter { constructor() { super(); adapters.push(this); } };
const shim = 'const OpenAIWebRTCAdapter=globalThis.OpenAITestAdapter,XAIWebSocketAdapter=globalThis.XAITestAdapter;\n';
const { RealtimeVoiceController: Controller } = await import('data:text/javascript;base64,' +
  Buffer.from(shim + source.replace(/^import .*;$/gm, '')).toString('base64'));
const saved = { fetch: globalThis.fetch, setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval };
try {
  globalThis.setInterval = () => 1; globalThis.clearInterval = () => {};
  const sent = [];
  globalThis.fetch = async (url, options) => { sent.push([url, options]);
    return { ok: true, json: async () => ({ provider: 'openai', model: 'm', warning_minutes: 20, max_minutes: 30 }) }; };
  // German: no new option, no new call. The bootstrap request and the opening instruction are as before.
  const german = new Controller({ bootstrapUrl: '/api/realtime-voice/bootstrap' });
  assert.equal(german.sessionId(), null);
  assert.equal(german.state(), 'idle');                                                // read-only: nothing is sent
  assert.equal(german.speak('nothing yet'), false);                                     // no session: nothing is sent
  await german.startSession({ provider: 'openai', persona: 'frau_berger', scene: 'bakery', learner_name: 'Robert' });
  assert.deepEqual(sent[0][1].headers, { 'Content-Type': 'application/json' });
  assert.equal(sent[0][1].body, JSON.stringify({ provider: 'openai', persona: 'frau_berger', scene: 'bakery', learner_name: 'Robert' }));
  const transport = adapters.at(-1);
  assert.equal(transport.sent.length, 1);                                                // the opening instruction, as before
  assert.match(transport.sent[0], /^Begin in character with one short, natural greeting/);
  assert.match(german.sessionId(), /^sess-/);                                            // German's own local id, as before
  n++;
  // A direct host: sessionId() is the server's id; speak() reaches the transport once per call.
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ provider: 'xai', model: 'm', warning_minutes: 20, max_minutes: 30,
    voice_session_id: '0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b' }) });
  const host = new Controller({ bootstrapUrl: '/b', openingInstruction: null });
  await host.startSession({ provider: 'xai', persona: 'master_craftsman', scene: '', conversation_id: 'c-1' });
  assert.equal(host.sessionId(), '0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b');
  assert.deepEqual(adapters.at(-1).sent, []);                                            // no opening instruction for a host
  assert.equal(host.speak('Still checking.'), true);
  assert.deepEqual(adapters.at(-1).sent, ['Still checking.']);
  adapters.at(-1).sendContinuationInstruction = () => { throw new Error('closed'); };
  assert.equal(host.speak('again'), false);                                              // a transport that refuses: false, no throw
  n++;
  // speakTracked(): the id of the response the instruction starts; a page that passes no onPlaybackDrained hears no drain.
  adapters.at(-1).sendContinuationInstruction = (t) => adapters.at(-1).sent.push(t);
  const tracked = host.speakTracked('Say only this.');
  adapters.at(-1).emit('response_started', { response_id: 'resp_9' });
  assert.equal(await tracked, 'resp_9');
  adapters.at(-1).emit('playback_drained', { response_id: 'resp_9' });                    // no callback: nothing happens
  const germanXai = new Controller({ bootstrapUrl: '/api/realtime-voice/bootstrap' });
  await germanXai.startSession({ provider: 'xai', persona: 'frau_berger', scene: 'bakery', learner_name: 'Robert' });
  adapters.at(-1).emit('playback_drained', { response_id: 'r1' });
  assert.equal(germanXai.state(), 'active');
  n++;
  // The managed path never takes a page instruction.
  const managed = new Controller({ bootstrapUrl: '/b', managedBootstrap: { sessionUrl: '/m' } });
  managed._adapter = new Adapter();
  assert.equal(managed.speak('x'), false);
  assert.deepEqual(managed._adapter.sent, []);
  n++;
} finally { Object.assign(globalThis, saved); }
console.log(`${n} shared-controller no-change scenarios passed (German source pinned, additive methods); no provider or microphone.`);
