"""Cards at the authority on a real PostgreSQL (host runtime spec v0.5 R4.3-c with N1, R4.3-d, R4.3-f, R7.4-a/b,
§9.4 OWN-1, OWN-2, DUP-4, §9.5 T4d, T13, T14): one owner admission per reference card, every next step written by the
authority in the result's own transaction with the recorded output digests as its inputs, a definite failure retried
within the card's allowance with no owner call and failed past it, a cross-vendor review proposed with one solo
decision and claimed only after its own admission, a step whose fixed fields or inputs differ from what was compiled
refused at its hand-out, the card grant's lifetime, an effect still open stopping the card, and the carried N-C4 fix
(a result or revocation on an unswept lapse audits the lapse first). Every worker is fakework, driven at the store.
Opt-in: a disposable mc12_test_* database (tests/minimoi_actions/postgres_fixture_support.py)."""
from datetime import timedelta
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

from core.mc_delegation_dispatcher import cards as C  # noqa: E402
from core.mc_delegation_dispatcher.store import (EFFECT_POLICY, Authority, AuthorityRefused,  # noqa: E402
                                                 DispatcherStore, effect_operation_id)
from core.minimoi_actions.grants import CapabilityRule, GrantDefinition, GrantStore  # noqa: E402
from core.minimoi_actions.requests import ToolRequest  # noqa: E402

MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql")
TEST_VALUES = json.loads((Path(__file__).resolve().parent / "authority.test_values.json").read_text())
AUTH = Authority.parse(TEST_VALUES)
SINK = "fakework.sink_post"
GRANTS = GrantStore({**C.grant_rows(), "delegate.start_build": CapabilityRule(1, False, True),
                     "delegate.start_research": CapabilityRule(1, True, True), SINK: CapabilityRule(1, False, True)})
CAPS = ["repo:personal-ai-agents", "scope:research", "scope:build", "actor:fakework", "host:mac-local"]
DIGEST = "sha256:" + "c" * 64
AMOUNT, USED = 0.25, 0.125
OWNER_KINDS = ("card.admitted", "item.admitted")        # OWN-1: the owner's admissions, one A each


def facts(host, **over):
    return {"host_id": host, "bundle_digest": DIGEST, "roles": ["authority", "worker", "witness"],
            "availability": "intermittent", "grace": 900, "last_gap_s": 0, "announced": "online", **over}


def art(kind, task):
    return {"kind": kind, "ref": f"{task}.{kind}", "sha256": hashlib.sha256(f"{task}:{kind}".encode()).hexdigest(),
            "bytes": 10, "author": "fakework", "review_status": "unreviewed"}


