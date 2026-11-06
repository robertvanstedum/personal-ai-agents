"""1.2 Beta blocker B4: failures go quiet (5 Nov, partnership status). SYNTHETIC, no Postgres, no provider.

- A failed child raises exactly one owner notice, through the existing notice path, from the runner door to the store.
- A review that ends without a valid verdict is recorded and reported as that, and is never an approval.
- A real review card states the one final answer that can count, and that answer passes the runner's and the door's
  own checks.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.mc_delegation_dispatcher import cards as card_mod
from core.mc_delegation_runner.runner import _verdict_doc
from core.mc_tools.tasks_adapter import TasksAdapter
from services.partnership_tasks import contracts, worker
from services.partnership_tasks.notice_port import PortalNotices, words_for
from services.partnership_tasks.reconstruct import outbox_queue
from services.partnership_tasks.runner_door import RunnerDoor, artifact_refs, set_digest, verdict_of
from tests.partnership.fake_harness import FakeHarness
from tests.partnership.synth import Flow, cmd, ref, service
from tests.partnership.test_worker import FakeDoor

NOW = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)
D = "tok-drainer"
PATCH = {"kind": "patch", "ref": "C-b.patch", "sha256": hashlib.sha256(b"patch").hexdigest(), "bytes": 5,
         "author": "codex", "review_status": "unreviewed"}
ROOT = Path(__file__).resolve().parents[2]
REAL = json.loads((ROOT / "config/partnership/executor_profiles.json").read_text())
FAKE = json.loads((ROOT / "config/partnership/executor_profiles.fakework.json").read_text())
SET = "sha256:" + "5" * 64


class Seat:
    def __init__(self, svc, token):
        self.svc, self.token = svc, token

    def submit(self, command):
        return self.svc.submit(command, self.token)

    def get(self, t, i):
        return self.svc.get(t, i, self.token)


class Drainer:
    def __init__(self, svc):
        self.svc = svc

    def outbox_pending(self):
        return outbox_queue(self.svc, D)


@pytest.fixture
def rig(tmp_path):
    svc = service(tmp_path / "tasks", clock=lambda: NOW.isoformat())
    f = Flow(svc)
    tid = f.promoted_task()
    f.transition(tid, "working", evidence=[ref("harness", "pickup-1")])
    door = RunnerDoor(None, principal="robert", environment="dev", reader=Seat(svc, "tok-principal"),
                      seats={"codex": Seat(svc, "tok-codex"), "claude-code": Seat(svc, "tok-claude-code"),
                             "worker-a": Seat(svc, "tok-worker")},
                      task_environment="synthetic")
    return svc, f, tid, door


def delegate(svc, parent, child, holder):
    t = svc.get("task", parent, "tok-principal")
    res = svc.submit(cmd("task.delegate", "task", parent,
                         {"child_task_id": child, "holder": holder, "outcome": "Draft section 2.",
                          "done_when": "Lead accepts it.", "return_window": None,
                          "fence_generation": t["fence_generation"]}, expected=t["revision"]), "tok-mc")
    assert res["outcome"] == "accepted", res
    return child


def item(state, step="build", **kw):
    return {"request_id": "11111111-1111-1111-1111-111111111111", "generation": 1, "step": step, "state": state,
            "result_event_id": "22222222-2222-2222-2222-222222222222", "artifacts": [], "output_text": None,
            "input_artifacts": [], **kw}


def op_for(svc, child, items):
    return {"task_id": child, "fence": svc.get("task", child, "tok-principal")["fence_generation"], "_items": items}


def notices(svc, tid):
    return [ob for ob in outbox_entries(svc) if ob["task_id"] == tid and ob["operation"] == "notify"]


def outbox_entries(svc):
    folder = svc.journal.root / "outbox"
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []


def pass_with_notices(svc, port):
    return worker.one_pass(drainer=Drainer(svc), svc=svc, harness=FakeHarness(svc.journal.root.parent / "h"),
                           notices=port, door=None, token=D, clock=lambda: NOW)


# -- a failed child ----------------------------------------------------------------------------------------------------

def test_a_failed_child_raises_exactly_one_owner_notice_from_the_door_to_the_store(rig):
    svc, f, tid, door = rig
    child = delegate(svc, tid, "TK-syn-0001c1", "worker-a")
    bad = item("timed_out")
    sent = door.settle(op_for(svc, child, [bad]), {})
    assert ("task.transition:failed", child, "accepted") in sent
    assert svc.get("task", child, "tok-principal")["state"] == "failed"
    assert door.settle(op_for(svc, child, [bad]), {}) == []                       # the door's next pass sends nothing

    found = notices(svc, child)
    assert len(found) == 1 and found[0]["destination"] == "notices" and found[0]["state"] == "pending"
    packet = found[0]["packet"]
    assert packet["kind"] == "child_failed" and packet["parent_task_id"] == tid and packet["holder"] == "worker-a"

    store = FakeDoor()
    port = PortalNotices("http://portal:5001", "t" * 40, lambda op: svc.get("outbox", op, D), session=store)
    first = pass_with_notices(svc, port)
    assert [state for op, state in first["drained"] if op == found[0]["operation_id"]] == ["acknowledged"]
    pass_with_notices(svc, port)                                                  # settled: it never goes again
    assert len(store.items) == 1 and len(store.calls) == 1
    words = next(iter(store.items.values()))
    assert child in words and tid in words and "failed" in words and "timed_out" in words and "Draft section 2." in words


def test_only_a_failure_of_a_child_raises_it(rig):
    svc, f, tid, door = rig
    child = delegate(svc, tid, "TK-syn-0001c1", "worker-a")
    # The parent ending cancels its children; that is not a failure of the child.
    assert f.transition(tid, "failed", reason="Stopped.", evidence=[ref("records", "e")])["outcome"] == "accepted"
    assert svc.get("task", child, "tok-principal")["state"] == "canceled"
    assert notices(svc, child) == [] and notices(svc, tid) == []


def test_a_notice_that_cannot_be_sent_does_not_hold_the_parent(rig):
    svc, f, tid, door = rig
    child = delegate(svc, tid, "TK-syn-0001c1", "worker-a")
    door.settle(op_for(svc, child, [item("failed")]), {})
    assert notices(svc, child)[0]["state"] == "pending"                           # no channel configured: never dropped
    current = f.ready_for_acceptance(tid, tag="p")
    assert f.accept(tid, current)["outcome"] == "accepted"


def test_a_child_that_fails_again_after_a_reopen_is_a_new_notice(rig):
    svc, f, tid, door = rig
    child = delegate(svc, tid, "TK-syn-0001c1", "worker-a")
    assert f.transition(child, "failed", token="tok-worker", reason="Red.",
                        evidence=[ref("harness", "c-1")])["outcome"] == "accepted"
    t = svc.get("task", child, "tok-principal")
    assert svc.submit(cmd("task.reopen", "task", child, {"reason": "Try again."},
                          expected=t["revision"]), "tok-mc")["outcome"] == "accepted"
    assert f.transition(child, "working", token="tok-worker", evidence=[ref("harness", "c-2")])["outcome"] == "accepted"
    assert f.transition(child, "failed", token="tok-worker", reason="Red again.",
                        evidence=[ref("harness", "c-3")])["outcome"] == "accepted"
    assert sorted(n["packet"]["reason"] for n in notices(svc, child)) == ["Red again.", "Red."]


# -- a review without a verdict ----------------------------------------------------------------------------------------

def reviewed_rig(rig):
    svc, f, tid, door = rig
    builder = delegate(svc, tid, "TK-syn-0001b1", "codex")
    f.transition(builder, "working", token="tok-codex", evidence=[ref("harness", "b")])
    t = svc.get("task", builder, "tok-principal")
    refs = artifact_refs([PATCH])
    assert svc.submit(cmd("task.submit_artifacts", "task", builder, {"artifacts": refs,
                                                                    "fence_generation": t["fence_generation"]},
                          expected=t["revision"]), "tok-codex")["outcome"] == "accepted"
    review = delegate(svc, tid, "TK-syn-0001r1", "claude-code")
    f.transition(review, "working", token="tok-claude-code", evidence=[ref("harness", "r")])
    return svc, f, tid, door, builder, review, set_digest(refs)


def review_item(text, artifacts):
    return item("completed", step="review", output_text=text, artifacts=artifacts,
                input_artifacts=[{"ref": PATCH["ref"], "sha256": PATCH["sha256"]}])


def verdict_artifact(text, sha=None):
    return {"kind": "verdict", "ref": "C-r.verdict", "sha256": sha or hashlib.sha256(text.encode()).hexdigest(),
            "bytes": len(text), "author": "claude-code", "review_status": "unreviewed"}


def verdict_text(reviewed):
    return json.dumps({"schema": "minimoi.partnership.review_verdict/1", "verdict": "approve",
                       "reviewed_set_digest": reviewed, "findings_digest": None})


@pytest.mark.parametrize("case", ["plain_words", "other_set", "bad_hash"])
def test_a_review_without_a_valid_verdict_is_reported_as_that_and_never_approves(rig, case):
    svc, f, tid, door, builder, review, reviewed = reviewed_rig(rig)
    if case == "plain_words":
        text = "Looks good to me, approved."
        it = review_item(text, [{"kind": "output", "ref": "C-r.output", "sha256": hashlib.sha256(text.encode()).hexdigest(),
                                 "bytes": len(text), "author": "claude-code", "review_status": "unreviewed"}])
    elif case == "other_set":
        text = verdict_text("sha256:" + "9" * 64)
        it = review_item(text, [verdict_artifact(text)])
    else:
        text = verdict_text(reviewed)
        it = review_item(text, [verdict_artifact(text, sha="0" * 64)])
    op = op_for(svc, review, [it])
    door.settle(op, {reviewed: builder})
    t = svc.get("task", builder, "tok-principal")
    assert [(r["verdict"], r.get("no_verdict")) for r in t["reviews"]] == [("changes_requested", True)]
    found = notices(svc, builder)
    assert len(found) == 1 and found[0]["packet"]["kind"] == "review_without_verdict"
    words = words_for(found[0]["packet"])
    assert "without a valid verdict" in words and "not an approval" in words and "claude-code" in words
    # The lead cannot accept on it, and a repeated pass adds nothing.
    assert f.accept(builder, reviewed, ack=[t["reviews"][0]["review_id"]])["detail"] == "no_non_author_approval_of_this_set"
    door.settle(op_for(svc, review, [it]), {reviewed: builder})
    assert len(notices(svc, builder)) == 1


def test_a_valid_verdict_is_no_such_report(rig):
    svc, f, tid, door, builder, review, reviewed = reviewed_rig(rig)
    text = verdict_text(reviewed)
    door.settle(op_for(svc, review, [review_item(text, [verdict_artifact(text)])]), {reviewed: builder})
    t = svc.get("task", builder, "tok-principal")
    assert [r["verdict"] for r in t["reviews"]] == ["approve"] and "no_verdict" not in t["reviews"][0]
    assert notices(svc, builder) == []


def test_a_carried_no_verdict_review_and_its_return_words_say_so():
    assert "codex no verdict" in words_for({"kind": "return_packet", "task_id": "TK-1", "outcome": "x", "accepted_by": "mc",
                                            "accepted_set": "sha256:" + "a" * 64, "artifacts": [],
                                            "disagreements_delivered": [{"reviewer": "codex", "no_verdict": True,
                                                                         "verdict": "changes_requested"}]})


# -- the real review card ----------------------------------------------------------------------------------------------

def adapter(doc):
    return TasksAdapter(client=None, environment="dev", card_ref="a" * 40, profiles_doc=doc)


CANONICAL = {"outcome": "Review the patch.", "done_when": "The reviewer answers."}


def answer_in(brief):
    return next(line for line in brief.splitlines() if line.startswith("{"))


@pytest.mark.parametrize("holder", ["claude-code", "codex"])
def test_a_real_review_brief_states_the_exact_answer_and_that_answer_counts(holder):
    brief = adapter(REAL)._brief("review", holder, CANONICAL, SET)
    assert brief.startswith("Review the patch.\nDone when: The reviewer answers.\n")
    assert "minimoi.partnership.review_verdict/1" in brief and SET in brief and "4096" in brief
    answer = answer_in(brief)
    assert _verdict_doc(answer) is not None                                       # the runner records it
    doc = json.loads(answer)
    assert contracts.errors("review_verdict", "verdict", doc) == [] and doc["reviewed_set_digest"] == SET
    # With the artifact the runner records for exactly those bytes, the door reads it as this verdict.
    found = verdict_of({"output_text": answer, "artifacts": [verdict_artifact(answer)]})
    assert found is not None and found[0]["verdict"] == "approve"


def test_a_real_review_without_the_reviewed_set_is_refused():
    with pytest.raises(ValueError):
        adapter(REAL)._brief("review", "codex", CANONICAL)


def test_the_longest_real_review_card_fits_the_card_limit():
    a = adapter(REAL)
    longest = {"outcome": "é" * 500, "done_when": "é" * 500}             # the tool's own bound on each
    brief = a._brief("review", "codex", longest, SET)
    assert len(brief) < 3000 < card_mod.MAX_BRIEF
    card = a._card("review", "codex", "TK-mc-" + "b" * 24, brief, [{"ref": "C-x.patch", "sha256": "0" * 64}])
    compiled = card_mod.Catalogue.load().compile(card)
    assert all(len(s.brief) <= card_mod.MAX_BRIEF for s in compiled.steps)


def test_build_briefs_and_the_fakework_path_are_unchanged():
    assert adapter(REAL)._brief("build", "codex", CANONICAL) == "Review the patch.\nDone when: The reviewer answers."
    assert adapter(FAKE)._brief("review", "claude-code", CANONICAL, SET) == (
        "fakework/1\n# Review the patch.\n# Done when: The reviewer answers.\n"
        f"findings none\nverdict approve set={SET}\nresult ok")
    assert adapter(FAKE)._brief("build", "codex", CANONICAL) == (
        "fakework/1\n# Review the patch.\n# Done when: The reviewer answers.\ncheckpoint build\nresult ok")
