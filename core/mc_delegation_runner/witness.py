"""The host's witness of its authority (host runtime spec v0.5 R5.6-a, E8; S1-H, the harness thread's option (a)).

A runtime with the ``witness`` role tells Robert once when its authority has not answered for longer than
``contact_timeout_s``, and once when it answers again:

    authority-unreachable:<endpoint>:<since>     past the contact timeout, the host's own calls all failing
    authority-back:<endpoint>:<since>            the first good call after a notice above

* **Its notifier can't depend on the store whose outage it reports.** It hands each notice to a host-side adapter (a
  file in acceptance; Telegram, its bot token and chat id read by Keychain reference at send time), and keeps its
  once-per-key memory in a file of its own under the host's artifact root (``witness/told.jsonl``, fsynced), never in
  the authority's database. A key is recorded after its send: a crash between the two repeats that one notice once
  at the next start, and never loses it (the same trade the supervisor's outbox makes).
* **Its memory survives a restart honestly** (S1-H review SF2). A restarted runtime knows only its own failed calls,
  never how long the authority was gone before it started. So the outage is read from the file: an
  ``authority-unreachable`` key with no ``authority-back`` for the same ``since`` is the outage still open. While it
  is open, no second unreachable notice is told, whatever the new process measures; at the first good call its end is
  told once, under the first notice's ``since``.
* **A send that fails is never silently dropped** (review N1). It is tried again at the next contact; meanwhile the
  failure is written once per key and code to ``witness/failures.jsonl`` beside the memory and to stderr (the
  runtime's log), and shown in ``host-state.json`` (``hosts[0].witness``, and an ``unknowns`` line while a notice is
  undelivered) and by ``doctor``. Only fixed codes and exception type names are written, never a value or a URL.
* **Silence where the spec wants it** (R2.4-b, R7.4-e). The outage is measured on the monotonic clock from the
  host's first failed call, so time the whole host spent asleep never counts. On the Mac-only gate the authority
  sleeps with the runtime: a whole-Mac sleep is no outage, and a wake whose first call succeeds says nothing.

Stdlib only; Python 3.9-compatible.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

_KEY = re.compile(r"^(authority-(unreachable|back)|sweeper-(stale|back)|host-(silent|back)):[A-Za-z0-9._:-]{1,64}:"
                  r"[0-9T:.+-]{10,40}$")
MESSAGE_CHARS = 3500        # Telegram cuts a message at 4096 (the supervisor's bot at 4000): a notice is never cut unseen
_SSM = re.compile(r"^ssm:/[A-Za-z0-9_.\-/]{1,200}$")
_CODE = re.compile(r"^[a-z][a-z0-9_:]{0,63}$")


def failure_code(exc) -> str:
    """A fixed code for a failed send: the error's own fixed code when it carries one (the credential store's
    ``code``, or this module's own ``witness_…`` messages), else its type's name. Never any other message, which
    could hold a URL or a value."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and _CODE.match(code):
        return code
    text = str(exc)
    if text.startswith("witness_") and _CODE.match(text):
        return text
    return f"witness_send_failed:{type(exc).__name__}"


class Memory:
    """Once per key, in a file: every key told, one JSON line each, fsynced; read back whole at start (a torn last
    line, from a crash mid-write, is skipped and its notice may be told once more)."""

    def __init__(self, path):
        self.path = Path(path)
        self.told, self.order = set(), []
        try:
            for line in self.path.read_text().splitlines():
                try:
                    key = json.loads(line)["key"]
                except (ValueError, KeyError, TypeError):
                    continue
                if isinstance(key, str) and key not in self.told:
                    self.told.add(key)
                    self.order.append(key)
        except FileNotFoundError:
            pass

    def record(self, key, at):
        _append(self.path, {"key": key, "at": at})
        if key not in self.told:
            self.told.add(key)
            self.order.append(key)

    def open_outage(self, endpoint_id):
        """(key, since) of the outage of this endpoint told and not yet told back, or None: the earliest such
        ``authority-unreachable`` key, as the file holds it. That is where a restarted runtime picks up (SF2)."""
        head = f"authority-unreachable:{endpoint_id}:"
        for key in self.order:
            if key.startswith(head):
                since = key[len(head):]
                if f"authority-back:{endpoint_id}:{since}" not in self.told:
                    return key, since
        return None


