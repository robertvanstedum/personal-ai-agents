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
// Both adapters changed for the playback events and response attribution, and OpenAI's again for the response tag on every
// request: new keys so browsers fetch them (Spec 165 §4.3, §5.3; review FR N3, V R2-M1).
import { OpenAIWebRTCAdapter } from "./adapters/openai-webrtc-adapter.js?v=20261028-bind1";
import { XAIWebSocketAdapter } from "./adapters/xai-websocket-adapter.js?v=20261028-attrib1";

const CONTINUATION_INSTRUCTION = "Continue naturally in character.";
// Response attribution (Spec 165 §5.1; review V R2-M1): a response this controller asked for that has not started in this
// long is taken as never coming; his ended utterance with no input item in this long is taken as noise (V note B).
const REQUEST_TTL_MS = 10000;
const MARK_TTL_MS = 3000;
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
    onSessionInfo, onPlaybackDrained = null, trackResponses = false,
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
    this._trackResponses = trackResponses;           // optional: tag each response this controller asks for (V R2-M1)
    this._inputs = [];               // owner input items (provider ids), in the order they became known (Spec 165 §5.1)
    this._utterance = null;          // inputs known when his current utterance began (speech_started), until it ends
    this._ended = null;              // his utterance that ended and awaits its response: {index, seq, at, committed}
    this._commits = false;           // the adapter reports committed input (OpenAI's input_audio_buffer.committed)
    this._requests = [];             // responses this controller asked for, not yet started, in order: {owner, tag, seq, at, done}
    this._seq = 0;                   // the order in which ended utterances and requests became known
    this._echoed = false;            // the adapter has reported a request's tag back (OpenAI's response.metadata)
    this._answers = new Map();       // response id -> the owner input it answers: a provider id, null, or {index} (pending)
    this._tags = 0;
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
      });
    } catch (err) {
      this._setState("idle");
      this._onFatalError({ reason: "bootstrap_network_error", detail: String(err) });
      return;
    }
    if (!resp.ok) {
      this._setState("idle");
      const body = await resp.json().catch(() => ({}));
      this._onFatalError({ reason: "bootstrap_rejected", status: resp.status, detail: body.error });
      return;
    }
    const bootstrap = await resp.json();
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
    // Response attribution (Spec 165 §5.1), first, so a host asking inputOf() from any callback below sees it current.
    on("connected", () => { this._utterance = this._ended = null; this._drop(this._requests.slice()); this._echoed = this._commits = false; });
    on("speech_started", () => { if (this._utterance === null) this._utterance = this._inputs.length; });
    on("speech_stopped", () => this._heardEnd());
    on("input_item", (evt) => this._committed(evt.item_id));
    on("input_transcript", (evt) => this._sawInput(evt.item_id));
    on("response_started", (evt) => this._started(evt));
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
    on("playback_drained", (evt) => { if (this._onPlaybackDrained) this._onPlaybackDrained(evt.response_id || null); });
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
   *    whether its transcript came before or after his speech ended, or committed or not; with none yet, to the first one
   *    to appear. Where the adapter reports committed input (OpenAI), only a committed utterance has a response. An ended
   *    utterance with no input item for MARK_TTL_MS is noise and binds nothing;
   *  - anything else (a response with no ended utterance and no request of this controller's) answers none of his inputs.
   *  Which is which: by the tag a request carries when the adapter reports it back (OpenAI's response.metadata, once seen
   *  in the session: then an untagged response is the provider's); else by order, the requests and his ended utterances
   *  starting their responses in the order they became known. The call is bound to its response by response_id. {state: "bound", index, text} once that item's transcript has completed
   *  (index: its place in the transcript list the host is given), {state: "pending"} until then, or null when the call is
   *  bound to no owner input. Never the latest input by default. German and Portuguese do not call it. */
  inputOf(call) {
    let owner = call && call.response_id ? this._answers.get(call.response_id) : undefined;
    if (owner === undefined || owner === null) return null;
    if (typeof owner === "object") owner = this._inputs[owner.index];       // the first input of his utterance, once known
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

  _sawInput(id) {
    if (id && !this._inputs.includes(id)) this._inputs.push(id);
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
    if (this._ended === null && id && !this._inputs.includes(id)) this._ended = { index: this._inputs.length, seq: ++this._seq, at: Date.now() };
    if (this._ended) this._ended.committed = true;
    this._sawInput(id);
  }

  // A response this controller asks for (a function result's follow-up, the opening or continuation instruction, or the
  // page's own), in order, before it is sent; tagged when the host tracks responses (trackResponses). Every response.create
  // the controller sends goes through here. Nothing is recorded if the transport refuses it.
  _ask(owner, send, done = null) {
    const entry = { owner, tag: this._trackResponses ? `minimoi-${++this._tags}` : null, seq: ++this._seq, at: Date.now(), done };
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
    } else if (!this._echoed && this._requests.length && (!his || this._requests[0].seq < his.seq)) {
      mine = this._requests[0];
    }
    let owner = null;
    if (mine) {
      owner = mine.owner;
      this._drop([mine], id);
    } else if (his) {
      const known = this._utterance === null ? this._inputs.length : Math.min(this._utterance, this._inputs.length);
      this._ended = null;
      owner = known > his.index ? this._inputs[known - 1] : { index: his.index };
    }
    if (id) this._answers.set(id, owner);
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
