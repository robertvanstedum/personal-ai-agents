// The shared controller and adapters after Spec 165 A1, its fix round and its binding pass, for German and Portuguese:
// byte-for-byte the files they ran before, plus additive parts. Some of those parts run in every session (the listeners see
// every German transcript and response, and xAI's per-response accounting runs; review FR S2), so the behaviour half below
// drives German's transcript path through them, on a browser without Array.prototype.at too. The additions are: the
// controller's public methods (sessionId(), state(), speak(), speakTracked(), inputOf()), its optional onPlaybackDrained
// callback, its oncePerItem option (review F2: a host hears each owner item's completion once; German passes none and hears
// every completion as before, pinned in test_response_binding.mjs), and its pageResponses and onUnbound options (the binding
// pass, Robert's C2 decision of 28 Oct 2026, "tie by construction": a host page asks for its responses one at a time, each
// tagged, and where the bootstrap says page_responses it creates every response that may act for one committed item of
// his; German passes neither, so every request is sent at once, untagged, with the same arguments as before: checked
// below); the adapter events it listens to (response_started, response_done, playback_drained, input_item: nothing reaches
// a page that passes no callback); the adapters' response tracking that emits those events (Spec 165 §5.1, §5.3; how each
// response's playback ended, v0.3 G3; each response's end and status, the binding pass), the response id on each function
// call, the OpenAI adapter's optional response tag (sent only when one is passed; German's bytes are unchanged, checked
// below), each adapter's createResponse() (called only where the page creates every response; German never does), and new
// adapter cache keys. The strip pairs below are generated from a line diff against 93bf00c6 and checked to give its bytes
// exactly. Pinned two ways: each source with exactly those additions taken out hashes to its 93bf00c6 file (the Chunk B
// baseline; what production German, Portuguese and CoS receive at merge from origin/main is listed in A1_FIX_RESULT.md);
// and the methods, driven on a synthetic transport, send nothing unless a host calls them, and nothing on the managed
// path. No provider, microphone or network.
import fs from 'node:fs';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';

