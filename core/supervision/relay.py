"""The relay between production's request ledger and the Mac's (OPS-2026-10-27-mc-front-door-prod), carried by the
supervision sync's worker token over outbound HTTPS from the Mac; nothing new is opened.

* ``pull``: requests Robert put to an agent through MC on production (origin ``owner_message_via_mc``) that the Mac has
  not taken yet. The Mac creates each in its shared ledger with the same id and content, then acks it.
* ``ack``: marks those requests relayed (a marker file in the request's folder, written once).
* ``apply``: the agents' replies from the Mac (receipt, progress, result, needs-you, cancel), each applied with the
  ledger's own functions, so the ledger's actor rules and duplicate refusal hold. Only for relayed requests; each Mac
  event once. The worker never creates a request here, never answers a decision and never acts as Robert.
* ``listeners``: an agent the Mac saw listening in the last two minutes is marked listening here too, with its age.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import time

from core.agent_requests import ledger as L

MAX_PULL = 20
MAX_EVENTS = 20
# The whole pull answer (decisions, nudges, then requests) stays under PULL_BYTES as the portal sends it (Flask's JSON:
# ASCII escapes, so one non-BMP character is 12 bytes). The Mac reads at most READ_MAX (supervision_sync.READ_MAX, the
# same number); the gap leaves room for an indented debug answer. Requests that do not fit come on the next pull.
PULL_BYTES, READ_MAX = 196608, 262144
LISTENING_S = 120
KINDS = ("receipt", "progress", "result", "needs_you", "cancel")
ACTORS = ("mc", "claude-code", "codex", "grok")          # never "robert": the worker cannot act as Robert
TEXT_KINDS = ("progress", "result", "needs_you")
MARKER, APPLIED, LOCK = "relayed.json", "relay-events.log", ".relay-lock"
EVENT_ID = re.compile(r"^[0-9a-f]{8,40}$")
ORIGIN = "owner_message_via_mc"


class Refused(ValueError):
    pass


def need(ok):
    if not ok:
        raise Refused("supervision_request_invalid")


def clean_event(e):
    """One reply from the Mac's ledger, as strictly as a stop line: known keys, kinds and actors, bounded text."""
    need(type(e) is dict and set(e) <= {"request_id", "event_id", "kind", "actor", "text"})
    need(L.valid_request_id(e.get("request_id")))
    need(type(e.get("event_id")) is str and EVENT_ID.match(e["event_id"]))
    need(e.get("kind") in KINDS and e.get("actor") in ACTORS)
    text = e.get("text", "")
    need(type(text) is str and len(text) <= L.TEXT_MAX and not re.search(r"[\x00-\x08\x0b-\x1f\x7f]", text))
    need(bool(text.strip()) if e["kind"] in TEXT_KINDS else not text)
    return {"request_id": e["request_id"], "event_id": e["event_id"], "kind": e["kind"], "actor": e["actor"],
            "text": text}


