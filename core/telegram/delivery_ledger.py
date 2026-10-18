"""The scheduled briefing's per-day delivery ledger: one shared set of rules (#305).

telegram_bot.deliver_briefing() writes data/curator/telegram_delivery/<UTC date>.json;
the EC2 cron wrapper reads it through cron_state(). Both use classify(), so a
record proves delivery only when it is exactly well formed for TODAY. A
malformed, unknown or wrong-day record is "invalid", and invalid means HOLD,
never "delivered" and never "safe to resend". Standard library only: the cron
runs it with `python3 -c` inside the Curator container.

A held day changes only through core/telegram/delivery_hold.py, which records an
operator's decision as a "resolved" record (see DISPOSITIONS).
"""
import datetime
import json
import os
import pathlib
import re
import stat

FORMAT = "curator-telegram-delivery/1"
STATES = ("started", "acknowledged", "not_sent", "partial", "uncertain", "resolved")
HOLD_STATES = ("started", "partial", "uncertain")
NOT_SENT_REASONS = ("missing_config", "missing_briefing", "empty_briefing", "rejected")
# An operator's decision on a held day. delivered and skipped end the day: nothing
# more is sent. resend lets the next run deliver again, like not_sent.
DISPOSITIONS = ("delivered", "skipped", "resend")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_OPERATOR = re.compile(r"[A-Za-z0-9._-]{1,40}\Z")
_ARCHIVE = re.compile(r"(\d{4}-\d{2}-\d{2})\.held-\d{8}T\d{6}Z\.json\Z")
_MAX_NOTE = 200
_COMMON = {"format", "date", "state"}
# Exactly the keys each state may carry; anything else is invalid.
_REQUIRED = {
    "started": {"started_at", "planned", "briefing_sha256"},
    "acknowledged": {"started_at", "finished_at", "planned", "accepted", "briefing_sha256"},
    "partial": {"started_at", "finished_at", "planned", "accepted", "briefing_sha256", "reason", "http_status"},
    "uncertain": {"started_at", "finished_at", "planned", "accepted", "briefing_sha256", "reason"},
    "not_sent": {"finished_at", "accepted", "reason"},
    "resolved": {"resolved_at", "disposition", "operator", "note", "held_state", "held_sha256", "held_archive"},
}
_OPTIONAL = {state: {"previous_attempts"} for state in STATES}
_OPTIONAL["not_sent"] |= {"started_at", "planned", "briefing_sha256", "http_status"}
_MAX_BYTES = 8192
_MAX_LATEST_BYTES = 16 << 20   # curator_latest.json is a few hundred KB


def today_utc():
    return datetime.datetime.now(datetime.timezone.utc).date().isoformat()


def delivery_dir():
    return pathlib.Path(os.environ.get("CURATOR_DELIVERY_DIR") or "data/curator/telegram_delivery")


def _count(value, low=0):
    return type(value) is int and low <= value <= 1000


def _stamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        datetime.datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def classify(record, today):
    """Return the record's state if it is exactly valid for `today`, else "invalid"."""
    if not isinstance(record, dict) or record.get("format") != FORMAT or record.get("date") != today:
        return "invalid"
    state = record.get("state")
    if state not in STATES:
        return "invalid"
    keys = set(record) - _COMMON
    required, optional = _REQUIRED[state], _OPTIONAL.get(state, set())
    if not required <= keys or keys - required - optional:
        return "invalid"
    for name in ("started_at", "finished_at", "resolved_at"):
        if name in record and not _stamp(record[name]):
            return "invalid"
    if "briefing_sha256" in record and not (isinstance(record["briefing_sha256"], str)
                                            and _SHA256.match(record["briefing_sha256"])):
        return "invalid"
    if "planned" in record and not _count(record["planned"], low=3):  # header + >=1 article + footer
        return "invalid"
    if "accepted" in record and not _count(record["accepted"]):
        return "invalid"
    if "previous_attempts" in record and not _count(record["previous_attempts"]):
        return "invalid"
    if state == "acknowledged" and record["accepted"] != record["planned"]:
        return "invalid"
    if state in ("partial", "uncertain") and record["accepted"] >= record["planned"]:
        return "invalid"
    if state == "partial" and not (record["accepted"] >= 1 and record["reason"] == "rejected"
                                   and type(record["http_status"]) is int and 400 <= record["http_status"] < 500):
        return "invalid"
    if state == "uncertain" and not (isinstance(record["reason"], str) and 0 < len(record["reason"]) <= 64):
        return "invalid"
    if state == "not_sent":
        if record["accepted"] != 0 or record["reason"] not in NOT_SENT_REASONS:
            return "invalid"
        if (record["reason"] == "rejected") != ("http_status" in record):
            return "invalid"
        if "http_status" in record and not (type(record["http_status"]) is int and 400 <= record["http_status"] < 500):
            return "invalid"
    if state == "resolved" and not valid_resolution(record, today):
        return "invalid"
    return state


