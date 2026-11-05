"""B4 v0.6 MC's tracked-work tools on real PostgreSQL, the real MC tool path and policy (every handler at its floor), and
an in-process task service. MC delegates, a cross-vendor review is composed from the builder's current set, MC accepts
and submits. Nothing here starts paid work: each child's card waits for Robert's approval.
Run with tests/mc_tools/run_postgres.sh <pgbin>, or MC_BINDING_TEST_DSN pointing at a disposable database."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from test_tool_path_postgres import Base, DSN  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.mc_tools.tasks_adapter import TasksAdapter  # noqa: E402
from tests.partnership.synth import Flow, cmd, service  # noqa: E402


class InProcess:
    def __init__(self, svc, token):
        self.svc, self.token = svc, token

    def get(self, t, i):
        return self.svc.get(t, i, self.token)

    def submit(self, command):
        return self.svc.submit(command, self.token)

    def open_work(self):
        from services.partnership_tasks.reconstruct import open_work
        return open_work(self.svc, self.token)


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class TaskToolTests(Base, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.tasks = service(Path(tempfile.mkdtemp()) / "tasks")
        self.parent = Flow(self.tasks).promoted_task()                 # lead and holder mc
        self.service.tasks = TasksAdapter(InProcess(self.tasks, "tok-mc"), environment="synthetic", card_ref="a" * 40)

    def task(self, tid):
        return self.tasks.get("task", tid, "tok-principal")

    def delegate(self, **kw):
        args = {"task_id": self.parent, "kind": "build", "holder": "codex", "outcome": "Draft the coordination note.",
                "done_when": "A reviewed draft exists.", **kw}
        return self.call("minimoi_task_delegate", args)

    def submit_set(self, child, seat_token, payload=b"patch"):
        t = self.task(child)
        if t["state"] == "submitted":
            self.assertEqual(self.tasks.submit(cmd("task.transition", "task", child, {
                "to": "working", "reason": "picked up", "waiting": None,
                "evidence_refs": [{"store": "harness", "id": "item-1", "edition": "1", "digest": None}],
                "fence_generation": t["fence_generation"]}, expected=t["revision"]), seat_token)["outcome"], "accepted")
            t = self.task(child)
        d = "sha256:" + hashlib.sha256(payload).hexdigest()
        self.assertEqual(self.tasks.submit(cmd("task.submit_artifacts", "task", child, {
            "artifacts": [{"store": "harness", "id": "C-x.patch", "edition": None, "digest": d}],
            "fence_generation": t["fence_generation"]}, expected=t["revision"]), seat_token)["outcome"], "accepted")
        return self.task(child)["artifact_sets"][-1]

    def test_list_reads_mcs_tracked_work(self):
        r = self.call("minimoi_task_list")
        self.assertEqual(r["status"], "done", r)
        self.assertIn(self.parent, [w.get("task_id") or w.get("id") for w in r["data"]["work"]])

    def test_delegate_records_a_child_and_its_card_but_starts_nothing_and_a_retry_is_the_same_operation(self):
        r = self.delegate()
        self.assertEqual(r["status"], "done", r)
        self.assertIn("Robert must approve its work card", r["message"])
        children = [t for t in self.tasks.get("task", self.parent, "tok-principal")["assignments"] if t["role"] == "delegate"]
        self.assertEqual([c["seat"] for c in children], ["codex"])
        from services.partnership_tasks.reconstruct import outbox_queue
        rows = [x for x in outbox_queue(self.tasks, "tok-drainer") if x["operation"] == "admit_bundle"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["has_card"] and rows[0]["approval"] is None)       # waits for Robert
        ob = self.tasks.get("outbox", rows[0]["operation_id"], "tok-principal")
        self.assertEqual(ob["packet"]["workers"]["builder"]["actor"], "codex")       # from config, not the model
        self.assertEqual(ob["packet"]["ref"], "a" * 40)
        again = self.delegate()
        self.assertEqual(again["status"], "done", again)
        self.assertEqual(len([x for x in outbox_queue(self.tasks, "tok-drainer") if x["operation"] == "admit_bundle"]), 1)

    def test_a_review_waits_for_the_builders_set_needs_another_family_and_inputs_the_current_set(self):
        self.delegate()
        child = next(a["reason"].split()[-1] for a in self.task(self.parent)["assignments"] if a["role"] == "delegate")
        self.next_turn("n-review-1")                               # a new message from Robert (quota is per message)
        refused = self.delegate(kind="review", holder="claude-code", reviewed_child=child)
        self.assertEqual((refused["status"], refused["code"]), ("refused", "builder_set_not_submitted"))
        current = self.submit_set(child, "tok-codex")
        same_family = self.delegate(kind="review", holder="codex", reviewed_child=child)
        self.assertEqual(same_family["code"], "review_needs_another_family")
        self.next_turn("n-review-2")
        ok = self.delegate(kind="review", holder="claude-code", reviewed_child=child,
                           outcome="Review the draft.", done_when="An explicit verdict is recorded.")
        self.assertEqual(ok["status"], "done", ok)
        builder = self.task(child)
        self.assertIn("claude-code", [a["seat"] for a in builder["assignments"] if a["role"] == "verifier"])
        from services.partnership_tasks.reconstruct import outbox_queue
        cards = [self.tasks.get("outbox", x["operation_id"], "tok-principal")["packet"]
                 for x in outbox_queue(self.tasks, "tok-drainer") if x["operation"] == "admit_bundle"]
        review = next(c for c in cards if c["class"] == "review")
        self.assertEqual(review["input_artifacts"],
                         [{"ref": a["id"], "sha256": a["digest"][7:]} for a in current["artifacts"]])

    def test_accept_names_every_dissenting_review_and_submit_carries_the_accepted_set_to_the_parent(self):
        self.delegate()
        child = next(a["reason"].split()[-1] for a in self.task(self.parent)["assignments"] if a["role"] == "delegate")
        current = self.submit_set(child, "tok-codex")
        t = self.task(child)
        self.assertEqual(self.tasks.submit(cmd("task.record_review", "task", child, {
            "review_id": "RV-test-approve", "reviewed_set_digest": current["digest"], "verdict": "approve",
            "review_ref": {"store": "harness", "id": "C-r.verdict", "edition": None, "digest": "sha256:" + "e" * 64},
            "findings_ref": None, "input_digest": current["digest"]}, expected=t["revision"]),
            "tok-claude-code")["outcome"], "accepted")
        t = self.task(child)                     # a dissent the acceptance must carry to Robert (the lead's own review)
        self.assertEqual(self.tasks.submit(cmd("task.record_review", "task", child, {
            "review_id": "RV-test-disagree", "reviewed_set_digest": current["digest"], "verdict": "disagree",
            "review_ref": {"store": "harness", "id": "C-r2.verdict", "edition": None, "digest": "sha256:" + "f" * 64},
            "findings_ref": None, "input_digest": current["digest"]}, expected=t["revision"]), "tok-mc")["outcome"],
            "accepted")
        # The child's admission must be settled first (here Robert declined it; on Dev it is admitted and worked).
        from services.partnership_tasks.reconstruct import outbox_queue
        for x in outbox_queue(self.tasks, "tok-drainer"):
            if x["operation"] == "admit_bundle":
                ob = self.tasks.get("outbox", x["operation_id"], "tok-principal")
                self.tasks.submit(cmd("outbox.record", "outbox", x["operation_id"], {
                    "observation": "declined", "destination_receipt": None, "code": "owner_declined"},
                    expected=ob["revision"]), "tok-principal")
        r = self.call("minimoi_task_accept", {"task_id": child})
        self.assertEqual(r["status"], "done", r)
        self.assertIn("dissenting", r["message"])
        acc = self.task(child)["acceptances"][-1]
        self.assertEqual(acc["disagreements_delivered"], ["RV-test-disagree"])
        s = self.call("minimoi_task_submit", {"task_id": self.parent, "child_id": child})
        self.assertEqual(s["status"], "done", s)
        self.assertEqual(self.task(self.parent)["artifact_sets"][-1]["digest"], current["digest"])
