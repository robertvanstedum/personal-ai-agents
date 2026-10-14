"""When a job is next due, from its registry schedule. Pure functions; the clock is always passed in."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def next_fire(schedule: dict, after: datetime) -> datetime:
    """The first scheduled start strictly after ``after`` (UTC). daily = HH:MM in a named zone; interval = every_s."""
    after = after.astimezone(timezone.utc)
    if schedule.get("kind") == "interval":
        return after + timedelta(seconds=int(schedule["every_s"]))
    if schedule.get("kind") != "daily":
        raise ValueError("unknown schedule kind")
    zone = ZoneInfo(schedule["tz"])
    hour, minute = (int(x) for x in schedule["at"].split(":"))
    local = after.astimezone(zone)
    day = local.date()
    for _ in range(3):
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        if candidate.astimezone(timezone.utc) > after:
            return candidate.astimezone(timezone.utc)
        day += timedelta(days=1)
    raise ValueError("no next time found")
