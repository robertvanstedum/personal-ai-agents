/**
 * realtime-voice-controller.js — shared realtime voice session controller.
 * One implementation used by German (Gespräche) and Portuguese (Conversas);
 * per build spec Section 5, domains supply only locale/labels/personas/
 * scenes/voices/save callbacks, never their own copy of this session loop.
 *
 * State machine (Section 11):
 *   idle -> requesting_mic -> connecting -> active
 *        -> reconnecting -> ending -> ended
 *
 * Usage from a domain page:
 *   import { RealtimeVoiceController } from "/static/realtime-voice/realtime-voice-controller.js";
 *   const controller = new RealtimeVoiceController({
 *     bootstrapUrl: "/api/realtime-voice/bootstrap",
 *     onStateChange(state) { ... },
 *     onWarning(elapsedSeconds) { ... },
 *     onStop(elapsedSeconds) { ... },
 *     onFatalError(info) { ... },
 *     onFinalize(result) { ... },   // {turns, partial, partial_reason, provider, model, session_id}
 *   });
 *   await controller.startSession({provider, persona, scene, learner_name});
 *   controller.endSession("user_ended");
 */
// Both adapters changed for the playback events and response attribution, OpenAI's again for the response tag on every
// request, both again for how a response's playback ended, and both again for the page's own responses (each response's
// end, and creating one for a committed item), and both for the live probes (OpenAI: a reply's end reported once, LV1; xAI:
// an interim transcript's status, LV2), and xAI's for a microphone granted after the start ended (Gate 2 V S2): new keys
// so browsers fetch them (Spec 165 §4.3, §5.1, §5.3; review FR N3, V R2-M1; v0.3 G3; the C2 binding pass).
import { OpenAIWebRTCAdapter } from "./adapters/openai-webrtc-adapter.js?v=20261028-live1";
import { XAIWebSocketAdapter } from "./adapters/xai-websocket-adapter.js?v=20261029-gate2";

const CONTINUATION_INSTRUCTION = "Continue naturally in character.";
// The page's own responses (Spec 165 §5.1 as amended for Robert's C2 decision, 28 Oct 2026: "tie by construction"): a page
// request whose response has not started in this long is let go; so is a started one that has shown no sign of life (no
// output of any response) in RESPONSE_IDLE_MS, or has not finished in RESPONSE_TTL_MS. A bound only lets a request go (its
// response keeps the owner it started with, and a later start of a request let go answers no one); it never names an owner.
const REQUEST_TTL_MS = 10000;
const RESPONSE_IDLE_MS = 20000;
const RESPONSE_TTL_MS = 120000;
// A call from a page response that may not act: what its function output says, so the model's context is true. No response
// is asked for it (review B S3). It says which (Gate 1 n2): overtaken (cancelled at his speech, or his next words began
// while it was in flight), or cut short (ended incomplete or failed, with no speech of his).
const SUPERSEDED = { ok: false, outcome: "not_done", error: "superseded",
  spoken: "Not run: superseded by what he said next. Nothing was done." };
const CUT_SHORT = { ok: false, outcome: "not_done", error: "cut_short",
  spoken: "Not run: the reply was cut short before it could run. Nothing was done." };
// A call passed to the host whose output is not sent yet, when the page must make another reply (live probes, LV4): its
// honest answer for now, as the sentence the voice should say, since the model reads it out word for word (Gate 2 E N2).
// Its result is said later, as a page line.
const PENDING = { ok: false, outcome: "pending", spoken: "That's still running. I'll tell you when it's back." };
// Confirm by voice, v3 (the app acts; the voice only talks): a host that refuses every model call (refuseCalls) answers
// one that arrives anyway with this output and asks no reply for it. No tools are offered on either provider.
const NO_ACTIONS = { ok: false, outcome: "not_done", error: "no_model_actions",
  spoken: "Not run: on this voice the app acts, only after he confirms. Nothing was done." };
const LATE_LINE = "Say this briefly, adding nothing: ";
const LATE_DONE = "That earlier check has finished; its answer is in the conversation.";
const OPENING_INSTRUCTION =
  "Begin in character with one short, natural greeting and one brief question " +
  "inviting the learner to say what they need. Do not give directions, suggest " +
  "a destination, or advance the scenario before hearing the learner.";

const ADMISSION_MESSAGES = Object.freeze({
  voice_session_unresolved: "An earlier voice session has not been confirmed ended. Its status needs verification before voice can start. Typing still works.",
  agent_action_unresolved: "An earlier agent action needs verification before voice can start. Typing still works.",
});
function admissionRefused(error) {
  return error?.managedAdmissionRefused === true && Object.hasOwn(ADMISSION_MESSAGES,error.code);
}

