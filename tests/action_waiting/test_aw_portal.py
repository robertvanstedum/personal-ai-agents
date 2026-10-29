"""C4, the portal wiring (spec v0.2 §3.5): T13 switches and suppression, T22 the no-change proof with the switch off,
and review B1 (the verify path is recorded only when the guild write is confirmed)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from core.supervision.telegram import Bot
from minimoi_portal import action_waiting as notices, auth
from minimoi_portal.action_waiting import sources as src, store as st
from minimoi_portal.action_waiting.service import Service
from aw_fakes import FAKE_TOKEN, FakeGuildDB, FakeTelegram
from aw_fakes import auth_dir, clock, legacy, make_settings, telegram, token_file

PYTEST_FIXTURES = (auth_dir, clock, legacy, make_settings, telegram, token_file,)                  # used by name in the tests below

PASSWORD = "Minimoi2026"        # the local test-account convention


@pytest.fixture
def portal(portal_client, auth_dir, monkeypatch):
    import minimoi_portal.app as app_mod
    calls = {"new": [], "reset": [], "sql": []}
    rows = {"value": [(41, datetime(2026, 10, 29, 19, 2, tzinfo=timezone.utc))]}

    def db_execute(sql, params=None, *, returning=False):
        calls["sql"].append(sql)
        if isinstance(rows["value"], Exception):
            raise rows["value"]
        return rows["value"] if returning else None
    monkeypatch.setattr(app_mod, "_notify_telegram_new_request", lambda *a, **k: calls["new"].append((a, k)))
    monkeypatch.setattr(app_mod, "_notify_telegram_reset_request", lambda *a, **k: calls["reset"].append((a, k)))
    monkeypatch.setattr(app_mod, "_guild_db_execute", db_execute)
    monkeypatch.setattr(notices, "_SERVICE", None)
    (auth_dir / "users.json").write_text(json.dumps({"users": [
        {"username": "robert", "display_name": "Robert Test", "email": "robert@x.test", "tier": "owner",
         "password_hash": "x"}]}))

    class P:
        pass
    p = P()
    p.client, p.calls, p.rows, p.app = portal_client, calls, rows, app_mod
    return p


def _register(p, email="ann@x.test"):
    return p.client.post("/register", data={"display_name": "Ann Guest", "email": email, "password": PASSWORD})


def _verify(p, email="cy@x.test"):
    token = auth.create_verification(email, "Cy Guest", "learning", "portuguese")
    return p.client.get(f"/guest/verify/{token}")


def _forgot(p):
    return p.client.post("/forgot-password", data={"email": "robert@x.test"})


def _service(make_settings, clock, telegram, legacy, **over):
    settings = make_settings(**over)
    store = st.Store(settings.root, clock=clock)
    sources = src.Sources(settings, store.digest, guild_db=FakeGuildDB(), clock=clock)
    return Service(settings, store=store, sources=sources, opener=telegram, clock=clock,
                   legacy_bot=lambda: Bot(FAKE_TOKEN, opener=legacy), source_timeout=5)


@pytest.fixture
def on(portal, make_settings, clock, telegram, legacy, monkeypatch):
    """The service on and healthy: configured, with a fresh sweep heartbeat."""
    svc = _service(make_settings, clock, telegram, legacy)
    svc.sweep()
    assert svc.health() == {"healthy": True, "reasons": []}
    monkeypatch.setattr(notices, "_SERVICE", svc)
    yield svc
    svc.sender.release()


# ---- T22: off changes nothing ------------------------------------------------------------------------------------

def test_off_register_verify_and_reset_call_todays_sends_unchanged(portal, tmp_path):
    """T22: with the switch off the three create paths call today's functions with today's arguments."""
    assert _register(portal).status_code == 200
    assert _verify(portal).status_code == 200
    assert _forgot(portal).status_code == 200
    assert portal.calls["new"] == [(("Ann Guest", "ann@x.test"), {}),
                                   (("Cy Guest", "cy@x.test"), {"domain": "portuguese"})]
    assert portal.calls["reset"] == [(("Robert Test", "robert@x.test"), {})]
    assert not (tmp_path / "notices").exists()               # no folder access
    assert notices.current() is None


def test_off_survives_the_service_submodule_being_imported(portal):
    import minimoi_portal.action_waiting.service  # noqa: F401  (the name that once shadowed the accessor)
    assert callable(notices.current) and notices.current() is None
    assert _forgot(portal).status_code == 200 and len(portal.calls["reset"]) == 1


def test_off_install_returns_none_and_attaches_nothing():
    from flask import Flask
    app = Flask(__name__)
    assert notices.install(app, {"MINIMOI_ACTION_NOTICES": "off", "MINIMOI_ACTION_NOTICES_AGENT_TOKENS":
                                 "codex:" + "a" * 64}, base_url="https://dev.minimoi.test") is None
    assert not any(r.rule.startswith("/internal/notices") for r in app.url_map.iter_rules())
    assert notices.install(app, {"MINIMOI_ACTION_NOTICES": "bogus"}, base_url="x") is None   # unknown: off


def test_off_even_when_todays_send_fails_nothing_new_happens(portal, monkeypatch):
    portal.rows["value"] = RuntimeError("db down")
    assert _register(portal).status_code == 200
    assert portal.calls["new"] == [(("Ann Guest", "ann@x.test"), {})]


# ---- B1: the guild write is confirmed by RETURNING ---------------------------------------------------------------

def test_log_guest_request_returns_the_row_only_when_one_came_back(portal):
    row = portal.app._log_guest_request("Ann", "ann@x.test", "r")
    assert row == {"id": 41, "requested_at": datetime(2026, 10, 29, 19, 2, tzinfo=timezone.utc)}
    assert "RETURNING id, requested_at" in portal.calls["sql"][-1]
    portal.rows["value"] = []                                 # the email's row is requested or granted already
    assert portal.app._log_guest_request("Ann", "ann@x.test", "r") is None
    portal.rows["value"] = RuntimeError("db down")
    assert portal.app._log_guest_request("Ann", "ann@x.test", "r") is None


# ---- T13: on, record, unhealthy -----------------------------------------------------------------------------------

def test_on_and_healthy_records_and_suppresses_all_three_paths(portal, on, telegram, legacy):
    at = datetime(2026, 10, 29, 19, 2, tzinfo=timezone.utc)
    portal.rows["value"] = [(41, at)]
    _register(portal)
    portal.rows["value"] = [(42, at)]
    _verify(portal)
    _forgot(portal)
    on.sources.guild_db.add(41, "ann@x.test", hour=19)      # the rows those inserts made, as the sweep reads them
    on.sources.guild_db.add(42, "cy@x.test", hour=19)
    assert portal.calls["new"] == [] and portal.calls["reset"] == [] and legacy.sent == []
    data = on.store.read()
    kinds = sorted(i["kind"] for i in data["items"].values())
    assert kinds == ["guest_access", "guest_access", "password_reset"]
    blob = json.dumps(data)
    for personal in ("ann@x.test", "cy@x.test", "robert@x.test", "Ann Guest", "Robert Test"):
        assert personal not in blob
    on.sweep()
    assert len(telegram.sent) == 1 and telegram.texts[0].startswith("[dev] MiniMoi · 3 actions waiting")


def test_verify_with_a_granted_row_is_not_recorded_and_the_direct_notice_goes(portal, on, legacy):
    """T13 + B1: a revoked guest whose old row is granted: RETURNING gives nothing, so the notice goes directly."""
    portal.rows["value"] = []
    _verify(portal)
    assert portal.calls["new"] == []                          # never today's name-and-email message while enabled
    assert len(legacy.sent) == 1
    text = legacy.texts[0]
    assert "NOT RECORDED in MiniMoi" in text and "Guest access request · Meu Português" in text
    assert "cy@x.test" not in text and "Cy" not in text
    assert on.store.read()["items"] == {}


@pytest.mark.parametrize("break_it", ["token", "failing", "stale"])
def test_on_but_unhealthy_sends_the_safe_direct_notice_and_marks_it_told(portal, on, legacy, telegram, clock,
                                                                         token_file, break_it):
    if break_it == "token":
        token_file.unlink()
    elif break_it == "failing":
        telegram.fail = RuntimeError("down")
        on.sources.guild_db.add(99, "zed@x.test")
        on.sweep()
        assert "failing" in on.health()["reasons"]
    else:
        clock.advance(3 * on.settings.sweep_seconds)
    assert not on.suppresses()
    _register(portal)
    assert portal.calls["new"] == []
    assert len(legacy.sent) == 1
    text = legacy.texts[0]
    assert "(sent directly: notices service not healthy)" in text and "ann@x.test" not in text
    assert "Acts on: https://dev.minimoi.test/guild/operate or https://dev.minimoi.test/admin/guests" in text
    item = next(i for i in on.store.read()["items"].values() if i["delivery"].get("via") == "direct")
    assert item["delivery"]["state"] == "told"
    telegram.fail = None
    clock.advance(3600)
    on.sweep()
    assert all(item["code"] not in t for t in telegram.texts)   # not told a second time by MiniMoi


def test_record_mode_sends_the_safe_direct_notice(portal, make_settings, clock, telegram, legacy, monkeypatch):
    svc = _service(make_settings, clock, telegram, legacy, mode="record")
    monkeypatch.setattr(notices, "_SERVICE", svc)
    _forgot(portal)
    assert portal.calls["reset"] == [] and len(legacy.sent) == 1
    text = legacy.texts[0]
    assert "Password reset request · since" in text and "robert@x.test" not in text and "Robert" not in text
    assert len(st.open_items(svc.store.read())) == 1
    svc.sweep()
    assert telegram.sent == []


def test_an_unwritable_store_records_nothing_and_the_notice_still_goes(portal, make_settings, clock, telegram, legacy,
                                                                     monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    svc = _service(make_settings, clock, telegram, legacy, root=blocker / "notices")
    monkeypatch.setattr(notices, "_SERVICE", svc)
    _register(portal)
    assert len(legacy.sent) == 1 and "NOT RECORDED in MiniMoi" in legacy.texts[0]
    assert "store_unavailable" in svc.health()["reasons"]


def test_a_failed_direct_notice_is_logged_by_code_only(portal, make_settings, clock, telegram, monkeypatch, caplog):
    legacy = FakeTelegram()
    legacy.fail = OSError(f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage refused")
    svc = _service(make_settings, clock, telegram, legacy, mode="record")
    monkeypatch.setattr(notices, "_SERVICE", svc)
    _register(portal)
    assert FAKE_TOKEN not in caplog.text and "api.telegram.org" not in caplog.text
    assert "telegram_sendMessage_failed:OSError" in caplog.text


def test_the_thread_starts_only_from_main(portal, monkeypatch):
    started = []
    monkeypatch.setattr(notices, "start", lambda: started.append(True))
    monkeypatch.setattr(portal.app.app, "run", lambda **kw: None)
    portal.app.main()
    assert started == [True]
