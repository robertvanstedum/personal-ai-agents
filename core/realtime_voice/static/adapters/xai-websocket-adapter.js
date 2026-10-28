/**
 * xai-websocket-adapter.js — xAI Grok Voice Agent API browser client
 * (WebSocket).
 *
 * Approved architecture: browser connects to xAI directly via WebSocket
 * using an ephemeral token minted server-side. No mini-moi WebRTC relay
 * for xAI in this release (their published relay example is explicitly
 * not production-ready). This adapter never sees the long-lived
 * XAI_API_KEY.
 *
 * Unlike OpenAI's WebRTC transport, xAI's WebSocket transport does not
 * auto-truncate assistant audio on interruption -- this adapter must
 * manage that itself (stop playback immediately, cancel the in-flight
 * response) per build spec Section 9's barge-in requirement.
 *
 * API shape verified 2026-07-24 against
 * https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech,
 * https://docs.x.ai/developers/model-capabilities/audio/ephemeral-tokens.
 *
 * Implements the same shared adapter contract as OpenAIWebRTCAdapter.
 */
export class XAIWebSocketAdapter {
  constructor() {
    this._listeners = {};
    this._ws = null;
    this._audioContext = null;
    this._micStream = null;
    this._processor = null;
    this._muted = false;
    this._playbackQueueTime = 0;
    this._connected = false;
    this._connectTimer = null;
    this._sessionReady = false;
    this._micReady = false;
    this._connectedEmitted = false;
    this._playbackSources = new Set();
    this._responses = new Map();     // response id -> {sources, done, audio, status}: which response each chunk of audio belongs to
  }

  on(eventName, handler) {
    (this._listeners[eventName] ||= []).push(handler);
  }

  _emit(eventName, payload) {
    for (const handler of this._listeners[eventName] || []) handler(payload);
  }

  prepareSession(config) {
    this._sessionConfig = config; // {voice, instructions, turn_detection, audio} -- built server-side
  }

  async connect(credentials) {
    // credentials: {ephemeral_token, model, session_config} from the
    // bootstrap response. Browsers cannot set WebSocket headers, so the
    // token travels as a Sec-WebSocket-Protocol entry
    // ("xai-client-secret." prefix), per xAI's documented browser pattern.
    let micError = null;
    const micPromise = this._startMicCapture().catch((err) => {
      micError = err;
    });
    micPromise.then(() => {
      if (!micError) this._micReady = true;
      this._maybeMarkConnected();
    });
    const protocol = `xai-client-secret.${credentials.ephemeral_token}`;
    this._ws = new WebSocket(
      `wss://api.x.ai/v1/realtime?model=${encodeURIComponent(credentials.model)}`,
      [protocol]
    );

    this._ws.addEventListener("open", () => {
      this._ws.send(JSON.stringify({
        type: "session.update",
        session: credentials.session_config || this._sessionConfig,
      }));
      micPromise.then(() => {
        if (!micError) return;
        this._emit("fatal_error", {
          reason: "microphone_unavailable",
          detail: String(micError),
        });
        this._ws.close();
      });
    });

    this._ws.addEventListener("message", (event) => this._handleServerEvent(JSON.parse(event.data)));
    this._ws.addEventListener("error", (event) => {
      const info = { reason: "websocket_error", detail: String(event) };
      this._emit(this._connected ? "recoverable_error" : "fatal_error", info);
    });
    this._ws.addEventListener("close", () => this._emit("closed", { reason: "connection_closed" }));
    this._connectTimer = setTimeout(() => {
      if (this._connected) return;
      this._emit("fatal_error", {
        reason: "connection_timeout",
        detail: "Grok Voice did not connect within 12 seconds.",
      });
      this._stopMic();
      this._ws.close();
    }, 12000);
  }

  _maybeMarkConnected() {
    if (
      this._connectedEmitted || !this._sessionReady || !this._micReady ||
      this._ws?.readyState !== WebSocket.OPEN
    ) return;
    clearTimeout(this._connectTimer);
    this._connected = true;
    this._connectedEmitted = true;
    this._emit("connected", { provider: "xai" });
  }

