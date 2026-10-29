"""Recovery at the authority on a real PostgreSQL (host runtime spec v0.5 §3.2 reconcile and effect, §4.3-e, §4.5, §5,
§9.4-§9.5): reconcile's answers and their idempotence (X5), effects through receipts (DUP-1, DUP-2, DUP-5), an unknown
outcome waiting on the owner (T4c, DUP-6), readback and resume (T4b), the sweeper's lapse, grace and outage (T5c/T6b at
the authority, X7, R5.2-a), a dead sweeper (E11), revocation end to end (T10's W-REV), reassignment across two hosts
(DUP-3, R5.1-d), the kept reservation's release (R8.3-c) and the relay's reap moving to the sweeper (R3.3).
Opt-in: a disposable mc12_test_* database (tests/minimoi_actions/postgres_fixture_support.py). Time is moved by setting
lease, grace and heartbeat columns relative to the database clock, never by sleeping."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import sys
import time
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))

from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_delegation_dispatcher.store import (EFFECT_POLICY, Authority, AuthorityRefused,  # noqa: E402
                                                 DispatcherStore, effect_operation_id, item_subject_digest, parse_at)
from core.minimoi_actions.grants import CapabilityRule, GrantDefinition, GrantStore  # noqa: E402
from core.minimoi_actions.requests import ToolRequest  # noqa: E402

MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql")
TEST_VALUES = json.loads((Path(__file__).resolve().parent / "authority.test_values.json").read_text())
AUTH = Authority.parse(TEST_VALUES)
SINK = "fakework.sink_post"            # the test sink's effect (§9.3), a zero-spend capability like any other
GRANTS = GrantStore({"delegate.start_build": CapabilityRule(1, False, True), SINK: CapabilityRule(1, False, True)})
CAPS = ["repo:personal-ai-agents", "scope:research", "scope:build", "actor:fakework", "actor:claude-code",
        "host:mac-local"]
DIGEST = "sha256:" + "c" * 64
BRIEF = "Run the fakework program."
AMOUNT, USED = 0.25, 0.125       # §9.2's day is 2.0 test_units: several items fit in one test
CP1 = {"name": "s1", "digest": "sha256:" + "1" * 64, "paths": ["README.md"]}
TRANSITIONS = ("item.claimed", "item.resumed", "item.reassigned")


def lease_spans(events):
    """{(generation, host): [start, end]} of an item's leases, from its audit (DUP-3): each starts at the transition
    that granted it and ends at its latest lease_until, or earlier where a lapse recorded it, or where the item moved
    to another generation or ended."""
    spans, ends = {}, {}
    for ev in events:
        body, g = ev["body"], ev["generation"]
        if ev["kind"] in TRANSITIONS or ev["kind"] == "lease.renewed":
            key = next((k for k in spans if k[0] == g), (g, ev["host_id"]))
            start, end = spans.get(key, [ev["at"], ev["at"]])
            spans[key] = [min(start, ev["at"]), max(end, parse_at(body["lease_until"]))]
        if ev["kind"] in ("item.lapsed", "item.paused") and "lease_until" in body:
            ends[g] = min(ends.get(g, ev["at"]), parse_at(body["lease_until"]))
        if ev["kind"] == "item.reassigned" or ev["kind"] == "item.resumed":
            ends[body["from_generation"]] = min(ends.get(body["from_generation"], ev["at"]), ev["at"])
        if ev["kind"] in ("item.completed", "item.cancelled", "item.interrupted", "item.unknown", "result.accepted"):
            ends[g] = min(ends.get(g, ev["at"]), ev["at"])
    return {k: [s, min(e, ends.get(k[0], e))] for k, (s, e) in spans.items()}


def facts(host, **over):
    return {"host_id": host, "bundle_digest": DIGEST, "roles": ["authority", "worker", "witness"],
            "availability": "intermittent", "grace": 900, "last_gap_s": 0, "announced": "online", **over}


def stage(child="stopped", cp=CP1):
    return {"child": child, "checkpoint": None if cp is None else {"name": cp["name"], "digest": cp["digest"]}}


class Sink:
    """The test sink's two rules (§9.3, R4.5-b): one committed line per operation id, and no send after its lease."""

    def __init__(self):
        self.lines = []

    def post(self, operation_id, lease_until, now):
        if any(op == operation_id and out == "committed" for op, out in self.lines):
            out = "duplicate"
        elif lease_until is None or now >= parse_at(lease_until):
            out = "lease_expired"
        else:
            out = "committed"
        self.lines.append((operation_id, out))
        return out

    def committed(self, operation_id):
        return sum(1 for op, out in self.lines if op == operation_id and out == "committed")


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class Recovery(PostgresFixture, unittest.TestCase):
    migrations = MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations, guild.mc_action_grant_consumptions, guild.mc_action_grants,
                guild.mc_action_events, guild.mc_action_receipts CASCADE""")
        self.store = self.make_store(AUTH)
        self.n, self.grant_of, self.sink = 0, {}, Sink()

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

    def grant(self, capability, digests, *, ops=1, work_id=None, actors=("mc",), policy="policy"):
        gid = str(uuid4())
        with self.connect() as conn:
            GRANTS.issue(conn, GrantDefinition(gid, "robert", "dev", work_id or "conversation", ("owner-turn",),
                                               tuple(actors), policy, capability, 1, tuple(digests), ops,
                                               self.db_now() + timedelta(seconds=600), work_id=work_id))
        return gid

    def beat(self, runner="mac-a", host=None, **over):
        return self.store.heartbeat(runner, "online", CAPS, protocol=2, facts=facts(host or runner, **over))

    def item(self, job=None, amount=AMOUNT):
        self.n += 1
        job = job or f"job-{self.n}"
        subject = item_subject_digest(actor="fakework", scope="build", repository="personal-ai-agents", ref="a" * 40,
                                      brief=BRIEF, route="fakework", amount=amount)
        gid = self.grant("delegate.start_build", (subject,))
        self.grant_of[job] = gid
        self.assertEqual(self.store.enqueue(
            job_id=job, actor="fakework", scope="build", repository="personal-ai-agents", ref="a" * 40, brief=BRIEF,
            cost_cap_usd=1.0, deadline_s=600, route="fakework", amount=amount, grant_id=gid), "accepted")
        time.sleep(0.002)
        return job

    def claimed(self, runner="mac-a", host=None, job=None):
        """A host checks in and claims one item: (job, envelope)."""
        self.beat(runner, host)
        job = self.item(job)
        got = self.store.claim(runner, CAPS, protocol=2)
        self.assertIsNotNone(got)
        return job, got["envelope"]

    def row(self, job):
        return self.store.by_job(job)

    def events(self, kind=None, item=None):
        rows = self.sql("SELECT * FROM guild.mc_delegation_events ORDER BY seq")
        return [r for r in rows if (kind is None or r["kind"] == kind)
                and (item is None or str(r["item_id"]) == str(item))]

    def progress(self, env, *, runner="mac-a", event_id=None, checkpoint=None, generation=None):
        return self.store.progress(env["request_id"], env["generation"] if generation is None else generation,
                                   "executing", protocol=2, runner_id=runner, event_id=event_id or str(uuid4()),
                                   at=self.db_now().isoformat(), checkpoint=checkpoint)

    def result(self, env, *, runner="mac-a", generation=None, stopped=True, amount=USED, status="completed"):
        return self.store.result(env["request_id"], env["generation"] if generation is None else generation,
                                 event_id=str(uuid4()), status=status, artifacts=[], detail={}, output_text="done",
                                 protocol=2, runner_id=runner, at=self.db_now().isoformat(), route="fakework",
                                 usage={"amount": amount, "unit": "test_units", "children_stopped": stopped})

    def reconcile(self, env, *, runner="mac-a", generation=None, child="stopped", cp=CP1, ops=(), store=None):
        return (store or self.store).reconcile(runner, env["request_id"],
                                               env["generation"] if generation is None else generation,
                                               stage(child, cp), list(ops))

    def lapse(self, job, seconds=1):
        """The item's lease ran out ``seconds`` ago, by the database clock."""
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - %s * interval '1 second' "
                 "WHERE job_id=%s", (seconds, job))

    def seen(self, runner, seconds_ago):
        self.sql("UPDATE guild.mc_delegation_runners SET seen_at = clock_timestamp() - %s * interval '1 second' "
                 "WHERE runner_id=%s", (seconds_ago, runner))

    def age_sweeper(self, seconds):
        self.sql("UPDATE guild.mc_delegation_runners SET seen_at = seen_at - %s * interval '1 second' "
                 "WHERE runner_id LIKE 'sweeper/%%'", (seconds,))

    # effects
    def effect_grant(self, job, *keys, ops=None):
        digests = tuple(ToolRequest(SINK, 1, json.dumps({"op": k}).encode()).arguments_sha256 for k in keys)
        return self.grant(SINK, digests, ops=ops or len(keys), work_id=job, actors=("fakework",), policy=EFFECT_POLICY)

    def effect(self, env, op, key, gid, *, runner="mac-a", generation=None, lease=None, state=None, evidence=None):
        e = {"op": op, "key": key, "grant_id": gid, "request": {"operation": SINK, "operation_version": 1,
                                                                  "arguments": {"op": key}}}
        if op in ("dispatch", "finish"):
            e["lease"] = {k: lease[k] for k in ("receipt_generation", "token", "mode")}
        if op == "finish":
            e.update(state=state, evidence=evidence)
        return self.store.effect(runner, env["request_id"], env["generation"] if generation is None else generation, e)

    def send(self, env, key, gid, **kw):
        """prepare → claim → dispatch → the send: (sink answer, the claim's lease)."""
        self.assertEqual(self.effect(env, "prepare", key, gid, **kw)["state"], "ok")
        claim = self.effect(env, "claim", key, gid, **kw)
        self.assertEqual(claim["state"], "ok", claim)
        sent = self.effect(env, "dispatch", key, gid, lease=claim["lease"], **kw)
        self.assertEqual(sent["state"], "ok", sent)
        return self.sink.post(sent["operation_id"], sent["lease_until"], self.db_now()), claim["lease"]

    def receipt(self, env, key):
        return self.sql("SELECT * FROM guild.mc_action_receipts WHERE operation_id=%s",
                        (effect_operation_id(env["request_id"], key),))[0]

    # ── reconcile ──
    def test_continue_while_the_lease_runs_and_resume_after_it_lapsed(self):
        job, env = self.claimed()
        self.assertEqual(self.progress(env, checkpoint=CP1)["state"], "applied")
        answer = self.reconcile(env, child="frozen")
        self.assertEqual((answer["answer"], answer["generation"]), ("continue", 1))
        self.assertEqual(self.row(job)["generation"], 1)
        self.lapse(job)
        answer = self.reconcile(env, child="frozen")
        self.assertEqual((answer["answer"], answer["generation"], answer["checkpoint"]),
                         ("resume", 2, {"name": "s1", "digest": CP1["digest"]}))
        row = self.row(job)
        self.assertEqual((row["state"], row["generation"], row["runner_id"]), ("claimed", 2, "mac-a"))
        self.assertGreater(row["lease_until"], row["now"])
        self.assertEqual(row["reservation"]["generation"], 2)
        # The old generation is fenced: its writes are refused; the new one's are taken.
        self.assertEqual(self.progress(env)["code"], "stale_generation")
        self.assertTrue(self.store.renew(env["request_id"], 2, protocol=2, runner_id="mac-a"))
        # A child that no longer runs is never continued, even inside its lease: a new attempt gets a new generation.
        answer = self.reconcile(env, generation=2, child="stopped")
        self.assertEqual((answer["answer"], answer["generation"]), ("resume", 3))
        self.assertEqual(len(self.events("item.reconciled", env["request_id"])), 3)

    def test_x5_the_generation_rises_once_per_decision(self):
        job, env = self.claimed()
        replayed = str(uuid4())
        self.progress(env, checkpoint=CP1, event_id=replayed)
        self.lapse(job)
        first = self.reconcile(env)
        # The authority restarts before the host hears the answer: a fresh store, the same question.
        again = self.reconcile(env, store=self.make_store(AUTH))
        self.assertEqual((first["answer"], first["generation"]), ("resume", 2))
        self.assertEqual({**again, "lease_until": None}, {**first, "lease_until": None})
        # Concurrent retries answer the same decision.
        with ThreadPoolExecutor(4) as pool:
            answers = list(pool.map(lambda _: self.reconcile(env), range(4)))
        self.assertEqual({(a["answer"], a["generation"]) for a in answers}, {("resume", 2)})
        self.assertEqual(self.row(job)["generation"], 2)
        self.assertEqual(len(self.events("item.resumed", env["request_id"])), 1)
        # The host's replay of its pending records applies nothing new (DUP-5).
        self.assertEqual(self.store.progress(env["request_id"], 1, "executing", protocol=2, runner_id="mac-a",
                                             event_id=replayed, at=self.events("item.progress")[0]["at"].isoformat(),
                                             checkpoint=CP1)["state"], "already_applied")
        # The answer stands even after its generation lapsed; once a newer one exists, the old question gets stop.
        self.lapse(job)
        self.assertEqual(self.reconcile(env)["generation"], 2)
        self.assertEqual(self.reconcile(env, generation=2)["generation"], 3)
        self.assertEqual(self.reconcile(env)["answer"], "stop")
        self.assertEqual(self.row(job)["generation"], 3)

    def test_each_failed_condition_is_named(self):
        # identity
        job, env = self.claimed()
        self.assertEqual(self.reconcile(env, runner="mac-b")["code"], "not_owner")
        self.assertEqual(self.reconcile(env, generation=7)["code"], "generation_unknown")
        self.assertEqual(self.store.reconcile("mac-a", str(uuid4()), 1, stage(), [])["code"], "unknown_request")
        for a in (self.reconcile(env, runner="mac-b"), self.reconcile(env, generation=7)):
            self.assertEqual(a["condition"], "identity")
        # checkpoint: the host can't rebuild at the checkpoint the authority holds
        self.progress(env, checkpoint=CP1)
        self.lapse(job)
        self.assertEqual(self.reconcile(env, cp=None),
                         {"answer": "stop", "condition": "checkpoint", "code": "checkpoint_mismatch",
                          "state": "interrupted"})
        row = self.row(job)
        self.assertEqual((row["state"], row["result_detail"]["workspace"], row["reservation"]["state"]),
                         ("interrupted", "kept", "unreported"))      # its child stopped; usage never reported
        # budget: the route closed by unknown usage elsewhere
        job2, env2 = self.claimed(runner="mac-c", job="job-budget")
        self.sql("UPDATE guild.mc_delegation_reservations SET usage_hold='{\"since\": \"x\", \"items\": []}'::jsonb")
        self.lapse(job2)
        a = self.reconcile(env2, runner="mac-c", cp=None, child="frozen")
        self.assertEqual((a["condition"], a["code"], a["state"]), ("budget", "usage_unknown", "interrupted"))
        self.assertEqual(self.row(job2)["reservation"]["state"], "kept")          # its frozen child isn't stopped yet
        self.sql("UPDATE guild.mc_delegation_reservations SET usage_hold=NULL")
        # authority: the deadline passed
        job3, env3 = self.claimed(runner="mac-d", job="job-late")
        self.sql("UPDATE guild.mc_delegations SET deadline = clock_timestamp() - interval '1 second', "
                 "lease_until = clock_timestamp() - interval '1 second' WHERE job_id='job-late'")
        a = self.reconcile(env3, runner="mac-d", cp=None)
        self.assertEqual((a["condition"], a["code"], a["state"]), ("authority", "deadline_passed", "timed_out"))
        # the owner's Stop
        job4, env4 = self.claimed(runner="mac-e", job="job-stop")
        self.assertEqual(self.store.stop("job-stop"), "requested")
        a = self.reconcile(env4, runner="mac-e", child="frozen", cp=None)
        self.assertEqual((a["answer"], a["code"], a["state"]), ("stop", "owner_stop", "cancelled"))
        # an ended item: stop with its own code, every time
        self.assertEqual(self.reconcile(env4, runner="mac-e", cp=None)["code"], "owner_stop")

    # ── effects ──
    def test_t4b_readback_verifies_and_the_resumed_attempt_never_repeats_the_effect_dup1(self):
        job, env = self.claimed()
        gid = self.effect_grant(job, "e1", "e2")
        self.progress(env, checkpoint=CP1)
        answer, _ = self.send(env, "e1", gid)
        self.assertEqual(answer, "committed")
        # The runtime is killed the moment e1 is committed: no finish. On restart the lease has lapsed.
        self.lapse(job)
        self.assertEqual(self.effect(env, "dispatch", "e2", gid,
                                     lease={"receipt_generation": 1, "token": "A" * 43, "mode": "execute"})["code"],
                         "lease_lapsed")
        # Readback finds e1 committed: the receipt is reconciled and verified, though the item has lapsed.
        self.assertEqual(self.effect(env, "reconcile", "e1", gid)["code"], "receipt_busy")   # its own lease first
        self.sql("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 second'")
        rec = self.effect(env, "reconcile", "e1", gid)
        self.assertEqual((rec["state"], rec["receipt_state"]), ("ok", "unknown"))
        self.assertEqual(self.effect(env, "finish", "e1", gid, lease=rec["lease"], state="verified",
                                     evidence={"kind": "readback", "outcome": "committed"})["state"], "ok")
        self.assertEqual(self.receipt(env, "e1")["state"], "verified")
        a = self.reconcile(env)
        self.assertEqual((a["answer"], a["generation"]), ("resume", 2))
        # The resumed attempt reaches e1 again from s1: the same operation id, already verified, never sent again.
        g2 = {**env, "generation": 2}
        again = self.effect(g2, "prepare", "e1", gid)
        self.assertEqual((again["state"], again["receipt_state"], again["operation_id"]),
                         ("ok", "verified", effect_operation_id(env["request_id"], "e1")))
        self.assertEqual(self.effect(g2, "claim", "e1", gid)["code"], "receipt_terminal")
        self.assertEqual(self.send(g2, "e2", gid)[0], "committed")
        self.assertEqual((self.sink.committed(effect_operation_id(env["request_id"], "e1")),
                          self.sink.committed(effect_operation_id(env["request_id"], "e2"))), (1, 1))
        consumed = self.sql("SELECT consumed_operations FROM guild.mc_action_grants WHERE grant_id=%s", (gid,))[0]
        self.assertEqual(consumed["consumed_operations"], 2)                       # e1 once, e2 once
        self.assertEqual(self.result(g2)["state"], "accepted")
        # The evidence is the authority's own audit row, cited by the receipt; no token or argument is recorded.
        ev = self.events("effect.evidence", env["request_id"])
        self.assertEqual(json.loads(json.dumps(self.receipt(env, "e1")["evidence_refs"])), [str(ev[0]["event_id"])])
        bodies = json.dumps([e["body"] for e in self.events()])
        self.assertNotIn(rec["lease"]["token"], bodies)
        self.assertNotIn('"arguments"', bodies)

    def test_t4c_an_unknown_outcome_decides_once_and_waits_on_the_owner_dup6(self):
        job, env = self.claimed()
        gid = self.effect_grant(job, "e1")
        self.progress(env, checkpoint=CP1)
        self.assertEqual(self.send(env, "e1", gid)[0], "committed")
        self.lapse(job)
        # The sink cannot be asked (--no-lookup): the host leaves the receipt dispatched and reconciles.
        a = self.reconcile(env, ops=[effect_operation_id(env["request_id"], "e1")])
        op = effect_operation_id(env["request_id"], "e1")
        self.assertEqual((a["answer"], a["code"], a["operations"]),
                         ("decide", "effect_unknown", [{"operation_id": op, "state": "dispatched"}]))
        row = self.row(job)
        self.assertEqual((row["state"], row["generation"], row["result_detail"]["workspace"]), ("unknown", 1, "kept"))
        self.assertEqual(row["reservation"]["reserved"], AMOUNT)                   # still reserved: nothing settled
        # Asked again (and by another call path), the same one decision; nothing raises the generation.
        self.assertEqual(self.reconcile(env)["decision_id"], a["decision_id"])
        self.store.sweep()
        self.assertIsNone(self.store.claim("mac-a", CAPS, protocol=2))
        self.beat("mac-b")
        self.assertIsNone(self.store.claim("mac-b", CAPS, protocol=2))
        with self.assertRaisesRegex(AuthorityRefused, "receipts_open"):
            self.store.reassign(env["request_id"], "mac-a", by="robert", note="try again")
        self.assertFalse(self.store.renew(env["request_id"], 1, protocol=2, runner_id="mac-a"))
        decisions = self.events("decision.queued", env["request_id"])
        self.assertEqual([(d["body"]["class"], d["body"]["solo"], str(d["event_id"])) for d in decisions],
                         [("unknown_resolution", True, a["decision_id"])])
        self.assertEqual(self.row(job)["generation"], 1)
        # The owner answers "end".
        self.assertEqual(self.store.end(env["request_id"], by="robert", note="T4c: end it"),
                         {"state": "interrupted", "code": "owner_decision"})
        row = self.row(job)
        self.assertEqual((row["state"], row["generation"], row["result_detail"]["code"]),
                         ("interrupted", 1, "owner_decision"))
        self.assertEqual([e["body"]["answer"] for e in self.events("decision.resolved")], ["end"])
        self.assertEqual(self.reconcile(env)["answer"], "stop")
        self.assertEqual(len(self.events("item.resumed")), 0)

    def test_an_unverifiable_child_decides_and_unknown_is_never_retried(self):
        job, env = self.claimed()
        a = self.reconcile(env, child="unverified", cp=None)
        self.assertEqual((a["answer"], a["code"], self.row(job)["state"]), ("decide", "process_unverified", "unknown"))
        # An unknown receipt can never be claimed to execute again.
        job2, env2 = self.claimed(runner="mac-b", job="job-u")
        gid = self.effect_grant(job2, "e1")
        _, lease = self.send(env2, "e1", gid, runner="mac-b")
        self.sql("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 second'")
        rec = self.effect(env2, "reconcile", "e1", gid, runner="mac-b")
        self.assertEqual(self.effect(env2, "finish", "e1", gid, runner="mac-b", lease=rec["lease"], state="unknown")
                         ["state"], "ok")
        self.assertEqual(self.effect(env2, "prepare", "e1", gid, runner="mac-b")["receipt_state"], "unknown")
        self.assertEqual(self.effect(env2, "claim", "e1", gid, runner="mac-b")["code"],
                         "receipt_reconciliation_required")

    def test_dup2_no_stale_effect(self):
        job, env = self.claimed()
        gid = self.effect_grant(job, "e1", "e2", "e3")
        # The receipt's lease never outlives the item's: with 10 s of lease left, the receipt gets at most 10 s.
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() + interval '10 seconds' "
                 "WHERE job_id=%s", (job,))
        self.effect(env, "prepare", "e1", gid)
        claim = self.effect(env, "claim", "e1", gid)
        self.assertLessEqual(parse_at(claim["lease"]["lease_until"]), self.row(job)["lease_until"])
        self.assertTrue(self.store.renew(env["request_id"], 1, protocol=2, runner_id="mac-a"))
        # A dispatch on an expired receipt lease is refused, and so is the send: the sink sees nothing committed.
        self.sql("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 second'")
        stale = self.effect(env, "dispatch", "e1", gid, lease=claim["lease"])
        self.assertEqual(stale["code"], "receipt_lease_stale")
        self.assertEqual(self.receipt(env, "e1")["state"], "prepared")
        late = parse_at(claim["lease"]["lease_until"]) + timedelta(seconds=1)       # a send after the lease ran out
        self.assertEqual(self.sink.post(stale["operation_id"], claim["lease"]["lease_until"], late), "lease_expired")
        # A woken child's stale send after a dispatch (T6a's e2'): the target refuses it past the receipt's lease.
        answer, lease = self.send(env, "e2", gid)
        self.assertEqual(answer, "committed")
        self.assertEqual(self.effect(env, "finish", "e2", gid, lease=lease, state="verified",
                                     evidence={"kind": "send", "outcome": "committed"})["state"], "ok")
        self.assertEqual(self.sink.post(effect_operation_id(env["request_id"], "e2"), lease["lease_until"],
                                        self.db_now() + timedelta(seconds=31)), "duplicate")
        # A stale generation and a lapsed item are refused before any receipt is touched.
        self.effect(env, "prepare", "e3", gid)
        claim = self.effect(env, "claim", "e3", gid)
        self.lapse(job)
        self.assertEqual(self.effect(env, "dispatch", "e3", gid, lease=claim["lease"])["code"], "lease_lapsed")
        self.reconcile(env, cp=None)                                               # resume: generation 2
        self.assertEqual(self.effect(env, "dispatch", "e3", gid, lease=claim["lease"])["code"], "stale_generation")
        self.assertEqual(self.receipt(env, "e3")["state"], "prepared")
        self.assertEqual(self.effect({**env, "generation": 2}, "claim", "e9", gid)["code"], "operation_not_of_item")
        refused = {e["body"]["code"] for e in self.events("effect.refused", env["request_id"])}
        self.assertTrue({"receipt_lease_stale", "lease_lapsed", "stale_generation"} <= refused)

    def test_dup5_a_replayed_finish_and_prepare_apply_once(self):
        job, env = self.claimed()
        gid = self.effect_grant(job, "e1")
        self.effect(env, "prepare", "e1", gid)
        self.assertEqual(self.effect(env, "prepare", "e1", gid)["receipt_state"], "prepared")
        claim = self.effect(env, "claim", "e1", gid)
        self.effect(env, "dispatch", "e1", gid, lease=claim["lease"])
        done = {"lease": claim["lease"], "state": "verified", "evidence": {"kind": "send", "outcome": "committed"}}
        self.assertEqual(self.effect(env, "finish", "e1", gid, **done)["state"], "ok")
        self.assertEqual(self.effect(env, "finish", "e1", gid, **done)["state"], "already_applied")
        self.assertEqual(len(self.events("effect.evidence")), 1)
        self.assertEqual(len(self.events("effect.prepared")), 1)
        self.assertEqual(self.sql("SELECT consumed_operations FROM guild.mc_action_grants WHERE grant_id=%s",
                                  (gid,))[0]["consumed_operations"], 1)
        # A different outcome under a spent lease is refused, never applied.
        self.assertEqual(self.effect(env, "finish", "e1", gid, lease=claim["lease"], state="failed",
                                     evidence={"kind": "send", "outcome": "rejected"})["state"], "refused")

    def test_only_the_owner_reaches_an_items_effects(self):
        job, env = self.claimed()
        gid = self.effect_grant(job, "e1")
        self.assertEqual(self.effect(env, "prepare", "e1", gid, runner="mac-b")["code"], "not_owner")
        other = self.effect_grant("job-elsewhere", "e1")                  # a grant for other work covers nothing here
        self.assertEqual(self.effect(env, "prepare", "e1", other)["code"], "grant_context_mismatch")
        self.store.stop(job)
        self.assertEqual(self.effect(env, "prepare", "e1", gid)["code"], "cancel_requested")

    # ── the sweeper ──
    def test_t5c_lapse_with_cause_and_grace_then_owner_unreachable(self):
        job, env = self.claimed()
        self.progress(env, checkpoint=CP1)
        self.seen("mac-a", 120)
        self.lapse(job, 60)
        self.assertEqual(self.store.sweep()["lapsed"], 1)
        row = self.row(job)
        self.assertEqual((row["state"], row["lapse_cause"], row["runner_id"], row["generation"]),
                         ("lapsed", "owner_contact_lost", "mac-a", 1))
        self.assertEqual(row["grace_deadline"], row["lapsed_since"] + timedelta(seconds=900))
        self.assertEqual(row["lapsed_since"], row["lease_until"])
        self.assertFalse(self.store.renew(env["request_id"], 1, protocol=2, runner_id="mac-a"))
        self.assertEqual(self.progress(env)["code"], "lease_lapsed")
        self.assertEqual(self.store.sweep().get("lapsed"), None)                   # written once
        # Past grace: interrupted, checkpoint kept, reservation kept until the children are confirmed stopped.
        self.sql("UPDATE guild.mc_delegations SET grace_deadline = clock_timestamp() - interval '1 second'")
        self.assertEqual(self.store.sweep()["interrupted"], 1)
        row = self.row(job)
        self.assertEqual((row["state"], row["result_detail"]["code"], row["checkpoint"]["name"],
                          row["reservation"]["state"]), ("interrupted", "owner_unreachable", "s1", "kept"))
        # The owner returns: stop; its late result is refused; its stopped child releases the reservation.
        self.beat("mac-a")
        a = self.reconcile(env)
        self.assertEqual((a["answer"], a["code"], a["state"]), ("stop", "owner_unreachable", "interrupted"))
        self.assertEqual(self.row(job)["reservation"]["state"], "unreported")
        self.assertEqual(self.result(env)["code"], "item_ended")
        self.assertEqual(self.sql("SELECT reserved FROM guild.mc_delegation_reservations")[0]["reserved"], AMOUNT)

    def test_the_lapse_cause_and_a_stopped_owner_pauses(self):
        job, env = self.claimed()
        self.lapse(job, 60)                                     # the host is still in contact, but didn't renew
        self.store.sweep()
        self.assertEqual((self.row(job)["state"], self.row(job)["lapse_cause"]), ("lapsed", "lease_not_renewed"))
        job2, env2 = self.claimed(runner="mac-b", job="job-p")
        self.beat("mac-b", announced="stopped")                 # G4 done: stopped at a checkpoint (E2, E14)
        row = self.row(job2)                                    # paused at once, not at the next sweep
        self.assertEqual((row["state"], row["lapse_cause"], row["runner_id"]), ("paused", "owner_stopped", "mac-b"))
        self.assertLessEqual(row["lease_until"], row["now"])                  # it holds no lease while paused
        self.assertEqual(row["grace_deadline"], row["lapsed_since"] + timedelta(seconds=900))
        self.assertFalse(self.store.renew(env2["request_id"], 1, protocol=2, runner_id="mac-b"))
        self.store.sweep()
        self.assertEqual(self.row(job2)["state"], "paused")
        self.beat("mac-c")
        self.assertIsNone(self.store.claim("mac-c", CAPS, protocol=2))       # nobody else takes it
        self.beat("mac-b")                                                   # back online: reconcile resumes it
        a = self.reconcile(env2, runner="mac-b", cp=None)
        self.assertEqual((a["answer"], a["generation"]), ("resume", 2))

    def test_grace_is_extended_by_the_authoritys_own_outage(self):
        job, env = self.claimed()
        self.lapse(job, 60)
        self.store.sweep()
        before = self.row(job)["grace_deadline"]
        job2, env2 = self.claimed(runner="mac-b", job="job-w")
        # The whole Mac slept 20 minutes: the sweeper's heartbeat stopped, and W's lease ran out during the sleep.
        self.age_sweeper(1200)
        self.sql("UPDATE guild.mc_delegation_events SET recorded_at = recorded_at - interval '1200 seconds'")
        self.sql("UPDATE guild.mc_delegation_runners SET seen_at = seen_at - interval '1200 seconds' "
                 "WHERE runner_id NOT LIKE 'sweeper/%%'")
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '600 seconds' "
                 "WHERE job_id='job-w'")
        out = self.store.sweep()
        self.assertIsNotNone(out["suspended"])
        self.assertAlmostEqual(out["suspended"]["outage_s"], 1200, delta=5)
        sus = self.events("authority.suspended")
        self.assertEqual(len(sus), 1)
        self.assertEqual(set(sus[0]["body"]) >= {"from", "to"}, True)
        after = self.row(job)["grace_deadline"]
        self.assertAlmostEqual((after - before).total_seconds(), out["suspended"]["outage_s"], delta=0.01)
        self.assertEqual(len(self.events("item.grace_extended", env["request_id"])), 1)
        # A lease that ran out inside the outage lapsed authority_down, with its grace from the restart (X2a).
        row = self.row("job-w")
        self.assertEqual((row["state"], row["lapse_cause"]), ("lapsed", "authority_down"))
        self.assertEqual(row["grace_deadline"], parse_at(sus[0]["body"]["to"]) + timedelta(seconds=900))
        self.assertIsNone(self.store.sweep()["suspended"])                       # measured once

    def test_a_dead_sweeper_records_no_outage_while_the_authority_served(self):
        self.store.sweep()
        self.claimed()
        self.age_sweeper(600)
        # Contact and renewals kept arriving every 20 s while the sweeper was dead.
        self.sql("""INSERT INTO guild.mc_delegation_events (event_id, schema, kind, principal_id, environment, source,
            at, recorded_at, body)
            SELECT gen_random_uuid(), 'minimoi.runtime.event.v1', 'lease.renewed', 'robert', 'dev', 'authority',
                   clock_timestamp() - s * interval '1 second', clock_timestamp() - s * interval '1 second', '{}'
            FROM generate_series(20, 600, 20) AS s""")
        self.assertIsNone(self.store.sweep()["suspended"])
        self.assertEqual(self.events("authority.suspended"), [])

    def test_x7_an_owner_reconciling_before_the_sweeper_after_a_whole_runtime_sleep_resumes(self):
        job, env = self.claimed()
        self.progress(env, checkpoint=CP1)
        self.store.sweep()
        # 20 minutes asleep (test grace 900 s): the lease ran out long ago, and the sweeper has not run since.
        self.age_sweeper(1200)
        self.lapse(job, 1140)
        self.assertFalse(self.store.sweeper_status()["fresh"])
        a = self.reconcile(env)
        self.assertEqual((a["answer"], a["generation"]), ("resume", 2))       # never lost to an unmeasured outage
        self.assertIsNotNone(self.store.sweep()["suspended"])

    def test_with_the_sweeper_current_a_past_grace_is_judged_at_reconcile(self):
        job2, env2 = self.claimed(runner="mac-b", job="job-x")
        self.store.sweep()
        self.lapse("job-x", 1000)
        self.assertTrue(self.store.sweeper_status()["fresh"])
        a = self.reconcile(env2, runner="mac-b", cp=None)
        self.assertEqual((a["answer"], a["code"]), ("stop", "owner_unreachable"))
        self.assertEqual([e["kind"] for e in self.events(item=env2["request_id"])
                          if e["kind"] in ("item.lapsed", "item.interrupted")], ["item.lapsed", "item.interrupted"])

    def test_e11_a_dead_sweeper_leaves_no_item_looking_live(self):
        job, env = self.claimed()
        self.assertIsNone(self.store.sweeper_status())                    # it never ran
        self.lapse(job, 30)
        row = self.row(job)
        self.assertEqual(row["state"], "claimed")                         # stored, but every answer reads the clock:
        self.assertLess(row["lease_until"], row["now"])
        self.assertFalse(self.store.renew(env["request_id"], 1, protocol=2, runner_id="mac-a"))
        self.assertEqual(self.progress(env)["code"], "lease_lapsed")
        self.assertEqual(self.result(env)["code"], "lease_lapsed")
        self.item()
        self.assertIsNone(self.store.claim("mac-a", CAPS, protocol=2))     # withheld until it reconciles
        self.assertEqual(self.reconcile(env, cp=None, child="frozen")["answer"], "resume")   # never continue
        # The sweeper writes lapse transitions only, never running: an item whose lease runs is left alone.
        seq = max(e["seq"] for e in self.events())
        for _ in range(2):
            self.store.sweep()
        row = self.row(job)
        self.assertEqual((row["state"], row["generation"]), ("claimed", 2))
        self.assertEqual([e for e in self.events() if e["seq"] > seq], [])
        self.lapse(job)
        self.store.sweep()
        swept = {e["kind"] for e in self.events() if e["seq"] > seq}
        self.assertEqual(swept, {"item.lapsed"})

    # ── the owner's acts ──
    def test_t10_w_rev_revocation_end_to_end(self):
        job, env = self.claimed()
        gid = self.effect_grant(job, "e1")
        self.effect(env, "prepare", "e1", gid)
        claim = self.effect(env, "claim", "e1", gid)                           # a dispatch is pending
        queued = self.item("job-q")
        self.sql("UPDATE guild.mc_delegations SET grant_id=%s WHERE job_id='job-q'", (self.grant_of[job],))
        out = self.store.revoke(self.grant_of[job], by="robert", note="W-REV")
        self.assertEqual(sorted(out["items"]), sorted([env["request_id"], str(self.row(queued)["request_id"])]))
        row = self.row(job)
        self.assertEqual((row["state"], row["result_detail"]["code"], row["reservation"]["state"]),
                         ("cancelled", "grant_revoked", "kept"))
        self.assertEqual(self.row(queued)["state"], "cancelled")
        self.assertFalse(self.store.renew(env["request_id"], 1, protocol=2, runner_id="mac-a"))
        self.assertEqual(self.effect(env, "dispatch", "e1", gid, lease=claim["lease"])["code"], "item_ended")
        self.assertEqual(self.receipt(env, "e1")["state"], "prepared")
        self.assertEqual(self.result(env)["code"], "item_ended")                  # a late result is refused
        a = self.reconcile(env, cp=None)
        self.assertEqual((a["answer"], a["code"], a["state"]), ("stop", "grant_revoked", "cancelled"))
        self.assertEqual(self.row(job)["reservation"]["state"], "unreported")
        self.assertEqual(len(self.events("grant.revoked")), 1)
        # Revoked straight through GrantStore, not the store: a dispatch is still refused at once (R4.3-e).
        job2, env2 = self.claimed(runner="mac-b", job="job-r2")
        gid2 = self.effect_grant(job2, "e1")
        self.effect(env2, "prepare", "e1", gid2, runner="mac-b")
        claim2 = self.effect(env2, "claim", "e1", gid2, runner="mac-b")
        with self.connect() as conn:
            GRANTS.revoke(conn, "robert", "dev", self.grant_of[job2])
        self.assertEqual(self.effect(env2, "dispatch", "e1", gid2, runner="mac-b", lease=claim2["lease"])["code"],
                         "grant_revoked")
        self.lapse(job2)
        self.store.sweep()
        self.assertEqual((self.row(job2)["state"], self.row(job2)["result_detail"]["code"]),
                         ("cancelled", "grant_revoked"))

    def test_dup3_two_hosts_and_a_reassignment(self):
        across = Authority.parse({**TEST_VALUES, "reassign_across_hosts": True})
        job, env = self.claimed(runner="mac-a", host="mac-test-1")
        gid = self.effect_grant(job, "e1", "e2")
        self.progress(env, checkpoint=CP1)
        self.assertEqual(self.send(env, "e1", gid)[0], "committed")
        rec = self.effect(env, "reconcile", "e1", gid)
        self.assertEqual(rec["code"], "receipt_busy")                              # its execute lease still runs
        self.sql("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 second'")
        rec = self.effect(env, "reconcile", "e1", gid)
        self.effect(env, "finish", "e1", gid, lease=rec["lease"], state="verified",
                    evidence={"kind": "readback", "outcome": "committed"})
        self.beat("stg-b", "staging-1")
        with self.assertRaisesRegex(AuthorityRefused, "lease_current"):             # held: nobody may act
            self.store.reassign(env["request_id"], "stg-b", by="robert", note="move it")
        self.lapse(job, 0)                                    # its lease runs out now (never before it was claimed)
        self.store.sweep()
        # A Mac-only build refuses cross-host takeover outright (R2.4-c).
        with self.assertRaisesRegex(AuthorityRefused, "cross_host_refused"):
            self.store.reassign(env["request_id"], "stg-b", by="robert", note="move it")
        store = self.make_store(across)
        out = store.reassign(env["request_id"], "stg-b", by="robert", note="T5d: move W-M to S")
        self.assertEqual((out["generation"], out["runner_id"]), (2, "stg-b"))
        row = self.row(job)
        self.assertEqual((row["state"], row["generation"], row["runner_id"], row["checkpoint"]),
                         ("claimed", 2, "stg-b", None))
        # The old owner: reconcile answers stop; its stale writes and effects are refused.
        self.assertEqual(self.reconcile(env, store=store)["code"], "reassigned")
        self.assertEqual(self.progress(env)["code"], "stale_generation")
        self.assertEqual(self.result(env)["code"], "stale_generation")
        self.assertEqual(self.effect(env, "prepare", "e2", gid)["code"], "stale_generation")
        # The new owner's claim hands it over at generation 2; e1 is found verified, e2 goes once.
        got = store.claim("stg-b", CAPS, protocol=2)
        self.assertEqual((got["envelope"]["request_id"], got["envelope"]["generation"]), (env["request_id"], 2))
        g2 = got["envelope"]
        self.assertEqual(self.effect(g2, "prepare", "e1", gid, runner="stg-b")["receipt_state"], "verified")
        self.assertEqual(self.send(g2, "e2", gid, runner="stg-b")[0], "committed")
        self.assertEqual(self.result(g2, runner="stg-b")["state"], "accepted")
        # From the audit: the hosts' lease intervals on the item never overlap.
        spans = lease_spans(self.events(item=env["request_id"]))
        self.assertEqual(set(spans), {(1, "mac-test-1"), (2, "staging-1")})
        (a0, a1), (b0, b1) = spans[(1, "mac-test-1")], spans[(2, "staging-1")]
        self.assertLessEqual(a1, b0)                                       # A's lease ended before B's began
        self.assertLess(a0, a1)
        self.assertLess(b0, b1)
        self.assertEqual(self.sink.committed(effect_operation_id(env["request_id"], "e1")), 1)

    def test_the_kept_reservation_of_a_result_is_released_when_its_children_stop(self):
        job, env = self.claimed()
        self.assertEqual(self.result(env, stopped=False)["state"], "accepted")
        self.assertEqual(self.row(job)["reservation"]["state"], "kept")
        day = self.sql("SELECT reserved, observed FROM guild.mc_delegation_reservations")[0]
        self.assertEqual((day["reserved"], day["observed"]), (AMOUNT, 0))
        self.assertEqual(self.reconcile(env, child="frozen", cp=None)["answer"], "stop")
        self.assertEqual(self.row(job)["reservation"]["state"], "kept")              # not yet: still frozen
        self.reconcile(env, cp=None)
        row = self.row(job)
        self.assertEqual((row["reservation"]["state"], "released_at" in row["facts"]), ("settled", True))
        day = self.sql("SELECT reserved, observed FROM guild.mc_delegation_reservations")[0]
        self.assertEqual((day["reserved"], day["observed"]), (0, USED))
        self.reconcile(env, cp=None)                                                  # once
        self.assertEqual(self.sql("SELECT observed FROM guild.mc_delegation_reservations")[0]["observed"], USED)

    def test_the_relay_reap_moves_to_the_sweeper(self):
        from core.mc_delegation_dispatcher.relay import DelegationRelay
        no_sweeper = {k: v for k, v in TEST_VALUES.items() if k != "sweeper"}
        for authority, reaps in ((AUTH, False), (Authority.parse(no_sweeper), True)):
            self.setUp()
            store = self.make_store(authority)
            relay = DelegationRelay(store, ref_for=lambda r: "a" * 40)
            self.assertEqual(relay.start({"agent": "claude-code", "job_id": "j-v1", "task": "t"})[0], "accepted")
            store.heartbeat("mac-prod", "online", {"cost_enforced_actors": ["claude-code"], "scopes": ["research"],
                                                   "repositories": ["personal-ai-agents"]})
            self.assertIsNotNone(store.claim("mac-prod", {"cost_enforced_actors": ["claude-code"],
                                                          "scopes": ["research"],
                                                          "repositories": ["personal-ai-agents"]}))
            self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '300 seconds'")
            state = relay.status("j-v1")[1]
            self.assertEqual((state["state"], state["error_class"]),
                             ("failed", "interrupted") if reaps else ("running", None), authority.sweeper)
            if not reaps:
                self.assertEqual(store.sweep()["runner_lost"], 1)                # the sweeper reaps it instead
                self.assertEqual(relay.status("j-v1")[1]["error_class"], "interrupted")
            self.assertEqual(self.row("j-v1")["result_detail"]["code"], "runner_lost")


if __name__ == "__main__":
    unittest.main()
