"""S1-H: the authority service (core/mc_delegation_dispatcher/server.py) and the door client's reading of replies
(door.HttpDispatcherV2, N-D5). Unit tests, no database: the store is a fake that answers as the real one's shape.
One test runs the thin server over real TLS (a throwaway self-signed certificate) with the real client."""
import hashlib
import io
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.mc_delegation_dispatcher import door, server  # noqa: E402
from core.mc_delegation_dispatcher.store import AuthorityRefused  # noqa: E402
from core.mc_delegation_runner import cli, host as host_mod, runner  # noqa: E402

HOST_TOKEN = "h" * 48
OTHER_TOKEN = "o" * 48
READER_TOKEN = "r" * 48
HOSTS = {"mac-test-1": hashlib.sha256(HOST_TOKEN.encode()).hexdigest(),
         "staging-1": hashlib.sha256(OTHER_TOKEN.encode()).hexdigest()}
READERS = {"collector": {"sha256": hashlib.sha256(READER_TOKEN.encode()).hexdigest(), "role": "observer"}}
FACTS = {"host_id": "mac-test-1", "bundle_digest": "sha256:" + "a" * 64, "roles": ["worker", "witness"],
         "availability": "intermittent", "grace": 900, "last_gap_s": 0, "announced": "online"}


def beat(runner_id="mac-test-1:runner"):
    return {"op": "heartbeat", "protocol": 2, "runner_id": runner_id, "state": "online",
            "capabilities": ["actor:fakework"], "facts": FACTS}


class FakeStore:
    def __init__(self, raises=None):
        self.calls, self.raises, self.reads = [], raises, []

    def heartbeat(self, runner_id, state, capabilities, *, protocol, facts, census=None):
        self.calls.append(("heartbeat", runner_id))
        if self.raises is not None:
            raise self.raises
        return {"state": "recorded"}

    def note_read(self, reader, role):
        self.reads.append((reader, role))


def raw(doc):
    return json.dumps(doc).encode()


# ── the door's answers ───────────────────────────────────────────────────────────────────────────────────────────
def test_a_host_speaks_only_with_its_own_token_and_only_for_itself():
    s = FakeStore()
    assert server.answer(s, HOSTS, "", raw(beat()), "application/json") == (401, {"error": "unauthorized"})
    assert server.answer(s, HOSTS, "x" * 48, raw(beat()), "application/json")[0] == 401
    assert server.answer(s, HOSTS, HOST_TOKEN, raw(beat("staging-1:runner")), "application/json") == \
        (403, {"error": "runner_not_this_credential"})                 # another host's id: refused before the store
    assert server.answer(s, HOSTS, OTHER_TOKEN, raw(beat()), "application/json")[0] == 403
    assert s.calls == []
    assert server.answer(s, HOSTS, HOST_TOKEN, raw(beat()), "application/json") == (200, {"value": {"state": "recorded"}})
    assert s.calls == [("heartbeat", "mac-test-1:runner")]


def test_every_shape_the_door_refuses_is_a_fixed_code_and_the_store_never_sees_it():
    s = FakeStore()
    assert server.answer(s, HOSTS, HOST_TOKEN, raw(beat()), "text/html")[0] == 400
    assert server.answer(s, HOSTS, HOST_TOKEN, b"not json", "application/json") == \
        (400, {"error": "runner_request_invalid"})
    assert server.answer(s, HOSTS, HOST_TOKEN, b"x" * (door.BODY_MAX + 1), "application/json") == \
        (413, {"error": "runner_request_bound"})
    v1 = {"op": "heartbeat", "runner_id": "mac-test-1:runner", "state": "online", "capabilities": {}}
    assert server.answer(s, HOSTS, HOST_TOKEN, raw(v1), "application/json") == \
        (400, {"error": "runner_protocol_2_required"})              # production's version 1 keeps the portal's door
    bad = {**beat(), "extra": 1}
    assert server.answer(s, HOSTS, HOST_TOKEN, raw(bad), "application/json") == \
        (400, {"error": "runner_request_invalid"})
    assert s.calls == []


def test_a_store_refusal_is_its_code_and_a_store_failure_is_unavailable():
    got = server.answer(FakeStore(AuthorityRefused("protocol_not_enabled")), HOSTS, HOST_TOKEN, raw(beat()),
                        "application/json")
    assert got == (400, {"error": "protocol_not_enabled"})
    got = server.answer(FakeStore(AuthorityRefused("Some Text With Spaces")), HOSTS, HOST_TOKEN, raw(beat()),
                        "application/json")
    assert got == (400, {"error": "authority_refused"})             # never anything but a fixed code
    got = server.answer(FakeStore(RuntimeError("database down")), HOSTS, HOST_TOKEN, raw(beat()), "application/json")
    assert got == (503, {"error": "unavailable"})                    # a host reads it as a lost connection


