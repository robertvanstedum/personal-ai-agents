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

_KEY = re.compile(r"^authority-(unreachable|back):[A-Za-z0-9._:-]{1,64}:[0-9T:.+-]{10,40}$")
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


class TelegramNotifier:
    """Robert's Telegram, from the host: the bot token and the chat id are read by Keychain reference at each send
    (``keychain:<service>/<account>``, launch.KeychainCredentialStore), never held in a file, the environment or a log.
    Built for the Mac; not exercised in S1 (no Keychain read happens there)."""
    API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token_ref, chat_ref, *, store=None, opener=None):
        from . import launch
        self.token_ref, self.chat_ref = token_ref, chat_ref
        self.store = store or launch.KeychainCredentialStore()
        self._open = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open

    def send(self, key, text, at):
        token, chat = self.store.read(self.token_ref), self.store.read(self.chat_ref)
        body = json.dumps({"chat_id": int(chat), "text": text}).encode()
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

    def __init__(self, host_id, endpoint_id, notifier, memory, timeout_s):
        self.host_id, self.endpoint_id, self.notifier, self.memory = host_id, endpoint_id, notifier, memory
        self.timeout_s = timeout_s
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
        if self.undelivered == key:
            self.undelivered = None
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


def build(host, contact_timeout_s, artifact_root):
    """The witness a host configuration asks for, or None: the ``witness`` role and a ``notifier``."""
    if "witness" not in host.roles or host.notifier is None:
        return None
    n = host.notifier
    notifier = FileNotifier(n.path) if n.kind == "file" else TelegramNotifier(n.token_ref, n.chat_ref)
    return Witness(host.host_id, host.authority.endpoint_id, notifier,
                   Memory(Path(artifact_root) / "witness" / "told.jsonl"), contact_timeout_s)
