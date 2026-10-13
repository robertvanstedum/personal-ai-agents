"""The Chief of Staff's existing speech and upload-transcription routes: unchanged until MINIMOI_VOICE_BUDGET=1, then gated by the shared voice budget before any provider call.
The CoS app is imported with its scheduler stubbed; no network, no paid call."""
from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock

import pytest

from core.realtime_voice import budget as B


@pytest.fixture
def cos(monkeypatch, tmp_path):
    for name in ("apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background", "apscheduler.triggers", "apscheduler.triggers.cron", "apscheduler.triggers.interval"):
        monkeypatch.setitem(sys.modules, name, MagicMock())
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/none")
    monkeypatch.setenv(B.ENV_DSN, f"sqlite:///{tmp_path / 'vb.db'}")
    monkeypatch.setenv("MINIMOI_LIMITS_DIR", str(tmp_path / "limits"))
    monkeypatch.delenv(B.ENV_ENFORCE, raising=False)
    monkeypatch.setattr(B, "_default", None)
    sys.modules.pop("domains.cos.chief_of_staff", None)
    import domains.cos.chief_of_staff as module
    from core.realtime_voice.providers import openai_speech
    spoken = []

    class Upstream:
        def iter_content(self, chunk_size=0):
            yield b"ID3"

        def close(self):
            pass
    monkeypatch.setattr(openai_speech, "create_speech_stream", lambda **kw: (spoken.append(kw), Upstream())[1])
    module.spoken = spoken
    module.ledger = B.default_budget()
    yield module
    sys.modules.pop("domains.cos.chief_of_staff", None)


def _turn(cos, text="A short reply from the Chief of Staff."):
    cos._spoken_replies.put("turn-1", text=text, provider="openai", user_id="7")
    return cos.app.test_client(), {"X-Minimoi-Auth-Id": "7"}


def test_speech_is_unchanged_when_the_budget_is_not_switched_on(cos):
    client, headers = _turn(cos)
    r = client.get("/ui/speech/turn-1", headers=headers)
    assert r.status_code == 200 and r.data == b"ID3" and len(cos.spoken) == 1
    assert not B.default_dsn() or not __import__("os").path.exists(B.default_dsn()[len("sqlite:///"):])         # no ledger was even created


def test_speech_reserves_then_refuses_at_the_cap_without_a_provider_call(cos, monkeypatch):
    monkeypatch.setenv(B.ENV_ENFORCE, "1")
    cos.ledger.ensure_schema()
    client, headers = _turn(cos)
    assert client.get("/ui/speech/turn-1", headers=headers).status_code == 200
    assert cos.ledger.status()["reserved_usd"] == B.tts_allowance_micro("A short reply from the Chief of Staff.") / B.MICRO
    cos.ledger.reserve(kind="tts", domain="cos", principal="x", amount_micro=B.micro(19.99))
    r = client.get("/ui/speech/turn-1", headers=headers)
    assert r.status_code == 429 and "used up" in r.get_json()["error"] and len(cos.spoken) == 1


def test_speech_fails_closed_without_a_ledger_when_enforced(cos, monkeypatch):
    monkeypatch.setenv(B.ENV_ENFORCE, "1")                       # tables were never created
    client, headers = _turn(cos)
    r = client.get("/ui/speech/turn-1", headers=headers)
    assert r.status_code == 503 and not cos.spoken


def test_upload_transcription_is_gated_by_the_upload_size_when_enforced(cos, monkeypatch):
    import openai
    calls = []

    class FakeOpenAI:
        def __init__(self, api_key=None):
            self.audio = MagicMock()
            self.audio.transcriptions.create = lambda **kw: (calls.append(kw), "hello")[1]
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    import core.get_secret as gs
    monkeypatch.setattr(gs, "get_secret", lambda *a, **k: "sk-test")
    client = cos.app.test_client()
    clip = lambda n: {"audio": (io.BytesIO(b"x" * n), "clip.webm")}
    r = client.post("/ui/transcribe", data=clip(1024), content_type="multipart/form-data")
    assert r.status_code == 200 and r.get_json() == {"transcript": "hello"} and len(calls) == 1                  # not enforced: unchanged

    monkeypatch.setenv(B.ENV_ENFORCE, "1")
    cos.ledger.ensure_schema()
    r = client.post("/ui/transcribe", data=clip(2 * 1024 * 1024), content_type="multipart/form-data")
    assert r.status_code == 200 and cos.ledger.status()["reserved_usd"] == B.upload_allowance_micro(2 * 1024 * 1024) / B.MICRO and len(calls) == 2   # estimated from size, not from duration
    cos.ledger.reserve(kind="tts", domain="cos", principal="x", amount_micro=B.micro(19.60))
    r = client.post("/ui/transcribe", data=clip(1024 * 1024), content_type="multipart/form-data")
    assert r.status_code == 429 and "used up" in r.get_json()["error"] and len(calls) == 2
    big = client.post("/ui/transcribe", data=clip(B.UPLOAD_MAX_BYTES + 1), content_type="multipart/form-data")
    assert big.status_code == 413 and len(calls) == 2                                                         # over the strict size bound: refused before any reservation or provider call


def test_a_large_upload_is_not_limited_when_the_budget_is_not_switched_on(cos, monkeypatch):
    import openai
    calls = []
    monkeypatch.setattr(openai, "OpenAI", lambda api_key=None: MagicMock(audio=MagicMock(transcriptions=MagicMock(create=lambda **kw: (calls.append(kw), "ok")[1]))))
    import core.get_secret as gs
    monkeypatch.setattr(gs, "get_secret", lambda *a, **k: "sk-test")
    r = cos.app.test_client().post("/ui/transcribe", data={"audio": (io.BytesIO(b"x" * (B.UPLOAD_MAX_BYTES + 1)), "big.webm")}, content_type="multipart/form-data")
    assert r.status_code == 200 and len(calls) == 1