def wire_size(value) -> int:
    """Bytes of ``value`` as the portal's jsonify writes it (ensure_ascii, sorted keys, compact)."""
    return len(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii"))


def fill(answer: dict, requests: list[dict]) -> dict:
    """``answer`` with as many of ``requests`` as fit, in order, under PULL_BYTES for the whole answer. Decisions and
    nudges are already in it, so requests never crowd them out; the rest wait for the next pull."""
    out = dict(answer, requests=[])
    used = wire_size(out)
    for r in requests:
        size = wire_size(r) + (1 if out["requests"] else 0)
        if used + size > PULL_BYTES:
            break
        out["requests"].append(r)
        used += size
    return out


def clean_listeners(value):
    """{agent: seconds since its watcher last said it was listening}, as the Mac read its armed markers."""
    need(type(value) is dict and len(value) <= len(L.RECIPIENTS))
    need(all(k in L.RECIPIENTS and type(v) in (int, float) and 0 <= v <= 30 * 86400 for k, v in value.items()))
    return dict(value)


class Relay:
    def __init__(self, root: str):
        self.root = root

    def _folder(self, rid):
        return os.path.join(self.root, "requests", rid)

    def relayed(self, rid) -> bool:
        path = os.path.join(self._folder(rid), MARKER)
        return not os.path.islink(path) and os.path.isfile(path)

    def _eligible(self, doc) -> bool:
        r = doc["request"]
        return (r.get("origin") == ORIGIN and r.get("from") == "mc" and r.get("to") in L.RECIPIENTS
                and r.get("topic_id") is None and r.get("pointer") is None)

    def pull(self) -> list[dict]:
        """Open requests for the Mac to take, oldest first, at most MAX_PULL."""
        out = []
        for rid in L._all_ids(self.root):
            doc = L.load(self.root, rid)
            if doc and self._eligible(doc) and doc["closed"] is None and not self.relayed(rid):
                out.append(doc)
        out.sort(key=lambda d: L._order(self.root, d))
        return [{"request_id": d["request"]["request_id"], "to": d["request"]["to"], "action": d["request"]["action"],
                 "conversation_id": d["request"]["conversation_id"], "created_at": d["request"]["created_at"],
                 "content_sha256": d["request"]["content_sha256"]} for d in out[:MAX_PULL]]

    def ack(self, ids) -> list[str]:
        """Mark each eligible request relayed, once (temp file, then link: never half written, never replaced)."""
        done = []
        for rid in ids:
            doc = L.load(self.root, rid)
            if not doc or not self._eligible(doc):
                continue
            L._write_new(os.path.join(self._folder(rid), MARKER),
                         json.dumps({"relayed_at": L._now()}).encode("utf-8"))
            done.append(rid)
        return done

    def _applied(self, rid) -> set:
        path = os.path.join(self._folder(rid), APPLIED)
        try:
            if os.path.islink(path):
                return set()
            with open(path, "rb") as fh:
                return set(fh.read(1024 * 1024).decode("utf-8").split())
        except OSError:
            return set()

    def apply(self, events) -> list[dict]:
        """Each event: ``applied`` (the ledger took it), ``duplicate`` (this Mac event, or the very same report, was
        already applied: no second effect) or ``refused`` with the ledger's code (or ``not_relayed``)."""
        out = []
        for e in events:
            rid, key = e["request_id"], f"{e['request_id']}:{e['event_id']}"
            if L._request(self.root, rid) is None or not self.relayed(rid):
                out.append({"key": key, "state": "refused", "code": "not_relayed"})
                continue
            fd = os.open(os.path.join(self._folder(rid), LOCK), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                         0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                if e["event_id"] in self._applied(rid):
                    out.append({"key": key, "state": "duplicate"})
                    continue
                try:
                    if e["kind"] == "receipt":
                        _, created = L.receipt(self.root, rid, e["actor"])
                    elif e["kind"] == "cancel":
                        _, created = L.cancel(self.root, rid, e["actor"])
                    else:
                        _, created = getattr(L, e["kind"])(self.root, rid, e["actor"], e["text"])
                except L.Refused as exc:
                    out.append({"key": key, "state": "refused", "code": exc.code})
                    continue
                L._append_line(os.path.join(self._folder(rid), APPLIED), e["event_id"])
                out.append({"key": key, "state": "applied" if created else "duplicate"})
            finally:
                os.close(fd)
        return out

    def listeners(self, ages, now=None) -> list[str]:
        """Mark listening here each agent the Mac saw in the last LISTENING_S, carrying its age; others untouched."""
        now = time.time() if now is None else now
        armed = []
        for agent, age in sorted(ages.items()):
            if age <= LISTENING_S:
                L.arm(self.root, agent)
                path = os.path.join(self.root, "pending", f"{agent}.armed")
                os.utime(path, (now - age, now - age), follow_symlinks=False)
                armed.append(agent)
        return armed
