"""Contract tests only: stub transports never prove live H1 participation."""
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
import requests

from domains.cos.backends.openclaw_backend import AgentRuntimeError, OpenClawBackend


REQUEST_ID = "123e4567-e89b-12d3-a456-426614174000"
RUN_ID = "chatcmpl_b041a9b2-ead0-44c3-9cd6-10ba1da044a6"


def completion():
    return {"id": RUN_ID, "object": "chat.completion", "model": "openclaw/cos-agent-a",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": " Reply "}}]}


def context():
    return {"confer": {"receipt_id": REQUEST_ID, "conversation_id": "synthetic-session"}}


def backend(payload, calls):
    def post(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(payload, Exception):
            raise payload
        class Response:
            status_code = 200
            def json(self):
                return deepcopy(payload)
        return Response()
    return OpenClawBackend(lambda _: None, lambda *_: None,
                           gateway_url="http://cos-agent-a:18789/v1",
                           gateway_token="synthetic-test-token", agent_id="cos-agent-a", http_post=post)


def test_response_identity_is_captured_not_taken_from_context_or_prose():
    calls = []
    ctx = context()
    ctx["openclaw_run_id"] = "fabricated-context-id"
    ctx["confer"]["turn_id"] = "another-caller-id"
    payload = completion()
    payload["choices"][0]["message"]["content"] = "I claim my run is fabricated-prose-id."
    result = backend(payload, calls).call_backend_with_evidence("hello", ctx, {})
    assert result.coordination_request_id == REQUEST_ID
    assert result.openclaw_run_id == RUN_ID
    assert result.openclaw_run_id != result.coordination_request_id
    assert result.agent_id == "cos-agent-a"
    assert result.mode == "actual_agent_response"
    assert len(calls) == 1
    assert calls[0][1]["allow_redirects"] is False
    assert "synthetic-test-token" not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.openclaw_run_id = "replacement"


@pytest.mark.parametrize("request_id", [None, "bad", 123, "123E4567-E89B-12D3-A456-426614174000"])
def test_bad_correlation_is_rejected_before_inference(request_id):
    calls = []
    ctx = context()
    ctx["confer"]["receipt_id"] = request_id
    with pytest.raises(ValueError, match="coordination request UUID"):
        backend(completion(), calls).call_backend_with_evidence("hello", ctx, {})
    assert calls == []


@pytest.mark.parametrize("field,value", [
    ("id", None), ("id", REQUEST_ID), ("id", "chatcmpl_" + REQUEST_ID),
    ("id", RUN_ID + "\n"), ("id", "caller-generated"),
    ("object", "acknowledgement"), ("model", "minimoi-cos-agent"),
    ("model", "openclaw/another-agent"), ("choices", []), ("choices", None),
])
def test_invalid_runtime_evidence_never_becomes_a_result(field, value):
    payload = completion()
    payload[field] = value
    calls = []
    with pytest.raises(AgentRuntimeError, match="valid execution evidence"):
        backend(payload, calls).call_backend_with_evidence("hello", context(), {})
    assert len(calls) == 1  # No fallback or inference retry.


@pytest.mark.parametrize("change", ["length", "tool_calls", "function_call", "refusal", "role", "empty"])
def test_only_completed_assistant_text_is_accepted(change):
    payload = completion()
    choice = payload["choices"][0]
    message = choice["message"]
    if change == "length":
        choice["finish_reason"] = "length"
    elif change == "role":
        message["role"] = "tool"
    elif change == "empty":
        message["content"] = " "
    else:
        message[change] = ["unexpected"]
    with pytest.raises(AgentRuntimeError):
        backend(payload, []).call_backend_with_evidence("hello", context(), {})


def test_timeout_is_uncertain_and_not_retried():
    calls = []
    with pytest.raises(AgentRuntimeError, match="timed out"):
        backend(requests.Timeout(), calls).call_backend_with_evidence("hello", context(), {})
    assert len(calls) == 1


def test_legacy_text_contract_does_not_require_new_evidence():
    calls = []
    legacy = {"choices": [{"message": {"content": " legacy reply "}}]}
    assert backend(legacy, calls).call_backend("hello", {}, {}) == "legacy reply"
    assert len(calls) == 1
