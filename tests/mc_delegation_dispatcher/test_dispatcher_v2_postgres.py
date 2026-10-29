"""Protocol version 2 of the delegation dispatcher on a real PostgreSQL (host runtime spec v0.5 §3.2, §4, §8.3, §9):
the migration, version 1 on the migrated schema, host contact, claims under grant, route gate and reservation, the
reservation race (T11m), the route gate (T11b), the expired and revoked grant (T10), progress ordering, and DUP-3 and
DUP-4 at unit level. Opt-in: a disposable mc12_test_* database (tests/minimoi_actions/postgres_fixture_support.py)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))

from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_delegation_dispatcher.store import Authority, AuthorityRefused, DispatcherStore  # noqa: E402
from core.minimoi_actions.grants import CapabilityRule, GrantDefinition, GrantStore  # noqa: E402

MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql")
TEST_VALUES = json.loads((Path(__file__).resolve().parent / "authority.test_values.json").read_text())
AUTH = Authority.parse(TEST_VALUES)
# A test catalogue: the delegation capability as a plain zero-spend grant (S1-C adds delegate.card).
GRANTS = GrantStore({"delegate.start_build": CapabilityRule(1, False, True)})
CAPS = ["repo:personal-ai-agents", "scope:research", "scope:build", "actor:fakework", "actor:claude-code",
        "host:mac-local"]
V1_CAPS = {"cost_enforced_actors": ["claude-code"], "scopes": ["research"], "repositories": ["personal-ai-agents"]}
DIGEST = "sha256:" + "c" * 64


def facts(host, **over):
    return {"host_id": host, "bundle_digest": DIGEST, "roles": ["authority", "worker", "witness"],
            "availability": "intermittent", "grace": 900, "last_gap_s": 0, "announced": "online", **over}


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class DispatcherV2(PostgresFixture, unittest.TestCase):
    migrations = MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations, guild.mc_action_grant_consumptions, guild.mc_action_grants,
                guild.mc_action_events, guild.mc_action_receipts CASCADE""")
        self.store = self.make_store(AUTH)
        self.n = 0

    def make_store(self, authority):
        return DispatcherStore(self.connect, principal="robert", environment="dev", authority=authority, grants=GRANTS)

    # ── helpers ──
    def sql(self, query, args=()):
        from psycopg2.extras import RealDictCursor
        with self.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, args)
            return cur.fetchall() if cur.description else None

    def db_now(self):
        return self.sql("SELECT clock_timestamp() AS now")[0]["now"]

    def grant(self, *, seconds=600, work_id=None):
        gid = str(uuid4())
        with self.connect() as conn:
            GRANTS.issue(conn, GrantDefinition(gid, "robert", "dev", "conversation", ("owner-turn",), ("mc",), "policy",
                                               "delegate.start_build", 1, ("d" * 64,), 1,
                                               self.db_now() + timedelta(seconds=seconds), work_id=work_id))
        return gid

    def beat(self, runner="mac-a", host=None, **over):
        return self.store.heartbeat(runner, "online", CAPS, protocol=2, facts=facts(host or runner, **over))

    def item(self, route="fakework", amount=1.5, *, grant=True, actor="fakework", scope="build", caps=(), arts=(),
             job=None):
        self.n += 1
        job = job or f"job-{self.n}"
        self.assertEqual(self.store.enqueue(
            job_id=job, actor=actor, scope=scope, repository="personal-ai-agents", ref="a" * 40,
            brief="Run the fakework program.", cost_cap_usd=1.0, deadline_s=600, route=route, amount=amount,
            grant_id=(self.grant() if grant is True else grant) or None, required_capabilities=list(caps),
            input_artifacts=list(arts)), "accepted")
        time.sleep(0.002)                                          # distinct created_at, so "oldest" is well defined
        return job

    def row(self, job):
        return self.store.by_job(job)

    def claim(self, runner="mac-a", caps=CAPS):
        return self.store.claim(runner, caps, protocol=2)

    def events(self, kind=None, item=None):
        rows = self.sql("SELECT * FROM guild.mc_delegation_events ORDER BY seq")
        return [r for r in rows if (kind is None or r["kind"] == kind)
                and (item is None or str(r["item_id"]) == str(item))]

    def day_rows(self, route="fakework"):
        return self.sql("SELECT * FROM guild.mc_delegation_reservations WHERE route=%s ORDER BY day", (route,))

    def progress(self, env, at, *, runner="mac-a", event_id=None, phase="executing", checkpoint=None):
        return self.store.progress(env["request_id"], env["generation"], phase, protocol=2, runner_id=runner,
                                   event_id=event_id or str(uuid4()), at=at.isoformat(), checkpoint=checkpoint)

    def result(self, env, *, runner="mac-a", event_id=None, status="completed", route="fakework", amount=1.0,
               unit="test_units", stopped=True, at=None, generation=None):
        return self.store.result(env["request_id"], env["generation"] if generation is None else generation,
                                 event_id=event_id or str(uuid4()), status=status, artifacts=[], detail={},
                                 output_text="done", protocol=2, runner_id=runner,
                                 at=(at or self.db_now()).isoformat(), route=route,
                                 usage={"amount": amount, "unit": unit, "children_stopped": stopped})

    def race(self, *runners):
        """Each runner claims at the same instant, on its own connection."""
        barrier = threading.Barrier(len(runners))

        def go(r):
            barrier.wait()
            return self.claim(r)
        with ThreadPoolExecutor(len(runners)) as pool:
            return list(pool.map(go, runners))

    # ── the migration ──
    def test_migration_is_additive_and_idempotent(self):
        """001 then 002 into a scratch schema with a version 1 row in it; then both again, twice: nothing changes."""
        texts = [(ROOT / m).read_text().replace("guild.", "mig_check.") for m in MIGRATIONS[:2]]
        shape = """SELECT
            (SELECT json_agg(x ORDER BY x) FROM (SELECT table_name || '.' || column_name || ':' || data_type || ':'
               || is_nullable || ':' || coalesce(column_default, '') AS x FROM information_schema.columns
               WHERE table_schema='mig_check') c) AS columns,
            (SELECT json_agg(x ORDER BY x) FROM (SELECT conrelid::regclass::text || '.' || conname || ':'
               || pg_get_constraintdef(oid) AS x FROM pg_constraint WHERE connamespace='mig_check'::regnamespace) k)
               AS constraints,
            (SELECT json_agg(indexdef ORDER BY indexdef) FROM pg_indexes WHERE schemaname='mig_check') AS indexes"""
        try:
            with self.connect() as conn, conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS mig_check CASCADE; CREATE SCHEMA mig_check")
                cur.execute(texts[0])
                cur.execute("""INSERT INTO mig_check.mc_delegations (request_id, job_id, principal_id, environment, actor,
                    scope, repository, ref, brief, cost_cap_usd, deadline, state)
                    VALUES (gen_random_uuid(), 'old-job', 'robert', 'dev', 'codex', 'research', 'r', 'ref', 'b', 2,
                            clock_timestamp(), 'completed')""")
                cur.execute("INSERT INTO mig_check.mc_delegation_runners (runner_id, state, capabilities) VALUES ('m','online','{}')")
                cur.execute(texts[1])
                cur.execute(shape)
                first = cur.fetchone()
                for _ in range(2):
                    cur.execute(texts[0])
                    cur.execute(texts[1])
                cur.execute(shape)
                self.assertEqual(cur.fetchone(), first)
                cur.execute("SELECT state, route, required_capabilities, input_artifacts FROM mig_check.mc_delegations")
                self.assertEqual(cur.fetchone(), ("completed", None, [], []))
                cur.execute("SELECT protocol, host_id FROM mig_check.mc_delegation_runners")
                self.assertEqual(cur.fetchone(), (1, None))
                defs = " ".join(first[1])
                for name in ("'proposed'", "'lapsed'", "'paused'", "'unknown'", "'expired'", "'fakework'"):
                    self.assertIn(name, defs)
                # One state check and one actor check, not an old narrow one left beside the new.
                self.assertEqual(sum("state = ANY" in d for d in first[1]), 1)
                self.assertEqual(sum("actor = ANY" in d for d in first[1]), 1)
        finally:
            with self.connect() as conn, conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS mig_check CASCADE")

    # ── version 1 on the migrated schema ──
    def test_version_1_keeps_its_meaning_on_the_migrated_schema(self):
        for authority in (None, AUTH, Authority.load(ROOT / "core/mc_delegation_dispatcher/authority.prod.json")):
            self.setUp()
            store = DispatcherStore(self.connect, principal="robert",
                                    environment="prod" if authority is not None and authority.environment == "prod"
                                    else "dev", authority=authority, grants=GRANTS if authority is AUTH else None)
            if authority is AUTH:
                self.beat()
                self.item(actor="claude-code", scope="research")   # an older version 2 item a v1 runner could match
            self.assertEqual(store.enqueue(job_id="v1-job", actor="claude-code", scope="research",
                                           repository="personal-ai-agents", ref="a" * 40, brief="Research.",
                                           cost_cap_usd=2.0, deadline_s=600), "accepted")
            store.heartbeat("mac-1", "online", V1_CAPS)
            env = store.claim("mac-1", V1_CAPS)
            self.assertEqual((env["work_id"], env["generation"], env["input_artifacts"]), ("v1-job", 1, []))
            store.progress(env["request_id"], 1, "executing")
            self.assertFalse(store.cancel_requested(env["request_id"], 1))
            self.assertTrue(store.renew(env["request_id"], 1))
            eid = str(uuid4())
            self.assertEqual(store.result(env["request_id"], 1, event_id=eid, status="completed", artifacts=[],
                                          detail={}, output_text="answer"), {"state": "accepted", "event_id": eid})
            row = store.by_job("v1-job")
            self.assertEqual((row["state"], row["route"], row["reservation"], row["output_text"]),
                             ("completed", None, None, "answer"))
            self.assertEqual(self.sql("SELECT protocol FROM guild.mc_delegation_runners WHERE runner_id='mac-1'"),
                             [{"protocol": 1}])
            self.assertEqual(self.events(item=env["request_id"]), [])            # version 1 writes no audit
            if authority is AUTH:
                self.assertEqual(self.row("job-1")["state"], "queued")         # the version 2 item was never touched

    def test_a_version_1_claim_without_the_configuration_cannot_take_a_version_2_item(self):
        import psycopg2
        self.beat()
        self.item(actor="claude-code", scope="research")
        plain = DispatcherStore(self.connect, principal="robert", environment="dev")
        with self.assertRaises(psycopg2.errors.CheckViolation):              # mc_delegations_v2_owned_reserved
            plain.claim("mac-1", V1_CAPS)
        self.assertEqual((self.row("job-1")["state"], self.row("job-1")["generation"]), ("queued", 0))

    def test_version_1_and_version_2_never_act_on_each_others_items(self):
        self.beat()
        self.item()
        env = self.claim()
        rid = env["envelope"]["request_id"]
        self.assertFalse(self.store.renew(rid, 1))
        self.assertFalse(self.store.cancel_requested(rid, 1))
        self.store.progress(rid, 1, "executing")
        self.assertIsNone(self.row("job-1")["progress_phase"])
        self.assertEqual(self.store.result(rid, 1, event_id=str(uuid4()), status="refused", artifacts=[], detail={})["code"],
                         "protocol_mismatch")
        self.store.enqueue(job_id="v1-job", actor="claude-code", scope="research", repository="personal-ai-agents",
                           ref="a" * 40, brief="Research.", cost_cap_usd=2.0, deadline_s=600)
        v1 = self.store.claim("mac-1", V1_CAPS)
        self.assertEqual(self.store.renew(v1["request_id"], 1, protocol=2, runner_id="mac-1"), False)
        self.assertEqual(self.result({"request_id": v1["request_id"], "generation": 1}, runner="mac-1")["code"],
                         "protocol_mismatch")

    # ── heartbeat ──
    def test_heartbeat_records_contact_and_audits_only_a_change_of_facts(self):
        self.assertEqual(self.beat(), {"state": "recorded"})
        first = self.sql("SELECT * FROM guild.mc_delegation_runners")[0]
        self.assertEqual((first["host_id"], first["protocol"], first["environment"], first["roles"], first["announced"],
                          first["unreachable_grace_s"], first["expected_availability"], first["bundle_digest"]),
                         ("mac-a", 2, "dev", ["authority", "worker", "witness"], "online", 900, "intermittent", DIGEST))
        self.beat()
        second = self.sql("SELECT * FROM guild.mc_delegation_runners")[0]
        self.assertGreater(second["seen_at"], first["seen_at"])                # the host's last contact
        self.assertEqual(second["announced_at"], first["announced_at"])
        self.assertEqual(len(self.events("host.facts")), 1)
        self.beat(announced="stopping", last_gap_s=612.5)
        third = self.sql("SELECT * FROM guild.mc_delegation_runners")[0]
        self.assertGreater(third["announced_at"], first["announced_at"])
        [_, change] = self.events("host.facts")
        self.assertEqual((change["host_id"], change["source"], change["body"]["changed"]),
                         ("mac-a", "host", ["announced", "last_gap_s"]))
        with self.assertRaisesRegex(AuthorityRefused, "runner_host_mismatch"):     # an owner id is never taken over
            self.beat("mac-a", host="staging-1")

    def test_a_host_without_current_facts_gets_nothing(self):
        self.item()
        self.assertIsNone(self.claim())                                         # never in contact
        self.beat(announced="stopping")
        self.assertIsNone(self.claim())
        self.beat(roles=["witness"])
        self.assertIsNone(self.claim())
        self.beat()
        self.store.heartbeat("mac-a", "online", V1_CAPS)                         # it last spoke version 1
        self.assertIsNone(self.claim())
        self.beat()
        self.assertIsNotNone(self.claim())

    # ── claim ──
    def test_claim_matches_capabilities_reserves_and_returns_inputs_by_digest(self):
        self.beat()
        arts = [{"ref": "build-output.patch", "sha256": "e" * 64}]
        self.item(caps=["host:always-on"])
        self.item(caps=["host:mac-local"], arts=arts)
        claimed = self.claim()
        self.assertEqual(claimed["envelope"]["work_id"], "job-2")              # job-1 needs a host this is not
        self.assertEqual(claimed["envelope"]["input_artifacts"], arts)
        self.assertEqual((claimed["route"], claimed["unit"], claimed["reserved"], claimed["envelope"]["actor"]),
                         ("fakework", "test_units", 1.5, "fakework"))
        row = self.row("job-2")
        self.assertEqual((row["state"], row["generation"], row["runner_id"]), ("claimed", 1, "mac-a"))
        self.assertEqual({k: row["reservation"][k] for k in ("reserved", "unit", "generation")},
                         {"reserved": 1.5, "unit": "test_units", "generation": 1})
        self.assertIn("picked_up_at", row["facts"])
        lease_s = (row["lease_until"] - row["heartbeat_at"]).total_seconds()
        self.assertAlmostEqual(lease_s, 60, delta=1)                           # the test lease
        [day] = self.day_rows()
        self.assertEqual((float(day["reserved"]), float(day["observed"]), day["unit"], float(day["cap"])),
                         (1.5, 0.0, "test_units", 2.0))
        [ev] = self.events("item.claimed")
        self.assertEqual((ev["host_id"], ev["generation"], ev["schema"], ev["source"]),
                         ("mac-a", 1, "minimoi.runtime.event.v1", "authority"))
        self.assertEqual(self.row("job-1")["state"], "queued")
        self.item(amount=0.5)                                                   # a host that cannot run fakework
        self.assertIsNone(self.claim(caps=[c for c in CAPS if c != "actor:fakework"]))
        self.assertEqual((self.row("job-3")["state"], self.row("job-3")["reservation"]), ("queued", {"requested": 0.5}))

    def test_a_host_with_unreconciled_items_gets_nothing(self):
        self.beat()
        self.beat("mac-b")
        self.item(amount=0.5)
        env = self.claim()
        self.item(amount=0.5)
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' WHERE job_id='job-1'")
        self.assertIsNone(self.claim())                                         # its own lapsed claim comes first
        self.assertIsNone(self.claim())
        [withheld] = self.events("host.claim_withheld")                         # recorded once, not every poll
        self.assertEqual(withheld["body"]["items"], [f"{env['envelope']['request_id']}:1"])
        self.assertEqual(self.claim("mac-b")["envelope"]["work_id"], "job-2")   # another host is not held back
        for state, blocks in (("paused", True), ("lapsed", True), ("unknown", False)):
            self.sql("UPDATE guild.mc_delegations SET state=%s WHERE job_id='job-1'", (state,))
            self.item(amount=0.5, job=f"after-{state}")
            self.assertEqual(self.claim() is None, blocks, state)

    # ── the route gate (T11b) ──
    def test_route_gate_t11b(self):
        self.beat()
        self.item(route="fakework.subscription")
        self.item(route="anthropic.api", amount=3.0, actor="claude-code")
        self.item(route="fakework")
        claimed = self.claim()
        self.assertEqual(claimed["envelope"]["work_id"], "job-3")               # the fakework item runs
        sub, api = self.row("job-1"), self.row("job-2")
        self.assertEqual((sub["state"], sub["result_detail"], sub["generation"]),
                         ("refused", {"code": "route_supervised_only", "reason": "route_configured"}, 0))
        self.assertEqual((api["state"], api["result_detail"], api["generation"]),
                         ("refused", {"code": "route_supervised_only", "reason": "closure_pending"}, 0))
        self.assertEqual(len(self.events("item.refused")), 2)
        self.assertEqual(self.day_rows("anthropic.api"), [])                   # nothing reserved for a refused item
        # A result on a route other than the reserved one is refused, and nothing is settled.
        env = {"request_id": claimed["envelope"]["request_id"], "generation": 1}
        self.assertEqual(self.result(env, route="fakework.api", unit="usd")["code"], "route_mismatch")
        self.assertEqual(self.result(env, unit="usd")["code"], "unit_mismatch")
        self.assertEqual((self.row("job-3")["state"], float(self.day_rows()[0]["reserved"])), ("claimed", 1.5))
        self.assertEqual(self.result(env)["state"], "accepted")
        # Once the owner records closure as verified, an API-route item may run.
        verified = self.make_store(Authority.parse({**TEST_VALUES, "launch_closure": "verified"}))
        self.store = verified
        self.item(route="anthropic.api", amount=3.0, actor="claude-code")
        self.assertEqual(self.claim()["route"], "anthropic.api")

    # ── reservations: T11m, units ──
    def test_reservation_race_t11m(self):
        for round_ in range(3):
            self.setUp()
            self.beat("mac-a")
            self.beat("mac-b")
            self.item()
            self.item()
            outcomes = self.race("mac-a", "mac-b")
            self.assertEqual(sum(o is not None for o in outcomes), 1, f"round {round_}: {outcomes}")
            self.claim("mac-a")
            self.claim("mac-b")
            states = sorted((self.row(j)["state"], (self.row(j)["reservation"] or {}).get("held")) for j in ("job-1", "job-2"))
            self.assertEqual(states, [("claimed", None), ("queued", "reservation_cap")])
            self.assertEqual(float(self.day_rows()[0]["reserved"]), 1.5)
        # The admitted item reports usage unknown: the route closes until a recorded resolution.
        claimed = next(j for j in ("job-1", "job-2") if self.row(j)["state"] == "claimed")
        waiting = "job-2" if claimed == "job-1" else "job-1"
        row = self.row(claimed)
        env = {"request_id": str(row["request_id"]), "generation": row["generation"]}
        self.assertEqual(self.result(env, runner=row["runner_id"], amount=None)["state"], "accepted")
        self.assertEqual(self.row(claimed)["reservation"]["state"], "usage_unknown")
        self.assertEqual(float(self.day_rows()[0]["reserved"]), 1.5)          # unknown is never counted as zero
        small = self.item(amount=0.1)
        self.assertIsNone(self.claim("mac-a"))
        self.assertEqual(self.row(small)["reservation"]["held"], "usage_unknown")
        with self.assertRaises(AuthorityRefused):
            self.store.resolve_usage_hold("fakework", note=" ", by="robert")
        self.assertTrue(self.store.resolve_usage_hold("fakework", note="Reconciled against the fakework log.", by="robert"))
        self.assertFalse(self.store.resolve_usage_hold("fakework", note="again", by="robert"))
        [resolved] = self.events("reservation.hold_resolved")
        self.assertEqual((resolved["source"], resolved["body"]["by"], resolved["body"]["holds"][0]["items"][0]["item_id"]),
                         ("owner", "robert", env["request_id"]))
        self.assertEqual(self.claim("mac-a")["envelope"]["work_id"], small)  # 1.5 still held + 0.1 fits under 2.0
        self.assertEqual(self.row(waiting)["reservation"]["held"], "reservation_cap")

    def test_concurrent_claims_across_many_grants_neither_deadlock_nor_overspend(self):
        hosts = [f"mac-{c}" for c in "abcd"]
        for h in hosts:
            self.beat(h)
        for _ in range(8):
            self.item()                                                         # each under its own grant
        got = []
        for _ in range(3):
            got.extend(o for o in self.race(*hosts) if o is not None)           # any lock wait error fails here
        self.assertEqual(len(got), 1)
        held = [self.row(f"job-{i}")["reservation"].get("held") for i in range(1, 9)]
        self.assertEqual(sorted(h or "" for h in held), [""] + ["reservation_cap"] * 7)
        self.assertEqual(float(self.day_rows()[0]["reserved"]), 1.5)

    def test_units_are_never_summed(self):
        self.beat()
        self.item(route="fakework", amount=1.5)
        self.item(route="fakework.api", amount=3.0)
        self.assertEqual(self.claim()["unit"], "test_units")
        self.assertEqual(self.claim()["unit"], "usd")
        rows = {r["route"]: r for r in self.sql("SELECT * FROM guild.mc_delegation_reservations")}
        self.assertEqual({k: (r["unit"], float(r["reserved"])) for k, r in rows.items()},
                         {"fakework": ("test_units", 1.5), "fakework.api": ("usd", 3.0)})
        # The route's unit changes mid-day: the day's row keeps its unit, and nothing is added to it.
        changed = {**TEST_VALUES, "routes": {**TEST_VALUES["routes"],
                                             "fakework": {"unit": "usd", "per_day": 10.0, "per_item": 3.0,
                                                          "supervised_only": False}}}
        self.store = self.make_store(Authority.parse(changed))
        self.item(route="fakework", amount=0.1)
        self.assertIsNone(self.claim())
        self.assertEqual(self.row("job-3")["reservation"]["held"], "route_unit_changed")
        self.assertEqual(float(self.day_rows()[0]["reserved"]), 1.5)

    def test_result_settles_and_a_cancelled_item_keeps_its_reservation_until_its_children_stop(self):
        self.beat()
        self.item(amount=0.5)
        self.item(amount=0.5)
        a, b = self.claim(), self.claim()
        a = {"request_id": a["envelope"]["request_id"], "generation": 1}
        b = {"request_id": b["envelope"]["request_id"], "generation": 1}
        self.assertEqual(self.result(a, amount=0.25)["state"], "accepted")
        row = self.row("job-1")
        self.assertEqual((row["reservation"]["state"], row["reservation"]["observed"]), ("settled", 0.25))
        self.assertTrue({"reported_at", "released_at"} <= set(row["facts"]))
        day = self.day_rows()[0]
        self.assertEqual((float(day["reserved"]), float(day["observed"])), (0.5, 0.25))
        self.assertEqual(self.store.stop("job-2"), "requested")
        self.assertEqual(self.result(b, amount=0.1, stopped=False)["state"], "accepted")
        row = self.row("job-2")
        self.assertEqual((row["state"], row["reservation"]["state"], row["reservation"]["kept"]),
                         ("cancelled", "kept", "children_unconfirmed"))       # Stop was pressed; children unconfirmed
        self.assertNotIn("released_at", row["facts"])
        day = self.day_rows()[0]
        self.assertEqual((float(day["reserved"]), float(day["observed"])), (0.5, 0.25))   # still reserved
        self.assertEqual([e["kind"] for e in self.events(item=b["request_id"])],
                         ["item.queued", "item.claimed", "item.cancel_requested", "result.accepted", "reservation.kept"])

    # ── grants: T10 ──
    def test_an_expired_grant_ends_the_item_expired_never_claimed_w_exp(self):
        self.beat()
        self.item(grant=self.grant(seconds=1))
        deadline = time.monotonic() + 10
        while self.sql("SELECT expires_at <= clock_timestamp() AS past FROM guild.mc_action_grants")[0]["past"] is False:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.2)
        self.assertIsNone(self.claim())
        row = self.row("job-1")
        self.assertEqual((row["state"], row["result_detail"], row["generation"], row["runner_id"]),
                         ("expired", {"code": "grant_expired"}, 0, None))
        self.assertEqual(len(self.events("item.expired")), 1)
        self.assertEqual(self.day_rows(), [])

    def test_grant_checks_at_claim(self):
        self.beat()
        revoked = self.grant()
        with self.connect() as conn:
            GRANTS.revoke(conn, "robert", "dev", revoked)
        self.item(grant=revoked)
        self.item(grant=str(uuid4()))                                            # names no grant that exists
        self.item(grant=self.grant(work_id="another-card"))
        self.item(grant=False)                                                   # no grant: proposed, waits on a decision
        self.item(grant=self.grant(work_id="job-5"), job="job-5")
        self.assertEqual(self.claim()["envelope"]["work_id"], "job-5")
        got = {j: (self.row(j)["state"], (self.row(j)["result_detail"] or {}).get("code"))
               for j in ("job-1", "job-2", "job-3", "job-4")}
        self.assertEqual(got, {"job-1": ("cancelled", "grant_revoked"), "job-2": ("refused", "grant_not_found"),
                               "job-3": ("refused", "grant_work_mismatch"), "job-4": ("proposed", None)})

    def test_a_revoked_grant_makes_renew_false(self):
        self.beat()
        gid = self.grant()
        self.item(grant=gid)
        env = self.claim()
        rid = env["envelope"]["request_id"]
        self.assertTrue(self.store.renew(rid, 1, protocol=2, runner_id="mac-a"))
        self.assertFalse(self.store.renew(rid, 1, protocol=2, runner_id="mac-b"))   # not the owner
        before = self.row("job-1")["lease_until"]
        with self.connect() as conn:
            GRANTS.revoke(conn, "robert", "dev", gid)
        self.assertFalse(self.store.renew(rid, 1, protocol=2, runner_id="mac-a"))
        self.assertFalse(self.store.renew(rid, 1, protocol=2, runner_id="mac-a"))
        self.assertEqual(self.row("job-1")["lease_until"], before)
        [refused] = self.events("lease.renew_refused")
        self.assertEqual(refused["body"], {"code": "grant_revoked"})

    # ── progress ──
    def test_progress_keeps_the_newest_and_never_touches_the_lease(self):
        self.beat()
        self.item()
        claimed = self.claim()
        env = {"request_id": claimed["envelope"]["request_id"], "generation": 1}
        before = self.row("job-1")
        t0 = self.db_now() - timedelta(seconds=30)                              # events made earlier, inside the lease
        cp1 = {"name": "s1", "digest": "sha256:" + "1" * 64, "paths": ["README.md"]}
        cp2 = {"name": "s2", "digest": "sha256:" + "2" * 64, "paths": ["README.md", "core/x.py"]}
        e2 = str(uuid4())
        self.assertEqual(self.progress(env, t0, event_id=e2, phase="building", checkpoint=cp2)["state"], "applied")
        row = self.row("job-1")
        self.assertEqual((row["state"], row["progress_phase"], row["checkpoint"]["name"]), ("running", "building", "s2"))
        self.assertEqual((row["lease_until"], row["heartbeat_at"]), (before["lease_until"], before["heartbeat_at"]))
        # An older event (a replayed pending record) never overwrites the newer one.
        self.assertEqual(self.progress(env, t0 - timedelta(seconds=5), phase="starting", checkpoint=cp1)["state"],
                         "superseded")
        row = self.row("job-1")
        self.assertEqual((row["progress_phase"], row["checkpoint"]["name"], row["progress_at"]), ("building", "s2", t0))
        self.assertEqual(self.progress(env, t0, event_id=e2, phase="building", checkpoint=cp2)["state"], "already_applied")
        # A newer phase without a checkpoint keeps the stored checkpoint.
        self.assertEqual(self.progress(env, t0 + timedelta(seconds=1), phase="testing")["state"], "applied")
        row = self.row("job-1")
        self.assertEqual((row["progress_phase"], row["checkpoint"]["name"]), ("testing", "s2"))
        self.assertEqual((row["lease_until"], row["heartbeat_at"]), (before["lease_until"], before["heartbeat_at"]))
        self.assertEqual(len(self.events("item.progress")), 3)                  # the superseded one is still audited
        # Only the owner of the current generation, with an event time inside its lease.
        self.assertEqual(self.progress(env, t0, runner="mac-b")["code"], "not_owner")
        self.assertEqual(self.progress({**env, "generation": 2}, t0)["code"], "stale_generation")
        self.assertEqual(self.progress(env, t0 + timedelta(seconds=120))["code"], "event_time_invalid")
        lease = row["lease_until"]
        self.assertEqual(self.progress(env, lease)["code"], "lease_lapsed")
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second'")
        late = self.row("job-1")["lease_until"]
        self.assertEqual(self.progress(env, late - timedelta(seconds=1), phase="replayed")["state"], "applied")
        self.assertEqual(self.progress(env, late + timedelta(seconds=1))["code"], "lease_lapsed")
        # An event id is one event: reusing it for another item is refused.
        self.item(amount=0.5)
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() + interval '60 second' WHERE job_id='job-1'")
        second = self.claim()
        env2 = {"request_id": second["envelope"]["request_id"], "generation": 1}
        self.assertEqual(self.progress(env2, self.db_now(), event_id=e2)["code"], "event_id_reused")

    # ── DUP-3, DUP-4 at unit level ──
    def test_dup3_one_host_holds_a_lease_at_a_time(self):
        self.beat("mac-a")
        self.beat("mac-b")
        for _ in range(4):
            self.item(amount=0.25)
        got = []
        for _ in range(4):
            got.extend(o for o in self.race("mac-a", "mac-b") if o is not None)
        self.assertEqual(sorted(o["envelope"]["work_id"] for o in got), ["job-1", "job-2", "job-3", "job-4"])
        for o in got:
            rid, owner = o["envelope"]["request_id"], self.row(o["envelope"]["work_id"])["runner_id"]
            other = "mac-b" if owner == "mac-a" else "mac-a"
            self.assertTrue(self.store.renew(rid, 1, protocol=2, runner_id=owner))
            self.assertFalse(self.store.renew(rid, 1, protocol=2, runner_id=other))
        # From the audit: per item, lease intervals [claimed at, last lease_until] by host never overlap.
        intervals = {}
        for ev in self.events():
            if ev["kind"] in ("item.claimed", "lease.renewed"):
                key = (str(ev["item_id"]), ev["generation"], ev["host_id"])
                start, _ = intervals.get(key, (ev["at"], None))
                intervals[key] = (start, ev["body"]["lease_until"])
        by_item = {}
        for (item, _, host), span in intervals.items():
            by_item.setdefault(item, []).append((host, span))
        self.assertEqual(len(by_item), 4)
        for item, spans in by_item.items():
            hosts = {h for h, _ in spans}
            self.assertEqual(len(hosts), 1, item)                              # never two hosts on one item

    def test_dup4_one_accepted_result_per_item(self):
        self.beat("mac-a")
        self.beat("mac-b")
        self.item()
        claimed = self.claim()
        env = {"request_id": claimed["envelope"]["request_id"], "generation": 1}
        self.assertEqual(self.result(env, runner="mac-b")["code"], "not_owner")
        self.assertEqual(self.result(env, generation=2)["code"], "stale_generation")
        eid = str(uuid4())
        self.assertEqual(self.result(env, event_id=eid), {"state": "accepted", "event_id": eid})
        self.assertEqual(self.result(env, event_id=eid)["state"], "already_applied")
        late = self.result(env, event_id=str(uuid4()), status="failed")
        self.assertEqual((late["state"], late["code"]), ("refused", "different_result_already_recorded"))
        self.assertEqual(len(self.events("result.accepted", item=env["request_id"])), 1)
        self.assertEqual(str(self.row("job-1")["result_event_id"]), eid)
        # A result made after the lease ran out is not taken (R5.1-c); one made before it, sent late, is.
        self.item(amount=0.5)
        nxt = self.claim()
        env2 = {"request_id": nxt["envelope"]["request_id"], "generation": 1}
        made = self.db_now() - timedelta(seconds=2)
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' WHERE job_id='job-2'")
        self.assertEqual(self.result(env2, at=self.db_now() + timedelta(seconds=1))["code"], "lease_lapsed")
        self.assertEqual(self.result(env2, at=made, amount=0.5)["state"], "accepted")
        # Refused results are audited once per event and reason, however often they are resent.
        self.assertEqual(len([e for e in self.events("result.refused") if e["body"]["code"] == "not_owner"]), 1)

    def test_every_transition_writes_one_audit_event(self):
        self.beat()
        self.item()
        claimed = self.claim()
        env = {"request_id": claimed["envelope"]["request_id"], "generation": 1}
        self.progress(env, self.db_now(), checkpoint={"name": "s1", "digest": DIGEST, "paths": []})
        self.assertTrue(self.store.renew(env["request_id"], 1, protocol=2, runner_id="mac-a"))
        self.result(env, amount=1.0)
        evs = self.events(item=env["request_id"])
        self.assertEqual([e["kind"] for e in evs], ["item.queued", "item.claimed", "item.progress", "lease.renewed",
                                                    "result.accepted", "reservation.settled"])
        for e in evs:                                                           # R7.6-d's fields
            self.assertTrue(e["event_id"] and e["at"] and e["schema"] == "minimoi.runtime.event.v1")
            self.assertEqual(str(e["item_id"]), env["request_id"])
        self.assertEqual([e["generation"] for e in evs], [0, 1, 1, 1, 1, 1])
        self.assertEqual([e["host_id"] for e in evs], [None, "mac-a", "mac-a", "mac-a", "mac-a", "mac-a"])
        self.assertEqual([e["source"] for e in evs], ["owner", "authority", "host", "authority", "host", "authority"])


if __name__ == "__main__":
    unittest.main()
