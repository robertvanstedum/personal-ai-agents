"""MC asks its Apprentice, end to end against the real task service and the real worker (spec v0.4 HA1, HA5, HA8, HA11)."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from core.mc_tools import catalogue, effects
from core.mc_tools.tasks_adapter import TasksAdapter
from services.helper_seats import spend, types as T
from services.helper_seats.worker import Worker
from services.partnership_tasks.charters import Charter, Charters
from tests.partnership.synth import service
from tests.partnership.test_build_item import Client, Host, _Receipts, caller
from tests.partnership.test_helper_charters import KIND
from tests.partnership.test_helper_worker import Client as WorkerClient

CHARTER = Charter("apprentice", 1, "sha256:" + "a" * 64, frozenset({"mc"}), frozenset({"personal"}), {"repo-lookup": KIND, "hygiene-check": dict(KIND, model="deterministic")}, 1.0)
SAID = "Where is the build log template?"


@pytest.fixture
def rig(tmp_path):
    receipts = _Receipts()
    patches = [mock.patch.object(effects, "receipt_exists", receipts.exists), mock.patch.object(effects, "begin_standing_effect", receipts.begin),
               mock.patch.object(effects, "reconcile_effect", receipts.reconcile), mock.patch.object(effects, "record_evidence", lambda *a, **k: "ev-1"),
               mock.patch.object(effects, "RECEIPTS", SimpleNamespace(finish=receipts.finish, read=receipts.read))]
    [p.start() for p in patches]
    svc = service(tmp_path / "tasks")
    svc.charters = Charters([CHARTER])
    from services.partnership_tasks.auth import Bindings, token_sha256
    from tests.partnership import synth
    recs = [{"binding_id": f"bind-{t[4:]}", "token_sha256": token_sha256(t), "seat": s, "principal": "principal", "environment": "synthetic", "origin": o, "scopes": sc, "read_scopes": r, "operations": sorted(set(ops))}
            for t, (s, o, ops, sc, r) in synth.SEATS.items() if t not in ("tok-expired", "tok-revoked")]
    recs.append({"binding_id": "bind-apprentice", "token_sha256": token_sha256("tok-apprentice"), "seat": "apprentice", "principal": "principal", "environment": "synthetic", "origin": "agent",
                 "scopes": ["personal"], "read_scopes": ["personal"], "operations": ["task.transition", "source_event.record", "task.submit_artifacts"]})
    svc.bindings = Bindings(recs)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build_log.html").write_text("the build log template lives here")
    ledger = spend.Ledger(tmp_path / "spend", daily_cap=1.0)
    ctx = lambda task, kind, n: T.Ctx(repo_root=repo, read_items=lambda: [], ask_model=lambda p, q: "It's build_log.html.")   # noqa: E731
    worker = Worker(WorkerClient(svc, "tok-apprentice"), "apprentice", CHARTER, environment="synthetic", ledger=ledger, ctx_for=ctx)
    profiles = json.loads(Path("config/partnership/executor_profiles.json").read_text())
    ticks = []
    adapter = TasksAdapter(Client(svc, "tok-mc"), environment="synthetic", card_ref="a" * 40, profiles_doc=profiles, platform=Client(svc, "tok-platform"),
                           charters=Charters([CHARTER]), sleep=lambda s: (ticks.append(s), worker.pass_once() if rig_state["work"] else None))
    rig_state = {"work": True}
    yield SimpleNamespace(svc=svc, adapter=adapter, host=Host(None, SAID), worker=worker, state=rig_state, receipts=receipts, ticks=ticks)
    [p.stop() for p in patches]


def ask(rig, type_id="repo-lookup"):
    args = catalogue.validate("minimoi_helper_ask", {"type_id": type_id, "question": "where is the build log template?"})
    return rig.adapter.helper_ask(rig.host, args, caller())


def review(rig, r, verdict="approve"):
    args = catalogue.validate("minimoi_helper_review", {"task_id": r["data"]["task_id"], "set_digest": r["data"]["set_digest"], "verdict": verdict, "reasons": "I checked the file."})
    return rig.adapter.helper_review(rig.host, args, caller())


def test_it_answers_in_the_turn_with_who_drafted_it(rig):
    r = ask(rig)
    assert (r["status"], r["code"]) == ("done", "helper_result"), r
    assert r["data"]["drafted_by"] == "apprentice" and "build_log.html" in r["data"]["result"]
    assert r["data"]["accepted"] is False


def test_the_lead_accepts_and_the_credit_comes_from_the_record(rig):
    r = ask(rig)
    a = review(rig, r)
    assert a["status"] == "done", a
    assert "drafted by apprentice, accepted by you" in a["message"]
    assert rig.svc.get("task", r["data"]["task_id"], "tok-mc")["state"] == "completed"


def test_sent_back_it_comes_round_again(rig):
    r = ask(rig)
    assert review(rig, r, "changes_requested")["status"] == "done"
    rig.worker.pass_once()
    assert len(rig.svc.get("task", r["data"]["task_id"], "tok-mc")["artifact_sets"]) == 2


class TestTimeout:
    def test_it_says_still_running_and_never_promises_to_return(self, rig):
        rig.state["work"] = False
        r = ask(rig)
        assert r["code"] == "still_running"
        assert "can't come back on your own" in r["message"]
        assert sum(rig.ticks) >= rig.adapter.HELPER_WAIT_S

    def test_the_late_result_is_on_the_same_task(self, rig):
        rig.state["work"] = False
        first = ask(rig)
        rig.worker.pass_once()
        args = catalogue.validate("minimoi_helper_result", {"task_id": first["data"]["task_id"]})
        late = rig.adapter.helper_result(rig.host, args, caller())
        assert late["code"] == "helper_result" and "build_log.html" in late["data"]["result"]

    def test_a_retry_makes_no_second_task(self, rig):
        rig.state["work"] = False
        first = ask(rig)
        rig.receipts.rows.clear()
        second = ask(rig)
        assert second["data"]["task_id"] == first["data"]["task_id"]
        assert len(list(Path(rig.svc.journal.root, "tasks").glob("TK-mc-*"))) == 1


def test_an_unchartered_type_is_refused_before_anything_exists(rig):
    rig.adapter.charters = Charters([Charter("apprentice", 1, CHARTER.digest, CHARTER.leads, CHARTER.scopes, {"hygiene-check": KIND}, 1.0)])
    r = ask(rig)
    assert r["code"] == "not_chartered"
    assert not list(Path(rig.svc.journal.root, "tasks").glob("TK-mc-*"))


def test_keep_here_hands_nothing_over(rig):
    rig.host.note = "Look that up but keep this here for now."
    assert ask(rig)["code"] == "kept_here"


def test_off_without_a_charter(rig):
    rig.adapter.charters = None
    assert ask(rig)["status"] == "refused"


@pytest.mark.parametrize("path", ["mc_adapter", "shared_lead"])
def test_review_reasons_are_retained_and_retries_reuse_the_evidence(rig, path):
    from core.canonical_json import digest
    from services.helper_seats import lead

    response = ask(rig)
    tid = response["data"]["task_id"]

    def submit_review():
        if path == "mc_adapter":
            assert review(rig, response)["status"] == "done"
        else:
            lead.review(rig.adapter.client, tid, response["data"]["set_digest"],
                        "approve", "I checked the file.", "retained-review", "synthetic", evidence_client=rig.adapter.platform)

    submit_review()
    task = rig.svc.get("task", tid, "tok-mc")
    retained = task["reviews"][-1]["review_ref"]
    assert retained["store"] == "task_service"
    source = rig.svc.get("source_event", retained["id"], "tok-mc")
    assert source["content"] == "I checked the file."
    assert retained["digest"] == digest(source["content"])
    assert source["producer"] == "platform"
    submit_review()
    retried = rig.svc.get("task", tid, "tok-mc")
    assert retried["reviews"] == task["reviews"]
    assert retried["acceptances"] == task["acceptances"]