def test_credentials_are_digests_for_one_environment_and_never_shared(tmp_path):
    p = tmp_path / "creds.json"
    good = {"schema": server.CREDENTIALS_SCHEMA, "environment": "dev", "hosts": HOSTS, "readers": READERS}
    p.write_text(json.dumps(good))
    assert server.load_credentials(p, "dev") == (HOSTS, READERS)
    for doc, env in ((good, "prod"), ({**good, "hosts": {"mac-test-1": HOST_TOKEN}}, "dev"),
                     ({**good, "extra": 1}, "dev"), ({**good, "hosts": {}}, "dev"),
                     ({**good, "readers": {"collector": {"sha256": HOSTS["mac-test-1"], "role": "observer"}}}, "dev")):
        p.write_text(json.dumps(doc))
        with pytest.raises(ValueError):
            server.load_credentials(p, env)


# ── the client reads only the door's JSON as an answer (N-D5) ───────────────────────────────────────────────────
class _Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]


def _client(reply):
    def opener(req, timeout):
        if isinstance(reply, Exception):
            raise reply
        return _Reply(reply)
    return door.HttpDispatcherV2(door.DOORS[1], HOST_TOKEN, Path("/nonexistent"), opener=opener)


def _http_error(status, body):
    return urllib.error.HTTPError(door.DOORS[1], status, "x", {}, io.BytesIO(body))


@pytest.mark.parametrize("reply", [
    b"<html><body>502 Bad Gateway</body></html>",          # a proxy's page with a 200
    b"",                                                    # an empty body
    b'{"value": 1',                                         # cut short
    b'{"error": "x"}',                                      # JSON, but not an answer
    b'[1, 2]',
    b'{"value": "' + b"x" * door.ANSWER_MAX + b'"}',         # past the bound: read cut short
])
def test_a_reply_that_is_not_the_doors_answer_reads_as_a_lost_connection(reply):
    with pytest.raises(door.DoorUnreachable) as got:
        _client(reply).heartbeat("mac-test-1:runner", "online", [], FACTS)
    assert runner._transport(got.value) is True                # offline: retried, never marked final


@pytest.mark.parametrize("status,body", [
    (404, b"<html>Not Found</html>"),                       # a proxy's 404
    (400, b"<html>Bad Request</html>"),                     # a refusal status, not the door's JSON
    (429, b'{"error": "rate_limited"}'),                    # refuses nothing about the call
    (408, b'{"error": "timeout"}'),
    (502, b"<html>Bad Gateway</html>"),
    (503, b'{"error": "unavailable"}'),
])
def test_an_error_status_without_the_doors_refusal_is_a_lost_connection(status, body):
    with pytest.raises(door.DoorUnreachable) as got:
        _client(_http_error(status, body)).renew("8b1e9f00-0000-4000-8000-000000000001", 1,
                                                 runner_id="mac-test-1:runner")
    assert runner._transport(got.value) is True


def test_the_doors_own_refusal_is_an_answer_with_its_code():
    with pytest.raises(door.DoorRefused) as got:
        _client(_http_error(400, b'{"error": "runner_request_invalid"}')).heartbeat("mac-test-1:runner", "online",
                                                                                   [], FACTS)
    assert runner._transport(got.value) is False
    assert runner._error_code(got.value) == "runner_request_invalid"
    assert _client(b'{"value": {"state": "recorded"}}').heartbeat("mac-test-1:runner", "online", [], FACTS) == \
        {"state": "recorded"}


