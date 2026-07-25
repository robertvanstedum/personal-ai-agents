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
import { OpenAIWebRTCAdapter } from "./adapters/openai-webrtc-adapter.js?v=20260725-transcript1";
import { XAIWebSocketAdapter } from "./adapters/xai-websocket-adapter.js?v=20260725-transcript1";

const CONTINUATION_INSTRUCTION = "Continue naturally in character.";
const OPENING_INSTRUCTION =
  "Begin in character with one short, natural greeting and one brief question " +
  "inviting the learner to say what they need. Do not give directions, suggest " +
  "a destination, or advance the scenario before hearing the learner.";

export class RealtimeVoiceController {
  constructor({
    bootstrapUrl, onStateChange, onInputState, onWarning, onStop,
    onFatalError, onFinalize,
  }) {
    this._bootstrapUrl = bootstrapUrl;
    this._onStateChange = onStateChange || (() => {});
    this._onInputState = onInputState || (() => {});
    this._onWarning = onWarning || (() => {});
    this._onStop = onStop || (() => {});
    this._onFatalError = onFatalError || (() => {});
    this._onFinalize = onFinalize || (() => {});

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

  async startSession({ provider, persona, scene, learner_name }) {
    this._setState("requesting_mic");
    let resp;
    try {
      resp = await fetch(this._bootstrapUrl, {
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
    this._provider = bootstrap.provider;
    this._model = bootstrap.model;
    this._sessionId = `sess-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    this._warningMinutes = bootstrap.warning_minutes;
    this._maxMinutes = bootstrap.max_minutes;

    this._adapter = bootstrap.provider === "openai" ? new OpenAIWebRTCAdapter() : new XAIWebSocketAdapter();
    this._wireAdapterEvents();

    this._setState("connecting");
    this._adapter.prepareSession(bootstrap.session_config || {});
    await this._adapter.connect(bootstrap);
    this._adapter.start();
  }

  _wireAdapterEvents() {
    this._adapter.on("connected", () => {
      this._setState("active");
      this._startedAt = Date.now();
      this._startDurationWatch();
      this._adapter.sendContinuationInstruction(OPENING_INSTRUCTION);
    });
    this._adapter.on("provider_phase", (evt) => {
      this._onInputState(evt.phase);
    });
    this._adapter.on("speech_started", () => this._onInputState("speech_started"));
    this._adapter.on("speech_stopped", () => this._onInputState("speech_stopped"));
    this._adapter.on("input_transcript", (evt) => {
      this._recordTranscriptEvent("user", evt);
      if (evt.completed) this._onInputState("understood");
    });
    this._adapter.on("output_transcript", (evt) => this._recordTranscriptEvent("assistant", evt));
    this._adapter.on("interrupted", () => {
      // Barge-in must leave conversation state consistent (Section 9) --
      // nothing else to do here, both adapters already stop/cancel
      // playback themselves before emitting this event.
    });
    this._adapter.on("usage", (usage) => {
      this._lastUsage = usage;
    });
    this._adapter.on("recoverable_error", (info) => {
      this._attemptReconnectOrEndVisibly(info);
    });
    this._adapter.on("fatal_error", (info) => {
      this._onFatalError(info);
      this._setState("ending");
      this._adapter?.end("fatal_error");
      this._finalizeAndEnd("fatal_error");
    });
    this._adapter.on("closed", () => {
      if (this._state !== "ending" && this._state !== "ended") {
        this._finalizeAndEnd("disconnect");
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
      return;
    }
    existing.text = evt.is_delta ? existing.text + evt.text : evt.text;
    existing.completed = existing.completed || evt.completed;
    // Deliberately never surfaced to the UI while active (Section 10) --
    // this method only accumulates in memory.
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
    this._adapter?.sendContinuationInstruction(CONTINUATION_INSTRUCTION);
  }

  mute() { this._adapter?.mute(); }
  unmute() { this._adapter?.unmute(); }

  endSession(reason) {
    if (this._state === "ending" || this._state === "ended") return;
    this._setState("ending");
    this._adapter?.end(reason);
    this._finalizeAndEnd(reason);
  }

  _attemptReconnectOrEndVisibly(info) {
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
      turns,
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
