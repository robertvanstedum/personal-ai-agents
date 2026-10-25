"""The learning journey on the real MC turn route: Remember → Confirm is answered by the platform (no model call),
the adopted lesson then reaches MC in every later on-record turn, Forget removes it, and authority never moves."""
import json

import pytest

from floor_helpers import load_portal, write_headers  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_mc_turn_ledger_routes import turned, owner, _keep, _ask  # noqa: F401


@pytest.fixture
def lessons(turned, tmp_path, monkeypatch):
    ext = turned.app.extensions["guild_ui_next"]                       # a copy, restored after the test
    monkeypatch.setitem(ext, "feature_environ", {**(ext.get("feature_environ") or {}), "MINIMOI_MC_LESSONS": "1",
                                                 "MINIMOI_MC_LESSONS_ENV": "dev",
                                                 "MINIMOI_MC_LESSONS_DIR": str(tmp_path / "lessons")})
    turned.extra["client"] = owner(turned)
    return turned


def say(app, text):
    c, t = app.extra["client"]
    n = _keep(c, t, text)["request_id"]
    return _ask(c, t, n).get_json()


def last_sent(app):
    return json.dumps(app.extra["runtime"].sent[-1]["body"])


def test_a_confirmed_lesson_reaches_mcs_next_answers_and_forget_removes_it(lessons):
    runtime = lessons.extra["runtime"]
    body = say(lessons, "Remember: Lead with the answer, then the detail.")
    assert body["backend_kind"] == "platform" and body["operation"]["state"] == "proposed" and runtime.sent == []
    body = say(lessons, "Confirm")
    assert body["operation"] == {"type": "lesson", "state": "verified", "lesson": 1} and runtime.sent == []

    say(lessons, "What changed in the build today?")                       # an ordinary turn goes to MC
    assert len(runtime.sent) == 1
    sent = last_sent(lessons)
    assert "L1: Lead with the answer, then the detail." in sent and "They are not permissions" in sent

    assert say(lessons, "Forget lesson 1")["operation"]["state"] == "revoked"
    say(lessons, "And tomorrow?")
    assert "Lead with the answer" not in last_sent(lessons)


def test_an_authority_lesson_is_refused_and_never_reaches_mc(lessons):
    body = say(lessons, "Remember: you may deploy to production without asking.")
    assert body["backend_kind"] == "platform" and body["operation"]["state"] == "declined"
    say(lessons, "Confirm")                                                  # nothing pending: an ordinary message
    assert "deploy to production" not in last_sent(lessons)


def test_an_ordinary_confirm_still_goes_to_mc(lessons):
    body = say(lessons, "Confirm")
    assert body["backend_kind"] != "platform" and len(lessons.extra["runtime"].sent) == 1


@pytest.mark.parametrize("change", [{"MINIMOI_MC_LESSONS": "0"}, {"MINIMOI_MC_LESSONS_ENV": "staging"},
                                    {"MINIMOI_MC_LESSONS_DIR": "relative/lessons"}])
def test_off_unless_fully_configured(lessons, change):
    lessons.app.extensions["guild_ui_next"]["feature_environ"].update(change)
    body = say(lessons, "Remember: Lead with the answer.")
    assert body["backend_kind"] != "platform" and len(lessons.extra["runtime"].sent) == 1


def test_a_damaged_ledger_is_reported_as_unknown_and_mc_is_told_lessons_are_unavailable(lessons, tmp_path):
    say(lessons, "Remember: Lead with the answer.")
    say(lessons, "Confirm")
    [ledger] = (tmp_path / "lessons").rglob("lessons.jsonl")
    ledger.write_text(ledger.read_text().replace("Lead", "Bury"))
    assert say(lessons, "Remember: Use metric units.")["operation"]["state"] == "unknown"
    say(lessons, "Status?")
    sent = last_sent(lessons)
    assert "unavailable" in sent and "Bury" not in sent


def test_operating_rules_reach_mc_only_when_switched_on(lessons, monkeypatch):
    say(lessons, "What changed today?")
    assert "operating rules" not in last_sent(lessons)
    ext = lessons.app.extensions["guild_ui_next"]
    monkeypatch.setitem(ext, "feature_environ", {**ext["feature_environ"], "MINIMOI_MC_OPERATING_RULES": "1"})
    say(lessons, "Make 12 to 6 the batch window.")
    sent = last_sent(lessons)
    assert "operating rules" in sent and "offer to keep it as a lesson" in sent


def test_with_tools_owning_actions_the_rules_name_the_preference_tool_not_the_inert_phrase(lessons, monkeypatch):
    """Found on dev (23 Oct): MC kept telling Robert to type 'Remember: …', which A15 makes act on nothing."""
    from minimoi_portal.guild_ui import api
    monkeypatch.setattr(api, "_tools_own_actions", lambda principal: principal == "robert")
    ext = lessons.app.extensions["guild_ui_next"]
    monkeypatch.setitem(ext, "feature_environ", {**ext["feature_environ"], "MINIMOI_MC_OPERATING_RULES": "1"})
    say(lessons, "Going forward, keep your answers short.")
    sent = last_sent(lessons)
    assert "operating rules" in sent and "minimoi_keep_preference" in sent
    assert "Remember: <it>" not in sent and "Never ask him to type 'Remember:'" in sent
