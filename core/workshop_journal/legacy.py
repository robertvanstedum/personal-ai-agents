"""The v1 Workshop API on top of the new journal engine (compatibility layer).

``minimoi_portal/workshop/record.py`` re-exports this module, so the screen, the observer and the CLI keep working. The v1
vocabulary and ``validate`` are carried over unchanged (v1 events keep their exact line format); what changed is underneath:
appends go through the one locked, recovering, durable writer in ``journal`` (no unlocked legacy writer remains), reads never
trust a stale ``state.json`` (it loses to the journal), and the reducer no longer lets a label clear an owner question.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from core.workshop_journal import reducer
from core.workshop_journal.errors import JournalError
from core.workshop_journal.journal import Journal, Result

VERSION = 1
ACTORS = ("claude-code", "codex", "robert", "host", "mc")
KINDS = ("started", "progress", "needs_you", "decision", "blocked", "done", "review", "health", "next")
STAGES = ("design", "review", "build", "test", "staging", "merged", "production")
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
ITEM_RE = re.compile(r"^(pr|spec|queue|init|host|room):[A-Za-z0-9._-]{1,60}$")
FIELDS = {"v", "event_id", "at", "workshop", "actor", "kind", "item", "stage", "text", "refs", "room",
          "next_actor", "health"}
TEXT_MAX = 280
LINE_MAX = 4000
STALE_AFTER = timedelta(minutes=15)
CLOCK_AHEAD = timedelta(minutes=2)          # a reading dated further ahead than this is untrustworthy
_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|xai-[A-Za-z0-9]{16,}|tvly-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{10,}|"
                     r"github_pat_|bearer\s+\S+|-----BEGIN|eyJ[A-Za-z0-9_\-]{20,}\.|AKIA[0-9A-Z]{12,}|"
                     r"[a-z]+://[^\s/:@]+:[^\s/@]+@)", re.IGNORECASE)


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def parse(value) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(value))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _clean(name, value, required=False, limit=200):
    if value is None:
        if required:
            raise ValueError(f"workshop event {name} is required")
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"workshop event {name} must be short text")
    if _SECRET.search(value):
        raise ValueError(f"workshop event {name} looks like a credential; refused")
    return value.strip()


def validate(ev: dict) -> dict:
    if not isinstance(ev, dict):
        raise ValueError("a workshop event is an object")
    unknown = set(ev) - FIELDS
    if unknown:
        raise ValueError(f"workshop event has unknown fields: {sorted(unknown)}")
    out = {"v": VERSION, "event_id": str(uuid.UUID(str(ev.get("event_id") or uuid.uuid4()))),
           "at": _clean("at", ev.get("at") or iso(now()), True, 40)}
    if parse(out["at"]) is None:
        raise ValueError("workshop event at must be an ISO time")
    out["workshop"] = _clean("workshop", ev.get("workshop"), True, 32)
    if not ID_RE.fullmatch(out["workshop"]):
        raise ValueError("workshop id is lower-case letters, digits and -")
    for name, allowed in (("actor", ACTORS), ("kind", KINDS)):
        out[name] = _clean(name, ev.get(name), True, 20)
        if out[name] not in allowed:
            raise ValueError(f"workshop event {name} must be one of {allowed}")
    out["item"] = _clean("item", ev.get("item"), True, 64)
    if not ITEM_RE.fullmatch(out["item"]):
        raise ValueError("workshop event item looks like pr:265, queue:146, spec:streaming or host:mac")
    out["stage"] = _clean("stage", ev.get("stage"), False, 20)
    if out["stage"] is not None and out["stage"] not in STAGES:
        raise ValueError(f"workshop event stage must be one of {STAGES}")
    out["text"] = _clean("text", ev.get("text"), True, TEXT_MAX)
    out["next_actor"] = _clean("next_actor", ev.get("next_actor"), False, 20)
    if out["next_actor"] is not None and out["next_actor"] not in ACTORS:
        raise ValueError(f"workshop event next_actor must be one of {ACTORS}")
    out["room"] = _clean("room", ev.get("room"), False, 64)
    refs = ev.get("refs")
    if refs is not None:
        if not isinstance(refs, dict) or len(refs) > 6:
            raise ValueError("workshop event refs is a small object")
        refs = {_clean("refs key", k, True, 24): _clean("refs value", str(v), True, 160) for k, v in refs.items()}
    out["refs"] = refs
    health = ev.get("health")
    if health is not None:
        if out["kind"] != "health" or not isinstance(health, dict):
            raise ValueError("health data belongs to a health event")
        text = json.dumps(health)
        if len(text) > 2500 or _SECRET.search(text):
            raise ValueError("health data is small and has no credentials")
    out["health"] = health
    if len(json.dumps(out, separators=(",", ":"))) > LINE_MAX:
        raise ValueError("workshop event is too long")
    return out



class Workshop:
    """The v1 object API. One per workshop ID; every write goes through ``Journal.append_legacy``."""

    def __init__(self, root: str, workshop_id: str):
        if not ID_RE.fullmatch(workshop_id or ""):
            raise ValueError("workshop id is lower-case letters, digits and -")
        self.id = workshop_id
        self.journal = Journal(root, workshop_id, actors=None)
        self.dir = self.journal.dir
        self.events_path = self.journal.events_path
        self.state_path = self.journal.state_path

    def append(self, event: dict) -> dict:
        ev = validate({**event, "workshop": self.id})
        result: Result = self.journal.append_legacy(ev)
        if not result.ok:
            raise OSError(f"workshop append failed: {result.status} ({result.reason})")
        return ev

    def events(self) -> list[dict]:
        """The valid prefix of the journal (legacy and v2 rows). An unterminated tail is never part of it."""
        return self.journal.read(deep=False).events

    def write_state(self) -> dict:
        return self.journal.write_state()


def load_state(root: str | None, workshop_id: str) -> tuple[dict | None, str]:
    """(state, status): ok, missing, unreadable or stale. Never "nothing running" when unknown.

    ``state.json`` always loses to the journal: if its watermark does not match the journal as read now, the state is rebuilt
    from the journal (in memory; a read writes nothing), and a journal that cannot be read refuses instead of reporting a stale
    "as of"."""
    if not root:
        return None, "missing"
    try:
        ws = Workshop(root, workshop_id)
    except ValueError:
        return None, "missing"
    state, source = ws.journal.state()
    if state is None:
        return None, "missing" if source in ("missing", "unsupported_writer") else "unreadable"
    host = state.get("host") if isinstance(state.get("host"), dict) else {}
    seen = parse(host.get("observed_at") or host.get("at")) if host else None
    # A reading dated more than CLOCK_AHEAD in the future is not trusted either (the Mac's and the server's clocks
    # disagree): never "fresh".
    if seen is None or now() - seen > STALE_AFTER or seen - now() > CLOCK_AHEAD:
        return state, "stale"
    return state, "ok"


reduce = reducer.reduce

__all__ = ["Workshop", "validate", "reduce", "load_state", "ACTORS", "KINDS", "STAGES", "STALE_AFTER", "CLOCK_AHEAD", "VERSION",
           "iso", "now", "parse"]
