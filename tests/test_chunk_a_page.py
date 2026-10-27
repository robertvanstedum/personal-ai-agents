"""Chunk A page: the record is synthetic, and the host refuses to start without keys."""
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_page_record_counts_without_storing_words():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    done = subprocess.run(
        [node, "--test", str(ROOT / "tests/realtime_voice/test_chunk_a_page.mjs")],
        capture_output=True, text=True, timeout=20,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "chunk A page record: passed" in done.stdout


def test_bootstrap_issues_a_session_and_a_consult_is_one_effect(monkeypatch):
    import core.realtime_voice.bootstrap as bootstrap
    monkeypatch.setattr(bootstrap, "_rate_limit_state", {})
    from core.realtime_voice.bench.serve import create_app
    monkeypatch.setattr(
        "core.realtime_voice.bootstrap.openai_realtime.mint_ephemeral_credential",
        lambda **kwargs: {"provider": "openai", "client_secret": "ek_fake", "expires_at": 1, "model": "gpt-realtime-2.1"},
    )
    client = create_app().test_client()
    started = client.post("/api/realtime-voice/bootstrap", json={"provider": "openai", "persona": "bench"})
    assert started.status_code == 200, started.get_json()
    session_id = started.get_json()["voice_session_id"]
    body = {
        "session_id": session_id, "call_id": "call-1", "name": "consult",
        "owner_transcript_item": "What time is it?", "arguments": {},
    }
    first = client.post("/api/realtime-voice/consult", json=body)
    replay = client.post("/api/realtime-voice/consult", json=body)
    assert first.status_code == 200
    assert replay.get_json() == first.get_json()
    assert "What time is it?" in first.get_json()["spoken"]
    client.application.extensions["voice_ledger"].close_session(session_id)
    late = client.post("/api/realtime-voice/consult", json={**body, "call_id": "call-2"})
    assert late.status_code == 409


def test_host_stops_when_a_voice_key_is_missing():
    serve = (ROOT / "core/realtime_voice/bench/serve.py").read_text()
    assert "find-generic-password" in serve
    assert "Nothing was started" in serve
    assert "127.0.0.1" in serve
    assert "8790" in serve
    assert '"openai", "api_key"' in serve
    assert "os.environ.get(env_name)" in serve
    page = (ROOT / "core/realtime_voice/bench/a_page.html").read_text()
    assert "Provider use is billed" in page
    assert "playback once per session" in page
