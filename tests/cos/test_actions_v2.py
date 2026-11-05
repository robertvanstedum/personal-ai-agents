"""F2 step 0b: the actions tool v2 contract. Identity comes from the binding, never from the agent's arguments."""
from datetime import datetime, timezone

import pytest

from domains.cos.store import actions_v2 as v2
from domains.cos.store.writer import StoreWriter

AT = datetime(2026, 11, 4, 23, 0, tzinfo=timezone.utc)


@pytest.fixture
def writer(tmp_path):
    return StoreWriter(tmp_path / "store", "dev")


def call(n=1, scope="owner", env="dev", at=AT):
    return v2.BoundCall(environment=env, scope=scope, call_id=f"run-1:tool-{n}", at=at)


def test_lifecycle_traces_every_row_to_its_call(writer):
    opened = v2.serve(writer, call(1), "open", {"title": "Draft deck", "kind": "action"})
    assert opened["status"] == 200, opened
    row = opened["body"]["row"]
    item = row["item_id"]
    assert row["opened_by"] == "cos" and row["title_provenance"] == "agent_draft"
    assert opened["body"]["call_ref"] == call(1).ref
    updated = v2.serve(writer, call(2), "update", {"item_id": item, "revision": 1, "status": "doing"})
    assert updated["body"]["row"]["revision"] == 2
    commented = v2.serve(writer, call(3), "comment", {"item_id": item, "revision": 2, "text": "Ready"})
    assert commented["body"]["row"]["comments"][0]["author"] == "cos"
    closed = v2.serve(writer, call(4), "close", {"item_id": item, "revision": 3, "outcome": "done"})
    assert closed["body"]["row"]["status"] == "done"
    journal = (writer.root / "journal" / "journal.jsonl").read_text()
    for n, out in enumerate((opened, updated, commented, closed), 1):
        assert "cmd-cos-v2-" + call(n).ref[3:] in journal
        assert out["body"]["result"]["receipt_id"] in journal


def test_replayed_call_returns_first_receipt_and_one_row(writer):
    first = v2.serve(writer, call(1), "open", {"title": "Once"})
    again = v2.serve(writer, call(1), "open", {"title": "Once"})
    assert again["status"] == 200 and again["body"]["result"]["replayed"] is True
    assert again["body"]["result"]["receipt_id"] == first["body"]["result"]["receipt_id"]
    assert len(writer.docs("cos.in_flight")) == 1


def test_same_call_with_other_arguments_is_refused(writer):
    v2.serve(writer, call(1), "open", {"title": "Once"})
    other = v2.serve(writer, call(1), "open", {"title": "Twice"})
    assert other["status"] == 409 and other["body"]["error"] == "command_id_reused"
    assert len(writer.docs("cos.in_flight")) == 1


def test_different_calls_open_different_rows(writer):
    v2.serve(writer, call(1), "open", {"title": "A"})
    v2.serve(writer, call(2), "open", {"title": "A"})
    assert len(writer.docs("cos.in_flight")) == 2


@pytest.mark.parametrize("extra", [{"actor": "robert"}, {"seat": "mc"}, {"origin": "owner_turn"},
                                   {"environment": "prod"}, {"op_id": "operation-00000001"},
                                   {"at": "2020-01-01T00:00:00Z"}, {"by": "mc"}, {"provenance": "approved"},
                                   {"room": "r-1"}, {"basis_ref": {"type": "cos.turn", "id": "t"}}])
def test_identity_fields_in_arguments_are_refused(writer, extra):
    out = v2.serve(writer, call(1), "open", {"title": "Test", **extra})
    assert out["status"] == 422 and out["body"]["error"] == "unexpected_fields"
    assert writer.docs("cos.in_flight") == []


def test_meeting_scope_reads_but_never_writes(writer):
    item = v2.serve(writer, call(1), "open", {"title": "Owner row"})["body"]["row"]["item_id"]
    assert v2.serve(writer, call(2, "meeting"), "list", {})["status"] == 200
    assert v2.serve(writer, call(3, "meeting"), "get", {"item_id": item})["status"] == 200
    for op, args in (("open", {"title": "x"}), ("update", {"item_id": item, "revision": 1, "title": "y"}),
                     ("comment", {"item_id": item, "revision": 1, "text": "z"}),
                     ("close", {"item_id": item, "revision": 1, "outcome": "done"})):
        out = v2.serve(writer, call(9, "meeting"), op, args)
        assert out["status"] == 403 and out["body"]["error"] == "scope_read_only"
    assert writer.get("cos.in_flight", item)["revision"] == 1


@pytest.mark.parametrize("bad", [None, "not-a-call",
                                 v2.BoundCall(environment="dev", scope="owner", call_id="", at=AT),
                                 v2.BoundCall(environment="dev", scope="owner", call_id="x",
                                              at=datetime(2026, 11, 4))])
def test_unbound_calls_are_refused(writer, bad):
    assert v2.serve(writer, bad, "open", {"title": "x"})["body"]["error"] == "unbound"
    assert writer.docs("cos.in_flight") == []


def test_wrong_environment_and_unknown_scope(writer):
    assert v2.serve(writer, call(env="prod"), "list", {})["body"]["error"] == "wrong_environment"
    assert v2.serve(writer, call(scope="peer"), "list", {})["body"]["error"] == "scope_not_allowed"
    assert v2.serve(writer, call(), "memory", {})["body"]["error"] == "invalid_operation"


def test_completion_uses_close_and_updates_need_a_revision(writer):
    assert v2.serve(writer, call(1), "open", {"title": "x", "status": "done"})["body"]["error"] == \
        "use_close_for_completion"
    assert v2.serve(writer, call(2), "update", {"item_id": "IF-abc", "title": "x"})["body"]["error"] == \
        "read_current_revision_first"


def test_v1_module_is_untouched_by_v2():
    from domains.cos.store import actions_api
    assert actions_api.PREFIX == "/internal/cos-actions/v1"
