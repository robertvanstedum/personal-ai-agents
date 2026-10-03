"""Memory-copy status and the Agents light (agent-memory v0.4 §6, §7).

``_status.json`` holds codes, times and counts ONLY: never a file name or any
content. ``memory_copy_state`` is a pure function; the portal's status
endpoint (built by the coordinator) feeds it the per-source status dicts.

Per enabled source (§7):

* no answer, or a data time in the future: unknown
* data age <= 36 h green, 36-72 h yellow, > 72 h red
* enabled, never succeeded, past 36 h since first seen: red
* the last run incomplete or failed: at least yellow

The Mac source's data time is the copy manifest's ``copied_at``, so a dead Mac
job shows as stale even though staging itself keeps running.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .fsio import write_atomic
from .headroom import check_headroom
from .snapshot import iso_utc

GREEN, YELLOW, RED, UNKNOWN = "green", "yellow", "red", "unknown"
GREEN_MAX_H, YELLOW_MAX_H = 36.0, 72.0
REASON_LIMIT = 40            # the Guild light's reason line limit
STATUS_FILE = "_status.json"
STATUS_MIN_FREE = 1024 * 1024   # a status file is tiny; see update_status
_SEVERITY = {GREEN: 0, UNKNOWN: 1, YELLOW: 2, RED: 3}

__all__ = ["check_headroom", "load_sources_status", "memory_copy_state", "memory_copy_states",
           "read_status", "update_status"]


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _fit(text: str) -> str:
    return text if len(text) <= REASON_LIMIT else text[:REASON_LIMIT - 1] + "…"


def _state_of(now: datetime, name: str, status: Mapping[str, Any] | None) -> tuple[str, str]:
    label = f"{name} memory copy"
    if not isinstance(status, Mapping):
        return UNKNOWN, _fit(f"{label} no answer")
    failed = status.get("last_ok") is False
    fresh = _parse(status.get("data_time"))
    if fresh is None:                                   # never succeeded
        since = _parse(status.get("first_seen_at")) or _parse(status.get("last_run_at"))
        if since is None or since > now:
            return UNKNOWN, _fit(f"{label} not yet run")
        if (now - since).total_seconds() / 3600 > GREEN_MAX_H:
            return RED, _fit(f"{label} never succeeded")
        return (YELLOW, _fit(f"{label} incomplete")) if failed else (UNKNOWN, _fit(f"{label} not yet run"))
    if fresh > now:
        return UNKNOWN, _fit(f"{label} time in future")
    hours = (now - fresh).total_seconds() / 3600
    if hours > YELLOW_MAX_H:
        state = RED
    elif hours > GREEN_MAX_H:
        state = YELLOW
    else:
        state = GREEN
    if state == GREEN and failed:
        return YELLOW, _fit(f"{label} incomplete")
    return state, _fit(f"{label} {int(hours)} h old")


def memory_copy_states(now: datetime, sources_status: Mapping[str, Mapping[str, Any] | None] | None
                       ) -> dict[str, dict[str, str]]:
    """{source: {state, reason}} for each enabled source (the Systems detail rows)."""
    if not isinstance(sources_status, Mapping):
        return {}
    out: dict[str, dict[str, str]] = {}
    for name, status in sources_status.items():
        if isinstance(status, Mapping) and status.get("enabled") is False:
            continue
        state, reason = _state_of(now, str(name), status)
        out[str(name)] = {"state": state, "reason": reason}
    return out


def memory_copy_state(now: datetime, sources_status: Mapping[str, Mapping[str, Any] | None] | None
                      ) -> dict[str, str]:
    """The Agents light: the worst source wins, and its reason names that source."""
    rows = memory_copy_states(now, sources_status)
    if not rows:
        return {"state": UNKNOWN, "reason": "memory copy no answer"}
    worst = max(rows.items(), key=lambda kv: _SEVERITY[kv[1]["state"]])
    return {"state": worst[1]["state"], "reason": worst[1]["reason"]}


def read_status(source_dir: Path) -> dict | None:
    try:
        doc = json.loads((source_dir / STATUS_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def load_sources_status(root: Path, names: list[str]) -> dict[str, dict | None]:
    """What the scheduler's status endpoint serves: each source's status, ``None`` if unreadable."""
    return {name: read_status(root / name) for name in names}


def update_status(source_dir: Path, name: str, now: datetime, *, ok: bool, code: str | None,
                  complete: bool, data_time: datetime | None = None,
                  counts: dict | None = None, snapshot: str | None = None) -> bool:
    """Record one run. Codes, times and counts only. Never raises; returns whether it was written.

    This is the one write that does not use the full headroom rule: a status
    file is tiny and must be able to say ``disk_low``. It still requires 1 MB free.
    """
    try:
        if check_headroom(source_dir, STATUS_MIN_FREE, None):
            return False
        previous = read_status(source_dir) or {}
        doc = {
            "schema_version": 1, "source": name,
            "first_seen_at": previous.get("first_seen_at") or iso_utc(now),
            "last_run_at": iso_utc(now), "last_ok": ok, "last_code": code, "last_complete": complete,
            "last_success_at": iso_utc(now) if ok else previous.get("last_success_at"),
            "data_time": iso_utc(data_time or now) if ok else previous.get("data_time"),
            "last_snapshot": snapshot or previous.get("last_snapshot"),
            "counts": counts if ok and counts is not None else previous.get("counts", {}),
        }
        write_atomic(source_dir / STATUS_FILE, (json.dumps(doc, indent=2) + "\n").encode("utf-8"))
        return True
    except Exception:  # noqa: BLE001 - a status failure must never surface or carry text
        return False
