"""Portuguese's activity record (DAR v2, spec v0.3 section 4.3), built with German's under the parity rule.

metric ``sessions_completed``. The sources:
- **Conversas sessions** (``conversas_sessions/user_<id>.json``): each has ``created_at``, a naive time written by
  this same process, so it is read in this process's own zone, at ``second`` precision. The Postgres
  ``portuguese.sessions`` rows are a projection of these saves (and pre-migration history), so they aren't read again.
- **Writing** (``writing_sessions/user_<id>.json``): ``created_at`` or ``timestamp``, the same way. The legacy flat
  ``writing_sessions.json`` names no owner, so it is left out rather than assumed to be Robert's.
- **Drills:** Portuguese doesn't record drill results today (``/api/pt/drill-result`` keeps nothing), so there is
  nothing honest to report. That's a parity gap with German, for Portuguese's own work.

Only the owner's records are read (``MINIMOI_ACTIVITY_OWNER_ID``).

    python -m domains.portuguese.activity --final [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from core.activity import config
from core.activity.writer import TZ, local_moment, today, yesterday

DOMAIN, METRIC, SERVICE = "portuguese", "sessions_completed", "minimoi-portuguese"


def default_data_dir(environ=os.environ) -> Path:
    """The folder html_server.py uses (_PT_DATA_DIR), from the same setting. Not imported: the server runs as a
    script, and importing it would load a second copy of the app."""
    return Path(environ.get("PORTUGUESE_DATA_DIR") or Path(__file__).parent / "data").expanduser()


def _entries(path: Path, key: str) -> tuple[list[dict], bool]:
    """(rows, readable). A missing file is no activity; an unreadable one makes the observation partial."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], True
    except (OSError, ValueError):
        return [], False
    rows = data.get(key) if isinstance(data, dict) else None
    return (rows if isinstance(rows, list) else []), True


def _in_day(moment, local_date: str) -> bool:
    return moment is not None and moment.astimezone(ZoneInfo(TZ)).date().isoformat() == local_date


def observe(local_date: str, *, data_dir: Path, owner: int) -> tuple[list[dict], int, bool]:
    items, sessions = [], 0
    rows, sessions_ok = _entries(data_dir / "conversas_sessions" / f"user_{owner}.json", "sessions")
    for row in rows:
        moment = local_moment(row.get("created_at"))
        if row.get("user_id") != owner or not _in_day(moment, local_date):
            continue
        label = row.get("scenario") or row.get("persona") or "sessão"
        items.append({"event_id": config.safe_id("portuguese-sess", row.get("id")), "kind": "session",
                      "time_precision": "second", "event_time": moment.isoformat(timespec="seconds"),
                      "one_line": config.one_line(f"Conversas · {label}"),
                      "ref": {"type": "portuguese.session", "id": config.safe_id(row.get("id")), "revision": 1}})
        sessions += 1
    rows, writing_ok = _entries(data_dir / "writing_sessions" / f"user_{owner}.json", "entries")
    for row in rows:
        moment = local_moment(row.get("created_at") or row.get("timestamp"))
        if row.get("user_id") != owner or not _in_day(moment, local_date):
            continue
        items.append({"event_id": config.safe_id("portuguese-writing", row.get("id")), "kind": "writing",
                      "time_precision": "second", "event_time": moment.isoformat(timespec="seconds"),
                      "one_line": config.one_line(f"Escrita · {row.get('mode') or 'writing'}"),
                      "ref": {"type": "portuguese.writing", "id": config.safe_id(row.get("id"))}})
    return items, sessions, sessions_ok and writing_ok


def publish(local_date: str, *, final: bool, data_dir: Path | None = None, environ=os.environ):
    """One pass. Returns the record written, or None when the writer is off or the pass failed (never raises)."""
    writer = config.writer_from_environ(domain=DOMAIN, metric=METRIC, service=SERVICE, environ=environ)
    owner = config.owner_id(environ)
    if writer is None or owner is None:
        return None
    if data_dir is None:
        data_dir = default_data_dir(environ)
    if not Path(data_dir).is_dir():
        return writer.publish_quietly(local_date, state="unreadable", error="data_dir_missing")
    items, sessions, whole = observe(local_date, data_dir=Path(data_dir), owner=owner)
    complete = final and whole
    return writer.publish_quietly(local_date, state="complete" if complete else "partial", items=items,
                                  count=sessions, final=complete, covers_to=None if final else writer.now())


def after_save() -> None:
    """Called at the end of Portuguese's own saves: today's observation so far. Never raises."""
    try:
        publish(today(), final=False)
    except Exception:                                       # noqa: BLE001 - never fail the domain's save
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish Portuguese's activity record (spec v0.3 section 4.3).")
    parser.add_argument("--final", action="store_true", help="the batch-window pass: the whole day, final")
    parser.add_argument("--date", help="the local date (America/Chicago); default yesterday with --final, else today")
    args = parser.parse_args(argv)
    local_date = args.date or (yesterday() if args.final else today())
    record = publish(local_date, final=args.final)
    print(json.dumps({"local_date": local_date, "published": record is not None,
                      "state": record["observation"]["state"] if record else None,
                      "count": record["count"] if record else None}))
    return 0 if record is not None else 1


if __name__ == "__main__":
    sys.exit(main())