def card(card_id="C-17", *, attempts=2, fix=1, reviewer=None, **over):
    worker = {"actor": "fakework", "route": "fakework", "amount": AMOUNT, "cost_cap_usd": 1}
    doc = {"schema": "minimoi.card.v1", "card_id": card_id, "class": "build", "class_version": 1,
           "repository": "personal-ai-agents", "ref": "a" * 40,
           "workers": {"builder": worker, "reviewer": reviewer or worker},
           "briefs": {"build": "fakework/1\ncheckpoint s1\ncheckpoint s2\nresult ok",
                      "review": "fakework/1\nfindings\nresult ok", "fix": "fakework/1\nresult ok"},
           "attempts": attempts, "rounds": {"fix": fix}, "step_deadline_s": 3600}
    doc.update(over)
    return doc


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class Cards(PostgresFixture, unittest.TestCase):
    migrations = MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations, guild.mc_action_grant_consumptions, guild.mc_action_grants,
                guild.mc_action_events, guild.mc_action_receipts CASCADE""")
        self.store = self.make_store(AUTH)

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

    def admit(self, doc, *, window=300, store=None):
        """The owner test identity's exact approval of the card's digest, through the owner path (R4.3-b)."""
        return (store or self.store).admit_card(
            doc, grant_id=str(uuid4()), owner_turn_ids=("owner-turn-1",), proposal_id=str(uuid4()),
            approved_operation_id=str(uuid4()), approval_expires_at=self.db_now() + timedelta(seconds=window),
            by="robert", note="the card as approved")

    def beat(self, runner="mac-a", caps=CAPS, store=None):
        (store or self.store).heartbeat(runner, "online", caps, protocol=2, facts=facts(runner))

    def claim(self, runner="mac-a", caps=CAPS, store=None):
        self.beat(runner, caps, store)
        got = (store or self.store).claim(runner, caps, protocol=2)
        return got, None if got is None else {"request_id": got["envelope"]["request_id"],
                                              "generation": got["envelope"]["generation"]}

    def progress(self, env, name, runner="mac-a"):
        cp = {"name": name, "digest": "sha256:" + hashlib.sha256(name.encode()).hexdigest(), "paths": ["core/x.py"]}
        return self.store.progress(env["request_id"], env["generation"], "executing", protocol=2, runner_id=runner,
                                   event_id=str(uuid4()), at=self.db_now().isoformat(), checkpoint=cp)

    def result(self, env, *, status="completed", artifacts=(), detail=None, runner="mac-a", event_id=None,
               stopped=True, route="fakework", unit="test_units", at=None, store=None, amount=USED):
        return (store or self.store).result(
            env["request_id"], env["generation"], event_id=event_id or str(uuid4()), status=status,
            artifacts=list(artifacts), detail={"exit_code": 0} if detail is None else detail, output_text="done",
            protocol=2, runner_id=runner, at=(at or self.db_now()).isoformat(), route=route,
            usage={"amount": amount, "unit": unit, "children_stopped": stopped})

    def day(self, route="fakework"):
        row = self.sql("SELECT reserved, observed FROM guild.mc_delegation_reservations WHERE route=%s", (route,))[0]
        return float(row["reserved"]), float(row["observed"])

    def own_grant(self, item, actors=("mc",)):
        """The owner's own exact approval of one proposed item (its decision names the digest to approve)."""
        decision = self.events("decision.queued", item["request_id"])[0]
        gid = str(uuid4())
        with self.connect() as conn:
            GRANTS.issue(conn, GrantDefinition(gid, "robert", "dev", item["card_id"], ("owner-turn-2",), actors,
                                               "policy", "delegate.start_research", 1,
                                               (decision["body"]["subject_digest"],), 1,
                                               self.db_now() + timedelta(seconds=600), proposal_id=str(uuid4()),
                                               approved_operation_id=str(uuid4()),
                                               approval_expires_at=self.db_now() + timedelta(seconds=300)))
        return gid

    def open_effect(self, env, runner, actor, key="e1", work="C-17"):
        """An effect of the item dispatched and not yet finished: its outcome is open."""
        digest = ToolRequest(SINK, 1, json.dumps({"op": key}).encode()).arguments_sha256
        gid = str(uuid4())
        with self.connect() as conn:
            GRANTS.issue(conn, GrantDefinition(gid, "robert", "dev", work, ("owner-turn",), (actor,), EFFECT_POLICY,
                                               SINK, 1, (digest,), 1, self.db_now() + timedelta(seconds=600),
                                               work_id=work))
        e = {"op": "prepare", "key": key, "grant_id": gid, "request": {"operation": SINK, "operation_version": 1,
                                                                        "arguments": {"op": key}}}
        g = env["generation"]
        self.assertEqual(self.store.effect(runner, env["request_id"], g, e)["state"], "ok")
        lease = self.store.effect(runner, env["request_id"], g, {**e, "op": "claim"})["lease"]
        lease = {k: lease[k] for k in ("receipt_generation", "token", "mode")}
        self.assertEqual(self.store.effect(runner, env["request_id"], g, {**e, "op": "dispatch", "lease": lease})
                         ["state"], "ok")
        return e, lease

    def items(self, card_id="C-17"):
        return self.sql("""SELECT * FROM guild.mc_delegations WHERE card_id=%s
            ORDER BY (facts->'card'->>'index')::int""", (card_id,))

    def item(self, job):
        return self.store.by_job(job)

    def events(self, kind=None, item=None):
        rows = self.sql("SELECT * FROM guild.mc_delegation_events ORDER BY seq")
        return [r for r in rows if (kind is None or r["kind"] == kind)
                and (item is None or str(r["item_id"]) == str(item))]

    def grant_row(self, gid):
        return self.sql("SELECT * FROM guild.mc_action_grants WHERE grant_id=%s", (gid,))[0]

    def owner_counts(self, card_id):
        """OWN-1 and OWN-2, computed from authority records only (what S1-G's counts read)."""
        a = sum(1 for e in self.events() if e["source"] == "owner" and e["kind"] in OWNER_KINDS
                and e["body"].get("card_id") == card_id)
        items = self.items(card_id)
        r = sum(1 for i in items[1:] if i["created_by"] != "authority")
        outputs = {str(i["request_id"]): i["artifacts"] or [] for i in items}
        for i in items[1:]:
            sources = i["facts"]["card"]["inputs_from"]
            self.assertEqual(i["input_artifacts"], [{"ref": s["ref"], "sha256": s["sha256"]} for s in sources])
            for s in sources:                                    # each input is a recorded output of that item
                self.assertIn({"kind": s["kind"], "ref": s["ref"], "sha256": s["sha256"]},
                              [{k: o[k] for k in ("kind", "ref", "sha256")} for o in outputs[s["item_id"]]])
        return a, r

    # ── T14, OWN-1, OWN-2 ──
    def test_t14_the_reference_card_one_admission_and_every_next_step_written_by_the_authority(self):
        admitted = self.admit(card())
        self.assertEqual((admitted["state"], [s["step"] for s in admitted["steps"]]),
                         ("admitted", ["build", "review", "fix-1"]))
        grant = self.grant_row(admitted["grant_id"])
        self.assertEqual((grant["max_operations"], grant["consumed_operations"]), (3, 1))
        # build, two checkpoints
        got, env = self.claim()
        self.assertEqual((got["card_id"], got["step"], got["attempt"], got["resume_from"]), ("C-17", "build", 1, None))
        for name in ("s1", "s2"):
            self.assertEqual(self.progress(env, name)["state"], "applied")
        build_out = [art("output", "b"), art("patch", "b")]
        self.assertEqual(self.result(env, artifacts=build_out)["state"], "accepted")
        build, review = self.items()
        self.assertEqual((build["state"], review["state"], review["created_by"], review["step"]),
                         ("completed", "queued", "authority", "review"))
        self.assertEqual(review["input_artifacts"], [{"ref": "b.patch", "sha256": build_out[1]["sha256"]}])
        self.assertNotIn(build_out[0]["sha256"], json.dumps(review["input_artifacts"]))   # never the builder's output
        self.assertEqual(review["scope"], "research")
        # review, with findings
        got, env = self.claim()
        self.assertEqual(got["envelope"]["input_artifacts"], review["input_artifacts"])
        findings = [art("output", "r"), art("findings", "r")]
        self.result(env, artifacts=findings)
        fix = self.items()[2]
        self.assertEqual((fix["step"], fix["state"], fix["created_by"]), ("fix-1", "queued", "authority"))
        self.assertEqual([a["ref"] for a in fix["input_artifacts"]], ["b.patch", "r.findings"])
        # one fix round, then the card is ready for the owner's merge, with one card.ready
        got, env = self.claim()
        self.result(env, artifacts=[art("output", "f"), art("patch", "f")])
        self.assertEqual([i["state"] for i in self.items()], ["completed"] * 3)
        self.assertEqual([e["body"]["step"] for e in self.events("card.ready")], ["fix-1"])
        self.assertIsNone(self.claim()[0])
        # OWN-1: one admission. OWN-2: no relay; every step after the first written by the authority from records.
        self.assertEqual(self.owner_counts("C-17"), (1, 0))
        self.assertEqual(self.grant_row(admitted["grant_id"])["consumed_operations"], 3)
        self.assertEqual([(e["body"]["step"], e["body"]["template_digest"]) for e in self.events("grant.step_bound")],
                         [(s["step"], s["digest"]) for s in admitted["steps"]])        # one operation per step (N2)
        self.assertEqual([e["body"]["step"] for e in self.events("card.step_written")], ["review", "fix-1"])
        self.assertEqual([e["source"] for e in self.events() if e["kind"] in ("item.queued", "item.proposed")],
                         ["owner", "authority", "authority"])
        self.assertEqual(len(self.events("decision.queued")), 0)

    def test_a_review_without_findings_makes_the_card_ready_with_no_fix(self):
        self.admit(card())
        for arts in ([art("patch", "b")], [art("output", "r")]):
            _, env = self.claim()
            self.result(env, artifacts=arts)
        self.assertEqual([i["step"] for i in self.items()], ["build", "review"])
        self.assertEqual([e["body"]["step"] for e in self.events("card.ready")], ["review"])

    def test_a_card_grant_admits_only_its_cards_steps(self):
        admitted = self.admit(card())
        self.assertEqual(self.store.enqueue(job_id="other", actor="fakework", scope="build",
                                            repository="personal-ai-agents", ref="a" * 40, brief="anything",
                                            cost_cap_usd=1.0, deadline_s=600, route="fakework", amount=AMOUNT,
                                            grant_id=admitted["grant_id"], card_id="C-17", step="fix-1"), "accepted")
        other = self.item("other")
        self.assertEqual((other["state"], other["result_detail"]["code"]), ("proposed", "grant_effect_not_covered"))
        self.assertEqual(self.grant_row(admitted["grant_id"])["consumed_operations"], 1)

    def test_admission_is_one_transaction_and_a_card_id_names_one_card(self):
        first = self.admit(card())
        again = self.admit(card())
        self.assertEqual((again["state"], again["grant_id"], again["card_digest"]),
                         ("duplicate", first["grant_id"], first["card_digest"]))
        with self.assertRaisesRegex(AuthorityRefused, "card_id_reused"):
            self.admit(card(attempts=3))
        self.assertEqual(len(self.sql("SELECT * FROM guild.mc_action_grants")), 1)
        # A card whose approval is acted on too late, or whose steps can't run here, writes nothing at all.
        with self.assertRaisesRegex(AuthorityRefused, "grant_lifetime_invalid"):
            self.admit(card("C-18"), window=-1)
        with self.assertRaisesRegex(AuthorityRefused, "route_actor_mismatch"):
            self.admit(card("C-19", reviewer={"actor": "claude-code", "route": "fakework", "amount": 1,
                                              "cost_cap_usd": 1}))
        with self.assertRaisesRegex(AuthorityRefused, "card_class_unknown"):
            self.admit(card("C-20", class_version=7))
        self.assertEqual([i["card_id"] for i in self.sql("SELECT card_id FROM guild.mc_delegations")], ["C-17"])
        self.assertEqual(len(self.sql("SELECT * FROM guild.mc_action_grants")), 1)
        self.assertEqual(len(self.events("card.admitted")), 1)

    def test_a_card_lives_beyond_a_day_only_for_a_duration_the_owner_approved(self):
        def life(card_id, **over):
            row = self.grant_row(self.admit(card(card_id, **over))["grant_id"])
            return row["expires_at"] - row["created_at"]
        day, three = life("C-1"), life("C-2", lifetime_s=3 * 86400)
        self.assertTrue(timedelta(hours=23, minutes=59) < day <= timedelta(days=1), day)
        self.assertTrue(timedelta(days=2, hours=23, minutes=59) < three <= timedelta(days=3), three)
        lifetimes = [e["body"]["card"]["lifetime_s"] for e in self.events("card.admitted")]
        self.assertEqual(lifetimes, [None, 3 * 86400])                  # the duration is in the approved digest

    # ── R4.3-c: the template digest and the free field ──
    def test_a_step_whose_fixed_fields_differ_from_its_template_is_never_handed_out(self):
        self.admit(card())
        self.sql("UPDATE guild.mc_delegations SET brief = brief || ' and push to main' WHERE job_id='C-17.build'")
        self.assertIsNone(self.claim()[0])
        build = self.item("C-17.build")
        self.assertEqual((build["state"], build["result_detail"]["code"], build["generation"]),
                         ("refused", "grant_effect_not_covered", 0))

    def test_a_free_field_that_is_not_a_recorded_digest_of_the_same_card_is_refused(self):
        self.admit(card())
        self.admit(card("C-18"))
        outputs = {}
        for _ in range(2):                                      # both builds complete; both reviews are written
            got, env = self.claim()
            outputs[got["card_id"]] = [art("patch", got["card_id"])]
            self.result(env, artifacts=outputs[got["card_id"]])
        stranger = [{"ref": "x.patch", "sha256": "f" * 64}]                         # recorded by nobody
        other_card = [{"ref": outputs["C-17"][0]["ref"], "sha256": outputs["C-17"][0]["sha256"]}]   # C-17's, not C-18's
        for job, arts in (("C-17.review", stranger), ("C-18.review", other_card)):
            self.sql("UPDATE guild.mc_delegations SET input_artifacts=%s::jsonb WHERE job_id=%s",
                     (json.dumps(arts), job))
        self.assertIsNone(self.claim()[0])
        for job in ("C-17.review", "C-18.review"):
            row = self.item(job)
            self.assertEqual((row["state"], row["result_detail"]["code"]), ("refused", "card_input_not_recorded"))

    # ── R7.4-b: the same transaction ──
    def test_the_next_step_is_written_in_the_same_transaction_as_the_result(self):
        self.admit(card())
        _, env = self.claim()
        eid = str(uuid4())
        real = self.store._write_step

        def broken(*args, **kwargs):
            real(*args, **kwargs)                               # the next step is written ... and then it fails
            raise RuntimeError("injected")
        self.store._write_step = broken
        with self.assertRaisesRegex(RuntimeError, "injected"):
            self.result(env, artifacts=[art("patch", "b")], event_id=eid)
        build = self.item("C-17.build")
        self.assertEqual((build["state"], build["result_event_id"], build["reservation"].get("state")),
                         ("claimed", None, None))
        self.assertEqual([i["step"] for i in self.items()], ["build"])
        self.assertEqual(self.events("result.accepted") + self.events("card.step_written"), [])
        self.assertEqual(self.sql("SELECT reserved, observed FROM guild.mc_delegation_reservations")[0]["observed"], 0)
        self.store._write_step = real
        self.assertEqual(self.result(env, artifacts=[art("patch", "b")], event_id=eid)["state"], "accepted")
        self.assertEqual([(i["step"], i["state"]) for i in self.items()], [("build", "completed"), ("review", "queued")])

    def test_a_step_whose_result_leaves_an_effect_open_writes_no_next_step(self):
        self.admit(card())
        _, env = self.claim()
        digest = ToolRequest(SINK, 1, json.dumps({"op": "e1"}).encode()).arguments_sha256
        gid = str(uuid4())
        with self.connect() as conn:
            GRANTS.issue(conn, GrantDefinition(gid, "robert", "dev", "C-17", ("owner-turn",), ("fakework",),
                                               EFFECT_POLICY, SINK, 1, (digest,), 1,
                                               self.db_now() + timedelta(seconds=600), work_id="C-17"))
        e = {"op": "prepare", "key": "e1", "grant_id": gid, "request": {"operation": SINK, "operation_version": 1,
                                                                         "arguments": {"op": "e1"}}}
        self.assertEqual(self.store.effect("mac-a", env["request_id"], 1, e)["state"], "ok")
        lease = self.store.effect("mac-a", env["request_id"], 1, {**e, "op": "claim"})["lease"]
        self.assertEqual(self.store.effect("mac-a", env["request_id"], 1, {
            **e, "op": "dispatch", "lease": {k: lease[k] for k in ("receipt_generation", "token", "mode")}})["state"],
            "ok")
        ack = self.result(env, artifacts=[art("patch", "b")])
        self.assertEqual(ack["code"], "effects_open")
        self.assertEqual([(i["step"], i["state"]) for i in self.items()], [("build", "unknown")])
        self.assertEqual(self.events("card.step_written") + self.events("card.ended") + self.events("card.ready"), [])
        self.assertEqual(self.store.open_operations(env["request_id"])[0]["operation_id"],
                         effect_operation_id(env["request_id"], "e1"))

    # ── T4d, R4.3-d ──
    def test_t4d_a_definite_failure_is_retried_from_its_checkpoint_within_the_allowance(self):
        admitted = self.admit(card(attempts=2))
        got, env = self.claim()
        self.progress(env, "s1")
        eid, made = str(uuid4()), self.db_now()
        crashed = dict(status="failed", detail={"exit_code": 70}, artifacts=[art("output", "b1")], event_id=eid,
                       at=made)                                                   # crash: self, as the host keeps it
        ack = self.result(env, **crashed)
        self.assertEqual(ack, {"state": "accepted", "event_id": eid, "code": "retry", "attempt": 2, "generation": 2})
        replay = self.result(env, **crashed)
        self.assertEqual((replay["state"], replay["code"], replay["generation"]), ("already_applied", "retry", 2))
        # Probe P1b (review N5): anything else under that event id is refused, as for every owner write (S1-A).
        for other in (dict(status="completed", artifacts=[art("patch", "x")], detail={"exit_code": 0}),
                      dict(detail={"exit_code": 71}), dict(amount=0.2), dict(artifacts=[]),
                      dict(at=made - timedelta(seconds=1))):
            conflict = self.result(env, **{**crashed, **other})
            self.assertEqual((conflict["state"], conflict["code"]), ("refused", "event_id_conflict"), other)
        build = self.item("C-17.build")
        self.assertEqual((build["state"], build["generation"], build["runner_id"], build["result_event_id"],
                          build["facts"]["card"]["attempt"]), ("queued", 2, "mac-a", None, 2))
        self.assertIsNone(self.claim("mac-b")[0])                 # the checkpoint is on mac-a: the retry is its own
        got, env = self.claim()
        self.assertEqual((got["envelope"]["generation"], got["attempt"], got["resume_from"]["name"]), (2, 2, "s1"))
        self.assertEqual(got["reserved"], AMOUNT - USED)      # one reservation per item: the retry gets what remains
        self.assertEqual(self.day(), (AMOUNT - USED, USED))   # never more than the item's one amount, all told
        self.assertEqual(self.result(env, artifacts=[art("patch", "b2")])["state"], "accepted")
        self.assertEqual(self.day(), (0.0, 2 * USED))
        self.assertEqual([(i["step"], i["state"]) for i in self.items()], [("build", "completed"), ("review", "queued")])
        late = self.result({"request_id": env["request_id"], "generation": 1}, **crashed)
        self.assertEqual((late["state"], late["code"]), ("already_applied", "retry"))     # resent after it all: DUP-5
        other = self.result({"request_id": env["request_id"], "generation": 1},
                            **{**crashed, "status": "completed", "artifacts": [art("patch", "b2")]})
        self.assertEqual((other["state"], other["code"]), ("refused", "event_id_conflict"))
        # DUP-4: one accepted result for the item; the failed attempt is its own record. OWN-5: no owner call.
        rid = env["request_id"]
        self.assertEqual((len(self.events("result.accepted", rid)), len(self.events("attempt.failed", rid)),
                          len(self.events("item.retried", rid))), (1, 1, 1))
        self.assertEqual([e["kind"] for e in self.events() if e["source"] == "owner"],
                         ["card.admitted", "item.queued"])                       # the admission's own, nothing since
        self.assertEqual(self.grant_row(admitted["grant_id"])["consumed_operations"], 2)    # build and review only
        self.assertEqual(self.owner_counts("C-17"), (1, 0))

    def test_t4d_a_second_card_past_its_allowance_ends_failed_with_no_retry(self):
        self.admit(card("C-18", attempts=1))
        _, env = self.claim()
        ack = self.result(env, status="failed", detail={"exit_code": 70})
        self.assertEqual((ack["state"], ack.get("code")), ("accepted", None))
        build = self.item("C-18.build")
        self.assertEqual((build["state"], build["generation"]), ("failed", 1))
        self.assertEqual([(e["body"]["state"], e["body"]["code"]) for e in self.events("card.ended")],
                         [("failed", "attempts_exhausted")])
        self.assertEqual([i["step"] for i in self.items("C-18")], ["build"])
        self.assertIsNone(self.claim()[0])
        self.assertEqual(self.events("item.retried"), [])

    def test_only_a_known_exit_is_retried_and_never_an_unknown_outcome(self):
        for n, (status, detail, stopped, code) in enumerate((
                ("failed", {"code": "capture_incomplete"}, True, "exit_unknown"),
                ("failed", {"exit_code": 70}, False, "children_unconfirmed"),
                ("interrupted", {"code": "child_outcome_unknown"}, True, "interrupted"),
                ("timed_out", {"exit_code": None}, True, "timed_out"))):
            self.admit(card(f"C-{n}", attempts=3))
            _, env = self.claim()
            self.assertNotIn("code", self.result(env, status=status, detail=detail, stopped=stopped))
            self.assertEqual(self.item(f"C-{n}.build")["state"], status)
            self.assertEqual(self.events("card.ended")[-1]["body"]["code"], code)
        self.assertEqual(self.events("item.retried"), [])

    def test_a_card_step_ended_by_the_owner_ends_the_card(self):
        self.admit(card())
        self.assertEqual(self.store.stop("C-17.build"), "confirmed")
        self.admit(card("C-18"))
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])
        self.assertEqual(self.store.stop("C-18.review"), "confirmed")
        self.assertEqual([(e["body"]["card_id"], e["body"]["step"], e["body"]["code"]) for e in self.events("card.ended")],
                         [("C-17", "build", "owner_stop"), ("C-18", "review", "owner_stop")])
        self.assertIsNone(self.claim()[0])

    # ── T13 ──
    def test_t13_a_cross_vendor_review_is_proposed_with_one_solo_decision_and_claimed_only_after_its_admission(self):
        verified = self.make_store(Authority.parse({**TEST_VALUES, "launch_closure": "verified"}))
        codex_like = {"actor": "claude-code", "route": "anthropic.api", "amount": 1, "cost_cap_usd": 1}
        admitted = self.admit(card(reviewer=codex_like), store=verified)
        self.assertEqual([(s["step"], s["on_card"]) for s in admitted["steps"]],
                         [("build", True), ("review", False), ("fix-1", True)])
        self.assertEqual(self.grant_row(admitted["grant_id"])["max_operations"], 2)  # the review is not a card step
        _, env = self.claim(store=verified)
        self.result(env, artifacts=[art("patch", "b")], store=verified)
        review = self.item("C-17.review")
        self.assertEqual((review["state"], review["created_by"], review["result_detail"]["code"]),
                         ("proposed", "authority", "cross_vendor"))
        decisions = self.events("decision.queued", review["request_id"])
        self.assertEqual(len(decisions), 1)
        self.assertEqual((decisions[0]["body"]["class"], decisions[0]["body"]["solo"]), ("cross_vendor_admission", True))
        claude = CAPS + ["actor:claude-code"]
        self.assertIsNone(self.claim("mac-c", claude, store=verified)[0])           # no claim before its admission
        # The owner's own exact approval of that review (one more A, as today, §7.3).
        gid, proposal = str(uuid4()), str(uuid4())
        with self.connect() as conn:
            GRANTS.issue(conn, GrantDefinition(gid, "robert", "dev", "C-17", ("owner-turn-2",), ("mc",), "policy",
                                               "delegate.start_research", 1,
                                               (decisions[0]["body"]["subject_digest"],), 1,
                                               self.db_now() + timedelta(seconds=600), proposal_id=proposal,
                                               approved_operation_id=str(uuid4()),
                                               approval_expires_at=self.db_now() + timedelta(seconds=300)))
        self.assertEqual(verified.admit(review["request_id"], gid, by="robert", note="cross-vendor review, yes")
                         ["state"], "queued")
        self.assertEqual([e["body"]["decision_id"] for e in self.events("decision.resolved")],
                         [str(decisions[0]["event_id"])])
        got, env = self.claim("mac-c", claude, store=verified)
        self.assertEqual((got["step"], got["envelope"]["actor"]), ("review", "claude-code"))
        self.result(env, runner="mac-c", artifacts=[art("findings", "r")], route="anthropic.api", unit="usd",
                    store=verified)
        fix = self.item("C-17.fix-1")                       # the fix is a card step again: under the card's grant
        self.assertEqual((fix["state"], str(fix["grant_id"])), ("queued", admitted["grant_id"]))
        self.assertEqual([a["ref"] for a in fix["input_artifacts"]], ["b.patch", "r.findings"])
        self.assertEqual(self.owner_counts("C-17"), (2, 0))
        with self.assertRaisesRegex(AuthorityRefused, "item_not_proposed"):
            verified.admit(review["request_id"], gid, by="robert", note="again")

    def test_a_step_the_card_grant_no_longer_admits_is_proposed_with_one_routine_decision(self):
        admitted = self.admit(card())
        self.sql("UPDATE guild.mc_action_grants SET consumed_operations = max_operations")
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])
        review = self.item("C-17.review")
        self.assertEqual((review["state"], review["result_detail"]["code"]), ("proposed", "grant_exhausted"))
        decision = self.events("decision.queued", review["request_id"])
        self.assertEqual([(d["body"]["class"], d["body"]["solo"]) for d in decision], [("step_admission", False)])
        self.assertEqual(decision[0]["body"]["subject_digest"], admitted["steps"][1]["digest"])
        self.assertIsNone(self.claim()[0])

    # ── the review's fixes (REVIEW_independent_S1-C.md) ──
    def test_sf1_a_next_step_this_authority_now_refuses_never_loses_the_finished_result(self):
        """Probe P2: the authority's configuration changed mid-card. The finished step's result is recorded, and the
        card ends with the reason; nothing aborts, nothing is lost."""
        routes = TEST_VALUES["routes"]
        for card_id, changed, code in (
                ("C-17", {**routes["fakework"], "per_item": 0.1}, "item_cap_exceeded"),
                ("C-18", {**routes["fakework"], "actors": []}, "route_actor_mismatch")):
            self.admit(card(card_id))
            _, env = self.claim()
            later = self.make_store(Authority.parse({**TEST_VALUES, "routes": {**routes, "fakework": changed}}))
            eid, made = str(uuid4()), self.db_now()
            self.assertEqual(self.result(env, artifacts=[art("patch", card_id)], event_id=eid, store=later,
                                         at=made)["state"], "accepted")
            build = self.item(f"{card_id}.build")
            self.assertEqual((build["state"], str(build["result_event_id"])), ("completed", eid))
            self.assertEqual(self.result(env, artifacts=[art("patch", card_id)], event_id=eid, store=later,
                                         at=made)["state"], "already_applied")
            self.assertEqual([i["step"] for i in self.items(card_id)], ["build"])
            ended = [e["body"] for e in self.events("card.ended") if e["body"]["card_id"] == card_id]
            self.assertEqual([(b["step"], b["state"], b["code"]) for b in ended], [("review", "not_written", code)])
        self.assertEqual(len(self.events("result.accepted")), 2)

    def test_sf2_revoking_a_card_grant_ends_the_card_and_every_step_of_it(self):
        """Probe P3: a cross-vendor review admitted under its own grant is running when Robert revokes the card. The
        card ends once (grant_revoked), the review is ended as revocation ends any item, its late result writes no
        next step, and no routine decision appears. A card whose review is still proposed has that decision
        withdrawn."""
        verified = self.make_store(Authority.parse({**TEST_VALUES, "launch_closure": "verified"}))
        cross = {"actor": "claude-code", "route": "anthropic.api", "amount": 1, "cost_cap_usd": 1}
        claude = CAPS + ["actor:claude-code"]
        admitted = self.admit(card(reviewer=cross), store=verified)
        waiting = self.admit(card("C-18", reviewer=cross), store=verified)
        for _ in range(2):
            _, env = self.claim(store=verified)
            self.result(env, artifacts=[art("patch", env["request_id"])], store=verified)
        review = self.item("C-17.review")
        verified.admit(review["request_id"], self.own_grant(review), by="robert", note="cross-vendor review, yes")
        _, renv = self.claim("mac-c", claude, store=verified)
        out = verified.revoke(admitted["grant_id"], by="robert", note="stop the card")
        self.assertEqual((out["items"], out["cards"]), ([renv["request_id"]], ["C-17"]))
        self.assertEqual(self.item("C-17.review")["result_detail"]["code"], "grant_revoked")
        late = self.result(renv, runner="mac-c", artifacts=[art("findings", "r")], route="anthropic.api", unit="usd",
                           store=verified)
        self.assertEqual((late["state"], late["code"]), ("refused", "item_ended"))
        self.assertIsNone(self.item("C-17.fix-1"))
        # The card still waiting on its review's own approval: revoked, the review closed, its decision withdrawn.
        proposed = self.item("C-18.review")
        verified.revoke(waiting["grant_id"], by="robert", note="and this one")
        self.assertEqual(self.item("C-18.review")["state"], "cancelled")
        self.assertEqual([e["body"]["answer"] for e in self.events("decision.resolved", proposed["request_id"])],
                         ["withdrawn"])
        with self.assertRaisesRegex(AuthorityRefused, "item_not_proposed"):
            verified.admit(proposed["request_id"], self.own_grant(proposed), by="robert", note="too late")
        verified.revoke(admitted["grant_id"], by="robert", note="again")              # a replay ends nothing twice
        self.assertEqual(sorted((e["body"]["card_id"], e["body"]["code"]) for e in self.events("card.ended")),
                         [("C-17", "grant_revoked"), ("C-18", "grant_revoked")])
        self.assertEqual({d["body"]["class"] for d in self.events("decision.queued")}, {"cross_vendor_admission"})

    def test_sf2_a_step_of_a_revoked_card_with_an_effect_open_waits_then_ends_and_writes_nothing_after(self):
        verified = self.make_store(Authority.parse({**TEST_VALUES, "launch_closure": "verified"}))
        self.store = verified                     # the effect helper speaks to self.store
        cross = {"actor": "claude-code", "route": "anthropic.api", "amount": 1, "cost_cap_usd": 1}
        admitted = self.admit(card(reviewer=cross))
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])
        review = self.item("C-17.review")
        verified.admit(review["request_id"], self.own_grant(review), by="robert", note="yes")
        _, renv = self.claim("mac-c", CAPS + ["actor:claude-code"])
        e, lease = self.open_effect(renv, "mac-c", "claude-code")
        out = verified.revoke(admitted["grant_id"], by="robert", note="stop the card")
        self.assertEqual(out["waiting"], [renv["request_id"]])
        self.assertFalse(verified.renew(renv["request_id"], 1, protocol=2, runner_id="mac-c"))
        refused = verified.effect("mac-c", renv["request_id"], 1, {**e, "key": "e2", "op": "prepare"})
        self.assertEqual((refused["state"], refused["code"]), ("refused", "grant_revoked"))
        late = self.result(renv, runner="mac-c", artifacts=[art("findings", "r")], route="anthropic.api", unit="usd")
        self.assertEqual((late["state"], late["code"]), ("refused", "grant_revoked"))
        self.assertEqual(verified.effect("mac-c", renv["request_id"], 1, {**e, "op": "finish", "lease": lease,
                                                                         "state": "verified",
                                                                         "evidence": {"kind": "sink",
                                                                                      "outcome": "committed"}})
                         ["state"], "ok")
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                 "WHERE job_id='C-17.review'")
        verified.sweep()
        self.assertEqual((self.item("C-17.review")["state"], self.item("C-17.review")["result_detail"]["code"]),
                         ("cancelled", "grant_revoked"))
        self.assertIsNone(self.item("C-17.fix-1"))
        self.assertEqual([e["body"]["code"] for e in self.events("card.ended")], ["grant_revoked"])

    def test_sf3_a_retry_waiting_for_its_owner_can_be_reassigned_stopped_or_ended(self):
        """Probe P1: the retry waits for mac-a's runner, which is gone. Robert reassigns it to another runner of the
        same host (the Mac-only rule). It keeps the item's one reservation, starts from the item's inputs (the
        checkpoint was mac-a's), and the card carries on. Stopping a waiting retry releases what remains of it."""
        self.admit(card(attempts=2))
        _, env = self.claim()
        self.progress(env, "s1")
        self.assertEqual(self.result(env, status="failed", detail={"exit_code": 70})["code"], "retry")
        self.store.heartbeat("mac-a2", "online", CAPS, protocol=2, facts=facts("mac-a"))
        self.assertEqual(self.store.reassign(env["request_id"], "mac-a2", by="robert", note="mac-a's runner is gone"),
                         {"state": "reassigned", "generation": 3, "runner_id": "mac-a2"})
        build = self.item("C-17.build")
        self.assertEqual(("retry" in build["facts"], build["checkpoint"], build["facts"]["card"]["attempt"]),
                         (False, None, 2))
        self.assertIsNone(self.store.claim("mac-a", CAPS, protocol=2))
        got = self.store.claim("mac-a2", CAPS, protocol=2)
        self.assertEqual((got["envelope"]["generation"], got["attempt"], got["resume_from"], got["reserved"]),
                         (3, 2, None, AMOUNT - USED))
        self.result({"request_id": env["request_id"], "generation": 3}, runner="mac-a2",
                    artifacts=[art("patch", "b")])
        self.assertEqual([(i["step"], i["state"]) for i in self.items()], [("build", "completed"), ("review", "queued")])
        # A second card's waiting retry, stopped: nothing ran since its failure, so what remains is released.
        self.admit(card("C-18", attempts=2))
        while True:
            got, env = self.claim()
            if got["card_id"] == "C-18":
                break
            self.result(env, artifacts=[art("output", "r")])                             # C-17's review, done
        self.result(env, status="failed", detail={"exit_code": 70})
        self.assertEqual(self.store.stop("C-18.build"), "confirmed")
        stopped = self.item("C-18.build")
        self.assertEqual((stopped["state"], stopped["reservation"]["state"]), ("cancelled", "settled"))
        self.assertEqual(self.day()[0], 0.0)                  # nothing is held for work that will never run

    def test_sf4_one_reservation_per_item_across_its_attempts_and_resumes(self):
        """The brief's carried rule and Robert's per-item cap: a retry, and a resume of it, get only what remains of the
        item's one reservation; with nothing left there is no retry, and unknown usage never assumes a remainder."""
        self.admit(card(attempts=3))
        _, env = self.claim()
        self.assertEqual(self.result(env, status="failed", detail={"exit_code": 70}, amount=0.1)["code"], "retry")
        got, env = self.claim()
        self.assertEqual(got["reserved"], 0.15)
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                 "WHERE job_id='C-17.build'")
        answer = self.store.reconcile("mac-a", env["request_id"], env["generation"],
                                      {"child": "stopped", "checkpoint": None}, [])
        self.assertEqual((answer["answer"], answer["reserved"], answer["unit"]), ("resume", 0.15, "test_units"))
        env = {"request_id": env["request_id"], "generation": answer["generation"]}
        self.assertNotIn("code", self.result(env, status="failed", detail={"exit_code": 70}, amount=0.15))
        self.assertEqual(self.item("C-17.build")["state"], "failed")
        self.assertEqual(self.events("card.ended")[-1]["body"]["code"], "reservation_exhausted")
        self.assertEqual(self.day(), (0.0, 0.25))             # the item's one amount, across all its attempts
        self.admit(card("C-18", attempts=3))
        _, env = self.claim()
        self.assertNotIn("code", self.result(env, status="failed", detail={"exit_code": 70}, amount=None))
        self.assertEqual(self.events("card.ended")[-1]["body"]["code"], "usage_unknown")

    def test_n2_one_operation_of_a_card_grant_per_step(self):
        """Probe P4 on the path the authority uses: an operation of a card grant is bound to one step, recorded with
        its template digest, and a second admission of the same step spends nothing."""
        admitted = self.admit(card())
        build = admitted["steps"][0]
        bound = self.events("grant.step_bound")
        self.assertEqual([(b["body"]["step"], b["body"]["template_digest"], b["body"]["operation_id"]) for b in bound],
                         [("build", build["digest"], C.step_operation_id(admitted["grant_id"], build["digest"]))])
        compiled = C.Catalogue.load().compile(card())
        step = compiled.steps[0]
        item = self.store._v2_item("C-17.build-again", step.actor, step.scope, "personal-ai-agents", "a" * 40,
                                   step.brief, step.cost_cap_usd, 3600, step.route, step.amount, admitted["grant_id"],
                                   [], [], "C-17", "build", "authority")
        with self.store._tx() as cur:
            again = self.store._write(cur, item, subject=build["digest"], facts={"card": {"on_card": True}})
        self.assertEqual((again["state"], again["code"]), ("proposed", "grant_step_used"))
        self.assertEqual(self.grant_row(admitted["grant_id"])["consumed_operations"], 1)
        self.assertEqual(len(self.events("grant.step_bound")), 1)

    # ── the carried fix from the S1-B closure check (N-C4 part 2) ──
    def test_n_c4_a_result_made_before_an_unswept_lapse_audits_the_lapse_before_the_end(self):
        self.admit(card())
        _, env = self.claim()
        self.sql("""UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '10 seconds',
            facts = facts || jsonb_build_object('leased_at', clock_timestamp() - interval '60 seconds')""")
        made = self.db_now() - timedelta(seconds=20)
        self.assertEqual(self.result(env, artifacts=[art("patch", "b")], at=made)["state"], "accepted")
        kinds = [(e["kind"], e["generation"]) for e in self.events(item=env["request_id"])
                 if e["kind"] in ("item.lapsed", "result.accepted")]
        self.assertEqual(kinds, [("item.lapsed", 1), ("result.accepted", 1)])
        self.assertIn(self.events("item.lapsed")[0]["body"]["cause"], ("owner_contact_lost", "lease_not_renewed"))
        self.assertEqual(self.item("C-17.build")["state"], "completed")

    def test_n_c4_a_revocation_of_an_unswept_lapse_audits_the_lapse_before_the_end(self):
        admitted = self.admit(card())
        _, env = self.claim()
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '10 seconds'")
        self.store.revoke(admitted["grant_id"], by="robert", note="stop the card")
        kinds = [e["kind"] for e in self.events(item=env["request_id"]) if e["kind"] in ("item.lapsed", "item.cancelled")]
        self.assertEqual(kinds, ["item.lapsed", "item.cancelled"])


if __name__ == "__main__":
    unittest.main()
