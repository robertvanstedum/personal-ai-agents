"""A15 path parity (spec v1.2 §16.1 M1.2): with MC's typed tools on for this deploy, no regex owns the same intent. The
owner's guest-rejection and "Remember:" phrases act on nothing, and MC's own JOB: / ASK / MEETING lines are plain
text; the owner's other commands and file delivery are untouched. Real portal routes, scripted relay, no model."""
from __future__ import annotations

import json

import pytest

from floor_helpers import load_portal  # noqa: F401
from floor_db_helpers import floor_db, floored, keyed  # noqa: F401
from test_jobs_proposal import _ask as _stream_ask, _reply, _rig  # noqa: F401
from test_mc_guest_commands_route import FakeCommands, say
from test_mc_turn_ledger_routes import turned  # noqa: F401
from core.mc_domain_actions import guest_commands as gc
from minimoi_portal.guild_ui import mc_tools_wiring as w

TOOLS_ON = {"MINIMOI_MC_TOOLS": "1", "MINIMOI_MC_TOOLS_ENV": "dev", "MINIMOI_MC_TOOLS_PRINCIPAL": "robert",
            "MINIMOI_MC_TURN_LEDGER": "1", "MINIMOI_MC_TOOLS_TOKEN_SHA256": "a" * 64}


def _flags(portal, monkeypatch, **extra):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    ext = portal.app.extensions["guild_ui_next"]
    monkeypatch.setitem(ext, "feature_environ", {**(ext.get("feature_environ") or {}), **extra})


def _tools_mode(monkeypatch, on=True):
    """The deploy's one switch (owns_actions, tested below on its real inputs), forced for the route tests: the full
    tool settings would also switch on the PostgreSQL turn ledger these SQLite fixtures do not have."""
    from minimoi_portal.guild_ui import api
    monkeypatch.setattr(api, "_tools_own_actions", lambda principal: on and principal == "robert")


@pytest.fixture
def lane_tools(floored, tmp_path, monkeypatch):
    portal = _rig(floored, tmp_path, monkeypatch)
    _tools_mode(monkeypatch)
    return portal


@pytest.fixture
def guests_tools(turned, monkeypatch, tmp_path):
    FakeCommands.calls, FakeCommands.pending = [], False
    monkeypatch.setattr(gc, "GuestCommands", FakeCommands)
    _flags(turned, monkeypatch, MINIMOI_MC_GUEST_ACTIONS="1", MINIMOI_MC_GUEST_ACTIONS_ENV="dev",
           MINIMOI_MC_LESSONS="1", MINIMOI_MC_LESSONS_ENV="dev", MINIMOI_MC_LESSONS_DIR=str(tmp_path / "lessons"))
    (tmp_path / "lessons").mkdir()
    _tools_mode(monkeypatch)
    return turned


def test_one_switch_per_deploy_and_owner(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    assert w.owns_actions(TOOLS_ON, "robert") is True
    assert w.owns_actions(TOOLS_ON, "someone") is False
    assert w.owns_actions({k: v for k, v in TOOLS_ON.items() if k != "MINIMOI_MC_TOOLS"}, "robert") is False
    assert w.owns_actions(None, "robert") is False
    monkeypatch.delenv("DATABASE_URL")
    assert w.owns_actions(TOOLS_ON, "robert") is False                         # no database: no tool path, phrases work


@pytest.mark.parametrize("chunks", [("JOB: Compare the resumes\nI'll run that.",), ("ASK claude-code: look at it",),
                                    ("MEETING now with Codex",)])
def test_mc_lines_that_used_to_act_are_plain_text(lane_tools, chunks):
    _reply(lane_tools, *chunks)
    client, token, cid, note, events = _stream_ask(lane_tools)
    done = events[-1]
    assert done["t"] == "done" and done["status"] == "answered" and "job" not in done
    assert lane_tools.extra["manager"].store.list(cid) == []
    assert done["reply_note"]["text"] == "".join(chunks)                       # kept as written, nothing acted on


def test_the_guest_rejection_phrase_goes_to_mc_and_acts_on_nothing(guests_tools):
    sent = len(guests_tools.extra["runtime"].sent)
    body = say(guests_tools, "Reject the guest request from Bruno")
    assert body["backend_kind"] != "platform" and FakeCommands.calls == []
    assert len(guests_tools.extra["runtime"].sent) == sent + 1


def test_remember_goes_to_mc_but_the_owners_lesson_list_still_answers(guests_tools):
    body = say(guests_tools, "Remember: lead with the answer, then the detail.")
    assert body["backend_kind"] != "platform"
    listed = say(guests_tools, "What lessons do you have?")
    assert listed["backend_kind"] == "platform"


def test_without_tools_the_phrases_still_work_as_before(guests_tools, monkeypatch):
    _tools_mode(monkeypatch, on=False)
    body = say(guests_tools, "Reject the guest request from Bruno")
    assert body["backend_kind"] == "platform" and FakeCommands.calls == ["reject"]


def test_the_agent_status_never_teaches_ask_or_meeting_lines_with_tools_on(tmp_path):
    from minimoi_portal.guild_ui import agent_ask
    svc = agent_ask.AskService.__new__(agent_ask.AskService)
    svc.root = tmp_path
    svc._told = lambda cid: set()
    svc._limits_part = lambda told: ("", [])
    svc._join = lambda *parts: ("\n".join(p[0] for p in parts if p[0]), [i for p in parts for i in p[1]])
    taught, _ = svc.status_block("c" * 32)
    quiet, ids = svc.status_block("c" * 32, teach=False)
    assert "ASK" in taught and "ASK" not in quiet and ids == []
    assert json.dumps(quiet).count("MEETING") == 0
