from unittest.mock import Mock, patch

import pytest
from flask import Flask

from core.realtime_voice.bootstrap import _rate_limit_state
from core.realtime_voice.confer import create_confer_voice_blueprint
from core.realtime_voice.providers import openai_speech
from domains.cos.spoken_reply_store import SpokenReplyStore


def _client():
    _rate_limit_state.clear()
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(create_confer_voice_blueprint(
        build_voice_instructions=lambda user_id: f"COS voice for {user_id}",
    ))
    return app.test_client()


def test_confer_capabilities_require_identity():
    response = _client().get("/api/realtime-voice/confer/capabilities")
    assert response.status_code == 401


def test_confer_capabilities_advertise_realtime_provider_choices():
    response = _client().get(
        "/api/realtime-voice/confer/capabilities",
        headers={"X-Minimoi-Auth-Id": "42"},
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["mode"] == "agent_conversation"
    assert data["default_provider"] == "openai"
    assert [provider["provider"] for provider in data["providers"]] == ["openai", "xai"]


def test_confer_bootstrap_mints_openai_realtime_conversation_credential():
    with patch(
        "core.realtime_voice.confer.openai_realtime.mint_ephemeral_credential",
        return_value={
            "provider": "openai",
            "client_secret": "ephemeral",
            "model": "gpt-realtime-2.1",
        },
    ) as mint:
        response = _client().post(
            "/api/realtime-voice/confer/bootstrap",
            json={"provider": "openai"},
            headers={"X-Minimoi-Auth-Id": "42"},
        )
    assert response.status_code == 200
    assert response.get_json()["model"] == "gpt-realtime-2.1"
    kwargs = mint.call_args.kwargs
    assert kwargs["instructions"].startswith("COS voice for 42\n\nSession facts:")
    assert "active voice provider is OpenAI (openai)" in kwargs["instructions"]
    assert "completed transcript to Confer" in kwargs["instructions"]
    assert kwargs["voice"] == "cedar"
    assert kwargs["user_id_for_safety_identifier"] == "42"
    assert kwargs["tool_choice"] == "auto"
    assert [tool["name"] for tool in kwargs["tools"]] == [
        "consult_cos_agent", "save_cos_note",
    ]
    note_tool = kwargs["tools"][1]
    assert "Do not prefix it with 'User asked'" in (
        note_tool["parameters"]["properties"]["note"]["description"]
    )


def test_confer_bootstrap_returns_configured_session_duration(monkeypatch):
    monkeypatch.setenv("VOICE_CONFER_WARNING_MINUTES", "18")
    monkeypatch.setenv("VOICE_CONFER_MAX_MINUTES", "20")
    with patch(
        "core.realtime_voice.confer.openai_realtime.mint_ephemeral_credential",
        return_value={"provider": "openai", "client_secret": "ephemeral"},
    ) as mint:
        response = _client().post(
            "/api/realtime-voice/confer/bootstrap",
            json={"provider": "openai"},
            headers={"X-Minimoi-Auth-Id": "42"},
        )

    assert response.status_code == 200
    assert response.get_json()["max_minutes"] == 20
    assert mint.call_count == 1


def test_confer_bootstrap_mints_xai_realtime_conversation_credential():
    with patch(
        "core.realtime_voice.confer.xai_voice.mint_ephemeral_credential",
        return_value={
            "provider": "xai",
            "ephemeral_token": "ephemeral",
            "model": "grok-voice-latest",
            "session_config": {},
        },
    ) as mint:
        response = _client().post(
            "/api/realtime-voice/confer/bootstrap",
            json={"provider": "xai"},
            headers={"X-Minimoi-Auth-Id": "43"},
        )
    assert response.status_code == 200
    assert response.get_json()["model"] == "grok-voice-latest"
    assert mint.call_args.kwargs["instructions"].startswith(
        "COS voice for 43\n\nSession facts:"
    )
    assert "active voice provider is Grok (xai)" in mint.call_args.kwargs["instructions"]
    assert mint.call_args.kwargs["tool_choice"] == "auto"


def test_spoken_reply_store_is_user_scoped_and_bounded(monkeypatch):
    clock = iter([10.0, 10.0, 10.0, 10.0, 14.0])
    monkeypatch.setattr("domains.cos.spoken_reply_store.time.monotonic", lambda: next(clock))
    store = SpokenReplyStore(ttl_seconds=3, max_entries=1)
    store.put("turn-1", text="first", provider="openai", user_id="7")
    assert store.get("turn-1", user_id="8") is None
    store.put("turn-2", text="second", provider="openai", user_id="7")
    assert store.get("turn-1", user_id="7") is None
    assert store.get("turn-2", user_id="7") is None


def test_openai_speech_stream_uses_exact_server_owned_reply():
    provider_response = Mock()
    with (
        patch.object(openai_speech, "get_secret", return_value="secret"),
        patch.object(openai_speech.requests, "post", return_value=provider_response) as post,
    ):
        result = openai_speech.create_speech_stream(
            text="Canonical CoS reply.",
            user_id="42",
        )
    assert result is provider_response
    assert post.call_args.kwargs["json"]["input"] == "Canonical CoS reply."
    assert post.call_args.kwargs["json"]["model"] == "gpt-4o-mini-tts"
    assert post.call_args.kwargs["stream"] is True


def test_openai_speech_translates_secret_store_failure():
    with patch.object(
        openai_speech,
        "get_secret",
        side_effect=RuntimeError("secret backend unavailable"),
    ):
        with pytest.raises(
            openai_speech.OpenAISpeechError,
            match="OpenAI API key not configured",
        ):
            openai_speech.create_speech_stream(text="Reply.", user_id="42")


# ── Phase A: every Confer bootstrap logs one outcome line, like Gespräche ────

def _outcome_lines(capsys):
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith("[realtime_voice] domain=cos")]


