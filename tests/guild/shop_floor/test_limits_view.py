"""Operate "Usage & limits" (#296 slice 1): every limit with what is used against it; a source it cannot read is Unknown, never green by default."""
from __future__ import annotations

import pytest

from floor_db_helpers import floor_db, floored, keyed  # noqa: F401  (pytest fixtures)

from core.limits import Limits
from floor_helpers import load_portal, write_headers  # noqa: F401
from minimoi_portal.guild_ui import limits_view


def env(tmp_path):
    return {"MINIMOI_LIMITS_DIR": str(tmp_path)}


def rows(view):
    return {r["id"]: r for r in view["rows"]}


def test_no_status_from_the_connection_means_unknown_never_ok(tmp_path):
    v = limits_view.overview(agent_status=None, environ=env(tmp_path))
    assert v["light"]["state"] == "unknown" and v["word"] == "Unknown"
    assert rows(v)["claude.daily_ceiling_usd"]["state"] == "unknown" and rows(v)["claude.daily_ceiling_usd"]["used"] is None
    assert rows(v)["mc.relay_idle_s"]["state"] == "info"


def test_a_healthy_connection_is_ok_and_shows_used_and_share(tmp_path):
    st = {"attention": "", "usage": {"spent_today_usd": 4.0, "daily_ceiling_usd": 20.0, "daily_hold_line_usd": 18.0, "process_spent_usd": 1.0, "process_cap_usd": 5.0, "recycles_today": 2}}
    v = limits_view.overview(agent_status=st, environ=env(tmp_path))
    assert v["light"]["state"] == "green"
    d = rows(v)["claude.daily_ceiling_usd"]
    assert d["used"] == 4.0 and round(d["share"], 2) == 0.2 and d["state"] == "ok"
    s = rows(v)["claude.session_cap_usd"]
    assert s["used"] == 1.0 and round(s["share"], 2) == 0.2 and "2 restart(s) today" in s["note"]


def test_near_the_daily_ceiling_is_watch_and_at_it_is_a_problem_with_the_reason(tmp_path):
    near = {"usage": {"spent_today_usd": 17.0, "process_spent_usd": 0.1, "process_cap_usd": 5.0}}
    v = limits_view.overview(agent_status=near, environ=env(tmp_path))
    assert rows(v)["claude.daily_ceiling_usd"]["state"] == "watch" and v["light"]["state"] == "yellow"
    hit = {"attention": "daily_ceiling_reached:21.00 of 20", "usage": {"spent_today_usd": 21.0, "process_spent_usd": 0.1, "process_cap_usd": 5.0}}
    v = limits_view.overview(agent_status=hit, environ=env(tmp_path))
    assert rows(v)["claude.daily_ceiling_usd"]["state"] == "problem" and "wait until the day rolls over" in rows(v)["claude.daily_ceiling_usd"]["trip"]
    assert v["light"]["state"] == "red"


def test_a_session_that_hit_its_cap_shows_the_trip_and_that_it_restarts_now(tmp_path):
    st = {"attention": "turn_failed:error_max_budget_usd", "usage": {"spent_today_usd": 6.0, "process_spent_usd": 5.2, "process_cap_usd": 5.0}}
    v = limits_view.overview(agent_status=st, environ=env(tmp_path))
    r = rows(v)["claude.session_cap_usd"]
    assert r["state"] == "problem" and "restarts between turns now" in r["trip"]


def test_a_changed_value_is_shown_and_a_damaged_file_is_a_visible_problem_with_defaults(tmp_path):
    Limits(str(tmp_path)).set("claude.daily_ceiling_usd", 40, by="robert")
    st = {"usage": {"spent_today_usd": 10.0, "process_spent_usd": 0.1, "process_cap_usd": 5.0}}
    v = limits_view.overview(agent_status=st, environ=env(tmp_path))
    assert rows(v)["claude.daily_ceiling_usd"]["value"] == 40.0 and rows(v)["claude.daily_ceiling_usd"]["is_default"] is False
    (tmp_path / "limits.json").write_text("{broken")
    v = limits_view.overview(agent_status=st, environ=env(tmp_path))
    assert "damaged" in v["error"] and v["light"]["state"] == "unknown" and rows(v)["claude.daily_ceiling_usd"]["value"] == 20.0


