"""#585: at the start of MC's turn, the handoffs MC proposed that finished since Robert last spoke, in one line. Files
only; a finish is told once, and only after MC's answer is confirmed (``TurnState.mark``)."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from core import agent_requests as ar
from minimoi_portal.guild_ui import agent_ask, handoff_status as hs

HERE, THERE = "c-aaaaaaaaaaaa", "c-bbbbbbbbbbbb"


def ask(root, seed, *, frm="mc", to="claude-code", conversation=HERE):
    return ar.create_request(root, request_id=ar.request_id_for(seed), frm=frm, to=to, action=f"do {seed}",
                             conversation_id=conversation, origin="owner_message_via_mc" if frm == "mc" else "agent")[0]["request_id"]


@pytest.fixture
def root(tmp_path):
    return str(tmp_path / "agent_requests")


def test_nothing_finished_says_nothing(root):
    ask(root, "open")
    assert hs.line(root, conversation_id=HERE) == ("", [])


def test_a_finished_handoff_is_one_line_across_conversations_without_its_content(root):
    done = ask(root, "done", conversation=THERE)
    gone = ask(root, "gone", to="codex")
    still = ask(root, "still")
    ar.result(root, done, "claude-code", "SECRET REPORT TEXT")
    ar.cancel(root, gone, "mc")
    ar.receipt(root, still, "claude-code")
    text, ids = hs.line(root, conversation_id=HERE)
    finished = text.splitlines()[1]
    assert finished.startswith("Finished: ") and finished.count(";") == 1
    assert f"Claude Code returned a report on {done}" in finished and f"conversation {THERE}" in finished
    assert f"Codex was cancelled on {gone}" in finished and "this conversation" in finished
    assert "Still open, with activity in the last 3 days: 1." in finished
    assert "SECRET" not in text and "do done" not in text                     # ids and outcomes only, never the report
    assert len(ids) == 2


def test_only_mcs_own_handoffs_count(root):
    other = ask(root, "theirs", frm="codex")
    ar.result(root, other, "claude-code", "report")
    assert hs.line(root, conversation_id=HERE) == ("", [])


def test_told_once_after_mark_and_old_finishes_are_history(root):
    rid = ask(root, "done")
    ar.result(root, rid, "claude-code", "report")
    text, ids = hs.line(root)
    assert text and hs.line(root)[1] == ids                                   # building the line alone consumes nothing
    hs.mark_seen(root, ids)
    assert hs.line(root) == ("", [])
    rid2 = ask(root, "later")
    ar.result(root, rid2, "claude-code", "report")
    assert hs.line(root, now=time.time() + hs.WINDOW_S + 60) == ("", [])        # older than the window: not news


def test_more_than_named_max_are_counted_and_named_next_turn(root):
    for n in range(hs.NAMED_MAX + 2):
        ar.result(root, ask(root, f"r{n}"), "claude-code", "report")
    text, ids = hs.line(root)
    assert "and 2 more" in text and len(ids) == hs.NAMED_MAX
    hs.mark_seen(root, ids)
    text, ids = hs.line(root)
    assert len(ids) == 2 and "more" not in text


def test_turn_state_carries_the_line_and_mark_consumes_it(root):
    rid = ask(root, "done", conversation=THERE)
    ar.result(root, rid, "claude-code", "report")
    told = []
    svc = SimpleNamespace(root=root, status_block=lambda cid, teach=True: ("[status of THIS conversation]", ["e-1"]),
                          mark_told=lambda cid, ids: told.append(list(ids)))
    with mock.patch.object(agent_ask, "service", lambda: svc), \
            mock.patch("minimoi_portal.guild_ui.api._tools_own_actions", lambda principal: True):
        state = agent_ask.turn_state({"id": HERE})
        assert state.text.startswith("[status of THIS conversation]\n\n" + hs.HEAD)
        state.mark()
        assert told == [["e-1"]]
        assert "Finished" not in agent_ask.turn_state({"id": HERE}).text


def test_an_unreadable_ledger_leaves_the_turn_as_before(root):
    svc = SimpleNamespace(root=root, status_block=lambda cid, teach=True: ("", []), mark_told=lambda cid, ids: None)
    with mock.patch.object(agent_ask, "service", lambda: svc), \
            mock.patch("minimoi_portal.guild_ui.api._tools_own_actions", lambda principal: True), \
            mock.patch.object(hs, "line", side_effect=OSError("disk")):
        assert agent_ask.turn_state({"id": HERE}) is None