def _append(path, doc):
    """One JSON line, appended and fsynced, in the witness's own folder (0700) and file (0600)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (json.dumps(doc, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


class FileNotifier:
    """The acceptance adapter: one line per notice in the run's ``notify.jsonl`` (fake-notifier's file), marked as the
    witness's. Never a value: the words are fixed, with ids and times."""

    def __init__(self, path):
        self.path = Path(path)

    def send(self, key, text, at):
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps({"source": "witness", "key": key, "text": text, "at": at}, sort_keys=True)
                          + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)


def bounded(text, limit=MESSAGE_CHARS) -> str:
    """A notice that fits one Telegram message: cut at ``limit`` characters with a marker naming how much was left out,
    so a long one is never silently cut by Telegram after its key was recorded as told (the reviewers' 4,000-character
    note)."""
    if len(text) <= limit:
        return text
    tail = f"\n[cut: {len(text) - limit + 40} more characters; the full notice is in the snapshot]"
    return text[:limit - len(tail)] + tail


class SsmCredentialStore:
    """AWS Parameter Store by reference (``ssm:/<parameter name>``), read with decryption at each use, never kept: the
    EC2 witness's bot token and chat id (P1-a). boto3 is the portal image's; ``client`` is injectable for tests, which
    never reach AWS."""
    scheme = "ssm"

    def __init__(self, client=None, region="us-east-1"):
        self._client, self._region = client, region

    def read(self, ref: str) -> str:
        if not isinstance(ref, str) or not _SSM.match(ref):
            raise WitnessRefused("witness_ref_invalid")
        try:
            client = self._client
            if client is None:
                import boto3
                client = self._client = boto3.client("ssm", region_name=self._region)
            value = client.get_parameter(Name=ref[4:], WithDecryption=True)["Parameter"]["Value"]
        except Exception as exc:
            raise WitnessRefused(f"witness_ssm_unavailable:{type(exc).__name__}") from None
        if not isinstance(value, str) or not value.strip():
            raise WitnessRefused("witness_ssm_unavailable:empty")
        return value.strip()


