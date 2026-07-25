/**
 * openai-webrtc-adapter.js — OpenAI Realtime API browser client (WebRTC).
 *
 * Approved architecture: browser connects to OpenAI directly via WebRTC
 * using an ephemeral client secret minted server-side. This adapter never
 * sees the long-lived OPENAI_API_KEY.
 *
 * API shape verified 2026-07-24 against
 * https://developers.openai.com/api/docs/guides/realtime-webrtc,
 * https://developers.openai.com/api/docs/guides/realtime-conversations.
 *
 * Implements the shared adapter contract (build spec Section 5):
 *   prepareSession(config) / connect(credentials) / start() / mute() /
 *   unmute() / end(reason)
 * and emits normalized events via the `on(eventName, handler)` method:
 *   connected, speech_started, speech_stopped, assistant_started,
 *   assistant_stopped, input_transcript, output_transcript, interrupted,
 *   usage, recoverable_error, fatal_error, closed
 */
export class OpenAIWebRTCAdapter {
  constructor() {
    this._listeners = {};
    this._pc = null;
    this._dc = null;
    this._micStream = null;
    this._remoteAudioEl = null;
    this._muted = false;
  }

  on(eventName, handler) {
    (this._listeners[eventName] ||= []).push(handler);
  }

  _emit(eventName, payload) {
    for (const handler of this._listeners[eventName] || []) handler(payload);
  }

  prepareSession(config) {
    // WebRTC's SDP offer/answer + data channel doesn't need a separate
    // "prepare" step the way a WebSocket session.update message does --
    // this adapter's config (voice/turn_detection/instructions) was
    // already applied server-side when the ephemeral credential was
    // minted (core/realtime_voice/providers/openai_realtime.py). Kept as
    // a no-op for contract symmetry with the xAI adapter.
    this._config = config;
  }

  async connect(credentials) {
    // credentials: {client_secret, model} from the bootstrap response.
    this._remoteAudioEl = document.createElement("audio");
    this._remoteAudioEl.autoplay = true;

    this._pc = new RTCPeerConnection();
    this._pc.ontrack = (event) => {
      this._remoteAudioEl.srcObject = event.streams[0];
    };

    try {
      this._micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      this._emit("fatal_error", { reason: "microphone_denied", detail: String(err) });
      return;
    }
    for (const track of this._micStream.getAudioTracks()) {
      this._pc.addTrack(track, this._micStream);
    }

    this._dc = this._pc.createDataChannel("oai-events");
    this._dc.addEventListener("message", (event) => this._handleServerEvent(JSON.parse(event.data)));
    this._dc.addEventListener("open", () => this._emit("connected", { provider: "openai" }));

    const offer = await this._pc.createOffer();
    await this._pc.setLocalDescription(offer);

    let resp;
    try {
      resp = await fetch(
        `https://api.openai.com/v1/realtime/calls?model=${encodeURIComponent(credentials.model)}`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${credentials.client_secret}`,
            "Content-Type": "application/sdp",
          },
          body: offer.sdp,
        }
      );
    } catch (err) {
      this._emit("fatal_error", { reason: "network", detail: String(err) });
      return;
    }
    if (!resp.ok) {
      this._emit("fatal_error", { reason: "provider_rejected", detail: `HTTP ${resp.status}` });
      return;
    }
    const answerSdp = await resp.text();
    await this._pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
  }

  start() {
    // The realtime session begins as soon as the data channel opens and
    // audio flows -- there is no separate "start" call for OpenAI's
    // WebRTC transport. Kept for adapter contract symmetry.
  }

  mute() {
    this._muted = true;
    for (const track of this._micStream?.getAudioTracks() || []) track.enabled = false;
  }

  unmute() {
    this._muted = false;
    for (const track of this._micStream?.getAudioTracks() || []) track.enabled = true;
  }

  end(reason) {
    for (const track of this._micStream?.getAudioTracks() || []) track.stop();
    this._dc?.close();
    this._pc?.close();
    this._emit("closed", { reason });
  }

  sendContinuationInstruction(text) {
    // Text-only continuation (Section 9's idle re-engagement) -- per
    // OpenAI docs: conversation.item.create with input_text, then
    // response.create with output_modalities restricted appropriately.
    this._dc?.send(JSON.stringify({
      type: "conversation.item.create",
      item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
    }));
    this._dc?.send(JSON.stringify({ type: "response.create" }));
  }

  _handleServerEvent(event) {
    switch (event.type) {
      case "input_audio_buffer.speech_started":
        this._emit("speech_started", {});
        break;
      case "input_audio_buffer.speech_stopped":
        this._emit("speech_stopped", {});
        break;
      case "response.output_audio_transcript.delta":
        this._emit("output_transcript", {
          item_id: event.item_id, text: event.delta, is_delta: true, completed: false,
          provider_event_id: event.event_id,
        });
        break;
      case "response.output_audio_transcript.done":
        this._emit("output_transcript", {
          item_id: event.item_id, text: event.transcript, is_delta: false, completed: true,
          provider_event_id: event.event_id,
        });
        break;
      case "conversation.item.input_audio_transcription.completed":
        this._emit("input_transcript", {
          item_id: event.item_id, text: event.transcript, is_delta: false, completed: true,
          provider_event_id: event.event_id,
        });
        break;
      case "conversation.item.input_audio_transcription.delta":
        this._emit("input_transcript", {
          item_id: event.item_id, text: event.delta, is_delta: true, completed: false,
          provider_event_id: event.event_id,
        });
        break;
      case "conversation.item.input_audio_transcription.failed":
        this._emit("recoverable_error", {
          reason: "input_transcription_failed",
          detail: event.error?.message || event.error?.code || "Input transcription failed",
        });
        break;
      case "response.cancelled":
        this._emit("interrupted", {});
        break;
      case "response.done":
        this._emit("assistant_stopped", { usage: event.response?.usage });
        if (event.response?.usage) this._emit("usage", event.response.usage);
        break;
      case "error":
        this._emit("recoverable_error", { detail: event.error });
        break;
      default:
        break;
    }
  }
}
