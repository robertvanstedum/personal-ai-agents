"""The standing worker's scheduler loop: one attempt per window, retries spaced, no catch-up flood."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "mc_standing"))
import schedule_loop  # noqa: E402

from core.mc_standing.spool import Spool

# 21 Oct 2026, America/Chicago is UTC-5 (CDT)
def chicago(h, m):
    return datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc) + timedelta(hours=h, minutes=m)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeCycle:
    def __init__(self, root, outcome="delivered"):
        self.root, self.outcome, self.calls = root, outcome, []

    def __call__(self, args):
        self.calls.append("ack" if "--ack-control-only" in args else "cycle")
        if "--ack-control-only" in args:
            return 0
        if self.outcome == "raise":
            raise RuntimeError("boom")
        spool = Spool(self.root)
        spool.put_state("cycles", "prod.2026-10-21.p0", {"state": self.outcome})
        return 0 if self.outcome in ("delivered", "paused") else 3


def loop(tmp_path, clock, outcome="delivered", retry_s=300):
    fake = FakeCycle(tmp_path / "state", outcome)
    return schedule_loop.Loop(["--state-root", str(tmp_path / "state")], retry_s=retry_s, cycle=fake,
                              clock=clock), fake


def test_acknowledges_every_tick_but_runs_the_cycle_only_in_the_window(tmp_path):
    clock = Clock(chicago(4, 0))
    lp, fake = loop(tmp_path, clock)
    for t in (chicago(4, 0), chicago(5, 39), chicago(9, 0), chicago(23, 0)):
        clock.t = t
        assert lp.tick()["cycle"] is None
    assert fake.calls == ["ack"] * 4
    clock.t = chicago(5, 40)
    assert lp.tick()["cycle"] == 0 and fake.calls[-1] == "cycle"


def test_once_final_no_more_attempts_that_day(tmp_path):
    clock = Clock(chicago(5, 40))
    lp, fake = loop(tmp_path, clock)
    lp.tick()
    for minute in range(41, 60):
        clock.t = chicago(5, minute)
        assert lp.tick()["cycle"] is None
    assert fake.calls.count("cycle") == 1


def test_non_final_results_retry_no_sooner_than_retry_s(tmp_path):
    clock = Clock(chicago(5, 40))
    lp, fake = loop(tmp_path, clock, outcome="delivery_pending", retry_s=300)
    lp.tick()
    clock.t = chicago(5, 44)
    assert lp.tick()["cycle"] is None
    clock.t = chicago(5, 45)
    assert lp.tick()["cycle"] == 3
    assert fake.calls.count("cycle") == 2


def test_paused_exit_zero_is_not_final_and_is_retried_after_resume(tmp_path):
    clock = Clock(chicago(5, 40))
    lp, fake = loop(tmp_path, clock, outcome="paused", retry_s=60)
    lp.tick()
    fake.outcome = "delivered"
    clock.t = chicago(5, 41)
    lp.tick()
    assert fake.calls.count("cycle") == 2 and lp.done_for == "2026-10-21"


def test_exceptions_never_stop_the_loop(tmp_path):
    clock = Clock(chicago(5, 40))
    lp, fake = loop(tmp_path, clock, outcome="raise", retry_s=60)
    assert lp.tick()["cycle"] == "error:RuntimeError"
    clock.t = chicago(5, 41)
    assert lp.tick()["cycle"] == "error:RuntimeError"


def test_a_restart_after_the_cutoff_attempts_nothing(tmp_path):
    clock = Clock(chicago(11, 0))
    lp, fake = loop(tmp_path, clock)
    for _ in range(5):
        lp.tick()
        clock.t += timedelta(minutes=1)
    assert fake.calls.count("cycle") == 0


def test_cli_requires_the_state_root(capsys):
    assert schedule_loop.main(["--once", "--", "--watchlist", "x"]) == 2