class WitnessRefused(RuntimeError):
    """A fixed code (``code``), never a value or a URL."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def credential_stores(*refs) -> dict:
    """The stores a set of references needs, by scheme: the login Keychain on the Mac, Parameter Store on EC2."""
    out = {}
    for ref in refs:
        scheme = ref.split(":", 1)[0] if isinstance(ref, str) else ""
        if scheme == "keychain" and scheme not in out:
            from . import launch
            out[scheme] = launch.KeychainCredentialStore()
        elif scheme == "ssm" and scheme not in out:
            out[scheme] = SsmCredentialStore()
        elif scheme not in ("keychain", "ssm"):
            raise WitnessRefused("witness_ref_invalid")
    return out


class TelegramNotifier:
    """Robert's Telegram, direct from the witness (never through the supervisor, which may be what is down): the bot
    token and the chat id are read by reference at each send, ``keychain:<service>/<account>`` on the Mac
    (launch.KeychainCredentialStore) or ``ssm:/<name>`` on EC2 (SsmCredentialStore), never held in a file, the
    environment or a log. Each text is bounded to one message (``bounded``). Not exercised against Telegram in S1/P1-a's
    tests (no Keychain or SSM read happens there)."""
    API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token_ref, chat_ref, *, store=None, stores=None, opener=None):
        self.token_ref, self.chat_ref = token_ref, chat_ref
        if stores is None:
            if store is None:
                stores = credential_stores(token_ref, chat_ref)
            else:
                stores = {ref.split(":", 1)[0]: store for ref in (token_ref, chat_ref)}
        self.stores = stores
        self._open = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def _read(self, ref):
        store = self.stores.get(ref.split(":", 1)[0])
        if store is None:
            raise WitnessRefused("witness_ref_store_missing")
        return store.read(ref)

    def send(self, key, text, at):
        token, chat = self._read(self.token_ref), self._read(self.chat_ref)
        body = json.dumps({"chat_id": int(chat), "text": bounded(text)}).encode()
        req = urllib.request.Request(self.API.format(token=token), data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with self._open(req, timeout=20) as r:
                ok = json.loads(r.read(64 * 1024)).get("ok") is True
        except Exception as exc:
            raise RuntimeError(f"witness_send_failed:{type(exc).__name__}") from None   # never the URL (its token)
        if not ok:
            raise RuntimeError("witness_send_refused")


class Witness:
    """Watches whether the authority answers, from the host's own contact (R5.6-a's first check). ``observe`` is called
    after every contact attempt with how long the host has been offline by the monotonic clock (None while in
    contact) and the wall-clock time now."""

    def __init__(self, host_id, endpoint_id, notifier, memory, timeout_s, *, snapshot=None):
        self.host_id, self.endpoint_id, self.notifier, self.memory = host_id, endpoint_id, notifier, memory
        self.timeout_s = timeout_s
        self.snapshot = snapshot                # SnapshotChecks: R5.6-a's second and third checks (P1-a), or None
        # (key, since) of the outage told, until the authority answers again: from the file at start, so a restart
        # during an outage neither tells it twice nor loses its end (SF2).
        self.outage = memory.open_outage(endpoint_id)
        self.errors = 0
        self.last_failure = None                # {code, key, at} of the last failed send or look (N1)
        self.undelivered = None                 # the key whose send failed last and is not yet told
        self._failures_path = memory.path.with_name("failures.jsonl")
        self._failed = set()                    # (key, code) already written to failures.jsonl and the log
        try:
            for line in self._failures_path.read_text().splitlines():
                try:
                    doc = json.loads(line)
                    self._failed.add((doc["key"], doc["code"]))
                except (ValueError, KeyError, TypeError):
                    continue
        except OSError:
            pass

    def observe(self, offline_for_s, now_wall):
        from datetime import timedelta
        if offline_for_s is not None and offline_for_s > self.timeout_s and self.outage is None:
            since = (now_wall - timedelta(seconds=offline_for_s)).replace(microsecond=0).isoformat()
            key = f"authority-unreachable:{self.endpoint_id}:{since}"
            self.outage = (key, since)
            if not self._tell(key, f"Host {self.host_id} has not reached its authority {self.endpoint_id} since "
                                   f"{since} ({int(offline_for_s)} s, past its {self.timeout_s} s contact timeout). "
                                   f"Nothing is admitted, claimed or committed meanwhile, and its running work is "
                                   f"frozen. Next: an operator checks the authority (its service or its database).",
                              now_wall):
                self.outage = None              # not told: tried again at the next contact
        elif offline_for_s is None and self.outage is not None:
            since = self.outage[1]
            if self._tell(f"authority-back:{self.endpoint_id}:{since}",
                          f"Host {self.host_id} reaches its authority {self.endpoint_id} again (unreachable since "
                          f"{since}). Next: nothing to do; the host reconciles its work.", now_wall):
                self.outage = None              # until told, tried again at the next contact
        elif offline_for_s is None and (self.undelivered or "").startswith("authority-unreachable:"):
            self.undelivered = None             # its outage ended untold: the failure stays counted and written
        if offline_for_s is None and self.snapshot is not None:
            self.snapshot.look(self, now_wall)  # in contact: the sweeper's age and the other hosts' contact

    def _tell(self, key, text, now_wall) -> bool:
        """True once the key is told (now, or before); False when its send failed."""
        if not _KEY.match(key):
            return True                         # an id this rule can't name is never sent
        if key in self.memory.told:
            return True
        at = now_wall.isoformat()
        try:
            self.notifier.send(key, text, at)
        except Exception as exc:
            self.failed(failure_code(exc), at, key)
            return False
        self.memory.record(key, at)
        if (self.undelivered or "").split(":", 1)[0] == key.split(":", 1)[0]:
            self.undelivered = None             # this kind of notice is told now (its since may have moved)
        return True

    def failed(self, code, at, key=None):
        """A failed send (or look) is counted, kept as the last failure, and written once per key and code to
        ``failures.jsonl`` and to stderr (the runtime's log), so it is never silently dropped (N1). A write that
        itself fails leaves the count and the host-state line."""
        self.errors += 1
        self.last_failure = {"code": code, "key": key, "at": at}
        if key is not None:
            self.undelivered = key
        if (key, code) in self._failed:
            return
        self._failed.add((key, code))
        line = {"witness": "send_failed" if key else "failed", "code": code, "key": key, "at": at}
        try:
            _append(self._failures_path, line)
        except OSError:
            pass
        try:
            print(json.dumps(line, sort_keys=True), file=sys.stderr, flush=True)
        except Exception:
            pass

    def status(self) -> dict:
        """What host-state.json and doctor show of the witness: the open outage, failures, the undelivered key."""
        return {"notifier": type(self.notifier).__name__, "outage_since": None if self.outage is None
                else self.outage[1], "failures": self.errors, "last_failure": self.last_failure,
                "undelivered": self.undelivered}


class SnapshotChecks:
    """R5.6-a's second and third checks (P1-a), from the authority's own snapshot (``minimoi.runtime.state.v1``),
    read through ``read()`` (the read path with an observer credential, or a published state.json). Told through the
    witness's own notifier and memory, once per key:

        sweeper-stale:<endpoint>:<since>   the snapshot's sweeper, or the snapshot itself, older than the gap
                                           threshold (the authority's timers or its publisher stopped); since = when
                                           the sweeper last ran, as the snapshot says
        sweeper-back:<endpoint>:<since>    fresh again
        host-silent:<host>:<since>         another always-on host unreachable past its contact timeout; since = its
                                           last contact. An intermittent host (the Mac) is never told: its silence is
                                           expected (R5.1-e, R5.6-b)
        host-back:<host>:<since>           in contact again

    A read that fails is the witness's own failure (counted, written, shown, ``witness_read_failed``), never a notice:
    whether the authority answers is the first check's, from the host's contact (or, for a witness that is not a
    host, from these reads themselves: ``serve``). ``every_s`` bounds how often it reads."""

    def __init__(self, read, *, gap_threshold_s, every_s, mono=None):
        import time as _time
        self.read, self.gap_threshold_s, self.every_s = read, gap_threshold_s, every_s
        self._mono = mono or _time.monotonic
        self._due = None
        self.sweeper_out = None                 # (key, since) told stale, not yet told back
        self.silent = {}                        # host_id -> (key, since) told silent, not yet told back

    def look(self, witness, now_wall, *, doc=None):
        if doc is None:
            now = self._mono()
            if self._due is not None and now < self._due:
                return
            self._due = now + self.every_s
            try:
                doc = self.read()
            except Exception as exc:
                witness.failed(f"witness_read_failed:{type(exc).__name__}", now_wall.isoformat())
                return
        self.evaluate(witness, doc, now_wall)

    def evaluate(self, witness, doc, now_wall):
        from datetime import datetime, timedelta
        if not isinstance(doc, dict) or doc.get("schema") != "minimoi.runtime.state.v1":
            witness.failed("witness_read_failed:schema", now_wall.isoformat())
            return
        endpoint = witness.endpoint_id
        try:
            as_of = datetime.fromisoformat(str(doc.get("as_of")))
        except ValueError:
            as_of = None
        view_age = doc.get("view_age_s")
        sweeper_age = (doc.get("authority") or {}).get("sweeper_age_s")
        if as_of is not None and isinstance(view_age, (int, float)) and isinstance(sweeper_age, (int, float)):
            age = view_age + sweeper_age                       # how long since the sweeper ran, as of now
            since = (as_of - timedelta(seconds=sweeper_age)).replace(microsecond=0).isoformat()
            if age > self.gap_threshold_s and self.sweeper_out is None:
                key = f"sweeper-stale:{endpoint}:{since}"
                if witness._tell(key, f"The authority {endpoint}'s sweeper has not run since {since} ({int(age)} s, "
                                      f"past its {self.gap_threshold_s} s gap threshold), as {witness.host_id} reads "
                                      f"its snapshot. Leases are still judged on every call, but lapses, grace ends "
                                      f"and notices wait. Next: an operator restarts the authority's timers.", now_wall):
                    self.sweeper_out = (key, since)
            elif age <= self.gap_threshold_s and self.sweeper_out is not None:
                old = self.sweeper_out[1]
                if witness._tell(f"sweeper-back:{endpoint}:{old}",
                                 f"The authority {endpoint}'s sweeper runs again (stale since {old}). Next: nothing to "
                                 f"do.", now_wall):
                    self.sweeper_out = None
        seen = set()
        for h in doc.get("hosts") or []:
            hid = h.get("host_id")
            if not isinstance(hid, str) or hid == witness.host_id or h.get("availability") != "always_on":
                continue
            seen.add(hid)
            reach, age = h.get("reachability"), h.get("contact_age_s")
            if reach == "unreachable" and hid not in self.silent and as_of is not None \
                    and isinstance(age, (int, float)):
                since = (as_of - timedelta(seconds=age)).replace(microsecond=0).isoformat()
                key = f"host-silent:{hid}:{since}"
                if witness._tell(key, f"Host {hid} (always on) has not reached the authority {endpoint} since {since} "
                                      f"({int(age)} s), as {witness.host_id} reads its snapshot. Its items are held, "
                                      f"then lapsed; nobody takes them over. Next: an operator checks that host.",
                                 now_wall):
                    self.silent[hid] = (key, since)
            elif reach == "reachable" and hid in self.silent:
                old = self.silent[hid][1]
                if witness._tell(f"host-back:{hid}:{old}", f"Host {hid} reaches the authority {endpoint} again "
                                                          f"(silent since {old}). Next: nothing to do.", now_wall):
                    del self.silent[hid]


def https_reader(url, token_ref, *, stores=None, opener=None, timeout=10):
    """A ``read()`` for SnapshotChecks over the read path: GET with the observer credential read by reference at each
    read (never kept), only the snapshot's JSON as an answer, no redirect followed, no proxy."""
    from core.mc_delegation_dispatcher import door
    stores = stores if stores is not None else credential_stores(token_ref)
    open_ = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), door._NoRedirect()).open

    def read():
        store = stores.get(token_ref.split(":", 1)[0])
        if store is None:
            raise WitnessRefused("witness_ref_store_missing")
        req = urllib.request.Request(url, method="GET", headers={
            "Authorization": "Bearer " + store.read(token_ref), "User-Agent": "MiniMoi-witness/1"})
        with open_(req, timeout=timeout) as r:
            raw = r.read(32 * 1024 * 1024 + 1)
        doc = json.loads(raw.decode("utf-8"))
        if not isinstance(doc, dict) or doc.get("schema") != "minimoi.runtime.state.v1":
            raise WitnessRefused("witness_read_not_a_snapshot")
        return doc
    return read


