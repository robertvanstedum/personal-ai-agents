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
import { OpenAIWebRTCAdapter } from "./adapters/openai-webrtc-adapter.js?v=20261015-managed-end1";
import { XAIWebSocketAdapter } from "./adapters/xai-websocket-adapter.js?v=20260725-transcript1";

const CONTINUATION_INSTRUCTION = "Continue naturally in character.";
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
    onFatalError, onFinalize, onUserTurn, onAssistantTurn, onFunctionCall, onTranscriptUpdate,
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, persona, scene, learner_name }),
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
    this._sessionId = this._managedBootstrap ? null : `sess-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    this._warningMinutes = bootstrap.warning_minutes;
    this._maxMinutes = bootstrap.max_minutes;

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
    on("connected", () => {
      this._setState("active");
      this._startedAt = Date.now();
      this._startDurationWatch();
      if (this._openingInstruction && !this._managedBootstrap) {
        this._adapter.sendContinuationInstruction(this._openingInstruction);
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
        this._adapter.sendFunctionResult(call.call_id, {
          error: "Function bridge unavailable",
        });
        return;
      }
      this._onInputState("consulting");
      try {
        const output = await this._onFunctionCall(call);
        this._adapter.sendFunctionResult(call.call_id, output);
      } catch (error) {
        this._adapter.sendFunctionResult(call.call_id, {
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
    if (!this._managedBootstrap) this._adapter?.sendContinuationInstruction(CONTINUATION_INSTRUCTION);
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
      const timer=setTimeout(()=>abort.abort(),15000);
      try {
        const response=await fetch(`${this._managedBootstrap.sessionUrl}/${encodeURIComponent(sessionId)}/end`,{
          method:"POST",credentials:"same-origin",keepalive:true,signal:abort.signal,
          headers:{"Content-Type":"application/json",...this._requestHeaders()},body:"{}",
        });
        const receipt=await response.json();
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
