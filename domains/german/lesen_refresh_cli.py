#!/usr/bin/env python3
"""Scheduled Lesen refresh command packaged inside the German image."""

from __future__ import annotations

import datetime as dt
import json
from typing import Callable
from zoneinfo import ZoneInfo

try:
    # html_server and the deployed direct command both import this module by
    # its top-level name; doing the same prevents two independent module
    # instances with different configured data paths during tests.
    import german_domain
except ImportError:
    from . import german_domain


CHICAGO = ZoneInfo("America/Chicago")


def _as_chicago(value: dt.datetime | None = None) -> dt.datetime:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CHICAGO)
    return current.astimezone(CHICAGO)


def _successful_local_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CHICAGO)
        return parsed.astimezone(CHICAGO).date()
    except (TypeError, ValueError):
        return None


def run_scheduled_refresh(
    *,
    now: dt.datetime | None = None,
    refresh_func: Callable = german_domain.refresh_lesen_feed,
) -> tuple[int, dict]:
    current = _as_chicago(now)
    if current.hour < 6 or current.hour >= 22:
        return 0, {"ok": True, "status": "outside_window", "local_time": current.isoformat()}

    try:
        data = german_domain._load_lesen_articles_strict()
    except german_domain.LesenRefreshError as exc:
        return 1, {"ok": False, "status": "invalid_store", "error": str(exc)}
    if _successful_local_date(data.get("last_fetched")) == current.date():
        return 0, {"ok": True, "status": "already_succeeded", "local_date": current.date().isoformat()}

    try:
        result = refresh_func(now=current)
    except german_domain.LesenRefreshError as exc:
        return 1, {"ok": False, "status": "refresh_error", "error": str(exc)}
    if result.get("ok"):
        return 0, {**result, "refresh_status": result.get("status"), "status": "ran"}
    return 1, result


def main() -> int:
    exit_code, result = run_scheduled_refresh()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
