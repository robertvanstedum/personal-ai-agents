"""The one authoritative record of what is waiting on Robert (spec v0.2 §3.2–3.3).

``store.json`` holds the code counter, the key index (every ref version ever seen under each key) and every item. Each
transition (create, join, tell a group, resolve) is one atomic replace of that file, so a crash leaves the old or the
new record and never a mix (Codex B2). ``state.json`` is only a heartbeat, rebuilt when malformed. ``events.jsonl`` is
an audit trail appended after each commit and never read back to rebuild anything; it holds codes and fixed reason
codes only, never free text.

Every write takes ``.lock``: an in-process ``threading.RLock`` plus ``fcntl.flock`` on a fresh descriptor (the
``auth._auth_lock`` pattern), so request threads and the sender thread exclude each other as well as other processes.
"""
from __future__ import annotations

import contextlib
import fcntl
import glob
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path

SCHEMA = "action-notices/2"
ITEM_SCHEMA = "action-notice/2"
STORE, STATE, EVENTS, ARCHIVE, KEY = "store.json", "state.json", "events.jsonl", "archive.jsonl", "hmac.key"
KINDS = ("guest_access", "password_reset", "decision", "agent_ask")
RESOLUTIONS = ("source_closed", "agent_resolved", "owner_dismissed", "expired")
CODE = re.compile(r"^W-(\d{1,6})$")
_REASON = re.compile(r"^[A-Za-z0-9_:.\-]{1,80}$")

_log = logging.getLogger("action_waiting")


class StoreUnavailable(RuntimeError):
    """The store could not be read or written. The message is a fixed code, never a path or content."""


def empty() -> dict:
    return {"schema": SCHEMA, "next_code": 1, "index": {}, "items": {}, "sources": {}}


def code_number(code) -> int:
    m = CODE.match(str(code or ""))
    return int(m.group(1)) if m else 0


def _valid(data) -> bool:
    return (isinstance(data, dict) and data.get("schema") == SCHEMA and isinstance(data.get("next_code"), int)
            and isinstance(data.get("index"), dict) and isinstance(data.get("items"), dict)
            and isinstance(data.get("sources", {}), dict))


class Tx:
    """One store transaction: the loaded record, and the audit events to append once it is committed."""

    def __init__(self, data):
        self.data = data
        self.events = []

    def event(self, kind, code, *, reason=None, message_id=None):
        entry = {"kind": kind, "code": code}
        if reason is not None:
            entry["reason"] = reason if _REASON.match(str(reason)) else "other"
        if message_id is not None:
            entry["message_id"] = int(message_id)
        self.events.append(entry)