const PATH = new URL('../../core/realtime_voice/static/realtime-voice-controller.js', import.meta.url);
const BEFORE = '682906489b0b3f9f02cd3df951da6fbaaef74c2aa49927d5905827ea3b8c7f05';     // 93bf00c6, as German loads it today
const CONTROLLER = [
  [`// Both adapters changed for the playback events and response attribution, OpenAI's again for the response tag on every
// request, both again for how a response's playback ended, and both again for the page's own responses (each response's
// end, and creating one for a committed item): new keys so browsers fetch them (Spec 165 §4.3, §5.1, §5.3; review FR N3,
// V R2-M1; v0.3 G3; the C2 binding pass).
import { OpenAIWebRTCAdapter } from "./adapters/openai-webrtc-adapter.js?v=20261028-paged1";
import { XAIWebSocketAdapter } from "./adapters/xai-websocket-adapter.js?v=20261028-paged1";
`,
   `import { OpenAIWebRTCAdapter } from "./adapters/openai-webrtc-adapter.js?v=20261015-managed-end1";
import { XAIWebSocketAdapter } from "./adapters/xai-websocket-adapter.js?v=20260725-transcript1";
`],
  [`// The page's own responses (Spec 165 §5.1 as amended for Robert's C2 decision, 28 Oct 2026: "tie by construction"): a page
// request whose response has not started in this long is let go, and so is a started one that has not finished in this
// long. A bound only lets a request go (its response, if it starts later, answers no one); it never names an owner.
const REQUEST_TTL_MS = 10000;
const RESPONSE_TTL_MS = 120000;
`,
   ``],
  [`    onSessionInfo, onPlaybackDrained = null, pageResponses = false, oncePerItem = false, onUnbound = null,
`,
   `    onSessionInfo,
`],
  [`    this._onPlaybackDrained = onPlaybackDrained;     // optional (Spec 165 §5.3): a host that waits for a response's audio
    // Optional (Spec 165 §5.1 as amended 28 Oct 2026; Robert's C2 decision, "tie by construction"): a host page that asks
    // for its responses one at a time, each tagged (pageResponses: MC's direct page). Where the bootstrap also says
    // page_responses (a provider eligible for the model's own actions: it names the committed item and creates no response
    // of its own), the page creates every response that may act, one for each committed owner item, and a call acts on
    // that item's words alone. German and Portuguese pass no option: every request is sent at once, untagged, as before.
    this._pageResponses = pageResponses;
    this._owned = false;             // every response of this session is the page's (the bootstrap said page_responses)
    this._queue = [];                // page requests waiting their turn, in order: {kind, owner, turn, tag, send, done}
    this._inflight = null;           // the one page request in flight, from its response.create to its response.done
    this._answers = new Map();       // response id -> {owner: his item id or null, turn, acts}, or null: not the page's
    this._turn = 0;                  // his turns: each speech start and each commit begins a new one
    this._lost = 0;                  // page requests let go before they started: a late start of one is not told by order
    this._tags = 0;
    this._expiry = null;
    this._onUnbound = onUnbound || ((info) => console.warn?.("[realtime-voice] a response bound to nothing", info));
    // Optional (review F2): a host that hears each owner item's completion once (MC's page). A replayed provider event, or
    // a second completion of an item already completed, is not a new turn. German and Portuguese pass no option.
    this._fresh = true;              // the completion being handled is the first of its item
    if (oncePerItem && onUserTurn) this._onUserTurn = (text) => { if (this._fresh) onUserTurn(text); };
`,
   ``],
  [`    this._owned = this._pageResponses && bootstrap.page_responses === true;
`,
   ``],
  [`    // The page's own responses (Spec 165 §5.1), first, so a host asking inputOf() from any callback below sees them current.
    on("connected", () => this._reset());
    on("closed", () => this._reset());
    on("speech_started", () => { this._turn += 1; });
    on("input_item", (evt) => this._committed(evt.item_id || null));
    on("input_transcript", (evt) => { this._fresh = this._firstCompletion(evt); });
    on("response_started", (evt) => this._started(evt));
    on("response_done", (evt) => this._finished(evt));
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
  [`      if (this._refused(call)) return;
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
   *  session's transport took it (with pageResponses: when it is sent or waits its turn). Its response answers none of his
   *  inputs. Never on the managed path. German and Portuguese do not call it. */
  speak(instruction) {
    if (this._managedBootstrap || !this._adapter) return false;
    try { this._ask(null, (tag) => this._instruct(instruction, tag)); return true; } catch (_) { return false; }
  }

  /** speak(), tracked (Spec 165 §5.3; review FR N3, V note D): resolves with the id of the response this instruction
   *  starts, or null when it has not started within maxMs, or the transport refused it. With pageResponses the response is
   *  known as every page request's is (see _started): by its echoed tag, or as the one page request in flight. A host
   *  passes onPlaybackDrained to learn when that response's audio has played out. German and Portuguese do not call it. */
  speakTracked(instruction, maxMs = 5000) {
    if (this._managedBootstrap || !this._adapter) return Promise.resolve(null);
    return new Promise((resolve) => {
      let timer = null, entry = null;
      try { entry = this._ask(null, (tag) => this._instruct(instruction, tag), (id) => { clearTimeout(timer); resolve(id); }); }
      catch (_) { resolve(null); return; }
      timer = setTimeout(() => { entry.done = null; resolve(null); }, maxMs);   // it keeps its turn; only the wait ends
    });
  }

  /** The owner input item a function call acts on (Spec 165 §5.1 as amended 28 Oct 2026, C2 "tie by construction"). Only
   *  where the page creates every response (pageResponses, and the bootstrap's page_responses). Then:
   *  - each response that may act was created by the page for one committed owner item, named by the provider's commit
   *    (input_audio_buffer.committed's item_id), or is a function result's follow-up, owned by the item of the call it
   *    continues;
   *  - a response is the page's by its echoed tag, or as the one page request in flight; any other response answers no one;
   *  - its calls act only if it completed and no new turn of his began while it was in flight (I5).
   *  No order and no timing names an owner. The call is bound to its response by response_id, and its words are that
   *  item's transcript, joined by the provider's item id alone: {state: "bound", index, text} once it has completed
   *  (index: its place in the transcript list the host is given), {state: "pending"} until then, or null when the call
   *  may act on no owner input. Never the latest input by default. German and Portuguese do not call it. */
  inputOf(call) {
    const r = this._owned && call && call.response_id ? this._answers.get(call.response_id) : null;
    if (!r || !r.owner || r.acts !== true) return null;
    const order = [...this._items.entries()].sort((a, b) => a[1].firstSeq - b[1].firstSeq);
    const index = order.findIndex(([id]) => id === r.owner);
    if (index < 0) return { state: "pending" };
    const item = order[index][1];
    return item.completed ? { state: "bound", index, text: item.text || "" } : { state: "pending" };
  }

  // Whether a transcript event completes its item for the first time (read before the event is recorded).
  _firstCompletion(evt) {
    if (!evt.completed || (evt.provider_event_id && this._seenEventIds.has(evt.provider_event_id))) return false;
    const item = this._items.get(evt.item_id);
    return !(item && item.completed);
  }

  // His input committed (OpenAI's input_audio_buffer.committed): a new turn of his. Where the page creates every response,
  // it asks now, in this same tick, for one response owned by the committed item. An earlier committed item still waiting
  // its turn is let go: it never gets a response of its own, so nothing acts on its words (the newest is answered; he says
  // the earlier one again if he needs it). A commit naming no item still gets a response, which may act on no one.
  _committed(item) {
    this._turn += 1;
    if (!this._owned) return;
    for (const entry of this._queue.filter((e) => e.kind === "turn")) this._let(entry, "superseded");
    if (!item) this._onUnbound({ reason: "commit_without_item" });
    this._ask(item, (tag) => this._adapter.createResponse(tag), null, "turn");
  }

  // A response this controller asks for: his committed item's (turn), a function result's follow-up (follow), or the
  // page's own line, the opening or the continuation (line). Every response.create the controller sends goes through here.
  // Without pageResponses it is sent at once, untagged, as before. With it, it is tagged and waits its turn: one page
  // request is in flight at a time, from its response.create until that response is done. Throws if the transport refuses
  // it at once.
  _ask(owner, send, done = null, kind = "line") {
    if (!this._pageResponses) { send(null); return { done }; }
    const entry = { kind, owner, turn: this._turn, tag: \`minimoi-\${++this._tags}\`, send, done, at: 0, started: false, id: null };
    this._queue.push(entry);
    this._pump();
    if (entry.refused) throw new Error("Voice transport refused the request");
    return entry;
  }

  _instruct(text, tag) {
    if (tag) this._adapter.sendContinuationInstruction(text, tag); else this._adapter.sendContinuationInstruction(text);
  }

  // A function result: its follow-up is owned by the item of the call it continues (V R2-M1, R2-S1).
  _sendResult(call, output) {
    const r = call && this._answers.get(call.response_id);
    this._ask(r ? r.owner : null, (tag) => {
      if (tag) this._adapter.sendFunctionResult(call.call_id, output, tag); else this._adapter.sendFunctionResult(call.call_id, output);
    }, null, "follow");
  }

  // The next page request goes once none is in flight. A request in flight past its bound is let go first. His committed
  // item's request goes only while no newer turn of his has begun; otherwise it is let go (he is speaking again: the commit
  // that ends it asks for its own response).
  _pump() {
    const f = this._inflight;
    if (f && Date.now() - f.at >= (f.started ? RESPONSE_TTL_MS : REQUEST_TTL_MS)) {
      this._inflight = null;
      if (!f.started) this._lost += 1;
      this._let(f, f.started ? "unfinished" : "never_started");
    }
    while (!this._inflight && this._queue.length) {
      const entry = this._queue.shift();
      if (entry.kind === "turn" && entry.turn !== this._turn) { this._let(entry, "superseded"); continue; }
      entry.turn = this._turn; entry.at = Date.now();
      this._inflight = entry;
      try { entry.send(entry.tag); } catch (_) { this._inflight = null; entry.refused = true; this._let(entry, "refused"); }
    }
    this._arm();
  }

  // One timer for the request in flight, so a bound passes even when no provider event comes. Unreferenced where the
  // runtime allows it (node's tests); cleared when the request settles and when the transport closes.
  _arm() {
    clearTimeout(this._expiry);
    this._expiry = null;
    const f = this._inflight;
    if (!f) return;
    const left = (f.started ? RESPONSE_TTL_MS : REQUEST_TTL_MS) - (Date.now() - f.at);
    this._expiry = setTimeout(() => { this._expiry = null; this._pump(); }, Math.max(0, left));
    if (this._expiry && typeof this._expiry.unref === "function") this._expiry.unref();
  }

  // A page request let go: its wait ends with no response, and it answers no one.
  _let(entry, reason) {
    const i = this._queue.indexOf(entry);
    if (i >= 0) this._queue.splice(i, 1);
    if (entry.done) { const done = entry.done; entry.done = null; done(null); }
    if (this._owned && entry.kind !== "line") this._onUnbound({ reason, owner: entry.owner || null });
  }

  _reset() {
    const pending = this._inflight ? [this._inflight, ...this._queue] : this._queue.slice();
    this._queue = []; this._inflight = null; this._lost = 0;
    for (const entry of pending) if (entry.done) { const done = entry.done; entry.done = null; done(null); }
    this._arm();
  }

  // A response started (response.created). It is the page's when it echoes the tag of the page request in flight, or, with
  // no tag, while that request has not started yet: it is then that request's, owned as the request is. Any other response
  // (none in flight, one too many, another tag) answers no one; where the page creates every response it is logged. After
  // a request was let go before it started, an untagged start cannot be told from that request's late one: it settles the
  // request in flight, but answers no one ("say it again"), once for each request let go.
  _started(evt) {
    if (!this._pageResponses) return;
    const id = evt.response_id || null;
    if (id && this._answers.has(id)) return;                     // its start was already seen
    this._pump();                                                // a request past its bound is let go first
    const f = this._inflight;
    if (!f || f.started || (evt.tag && evt.tag !== f.tag)) {
      if (id) this._answers.set(id, null);
      if (this._owned) this._onUnbound({ reason: !f ? "unrequested" : f.started ? "one_too_many" : "another_tag", response_id: id });
      return;
    }
    const unsure = !evt.tag && this._lost > 0;
    if (unsure) {
      this._lost -= 1;
      if (this._owned) this._onUnbound({ reason: "after_lost_request", response_id: id });
    }
    f.started = true; f.id = id; f.at = Date.now();
    if (id) this._answers.set(id, { owner: unsure ? null : f.owner, turn: f.turn, acts: null });
    this._arm();
    if (f.done) { const done = f.done; f.done = null; done(id); }
  }

  // A response is done (response.done, before its function calls are passed on). Its calls act only if it completed and no
  // new turn of his began while it was in flight: one cancelled at his barge-in, cut short, or overtaken by his next words
  // is refused, never re-bound. The page request it answered is settled, and the next one goes.
  _finished(evt) {
    if (!this._pageResponses) return;
    const id = evt.response_id || null;
    const r = id ? this._answers.get(id) : null;
    if (r && r.acts === null) r.acts = evt.status === "completed" && r.turn === this._turn;
    const f = this._inflight;
    if (f && f.started && f.id === id) { this._inflight = null; this._pump(); }
  }

  // A call from a page response that may not act (cancelled, cut short or overtaken by his speech): nothing is dispatched,
  // and no result or follow-up is sent for it; the response that answers his newer words is the one that acts.
  _refused(call) {
    const r = this._pageResponses && call && call.response_id ? this._answers.get(call.response_id) : null;
    if (!r || r.acts !== false) return false;
    if (this._owned) this._onUnbound({ reason: "superseded_call", response_id: call.response_id, owner: r.owner || null });
    return true;
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
  [`    this._responses = new Map();     // response id -> {sources, done, audio, status}: which response each chunk of audio belongs to
`,
   ``],
  [`  // Spec 165 §5.3: playback_drained {response_id, outcome} once a response is done and every chunk of its audio has played
  // out (or was stopped at barge-in); a response with no audio drains at its end. The outcome names how it ended (v0.3, G3):
  // drained (completed, and its audio played to the end), barged_in (stopped at his speech), cancelled (the provider
  // cancelled it, or ended it incomplete or failed), no_audio; or none claimed (null) when xAI gave no status.
  // Nothing listens unless a host passes onPlaybackDrained to the controller; German and Portuguese do not.
  _response(id) {
    const key = id || this._responseId || null;
    if (!this._responses.has(key)) this._responses.set(key, { sources: 0, done: false, audio: false, status: null });
    return [key, this._responses.get(key)];
  }

  _drainedIf(key, outcome = null) {
    const r = this._responses.get(key);
    if (r && r.done && r.sources <= 0) {
      this._responses.delete(key);
      const cut = ["cancelled", "incomplete", "failed"].includes(r.status);
      this._emit("playback_drained", { response_id: key,
        outcome: outcome || (cut ? "cancelled" : !r.audio ? "no_audio" : r.status === "completed" ? "drained" : null) });
    }
  }

`,
   ``],
  [`    this._ws?.send(JSON.stringify({ type: "response.create" }));
  }

  // Spec 165 §5.1 as amended (C2 by construction): a response the page asks for itself. xAI is not eligible for the page's
  // own responses until A4 shows it names the committed item and creates none of its own; nothing calls this until then.
  createResponse() {
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
  [`        // Spec 165 §5.1 (C2 by construction): the response's end and how it ended, before its function calls are passed on.
        this._emit("response_done", { response_id: event.response?.id || this._responseId || null, status: event.response?.status || null });
`,
   ``],
  [`              response_id: event.response?.id || this._responseId || null,
`,
   ``],
  [`        }
        {
          const [key, r] = this._response(event.response?.id);
          r.done = true;
          // Cut short: cancelled, incomplete or failed is never heard to the end (review L N9). Only a response whose status
          // says completed is drained once its audio has played; with no status (xAI's status field is not verified) no
          // outcome is claimed (review W N1).
          r.status = event.response?.status || null;
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
   ``],
  ]],
  'adapters/openai-webrtc-adapter.js': ['dae118fd252add9a80755a363d64ce6b4ef377b004d177e8dc10bba5a3c274a7', [
  [`    this._audible = new Set();       // responses whose audio the provider says is playing (output_audio_buffer.started)
`,
   ``],
  [`  sendContinuationInstruction(text, tag) {
`,
   `  sendContinuationInstruction(text) {
`],
  [`      item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
    }));
    this._dc?.send(JSON.stringify(_responseCreate(tag)));
  }

`,
   `      item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
    }));
    this._dc?.send(JSON.stringify({ type: "response.create" }));
  }

