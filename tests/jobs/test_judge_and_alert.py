"""The contract's rules (judge) and the one-message Telegram helper (alert)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.jobs import alert, judge, registry, status

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
JOB = registry.load().get("memory-watch")          # expected 24 h, late after 26 h, missed after 50 h, stuck after 2 h
H = timedelta(hours=1)


def doc(state="ok", *, started=NOW - 8 * H, finished="auto", success="auto", run="r1"):
    fin = (started + timedelta(minutes=1)) if finished == "auto" and state != "running" else (None if finished == "auto" else finished)
    suc = (fin if state in ("ok", "warn") else None) if success == "auto" else success
    suc_text = suc if isinstance(suc, str) or suc is None else status.iso(suc)
    return {"schema_version": 1, "job_id": "memory-watch", "host": "mac", "run_id": run, "state": state,
            "started_at": status.iso(started), "finished_at": status.iso(fin), "exit_code": None if state == "running" else 0,
            "summary": "", "results": {}, "next_due_by": None, "last_success_at": suc_text, "alert": None}


def v(d, now=NOW, job=JOB):
    return judge.judge(job, d, now)


# ── a readable file ─────────────────────────────────────────────────────────

def test_ok_within_the_window_is_green():
    assert v(doc("ok")) == judge.Verdict("green", "ok", "r1")


def test_warn_is_yellow():
    assert v(doc("warn")).state == "yellow" and v(doc("warn")).code == "warn"


def test_failed_is_red_even_with_a_recent_success():
    d = doc("failed", success=status.iso(NOW - H))
    assert v(d) == judge.Verdict("red", "failed", "r1")


def test_late_but_not_missed_is_yellow_and_missed_is_red():
    assert v(doc("ok", started=NOW - 27 * H)).code == "late" and v(doc("ok", started=NOW - 27 * H)).state == "yellow"
    assert v(doc("ok", started=NOW - 25 * H)).state == "green"                                  # inside expected + grace
    assert v(doc("ok", started=NOW - 51 * H)).state == "red" and v(doc("ok", started=NOW - 51 * H)).code == "missed"
    assert v(doc("ok", started=NOW - 49 * H)).state == "yellow"                                 # not yet missed_after (50 h)


def test_missed_key_is_the_last_success_so_it_alerts_once_per_gap():
    d = doc("ok", started=NOW - 60 * H)
    assert v(d).key == d["last_success_at"]


def test_running_past_stuck_after_is_red_and_within_it_follows_the_last_success():
    d = doc("running", started=NOW - 3 * H, success=status.iso(NOW - 10 * H))
    assert v(d) == judge.Verdict("red", "stuck", "r1")
    d = doc("running", started=NOW - H, success=status.iso(NOW - 10 * H))
    assert v(d).state == "green"
    d = doc("running", started=NOW - H, success=status.iso(NOW - 30 * H))
    assert v(d).state == "yellow" and v(d).code == "late"
    d = doc("running", started=NOW - H, success=status.iso(NOW - 60 * H))
    assert v(d).state == "red" and v(d).code == "missed"


def test_stuck_exactly_at_the_limit_is_not_yet_stuck():
    d = doc("running", started=NOW - timedelta(seconds=JOB.stuck_after_s), success=status.iso(NOW - H))
    assert v(d).state == "green"
    d = doc("running", started=NOW - timedelta(seconds=JOB.stuck_after_s + 1), success=status.iso(NOW - H))
    assert v(d).code == "stuck"


def test_first_run_in_progress_is_yellow_never_green_and_a_run_that_never_succeeded_is_red():
    assert v(doc("running", started=NOW - H, success=None)) == judge.Verdict("yellow", "first_run", "r1")
    assert v(doc("running", started=NOW - 3 * H, success=None)).code == "stuck"
    assert v(doc("failed", success=None)).code == "failed"
    odd = doc("ok", success=None)
    assert v(odd) == judge.Verdict("red", "no_success", "r1")


@pytest.mark.parametrize("field", ["started_at", "finished_at", "last_success_at"])
def test_a_time_in_the_future_is_unknown_never_green(field):
    d = doc("ok")
    d[field] = status.iso(NOW + timedelta(minutes=5))
    assert v(d) == judge.Verdict("unknown", "skew", "r1")
    d[field] = status.iso(NOW + timedelta(seconds=30))               # inside the 60 s tolerance
    assert v(d).state != "unknown"


# ── no readable file ────────────────────────────────────────────────────────

def test_a_missing_file_is_red_only_once_a_run_was_due():
    first_due = JOB.active_from + timedelta(seconds=JOB.overdue_after_s)
    assert v(None, now=first_due + timedelta(seconds=1)) == judge.Verdict("red", "never_ran", "none")
    assert v(None, now=first_due - timedelta(seconds=1)) == judge.Verdict("unknown", "not_due", "none")
    assert v(None, now=JOB.active_from - 10 * H).state == "unknown"


def test_a_missing_file_with_no_active_from_is_unknown_not_green():
    bare = registry.parse({"schema_version": 1, "jobs": [{
        "id": "x", "name": "x", "host": "mac", "schedule": {"text": "t", "kind": "interval", "every_s": 60},
        "expected_every_s": 60, "missed_after_s": 120, "stuck_after_s": 30, "status_file": "x.json",
        "owner": "o", "runbook": "r"}]}).jobs[0]
    assert judge.judge(bare, None, NOW).state == "unknown"


def test_summary_precedence_is_unknown_red_yellow_green():
    assert judge.worst(["green", "yellow", "red", "unknown"]) == "unknown"
    assert judge.worst(["green", "yellow", "red"]) == "red"
    assert judge.worst(["green", "yellow"]) == "yellow"
    assert judge.worst(["green"]) == "green"
    assert judge.worst([]) == "unknown"


# ── Telegram helper ─────────────────────────────────────────────────────────

def test_sends_with_keychain_credentials_and_reports_sent():
    seen = []
    res = alert.send("hello", env={}, keychain={"bot_token": "TOK", "chat_id": "42"}.get,
                     post=lambda url, payload, timeout: seen.append((url, payload)) or 200)
    assert res == "sent" and seen[0][0].endswith("/botTOK/sendMessage") and seen[0][1]["chat_id"] == "42"


def test_falls_back_to_the_environment():
    seen = []
    res = alert.send("hi", env={"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "7"}, keychain=lambda a: None,
                     post=lambda *a: seen.append(a) or 200)
    assert res == "sent" and len(seen) == 1


@pytest.mark.parametrize("env,keys", [({}, {}), ({"TELEGRAM_BOT_TOKEN": "T"}, {}), ({}, {"bot_token": "T"})])
def test_no_credentials_is_not_sent_and_nothing_is_posted(env, keys):
    called = []
    assert alert.send("x", env=env, keychain=keys.get, post=lambda *a: called.append(a) or 200) == "not_sent"
    assert called == []


def test_http_error_or_exception_is_not_sent_and_the_token_never_escapes(capsys):
    def boom(url, payload, timeout):
        raise OSError(f"cannot reach {url}")
    assert alert.send("x", env={}, keychain={"bot_token": "SECRETTOK", "chat_id": "1"}.get, post=boom) == "not_sent"
    assert alert.send("x", env={}, keychain={"bot_token": "SECRETTOK", "chat_id": "1"}.get,
                      post=lambda *a: 500) == "not_sent"
    out = capsys.readouterr()
    assert "SECRETTOK" not in out.out + out.err


def test_text_outside_the_narrow_character_set_is_dropped_before_sending():
    seen = []
    alert.send("job memory-watch failed: codex=exit_1 /Users/robert/secret.md \"quoted title\" <b>",
               env={}, keychain={"bot_token": "T", "chat_id": "1"}.get, post=lambda u, p, t: seen.append(p) or 200)
    text = seen[0]["text"]
    assert "/" not in text and '"' not in text and "<" not in text and "codex=exit_1" in text
    assert len(alert.clean("a" * 1000)) == alert.MAX_CHARS
