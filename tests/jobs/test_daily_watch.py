"""The daily memory-watch wrapper: approval gate, status file, one alert on failure. Synthetic only:
a fake ``moi``, a fake approval source and a fake Telegram sender; temporary folders; no network."""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.jobs import status

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("daily_watch_under_test", REPO / "scripts" / "memory" / "daily_watch.py")
dw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dw)

T0 = datetime(2026, 10, 4, 9, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start=T0):
        self.t = start

    def __call__(self):
        self.t += timedelta(seconds=20)
        return self.t


def watch_line(source, word="ok", counts=None):
    return f"watch\t{source}\t{word}\t{json.dumps(counts if counts is not None else {'captured': 3})}\n"


class Fakes:
    """One place to script approvals, watches and the sender, and to see what was called."""

    def __init__(self, approvals=None, outputs=None, send_result="sent"):
        self.approvals = {"claude-code": "approved", "codex": "approved", "rooms": "not_configured", **(approvals or {})}
        self.outputs = outputs or {}
        self.watched, self.sent, self.send_result = [], [], send_result

    def approval(self, source):
        value = self.approvals[source]
        if isinstance(value, Exception):
            raise value
        return value

    def watch(self, source):
        self.watched.append(source)
        out = self.outputs.get(source, (0, watch_line(source)))
        if isinstance(out, Exception):
            raise out
        return out

    def sender(self, text):
        self.sent.append(text)
        if isinstance(self.send_result, Exception):
            raise self.send_result
        return self.send_result


def go(tmp_path, fakes, **kw):
    out, err = io.StringIO(), io.StringIO()
    code = dw.run(jobs_root=tmp_path / "jobs", approval=fakes.approval, watch=fakes.watch, sender=fakes.sender,
                  now=kw.pop("now", Clock()), out=out, err=err, **kw)
    doc = status.read_status(tmp_path / "jobs", "memory-watch.json", "memory-watch")
    return code, doc, out.getvalue(), err.getvalue()


# ── the happy path and the approval gate ────────────────────────────────────

def test_both_sources_approved_and_ok(tmp_path):
    f = Fakes()
    code, doc, out, _ = go(tmp_path, f)
    assert code == 0 and doc["state"] == "ok" and doc["exit_code"] == 0
    assert f.watched == ["claude-code", "codex"] and f.sent == []
    assert doc["results"] == {"claude-code": "ok", "codex": "ok"} and doc["alert"] == "not_needed"
    assert doc["summary"] == "2 of 2 sources ok"
    assert doc["last_success_at"] == doc["finished_at"] and doc["next_due_by"] == "2026-10-05T09:00:00Z"
    assert "memory-watch ok" in out


def test_an_unapproved_source_is_never_captured_and_is_not_an_error(tmp_path):
    f = Fakes(approvals={"codex": "not_approved"})
    code, doc, _, _ = go(tmp_path, f)
    assert f.watched == ["claude-code"]                    # codex was never run
    assert code == 0 and doc["state"] == "warn" and doc["results"] == {"claude-code": "ok", "codex": "not_approved"}
    assert f.sent == [] and doc["alert"] == "not_needed"


@pytest.mark.parametrize("gate,code_word", [("not_approved", "not_approved"), ("no_dry_run", "not_approved"),
                                            ("stale", "stale"), ("not_configured", "not_configured"),
                                            ("something_new", "not_approved")])
def test_any_gate_other_than_approved_blocks_capture(tmp_path, gate, code_word):
    f = Fakes(approvals={"claude-code": gate, "codex": gate})
    code, doc, _, _ = go(tmp_path, f)
    assert f.watched == [] and code == 0 and doc["state"] == "warn"
    assert set(doc["results"].values()) == {code_word}


def test_the_inbox_is_never_run_and_only_the_two_sources_are_asked(tmp_path):
    assert dw.SOURCES == ("claude-code", "codex")
    f = Fakes()
    go(tmp_path, f)
    assert "inbox" not in f.watched and set(f.approvals) == set(dw.SOURCES) | set(dw.OPTIONAL_SOURCES)


