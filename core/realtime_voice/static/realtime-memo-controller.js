import { OpenAITranscriptionWebRTCAdapter } from "./adapters/openai-transcription-webrtc-adapter.js?v=20260809-memo1";

const PREFERENCE_KEY = "minimoi.voice.provider";
let activeBinding = null;

function joinText(left, right) {
  const a = (left || "").trimEnd();
  const b = (right || "").trimStart();
  if (!a) return b;
  if (!b) return a;
  return `${a} ${b}`;
}

export class RealtimeMemoController {
  constructor({ bootstrapUrl, provider, initialText = "", onStateChange, onTranscript, onWarning, onError }) {
    this._bootstrapUrl = bootstrapUrl;
    this._provider = provider;
    this._initialText = initialText;
    this._onStateChange = onStateChange || (() => {});
    this._onTranscript = onTranscript || (() => {});
    this._onWarning = onWarning || (() => {});
    this._onError = onError || (() => {});
    this._items = new Map();
    this._seenEventIds = new Set();
    this._sequence = 0;
    this._adapter = null;
    this._generation = 0;
    this._stopping = false;
    this._reconnectAttempted = false;
    this._durationTimer = null;
    this._startedAt = null;
    this._warningMinutes = 13;
    this._maxMinutes = 15;
  }

  async start() {
    this._startedAt = Date.now();
    this._onStateChange("requesting");
    await this._openSession();
    this._startDurationGuard();
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
    this._warningMinutes = credentials.warning_minutes || 13;
    this._maxMinutes = credentials.max_minutes || 15;

    if (credentials.provider !== "openai") {
      throw new Error(`Unsupported memo provider: ${credentials.provider}`);
    }

    const generation = ++this._generation;
    const adapter = new OpenAITranscriptionWebRTCAdapter();
    this._adapter = adapter;
    adapter.on("connected", () => {
      if (generation === this._generation && !this._stopping) this._onStateChange("listening");
    });
    adapter.on("transcript", (event) => {
      if (generation === this._generation) this._addTranscript(event);
    });
    adapter.on("recoverable_error", (error) => {
      if (generation === this._generation && !this._stopping) this._recover(error);
    });
    adapter.on("fatal_error", (error) => {
      if (generation === this._generation && !this._stopping) this._fail(error);
    });
    await adapter.connect(credentials);
  }

  _addTranscript(event) {
    if (event.provider_event_id) {
      if (this._seenEventIds.has(event.provider_event_id)) return;
      this._seenEventIds.add(event.provider_event_id);
    }
    // Item identifiers are only guaranteed within one provider session. A
    // reconnect starts a new session and may reuse an identifier, so namespace
    // it by connection generation before reconciling provisional/final text.
    const itemId = `${this._generation}:${event.item_id || `memo-${this._sequence + 1}`}`;
    const existing = this._items.get(itemId);
    if (!existing) {
      this._items.set(itemId, {
        text: event.text || "",
        completed: !!event.completed,
        sequence: this._sequence++,
      });
    } else {
      existing.text = event.is_delta ? existing.text + (event.text || "") : (event.text || "");
      existing.completed = existing.completed || !!event.completed;
    }
    this._onTranscript(this.text(), { completed: !!event.completed });
  }

  text() {
    const memo = [...this._items.values()]
      .sort((a, b) => a.sequence - b.sequence)
      .map((item) => item.text.trim())
      .filter(Boolean)
      .join(" ");
    return joinText(this._initialText, memo);
  }

  async stop(reason = "user_ended") {
    if (this._stopping) return;
    this._stopping = true;
    clearInterval(this._durationTimer);
    this._onStateChange("finalizing");
    try {
      await this._adapter?.finish();
    } finally {
      this._adapter?.end(reason);
      this._onTranscript(this.text(), { completed: true });
      this._onStateChange("stopped", { reason });
    }
  }

  async _recover(error) {
    if (this._reconnectAttempted) {
      this._fail(error);
      return;
    }
    this._reconnectAttempted = true;
    this._onStateChange("reconnecting");
    const oldAdapter = this._adapter;
    ++this._generation;
    oldAdapter?.end("reconnect");
    try {
      await this._openSession();
    } catch (reconnectError) {
      this._fail({ reason: "reconnect_failed", detail: reconnectError.message });
    }
  }