class Store:
    def __init__(self, root, *, clock=time.time):
        self.root = Path(root) if root else None    # None: MINIMOI_ACTION_NOTICES_DIR unset; every use refuses
        self.clock = clock
        self._thread_lock = threading.RLock()
        self._local = threading.local()
        self._key = None
        self.last_commit_ok = None          # None: nothing written yet by this process

    # ---- locking ------------------------------------------------------------------------------------------------
    @contextlib.contextmanager
    def lock(self):
        with self._thread_lock:
            depth = getattr(self._local, "depth", 0)
            if depth:
                self._local.depth = depth + 1
                try:
                    yield
                finally:
                    self._local.depth -= 1
                return
            if self.root is None:
                self.last_commit_ok = False
                raise StoreUnavailable("store_unset")
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.root / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
            except OSError:
                self.last_commit_ok = False
                raise StoreUnavailable("store_unwritable") from None
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._local.depth = 1
                try:
                    yield
                finally:
                    self._local.depth = 0
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    # ---- the authoritative record ------------------------------------------------------------------------------
    def load(self) -> dict:
        if self.root is None:
            raise StoreUnavailable("store_unset")
        try:
            raw = (self.root / STORE).read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty()
        except OSError:
            raise StoreUnavailable("store_unreadable") from None
        try:
            data = json.loads(raw)
        except ValueError:
            raise StoreUnavailable("store_malformed") from None
        if not _valid(data):
            raise StoreUnavailable("store_malformed")
        data.setdefault("sources", {})
        return data

    def readable(self) -> bool:
        try:
            self.load()
            return True
        except StoreUnavailable:
            return False

    def _atomic_write(self, name: str, payload: bytes, mode=0o600):
        tmp = self.root / f".{name}.{os.getpid()}.{threading.get_ident()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, self.root / name)
        try:
            dfd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass                                       # a filesystem without directory fsync; the rename stands

    def _sweep_tmp(self, name: str):
        """A temp file a crashed writer left behind is never read; remove it on the next commit."""
        mine = f".{name}.{os.getpid()}.{threading.get_ident()}.tmp"
        for path in glob.glob(str(self.root / f".{name}.*.tmp")):
            if os.path.basename(path) != mine:
                with contextlib.suppress(OSError):
                    os.unlink(path)

    @contextlib.contextmanager
    def edit(self):
        """Load, change, commit once (only if something changed), then append the audit events."""
        with self.lock():
            data = self.load()
            before = json.dumps(data, sort_keys=True)
            tx = Tx(data)
            yield tx
            after = json.dumps(data, sort_keys=True)
            if after != before:
                try:
                    self._sweep_tmp(STORE)
                    self._atomic_write(STORE, after.encode("utf-8"))
                except OSError:
                    self.last_commit_ok = False
                    raise StoreUnavailable("store_write_failed") from None
                self.last_commit_ok = True
            self._append(EVENTS, [dict(e, at=round(self.clock(), 3)) for e in tx.events])

    def read(self) -> dict:
        with self.lock():
            return self.load()

    # ---- side files ---------------------------------------------------------------------------------------------
    def _append(self, name, records):
        if not records or self.root is None:
            return
        try:
            with open(self.root / name, "a", encoding="utf-8") as fh:
                for record in records:
                    fh.write(json.dumps(record, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            _log.warning("action notices: %s could not be appended", name)

    def archive(self, items):
        """Resolved items leaving the store are appended here first (an item in both is the store's)."""
        self._append(ARCHIVE, items)

    def write_state(self, state: dict):
        if self.root is None:
            return
        try:
            self._sweep_tmp(STATE)
            self._atomic_write(STATE, json.dumps(state, sort_keys=True).encode("utf-8"))
        except OSError:
            _log.warning("action notices: state.json could not be written")

    def read_state(self) -> dict:
        if self.root is None:
            return {}
        try:
            data = json.loads((self.root / STATE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # ---- keys ---------------------------------------------------------------------------------------------------
    def key_bytes(self) -> bytes:
        """The per-environment HMAC key for guest and reset keys (never an unsalted email hash, review N7)."""
        if self._key is not None:
            return self._key
        path = self.root / KEY
        with self.lock():
            try:
                key = path.read_bytes()
            except FileNotFoundError:
                key = None
            except OSError:
                raise StoreUnavailable("key_unreadable") from None
            if key is None:
                key = secrets.token_bytes(32)
                try:
                    self._atomic_write(KEY, key)
                except OSError:
                    raise StoreUnavailable("store_unwritable") from None
            elif len(key) != 32:
                raise StoreUnavailable("key_malformed")
        self._key = key
        return key

    def digest(self, kind: str, value: str) -> str:
        return hmac.new(self.key_bytes(), f"{kind}\x00{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:20]


# ---- items ----------------------------------------------------------------------------------------------------------

def new_delivery(now, notify_after=None) -> dict:
    return {"state": "pending", "attempts": 0, "last_code": None, "next_try_at": now, "failing_since": None,
            "notify_after": notify_after if notify_after is not None else now, "told_at": None, "message_id": None,
            "via": None, "reminded_at": None, "remind_attempts": 0, "remind_next_try_at": None}


def _apply(item, fields, *, only_missing=False):
    for name, value in (fields or {}).items():
        if name == "facts":
            facts = item.setdefault("facts", {})
            for k, v in (value or {}).items():
                if not only_missing or facts.get(k) in (None, ""):
                    facts[k] = v
        elif name in ("app", "words", "words_sha256"):
            if not only_missing or item.get(name) in (None, ""):
                item[name] = value


def _create(tx, *, key, kind, versions, now, fields=None, notify_after=None, generation=1, seen=()):
    data = tx.data
    code = f"W-{data['next_code']}"
    data["next_code"] += 1
    item = {"schema": ITEM_SCHEMA, "code": code, "key": key, "generation": generation, "kind": kind,
            "refs": list(dict.fromkeys(versions)), "app": None, "facts": {}, "words": "", "words_sha256": None,
            "opened_at": now, "state": "open", "stale": False, "source_lost": False,
            "resolved_at": None, "resolution": None, "delivery": new_delivery(now, notify_after)}
    _apply(item, fields)
    data["items"][code] = item
    data["index"][key] = {"code": code, "generation": generation, "state": "open",
                          "seen": list(dict.fromkeys(list(seen) + list(versions)))}
    tx.event("created", code, reason=kind)
    return item


def observe(tx, *, key, kind, versions, now, fields=None, notify_after=None):
    """A source shows ``versions`` under ``key``. Returns (outcome, item):

    * ``created``: no open item and a version never seen → a new item (the next generation);
    * ``joined``: an open item and a new version → added to it, no new notice;
    * ``open``: an open item, nothing new;
    * ``seen``: no open item and every version already seen (a flicker, or a lingering answered ref) → nothing."""
    data = tx.data
    idx = data["index"].get(key)
    seen = list(idx["seen"]) if idx else []
    fresh = [v for v in dict.fromkeys(versions) if v not in seen]
    if idx and idx.get("state") == "open" and idx.get("code") in data["items"]:
        item = data["items"][idx["code"]]
        if fresh:
            item["refs"].extend(v for v in fresh if v not in item["refs"])
            idx["seen"] = seen + fresh
            item["source_lost"] = False
            _apply(item, fields, only_missing=True)
            tx.event("joined", item["code"])
            return "joined", item
        return "open", item
    if not fresh:
        return "seen", None
    generation = (idx.get("generation", 0) + 1) if idx else 1
    return "created", _create(tx, key=key, kind=kind, versions=versions, now=now, fields=fields,
                              notify_after=notify_after, generation=generation, seen=seen)


def open_agent(tx, *, key, now, fields):
    """An agent ask by its caller key: the open item, or a new generation (a resolved key asks again, S3)."""
    data = tx.data
    idx = data["index"].get(key)
    if idx and idx.get("state") == "open" and idx.get("code") in data["items"]:
        return "open", data["items"][idx["code"]]
    generation = (idx.get("generation", 0) + 1) if idx else 1
    seen = list(idx["seen"]) if idx else []
    return "created", _create(tx, key=key, kind="agent_ask", versions=[f"agent:g{generation}"], now=now,
                              fields=fields, generation=generation, seen=seen)


def resolve(tx, item, resolution, now) -> bool:
    if item.get("state") != "open":
        return False
    item.update(state="resolved", resolved_at=now, resolution=resolution)
    idx = tx.data["index"].get(item["key"])
    if idx and idx.get("code") == item["code"]:
        idx["state"] = "resolved"
    tx.event("dismissed" if resolution == "owner_dismissed" else "resolved", item["code"], reason=resolution)
    return True


def open_items(data) -> list:
    return sorted((i for i in data["items"].values() if i.get("state") == "open"),
                  key=lambda i: (i.get("opened_at") or 0, code_number(i.get("code"))))


def by_key(data, key):
    idx = data["index"].get(key)
    return data["items"].get(idx["code"]) if idx else None


def store_of(ref: str) -> str | None:
    """Which source a ref version belongs to."""
    head = str(ref).split(":", 1)[0]
    return {"guild": "guild", "pending": "pending", "reset": "resets", "decision": "decisions"}.get(head)
