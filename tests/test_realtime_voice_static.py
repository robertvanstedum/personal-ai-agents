"""
tests/test_realtime_voice_static.py — both domains serve the shared
realtime-voice JS from one source of truth (core/realtime_voice/static/),
not a per-domain copy.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_german_serves_shared_controller_js(german_client):
    resp = german_client.get("/static/realtime-voice/realtime-voice-controller.js")
    assert resp.status_code == 200
    assert b"RealtimeVoiceController" in resp.data


def test_portuguese_serves_shared_controller_js(portuguese_client):
    resp = portuguese_client.get("/static/realtime-voice/realtime-voice-controller.js")
    assert resp.status_code == 200
    assert b"RealtimeVoiceController" in resp.data


def test_german_serves_openai_adapter(german_client):
    resp = german_client.get("/static/realtime-voice/adapters/openai-webrtc-adapter.js")
    assert resp.status_code == 200
    assert b"OpenAIWebRTCAdapter" in resp.data


def test_german_serves_xai_adapter(german_client):
    resp = german_client.get("/static/realtime-voice/adapters/xai-websocket-adapter.js")
    assert resp.status_code == 200
    assert b"XAIWebSocketAdapter" in resp.data


def test_german_serves_shared_memo_controller(german_client):
    response = german_client.get("/static/realtime-voice/realtime-memo-controller.js")
    assert response.status_code == 200
    assert "RealtimeMemoController" in response.get_data(as_text=True)


def test_portuguese_serves_shared_memo_controller(portuguese_client):
    response = portuguese_client.get("/static/realtime-voice/realtime-memo-controller.js")
    assert response.status_code == 200
    assert "RealtimeMemoController" in response.get_data(as_text=True)


def test_openai_memo_adapter_commits_only_on_explicit_finish(german_client):
    response = german_client.get(
        "/static/realtime-voice/adapters/openai-transcription-webrtc-adapter.js"
    )
    source = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'type: "input_audio_buffer.commit"' in source
    assert "conversation.item.input_audio_transcription.delta" in source
    assert "conversation.item.input_audio_transcription.completed" in source
    assert "SpeechRecognition" not in source
    assert '"https://api.openai.com/v1/realtime/calls"' in source
    assert "realtime/calls?model=" not in source
    assert "finishTimeoutMs = 2500" in source

    controller = german_client.get(
        "/static/realtime-voice/realtime-memo-controller.js"
    ).get_data(as_text=True)
    assert "autoCommitOnSilence: false" in controller
    assert "finishTimeoutMs: 10000" in controller


def test_memo_provider_preference_only_renders_when_choice_exists(german_client):
    response = german_client.get("/static/realtime-voice/realtime-memo-controller.js")
    source = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "available.length > 1" in source
    assert "approved single-provider fallback" in source


def test_shared_controller_starts_with_persona_and_hides_live_transcript(german_client):
    resp = german_client.get("/static/realtime-voice/realtime-voice-controller.js")
    source = resp.get_data(as_text=True)

    assert "one short, natural greeting" in source
    assert "Do not give directions" in source
    assert "sendContinuationInstruction(this._openingInstruction)" in source
    assert 'this._onInputState("speech_started")' in source
    assert "never surfaced to the UI while active" in source
    assert "openai-webrtc-adapter.js?v=20260929-voice3" in source
    assert "xai-websocket-adapter.js?v=20260929-voice3" in source


def test_xai_adapter_handles_current_audio_delta_and_connection_failures(german_client):
    resp = german_client.get("/static/realtime-voice/adapters/xai-websocket-adapter.js")
    source = resp.get_data(as_text=True)

    assert "event.delta || event.audio" in source
    assert "connection_timeout" in source
    assert "microphone_unavailable" in source
    assert 'case "session.updated"' in source
    assert "this._sessionReady &&" in source
    assert 'case "error"' in source
    assert 'reason: "provider_error"' in source
    assert 'case "conversation.item.input_audio_transcription.updated"' in source
    assert 'case "conversation.item.input_audio_transcription.completed"' in source


def test_openai_adapter_captures_learner_transcription(german_client):
    resp = german_client.get("/static/realtime-voice/adapters/openai-webrtc-adapter.js")
    source = resp.get_data(as_text=True)

    assert 'case "conversation.item.input_audio_transcription.delta"' in source
    assert 'case "conversation.item.input_audio_transcription.completed"' in source
    assert 'case "conversation.item.input_audio_transcription.failed"' in source
    assert '"https://api.openai.com/v1/realtime/calls"' in source
    assert "realtime/calls?model=" not in source


def test_every_session_end_releases_the_microphone_once():
    """#273: a provider-side close or a provider error ends the session
    through _finalizeAndEnd, which must release the adapter (and so the
    microphone); the adapters' end() is idempotent. The browser proof, on
    Confer, German and Portuguese, is tests/browser_checks_voice_mic.py."""
    static = REPO / "core" / "realtime_voice" / "static"
    source = (static / "realtime-voice-controller.js").read_text()
    finalize = source[source.index("  _finalizeAndEnd(reason) {"):]
    finalize = finalize[:finalize.index("\n  }\n")]
    assert "this._endAdapter(reason);" in finalize
    end_adapter = source[source.index("  _endAdapter(reason) {"):]
    assert "if (!this._adapter || this._adapterEnded) return;" in end_adapter[:200]
    assert "this._adapter?.end(" not in source                  # every end goes through _endAdapter
    for adapter in ("openai-webrtc-adapter.js", "xai-websocket-adapter.js"):
        text = (static / "adapters" / adapter).read_text()
        body = text[text.index("  end(reason) {"):]
        assert "if (this._ended) return;" in body[:200], adapter


def test_a_provider_error_ends_the_session_visibly_never_a_silent_wait():
    """Phase A: no "reconnecting" state; a provider error calls onFatalError
    and ends the session (releasing the microphone); benign notices do not.
    The browser proof on all three pages is tests/browser_checks_voice_mic.py."""
    static = REPO / "core" / "realtime_voice" / "static"
    source = (static / "realtime-voice-controller.js").read_text()
    assert "reconnecting" not in source.replace('never a silent "reconnecting" wait', "")
    assert "_attemptReconnectOrEndVisibly" not in source
    assert 'this._adapter.on("recoverable_error", (info) => this._onProviderError(info));' in source
    handler = source[source.index("  _onProviderError(info) {"):]
    handler = handler[:handler.index("\n  }\n")]
    assert "this._onFatalError(" in handler
    assert 'this._endAdapter("provider_error");' in handler
    for code in ("conversation_already_has_active_response", "response_cancel_not_active",
                 "input_audio_buffer_commit_empty"):
        assert f'"{code}"' in source
    openai = (static / "adapters" / "openai-webrtc-adapter.js").read_text()
    assert 'this._emit("recoverable_error", { detail: event.error });' not in openai
    assert "code: event.error?.code || null," in openai and "type: event.error?.type || null," in openai
    xai = (static / "adapters" / "xai-websocket-adapter.js").read_text()
    assert "code: event.error?.code || null," in xai and "type: event.error?.type || null," in xai
    # #281 review F6: the code and type of an error that ends a session are
    # logged on the server (never the message), benign by code or by type.
    assert "BENIGN_PROVIDER_ERRORS.has(code) || BENIGN_PROVIDER_ERRORS.has(type)" in handler
    assert "this._reportProviderError({ ...info, code, type });" in handler
    report = source[source.index("  _reportProviderError(info) {"):]
    report = report[:report.index("\n  }\n")]
    assert "message" not in report and "detail" not in report
    assert 'String(bootstrapUrl).replace(/bootstrap$/, "outcome")' in source


def test_write_only_mutes_playback_in_both_adapters():
    """Phase A: "write only" mutes the reply's audio on the device (both
    providers); the provider still sends the transcript, which pages show."""
    static = REPO / "core" / "realtime_voice" / "static"
    controller = (static / "realtime-voice-controller.js").read_text()
    assert 'replyMode = "speak",' in controller
    assert "setReplyMode(mode) {" in controller
    assert 'this._adapter.setOutputMuted?.(this._replyMode === "write");' in controller
    openai = (static / "adapters" / "openai-webrtc-adapter.js").read_text()
    assert "this._remoteAudioEl.muted = this._outputMuted;" in openai
    xai = (static / "adapters" / "xai-websocket-adapter.js").read_text()
    assert "if (this._outputMuted || !this._audioContext || !base64Audio) return;" in xai
    assert "if (this._outputMuted) this._stopPlayback();" in xai
    helper = (static / "voice-reply-mode.js").read_text()
    assert 'const KEY_PREFIX = "minimoi.voice.reply_mode.";' in helper
    assert helper.count("try {") == 2                    # storage read and write can both throw


def test_every_page_serves_the_reply_mode_helper(german_client, portuguese_client):
    for client in (german_client, portuguese_client):
        resp = client.get("/static/realtime-voice/voice-reply-mode.js")
        assert resp.status_code == 200
        assert "export function mountReplyModeToggle" in resp.get_data(as_text=True)
