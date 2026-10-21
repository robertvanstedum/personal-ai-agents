"""Ingress HTTP client status mapping, and the read-only payload resolver, without a network.
Covers Codex P-F1/P-F2: exact ack, uncertainty on any possibly-committed response, authenticated
404 only as absence, no redirects, and a resolver that never writes."""
import builtins
import hashlib
import io
import json
import os
import socket
import urllib.error
import uuid

import pytest

from core.mc_standing import runner
from core.mc_standing.ack import SCHEMA
from core.mc_standing.ingress_client import IngressClient
from core.mc_standing.payload import load_brief_text, load_payload
from core.mc_standing.spool import Spool

EID = str(uuid.uuid4())


def ack(state="accepted", event_id=EID, **over):
    doc = {"schema": SCHEMA, "event_id": event_id, "state": state, "record_id": "5", "work_id": None, "code": ""}
    doc.update(over)
    return doc


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
            return Resp(value if isinstance(value, bytes) else json.dumps(value).encode())
        if kind == "http":
            code, body = value if isinstance(value, tuple) else (value, b'{"error":"event_id_reused"}')
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(body))
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


def test_exact_ack_is_returned_with_bearer_token():
    c, seen = client(("ok", ack()))
    assert c.post({"event_id": EID})["state"] == "accepted"
    assert seen[0].get_header("Authorization").startswith("Bearer ")
    assert seen[0].get_method() == "POST"


@pytest.mark.parametrize("code", [400, 401, 403, 409, 413, 415])
def test_contract_failures_are_refused(code):
    c, _ = client(("http", code))
    out = c.post({"event_id": EID})
    assert out == {"state": "refused", "code": "event_id_reused"}


@pytest.mark.parametrize("behaviour", [
    ("http", 500), ("http", 502), ("http", 503), ("http", 504), ("timeout", None), ("reset", None),
    ("ok", ack(event_id=str(uuid.uuid4()))),           # another event's ack
    ("ok", ack(schema="mc-standing-ack/2")), ("ok", ack(state="received")),
    ("ok", b"{not json"), ("ok", b""), ("ok", b'"accepted"'),
    ("ok", json.dumps(ack(pad="x" * 70_000)).encode())])  # oversize
def test_possibly_committed_or_unreadable_success_is_uncertain(behaviour):
    c, _ = client(behaviour)
    with pytest.raises(runner.IngressUncertain):
        c.post({"event_id": EID})


def test_redirects_are_never_followed_and_refused():
    c, _ = client(("http", (307, b"")))
    assert c.post({"event_id": EID}) == {"state": "refused", "code": "redirect_refused"}


def test_default_opener_does_not_follow_redirects():
    import http.server
    import threading
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append((self.path, self.headers.get("Authorization")))
            self.send_response(307)
            self.send_header("Location", "http://127.0.0.1:%d/elsewhere" % self.server.server_port)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    try:
        c = IngressClient(f"http://127.0.0.1:{srv.server_port}", lambda: "tok-" + "x" * 40)
        assert c.post({"event_id": EID}) == {"state": "refused", "code": "redirect_refused"}
    finally:
        t.join(2)
        srv.server_close()
    assert len(hits) == 1                                   # the token went to one place only


def test_refused_connection_is_safe_to_retry():
    c, _ = client(("refused", None))
    with pytest.raises(ConnectionError):
        c.post({"event_id": EID})


def test_get_only_authenticated_404_is_absence():
    c, _ = client(("http", (404, b'{"error":"event_not_found"}')))
    assert c.get(EID) is None
    for behaviour in [("http", (404, b"<html>proxy</html>")), ("http", (404, b"")), ("http", 503),
                      ("http", 502), ("timeout", None), ("ok", b"{bad"), ("ok", ack(event_id=str(uuid.uuid4()))),
                      ("ok", {}), ("http", (301, b""))]:
        c, _ = client(behaviour)
        with pytest.raises(ConnectionError):
            c.get(EID)
    c, _ = client(("ok", ack("already_applied")))
    assert c.get(EID)["state"] == "already_applied"
    with pytest.raises(ValueError):
        c.get("../../etc")


