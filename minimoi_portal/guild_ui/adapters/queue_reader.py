"""Build Queue reads, through the queue store (domains/guild/queue_store.py).

The reader validates the file itself and never returns a false zero: a
missing, unreadable or malformed file makes the whole queue *unknown*. A row
with an unrecognised status, or with a field of the wrong type (an integer
``last_transition_at``, a list for ``spec_title``), is kept, marked unknown
and excluded from every count (review F2); its display fields are always
text, so one bad row never breaks a sort or a template. Every row carries
its ``item_digest``, which a Save sends back as the per-item unchanged-check
(spec §6.2).

The journal's open Checks are a read of their own: an unreadable journal is
*unknown*, never "no Checks" (review F3).
"""
from __future__ import annotations

from domains.guild import queue_store as qs

from .contract import INVALID, READ_FAILED, SourceResult, live_ok, live_unknown
from .spec_author import spec_author

STATUSES = qs.STATUSES
ACTIVE = ("spec_ready", "in_build")
TROUBLE = qs.TROUBLE            # blocked or rework (Guild 1.1 slice 2, spec §4.2)
_NONE = type(None)
# The type each display field may have on disk; anything else makes the row unknown.
FIELD_TYPES = {
    "spec_title": (str, _NONE),
    "summary": (str, _NONE),
    "spec_file": (str, _NONE),
    "last_transition_at": (str, _NONE),
    "blocked_reason": (str, _NONE),
    "github_issue": (str, int, _NONE),
    # Guild 1.1 slice 2 (spec §4.1-4.3): additive fields.
    "priority": (str, _NONE),
    "notes": (str, _NONE),
    "trouble_reason": (str, _NONE),
    "trouble_from": (str, _NONE),
    "trouble_since": (str, _NONE),
    "owner_rank": (int, _NONE),
}
JOURNAL_EVIDENCE = f"{qs.JOURNAL_NAME} (queue store, read)"


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
    return f"{n} unknown row{'s' if n != 1 else ''} (status or fields unreadable)"


def field_problems(item: dict) -> list[str]:
    """Display fields whose on-disk type is wrong (bool is never a number here)."""
    bad = [name for name, allowed in FIELD_TYPES.items()
           if isinstance(item.get(name), bool) or not isinstance(item.get(name), allowed)]
    if "owner_rank" not in bad and item.get("owner_rank") is not None and not qs.valid_rank(item.get("owner_rank")):
        bad.append("owner_rank")                      # a rank is 1, 2 or 3, or absent
    return bad


def _text(value, *, allow_int: bool = False) -> str:
    """The value as display text; a value of the wrong type is shown as empty."""
    if allow_int and isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return value if isinstance(value, str) else ""


def normalize(item: dict) -> dict:
    raw = item.get("status")
    bad = field_problems(item)
    status_ok = isinstance(raw, str) and raw in STATUSES
    known = status_ok and not bad
    if bad:
        label = f"unknown row (bad {', '.join(bad)})"
    elif status_ok:
        label = raw.replace("_", " ")
    else:
        label = _status_label(raw)
    return {
        "id": item["id"],
        "status": raw if known else None,
        "status_known": known,
        "raw_status": raw if isinstance(raw, str) else None,
        "status_label": label,
        "field_problems": bad,
        "title": _text(item.get("spec_title")) or _text(item.get("spec_file")) or "(untitled)",
        "summary": _text(item.get("summary")),
        "spec_file": _text(item.get("spec_file")),
        "github_issue": _text(item.get("github_issue"), allow_int=True),
        "last_transition_at": _text(item.get("last_transition_at")),
        "blocked_reason": _text(item.get("blocked_reason")),
        "priority": _text(item.get("priority")),
        "notes": _text(item.get("notes")),
        "owner_rank": item.get("owner_rank") if qs.valid_rank(item.get("owner_rank")) else None,
        "spec_author": spec_author(item.get("spec_file")),       # read time, never stored; blank when absent
        "trouble": known and raw in TROUBLE,
        "trouble_reason": _text(item.get("trouble_reason")) or (
            _text(item.get("blocked_reason")) if known and raw == "blocked" else ""),
        "trouble_from": _text(item.get("trouble_from")),
        "trouble_since": _text(item.get("trouble_since")),
        "item_digest": qs.item_digest(item),
    }


def by_recent(row: dict) -> str:
    """Sort key for "most recent first": always text, so a sort never raises."""
    return _text(row.get("last_transition_at"))


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
        res.rank_digest = qs.rank_digest(raw)
        res.note = unknown_status_note(sum(1 for i in res.data if not i["status_known"]))
        return res

    def list_items(self, statuses=None) -> SourceResult:
        res = self._all()
        if not res.ok:
            return res
        items = [i for i in res.data if statuses is None or (i["status_known"] and i["status"] in statuses)]
        out = SourceResult(**{**res.to_dict(), "data": items})
        out.rank_digest = res.rank_digest
        return out

    def journal(self, item_id: int) -> SourceResult:
        """The item's completed changes from the file journal (spec §4.4): the
        real principal. An unreadable journal is unknown, never "no history"."""
        try:
            found = self.store.item_journal(item_id)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return live_unknown(READ_FAILED, f"journal unreadable ({type(exc).__name__})", JOURNAL_EVIDENCE)
        return live_ok(found, JOURNAL_EVIDENCE)

    def get_item(self, item_id: int) -> SourceResult:
        res = self._all()
        if not res.ok:
            return res
        found = next((i for i in res.data if i["id"] == item_id), None)
        return SourceResult(**{**res.to_dict(), "data": found})

    def checks(self) -> SourceResult:
        """Saves left uncertain and not yet marked checked (spec §6.3).

        An unreadable journal is unknown, never an empty list: an uncertain
        Save may be waiting in it (review F3)."""
        try:
            found = self.store.unresolved_checks()
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return live_unknown(READ_FAILED, f"journal unreadable ({type(exc).__name__})", JOURNAL_EVIDENCE)
        return live_ok(list(found), JOURNAL_EVIDENCE)
