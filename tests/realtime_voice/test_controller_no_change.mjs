// The shared controller and adapters after Spec 165 A1 and its fix round, for German and Portuguese: byte-for-byte the files
// they ran before, plus additive parts. Some of those parts run in every session (the response-attribution listeners and
// xAI's per-response accounting see every German transcript and response; review FR S2), so the behaviour half below
// drives German's transcript path through them, on a browser without Array.prototype.at too. The additions are: the
// controller's public methods (sessionId(), state(), speak(), speakTracked(), inputOf()), its optional onPlaybackDrained
// callback, its trackResponses and oncePerItem options (oncePerItem, review F2: a host hears each owner item's completion
// once; German passes none and hears every completion as before, pinned in test_response_binding.mjs), and the adapter
// events it listens to (response_started, playback_drained, input_item, speech_stopped: nothing reaches a page that passes
// no callback); its response attribution (review CX A1-C2,
// V R2-M1: which owner input each response answers), for which the opening instruction, continueNaturally() and every
// function result now pass through one recorder of the responses it asks for (the calls the adapter receives are the same,
// with the same arguments, unless a host sets trackResponses; checked below); the adapters' response tracking that emits
// those events (Spec 165 §5.1, §5.3; with how each response's playback ended, v0.3 G3), the response id on each function call, the OpenAI adapter's optional response tag
// (sent only when one is passed; German's bytes are unchanged, checked below), and new adapter cache keys. The strip
// pairs below are generated from the diff against 93bf00c6 and checked to give its bytes exactly. Pinned two ways: each source with exactly those additions taken out hashes to
// its 93bf00c6 file (the Chunk B baseline; what production German, Portuguese and CoS receive at merge from origin/main is
// listed in A1_FIX_RESULT.md); and the methods, driven on a synthetic transport, send nothing unless a host calls them, and
// nothing on the managed path. No provider, microphone or network.
import fs from 'node:fs';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';

