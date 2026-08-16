from pathlib import Path
from urllib.parse import urljoin


ROOT = Path(__file__).resolve().parents[2]


def test_confer_page_uses_shared_controller_and_keeps_typed_path():
    template = (ROOT / "domains/cos/templates/cos_ui.html").read_text()
    assert "realtime-confer-controller.js" in template
    assert "realtime-confer-controller.js?v=20260815-confer8" in template
    assert "../static/realtime-voice/realtime-confer-controller.js" in template
    assert "capabilitiesUrl: '../api/realtime-voice/confer/capabilities'" in template
    assert "bootstrapUrl: '../api/realtime-voice/confer/bootstrap'" in template
    assert "turnUrl: 'send'" in template
    assert 'aria-label="Start voice conversation">🎤</button>' in template
    assert "if (['stopped', 'error'].includes(state)) voiceRunning = false" in template
    assert "voiceButton.textContent = voiceActive ? '■' : '🎤'" in template
    assert "Voice is AI-generated." in template
    assert "channel: 'html_text'" in template
    assert "captureWithVAD" not in template
    assert "/ui/transcribe" not in template


def test_confer_relative_voice_paths_work_directly_and_through_portal():
    direct_page = "https://cos.example/ui/confer"
    portal_page = "https://minimoi.example/app/cos/ui/confer"

    assert urljoin(direct_page, "../static/realtime-voice/controller.js") == (
        "https://cos.example/static/realtime-voice/controller.js"
    )
    assert urljoin(portal_page, "../static/realtime-voice/controller.js") == (
        "https://minimoi.example/app/cos/static/realtime-voice/controller.js"
    )
    assert urljoin(direct_page, "send") == "https://cos.example/ui/send"
    assert urljoin(portal_page, "send") == (
        "https://minimoi.example/app/cos/ui/send"
    )
    assert urljoin(direct_page, "speech/turn-1") == (
        "https://cos.example/ui/speech/turn-1"
    )
    assert urljoin(portal_page, "speech/turn-1") == (
        "https://minimoi.example/app/cos/ui/speech/turn-1"
    )


def test_shared_confer_controller_keeps_voice_and_agent_boundaries_separate():
    source = (
        ROOT / "core/realtime_voice/static/realtime-confer-controller.js"
    ).read_text()
    assert 'channel: "html_voice"' in source
    assert "autoCommitOnSilence: false" in source
    assert "openai-transcription-webrtc-adapter.js?v=20260815-confer8" in source
    assert "const requestId = crypto.randomUUID()" in source
    assert "request_id: requestId" in source
    assert "voice_provider: this._provider" in source
    assert "speech_url" in source
    assert "OpenAITranscriptionWebRTCAdapter" in source
    assert "GrokBackend" not in source
    assert "OpenClaw" not in source


def test_confer_uses_provider_turn_detection_not_browser_timer():
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
    assert "autoCommitOnSilence: false" in controller
    assert "silenceMs:" not in controller
