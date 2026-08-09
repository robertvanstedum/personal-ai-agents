import { OpenAITranscriptionWebRTCAdapter } from "./adapters/openai-transcription-webrtc-adapter.js?v=20260809-confer2";

const PREFERENCE_KEY = "minimoi.voice.provider";

export class ConferVoiceController {
  constructor({
    capabilitiesUrl,
    bootstrapUrl,
    turnUrl,
    conversationId = "owner",
    onStateChange,
    onProvisionalTranscript,
    onUserTurn,
    onAssistantTurn,
    onWarning,
    onError,
  }) {
    this._capabilitiesUrl = capabilitiesUrl;
    this._bootstrapUrl = bootstrapUrl;
    this._turnUrl = turnUrl;
    this._conversationId = conversationId;
    this._onStateChange = onStateChange || (() => {});
    this._onProvisionalTranscript = onProvisionalTranscript || (() => {});
    this._onUserTurn = onUserTurn || (() => {});
    this._onAssistantTurn = onAssistantTurn || (() => {});
    this._onWarning = onWarning || (() => {});
    this._onError = onError || (() => {});
    this._provider = null;
    this._adapter = null;
    this._partials = new Map();
    this._completedItems = new Set();
    this._generation = 0;
    this._active = false;
    this._reconnectAttempted = false;
    this._turnChain = Promise.resolve();
    this._audio = null;
    this._durationTimer = null;
    this._startedAt = null;
    this._warningMinutes = 13;
    this._maxMinutes = 15;
  }

  async availableProviders() {
    const response = await fetch(this._capabilitiesUrl);
    const body = await response.json().catch(() => ({}));
    if (!response.ok || !body.ok) {
      throw new Error(body.error || `Voice unavailable (HTTP ${response.status})`);
    }
    return body;
  }

  async start(provider = null) {
    if (this._active) return;
    const capabilities = await this.availableProviders();
    const available = capabilities.providers || [];
    const saved = localStorage.getItem(PREFERENCE_KEY);
    this._provider = provider || (
      available.some((item) => item.provider === saved)
        ? saved
        : capabilities.default_provider
    );
    if (!available.some((item) => item.provider === this._provider)) {
      throw new Error(`Voice provider ${this._provider || "(none)"} is unavailable`);
    }
    localStorage.setItem(PREFERENCE_KEY, this._provider);
    this._active = true;
    this._startedAt = Date.now();
    this._onStateChange("requesting", { provider: this._provider });
    try {
      await this._openSession();
      this._startDurationGuard();
    } catch (error) {
      this._fail({ reason: "start_failed", detail: error.message });
      throw error;
    }
  }

  async _openSession() {
    const response = await fetch(this._bootstrapUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: this._provider }),
    });
    const credentials = await response.json().catch(() => ({}));
    if (!response.ok || !credentials.ok) {
      throw new Error(credentials.error || `Voice session failed (HTTP ${response.status})`);
    }
    if (credentials.provider !== "openai") {
      throw new Error(`Unsupported Confer voice provider: ${credentials.provider}`);
    }
    this._warningMinutes = credentials.warning_minutes || 13;
    this._maxMinutes = credentials.max_minutes || 15;

    const generation = ++this._generation;
    const adapter = new OpenAITranscriptionWebRTCAdapter();
    this._adapter = adapter;
    adapter.on("connected", () => {
      if (this._isCurrent(generation)) this._onStateChange("listening");
    });
    adapter.on("speech_started", () => {
      if (!this._isCurrent(generation)) return;
      this._cancelPlayback("interrupted");
      this._onStateChange("listening");
    });
    adapter.on("transcript", (event) => {
      if (this._isCurrent(generation)) this._handleTranscript(event);
    });
    adapter.on("recoverable_error", (error) => {
      if (this._isCurrent(generation)) this._recover(error);
    });
    adapter.on("fatal_error", (error) => {
      if (this._isCurrent(generation)) this._fail(error);
    });
    await adapter.connect(credentials);
  }

  _isCurrent(generation) {
    return this._active && generation === this._generation;
  }

  _handleTranscript(event) {
    const itemId = `${this._generation}:${event.item_id || "current"}`;
    if (event.completed) {
      if (this._completedItems.has(itemId)) return;
      this._completedItems.add(itemId);
      this._partials.delete(itemId);
      this._onProvisionalTranscript("");
      const text = (event.text || "").trim();
      if (text) {
        this._turnChain = this._turnChain
          .then(() => this._submitTurn(text))
          .catch((error) => {
            this._onError({ reason: "turn_failed", detail: error.message });
            if (this._active) this._onStateChange("listening");
          });
      }
      return;
    }
    const existing = this._partials.get(itemId) || "";
    this._partials.set(
      itemId,
      event.is_delta ? existing + (event.text || "") : (event.text || "")
    );
    this._onProvisionalTranscript(
      [...this._partials.values()].join(" ").trim()
    );
  }

  async _submitTurn(text) {
    if (!this._active) return;
    this._onUserTurn(text);
    this._onStateChange("thinking");
    const response = await fetch(this._turnUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        channel: "html_voice",
        conversation_id: this._conversationId,
        voice_provider: this._provider,
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.reply || body.error || `CoS turn failed (HTTP ${response.status})`);
    }
    this._onAssistantTurn(body.reply || "(empty reply)", body);
    if (body.speech_url && this._active) {
      await this._play(body.speech_url);
    } else if (this._active) {
      this._onStateChange("listening");
    }
  }

  async _play(url) {
    this._cancelPlayback("superseded");
    const audio = new Audio(url);
    this._audio = audio;
    this._onStateChange("speaking");
    await audio.play();
    await new Promise((resolve) => {
      audio.addEventListener("ended", resolve, { once: true });
      audio.addEventListener("error", resolve, { once: true });
      audio.addEventListener("pause", resolve, { once: true });
    });
    if (this._audio === audio) this._audio = null;
    if (this._active) this._onStateChange("listening");
  }

  _cancelPlayback(reason) {
    if (!this._audio) return;
    this._audio.pause();
    this._audio.removeAttribute("src");
    this._audio.load();
    this._audio = null;
    this._onStateChange("interrupted", { reason });
  }

  async _recover(error) {
    if (!this._active) return;
    if (this._reconnectAttempted) {
      this._fail(error);
      return;
    }
    this._reconnectAttempted = true;
    this._onStateChange("reconnecting");
    ++this._generation;
    this._adapter?.end("reconnect");
    try {
      await this._openSession();
    } catch (reconnectError) {
      this._fail({ reason: "reconnect_failed", detail: reconnectError.message });
    }
  }

  stop(reason = "user_ended") {
    if (!this._active) return;
    this._active = false;
    clearInterval(this._durationTimer);
    ++this._generation;
    this._adapter?.end(reason);
    this._cancelPlayback(reason);
    this._partials.clear();
    this._onProvisionalTranscript("");
    this._onStateChange("stopped", { reason });
  }

  _fail(error) {
    if (!this._active) return;
    this.stop("error");
    this._onStateChange("error", error);
    this._onError(error);
  }

  _startDurationGuard() {
    let warned = false;
    this._durationTimer = setInterval(() => {
      const minutes = (Date.now() - this._startedAt) / 60000;
      if (minutes >= this._maxMinutes) {
        this.stop("duration_limit");
      } else if (!warned && minutes >= this._warningMinutes) {
        warned = true;
        this._onWarning();
      }
    }, 5000);
  }
}
