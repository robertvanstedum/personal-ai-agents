/** Browser-direct OpenAI transcription transport using an ephemeral key. */
export class OpenAITranscriptionWebRTCAdapter {
  constructor({ autoCommitOnSilence = false, finishTimeoutMs = 2500 } = {}) {
    this._listeners = {};
    this._pc = null;
    this._dc = null;
    this._micStream = null;
    this._ending = false;
    this._finishResolve = null;
    this._finishTimer = null;
    this._audioContext = null;
    this._analyser = null;
    this._vadTimer = null;
    this._speaking = false;
    this._speechStartedAt = 0;
    this._lastVoiceAt = 0;
    this._autoCommitOnSilence = autoCommitOnSilence;
    this._finishTimeoutMs = finishTimeoutMs;
  }

  on(eventName, handler) {
    (this._listeners[eventName] ||= []).push(handler);
  }

  _emit(eventName, payload = {}) {
    for (const handler of this._listeners[eventName] || []) handler(payload);
  }

  async connect(credentials) {
    try {
      this._micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (error) {
      this._emit("fatal_error", { reason: "microphone_denied", detail: String(error) });
      throw error;
    }

    this._pc = new RTCPeerConnection();
    for (const track of this._micStream.getAudioTracks()) {
      this._pc.addTrack(track, this._micStream);
    }

    this._pc.addEventListener("connectionstatechange", () => {
      const state = this._pc?.connectionState;
      if (!this._ending && ["failed", "disconnected"].includes(state)) {
        this._emit("recoverable_error", { reason: `webrtc_${state}` });
      }
    });

    this._dc = this._pc.createDataChannel("oai-events");
    this._dc.addEventListener("message", (event) => {
      try {
        this._handleServerEvent(JSON.parse(event.data));
      } catch (error) {
        this._emit("recoverable_error", { reason: "invalid_provider_event", detail: String(error) });
      }
    });
    this._dc.addEventListener("open", () => {
      if (this._autoCommitOnSilence) this._startLocalTurnDetection();
      this._emit("connected", { provider: "openai" });
    });
    this._dc.addEventListener("close", () => {
      if (!this._ending) this._emit("recoverable_error", { reason: "data_channel_closed" });
    });

    const offer = await this._pc.createOffer();
    await this._pc.setLocalDescription(offer);

    // The ephemeral credential is already bound to the transcription session
    // and model. The GA WebRTC contract posts the SDP to /v1/realtime/calls
    // without repeating the model in the query string.
    const response = await fetch(
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
    if (!response.ok) {
      const error = new Error(`OpenAI transcription connection failed (HTTP ${response.status})`);
      this._emit("fatal_error", { reason: "provider_rejected", detail: error.message });
      throw error;
    }
    await this._pc.setRemoteDescription({ type: "answer", sdp: await response.text() });
  }

  async finish() {
    if (this._finishResolve) return;
    if (this._dc?.readyState !== "open") return;

    return new Promise((resolve) => {
      this._finishResolve = resolve;
      this._dc.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
      this._finishTimer = setTimeout(() => this._resolveFinish(), this._finishTimeoutMs);
    });
  }

  end(reason = "user_ended") {
    this._ending = true;
    clearTimeout(this._finishTimer);
    clearInterval(this._vadTimer);
    this._vadTimer = null;
    this._resolveFinish();
    for (const track of this._micStream?.getAudioTracks() || []) track.stop();
    this._audioContext?.close().catch(() => {});
    this._dc?.close();
    this._pc?.close();
    this._emit("closed", { reason });
  }

  _resolveFinish() {
    if (!this._finishResolve) return;
    const resolve = this._finishResolve;
    this._finishResolve = null;
    resolve();
  }

  _startLocalTurnDetection() {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass || !this._micStream) {
      this._emit("fatal_error", {
        reason: "local_turn_detection_unavailable",
        detail: "This browser cannot detect speech boundaries.",
      });
      return;
    }

    this._audioContext = new AudioContextClass();
    const source = this._audioContext.createMediaStreamSource(this._micStream);
    this._analyser = this._audioContext.createAnalyser();
    this._analyser.fftSize = 1024;
    source.connect(this._analyser);
    const samples = new Float32Array(this._analyser.fftSize);
    const threshold = 0.025;
    const minimumSpeechMs = 250;
    const silenceMs = 800;

    this._vadTimer = setInterval(() => {
      if (this._ending || !this._analyser) return;
      this._analyser.getFloatTimeDomainData(samples);
      let energy = 0;
      for (const sample of samples) energy += sample * sample;
      const rms = Math.sqrt(energy / samples.length);
      const now = Date.now();

      if (rms >= threshold) {
        this._lastVoiceAt = now;
        if (!this._speaking) {
          this._speaking = true;
          this._speechStartedAt = now;
          this._emit("speech_started", { source: "browser_vad" });
        }
        return;
      }

      if (!this._speaking || now - this._lastVoiceAt < silenceMs) return;
      const speechDuration = now - this._speechStartedAt;
      this._speaking = false;
      this._emit("speech_stopped", { source: "browser_vad" });
      if (speechDuration >= minimumSpeechMs && this._dc?.readyState === "open") {
        this._dc.send(JSON.stringify({ type: "input_audio_buffer.commit" }));
      }
    }, 50);
  }

  _handleServerEvent(event) {
    switch (event.type) {
      case "input_audio_buffer.speech_started":
        this._emit("speech_started", { provider_event_id: event.event_id });
        break;
      case "input_audio_buffer.speech_stopped":
        this._emit("speech_stopped", { provider_event_id: event.event_id });
        break;
      case "conversation.item.input_audio_transcription.delta":
        this._emit("transcript", {
          item_id: event.item_id,
          text: event.delta || "",
          is_delta: true,
          completed: false,
          provider_event_id: event.event_id,
        });
        break;
      case "conversation.item.input_audio_transcription.completed":
        this._emit("transcript", {
          item_id: event.item_id,
          text: event.transcript || "",
          is_delta: false,
          completed: true,
          provider_event_id: event.event_id,
        });
        this._resolveFinish();
        break;
      case "conversation.item.input_audio_transcription.failed":
        this._emit("recoverable_error", {
          reason: "transcription_failed",
          detail: event.error?.message || event.error?.code || "Transcription failed",
        });
        this._resolveFinish();
        break;
      case "transcript.done":
        this._resolveFinish();
        break;
      case "error":
        this._emit("recoverable_error", {
          reason: "provider_error",
          detail: event.error?.message || event.error?.code || "Provider error",
        });
        break;
      default:
        break;
    }
  }
}
