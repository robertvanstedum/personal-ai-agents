"""Lights (S4, C22): unknown is never zero or green; the Systems rule; grey
"not instrumented" lights; Needs you on a failed read (W2, W3, W10)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from minimoi_portal.guild_ui import lights
from minimoi_portal.guild_ui.adapters import OperationsProbe
from minimoi_portal.guild_ui.adapters.contract import SourceResult, live_ok, live_unknown, not_configured
from floor_helpers import load_portal, staging  # noqa: F401  (pytest fixtures)

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


def _status(state="running", minutes_ago=2, escalations=0):
    checkin = (NOW - timedelta(minutes=minutes_ago)).isoformat() if minutes_ago is not None else None
    return live_ok({"state": state, "last_checkin": checkin, "open_escalations": escalations}, "probe")


@pytest.mark.parametrize("res,state", [
    (_status(), "green"),
    (_status(escalations=7), "green"),        # escalations never decide the colour
    (_status(minutes_ago=11), "unknown"),     # stale check-in
    (_status(state="starting"), "unknown"),
    (_status(state="stopped", escalations=0), "unknown"),
    (_status(minutes_ago=None), "unknown"),
    (_status(minutes_ago=-5), "unknown"),     # a check-in in the future is clock skew, not fresh
    (_status(minutes_ago=-600), "unknown"),
    (live_unknown("read_failed", "unreachable", "probe"), "unknown"),
    (not_configured("GUILD_OPERATIONS_STATUS_URL", "x"), "unknown"),
])
def test_systems_rule(res, state):
    light = lights.systems_light(res, NOW)
    assert light["state"] == state
    assert len(light["reason"]) <= lights.REASON_MAX
    if state != "green":
        assert light["word"] == "Unknown"


def test_a_future_check_in_is_clock_skew_never_green():
    """Review F6: never green unless reachable, running and checked in within
    10 minutes; a check-in ahead of this portal's clock is unknown, with the reason."""
    light = lights.systems_light(_status(minutes_ago=-5), NOW)
    assert light["state"] == "unknown" and light["word"] == "Unknown"
    assert "clock skew" in light["reason"]
    assert any("in the future" in line for line in light["detail"])
    # a few seconds ahead (the same clock, rounding) is still a fresh check-in
    near = live_ok({"state": "running", "last_checkin": (NOW + timedelta(seconds=20)).isoformat(),
                    "open_escalations": 0}, "probe")
    assert lights.systems_light(near, NOW)["state"] == "green"


def test_escalation_count_is_shown_as_reported_only():
    light = lights.systems_light(_status(escalations=0), NOW)
    assert any("never counts toward green" in line for line in light["detail"])
    stale_with_zero = lights.systems_light(_status(minutes_ago=30, escalations=0), NOW)
    assert stale_with_zero["state"] == "unknown"


def test_probe_is_cached_bounded_and_unknown_on_failure():
    calls = []
    clock = [100.0]

    class Answer:
        status_code = 200

        def json(self):
            return {"state": "running", "last_checkin": NOW.isoformat(), "open_escalations": 0}

    def get(url, timeout):
        calls.append((url, timeout))
        return Answer()

    probe = OperationsProbe("http://ops:8768/status", http_get=get, monotonic=lambda: clock[0])
    assert probe.status().ok and probe.status().ok
    assert calls == [("http://ops:8768/status", 2.0)]
    clock[0] += 61
    probe.status()
    assert len(calls) == 2

    def broken(url, timeout):
        raise TimeoutError()
    res = OperationsProbe("http://ops/status", http_get=broken).status()
    assert res.status == "unknown" and res.data is None

    class NotJson:
        status_code = 200

        def json(self):
            raise ValueError()
    assert OperationsProbe("u", http_get=lambda u, t: NotJson()).status().status == "unknown"
    assert OperationsProbe(None).status().source == "not_instrumented"


def test_queue_light_rules():
    ok = lambda rows: live_ok(rows, "q")  # noqa: E731
    row = lambda s, known=True: {"status": s if known else None, "status_known": known}  # noqa: E731
    assert lights.queue_light(ok([row("in_build")]))["state"] == "green"
    assert lights.queue_light(ok([row("blocked"), row("in_build")]))["state"] == "red"
    assert lights.queue_light(ok([row(None, known=False)]))["state"] == "yellow"
    assert lights.queue_light(ok([]))["reason"] == "0 active · 0 blocked"   # a valid empty read is a real zero
    failed = lights.queue_light(live_unknown("read_failed", "gone", "q"))
    assert failed["state"] == "unknown" and "0" not in failed["reason"]


def test_grey_lights_are_not_instrumented_and_never_green(staging):
    floor = staging.owner().get("/guild-next/api/v1/floor").get_json()
    by_id = {l["id"]: l for l in floor["lights"]}
    for grey in ("agents", "usage", "rollouts"):
        assert by_id[grey]["state"] == "unknown"
        assert by_id[grey]["source_mark"] == "not instrumented"
        assert by_id[grey]["reason"]
    assert by_id["usage"]["reason"] == "Usage watch arrives in B2"
    assert by_id["systems"]["state"] == "unknown"  # no Operations address configured here
    assert by_id["build_queue"]["source"] == "live"
    # Agents now has a source (the memory copy), unconfigured here: it reads "unknown", so two lights are not instrumented.
    assert "2 not instrumented" in floor["briefing"]["text"]