  async _startMicCapture() {
    try {
      this._micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      throw new Error(`microphone_denied: ${err}`);
    }

    this._audioContext = new AudioContext({ sampleRate: 24000 });
    await this._audioContext.resume();
    if (this._audioContext.state !== "running") {
      throw new Error(`audio_context_${this._audioContext.state}`);
    }
    const source = this._audioContext.createMediaStreamSource(this._micStream);
    // ScriptProcessorNode is deprecated in favor of AudioWorklet; kept here
    // for a first working version per the API docs' own example -- a
    // follow-up should migrate to AudioWorkletNode for lower latency and
    // to avoid the deprecation warning, not required for this release's
    // acceptance criteria.
    this._processor = this._audioContext.createScriptProcessor(4096, 1, 1);
    this._processor.onaudioprocess = (event) => {
      if (this._muted) return;
      const input = event.inputBuffer.getChannelData(0);
      const pcm16 = _float32ToPCM16(input);
      const base64 = _arrayBufferToBase64(pcm16.buffer);
      this._sessionReady && this._ws?.readyState === WebSocket.OPEN &&
        this._ws.send(JSON.stringify({ type: "input_audio_buffer.append", audio: base64 }));
    };
    source.connect(this._processor);
    this._processor.connect(this._audioContext.destination);
  }

  start() {
    // Session begins as soon as the WebSocket opens and session.update is
    // acknowledged -- no separate start call. Kept for contract symmetry.
  }

  mute() {
    this._muted = true;
  }

  unmute() {
    this._muted = false;
  }

  end(reason) {
    clearTimeout(this._connectTimer);
    this._stopMic();
    this._ws?.close();
    this._emit("closed", { reason });
  }

  _stopMic() {
    for (const track of this._micStream?.getAudioTracks() || []) track.stop();
    this._processor?.disconnect();
    this._audioContext?.close();
    this._micStream = null;
    this._processor = null;
    this._audioContext = null;
  }

  // Spec 165 §5.3: playback_drained {response_id, outcome} once a response is done and every chunk of its audio has played
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

  sendContinuationInstruction(text) {
    this._ws?.send(JSON.stringify({
      type: "conversation.item.create",
      item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
    }));
    this._ws?.send(JSON.stringify({ type: "response.create" }));
  }

