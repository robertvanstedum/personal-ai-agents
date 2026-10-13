"""The shared voice budget (core.realtime_voice.budget): durable, concurrent, Chicago-day admission before any provider call. SQLite stands in for Postgres; no network, no paid call.

Every ledger test runs on SQLite and, when GUILD_FLOOR_TEST_DATABASE_URL names a disposable Postgres, on that Postgres too (the same admission SQL, real row locks)."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.realtime_voice import budget as B

REPO = Path(__file__).resolve().parents[1]
CENT = 10_000


class Clock:
    def __init__(self, iso="2026-10-13T15:00:00+00:00"):
        self.now = datetime.fromisoformat(iso)

    def __call__(self):
        return self.now


PG_URL = os.environ.get("GUILD_FLOOR_TEST_DATABASE_URL")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])


class Ledger(B.VoiceBudget):
    """A VoiceBudget plus what the tests need: its dsn, a clock and a mutable cap, and a way to read the tables on either backend."""

    def rows(self, sql, params=()):
        with self._tx() as run:
            return run(sql, params).fetchall()


def make_ledger(dsn, clock=None, cap=None):
    clock = clock or Clock()
    cap = cap if cap is not None else {"usd": 20.0}
    b = Ledger(dsn, clock=clock, cap_usd=lambda: cap["usd"])
    b.ensure_schema()
    b.dsn, b.clock, b.cap = dsn, clock, cap
    return b


@pytest.fixture(params=BACKENDS)
def ledger(request, tmp_path):
    dsn = f"sqlite:///{tmp_path / 'vb.db'}" if request.param == "sqlite" else PG_URL
    b = make_ledger(dsn)
    if request.param == "postgres":
        with b._tx() as run:
            run("DELETE FROM voice_budget_entry"); run("DELETE FROM voice_budget_day")
    b.backend = request.param
    return b


def take(b, usd, who="u1", kind="tts", domain="mc"):
    return b.reserve(kind=kind, domain=domain, principal=who, amount_micro=B.micro(usd))


# ── the day is the America/Chicago calendar day, DST included ──
@pytest.mark.parametrize("utc,day", [
    ("2026-10-13T04:59:59+00:00", "2026-10-12"), ("2026-10-13T05:00:00+00:00", "2026-10-13"),            # CDT: midnight is 05:00Z
    ("2026-12-01T05:59:59+00:00", "2026-11-30"), ("2026-12-01T06:00:00+00:00", "2026-12-01"),            # CST: midnight is 06:00Z
    ("2026-11-01T06:30:00+00:00", "2026-11-01"), ("2026-11-01T07:30:00+00:00", "2026-11-01"),            # the repeated 01:30 on the 25-hour day is one day
    ("2026-11-02T05:59:59+00:00", "2026-11-01"), ("2026-11-02T06:00:00+00:00", "2026-11-02"),
    ("2026-03-09T04:59:59+00:00", "2026-03-08"), ("2026-03-09T05:00:00+00:00", "2026-03-09"),            # the 23-hour spring day
])
def test_the_day_is_the_chicago_calendar_day(utc, day):
    assert B.chicago_day(datetime.fromisoformat(utc)) == day


# ── admission ──
def test_admits_up_to_the_cap_exactly_then_refuses_with_an_honest_message(ledger):
    for _ in range(100):
        take(ledger, 0.20)
    s = ledger.status()
    assert s["reserved_usd"] == 20.0 and s["remaining_usd"] == 0.0 and s["cap_usd"] == 20.0 and s["day"] == "2026-10-13"
    with pytest.raises(B.BudgetExhausted) as e:
        take(ledger, 0.01)
    assert e.value.status == 429
    assert "reserved allowance" in e.value.public and "not a bill" in e.value.public and "midnight Chicago time" in e.value.public and "$20.00" in e.value.public
    assert ledger.status()["reserved_usd"] == 20.0                      # a refused ask reserves nothing


def test_a_refusal_runs_no_provider_call(ledger):
    for _ in range(66):
        take(ledger, 0.30)
    called = []
    with pytest.raises(B.BudgetExhausted):
        with B.admit(kind="tts", domain="mc", principal="u1", amount_micro=B.micro(1.0), required=True, budget=ledger):
            called.append("provider")
    assert called == []


def test_concurrent_threads_never_pass_the_same_last_dollar(ledger):
    admitted, refused, errors = [], [], []
    start = threading.Barrier(40)

    def worker():
        start.wait()
        for _ in range(3):
            try:
                take(ledger, 0.30, who=threading.current_thread().name)
                admitted.append(1)
            except B.BudgetExhausted:
                refused.append(1)
            except Exception as exc:                                    # pragma: no cover - would be a real failure
                errors.append(exc)
    threads = [threading.Thread(target=worker, name=f"t{i}") for i in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(admitted) == 66 and len(refused) == 120 - 66           # 66 x $0.30 = $19.80; the 67th would be $20.10
    assert ledger.status()["reserved_usd"] == 19.8
    assert [tuple(map(int, r)) for r in ledger.rows("SELECT count(*), sum(micro) FROM voice_budget_entry WHERE state='reserved'")] == [(66, 66 * B.micro(0.30))]


def test_concurrent_processes_share_one_ledger(ledger, tmp_path):
    code = ("import sys; from datetime import datetime; from core.realtime_voice import budget as B\n"
            "b = B.VoiceBudget(sys.argv[1], clock=lambda: datetime.fromisoformat('2026-10-13T15:00:00+00:00'), cap_usd=lambda: 20.0)\n"
            "n = 0\n"
            "for _ in range(25):\n"
            "    try: b.reserve(kind='tts', domain='cos', principal='p', amount_micro=B.micro(0.30)); n += 1\n"
            "    except B.BudgetExhausted: pass\n"
            "print(n)")
    env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONDONTWRITEBYTECODE": "1"}
    procs = [subprocess.Popen([sys.executable, "-c", code, ledger.dsn], stdout=subprocess.PIPE, text=True, env=env, cwd=REPO) for _ in range(6)]
    total = sum(int(p.communicate(timeout=120)[0].strip()) for p in procs)
    assert total == 66 and ledger.status()["reserved_usd"] == 19.8


def test_the_count_survives_a_restart(ledger):
    take(ledger, 12.0)
    fresh = B.VoiceBudget(ledger.dsn, clock=ledger.clock, cap_usd=lambda: 20.0)          # a new process: nothing in memory
    assert fresh.status()["reserved_usd"] == 12.0
    take(fresh, 8.0)
    with pytest.raises(B.BudgetExhausted):
        take(fresh, 0.01)


def test_midnight_chicago_starts_a_fresh_day_and_the_old_day_is_kept(ledger):
    ledger.clock.now = datetime.fromisoformat("2026-10-13T04:59:30+00:00")                              # 23:59:30 CDT on the 12th
    take(ledger, 20.0)
    with pytest.raises(B.BudgetExhausted):
        take(ledger, 0.01)
    ledger.clock.now += timedelta(seconds=31)                                                          # 00:00:01 CDT on the 13th
    take(ledger, 0.30)
    assert ledger.status()["day"] == "2026-10-13" and ledger.status()["reserved_usd"] == 0.30
    ledger.clock.now = datetime.fromisoformat("2026-10-13T04:00:00+00:00")
    assert ledger.status()["day"] == "2026-10-12" and ledger.status()["reserved_usd"] == 20.0


def test_lowering_the_cap_applies_at_once_and_raising_it_waits_for_the_next_day(ledger):
    take(ledger, 5.0)
    ledger.cap["usd"] = 6.0
    with pytest.raises(B.BudgetExhausted):
        take(ledger, 1.5)
    take(ledger, 1.0)
    ledger.cap["usd"] = 50.0                                                                            # raised mid-day: the day stays at the lowered $6 (it is written to the day's row)
    with pytest.raises(B.BudgetExhausted):
        take(ledger, 0.01)
    ledger.clock.now += timedelta(days=1)
    take(ledger, 30.0)                                                                                  # the next day uses the new cap


# ── fail closed ──
@pytest.mark.parametrize("dsn", ["sqlite:////nonexistent-dir-for-voice-budget/x.db", None])
def test_an_unreadable_ledger_refuses_and_the_provider_is_not_contacted(dsn, monkeypatch, tmp_path):
    monkeypatch.delenv(B.ENV_DSN, raising=False)
    monkeypatch.setattr(B, "default_dsn", lambda environ=None: None)
    b = B.VoiceBudget(dsn, clock=Clock(), cap_usd=lambda: 20.0)
    called = []
    with pytest.raises(B.BudgetUnavailable) as e:
        with B.admit(kind="tts", domain="mc", principal="u", amount_micro=1000, required=True, budget=b):
            called.append("provider")
    assert called == [] and e.value.status == 503 and "nothing was sent to the speech provider" in e.value.public


def test_a_ledger_without_its_tables_refuses_rather_than_creating_them_on_a_request(tmp_path):
    b = B.VoiceBudget(f"sqlite:///{tmp_path / 'empty.db'}", clock=Clock(), cap_usd=lambda: 20.0)
    with pytest.raises(B.BudgetUnavailable):
        take(b, 0.30)
    assert not any(r[0].startswith("voice_budget") for r in sqlite3.connect(tmp_path / "empty.db").execute("SELECT name FROM sqlite_master"))


# ── no rebate from the client, no retry, release only for a definite rejection ──
class ProviderError(Exception):
    def __init__(self, status_code=None):
        super().__init__("provider")
        self.status_code = status_code


@pytest.mark.parametrize("status,kept", [(401, False), (429, False), (400, False), (500, True), (502, True), (503, True), (None, True)])
def test_only_a_definite_4xx_rejection_gives_the_allowance_back_and_nothing_retries(ledger, status, kept):
    calls = []
    with pytest.raises(ProviderError):
        with B.admit(kind="tts", domain="mc", principal="u", amount_micro=B.micro(1.0), required=True, budget=ledger):
            calls.append(1)
            raise ProviderError(status)
    assert calls == [1]                                                                                  # one attempt, never a second
    assert ledger.status()["reserved_usd"] == (1.0 if kept else 0.0)


def test_a_timeout_or_a_client_that_hangs_up_keeps_the_allowance(ledger):
    import requests
    with pytest.raises(requests.Timeout):
        with B.admit(kind="tts", domain="mc", principal="u", amount_micro=B.micro(1.0), required=True, budget=ledger):
            raise requests.Timeout("slow")
    with pytest.raises(GeneratorExit):
        with B.admit(kind="tts", domain="mc", principal="u", amount_micro=B.micro(1.0), required=True, budget=ledger):
            raise GeneratorExit()
    assert ledger.status()["reserved_usd"] == 2.0


def test_nothing_a_browser_can_reach_releases_or_reports_usage():
    from minimoi_portal.guild_ui.api import RULES
    reachable = [(r[0], r[3]) for r in RULES if "voice" in r[0] or "dictation" in r[0] or "budget" in r[0]]
    assert reachable
    for rule, methods in reachable:
        assert "release" not in rule and "rebate" not in rule and "usage" not in rule and "report" not in rule
    assert not [r for r in RULES if "budget" in r[0]]
    assert not hasattr(B, "release") and "release" in dir(B.Reservation)                                # server-side Reservation.release only


# ── privacy: the ledger holds numbers and a hash, nothing else ──
def test_the_ledger_stores_no_text_audio_or_raw_identity(ledger):
    take(ledger, 0.30, who="robert@example.com")
    cols = [r[0] for r in ledger.rows("SELECT column_name FROM information_schema.columns WHERE table_name = 'voice_budget_entry' ORDER BY ordinal_position")] \
        if ledger.backend == "postgres" else [r[1] for r in ledger.rows("PRAGMA table_info(voice_budget_entry)")]
    assert cols == ["id", "day", "at", "domain", "kind", "who", "micro", "state"]
    dump = json.dumps([list(map(str, r)) for r in ledger.rows("SELECT * FROM voice_budget_entry")])
    assert "robert" not in dump and "example.com" not in dump
    assert ledger.rows("SELECT who FROM voice_budget_entry")[0][0] != "robert@example.com"


# ── who is gated ──
def test_a_caller_that_is_not_required_is_unchanged_until_enforcement_is_switched_on(ledger):
    broken = B.VoiceBudget("sqlite:////nonexistent-dir-for-voice-budget/x.db", clock=Clock(), cap_usd=lambda: 20.0)
    ran = []
    with B.admit(kind="tts", domain="cos", principal="u", amount_micro=1000, required=False, budget=broken, environ={}):
        ran.append(1)
    assert ran == [1]                                                                                   # not enforced: no ledger touched, even a broken one
    with pytest.raises(B.BudgetUnavailable):
        with B.admit(kind="tts", domain="cos", principal="u", amount_micro=1000, required=False, budget=broken, environ={B.ENV_ENFORCE: "1"}):
            ran.append(2)
    assert ran == [1]
    with B.admit(kind="tts", domain="cos", principal="u", amount_micro=B.micro(1), required=False, budget=ledger, environ={B.ENV_ENFORCE: "1"}):
        pass
    assert ledger.status()["reserved_usd"] == 1.0


def test_a_session_reserves_the_providers_own_maximum_not_the_page_timer():
    assert B.REALTIME_SESSION_MAX_MINUTES == 60
    assert B.session_allowance_micro() == B.micro(1.02)                                  # 60 min x the published $0.017/min live-transcription rate, no safety multiple
    assert B.session_allowance_micro(15) == B.micro(0.255)                               # only if a caller states a shorter, enforced length


def test_speech_and_upload_allowances_are_estimates_and_say_so():
    assert B.tts_allowance_micro("x" * 3500) == B.micro(0.175) and B.tts_allowance_micro("hi") == B.micro(0.01)
    # a clip's duration cannot be read from its size: assume a very low bitrate (750 B/s) and say it is an estimate
    assert B.upload_allowance_micro(10) == B.micro(0.02) and B.upload_allowance_micro(1024 * 1024) == B.micro(0.144) and B.upload_allowance_micro(B.UPLOAD_MAX_BYTES) == B.micro(0.702)
    assert "not a bill" in B.LABEL and "reserved allowance" in B.LABEL and "not a guaranteed spending limit" in B.LABEL


def test_a_lowered_cap_is_written_to_the_day_so_a_second_service_cannot_exceed_it(ledger):
    other_cap = {"usd": 20.0}
    second = make_ledger(ledger.dsn, clock=ledger.clock, cap=other_cap)                  # another service/container with its own idea of the cap
    take(ledger, 3.0)
    ledger.cap["usd"] = 10.0                                                             # the first service has been lowered to $10
    take(ledger, 3.0)                                                                    # ...and writes that to the day's row
    take(second, 4.0)                                                                    # second still believes $20, but the day now says $10: 6 + 4 = 10 fits exactly
    with pytest.raises(B.BudgetExhausted) as e:
        take(second, 0.01)
    assert e.value.cap == B.micro(10.0)
    assert ledger.status()["reserved_usd"] == 10.0 and second.status()["cap_usd"] == 10.0


def test_the_cap_comes_from_the_limits_registry_bounded_and_never_none(tmp_path):
    (tmp_path / "limits.json").write_text('{"version": 1, "values": {"voice.daily_cap_usd": 5}}')
    assert B.effective_cap_usd({"MINIMOI_LIMITS_DIR": str(tmp_path)}) == 5.0
    (tmp_path / "limits.json").write_text('{"version": 1, "values": {"voice.daily_cap_usd": 5000}}')
    assert B.effective_cap_usd({"MINIMOI_LIMITS_DIR": str(tmp_path)}) == 50.0                           # hard maximum
    (tmp_path / "limits.json").write_text("{broken")
    assert B.effective_cap_usd({"MINIMOI_LIMITS_DIR": str(tmp_path)}) == 20.0                           # damaged file is the default, never 'no cap'
    assert B.effective_cap_usd({"MINIMOI_LIMITS_DIR": str(tmp_path / "missing")}) == 20.0


def test_a_lowered_cap_persists_even_when_the_lowering_request_is_refused(ledger):
    second=make_ledger(ledger.dsn,ledger.clock,{"usd":20.0})
    take(ledger,8.0)
    ledger.cap["usd"]=5.0
    with pytest.raises(B.BudgetExhausted):
        take(ledger,1.0)
    assert second.status()["cap_usd"]==5.0
    with pytest.raises(B.BudgetExhausted):
        take(second,1.0)
    assert second.status()["reserved_usd"]==8.0


def test_postgres_admission_uses_a_cap_lowered_after_the_call_started(ledger):
    if ledger.backend!='postgres':
        pytest.skip('Requires independent PostgreSQL transactions')
    import contextlib
    take(ledger,8.0)
    low=make_ledger(ledger.dsn,ledger.clock,{"usd":10.0})
    at_admission=threading.Event()
    resume=threading.Event()
    original=ledger._tx
    @contextlib.contextmanager
    def paused_tx():
        with original() as run:
            def intercept(sql,params=()):
                if sql.startswith('UPDATE voice_budget_day SET reserved_micro = reserved_micro +'):
                    at_admission.set()
                    assert resume.wait(3)
                return run(sql,params)
            yield intercept
    ledger._tx=paused_tx
    result=[]
    def high_call():
        try:
            take(ledger,4.0)
            result.append('admitted')
        except B.BudgetExhausted:
            result.append('refused')
        except Exception as exc:
            result.append(type(exc).__name__)
    thread=threading.Thread(target=high_call)
    thread.start()
    try:
        assert at_admission.wait(3)
        take(low,1.0)
    finally:
        resume.set()
        thread.join(4)
    assert not thread.is_alive() and result==['refused']
    assert low.status()['reserved_usd']==9.0 and low.status()['cap_usd']==10.0
