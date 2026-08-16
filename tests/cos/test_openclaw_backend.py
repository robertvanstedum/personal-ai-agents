"""Contract tests for the COS Agent A HTTP backend adapter."""

import requests
import pytest

from domains.cos.backends.openclaw_backend import (
    AgentRuntimeError,
    AgentSessionRecoveryRequired,
    OpenClawBackend,
)


class StubResponse:
    def __init__(self, *, status_code=200, payload=None, json_error=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        self.text = text

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


def _context(conversation_id="owner"):
    return {
        "system_prompt": "platform context",
        "confer": {
            "conversation_id": conversation_id,
            "input_channel": "html_text",
        },
    }


def _backend(http_post, **overrides):
    return OpenClawBackend(
        write_memory=lambda entry: True,
        dispatch_tool=lambda name, args: {},
        gateway_url=overrides.get("gateway_url", "http://cos-agent-a:18789/v1"),
        gateway_token=overrides.get("gateway_token", "test-secret-token"),
        agent_id=overrides.get("agent_id", "cos-agent-a"),
        model_label=overrides.get("model_label", "xai/grok-4"),
        http_post=http_post,
    )


def test_success_uses_agent_target_opaque_session_and_platform_context():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return StubResponse(payload={
            "choices": [{"message": {"content": "  Olá, Robert.  "}}],
            "model": "openclaw/cos-agent-a",
        })

    backend = _backend(post)
    reply = backend.call_backend("  hello  ", _context("private-owner-id"), {
        "observation": True,
        "mutation": False,
    })

    assert reply == "Olá, Robert."
    assert backend.backend_label == "COS Agent A (OpenClaw)"
    assert backend.model_label == "xai/grok-4"
    assert len(calls) == 1
    url, request = calls[0]
    assert url == "http://cos-agent-a:18789/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-secret-token"
    assert request["timeout"] == (5, 120)
    assert request["allow_redirects"] is False
    assert request["json"]["model"] == "openclaw/cos-agent-a"
    assert request["json"]["stream"] is False
    assert len(request["json"]["messages"]) == 2
    assert request["json"]["messages"][0]["role"] == "system"
    assert request["json"]["messages"][1] == {
        "role": "user",
        "content": "hello",
    }
    system_message = request["json"]["messages"][0]["content"]
    assert system_message.startswith("platform context\n\n")
    assert "Authoritative COS platform time:" in system_message
    assert "(America/Chicago)" in system_message
    assert "owner's current local calendar date is" in system_message
    assert "happened today and must never be called yesterday" in system_message
    assert "conflicting UTC-relative label" in system_message
    session_user = request["json"]["user"]
    assert session_user.startswith("cos-confer:")
    assert "private-owner-id" not in session_user


def test_session_mapping_is_stable_and_separates_conversations():
    backend = _backend(lambda *args, **kwargs: None)

    first = backend._session_user("conversation-a")
    assert first == backend._session_user("conversation-a")
    assert first != backend._session_user("conversation-b")
    assert len(first.removeprefix("cos-confer:")) == 32


def test_relative_date_is_resolved_next_to_user_prompt(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        return StubResponse(payload={
            "choices": [{"message": {"content": "date resolved"}}],
        })

    monkeypatch.setenv("COS_AGENT_TIMEZONE", "America/Chicago")
    backend = _backend(post)
    assert backend.call_backend("score today?", _context(), {}) == "date resolved"

    user_message = calls[0]["json"]["messages"][-1]["content"]
    assert user_message.endswith("\n\nscore today?")
    assert "Robert's local calendar date is" in user_message
    assert "in America/Chicago" in user_message
    assert "is today" in user_message


def test_receipt_id_is_embedded_only_in_system_context_for_gateway_correlation():
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        return StubResponse(payload={
            "choices": [{"message": {"content": "receipt ok"}}],
        })

    context = _context()
    context["confer"]["receipt_id"] = "123e4567-e89b-12d3-a456-426614174000"
    backend = _backend(post)
    assert backend.call_backend("hello", context, {}) == "receipt ok"

    messages = calls[0]["json"]["messages"]
    assert (
        "minimoi-routing-receipt:123e4567-e89b-12d3-a456-426614174000;"
        "logical-model:minimoi-cos-agent"
    ) in messages[0]["content"]
    assert "minimoi-routing-receipt" not in messages[-1]["content"]


def test_reset_uses_same_mapped_session_and_supported_gateway_command():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return StubResponse(payload={
            "choices": [{"message": {"content": "Session reset."}}],
        })

    backend = _backend(post)
    assert backend.reset_conversation("conversation-a") is True
    assert calls[0][1]["json"]["messages"] == [
        {"role": "user", "content": "/reset"},
    ]
    assert calls[0][1]["json"]["user"] == backend._session_user("conversation-a")


def test_corrupt_provider_history_returns_explicit_recovery_instruction():
    backend = _backend(lambda *args, **kwargs: StubResponse(
        status_code=500,
        text='provider rejected "encrypted_content" in stored history',
    ))

    with pytest.raises(AgentSessionRecoveryRequired, match="Send /new"):
        backend.call_backend("continue", _context(), {})


@pytest.mark.parametrize("url", ["", "ws://cos-agent-a:18789", "not-a-url"])
def test_invalid_runtime_url_fails_before_any_request(url):
    with pytest.raises(ValueError, match=r"http\(s\) URL"):
        _backend(lambda *args, **kwargs: None, gateway_url=url)


def test_missing_token_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("COS_AGENT_RUNTIME_TOKEN", raising=False)
    with pytest.raises(ValueError, match="COS_AGENT_RUNTIME_TOKEN is required"):
        _backend(lambda *args, **kwargs: None, gateway_token="")


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (requests.exceptions.Timeout(), "timed out"),
        (requests.exceptions.ConnectionError(), "unavailable"),
        (requests.exceptions.RequestException(), "request failed"),
    ],
)
def test_transport_failures_are_safe_and_do_not_leak_token(failure, expected):
    def post(*args, **kwargs):
        raise failure

    backend = _backend(post, gateway_token="never-show-this-token")
    with pytest.raises(AgentRuntimeError) as caught:
        backend.call_backend("hello", _context(), {})

    assert expected in str(caught.value)
    assert "never-show-this-token" not in str(caught.value)


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (StubResponse(status_code=401), "authentication failed"),
        (StubResponse(status_code=302), "HTTP 302"),
        (StubResponse(status_code=500), "HTTP 500"),
        (StubResponse(payload={"choices": []}), "invalid response"),
        (StubResponse(json_error=ValueError("bad json")), "invalid response"),
        (StubResponse(payload={"choices": [{"message": {"content": ""}}]}), "no visible reply"),
    ],
)
def test_http_and_payload_failures_are_explicit(response, expected):
    backend = _backend(lambda *args, **kwargs: response)
    with pytest.raises(AgentRuntimeError, match=expected):
        backend.call_backend("hello", _context(), {})


def test_empty_prompt_is_rejected_without_calling_runtime():
    backend = _backend(lambda *args, **kwargs: pytest.fail("runtime was called"))
    with pytest.raises(ValueError, match="prompt must not be empty"):
        backend.call_backend("   ", _context(), {})
