"""The shared turn-log writer (core/agent_turns, v0.5.1 §8), tested alone with
synthetic data. CoS's own behaviour is pinned in tests/cos/test_turn_log.py and
tests/cos/test_turn_log_format.py; Master Craftsman uses the same functions."""
from __future__ import annotations

import errno
import json
import stat
import threading
from datetime import datetime, timezone

import pytest

from core.agent_turns import writer
from core.agent_turns.writer import (DISK_LOW, INTERNAL, SAVED, WRITE_FAILED, append_record, cap_and_scrub,
                                     container_name, save_record)

NOW = datetime(2026, 10, 4, 3, 30, 5, tzinfo=timezone.utc)      # Oct 3, 22:30 in Chicago
FLOOR, FRACTION = "T_MIN_FREE_BYTES", "T_MIN_FREE_FRACTION"
FAKE_KEY = "sk-ant-FAKEFAKEFAKE12345"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv(FLOOR, "1")
    monkeypatch.delenv(FRACTION, raising=False)
    monkeypatch.delenv("AGENT_TURNS_TIMEZONE", raising=False)
    monkeypatch.setenv("COS_AGENT_TIMEZONE", "America/Chicago")


def save(root, record=None, **kw):
    args = dict(now=NOW, container="c1", log_tag="t", floor_env=FLOOR, fraction_env=FRACTION)
    args.update(kw)
    return save_record(root, lambda: record or {"record_type": "x", "n": 1}, **args)


def lines(root):
    return [json.loads(l) for p in sorted(root.rglob("*.jsonl")) for l in p.read_text().splitlines() if l.strip()]


def test_the_file_is_named_by_the_local_day_with_utc_time_inside_and_modes_0600_0700(tmp_path):
    root = tmp_path / "t"
    assert save(root, {"time": "2026-10-04T03:30:05Z"}) == (True, SAVED)
    target = root / "2026" / "2026-10-03.jsonl"                  # not the UTC day
    assert json.loads(target.read_text())["time"] == "2026-10-04T03:30:05Z"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "2026").stat().st_mode) == 0o700 and stat.S_IMODE(root.stat().st_mode) == 0o700


def test_the_timezone_can_be_set_for_agents_without_a_cos_name(tmp_path, monkeypatch):
    monkeypatch.delenv("COS_AGENT_TIMEZONE")
    monkeypatch.setenv("AGENT_TURNS_TIMEZONE", "Asia/Tokyo")      # Oct 4, 12:30
    append_record(tmp_path, {"a": 1}, NOW)
    assert (tmp_path / "2026" / "2026-10-04.jsonl").exists()
    monkeypatch.setenv("AGENT_TURNS_TIMEZONE", "Not/AZone")        # unknown zone: Chicago
    append_record(tmp_path, {"a": 2}, NOW)
    assert (tmp_path / "2026" / "2026-10-03.jsonl").exists()


def test_a_crash_truncated_tail_is_closed_with_a_log_gap_line(tmp_path):
    append_record(tmp_path, {"a": 1}, NOW)
    path = tmp_path / "2026" / "2026-10-03.jsonl"
    path.write_bytes(path.read_bytes() + b'{"half":')
    append_record(tmp_path, {"a": 2}, NOW)
    raw = path.read_text().splitlines()
    assert raw[1] == '{"half":'                                   # the fragment is kept, on its own line
    assert json.loads(raw[2]) == {"record_type": "log_gap", "reason": "truncated_tail"}
    assert json.loads(raw[3]) == {"a": 2}


def test_concurrent_appends_stay_whole_lines(tmp_path):
    def worker(i):
        for j in range(25):
            append_record(tmp_path, {"i": i, "j": j, "pad": "x" * 500}, NOW)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(lines(tmp_path)) == 150