# ── a redirect, or any error on the way, is no answer either (S1-H review SF3) ───────────────────────────────────
@pytest.fixture()
def redirecting():
    """A plain local server answering every POST with a redirect, as a proxy's login page in front of the door would."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(self.server.status)
            self.send_header("Location", "https://login.example.test/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), Redirect)
    srv.status = 302
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def own_client(port):
    """The real client with its own handler chain (no proxy, redirects refused), its request sent to a local port."""
    client = door.HttpDispatcherV2(door.DOORS[1], HOST_TOKEN, Path("/x"))
    own = client._open

    def retarget(req, timeout):
        local = urllib.request.Request(f"http://127.0.0.1:{port}{door.PATH}", data=req.data,
                                       method=req.get_method(), headers=dict(req.header_items()))
        return own(local, timeout=timeout)
    client._open = retarget
    return client


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_from_the_doors_address_reads_as_a_lost_connection(redirecting, status):
    redirecting.status = status
    with pytest.raises(door.DoorUnreachable, match=r"runner_door_(redirect_refused|status_30\d)") as got:
        own_client(redirecting.server_address[1]).heartbeat("mac-test-1:runner", "online", [], FACTS)
    assert runner._transport(got.value) is True                # offline: never counted as contact


@pytest.mark.parametrize("error", [RuntimeError("runner_door_redirect_refused"), RuntimeError("Some proxy said no"),
                                   LookupError("x"), ValueError("not a door answer")])
def test_any_error_on_the_way_that_is_not_the_doors_answer_is_a_lost_connection(error):
    with pytest.raises(door.DoorUnreachable) as got:
        _client(error).renew("8b1e9f00-0000-4000-8000-000000000001", 1, runner_id="mac-test-1:runner")
    assert runner._transport(got.value) is True
    assert str(got.value) in ("runner_door_redirect_refused", "runner_door_answer_not_authority")


def test_version_1_client_is_unchanged():
    """Production's client still raises its own error for an answer without a value (its behaviour is frozen)."""
    def opener(req, timeout):
        return _Reply(b'{"nothing": 1}')
    with pytest.raises(RuntimeError, match="runner_door_answer_invalid"):
        door.HttpDispatcher(door.DOORS[0], HOST_TOKEN, Path("/x"), opener=opener).renew("r", 1)


# ── over real TLS, with the real client ────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def tls_server(tmp_path):
    openssl = shutil.which("openssl")
    if openssl is None:
        pytest.skip("openssl is not installed")
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
                    "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True)
    store = FakeStore()
    state = tmp_path / "authority" / "state.json"
    from core.mc_delegation_dispatcher import snapshot
    snapshot.write_state_file(state.parent, {"schema": snapshot.SCHEMA, "as_of": "2026-10-29T12:00:00+00:00",
                                             "environment": "dev", "items": []})
    srv = server.DoorServer(("127.0.0.1", 0), store, hosts=HOSTS, readers=READERS, state_file=state,
                            tls=server.tls_context(cert, key))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, store, cert
    finally:
        srv.shutdown()
        srv.server_close()


def _local_opener(port, cert):
    import ssl
    ctx = ssl.create_default_context(cafile=str(cert))
    https = urllib.request.HTTPSHandler(context=ctx)
    real = urllib.request.build_opener(urllib.request.ProxyHandler({}), https).open

    def opener(req, timeout):
        local = urllib.request.Request(f"https://127.0.0.1:{port}{door.PATH}", data=req.data, method=req.get_method(),
                                       headers=dict(req.header_items()))
        return real(local, timeout=timeout)
    return opener


def test_the_thin_server_over_tls_answers_the_real_client(tls_server):
    srv, store, cert = tls_server
    port = srv.server_address[1]
    client = door.HttpDispatcherV2(door.DOORS[1], HOST_TOKEN, Path("/x"), opener=_local_opener(port, cert))
    assert client.heartbeat("mac-test-1:runner", "online", ["actor:fakework"], FACTS) == {"state": "recorded"}
    wrong = door.HttpDispatcherV2(door.DOORS[1], OTHER_TOKEN, Path("/x"), opener=_local_opener(port, cert))
    with pytest.raises(door.DoorRefused, match="runner_not_this_credential"):
        wrong.heartbeat("mac-test-1:runner", "online", ["actor:fakework"], FACTS)
    assert store.calls == [("heartbeat", "mac-test-1:runner")]


def test_the_read_path_takes_a_readers_token_only(tls_server):
    import ssl
    srv, store, cert = tls_server
    url = f"https://127.0.0.1:{srv.server_address[1]}{server.STATE_PATH}"
    ctx = ssl.create_default_context(cafile=str(cert))

    def get(token):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
    status, doc = get(READER_TOKEN)
    assert status == 200 and doc["schema"] == "minimoi.runtime.state.v1" and doc["view_age_s"] is not None
    assert store.reads == [("collector", "observer")]
    assert get(HOST_TOKEN) == (401, {"error": "unauthorized"})          # a host's credential reads nothing here