  sendFunctionResult(callId, output) {
    this._ws?.send(JSON.stringify({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        output: typeof output === "string" ? output : JSON.stringify(output),
      },
    }));
    this._ws?.send(JSON.stringify({ type: "response.create" }));
  }

  _handleServerEvent(event) {
    switch (event.type) {
      case "session.updated":
        this._sessionReady = true;
        this._emit("provider_phase", { phase: "session_ready" });
        this._maybeMarkConnected();
        break;
      case "input_audio_buffer.speech_started":
        // Manual barge-in: xAI's WebSocket transport does not
        // auto-truncate playback the way OpenAI's WebRTC transport does
        // (Section 9/11) -- stop immediately and let the server's
        // response.cancelled (if sent) or a fresh response supersede it.
        this._stopPlayback();
        this._emit("speech_started", {});
        this._emit("interrupted", {});
        break;
      case "input_audio_buffer.speech_stopped":
        this._emit("speech_stopped", {});
        break;
      case "response.created":
        this._responseId = event.response?.id || null;
        this._emit("response_started", { response_id: this._responseId });
        break;
      // Spec 165 §5.1: the owner input item a response answers (if xAI sends this OpenAI-shaped event; not verified).
      case "input_audio_buffer.committed":
        this._emit("input_item", { item_id: event.item_id || null });
        break;
      case "response.output_audio.delta":
        this._playAudioChunk(event.delta || event.audio, event.response_id);
        this._emit("assistant_started", {});
        break;
      case "response.done":
        for (const item of event.response?.output || []) {
          if (item.type === "function_call") {
            this._emit("function_call", {
              call_id: item.call_id,
              name: item.name,
              arguments: item.arguments,
              response_id: event.response?.id || this._responseId || null,
            });
          }
        }
        {
          const [key, r] = this._response(event.response?.id);
          r.done = true;
          // Cut short: cancelled, incomplete or failed is never heard to the end (review L N9). Only a response whose status
          // says completed is drained once its audio has played; with no status (xAI's status field is not verified) no
          // outcome is claimed (review W N1).
          r.status = event.response?.status || null;
          this._drainedIf(key);
        }
        this._emit("assistant_stopped", { usage: event.response?.usage || event.usage });
        if (event.response?.usage || event.usage) {
          this._emit("usage", event.response?.usage || event.usage);
        }
        break;
      case "conversation.item.input_audio_transcription.updated":
        // xAI's own docs: "cumulative, not delta" -- is_delta: false tells
        // the shared transcript accumulator to replace, not concatenate.
        this._emit("input_transcript", {
          item_id: event.item_id, text: event.transcript, is_delta: false,
          completed: !!event.completed, provider_event_id: event.event_id,
        });
        break;
      case "conversation.item.input_audio_transcription.completed":
        this._emit("input_transcript", {
          item_id: event.item_id, text: event.transcript, is_delta: false,
          completed: true, provider_event_id: event.event_id,
        });
        break;
      case "response.output_audio_transcript.delta":
        this._emit("output_transcript", {
          item_id: event.item_id, text: event.delta, is_delta: true,
          completed: false, provider_event_id: event.event_id,
        });
        break;
      case "response.output_audio_transcript.done":
        this._emit("output_transcript", {
          item_id: event.item_id, text: event.transcript, is_delta: false,
          completed: true, provider_event_id: event.event_id,
        });
        break;
      case "error": {
        const detail = event.error?.message || event.error?.code || JSON.stringify(event.error || event);
        this._emit(this._connected ? "recoverable_error" : "fatal_error", {
          reason: "provider_error",
          detail,
        });
        break;
      }
      case "session.created":
        break;
      default:
        break;
    }
  }

  _playAudioChunk(base64Audio, responseId) {
    if (!this._audioContext || !base64Audio) return;
    const float32 = _base64PCM16ToFloat32(base64Audio);
    const buffer = this._audioContext.createBuffer(1, float32.length, 24000);
    buffer.copyToChannel(float32, 0);
    const source = this._audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(this._audioContext.destination);
    this._playbackSources.add(source);
    if (this._playbackSources.size === 1) this._emit("playback_started", {});
    const [key, owner] = this._response(responseId);
    owner.sources += 1;
    owner.audio = true;
    source.addEventListener("ended", () => {
      this._playbackSources.delete(source);
      if (owner.sources > 0) { owner.sources -= 1; this._drainedIf(key); }
    }, { once: true });
    const startAt = Math.max(this._audioContext.currentTime, this._playbackQueueTime);
    source.start(startAt);
    this._playbackQueueTime = startAt + buffer.duration;
  }

  _stopPlayback() {
    const cut = [...this._responses].filter(([, r]) => r.sources > 0).map(([key]) => key);
    for (const source of this._playbackSources) {
      try { source.stop(); } catch (_) {}
    }
    this._playbackSources.clear();
    this._playbackQueueTime = this._audioContext?.currentTime || 0;
    for (const key of cut) {                                // stopped at barge-in: what was queued is not going to play
      const r = this._responses.get(key);
      if (r) { r.sources = 0; r.done = true; this._drainedIf(key, "barged_in"); }
    }
  }
}

function _float32ToPCM16(float32Array) {
  const pcm16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return pcm16;
}

function _arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function _base64PCM16ToFloat32(base64String) {
  const bytes = Uint8Array.from(atob(base64String), (c) => c.charCodeAt(0));
  const pcm16 = new Int16Array(bytes.buffer);
  const float32 = new Float32Array(pcm16.length);
  for (let i = 0; i < pcm16.length; i++) float32[i] = pcm16[i] / 32768.0;
  return float32;
}
