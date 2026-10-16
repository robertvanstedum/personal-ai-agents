import { OpenAITranscriptionWebRTCAdapter } from "./adapters/openai-transcription-webrtc-adapter.js?v=20260815-confer8";

const PREFERENCE_KEY = "minimoi.voice.provider";

export class ConferVoiceController {
  constructor({
    capabilitiesUrl,
    bootstrapUrl,
    turnUrl,
    conversationId = "owner",
    requestHeaders = null,
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
    // Optional extra headers for the session request (a host page that guards its writes with a token). Unset: unchanged.
    this._requestHeaders = requestHeaders;
    this._onStateChange = onStateChange || (() => {});
    this._onProvisionalTranscript = onProvisionalTranscript || (() => {});
    this._onUserTurn = onUserTurn || (() => {});
    this._onAssistantTurn = onAssistantTurn || (() => {});
    this._onWarning = onWarning || (() => {});
    this._onError = onError || (() => {});
    this._provider = null;
    this._startToken = 0;       // a stop() invalidates every start, bootstrap, connect or reconnect still awaiting
    this._starting = false;
    this._adapter = null;
    this._partials = new Map();
    this._completedItems = new Set();
    this._generation = 0;
    this._active = false;
    this._reconnectAttempted = false;
    this._turnChain = Promise.resolve();
    this._audio = null;
    this._audioUrl = null;
    this._speechAbort = null;
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
    if (this._active || this._starting) return;
    const token = ++this._startToken;
    this._starting = true;
    this._turnChain = Promise.resolve(); // a restarted session never waits on an abandoned turn
    this._partials.clear();
    this._completedItems.clear();
    this._reconnectAttempted = false;                               // a new start gets its own one reconnect
    try {
      const capabilities = await this.availableProviders();
      if (token !== this._startToken) return;                       // stopped while the capabilities call was out
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
        await this._openSession(token);
        if (token !== this._startToken) return;
        this._startDurationGuard();
      } catch (error) {
        if (token !== this._startToken || error?.cancelled) return;   // a stop is not a failure
        this._fail({ reason: "start_failed", detail: error.message });
        throw error;
      }
    } finally {
      if (token === this._startToken) this._starting = false;
    }
  }