def test_status_file_has_fixed_fields_and_no_text(tmp_path):
    root = tmp_path / "t"
    save(root)
    status = json.loads((root / "_status" / "c1.json").read_text())
    assert status == {"schema_version": 1, "container": "c1", "last_success_at": "2026-10-04T03:30:05Z"}
    assert stat.S_IMODE((root / "_status" / "c1.json").stat().st_mode) == 0o600
    assert save(root, append=lambda *a: (_ for _ in ()).throw(OSError(errno.ENOSPC, "no space left: SECRET-TEXT"))) \
        == (False, WRITE_FAILED)
    raw = (root / "_status" / "c1.json").read_text()
    assert json.loads(raw)["last_failure_code"] == WRITE_FAILED and "SECRET-TEXT" not in raw


def test_an_unknown_failure_code_is_stored_as_internal(tmp_path):
    writer.write_status(tmp_path, "c1", ok=False, code="some free text", now=NOW)
    assert json.loads((tmp_path / "_status" / "c1.json").read_text())["last_failure_code"] == INTERNAL


def test_below_the_floor_skips_with_disk_low_and_deletes_nothing(tmp_path, monkeypatch):
    root = tmp_path / "t"
    save(root)
    before = (root / "2026" / "2026-10-03.jsonl").read_bytes()
    monkeypatch.setenv(FLOOR, str(10 ** 18))
    built = []
    assert save_record(root, lambda: built.append(1) or {"a": 1}, now=NOW, container="c1", log_tag="t",
                       floor_env=FLOOR, fraction_env=FRACTION) == (False, DISK_LOW)
    assert not built                                              # a skipped write builds nothing
    assert (root / "2026" / "2026-10-03.jsonl").read_bytes() == before
    assert json.loads((root / "_status" / "c1.json").read_text())["last_failure_code"] == DISK_LOW


def test_the_fraction_floor_counts_too(tmp_path, monkeypatch):
    monkeypatch.setenv(FRACTION, "0.9999999")
    assert save(tmp_path / "t") == (False, DISK_LOW)


def test_an_unreadable_disk_counts_as_low(tmp_path, monkeypatch):
    def boom(_):
        raise OSError("gone")
    monkeypatch.setattr(writer.shutil, "disk_usage", boom)
    assert save(tmp_path / "t") == (False, DISK_LOW)


def test_a_build_failure_is_internal_and_never_raises(tmp_path):
    def build():
        raise ValueError("secret detail")
    assert save_record(tmp_path / "t", build, now=NOW, container="c1", log_tag="t",
                       floor_env=FLOOR, fraction_env=FRACTION) == (False, INTERNAL)


def test_an_unwritable_folder_is_a_write_failure(tmp_path, capsys):
    (tmp_path / "file").write_text("a file, not a folder")
    assert save(tmp_path / "file" / "t") == (False, WRITE_FAILED)
    out = capsys.readouterr().out
    assert "saved=false code=disk_write_failed" in out and "file" not in out.replace("saved=false", "")


def test_caps_come_first_then_the_scrub_and_the_flag(tmp_path):
    text, changed = cap_and_scrub("a" * 9000, 8000)
    assert len(text) == 8000 and not changed
    text, changed = cap_and_scrub(f"key {FAKE_KEY} and card 4111 1111 1111 1111", 8000)
    assert changed and FAKE_KEY not in text and "4111" not in text
    assert cap_and_scrub(None, 10) == ("", False)
    assert (writer.MAX_USER_TEXT, writer.MAX_REPLY) == (8000, 16000)


def test_a_secret_past_the_cap_is_cut_not_kept():
    text, _ = cap_and_scrub("a" * 8000 + FAKE_KEY, 8000)
    assert FAKE_KEY not in text


def test_container_name_is_filename_safe_and_bounded(monkeypatch):
    monkeypatch.setenv("X_NAME", "../evil name/" + "z" * 100)
    name = container_name("X_NAME", "dflt")
    assert "/" not in name and " " not in name and len(name) <= 64
    monkeypatch.delenv("X_NAME")
    monkeypatch.setattr(writer.socket, "gethostname", lambda: "")
    assert container_name("X_NAME", "dflt") == "dflt"
