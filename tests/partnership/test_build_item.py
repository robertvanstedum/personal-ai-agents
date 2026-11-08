"""MC front door spec A9: "build it" on one Build Log item. The task service and the Build Log store are the real ones; the
turn binding and the receipts are stood in for. A new revision must refuse the old approval by a named error."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from core.mc_tools import catalogue, effects, work_item
from core.mc_tools.tasks_adapter import TasksAdapter
from domains.guild import queue_store as qs
from tests.partnership.synth import Flow, cmd, ref, service

CONV, NOTE = "c-0123456789abcdef", "n-owner-note-1"
SAID = "Build it. Build Log #8, the language inbox."


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass


class _Receipts:
    def __init__(self):
        self.rows = {}

    def exists(self, conn, principal, environment, op):
        return op in self.rows

    def begin(self, conn, b, req, *, operation_id, worker):
        row = self.rows[operation_id] = {"receipt_id": f"rc-{len(self.rows) + 1}", "state": "dispatched", "outcome": None}

        def finish(conn, kind, outcome, *, verified):
            row.update(state="verified" if verified else "failed", outcome=outcome)
            return row
        return SimpleNamespace(finish=finish, unknown=lambda conn: row.update(state="unknown"))

    def reconcile(self, conn, *, principal, environment, operation_id, worker):
        return None

    def finish(self, conn, lease, state, *, evidence_refs=()):
        pass

    def read(self, conn, principal, environment, op):
        return self.rows[op]


class Client:
    """A seat's view of the real task service."""

    def __init__(self, svc, token):
        self.svc, self.token = svc, token

    def submit(self, c):
        return self.svc.submit(c, self.token)

    def get(self, kind, rid):
        return self.svc.get(kind, rid, self.token)


class Host:
    principal, environment, worker = "robert", "synthetic", "mc-tools"

    def __init__(self, store, note):
        self.build_log, self.note = store, note

    def _tx(self):
        return _Conn()

    def _bind(self, conn, caller):
        return SimpleNamespace(conversation_id=CONV, note_request_id=NOTE, policy_version="mc-policy:x:y", context=lambda *a: None)

    def _bind_again(self, conn, caller, b):
        return b

    def _owner_words(self, b):
        return self.note, None

    def _kept_here(self, text, what):
        from core.mc_tools import keep_here
        return {"status": "refused", "code": "kept_here"} if keep_here.says_keep_here(text) else None

    def _operation_id(self, b, capability, canonical):
        seed = json.dumps([capability, canonical], sort_keys=True).encode()
        h = hashlib.sha256(seed).hexdigest()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def _tier2(self, caller):
        return True


@pytest.fixture
def rig(tmp_path):
    receipts = _Receipts()
    patches = [mock.patch.object(effects, "receipt_exists", receipts.exists),
               mock.patch.object(effects, "begin_standing_effect", receipts.begin),
               mock.patch.object(effects, "reconcile_effect", receipts.reconcile),
               mock.patch.object(effects, "record_evidence", lambda *a, **k: "ev-1"),
               mock.patch.object(effects, "RECEIPTS", SimpleNamespace(finish=receipts.finish, read=receipts.read))]
    [p.start() for p in patches]
    folder = tmp_path / "state" / "guild"
    folder.mkdir(parents=True)
    path = folder / "build_queue.json"
    path.write_bytes(qs.serialize([{"id": 8, "spec_title": "Language inbox", "status": "idea", "blocked_reason": None,
                                    "summary": "Capture corrections.", "owner_words": "Learning everywhere."}]))
    store = qs.QueueStore(path, in_container=False, code_root=folder.parent.parent / "code")
    svc = service(tmp_path / "tasks")
    profiles = json.loads(Path("config/partnership/executor_profiles.json").read_text())
    adapter = TasksAdapter(Client(svc, "tok-mc"), environment="synthetic", card_ref="a" * 40, profiles_doc=profiles,
                           platform=Client(svc, "tok-platform"))
    yield SimpleNamespace(svc=svc, adapter=adapter, host=Host(store, SAID), path=path, receipts=receipts, f=Flow(svc))
    [p.stop() for p in patches]


def caller():
    return SimpleNamespace(tool_call_id="c1", rule=SimpleNamespace(tier=2, max_operations_per_turn=2))


def propose(rig, **over):
    args = catalogue.validate("minimoi_build_item", {"item_id": 8, "done_when": "The inbox takes a correction.", **over})
    return rig.adapter.build_item(rig.host, args, caller())


