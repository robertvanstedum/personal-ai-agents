"""Workshop events and the derived state (file first; never a model call).

Per workshop:  <root>/<id>/events.jsonl  append-only, one line per meaningful
moment (O_APPEND writes under 4 KB, so several local emitters never
interleave), and  <root>/<id>/state.json  derived by reduce(), written
atomically. No code, logs or secrets: credential-shaped text is refused.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

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
    def __init__(self, root: str, workshop_id: str):
        if not ID_RE.fullmatch(workshop_id or ""):
            raise ValueError("workshop id is lower-case letters, digits and -")
        self.id = workshop_id
        self.dir = os.path.join(root, workshop_id)
        self.events_path = os.path.join(self.dir, "events.jsonl")
        self.state_path = os.path.join(self.dir, "state.json")

    def _ensure(self):
        root = os.path.dirname(self.dir)
        if not os.path.isdir(root):
            os.makedirs(root, mode=0o700, exist_ok=True)     # the home too, not only the leaf, is owner-only
        os.makedirs(self.dir, mode=0o700, exist_ok=True)

    @contextmanager
    def _lock(self):
        self._ensure()
        fd = os.open(os.path.join(self.dir, ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def append(self, event: dict) -> dict:
        ev = validate({**event, "workshop": self.id})
        data = (json.dumps(ev, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        self._ensure()
        fd = os.open(self.events_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        self.write_state()
        return ev

    def events(self) -> list[dict]:
        try:
            with open(self.events_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        out = []
        for line in lines:
            try:
                ev = json.loads(line)
            except ValueError:
                continue                    # a torn last line: skipped, never fatal
            if isinstance(ev, dict) and ev.get("v") == VERSION:
                out.append(ev)
        return out

    def write_state(self) -> dict:
        """The one writer of state.json: the reducer, under the lock, atomically."""
        with self._lock():
            state = reduce(self.id, self.events())
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".state-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, sort_keys=True, indent=1)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.state_path)
        return state


def reduce(workshop_id: str, events: list[dict]) -> dict:
    """state.json from the events: in progress, next, needs you, host health."""
    by_item: dict[str, list[dict]] = {}
    for ev in sorted(events, key=lambda e: e.get("at") or ""):
        by_item.setdefault(ev["item"], []).append(ev)
    in_progress, nxt, needs, host = [], [], [], None
    for item, evs in by_item.items():
        last = evs[-1]
        if item.startswith("host:"):
            health = [e for e in evs if e.get("kind") == "health"]
            if health:
                host = health[-1]
            continue
        if last["kind"] == "next" or (last.get("stage") == "design" and not any(e["kind"] == "started" for e in evs)):
            nxt.append(_brief(last))
        elif last["kind"] != "done":
            in_progress.append(_brief(last))
        open_needs = [e for e in evs if e["kind"] == "needs_you"]
        if open_needs:
            decided_after = [e for e in evs if e["kind"] == "decision" and e["at"] >= open_needs[-1]["at"]]
            if not decided_after:
                needs.append(_brief(open_needs[-1]))
    items = {item: _brief(evs[-1]) for item, evs in by_item.items() if not item.startswith("host:")}
    return {"v": VERSION, "workshop": workshop_id, "updated_at": iso(now()), "events": len(events),
            "in_progress": in_progress, "next": nxt, "needs_you": needs, "items": items,
            "host": _host(host), "last_event": _brief(events[-1]) if events else None}


def _brief(ev: dict) -> dict:
    return {k: ev.get(k) for k in ("at", "actor", "kind", "item", "stage", "text", "next_actor", "refs", "room")}


def _host(ev: dict | None) -> dict | None:
    if not ev:
        return None
    return {"at": ev.get("at"), "text": ev.get("text"), **(ev.get("health") or {})}


def load_state(root: str | None, workshop_id: str) -> tuple[dict | None, str]:
    """(state, status): status is ok, missing, unreadable or stale. Never
    "nothing running" when unknown."""
    if not root:
        return None, "missing"
    try:
        ws = Workshop(root, workshop_id)
    except ValueError:
        return None, "missing"
    try:
        with open(ws.state_path, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError):
        return None, "unreadable"
    if not isinstance(state, dict):
        return None, "unreadable"
    host = state.get("host") if isinstance(state.get("host"), dict) else {}
    seen = parse(host.get("observed_at") or host.get("at")) if host else None
    # A reading dated more than CLOCK_AHEAD in the future is not trusted
    # either (the Mac's and the server's clocks disagree): never "fresh".
    if seen is None or now() - seen > STALE_AFTER or seen - now() > CLOCK_AHEAD:
        return state, "stale"
    return state, "ok"


__all__ = ["Workshop", "validate", "reduce", "load_state", "ACTORS", "KINDS", "STAGES", "STALE_AFTER", "CLOCK_AHEAD", "iso", "now", "parse"]