# ── warn conditions ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected", [
    (watch_line("codex", "disk_low", {}), "disk_low"),
    (watch_line("codex", "ok", {"captured": 1, "unstable": 2}), "ok_unstable"),
    (watch_line("codex", "ok", {"failed": 1}), "ok_failed_files"),
    (watch_line("codex", "ok", {"captured": 1, "possible_gap": 3}), "ok_possible_gap"),
    (watch_line("codex", "ok", {"unknown_kind": 1}), "ok_unknown_kind"),
    (watch_line("codex", "ok", {"possible_gap": 1, "unknown_kind": 2}), "ok_possible_gap"),
    (watch_line("codex", "ok", {"refused": 1, "revision_conflict": 1}), "ok_refused_files"),
    (watch_line("codex", "ok", {"excluded": 2, "unknown_participant": 1}), "ok_unknown_participant"),
    (watch_line("codex", "dry_run_only", {}), "not_approved"),
    (watch_line("codex", "not_approved", {}), "not_approved"),
])
def test_warn_outcomes(tmp_path, line, expected):
    f = Fakes(outputs={"codex": (0, line)})
    code, doc, _, _ = go(tmp_path, f)
    assert code == 0 and doc["state"] == "warn" and doc["results"]["codex"] == expected and f.sent == []


# ── failure: one message, nonzero exit, honest alert field ──────────────────

def test_a_nonzero_watch_fails_the_job_sends_one_message_and_exits_1(tmp_path):
    f = Fakes(outputs={"codex": (1, "")})
    code, doc, _, _ = go(tmp_path, f)
    assert code == 1 and doc["state"] == "failed" and doc["exit_code"] == 1
    assert doc["results"] == {"claude-code": "ok", "codex": "exit_1"} and doc["alert"] == "sent"
    assert len(f.sent) == 1
    assert "memory-watch" in f.sent[0] and "FAILED" in f.sent[0] and "codex=exit_1" in f.sent[0]
    assert doc["last_success_at"] is None                  # a failed run is not a success


def test_when_telegram_is_unavailable_the_status_says_not_sent_and_the_exit_is_still_nonzero(tmp_path):
    for result in ("not_sent", RuntimeError("down")):
        code, doc, _, _ = go(tmp_path, Fakes(outputs={"codex": (2, "")}, send_result=result))
        assert code == 1 and doc["state"] == "failed" and doc["alert"] == "not_sent"


@pytest.mark.parametrize("exc,expected", [
    (subprocess.TimeoutExpired(["moi"], 1), "timeout"),
    (RuntimeError("TOPSECRET conversation title"), "error_runtimeerror"),
])
def test_a_watch_that_raises_is_a_failure_with_a_fixed_code_and_no_message(tmp_path, exc, expected):
    f = Fakes(outputs={"claude-code": exc})
    code, doc, _, _ = go(tmp_path, f)
    assert code == 1 and doc["results"]["claude-code"] == expected
    assert "TOPSECRET" not in json.dumps(doc) + " ".join(f.sent)


@pytest.mark.parametrize("output", [(0, ""), (0, "garbage\n"), (0, "watch\tcodex\tok\tnot-json\n"),
                                    (0, "watch\tcodex\tok\t[1]\n"), (0, watch_line("codex", "exploded"))])
def test_output_it_cannot_read_is_a_failure_never_a_pass(tmp_path, output):
    code, doc, _, _ = go(tmp_path, Fakes(outputs={"codex": output}))
    assert code == 1 and doc["state"] == "failed"


def test_an_approval_check_that_cannot_run_fails_closed_and_captures_nothing(tmp_path):
    f = Fakes(approvals={"claude-code": dw.ApprovalUnavailable("ImportError")})
    code, doc, _, _ = go(tmp_path, f)
    assert f.watched == [] and code == 1 and doc["state"] == "failed"
    assert doc["results"] == {"approvals": "approval_unavailable"} and len(f.sent) == 1


