"""Build queue adapters. The live adapter reads data/guild/build_queue.json
read-only and validates it itself; it deliberately does not reuse the
portal's _load_build_queue(), which returns [] on any error (a false zero)."""
from __future__ import annotations

import json
from pathlib import Path

from .contract import INVALID, READ_FAILED, SourceResult, live_ok, live_unknown, sample

# Same ten statuses and two active columns as minimoi_portal/app.py.
STATUSES = ("idea", "design", "backlog", "spec_ready", "in_build",
            "blocked", "deferred", "cancelled", "superseded", "done")
ACTIVE = ("spec_ready", "in_build")


def _validate(items) -> str | None:
    """Whole-file problems only: wrong top-level shape, non-object rows, rows
    without an integer id. An unrecognised or missing status is a per-row
    problem (kept, marked, excluded from active counts), not a file failure."""
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


def _normalize(item: dict, is_sample: bool) -> dict:
    raw = item.get("status")
    known = isinstance(raw, str) and raw in STATUSES
    return {
        "id": item["id"],
        "status": raw if known else None,          # never a valid status by accident
        "status_known": known,
        "raw_status": raw if isinstance(raw, str) else None,
        "status_label": raw.replace("_", " ") if known else _status_label(raw),
        "title": item.get("spec_title") or item.get("spec_file") or "(untitled)",
        "summary": item.get("summary") or "",
        "spec_file": item.get("spec_file") or "",
        "github_issue": item.get("github_issue") or "",
        "last_transition_at": item.get("last_transition_at") or "",
        "blocked_reason": item.get("blocked_reason") or "",
        "history": item.get("history") or [],
        "sample": is_sample,
    }


class BuildQueueAdapter:
    """Contract: list_items(statuses) / get_item(id) / history(id) -> SourceResult."""

    def _all(self) -> SourceResult:
        raise NotImplementedError

    def list_items(self, statuses=None) -> SourceResult:
        """statuses=None returns every row, including unknown-status rows.
        A status filter only matches known statuses, so unknown rows never
        count as active. The note always describes the whole file."""
        res = self._all()
        if not res.ok:
            return res
        items = [i for i in res.data if statuses is None or (i["status_known"] and i["status"] in statuses)]
        return SourceResult(**{**res.to_dict(), "data": items})

    @staticmethod
    def _with_note(res: SourceResult) -> SourceResult:
        res.note = unknown_status_note(sum(1 for i in res.data if not i["status_known"]))
        return res

    def get_item(self, item_id: int) -> SourceResult:
        res = self._all()
        if not res.ok:
            return res
        found = next((i for i in res.data if i["id"] == item_id), None)
        return SourceResult(**{**res.to_dict(), "data": found})

    def history(self, item_id: int) -> SourceResult:
        raise NotImplementedError


class LiveBuildQueue(BuildQueueAdapter):
    def __init__(self, path: Path):
        self.path = Path(path)

    def _evidence(self):
        return f"{self.path.name} (read-only)"

    def _all(self) -> SourceResult:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:  # read-only by construction
                raw = json.load(fh)
        except FileNotFoundError:
            return live_unknown(READ_FAILED, f"{self.path.name} not found", self._evidence())
        except (OSError, UnicodeDecodeError) as exc:
            return live_unknown(READ_FAILED, f"{self.path.name} unreadable: {type(exc).__name__}", self._evidence())
        except json.JSONDecodeError as exc:
            return live_unknown(INVALID, f"{self.path.name} is not valid JSON (line {exc.lineno})", self._evidence())
        problem = _validate(raw)
        if problem:
            return live_unknown(INVALID, f"{self.path.name}: {problem}", self._evidence())
        return self._with_note(live_ok([_normalize(i, False) for i in raw], self._evidence(), fresh_for_s=300))

    def history(self, item_id: int) -> SourceResult:
        # Real history lives in guild.design_log_transitions (DB); not read here.
        return SourceResult("not_instrumented", "unknown", None, None, None,
                            "guild.design_log_transitions (DB, not read by the prototype)",
                            "not_configured", "transition history is DB-backed; not connected")


class SampleBuildQueue(BuildQueueAdapter):
    def __init__(self, path: Path):
        self.path = Path(path)

    def _all(self) -> SourceResult:
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        items = doc["items"]
        problem = _validate(items)
        if problem:  # a broken fixture is a bug; fail loudly
            raise ValueError(f"sample queue fixture invalid: {problem}")
        return self._with_note(sample([_normalize(i, True) for i in items], f"fixtures/{self.path.name}", doc.get("observed_at")))

    def history(self, item_id: int) -> SourceResult:
        res = self.get_item(item_id)
        return sample((res.data or {}).get("history", []), f"fixtures/{self.path.name}")
