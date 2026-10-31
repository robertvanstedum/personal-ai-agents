"""Curator's activity record (DAR v2, spec v0.3 section 4.3): Robert's feedback on the briefing, by day.

metric ``feedback_actions``: likes, dislikes and saves, from ``curator_preferences.json``'s ``feedback_history``
(written by ``curator_feedback.record_feedback``). Reading itself isn't measured, so CoS may say "I haven't seen any
Curator feedback since …", never "you haven't been reading". Each entry's ``timestamp`` is a naive time written by this
same process, so it is read in this process's own zone. ``feedback_history`` is filed by that zone's date, so a day
either side is read too. Curator has one owner, so there is no owner filter.

    python -m domains.curator.activity --final [--date YYYY-MM-DD]
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

DOMAIN, METRIC, SERVICE = "curator", "feedback_actions", "minimoi-curator"
_KINDS = {"liked": ("feedback_like", "Liked"), "disliked": ("feedback_dislike", "Disliked"),
          "saved": ("feedback_save", "Saved")}


def default_preferences_file(environ=os.environ) -> Path:
    """curator_feedback.PREFERENCES_FILE, from the same setting."""
    repo = Path(__file__).resolve().parents[2]
    return Path(environ.get("CURATOR_DATA_DIR") or repo / "data" / "curator") / "curator_preferences.json"


def observe(local_date: str, *, preferences: dict) -> list[dict]:
    items = []
    history = preferences.get("feedback_history") or {}
    for day in neighbour_dates(local_date):
        for action, (kind, verb) in _KINDS.items():
            for entry in (history.get(day) or {}).get(action) or []:
                moment = local_moment(entry.get("timestamp"))
                if moment is None or moment.astimezone(ZoneInfo(TZ)).date().isoformat() != local_date:
                    continue
                article = entry.get("article_id") or entry.get("url") or "article"
                source, title = entry.get("source") or "", entry.get("title") or ""
                items.append({"event_id": config.safe_id("curator", action, article), "kind": kind,
                              "time_precision": "second", "event_time": moment.isoformat(timespec="seconds"),
                              "one_line": config.one_line(f"{verb} · {source}: {title}" if source else f"{verb} · {title}"),
                              "ref": {"type": "curator.article", "id": config.safe_id(article)}})
    return items


def publish(local_date: str, *, final: bool, preferences_file: Path | None = None, environ=os.environ):
    """One pass. Returns the record written, or None when the writer is off or the pass failed (never raises)."""
    writer = config.writer_from_environ(domain=DOMAIN, metric=METRIC, service=SERVICE, environ=environ)
    if writer is None:
        return None
    path = Path(preferences_file or default_preferences_file(environ))
    try:
        preferences = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        preferences = {}
    except (OSError, ValueError):
        return writer.publish_quietly(local_date, state="unreadable", error="preferences_unreadable")
    items = observe(local_date, preferences=preferences if isinstance(preferences, dict) else {})
    return writer.publish_quietly(local_date, state="complete" if final else "partial", items=items,
                                  count=len(items), final=final, covers_to=None if final else writer.now())


def after_save() -> None:
    """Called after a feedback action is saved: today's observation so far. Never raises."""
    try:
        publish(today(), final=False)
    except Exception:                                       # noqa: BLE001 - never fail the domain's save
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish Curator's activity record (spec v0.3 section 4.3).")
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