def edit(rig, **fields):
    doc = json.loads(rig.path.read_text())
    doc[0].update(fields)
    rig.path.write_bytes(qs.serialize(doc))


def interpretation_id(rig):
    folder = next(Path(rig.svc.journal.root).glob("interpretations"))
    return next(p.stem for p in folder.glob("IN-mc-*"))


def test_it_records_his_words_and_the_exact_revision_and_starts_nothing(rig):
    r = propose(rig)
    assert r["status"] == "done", r
    assert "Nothing has started" in r["message"]
    iid = interpretation_id(rig)
    rec = rig.svc.get("interpretation", iid, "tok-mc")
    item = json.loads(rig.path.read_text())[0]
    assert rec["context_refs"] == [work_item.reference(item)]
    assert rec["disposition"] == "proposed"
    source = rig.svc.get("source_event", rec["source_event_ids"][0], "tok-mc")
    assert source["content"] == SAID                                     # his words, from the note, not the model


def test_the_source_event_comes_from_the_platform_seat(rig):
    propose(rig)
    rec = rig.svc.get("interpretation", interpretation_id(rig), "tok-mc")
    assert rig.svc.get("source_event", rec["source_event_ids"][0], "tok-mc")["producer"] == "platform"


def test_a_retry_is_the_same_proposal(rig):
    propose(rig)
    first = interpretation_id(rig)
    rig.receipts.rows.clear()
    r = propose(rig)
    assert r["status"] == "done", r
    assert interpretation_id(rig) == first


def test_an_item_that_does_not_exist_is_refused(rig):
    assert propose(rig, item_id=99)["code"] == "item_not_found"


def test_keep_here_proposes_nothing(rig):
    rig.host.note = "Build it, but keep this here for now."
    assert propose(rig)["code"] == "kept_here"


def test_a_long_note_is_marked_as_cut_never_silently(rig):
    rig.host.note = "x" * 9000
    propose(rig)
    rec = rig.svc.get("interpretation", interpretation_id(rig), "tok-mc")
    content = rig.svc.get("source_event", rec["source_event_ids"][0], "tok-mc")["content"]
    assert "[cut for the task record" in content and hashlib.sha256(("x" * 9000).encode()).hexdigest() in content


def test_without_a_platform_seat_it_is_off(rig):
    rig.adapter.platform = None
    assert propose(rig)["status"] == "refused"


class TestRevisionCheck:
    def ref(self, rig):
        return work_item.reference(json.loads(rig.path.read_text())[0])

    def test_unchanged_passes(self, rig):
        assert work_item.problem(self.ref(rig), rig.host.build_log.read_items) is None

    def test_a_status_move_is_not_a_change(self, rig):
        r = self.ref(rig)
        edit(rig, status="in_build", last_transition_at="2026-11-08T20:00:00Z")
        assert work_item.problem(r, rig.host.build_log.read_items) is None

    @pytest.mark.parametrize("field,value", [("spec_title", "A new title"), ("summary", "Changed."), ("owner_words", "Other words.")])
    def test_an_edit_of_what_it_says_refuses_by_name(self, rig, field, value):
        r = self.ref(rig)
        edit(rig, **{field: value})
        code, words = work_item.problem(r, rig.host.build_log.read_items)
        assert code == "item_changed" and "#8" in words and "Nothing was started" in words

    def test_a_removed_item_is_refused(self, rig):
        r = self.ref(rig)
        rig.path.write_bytes(qs.serialize([]))
        assert work_item.problem(r, rig.host.build_log.read_items)[0] == "item_gone"

    def test_unreadable_fails_closed(self, rig):
        r = self.ref(rig)
        assert work_item.problem(r, None)[0] == "item_unreadable"
        def boom():
            raise OSError("no")
        assert work_item.problem(r, boom)[0] == "item_unreadable"

    def test_a_task_with_no_item_is_not_checked(self, rig):
        assert work_item.problem(None, rig.host.build_log.read_items) is None