def build(host, contact_timeout_s, artifact_root, *, gap_threshold_s=None, every_s=None):
    """The witness a host configuration asks for, or None: the ``witness`` role and a ``notifier``. With
    ``witness_read`` (P1-a) it also reads the snapshot every ``every_s`` while in contact."""
    if "witness" not in host.roles or host.notifier is None:
        return None
    n = host.notifier
    notifier = FileNotifier(n.path) if n.kind == "file" else TelegramNotifier(n.token_ref, n.chat_ref)
    snapshot = None
    wr = getattr(host, "witness_read", None)
    if wr is not None and gap_threshold_s is not None:
        snapshot = SnapshotChecks(https_reader(wr.url, wr.credential_ref), gap_threshold_s=gap_threshold_s,
                                  every_s=every_s or contact_timeout_s)
    return Witness(host.host_id, host.authority.endpoint_id, notifier,
                   Memory(Path(artifact_root) / "witness" / "told.jsonl"), contact_timeout_s, snapshot=snapshot)


# ── the witness on its own (P1-a): a witness that is not a host, beside an always-on authority ────────────────────
SERVE_ENV = ("MINIMOI_WITNESS_HOST_ID", "MINIMOI_WITNESS_ENDPOINT_ID", "MINIMOI_WITNESS_READ_URL",
             "MINIMOI_WITNESS_READ_TOKEN_REF", "MINIMOI_WITNESS_TELEGRAM_TOKEN_REF", "MINIMOI_WITNESS_TELEGRAM_CHAT_REF",
             "MINIMOI_WITNESS_MEMORY_DIR", "MINIMOI_WITNESS_AUTHORITY_FILE")
