"""End settles a direct voice session's allowance on the server (core.realtime_voice.direct_host, budget.settle_session). The fake adapter of test_direct_host.py with the real routes,
bootstrap and budget (SQLite), whose clock each test moves. A $2 reservation (ENV). No provider, no Keychain, no microphone, no network."""
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from core.realtime_voice import budget as B
from core.realtime_voice import direct_host as core
from core.realtime_voice.direct_host import install_direct_voice

from test_direct_host import BASE, ENV, OWNER, rig, start  # noqa: F401


@pytest.fixture
def r(rig):  # noqa: F811
    rig.now = [datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)]
    rig.budget._clock = lambda: rig.now[0]
    return rig


def later(r, **elapsed):
    r.now[0] += timedelta(**elapsed)


def end(r, sid, client=None, headers=OWNER, **body):
    return (client or r.client).post(BASE + f"/session/{sid}/end", headers=headers, json=body)


def reserved(r):
    return r.budget.status()["reserved_usd"]


def settled(r, sid):
    with r.budget._tx() as run:
        return run("SELECT micro, settled_micro FROM voice_budget_entry WHERE id = ?", (B.direct_entry_id(sid),)).fetchone()


def test_the_session_reserves_at_start_and_a_short_one_settles_small_at_end(r):
    sid = start(r)
    assert reserved(r) == 2.0
    later(r, seconds=10)
    assert end(r, sid).status_code == 200
    assert reserved(r) == 0.10 and settled(r, sid) == (B.micro(2), B.micro(0.10))


def test_a_long_session_settles_by_its_length_capped_at_its_reservation(r):
    medium, long_ = start(r), start(r)
    later(r, minutes=12, seconds=30)
    end(r, medium)
    assert reserved(r) == pytest.approx(1.30 + 2.0)
    later(r, hours=2)
    end(r, long_)
    assert settled(r, long_) == (B.micro(2), B.micro(2)) and reserved(r) == pytest.approx(3.30)


def test_a_session_with_no_end_keeps_its_full_reservation(r):
    sid = start(r)
    later(r, minutes=5)
    assert reserved(r) == 2.0 and settled(r, sid)[1] is None


def test_two_ends_settle_once(r):
    sid = start(r)
    later(r, seconds=10)
    assert end(r, sid).status_code == 200
    later(r, minutes=15)
    assert end(r, sid).status_code == 200
    assert reserved(r) == 0.10


def test_nothing_the_client_sends_names_an_amount_or_a_time(r):
    sid = start(r)
    later(r, minutes=3)
    forged = {"amount": 0, "amount_micro": 1, "settled_micro": 1, "micro": 1, "elapsed": 1, "elapsed_seconds": 1, "started": "2026-10-13T15:02:59+00:00",
              "ended": "2026-10-13T15:00:01+00:00", "minutes": 0}
    assert end(r, sid, **forged).status_code == 200
    assert settled(r, sid) == (B.micro(2), B.micro(0.30))


def test_an_end_by_someone_else_settles_nothing(r):
    sid = start(r)
    later(r, seconds=10)
    assert end(r, sid, headers={**OWNER, "X-Owner": "mallory"}).status_code == 403
    assert reserved(r) == 2.0 and settled(r, sid)[1] is None


def test_a_failure_to_settle_leaves_the_reservation_and_never_breaks_end(r, monkeypatch):
    sid = start(r)

    def down(*_a, **_k):
        raise B.BudgetUnavailable("ledger down")
    monkeypatch.setattr(r.budget, "settle_session", down)
    answer = end(r, sid)
    assert answer.status_code == 200 and answer.get_json()["ended"] is True
    assert (sid, "end") in r.store.markers
    monkeypatch.undo()
    assert reserved(r) == 2.0 and settled(r, sid)[1] is None


def test_end_after_a_restart_settles_from_the_durable_entry(r):
    sid = start(r)
    later(r, seconds=90)
    app = Flask("restarted")
    install_direct_voice(app, r.host, url_prefix="/tp/voice", environ=dict(ENV), budget=r.budget, clock=lambda: r.clock[0], summary_http=r.gateway)
    client = app.test_client()
    assert end(r, sid, client=client, conversation_id="owner").status_code == 200
    assert settled(r, sid)[1] == B.micro(0.20)
    assert end(r, sid, client=client, conversation_id="owner").status_code == 200 and reserved(r) == 0.20


def test_a_summary_that_ends_the_session_settles_it(r):
    sid = start(r)
    later(r, seconds=30)
    r.client.post(BASE + f"/session/{sid}/summary", headers=OWNER, json={"conversation_id": "owner"})
    assert settled(r, sid)[1] == B.micro(0.10)


def test_the_sweep_settles_a_session_with_no_end_at_the_maximum_and_leaves_an_ended_one(r):
    dropped, ended = start(r), start(r)
    later(r, seconds=10)
    end(r, ended)
    r.clock[0] += 31 * 60 + core.PRUNE_AFTER_S + 60                # past the 30-minute session and the prune window
    later(r, hours=2)
    start(r)                                                       # a new session lets go of long-ended ones
    assert settled(r, dropped) == (B.micro(2), B.micro(2))         # 60 minutes at $0.10 is more than this reservation
    assert settled(r, ended)[1] == B.micro(0.10)


def test_a_sweep_that_cannot_settle_does_not_stop_a_new_session(r, monkeypatch):
    dropped = start(r)
    r.clock[0] += 31 * 60 + core.PRUNE_AFTER_S + 60

    def down(*_a, **_k):
        raise B.BudgetUnavailable("ledger down")
    monkeypatch.setattr(r.budget, "settle_session", down)
    start(r)
    assert settled(r, dropped)[1] is None


def test_a_session_that_never_started_is_not_settled(r, monkeypatch):
    """The mint failed without a definite rejection: the reservation stays (it may have been billed), with no End to settle it."""
    def fail(**_kw):
        raise core_error()

    def core_error():
        from core.realtime_voice.providers.openai_realtime import OpenAIRealtimeError
        return OpenAIRealtimeError("timed out")
    monkeypatch.setattr("core.realtime_voice.bootstrap.openai_realtime.mint_ephemeral_credential", fail)
    answer = r.client.post(BASE + "/bootstrap", headers=OWNER, json={"persona": "test_person", "provider": "openai", "conversation_id": "owner"})
    assert answer.status_code == 502 and reserved(r) == 2.0


def test_a_day_of_short_sessions_through_the_routes_does_not_hit_the_cap(r):
    import core.realtime_voice.bootstrap as boot
    for _ in range(15):                                            # at $2 a session, the old count refused the eleventh
        boot._rate_limit_state.clear()
        sid = start(r)
        later(r, seconds=10)
        assert end(r, sid).status_code == 200
    assert reserved(r) == pytest.approx(1.50)
