from pathlib import Path
from urllib.parse import urljoin


ROOT = Path(__file__).resolve().parents[2]


def test_confer_page_uses_shared_controller_and_keeps_typed_path():
    template = (ROOT / "domains/cos/templates/cos_ui.html").read_text()
    assert "realtime-voice-controller.js?v=20260929-voice3" in template
    assert "bootstrapUrl: '../api/realtime-voice/confer/bootstrap'" in template
    assert 'id="voice-provider-select"' in template
    assert "OpenAI Voice" in template
    assert "Grok Voice" in template
    assert "onFunctionCall" in template
    assert "speech_output: false" in template
    assert "onFinalize: async (result)" in template
    assert 'aria-label="Start voice conversation">🎤</button>' in template
    assert "voiceButton.textContent = voiceActive ? '■' : '🎤'" in template
    assert "Voice is AI-generated." in template
    assert "channel: 'html_text'" in template
    assert "captureWithVAD" not in template
    assert "/ui/transcribe" not in template


def test_confer_uses_the_standard_voice_flow():
    """Phase A: CoS greets first, both sides render live, the transcript is
    posted when voice stops, and the reply toggle is the shared one."""
    template = (ROOT / "domains/cos/templates/cos_ui.html").read_text()
    assert "openingInstruction: null" not in template
    assert "openingInstruction: CONFER_OPENING," in template
    assert "one short, natural line" in template
    assert "onUserTurn: (text) => {\n    if (String(text || '').trim()) appendMsg('user'" in template
    assert "onAssistantTurn: (text) => {\n    if (String(text || '').trim()) appendMsg('cos'" in template
    assert "fetch('voice/transcript', {" in template
    assert "Private: not kept in your CoS history." in template
    assert "voice-reply-mode.js?v=20260929-voice3" in template
    assert 'id="voice-reply-mode"' in template
    assert '<option value="speak" selected>Speak and write</option>' in template
    assert '<option value="write">Write only</option>' in template
    assert "scope: 'cos'" in template
    assert "voiceController.setReplyMode(mode)" in template
    assert "reconnecting:" not in template
    # #281 review: the Private switch, its marks, and the Write only note.
    assert '<button id="btn-private" type="button" aria-pressed="false" disabled>' in template
    assert "fetch('private-mode', {" in template
    assert "The agent itself may still remember it." in template
    assert "appendMsg('cos', data.reply || '(empty reply)', { private: data.private === true });" in template
    assert "mode_epoch: result.session_context?.epoch ?? null," in template
    assert "window.cosPrivate?.known === false" in template          # unknown mode: Private
    assert "the provider still generates (and bills) the audio." in template


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
    assert urljoin(direct_page, "voice/transcript") == "https://cos.example/ui/voice/transcript"
    assert urljoin(portal_page, "voice/transcript") == (
        "https://minimoi.example/app/cos/ui/voice/transcript"
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