_INTERNAL_READ = re.compile(r"^http://portal:5001/internal/mc/v1/runtime/state$")   # the compose network's own path


def serve_config(environ) -> dict:
    """The stand-alone witness's configuration, from the environment: all of SERVE_ENV, each checked, or a refusal
    with a fixed code (a missing value admits nothing). The read URL is the door's own read path on one of its hosts,
    or the portal's on production's compose network; every credential is a reference."""
    missing = [k for k in SERVE_ENV if not environ.get(k)]
    if missing:
        raise WitnessRefused("witness_config_missing")
    from core.mc_delegation_dispatcher import door
    host_re = re.compile(r"^[A-Za-z0-9._-]{1,56}$")
    url = environ["MINIMOI_WITNESS_READ_URL"]
    bases = [d[:-len(door.PATH)] + "/internal/mc/v1/runtime/state" for d in door.DOORS]
    refs = [environ[k] for k in ("MINIMOI_WITNESS_READ_TOKEN_REF", "MINIMOI_WITNESS_TELEGRAM_TOKEN_REF",
                                 "MINIMOI_WITNESS_TELEGRAM_CHAT_REF")]
    ref_re = re.compile(r"^(keychain:[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,64}|ssm:/[A-Za-z0-9_./-]{1,200})$")
    if not (host_re.match(environ["MINIMOI_WITNESS_HOST_ID"]) and _ID.match(environ["MINIMOI_WITNESS_ENDPOINT_ID"])
            and (url in bases or _INTERNAL_READ.match(url)) and all(ref_re.match(r) for r in refs)):
        raise WitnessRefused("witness_config_invalid")
    from core.mc_delegation_dispatcher.store import Authority
    auth = Authority.load(environ["MINIMOI_WITNESS_AUTHORITY_FILE"])
    if auth.hosts is None or auth.sweeper is None:
        raise WitnessRefused("witness_values_missing")
    return {"host_id": environ["MINIMOI_WITNESS_HOST_ID"], "endpoint_id": environ["MINIMOI_WITNESS_ENDPOINT_ID"],
            "url": url, "read_ref": refs[0], "token_ref": refs[1], "chat_ref": refs[2],
            "memory_dir": Path(environ["MINIMOI_WITNESS_MEMORY_DIR"]),
            "contact_interval_s": auth.hosts.contact_interval_s, "contact_timeout_s": auth.hosts.contact_timeout_s,
            "gap_threshold_s": auth.sweeper.gap_threshold_s}


