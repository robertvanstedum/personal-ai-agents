"""CoS's In flight table in the CoS store (Review 06, slice 3): one record type, cos.in_flight, and its four commands.

The shape is the UI note of 1 Nov 2026 (planning-studio/operations/threads/NOTE_UI_TO_COMMS_COS_WRITE_PATHS_ANSWER_
2026-11-01.md): in_flight.open, .update, .comment and .close, folded by the writer into one row per document. Provenance
follows the store's rules: robert_source only with a verified turn; a page click is approved, citing its action.
"""
from __future__ import annotations

import pytest

from core.canonical_json import digest
from domains.cos.store import StoreWriter, contracts
from domains.cos.store.fold import apply_in_flight, in_flight_refusal

ENV = "dev"
SCOPE = {"data_scope": "personal", "environment": ENV}
TURN = {"type": "cos.turn", "id": "cosc-0a1b2c3d4e5f:t-0a1b2c3d4e5f6a"}
WORDS = "Send the portfolio walkthrough to Thursday's two"
CLICK = {"type": "cos.owner_ui", "id": "op-0001aaaa"}
ITEM = "IF-walkthrough"


def verified(turn_ref, text):
    return turn_ref == TURN and text == WORDS


@pytest.fixture
def store(tmp_path):
    return StoreWriter(tmp_path / "store", ENV, verify_robert_source=verified)


def event(op, at="2026-11-01T15:00:00+00:00", provenance="approved", **fields):
    doc = {"schema": f"minimoi.cos.in_flight_{op}/1", **SCOPE, "item_id": ITEM, "at": at, "provenance": provenance}
    if provenance == "approved":
        doc["ref"] = CLICK
    if provenance == "robert_source":
        doc["turn_ref"] = TURN
    doc.update(fields)
    return doc


def opened(**over):
    fields = dict(kind="follow-up", title=WORDS, priority=2, status="open", by="robert", source=None)
    fields["with"] = "robert"
    fields.update(over)
    return event("open", **fields)


def command(op, payload, *, command_id, expected, actor="robert", seat=None, origin="owner_turn", basis=CLICK):
    return {"schema": "minimoi.cos.store_command/1", "command_id": command_id, "kind": f"in_flight.{op}",
            "target": {"type": "cos.in_flight", "id": payload["item_id"]}, "expected_revision": expected,
            "actor": actor, "seat": seat, "origin": origin, "basis_ref": basis, "payload": payload,
            "payload_digest": digest(payload), **SCOPE}


def submit(store, op, payload, n, expected):
    return store.submit(command(op, payload, command_id=f"cmd-if-{n}", expected=expected))


def test_open_writes_one_folded_row_that_the_contract_accepts(store):
    result = submit(store, "open", opened(), 1, None)
    assert result["outcome"] == "accepted" and result["revision"] == 1
    row = store.get("cos.in_flight", ITEM)
    assert row == {"schema": "minimoi.cos.in_flight/1", **SCOPE, "item_id": ITEM, "revision": 1, "kind": "follow-up",
                   "title": WORDS, "title_provenance": "approved", "priority": 2, "status": "open", "with": "robert",
                   "opened_at": "2026-11-01T15:00:00+00:00", "last_moved_at": "2026-11-01T15:00:00+00:00",
                   "closed_at": None, "outcome": None, "source": None, "comments": [], "opened_by": "robert"}
    assert contracts.errors("store-records", "in_flight", row) == []
    assert store.docs("cos.in_flight") == [row]
    assert (store.root / "in_flight" / f"{ITEM}.json").exists()
    # The same command again replays its receipt; opening the same row under a new id is refused.
    assert submit(store, "open", opened(), 1, None)["replayed"] is True
    again = submit(store, "open", opened(), 2, 1)
    assert again == {"schema": "minimoi.cos.store_result/1", "command_id": "cmd-if-2", "outcome": "refused",
                     "code": "transition_not_allowed"}


def test_priority_and_status_changes_move_the_row(store):
    submit(store, "open", opened(), 1, None)
    assert submit(store, "update", event("update", at="2026-11-01T16:00:00+00:00", priority=1, by="robert"), 2, 1)["revision"] == 2
    assert submit(store, "update", event("update", at="2026-11-01T17:00:00+00:00", status="waiting", by="robert",
                                         **{"with": "mc"}), 3, 2)["revision"] == 3
    row = store.get("cos.in_flight", ITEM)
    assert (row["priority"], row["status"], row["with"], row["last_moved_at"]) == (1, "waiting", "mc", "2026-11-01T17:00:00+00:00")
    assert row["opened_at"] == "2026-11-01T15:00:00+00:00"
    # A stale revision is a conflict, never a silent overwrite; an empty update is refused by the contract.
    stale = submit(store, "update", event("update", priority=3, by="robert"), 4, 2)
    assert stale["outcome"] == "conflict" and stale["current_revision"] == 3
    assert submit(store, "update", event("update", by="robert"), 5, 3)["code"] == "payload_invalid"
    assert submit(store, "update", event("update", priority=4, by="robert"), 6, 3)["code"] == "payload_invalid"


