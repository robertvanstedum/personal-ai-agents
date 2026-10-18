"""The HOLD-resolution command for a held scheduled-briefing day (#305).

A held day is never resent automatically. An operator checks the chat, then
records delivered, skipped or resend. History is kept: the held bytes are
archived and every decision is appended to resolutions.jsonl. Local files and
fake Telegram only.
"""

import fcntl
import hashlib
import io
import json
import os

import pytest

from core.telegram import delivery_hold as hold
from core.telegram import delivery_ledger as dl
from tests.test_curator_delivery_ledger import OK, TODAY, bot, cron  # noqa: F401  (fixtures)


def _held(state="uncertain", accepted=1, planned=4):
    record = {"format": dl.FORMAT, "date": TODAY, "state": state, "started_at": "2026-10-16T13:00:00+00:00",
              "planned": planned, "briefing_sha256": "a" * 64}
    if state != "started":
        record.update(finished_at="2026-10-16T13:00:31+00:00", accepted=accepted, reason="Timeout")
    return (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()


@pytest.fixture()
def day(tmp_path, monkeypatch):
    folder = tmp_path / "telegram_delivery"
    folder.mkdir()
    monkeypatch.setenv("CURATOR_DELIVERY_DIR", str(folder))
    path = folder / f"{TODAY}.json"

    def put(raw):
        path.write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def run(disposition="delivered", sha=None, operator="codex", note="checked the chat"):
        out = io.StringIO()
        code = hold.resolve(TODAY, disposition, sha, operator, note, out=out)
        return code, out.getvalue()
    run.put, run.path, run.folder = put, path, folder
    return run


def _status():
    out = io.StringIO()
    assert hold.status(TODAY, out=out) == 0
    return out.getvalue()


def test_status_is_read_only_and_shows_the_hash_to_confirm(day):
    assert "state: none" in _status()
    sha = day.put(_held())
    text = _status()
    assert "state: uncertain  (HELD" in text and f"sha256: {sha}" in text and "lock: free" in text
    assert sorted(p.name for p in day.folder.iterdir()) == [f"{TODAY}.json"]   # no lock file created


def test_resolving_as_delivered_keeps_history_and_ends_the_day(day):
    raw = _held()
    sha = day.put(raw)
    code, text = day("delivered", sha)
    assert code == 0 and "RESOLVED" in text
    record = json.loads(day.path.read_text())
    assert dl.classify(record, TODAY) == "resolved"
    assert record["held_state"] == "uncertain" and record["held_sha256"] == sha
    assert (day.folder / record["held_archive"]).read_bytes() == raw          # held bytes kept exactly
    audit = [json.loads(l) for l in (day.folder / "resolutions.jsonl").read_text().splitlines()]
    assert audit == [record]
    assert dl.cron_state(TODAY) == "resolved"


def test_a_malformed_record_can_be_resolved_and_its_bytes_are_kept(day):
    raw = b'{"format": "x", "accepted": 0, "accepted": 3}'
    sha = day.put(raw)
    assert dl.cron_state(TODAY) == "hold"
    assert day("skipped", sha)[0] == 0
    record = json.loads(day.path.read_text())
    assert record["held_state"] == "invalid" and (day.folder / record["held_archive"]).read_bytes() == raw
    assert dl.cron_state(TODAY) == "resolved"


@pytest.mark.parametrize("raw", [
    None,                                                                       # no record
    _held("uncertain").replace(b'"uncertain"', b'"acknowledged"').replace(b'"accepted": 1', b'"accepted": 4')
    .replace(b'  "reason": "Timeout",\n', b""),                                # delivered: not held
])
def test_a_day_that_is_not_held_is_refused(day, raw):
    sha = day.put(raw) if raw is not None else "0" * 64
    if raw is not None:
        assert dl.read_state(day.path, TODAY) == "acknowledged"
    code, text = day("delivered", sha)
    assert code == hold.REFUSED and "nothing changed" in text
    assert not (day.folder / "resolutions.jsonl").exists()


def test_a_changed_record_is_refused(day):
    day.put(_held())
    code, text = day("resend", "f" * 64)
    assert code == hold.REFUSED and "changed since it was inspected" in text
    assert json.loads(day.path.read_text())["state"] == "uncertain"


def test_a_running_delivery_blocks_the_resolve(day):
    sha = day.put(_held("started"))
    fd = os.open(day.folder / f"{TODAY}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert "lock: BUSY" in _status()
        code, text = day("resend", sha)
        assert code == hold.BUSY and "nothing changed" in text
    finally:
        os.close(fd)
    assert json.loads(day.path.read_text())["state"] == "started"


@pytest.mark.parametrize("operator,note", [
    ("", "ok"), ("rob ert", "ok"), ("x" * 41, "ok"),
    ("codex", ""), ("codex", "two\nlines"), ("codex", " padded"), ("codex", "n" * 201),
])
def test_operator_and_note_are_validated(day, operator, note):
    sha = day.put(_held())
    assert day("delivered", sha, operator=operator, note=note)[0] == hold.REFUSED


def test_an_already_resolved_day_is_not_resolved_again(day):
    sha = day.put(_held())
    assert day("delivered", sha)[0] == 0
    again = hashlib.sha256(day.path.read_bytes()).hexdigest()
    code, text = day("resend", again)
    assert code == hold.REFUSED and "not held (state resolved)" in text


@pytest.mark.parametrize("field,value", [
    ("disposition", "maybe"), ("held_state", "acknowledged"), ("held_sha256", "nope"),
    ("held_archive", "../../etc/passwd"), ("held_archive", "2001-01-01.held-20010101T000000Z.json"),
    ("operator", "a b"), ("note", ""), ("extra", 1),
])
def test_a_tampered_resolution_never_proves_anything(day, field, value):
    sha = day.put(_held())
    day("delivered", sha)
    record = json.loads(day.path.read_text())
    record[field] = value
    day.path.write_text(json.dumps(record))
    assert dl.cron_state(TODAY) == "hold"


def test_a_future_day_is_refused(day):
    out = io.StringIO()
    assert hold.resolve("2999-01-01", "delivered", "0" * 64, "codex", "x", out=out) == hold.REFUSED
    assert "has not started" in out.getvalue()


# ── The real sender honours the decision ──────────────────────────────────────

def _resolve_bot_day(tb, monkeypatch, disposition):
    monkeypatch.setenv("CURATOR_DELIVERY_DIR", str(tb.DELIVERY_DIR))
    path = tb.DELIVERY_DIR / f"{TODAY}.json"
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert hold.resolve(TODAY, disposition, sha, "codex", "checked the chat", out=io.StringIO()) == 0


@pytest.mark.parametrize("disposition", ["delivered", "skipped"])
def test_sender_sends_nothing_on_a_day_resolved_as_ended(bot, monkeypatch, disposition):
    import requests
    bot.script(OK, requests.exceptions.Timeout())
    assert bot.deliver_briefing("tkn", "42") == 3
    _resolve_bot_day(bot, monkeypatch, disposition)
    sent = len(bot.posts)
    assert bot.deliver_briefing("tkn", "42") == 0
    assert len(bot.posts) == sent
    assert json.loads((bot.DELIVERY_DIR / f"{TODAY}.json").read_text())["state"] == "resolved"


def test_sender_delivers_again_after_resend(bot, monkeypatch):
    import requests
    bot.script(OK, requests.exceptions.Timeout())
    assert bot.deliver_briefing("tkn", "42") == 3
    _resolve_bot_day(bot, monkeypatch, "resend")
    sent = len(bot.posts)
    bot.script()
    assert bot.deliver_briefing("tkn", "42") == 0
    record = json.loads((bot.DELIVERY_DIR / f"{TODAY}.json").read_text())
    assert record["state"] == "acknowledged" and record["previous_attempts"] == 1
    assert len(bot.posts) == sent + record["planned"]
    archived = list(bot.DELIVERY_DIR.glob(f"{TODAY}.held-*.json"))
    assert len(archived) == 1 and json.loads(archived[0].read_text())["state"] == "uncertain"


# ── The real cron wrapper honours the decision ────────────────────────────────

def _resolve_cron_day(monkeypatch, tmp_path, disposition):
    folder = tmp_path / "app" / "data" / "curator" / "telegram_delivery"
    monkeypatch.setenv("CURATOR_DELIVERY_DIR", str(folder))
    sha = hashlib.sha256((folder / f"{TODAY}.json").read_bytes()).hexdigest()
    assert hold.resolve(TODAY, disposition, sha, "codex", "checked the chat", out=io.StringIO()) == 0


def test_composed_resolved_day_runs_nothing(cron, tmp_path, monkeypatch):
    result, _ = cron("timeout_first")
    assert result.returncode == 1 and cron.ledger()["state"] == "uncertain" and cron.posts() == 1
    _resolve_cron_day(monkeypatch, tmp_path, "delivered")
    result, calls = cron("ok")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "resolved by an operator" in result.stdout and calls == ["state"] and cron.posts() == 1


def test_composed_resend_delivers_without_rescoring(cron, tmp_path, monkeypatch):
    result, _ = cron("timeout_first")
    assert result.returncode == 1 and cron.posts() == 1
    _resolve_cron_day(monkeypatch, tmp_path, "resend")
    result, calls = cron("ok")
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == ["state", "telegram"]                         # delivery only: no paid rescoring
    assert cron.ledger()["state"] == "acknowledged" and cron.ledger()["previous_attempts"] == 1


# ── recovery under faults (Codex review, 17 Oct): short writes and the final directory sync ──

def test_a_short_write_still_archives_every_held_byte(day, monkeypatch):
    raw = _held()
    sha = day.put(raw)
    real, calls = hold._raw_write, []

    def short(fd, data):
        calls.append(len(data))
        return real(fd, bytes(data)[:max(1, len(data) // 2)])        # the kernel may write less than asked
    monkeypatch.setattr(hold, "_raw_write", short)
    assert day("delivered", sha)[0] == 0
    record = json.loads(day.path.read_text())
    assert (day.folder / record["held_archive"]).read_bytes() == raw and len(calls) > 3
    assert json.loads((day.folder / "resolutions.jsonl").read_text().splitlines()[0]) == record


def test_a_write_that_makes_no_progress_refuses_and_the_day_stays_held(day, monkeypatch):
    sha = day.put(_held())
    monkeypatch.setattr(hold, "_raw_write", lambda fd, data: 0)
    code, text = day("delivered", sha)
    assert code == hold.REFUSED and "stays held" in text
    assert json.loads(day.path.read_text())["state"] == "uncertain"
    assert dl.cron_state(TODAY) == "hold"
    assert not list(day.folder.glob(".*.tmp"))


def test_a_failed_final_directory_sync_reports_the_resolved_state_truthfully(day, monkeypatch):
    sha = day.put(_held())
    real, calls = hold._fsync_dir, []

    def fsync_dir(path):
        calls.append(path)
        if len(calls) == 2:                                          # the sync after the record was replaced
            raise OSError("synthetic fsync failure")
        return real(path)
    monkeypatch.setattr(hold, "_fsync_dir", fsync_dir)
    code, text = day("resend", sha)
    assert code == hold.UNCERTAIN and "now reads RESOLVED" in text and "stays held" not in text
    assert json.loads(day.path.read_text())["state"] == "resolved"     # what the cron and sender will see
    assert dl.cron_state(TODAY) in ("generated", "none")              # resend: delivery may run again


def test_a_failed_sync_before_the_replace_keeps_the_day_held(day, monkeypatch):
    sha = day.put(_held())

    def fail(path):
        raise OSError("synthetic fsync failure")
    monkeypatch.setattr(hold, "_fsync_dir", fail)                      # the first sync, before the replace
    code, text = day("delivered", sha)
    assert code == hold.REFUSED and "stays held" in text
    assert json.loads(day.path.read_text())["state"] == "uncertain"


def test_a_temp_left_by_an_earlier_run_is_preserved_and_the_day_stays_held(day, monkeypatch):
    sha = day.put(_held())
    monkeypatch.setattr(hold.os, "getpid", lambda: 4242)
    sentinel = day.folder / f".{TODAY}.json.4242.tmp"
    sentinel.write_bytes(b"earlier evidence")
    code, text = day("delivered", sha)
    assert code == hold.REFUSED and "stays held" in text and "already exists; wait" not in text
    assert sentinel.read_bytes() == b"earlier evidence"                 # never deleted
    assert json.loads(day.path.read_text())["state"] == "uncertain"


def test_a_failed_write_removes_only_the_temp_this_run_created(day, monkeypatch):
    sha = day.put(_held())
    real, calls = hold._raw_write, []

    def fail_third(fd, data):                    # archive, audit line, then the record's temp write fails
        calls.append(1)
        if len(calls) == 3:
            raise OSError("synthetic write failure")
        return real(fd, data)
    monkeypatch.setattr(hold, "_raw_write", fail_third)
    code, _ = day("delivered", sha)
    assert code == hold.REFUSED and not list(day.folder.glob(".*.tmp"))
    assert json.loads(day.path.read_text())["state"] == "uncertain"
