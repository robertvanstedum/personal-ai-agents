"""A direct voice session's allowance settles when it ends (core.realtime_voice.budget.settle_session): the ledger keeps an estimate from how long it ran and gives back the rest, once.

Every ledger test runs on SQLite and, when GUILD_FLOOR_TEST_DATABASE_URL names a disposable Postgres, on that Postgres too. No network, no paid call."""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from core.realtime_voice import budget as B
from core.realtime_voice.budget import REALTIME_DIRECT_KIND as DIRECT

from test_voice_budget import Clock, make_ledger

PG_URL = os.environ.get("GUILD_FLOOR_TEST_DATABASE_URL")
BACKENDS = ["sqlite"] + (["postgres"] if PG_URL else [])
SQL = Path(B.__file__).parent / "sql" / "003_voice_budget_settlement.sql"


@pytest.fixture(params=BACKENDS)
def ledger(request, tmp_path):
    dsn = f"sqlite:///{tmp_path / 'vb.db'}" if request.param == "sqlite" else PG_URL
    b = make_ledger(dsn)
    if request.param == "postgres":
        with b._tx() as run:
            run("DELETE FROM voice_budget_entry"); run("DELETE FROM voice_budget_day"); run("DELETE FROM voice_budget_topup")
    return b


def open_session(b, sid="s1", usd=5.0):
    return b.reserve(kind=DIRECT, domain="mc", principal="robert", amount_micro=B.micro(usd), entry_id=B.direct_entry_id(sid))


def end_after(b, sid="s1", **elapsed):
    b.clock.now += timedelta(**elapsed)
    return b.settle_session(B.direct_entry_id(sid))


def entry(b, sid="s1"):
    return b.rows("SELECT micro, settled_micro, settled_at, state FROM voice_budget_entry WHERE id = ?", (B.direct_entry_id(sid),))[0]


@pytest.mark.parametrize("seconds,reserved,kept", [
    (10, 5.0, 0.10), (0, 5.0, 0.10), (-30, 5.0, 0.10),            # the minimum, a clock that stepped back included
    (60, 5.0, 0.10), (61, 5.0, 0.20), (750, 5.0, 1.30),           # whole minutes, rounded up
    (3000, 5.0, 5.0), (36000, 5.0, 5.0), (120, 0.15, 0.15),       # never more than the reservation
])
def test_the_charge_is_whole_minutes_at_the_rate_with_a_minimum_and_the_reservation_as_its_cap(seconds, reserved, kept):
    assert B.realtime_charge_micro(seconds, B.micro(reserved)) == B.micro(kept)


def test_the_rate_is_a_named_estimate_above_both_reported_rates():
    assert B.REALTIME_ESTIMATE_USD_PER_MINUTE == 0.10 and B.REALTIME_ESTIMATE_MICRO_PER_MINUTE == B.micro(B.REALTIME_ESTIMATE_USD_PER_MINUTE)


def test_a_short_session_settles_small_and_the_entry_keeps_the_original(ledger):
    open_session(ledger)
    assert ledger.status()["reserved_usd"] == 5.0                  # admission-time exposure, until End
    settled = end_after(ledger, seconds=10)
    assert settled == {"day": "2026-10-13", "reserved_micro": B.micro(5), "settled_micro": B.micro(0.10), "released_micro": B.micro(4.90)}
    assert ledger.status()["reserved_usd"] == 0.10
    micro, kept, at, state = entry(ledger)
    assert (micro, kept, state) == (B.micro(5), B.micro(0.10), "reserved") and at == "2026-10-13T15:00:10+00:00"


def test_a_long_session_settles_by_its_length_and_never_above_its_reservation(ledger):
    open_session(ledger, "medium")
    open_session(ledger, "long")
    end_after(ledger, "medium", minutes=20, seconds=1)
    assert ledger.status()["reserved_usd"] == pytest.approx(2.10 + 5.0)
    end_after(ledger, "long", hours=3)
    assert ledger.status()["reserved_usd"] == pytest.approx(2.10 + 5.0)
    assert entry(ledger, "long")[1] == B.micro(5)


def test_a_session_with_no_end_keeps_its_full_reservation(ledger):
    open_session(ledger)
    ledger.clock.now += timedelta(hours=2)
    assert ledger.status()["reserved_usd"] == 5.0 and entry(ledger)[1] is None


