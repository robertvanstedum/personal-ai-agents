from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_confer_page_uses_shared_controller_and_keeps_typed_path():
    template = (ROOT / "domains/cos/templates/cos_ui.html").read_text()
    assert "realtime-confer-controller.js" in template
    assert "Voice is AI-generated." in template
    assert "channel: 'html_text'" in template
    assert "captureWithVAD" not in template
    assert "/ui/transcribe" not in template


def test_shared_confer_controller_keeps_voice_and_agent_boundaries_separate():
    source = (
        ROOT / "core/realtime_voice/static/realtime-confer-controller.js"
    ).read_text()
    assert 'channel: "html_voice"' in source
    assert "voice_provider: this._provider" in source
    assert "speech_url" in source
    assert "OpenAITranscriptionWebRTCAdapter" in source
    assert "GrokBackend" not in source
    assert "OpenClaw" not in source


def test_openai_adapter_commits_browser_detected_speech_turns():
    source = (
        ROOT
        / "core/realtime_voice/static/adapters/openai-transcription-webrtc-adapter.js"
    ).read_text()
    assert "createAnalyser" in source
    assert 'source: "browser_vad"' in source
    assert 'type: "input_audio_buffer.commit"' in source

    controller = (
        ROOT / "core/realtime_voice/static/realtime-confer-controller.js"
    ).read_text()
    assert "autoCommitOnSilence: true" in controller
