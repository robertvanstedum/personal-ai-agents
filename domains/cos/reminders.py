"""File-first store and due-check delivery for explicit COS reminders.

A reminder exists only after ``add`` has written it to disk; the Confer turn
receipt is built from that record. Delivery is one single-purpose due check
(Loop R in ``chief_of_staff``), not a general scheduler.

Storage lives in ``COS_REMINDERS_DIR`` (default ``data/cos_reminders`` under
the repository root):

- ``reminders.json``: ``{"schema_version": 1, "reminders": [...]}``, replaced
  atomically (temporary file in the same directory, fsync, ``os.replace``).
- ``reminders.lock``: every read-modify-write holds an exclusive ``flock`` on
  this file, so concurrent Confer turns and due checks cannot lose updates.

Each record moves ``pending -> sending -> delivered | failed | uncertain``.
The ``sending`` mark is written before the send call, and the send itself
runs outside the lock. A crash or an ambiguous send therefore ends in
``uncertain`` and is never sent again: a missed reminder is visible in the
file, a duplicate one is not possible. Only a definite failure (Telegram did
not accept the message) returns to ``failed`` and is retried, a bounded number
of times.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import tempfile
from typing import Callable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BASE_DIR = Path(__file__).parent.parent.parent   # repo root
SCHEMA_VERSION = 1
STORE_FILE = "reminders.json"
LOCK_FILE = "reminders.lock"

MAX_TEXT_CHARS = 500
MAX_OPEN_REMINDERS = 500
PAST_TOLERANCE = timedelta(seconds=120)
MAX_LEAD_TIME = timedelta(days=366)
MAX_ATTEMPTS = 3
RETRY_AFTER = timedelta(minutes=5)
STALE_SENDING_AFTER = timedelta(minutes=10)
LATE_NOTICE_AFTER = timedelta(minutes=10)
MAX_SENDS_PER_RUN = 20
DISPLAY_TIMEZONE = "America/Chicago"

# Non-final states count against MAX_OPEN_REMINDERS.
OPEN_STATES = frozenset({"pending", "sending", "failed"})
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class ReminderRefused(ValueError):
    """Raised when a reminder is not stored; ``code`` names the reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReminderStoreError(RuntimeError):
    """Raised when the reminder file exists but is not a valid store."""


class DeliveryFailed(RuntimeError):
    """The channel definitely did not accept the message; retry is safe."""

    def __init__(self, code: str = "delivery_failed") -> None:
        super().__init__(code)
        self.code = code


class DeliveryUncertain(RuntimeError):
    """The message may have been delivered; it must never be sent again."""

    def __init__(self, code: str = "delivery_uncertain") -> None:
        super().__init__(code)
        self.code = code


def store_dir(root: str | os.PathLike | None = None) -> Path:
    """Return the reminder directory: explicit root, env override, default."""
    if root is not None:
        return Path(root)
    configured = os.environ.get("COS_REMINDERS_DIR", "").strip()
    return Path(configured) if configured else BASE_DIR / "data" / "cos_reminders"


def format_utc(moment: datetime) -> str:
    """Canonical second-precision UTC text, e.g. ``2026-10-19T14:00:00Z``."""
    return moment.astimezone(timezone.utc).strftime(_TIME_FORMAT)


def parse_utc(text: str) -> datetime:
    return datetime.strptime(text, _TIME_FORMAT).replace(tzinfo=timezone.utc)


def _now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return current.astimezone(timezone.utc)


def _ensure_dir(directory: Path) -> Path:
    if not directory.exists():
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkdir's mode is filtered by the umask; make the new leaf exact.
        os.chmod(directory, 0o700)
    return directory


@contextmanager
def _locked(directory: Path) -> Iterator[None]:
    fd = os.open(
        directory / LOCK_FILE,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC,
        0o600,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        # Closing the descriptor releases the lock.
        os.close(fd)


def _read(directory: Path) -> dict:
    try:
        raw = (directory / STORE_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"schema_version": SCHEMA_VERSION, "reminders": []}
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ReminderStoreError("reminder_store_unreadable") from exc
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != SCHEMA_VERSION
        or not isinstance(document.get("reminders"), list)
        or not all(isinstance(record, dict) for record in document["reminders"])
    ):
        raise ReminderStoreError("reminder_store_unreadable")
    return document