def test_confer_bootstrap_logs_started_with_instruction_size(capsys):
    with patch(
        "core.realtime_voice.confer.openai_realtime.mint_ephemeral_credential",
        return_value={"provider": "openai", "client_secret": "ephemeral", "model": "gpt-realtime-2.1"},
    ) as mint:
        response = _client().post(
            "/api/realtime-voice/confer/bootstrap",
            json={"provider": "openai"},
            headers={"X-Minimoi-Auth-Id": "42"},
        )
    assert response.status_code == 200
    size = len(mint.call_args.kwargs["instructions"])
    lines = _outcome_lines(capsys)
    assert lines == [
        f"[realtime_voice] domain=cos user_id=42 provider=openai model=gpt-realtime-2.1 "
        f"outcome=started instructions_chars={size}"
    ]
    assert "COS voice for" not in "\n".join(lines)          # sizes only, never content
    assert "ephemeral" not in "\n".join(lines)


def test_confer_bootstrap_logs_every_refusal(capsys):
    client = _client()
    assert client.post("/api/realtime-voice/confer/bootstrap", json={}).status_code == 401
    assert client.post(
        "/api/realtime-voice/confer/bootstrap", json={"provider": "nope"},
        headers={"X-Minimoi-Auth-Id": "42"},
    ).status_code == 400
    with patch(
        "core.realtime_voice.confer.openai_realtime.mint_ephemeral_credential",
        side_effect=__import__("core.realtime_voice.providers.openai_realtime", fromlist=["x"]).OpenAIRealtimeError("down"),
    ):
        assert client.post(
            "/api/realtime-voice/confer/bootstrap", json={"provider": "openai"},
            headers={"X-Minimoi-Auth-Id": "42"},
        ).status_code == 502
    outcomes = [line.split("outcome=")[1].split()[0] for line in _outcome_lines(capsys)]
    assert outcomes == ["identity_required", "invalid_provider", "provider_error"]


def test_confer_bootstrap_logs_rate_limited(capsys, monkeypatch):
    monkeypatch.setattr("core.realtime_voice.confer.check_voice_rate_limit", lambda user_id: False)
    response = _client().post(
        "/api/realtime-voice/confer/bootstrap", json={"provider": "openai"},
        headers={"X-Minimoi-Auth-Id": "42"},
    )
    assert response.status_code == 429
    assert _outcome_lines(capsys) == [
        "[realtime_voice] domain=cos user_id=42 provider=None model=None outcome=rate_limited"
    ]


