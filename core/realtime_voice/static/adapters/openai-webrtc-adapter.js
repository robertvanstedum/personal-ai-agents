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
    this._audible = new Set();       // responses whose audio the provider says is playing (output_audio_buffer.started)
    this._ended = new Set();         // responses whose response.done was seen (LV1: OpenAI may send a second one)
    this._played = new Set();        // responses whose playback end was reported: each once (LV1)
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
      this._remoteAudioEl.addEventListener("playing", () => {
        this._emit("playback_started", {});
      }, { once: true });
    };

    try {
      this._micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      this._emit("fatal_error", { reason: "microphone_denied", detail: String(err) });
      return;
    }
    if (credentials.isCurrent && !credentials.isCurrent()) {
      for (const track of this._micStream.getTracks()) track.stop();
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
      // The ephemeral credential is already bound to the configured session
      // and model. The GA WebRTC contract accepts the SDP at this URL without
      // a model query parameter.
      if (credentials.exchangeSdp) {
        if (credentials.isCurrent && !credentials.isCurrent()) return;
        const answer = await credentials.exchangeSdp(offer.sdp);
        this._emit("session_identity", {session_id: answer.session_id});
        if (credentials.isCurrent && !credentials.isCurrent()) {
          this.end("stale_connection");
          return;
        }
        await this._pc.setRemoteDescription({type: "answer", sdp: answer.sdp});
        return;
      }
      resp = await fetch(
        "https://api.openai.com/v1/realtime/calls",
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
      // The managed controller owns the fixed allowlisted admission message.
      // Do not erase that refusal with a generic transport fatal event first.
      if (credentials.exchangeSdp && err?.managedAdmissionRefused === true) throw err;
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

  stopCapture() {
    for (const track of this._micStream?.getAudioTracks() || []) track.stop();
    // Stop local capture/playback immediately, retaining the transport until
    // the server has attempted its single provider hangup.
    this._remoteAudioEl?.pause?.();
  }

  end(reason) {
    this.stopCapture();
    this._dc?.close();
    this._pc?.close();
    this._emit("closed", { reason });
  }

  sendContinuationInstruction(text, tag, role) {
    // Text-only continuation (Section 9's idle re-engagement) -- per
    // OpenAI docs: conversation.item.create with input_text, then
    // response.create with output_modalities restricted appropriately.
    // role "system": the direct page's own lines (the controller's _instruct where the page creates every response; the
    // v3 live check, fault 1). Any other value, or none, is "user", the bytes as before (German and Portuguese).
    this._dc?.send(JSON.stringify({
      type: "conversation.item.create",
      item: { type: "message", role: role === "system" ? "system" : "user", content: [{ type: "input_text", text }] },
    }));
    this._dc?.send(JSON.stringify(_responseCreate(tag)));
  }

  // Spec 165 §5.1 as amended (C2 by construction): the page's own response for a committed item of his, tagged. Only a host
  // that creates every response calls it (the session's turn detection has create_response false); German never does.
  createResponse(tag) {
    this._dc?.send(JSON.stringify(_responseCreate(tag)));
  }

  // Spec 165 §5.1 (C2 by construction; review B S3): a function output that asks for no response, for a call that may not
  // act. Only a host that creates every response calls it; German never does.
  sendFunctionOutput(callId, output) {
    this._dc?.send(JSON.stringify(_functionOutput(callId, output)));
  }

  sendFunctionResult(callId, output, tag) {
    this._dc?.send(JSON.stringify({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        output: typeof output === "string" ? output : JSON.stringify(output),
      },
    }));
    this._dc?.send(JSON.stringify(_responseCreate(tag)));
  }

  _handleServerEvent(event) {
    switch (event.type) {
      case "input_audio_buffer.speech_started":
        // Spec 165 §5.1 (review W M1): the item this speech will be committed as, when the provider names it.
        this._emit("speech_started", { item_id: event.item_id || null });
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
      // Spec 165 §5.3: response_started on response.created; playback_drained {response_id, outcome} when the provider says
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
        // The v3 fix round (review M2): the response's first audio, for a host that counts a question from it. Nothing
        // listens unless a host passes onAudioStarted to the controller; German and Portuguese do not.
        this._emit("audio_started", { response_id: event.response_id || null });
        break;
      case "output_audio_buffer.stopped":
      case "output_audio_buffer.cleared":
        for (const id of event.response_id ? [event.response_id] : [...this._audible]) {
          this._audible.delete(id);
          if (this._played.has(id)) continue;
          this._played.add(id);
          this._emit("playback_drained", { response_id: id, outcome: event.type === "output_audio_buffer.cleared" ? "cleared" : "drained" });
        }
        break;
      case "response.done":
        // Spec 165 §5.1 (C2 by construction): the response's end and how it ended, before its function calls are passed on.
        this._emit("response_done", { response_id: event.response?.id || null, status: event.response?.status || null });
        for (const item of event.response?.output || []) {
          if (item.type === "function_call") {
            this._emit("function_call", {
              call_id: item.call_id,
              name: item.name,
              arguments: item.arguments,
              response_id: event.response?.id || null,
            });
          }
        }
        // LV1 (live probes): a second response.done for a reply already done, cancelled (turn_detected), comes when he speaks
        // while its audio still plays: its playback was cut there (barged_in), unless its end was already reported.
        {
          const id = event.response?.id || null;
          if (!this._played.has(id) && this._ended.has(id) && event.response?.status === "cancelled") {
            this._played.add(id);
            this._emit("playback_drained", { response_id: id, outcome: "barged_in" });
          } else if (!this._played.has(id) && !this._ended.has(id) && !this._audible.has(id) && !_hasAudio(event.response)) {
            this._played.add(id);
            this._emit("playback_drained", { response_id: id, outcome: event.response?.status === "cancelled" ? "cancelled" : "no_audio" });
          }
          this._ended.add(id);
        }
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

// A host that tracks its responses (Spec 165 §5.1, §5.3; review FR N3, V R2-M1) tags each one it asks for in the response's
// metadata, and response.created reports the tag back. Untagged, the bytes are as before.
function _responseCreate(tag) {
  return tag ? { type: "response.create", response: { metadata: { minimoi_tag: tag } } } : { type: "response.create" };
}

// A function call's output item (the bytes sendFunctionResult has always sent first).
function _functionOutput(callId, output) {
  return { type: "conversation.item.create",
    item: { type: "function_call_output", call_id: callId, output: typeof output === "string" ? output : JSON.stringify(output) } };
}

// Whether a finished response carried audio (its output has an audio content part): its drain is then the provider's
// output_audio_buffer.stopped, not its end.
function _hasAudio(response) {
  return (response?.output || []).some((item) => (item?.content || []).some((part) => ["audio", "output_audio"].includes(part?.type)));
}