`],
  [`  // Spec 165 §5.1 as amended (C2 by construction): the page's own response for a committed item of his, tagged. Only a host
  // that creates every response calls it (the session's turn detection has create_response false); German never does.
  createResponse(tag) {
    this._dc?.send(JSON.stringify(_responseCreate(tag)));
  }

  sendFunctionResult(callId, output, tag) {
`,
   `  sendFunctionResult(callId, output) {
`],
  [`      },
    }));
    this._dc?.send(JSON.stringify(_responseCreate(tag)));
  }

`,
   `      },
    }));
    this._dc?.send(JSON.stringify({ type: "response.create" }));
  }

`],
  [`        // Spec 165 §5.1 (review W M1): the item this speech will be committed as, when the provider names it.
        this._emit("speech_started", { item_id: event.item_id || null });
`,
   `        this._emit("speech_started", {});
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
  [`        // Spec 165 §5.1 (C2 by construction): the response's end and how it ended, before its function calls are passed on.
        this._emit("response_done", { response_id: event.response?.id || null, status: event.response?.status || null });
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
   ``],
  ]],
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
  // A direct host (pageResponses, as the direct page passes it): sessionId() is the server's id; speak() reaches the transport
  // when no page request is in flight, and waits its turn otherwise (C2 by construction: one page request at a time).
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ provider: 'xai', model: 'm', warning_minutes: 20, max_minutes: 30,
    voice_session_id: '0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b' }) });
  const host = new Controller({ bootstrapUrl: '/b', openingInstruction: null, pageResponses: true, onUnbound: () => {} });
  await host.startSession({ provider: 'xai', persona: 'master_craftsman', scene: '', conversation_id: 'c-1' });
  assert.equal(host.sessionId(), '0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7b');
  assert.deepEqual(adapters.at(-1).sent, []);                                            // no opening instruction for a host
  assert.equal(host.speak('Still checking.'), true);
  assert.deepEqual(adapters.at(-1).sent, ['Still checking.']);
  n++;
  // speakTracked(): it waits behind the line in flight, then the response it starts is known as the one page request in
  // flight; a page that passes no onPlaybackDrained hears no drain.
  const tracked = host.speakTracked('Say only this.');
  assert.deepEqual(adapters.at(-1).sent, ['Still checking.']);
  adapters.at(-1).emit('response_started', { response_id: 'resp_8' });
  adapters.at(-1).emit('response_done', { response_id: 'resp_8', status: 'completed' });
  assert.deepEqual(adapters.at(-1).sent, ['Still checking.', 'Say only this.']);
  adapters.at(-1).emit('response_started', { response_id: 'resp_9' });
  assert.equal(await tracked, 'resp_9');
  adapters.at(-1).emit('response_done', { response_id: 'resp_9', status: 'completed' });
  adapters.at(-1).emit('playback_drained', { response_id: 'resp_9' });                    // no callback: nothing happens
  adapters.at(-1).sendContinuationInstruction = () => { throw new Error('closed'); };
  assert.equal(host.speak('again'), false);                                              // a transport that refuses: false, no throw
  // xAI is not eligible (no page_responses): a commit asks for nothing.
  adapters.at(-1).emit('input_item', { item_id: 'i1' });
  assert.deepEqual(adapters.at(-1).created || [], []);
  n++;
  // The bootstrap says page_responses (OpenAI on the direct path): a commit asks, in the same tick, for one tagged response.
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ provider: 'openai', model: 'm', warning_minutes: 20, max_minutes: 30,
    voice_session_id: '0b5c9e2e-7f1a-4b8e-9d3c-2a1f0e9d8c7c', page_responses: true }) });
  const paged = new Controller({ bootstrapUrl: '/b', openingInstruction: null, pageResponses: true, onUnbound: () => {} });
  await paged.startSession({ provider: 'openai', persona: 'master_craftsman', scene: '', conversation_id: 'c-1' });
  adapters.at(-1).createResponse = function (tag) { (this.created ||= []).push(tag); };
  adapters.at(-1).emit('input_item', { item_id: 'i1' });
  assert.deepEqual(adapters.at(-1).created, ['minimoi-1']);
  // German (no pageResponses), even with a bootstrap that said page_responses: a commit asks for nothing.
  const germanPaged = new Controller({ bootstrapUrl: '/api/realtime-voice/bootstrap' });
  await germanPaged.startSession({ provider: 'openai', persona: 'frau_berger', scene: 'bakery', learner_name: 'Robert' });
  adapters.at(-1).createResponse = function (tag) { (this.created ||= []).push(tag); };
  adapters.at(-1).emit('input_item', { item_id: 'i1' });
  assert.equal(adapters.at(-1).created, undefined);
  n++;
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
  // A function result from a German session (no pageResponses) reaches the adapter with the same two arguments as before.
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
