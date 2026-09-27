"""Build Queue reads, through the queue store (domains/guild/queue_store.py).

The reader validates the file itself and never returns a false zero: a
missing, unreadable or malformed file makes the whole queue *unknown*. A row
with an unrecognised status is kept, marked, and excluded from active
counts. Every row carries its ``item_digest``, which a Save sends back as the
per-item unchanged-check (spec §6.2).
"""
from __future__ import annotations

from domains.guild import queue_store as qs

from .contract import INVALID, READ_FAILED, SourceResult, live_ok, live_unknown

STATUSES = qs.STATUSES
ACTIVE = ("spec_ready", "in_build")


def _validate(items) -> str | None:
    if not isinstance(items, list):
        return "expected a JSON list of items"
    for n, item in enumerate(items):
        if not isinstance(item, dict):
            return f"item {n} is not an object"
        if not isinstance(item.get("id"), int) or isinstance(item.get("id"), bool):
            return f"item {n} has no integer id"
    return None


def _status_label(raw) -> str:
    if raw is None:
        return "unknown status (missing)"
    if not isinstance(raw, str):
        return "unknown status (not text)"
    return f"unknown status '{raw}'"


def unknown_status_note(n: int) -> str | None:
    if not n:
        return None
    return f"{n} row{'s' if n != 1 else ''} with unknown status"


def normalize(item: dict) -> dict:
    raw = item.get("status")
    known = isinstance(raw, str) and raw in STATUSES
    return {
        "id": item["id"],
        "status": raw if known else None,
        "status_known": known,
        "raw_status": raw if isinstance(raw, str) else None,
        "status_label": raw.replace("_", " ") if known else _status_label(raw),
        "title": item.get("spec_title") or item.get("spec_file") or "(untitled)",
        "summary": item.get("summary") or "",
        "spec_file": item.get("spec_file") or "",
        "github_issue": item.get("github_issue") or "",
        "last_transition_at": item.get("last_transition_at") or "",
        "blocked_reason": item.get("blocked_reason") or "",
        "item_digest": qs.item_digest(item),
    }


class LiveBuildQueue:
    """list_items(statuses) / get_item(id) -> SourceResult; the store does the I/O."""

    def __init__(self, store: "qs.QueueStore"):
        self.store = store

    @property
    def path(self):
        return self.store.path

    def _evidence(self) -> str:
        name = (self.store.path or "the queue file").rsplit("/", 1)[-1]
        return f"{name} (queue store, read)"

    def _all(self) -> SourceResult:
        evidence = self._evidence()
        try:
            raw = self.store.read_items()
        except qs.QueueUnavailable as exc:
            text = str(exc)
            reason = INVALID if "not valid JSON" in text or "not a list" in text else READ_FAILED
            return live_unknown(reason, text, evidence)
        except (OSError, UnicodeDecodeError) as exc:
            return live_unknown(READ_FAILED, f"queue unreadable: {type(exc).__name__}", evidence)
        problem = _validate(raw)
        if problem:
            return live_unknown(INVALID, problem, evidence)
        res = live_ok([normalize(i) for i in raw], evidence, fresh_for_s=300)
        res.note = unknown_status_note(sum(1 for i in res.data if not i["status_known"]))
        return res

    def list_items(self, statuses=None) -> SourceResult:
        res = self._all()
        if not res.ok:
            return res
        items = [i for i in res.data if statuses is None or (i["status_known"] and i["status"] in statuses)]
        return SourceResult(**{**res.to_dict(), "data": items})

    def get_item(self, item_id: int) -> SourceResult:
        res = self._all()
        if not res.ok:
            return res
        found = next((i for i in res.data if i["id"] == item_id), None)
        return SourceResult(**{**res.to_dict(), "data": found})

    def checks(self) -> list[dict]:
        """Saves left uncertain and not yet marked checked (spec §6.3)."""
        try:
            return self.store.unresolved_checks()
        except OSError:
            return []