class TestDelegate:
    def task(self, rig):
        """Robert confirms the proposal, as the portal does."""
        from core.canonical_json import digest
        propose(rig)
        iid = interpretation_id(rig)
        rec = rig.svc.get("interpretation", iid, "tok-mc")
        action = {"action": "confirm", "interpretation_id": iid, "proposal_digest": digest(rec["proposed"])}
        assert rig.svc.submit(cmd("source_event.record", "source_event", "SE-own-conf",
                                  {"source": {"store": "portal-owner-action", "id": "n-1", "position": 1},
                                   "content": json.dumps(action), "correction_of": None}), "tok-principal")["outcome"] == "accepted"
        assert rig.svc.submit(cmd("interpretation.dispose", "interpretation", iid,
                                  {"disposition": "adopted", "authority": {"kind": "owner_confirmation", "ref": ref("task_service", "SE-own-conf")}},
                                  expected=rig.f.rev("interpretation", iid)), "tok-principal")["outcome"] == "accepted"
        rig.f.request("RQ-mc-0001", rec["source_event_ids"][0], "Build it.", iid=iid, token="tok-principal")
        out = rig.svc.submit(cmd("request.promote", "request", "RQ-mc-0001", {"task_id": "TK-mc-0001", "parent_ref": None, **rec["proposed"]["task"]},
                                 expected=rig.f.rev("request", "RQ-mc-0001")), "tok-principal")
        assert out["outcome"] == "accepted", out
        return rig.svc.get("task", "TK-mc-0001", "tok-mc")

    def delegate(self, rig):
        args = {"task_id": "TK-mc-0001", "kind": "build", "holder": "claude-code", "outcome": "Build the inbox.", "done_when": "It takes a correction."}
        return rig.adapter.delegate(rig.host, args, caller())

    def test_the_task_knows_its_item(self, rig):
        task = self.task(rig)
        assert work_item.bound(rig.adapter.client, task)["id"] == "8"

    def test_a_changed_item_refuses_the_delegate_by_name(self, rig):
        self.task(rig)
        edit(rig, summary="Edited after he said build it.")
        r = self.delegate(rig)
        assert (r["status"], r["code"]) == ("refused", "item_changed")

    def test_the_card_names_the_revision_so_its_digest_covers_it(self, rig):
        task = self.task(rig)
        r = self.delegate(rig)
        assert r["status"] == "done", r
        child = next(p.stem for p in Path(rig.svc.journal.root, "tasks").glob("TK-mc-*") if p.stem != "TK-mc-0001")
        outbox = next(Path(rig.svc.journal.root, "outbox").glob("OB-mc-*")).stem
        card = rig.svc.get("outbox", outbox, "tok-mc")["packet"]
        revision = work_item.bound(rig.adapter.client, task)["edition"]
        assert revision in json.dumps(card)
        assert rig.svc.get("task", child, "tok-mc")["interpretation_ref"] == task["interpretation_ref"]


class TestOwnerApproval:
    def setup_client(self, rig):
        item = json.loads(rig.path.read_text())[0]
        client = mock.Mock()
        client.get.side_effect = lambda kind, rid: {"task": {"interpretation_ref": "IN-1"},
                                                    "interpretation": {"context_refs": [work_item.reference(item)]}}[kind]
        return client

    def problem(self, rig, client):
        from minimoi_portal.guild_ui.tasks_owner_api import _work_item_problem
        with mock.patch.dict("os.environ", {"GUILD_QUEUE_PATH": str(rig.path)}), \
                mock.patch("domains.guild.queue_store.QueueStore", lambda path: rig.host.build_log):
            return _work_item_problem(client, {"task_id": "TK-1"})

    def test_an_unchanged_item_lets_the_card_through(self, rig):
        assert self.problem(rig, self.setup_client(rig)) is None

    def test_a_changed_item_stops_his_click_by_name(self, rig):
        client = self.setup_client(rig)
        edit(rig, summary="Edited after the card was made.")
        assert self.problem(rig, client)[0] == "item_changed"

    def test_no_queue_configured_fails_closed(self, rig):
        from minimoi_portal.guild_ui.tasks_owner_api import _work_item_problem
        with mock.patch.dict("os.environ", {"GUILD_QUEUE_PATH": ""}):
            assert _work_item_problem(self.setup_client(rig), {"task_id": "TK-1"})[0] == "item_unreadable"

    def test_a_card_with_no_item_is_not_affected(self, rig):
        from minimoi_portal.guild_ui.tasks_owner_api import _work_item_problem
        client = mock.Mock()
        client.get.side_effect = lambda kind, rid: {"task": {"interpretation_ref": "IN-1"}, "interpretation": {"context_refs": []}}[kind]
        assert _work_item_problem(client, {"task_id": "TK-1"}) is None