def test_failed_authentication_is_limited_per_address_and_a_host_never_is(tls_server):
    """The failure counter is never cleared here (the old test did, and so hid S1-H review SF4): a good host on the
    same address as a caller with bad tokens, already limited, is still answered, every time, and that caller stays
    limited."""
    srv, store, cert = tls_server
    port = srv.server_address[1]
    bad = door.HttpDispatcherV2(door.DOORS[1], "z" * 48, Path("/x"), opener=_local_opener(port, cert))
    for _ in range(server.FAILED_AUTH_PER_MINUTE):
        with pytest.raises(door.DoorRefused, match="unauthorized"):
            bad.heartbeat("mac-test-1:runner", "online", [], FACTS)
    with pytest.raises(door.DoorUnreachable, match="runner_door_status_429"):   # the address waits, nothing final
        bad.heartbeat("mac-test-1:runner", "online", [], FACTS)
    good = door.HttpDispatcherV2(door.DOORS[1], HOST_TOKEN, Path("/x"), opener=_local_opener(port, cert))
    for _ in range(server.FAILED_AUTH_PER_MINUTE + 5):
        assert good.heartbeat("mac-test-1:runner", "online", [], FACTS) == {"state": "recorded"}
    with pytest.raises(door.DoorUnreachable, match="runner_door_status_429"):   # brute force still limited
        bad.heartbeat("mac-test-1:runner", "online", [], FACTS)
    wrong_host = door.HttpDispatcherV2(door.DOORS[1], OTHER_TOKEN, Path("/x"), opener=_local_opener(port, cert))
    with pytest.raises(door.DoorRefused, match="runner_not_this_credential"):   # authenticated: answered, not limited
        wrong_host.heartbeat("mac-test-1:runner", "online", [], FACTS)


def test_a_reader_is_never_limited_by_failures_from_its_address(tls_server):
    import ssl
    srv, store, cert = tls_server
    url = f"https://127.0.0.1:{srv.server_address[1]}{server.STATE_PATH}"
    ctx = ssl.create_default_context(cafile=str(cert))

    def get(token):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as exc:
            return exc.code
    assert [get("z" * 48) for _ in range(server.FAILED_AUTH_PER_MINUTE)] == [401] * server.FAILED_AUTH_PER_MINUTE
    assert get("z" * 48) == 429
    assert get(READER_TOKEN) == 200


# ── the authority's own timers ──────────────────────────────────────────────────────────────────────────────────
class LoopStore:
    """run_liveness / run_sweeper as the real store's loops are: a step every sweep_s until stop is set."""
    sweeps = True

    def __init__(self):
        self.authority = SimpleNamespace(sweeper=SimpleNamespace(sweep_s=0.05, gap_threshold_s=0.2))
        self.beats, self.sweeps_done, self.published, self.last_sweep = 0, 0, [], time.monotonic()

    def run_liveness(self, stop, *, log=None):
        while not stop.is_set():
            self.beats += 1
            stop.wait(0.05)

    def run_sweeper(self, stop, *, log=None, state_dir=None, endpoint_id=None, host_id=None):
        while not stop.is_set():
            self.sweeps_done += 1
            self.last_sweep = time.monotonic()
            stop.wait(0.05)

    def sweeper_status(self):
        age = time.monotonic() - self.last_sweep
        return {"age_s": age, "fresh": age <= self.authority.sweeper.gap_threshold_s}

    def publish(self, state_dir, *, endpoint_id, host_id):
        self.published.append((str(state_dir), endpoint_id, host_id))


def test_liveness_and_the_sweeper_run_apart_and_a_stopped_sweeper_is_published_by_the_watch(tmp_path):
    s = LoopStore()
    loops = server.AuthorityLoops(s, state_dir=tmp_path, endpoint_id="dev-authority", host_id="mac-test-1").start()
    try:
        time.sleep(0.4)
        assert loops.threads() == {"liveness": True, "sweeper": True, "watch": True}
        assert s.published == []                                         # a fresh sweeper publishes for itself
        loops.stop_sweeper()
        time.sleep(0.6)
        beats = s.beats
        assert loops.threads() == {"liveness": True, "sweeper": False, "watch": True}   # liveness goes on: no outage
        time.sleep(0.2)
        assert s.beats > beats
        assert s.published and s.published[-1] == (str(tmp_path), "dev-authority", "mac-test-1")
    finally:
        loops.stop()
    assert not any(loops.threads().values())


def test_loops_refuse_a_store_without_a_sweeper(tmp_path):
    s = LoopStore()
    s.sweeps = False
    with pytest.raises(ValueError):
        server.AuthorityLoops(s, state_dir=tmp_path, endpoint_id="e", host_id="h")


