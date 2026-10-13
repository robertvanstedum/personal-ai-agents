"""The isolated alpha cannot enable incomplete shared voice, even in staging."""
import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_turns import API, turned  # noqa: F401
from minimoi_portal.guild_ui import dictation, mc_voice, voice_release, feature_enabled


@pytest.mark.parametrize("method,path", [
    ("get", "/dictation/capabilities"), ("post", "/dictation/bootstrap"),
    ("get", "/dictation/static/realtime-memo-controller.js"),
    ("get", "/mc/voice/capabilities"), ("post", "/mc/voice/bootstrap"),
    ("get", "/mc/voice/speech/alpha-voice-note-01"),
    ("get", "/mc/voice/static/realtime-confer-controller.js"),
])
def test_flags_cannot_enable_partial_voice_routes(turned, monkeypatch, method, path):
    monkeypatch.setenv("MINIMOI_DICTATION", "1")
    monkeypatch.setenv("MINIMOI_MC_VOICE", "1")
    client = turned.owner()
    token = turned.csrf(client)
    response = getattr(client, method)(API + path, headers=write_headers(token))
    assert response.status_code == 404
    assert response.get_json()["ok"] is False


def test_flags_cannot_expose_controls_or_provider_origin(turned, monkeypatch):
    monkeypatch.setenv("MINIMOI_DICTATION", "1")
    monkeypatch.setenv("MINIMOI_MC_VOICE", "1")
    response = turned.owner().get("/guild-next/guild/build")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "data-mc-voice" not in html and "data-dictation" not in html
    assert "https://api.openai.com" not in response.headers["Content-Security-Policy"]
    with turned.app.test_request_context("/guild-next/guild/build"):
        assert feature_enabled("MINIMOI_MC_VOICE") and feature_enabled("MINIMOI_DICTATION")
        assert not mc_voice.enabled() and not dictation.enabled()
    assert voice_release.SHARED_VOICE_READY is False
    explicit_flags = {"MINIMOI_DICTATION": "1", "MINIMOI_MC_VOICE": "1"}
    assert not dictation.enabled(explicit_flags) and not mc_voice.enabled(explicit_flags)


def test_ready_voice_requires_the_real_shared_interfaces():
    """Minimum compatibility guard; full controller behavior belongs to the voice slice."""
    if voice_release.SHARED_VOICE_READY:
        import inspect
        from pathlib import Path
        from core.realtime_voice.providers.openai_speech import create_speech_stream
        assert "instructions" in inspect.signature(create_speech_stream).parameters
        shared = Path(__file__).resolve().parents[3] / "core/realtime_voice/static"
        for name in ("realtime-confer-controller.js", "realtime-memo-controller.js"):
            assert "requestHeaders" in (shared / name).read_text()