def test_a_session_swept_at_the_maximum_counts_the_providers_maximum_whatever_the_clock_says(ledger):
    open_session(ledger, "full", usd=5.0)
    open_session(ledger, "large", usd=8.0)
    assert ledger.settle_session(B.direct_entry_id("full"), at_maximum=True)["released_micro"] == 0
    assert ledger.settle_session(B.direct_entry_id("large"), at_maximum=True)["settled_micro"] == B.micro(6.0)   # 60 minutes at $0.10
    assert ledger.status()["reserved_usd"] == 11.0


def test_a_second_end_settles_nothing_and_the_first_amount_stands(ledger):
    open_session(ledger)
    assert end_after(ledger, seconds=10) is not None
    assert end_after(ledger, minutes=30) is None
    assert ledger.status()["reserved_usd"] == 0.10 and entry(ledger)[1] == B.micro(0.10)


def test_concurrent_ends_settle_once(ledger):
    open_session(ledger)
    ledger.clock.now += timedelta(seconds=10)
    # A ledger per thread, as separate processes would be. The other session's reservation shows a double release.
    open_session(ledger, "other")
    barrier, results = threading.Barrier(8), []

    def race():
        b = make_ledger(ledger.dsn, ledger.clock, ledger.cap)
        barrier.wait()
        results.append(b.settle_session(B.direct_entry_id("s1")))
    threads = [threading.Thread(target=race) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 8 and sum(r is not None for r in results) == 1
    assert ledger.status()["reserved_usd"] == pytest.approx(5.10)


def test_only_a_direct_session_still_reserved_settles(ledger):
    other = ledger.reserve(kind="tts", domain="mc", principal="u", amount_micro=B.micro(1), entry_id="direct:tts")
    assert ledger.settle_session(other.id) is None and ledger.settle_session("direct:unknown") is None
    released = open_session(ledger)
    released.release()
    assert end_after(ledger, seconds=10) is None
    assert ledger.status()["reserved_usd"] == 1.0                  # the released session gives nothing back twice


def test_a_session_across_midnight_gives_back_to_the_day_that_held_it(ledger):
    open_session(ledger)                                           # 10:00 in Chicago
    ledger.clock.now = ledger.clock.now.replace(hour=4, minute=59) + timedelta(days=1)
    open_session(ledger, "yesterday-still-holds", usd=1.0)         # 23:59 the same Chicago day
    ledger.clock.now = ledger.clock.now.replace(hour=5, minute=1)  # 00:01 the next Chicago day
    assert ledger.settle_session(B.direct_entry_id("s1"))["day"] == "2026-10-13"
    assert ledger.status() ["reserved_usd"] == 0.0                 # the new day holds nothing
    assert ledger.rows("SELECT reserved_micro FROM voice_budget_day WHERE day = '2026-10-13'")[0][0] == B.micro(1.0 + 5.0)


def test_a_day_of_short_sessions_stays_under_the_cap_that_four_used_to_fill(ledger):
    for n in range(40):
        open_session(ledger, f"s{n}")
        end_after(ledger, f"s{n}", seconds=10)
    assert ledger.status()["reserved_usd"] == pytest.approx(4.0) and ledger.status()["remaining_usd"] == pytest.approx(16.0)


def test_open_sessions_still_hold_the_cap_and_the_refusal_says_what_counted(ledger):
    for n in range(3):
        open_session(ledger, f"s{n}")
    open_session(ledger, "small", usd=1.5)
    with pytest.raises(B.BudgetExhausted) as refused:
        open_session(ledger, "over")
    words = refused.value.public
    assert "$16.50 of $20.00 counted so far" in words and "needs $5.00 held to start" in words
    assert "estimate" in words and "not a bill" in words and "voice allowance is used up" in words      # notices.js reads this phrase
    end_after(ledger, "s0", seconds=10)
    open_session(ledger, "over")                                   # an End makes room


def test_settlement_failure_leaves_the_reservation_whole(ledger):
    open_session(ledger)
    original = ledger._tx
    import contextlib

    @contextlib.contextmanager
    def failing():
        with original() as run:
            def intercept(sql, params=()):
                if sql.startswith("UPDATE voice_budget_day SET reserved_micro = reserved_micro -"):
                    raise RuntimeError("the day could not be written")
                return run(sql, params)
            yield intercept
    ledger._tx = failing
    with pytest.raises(B.BudgetUnavailable):
        end_after(ledger, seconds=10)
    ledger._tx = original
    assert ledger.status()["reserved_usd"] == 5.0 and entry(ledger)[1] is None      # the entry's change rolled back with it
    assert ledger.settle_session(B.direct_entry_id("s1"))["settled_micro"] == B.micro(0.10)   # End retried


# ── before the migration ──
def old_ledger(tmp_path):
    """A ledger made by the code before settlement: its entry table has no settled columns."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE voice_budget_topup (approval TEXT PRIMARY KEY, day TEXT NOT NULL, micro BIGINT NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE voice_budget_day (day TEXT PRIMARY KEY, cap_micro BIGINT NOT NULL, reserved_micro BIGINT NOT NULL DEFAULT 0, created_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE voice_budget_entry (id TEXT PRIMARY KEY, day TEXT NOT NULL, at TEXT NOT NULL, domain TEXT NOT NULL, kind TEXT NOT NULL, who TEXT NOT NULL, micro BIGINT NOT NULL, state TEXT NOT NULL)")
    conn.commit()
    conn.close()
    clock = Clock()
    b = B.VoiceBudget(f"sqlite:///{path}", clock=clock, cap_usd=lambda: 20.0)
    b.clock = clock
    return b


def test_before_the_migration_admission_works_and_a_session_keeps_its_full_reservation(tmp_path):
    b = old_ledger(tmp_path)
    open_session(b)
    with pytest.raises(B.BudgetUnavailable):
        end_after(b, seconds=10)
    assert b.status()["reserved_usd"] == 5.0
    open_session(b, "next")                                        # admission never reads the new columns
    assert b.status()["reserved_usd"] == 10.0


def test_the_migration_adds_the_columns_keeps_every_row_and_can_run_twice(tmp_path):
    b = old_ledger(tmp_path)
    open_session(b)
    b.ensure_schema()
    b.ensure_schema()
    assert b.status()["reserved_usd"] == 5.0
    assert end_after(b, seconds=10)["settled_micro"] == B.micro(0.10)
    assert b.status()["reserved_usd"] == 0.10


def test_the_operator_migration_is_additive_and_names_both_columns():
    words = SQL.read_text()
    assert "ADD COLUMN IF NOT EXISTS settled_micro BIGINT" in words and "ADD COLUMN IF NOT EXISTS settled_at TEXT" in words
    statements = [line for line in words.splitlines() if line.strip() and not line.startswith("--")]
    assert len(statements) == 2 and all(line.startswith("ALTER TABLE guild.voice_budget_entry ADD COLUMN IF NOT EXISTS") for line in statements)


def test_only_the_owner_can_settle_and_the_owner_can_after_a_restart(ledger):
    """#578 S5: End is bound to the owner by the ledger row itself, not a host's in-memory table."""
    open_session(ledger, "walk1")
    ledger.clock.now += timedelta(minutes=2)
    assert ledger.settle_session(B.direct_entry_id("walk1"), principal="someone-else") is None
    out = ledger.settle_session(B.direct_entry_id("walk1"), principal="robert")
    assert out["settled_micro"] == 2 * B.REALTIME_ESTIMATE_MICRO_PER_MINUTE      # a 2-minute call keeps $0.20
    assert ledger.settle_session(B.direct_entry_id("walk1"), principal="robert") is None   # once


def test_a_call_that_never_ended_is_swept_at_the_maximum_and_only_in_its_domain(ledger):
    open_session(ledger, "old", usd=6.0)
    ledger.reserve(kind=DIRECT, domain="cos", principal="robert", amount_micro=B.micro(6.0), entry_id=B.direct_entry_id("cos-old"))
    ledger.clock.now += timedelta(minutes=30)
    open_session(ledger, "young", usd=6.0)
    assert ledger.settle_stale(domain="mc") == 0                     # nothing past the cap yet
    ledger.clock.now += timedelta(minutes=40)                        # "old" is 70 minutes, "young" 40
    assert ledger.settle_stale(domain="mc") == 1
    assert ledger.settle_session(B.direct_entry_id("old")) is None   # already settled, at the maximum
    assert ledger.settle_session(B.direct_entry_id("young"), principal="robert")["settled_micro"] == 40 * B.REALTIME_ESTIMATE_MICRO_PER_MINUTE
    assert ledger.settle_session(B.direct_entry_id("cos-old")) is not None   # CoS's row was left to her own host