def valid_note(note):
    return isinstance(note, str) and 0 < len(note) <= _MAX_NOTE and note.isprintable() and note.strip() == note


def valid_resolution(record, today):
    archive = record["held_archive"]
    match = _ARCHIVE.match(archive) if isinstance(archive, str) else None
    return (record["disposition"] in DISPOSITIONS
            and record["held_state"] in HOLD_STATES + ("invalid",)
            and isinstance(record["held_sha256"], str) and bool(_SHA256.match(record["held_sha256"]))
            and isinstance(record["operator"], str) and bool(_OPERATOR.match(record["operator"]))
            and valid_note(record["note"])
            and match is not None and match.group(1) == today)


def _no_duplicate_keys(pairs):
    record = {}
    for key, value in pairs:
        if key in record:
            raise ValueError("duplicate_key")
        record[key] = value
    return record


def _read_regular(path, limit):
    """Bytes of a regular, non-symlink file, at most limit + 1 of them.

    O_NONBLOCK keeps a FIFO or device from blocking the open; fstat then refuses
    anything that is not a regular file. Raises FileNotFoundError or OSError.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not_a_regular_file")
        return os.read(fd, limit + 1)
    finally:
        os.close(fd)


def read_record(path, today):
    """(None, None) when no record exists; otherwise (state, record or None).

    Reads at most _MAX_BYTES + 1 bytes from a regular, non-symlink file, and
    rejects duplicate JSON keys (a later key would silently override an earlier
    one). Anything unreadable, oversize or malformed is ("invalid", None).
    """
    try:
        raw = _read_regular(path, _MAX_BYTES)
    except FileNotFoundError:
        return None, None
    except OSError:
        return "invalid", None
    if len(raw) > _MAX_BYTES:
        return "invalid", None
    try:
        record = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (ValueError, UnicodeError):
        return "invalid", None
    state = classify(record, today)
    return state, (record if state != "invalid" else None)


def read_state(path, today):
    """None when no record exists; otherwise its classified state."""
    return read_record(path, today)[0]


def cron_state(today=None):
    """What the cron should do today: delivered, resolved, hold, generated or none."""
    today = today or today_utc()
    state, record = read_record(delivery_dir() / (today + ".json"), today)
    if state == "acknowledged":
        return "delivered"
    if state == "resolved" and record["disposition"] != "resend":
        return "resolved"
    if state is not None and state not in ("not_sent", "resolved"):
        return "hold"
    try:
        raw = _read_regular("data/curator/curator_latest.json", _MAX_LATEST_BYTES)
        latest = json.loads(raw) if len(raw) <= _MAX_LATEST_BYTES else None
        generated = str(latest[0].get("briefing_date", ""))[:10] == today
    except Exception:
        generated = False
    out = pathlib.Path(os.environ.get("CURATOR_OUTPUT_PATH") or "curator_output.txt")
    fresh = out.is_file() and datetime.datetime.fromtimestamp(
        out.stat().st_mtime, datetime.timezone.utc).date().isoformat() == today
    return "generated" if generated and fresh else "none"