_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class Standalone:
    """One witness pass at a time: read the snapshot; a read that fails starts (or continues) the outage measured on
    the monotonic clock, told once past the contact timeout (the first check); a good read ends it and runs the
    second and third checks on what it read. ``health`` is touched after every pass."""

    def __init__(self, witness, read, checks, *, mono=None, now=None, health=None):
        import time as _time
        from datetime import datetime, timezone
        self.witness, self.read, self.checks = witness, read, checks
        self._mono = mono or _time.monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.health = None if health is None else Path(health)
        self.failing_since = None

    def tick(self):
        now = self._now()
        try:
            doc = self.read()
        except Exception as exc:
            if self.failing_since is None:
                self.failing_since = self._mono()
            self.witness.failed(f"witness_read_failed:{getattr(exc, 'code', None) or type(exc).__name__}",
                                now.isoformat())
            self.witness.observe(self._mono() - self.failing_since, now)
        else:
            self.failing_since = None
            self.witness.observe(None, now)
            self.checks.evaluate(self.witness, doc, now)
        if self.health is not None:
            try:
                self.health.write_text(now.isoformat())
            except OSError:
                pass


def serve(environ=None, *, stop=None) -> int:
    """``python -m core.mc_delegation_runner.witness serve``: the stand-alone witness (production's EC2 witness, in a
    process of its own, never the authority's). Telegram direct, by reference; its memory a file of its own."""
    import signal
    import threading
    environ = os.environ if environ is None else environ
    try:
        cfg = serve_config(environ)
    except Exception as exc:
        print(json.dumps({"state": "refused", "code": getattr(exc, "code", None) or type(exc).__name__}),
              file=sys.stderr, flush=True)
        return 2
    memory = Memory(cfg["memory_dir"] / "told.jsonl")
    w = Witness(cfg["host_id"], cfg["endpoint_id"], TelegramNotifier(cfg["token_ref"], cfg["chat_ref"]), memory,
                cfg["contact_timeout_s"])
    checks = SnapshotChecks(None, gap_threshold_s=cfg["gap_threshold_s"], every_s=cfg["contact_interval_s"])
    runner = Standalone(w, https_reader(cfg["url"], cfg["read_ref"]), checks,
                        health=environ.get("MINIMOI_WITNESS_HEALTH_FILE") or "/tmp/witness.alive")
    stop = stop or threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(json.dumps({"state": "watching", "host_id": cfg["host_id"], "endpoint_id": cfg["endpoint_id"],
                      "every_s": cfg["contact_interval_s"]}), flush=True)
    while not stop.is_set():
        runner.tick()
        stop.wait(cfg["contact_interval_s"])
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["serve"]:
        print("usage: python -m core.mc_delegation_runner.witness serve", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(serve())
