"""The domain activity record writer (DAR v2; spec v0.3 sections 4.3 and 4.4; contracts/cos/v1/activity-record.schema.json).

One daily observation per domain, written by the domain's own service and nobody else, at
``<activity dir>/<local_date>.<part>.json``. It is an index into the domain's knowledge, never the knowledge itself.

The rules this writer keeps:
- **seq** is allocated under an exclusive lock on a sidecar ``.lock`` file and only ever grows for a key.
  **generation** is fresh each time the process starts.
- **complete** replaces the last-known items. An item published earlier and absent from a complete read is listed in
  ``withdrawn``: the writer read the whole interval, so that is an explicit withdrawal, never a silent omission.
- **partial** adds or updates items by event id and never retracts.
- **unreadable** is published only when nothing readable exists yet for that day. Over a readable record it changes
  only ``health.last_error``, so the reader keeps the last-known items and sees from ``source_checked_at`` and the
  error that they are stale.
- Over 50 items, the newest 50 are kept, and ``items_total`` and ``items_truncated`` say so.
- The **digest** is SHA-256 over canonical JSON without ``seq``, ``digest`` and ``published_at``.
- **Never blocking:** ``publish_quietly`` logs a failure and returns None. The domain's own save never fails because
  of this record; the next pass retries.

Files are 0640 and folders 0750, keeping the deploy's setgid group (core/durable_files.py), so the CoS backend reads.
"""
from __future__ import annotations

import fcntl
import logging
import os
import secrets
import socket
import threading
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from core import durable_files
from core.canonical_json import digest

TZ = "America/Chicago"
MAX_ITEMS = 50
_UNHASHED = ("seq", "digest", "published_at")
_log = logging.getLogger("activity")


def record_digest(record: dict) -> str:
    return digest({k: v for k, v in record.items() if k not in _UNHASHED})


