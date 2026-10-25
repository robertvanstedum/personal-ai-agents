"""Guest reject through the MC action path, on a real disposable PostgreSQL (journey link 3/6/7).

Run: tests/mc_domain_actions/run_postgres.sh (or Codex's fixture runner with this start dir).
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sys
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "minimoi_actions"))
from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_domain_actions import guest  # noqa: E402
from core.minimoi_actions.context import ActionContext, ActionContractError  # noqa: E402
from core.minimoi_actions.grants import GrantDefinition, GrantStore  # noqa: E402
from core.minimoi_actions.receipts import EffectIntent  # noqa: E402

GUESTS_DDL = """
DROP TABLE IF EXISTS guild.guest_requests;
CREATE TABLE guild.guest_requests (
  id SERIAL PRIMARY KEY, name TEXT, email TEXT NOT NULL UNIQUE, reason TEXT,
  domain VARCHAR(50) DEFAULT 'portuguese',
  requested_at TIMESTAMPTZ DEFAULT NOW(), status TEXT DEFAULT 'requested'
    CHECK (status IN ('requested','pending','granted','rejected','approved_pending_email')),
  actioned_at TIMESTAMPTZ, last_nudged_at TIMESTAMPTZ);
"""


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class GuestActionsPostgresTests(PostgresFixture, unittest.TestCase):
    migrations = ("core/minimoi_actions/sql/004_action_receipts.sql",
                  "core/minimoi_actions/sql/005_action_grants.sql",
                  "core/mc_domain_actions/sql/001_guest_action_evidence.sql")

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE guild.mc_guest_action_evidence, guild.mc_action_grant_consumptions,"
                        "guild.mc_action_grants, guild.mc_action_events, guild.mc_action_receipts CASCADE")
            cur.execute(GUESTS_DDL)
            cur.execute("INSERT INTO guild.guest_requests (name,email) VALUES "
                        "('Ana Souza','ana@example.test'),('Ana Lima','ana.lima@example.test'),"
                        "('Bruno Costa','bruno@example.test')")
        self.store = GrantStore({guest.CAPABILITY: guest.CATALOGUE_RULE})
        self.actions = guest.GuestActions(self.store, owner_principal="owner")
        self.context = ActionContext("owner", "mc", "dev", "conversation", "owner-turn", "dispatch", 1,
                                     datetime.now(timezone.utc) + timedelta(minutes=5), "policy", "role",
                                     "recorded", "budget")

    # ── helpers: what trusted owner-control code does after Robert approves ──
    def approved(self, name="Bruno", *, minutes=10):
        with self.connect() as conn:
            row = self.actions.find(conn, self.context, name)[0]
        request = guest.reject_request(row["ref"])
        proposal, operation = str(uuid4()), str(uuid4())
        grant = GrantDefinition(str(uuid4()), "owner", "dev", "conversation", ("owner-turn",), ("mc",), "policy",
                                guest.CAPABILITY, guest.VERSION, (request.arguments_sha256,), 1,
                                datetime.now(timezone.utc) + timedelta(minutes=minutes), proposal_id=proposal,
                                approved_operation_id=operation,
                                approval_expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
                                expected_target_version=row["target_version"])
        with self.connect() as conn:
            self.store.issue(conn, grant)
        context = ActionContext(**{**{f: getattr(self.context, f) for f in self.context.__slots__},
                                   "grant_ids": (grant.grant_id,)})
        intent = EffectIntent(operation, request, grant_id=grant.grant_id, proposal_id=proposal,
                              expected_target_version=row["target_version"])
        return context, intent, grant, row

    def status(self, ref):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM guild.guest_requests WHERE id=%s", (int(ref.split(":")[1]),))
            return cur.fetchone()[0]

    def run_journey(self, context, intent):
        with self.connect() as conn:
            self.actions.prepare(conn, context, intent, current_policy_version="policy")
        with self.connect() as conn:
            return self.actions.execute(conn, context, intent, "worker-1", current_policy_version="policy")

    # ── observation ────────────────────────────────────────────────────────
    def test_find_returns_open_matches_and_ambiguity_is_visible(self):
        with self.connect() as conn:
            self.assertEqual(len(self.actions.find(conn, self.context, "ana")), 2)       # MC must ask which
            only = self.actions.find(conn, self.context, "bruno@example")
            self.assertEqual([r["name"] for r in only], ["Bruno Costa"])
            self.assertEqual(self.actions.find(conn, self.context, "nobody"), [])

    def test_observation_is_owner_only(self):
        other = ActionContext(**{**{f: getattr(self.context, f) for f in self.context.__slots__},
                                 "principal_id": "someone-else"})
        with self.connect() as conn, self.assertRaisesRegex(ActionContractError, "context_refused"):
            self.actions.open_requests(conn, other)

    # ── the journey ────────────────────────────────────────────────────────
    def test_approved_reject_is_performed_and_verified_in_one_transaction(self):
        context, intent, _, row = self.approved()
        result = self.run_journey(context, intent)
        self.assertEqual(result["receipt"]["state"], "verified")
        self.assertEqual(result["outcome"], "rejected")
        self.assertEqual(self.status(row["ref"]), "rejected")
        self.assertIn("rejected, verified by read-back", self.actions.reply(result))
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT outcome,status_after FROM guild.mc_guest_action_evidence")
            self.assertEqual(cur.fetchall(), [("rejected", "rejected")])
        self.assertEqual(self.status("guild:1"), "requested")                         # others untouched

    def test_without_an_approval_nothing_can_be_prepared(self):
        context, intent, _, row = self.approved()
        unapproved = EffectIntent(str(uuid4()), intent.request, grant_id=str(uuid4()),
                                  proposal_id=intent.proposal_id,
                                  expected_target_version=intent.expected_target_version)
        with self.connect() as conn, self.assertRaises(ActionContractError):
            self.actions.prepare(conn, context, unapproved, current_policy_version="policy")
        self.assertEqual(self.status(row["ref"]), "requested")

    def test_revoked_approval_refuses_execution_and_changes_nothing(self):
        context, intent, grant, row = self.approved()
        with self.connect() as conn:
            self.actions.prepare(conn, context, intent, current_policy_version="policy")
        with self.connect() as conn:
            self.store.revoke(conn, "owner", "dev", grant.grant_id)
        with self.connect() as conn, self.assertRaisesRegex(ActionContractError, "revoked"):
            self.actions.execute(conn, context, intent, "worker-1", current_policy_version="policy")
        self.assertEqual(self.status(row["ref"]), "requested")

    def test_request_changed_after_approval_is_refused_at_prepare(self):
        context, intent, _, row = self.approved()
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.guest_requests SET status='granted' WHERE id=%s", (int(row["ref"][6:]),))
        with self.connect() as conn, self.assertRaisesRegex(ActionContractError, "changed_since_approval"):
            self.actions.prepare(conn, context, intent, current_policy_version="policy")

    def test_granted_between_prepare_and_execute_is_a_failed_receipt_not_a_flip(self):
        context, intent, _, row = self.approved()
        with self.connect() as conn:
            self.actions.prepare(conn, context, intent, current_policy_version="policy")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE guild.guest_requests SET status='granted' WHERE id=%s", (int(row["ref"][6:]),))
        with self.connect() as conn:
            result = self.actions.execute(conn, context, intent, "worker-1", current_policy_version="policy")
        self.assertEqual((result["receipt"]["state"], result["outcome"]), ("failed", "conflict"))
        self.assertEqual(self.status(row["ref"]), "granted")
        self.assertIn("Nothing was changed", self.actions.reply(result))

    def test_a_retry_after_success_never_acts_twice(self):
        context, intent, _, row = self.approved()
        self.run_journey(context, intent)
        with self.connect() as conn, self.assertRaisesRegex(ActionContractError, "terminal"):
            self.actions.execute(conn, context, intent, "worker-2", current_policy_version="policy")
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM guild.mc_guest_action_evidence")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_a_crash_mid_execution_rolls_everything_back_and_can_be_retried(self):
        context, intent, _, row = self.approved()
        with self.connect() as conn:
            self.actions.prepare(conn, context, intent, current_policy_version="policy")
        original = self.actions.receipts.finish

        def crash(*a, **k):
            raise RuntimeError("worker died before finishing")
        self.actions.receipts.finish = crash
        try:
            with self.assertRaises(RuntimeError), self.connect() as conn:
                self.actions.execute(conn, context, intent, "worker-1", current_policy_version="policy")
        finally:
            self.actions.receipts.finish = original
        self.assertEqual(self.status(row["ref"]), "requested")                      # nothing half-applied
        with self.connect() as conn:
            result = self.actions.execute(conn, context, intent, "worker-2", current_policy_version="policy")
        self.assertEqual(result["receipt"]["state"], "verified")

    def test_concurrent_executions_apply_once(self):
        context, intent, _, row = self.approved()
        with self.connect() as conn:
            self.actions.prepare(conn, context, intent, current_policy_version="policy")

        def attempt(i):
            try:
                with self.connect() as conn:
                    return self.actions.execute(conn, context, intent, f"worker-{i}",
                                                current_policy_version="policy")["receipt"]["state"]
            except ActionContractError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=4) as pool:
            states = list(pool.map(attempt, range(4)))
        self.assertEqual(states.count("verified"), 1, states)
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM guild.mc_guest_action_evidence")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_model_supplied_extras_and_file_registrations_are_refused(self):
        context, intent, _, _ = self.approved()
        for bad in ("pending:abc", "guild:0", "guild:1; DROP TABLE", ""):
            with self.subTest(ref=bad), self.assertRaises(ActionContractError):
                guest.reject_request(bad)
        from core.minimoi_actions.requests import ToolRequest
        extra = ToolRequest(guest.CAPABILITY, 1, b'{"ref":"guild:3","approved":true}')
        forged = EffectIntent(intent.operation_id, extra, grant_id=intent.grant_id, proposal_id=intent.proposal_id,
                              expected_target_version=intent.expected_target_version)
        with self.connect() as conn, self.assertRaisesRegex(ActionContractError, "arguments_invalid"):
            self.actions.prepare(conn, context, forged, current_policy_version="policy")


if __name__ == "__main__":
    unittest.main()