export class RealtimeVoiceController {
  constructor({
    bootstrapUrl, onStateChange, onInputState, onWarning, onStop,
    onFatalError, onFinalize, onUserTurn, onAssistantTurn, onFunctionCall, onTranscriptUpdate, onPlayback,
    onSessionInfo, onPlaybackDrained = null, pageResponses = false, oncePerItem = false, onUnbound = null, bootstrapTimeoutMs = 0,
    respondFor = null, onResponseStarted = null, refuseCalls = false,
    openingInstruction = OPENING_INSTRUCTION,
    managedBootstrap = null, requestHeaders = null, includeTranscriptAfterEnd = true,
  }) {
    this._bootstrapUrl = bootstrapUrl;
    this._managedBootstrap = managedBootstrap;
    this._requestHeaders = requestHeaders || (() => ({}));
    this._includeTranscriptAfterEnd = includeTranscriptAfterEnd;
    this._generation = 0;
    this._endingPromise = null;
    this._managedCreate = null;
    this._managedEndRequests = new Map();
    this._onStateChange = onStateChange || (() => {});
    this._onInputState = onInputState || (() => {});
    this._onWarning = onWarning || (() => {});
    this._onStop = onStop || (() => {});
    this._onFatalError = onFatalError || (() => {});
    this._onFinalize = onFinalize || (() => {});
    this._onTranscriptUpdate = onTranscriptUpdate || (() => {});
    this._onUserTurn = onUserTurn || (() => {});
    this._onAssistantTurn = onAssistantTurn || (() => {});
    this._onFunctionCall = onFunctionCall || null;
    this._onPlayback = onPlayback || (() => {});
    this._onSessionInfo = onSessionInfo || (() => {});
    this._onPlaybackDrained = onPlaybackDrained;     // optional (Spec 165 §5.3): a host that waits for a response's audio
    // Optional (review W N3): the direct-mode bootstrap POST, and the reading of its answer, aborted after this long (the
    // direct page passes it). At the bound it is the bootstrap's network failure. German and Portuguese pass none: unbounded.
    this._bootstrapTimeoutMs = Number(bootstrapTimeoutMs) > 0 ? Number(bootstrapTimeoutMs) : 0;
    // Optional (Spec 165 §5.1 as amended 28 Oct 2026; Robert's C2 decision, "tie by construction"): a host page that asks
    // for its responses one at a time, each tagged (pageResponses: MC's direct page). Where the bootstrap also says
    // page_responses (a provider eligible for the model's own actions: it names the committed item and creates no response
    // of its own), the page creates every response that may act, one for each committed owner item, and a call acts on
    // that item's words alone. German and Portuguese pass no option: every request is sent at once, untagged, as before.
    this._pageResponses = pageResponses;
    // Optional, confirm by voice v3 (V3_BUILD_BRIEF "Asking, per provider"); the direct page passes them, German and
    // Portuguese pass none, so nothing changes for them:
    // - respondFor(itemId): where the page creates every response (OpenAI), each committed item's response waits for the
    //   host's word on it (its server decision, at most the host's own bound): a Promise of {instruction} (the response
    //   is created with that page instruction), {skip: true} (no response for this item: the host speaks for it later), or
    //   null (a normal response). A newer turn of his while it waits supersedes it, as before.
    // - onResponseStarted({response_id, owner, page}): a response started (owner: his item id, where the page made it for
    //   one; page: whether it was a page request of the host's). Evidence of when something started playing, never
    //   authority to act.
    // - refuseCalls: every model function call is answered NO_ACTIONS, with no reply asked, and never passed to the host.
    this._respondFor = typeof respondFor === "function" ? respondFor : null;
    this._onResponseStarted = typeof onResponseStarted === "function" ? onResponseStarted : null;
    this._refuseCalls = refuseCalls === true;
    this._owned = false;             // every response of this session is the page's (the bootstrap said page_responses)
    this._queue = [];                // page requests waiting their turn, in order: {kind, owner, turn, tag, send, done}
    this._inflight = null;           // the one page request in flight, from its response.create to its response.done
    this._answers = new Map();       // response id -> {owner: his item id or null, turn, acts}, or null: not the page's
    this._turn = 0;                  // his turns: each speech start and each commit begins a new one
    this._lost = false;              // a page request was let go before it started: from then on no untagged start is ours
    this._owed = 0;                  // where the provider replies itself: its replies to his turns not yet started (G-S1)
    this._utterance = null;          // his utterance so far: "speaking", then "stopped" or "committed" (counted once)
    this._done = new Set();          // replies whose response.done was handled: a later one only informs playback (LV1)
    this._calls = new Set();         // function calls passed to the host, by call_id: each once (LV1)
    this._outstanding = new Map();   // call_id -> {pending}: a call passed to the host whose output is not sent yet (LV4)
    this._repeat = null;             // the reply whose repeated response.done is being handled: its calls are dropped (Q N15)
    this._active = new Map();        // where the provider replies itself (xAI): its replies under way, id -> {seen} (LV3)
    this._speaking = false;          // there: his speech is under way (LV3)
    this._due = 0;                   // there: when his last counted turn ended, until a reply starts (LV3)
    this._holding = false;           // no page request is sent while a start is being read (LV3)
    this._echoed = false;            // the provider has echoed a page request's tag: from then on an untagged start is not ours
    this._tags = 0;
    this._expiry = null;
    this._onUnbound = onUnbound || ((info) => console.warn?.("[realtime-voice] a response bound to nothing", info));
    // Optional (review F2): a host that hears each owner item's completion once (MC's page). A replayed provider event, or
    // a second completion of an item already completed, is not a new turn. German and Portuguese pass no option.
    this._fresh = true;              // the completion being handled is the first of its item
    if (oncePerItem && onUserTurn) this._onUserTurn = (text) => { if (this._fresh) onUserTurn(text); };
    this._openingInstruction = openingInstruction;
    this._pendingAssistantTurns = [];

    this._state = "idle";
    this._adapter = null;
    this._items = new Map(); // item_id -> {speaker, text, completed, firstSeq}
    this._seenEventIds = new Set();
    this._seq = 0;
    this._startedAt = null;
    this._warned = false;
    this._durationTimer = null;
    this._warningMinutes = 20;
    this._maxMinutes = 30;
    this._provider = null;
    this._model = null;
    this._sessionId = null;
    this._reconnectAttempted = false;
  }

  _setState(state) {
    this._state = state;
    this._onStateChange(state);
  }

