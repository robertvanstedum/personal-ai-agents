"""A version 2 task's pending records, ``<task>.pending.jsonl`` (host runtime spec v0.5 R3.2-c): the runner's own
resend-until-acknowledged rule, applied to every owner write.

* **Write-ahead.** Every owner write (progress with its checkpoint, an effect's ``finish``, the result) is appended and
  fsynced *before* it is first sent, with its ``event_id``, its host time ``at`` and its exact arguments. So whatever
  might have reached the authority is on file, and a replay sends exactly what was first sent (byte-faithful: the
  authority refuses any other body under a used event id, ``event_id_conflict``). The one exception is an effect
  finish's receipt-lease token, which never touches disk: it lives in memory for the lease's at most 30 s, and a finish
  replayed without it records the same outcome under a reconcile lease (the store's readback path).
* **Marks.** An answer appends a mark: ``applied`` (accepted, applied, superseded, ok or already_applied) or ``final``
  (a refusal the authority will repeat for these same bytes: grant_revoked, event_id_conflict, lease_lapsed outside
  the pre-lapse window, grace_ended, item_ended, stale_generation, ...). Either stops resending. A record with no mark
  is pending; a transport failure leaves it so.
* **Notes.** Facts the host keeps for itself, never sent and never replayed: ``freeze``, ``thaw``, ``reconcile`` (with
  the answer), ``usage``, ``effect_open``, ``stop``, ``wake``. T6a reads ``freeze`` before ``reconcile`` here.
* **Damage fails closed.** Each line carries the SHA-256 of its own canonical form. A line that does not parse or
  whose sum does not match makes the whole file ``JournalDamaged``: nothing in it is replayed and the host takes no
  new work until an operator repairs or archives it (as the connection manager's history, R3.2-c). Only a missing file
  means "no records".
* The journal admits, claims and extends nothing.

Stdlib only; Python 3.9-compatible.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

SCHEMA = 1
CALL_KINDS = ("progress", "result", "effect_finish", "claim")   # claim: the host's own claim requests (Codex F2)
NOTE_KINDS = ("freeze", "thaw", "reconcile", "usage", "effect_open", "effect_closed", "stop", "wake", "checkpoint")
MARKS = ("applied", "final")
MAX_BYTES = 16 * 1024 * 1024
# Refusals a resend cannot change: the same bytes get the same answer. Anything else (a transport failure, or a host
# time the authority judged to be in its future) stays pending.
RETRYABLE = ("event_time_invalid",)
# A finish answers its receipt lease's state, not its bytes: a lease refusal is never its final answer. The runtime sends
# the same outcome under a reconcile lease instead (runner._send_finish), so these never stop it (review S2).
RETRYABLE_BY_KIND = {"effect_finish": ("receipt_lease_stale", "receipt_busy")}


class JournalDamaged(RuntimeError):
    """The file exists and cannot be read completely and exactly: fail closed (the message is a fixed code)."""


def _canon(doc) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sum(doc) -> str:
    return hashlib.sha256(_canon({k: v for k, v in doc.items() if k != "sum"})).hexdigest()


class Journal:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    # ── reading ──
    def entries(self) -> list:
        """Every line, checked. [] for a missing file; JournalDamaged for anything else that is not exact."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return []
        if size > MAX_BYTES:
            raise JournalDamaged("pending_file_too_large")
        try:
            raw = self.path.read_bytes()
        except OSError:
            raise JournalDamaged("pending_file_unreadable") from None
        if raw and not raw.endswith(b"\n"):
            raise JournalDamaged("pending_file_torn")
        out = []
        for n, line in enumerate(raw.split(b"\n")[:-1], start=1):
            try:
                doc = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise JournalDamaged(f"pending_line_unreadable:{n}") from None
            if (type(doc) is not dict or doc.get("v") != SCHEMA or doc.get("sum") != _sum(doc)
                    or not _shape_ok(doc)):
                raise JournalDamaged(f"pending_line_invalid:{n}")
            out.append(doc)
        return out

    def calls(self) -> list:
        """Each call record, with its latest mark folded in as ``mark``/``answer`` (None while pending)."""
        entries = self.entries()
        marks = {}
        for e in entries:
            if e["type"] == "mark":
                marks[e["event_id"]] = e
        out = []
        for e in entries:
            if e["type"] == "call":
                m = marks.get(e["event_id"])
                out.append({**e, "mark": None if m is None else m["mark"], "answer": None if m is None else m["answer"]})
        return out

    def pending(self) -> list:
        return [c for c in self.calls() if c["mark"] is None]

    def notes(self, kind=None) -> list:
        return [e for e in self.entries() if e["type"] == "note" and (kind is None or e["kind"] == kind)]

    # ── writing ──
    def _append(self, doc: dict) -> dict:
        doc = {**doc, "v": SCHEMA}
        doc["sum"] = _sum(doc)
        line = _canon(doc) + b"\n"
        with self._lock:
            new = not self.path.exists()
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                os.close(fd)
            if new:                                    # the file's own name is durable too
                dfd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
        return doc

    def clear(self):
        """Start the file again, empty: only when every record in it is answered (the claim requests' journal, which
        would otherwise grow by one record each pass)."""
        with self._lock:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                return
            dfd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)

    def call(self, kind, event_id, request_id, generation, at, args) -> dict:
        assert kind in CALL_KINDS
        return self._append({"type": "call", "kind": kind, "event_id": event_id, "request_id": request_id,
                             "generation": generation, "at": at, "args": args})

    def mark(self, event_id, mark, answer) -> dict:
        assert mark in MARKS
        return self._append({"type": "mark", "event_id": event_id, "mark": mark, "answer": answer})

    def note(self, kind, at, **fields) -> dict:
        assert kind in NOTE_KINDS
        return self._append({"type": "note", "kind": kind, "at": at, "fields": fields})


def _shape_ok(doc) -> bool:
    t = doc.get("type")
    if t == "call":
        return (set(doc) == {"v", "sum", "type", "kind", "event_id", "request_id", "generation", "at", "args"}
                and doc["kind"] in CALL_KINDS and type(doc["event_id"]) is str and type(doc["args"]) is dict
                and type(doc["generation"]) is int and type(doc["at"]) is str)
    if t == "mark":
        return (set(doc) == {"v", "sum", "type", "event_id", "mark", "answer"} and doc["mark"] in MARKS
                and type(doc["event_id"]) is str)
    if t == "note":
        return (set(doc) == {"v", "sum", "type", "kind", "at", "fields"} and doc["kind"] in NOTE_KINDS
                and type(doc["fields"]) is dict)
    return False


def classify(kind, answer) -> str:
    """'applied', 'final' or 'pending' for one answer to a replayed or first-sent call."""
    if not isinstance(answer, dict):
        return "pending"
    state = answer.get("state")
    if state in ("applied", "superseded", "accepted", "ok", "already_applied"):
        return "applied"
    if state == "refused":
        code = answer.get("code")
        return "pending" if code in RETRYABLE or code in RETRYABLE_BY_KIND.get(kind, ()) else "final"
    return "pending"