  async _openSession(token = this._startToken) {
    const stale = () => token !== this._startToken || !this._active;
    const response = await fetch(this._bootstrapUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(typeof this._requestHeaders === "function" ? this._requestHeaders() : (this._requestHeaders || {})),
      },
      body: JSON.stringify({ provider: this._provider }),
    });
    const credentials = await response.json().catch(() => ({}));
    if (stale()) return;                                            // stopped while the session request was out: connect nothing
    if (!response.ok || !credentials.ok) {
      throw new Error(credentials.error || `Voice session failed (HTTP ${response.status})`);
    }
    if (credentials.provider !== "openai") {
      throw new Error(`Unsupported Confer voice provider: ${credentials.provider}`);
    }
    this._warningMinutes = credentials.warning_minutes || 13;
    this._maxMinutes = credentials.max_minutes || 15;

    const generation = ++this._generation;
    // Transcription-only gpt-live-transcribe session. Browser VAD commits
    // on a pause; the speech provider cannot answer for CoS/MC.
    const adapter = new OpenAITranscriptionWebRTCAdapter({
      autoCommitOnSilence: true,
    });
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
    try {
      await adapter.connect(credentials);
    } catch (error) {
      if (stale() || error?.cancelled) { adapter.end("cancelled"); return; }
      throw error;
    }
    if (stale()) adapter.end("cancelled");                          // stopped while connecting: close the microphone and the connection
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
        // Correlate platform operations exactly as the typed Confer path does.
        // The same id follows this committed transcript through submission so
        // an explicit note cannot be duplicated by a correlated retry.
        const requestId = crypto.randomUUID();
        const token = this._startToken;                              // the session this transcript belongs to
        this._turnChain = this._turnChain
          .then(() => {
            if (token !== this._startToken || !this._active) return undefined;     // stopped (or restarted) while this waited its turn: drop it
            return this._submitTurn(text, requestId, token);
          })
          .catch((error) => {
            if (token !== this._startToken) return;                  // a failure of a session that has since ended is not reported into the new one
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

  async _submitTurn(text, requestId, token = this._startToken) {
    if (!this._active || token !== this._startToken) return;
    this._onUserTurn(text);
    this._onStateChange("thinking");
    const response = await fetch(this._turnUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json",
        ...(typeof this._requestHeaders === "function" ? this._requestHeaders() : (this._requestHeaders || {})),
      },
      body: JSON.stringify({
        text,
        channel: "html_voice",
        conversation_id: this._conversationId,
        request_id: requestId,
        voice_provider: this._provider,
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (token !== this._startToken || !this._active) return;       // stopped or restarted while the answer was on its way: show and speak nothing
    if (!response.ok) {
      throw new Error(body.reply || body.error || `CoS turn failed (HTTP ${response.status})`);
    }
    this._onAssistantTurn(body.reply || "(empty reply)", body);
    if (body.speech_url) {
      await this._play(body.speech_url, token);
    } else {
      this._onStateChange("listening");
    }
  }

  async _play(url, token = this._startToken) {
    if (!this._active || token !== this._startToken) return;
    this._cancelPlayback("superseded");
    const abort = new AbortController();
    this._speechAbort = abort;
    let audio = null, objectUrl = null;
    try {
      // Exactly one paid request. A refusal is JSON, so read its actual
      // explanation before creating Audio; never retry the speech endpoint.
      const response = await fetch(url, { credentials: "same-origin", signal: abort.signal });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(`${body.error || `The answer could not be read aloud (HTTP ${response.status}).`} The answer is in the chat.`);
      }
      const blob = await response.blob();
      if (abort.signal.aborted || !this._active || token !== this._startToken) return;
      objectUrl = URL.createObjectURL(blob);
      this._audioUrl = objectUrl;
      audio = new Audio(objectUrl);
      this._audio = audio;
      this._onStateChange("speaking");
      await audio.play();
      if (!this._active || token !== this._startToken || abort.signal.aborted) { audio.pause(); return; }
      await new Promise((resolve, reject) => {
        audio.addEventListener("ended", resolve, { once: true });
        audio.addEventListener("pause", resolve, { once: true });
        audio.addEventListener("error", () => reject(new Error("The answer could not be read aloud. It remains in the chat.")), { once: true });
      });
    } catch (error) {
      if (!abort.signal.aborted && this._active && token === this._startToken) throw error;
    } finally {
      if (this._speechAbort === abort) this._speechAbort = null;
      if (this._audio === audio) this._audio = null;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (this._audioUrl === objectUrl) this._audioUrl = null;
      if (this._active && token === this._startToken) this._onStateChange("listening");
    }
  }

  _cancelPlayback(reason) {
    const hadPlayback = Boolean(this._audio || this._speechAbort);
    this._speechAbort?.abort();
    this._speechAbort = null;
    if (this._audio) {
      this._audio.pause();
      this._audio.removeAttribute("src");
      this._audio.load();
      this._audio = null;
    }
    if (this._audioUrl) URL.revokeObjectURL(this._audioUrl);
    this._audioUrl = null;
    if (hadPlayback) this._onStateChange("interrupted", { reason });
  }

  async _recover(error) {
    if (!this._active) return;
    const token = this._startToken;                                  // bound to this session: a stop, or a stop and a new start, ends it
    if (this._reconnectAttempted) {
      this._fail(error);
      return;
    }
    this._reconnectAttempted = true;
    this._onStateChange("reconnecting");
    ++this._generation;
    this._adapter?.end("reconnect");
    try {
      await this._openSession(token);
    } catch (reconnectError) {
      if (token !== this._startToken || reconnectError?.cancelled) return;     // never fail a newly restarted session for the old one's reconnect
      this._fail({ reason: "reconnect_failed", detail: reconnectError.message });
    }
  }

  stop(reason = "user_ended") {
    const pending = this._starting;
    ++this._startToken;                                              // any start, bootstrap, connect or reconnect still awaiting is now stale
    this._starting = false;
    if (!this._active) {
      if (pending) this._onStateChange("stopped", { reason });
      return;
    }
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