def day_bounds(local_date: str, tz: str = TZ) -> tuple[datetime, datetime]:
    zone = ZoneInfo(tz)
    day = date.fromisoformat(local_date)
    return (datetime.combine(day, time(0, 0, 0), zone), datetime.combine(day, time(23, 59, 59), zone))


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def local_moment(value: str | None) -> datetime | None:
    """A source timestamp as an aware datetime. A naive one is this process's local time: the writer runs in the same
    container as the code that wrote it, so the zone is known, not guessed."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.astimezone()


def producer(service: str) -> dict:
    out = {"host": (os.environ.get("MINIMOI_HOST_NAME") or socket.gethostname() or "unknown")[:128],
           "service": service}
    tag = os.environ.get("MINIMOI_IMAGE_TAG", "")
    if tag:
        out["image_tag"] = tag[:128]
    commit = os.environ.get("MINIMOI_COMMIT", "").lower()
    if 7 <= len(commit) <= 40 and all(c in "0123456789abcdef" for c in commit):
        out["commit"] = commit
    return out


def _newest_first(item: dict) -> str:
    return item.get("event_time") or (item.get("event_date", "") + "T23:59:59")


class ActivityWriter:
    """One per domain part, in that domain's service process."""

    def __init__(self, folder: Path | str, *, domain: str, metric: str, environment: str, service: str,
                 part: str = "main", tz: str = TZ, clock: Callable[[], datetime] | None = None):
        if environment not in ("dev", "staging", "prod"):
            raise ValueError("environment must be dev, staging or prod")
        self.folder = Path(folder)
        self.domain, self.metric, self.part, self.tz = domain, metric, part, tz
        self.environment = environment
        self.service = service
        self.generation = secrets.token_hex(8)
        self._clock = clock or (lambda: datetime.now(ZoneInfo(tz)))
        self._thread_lock = threading.Lock()

    def now(self) -> datetime:
        return self._clock()

    def path(self, local_date: str) -> Path:
        return self.folder / f"{local_date}.{self.part}.json"

    def publish(self, local_date: str, *, state: str, items: Iterable[dict] = (), count: int | None = None,
                covers_to: datetime | None = None, final: bool = False, detail: dict | None = None,
                withdrawn: Iterable[str] = (), error: str | None = None) -> dict:
        """Write one observation of ``local_date``; returns the record now on disk."""
        if state not in ("complete", "partial", "unreadable"):
            raise ValueError("state must be complete, partial or unreadable")
        start, end = day_bounds(local_date, self.tz)
        now = self._clock()
        items = [dict(i) for i in items]
        with self._thread_lock, self._file_lock():
            path = self.path(local_date)
            previous = durable_files.read_doc(path)
            seq = int(previous["seq"]) + 1 if previous else 1
            if state == "unreadable" and previous and previous["observation"]["state"] != "unreadable":
                record = dict(previous, health={"writer_last_ok": previous["health"]["writer_last_ok"],
                                                "last_error": error or "source_unreadable"})
                return self._write(path, record, seq, now)
            gone = set(withdrawn) | set(previous["withdrawn"] if previous else [])
            if state == "partial" and previous:
                by_id = {i["event_id"]: i for i in previous["items"]}
                by_id.update({i["event_id"]: i for i in items})
                items = list(by_id.values())
            elif state == "complete" and previous:
                seen = {i["event_id"] for i in items}
                gone |= {i["event_id"] for i in previous["items"] if i["event_id"] not in seen}
            items = [i for i in items if i["event_id"] not in gone]
            items.sort(key=_newest_first, reverse=True)
            total = len(items)
            record = {
                "schema": "minimoi.activity.v2", "data_scope": "personal", "environment": self.environment,
                "domain": self.domain, "part": self.part, "local_date": local_date, "tz": self.tz,
                "generation": self.generation, "producer": producer(self.service), "metric": self.metric,
                "observation": {"state": state,
                                "covers": {"from": iso(start), "to": iso(min(covers_to or end, end))},
                                "source_checked_at": iso(now),
                                "final": bool(final) and state == "complete"},
                "count": None if state == "unreadable" else (total if count is None else count),
                "items": [] if state == "unreadable" else items[:MAX_ITEMS],
                "items_total": None if state == "unreadable" else total,
                "items_truncated": state != "unreadable" and total > MAX_ITEMS,
                "withdrawn": sorted(gone),
                "health": {"writer_last_ok": None if state == "unreadable" else iso(now),
                           "last_error": (error or "source_unreadable") if state == "unreadable" else None},
            }
            if detail is not None:
                record["detail"] = detail
            return self._write(path, record, seq, now)

    def publish_quietly(self, local_date: str, **kwargs) -> dict | None:
        """publish(), but a failure is logged and swallowed: it never fails the domain's own save."""
        try:
            return self.publish(local_date, **kwargs)
        except Exception:                                   # noqa: BLE001 - by design, see the module docstring
            _log.warning("activity %s/%s %s not published; the next pass retries", self.domain, self.part,
                         local_date, exc_info=True)
            return None

    def _write(self, path: Path, record: dict, seq: int, now: datetime) -> dict:
        record = dict(record, seq=seq, published_at=iso(now))
        record["digest"] = record_digest(record)
        durable_files.write_doc(path, record)
        return record

    @contextmanager
    def _file_lock(self):
        durable_files.ensure_dir(self.folder)
        fd = os.open(self.folder / ".lock", os.O_RDWR | os.O_CREAT, durable_files.FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def today(tz: str = TZ) -> str:
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def neighbour_dates(local_date: str) -> list[str]:
    """The day before, the day, and the day after: for sources that file by a date in another zone."""
    day = date.fromisoformat(local_date)
    return [date.fromordinal(day.toordinal() + d).isoformat() for d in (-1, 0, 1)]


def yesterday(tz: str = TZ) -> str:
    return date.fromordinal(datetime.now(ZoneInfo(tz)).date().toordinal() - 1).isoformat()