  _fail(error) {
    if (this._stopping) return;
    this._stopping = true;
    clearInterval(this._durationTimer);
    ++this._generation;
    this._adapter?.end("error");
    this._onTranscript(this.text(), { completed: true });
    this._onStateChange("error", error);
    this._onError(error);
  }

  _startDurationGuard() {
    let warned = false;
    this._durationTimer = setInterval(() => {
      const elapsedMinutes = (Date.now() - this._startedAt) / 60000;
      if (elapsedMinutes >= this._maxMinutes) {
        this.stop("duration_limit");
      } else if (!warned && elapsedMinutes >= this._warningMinutes) {
        warned = true;
        this._onWarning();
      }
    }, 5000);
  }
}

export async function initializeRealtimeMemo({
  capabilitiesUrl,
  bootstrapUrl,
  preferenceHost,
  bindings,
  labels,
}) {
  let capabilities;
  try {
    const response = await fetch(capabilitiesUrl);
    capabilities = await response.json();
    if (!response.ok || !capabilities.ok) throw new Error(capabilities.error || "Voice unavailable");
  } catch (error) {
    for (const binding of bindings) {
      const button = document.getElementById(binding.buttonId);
      if (button) button.disabled = true;
    }
    return;
  }

  const available = capabilities.providers || [];
  let provider = localStorage.getItem(PREFERENCE_KEY);
  if (!available.some((item) => item.provider === provider)) {
    provider = capabilities.default_provider;
    localStorage.setItem(PREFERENCE_KEY, provider);
  }

  const host = document.getElementById(preferenceHost);
  // The writing surface keeps its existing compact microphone UI. A shared
  // provider preference appears only when there is a real choice; the
  // approved single-provider fallback must not add a disabled selector.
  if (host && available.length > 1) {
    const label = document.createElement("label");
    label.className = "memo-voice-preference-label";
    label.textContent = labels.preference;
    const select = document.createElement("select");
    select.className = "memo-voice-preference-select";
    select.setAttribute("aria-label", labels.preference);
    for (const item of available) {
      const option = document.createElement("option");
      option.value = item.provider;
      option.textContent = item.label;
      select.appendChild(option);
    }
    select.value = provider;
    select.addEventListener("change", () => {
      provider = select.value;
      localStorage.setItem(PREFERENCE_KEY, provider);
    });
    label.appendChild(select);
    host.replaceChildren(label);
  } else if (host) {
    host.replaceChildren();
  }

  for (const binding of bindings) {
    bindMemoButton({ ...binding, bootstrapUrl, getProvider: () => provider, labels });
  }
}

function bindMemoButton({ buttonId, textareaId, statusId, bootstrapUrl, getProvider, labels }) {
  const button = document.getElementById(buttonId);
  const textarea = document.getElementById(textareaId);
  const status = document.getElementById(statusId);
  if (!button || !textarea) return;

  const setState = (state) => {
    const text = labels.states[state] || "";
    button.dataset.voiceState = state;
    button.classList.toggle("listening", state === "listening");
    button.textContent = ["requesting", "listening", "reconnecting", "finalizing"].includes(state) ? "⏹" : "🎤";
    button.setAttribute("aria-label", text || labels.start);
    button.title = text || labels.start;
    if (status && text) status.textContent = text;
    textarea.readOnly = !["stopped", "error"].includes(state);
  };

  button.addEventListener("click", async () => {
    if (activeBinding?.button === button) {
      await activeBinding.controller.stop();
      textarea.readOnly = false;
      activeBinding = null;
      return;
    }
    if (activeBinding) await activeBinding.controller.stop("field_changed");

    const controller = new RealtimeMemoController({
      bootstrapUrl,
      provider: getProvider(),
      initialText: textarea.value,
      onStateChange: (state) => {
        setState(state);
        if (["stopped", "error"].includes(state) && activeBinding?.controller === controller) {
          activeBinding = null;
          textarea.readOnly = false;
        }
      },
      onTranscript: (text) => { textarea.value = text; },
      onWarning: () => { if (status) status.textContent = labels.warning; },
      onError: (error) => {
        if (status) status.textContent = `${labels.error} ${error?.detail || error?.reason || ""}`.trim();
      },
    });
    activeBinding = { button, controller };
    try {
      await controller.start();
    } catch (error) {
      controller._fail({ reason: "start_failed", detail: error.message });
    }
  });
}