def test_an_unexpected_exception_is_a_failed_run_not_a_crash(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise ValueError("TOPSECRET")
    monkeypatch.setattr(dw, "capture", boom)
    f = Fakes()
    code, doc, _, _ = go(tmp_path, f)
    assert code == 1 and doc["results"] == {"wrapper": "exception_valueerror"} and "TOPSECRET" not in str(f.sent)


# ── the status file never holds text ────────────────────────────────────────

def test_watch_output_beyond_status_and_counts_never_reaches_the_status_file_or_the_message(tmp_path):
    noisy = ("session 'Plan the secret merger' in /Users/robert/.claude/projects/x/abc.jsonl\n"
             + watch_line("codex", "ok", {"captured": 1, "note": "TOPSECRET"}))
    f = Fakes(outputs={"codex": (0, noisy), "claude-code": (1, "TOPSECRET stderr-ish")})
    _, doc, out, err = go(tmp_path, f)
    blob = (tmp_path / "jobs" / "memory-watch.json").read_text() + " ".join(f.sent) + out + err
    assert "TOPSECRET" not in blob and "secret merger" not in blob and "/Users/" not in blob


# ── the file around the run ─────────────────────────────────────────────────

def test_the_running_file_exists_while_the_watch_runs_and_last_success_is_kept(tmp_path):
    seen = {}

    class Peek(Fakes):
        def watch(self, source):
            seen[source] = status.read_status(tmp_path / "jobs", "memory-watch.json")
            return super().watch(source)

    go(tmp_path, Peek())
    assert seen["claude-code"]["state"] == "running" and seen["claude-code"]["finished_at"] is None
    _, first, _, _ = go(tmp_path, Fakes(), now=Clock(T0 + timedelta(days=1)))
    _, second, _, _ = go(tmp_path, Fakes(outputs={"codex": (1, "")}), now=Clock(T0 + timedelta(days=2)))
    assert second["state"] == "failed" and second["last_success_at"] == first["last_success_at"]


def test_if_the_status_cannot_be_written_the_wrapper_exits_2_and_still_tries_to_alert(tmp_path):
    (tmp_path / "jobs").write_text("a file where the folder should be")
    f = Fakes()
    code, _, _, err = go(tmp_path, f)
    assert code == 2 and f.watched == [] and len(f.sent) == 1 and "cannot write" in err


def test_if_the_final_write_fails_the_file_stays_running_and_the_exit_is_2(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk")
    monkeypatch.setattr(dw.status, "finish_run", boom)
    code, doc, _, _ = go(tmp_path, Fakes())
    assert code == 2 and doc["state"] == "running"         # the watchdog will report it stuck


def test_a_missing_registry_falls_back_and_warns(tmp_path):
    code, doc, _, _ = go(tmp_path, Fakes(), registry_path=tmp_path / "nope.json")
    assert code == 0 and doc["state"] == "warn" and doc["results"]["registry"] == "unreadable"
    assert doc["next_due_by"] == "2026-10-05T09:00:00Z"


def test_main_reads_the_jobs_root_from_the_environment(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(dw, "run", lambda **kw: seen.update(kw) or 0)
    monkeypatch.setenv("MINIMOI_JOBS_ROOT", str(tmp_path / "elsewhere"))
    assert dw.main() == 0 and seen["jobs_root"] == tmp_path / "elsewhere"
    monkeypatch.delenv("MINIMOI_JOBS_ROOT")
    dw.main()
    assert str(seen["jobs_root"]).endswith("minimoi-staging/data/jobs")


# ── the real seams ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("rc,out,expected", [
    (0, watch_line("claude-code"), "ok"), (3, "", "exit_3"), (-9, "", "exit_-9"),
    (0, "watch\tx\tok\n", "unparseable"),
])
def test_code_for_watch(rc, out, expected):
    assert dw.code_for_watch(rc, out) == expected


def _fake_shelf(monkeypatch, *, sources, answer="approved", config_error=None):
    import core.memory_shelf as pkg
    cfg = types.SimpleNamespace(shelf_root="/x", min_free_bytes=1, min_free_fraction=None,
                                sources={s: types.SimpleNamespace(fingerprint=lambda: "fp") for s in sources})

    def load(path=None):
        if config_error:
            raise config_error
        return cfg
    config = types.SimpleNamespace(load=load)
    approvals = types.SimpleNamespace(source_status=lambda shelf, name, fp: answer)
    shelf_mod = types.ModuleType("core.memory_shelf.shelf")
    shelf_mod.Shelf = lambda *a, **k: object()
    monkeypatch.setattr(pkg, "config", config, raising=False)
    monkeypatch.setattr(pkg, "approvals", approvals, raising=False)
    monkeypatch.setitem(sys.modules, "core.memory_shelf.shelf", shelf_mod)


def test_shelf_approval_reads_the_shelfs_own_answer(monkeypatch):
    _fake_shelf(monkeypatch, sources=["claude-code"], answer="stale")
    assert dw.shelf_approval("claude-code") == "stale"
    assert dw.shelf_approval("codex") == "not_configured"


def test_shelf_approval_that_cannot_run_raises_unavailable(monkeypatch):
    _fake_shelf(monkeypatch, sources=["codex"], config_error=ValueError("bad"))
    with pytest.raises(dw.ApprovalUnavailable):
        dw.shelf_approval("codex")
    monkeypatch.setitem(sys.modules, "core.memory_shelf.shelf", None)       # the shelf code is not importable
    with pytest.raises(dw.ApprovalUnavailable):
        dw.shelf_approval("codex")


def test_moi_watch_runs_the_moi_script_for_one_source_without_a_shell(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(argv=argv, kw=kw)
        return types.SimpleNamespace(returncode=0, stdout="watch\tcodex\tok\t{}\n")
    monkeypatch.setattr(dw.subprocess, "run", fake_run)
    monkeypatch.delenv("MOI_BIN", raising=False)
    assert dw.moi_watch("codex") == (0, "watch\tcodex\tok\t{}\n")
    assert seen["argv"] == [str(REPO / "scripts" / "moi"), "watch", "codex"]
    assert seen["kw"]["timeout"] == dw.WATCH_TIMEOUT_S and seen["kw"].get("shell") is not True
    monkeypatch.setenv("MOI_BIN", "/opt/moi")
    dw.moi_watch("codex")
    assert seen["argv"][0] == "/opt/moi"


# ── Rooms (optional source) ─────────────────────────────────────────────────

def test_an_unconfigured_rooms_source_is_silently_left_out(tmp_path):
    f = Fakes()
    code, doc, *_ = go(tmp_path, f)
    assert doc["state"] == "ok" and "rooms" not in doc["results"] and "rooms" not in f.watched


def test_a_configured_and_approved_rooms_source_runs_with_the_others(tmp_path):
    f = Fakes(approvals={"rooms": "approved"}, outputs={"rooms": (0, watch_line("rooms", "ok", {"captured": 2, "excluded": 1, "other_participant": 1}))})
    code, doc, *_ = go(tmp_path, f)
    assert doc["state"] == "ok" and doc["results"]["rooms"] == "ok" and f.watched == ["claude-code", "codex", "rooms"]


def test_a_configured_rooms_source_that_is_not_approved_warns_and_is_never_run(tmp_path):
    f = Fakes(approvals={"rooms": "not_approved"})
    code, doc, *_ = go(tmp_path, f)
    assert doc["state"] == "warn" and doc["results"]["rooms"] == "not_approved" and "rooms" not in f.watched


def test_an_ordinary_guest_exclusion_alone_is_not_a_warning(tmp_path):
    f = Fakes(approvals={"rooms": "approved"}, outputs={"rooms": (0, watch_line("rooms", "ok", {"excluded": 3, "other_participant": 3}))})
    assert go(tmp_path, f)[1]["state"] == "ok"
