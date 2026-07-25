"""
tests/test_realtime_voice_static.py — both domains serve the shared
realtime-voice JS from one source of truth (core/realtime_voice/static/),
not a per-domain copy.
"""


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


def test_shared_controller_starts_with_persona_and_hides_live_transcript(german_client):
    resp = german_client.get("/static/realtime-voice/realtime-voice-controller.js")
    source = resp.get_data(as_text=True)

    assert "one short, natural greeting" in source
    assert "Do not give directions" in source
    assert "sendContinuationInstruction(OPENING_INSTRUCTION)" in source
    assert 'this._onInputState("speech_started")' in source
    assert "never surfaced to the UI while active" in source
    assert "openai-webrtc-adapter.js?v=20260725-transcript1" in source
    assert "xai-websocket-adapter.js?v=20260725-transcript1" in source


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