def test_a_comment_thread_keeps_each_voice_and_its_provenance(store):
    submit(store, "open", opened(), 1, None)
    submit(store, "comment", event("comment", at="2026-11-01T15:10:00+00:00", author="robert", text="Drafts first."), 2, 1)
    cos_note = event("comment", at="2026-11-01T15:20:00+00:00", provenance="agent_draft", author="cos", text="Drafts are in Files.")
    assert store.submit(command("comment", cos_note, command_id="cmd-if-3", expected=2, actor="cos", seat="cos",
                                basis=TURN))["outcome"] == "accepted"
    submit(store, "comment", event("comment", at="2026-11-01T15:30:00+00:00", author="mc", provenance="agent_draft",
                                   text="Tests pass locally."), 4, 3)
    row = store.get("cos.in_flight", ITEM)
    assert [(c["author"], c["text"], c["provenance"]) for c in row["comments"]] == [
        ("robert", "Drafts first.", "approved"), ("cos", "Drafts are in Files.", "agent_draft"), ("mc", "Tests pass locally.", "agent_draft")]
    assert row["comments"][0]["ref"] == CLICK
    assert row["last_moved_at"] == "2026-11-01T15:30:00+00:00"


def test_robert_source_needs_his_verified_turn(store):
    # From Robert's live turn, his own words verify; anything else claiming to be his is refused.
    assert submit(store, "open", opened(provenance="robert_source"), 1, None)["outcome"] == "accepted"
    assert store.get("cos.in_flight", ITEM)["title_provenance"] == "robert_source"
    forged = event("comment", provenance="robert_source", author="robert", text="Something he never said.")
    assert submit(store, "comment", forged, 2, 1)["code"] == "robert_source_unverified"
    # A status change carries no words to verify, so it can't be robert_source.
    assert submit(store, "update", event("update", provenance="robert_source", status="doing", by="robert"), 3, 1)["code"] == "robert_source_unverified"
    # robert_source without a turn, and approved without a ref, fail the contract.
    bare = event("comment", provenance="robert_source", author="robert", text=WORDS)
    bare.pop("turn_ref")
    assert submit(store, "comment", bare, 4, 1)["code"] == "payload_invalid"
    unref = event("comment", author="robert", text="ok")
    unref.pop("ref")
    assert submit(store, "comment", unref, 5, 1)["code"] == "payload_invalid"


def test_close_moves_the_row_to_earlier_and_stops_changes(store):
    submit(store, "open", opened(), 1, None)
    done = submit(store, "close", event("close", at="2026-11-02T09:00:00+00:00", outcome="done", note="Sent both.", by="robert"), 2, 1)
    assert done["outcome"] == "accepted"
    row = store.get("cos.in_flight", ITEM)
    assert (row["closed_at"], row["outcome"], row["status"], row["close_note"]) == ("2026-11-02T09:00:00+00:00", "done", "done", "Sent both.")
    assert submit(store, "update", event("update", priority=1, by="robert"), 3, 2)["code"] == "transition_not_allowed"
    assert submit(store, "close", event("close", outcome="dropped", by="robert"), 4, 2)["code"] == "transition_not_allowed"
    # Its thread stays answerable in Earlier.
    assert submit(store, "comment", event("comment", author="robert", text="Both replied."), 5, 2)["outcome"] == "accepted"
    # Dropping keeps the last status.
    other = dict(opened(), item_id="IF-other")
    submit(store, "open", other, 6, None)
    dropped = dict(event("close", outcome="dropped", by="robert"), item_id="IF-other")
    submit(store, "close", dropped, 7, 1)
    assert store.get("cos.in_flight", "IF-other")["status"] == "open"


def test_commands_on_a_missing_row_and_wrong_targets_are_refused(store):
    assert submit(store, "comment", event("comment", author="robert", text="hi"), 1, None)["code"] == "transition_not_allowed"
    wrong = command("open", opened(), command_id="cmd-if-2", expected=None)
    wrong["target"]["id"] = "IF-elsewhere"
    assert store.submit(wrong)["code"] == "payload_invalid"
    bad_type = command("open", opened(), command_id="cmd-if-3", expected=None)
    bad_type["target"]["type"] = "cos.goal"
    assert store.submit(bad_type)["code"] == "payload_invalid"
    assert store.docs("cos.in_flight") == []


def test_the_fold_alone():
    row = apply_in_flight(None, "in_flight.open", opened())
    assert in_flight_refusal(row, "in_flight.open", opened()) == "row_exists"
    assert in_flight_refusal(None, "in_flight.update", {}) == "no_row"
    closed = apply_in_flight(row, "in_flight.close", event("close", outcome="done", by="robert"))
    assert in_flight_refusal(closed, "in_flight.update", {}) == "row_closed"
    assert in_flight_refusal(closed, "in_flight.comment", {}) is None
    assert row["comments"] == [] and closed is not row


def test_recovery_completes_an_in_flight_write_that_landed(store, tmp_path):
    submit(store, "open", opened(), 1, None)
    again = StoreWriter(tmp_path / "store", ENV, verify_robert_source=verified)
    assert again.recover()["abandoned"] == 0
    assert again.submit(command("open", opened(), command_id="cmd-if-1", expected=None))["replayed"] is True
