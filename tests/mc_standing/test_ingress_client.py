"""Ingress HTTP client status mapping, and the payload resolver, without a network."""
import hashlib
import io
import json
import socket
import urllib.error
import uuid

import pytest

from core.mc_standing import runner
from core.mc_standing.ingress_client import IngressClient
from core.mc_standing.payload import load_brief_text
from core.mc_standing.spool import Spool

EID = str(uuid.uuid4())


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(behaviour):
    seen = []

    def open_(req, timeout):
        seen.append(req)
        kind, value = behaviour
        if kind == "ok":
            return Resp(json.dumps(value).encode())
        if kind == "http":
            raise urllib.error.HTTPError(req.full_url, value, "x", {}, io.BytesIO(b'{"error":"event_id_reused"}'))
        if kind == "refused":
            raise urllib.error.URLError(ConnectionRefusedError())
        if kind == "timeout":
            raise socket.timeout()
        if kind == "reset":
            raise urllib.error.URLError(ConnectionResetError())
    return open_, seen


def client(behaviour):
    open_, seen = opener(behaviour)
    return IngressClient("http://ingress:8780", lambda: "producer-token-xxxxxxxxxxxxxxxxxxxxxxxx", opener=open_), seen


def test_accepted_is_returned_with_bearer_token():
    c, seen = client(("ok", {"state": "accepted", "record_id": "5"}))
    assert c.post({"event_id": EID})["state"] == "accepted"
    assert seen[0].get_header("Authorization").startswith("Bearer ")
    assert seen[0].get_method() == "POST"


@pytest.mark.parametrize("code", [400, 401, 403, 409, 413, 415])
def test_contract_failures_are_refused(code):
    c, _ = client(("http", code))
    out = c.post({"event_id": EID})
    assert out["state"] == "refused"


@pytest.mark.parametrize("behaviour", [("http", 503), ("timeout", None), ("reset", None)])
def test_maybe_committed_is_uncertain(behaviour):
    c, _ = client(behaviour)
    with pytest.raises(runner.IngressUncertain):
        c.post({"event_id": EID})


def test_refused_connection_is_safe_to_retry():
    c, _ = client(("refused", None))
    with pytest.raises(ConnectionError):
        c.post({"event_id": EID})


def test_get_absent_and_unavailable():
    c, _ = client(("http", 404))
    assert c.get(EID) is None
    c, _ = client(("http", 503))
    with pytest.raises(ConnectionError):
        c.get(EID)
    with pytest.raises(ValueError):
        c.get("../../etc")


def test_url_with_credentials_refused():
    with pytest.raises(ValueError):
        IngressClient("http://user:pw@host", lambda: "t")


def test_payload_resolver_returns_digest_matching_text(tmp_path):
    s = Spool(tmp_path)
    text = "MC operations brief, production, 2026-10-21.\nAll 5 checks healthy and current."
    ref = "prod.2026-10-21.p0.morning.brief"
    s.put_immutable("delivery", ref, {"cycle_key": "x", "text": text,
                                      "sha256": hashlib.sha256(text.encode()).hexdigest()})
    data = load_brief_text(tmp_path, ref)
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(text.encode()).hexdigest()
    for bad in ("../secrets", "prod.2026-10-21.p0.morning.site", "/etc/passwd"):
        with pytest.raises(ValueError):
            load_brief_text(tmp_path, bad)
    with pytest.raises(LookupError):
        load_brief_text(tmp_path, "prod.2026-10-22.p0.morning.brief")
