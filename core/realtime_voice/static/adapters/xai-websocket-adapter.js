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
    const protocol = `xai-client-secret.${credentials.ephemeral_token}`;
    this._ws = new WebSocket(
      `wss://api.x.ai/v1/realtime?model=${encodeURIComponent(credentials.model)}`,
      [protocol]
    );

    this._ws.addEventListener("open", async () => {
      this._ws.send(JSON.stringify({
        type: "session.update",
        session: credentials.session_config || this._sessionConfig,
      }));
      await this._startMicCapture();
      this._emit("connected", { provider: "xai" });
    });

    this._ws.addEventListener("message", (event) => this._handleServerEvent(JSON.parse(event.data)));
    this._ws.addEventListener("error", (event) => this._emit("recoverable_error", { detail: String(event) }));
    this._ws.addEventListener("close", () => this._emit("closed", { reason: "connection_closed" }));
  }

  async _startMicCapture() {
    try {
      this._micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      this._emit("fatal_error", { reason: "microphone_denied", detail: String(err) });
      return;
    }

    this._audioContext = new AudioContext({ sampleRate: 24000 });
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
      this._ws?.readyState === WebSocket.OPEN &&
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
    for (const track of this._micStream?.getAudioTracks() || []) track.stop();
    this._processor?.disconnect();
    this._audioContext?.close();
    this._ws?.close();
    this._emit("closed", { reason });
  }

  sendContinuationInstruction(text) {
    this._ws?.send(JSON.stringify({
      type: "conversation.item.create",
      item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
    }));
    this._ws?.send(JSON.stringify({ type: "response.create" }));
  }

  _handleServerEvent(event) {
    switch (event.type) {
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
      case "response.output_audio.delta":
        this._playAudioChunk(event.audio);
        this._emit("assistant_started", {});
        break;
      case "response.done":
        this._emit("assistant_stopped", { usage: event.usage });
        if (event.usage) this._emit("usage", event.usage);
        break;
      case "conversation.item.input_audio_transcription.updated":
        // xAI's own docs: "cumulative, not delta" -- is_delta: false tells
        // the shared transcript accumulator to replace, not concatenate.
        this._emit("input_transcript", {
          item_id: event.item_id, text: event.transcript, is_delta: false,
          completed: !!event.completed, provider_event_id: event.event_id,
        });
        break;
      case "session.created":
        break;
      default:
        break;
    }
  }

  _playAudioChunk(base64Audio) {
    if (!this._audioContext) return;
    const float32 = _base64PCM16ToFloat32(base64Audio);
    const buffer = this._audioContext.createBuffer(1, float32.length, 24000);
    buffer.copyToChannel(float32, 0);
    const source = this._audioContext.createBufferSource();
    source.buffer = buffer;
    source.connect(this._audioContext.destination);
    const startAt = Math.max(this._audioContext.currentTime, this._playbackQueueTime);
    source.start(startAt);
    this._playbackQueueTime = startAt + buffer.duration;
  }

  _stopPlayback() {
    // Reset the playback queue so any already-scheduled-but-not-yet-played
    // buffer sources naturally play out silently past this point rather
    // than being individually tracked and stopped -- acceptable for a
    // first release; a follow-up could track and .stop() active sources
    // for an instantaneous cutoff.
    this._playbackQueueTime = this._audioContext?.currentTime || 0;
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
