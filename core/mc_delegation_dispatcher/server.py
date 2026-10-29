"""The authority service (host runtime spec v0.5 §3.3, R3.4-a; S1-H): ``door.handle`` over ``DispatcherStore`` behind a
thin HTTPS server, plus the authority's own timers.

* **Its timers** (``AuthorityLoops``), each a thread of its own, so one that dies leaves the others running:
  - **liveness** (``DispatcherStore.run_liveness``): the authority beats its own row every ``sweep_s``. A gap in that
    row, and only that, is the authority's own outage (R5.2-a: ``authority.suspended``, every open grace extended);
  - **the sweeper** (``run_sweeper`` with its state folder): lapses, grace ends, the published snapshot (``state.json``
    and each notice recorded once, R7.6-a);
  - **the sweeper watch**: on the same interval, the authority reads its own sweeper's age, and while it is stale
    publishes the snapshot itself, so ``runtime-sweeper-stale`` is raised once and reaches Robert (E11, R5.6-a's
    "the sweeper's age"). A dead sweeper never looks like an outage, and never goes unseen.

  ``stop_sweeper`` stops the sweeper alone (the operator's SIGUSR1, E11's injection in acceptance); liveness and the
  watch go on, and a restart of the process brings it back.
* **Where they run.** A host that serves other hosts runs this service (``mc-delegation-runner authority``,
  ``minimoi-authority-<env>.service``): the door, and the three timers. On the Mac-only interim (R2.4-a: the
  ``authority`` role with the store reached by DSN) there is no door, and the runtime process is the only one serving
  the store's calls, so it runs the same ``AuthorityLoops`` itself (``cli._run_v2``). Either way the timers live in
  the process that serves: if they died while it kept serving, the gap would wrongly read as an outage.
* **The door** (``DoorServer``), version 2 only (production's version 1 runner keeps the portal's door): ``POST
  door.PATH`` with a host's bearer token, checked in constant time against each host credential's SHA-256 (only
  digests are configured: ``minimoi.door_credentials.v1``), a JSON body within ``door.BODY_MAX``, and every
  ``runner_id`` bound to the credential that sent it (``<host_id>:runner``): a host can speak only for itself (R4.4,
  S1-A N5, on every call that names one, reconcile and effect included). ``GET STATE_PATH`` with a reader's token is
  the snapshot's read path (``snapshot.read_request``: observer or owner; the owner's read is counted for C).
* **Answers.** 200 ``{"value": ...}`` for every answer the store gives, refusals in the value included. A body the door
  refuses, or a call the store refuses outright, is 400 ``{"error": <fixed code>}``; a token that matches nothing 401;
  a runner id that is not the credential's 403; a body over the bound 413. Anything else (the store's database
  unreachable among it) is 503 ``{"error": "unavailable"}``, which a host reads as a lost connection and retries. The
  host's client (``door.HttpDispatcherV2``) reads only this JSON as the authority's answer (N-D5). No answer ever
  echoes a token or a value from a body.
* **Unauthenticated callers** are limited per address (``FAILED_AUTH_PER_MINUTE``); an authenticated host never is,
  because its contact, renewals and census are what keep its work alive.

Stdlib only, apart from the store's own psycopg2. Built and unit-tested in S1; it runs on staging at gate D, never on
the Mac in this build.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core.mc_delegation_dispatcher import door, snapshot as snap

_log = logging.getLogger("mc_delegation")

STATE_PATH = "/internal/mc/v1/runtime/state"
CREDENTIALS_SCHEMA = "minimoi.door_credentials.v1"
FAILED_AUTH_PER_MINUTE = 30
REQUEST_TIMEOUT_S = 30                   # a caller that stalls mid-request is dropped
_HEX = re.compile(r"^[0-9a-f]{64}$")
_HOST = re.compile(r"^[A-Za-z0-9._-]{1,56}$")
_CODE = re.compile(r"^[a-z][a-z0-9_:]{0,63}$")


# ── the authority's own timers ──────────────────────────────────────────────────────────────────────────────────
class AuthorityLoops:
    """Liveness, the sweeper and the sweeper watch, each on a thread of its own, over one version 2 store.
    ``state_dir`` is where the snapshot is published; ``endpoint_id`` and ``host_id`` name the authority in it."""

    def __init__(self, store, *, state_dir, endpoint_id, host_id, log=None):
        if not store.sweeps:
            raise ValueError("authority_loops_need_a_sweeper")
        self.store, self.state_dir = store, Path(state_dir)
        self.endpoint_id, self.host_id, self.log = endpoint_id, host_id, log or _log
        self._stops = {name: threading.Event() for name in ("liveness", "sweeper", "watch")}
        self._threads = {}

    def start(self):
        s = self.store
        self._spawn("liveness", lambda stop: s.run_liveness(stop, log=self.log))
        self._spawn("sweeper", lambda stop: s.run_sweeper(stop, log=self.log, state_dir=self.state_dir,
                                                          endpoint_id=self.endpoint_id, host_id=self.host_id))
        self._spawn("watch", self._watch)
        return self

    def _spawn(self, name, target):
        t = threading.Thread(target=target, args=(self._stops[name],), name=f"mc-authority-{name}", daemon=True)
        self._threads[name] = t
        t.start()

    def stop_sweeper(self):
        """The sweeper alone stops (E11): lapses and grace ends go unwritten, the snapshot stays correct (every answer
        reads the clock), and the watch raises ``runtime-sweeper-stale`` once. Restarting the process brings it back."""
        self.log.warning("authority sweeper stopped on request; liveness and the sweeper watch go on")
        self._stops["sweeper"].set()

    def stop(self, timeout=10.0):
        for e in self._stops.values():
            e.set()
        deadline = time.monotonic() + timeout
        for t in self._threads.values():
            t.join(max(0.0, deadline - time.monotonic()))

    def threads(self) -> dict:
        return {name: t.is_alive() for name, t in self._threads.items()}

    def _watch(self, stop):
        """Every sweep interval: a sweeper that has not run within the gap threshold is stale, and the authority
        publishes the snapshot itself, so its stale notice is raised once (the snapshot's own rule and key)."""
        every = self.store.authority.sweeper.sweep_s
        while not stop.wait(every):
            try:
                status = self.store.sweeper_status()
                if status is None or not status["fresh"]:
                    self.store.publish(self.state_dir, endpoint_id=self.endpoint_id, host_id=self.host_id)
            except Exception as exc:
                self.log.warning("sweeper watch failed (%s)", type(exc).__name__)


def ages(store) -> dict:
    """The two timers as read from the authority's own rows (``doctor``): each one's age, and whether it is within the
    gap threshold. None for a row never written. Read only."""
    threshold = store.authority.sweeper.gap_threshold_s
    with store._tx() as cur:
        cur.execute("""SELECT runner_id, seen_at, clock_timestamp() AS now FROM guild.mc_delegation_runners
            WHERE runner_id = ANY(%s)""", ([store._alive_id, store._sweeper_id],))
        rows = {r["runner_id"]: r for r in cur.fetchall()}
    out = {}
    for name, rid in (("liveness", store._alive_id), ("sweeper", store._sweeper_id)):
        r = rows.get(rid)
        age = None if r is None else round((r["now"] - r["seen_at"]).total_seconds(), 1)
        out[name] = {"row": rid, "age_s": age, "fresh": age is not None and age <= threshold,
                     "gap_threshold_s": threshold}
    return out


# ── the door's credentials (digests only) ───────────────────────────────────────────────────────────────────────
def load_credentials(path, environment) -> tuple:
    """(hosts {host_id: sha256}, readers {name: {sha256, role}}) from ``minimoi.door_credentials.v1``. Strict: a
    file that is not exactly that, for this environment, refuses to start the door (ValueError, a fixed code)."""
    try:
        raw = Path(path).read_bytes()
        doc = json.loads(raw.decode("utf-8")) if len(raw) <= 64 * 1024 else None
    except (OSError, ValueError, UnicodeDecodeError):
        doc = None
    if type(doc) is not dict or not {"schema", "environment", "hosts", "readers"} <= set(doc) \
            or not set(doc) <= {"schema", "environment", "hosts", "readers", "note"}:
        raise ValueError("door_credentials_invalid")
    if doc["schema"] != CREDENTIALS_SCHEMA or doc["environment"] != environment:
        raise ValueError("door_credentials_invalid")
    hosts = doc["hosts"]
    if type(hosts) is not dict or not hosts or not all(type(k) is str and _HOST.match(k) and type(v) is str
                                                       and _HEX.match(v) for k, v in hosts.items()):
        raise ValueError("door_credentials_invalid")
    readers = snap.check_readers(doc["readers"])
    digests = list(hosts.values()) + [r["sha256"] for r in readers.values()]
    if len(set(digests)) != len(digests):
        raise ValueError("door_credentials_shared")          # one token never speaks as two callers
    return dict(hosts), dict(readers)


def _caller(token, hosts) -> str | None:
    """The host a bearer token belongs to, compared in constant time against every digest."""
    if type(token) is not str or not 32 <= len(token) <= 256:
        return None
    given = hashlib.sha256(token.encode("utf-8")).hexdigest()
    found = None
    for host_id, want in sorted(hosts.items()):
        if hmac.compare_digest(given, want):
            found = host_id
    return found


# ── the door ────────────────────────────────────────────────────────────────────────────────────────────────────
def answer(store, hosts, token, body_bytes, content_type) -> tuple:
    """One door call -> (status, JSON object). Pure of HTTP, so it is tested directly."""
    host_id = _caller(token, hosts)
    if host_id is None:
        return 401, {"error": "unauthorized"}
    if (content_type or "").split(";")[0].strip().lower() != "application/json":
        return 400, {"error": "runner_request_invalid"}
    if body_bytes is None or len(body_bytes) > door.BODY_MAX:
        return 413, {"error": "runner_request_bound"}
    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return 400, {"error": "runner_request_invalid"}
    if type(body) is not dict or "protocol" not in body:
        return 400, {"error": "runner_protocol_2_required"}      # version 1 keeps the portal's door
    if "runner_id" in body and body["runner_id"] != f"{host_id}:runner":
        return 403, {"error": "runner_not_this_credential"}
    from core.mc_delegation_dispatcher.store import AuthorityRefused
    try:
        return 200, door.handle(store, body)
    except door.Refused:
        return 400, {"error": "runner_request_invalid"}
    except AuthorityRefused as exc:
        code = str(exc)
        return 400, {"error": code if _CODE.match(code) else "authority_refused"}
    except ValueError:
        return 400, {"error": "runner_request_invalid"}
    except Exception as exc:
        _log.warning("door call failed (%s)", type(exc).__name__)
        return 503, {"error": "unavailable"}


class DoorServer(ThreadingHTTPServer):
    """The thin HTTPS server: ``door.PATH`` (POST) and ``STATE_PATH`` (GET) only. ``tls`` is an ``ssl.SSLContext``
    (``tls_context``); without one it serves plain HTTP, which only tests do."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, store, *, hosts, readers, state_file, tls=None):
        self.store, self.hosts, self.readers, self.state_file = store, hosts, readers, Path(state_file)
        self._failed, self._failed_lock, self._tls = {}, threading.Lock(), tls
        super().__init__(address, _Handler)

    def get_request(self):
        """Each accepted connection is wrapped here and its handshake left to the handler's thread (under the
        handler's timeout), so a slow or silent caller never holds up the accept loop."""
        sock, addr = self.socket.accept()
        if self._tls is not None:
            sock = self._tls.wrap_socket(sock, server_side=True, do_handshake_on_connect=False)
        return sock, addr

    def limited(self, address) -> bool:
        """True when this address has failed authentication too often in the last minute."""
        now = time.monotonic()
        with self._failed_lock:
            recent = [t for t in self._failed.get(address, ()) if now - t < 60]
            self._failed[address] = recent
            if len(self._failed) > 1000:
                self._failed.clear()
            return len(recent) >= FAILED_AUTH_PER_MINUTE

    def failed(self, address):
        with self._failed_lock:
            self._failed.setdefault(address, []).append(time.monotonic())


class _Handler(BaseHTTPRequestHandler):
    server_version = "MiniMoi-authority/1"
    sys_version = ""
    timeout = REQUEST_TIMEOUT_S

    def log_message(self, fmt, *args):                 # never the request line's query or a header
        pass

    def _send(self, status, doc):
        data = json.dumps(doc, sort_keys=True, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _token(self):
        header = self.headers.get("Authorization", "")
        return header[7:] if header.startswith("Bearer ") else ""

    def do_POST(self):
        who = self.client_address[0]
        if self.path != door.PATH:
            return self._send(404, {"error": "not_found"})
        if self.server.limited(who):
            return self._send(429, {"error": "rate_limited"})
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._send(411, {"error": "length_required"})
        body = self.rfile.read(length) if 0 <= length <= door.BODY_MAX else None
        status, doc = answer(self.server.store, self.server.hosts, self._token(), body,
                             self.headers.get("Content-Type"))
        if status == 401:
            self.server.failed(who)
        self._send(status, doc)

    def do_GET(self):
        who = self.client_address[0]
        if self.path != STATE_PATH:
            return self._send(404, {"error": "not_found"})
        if self.server.limited(who):
            return self._send(429, {"error": "rate_limited"})
        status, doc = snap.read_request(dict(self.headers.items()), readers=self.server.readers,
                                        provide=lambda: snap.read_state_file(self.server.state_file),
                                        note_read=self.server.store.note_read)
        if status == 401:
            self.server.failed(who)
        self._send(status, doc)


def tls_context(cert, key):
    """TLS 1.2 or later, the host's certificate chain and key (paths from host.yaml's ``door``)."""
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert), str(key))
    return ctx


def state_dir_of(host) -> Path:
    """Where an authority publishes its snapshot (R7.6-a's ``<authority_state_dir>``): beside the host's artifacts."""
    return Path(host.artifact_root) / "authority"
