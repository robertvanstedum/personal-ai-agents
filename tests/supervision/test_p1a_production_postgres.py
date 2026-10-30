"""Host runtime P1-a on a real PostgreSQL, with production's own authority file (authority.prod.json) and grant
catalogue: the portal's version 2 door over the real store (a host bound to its credential, a fakework card step
claimed in "prod" because the file admits the test routes), the switch-on check on a fresh authority (ready, nothing
to baseline), the rollback check naming a live item until it ends, and the integrated test's ProdEnv reads and owner
path. Opt-in: a disposable mc12_test_* database (tests/minimoi_actions/postgres_fixture_support.py)."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))

from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

PROD = ROOT / "core" / "mc_delegation_dispatcher" / "authority.prod.json"
MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql",
              "core/supervision/sql/001_supervision.sql", "core/supervision/sql/002_grok_and_exchanges.sql",
              "core/supervision/sql/003_runtime_decisions_and_watch.sql")
HOST_TOKEN, LEGACY = "h" * 48, "r" * 48
REF = "a" * 40
CAPS = ["repo:personal-ai-agents", "scope:build", "scope:research", "actor:fakework", "host:mac-local"]
FACTS = {"host_id": "mac-prod-1", "bundle_digest": "sha256:" + "b" * 64, "roles": ["worker", "witness"],
         "availability": "intermittent", "grace": 28800, "last_gap_s": 0, "announced": "online"}


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class ProductionWiring(PostgresFixture, unittest.TestCase):
    migrations = MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations, guild.mc_action_grant_consumptions, guild.mc_action_grants,
                guild.mc_action_events, guild.mc_action_receipts, guild.supervision_decision_messages,
                guild.supervision_contacts, guild.supervision_watch, guild.supervision_decisions,
                guild.supervision_alerts, guild.supervision_outbox RESTART IDENTITY CASCADE""")
        from core.mc_delegation_dispatcher import production
        self.auth = production.load(PROD, "prod")
        self.store = production.store(self.connect, principal="robert", environment="prod", authority=self.auth)

    def door(self, tmp):
        from flask import Flask
        from minimoi_portal.guild_ui import supervision
        creds = tmp / "door-credentials.json"
        creds.write_text(json.dumps({"schema": "minimoi.door_credentials.v1", "environment": "prod",
                                     "hosts": {"mac-prod-1": _sha(HOST_TOKEN)},
                                     "readers": {"robert": {"sha256": _sha("o" * 48), "role": "owner"}}}))
        env = {"MINIMOI_MC_DELEGATION_AUTHORITY_FILE": str(PROD),
               "MINIMOI_MC_DELEGATION_DOOR_CREDENTIALS": str(creds)}
        app = Flask("door")
        self.assertTrue(supervision.attach_runner_door_v2(app, env, _sha(LEGACY), "robert", "prod",
                                                          store_factory=lambda: self.store))
        return app.test_client()

    def card(self, card_id):
        worker = {"actor": "fakework", "route": "fakework", "amount": 0.25, "cost_cap_usd": 1}
        return {"schema": "minimoi.card.v1", "card_id": card_id, "class": "build", "class_version": 1,
                "repository": "personal-ai-agents", "ref": REF, "workers": {"builder": worker, "reviewer": worker},
                "briefs": {"build": "fakework/1\nresult ok", "review": "fakework/1\nresult ok",
                           "fix": "fakework/1\nresult ok"}, "attempts": 2, "rounds": {"fix": 1},
                "step_deadline_s": 3600}

    def test_the_production_door_binds_its_host_and_hands_out_a_fakework_step_in_prod(self):
        import tempfile
        c = self.door(Path(tempfile.mkdtemp()))
        auth = {"Authorization": f"Bearer {HOST_TOKEN}"}
        beat = {"op": "heartbeat", "protocol": 2, "runner_id": "mac-prod-1:runner", "state": "online",
                "capabilities": CAPS, "facts": FACTS}
        r = c.post("/internal/mc/v1/delegation-runner", json=beat, headers=auth)
        self.assertEqual((r.status_code, r.get_json()["value"]["state"]), (200, "recorded"))
        r = c.post("/internal/mc/v1/delegation-runner", json={**beat, "facts": {**FACTS, "host_id": "mac-b"}},
                   headers=auth)
        self.assertEqual(r.status_code, 403)                                       # Codex part 2 P2-1, in prod
        got = self.store.admit_card(self.card("C-P1A-1"), grant_id=str(uuid4()), owner_turn_ids=("console:t:1",),
                                    proposal_id=str(uuid4()), approved_operation_id=str(uuid4()),
                                    approval_expires_at=self._now() + __import__("datetime").timedelta(seconds=300),
                                    by="robert", note="P1-a test card")
        self.assertEqual(got["state"], "admitted")
        r = c.post("/internal/mc/v1/delegation-runner", headers=auth,
                   json={"op": "claim", "protocol": 2, "runner_id": "mac-prod-1:runner", "capabilities": CAPS})
        claimed = r.get_json()["value"]
        self.assertEqual((r.status_code, claimed["route"], claimed["unit"]), (200, "fakework", "test_units"))
        self.assertEqual(claimed["envelope"]["work_id"], "C-P1A-1.build")
        # The switch-off check names the live item; the snapshot shows the Mac as a production host.
        from core.supervision import switch_on
        self.assertEqual(len(switch_on.rollback_blockers(self.store)["live"]), 1)
        doc = self.store.snapshot(endpoint_id="prod-authority", host_id="prod-ec2-1")
        host = next(h for h in doc["hosts"] if h["host_id"] == "mac-prod-1")
        self.assertEqual((host["reachability"], host["availability"], host["grace_s"]),
                         ("reachable", "intermittent", 28800))
        rid, gen = claimed["envelope"]["request_id"], claimed["envelope"]["generation"]
        r = c.post("/internal/mc/v1/delegation-runner", headers=auth, json={
            "op": "result", "protocol": 2, "runner_id": "mac-prod-1:runner", "request_id": rid, "generation": gen,
            "event_id": str(uuid4()), "at": self._now().isoformat(), "status": "completed", "artifacts": [],
            "detail": {}, "output_text": None, "route": "fakework",
            "usage": {"amount": 0.1, "unit": "test_units", "children_stopped": True}})
        self.assertEqual((r.status_code, r.get_json()["value"]["state"]), (200, "accepted"))
        review = self.store.by_job("C-P1A-1.review")                              # written by the authority
        self.assertEqual((review["state"], review["created_by"]), ("queued", "authority"))
        self.assertEqual(switch_on.rollback_blockers(self.store)["live"], [str(review["request_id"])])
        self.store.stop("C-P1A-1.review")                                          # queued: the owner's Stop
        self.assertEqual(switch_on.rollback_blockers(self.store), {"live": [], "open_effects": []})

    def _now(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT clock_timestamp()")
            return cur.fetchone()[0]

    def test_a_fresh_production_authority_is_ready_to_switch_on_with_nothing_to_baseline(self):
        from core.supervision import switch_on
        from core.supervision.store import Store
        self.store.sweep()
        self.store.authority_beat()
        self.store.source_path = str(PROD)
        out = switch_on.check(self.store, Store(self.connect), self.connect)
        self.assertEqual((out["migrations"], out["contacts_ready"], out["raised"], out["untold"],
                          out["would_baseline"], out["ready"]),
                         ({"002": True, "003": True}, True, 0, 0, 0, True))
        self.assertEqual(out["authority_file"], "sha256:" + hashlib.sha256(PROD.read_bytes()).hexdigest())
        self.assertTrue(out["timers"]["liveness"]["fresh"])
        self.assertTrue(out["running"])                        # both timers fresh after a sweep and a beat
        self.assertEqual(switch_on.baseline(self.store, Store(self.connect))["baselined"], 0)

    def test_the_integrated_tests_prodenv_reads_and_owner_path(self):
        import os
        import tempfile
        from tests.harness_acceptance import p1a
        state = Path(tempfile.mkdtemp()) / "state.json"
        environ = {"DATABASE_URL": DSN, "MINIMOI_RUNTIME_AUTHORITY_FILE": str(PROD),
                   "MINIMOI_RUNTIME_STATE_FILE": str(state), "MINIMOI_RUNTIME_PRINCIPAL": "robert"}
        old = dict(os.environ)
        try:
            env = p1a.ProdEnv(environ)
        finally:
            os.environ.clear()
            os.environ.update(old)
        env._connect = self.connect                                 # the fixture's own bounded connection
        env.store = self.store
        env.ref = REF
        mark = env.audit_mark()
        with self.assertRaises(p1a.Stop):
            env.admit_card({**self.card("C-X"), "workers": {"builder": {"actor": "claude-code",
                            "route": "anthropic.api", "amount": 1, "cost_cap_usd": 1}}}, "console:t:x")
        doc = self.card("C-P1A-2")
        doc["briefs"]["build"] = "fakework/1\neffect e1 grant={grant}\nresult ok"
        gid = env.effect_grant("C-P1A-2.build", ("e1",), "console:t:effects")
        doc["briefs"]["build"] = doc["briefs"]["build"].replace("{grant}", gid)
        self.assertEqual(env.admit_card(doc, "console:t:2")["state"], "admitted")
        self.assertEqual([r["step"] for r in env.rows("C-P1A-2")], ["build"])
        self.assertEqual(env.live_items("P1A-2"), ["C-P1A-2.build"])
        kinds = [e["kind"] for e in env.events(after=mark)]
        self.assertIn("card.admitted", kinds)
        rid = str(env.row("C-P1A-2.build")["request_id"])
        self.assertTrue(env.events(item=rid, kind="item.admitted") or env.events(item=rid))
        self.assertEqual(env.decisions(), [])
        self.assertEqual(env.told(""), [])
        did = env.propose_review("C-P1A-2.build")
        self.assertFalse(env.decision_code(did))                    # no D-code until the link delivers it
        self.assertEqual(len(env.effect_operation_id(rid, "e1")), len(env.effect_operation_id(rid, "e2")))
