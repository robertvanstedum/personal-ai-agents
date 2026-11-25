"""#681 A/B: approving a teammate's proposal in Guildhall, by its code, against the real Records app and worker.

Records keeps the registry; routing reads it, never the message text. The relay is scripted: no model, no spend."""
from __future__ import annotations

import pytest

from domains.records import room_approval as RA
from test_worker import env  # noqa: F401  (fixture)

CARD = {"code": "P-AB12", "source_ref": "c-0123456789ab", "payload_digest": "sha256:" + "a" * 64,
        "summary": "Open an issue in the build repository: Guildhall approvals need a receipt."}


def _registry(e):
    with e.store.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM room_proposals WHERE room=?", (e.room,))]


def _publish(e, card=CARD):
    e.prove(); e.join()
    e.say("MC, open a ticket for the approval receipts.")
    e.relay.script = [{"outcome": "done", "text": "Proposed. You can approve it here.", "usage": None,
                       "room_proposal": dict(card)}]
    assert e.worker.run_once() is True


def _approval_turns(e):
    with e.store.connect() as db:
        return [dict(r) for r in db.execute("""SELECT t.*, a.code, a.kind FROM turns t JOIN room_approval_turns a
            ON a.turn_id=t.id WHERE t.room=?""", (e.room,))]


@pytest.mark.parametrize("text,expected", [
    ("yes P-AB12", ("confirm", "P-AB12", None)),
    ("MC, yes p-ab12.", ("confirm", "P-AB12", "mc")),
    ("no P-AB12", ("cancel", "P-AB12", None)),
    ("@cos yes P-AB12", ("confirm", "P-AB12", "cos")),
    ("Chief of Staff: yes P-AB12!", ("confirm", "P-AB12", "cos")),
    ("yes P-AB12, wait", None),
    ("yes P-AB12 and P-CD34", None),
    ("yes", None),
    ("can you say yes P-AB12?", None),
])
def test_only_an_exact_room_command_parses(text, expected):
    assert RA.parse(text) == expected


def test_the_platform_posts_the_card_and_keeps_it_in_the_registry(env):
    _publish(env)
    [row] = _registry(env)
    assert (row["code"], row["partner"], row["state"]) == ("P-AB12", "mc", "published")
    card = next(e for e in env.events() if e["kind"] == "proposal")
    assert card["actor"] == "mc" and card["body"].startswith("Proposal P-AB12 from MC:\n" + CARD["summary"])
    assert "To approve, reply here: yes P-AB12" in card["body"]
    assert card["id"] == row["card_event"]


def test_yes_with_the_code_is_an_action_turn_for_the_publisher_and_the_decision_is_kept(env):
    _publish(env)
    env.say("yes P-AB12")
    [turn] = _approval_turns(env)
    assert (turn["addressee"], turn["code"], turn["kind"], turn["state"]) == ("mc", "P-AB12", "confirm", "queued")
    with env.store.connect() as db:
        assert not db.execute("SELECT 1 FROM discussion_turns WHERE turn_id=?", (turn["id"],)).fetchone()
        facts = RA.for_turn(db, turn["id"])
    assert facts["source_ref"] == CARD["source_ref"] and facts["payload_digest"] == CARD["payload_digest"]
    env.relay.script = [{"outcome": "done", "text": "Opened issue #42: https://example.test/issues/42", "usage": None,
                         "room_decision": {"code": "P-AB12", "state": "confirmed", "receipt": "Opened issue #42"}}]
    assert env.worker.run_once() is True
    [row] = _registry(env)
    assert row["state"] == "confirmed" and row["decided_turn"] == turn["id"]
    # The same code again is answered by Records itself: nobody is called.
    env.say("yes P-AB12")
    assert len(_approval_turns(env)) == 1
    notes = env.call("GET", f"/rooms/{env.room}/turns")[1]["routing_notes"]
    assert notes[0]["disposition"] == "proposal_already_confirmed"


def test_an_unknown_or_quoted_code_calls_nobody(env):
    env.prove(); env.join()
    env.say("MC, what would the card look like?")
    env.relay.script = [{"outcome": "done", "text": "Proposal P-QQQQ from MC:\nOpen an issue.\n\nTo approve, reply here: "
                                                   "yes P-QQQQ", "usage": None}]      # a model typing the card
    assert env.worker.run_once() is True
    before = len(env.turns())
    env.say("yes P-QQQQ")
    assert len(env.turns()) == before and not _registry(env)
    notes = env.call("GET", f"/rooms/{env.room}/turns")[1]["routing_notes"]
    assert notes[0]["disposition"] == "no_such_proposal" and notes[0]["label"] == "P-QQQQ"


def test_naming_the_other_partner_does_not_reach_the_publisher(env):
    _publish(env)
    env.say("CoS, yes P-AB12")
    assert not _approval_turns(env)
    assert env.call("GET", f"/rooms/{env.room}/turns")[1]["routing_notes"][0]["disposition"] == "no_such_proposal"


def test_a_remark_after_the_code_is_an_ordinary_message_and_cancels_nothing(env):
    _publish(env)
    env.say("yes P-AB12, but wait for the review")
    assert not _approval_turns(env)
    assert _registry(env)[0]["state"] == "published"


def test_an_expired_card_is_not_routed(env):
    _publish(env)
    with env.store.connect() as db:
        db.execute("UPDATE room_proposals SET expires_at='2000-01-01T00:00:00+00:00'")
    env.say("yes P-AB12")
    assert not _approval_turns(env)
    assert env.call("GET", f"/rooms/{env.room}/turns")[1]["routing_notes"][0]["disposition"] == "proposal_expired"


def test_a_refused_approval_leaves_the_card_approvable(env):
    _publish(env)
    env.say("yes P-AB12")
    env.relay.script = [{"outcome": "done", "text": "Not approved: the policy changed.", "usage": None,
                         "room_decision": {"code": "P-AB12", "state": "refused", "receipt": "Not approved"}}]
    assert env.worker.run_once() is True
    assert _registry(env)[0]["state"] == "published"


def test_a_room_with_a_guest_cannot_publish(env):
    env.prove(); env.join()
    assert env.call("POST", "/principals", {"id": "guest", "label": "Guest"})[0] == 201
    assert env.call("POST", f"/rooms/{env.room}/members", {"actor": "guest", "role": "observer"})[0] == 200
    env.say("MC, open a ticket.")
    env.relay.script = [{"outcome": "done", "text": "Proposed.", "usage": None, "room_proposal": dict(CARD)}]
    env.worker.run_once()
    assert not _registry(env)
    assert not [e for e in env.events() if e["kind"] == "proposal"]