def test_confer_bootstrap_logs_unavailable_instructions(capsys):
    _rate_limit_state.clear()
    app = Flask(__name__)
    app.register_blueprint(create_confer_voice_blueprint(build_voice_instructions=None))
    response = app.test_client().post(
        "/api/realtime-voice/confer/bootstrap", json={"provider": "openai"},
        headers={"X-Minimoi-Auth-Id": "42"},
    )
    assert response.status_code == 503
    assert _outcome_lines(capsys)[0].endswith("provider=openai model=None outcome=provider_unavailable")


# ── #281 review: session context at bootstrap (F2) and error codes (F6) ──────

def test_confer_bootstrap_hands_the_page_the_session_context():
    _rate_limit_state.clear()
    app = Flask(__name__)
    app.register_blueprint(create_confer_voice_blueprint(
        build_voice_instructions=lambda user_id: "COS",
        session_context=lambda user_id: {"available": True, "private": True, "epoch": "abc"},
    ))
    with patch("core.realtime_voice.confer.openai_realtime.mint_ephemeral_credential",
               return_value={"provider": "openai", "client_secret": "ephemeral"}):
        data = app.test_client().post("/api/realtime-voice/confer/bootstrap", json={"provider": "openai"},
                                      headers={"X-Minimoi-Auth-Id": "42"}).get_json()
    assert data["session_context"] == {"available": True, "private": True, "epoch": "abc"}


def test_confer_bootstrap_without_a_context_hook_adds_none():
    with patch("core.realtime_voice.confer.openai_realtime.mint_ephemeral_credential",
               return_value={"provider": "openai", "client_secret": "ephemeral"}):
        data = _client().post("/api/realtime-voice/confer/bootstrap", json={"provider": "openai"},
                              headers={"X-Minimoi-Auth-Id": "42"}).get_json()
    assert "session_context" not in data


def test_a_session_ending_provider_error_logs_its_code_and_type_only(capsys):
    response = _client().post("/api/realtime-voice/confer/outcome", headers={"X-Minimoi-Auth-Id": "42"}, json={
        "outcome": "provider_error", "provider": "xai", "reason": "provider_error",
        "code": "conversation_busy", "type": "invalid_request_error", "detail": "SECRET MESSAGE"})
    assert response.status_code == 200
    assert _outcome_lines(capsys) == [
        "[realtime_voice] domain=cos user_id=42 provider=xai model=None outcome=session_provider_error "
        "reason=provider_error code=conversation_busy type=invalid_request_error"]


@pytest.mark.parametrize("body,status", [
    ({"outcome": "provider_error", "code": "bad code with spaces; rm -rf"}, 200),   # kept, code shown as none
    ({"outcome": "anything"}, 400),
])
def test_the_outcome_route_accepts_only_known_outcomes_and_safe_codes(capsys, body, status):
    response = _client().post("/api/realtime-voice/confer/outcome", headers={"X-Minimoi-Auth-Id": "42"}, json=body)
    assert response.status_code == status
    out = "\n".join(_outcome_lines(capsys))
    assert "rm -rf" not in out
    if status == 200:
        assert "code=none" in out and "provider=unknown" in out


def test_the_outcome_route_needs_identity_and_small_json():
    client = _client()
    assert client.post("/api/realtime-voice/confer/outcome", json={"outcome": "provider_error"}).status_code == 401
    big = client.post("/api/realtime-voice/confer/outcome", data=b'{"outcome":"provider_error","x":"' + b"y" * 3000 + b'"}',
                      headers={"X-Minimoi-Auth-Id": "42", "Content-Type": "application/json"})
    assert big.status_code == 400


def test_the_language_pages_have_the_same_outcome_route(capsys):
    from core.realtime_voice.bootstrap import create_bootstrap_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_bootstrap_blueprint(domain="german", locale="de-DE",
                                                      get_persona=lambda name: None, is_production=lambda: False))
    response = app.test_client().post("/api/realtime-voice/outcome", headers={"X-Minimoi-Auth-Id": "42"},
                                      json={"outcome": "provider_error", "provider": "openai", "code": "server_error"})
    assert response.status_code == 200
    assert "domain=german user_id=42 provider=openai model=None outcome=session_provider_error" in capsys.readouterr().out