def test_systems_green_only_when_the_agent_is_up(load_portal, monkeypatch):
    from minimoi_portal.guild_ui.adapters import systems

    class Answer:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    fresh = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(systems, "_default_get",
                        lambda url, timeout: Answer({"state": "running", "last_checkin": fresh, "open_escalations": 0}))
    portal = load_portal(ops_url="http://host.docker.internal:8768/status")
    light = {l["id"]: l for l in portal.owner().get("/guild-next/api/v1/floor").get_json()["lights"]}["systems"]
    assert light["state"] == "green" and light["source"] == "live"

    def down(url, timeout):
        raise ConnectionError("refused")
    monkeypatch.setattr(systems, "_default_get", down)
    portal = load_portal(ops_url="http://host.docker.internal:8768/status")
    light = {l["id"]: l for l in portal.owner().get("/guild-next/api/v1/floor").get_json()["lights"]}["systems"]
    assert light["state"] == "unknown" and light["reason"] == "Operations agent unreachable"


@pytest.mark.parametrize("breakage", ["missing", "corrupt", "not_a_list", "bad_row"])
def test_a_broken_queue_is_unknown_never_zero_green_or_empty(staging, breakage):
    path = staging.queue_path
    if breakage == "missing":
        path.unlink()
    elif breakage == "corrupt":
        path.write_text("[{")
    elif breakage == "not_a_list":
        path.write_text('{"items": []}')
    else:
        path.write_text('[{"title": "no id"}]')
    client = staging.owner()
    floor = client.get("/guild-next/api/v1/floor").get_json()
    queue_light = {l["id"]: l for l in floor["lights"]}["build_queue"]
    assert queue_light["state"] == "unknown" and queue_light["word"] == "Unknown"
    assert floor["queue"]["active"] is None and floor["queue"]["status"] == "unknown"
    assert floor["needs"]["status"] == "unknown" and floor["needs"]["total"] is None
    assert floor["needs"]["text"] == "Needs you · unknown — read failed"
    assert "Queue unknown" in floor["briefing"]["text"] and "needs you unknown" in floor["briefing"]["text"]
    api = client.get("/guild-next/api/v1/queue").get_json()
    assert api["status"] == "unknown" and api["items"] is None
    page = client.get("/guild-next/guild/build/queue").get_data(as_text=True)
    assert "Active items: <strong>unknown</strong>" in page
    assert 'data-active-count>0<' not in page
    floor_page = client.get("/guild-next/guild/build").get_data(as_text=True)
    assert "Nothing needs you" not in floor_page


def test_needs_you_decide_rows_and_empty_state(load_portal):
    portal = load_portal()
    needs = portal.owner().get("/guild-next/api/v1/floor").get_json()["needs"]
    assert needs["total"] == 1 and needs["items"][0]["tag"] == "Decide"
    assert needs["items"][0]["text"] == "#31 Blocked thing — blocked: waiting on Robert"
    assert needs["items"][0]["href"] == "/guild-next/guild/build/items/31"
    calm = load_portal(items=[{"id": 1, "spec_title": "x", "status": "in_build"}])
    needs = calm.owner().get("/guild-next/api/v1/floor").get_json()["needs"]
    assert needs["total"] == 0 and needs["text"] == "Nothing needs you"


def test_needs_you_shows_three_and_counts_all(load_portal):
    items = [{"id": n, "spec_title": f"b{n}", "status": "blocked", "blocked_reason": "r"} for n in range(1, 6)]
    needs = load_portal(items=items).owner().get("/guild-next/api/v1/floor").get_json()["needs"]
    assert needs["total"] == 5 and len(needs["items"]) == 3


def test_every_reason_is_one_short_line(staging):
    for light in staging.owner().get("/guild-next/api/v1/floor").get_json()["lights"]:
        assert 0 < len(light["reason"]) <= 40 and "\n" not in light["reason"]


def test_explain_card_is_labelled_platform_rules_no_model(staging):
    body = staging.owner().get("/guild-next/guild/build").get_data(as_text=True)
    assert 'id="tpl-explain"' in body
    assert body.count('data-ask="') == 5
    page_json = body[body.index('id="guild-page"'):]
    assert '"rules_label": "Guild platform \\u00b7 rules \\u00b7 no model"' in page_json


def test_source_marks_come_from_the_data(staging):
    floor = staging.owner().get("/guild-next/api/v1/floor").get_json()
    for light in floor["lights"]:
        expected = "live" if light["source"] == "live" else "not instrumented"
        assert light["source_mark"] == expected
    assert isinstance(SourceResult("live", "ok").ok, bool)