def test_the_operate_page_shows_the_table_and_does_not_assert_subscription_billing(load_portal, tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMOI_LIMITS_DIR", str(tmp_path))
    portal = load_portal()
    html = portal.owner().get("/guild-next/guild/operate").get_data(as_text=True)
    assert 'id="usage-limits"' in html and 'data-limit="claude.daily_ceiling_usd"' in html and 'data-limit="claude.session_cap_usd"' in html
    assert 'data-limit="mc.relay_idle_s"' in html and "Subscription versus API billing is not verified here" in html
    assert "not a billing record" in html and "own estimate on the subscription" not in html
    assert "Unknown" in html                                                                         # no connection status here: unknown, not green


def test_a_requested_value_that_is_not_yet_applied_is_never_shown_as_done(tmp_path):
    Limits(str(tmp_path)).set("claude.session_cap_usd", 8, by="robert")
    st = {"attention": "", "usage": {"spent_today_usd": 1.0, "process_spent_usd": 0.5, "process_cap_usd": 5.0,
                                       "applied": {"claude.session_cap_usd": 5.0, "claude.daily_ceiling_usd": 20.0, "claude.daily_headroom_fraction": 0.1}}}
    v = limits_view.overview(agent_status=st, environ=env(tmp_path))
    r = rows(v)["claude.session_cap_usd"]
    assert r["state"] == "watch" and "requested $8, running with $5" in r["note"] and v["light"]["state"] == "yellow"
    assert rows(v)["claude.daily_ceiling_usd"]["state"] == "ok"                                      # a live limit that matches is fine


def test_results_without_a_cost_and_the_reserve_line_are_visible(tmp_path):
    st = {"attention": "", "usage": {"spent_today_usd": 2.0, "unknown_results_today": 2, "daily_hold_line_usd": 18.0, "process_spent_usd": 0.5, "process_cap_usd": 5.0,
                                       "applied": {}}}
    v = limits_view.overview(agent_status=st, environ=env(tmp_path))
    d = rows(v)["claude.daily_ceiling_usd"]
    assert d["state"] == "watch" and "2 result(s) today had no readable cost" in d["note"]
    assert "new work stops at $18" in rows(v)["claude.daily_headroom_fraction"]["note"]
    held = {"attention": "usage_unknown", "usage": {"spent_today_usd": 2.0, "process_spent_usd": 0.5, "process_cap_usd": 5.0}}
    d = rows(limits_view.overview(agent_status=held, environ=env(tmp_path)))["claude.daily_ceiling_usd"]
    assert d["state"] == "problem" and "without a readable cost" in d["trip"]


def test_reported_disabled_daily_guard_is_off_not_default_twenty_dollar_limit(tmp_path):
    st = {"usage": {"spent_today_usd": 181.97, "daily_ceiling_usd": None,
                    "daily_hold_line_usd": None, "process_spent_usd": 0.5, "process_cap_usd": 5,
                    "applied": {"claude.daily_ceiling_usd": None, "claude.daily_headroom_fraction": 0.1}}}
    before = list(tmp_path.iterdir())
    view = limits_view.overview(agent_status=st, environ=env(tmp_path))
    row = rows(view)["claude.daily_ceiling_usd"]
    assert row["runtime_off"] and row["share"] is None and row["state"] == "info"
    assert row["used"] == 181.97 and "default $20 is not enforced" in row["note"]
    assert rows(view)["claude.daily_headroom_fraction"]["runtime_off"]
    assert view["word"] == "Info*" and view["light"]["word"] == "Info*" and "ceiling off*" in view["light"]["reason"]
    assert view["footnote"] == limits_view.CEILING_OFF_FOOTNOTE and "cannot tell whether the subscription is still active" in view["footnote"]
    text, disabled_id = limits_view.mc_block(agent_status=st, environ=env(tmp_path))
    assert "Subscription versus API billing is not verified here" in text and "own estimate on the subscription" not in text
    claude_line = next(line for line in text.splitlines() if line.startswith("- Claude Code daily ceiling"))
    assert "Off* on this connection" in text and "(910%)" not in text and "limit $20" not in claude_line
    assert limits_view.CEILING_OFF_FOOTNOTE in text and "real API cost" in text                       # the explanation travels with the number to Master Craftsman too
    assert list(tmp_path.iterdir()) == before  # read-only; never enable the guard
    st["usage"]["applied"]["claude.daily_ceiling_usd"] = 20
    st["usage"]["daily_ceiling_usd"] = 20
    assert disabled_id != limits_view.mc_block(agent_status=st, environ=env(tmp_path))[1]


@pytest.mark.parametrize("usage", [{}, {"applied": {"claude.daily_ceiling_usd": None}},
                                  {"daily_ceiling_usd": None}])
def test_incomplete_status_does_not_invent_disabled_guard(tmp_path, usage):
    view = limits_view.overview(agent_status={"usage": usage}, environ=env(tmp_path))
    row = rows(view)["claude.daily_ceiling_usd"]
    assert row["state"] == "unknown" and not row["runtime_off"]


def test_disabled_guard_does_not_hide_damaged_cost_records(tmp_path):
    st = {"attention": "usage_log_damaged", "usage": {"spent_today_usd": 0, "daily_ceiling_usd": None,
                        "applied": {"claude.daily_ceiling_usd": None}}}
    view = limits_view.overview(agent_status=st, environ=env(tmp_path))
    assert rows(view)["claude.daily_ceiling_usd"]["state"] == "problem"
    assert "damaged" in rows(view)["claude.daily_ceiling_usd"]["trip"]


def test_no_asterisk_or_footnote_when_the_ceiling_is_on_or_unknown(tmp_path):
    on = {"usage": {"spent_today_usd": 1.0, "daily_ceiling_usd": 20, "applied": {"claude.daily_ceiling_usd": 20}}}
    for st in (on, {"usage": {}}, None):
        view = limits_view.overview(agent_status=st, environ=env(tmp_path))
        assert view["footnote"] is None and "*" not in view["word"] and "*" not in view["light"]["reason"]
        assert limits_view.CEILING_OFF_FOOTNOTE not in limits_view.mc_block(agent_status=st, environ=env(tmp_path))[0]


def test_the_footnote_stays_with_a_disabled_ceiling_even_when_another_state_wins(tmp_path):
    st = {"attention": "usage_log_damaged", "usage": {"spent_today_usd": 0, "daily_ceiling_usd": None, "applied": {"claude.daily_ceiling_usd": None}}}
    view = limits_view.overview(agent_status=st, environ=env(tmp_path))
    assert view["word"] == "Problem" and view["footnote"] == limits_view.CEILING_OFF_FOOTNOTE       # a worse state replaces the word, never the explanation


def test_the_operate_page_shows_the_asterisk_and_the_explanation(floored, tmp_path, monkeypatch):
    from floor_helpers import write_headers  # noqa: F401
    st = {"usage": {"spent_today_usd": 181.97, "daily_ceiling_usd": None, "daily_hold_line_usd": None, "process_spent_usd": 0.5, "process_cap_usd": 5,
                    "applied": {"claude.daily_ceiling_usd": None, "claude.daily_headroom_fraction": 0.1}}}          # a complete status, like the unit test above
    real = limits_view.overview
    monkeypatch.setattr(limits_view, "overview", lambda **kw: real(agent_status=st, environ=env(tmp_path)))
    html = floored.owner().get("/guild-next/guild/operate").get_data(as_text=True)
    assert "Info*" in html and "Off*" in html and "data-ceiling-footnote" in html
    assert "cannot tell whether the subscription is still active" in html and "real API cost" in html
    monkeypatch.setattr(limits_view, "overview", lambda **kw: real(agent_status={"usage": {"daily_ceiling_usd": 20, "applied": {"claude.daily_ceiling_usd": 20}}}, environ=env(tmp_path)))
    html = floored.owner().get("/guild-next/guild/operate").get_data(as_text=True)
    assert "data-ceiling-footnote" not in html and "Off*" not in html