  async startSession({ provider, persona, scene, learner_name, conversation_id }) {
    if (this._managedBootstrap && !["idle", "ended"].includes(this._state)) return;
    this._endingPromise = null;
    this._managedCreate = null;
    const generation = ++this._generation;
    if (this._managedBootstrap) {
      if (provider !== "openai") throw new Error("Provider not verified for this voice surface");
      this._items = new Map(); this._seenEventIds = new Set();
      this._pendingAssistantTurns = []; this._seq = 0; this._warned = false;
      this._lastUsage = null; this._sessionId = null; this._startedAt = null;
    }
    this._setState("requesting_mic");
    const bound = this._bootstrapBound();
    let resp;
    try {
      resp = this._managedBootstrap ? {
        ok: true, json: async () => ({provider: "openai", model: "gpt-realtime-2.1", warning_minutes: 13, max_minutes: 15}),
      } : await fetch(this._bootstrapUrl, {
        method: "POST",
        // A host that guards its bootstrap (MC on the Guild: CSRF, record mode) passes requestHeaders and names
        // its conversation; German and Portuguese pass neither, so their request is unchanged.
        headers: { "Content-Type": "application/json", ...this._requestHeaders() },
        body: JSON.stringify({ provider, persona, scene, learner_name, ...(conversation_id ? { conversation_id } : {}) }),
        ...(bound ? { signal: bound.signal } : {}),
      });
    } catch (err) {
      bound?.clear();
      this._setState("idle");
      this._onFatalError({ reason: "bootstrap_network_error", detail: String(err) });
      return;
    }
    if (!resp.ok) {
      this._setState("idle");
      const body = await resp.json().catch(() => ({}));
      bound?.clear();
      this._onFatalError({ reason: "bootstrap_rejected", status: resp.status, detail: body.error });
      return;
    }
    const bootstrap = bound ? await bound.read(resp) : await resp.json();
    if (bound && bootstrap === null) return;          // the bound passed while the answer was read: said already
    // A direct start ended while its bootstrap was in flight (Gate 2 Q S3: endSession advanced the generation): it never
    // builds an adapter, connects or takes the microphone. The host is told the session it was given, so it ends it there.
    if (this._pageResponses && generation !== this._generation) {
      if (bootstrap && bootstrap.voice_session_id) this._onSessionInfo({ provider: bootstrap.provider, model: bootstrap.model,
        voice_session_id: bootstrap.voice_session_id, voice_notices: [] });
      return;
    }
    if (generation !== this._generation) return;
    if (this._managedBootstrap) {
      bootstrap.isCurrent = () => generation === this._generation;
      bootstrap.exchangeSdp = async sdp => {
        if (generation !== this._generation) throw new Error("Voice session ended before connecting");
        // Keep the actual outstanding create so End cannot mistake an unknown
        // server identity for proof that no provider session was attempted.
        const creating=(async()=>{
          const response = await fetch(this._managedBootstrap.sessionUrl, {
            method: "POST", credentials: "same-origin",
            headers: {"Content-Type": "application/json", ...this._requestHeaders()},
            body: JSON.stringify({sdp, provider: "openai", conversation_id}),
          });
          const result = await response.json();
          if (response.status === 409 && result.ok === false && Object.hasOwn(ADMISSION_MESSAGES,result.code)) {
            const error=new Error(ADMISSION_MESSAGES[result.code]);
            error.code=result.code;error.managedAdmissionRefused=true;
            throw error;
          }
          if (!response.ok || !result.ok || !result.session_id || !result.sdp) throw new Error("Voice session could not start");
          return result;
        })();
        this._managedCreate=creating;
        const result=await creating;
        if (generation !== this._generation) {
          await this._endManaged(result.session_id);
          throw new Error("Voice session ended while connecting");
        }
        return result;
      };
    }
    this._provider = bootstrap.provider;
    this._model = bootstrap.model;
    this._owned = this._pageResponses && bootstrap.page_responses === true;
    // A direct start (pageResponses) ended while its adapter still waits for the microphone permission (Gate 2 V S2): once
    // the request resolves, the adapter asks isCurrent(), stops every track it was given and connects nothing.
    if (this._pageResponses && !this._managedBootstrap) {
      bootstrap.isCurrent = () => generation === this._generation && !["ending", "ended"].includes(this._state);
    }
    this._sessionId = bootstrap.voice_session_id || (this._managedBootstrap ? null : `sess-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
    this._warningMinutes = bootstrap.warning_minutes;
    this._maxMinutes = bootstrap.max_minutes;
    // Optional, host-owned: what the bootstrap said about this session (MC shows its voice notices). Never the credential.
    this._onSessionInfo({provider: bootstrap.provider, model: bootstrap.model, voice_session_id: bootstrap.voice_session_id || null,
      voice_notices: Array.isArray(bootstrap.voice_notices) ? bootstrap.voice_notices.slice() : []});

    this._adapter = bootstrap.provider === "openai" ? new OpenAIWebRTCAdapter() : new XAIWebSocketAdapter();
    this._wireAdapterEvents();
    this._adapter.on("session_identity", event => { if (generation === this._generation) this._sessionId = event.session_id; });

    this._setState("connecting");
    this._adapter.prepareSession(bootstrap.session_config || {});
    try {
      await this._adapter.connect(bootstrap);
      if (generation === this._generation) this._adapter.start();
    } catch (error) {
      if (generation === this._generation) {
        this._onFatalError(admissionRefused(error)
          ? {reason:error.code,detail:ADMISSION_MESSAGES[error.code]}
          : {reason: "connection_failed", detail: "Voice could not connect"});
        this.endSession("connection_failed");
      }
    }
  }

  _wireAdapterEvents() {
    const adapter = this._adapter, generation = this._generation;
    const on = (name, handler) => adapter.on(name, event => {
      if (this._managedBootstrap && generation !== this._generation) return;
      handler(event);
    });
    // The page's own responses (Spec 165 §5.1), first, so a host asking inputOf() from any callback below sees them current.
    on("connected", () => this._reset());
    on("closed", () => this._reset(true));
    on("speech_started", () => { this._turn += 1; this._utterance = "speaking"; this._overtaken(); });
    on("speech_stopped", () => this._ended("stopped"));
    on("output_transcript", () => this._alive());
    on("assistant_started", () => this._alive());
    on("input_item", (evt) => this._committed(evt.item_id || null));
    on("input_transcript", (evt) => { if (this._interim(evt)) evt.completed = false; this._fresh = this._firstCompletion(evt); });
    on("response_started", (evt) => this._started(evt));
    on("response_done", (evt) => this._finished(evt));
    on("connected", () => {
      this._setState("active");
      this._startedAt = Date.now();
      this._startDurationWatch();
      if (this._openingInstruction && !this._managedBootstrap) {
        this._ask(null, (tag) => this._instruct(this._openingInstruction, tag));
      }
    });
    on("provider_phase", (evt) => {
      this._onInputState(evt.phase);
    });
    on("speech_started", () => this._onInputState("speech_started"));
    on("speech_stopped", () => this._onInputState("speech_stopped"));
    on("input_transcript", (evt) => {
      this._recordTranscriptEvent("user", evt);
      if (evt.completed) {
        this._onInputState("understood");
        this._onUserTurn(evt.text || "");
      }
    });
    on("output_transcript", (evt) => {
      this._recordTranscriptEvent("assistant", evt);
      if (evt.completed && evt.text?.trim()) {
        this._pendingAssistantTurns.push(evt.text.trim());
      }
    });
    on("playback_started", () => this._onPlayback());
    // How it ended (v0.3 §5.3, G3): drained, barged_in, cancelled, cleared or no_audio, or null when the adapter did not say.
    on("playback_drained", (evt) => { if (this._onPlaybackDrained) this._onPlaybackDrained(evt.response_id || null, evt.outcome || null); });
    on("assistant_stopped", () => {
      for (const text of this._pendingAssistantTurns.splice(0)) {
        this._onAssistantTurn(text);
      }
    });
    on("function_call", async (call) => {
      if (this._managedBootstrap) {
        this._onInputState("consulting"); // server sideband owns execution
        return;
      }
      if (this._once(call) || this._refused(call)) return;
      if (this._refuseCalls) {                                   // v3: no model action, on any provider
        this._outstanding.delete(call.call_id);
        try { this._adapter.sendFunctionOutput(call.call_id, NO_ACTIONS); } catch (_) { /* the transport is closed */ }
        this._onUnbound({ reason: "model_call", response_id: call.response_id || null, name: call.name || null });
        return;
      }
      if (!this._onFunctionCall) {
        this._sendResult(call, {
          error: "Function bridge unavailable",
        });
        return;
      }
      this._onInputState("consulting");
      try {
        const output = await this._onFunctionCall(call);
        this._sendResult(call, output);
      } catch (error) {
        this._sendResult(call, {
          error: error?.message || "COS function failed",
        });
      }
    });
    on("interrupted", () => {
      // Barge-in must leave conversation state consistent (Section 9) --
      // nothing else to do here, both adapters already stop/cancel
      // playback themselves before emitting this event.
    });
    on("usage", (usage) => {
      this._lastUsage = usage;
    });
    on("recoverable_error", (info) => {
      this._attemptReconnectOrEndVisibly(info);
    });
    on("fatal_error", (info) => {
      if (this._managedBootstrap) {
        this._onFatalError({reason: "provider_failure", detail: "Voice stopped"});
        this.endSession("fatal_error");
        return;
      }
      this._onFatalError(info);
      this._setState("ending");
      this._adapter?.end("fatal_error");
      this._finalizeAndEnd("fatal_error");
    });
    on("closed", () => {
      if (this._state !== "ending" && this._state !== "ended") {
        if (this._managedBootstrap) this.endSession("disconnect");
        else this._finalizeAndEnd("disconnect");
      }
    });
  }

  _recordTranscriptEvent(speaker, evt) {
    if (evt.provider_event_id) {
      if (this._seenEventIds.has(evt.provider_event_id)) return;
      this._seenEventIds.add(evt.provider_event_id);
    }
    const existing = this._items.get(evt.item_id);
    if (!existing) {
      this._items.set(evt.item_id, {
        speaker, text: evt.text, completed: evt.completed, firstSeq: this._seq++,
      });
    } else {
      existing.text = evt.is_delta ? existing.text + evt.text : evt.text;
      existing.completed = existing.completed || evt.completed;
    }
    // Optional host-owned display only. Never authorizes or persists an action.
    this._onTranscriptUpdate([...this._items.values()]
      .sort((a,b) => a.firstSeq-b.firstSeq)
      .map(t => ({speaker:t.speaker,text:t.text,completed:t.completed})));
  }

  _startDurationWatch() {
    this._durationTimer = setInterval(() => {
      const elapsedSeconds = (Date.now() - this._startedAt) / 1000;
      const elapsedMinutes = elapsedSeconds / 60;
      if (elapsedMinutes >= this._maxMinutes) {
        this._onStop(elapsedSeconds);
        this.endSession("duration_limit");
      } else if (elapsedMinutes >= this._warningMinutes && !this._warned) {
        this._warned = true;
        this._onWarning(elapsedSeconds);
      }
    }, 5000);
  }

  /** Idle re-engagement (Section 9) -- fixed, scene-neutral instruction. */
  continueNaturally() {
    if (!this._managedBootstrap && this._adapter) this._ask(null, (tag) => this._instruct(CONTINUATION_INSTRUCTION, tag));
  }

  /** The session's id (the server's voice_session_id when it issued one), or null. For a host page (Spec 165 §4.3);
   *  German and Portuguese do not call it. */
  sessionId() { return this._sessionId; }

  /** Whether the page creates every response of this session (the bootstrap's page_responses: OpenAI's direct path).
   *  For the direct page (v3); German and Portuguese do not call it. */
  ownsResponses() { return this._owned; }

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

  /** speak(), for a line that may stop being true before it is said (a holding line; review B N4): {withdraw()} or null
   *  when the transport refused it. withdraw() lets the line go while it still waits its turn (true), never once it is
   *  sent (false). German and Portuguese do not call it. */
  speakLine(instruction) {
    if (this._managedBootstrap || !this._adapter) return null;
    let entry = null;
    try { entry = this._ask(null, (tag) => this._instruct(instruction, tag)); } catch (_) { return null; }
    return { withdraw: () => { const i = this._queue.indexOf(entry); if (i < 0) return false; this._queue.splice(i, 1); return true; } };
  }

  /** The place of his item (the provider's id) in the transcript list the host is given, or null while its words have not
   *  appeared: what the host names it by (review B S3, N2: a commit or call not acted on). German and Portuguese do not call
   *  it. */
  placeOf(id) {
    const order = [...this._items.entries()].sort((a, b) => a[1].firstSeq - b[1].firstSeq);
    const index = order.findIndex(([key]) => key === id);
    return index < 0 ? null : index;
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

  // An interim transcript that says it is completed (LV2: xAI's .completed with status "in_progress", while he still speaks):
  // on the direct path it completes nothing, so the words the page posts, shows and sends with Check are his final ones,
  // never the first fragment. German and Portuguese pass no pageResponses, and hear every completion as before.
  _interim(evt) {
    return this._pageResponses && evt && evt.status === "in_progress";
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
    if (!this._owned) { this._ended("committed"); return; }
    for (const entry of this._queue.filter((e) => e.kind === "turn")) this._let(entry, "superseded");
    if (!item) this._onUnbound({ reason: "commit_without_item" });
    const respond = (word) => {
      const instruction = word && typeof word.instruction === "string" && word.instruction ? word.instruction : null;
      try {
        this._ask(item, (tag) => (instruction ? this._instruct(instruction, tag) : this._adapter.createResponse(tag)), null, "turn");
      } catch (_) { /* the transport is closed */ }
    };
    if (!this._respondFor || !item) { respond(null); return; }
    // v3: the host's word on this item first (its decision), then one response, or none. A newer turn of his meanwhile
    // supersedes it: it gets no response, exactly as a commit overtaken in the queue.
    const turn = this._turn, generation = this._generation;
    Promise.resolve().then(() => this._respondFor(item)).catch(() => null).then((word) => {
      if (generation !== this._generation || !this._owned || !this._adapter) return;
      if (turn !== this._turn) { this._onUnbound({ reason: "superseded", owner: item, kind: "turn" }); return; }
      if (word && word.skip === true) return;
      respond(word);
    });
  }

  // His turn ended (speech_stopped) or was committed. Where the provider creates its own replies (xAI), each turn of his
  // may still get one: counted, once per utterance, not flagged (review G-S1), so a page line's own start can never use up
  // the provider's reply still owed to "Yes.". A commit after its own speech_stopped is the same utterance; one with no
  // stop before it is counted too. Too many only makes a tracked wait end at its bound (never heard): it fails safe.
  _ended(how) {
    if (this._owned) return;
    if (how === "committed" ? this._utterance !== "stopped" : !["stopped", "committed"].includes(this._utterance)) {
      this._owed += 1;
      this._due = Date.now();
    }
    this._utterance = how;
    this._speaking = false;
    this._arm();
  }

  // LV3 (live probes): where the provider replies itself (xAI), his speech overtakes every reply under way, its own and a
  // page line's. One cut by barge-in ends "completed" with no output; one overtaken before its audio gets no response.done
  // at all. Neither is waited on: they stop counting as under way, and a page request in flight is let go (its reply keeps
  // the owner it started with: none). Nothing new is sent while he speaks.
  _overtaken() {
    if (!this._pageResponses || this._owned) return;
    this._speaking = true;
    this._active.clear();
    const f = this._inflight;
    if (f && f.started) { this._inflight = null; this._let(f, "overtaken"); }
    this._arm();
  }

  // LV3: where the provider replies itself (xAI), it silently drops a response.create sent while a reply of its own is under
  // way (3 of 3). So a page request is sent only when none is: not while one of its replies is under way (until its done,
  // his next speech, or RESPONSE_IDLE_MS without a sign of it), not while he speaks, and not in the REQUEST_TTL_MS after his
  // turn ends while its reply may still start. Each wait has its bound, so nothing waits on a done that may never come.
  _blocked(now = Date.now()) {
    if (!this._pageResponses || this._owned) return false;
    return this._holding || this._speaking || this._active.size > 0 || (this._due > 0 && now - this._due < REQUEST_TTL_MS);
  }

  // When _blocked's own bounds would next lift it (ms from now), or null when only an event can (his speech ending).
  _unblockIn(now) {
    if (this._speaking || this._holding) return null;
    const bounds = [...this._active.values()].map((a) => RESPONSE_IDLE_MS - (now - a.seen));
    if (this._due > 0) bounds.push(REQUEST_TTL_MS - (now - this._due));
    return bounds.length ? Math.max(0, Math.min(...bounds)) : null;
  }

  // A response this controller asks for: his committed item's (turn), a function result's follow-up (follow), or the
  // page's own line, the opening or the continuation (line). Every response.create the controller sends goes through here.
  // Without pageResponses it is sent at once, untagged, as before. With it, it is tagged and waits its turn: one page
  // request is in flight at a time, from its response.create until that response is done. Throws if the transport refuses
  // it at once.
  _ask(owner, send, done = null, kind = "line", call = null) {
    if (!this._pageResponses) { send(null); return { done }; }
    const entry = { kind, owner, turn: this._turn, tag: `minimoi-${++this._tags}`, send, done, at: 0, started: false, id: null,
      call };
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
    const entry = this._ask(r ? r.owner : null, (tag) => {
      if (tag) this._adapter.sendFunctionResult(call.call_id, output, tag); else this._adapter.sendFunctionResult(call.call_id, output);
    }, null, "follow", call ? { id: call.call_id, output } : null);
    return entry;
  }

  // LV4 (live probes, oa2): OpenAI spoke its own placeholder ("Waiting for the tool result, please politely inform the user
  // that this tool call is still running …") when the page made a reply for his next words while a call it had passed on had
  // no output yet. So before the page makes any other reply, every such call is answered, honestly, with PENDING (no reply
  // is asked for that output), and its real result, when it lands, is said as a page line, which answers none of his inputs
  // (§5.1's pending path). A follow-up that carries a call's result sends that output itself, as before.
  _settleCalls(entry) {
    for (const [id, held] of this._outstanding) {
      if (held.pending || (entry.call && id === entry.call.id)) continue;
      held.pending = true;
      try { this._adapter.sendFunctionOutput(id, PENDING); } catch (_) { /* the transport is closed */ }
    }
    if (!entry.call) return;
    const held = this._outstanding.get(entry.call.id);
    this._outstanding.delete(entry.call.id);
    if (!held || !held.pending || entry.kind !== "follow") return;
    const out = entry.call.output, said = out && typeof out.spoken === "string" ? out.spoken.trim() : "";
    entry.kind = "line"; entry.owner = null;
    entry.send = (tag) => this._instruct(LATE_LINE + (said || LATE_DONE), tag);
  }

  // The next page request goes once none is in flight. A request in flight past its bound is let go first. His committed
  // item's request goes only while no newer turn of his has begun; otherwise it is let go (he is speaking again: the commit
  // that ends it asks for its own response).
  _pump() {
    const f = this._inflight;
    if (f && this._overdue(f, Date.now())) {
      this._inflight = null;
      if (!f.started) this._lost = true;
      this._let(f, f.started ? "unfinished" : "never_started");
    }
    for (const [id, a] of this._active) if (Date.now() - a.seen >= RESPONSE_IDLE_MS) this._active.delete(id);
    if (this._due > 0 && Date.now() - this._due >= REQUEST_TTL_MS) this._due = 0;
    while (!this._inflight && this._queue.length && !this._blocked()) {
      const entry = this._queue.shift();
      if (entry.kind === "turn" && entry.turn !== this._turn) { this._let(entry, "superseded"); continue; }
      entry.turn = this._turn; entry.at = Date.now();
      this._inflight = entry;
      this._settleCalls(entry);                                  // LV4: no reply while a call passed on has no output
      try { entry.send(entry.tag); } catch (_) { this._inflight = null; entry.refused = true; this._let(entry, "refused"); }
    }
    this._arm();
  }

  // One timer for the request in flight, so a bound passes even when no provider event comes. Unreferenced where the
  // runtime allows it (node's tests); cleared when the request settles and when the transport closes.
  // Whether the request in flight is past its bound: not started in REQUEST_TTL_MS; started, and no output of any response
  // for RESPONSE_IDLE_MS (review B N5), or not finished in RESPONSE_TTL_MS.
  _overdue(f, now) {
    return f.started ? now - f.seen >= RESPONSE_IDLE_MS || now - f.at >= RESPONSE_TTL_MS : now - f.at >= REQUEST_TTL_MS;
  }

  // Some response's output (its transcript, its audio): the started request in flight is alive. One page request is in
  // flight at a time; output of a response that is not the page's only keeps the wait going, never names an owner.
  _alive() {
    const f = this._inflight;
    for (const a of this._active.values()) a.seen = Date.now();
    if (f && f.started) { f.seen = Date.now(); this._arm(); }
  }

  _arm() {
    clearTimeout(this._expiry);
    this._expiry = null;
    const f = this._inflight;
    const now = Date.now();
    const waits = !f && this._queue.length && this._blocked(now) ? this._unblockIn(now) : null;   // LV3: a page request waiting
    if (!f && waits === null) return;
    const left = !f ? waits : f.started ? Math.min(RESPONSE_IDLE_MS - (now - f.seen), RESPONSE_TTL_MS - (now - f.at))
      : REQUEST_TTL_MS - (now - f.at);
    this._expiry = setTimeout(() => { this._expiry = null; this._pump(); }, Math.max(0, left));
    if (this._expiry && typeof this._expiry.unref === "function") this._expiry.unref();
  }

  // A page request let go: its wait ends with no response, and it answers no one.
  _let(entry, reason) {
    const i = this._queue.indexOf(entry);
    if (i >= 0) this._queue.splice(i, 1);
    if (entry.done) { const done = entry.done; entry.done = null; done(null); }
    if (this._owned && entry.kind !== "line") this._onUnbound({ reason, owner: entry.owner || null, kind: entry.kind });
  }

  // The transport opened or closed: every page request is dropped. At close (End, the page leaving, the provider closing)
  // each one owned by his item is named (Gate 1 n3), as a request let go is, so the reveal can say it was not acted on.
  _reset(closing = false) {
    const pending = this._inflight ? [this._inflight, ...this._queue] : this._queue.slice();
    this._queue = []; this._inflight = null; this._lost = false; this._echoed = false; this._owed = 0; this._utterance = null;
    this._done = new Set(); this._calls = new Set(); this._active = new Map(); this._speaking = false; this._due = 0;
    this._outstanding = new Map();
    for (const entry of pending) if (entry.done) { const done = entry.done; entry.done = null; done(null); }
    if (closing && this._owned) for (const entry of pending) {
      if (entry.kind !== "line") this._onUnbound({ reason: "closed", owner: entry.owner || null, kind: entry.kind });
    }
    this._arm();
  }

  // A response started (response.created). It is the page's when it echoes the tag of the page request in flight, or, with
  // no tag, while that request has not started yet and no tag has been echoed in this session: it is then that request's,
  // owned as the request is. Any other response (none in flight, one too many, another tag, or no tag once the provider has
  // shown it echoes them) answers no one; where the page creates every response it is logged. Once a request has been let
  // go before it started, no untagged start can be told from that request's late one, for the rest of the session (review
  // B S1): it settles the request in flight but answers no one ("say it again"); a tagged one still binds exactly. Where the
  // provider creates its own replies (xAI), an untagged start while a turn of his may still get the provider's reply cannot
  // be told from that reply (review B S2): it settles the request in flight but resolves no wait (a tracked summary then
  // ends at its bound, never heard). The replies still owed are counted, never flagged (review G-S1): such a start leaves
  // the count as it is (it was that reply or the request's own, and the other can still come); an untagged start with no
  // request of the page's to be is one of them, and the count goes down by one.
  _started(evt) {
    if (!this._pageResponses) return;
    const id = evt.response_id || null;
    if (id && this._answers.has(id)) return;                     // its start was already seen
    this._holding = true;
    try { this._pump(); } finally { this._holding = false; }     // a request past its bound is let go first; none is sent now
    const f = this._inflight;
    if (this._onResponseStarted) {                               // v3: when it started, never who may act
      const mine = Boolean(f && !f.started && (evt.tag ? evt.tag === f.tag : !this._echoed));
      try { this._onResponseStarted({ response_id: id, owner: mine && f.owner ? f.owner : null, page: mine, kind: mine ? f.kind : null }); }
      catch (_) { /* a host's observer never stops the binding */ }
    }
    const theirs = !this._owned && !evt.tag && this._owed > 0;  // possibly the provider's own reply to a turn of his
    if (!this._owned && !evt.tag) this._due = 0;                // a reply has started: his turn's is no longer to come (LV3)
    if (evt.tag) this._echoed = true;
    if (!f || f.started || (evt.tag ? evt.tag !== f.tag : this._echoed)) {
      if (id) this._answers.set(id, null);
      if (theirs) this._owed -= 1;                                // one of the provider's replies still owed has started
      if (!this._owned && id) this._active.set(id, { seen: Date.now() });   // the provider's own reply, under way (LV3)
      if (this._owned) this._onUnbound({ reason: !f ? "unrequested" : f.started ? "one_too_many" : evt.tag ? "another_tag" : "untagged",
        response_id: id });
      this._arm();
      return;
    }
    const unsure = !evt.tag && this._lost;
    if (unsure && this._owned) this._onUnbound({ reason: "after_lost_request", response_id: id });
    f.started = true; f.id = id; f.at = f.seen = Date.now();
    if (id) this._answers.set(id, { owner: unsure ? null : f.owner, turn: f.turn, acts: null });
    this._arm();
    if (f.done) { const done = f.done; f.done = null; done(unsure || theirs ? null : id); }
  }

  // A response is done (response.done, before its function calls are passed on). Its calls act only if it completed and no
  // new turn of his began while it was in flight: one cancelled at his barge-in, cut short, or overtaken by his next words
  // is refused, never re-bound. The page request it answered is settled, and the next one goes.
  // A second response.done for a reply already done (live probes, LV1: OpenAI sends one, cancelled with turn_detected and
  // the same output, its function calls included, when he speaks while the finished reply's audio still plays) changes
  // nothing and dispatches nothing: every call it carries is dropped (_once), the same calls as already handled and a
  // call first seen there as never dispatched (Gate 2 Q N15). The adapter alone says what it means for playback
  // (barged_in, v0.3 G3).
  _finished(evt) {
    if (!this._pageResponses) return;
    const id = evt.response_id || null;
    this._repeat = id && this._done.has(id) ? id : null;           // the calls passed on right after this done are its own
    if (id && this._done.has(id)) return;
    if (id) this._done.add(id);
    const r = id ? this._answers.get(id) : null;
    if (r && r.acts === null) {
      r.acts = evt.status === "completed" && r.turn === this._turn;
      // Why it may not act (Gate 1 n2): his speech overtook it (a new turn of his, or cancelled at his barge-in), or it was cut
      // short (incomplete or failed) with no speech of his.
      if (!r.acts) r.why = r.turn !== this._turn || evt.status === "cancelled" ? "overtaken" : "cut_short";
    }
    const ended = id !== null && this._active.delete(id);           // the provider's own reply is no longer under way (LV3)
    const f = this._inflight;
    if (f && f.started && f.id === id) { this._inflight = null; this._pump(); }
    else if (ended) this._pump();
  }

  // Each function call is passed to the host once, by its call_id (LV1): the same call repeated in a later response.done is
  // dropped, so it is never dispatched twice, nor answered twice, nor followed up twice. Only where the page asks for its
  // own responses; German never gets here with a call.
  _once(call) {
    if (!this._pageResponses || !call || !call.call_id) return false;
    if (this._calls.has(call.call_id)) return true;
    this._calls.add(call.call_id);
    // A call first seen in a repeated response.done (OpenAI's second, cancelled at his speech; Gate 2 Q N15): never
    // dispatched, as LV1 says of that done. It is answered not run (his speech overtook that reply), with no reply asked.
    if (call.response_id && call.response_id === this._repeat) {
      const r = this._answers.get(call.response_id);
      try { this._adapter.sendFunctionOutput(call.call_id, SUPERSEDED); } catch (_) { /* the transport is closed */ }
      if (this._owned) this._onUnbound({ reason: "superseded_call", response_id: call.response_id, owner: (r && r.owner) || null, why: "overtaken" });
      return true;
    }
    this._outstanding.set(call.call_id, { pending: false });     // until its output is sent (LV4)
    return false;
  }

  // A call from a page response that may not act (cancelled, cut short or overtaken by his speech): nothing is dispatched,
  // and no follow-up is asked for it; the response that answers his newer words is the one that acts. Its function output
  // says it did not run, so the model's context is true (review B S3), and the host is told (onUnbound), so it can show
  // which of his turns was not acted on.
  _refused(call) {
    const r = this._pageResponses && call && call.response_id ? this._answers.get(call.response_id) : null;
    if (!r || r.acts !== false) return false;
    this._outstanding.delete(call.call_id);                     // answered here: not run
    const why = r.why === "cut_short" ? "cut_short" : "overtaken";
    try { this._adapter.sendFunctionOutput(call.call_id, why === "cut_short" ? CUT_SHORT : SUPERSEDED); } catch (_) { /* the transport is closed */ }
    this._onUnbound({ reason: "superseded_call", response_id: call.response_id, owner: r.owner || null, why });
    return true;
  }

  // The opt-in bootstrap bound (review W N3): {signal, clear, read(resp)}, or null when none was asked for (German and
  // Portuguese) or on the managed path. Past the bound the request, or the reading of its answer, is aborted and the start
  // fails as the bootstrap's network failure; read() then answers null (the failure already said).
  _bootstrapBound() {
    if (!this._bootstrapTimeoutMs || this._managedBootstrap || typeof AbortController !== "function") return null;
    const abort = new AbortController();
    let timer = setTimeout(() => { timer = null; abort.abort(); }, this._bootstrapTimeoutMs);
    if (timer && typeof timer.unref === "function") timer.unref();
    const clear = () => { if (timer) clearTimeout(timer); timer = null; };
    return {
      signal: abort.signal, clear,
      read: async (resp) => {
        try { return await resp.json(); }
        catch (err) {
          if (!abort.signal.aborted) throw err;
          this._setState("idle");
          this._onFatalError({ reason: "bootstrap_network_error", detail: "the server did not answer in time" });
          return null;
        } finally { clear(); }
      },
    };
  }

  mute() { this._adapter?.mute(); }
  unmute() { this._adapter?.unmute(); }

  _terminationUncertain() {
    this._onFatalError({reason:"termination_uncertain",detail:"Microphone stopped locally; provider termination is unconfirmed. Do not retry yet."});
    return {termination_confirmed:false};
  }

  _endManaged(sessionId) {
    if (!sessionId) return Promise.resolve(this._terminationUncertain());
    if(this._managedEndRequests.has(sessionId))return this._managedEndRequests.get(sessionId);
    const pending=(async()=>{
      const abort=new AbortController();
      let timer;
      try {
        // Cancellation alone does not bound a stalled browser fetch/body read.
        // Keep late network completion side-effect free: only the winning
        // receipt may update UI state or report transcript retention.
        const request=(async()=>{
          const response=await fetch(`${this._managedBootstrap.sessionUrl}/${encodeURIComponent(sessionId)}/end`,{
            method:"POST",credentials:"same-origin",keepalive:true,signal:abort.signal,
            headers:{"Content-Type":"application/json",...this._requestHeaders()},body:"{}",
          });
          return {response,receipt:await response.json()};
        })();
        const {response,receipt}=await Promise.race([request,new Promise((_,reject)=>{
          timer=setTimeout(()=>{abort.abort();reject(new Error("end timeout"));},15000);
        })]);
        if(!response.ok||receipt.termination_confirmed!==true)return this._terminationUncertain();
        if(receipt.transcript_retention==='failed')this._onFatalError({reason:'transcript_retention_failed',detail:'Voice ended, but some transcript turns could not be saved.'});
        return receipt;
      }catch(_){return this._terminationUncertain();}
      finally{clearTimeout(timer);}
    })();
    this._managedEndRequests.set(sessionId,pending);
    return pending;
  }

  endSession(reason) {
    if(this._managedBootstrap&&this._endingPromise)return this._endingPromise;
    if(this._state==="ending"||this._state==="ended")return;
    // Legacy German/PT flow remains synchronous and otherwise unchanged.
    if(!this._managedBootstrap){
      if(this._pageResponses)++this._generation;   // a direct-path end also stops a start still in flight (Gate 2 Q S3)
      this._setState("ending");this._adapter?.end(reason);this._finalizeAndEnd(reason);return;
    }
    const adapter=this._adapter,sessionId=this._sessionId,creating=this._managedCreate;
    ++this._generation;
    this._setState("ending");
    clearInterval(this._durationTimer);
    try{adapter?.stopCapture();}catch(_){/* Local transport is still closed below. */}
    this._endingPromise=Promise.resolve().then(async()=>{
      let receipt;
      try{
        let id=sessionId;
        if(!id&&creating){
          let timer;
          try{
            const result=await Promise.race([creating,new Promise((_,reject)=>{
              timer=setTimeout(()=>reject(new Error("create unresolved")),15000);
            })]);
            id=result.session_id;
          }catch(error){
            // The server refused this attempt before provider admission. This
            // says nothing about the earlier held operation's termination.
            if(admissionRefused(error))return {provider_attempted:false,admission_refused:error.code};
            return this._terminationUncertain();
          }
          finally{clearTimeout(timer);}
        }
        receipt=id?await this._endManaged(id):{termination_confirmed:true,provider_attempted:false};
        return receipt;
      }finally{
        // Never dereference this._adapter after awaiting: only this attempt's
        // transport may be torn down, and it closes even on uncertain End.
        try{adapter?.end(reason);}finally{this._finalizeAndEnd(reason);}
      }
    });
    return this._endingPromise;
  }

  _attemptReconnectOrEndVisibly(info) {
    if (this._managedBootstrap) {
      this.endSession("provider_control_error");
      this._onFatalError({reason: "provider_control_error", detail: "Voice stopped; start a new session after its outcome is known."});
      return;
    }
    // Section 11: one automatic reconnect attempt is allowed only before
    // meaningful state would be lost. Kept conservative for this release
    // -- one attempt, then end visibly and preserve the partial transcript
    // rather than loop.
    if (this._reconnectAttempted) {
      this._finalizeAndEnd("recoverable_error_no_retry");
      return;
    }
    this._reconnectAttempted = true;
    this._setState("reconnecting");
    // Actual reconnect (re-running startSession with the same params) is
    // the domain page's responsibility to trigger via onStateChange
    // observing "reconnecting" and offering the user "Reconnect" / "Start
    // a new session" (Section 11) -- the controller does not silently
    // retry on its own, matching "end visibly... offer Reconnect."
  }

  _finalizeAndEnd(reason) {
    if (this._state === "ended") return;
    clearInterval(this._durationTimer);
    const partial = reason !== "user_ended" && reason !== "normal_end";
    const turns = [...this._items.entries()]
      .sort((a, b) => a[1].firstSeq - b[1].firstSeq)
      .map(([, item]) => ({ speaker: item.speaker, text: item.text, completed: item.completed }));

    this._setState("ended");
    this._onFinalize({
      turns: this._includeTranscriptAfterEnd ? turns : [],
      partial,
      partial_reason: partial ? reason : null,
      provider: this._provider,
      model: this._model,
      session_id: this._sessionId,
      duration_seconds: this._startedAt ? (Date.now() - this._startedAt) / 1000 : 0,
      usage: this._lastUsage || null,
    });
  }
}
