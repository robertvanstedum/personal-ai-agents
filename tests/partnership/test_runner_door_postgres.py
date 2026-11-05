"""B4 v0.6 runner door on a real PostgreSQL: the harness's own records (pickup, a completed build step's recorded
outputs, a terminal failure) become task-service commands under the child's real holder seat, once.
Opt-in: a disposable mc12_test_* database (MC_BINDING_TEST_DSN)."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))
sys.path.insert(0, str(ROOT / "tests" / "mc_delegation_dispatcher"))

from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_delegation_dispatcher import cards as card_mod  # noqa: E402
from core.mc_delegation_dispatcher.store import DispatcherStore  # noqa: E402
from services.partnership_tasks.harness_port import HarnessDispatcher  # noqa: E402
from services.partnership_tasks.outbox import drain_once  # noqa: E402
from services.partnership_tasks.runner_door import RunnerDoor, artifact_refs, set_digest  # noqa: E402
from tests.partnership.synth import Flow, approve_admission, cmd, service  # noqa: E402

if DSN:
    from test_cards_postgres import AUTH, CAPS, GRANTS, USED, facts  # noqa: E402

MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql",
              "core/mc_delegation_dispatcher/sql/003_task_fences.sql")
D = "tok-drainer"


class InProcess:
    """A TaskClient stand-in over the in-process service, for one seat token."""
    def __init__(self, svc, token):
        self.svc, self.token = svc, token

    def submit(self, command):
        return self.svc.submit(command, self.token)

    def get(self, t, i):
        return self.svc.get(t, i, self.token)


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class RunnerDoorEndToEnd(PostgresFixture, unittest.TestCase):
    migrations = MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations, guild.mc_action_grant_consumptions, guild.mc_action_grants,
                guild.mc_action_events, guild.mc_action_receipts, guild.mc_task_fences, guild.mc_task_operations
                CASCADE""")
        self.svc = service(Path(tempfile.mkdtemp()) / "tasks", clock=lambda: datetime.now(timezone.utc).isoformat())
        f = self.flow = Flow(self.svc)
        f.source("SE-syn-0001", "Draft a design note on agent coordination.")
        f.interpret("IN-syn-0001", ["SE-syn-0001"], "A reviewed design note.")
        f.adopt("IN-syn-0001")
        f.request("RQ-syn-0001", "SE-syn-0001", "Design note.", iid="IN-syn-0001")
        f.promote("RQ-syn-0001", "TK-syn-0001", lead="mc", holder="worker-a")
        self.tid = "TK-syn-0001"
        self.store = DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH, grants=GRANTS)
        self.port = HarnessDispatcher(self.store, lambda op: self.svc.get("outbox", op, D))
        self.door = RunnerDoor(self.connect, principal="robert", environment="dev",
                               reader=InProcess(self.svc, "tok-principal"),
                               seats={"worker-a": InProcess(self.svc, "tok-worker")}, task_environment="synthetic")

    def admit(self, card_id="C-door-1", attempts=1):
        doc = {"schema": "minimoi.card.v1", "card_id": card_id, "class": "build_only", "class_version": 1,
               "repository": "personal-ai-agents", "ref": "a" * 40,
               "workers": {"builder": {"actor": "fakework", "route": "fakework", "amount": "0.25", "cost_cap_usd": "1"}},
               "briefs": {"build": "fakework/1\nresult ok"}, "attempts": attempts, "step_deadline_s": 3600}
        d = "sha256:" + card_mod.Catalogue.load().compile(doc).digest
        t = self.svc.get("task", self.tid, "tok-principal")
        self.assertEqual(self.svc.submit(cmd("task.request_admission", "task", self.tid, {
            "operation_id": "OB-door-1", "bundle_digest": d,
            "bundle_ref": {"store": "harness", "id": card_id, "edition": None, "digest": d},
            "fence_generation": t["fence_generation"], "card": doc}, expected=t["revision"]), "tok-mc")["outcome"],
            "accepted")
        approve_admission(self.svc, "OB-door-1", event="SE-door-ok-1")
        self.assertEqual(drain_once(self.svc, D, self.port, "OB-door-1"), "acknowledged")

    def now(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT clock_timestamp()")
            return cur.fetchone()[0]

    def run_step(self, status="completed", artifacts=()):
        self.store.heartbeat("mac-a", "online", CAPS, protocol=2, facts=facts("mac-a"))
        got = self.store.claim("mac-a", CAPS, protocol=2)
        env = {"request_id": got["envelope"]["request_id"], "generation": got["envelope"]["generation"]}
        return env, lambda: self.store.result(
            env["request_id"], env["generation"], event_id=str(uuid4()), status=status, artifacts=list(artifacts),
            detail={"exit_code": 0 if status == "completed" else 1}, output_text="done", protocol=2,
            runner_id="mac-a", at=self.now().isoformat(), route="fakework",
            usage={"amount": USED, "unit": "test_units", "children_stopped": True})

    def task(self):
        return self.svc.get("task", self.tid, "tok-principal")

    def test_pickup_then_a_completed_build_becomes_the_holders_artifact_set_once(self):
        self.admit()
        self.assertEqual(self.door.pass_once(), [])                          # queued, not picked up: nothing
        env, finish = self.run_step(artifacts=[{"kind": "patch", "ref": "C-door-1.patch",
                                                "sha256": hashlib.sha256(b"patch").hexdigest(), "bytes": 5,
                                                "author": "fakework", "review_status": "unreviewed"}])
        sent = self.door.pass_once()
        self.assertEqual([(k, o) for k, _, o in sent], [("task.transition:working", "accepted")])
        self.assertEqual(self.task()["state"], "working")
        finish()
        sent = self.door.pass_once()
        self.assertEqual([(k, o) for k, _, o in sent], [("task.submit_artifacts", "accepted")])
        t = self.task()
        want = set_digest(artifact_refs([{"ref": "C-door-1.patch", "sha256": hashlib.sha256(b"patch").hexdigest()}]))
        self.assertEqual(t["artifact_sets"][-1]["digest"], want)
        self.assertEqual(t["artifact_sets"][-1]["submitted_by"], "worker-a")   # the real holder seat, not the door
        self.assertEqual(self.door.pass_once(), [])                          # settled: nothing is sent twice

    def test_a_terminal_failure_fails_the_child_with_the_harness_item_as_evidence(self):
        self.admit()
        env, finish = self.run_step(status="failed")
        self.door.pass_once()
        finish()
        sent = self.door.pass_once()
        self.assertEqual([(k, o) for k, _, o in sent], [("task.transition:failed", "accepted")])
        t = self.task()
        self.assertEqual(t["state"], "failed")
        self.assertEqual(self.door.pass_once(), [])

    # ---- the cross-vendor review path (v0.5 M5-M6, S5; v0.6 Must 1) -----------------------------------------------
    def second_task(self):
        f = self.flow
        f.source("SE-syn-0002", "Review the design note.")
        f.interpret("IN-syn-0002", ["SE-syn-0002"], "A review of the design note.")
        f.adopt("IN-syn-0002")
        f.request("RQ-syn-0002", "SE-syn-0002", "Review.", iid="IN-syn-0002")
        f.promote("RQ-syn-0002", "TK-syn-0002", lead="mc", holder="mc")
        return "TK-syn-0002"

    def review_admit(self, rtid, inputs):
        doc = {"schema": "minimoi.card.v1", "card_id": "C-door-rev", "class": "review", "class_version": 1,
               "repository": "personal-ai-agents", "ref": "a" * 40,
               "workers": {"reviewer": {"actor": "fakework", "route": "fakework", "amount": "0.25", "cost_cap_usd": "1"}},
               "briefs": {"review": "fakework/1\nresult ok"}, "attempts": 1, "step_deadline_s": 3600,
               "input_artifacts": inputs}
        d = "sha256:" + card_mod.Catalogue.load().compile(doc).digest
        t = self.svc.get("task", rtid, "tok-principal")
        res = self.svc.submit(cmd("task.request_admission", "task", rtid, {
            "operation_id": "OB-door-rev", "bundle_digest": d,
            "bundle_ref": {"store": "harness", "id": "C-door-rev", "edition": None, "digest": d},
            "fence_generation": t["fence_generation"], "card": doc}, expected=t["revision"]), "tok-mc")
        self.assertEqual(res["outcome"], "accepted", res)
        approve_admission(self.svc, "OB-door-rev", event="SE-door-ok-rev")
        self.assertEqual(drain_once(self.svc, D, self.port, "OB-door-rev"), "acknowledged")

    def build_then_review(self, output, verdict_sha=None):
        import json as _json
        self.admit()
        patch = {"kind": "patch", "ref": "C-door-1.patch", "sha256": hashlib.sha256(b"patch").hexdigest(), "bytes": 5,
                 "author": "fakework", "review_status": "unreviewed"}
        env, finish = self.run_step(artifacts=[patch])
        self.door.pass_once(); finish(); self.door.pass_once()
        rtid = self.second_task()
        self.door.seats["mc"] = InProcess(self.svc, "tok-mc")
        self.review_admit(rtid, [{"ref": patch["ref"], "sha256": patch["sha256"]}])
        text = output if isinstance(output, str) else _json.dumps(output(self.task()["artifact_sets"][-1]["digest"]))
        verdict = {"kind": "verdict", "ref": "C-door-rev.verdict",
                   "sha256": verdict_sha or hashlib.sha256(text.encode()).hexdigest(), "bytes": len(text),
                   "author": "fakework", "review_status": "unreviewed"}
        self.store.heartbeat("mac-a", "online", CAPS, protocol=2, facts=facts("mac-a"))
        got = self.store.claim("mac-a", CAPS, protocol=2)
        renv = {"request_id": got["envelope"]["request_id"], "generation": got["envelope"]["generation"]}
        self.door.pass_once()
        self.store.result(renv["request_id"], renv["generation"], event_id=str(uuid4()), status="completed",
                          artifacts=[verdict], detail={"exit_code": 0}, output_text=text, protocol=2, runner_id="mac-a",
                          at=self.now().isoformat(), route="fakework",
                          usage={"amount": USED, "unit": "test_units", "children_stopped": True})
        return rtid, self.door.pass_once()

    def test_a_valid_verdict_is_recorded_on_the_builder_by_the_reviewer_seat_and_closes_the_review_child(self):
        ok = lambda reviewed: {"schema": "minimoi.partnership.review_verdict/1", "verdict": "approve",
                               "reviewed_set_digest": reviewed, "findings_digest": None}
        rtid, sent = self.build_then_review(ok)
        self.assertIn(("task.record_review:approve", self.tid, "accepted"), sent)
        self.assertIn(("task.submit_artifacts:verdict", rtid, "accepted"), sent)
        reviews = self.task()["reviews"]
        self.assertEqual([(r["reviewer"], r["verdict"]) for r in reviews], [("mc", "approve")])
        self.assertEqual(self.door.pass_once(), [])                             # once

    def test_a_verdict_whose_artifact_does_not_match_its_text_counts_as_changes_requested(self):
        ok = lambda reviewed: {"schema": "minimoi.partnership.review_verdict/1", "verdict": "approve",
                               "reviewed_set_digest": reviewed, "findings_digest": None}
        rtid, sent = self.build_then_review(ok, verdict_sha="0" * 64)
        self.assertIn(("task.record_review:changes_requested", self.tid, "accepted"), sent)

    def test_a_malformed_verdict_counts_as_changes_requested_never_approve(self):
        rtid, sent = self.build_then_review("LGTM, ship it")
        self.assertIn(("task.record_review:changes_requested", self.tid, "accepted"), sent)
        self.assertEqual([r["verdict"] for r in self.task()["reviews"]], ["changes_requested"])


    def test_a_cancel_revokes_the_running_card_so_no_result_is_taken_and_the_card_ends(self):
        self.admit()
        env, finish = self.run_step(artifacts=[])
        self.door.pass_once()                                                    # working
        res = self.flow.transition(self.tid, "canceled", reason="Owner said stop.",
                                   evidence=[{"store": "records", "id": "evt-stop", "edition": None, "digest": None}])
        self.assertEqual(res["outcome"], "accepted", res)
        from services.partnership_tasks.reconstruct import outbox_queue
        fence = [r["operation_id"] for r in outbox_queue(self.svc, D) if r["operation"] == "set_fence"][0]
        self.assertEqual(drain_once(self.svc, D, self.port, fence), "acknowledged")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT state FROM guild.mc_delegations WHERE card_id='C-door-1'")
            states = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT count(*) FROM guild.mc_delegation_events WHERE kind='grant.revoked'")
            revoked = cur.fetchone()[0]
        self.assertEqual(revoked, 1)
        self.assertTrue(all(s in ("cancelled", "claimed") for s in states), states)
