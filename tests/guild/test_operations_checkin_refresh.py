"""#240: the Operations agent refreshes its check-in on every successful
health check, so Guild's Systems light stays green while the agent is healthy,
and turns unknown once the checks start failing. A fake clock; no database,
no Telegram, no services are touched."""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

from minimoi_portal.guild_ui.adapters.contract import live_ok
from minimoi_portal.guild_ui.lights import systems_light

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _unload_new_modules(before: set):
    """Undo the agent's imports, so later tests import their modules fresh
    (it pulls in core.telegram.*, which other tests reload)."""
    for name in set(sys.modules) - before:
        sys.modules.pop(name, None)
        parent, _, child = name.rpartition(".")
        if parent in sys.modules and getattr(sys.modules[parent], child, None) is not None:
            try:
                delattr(sys.modules[parent], child)
            except AttributeError:
                pass


@pytest.fixture
def ops(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://test-only@127.0.0.1:1/none")     # never connected to
    before = set(sys.modules)
    mod = importlib.import_module("domains.guild.agents.operations")
    clock = {"now": T0}
    healthy = {"ok": True}

    def check_services():
        if not healthy["ok"]:
            raise RuntimeError("launchctl unavailable")
        return [("com.user.portal", True, "")]

    monkeypatch.setattr(mod, "_now", lambda: clock["now"])
    monkeypatch.setattr(mod, "_check_services", check_services)
    monkeypatch.setattr(mod, "_check_disk", lambda: {"pct": 40, "free_gb": 100})
    monkeypatch.setattr(mod, "_db_update_state", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_db_open_escalations", lambda: 0)
    monkeypatch.setattr(mod, "_ops_memory_write", lambda entry: True)
    monkeypatch.setattr(mod, "_log_file", lambda *a: None)
    monkeypatch.setattr(mod, "_state", {**mod._state, "last_checkin": None, "checks_run": 0})
    mod._set_state(state="running")                                   # startup, at T0
    yield mod, clock, healthy
    _unload_new_modules(before)


def _light(mod, now):
    status = mod.app.test_client().get("/status").get_json()
    data = {k: status.get(k) for k in ("state", "last_checkin", "open_escalations")}
    return systems_light(live_ok(data, "test"), now=now)


def test_the_light_stays_green_while_the_health_loop_succeeds(ops):
    mod, clock, _ = ops
    for minutes in range(5, 65, 5):                                   # an hour of 5-minute iterations
        clock["now"] = T0 + timedelta(minutes=minutes)
        assert mod._health_check_once() is True
        light = _light(mod, clock["now"] + timedelta(minutes=4, seconds=59))    # just before the next one
        assert light["state"] == "green", (minutes, light)
    assert mod._state["last_checkin"] == (T0 + timedelta(minutes=60)).isoformat()
    assert mod._state["checks_run"] == 12


def test_failing_checks_stop_refreshing_so_the_light_turns_unknown(ops):
    mod, clock, healthy = ops
    clock["now"] = T0 + timedelta(minutes=5)
    assert mod._health_check_once() is True
    healthy["ok"] = False
    for minutes in (10, 15):
        clock["now"] = T0 + timedelta(minutes=minutes)
        assert mod._health_check_once() is False
    assert mod._state["last_checkin"] == (T0 + timedelta(minutes=5)).isoformat()
    assert _light(mod, T0 + timedelta(minutes=14))["state"] == "green"      # 9 min since the last good check
    late = _light(mod, T0 + timedelta(minutes=16))
    assert late["state"] == "unknown" and "last check-in 11 min ago" in late["reason"]


def test_a_startup_checkin_alone_goes_unknown_after_ten_minutes(ops):
    mod, clock, _ = ops                                               # no health iteration at all (the old behaviour)
    assert _light(mod, T0 + timedelta(minutes=11))["state"] == "unknown"


def test_the_loop_runs_well_inside_the_window(ops):
    import inspect
    src = inspect.getsource(ops[0]._health_loop)
    assert "_health_check_once()" in src and "time.sleep(300)" in src
