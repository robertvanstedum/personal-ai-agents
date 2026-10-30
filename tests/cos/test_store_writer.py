"""B1a: the CoS store's one command writer (spec v0.3 section 4.5 and 4.6).

Each test names the guarantee it holds: a receipt only after read-back, replay without a second write, conflicts on
stale revisions, transitions folded from events, one claim per nudge and per ask, and recovery that resolves every
intent and never reads a torn line as a record.

The writer validates against contracts/cos/v1/store-command.schema.json, contracts/cos/v1/store-records.schema.json and
contracts/cos/v1/common.schema.json at runtime, so a change to any of them runs these tests too.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from domains.cos.store import StoreWriter
from domains.cos.store import contracts
from domains.cos.store.canonical import digest

ENV = "dev"
SCOPE = {"data_scope": "personal", "environment": ENV}
TURN = {"type": "cos.turn", "id": "cosc-0a1b2c3d4e5f:t-0a1b2c3d4e5f6a"}
TS = "2026-10-30T14:12:05-05:00"
WORDS = "Nag me on doing a german one everyday since I said I want to learn german."


def verified(turn_ref, text):
    return turn_ref == TURN and text == WORDS


@pytest.fixture
def store(tmp_path):
    return StoreWriter(tmp_path / "store", ENV, verify_robert_source=verified)


def command(kind, target_type, target_id, payload, *, command_id="cmd-1", expected=None, origin="owner_turn",
            actor="cos", seat="cos", basis=TURN):
    return {"schema": "minimoi.cos.store_command/1", "command_id": command_id, "kind": kind,
            "target": {"type": target_type, "id": target_id}, "expected_revision": expected, "actor": actor,
            "seat": seat, "origin": origin, "basis_ref": basis, "payload": payload,
            "payload_digest": digest(payload), **SCOPE}


def goal(**over):
    doc = {"schema": "minimoi.cos.goal/1", **SCOPE, "id": "GOAL-german-b1", "revision": 0, "title": "German to B1",
           "owner_words": [{"text": WORDS, "provenance": "robert_source", "turn_ref": TURN}],
           "domains": ["german"], "plan_ref": None, "state": "active", "created_at": TS}
    doc.update(over)
    return doc


def definition(obl_id="OBL-german-daily", **over):
    doc = {"schema": "minimoi.cos.obligation_definition/1", **SCOPE, "id": obl_id, "revision": 0, "who": "robert",
           "what": "One German session a day", "basis_ref": TURN, "recurrence": {"kind": "daily", "tz": "America/Chicago"},
           "practice_period": "local_day", "reminder_window": {"from": "18:00", "to": "20:00"},
           "evidence_rule": {"domain": "german", "metric": "sessions_completed", "min_count": 1},
           "quiet_after_days": 3, "must_not_miss": False, "issue_ref": None, "state": "active"}
    doc.update(over)
    return doc


def event(event_id, kind, revision_before, occ="OBL-german-daily@2026-10-30", **extra):
    doc = {"schema": "minimoi.cos.obligation_event/1", **SCOPE, "event_id": event_id, "type": kind,
           "occurrence_id": occ, "at": TS, "actor": "scheduler", "seat": None, "revision_before": revision_before}
    doc.update(extra)
    return doc


def ep_event(event_id, kind, revision_before, **extra):
    doc = {"schema": "minimoi.cos.episode_event/1", **SCOPE, "event_id": event_id, "type": kind,
           "episode_id": "EP-mc-update@2026-10-30", "publisher": "mc", "part": "update", "at": TS,
           "revision_before": revision_before}
    doc.update(extra)
    return doc


class Stream:
    """Submit events in order, tracking the stream revision the way a real sender reads it first."""

    def __init__(self, store):
        self.store, self.n = store, 0

    def send(self, payload, target_type="cos.occurrence", target_id=None, origin="scheduled", **kw):
        target_id = target_id or payload.get("occurrence_id") or payload.get("episode_id") or payload.get("id")
        payload = dict(payload)
        if "revision_before" in payload:
            payload["revision_before"] = self.n
        kind = {"cos.occurrence": "obligation.event", "cos.episode": "episode.event",
                "cos.decision": "decision.record"}[target_type]
        result = self.store.submit(command(kind, target_type, target_id, payload, command_id=f"cmd-s{self.n}-{payload.get('event_id', payload.get('id'))}",
                                           expected=self.n, origin=origin, actor="scheduler", seat=None, basis=kw.get("basis")))
        if result["outcome"] == "accepted":
            self.n += 1
        return result


def define(store, obl_id="OBL-german-daily", **over):
    result = store.submit(command("obligation.define", "cos.obligation", obl_id, definition(obl_id, **over),
                                  command_id=f"cmd-def-{obl_id}"))
    assert result["outcome"] == "accepted", result
    return result


# ---- receipts, replay, conflicts --------------------------------------------------------------------------------------

def test_accepted_only_after_read_back_with_a_receipt_and_group_readable_modes(store):
    result = store.submit(command("goal.put", "cos.goal", "GOAL-german-b1", goal()))
    assert result == {"schema": "minimoi.cos.store_result/1", "command_id": "cmd-1", "outcome": "accepted",
                      "revision": 1, "receipt_id": result["receipt_id"], "replayed": False}
    assert not contracts.errors("store-command", "result", result)
    stored = store.get("cos.goal", "GOAL-german-b1")
    assert stored["revision"] == 1 and stored["title"] == "German to B1"
    path = store.root / "goals" / "GOAL-german-b1.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o750


def test_repeated_command_returns_the_first_receipt_without_writing_again(store):
    first = store.submit(command("goal.put", "cos.goal", "GOAL-german-b1", goal()))
    path = store.root / "goals" / "GOAL-german-b1.json"
    mtime = os.stat(path).st_mtime_ns
    again = store.submit(command("goal.put", "cos.goal", "GOAL-german-b1", goal()))
    assert again["replayed"] is True and again["receipt_id"] == first["receipt_id"]
    assert os.stat(path).st_mtime_ns == mtime


def test_same_command_id_with_a_changed_payload_is_refused(store):
    store.submit(command("goal.put", "cos.goal", "GOAL-german-b1", goal()))
    changed = store.submit(command("goal.put", "cos.goal", "GOAL-german-b1", goal(title="Something else")))
    assert changed == {"schema": "minimoi.cos.store_result/1", "command_id": "cmd-1", "outcome": "refused",
                       "code": "command_id_reused"}


def test_stale_expected_revision_is_a_conflict_with_the_current_revision(store):
    store.submit(command("goal.put", "cos.goal", "GOAL-german-b1", goal()))
    update = command("goal.put", "cos.goal", "GOAL-german-b1", goal(state="paused"), command_id="cmd-2", expected=1)
    assert store.submit(update)["outcome"] == "accepted"
    stale = command("goal.put", "cos.goal", "GOAL-german-b1", goal(title="B2"), command_id="cmd-3", expected=1)
    assert store.submit(stale) == {"schema": "minimoi.cos.store_result/1", "command_id": "cmd-3",
                                   "outcome": "conflict", "current_revision": 2}
    create_again = command("goal.put", "cos.goal", "GOAL-german-b1", goal(), command_id="cmd-4", expected=None)
    assert store.submit(create_again)["outcome"] == "conflict"


@pytest.mark.parametrize("mutate,code", [
    (lambda c: c.update(environment="prod"), "wrong_environment"),
    (lambda c: c.update(data_scope="company:rvs"), "payload_invalid"),
    (lambda c: c.update(payload_digest="sha256:" + "0" * 64), "payload_digest_mismatch"),
    (lambda c: c.update(seat="admin"), "payload_invalid"),
    (lambda c: c.update(target={"type": "cos.plan", "id": "GOAL-german-b1"}), "payload_invalid"),
])
def test_refusals_have_fixed_codes(store, mutate, code):
    cmd = command("goal.put", "cos.goal", "GOAL-german-b1", goal())
    mutate(cmd)
    result = store.submit(cmd)
    assert result["outcome"] == "refused" and result["code"] == code
    assert store.get("cos.goal", "GOAL-german-b1") is None


def test_a_command_for_another_environment_is_refused_even_when_well_formed(store):
    payload = dict(goal(), environment="prod")
    cmd = dict(command("goal.put", "cos.goal", "GOAL-german-b1", payload), environment="prod")
    assert store.submit(cmd)["code"] == "wrong_environment"


def test_robert_source_is_stored_only_when_the_server_verifies_the_turn(tmp_path):
    unverifiable = StoreWriter(tmp_path / "a", ENV)
    assert unverifiable.submit(command("goal.put", "cos.goal", "GOAL-german-b1", goal()))["code"] == "robert_source_unverified"
    wrong = goal(owner_words=[{"text": "I want to learn German fast", "provenance": "robert_source", "turn_ref": TURN}])
    checked = StoreWriter(tmp_path / "b", ENV, verify_robert_source=verified)
    assert checked.submit(command("goal.put", "cos.goal", "GOAL-german-b1", wrong))["code"] == "robert_source_unverified"
    drafted = goal(owner_words=[{"text": "Robert wants B1", "provenance": "agent_draft"}])
    assert unverifiable.submit(command("goal.put", "cos.goal", "GOAL-german-b1", drafted, command_id="cmd-9"))["outcome"] == "accepted"


# ---- the commitment before the promise (N1) ----------------------------------------------------------------------------

def commitment(obl_id="OBL-cos-followup-0001"):
    return definition(obl_id, who="cos", what="Ask MC where the three ideas go", evidence_rule=None,
                      recurrence={"kind": "once", "tz": "America/Chicago", "due": "2026-10-30T18:00:00-05:00"},
                      practice_period="until_due", reminder_window=None, quiet_after_days=None)


def test_commitment_opens_once_and_only_from_robert_s_turn(store):
    ok = store.submit(command("commitment.open", "cos.obligation", "OBL-cos-followup-0001", commitment(), command_id="c1"))
    assert ok["outcome"] == "accepted" and ok["revision"] == 1
    again = store.submit(command("commitment.open", "cos.obligation", "OBL-cos-followup-0001", commitment(), command_id="c2"))
    assert again["code"] == "transition_not_allowed"
    scheduled = command("commitment.open", "cos.obligation", "OBL-cos-followup-0002", commitment("OBL-cos-followup-0002"),
                        command_id="c3", origin="scheduled")
    assert store.submit(scheduled)["code"] == "payload_invalid"


def test_must_not_miss_needs_robert_s_own_turn(store):
    sched = command("obligation.define", "cos.obligation", "OBL-tax", definition("OBL-tax", must_not_miss=True),
                    command_id="m1", origin="scheduled")
    assert store.submit(sched)["code"] == "not_authorized"
    own = command("obligation.define", "cos.obligation", "OBL-tax", definition("OBL-tax", must_not_miss=True), command_id="m2")
    assert store.submit(own)["outcome"] == "accepted"


# ---- occurrences ------------------------------------------------------------------------------------------------------

def test_one_nudge_claim_at_a_time_and_no_nudge_without_its_claim(store):
    define(store)
    s = Stream(store)
    assert s.send(event("e1", "nudged", 0, claim_id="k1"))["code"] == "transition_not_allowed"
    assert s.send(event("e2", "nudge_claimed", 0, claim_id="k1"))["outcome"] == "accepted"
    assert s.send(event("e3", "nudge_claimed", 0, claim_id="k2"))["code"] == "transition_not_allowed"
    assert s.send(event("e4", "nudged", 0, claim_id="k1"))["outcome"] == "accepted"
    assert store.occurrence("OBL-german-daily@2026-10-30").state == "nudged"


def test_acknowledging_never_completes_and_completion_needs_evidence(store):
    define(store)
    s = Stream(store)
    assert s.send(event("e1", "acknowledged", 0))["outcome"] == "accepted"
    assert store.occurrence("OBL-german-daily@2026-10-30").state == "acknowledged"
    assert s.send(event("e2", "satisfied", 0))["code"] == "payload_invalid"          # no evidence_ref
    evidence = {"type": "activity.item", "id": "german-sess-2026-10-30_001"}
    assert s.send(event("e3", "satisfied", 0, evidence_ref=evidence))["outcome"] == "accepted"
    occ = store.occurrence("OBL-german-daily@2026-10-30")
    assert occ.state == "satisfied" and occ.evidence == [evidence]


def test_a_session_before_the_window_means_no_nudge_afterwards(store):
    define(store)
    s = Stream(store)
    evidence = {"type": "activity.item", "id": "german-sess-2026-10-30_001"}
    assert s.send(event("e1", "satisfied", 0, evidence_ref=evidence))["outcome"] == "accepted"
    assert s.send(event("e2", "nudge_claimed", 0, claim_id="k1"))["code"] == "transition_not_allowed"


def test_late_evidence_satisfies_a_missed_day_and_reopening_needs_a_basis(store):
    define(store)
    s = Stream(store)
    assert s.send(event("e1", "missed", 0))["outcome"] == "accepted"
    late = event("e2", "satisfied_late", 0, evidence_ref={"type": "activity.item", "id": "german-sess-late"})
    assert s.send(late)["outcome"] == "accepted"
    assert s.send(event("e3", "reopened", 0))["code"] == "payload_invalid"           # no basis_ref
    assert s.send(event("e4", "reopened", 0, basis_ref=TURN))["outcome"] == "accepted"
    assert store.occurrence("OBL-german-daily@2026-10-30").state == "open"


def test_events_need_their_definition_and_a_retired_one_takes_no_nudges(store):
    s = Stream(store)
    assert s.send(event("e1", "acknowledged", 0, occ="OBL-nothing@2026-10-30"))["code"] == "transition_not_allowed"
    define(store, state="retired")
    assert s.send(event("e2", "nudge_claimed", 0, claim_id="k1"))["code"] == "transition_not_allowed"
    assert s.send(event("e3", "cancelled", 0, basis_ref=TURN))["outcome"] == "accepted"


def test_pause_racing_a_nudge_claim_gives_one_explainable_outcome(store):
    """Harder case: Robert pauses the goal while the scheduler claims its nudge. Both read revision N."""
    define(store)
    n = 0
    claim = command("obligation.event", "cos.occurrence", "OBL-german-daily@2026-10-30",
                    event("e1", "nudge_claimed", n, claim_id="k1"), command_id="claim", expected=n,
                    origin="scheduled", actor="scheduler", seat=None, basis=None)
    postpone = command("obligation.event", "cos.occurrence", "OBL-german-daily@2026-10-30",
                       event("e2", "postponed", n, basis_ref=TURN), command_id="pause", expected=n)
    first, second = store.submit(postpone), store.submit(claim)
    assert first["outcome"] == "accepted"
    assert second == {"schema": "minimoi.cos.store_result/1", "command_id": "claim", "outcome": "conflict",
                      "current_revision": 1}
    assert store.occurrence("OBL-german-daily@2026-10-30").state == "postponed"
    assert len(store.rows("cos.occurrence")) == 1


def test_an_event_id_is_used_once(store):
    define(store)
    s = Stream(store)
    assert s.send(event("e1", "acknowledged", 0))["outcome"] == "accepted"
    assert s.send(event("e1", "postponed", 0, basis_ref=TURN))["code"] == "transition_not_allowed"


# ---- reporting episodes -----------------------------------------------------------------------------------------------

def test_one_ask_one_escalation_and_closing_by_a_full_record_key(store):
    s = Stream(store)
    send = lambda payload: s.send(payload, target_type="cos.episode")
    assert send(ep_event("x1", "opened", 0))["outcome"] == "accepted"
    assert send(ep_event("x2", "ask_claimed", 0, claim_id="a1"))["outcome"] == "accepted"
    assert send(ep_event("x3", "ask_claimed", 0, claim_id="a2"))["code"] == "transition_not_allowed"
    consult = {"type": "cos.consultation", "id": "cons-0002"}
    assert send(ep_event("x4", "asked", 0, claim_id="a2", consultation_ref=consult))["code"] == "transition_not_allowed"
    assert send(ep_event("x5", "asked", 0, claim_id="a1", consultation_ref=consult))["outcome"] == "accepted"
    assert send(ep_event("x6", "escalation_claimed", 0, claim_id="z1"))["outcome"] == "accepted"
    assert send(ep_event("x7", "escalated", 0, claim_id="z1"))["outcome"] == "accepted"
    assert send(ep_event("x8", "escalation_claimed", 0, claim_id="z2"))["code"] == "transition_not_allowed"
    key = {"domain": "guild-build", "part": "update", "local_date": "2026-10-30", "generation": "8f0c2a91", "seq": 1}
    assert send(ep_event("x9", "closed", 0, closed_by=key))["outcome"] == "accepted"
    ep = store.episode("EP-mc-update@2026-10-30")
    assert ep.state == "closed" and ep.closed_by == key


# ---- streams of decisions, meeting links ------------------------------------------------------------------------------

def test_a_decision_id_is_recorded_once(store):
    dec = {"schema": "minimoi.cos.decision/1", **SCOPE, "id": "DEC-build-phase", "at": TS, "kind": "build_phase",
           "target": {"type": "cos.policy", "id": "reporting.mc.update"}, "basis": {"kind": "robert_words", "ref": TURN},
           "summary": "Build phase active."}
    s = Stream(store)
    assert s.send(dec, target_type="cos.decision", origin="owner_turn", basis=TURN)["outcome"] == "accepted"
    again = command("decision.record", "cos.decision", "DEC-build-phase", dec, command_id="dec-2", expected=1)
    assert store.submit(again)["code"] == "transition_not_allowed"


def test_a_meeting_link_is_written_once(store):
    link = {"schema": "minimoi.cos.meeting_link/1", **SCOPE, "occurrence_id": "OBL-standup@2026-10-31",
            "room_id": "standup", "session_id": "standup-2026-10-31", "event_ids": ["e-01"]}
    assert store.submit(command("meeting.link", "cos.meeting", "OBL-standup@2026-10-31", link, command_id="l1"))["outcome"] == "accepted"
    assert store.submit(command("meeting.link", "cos.meeting", "OBL-standup@2026-10-31",
                                dict(link, event_ids=["e-01", "e-02"]), command_id="l2"))["outcome"] == "conflict"


# ---- recovery ---------------------------------------------------------------------------------------------------------

def test_torn_tails_are_quarantined_and_never_read_as_records(tmp_path):
    root = tmp_path / "store"
    first = StoreWriter(root, ENV, verify_robert_source=verified)
    define(first)
    with open(root / "obligations" / "events.jsonl", "ab") as fh:
        fh.write(b'{"schema": "minimoi.cos.obligation_event/1", "event_id": "torn"')
    with open(root / "journal" / "journal.jsonl", "ab") as fh:
        fh.write(b'{"t": "intent", "command_id": "half')
    second = StoreWriter(root, ENV, verify_robert_source=verified)
    assert second.recover()["quarantined"] == 2
    assert second.rows("cos.occurrence") == []
    quarantined = sorted(p.name for p in (root / "journal").glob("quarantine-*"))
    assert len(quarantined) == 2
    assert Stream(second).send(event("e1", "acknowledged", 0))["outcome"] == "accepted"


def test_an_intent_whose_record_landed_is_recovered_with_its_receipt(tmp_path):
    root = tmp_path / "store"
    writer = StoreWriter(root, ENV, verify_robert_source=verified)
    cmd = command("goal.put", "cos.goal", "GOAL-german-b1", goal())
    result = writer.submit(cmd)
    journal = root / "journal" / "journal.jsonl"
    lines = journal.read_bytes().splitlines(keepends=True)
    journal.write_bytes(b"".join(lines[:-1]))          # crash after the write, before the completed line
    reborn = StoreWriter(root, ENV, verify_robert_source=verified)
    assert reborn.recover() == {"quarantined": 0, "recovered": 1, "abandoned": 0}
    replay = reborn.submit(cmd)
    assert replay["replayed"] is True and replay["receipt_id"] == result["receipt_id"]
    assert json.loads(journal.read_bytes().splitlines()[-1])["recovered"] is True


def test_an_intent_whose_record_never_landed_is_abandoned_and_can_be_sent_again(tmp_path):
    root = tmp_path / "store"
    writer = StoreWriter(root, ENV, verify_robert_source=verified)
    cmd = command("goal.put", "cos.goal", "GOAL-german-b1", goal())
    writer.submit(cmd)
    journal = root / "journal" / "journal.jsonl"
    intent = journal.read_bytes().splitlines(keepends=True)[0]
    journal.write_bytes(intent)                          # crash after the intent, before the write
    (root / "goals" / "GOAL-german-b1.json").unlink()
    reborn = StoreWriter(root, ENV, verify_robert_source=verified)
    assert reborn.recover()["abandoned"] == 1
    again = reborn.submit(cmd)
    assert again["outcome"] == "accepted" and again["replayed"] is False


def test_an_unreadable_document_fails_honestly_and_writes_nothing(store):
    store.submit(command("goal.put", "cos.goal", "GOAL-german-b1", goal()))
    path = store.root / "goals" / "GOAL-german-b1.json"
    path.write_text("{not json")
    result = store.submit(command("goal.put", "cos.goal", "GOAL-german-b1", goal(state="paused"), command_id="cmd-2", expected=1))
    assert result == {"schema": "minimoi.cos.store_result/1", "command_id": "cmd-2", "outcome": "failed",
                      "code": "store_unavailable"}
    assert path.read_text() == "{not json"


# ---- the contracts hold on disk ---------------------------------------------------------------------------------------

def test_everything_stored_validates_against_the_frozen_contracts(store):
    store.submit(command("goal.put", "cos.goal", "GOAL-german-b1", goal()))
    define(store)
    s = Stream(store)
    s.send(event("e1", "nudge_claimed", 0, claim_id="k1"))
    s.send(event("e2", "nudged", 0, claim_id="k1"))
    s.send(ep_event("x1", "opened", 0), target_type="cos.episode")
    for path in list((store.root / "goals").glob("*.json")) + list((store.root / "obligations" / "definitions").glob("*.json")):
        doc = json.loads(path.read_text())
        definition_name = {"minimoi.cos.goal/1": "goal", "minimoi.cos.obligation_definition/1": "obligation_definition"}[doc["schema"]]
        assert not contracts.errors("store-records", definition_name, doc), path
    for row in store.rows("cos.occurrence"):
        name = "obligation_event" if row["schema"] == "minimoi.cos.obligation_event/1" else "episode_event"
        assert not contracts.errors("store-records", name, row)