def _write(directory: Path, document: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".reminders.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / STORE_FILE)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    # Make the rename itself durable before a receipt is returned.
    dir_fd = os.open(directory, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _new_id(existing: set[str]) -> str:
    while True:
        reminder_id = f"rem-{secrets.token_hex(6)}"
        if reminder_id not in existing:
            return reminder_id


def add(
    due: datetime,
    text: str,
    operation_id: str,
    *,
    now: datetime | None = None,
    root: str | os.PathLike | None = None,
) -> dict:
    """Store one reminder durably and return ``{"saved", "deduplicated", "reminder"}``.

    A repeat of the same ``operation_id`` with the same due time and text is
    the same reminder (a client retry) and returns the stored record, even if
    its due time has since passed. The same ``operation_id`` with different
    content is refused rather than silently merged.
    """
    if not isinstance(due, datetime) or due.tzinfo is None or due.utcoffset() is None:
        raise ReminderRefused("reminder_due_naive")
    # Store a single inert text line, like explicit notes.
    text = " ".join(str(text).split())
    if not text:
        raise ReminderRefused("reminder_text_empty")
    if len(text) > MAX_TEXT_CHARS:
        raise ReminderRefused("reminder_text_too_long")
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise ReminderRefused("reminder_operation_id_missing")
    current = _now(now)
    due_utc = format_utc(due)
    due_at = parse_utc(due_utc)

    directory = _ensure_dir(store_dir(root))
    with _locked(directory):
        document = _read(directory)
        reminders = document["reminders"]
        for record in reminders:
            if record.get("operation_id") != operation_id:
                continue
            if record.get("due_utc") == due_utc and record.get("text") == text:
                _write(directory, document)  # Re-establish durability before confirming a replay.
                return {"saved": True, "deduplicated": True, "reminder": dict(record)}
            raise ReminderRefused("reminder_idempotency_conflict")

        if due_at < current - PAST_TOLERANCE:
            raise ReminderRefused("reminder_due_past")
        if due_at > current + MAX_LEAD_TIME:
            raise ReminderRefused("reminder_due_too_far")
        open_count = sum(1 for record in reminders if record.get("state") in OPEN_STATES)
        if open_count >= MAX_OPEN_REMINDERS:
            raise ReminderRefused("reminder_limit_reached")

        record = {
            "id": _new_id({str(item.get("id")) for item in reminders}),
            "operation_id": operation_id,
            "text": text,
            "due_utc": due_utc,
            "created_utc": format_utc(current),
            "state": "pending",
            "attempts": 0,
            "last_attempt_utc": None,
            "delivered_utc": None,
            "telegram_message_id": None,
            "error_code": None,
        }
        reminders.append(record)
        _write(directory, document)
    return {"saved": True, "deduplicated": False, "reminder": dict(record)}


def _classify(record: dict, current: datetime) -> str | None:
    """Return ``"send"``, ``"stale"`` or ``None``; raise on a malformed record."""
    state = record["state"]
    if state == "pending":
        return "send" if parse_utc(record["due_utc"]) <= current else None
    if state == "failed":
        retry_ready = current - parse_utc(record["last_attempt_utc"]) >= RETRY_AFTER
        return "send" if int(record["attempts"]) < MAX_ATTEMPTS and retry_ready else None
    if state == "sending":
        stale = current - parse_utc(record["last_attempt_utc"]) > STALE_SENDING_AFTER
        return "stale" if stale else None
    return None


def has_work(
    *,
    now: datetime | None = None,
    root: str | os.PathLike | None = None,
) -> bool:
    """Cheap read-only check so the due loop touches credentials only when needed."""
    directory = store_dir(root)
    if not (directory / STORE_FILE).exists():
        return False
    current = _now(now)
    # The file is only ever replaced atomically, so an unlocked read is whole.
    for record in _read(directory)["reminders"]:
        try:
            if _classify(record, current) is not None:
                return True
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return False


def _display_due(due_at: datetime) -> str:
    try:
        local = due_at.astimezone(ZoneInfo(DISPLAY_TIMEZONE))
        suffix = ""
    except ZoneInfoNotFoundError:
        local = due_at
        suffix = " UTC"
    return f"{local:%a} {local.day} {local:%b %H:%M}{suffix}"


def message_text(record: dict, now: datetime) -> str:
    """Plain delivery text; a late delivery says when it was due."""
    message = f"Reminder: {record['text']}"
    due_at = parse_utc(record["due_utc"])
    if now - due_at > LATE_NOTICE_AFTER:
        message += f" (was due {_display_due(due_at)})"
    return message


def deliver_due(
    send: Callable[[str], dict],
    *,
    now: datetime | None = None,
    root: str | os.PathLike | None = None,
) -> dict:
    """Send due reminders through ``send`` and record each durable outcome.

    ``send(text)`` returns ``{"ok": True, "message_id": int}`` on delivery,
    raises ``DeliveryFailed`` for a definite failure, and raises
    ``DeliveryUncertain`` when the message may have gone out. Any other result
    or exception is treated as uncertain. At most ``MAX_SENDS_PER_RUN`` sends
    happen per call; the rest wait for the next run.
    """
    current = _now(now)
    summary = {
        "due": 0,
        "delivered": 0,
        "failed": 0,
        "uncertain": 0,
        "stale_sending": 0,
        "deferred": 0,
        "invalid": 0,
    }
    directory = store_dir(root)
    if not (directory / STORE_FILE).exists():
        return summary

    # Phase 1, under the lock: settle interrupted sends and claim due work.
    batch: list[dict] = []
    with _locked(directory):
        document = _read(directory)
        candidates = []
        changed = False
        for record in document["reminders"]:
            try:
                action = _classify(record, current)
                attempts = int(record.get("attempts") or 0)
                # Read the sort key here too: one malformed record is skipped, never fatal to the run.
                due_key = format_utc(parse_utc(record["due_utc"])) if action == "send" else None
            except (KeyError, TypeError, ValueError, AttributeError):
                summary["invalid"] += 1
                continue
            if action == "stale":
                # The previous run died between claim and outcome. The message
                # may have gone out, so it is never sent again.
                record["state"] = "uncertain"
                record["error_code"] = "send_interrupted"
                summary["stale_sending"] += 1
                changed = True
            elif action == "send":
                candidates.append((due_key, attempts, record))
        candidates.sort(key=lambda item: item[0])
        for _, attempts, record in candidates:
            if len(batch) >= MAX_SENDS_PER_RUN:
                summary["deferred"] += 1
                continue
            record["state"] = "sending"
            record["attempts"] = attempts + 1
            record["last_attempt_utc"] = format_utc(current)
            batch.append(dict(record))
            changed = True
        if changed:
            _write(directory, document)
    summary["due"] = len(batch)
    if not batch:
        return summary

    # Phase 2, outside the lock: one send per claimed record.
    outcomes: dict[str, dict] = {}
    for record in batch:
        try:
            result = send(message_text(record, current))
        except DeliveryFailed as exc:
            outcomes[record["id"]] = {"state": "failed", "error_code": exc.code}
            continue
        except DeliveryUncertain as exc:
            outcomes[record["id"]] = {"state": "uncertain", "error_code": exc.code}
            continue
        except Exception:
            outcomes[record["id"]] = {"state": "uncertain", "error_code": "send_error"}
            continue
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if (
            isinstance(result, dict)
            and result.get("ok") is True
            and isinstance(message_id, int)
            and not isinstance(message_id, bool)
        ):
            outcomes[record["id"]] = {"state": "delivered", "message_id": message_id}
        else:
            outcomes[record["id"]] = {"state": "uncertain", "error_code": "send_unconfirmed"}

    # Phase 3, under the lock again: record outcomes on the claimed records.
    delivered_at = format_utc(datetime.now(timezone.utc) if now is None else current)
    with _locked(directory):
        document = _read(directory)
        for record in document["reminders"]:
            outcome = outcomes.get(record.get("id"))
            if outcome is None or record.get("state") != "sending":
                continue
            record["state"] = outcome["state"]
            if outcome["state"] == "delivered":
                record["delivered_utc"] = delivered_at
                record["telegram_message_id"] = outcome["message_id"]
                record["error_code"] = None
            else:
                record["error_code"] = outcome["error_code"]
            summary[outcome["state"]] += 1
        _write(directory, document)
    return summary


# ── Owner management: list, edit, cancel (MC 1.2 domain operations) ──────────
#
# These are the domain rules both the website and MC's action adapter call.
# They never touch a reminder that may be going out right now: a ``sending``
# record is refused (the send runs outside the lock, so a cancel or edit could
# not stop it), and ``uncertain`` stays as it is.

EDITABLE_STATES = frozenset({"pending"})
CANCELLABLE_STATES = frozenset({"pending", "failed"})


def list_reminders(
    *,
    states: frozenset[str] | set[str] | None = None,
    root: str | os.PathLike | None = None,
) -> list[dict]:
    """Copies of the stored reminders, soonest due first, optionally filtered by state.

    Read-only. An unreadable store raises ``ReminderStoreError`` rather than
    looking like "no reminders"; individual malformed records are skipped."""
    directory = store_dir(root)
    if not (directory / STORE_FILE).exists():
        return []
    found = []
    for record in _read(directory)["reminders"]:
        try:
            due_key = format_utc(parse_utc(record["due_utc"]))
            state = record["state"]
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if states is None or state in states:
            found.append((due_key, dict(record)))
    return [record for _, record in sorted(found, key=lambda item: item[0])]


def _find(document: dict, reminder_id: str) -> dict | None:
    return next((r for r in document["reminders"] if r.get("id") == reminder_id), None)


def cancel(
    reminder_id: str,
    *,
    now: datetime | None = None,
    root: str | os.PathLike | None = None,
) -> dict:
    """Cancel one reminder. Returns ``{"outcome", "reminder"}`` where outcome is
    ``cancelled`` | ``already_cancelled`` | ``already_delivered`` |
    ``in_flight`` (being sent now; not changed) | ``uncertain`` (may have been
    sent; not changed) | ``not_found``. The record is kept, marked cancelled."""
    if not isinstance(reminder_id, str) or not reminder_id.strip():
        raise ReminderRefused("reminder_id_missing")
    current = _now(now)
    directory = store_dir(root)
    if not (directory / STORE_FILE).exists():
        return {"outcome": "not_found", "reminder": None}
    with _locked(directory):
        document = _read(directory)
        record = _find(document, reminder_id)
        if record is None:
            return {"outcome": "not_found", "reminder": None}
        state = record.get("state")
        if state == "cancelled":
            return {"outcome": "already_cancelled", "reminder": dict(record)}
        if state == "delivered":
            return {"outcome": "already_delivered", "reminder": dict(record)}
        if state == "sending":
            return {"outcome": "in_flight", "reminder": dict(record)}
        if state not in CANCELLABLE_STATES:
            return {"outcome": "uncertain", "reminder": dict(record)}
        record["state"] = "cancelled"
        record["cancelled_utc"] = format_utc(current)
        _write(directory, document)
        return {"outcome": "cancelled", "reminder": dict(record)}


def edit(
    reminder_id: str,
    *,
    due: datetime | None = None,
    text: str | None = None,
    expected_due_utc: str | None = None,
    now: datetime | None = None,
    root: str | os.PathLike | None = None,
) -> dict:
    """Change a pending reminder's due time and/or text, with the same limits
    as ``add``. ``expected_due_utc`` (optional) guards against editing a
    reminder that changed since the caller read it. Returns the updated record.
    Anything not ``pending`` is refused: a failed or sending reminder may
    already be on its way."""
    if due is None and text is None:
        raise ReminderRefused("reminder_edit_empty")
    current = _now(now)
    new_text = None
    if text is not None:
        new_text = " ".join(str(text).split())
        if not new_text:
            raise ReminderRefused("reminder_text_empty")
        if len(new_text) > MAX_TEXT_CHARS:
            raise ReminderRefused("reminder_text_too_long")
    new_due = None
    if due is not None:
        if not isinstance(due, datetime) or due.tzinfo is None or due.utcoffset() is None:
            raise ReminderRefused("reminder_due_naive")
        new_due = format_utc(due)
        due_at = parse_utc(new_due)
        if due_at < current - PAST_TOLERANCE:
            raise ReminderRefused("reminder_due_past")
        if due_at > current + MAX_LEAD_TIME:
            raise ReminderRefused("reminder_due_too_far")
    directory = store_dir(root)
    if not (directory / STORE_FILE).exists():
        raise ReminderRefused("reminder_not_found")
    with _locked(directory):
        document = _read(directory)
        record = _find(document, reminder_id)
        if record is None:
            raise ReminderRefused("reminder_not_found")
        if record.get("state") not in EDITABLE_STATES:
            raise ReminderRefused("reminder_not_editable")
        if expected_due_utc is not None and record.get("due_utc") != expected_due_utc:
            raise ReminderRefused("reminder_changed")
        if new_due is not None:
            record["due_utc"] = new_due
        if new_text is not None:
            record["text"] = new_text
        record["edited_utc"] = format_utc(current)
        _write(directory, document)
        return dict(record)
