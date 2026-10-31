"""German's activity record (DAR v2, spec v0.3 section 4.3): what Robert did in German on a given day.

metric ``sessions_completed``; drills and writing are items, not counted. What each source can honestly say (the B0
time-field findings, contracts/cos/v1/README.md):
- **Gespräche sessions** (``sessions/<date>_<nnn>.json``): sessions saved since B2 carry ``created_at`` with an
  offset, so they are ``second`` precision. Older ones hold only the date the container recorded, so they are ``day``
  precision on that date. Skipped sessions (``mode: skipped``) are never counted.
- **Writing** (``writing_sessions/user_<id>.json``): a real time of day, naive, written by this same process, so it is
  read in this process's own zone.
- **Drills** (the phrasebook): only the latest practice per phrase is kept, so a drill is reported as practised on its
  ``last_practiced`` day, never counted per attempt.

Only the owner's records are read (``MINIMOI_ACTIVITY_OWNER_ID``; German serves several learners).

    python -m domains.german.activity --final [--date YYYY-MM-DD]    the batch-window pass (yesterday by default)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from core.activity import config
from core.activity.writer import TZ, local_moment, neighbour_dates, today, yesterday

DOMAIN, METRIC, SERVICE = "german", "sessions_completed", "minimoi-german"


class SourceUnreadable(Exception):
    pass


def default_state_dir(environ=os.environ) -> Path:
    """german_domain.GERMAN_STATE_DIR, from the same setting, without importing the domain module."""
    return Path(environ.get("GERMAN_STATE_DIR") or Path(__file__).parent / "data")


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return data if isinstance(data, dict) else None


def _in_day(moment, local_date: str) -> bool:
    return moment is not None and moment.astimezone(ZoneInfo(TZ)).date().isoformat() == local_date


def observe(local_date: str, *, state_dir: Path, owner: int) -> tuple[list[dict], int, bool]:
    """(items, sessions counted, whether every source file was read). Raises SourceUnreadable when the sources
    themselves can't be listed."""
    items, sessions, whole = [], 0, True
    # A session file is named by the date its container recorded, which may be UTC: look one day either side.
    near = neighbour_dates(local_date)
    try:
        paths = sorted(p for d in near for p in (state_dir / "sessions").glob(f"{d}_*.json"))
    except OSError as exc:
        raise SourceUnreadable from exc
    for path in paths:
        try:
            data = _read(path)
        except (OSError, ValueError):
            whole = False
            continue
        if not data or data.get("user_id") != owner or data.get("mode") == "skipped" or data.get("skipped"):
            continue
        moment = local_moment(data.get("created_at"))
        if moment is not None:
            if not _in_day(moment, local_date):
                continue
            when = {"time_precision": "second", "event_time": moment.isoformat(timespec="seconds")}
        elif data.get("date") == local_date:
            when = {"time_precision": "day", "event_date": local_date}
        else:
            continue
        label = data.get("scenario") or data.get("persona") or "session"
        items.append({"event_id": config.safe_id("german-sess", path.stem), "kind": "session",
                      "one_line": config.one_line(f"Gespräche · {label}"),
                      "ref": {"type": "german.session", "id": config.safe_id(path.stem), "revision": 1}, **when})
        sessions += 1
    try:
        writing = _read(state_dir / "writing_sessions" / f"user_{owner}.json") or {}
    except (OSError, ValueError):
        writing, whole = {}, False
    for entry in writing.get("entries", []):
        moment = local_moment(entry.get("timestamp"))
        if entry.get("user_id") not in (owner, None) or not _in_day(moment, local_date):
            continue
        items.append({"event_id": config.safe_id("german-writing", entry.get("id")), "kind": "writing",
                      "time_precision": "second", "event_time": moment.isoformat(timespec="seconds"),
                      "one_line": config.one_line(f"Schreiben · {entry.get('mode') or 'writing'}"),
                      "ref": {"type": "german.writing", "id": config.safe_id(entry.get("id"))}})
    try:
        phrasebook = _read(state_dir / "config" / "phrasebook.json") or {}
    except (OSError, ValueError):
        phrasebook, whole = {}, False
    for phrase in phrasebook.get("phrases", []):
        moment = local_moment(phrase.get("last_practiced"))
        if phrase.get("user") != str(owner) or not _in_day(moment, local_date):
            continue
        items.append({"event_id": config.safe_id("german-drill", phrase.get("id"), local_date), "kind": "drill",
                      "time_precision": "second", "event_time": moment.isoformat(timespec="seconds"),
                      "one_line": "Drill · last practised",
                      "ref": {"type": "german.phrase", "id": config.safe_id(phrase.get("id"))}})
    return items, sessions, whole


def publish(local_date: str, *, final: bool, state_dir: Path | None = None, environ=os.environ):
    """One pass. Returns the record written, or None when the writer is off or the pass failed (never raises)."""
    writer = config.writer_from_environ(domain=DOMAIN, metric=METRIC, service=SERVICE, environ=environ)
    owner = config.owner_id(environ)
    if writer is None or owner is None:
        return None
    if state_dir is None:
        state_dir = default_state_dir(environ)
    try:
        items, sessions, whole = observe(local_date, state_dir=Path(state_dir), owner=owner)
    except SourceUnreadable:
        return writer.publish_quietly(local_date, state="unreadable", error="sessions_unlistable")
    complete = final and whole
    return writer.publish_quietly(local_date, state="complete" if complete else "partial", items=items,
                                  count=sessions, final=complete,
                                  covers_to=None if final else writer.now())


def after_save() -> None:
    """Called at the end of German's own saves: today's observation so far. Never raises."""
    try:
        publish(today(), final=False)
    except Exception:                                       # noqa: BLE001 - never fail the domain's save
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish German's activity record (spec v0.3 section 4.3).")
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
