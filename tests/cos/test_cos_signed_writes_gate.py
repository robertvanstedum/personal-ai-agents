"""F3 (partnership v1.1 B0-2; spec A02): every CoS write needs the portal's signed assertion, default deny. On the
real CoS app: a write with forwarded owner headers but no signature is refused on every browser route and on /chat;
Master Craftsman's service:mc signature is accepted on /chat only; a signed owner must match the forwarded owner;
token-authenticated /internal/ routes are untouched; /event is still open (a recorded follow-up)."""
import io
import sys

import pytest

from core import portal_assertion as PA
from owner_signing import OWNER_SIGNING_KEY
from test_history_wiring import _load

OWNER = {"X-Minimoi-Auth-Id": "1", "X-Minimoi-User-Tier": "owner"}
UNSIGNED = {**OWNER, "X-Minimoi-Voice-Write-Verified": "1", "X-Test-Unsigned": "1"}


@pytest.fixture
def cos(monkeypatch, tmp_path):
    yield _load(monkeypatch, tmp_path, history_on=True)
    sys.modules.pop("domains.cos.chief_of_staff", None)


def signed(subject, path, body):
    return {"X-Portal-Owner-Assertion": PA.sign(OWNER_SIGNING_KEY, audience="cos-owner", subject=subject,
                                                method="POST", target=path, body=body, env="test"),
            "Content-Type": "application/json", "X-Test-Unsigned": "1"}


@pytest.mark.parametrize("path,kwargs", [
    ("/ui/send", {"json": {"text": "Delete the old room."}}),
    ("/chat", {"json": {"text": "Delete the old room."}}),
    ("/ui/upload", {"data": {"file": (io.BytesIO(b"x"), "a.txt")}, "content_type": "multipart/form-data"}),
    ("/ui/transcribe", {"data": {"audio": (io.BytesIO(b"x"), "a.webm")}, "content_type": "multipart/form-data"}),
    ("/ui/history/conversations", {"json": {"title": "x"}}),
    ("/ui/history/preferences", {"json": {"device": "desk", "picture": "faded"}}),
    ("/ui/history/selection", {"json": {"device": "desk", "conversation_id": "owner"}}),
])
def test_forwarded_owner_headers_alone_never_make_a_cos_write(cos, path, kwargs):
    r = cos.app.test_client().post(path, headers=UNSIGNED, **kwargs)
    assert r.status_code == 403 and r.get_json()["error"] == "not_allowed", (path, r.status_code)
    assert cos._test_sent == []                                       # no model turn ran


def test_mc_signed_chat_is_accepted_and_only_on_chat(cos):
    client = cos.app.test_client()
    body = b'{"text": "hello", "conversation_id": "guild-abc"}'
    assert client.post("/chat", data=body, headers=signed("service:mc", "/chat", body)).status_code == 200
    assert len(cos._test_sent) == 1
    send = b'{"text": "hello"}'
    assert client.post("/ui/send", data=send, headers=signed("service:mc", "/ui/send", send)).status_code == 403


def test_a_signed_owner_must_be_the_forwarded_owner(cos):
    client = cos.app.test_client()
    body = b'{"text": "hello"}'
    other = {**signed("owner:2", "/ui/send", body), **OWNER}
    assert client.post("/ui/send", data=body, headers=other).status_code == 403
    same = {**signed("owner:1", "/ui/send", body), **OWNER}
    assert client.post("/ui/send", data=body, headers=same).status_code == 200


def test_reads_and_internal_token_routes_are_not_gated_and_event_is_the_recorded_exception(cos):
    client = cos.app.test_client()
    assert client.get("/ui/confer", headers={**OWNER, "X-Test-Unsigned": "1"}).status_code == 200
    receipt = client.post("/internal/model-gateway/receipt", json={}, headers={"X-Test-Unsigned": "1"})
    assert receipt.status_code == 401 and receipt.get_json() == {"error": "unauthorized"}   # its own token check
    event = client.post("/event", json={"source": "test", "event_type": "ping"}, headers={"X-Test-Unsigned": "1"})
    assert event.status_code != 403                                   # follow-up: /event has no caller identity yet
