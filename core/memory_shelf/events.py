"""Append-only events with a closed vocabulary (v0.5 B2; amendment R2).

Who made a record is a fact (``chair``, ``source``); what has been done with it
is a fact (``events``); how much weight it carries is derived at read time
(``weight.py``). Events are appended, never edited or removed.

**Approval cannot come from text.** ``approved-direct`` and
``approved-under-mandate`` are accepted only with an ``OwnerAuthority`` token,
which only the owner's authenticated entry points create (``moi approve``, a
Guild owner control). A transcript, a summary, an agent's note or an imported
file saying "approved" has no way to build one; the normalizer and every
parser in this package never import it (a test checks that).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

KINDS = frozenset({
    "proposed", "approved-direct", "approved-under-mandate", "verified", "rejected",
    "superseded", "withdrawn", "designated-curated", "edition-added",
})
APPROVALS = frozenset({"approved-direct", "approved-under-mandate"})
# Which extra key each kind may carry (besides at, kind, by).
_EXTRA = {
    "proposed": {"via", "note"}, "approved-direct": {"via", "note"},
    "approved-under-mandate": {"via", "note"}, "verified": {"via", "note"},
    "rejected": {"via", "note"}, "superseded": {"successor", "note"},
    "withdrawn": {"note"}, "designated-curated": {"via", "note"},
    "edition-added": {"edition", "source_hash", "note"},
}
_RECORD_ID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
OWNER_ENTRY_POINTS = frozenset({"moi-approve", "guild-owner-control"})


class EventRefused(ValueError):
    """An event that breaks the contract. The message never quotes the event text."""


@dataclass(frozen=True)
class OwnerAuthority:
    """Proof that the owner took an authenticated action. Not creatable from text."""
    entry_point: str
    owner: str = "robert"

    def __post_init__(self):
        if self.entry_point not in OWNER_ENTRY_POINTS:
            raise EventRefused("unknown owner entry point")


def stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_event(kind: str, by: str, *, now: datetime | None = None,
               authority: OwnerAuthority | None = None, **extra) -> dict:
    """Build one valid event or raise ``EventRefused``."""
    if kind not in KINDS:
        raise EventRefused("kind is not in the closed vocabulary")
    if kind in APPROVALS:
        if authority is None or by != authority.owner:
            raise EventRefused("an approval needs the owner's authenticated action")
        extra.setdefault("via", authority.entry_point)
        if kind == "approved-under-mandate":
            mandate = extra.pop("mandate", None)
            if not mandate or not _RECORD_ID.match(str(mandate)):
                raise EventRefused("approved-under-mandate names the mandate record")
            extra["via"] = f"mandate:{mandate}"
    unknown = set(extra) - _EXTRA[kind]
    if unknown:
        raise EventRefused("an event key is not allowed for this kind")
    if kind == "superseded" and not _RECORD_ID.match(str(extra.get("successor", ""))):
        raise EventRefused("superseded names its successor record")
    if kind == "edition-added" and not (isinstance(extra.get("edition"), int) and extra.get("source_hash")):
        raise EventRefused("edition-added names the edition and its source hash")
    if not isinstance(by, str) or not by.strip():
        raise EventRefused("an event says who did it")
    event = {"at": stamp(now), "kind": kind, "by": by.strip()}
    event.update({k: v for k, v in extra.items() if v is not None})
    return event


def check_events(events: object) -> list[dict]:
    """Validate a stored events list (shape only; authority was checked when written)."""
    if not isinstance(events, list):
        raise EventRefused("events must be a list")
    last = ""
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in KINDS:
            raise EventRefused("an event is outside the closed vocabulary")
        at = event.get("at")
        if not isinstance(at, str) or not _STAMP.match(at) or at < last:
            raise EventRefused("events are dated and in order")
        last = at
    return events


__all__ = ["KINDS", "APPROVALS", "OwnerAuthority", "EventRefused", "make_event", "check_events", "stamp"]
