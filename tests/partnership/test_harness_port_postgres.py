"""B4 v0.6 end to end on a real PostgreSQL: task service -> Robert's approval -> outbox drainer -> HarnessDispatcher ->
the delegation dispatcher's admit_card_for_task. Nothing is admitted without Robert's fresh approval of that exact
card; a stale task closes its entry without a send; a fence raised by a cancel reaches the authority.
Opt-in: a disposable mc12_test_* database (MC_BINDING_TEST_DSN)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))
sys.path.insert(0, str(ROOT / "tests" / "mc_delegation_dispatcher"))

from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_delegation_dispatcher import cards as card_mod  # noqa: E402
from core.mc_delegation_dispatcher.store import DispatcherStore  # noqa: E402
from services.partnership_tasks.harness_port import HarnessDispatcher  # noqa: E402
from services.partnership_tasks.outbox import drain_once  # noqa: E402
from tests.partnership.synth import Flow, approve_admission, cmd, service  # noqa: E402

if DSN:
    from test_cards_postgres import AUTH, GRANTS  # noqa: E402

MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql",
              "core/mc_delegation_dispatcher/sql/003_task_fences.sql")
D = "tok-drainer"


def card(card_id="C-b4-e2e-1"):
    return {"schema": "minimoi.card.v1", "card_id": card_id, "class": "build_only", "class_version": 1,
            "repository": "personal-ai-agents", "ref": "a" * 40,
            "workers": {"builder": {"actor": "fakework", "route": "fakework", "amount": "0.25", "cost_cap_usd": "1"}},
            "briefs": {"build": "fakework/1\nresult ok"}, "attempts": 1, "step_deadline_s": 3600}


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class HarnessPortEndToEnd(PostgresFixture, unittest.TestCase):
    migrations = MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations, guild.mc_action_grant_consumptions, guild.mc_action_grants,
                guild.mc_action_events, guild.mc_action_receipts, guild.mc_task_fences, guild.mc_task_operations
                CASCADE""")
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        from datetime import datetime, timezone                  # real time: the authority checks approvals by its clock
        self.svc = service(self.tmp / "tasks", clock=lambda: datetime.now(timezone.utc).isoformat())
        self.flow = Flow(self.svc)
        self.tid = self.flow.promoted_task()
        self.store = DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH, grants=GRANTS)
        self.port = HarnessDispatcher(self.store, lambda op: self.svc.get("outbox", op, D))

    def request(self, doc, op="OB-e2e-0001"):
        compiled = card_mod.Catalogue.load().compile(doc)
        d = "sha256:" + compiled.digest
        t = self.svc.get("task", self.tid, "tok-principal")
        res = self.svc.submit(cmd("task.request_admission", "task", self.tid,
                                  {"operation_id": op, "bundle_digest": d,
                                   "bundle_ref": {"store": "harness", "id": doc["card_id"], "edition": None, "digest": d},
                                   "fence_generation": t["fence_generation"], "card": doc},
                                  expected=t["revision"]), "tok-mc")
        self.assertEqual(res["outcome"], "accepted", res)
        return d

    def cards(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT card_id FROM guild.mc_delegations")
            return sorted(r[0] for r in cur.fetchall())

    def test_robert_approves_and_the_card_is_admitted_once_with_his_approval_recorded(self):
        self.request(card())
        self.assertEqual(drain_once(self.svc, D, self.port, "OB-e2e-0001"), "pending")      # no approval: nothing
        self.assertEqual(self.cards(), [])
        approve_admission(self.svc, "OB-e2e-0001", event="SE-e2e-ok-1")
        self.assertEqual(drain_once(self.svc, D, self.port, "OB-e2e-0001"), "acknowledged")
        self.assertEqual(self.cards(), ["C-b4-e2e-1"])
        ob = self.svc.get("outbox", "OB-e2e-0001", D)
        self.assertEqual(ob["destination_receipt"]["digest"], ob["bundle_digest"])
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT approval_event, applied FROM guild.mc_task_operations WHERE operation_id=%s",
                        ("OB-e2e-0001",))
            self.assertEqual(cur.fetchone(), ("SE-e2e-ok-1", True))
        self.assertEqual(drain_once(self.svc, D, self.port, "OB-e2e-0001"), "acknowledged")  # settled; no resend

    def test_a_cancel_fences_the_task_at_the_authority_and_a_pending_admission_is_never_sent(self):
        self.request(card("C-b4-e2e-2"), op="OB-e2e-0002")
        approve_admission(self.svc, "OB-e2e-0002", event="SE-e2e-ok-2")
        res = self.flow.transition(self.tid, "canceled", reason="Owner said stop.",
                                   evidence=[{"store": "records", "id": "evt-stop", "edition": None, "digest": None}])
        self.assertEqual(res["outcome"], "accepted", res)
        self.assertEqual(self.svc.get("outbox", "OB-e2e-0002", D)["state"], "fenced")
        from services.partnership_tasks.reconstruct import outbox_queue
        fences = [r["operation_id"] for r in outbox_queue(self.svc, D) if r["operation"] == "set_fence"]
        self.assertTrue(fences)
        self.assertEqual(drain_once(self.svc, D, self.port, fences[0]), "acknowledged")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT fence FROM guild.mc_task_fences WHERE task_id=%s", (self.tid,))
            self.assertGreaterEqual(cur.fetchone()[0], 1)
        self.assertEqual(self.cards(), [])