def test_url_with_credentials_refused():
    with pytest.raises(ValueError):
        IngressClient("http://user:pw@host", lambda: "t")


# ── payload resolver (P-F1) ────────────────────────────────────────────────

def _brief(tmp_path):
    s = Spool(tmp_path)
    text = "MC operations brief, production, 2026-10-21.\nAll 5 checks healthy and current."
    ref = "prod.2026-10-21.p0.morning.brief"
    s.put_immutable("delivery", ref, {"cycle_key": "x", "text": text,
                                      "sha256": hashlib.sha256(text.encode()).hexdigest()})
    return ref, text


def test_payload_resolver_returns_digest_matching_text(tmp_path):
    ref, text = _brief(tmp_path)
    data = load_brief_text(tmp_path, ref)
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(text.encode()).hexdigest()
    for bad in ("../secrets", "prod.2026-10-21.p0.morning.site", "/etc/passwd", "learning.../x"):
        with pytest.raises(ValueError):
            load_brief_text(tmp_path, bad)
    with pytest.raises(LookupError):
        load_brief_text(tmp_path, "prod.2026-10-22.p0.morning.brief")


def test_payload_resolver_never_writes_on_a_read_only_mount(tmp_path, monkeypatch):
    ref, text = _brief(tmp_path)
    for d in [tmp_path, *tmp_path.iterdir()]:
        os.chmod(d, 0o500)                                 # read-only, as on the ingress mount
    real_open = os.open

    def no_write_open(path, flags, *a, **k):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT), "resolver tried to write"
        return real_open(path, flags, *a, **k)
    monkeypatch.setattr(os, "open", no_write_open)
    monkeypatch.setattr(os, "mkdir", lambda *a, **k: pytest.fail("mkdir"))
    monkeypatch.setattr(os, "chmod", lambda *a, **k: pytest.fail("chmod"))
    try:
        assert load_payload(tmp_path, ref) == text.encode()
    finally:
        monkeypatch.undo()
        for d in [tmp_path, *tmp_path.iterdir()]:
            os.chmod(d, 0o700)


def test_payload_resolver_refuses_symlinks_and_oversize(tmp_path):
    ref, _ = _brief(tmp_path)
    target = tmp_path / "delivery" / f"{ref}.json"
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps({"text": "not ours"}))
    target.unlink()
    target.symlink_to(elsewhere)
    with pytest.raises(OSError):
        load_payload(tmp_path, ref)
    target.unlink()
    target.write_text(json.dumps({"text": "x" * 300_000}))
    with pytest.raises(LookupError):
        load_payload(tmp_path, ref)


def test_learning_payloads_resolve_by_their_own_refs(tmp_path):
    s = Spool(tmp_path)
    payload = '{"t":1}'
    s.put_immutable("delivery", "learning.0123456789abcdef01234567.0", {"payload": payload, "sha256": "x"})
    assert load_payload(tmp_path, "learning.0123456789abcdef01234567.0") == payload.encode()
    with pytest.raises(ValueError):
        load_payload(tmp_path, "learning.NOTHEX.0")


def test_payload_resolver_never_hangs_on_a_fifo(tmp_path):
    """Codex delta review of 113d1cf6: a FIFO in a restored spool blocked the open."""
    import threading
    _brief(tmp_path)
    ref = "prod.2026-10-22.p0.morning.brief"
    os.mkfifo(tmp_path / "delivery" / f"{ref}.json")
    result = {}

    def call():
        try:
            load_payload(tmp_path, ref)
        except LookupError as exc:
            result["err"] = str(exc)
    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(3)
    assert not t.is_alive(), "resolver blocked on a FIFO"
    assert result["err"] == "payload_invalid"