const PATH = new URL('../../core/realtime_voice/static/realtime-voice-controller.js', import.meta.url);
const BEFORE = '682906489b0b3f9f02cd3df951da6fbaaef74c2aa49927d5905827ea3b8c7f05';     // 93bf00c6, as German loads it today
const CONTROLLER = [
  [`// Both adapters changed for the playback events and response attribution, OpenAI's again for the response tag on every
// request, and both again for how a response's playback ended: new keys so browsers fetch them (Spec 165 §4.3, §5.3;
// review FR N3, V R2-M1; v0.3 G3).
import { OpenAIWebRTCAdapter } from "./adapters/openai-webrtc-adapter.js?v=20261028-outcome1";
import { XAIWebSocketAdapter } from "./adapters/xai-websocket-adapter.js?v=20261028-outcome1";
`,
   `import { OpenAIWebRTCAdapter } from "./adapters/openai-webrtc-adapter.js?v=20261015-managed-end1";
import { XAIWebSocketAdapter } from "./adapters/xai-websocket-adapter.js?v=20260725-transcript1";
`],
  [`// Response attribution (Spec 165 §5.1; review V R2-M1): a response this controller asked for that has not started in this
// long is taken as never coming; his ended utterance with no input item in this long is taken as noise (V note B).
const REQUEST_TTL_MS = 10000;
const MARK_TTL_MS = 3000;
`,
   ``],
  [`    onSessionInfo, onPlaybackDrained = null, trackResponses = false, oncePerItem = false,
`,
   `    onSessionInfo,
`],
  [`    this._onPlaybackDrained = onPlaybackDrained;     // optional (Spec 165 §5.3): a host that waits for a response's audio
    this._trackResponses = trackResponses;           // optional: tag each response this controller asks for (V R2-M1)
    this._inputs = [];               // owner input items (provider ids), in the order they became known (Spec 165 §5.1)
    this._utterance = null;          // inputs known when his current utterance began (speech_started), until it ends
    this._ended = null;              // his utterance that ended and awaits its response: {index, seq, at, committed}
    this._commits = false;           // the adapter reports committed input (OpenAI's input_audio_buffer.committed)
    this._requests = [];             // responses this controller asked for, not yet started, in order: {owner, tag, seq, at, done}
    this._seq = 0;                   // the order in which ended utterances and requests became known
    this._echoed = false;            // the adapter has reported a request's tag back (OpenAI's response.metadata)
    this._answers = new Map();       // response id -> the owner input it answers: a provider id, null, or a hold (below)
    this._holds = [];                // holds not yet settled: a response bound to his utterance before its item was known,
                                     // {at: when his speech ended, item: undefined until known, then its id, or null (void)}
    this._tags = 0;
    // Optional (review F2): a host that hears each owner item's completion once (MC's page). A replayed provider event, or
    // a second completion of an item already completed, is not a new turn. German and Portuguese pass no option.
    this._fresh = true;              // the completion being handled is the first of its item
    if (oncePerItem && onUserTurn) this._onUserTurn = (text) => { if (this._fresh) onUserTurn(text); };
`,
   ``],
  [`    // Response attribution (Spec 165 §5.1), first, so a host asking inputOf() from any callback below sees it current.
    on("connected", () => { this._utterance = this._ended = null; this._drop(this._requests.slice()); this._echoed = this._commits = false; this._void(); });
    on("speech_started", () => { this._void(); if (this._utterance === null) this._utterance = this._inputs.length; });
    on("speech_stopped", () => this._heardEnd());
    on("input_item", (evt) => this._committed(evt.item_id));
    on("input_transcript", (evt) => { this._fresh = this._firstCompletion(evt); this._sawInput(evt.item_id); });
    on("response_started", (evt) => this._started(evt));
`,
   ``],
  [`        this._ask(null, (tag) => this._instruct(this._openingInstruction, tag));
`,
   `        this._adapter.sendContinuationInstruction(this._openingInstruction);
`],
  [`    // How it ended (v0.3 §5.3, G3): drained, barged_in, cancelled, cleared or no_audio, or null when the adapter did not say.
    on("playback_drained", (evt) => { if (this._onPlaybackDrained) this._onPlaybackDrained(evt.response_id || null, evt.outcome || null); });
`,
   ``],
  [`        this._sendResult(call, {
          error: "Function bridge unavailable",
`,
   `        this._adapter.sendFunctionResult(call.call_id, {
          error: "Function bridge unavailable",
`],
  [`        this._sendResult(call, output);
`,
   `        this._adapter.sendFunctionResult(call.call_id, output);
`],
  [`        this._sendResult(call, {
          error: error?.message || "COS function failed",
`,
   `        this._adapter.sendFunctionResult(call.call_id, {
          error: error?.message || "COS function failed",
`],
  [`    if (!this._managedBootstrap && this._adapter) this._ask(null, (tag) => this._instruct(CONTINUATION_INSTRUCTION, tag));
  }

  /** The session's id (the server's voice_session_id when it issued one), or null. For a host page (Spec 165 §4.3);
   *  German and Portuguese do not call it. */
  sessionId() { return this._sessionId; }

  /** The session's state (idle, requesting_mic, connecting, active, reconnecting, ending, ended). For a host page (Spec 165
   *  §4.3), so it never reads a private field; German and Portuguese do not call it. */
  state() { return this._state; }

  /** A host's one-off instruction to the voice on the direct path (a holding line, "say this briefly"): true when the
   *  session's transport took it. Its response answers none of his inputs. Never on the managed path. German and
   *  Portuguese do not call it. */
  speak(instruction) {
    if (this._managedBootstrap || !this._adapter) return false;
    try { this._ask(null, (tag) => this._instruct(instruction, tag)); return true; } catch (_) { return false; }
  }

  /** speak(), tracked (Spec 165 §5.3; review FR N3, V note D): resolves with the id of the response this instruction
   *  starts, or null when it has not started within maxMs, or the transport refused it. The response is known as every
   *  response this controller asks for is (see _started): by its tag when the adapter reports it back, else by the order
   *  of the requests. A host passes onPlaybackDrained to learn when that response's audio has played out. German and
   *  Portuguese do not call it. */
  speakTracked(instruction, maxMs = 5000) {
    if (this._managedBootstrap || !this._adapter) return Promise.resolve(null);
    return new Promise((resolve) => {
      let timer = null, entry = null;
      try { entry = this._ask(null, (tag) => this._instruct(instruction, tag), (id) => { clearTimeout(timer); resolve(id); }); }
      catch (_) { resolve(null); return; }
      timer = setTimeout(() => { entry.done = null; resolve(null); }, maxMs);   // it stays in order; only the wait ends
    });
  }

  /** The owner input item a function call answers (Spec 165 §5.1; review CX A1-C2, FR M2, V R2-M1). Each response is bound
   *  when it starts, by where it came from:
   *  - a response this controller asked for: a function result's follow-up answers what the call it continues answered
   *    (it inherits that call's owner item); the page's own instruction answers none of his inputs. Neither takes or uses
   *    up his utterance;
   *  - a response the provider made from his speech: it answers his utterance that ended (speech_stopped, or his input
   *    committed), bound to the newest input item that appeared since that utterance began and before any later one began,
   *    whether its transcript came before or after his speech ended, or committed or not; with none yet, it holds that
   *    utterance (never a place a later one fills) until the first item to appear, with no new speech in between and
   *    within MARK_TTL_MS of the end of his speech: new speech first, or the bound first, voids it (review F1). Where the
   *    adapter reports committed input (OpenAI), only a committed utterance has a response. An ended utterance with no
   *    input item for MARK_TTL_MS is noise and binds nothing;
   *  - anything else (a response with no ended utterance and no request of this controller's) answers none of his inputs.
   *  Which is which: by the tag a request carries when the adapter reports it back (OpenAI's response.metadata, once seen
   *  in the session: then an untagged response is the provider's); else by order, the requests starting their responses
   *  in the order they were sent. By order alone, a response that starts while one of the requests and his ended
   *  utterance both wait is ambiguous: it binds nothing (its calls are refused as not heard), and both are let go
   *  (review R3-S1). The call is bound to its response by response_id. {state: "bound", index, text} once that item's transcript has completed
   *  (index: its place in the transcript list the host is given), {state: "pending"} until then, or null when the call is
   *  bound to no owner input. Never the latest input by default. German and Portuguese do not call it. */
  inputOf(call) {
    let owner = call && call.response_id ? this._answers.get(call.response_id) : undefined;
    if (owner === undefined || owner === null) return null;
    if (typeof owner === "object") owner = this._settled(owner);            // a hold: his utterance's item once known
    if (owner === null) return null;
    if (owner === undefined) return { state: "pending" };
    const order = [...this._items.entries()].sort((a, b) => a[1].firstSeq - b[1].firstSeq);
    const index = order.findIndex(([id]) => id === owner);
    if (index < 0) return { state: "pending" };
    const item = order[index][1];
    return item.completed ? { state: "bound", index, text: item.text || "" } : { state: "pending" };
  }

  // Index access, not Array.prototype.at: these listeners run in every German, Portuguese and CoS session, and must work
  // on every browser the controller ran on before (Safari before 15.4 has no .at; review FR S2).
  _lastInput() { return this._inputs.length ? this._inputs[this._inputs.length - 1] : null; }

  // Whether a transcript event completes its item for the first time (read before the event is recorded).
  _firstCompletion(evt) {
    if (!evt.completed || (evt.provider_event_id && this._seenEventIds.has(evt.provider_event_id))) return false;
    const item = this._items.get(evt.item_id);
    return !(item && item.completed);
  }

  _sawInput(id) {
    if (id && !this._inputs.includes(id)) { this._inputs.push(id); this._known(id); }
  }

  // A hold (review F1): a response that started after his utterance ended, before that utterance had an input item, holds
  // the utterance itself, never a place in _inputs that a later utterance fills. It settles on the first input item to
  // appear after it, with no new speech in between and within MARK_TTL_MS of the end of his speech (the bound of the noise
  // rule); new speech first, or the bound first, voids it (null): its calls, and the follow-ups of its calls, which share
  // it, answer none of his inputs.
  _hold(his) {
    const hold = { at: his.at, item: undefined };
    this._holds.push(hold);
    return hold;
  }

  _settled(hold) {
    return hold.item === undefined && Date.now() - hold.at >= MARK_TTL_MS ? null : hold.item;
  }

  _known(id) {
    for (const hold of this._holds.splice(0)) hold.item = this._settled(hold) === null ? null : id;
  }

  _void() {
    for (const hold of this._holds.splice(0)) hold.item = null;
  }

  // His utterance has ended (speech_stopped): its response is the provider's next one of its own. A second utterance
  // ending before that response starts keeps the first one's start, so the response answers the newest of them.
  _heardEnd() {
    if (this._utterance === null) return;
    this._ended = { index: this._ended ? this._ended.index : this._utterance, seq: ++this._seq, at: Date.now() };
    this._utterance = null;
  }

  // His input committed (OpenAI's input_audio_buffer.committed): his utterance has ended, whatever speech events came, and
  // the provider's response to it may start. Where the adapter reports commits, only a committed utterance has one.
  _committed(id) {
    this._commits = true;
    if (this._utterance !== null) this._heardEnd();
    // A commit with no speech events is a new utterance, unless a response already holds his utterance for its item (F1):
    // then it is that utterance's late commit, and settles the hold.
    if (this._ended === null && id && !this._inputs.includes(id) && !this._holds.length) this._ended = { index: this._inputs.length, seq: ++this._seq, at: Date.now() };
    if (this._ended) this._ended.committed = true;
    this._sawInput(id);
  }

  // A response this controller asks for (a function result's follow-up, the opening or continuation instruction, or the
  // page's own), in order, before it is sent; tagged when the host tracks responses (trackResponses). Every response.create
  // the controller sends goes through here. Nothing is recorded if the transport refuses it.
  _ask(owner, send, done = null) {
    const entry = { owner, tag: this._trackResponses ? \`minimoi-\${++this._tags}\` : null, seq: ++this._seq, at: Date.now(), done };
    this._requests.push(entry);
    try { send(entry.tag); } catch (error) { this._requests.splice(this._requests.indexOf(entry), 1); throw error; }
    return entry;
  }

  _instruct(text, tag) {
    if (tag) this._adapter.sendContinuationInstruction(text, tag); else this._adapter.sendContinuationInstruction(text);
  }

  // A function result: its follow-up answers what the call it continues answered (V R2-M1, R2-S1).
  _sendResult(call, output) {
    const owner = call && this._answers.has(call.response_id) ? this._answers.get(call.response_id) : null;
    this._ask(owner, (tag) => {
      if (tag) this._adapter.sendFunctionResult(call.call_id, output, tag); else this._adapter.sendFunctionResult(call.call_id, output);
    });
  }

  _drop(entries, id = null) {
    for (const entry of entries) {
      const i = this._requests.indexOf(entry);
      if (i >= 0) this._requests.splice(i, 1);
      if (entry.done) { const done = entry.done; entry.done = null; done(id); }
    }
  }

  _started(evt) {
    const now = Date.now(), id = evt.response_id || null;
    this._drop(this._requests.filter((r) => now - r.at >= REQUEST_TTL_MS));
    if (this._ended && this._inputs.length <= this._ended.index && now - this._ended.at >= MARK_TTL_MS) this._ended = null;
    const his = this._ended && (!this._commits || this._ended.committed) ? this._ended : null;
    let mine = null;
    if (evt.tag) {
      this._echoed = true;
      mine = this._requests.find((r) => r.tag === evt.tag) || { owner: null };   // a request of another page: not his
    } else if (!this._echoed && this._requests.length) {
      if (his) {                                    // by order alone, a request of ours and his utterance both wait:
        this._ended = null;                         // which one this is cannot be told, so it binds nothing and both are
        this._drop([this._requests[0]]);            // let go; its calls are refused as not heard (review R3-S1)
        if (id) this._answers.set(id, null);
        return;
      }
      mine = this._requests[0];
    }
    let owner = null;
    if (mine) {
      owner = mine.owner;
      this._drop([mine], id);
    } else if (his) {
      const known = this._utterance === null ? this._inputs.length : Math.min(this._utterance, this._inputs.length);
      this._ended = null;
      owner = known > his.index ? this._inputs[known - 1] : this._hold(his);
    }
    if (id) this._answers.set(id, owner);
`,
   `    if (!this._managedBootstrap) this._adapter?.sendContinuationInstruction(CONTINUATION_INSTRUCTION);
`],
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
  [`    this._responses = new Map();     // response id -> {sources, done, audio, cancelled}: which response each chunk of audio belongs to
`,
   ``],
  [`  // Spec 165 §5.3: playback_drained {response_id, outcome} once a response is done and every chunk of its audio has played
  // out (or was stopped at barge-in); a response with no audio drains at its end. The outcome names how it ended (v0.3, G3):
  // drained (its audio played to the end), barged_in (stopped at his speech), cancelled (the provider cancelled it), no_audio.
  // Nothing listens unless a host passes onPlaybackDrained to the controller; German and Portuguese do not.
  _response(id) {
    const key = id || this._responseId || null;
    if (!this._responses.has(key)) this._responses.set(key, { sources: 0, done: false, audio: false, cancelled: false });
    return [key, this._responses.get(key)];
  }

  _drainedIf(key, outcome = null) {
    const r = this._responses.get(key);
    if (r && r.done && r.sources <= 0) {
      this._responses.delete(key);
      this._emit("playback_drained", { response_id: key, outcome: outcome || (r.cancelled ? "cancelled" : r.audio ? "drained" : "no_audio") });
    }
  }

`,
   ``],
  [`      case "response.created":
        this._responseId = event.response?.id || null;
        this._emit("response_started", { response_id: this._responseId });
        break;
      // Spec 165 §5.1: the owner input item a response answers (if xAI sends this OpenAI-shaped event; not verified).
      case "input_audio_buffer.committed":
        this._emit("input_item", { item_id: event.item_id || null });
        break;
`,
   ``],
  [`        this._playAudioChunk(event.delta || event.audio, event.response_id);
`,
   `        this._playAudioChunk(event.delta || event.audio);
`],
  [`              response_id: event.response?.id || this._responseId || null,
`,
   ``],
  [`        }
        {
          const [key, r] = this._response(event.response?.id);
          r.done = true;
          r.cancelled = event.response?.status === "cancelled";
          this._drainedIf(key);
`,
   ``],
  [`  _playAudioChunk(base64Audio, responseId) {
`,
   `  _playAudioChunk(base64Audio) {
`],
  [`    const [key, owner] = this._response(responseId);
    owner.sources += 1;
    owner.audio = true;
    source.addEventListener("ended", () => {
      this._playbackSources.delete(source);
      if (owner.sources > 0) { owner.sources -= 1; this._drainedIf(key); }
    }, { once: true });
`,
   `    source.addEventListener("ended", () => this._playbackSources.delete(source), { once: true });
`],
  [`    const cut = [...this._responses].filter(([, r]) => r.sources > 0).map(([key]) => key);
`,
   ``],
  [`    for (const key of cut) {                                // stopped at barge-in: what was queued is not going to play
      const r = this._responses.get(key);
      if (r) { r.sources = 0; r.done = true; this._drainedIf(key, "barged_in"); }
    }
`,
   ``]],
  ],
  'adapters/openai-webrtc-adapter.js': ['dae118fd252add9a80755a363d64ce6b4ef377b004d177e8dc10bba5a3c274a7', [
  [`    this._audible = new Set();       // responses whose audio the provider says is playing (output_audio_buffer.started)
`,
   ``],
  [`  sendContinuationInstruction(text, tag) {
`,
   `  sendContinuationInstruction(text) {
`],
  [`    this._dc?.send(JSON.stringify(_responseCreate(tag)));
  }

  sendFunctionResult(callId, output, tag) {
`,
   `    this._dc?.send(JSON.stringify({ type: "response.create" }));
  }

  sendFunctionResult(callId, output) {
`],
  [`    this._dc?.send(JSON.stringify(_responseCreate(tag)));
  }

  _handleServerEvent(event) {
`,
   `    this._dc?.send(JSON.stringify({ type: "response.create" }));
  }

  _handleServerEvent(event) {
`],
  [`      // Spec 165 §5.3: response_started on response.created; playback_drained {response_id, outcome} when the provider says
      // that response's audio buffer stopped (outcome drained: played out) or was cleared (cleared: barge-in), or at
      // response.done when it had no audio (no_audio, or cancelled when the provider cancelled it) (v0.3, G3).
      // That output_audio_buffer.stopped means audible playback has drained in the browser is not verified against the
      // provider. Nothing listens unless a host passes onPlaybackDrained; German and Portuguese do not.
      case "response.created":
        this._emit("response_started", { response_id: event.response?.id || null, tag: event.response?.metadata?.minimoi_tag || null });
        break;
      // Spec 165 §5.1: the owner input item a response answers is the one committed before it starts.
      case "input_audio_buffer.committed":
        this._emit("input_item", { item_id: event.item_id || null });
        break;
      case "output_audio_buffer.started":
        this._audible.add(event.response_id || null);
        break;
      case "output_audio_buffer.stopped":
      case "output_audio_buffer.cleared":
        for (const id of event.response_id ? [event.response_id] : [...this._audible]) {
          this._audible.delete(id);
          this._emit("playback_drained", { response_id: id, outcome: event.type === "output_audio_buffer.cleared" ? "cleared" : "drained" });
        }
        break;
`,
   ``],
  [`              response_id: event.response?.id || null,
`,
   ``],
  [`        }
        if (!this._audible.has(event.response?.id || null) && !_hasAudio(event.response)) {
          this._emit("playback_drained", { response_id: event.response?.id || null, outcome: event.response?.status === "cancelled" ? "cancelled" : "no_audio" });
`,
   ``],
  [`
// A host that tracks its responses (Spec 165 §5.1, §5.3; review FR N3, V R2-M1) tags each one it asks for in the response's
// metadata, and response.created reports the tag back. Untagged, the bytes are as before.
function _responseCreate(tag) {
  return tag ? { type: "response.create", response: { metadata: { minimoi_tag: tag } } } : { type: "response.create" };
}

// Whether a finished response carried audio (its output has an audio content part): its drain is then the provider's
// output_audio_buffer.stopped, not its end.
function _hasAudio(response) {
  return (response?.output || []).some((item) => (item?.content || []).some((part) => ["audio", "output_audio"].includes(part?.type)));
}
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
  sendContinuationInstruction(text) { this.sent.push(text); (this.argc ||= []).push(arguments.length); }
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
  assert.deepEqual(transport.argc, [1]);                                                 // with one argument, as before
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
  // speakTracked(): the id of the response the instruction starts (after the one "Still checking." asked for: responses
  // start in the order they were asked for, V note D); a page that passes no onPlaybackDrained hears no drain.
  adapters.at(-1).sendContinuationInstruction = (t) => adapters.at(-1).sent.push(t);
  const tracked = host.speakTracked('Say only this.');
  adapters.at(-1).emit('response_started', { response_id: 'resp_8' });
  adapters.at(-1).emit('response_started', { response_id: 'resp_9' });
  assert.equal(await tracked, 'resp_9');
  adapters.at(-1).emit('playback_drained', { response_id: 'resp_9' });                    // no callback: nothing happens
  const germanXai = new Controller({ bootstrapUrl: '/api/realtime-voice/bootstrap' });
  await germanXai.startSession({ provider: 'xai', persona: 'frau_berger', scene: 'bakery', learner_name: 'Robert' });
  adapters.at(-1).emit('playback_drained', { response_id: 'r1' });
  assert.equal(germanXai.state(), 'active');
  n++;
  // German's transcript path runs through the new listeners: its callbacks see what they saw before, and nothing throws on
  // a browser without Array.prototype.at (Safari before 15.4; review FR S2).
  const at = Array.prototype.at;
  delete Array.prototype.at;
  try {
    const seen = [], heard = [];
    const lesson = new Controller({ bootstrapUrl: '/api/realtime-voice/bootstrap', onTranscriptUpdate: (t) => seen.push(t),
      onUserTurn: (text) => heard.push(text) });
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ provider: 'xai', model: 'm', warning_minutes: 20, max_minutes: 30 }) });
    await lesson.startSession({ provider: 'xai', persona: 'frau_berger', scene: 'bakery', learner_name: 'Robert' });
    const wire = adapters[adapters.length - 1];
    wire.emit('speech_started', {}); wire.emit('speech_stopped', {});
    wire.emit('input_transcript', { item_id: 'i1', text: 'Ein Brot', is_delta: false, completed: false, provider_event_id: 'e1' });
    wire.emit('input_transcript', { item_id: 'i1', text: 'Ein Brot, bitte', is_delta: false, completed: true, provider_event_id: 'e2' });
    wire.emit('response_started', { response_id: 'r1' });
    assert.deepEqual(heard, ['Ein Brot, bitte']);
    assert.deepEqual(seen[seen.length - 1], [{ speaker: 'user', text: 'Ein Brot, bitte', completed: true }]);
  } finally { Array.prototype.at = at; }
  n++;
  // A function result from a German session (no trackResponses) reaches the adapter with the same two arguments as before.
  {
    const german2 = new Controller({ bootstrapUrl: '/api/realtime-voice/bootstrap', onFunctionCall: async () => ({ ok: true }) });
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ provider: 'openai', model: 'm', warning_minutes: 20, max_minutes: 30 }) });
    await german2.startSession({ provider: 'openai', persona: 'frau_berger', scene: 'bakery', learner_name: 'Robert' });
    const wire2 = adapters[adapters.length - 1], args = [];
    wire2.sendFunctionResult = (...a) => { args.push(a); };
    wire2.emit('function_call', { call_id: 'c1', name: 'x', arguments: '{}', response_id: 'r1' });
    await new Promise((r) => setImmediate(r));
    assert.deepEqual(args, [['c1', { ok: true }]]);
    assert.deepEqual(wire2.argc, [1]);                                                   // the opening instruction, one argument
  }
  n++;
  // The managed path never takes a page instruction.
  const managed = new Controller({ bootstrapUrl: '/b', managedBootstrap: { sessionUrl: '/m' } });
  managed._adapter = new Adapter();
  assert.equal(managed.speak('x'), false);
  assert.deepEqual(managed._adapter.sent, []);
  n++;
  // The real OpenAI adapter: German's instruction sends the bytes it sent before; only a host's tracked one carries a tag
  // in the response's metadata, and response.created reports it (review FR N3).
  const { OpenAIWebRTCAdapter: RealOpenAI } = await import(new URL('adapters/openai-webrtc-adapter.js', PATH));
  const wire = new RealOpenAI(), frames = [];
  wire._dc = { send: (frame) => frames.push(frame) };
  wire.sendContinuationInstruction('Begin.');
  assert.deepEqual(frames, ['{"type":"conversation.item.create","item":{"type":"message","role":"user","content":[{"type":"input_text","text":"Begin."}]}}',
    '{"type":"response.create"}']);
  wire.sendContinuationInstruction('Say only this.', 'minimoi-1');
  assert.equal(frames[3], '{"type":"response.create","response":{"metadata":{"minimoi_tag":"minimoi-1"}}}');
  wire.sendFunctionResult('call_1', { ok: true });                                         // a function result: as before
  assert.deepEqual(frames.slice(4), ['{"type":"conversation.item.create","item":{"type":"function_call_output","call_id":"call_1","output":"{\\"ok\\":true}"}}',
    '{"type":"response.create"}']);
  wire.sendFunctionResult('call_2', 'done', 'minimoi-2');                                   // tagged only for a tracking host
  assert.equal(frames[7], '{"type":"response.create","response":{"metadata":{"minimoi_tag":"minimoi-2"}}}');
  const started = [];
  wire.on('response_started', (e) => started.push(e));
  wire._handleServerEvent({ type: 'response.created', response: { id: 'r1' } });
  wire._handleServerEvent({ type: 'response.created', response: { id: 'r2', metadata: { minimoi_tag: 'minimoi-1' } } });
  assert.deepEqual(started, [{ response_id: 'r1', tag: null }, { response_id: 'r2', tag: 'minimoi-1' }]);
  n++;
} finally { Object.assign(globalThis, saved); }
// No syntax or API newer than these files used before (review FR S2): German, Portuguese and CoS load them on every browser
// they ran on. Comments aside; Object.hasOwn no more often than at 93bf00c6.
const NEWER = /\.at\(|\.findLast(?:Index)?\(|structuredClone\(|Object\.groupBy\(|\.toSorted\(|\.toReversed\(|\.toSpliced\(/;
for (const [name, hasOwn] of [['realtime-voice-controller.js', 2], ['adapters/xai-websocket-adapter.js', 0], ['adapters/openai-webrtc-adapter.js', 0]]) {
  const code = fs.readFileSync(new URL(name, PATH), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
  assert.equal(NEWER.exec(code)?.[0] ?? null, null, name);
  assert.ok((code.match(/Object\.hasOwn\(/g) || []).length <= hasOwn, name);
}
n++;
console.log(`${n} shared-controller no-change scenarios passed (German source pinned, additive methods); no provider or microphone.`);
