"""B1A: the partnership contract v1 accepts its synthetic fixtures and refuses the shapes the spec forbids.

SYNTHETIC fixtures only (tests/partnership/fixtures/records_v1.json)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from core.canonical_json import digest
from services.partnership_tasks import contracts

FIX = json.loads((Path(__file__).parent / "fixtures" / "records_v1.json").read_text("utf-8"))
GRANT = {"store": "grants", "id": "grant-syn-1", "edition": "1", "digest": None}


def rec(name, **over):
    record = copy.deepcopy(FIX[name])
    record.update(over)
    return record


@pytest.mark.parametrize("name", ["source_event", "interpretation", "request", "task"])
def test_each_fixture_record_is_valid(name):
    assert contracts.errors("records", name, FIX[name]) == []


def test_fixtures_reference_each_other():
    assert FIX["interpretation"]["source_event_ids"] == [FIX["source_event"]["source_event_id"]]
    assert FIX["request"]["interpretation_ref"] == FIX["interpretation"]["interpretation_id"]
    assert FIX["task"]["request_ref"] == FIX["request"]["request_id"]
    assert FIX["task"]["interpretation_ref"] == FIX["interpretation"]["interpretation_id"]


ACCEPTANCE = {"artifact_set_digest": "sha256:" + "1" * 64, "reviewed_set_digests": ["sha256:" + "1" * 64],
              "review_refs": [{"store": "artifacts", "id": "review-1", "edition": "1", "digest": None}],
              "by": "mc", "task_revision": 4, "disagreements_delivered": []}


@pytest.mark.parametrize("name,over", [
    ("source_event", {"extra": "field"}),                                      # closed shape
    ("source_event", {"environment": "production"}),                           # fixed environments
    ("request", {"state": "done"}),                                            # wire states are pinned (D04)
    ("request", {"data_scope": "company:acme"}),                               # scope names are fixed
    ("request", {"state": "promoted", "task_ref": None}),                      # promotion keeps its link (D02)
    ("request", {"state": "withdrawn", "reason": None}),                       # an ending says why
    ("task", {"state": "cancelled", "reason": "x"}),                           # one spelling: canceled
    ("task", {"state": "waiting", "waiting": None}),                           # waiting needs reason + who
    ("task", {"waiting": {"reason": "input", "who_can_unblock": "principal"}}),  # ...and only while waiting
    ("task", {"state": "completed"}),                                          # completed needs current acceptance
    ("task", {"state": "completed", "acceptances": [dict(ACCEPTANCE, status="superseded")]}),
    ("task", {"state": "failed", "reason": None}),                             # failed says why
    ("task", {"acceptance": None}),                                            # history array, not a single slot
    ("interpretation", {"source_event_ids": []}),                              # always grounded in a source
    ("interpretation", {"authority": {"kind": "adopted_by_cos", "ref": None}}),  # adoption is not authority
    ("interpretation", {"authority": {"kind": "existing_grant", "ref": None}}),  # authority needs its proof (Q02)
    ("interpretation", {"authority": {"kind": "owner_confirmation",
                                      "ref": {"store": "external", "id": "x", "edition": None, "digest": None}}}),
    ("interpretation", {"authority": {"kind": "none", "ref": GRANT}}),
    ("interpretation", {"disposition": "adopted", "disposition_by": None}),    # who adopted is recorded
])
def test_forbidden_shapes_are_refused(name, over):
    assert contracts.errors("records", name, rec(name, **over)) != []


def test_allowed_conditional_shapes_pass():
    assert contracts.errors("records", "task", rec("task", state="completed",
                                                   acceptances=[dict(ACCEPTANCE, status="current")])) == []
    assert contracts.errors("records", "task", rec("task", state="waiting",
                                                   waiting={"reason": "lead_unavailable", "who_can_unblock": "principal"})) == []
    assert contracts.errors("records", "request", rec("request", state="promoted", task_ref="TK-syn-0001")) == []
    assert contracts.errors("records", "interpretation", rec("interpretation", disposition="proposed",
                                                             disposition_by=None, authority={"kind": "none", "ref": None})) == []


def test_assignment_roles_cover_q03_with_effective_times():
    task = rec("task")
    task["assignments"].append({"role": "duty", "seat": "ops", "from_revision": 1, "to_revision": None,
                                "command_id": "cmd-syn-duty-1", "reason": "disk check owner",
                                "effective_from_at": "2026-11-01T00:00:00+00:00", "effective_to_at": None})
    assert contracts.errors("records", "task", task) == []
    del task["assignments"][-1]["effective_from_at"]
    assert contracts.errors("records", "task", task) != []


def _command(kind, target, payload, **over):
    cmd = {"schema": "minimoi.partnership.command/1", "command_id": "cmd-syn-x-1", "kind": kind, "target": target,
           "expected_revision": None, "payload": payload, "payload_digest": digest(payload),
           "environment": "synthetic", "data_scope": "personal", "basis_ref": None, "causal_parent": None,
           "session_id": "syn", "runtime": {"kind": "deterministic", "build": "test"}, "event_at": None}
    cmd.update(over)
    return cmd


SE_PAYLOAD = {"source": {"store": "records", "id": "e1", "position": 1}, "content": "hi", "correction_of": None}


def test_a_command_cannot_declare_its_own_actor_or_origin():
    cmd = _command("source_event.record", {"type": "source_event", "id": "SE-syn-0009"}, SE_PAYLOAD)
    assert contracts.errors("command", "command", cmd) == []
    for field, value in (("actor_seat", "principal"), ("origin", "owner_session")):
        assert contracts.errors("command", "command", dict(cmd, **{field: value})) != []


def test_the_envelope_binds_payload_to_kind_and_target_type_to_id():
    good = _command("source_event.record", {"type": "source_event", "id": "SE-syn-0009"}, SE_PAYLOAD)
    wrong_payload = _command("task.accept", {"type": "task", "id": "TK-syn-0009"}, SE_PAYLOAD)
    wrong_target = dict(good, target={"type": "task", "id": "SE-syn-0009"})
    assert contracts.errors("command", "command", good) == []
    assert contracts.errors("command", "command", wrong_payload) != []
    assert contracts.errors("command", "command", wrong_target) != []


def test_causal_parent_is_a_typed_reference():
    cmd = _command("source_event.record", {"type": "source_event", "id": "SE-syn-0009"}, SE_PAYLOAD,
                   causal_parent={"store": "obligations", "id": "OBL-disk@2026-11-04", "edition": None, "digest": None})
    assert contracts.errors("command", "command", cmd) == []
    assert contracts.errors("command", "command", dict(cmd, causal_parent="cmd-1")) != []


def test_every_kind_has_a_payload_definition():
    defs = json.loads((contracts.contracts_dir() / "command.schema.json").read_text("utf-8"))["$defs"]
    assert sorted(contracts.kinds()) == sorted(k[len("payload."):] for k in defs if k.startswith("payload."))


def test_promotion_payload_names_lead_and_holder_and_a_reserved_task_id():
    payload = {"task_id": "TK-syn-0002", "lead": "mc", "holder": "mc", "outcome": "o", "done_when": "d",
               "return_window": None, "parent_ref": None}
    assert contracts.errors("command", "payload.request.promote", payload) == []
    assert contracts.errors("command", "payload.request.promote", {k: v for k, v in payload.items() if k != "lead"})


def test_pending_promotion_records_the_whole_intent():
    pending = {"operation_id": "cmd-syn-pr-1", "kind": "promotion", "payload_digest": "sha256:" + "2" * 64,
               "expected_request_revision": 3, "reserved_task_id": "TK-syn-0002"}
    assert contracts.errors("records", "request", rec("request", pending_operation=pending)) == []
    for key in ("payload_digest", "expected_request_revision", "reserved_task_id"):
        short = {k: v for k, v in pending.items() if k != key}
        assert contracts.errors("records", "request", rec("request", pending_operation=short)) != []


def test_results_and_receipt_lookups_carry_what_their_outcome_promises():
    ok = {"schema": "minimoi.partnership.result/1", "command_id": "c-1", "outcome": "accepted",
          "revision": 1, "receipt_id": "rcpt-" + "a" * 16, "replayed": False}
    assert contracts.errors("command", "result", ok) == []
    assert contracts.errors("command", "result", {k: v for k, v in ok.items() if k != "receipt_id"}) != []
    lookup = {"schema": "minimoi.partnership.receipt_lookup/1", "command_id": "c-1", "answer": "recorded", "result": None}
    assert contracts.errors("command", "receipt_lookup", lookup) != []
    assert contracts.errors("command", "receipt_lookup", dict(lookup, result=ok)) == []
    assert contracts.errors("command", "receipt_lookup", dict(lookup, answer="absent")) == []


def test_journal_entries_are_typed_and_closed():
    ev = {"actor_seat": "mc", "origin": "agent", "session_id": "s", "runtime": {"kind": "deterministic"},
          "causal_parent": None, "basis_ref": None, "environment": "synthetic", "data_scope": "personal",
          "event_at": None, "targets": ["requests/RQ-syn-0001.json"], "binding_id": "bind-mc-syn",
          "revisions": {"requests/RQ-syn-0001.json": 1},
          "links": {"source_event_id": None, "interpretation_id": None, "request_id": "RQ-syn-0001",
                    "task_id": None, "authority_ref": None}}
    row = dict(ev, t="completed", seq=2, command_id="cmd-c-1", payload_digest="sha256:" + "3" * 64,
               kind="request.record", receipt_id="rcpt-" + "b" * 16, revision=1, recovered=False,
               recorded_at="2026-11-04T15:00:00+00:00", prev_hash="sha256:" + "4" * 64, hash="sha256:" + "5" * 64)
    assert contracts.errors("evidence", "journal_entry", row) == []
    assert contracts.errors("evidence", "journal_entry", dict(row, origin="owner")) != []
    assert contracts.errors("evidence", "journal_entry", dict(row, runtime=5)) != []
    assert contracts.errors("evidence", "journal_entry", dict(row, surprise=1)) != []
    assert contracts.errors("evidence", "journal_entry", {"t": "abandoned", "seq": 3, "command_id": "cmd-c-2",
                                                          "recorded_at": row["recorded_at"],
                                                          "prev_hash": row["hash"], "hash": row["hash"]}) != []


def test_seat_binding_stores_a_token_hash_never_a_token():
    binding = {"binding_id": "bind-mc-syn", "token_sha256": "a" * 64, "seat": "mc", "principal": "principal",
               "environment": "synthetic", "origin": "agent", "scopes": ["personal"], "read_scopes": ["personal"],
               "operations": ["request.record"]}
    assert contracts.errors("evidence", "seat_binding", binding) == []
    assert contracts.errors("evidence", "seat_binding", dict(binding, token="secret")) != []
    assert contracts.errors("evidence", "seat_binding", dict(binding, operations=["anything.goes"])) != []
    assert contracts.errors("evidence", "seat_binding", dict(binding, operations=["request.record"] * 2)) != []


def test_utc_is_a_valid_zone():
    task = rec("task", return_window={"at": "2026-11-05T14:00:00+00:00", "timezone": "UTC",
                                      "basis": "stated_by_principal", "basis_ref": None})
    assert contracts.errors("records", "task", task) == []