# ── where the timers run, and the call count ───────────────────────────────────────────────────────────────────
def _host(tmp_path, **over):
    doc = {"schema": "minimoi.host.v1", "host_id": "mac-test-1", "environment": "dev", "principal": "robert",
           "roles": ["authority", "worker", "witness"], "expected_availability": "intermittent",
           "unreachable_grace_s": 900, "authority": {"endpoint_id": "dev-authority", "dsn_file": str(tmp_path / "d"),
                                                     "values": str(tmp_path / "v")},
           "capabilities": ["actor:fakework"], "concurrency": 1, "artifact_root": str(tmp_path / "a"),
           "workspace_root": str(tmp_path / "w"), "mirrors": {"r": str(tmp_path / "m")},
           "interpreter": {"path": sys.executable, "version": "%d.%d" % sys.version_info[:2]},
           "bundle_root": str(tmp_path / "b"), **over}
    return host_mod.parse(doc)


def test_the_runtime_runs_the_timers_only_where_it_serves_the_store_itself(tmp_path):
    store = LoopStore()
    served = SimpleNamespace(store=store)
    assert isinstance(cli.authority_loops(_host(tmp_path), served), server.AuthorityLoops)
    assert cli.authority_loops(_host(tmp_path, roles=["worker", "witness"]), served) is None
    door_host = _host(tmp_path, roles=["worker"], authority={"endpoint_id": "dev-authority", "url": door.DOORS[1],
                                                             "credential_ref": "keychain:minimoi_runtime/dev",
                                                             "values": str(tmp_path / "v")})
    assert cli.authority_loops(door_host, served) is None
    assert cli.authority_loops(_host(tmp_path), SimpleNamespace()) is None


def test_calls_are_counted_and_everything_else_passes_through():
    inner = SimpleNamespace(protocol=2, store="the-store", heartbeat=lambda *a, **k: {"state": "recorded"},
                            claim=lambda *a, **k: None)
    d = cli.CountingDispatcher(inner)
    d.heartbeat("x")
    d.heartbeat("x")
    d.claim("x")
    assert (d.counts, d.protocol, d.store) == ({"heartbeat": 2, "claim": 1}, 2, "the-store")
    assert runner._call_counts(d) == {"heartbeat": 2, "claim": 1}
    assert runner._call_counts(inner) is None


# ── host.yaml: the venv pinned, and the door ────────────────────────────────────────────────────────────────────
def test_the_interpreter_pin_names_the_environment_and_what_is_installed_in_it(tmp_path):
    here = {"path": sys.executable, "version": "%d.%d" % sys.version_info[:2], "prefix": sys.prefix,
            "packages": host_mod.packages_digest()}
    assert host_mod.interpreter_problems(_host(tmp_path, interpreter=here)) == []
    other = _host(tmp_path, interpreter={**here, "prefix": str(tmp_path)})
    assert host_mod.interpreter_problems(other) == ["interpreter_prefix_mismatch"]
    changed = _host(tmp_path, interpreter={**here, "packages": "sha256:" + "0" * 64})
    assert host_mod.interpreter_problems(changed) == ["interpreter_packages_changed"]
    with pytest.raises(host_mod.HostRefused):
        _host(tmp_path, interpreter={**here, "packages": "not-a-digest"})
    assert host_mod.interpreter_problems(_host(tmp_path)) == []          # S1-D's two-field pin still holds


def test_only_an_authority_names_a_door_and_every_value_is_checked(tmp_path):
    d = {"listen": "0.0.0.0:8443", "tls_cert": "/etc/x.pem", "tls_key": "/etc/x.key", "dsn_file": "/etc/d",
         "credentials": "/etc/c.json"}
    got = _host(tmp_path, door=d).door
    assert (got.host, got.port, str(got.credentials)) == ("0.0.0.0", 8443, "/etc/c.json")
    for bad, code in (({**d, "listen": "8443"}, "host_door_invalid"), ({**d, "listen": "h:0"}, "host_door_invalid"),
                      ({**d, "tls_key": "rel/key"}, "host_door_invalid"), ({**d, "x": 1}, "host_door_invalid")):
        with pytest.raises(host_mod.HostRefused, match=code):
            _host(tmp_path, door=bad)
    with pytest.raises(host_mod.HostRefused, match="host_door_not_authority"):
        _host(tmp_path, door=d, roles=["worker"])


def test_the_authority_service_has_a_lock_of_its_own_beside_the_runtimes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    h = _host(tmp_path)
    rt, auth = host_mod.HostLock(h), host_mod.HostLock(h, service="authority")
    assert rt.path.name == "mac-test-1.dev.lock" and auth.path.name == "mac-test-1.dev.authority.lock"
    rt.acquire("now")
    auth.acquire("now")
    try:
        with pytest.raises(host_mod.HostRefused, match="host_lock_held"):
            host_mod.HostLock(h, service="authority").acquire("now")
    finally:
        rt.release()
        auth.release()
