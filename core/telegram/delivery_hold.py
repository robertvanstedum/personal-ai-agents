"""Operator command for a held scheduled-briefing day (#305).

A day is held when its delivery record is started, partial, uncertain or
unreadable. Neither the sender nor the cron ever resends a held day. After
checking the Telegram chat, an operator records one decision:

  delivered  enough of the briefing arrived; nothing more is sent that day
  skipped    no briefing that day; nothing more is sent
  resend     nothing usable arrived; the next run delivers again. Messages that
             did arrive will arrive twice.

  python3 -m core.telegram.delivery_hold status 2026-10-17
  python3 -m core.telegram.delivery_hold resolve 2026-10-17 --disposition resend \\
      --expect-sha256 <sha256 from status> --operator codex --note "only the header arrived"

resolve refuses unless the day is held, its record's bytes still hash to
--expect-sha256 and no delivery holds the day's lock. It keeps all history: the
held bytes are copied to <date>.held-<UTC stamp>.json (never overwritten) and the
decision is appended to resolutions.jsonl, both fsynced before the record is
replaced. Standard library only. Exit codes: 0 done, 2 refused (nothing visible changed), 3 resolved but
its durability is unconfirmed (the record already reads resolved; run status), 4 busy.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import sys

from core.telegram import delivery_ledger as dl

REFUSED, UNCERTAIN, BUSY = 2, 3, 4
_raw_write = os.write                 # the one low-level write; tests inject short writes here


def _now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _day(text):
    try:
        day = datetime.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a date like 2026-10-17") from None
    if day.isoformat() != text:
        raise argparse.ArgumentTypeError("expected a date like 2026-10-17")
    return text


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd, data):
    """os.write may write less than asked: loop until every byte is written; no progress is an error."""
    view = memoryview(data)
    while view:
        written = _raw_write(fd, view)
        if not written or written < 0:
            raise OSError("write made no progress")
        view = view[written:]


def _write_new(path, data):
    """Create path (never overwrite) with all of data, fsynced."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_line(path, line):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        _write_all(fd, line.encode("utf-8") + b"\n")
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(path, record):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        # A temp left by an earlier run is evidence: never touch it, and never call this "archive exists".
        raise OSError("temp_exists") from None
    try:                                  # from here the temp is this invocation's own: clean only it
        try:
            _write_all(fd, (json.dumps(record, indent=2, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def _snapshot(path, day):
    """(state, record, raw bytes or None, problem or None) for the day's record."""
    try:
        raw = dl._read_regular(path, dl._MAX_BYTES)
    except FileNotFoundError:
        return None, None, None, None
    except OSError as exc:
        return "invalid", None, None, f"not a readable regular file ({exc})"
    if len(raw) > dl._MAX_BYTES:
        return "invalid", None, None, f"larger than {dl._MAX_BYTES} bytes"
    state, record = dl.read_record(path, day)
    return state, record, raw, None


def _lock_busy(lock_path):
    """True when a delivery or another resolve holds the day's lock. Creates nothing."""
    try:
        fd = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def status(day, out=sys.stdout):
    folder = dl.delivery_dir()
    path = folder / f"{day}.json"
    state, record, raw, problem = _snapshot(path, day)
    print(f"date: {day}", file=out)
    print(f"record: {path}", file=out)
    if state is None:
        print("state: none (no record)", file=out)
    else:
        held = state in dl.HOLD_STATES or state == "invalid"
        print(f"state: {state}" + ("  (HELD: resolve after checking the Telegram chat)" if held else ""), file=out)
    if problem:
        print(f"problem: {problem}; inspect by hand, resolve refuses it", file=out)
    if raw is not None:
        print(f"sha256: {hashlib.sha256(raw).hexdigest()}", file=out)
        print("bytes (exactly what the sha256 covers):", file=out)
        print(raw.decode("utf-8", "replace"), file=out)
    print(f"lock: {'BUSY (a delivery or resolve is running)' if _lock_busy(folder / (day + '.lock')) else 'free'}",
          file=out)
    return 0


def resolve(day, disposition, expect_sha256, operator, note, now=_now, out=sys.stdout):
    def refuse(reason):
        print(f"REFUSED: {reason}; nothing changed", file=out)
        return REFUSED

    if day > now().date().isoformat():
        return refuse("that day has not started (UTC)")
    if disposition not in dl.DISPOSITIONS:
        return refuse(f"disposition must be one of {', '.join(dl.DISPOSITIONS)}")
    if not dl._OPERATOR.match(operator):
        return refuse("operator must be 1-40 letters, digits, dot, dash or underscore")
    if not dl.valid_note(note):
        return refuse(f"note must be 1-{dl._MAX_NOTE} printable characters on one line, no outer spaces")

    folder = dl.delivery_dir()
    path = folder / f"{day}.json"
    try:
        lock = os.open(folder / f"{day}.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as exc:
        return refuse(f"cannot open the day's lock ({type(exc).__name__})")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"BUSY: a delivery or resolve for {day} holds the lock; nothing changed", file=out)
            return BUSY
        state, record, raw, problem = _snapshot(path, day)
        if problem:
            return refuse(f"the record is {problem}; inspect it by hand")
        if state is None:
            return refuse("there is no record for that day")
        if state not in dl.HOLD_STATES and state != "invalid":
            return refuse(f"the day is not held (state {state})")
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expect_sha256:
            return refuse(f"the record changed since it was inspected (sha256 now {actual}); run status again")

        stamp = now()
        archive = folder / f"{day}.held-{stamp.strftime('%Y%m%dT%H%M%SZ')}.json"
        resolution = {"format": dl.FORMAT, "date": day, "state": "resolved",
                      "resolved_at": stamp.isoformat(), "disposition": disposition, "operator": operator,
                      "note": note, "held_state": state, "held_sha256": actual, "held_archive": archive.name}
        if record is not None and "previous_attempts" in record:
            resolution["previous_attempts"] = record["previous_attempts"]
        assert dl.classify(resolution, day) == "resolved", resolution
        try:
            _write_new(archive, raw)
            _fsync_dir(folder)
            _append_line(folder / "resolutions.jsonl", json.dumps(resolution, sort_keys=True))
            _replace(path, resolution)
        except FileExistsError:
            return refuse(f"{archive.name} already exists; wait a second and run again")
        except OSError as exc:
            # Before the replace nothing visible changed: the record still reads held.
            print(f"ERROR: resolution not completed ({type(exc).__name__}); the day stays held. "
                  f"Check {folder} for {archive.name} and resolutions.jsonl", file=out)
            return REFUSED
        try:
            _fsync_dir(folder)
        except OSError as exc:
            # After the replace the live record already reads resolved (a resend may run), but a crash before
            # the directory reaches disk could bring the held record back. Say exactly that.
            print(f"UNCERTAIN: {day} now reads RESOLVED as {disposition} (was {state}), but the final directory "
                  f"sync failed ({type(exc).__name__}); a crash could restore the held record. Run status {day} "
                  f"to see the current state. Held bytes kept in {archive.name}", file=out)
            return UNCERTAIN
    finally:
        os.close(lock)
    print(f"RESOLVED {day} as {disposition} (was {state}); held bytes kept in {archive.name}", file=out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m core.telegram.delivery_hold",
                                     description="Inspect or resolve a held scheduled-briefing day.")
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("status", help="show a day's delivery record (read-only)")
    show.add_argument("date", type=_day)
    fix = sub.add_parser("resolve", help="record an operator decision for a held day")
    fix.add_argument("date", type=_day)
    fix.add_argument("--disposition", required=True, choices=dl.DISPOSITIONS)
    fix.add_argument("--expect-sha256", required=True)
    fix.add_argument("--operator", required=True)
    fix.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    if args.command == "status":
        return status(args.date)
    return resolve(args.date, args.disposition, args.expect_sha256, args.operator, args.note)


if __name__ == "__main__":
    sys.exit(main())
