"""Catch-up publishing (17 Oct 2026): the Mac may sleep through the window, so the publish runs several times inside it
and again on wake. A run whose content matches the last confirmed publish uploads nothing; a failure never records a
fingerprint, so the next run retries. Synthetic shelf and index only."""
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest

from core.memory_backup import production

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("publish_production", ROOT / "scripts/vault/publish_production.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


@pytest.fixture
def vault(tmp_path):
    shelf, index = tmp_path / "memory", tmp_path / "memory-index" / "memory.sqlite"
    (shelf / "_ledger").mkdir(parents=True)
    (shelf / "_status").mkdir()
    (shelf / "sessions-raw" / "r1").mkdir(parents=True)
    (shelf / "sessions-raw" / "r1" / "r1.md").write_text("record one\n")
    (shelf / "_ledger" / "ledger.jsonl").write_text("{}\n")
    index.parent.mkdir()
    con = sqlite3.connect(index)
    con.execute("CREATE TABLE meta(key TEXT, value TEXT)")
    con.execute("INSERT INTO meta VALUES('updated_at', '2026-10-17T05:00:00Z')")
    con.commit()
    con.close()
    return shelf, index


def test_fingerprint_ignores_ledger_and_status_churn_but_sees_any_record_change(vault):
    shelf, index = vault
    first = production.content_fingerprint(shelf, index)
    with (shelf / "_ledger" / "ledger.jsonl").open("a") as f:
        f.write('{"outcome": "unchanged"}\n')                           # every pass appends, even with nothing new
    (shelf / "_status" / "watch-codex.json").write_text("{}")
    assert production.content_fingerprint(shelf, index) == first
    (shelf / "sessions-raw" / "r1" / "editions").mkdir()
    (shelf / "sessions-raw" / "r1" / "editions" / "2--abc.jsonl").write_text("new edition\n")
    assert production.content_fingerprint(shelf, index) != first


def test_fingerprint_sees_an_index_update(vault):
    shelf, index = vault
    first = production.content_fingerprint(shelf, index)
    con = sqlite3.connect(index)
    con.execute("UPDATE meta SET value='2026-10-17T06:00:00Z'")
    con.commit()
    con.close()
    assert production.content_fingerprint(shelf, index) != first


def _args(**kw):
    return type("A", (), {"registry": None, "allow_outside_window": True, "force": False, **kw})()


def _settings(vault, tmp_path):
    shelf, index = vault
    work = tmp_path / "work"
    work.mkdir()
    return {"shelf_root": str(shelf), "index_path": str(index), "dest": "file:///x", "recipient": "age1x", "workdir": str(work),
            "age_bin": "age", "expect_age": None, "jobs_root": str(tmp_path / "jobs")}


def test_a_repeated_run_uploads_nothing_and_a_failure_is_retried(vault, tmp_path, monkeypatch):
    s = _settings(vault, tmp_path)
    (tmp_path / "jobs").mkdir(mode=0o700)
    calls = []

    def fake_publish(**kw):
        calls.append(kw)
        if len(calls) == 1:
            raise P.backup.BackupError("upload_failed")
        return {"state": "ok", "attempt": "20261017T050000Z-aaaaaaaa", "shelf": {"records": 1},
                "index": {"chunks": 1}, "pair": {"shelf_records_not_in_index": 0},
                "objects": {"shelf": {"bytes": 1}, "index": {"bytes": 1}}}
    monkeypatch.setattr(P.production, "run_publish", fake_publish)
    monkeypatch.setattr(P.alert, "send", lambda text: "not_sent")
    assert P._publish(_args(), s) != P.EXIT_OK and len(calls) == 1          # failed: nothing remembered
    assert not (Path(s["workdir"]) / P.LAST_PUBLISHED).exists()
    assert P._publish(_args(), s) == P.EXIT_OK and len(calls) == 2          # the next run retries and publishes
    assert P._publish(_args(), s) == P.EXIT_OK and len(calls) == 2          # unchanged: nothing uploaded
    assert P._publish(_args(force=True), s) == P.EXIT_OK and len(calls) == 3
    (Path(s["shelf_root"]) / "sessions-raw" / "r1" / "r1.md").write_text("record one, edited\n")
    os.utime(Path(s["shelf_root"]) / "sessions-raw" / "r1" / "r1.md", ns=(1, 1))
    assert P._publish(_args(), s) == P.EXIT_OK and len(calls) == 4          # content changed: published again


def _fake_ok(calls):
    def fake_publish(**kw):
        calls.append(kw)
        return {"state": "ok", "attempt": f"20261017T050000Z-{len(calls):08d}", "shelf": {"records": 1},
                "index": {"chunks": 1}, "pair": {"shelf_records_not_in_index": 0},
                "objects": {"shelf": {"bytes": 1}, "index": {"bytes": 1}}}
    return fake_publish


def test_an_unchanged_run_writes_its_own_ok_status_run_so_the_watchdog_never_sees_a_missed_job(vault, tmp_path, monkeypatch):
    s = _settings(vault, tmp_path)
    (tmp_path / "jobs").mkdir(mode=0o700)
    calls = []
    monkeypatch.setattr(P.production, "run_publish", _fake_ok(calls))
    monkeypatch.setattr(P.alert, "send", lambda text: "not_sent")
    assert P._publish(_args(), s) == P.EXIT_OK and len(calls) == 1
    status_file = tmp_path / "jobs" / f"{P.JOB_ID}.json"
    first = json.loads(status_file.read_text())
    assert P._publish(_args(), s) == P.EXIT_OK and len(calls) == 1             # unchanged: nothing uploaded
    st = json.loads(status_file.read_text())
    assert st["run_id"] != first["run_id"] and st["state"] == "ok" and st["exit_code"] == 0
    assert st["results"] == {"publish": "unchanged"} and st["next_due_by"] and st["finished_at"]
    assert st["last_success_at"] == st["finished_at"] and st["last_success_at"] >= first["last_success_at"]


@pytest.mark.parametrize("change", ["dest", "recipient", "expect_age"])
def test_a_config_change_is_never_skipped_even_with_the_same_content(vault, tmp_path, monkeypatch, change):
    s = _settings(vault, tmp_path)
    (tmp_path / "jobs").mkdir(mode=0o700)
    calls = []
    monkeypatch.setattr(P.production, "run_publish", _fake_ok(calls))
    monkeypatch.setattr(P.alert, "send", lambda text: "not_sent")
    assert P._publish(_args(), s) == P.EXIT_OK and len(calls) == 1
    assert P._publish(_args(), s) == P.EXIT_OK and len(calls) == 1             # same content, same config: skipped
    s2 = {**s, **{"dest": {"dest": "file:///y"}, "recipient": {"recipient": "age1y"}, "expect_age": {"expect_age": "v1.4.0"}}[change]}
    assert P._publish(_args(), s2) == P.EXIT_OK and len(calls) == 2            # new destination / recipient / age: published
    assert calls[-1][change] == s2[change]
    remembered = json.loads((Path(s["workdir"]) / P.LAST_PUBLISHED).read_text())
    assert remembered["dest"] == s2["dest"] and remembered["recipient_fingerprint"] == P.backup.fingerprint(s2["recipient"])
    assert "age1" not in json.dumps(remembered)                                 # the recipient itself is never stored, only its fingerprint
    assert P._publish(_args(), s2) == P.EXIT_OK and len(calls) == 2            # and the new binding is remembered


def test_a_fingerprint_remembered_without_its_binding_does_not_skip(vault, tmp_path, monkeypatch):
    s = _settings(vault, tmp_path)
    (tmp_path / "jobs").mkdir(mode=0o700)
    calls = []
    monkeypatch.setattr(P.production, "run_publish", _fake_ok(calls))
    fp = production.content_fingerprint(s["shelf_root"], s["index_path"])
    (Path(s["workdir"]) / P.LAST_PUBLISHED).write_text(json.dumps({"fingerprint": fp}))   # the format before the binding
    assert P._publish(_args(), s) == P.EXIT_OK and len(calls) == 1
