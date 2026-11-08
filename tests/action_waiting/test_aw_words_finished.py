"""Finished tracked work is news, not a wait (Robert, 7 Nov: W-1 led every MC reply as "waiting on you")."""
from minimoi_portal.action_waiting import words


def item(text, agent="partnership", kind="agent_ask"):
    return {"code": "W-1", "kind": kind, "facts": {"agent": agent}, "words": text}


def test_finished_work_is_for_his_information_and_says_how_to_clear_it():
    done = item("Finished: Prepare a checklist. Accepted by Master Craftsman. Nothing needed from you. (task TK-1)")
    assert words.for_information(done)
    assert words.headline(done) == "Finished work, for your information"
    assert words.answer(done) == 'nothing to do; say "dismiss W-1" here to clear it'


def test_every_other_partnership_notice_still_waits_on_him():
    for text in ("Tracked work TK-1 needs you: its lead mc missed a checkpoint. Open the task view to decide.",
                 "Tracked work TK-2, a step of TK-1 held by codex, failed: x. Reason: y. Open the task view."):
        assert not words.for_information(item(text))
    assert not words.for_information(item("Finished: x", agent="claude-code"))
