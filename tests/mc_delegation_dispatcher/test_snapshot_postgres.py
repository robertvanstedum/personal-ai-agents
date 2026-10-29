"""S1-G at the authority on a real PostgreSQL (host runtime spec v0.5 §7, R4.6-b, R5.6-b; §9.4 OWN-1…OWN-6, DUP-7,
DUP-8, MC-0's judgment record; §9.5 T12, T14's counts, T15, T16, T17, T-PW1's logic): the snapshot built from
authority records and published as state.json; notices once per key with the next step; batched decisions answered
by one owner "yes" through the real supervisor and Telegram receipt path into fake-notifier; the solo classes alone;
the judgment reservation with fakejudge; a failed effect redone only as a new item on Robert's yes; an effects_open
result accepted with its card going on; production's notify-only watch of staging as prod-watch-test. Every worker
is fakework, driven at the store; no model, no provider, no network.
Opt-in: a disposable mc12_test_* database (tests/minimoi_actions/postgres_fixture_support.py)."""
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_delegation_dispatcher import cards as C, snapshot as S  # noqa: E402
from core.mc_delegation_dispatcher.store import (EFFECT_POLICY, Authority, AuthorityRefused,  # noqa: E402
                                                 DispatcherStore, effect_operation_id)
from core.minimoi_actions.grants import CapabilityRule, GrantDefinition, GrantStore  # noqa: E402
from core.minimoi_actions.requests import ToolRequest  # noqa: E402
from core.supervision import watch as W  # noqa: E402
from core.supervision.runtime import RuntimeLink  # noqa: E402
from core.supervision.store import Refused, Store  # noqa: E402
from fake_notifier import OWNER, FakeNotifier  # noqa: E402
from fakejudge import FakeJudge  # noqa: E402
from services.supervisor.main import Supervisor  # noqa: E402

MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql",
              "core/supervision/sql/001_supervision.sql", "core/supervision/sql/002_grok_and_exchanges.sql",
              "core/supervision/sql/003_runtime_decisions_and_watch.sql")
AUTH = Authority.parse(json.loads((Path(__file__).resolve().parent / "authority.test_values.json").read_text()))
SINK = "fakework.sink_post"
GRANTS = GrantStore({**C.grant_rows(), "delegate.start_build": CapabilityRule(1, True, True),
                     "delegate.start_research": CapabilityRule(1, True, True), SINK: CapabilityRule(1, False, True)})
CAPS = ["repo:personal-ai-agents", "scope:research", "scope:build", "actor:fakework", "host:mac-local"]
AMOUNT, USED = 0.25, 0.125
SOURCES = ("authority", "host", "owner")


class GitHub:
    def open_prs(self):
        return []

    def checks(self, sha):
        return []

    def runs(self):
        return []


def facts(host, **over):
    return {"host_id": host, "bundle_digest": "sha256:" + "c" * 64, "roles": ["authority", "worker", "witness"],
            "availability": "intermittent", "grace": 900, "last_gap_s": 0, "announced": "online", **over}


def art(kind, task):
    return {"kind": kind, "ref": f"{task}.{kind}", "sha256": hashlib.sha256(f"{task}:{kind}".encode()).hexdigest(),
            "bytes": 10, "author": "fakework", "review_status": "unreviewed"}


def card(card_id="C-17", **over):
    worker = {"actor": "fakework", "route": "fakework", "amount": AMOUNT, "cost_cap_usd": 1}
    doc = {"schema": "minimoi.card.v1", "card_id": card_id, "class": "build", "class_version": 1,
           "repository": "personal-ai-agents", "ref": "a" * 40, "workers": {"builder": worker, "reviewer": worker},
           "briefs": {"build": "fakework/1\ncheckpoint s1\ncheckpoint s2\nresult ok",
                      "review": "fakework/1\nfindings\nresult ok", "fix": "fakework/1\nresult ok"},
           "attempts": 2, "rounds": {"fix": 1}, "step_deadline_s": 3600}
    doc.update(over)
    return doc


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class HeadlessState(PostgresFixture, unittest.TestCase):
    migrations = MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations, guild.mc_action_grant_consumptions, guild.mc_action_grants,
                guild.mc_action_events, guild.mc_action_receipts, guild.supervision_decision_messages,
                guild.supervision_contacts, guild.supervision_watch, guild.supervision_nudges, guild.supervision_stops,
                guild.supervision_decisions, guild.supervision_checkins, guild.supervision_alerts,
                guild.supervision_exchanges, guild.supervision_outbox RESTART IDENTITY CASCADE""")
            cur.execute("UPDATE guild.supervision_telegram SET offset_=0")
        self.dir = Path(tempfile.mkdtemp())
        self.store = DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH, grants=GRANTS)
        self.sup_store = Store(self.connect)
        self.notify = FakeNotifier(self.dir / "notify.jsonl")
        self.link = RuntimeLink(self.store, owner="robert", view_bound_s=60)
        self.sup = Supervisor(self.sup_store, self.notify, GitHub(), chat_id=OWNER, user_id=OWNER, runtime=self.link)
        self.store.sweep()                                   # the sweeper has run: its age is known

    # ── helpers ──
    def sql(self, query, args=()):
        from psycopg2.extras import RealDictCursor
        with self.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, args)
            return cur.fetchall() if cur.description else None

    def db_now(self):
        return self.sql("SELECT clock_timestamp() AS now")[0]["now"]

    def admit(self, doc):
        return self.store.admit_card(doc, grant_id=str(uuid4()), owner_turn_ids=("owner-turn-1",),
                                     proposal_id=str(uuid4()), approved_operation_id=str(uuid4()),
                                     approval_expires_at=self.db_now() + timedelta(seconds=300), by="robert",
                                     note="the card as approved")

    def beat(self, runner="mac-a", **over):
        return self.store.heartbeat(runner, "online", CAPS, protocol=2, facts=facts(runner, **over))

    def claim(self, runner="mac-a"):
        self.beat(runner)
        got = self.store.claim(runner, CAPS, protocol=2)
        return got, None if got is None else {"request_id": got["envelope"]["request_id"],
                                              "generation": got["envelope"]["generation"]}

    def progress(self, env, name, paths=("core/x.py",), runner="mac-a"):
        cp = {"name": name, "digest": "sha256:" + hashlib.sha256(name.encode()).hexdigest(), "paths": list(paths)}
        return self.store.progress(env["request_id"], env["generation"], "executing", protocol=2, runner_id=runner,
                                   event_id=str(uuid4()), at=self.db_now().isoformat(), checkpoint=cp)

    def result(self, env, *, status="completed", artifacts=(), detail=None, runner="mac-a"):
        return self.store.result(env["request_id"], env["generation"], event_id=str(uuid4()), status=status,
                                 artifacts=list(artifacts), detail={"exit_code": 0} if detail is None else detail,
                                 output_text="done", protocol=2, runner_id=runner, at=self.db_now().isoformat(),
                                 route="fakework", usage={"amount": USED, "unit": "test_units", "children_stopped": True})

    def effect_open(self, env, work, key="e1", runner="mac-a"):
        """An effect of the item prepared, claimed and dispatched: its outcome is open. -> (effect, its lease)."""
        digest = ToolRequest(SINK, 1, json.dumps({"op": key}).encode()).arguments_sha256
        gid = str(uuid4())
        with self.connect() as conn:
            GRANTS.issue(conn, GrantDefinition(gid, "robert", "dev", work, ("owner-turn",), ("fakework",),
                                               EFFECT_POLICY, SINK, 1, (digest,), 1,
                                               self.db_now() + timedelta(seconds=600), work_id=work))
        e = {"op": "prepare", "key": key, "grant_id": gid, "request": {"operation": SINK, "operation_version": 1,
                                                                        "arguments": {"op": key}}}
        g = env["generation"]
        self.assertEqual(self.store.effect(runner, env["request_id"], g, e)["state"], "ok")
        lease = self.store.effect(runner, env["request_id"], g, {**e, "op": "claim"})["lease"]
        lease = {k: lease[k] for k in ("receipt_generation", "token", "mode")}
        self.assertEqual(self.store.effect(runner, env["request_id"], g, {**e, "op": "dispatch", "lease": lease})
                         ["state"], "ok")
        return e, lease

    def finish(self, env, e, lease, state, runner="mac-a"):
        outcome = "committed" if state == "verified" else "absent"
        return self.store.effect(runner, env["request_id"], env["generation"],
                                 {**e, "op": "finish", "lease": lease, "state": state,
                                  "evidence": {"kind": "readback", "outcome": outcome}})

    def events(self, kind=None, item=None):
        rows = self.sql("SELECT * FROM guild.mc_delegation_events ORDER BY seq")
        return [r for r in rows if (kind is None or r["kind"] == kind)
                and (item is None or str(r["item_id"]) == str(item))]

    def item(self, job):
        return self.store.by_job(job)

    def publish(self):
        return self.store.publish(self.dir / "state")["doc"]

    def owner_work(self, doc, card_id):
        return next(o for o in doc["owner_work"] if o["card_id"] == card_id)

    def receipt_of(self, d, words):
        return {"channel": "test_owner", "turn_id": f"owner-test:{uuid4()}", "words": words,
                "decision_sha256": d["text_sha256"]}

    def waiting(self, doc, cls=None):
        return [d for d in doc["decisions_waiting"] if cls is None or d["class"] == cls]

    def answer(self, decision_id, answer="approved", words="yes"):
        """The owner test identity's answer to one decision, bound to its words as the authority renders them now."""
        st = self.store.decisions_status([decision_id])[decision_id]
        return self.store.resolve(decision_id, answer, by="robert", receipt={
            "channel": "test_owner", "turn_id": f"owner-test:{uuid4()}", "words": words,
            "decision_sha256": st["text_sha256"]})

    def owner_yes(self, request_id):
        """MC asks to review an item, Robert says yes: the owner trigger reserve_judgment verifies (SF4)."""
        did = self.store.propose_review(request_id, by="mc")["decision_id"]
        self.assertEqual(self.answer(did)["action"], "authorize_review")
        return {"kind": "owner", "decision_id": did}

    def runtime_texts(self):
        return [m["text"] for m in self.notify.lines()]

    def backdate(self, request_id, seconds):
        self.sql("""UPDATE guild.mc_delegations SET progress_at = progress_at - make_interval(secs => %s),
            facts = facts || jsonb_build_object('leased_at', clock_timestamp() - make_interval(secs => %s))
            WHERE request_id=%s""", (seconds, seconds * 2, request_id))

    # ── T12, OWN-4, MC-0: the snapshot alone answers; one review_request per stall ──
    def test_t12_the_snapshot_alone_answers_and_a_stall_gives_one_notice_and_one_review_request(self):
        self.admit(card("C-1"))
        self.admit(card("C-2"))
        self.store.note_read("robert", "owner")                   # OWN-3: Robert looks with no notice: a check (C)
        _, env = self.claim()
        self.progress(env, "s1")
        self.backdate(env["request_id"], 200)                     # no progress for 200 s; build timeout 180 s
        self.assertTrue(self.store.renew(env["request_id"], 1, protocol=2, runner_id="mac-a"))
        doc = self.publish()
        stalled = next(i for i in doc["items"] if i["item_id"] == env["request_id"])
        idle = next(i for i in doc["items"] if i["card_id"] == "C-2")
        self.assertEqual((stalled["shown_as"], stalled["flags"]["stalled"], stalled["owner_host_id"]),
                         ("running", True, "mac-a"))
        self.assertGreater(stalled["progress_age_s"], 180)
        self.assertEqual((idle["shown_as"], idle["flags"]["stalled"]), ("queued", False))
        self.assertEqual(stalled["next_authorized_step"]["step"], "continue")
        self.assertEqual(stalled["grant"]["steps_left"], 2)
        # The stall's one notice (OWN-4) is its review request, queued at the sweep as one routine decision; a flag
        # starts no call (R7.5-d), and only Robert's yes to it would let MC make one (SF4).
        self.assertEqual([n for n in doc["notices"] if n["rule"] == "stalled"], [])
        (review,) = self.waiting(doc, "review_request")
        self.assertEqual((review["item_id"], review["yes"], review["no"]), (env["request_id"], "authorize_review",
                                                                             "withdraw"))
        (notice,) = [n for n in doc["notices"] if review["decision_id"] in (n.get("decisions") or [])]
        self.assertIsNotNone(notice["raised_at"])
        self.assertEqual(len(self.events("notice.raised")), len(doc["notices"]))
        self.assertEqual(len(self.events("proposal.review_request")), 1)
        # a second stall tick: the same notice, the same proposal, nothing new recorded
        before = len(self.events())
        again = self.publish()
        self.assertEqual(len(self.events()), before)
        self.assertEqual([p["proposal_id"] for p in again["proposals"]], [p["proposal_id"] for p in doc["proposals"]])
        self.assertEqual(again["proposals"][0]["state"], "queued")
        # The stub renderer reads only state.json.
        from test_snapshot import render_stub
        rows = {r["item"]: r for r in render_stub(self.dir / "state" / "state.json")}
        self.assertEqual((rows[env["request_id"]]["stalled"], rows[env["request_id"]]["owned_by"]), (True, "mac-a"))
        self.assertIsNotNone(rows[env["request_id"]]["contact_age_s"])
        # MC-0: no judgment call was made; the flag started none.
        self.assertEqual((again["judgment"]["calls_today"], self.events("judgment.reserved")), (0, []))
        # OWN-3: the read before any notice is one C; a read after the stall notice is prompted, not a check.
        self.store.note_read("robert", "owner")
        self.assertEqual(self.owner_work(self.publish(), "C-1")["C"], 1)
        self.assertEqual(self.sql("SELECT count(*) AS n FROM guild.mc_delegation_runners WHERE runner_id = "
                                  "'reader/dev/robert'")[0]["n"], 1)                  # its contact, never a host

    # ── T14's counts, OWN-1, OWN-2, OWN-3, O ──
    def test_t14_the_reference_card_counts_a1_r0_c0_and_its_ready_notice_precedes_the_merge(self):
        self.admit(card())
        _, env = self.claim()
        self.progress(env, "s1")
        self.progress(env, "s2")
        self.result(env, artifacts=[art("output", "b"), art("patch", "b")])
        _, env = self.claim()
        self.result(env, artifacts=[art("output", "r"), art("findings", "r")])
        _, env = self.claim()
        self.result(env, artifacts=[art("output", "f"), art("patch", "f")])
        doc = self.publish()
        self.assertEqual(self.owner_work(doc, "C-17"), {"card_id": "C-17", "A": 1, "R": 0, "C": 0, "O": 0, "J": 0})
        self.assertEqual([n["key"] for n in doc["notices"] if n["rule"] == "ready"], ["runtime-ready:C-17"])
        items = self.sql("SELECT * FROM guild.mc_delegations WHERE card_id='C-17' ORDER BY (facts->'card'->>'index')::int")
        for i in items[1:]:                                       # OWN-2 through inputs_from
            self.assertEqual(i["created_by"], "authority")
            self.assertEqual(i["input_artifacts"], [{"ref": s["ref"], "sha256": s["sha256"]}
                                                    for s in i["facts"]["card"]["inputs_from"]])
        self.store.record_owner_act("C-17", "merge", by="robert", note="merged after the ready notice")
        self.assertEqual(self.owner_work(self.publish(), "C-17"), {"card_id": "C-17", "A": 1, "R": 0, "C": 0, "O": 1,
                                                                   "J": 0})
        # An owner read with no notice since the previous one is a check (C); one after a notice is not.
        self.store.note_read("robert", "owner")
        self.assertEqual(self.owner_work(self.publish(), "C-17")["C"], 0)    # after the card: not counted for it

    # ── T15, OWN-6, DUP-7: three routine decisions, one notice, one yes, three receipts; the unknown alone ──
    def test_t15_own6_three_routine_decisions_share_one_notice_one_yes_and_an_unknown_waits_alone(self):
        for n in (1, 2, 3):
            self.admit(card(f"C-{n}"))
        self.sql("UPDATE guild.mc_action_grants SET consumed_operations = max_operations")   # their steps now need you
        for n in (1, 2, 3):
            _, env = self.claim()
            self.result(env, artifacts=[art("patch", f"b{n}")])
        self.admit(card("C-4"))
        _, env4 = self.claim()
        self.effect_open(env4, "C-4")
        self.assertEqual(self.result(env4, artifacts=[art("patch", "b4")])["code"], "effects_open")
        doc = self.publish()
        routine = self.waiting(doc, "step_admission")
        self.assertEqual(len(routine), 3)
        notices = [n for n in doc["notices"] if n["rule"] in ("decision", "decisions")]
        self.assertEqual(sorted((n["rule"], len(n["decisions"])) for n in notices), [("decision", 1), ("decisions", 3)])
        # The supervisor (the Telegram check-in's code, fake-notifier as its bot) sends one message each.
        self.sup.runtime_tick()
        self.sup.send_new()                                        # sends nothing more: the link sent them
        sent = self.notify.lines()
        self.assertEqual(len(sent), 2)
        batch = next(m for m in sent if m["text"].startswith("Decisions "))
        solo = next(m for m in sent if "on its own" in m["text"])
        for d in routine:
            self.assertIn(d["words"], batch["text"])               # each in full words
        rows = self.sup_store.by_batch(batch["message_id"])
        self.assertEqual(len(rows), 3)
        # One owner "yes" to the batched message: three receipts, the same words, each bound to its own digest.
        self.notify.reply("yes", batch["message_id"])
        self.sup.read_answers(wait=0)
        decided = self.sql("""SELECT * FROM guild.supervision_decisions WHERE requester->>'agent'='runtime'
            AND state='approved' ORDER BY id""")
        self.assertEqual(len(decided), 3)
        self.assertEqual({d["receipt"]["text"] for d in decided}, {"yes"})
        self.assertEqual([d["receipt"]["decision_sha256"] for d in decided], [d["text_sha256"] for d in decided])
        self.assertEqual(len({d["text_sha256"] for d in decided}), 3)
        # The answers reach the authority once each: each step admitted under its own exact approval (one A each).
        self.sup.runtime_tick()
        resolved = self.events("decision.resolved")
        self.assertEqual(sorted(e["body"]["action"] for e in resolved if e["source"] == "owner"), ["admit"] * 3)
        self.assertEqual(sorted(e["body"]["receipt"]["decision_sha256"] for e in resolved if e["source"] == "owner"),
                         sorted(d["text_sha256"] for d in decided))
        for n in (1, 2, 3):
            self.assertEqual(self.item(f"C-{n}.review")["state"], "queued")
        # The unknown arrived alone, and the batched "yes" left it waiting.
        doc = self.publish()
        self.assertEqual([d["class"] for d in self.waiting(doc)], ["unknown_resolution"])
        self.assertEqual(self.item("C-4.build")["state"], "unknown")
        unknown_row = self.sup_store.by_message(solo["message_id"])
        self.assertEqual(unknown_row["state"], "sent")
        work = {o["card_id"]: o for o in doc["owner_work"]}
        self.assertEqual([work[f"C-{n}"]["A"] for n in (1, 2, 3)], [2, 2, 2])
        self.assertEqual([work[f"C-{n}"]["C"] for n in (1, 2, 3)], [0, 0, 0])      # each answer followed its notice
        # DUP-7: one notice per key, however often published; the announce replies once per decision.
        self.publish()
        keys = [e["body"]["key"] for e in self.events("notice.raised")]
        self.assertEqual(len(keys), len(set(keys)))
        # SF2: once applied, each answer is told with what it did, in reply to the batched message.
        applied = [m for m in self.notify.lines() if m["text"].startswith("Applied: D-")]
        self.assertEqual(len(applied), 3)
        self.assertEqual({m["reply_to"] for m in applied}, {batch["message_id"]})
        self.assertTrue(all("the step is admitted under its own approval and queued" in m["text"] for m in applied))
        self.sup.announce()                                   # the generic announce tells runtime rows nothing more
        self.assertFalse([m for m in self.notify.lines() if m["text"].startswith("Recorded: D-")])

    def test_t15_mixed_answers_use_one_code_at_a_time(self):
        for n in (1, 2):
            self.admit(card(f"C-{n}"))
        self.sql("UPDATE guild.mc_action_grants SET consumed_operations = max_operations")
        for n in (1, 2):
            _, env = self.claim()
            self.result(env, artifacts=[art("patch", f"b{n}")])
        self.publish()
        self.sup.runtime_tick()
        (batch,) = self.notify.lines()
        rows = self.sup_store.by_batch(batch["message_id"])
        self.notify.reply(f"no {rows[1]['code']}", batch["message_id"])
        self.notify.reply("approve D-999", batch["message_id"])             # not in that message: not counted
        self.sup.read_answers(wait=0)
        self.assertEqual([r["state"] for r in self.sup_store.by_batch(batch["message_id"])], ["sent", "declined"])
        self.sup.runtime_tick()
        self.assertEqual(self.item("C-2.review")["state"], "cancelled")      # declined: the step ends, and its card
        self.assertEqual([e["body"]["code"] for e in self.events("card.ended")], ["owner_declined"])
        self.notify.reply("yes", batch["message_id"])                        # the rest: only what still waits
        self.sup.read_answers(wait=0)
        self.sup.runtime_tick()
        self.assertEqual(self.item("C-1.review")["state"], "queued")

    # ── T16: the judgment reservation, with fakejudge; SF4: its triggers verified ──
    def test_t16_one_call_per_window_escalation_after_two_and_the_cap(self):
        self.admit(card("C-1"))
        self.admit(card("C-2"))
        _, env = self.claim()
        w, other = env["request_id"], str(self.item("C-2.build")["request_id"])
        judge = FakeJudge(self.store, self.dir / "judge.jsonl")
        # SF4: MC cannot certify Robert: a made-up owner turn, or one he hasn't answered, starts nothing.
        self.assertEqual(judge.review([w], {"kind": "owner", "decision_id": str(uuid4())})["code"],
                         "judgment_owner_turn_not_found")
        asked = self.store.propose_review(w, by="mc")["decision_id"]
        self.assertEqual(judge.review([w], {"kind": "owner", "decision_id": asked})["code"],
                         "judgment_owner_turn_not_found")
        self.answer(asked)
        owner = {"kind": "owner", "decision_id": asked}
        self.assertEqual(judge.review([other], owner)["code"], "judgment_items_not_authorized")
        first = judge.review([w], owner)
        self.assertEqual(first["state"], "reserved")
        self.assertEqual(judge.review([w], owner)["code"], "judgment_trigger_used")      # once
        for _ in range(3):                                    # stall events: a flag never starts a call
            self.assertEqual(judge.review([w], {"kind": "flag", "flag": "stalled"})["code"],
                             "judgment_trigger_not_allowed")
        for _ in range(3):                                    # requests inside one window: coalesced, no call
            self.assertEqual(judge.review([w], self.owner_yes(w))["state"], "coalesced")
        self.assertEqual(len(judge.calls()), 1)
        self.sql("""UPDATE guild.mc_delegation_events SET at = at - interval '200 seconds'
            WHERE kind='judgment.reserved'""")                # the window (build: 180 s) has passed
        second = judge.review([w], self.owner_yes(w))
        self.assertEqual((second["state"], second["recorded"]["escalated"]), ("reserved", [w]))
        self.assertEqual(judge.review([w], self.owner_yes(w))["code"], "mc_escalated")
        self.assertEqual(judge.review([other], self.owner_yes(other))["code"], "mc_judgment_cap")
        self.assertEqual(len(judge.calls()), 2)
        reserved = self.events("judgment.reserved")
        self.assertEqual({(e["source"], e["body"]["by"], e["body"]["trigger"]["kind"]) for e in reserved},
                         {("authority", "mc", "owner")})      # the caller is named; the source is never "owner"
        doc = self.publish()
        self.assertEqual((doc["judgment"]["calls_today"], doc["judgment"]["cap_calls"],
                          doc["judgment"]["escalated"]), (2, 2, [w]))
        self.assertEqual([n["key"] for n in doc["notices"] if n["rule"] == "escalated"], [f"runtime-escalated:{w}"])
        day = self.sql("SELECT reserved, observed FROM guild.mc_delegation_reservations WHERE route='fakejudge'")[0]
        self.assertEqual((float(day["reserved"]), float(day["observed"])), (0.0, 0.5))

    # ── sweep A1: one open review request per item, and Robert's latest answer about reviewing it governs ──
    def stalled_with_mc_request_open(self):
        """MC asks to review an item (R1, open), and then the item stalls. -> (item id, R1, the published snapshot)."""
        self.admit(card("C-1"))
        _, env = self.claim()
        rid = env["request_id"]
        self.progress(env, "s1")
        r1 = self.store.propose_review(rid, by="mc")["decision_id"]
        self.backdate(rid, 200)                                   # build timeout 180 s
        self.assertTrue(self.store.renew(rid, 1, protocol=2, runner_id="mac-a"))
        return rid, r1, self.publish()

    def test_a1_a_stall_queues_no_second_review_request_and_its_one_notice_names_the_open_one(self):
        rid, r1, doc = self.stalled_with_mc_request_open()
        reviews = [d["decision_id"] for d in self.waiting(doc, "review_request") if d["item_id"] == rid]
        self.assertEqual(reviews, [r1])                           # one open handle, MC's own
        (prop,) = self.events("proposal.review_request", item=rid)
        self.assertEqual(prop["body"]["decision_id"], r1)         # the stall's proposal names it
        (stall,) = [n for n in doc["notices"] if n["rule"] == "stalled"]   # OWN-4: the stall's one notice, raised
        self.assertIsNotNone(stall["raised_at"])
        self.assertIn(f"decision {r1[:8]}", stall["text"])
        self.assertIn("already waiting", stall["next_authorized_step"]["step"])
        before = len(self.events())
        self.publish()                                            # a second stall tick: nothing new
        self.assertEqual(len(self.events()), before)
        self.assertEqual(self.store.propose_review(rid, by="mc")["decision_id"], r1)
        self.assertEqual(self.answer(r1)["action"], "authorize_review")
        got = self.store.reserve_judgment(trigger={"kind": "owner", "decision_id": r1}, items=[rid], by="mc")
        self.assertEqual(got["state"], "reserved")

    def test_a1_an_older_yes_is_never_used_after_a_newer_no_about_reviewing_the_same_item(self):
        """The sweep's sequence with the second handle made as a stall made it before the fix (records written then):
        Robert's yes to R1, then his no to R2, about the same item. R1's yes reserves nothing."""
        rid, r1, _ = self.stalled_with_mc_request_open()
        with self.store._tx() as cur:                             # the stall's own request, as the old path queued it
            r2 = self.store._queue_review(cur, self.store._item(cur, rid), {
                "reason": "stalled", "proposal_id": str(uuid4()), "since": "then", "owner_host_id": "mac-a",
                "timeout_s": 180})
        self.assertNotEqual(r1, r2)
        self.assertEqual(self.answer(r1, "approved", "yes")["action"], "authorize_review")   # t1: yes
        self.assertEqual(self.answer(r2, "declined", "no")["action"], "withdraw")            # t2: no (newer)
        got = self.store.reserve_judgment(trigger={"kind": "owner", "decision_id": r1}, items=[rid], by="mc")
        self.assertEqual(got, {"state": "refused", "code": "judgment_owner_turn_superseded"})
        self.assertEqual(self.events("judgment.reserved"), [])

    def test_a1_robert_s_latest_answer_governs_across_successive_review_requests(self):
        self.admit(card("C-1"))
        _, env = self.claim()
        rid = env["request_id"]
        r1 = self.store.propose_review(rid, by="mc")["decision_id"]
        self.answer(r1, "approved", "yes")
        r2 = self.store.propose_review(rid, by="mc")["decision_id"]          # R1 is closed: a new request
        self.assertNotEqual(r1, r2)
        self.answer(r2, "declined", "no")
        owner = {"kind": "owner", "decision_id": r1}
        self.assertEqual(self.store.reserve_judgment(trigger=owner, items=[rid], by="mc")["code"],
                         "judgment_owner_turn_superseded")
        r3 = self.store.propose_review(rid, by="mc")["decision_id"]
        self.answer(r3, "approved", "yes")                                    # his newest answer is yes again
        self.assertEqual(self.store.reserve_judgment(trigger=owner, items=[rid], by="mc")["code"],
                         "judgment_owner_turn_superseded")                    # still never the older one
        got = self.store.reserve_judgment(trigger={"kind": "owner", "decision_id": r3}, items=[rid], by="mc")
        self.assertEqual(got["state"], "reserved")

    def test_sf4_a_card_slot_reviews_its_own_cards_work_once(self):
        self.admit(card("C-1"))
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])       # the build is done: the review step is the slot
        self.admit(card("C-2"))
        build, slot, other = (str(self.item(j)["request_id"]) for j in ("C-1.build", "C-1.review", "C-2.build"))
        judge = FakeJudge(self.store, self.dir / "judge.jsonl")
        by_slot = {"kind": "card_slot", "item_id": slot}
        self.assertEqual(judge.review([other], by_slot)["code"], "judgment_items_not_of_slot")   # the reviewer's probe
        self.assertEqual(judge.review([build], {"kind": "card_slot", "item_id": build})["code"],
                         "judgment_slot_not_admitted")       # a build step is no review slot
        self.assertEqual(judge.review([build], by_slot)["state"], "reserved")
        self.assertEqual(judge.review([build], by_slot)["code"], "judgment_trigger_used")   # the slot is used up
        (e,) = self.events("judgment.reserved")
        self.assertEqual((e["source"], e["body"]["by"], e["body"]["trigger"]),
                         ("authority", "mc", {"kind": "card_slot", "item_id": slot, "card_id": "C-1"}))

    # ── T17: drift, one notice each, work not stopped ──
    def test_t17_drift_out_of_scope_and_over_estimate_one_notice_each_and_the_work_goes_on(self):
        self.admit(card("C-5", paths_allowed=["core/"], estimate_s=60))
        _, env = self.claim()
        self.assertEqual(self.progress(env, "s1", paths=("core/x.py", "docs/notes.md"))["state"], "applied")
        self.sql("UPDATE guild.mc_delegation_events SET at = at - interval '200 seconds' WHERE kind='card.admitted'")
        doc = self.publish()
        it = next(i for i in doc["items"] if i["item_id"] == env["request_id"])
        self.assertEqual(sorted(d["kind"] for d in it["flags"]["drift"]), ["out_of_scope_paths", "over_estimate"])
        drift = sorted(n["key"] for n in doc["notices"] if n["rule"] == "drift")
        self.assertEqual(drift, [f"runtime-drift:{env['request_id']}:out_of_scope_paths",
                                 f"runtime-drift:{env['request_id']}:over_estimate"])
        self.publish()
        self.assertEqual(sorted(e["body"]["key"] for e in self.events("notice.raised") if e["body"]["rule"] == "drift"),
                         drift)                               # once each
        self.assertTrue(self.store.renew(env["request_id"], 1, protocol=2, runner_id="mac-a"))     # not stopped
        self.assertEqual(self.progress(env, "s2")["state"], "applied")

    # ── item 9: a failed effect is redone only as a new item, on Robert's yes ──
    def test_a_definitively_failed_effect_ends_the_step_and_only_robert_s_yes_redoes_it_as_a_new_item(self):
        self.admit(card())
        _, env = self.claim()
        e, lease = self.effect_open(env, "C-17")
        self.assertEqual(self.finish(env, e, lease, "failed")["state"], "ok")
        ack = self.result(env, status="failed", detail={"exit_code": 69})
        self.assertEqual(ack["state"], "accepted")
        self.assertNotEqual(ack.get("code"), "retry")                       # never retried with the same item
        build = self.item("C-17.build")
        self.assertEqual((build["state"], build["result_detail"]["code"]), ("failed", "effect_failed"))
        self.assertEqual(self.events("card.ended"), [])                     # the card waits on the decision
        redo = self.item("C-17.build.redo1")
        self.assertEqual((redo["state"], redo["created_by"], redo["facts"]["card"]["redo_of"]),
                         ("proposed", "authority", str(build["request_id"])))
        doc = self.publish()
        (d,) = self.waiting(doc, "step_redo")
        self.assertFalse(d["solo"])
        self.assertIn("new operation ids", d["words"])
        out = self.store.resolve(d["decision_id"], "approved", by="robert", receipt=self.receipt_of(d, "yes"))
        self.assertEqual((out["state"], out["action"]), ("resolved", "admit"))
        self.assertEqual(self.item("C-17.build.redo1")["state"], "queued")
        got, env2 = self.claim()
        self.assertEqual(got["step"], "build")
        self.assertNotEqual(effect_operation_id(env2["request_id"], "e1"), effect_operation_id(env["request_id"], "e1"))
        self.result(env2, artifacts=[art("patch", "b2")])
        self.assertEqual(self.item("C-17.review")["state"], "queued")       # the card goes on
        work = self.owner_work(self.publish(), "C-17")
        self.assertEqual((work["A"], work["R"], work["C"]), (2, 0, 0))      # the redo's admission: one A, no relay

    # ── sweep A13: a redo never sends again an effect its failed step committed ──
    def effect_steps(self, env, eff):
        """prepare, claim, dispatch of one effect, as far as each is ok. -> (answers, lease or None)."""
        r, g = env["request_id"], env["generation"]
        out = [self.store.effect("mac-a", r, g, eff)]
        if out[-1]["state"] != "ok":
            return out, None
        got = self.store.effect("mac-a", r, g, {**eff, "op": "claim"})
        out.append(got)
        if got["state"] != "ok":
            return out, None
        lease = {k: got["lease"][k] for k in ("receipt_generation", "token", "mode")}
        out.append(self.store.effect("mac-a", r, g, {**eff, "op": "dispatch", "lease": lease}))
        return out, lease

    def test_a13_a_redo_carries_an_effect_its_failed_step_committed_and_never_sends_it_again(self):
        """The sweep's sequence: a card step commits e1 (verified), its e2 fails for good, the step ends failed and
        Robert says yes to its redo, whose child runs the same brief. e1 is answered as already done under the
        operation id it was committed with, never prepared, claimed or dispatched again; e2 is redone under a new one.
        A redo of the redo carries e1 still."""
        self.admit(card())
        _, env = self.claim()
        rid = env["request_id"]
        digest = ToolRequest(SINK, 1, json.dumps({"op": "e1"}).encode()).arguments_sha256
        gid = str(uuid4())
        with self.connect() as conn:                  # e1's grant leaves room for a redo (two operations)
            GRANTS.issue(conn, GrantDefinition(gid, "robert", "dev", "C-17", ("owner-turn",), ("fakework",),
                                               EFFECT_POLICY, SINK, 1, (digest,), 2,
                                               self.db_now() + timedelta(seconds=600), work_id="C-17"))
        e1 = {"op": "prepare", "key": "e1", "grant_id": gid,
              "request": {"operation": SINK, "operation_version": 1, "arguments": {"op": "e1"}}}
        steps, lease1 = self.effect_steps(env, e1)
        self.assertEqual([s["state"] for s in steps], ["ok", "ok", "ok"])
        self.assertEqual(self.finish(env, e1, lease1, "verified")["state"], "ok")      # e1 committed
        e2, lease2 = self.effect_open(env, "C-17", key="e2")
        self.assertEqual(self.finish(env, e2, lease2, "failed")["state"], "ok")         # e2 failed for good
        self.assertEqual(self.result(env, status="failed", detail={"exit_code": 69})["state"], "accepted")
        (d,) = self.waiting(self.publish(), "step_redo")
        self.assertEqual(self.store.resolve(d["decision_id"], "approved", by="robert",
                                            receipt=self.receipt_of(d, "yes"))["action"], "admit")
        _, env2 = self.claim()
        redo = env2["request_id"]
        self.assertNotEqual(redo, rid)
        committed = effect_operation_id(rid, "e1")
        steps2, _ = self.effect_steps(env2, e1)                # the redo's child runs the same brief: e1 again
        self.assertEqual([(s["state"], s.get("receipt_state"), s.get("code"), s["operation_id"]) for s in steps2],
                         [("ok", "verified", None, committed),            # already done (the runner stops here)
                          ("refused", None, "effect_already_verified", committed)])     # and never claimed
        for op, extra in (("claim", {}), ("dispatch", {"lease": lease1})):
            got = self.store.effect("mac-a", redo, env2["generation"], {**e1, "op": op, **extra})
            self.assertEqual((got["state"], got.get("code")), ("refused", "effect_already_verified"))
        other = {**e1, "request": {**e1["request"], "arguments": {"op": "e1", "to": "elsewhere"}}}
        self.assertEqual(self.store.effect("mac-a", redo, env2["generation"], other).get("code"),
                         "receipt_operation_reused")           # the same key for another request: never "done"
        self.assertEqual(self.sql("SELECT count(*) AS n FROM guild.mc_action_receipts WHERE operation_id=%s",
                                  (effect_operation_id(redo, "e1"),))[0]["n"], 0)          # no second receipt
        self.assertEqual(self.sql("SELECT consumed_operations AS n FROM guild.mc_action_grants WHERE grant_id=%s",
                                  (gid,))[0]["n"], 1)          # and no grant operation used for it
        self.assertEqual(self.sql("""SELECT count(*) AS n FROM guild.mc_action_events
            WHERE operation_id=%s AND event_type='dispatched'""", (committed,))[0]["n"], 1)   # DUP-1, across the redo
        self.assertEqual(len(self.events("effect.carried", item=redo)), 1)
        self.assertIn("the 1 effect it already committed keeps its operation id and is never sent again", d["words"])
        self.assertEqual(self.item("C-17.build.redo1")["facts"]["card"]["carried"],
                         {"e1": {"operation_id": committed, "from": rid}})
        # e2 is what the redo redoes, under its own new operation id; it fails again, so the step is redone again,
        # and that redo still carries e1 from the first item.
        e2r, lease2r = self.effect_open(env2, "C-17", key="e2")
        self.assertNotEqual(effect_operation_id(redo, "e2"), effect_operation_id(rid, "e2"))
        self.assertEqual(self.finish(env2, e2r, lease2r, "failed")["state"], "ok")
        self.assertEqual(self.result(env2, status="failed", detail={"exit_code": 69})["state"], "accepted")
        (d2,) = self.waiting(self.publish(), "step_redo")
        self.assertEqual(self.item("C-17.build.redo2")["facts"]["card"]["carried"],
                         {"e1": {"operation_id": committed, "from": rid}})
        self.assertIn("the 1 effect it already committed", d2["words"])

    def test_a_declined_redo_ends_the_card(self):
        self.admit(card())
        _, env = self.claim()
        e, lease = self.effect_open(env, "C-17")
        self.finish(env, e, lease, "failed")
        self.result(env, status="failed", detail={"exit_code": 69})
        (d,) = self.waiting(self.publish(), "step_redo")
        self.assertEqual(self.store.resolve(d["decision_id"], "declined", by="robert",
                                            receipt=self.receipt_of(d, "no"))["action"], "decline")
        self.assertEqual(self.item("C-17.build.redo1")["state"], "cancelled")
        self.assertEqual([e["body"]["code"] for e in self.events("card.ended")], ["owner_declined"])

    # ── an effects_open result, accepted once its effect is decided: the card goes on ──
    def test_an_effects_open_result_is_accepted_once_its_effect_is_decided_and_the_card_goes_on(self):
        self.admit(card())
        _, env = self.claim()
        e, lease = self.effect_open(env, "C-17")
        self.assertEqual(self.result(env, artifacts=[art("patch", "b")])["code"], "effects_open")
        (first,) = self.waiting(self.publish())
        self.assertEqual((first["class"], first["yes"], first["no"]), ("unknown_resolution", "end", "leave"))
        self.assertEqual(self.finish(env, e, lease, "verified")["state"], "ok")
        doc = self.publish()
        (second,) = self.waiting(doc)
        self.assertEqual((second["reason"], second["yes"], second["no"]), ("result_reported", "accept", "leave"))
        superseded = [x for x in self.events("decision.resolved") if x["body"]["answer"] == "superseded"]
        self.assertEqual([x["body"]["decision_id"] for x in superseded], [first["decision_id"]])
        with self.assertRaisesRegex(AuthorityRefused, "decision_words_changed"):
            self.store.resolve(second["decision_id"], "approved", by="robert", receipt=self.receipt_of(first, "yes"))
        out = self.store.resolve(second["decision_id"], "approved", by="robert", receipt=self.receipt_of(second, "yes"))
        self.assertEqual((out["action"], out["state"]), ("accept", "resolved"))
        self.assertEqual(self.item("C-17.build")["state"], "completed")
        review = self.item("C-17.review")
        self.assertEqual((review["state"], review["created_by"]), ("queued", "authority"))
        self.assertEqual(self.store.resolve(second["decision_id"], "approved", by="robert",
                                            receipt=self.receipt_of(second, "yes"))["state"], "already_resolved")
        work = self.owner_work(self.publish(), "C-17")
        self.assertEqual((work["A"], work["J"]), (1, 1))

    # ── a decision on an ended item, and a reassignment proposed by MC ──
    def test_a_decision_on_an_ended_item_is_acknowledged_and_a_proposed_reassignment_arrives_alone(self):
        self.admit(card())
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])
        ans = self.store.reconcile("mac-a", env["request_id"], 1, {"child": "unverified", "checkpoint": None}, [])
        self.assertEqual(ans["code"], "process_unverified")
        (d,) = self.waiting(self.publish())
        self.assertEqual((d["yes"], d["solo"]), ("acknowledge", True))
        self.assertEqual(self.store.resolve(d["decision_id"], "approved", by="robert",
                                            receipt=self.receipt_of(d, "yes"))["action"], "acknowledge")
        self.assertEqual(self.waiting(self.publish()), [])
        # a lapsed item: MC proposes a reassignment, alone; only Robert's yes moves it
        _, env = self.claim()
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                 "WHERE request_id=%s", (env["request_id"],))
        self.store.sweep()
        p = self.store.propose_reassignment(env["request_id"], "mac-a", by="mc")
        doc = self.publish()
        (r,) = self.waiting(doc, "reassignment")
        self.assertEqual((r["decision_id"], r["solo"]), (p["decision_id"], True))
        self.assertIn(f"decision:{p['decision_id']}", [n["key"] for n in doc["notices"]])
        self.assertEqual(self.item("C-17.review")["state"], "lapsed")
        out = self.store.resolve(r["decision_id"], "approved", by="robert", receipt=self.receipt_of(r, "yes"))
        self.assertEqual((out["action"], self.item("C-17.review")["state"]), ("reassign", "queued"))
        work = self.owner_work(self.publish(), "C-17")
        self.assertEqual((work["J"], work["C"]), (2, 0))     # the unverified process and the reassignment, each noticed

    # ── blocked host tasks (S1-D N-E3) ──
    def test_a_blocked_host_task_is_shown_with_its_age_and_one_notice_until_the_restart_clears_it(self):
        rid = str(uuid4())
        blocked = [{"item_id": rid, "generation": 1, "code": "authority_error:audit_event_not_written",
                    "since": self.db_now().isoformat()}]
        self.beat(blocked=blocked)
        self.beat(blocked=blocked)                                   # the same facts: audited once
        self.assertEqual(sum(1 for e in self.events("host.facts") if e["body"].get("blocked")), 1)
        doc = self.publish()
        (host,) = doc["hosts"]
        self.assertEqual((host["blocked"][0]["code"], host["blocked"][0]["item_id"]),
                         ("authority_error:audit_event_not_written", rid))
        self.assertEqual(len([n for n in doc["notices"] if n["rule"] == "blocked"]), 1)
        self.beat()                                                   # an operator restart: no task blocked
        self.assertEqual(self.publish()["hosts"][0]["blocked"], [])
        self.assertEqual(self.events("host.facts")[-1]["body"]["changed"], ["blocked"])

    # ── Codex part 2 P2-3: the supervisor's outbox, on the real store ──
    def test_p2_3_a_watch_notice_whose_send_failed_is_kept_across_a_restart_and_told_once(self):
        """Telegram is down while staging goes silent and comes back: both transitions are written with their notices
        (one transaction each), nothing is consumed, each failed send is counted; after a restart of the supervisor
        and Telegram's return, both are told once, in order, in one message, and marked sent. A runtime link error
        goes through the same outbox."""
        prod, bot = Store(self.connect), FakeNotifier(self.dir / "notify-prod.jsonl")
        armed = {"timeout_s": 45, "hosts": ["staging-1"]}
        sup = Supervisor(prod, bot, GitHub(), chat_id=OWNER, user_id=OWNER, watch=armed)
        beat = {"phase": "heartbeat", "host_id": "staging-1",
                "view": {"as_of": time.time(), "authority_answering": True, "sweeper_age_s": 4, "lapsed_always_on": 0}}
        prod.worker(beat, credential="watch:staging-1")
        self.sql("UPDATE guild.supervision_watch SET last_at = last_at - interval '60 seconds'")
        bot.fail_next = 3
        sup.watch_tick()
        sup.watch_tick()
        self.assertEqual(bot.lines(), [])
        (row,) = self.sql("SELECT key, attempts, last_error, sent_at FROM guild.supervision_outbox")
        self.assertEqual((row["key"].split(":")[0], row["attempts"], row["last_error"], row["sent_at"]),
                         ("staging-silent", 2, "telegram_down", None))
        self.assertIsNotNone(self.sql("SELECT silent_since FROM guild.supervision_watch")[0]["silent_since"])
        prod.worker(beat, credential="watch:staging-1")               # back, while Telegram is still down
        sup.watch_tick()
        self.assertEqual(bot.lines(), [])
        again = Supervisor(Store(self.connect), bot, GitHub(), chat_id=OWNER, user_id=OWNER, watch=armed)  # restart
        again.watch_tick()
        again.watch_tick()
        (told,) = [m["text"] for m in bot.lines()]
        self.assertLess(told.index("has been silent"), told.index("is back"))
        rows = self.sql("SELECT key, sent_at FROM guild.supervision_outbox ORDER BY queued_at")
        self.assertEqual([(r["key"].split(":")[0], r["sent_at"] is not None) for r in rows],
                         [("staging-silent", True), ("staging-back", True)])
        bot.fail_next = 1
        again._runtime_failed(["apply:boom"])
        again._runtime_failed(["apply:boom"])
        again._runtime_failed(["apply:boom"])
        self.assertEqual([m["text"].split(" (")[1].split(")")[0] for m in bot.lines()[1:]], ["apply:boom"])

    # ── DUP-8, R4.6-b, T-PW1: production's identities write nothing in dev; the watch only beats ──
    def test_t_pw1_prod_watch_test_silent_once_back_once_and_the_watch_credential_reaches_nothing_else(self):
        dev_events = len(self.events())
        prod = Store(self.connect)                                    # production's check-in code, as prod-watch-test
        notify_prod = FakeNotifier(self.dir / "notify-prod.jsonl")
        sup = Supervisor(prod, notify_prod, GitHub(), chat_id=OWNER, user_id=OWNER,
                         watch={"timeout_s": 45, "hosts": ["staging-1"]})       # armed by its credential (§5.6)
        beat = {"phase": "heartbeat", "host_id": "staging-1",
                "view": {"as_of": time.time(), "authority_answering": True, "sweeper_age_s": 4, "lapsed_always_on": 0}}
        sup.watch_tick()
        self.assertEqual(notify_prod.lines(), [])                     # not armed before its first heartbeat
        self.assertEqual(prod.worker(beat, credential="watch:staging-1"), {"recorded": True})
        sup.watch_tick()
        self.assertEqual(notify_prod.lines(), [])
        self.sql("UPDATE guild.supervision_watch SET last_at = last_at - interval '60 seconds'")
        sup.watch_tick()                                              # told on the tick, not at the next check-in
        sup.watch_tick()                                              # once
        self.assertEqual([m["text"].split("\n")[3].split(" has been")[0] for m in notify_prod.lines()],
                         ["• Staging staging-1"])
        prod.worker(beat, credential="watch:staging-1")
        sup.watch_tick()
        sup.watch_tick()
        texts = [m["text"] for m in notify_prod.lines()]
        self.assertEqual(len(texts), 2)
        self.assertIn("Staging staging-1 is back", texts[1])
        # Removing the credential alone disarms it: its host is no longer watched, however silent it goes.
        sup.watch = {"timeout_s": 45, "hosts": []}
        self.sql("UPDATE guild.supervision_watch SET last_at = last_at - interval '600 seconds'")
        sup.watch_tick()
        self.assertEqual(len(notify_prod.lines()), 2)
        # The watch credential can only beat: no pull, no report, no ack; the worker credential cannot beat.
        for body in ({"phase": "pull"}, {"phase": "report", "stops": []}, {"phase": "ack", "decisions": []}):
            with self.assertRaises(Refused):
                prod.worker(body, credential="watch:staging-1")
        with self.assertRaises(Refused):
            prod.worker(beat, credential="worker")
        with self.assertRaises(Refused):
            prod.worker({**beat, "host_id": "staging-2"}, credential="watch:staging-1")
        # Its token reads nothing in dev: the snapshot's read path takes only read credentials.
        token = "w" * 48
        readers = {"collector": {"sha256": hashlib.sha256(b"o" * 48).hexdigest(), "role": "observer"}}
        self.assertEqual(S.read_request({"Authorization": "Bearer " + token}, readers=readers,
                                        provide=self.store.snapshot)[0], 401)
        # R4.6-b: every call recorded its credential's contact, pull included.
        prod.worker({"phase": "pull"}, credential="worker")
        contacts = prod.contacts()
        self.assertEqual(contacts["worker"]["last_phase"], "pull")
        self.assertEqual(contacts["watch:staging-1"]["calls"], 6)    # 2 beats, 3 refused phases, 1 wrong host
        # DUP-8: no dev authority event was written by any production or watch identity.
        self.assertEqual(len(self.events()), dev_events)

    def test_dup8_every_authority_event_is_the_authoritys_a_hosts_or_the_owners(self):
        self.test_t15_own6_three_routine_decisions_share_one_notice_one_yes_and_an_unknown_waits_alone()
        before = len(self.events())
        self.sup_store.worker({"phase": "report", "stops": []}, credential="worker")
        self.sup_store.worker({"phase": "pull"}, credential="worker")
        self.assertEqual(len(self.events()), before)                  # the sync and the relay write no authority event
        hosts = {r["host_id"] for r in self.sql("SELECT host_id FROM guild.mc_delegation_runners WHERE protocol=2 "
                                                "AND host_id IS NOT NULL")}
        for e in self.events():
            self.assertIn(e["source"], SOURCES)
            if e["source"] == "host":
                self.assertIn(e["host_id"], hosts)
            if e["source"] == "owner" and e["kind"] == "decision.resolved":
                self.assertEqual(e["body"]["by"], "robert")

    # ── R4.6-b: a rule reading a view refuses when the view is stale ──
    def test_view_stale_rules_reading_a_stale_view_do_not_fire(self):
        sync = Store(self.connect)
        sync.worker({"phase": "report", "stops": [
            {"report_key": "stop-00000001", "agent": "claude-code", "session": "sess-abcd", "state": "unreported",
             "stopped_at": time.time() - 3600}]}, credential="worker")
        now = self.db_now()
        _, items = W.check_in(sync, GitHub(), now=now, sync_view_bound_s=600)
        self.assertIn("stalled:1", [i["key"] for i in items])        # fresh: the rule fires
        self.sql("UPDATE guild.supervision_contacts SET last_at = last_at - interval '20 minutes'")
        self.sql("UPDATE guild.supervision_stops SET escalated_at = NULL")
        _, items = W.check_in(sync, GitHub(), now=now, sync_view_bound_s=600)
        self.assertEqual([i["key"].split(":")[0] for i in items], ["view-stale"])
        # the runtime's published state, read as a view: past its bound, nothing fires and nothing is sent
        self.admit(card())
        self.store.publish(self.dir / "state")
        path = self.dir / "state" / "state.json"
        old = json.loads(path.read_text())
        old["as_of"] = (self.db_now() - timedelta(seconds=600)).isoformat()
        path.write_text(json.dumps(old))
        link = RuntimeLink(self.store, owner="robert", view_bound_s=60, source=lambda: S.read_state_file(path))
        items, running, runners, errors = link.check_in()
        self.assertEqual(([i["key"].split(":")[0] for i in items], running, errors), (["view-stale"], [], []))
        self.assertEqual(link.deliver(self.sup_store, self.notify, OWNER), [])

    def test_the_mac_sync_never_pulls_or_acks_a_runtime_decision(self):
        did = str(uuid4())
        row = self.sup_store.runtime_decision(did, "Admit card C-1 step review: words long enough.", notice="x",
                                              position=1, solo=False, environment="dev")
        self.sup_store.decide(row["id"], "approved", {"channel": "guild", "text": "approve D-1"},
                              text_sha256=row["text_sha256"])
        self.assertEqual(self.sup_store.worker({"phase": "pull"})["decisions"], [])
        self.sup_store.worker({"phase": "ack", "decisions": ["rt-" + did]})
        self.assertIsNone(self.sup_store.by_code(row["code"])["delivered_at"])
        with self.assertRaises(Refused):                              # another requester's key, or other words
            self.sup_store.runtime_decision(did, "Other words, long enough to be a decision.", notice="x",
                                            position=1, solo=False, environment="dev")

    # ── the review's fixes (REVIEW_independent_S1-G.md) ──
    def test_sf2_a_decision_the_authority_withdrew_is_closed_and_told_and_an_answer_to_it_does_nothing(self):
        admitted = self.admit(card())
        self.sql("UPDATE guild.mc_action_grants SET consumed_operations = max_operations")
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])         # the review step waits on Robert: one routine decision
        self.publish()
        self.sup.runtime_tick()
        (sent,) = self.notify.lines()
        self.store.revoke(admitted["grant_id"], by="robert", note="stop this card")    # the authority withdraws it
        self.sup.runtime_tick()
        told = self.notify.lines()[-1]
        self.assertEqual(told["reply_to"], sent["message_id"])
        self.assertTrue(told["text"].endswith("is no longer open: the runtime withdrew it (grant_revoked). "
                                              "Nothing to answer."), told["text"])
        self.assertEqual([r["state"] for r in self.sup_store.by_batch(sent["message_id"])], ["expired"])
        self.assertEqual(self.sup_store.pending(), [])          # no longer counted as waiting
        self.notify.reply("yes", sent["message_id"])
        self.sup.read_answers(wait=0)
        self.assertIn("is no longer open (the runtime withdrew it (grant_revoked)); nothing changed.",
                      self.notify.lines()[-1]["text"])
        self.assertEqual(self.sql("""SELECT count(*) AS n FROM guild.supervision_decisions
            WHERE state IN ('approved', 'declined')""")[0]["n"], 0)

    def test_sf2_a_refused_answer_is_told_and_its_decision_comes_back_with_a_fresh_d_code(self):
        # An authority whose catalogue lacks the owner's own admission rule: Robert's yes is refused there.
        narrow = DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH,
                                 grants=GrantStore({**C.grant_rows(), SINK: CapabilityRule(1, False, True)}))
        sup = Supervisor(self.sup_store, self.notify, GitHub(), chat_id=OWNER, user_id=OWNER,
                         runtime=RuntimeLink(narrow, owner="robert", view_bound_s=60))
        self.admit(card())
        self.sql("UPDATE guild.mc_action_grants SET consumed_operations = max_operations")
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])
        self.publish()
        sup.runtime_tick()
        (first,) = self.notify.lines()
        self.notify.reply("yes", first["message_id"])
        sup.read_answers(wait=0)
        sup.runtime_tick()
        texts = self.runtime_texts()
        again = next(m for m in self.notify.lines() if "on its own" in m["text"])
        self.assertTrue(any(t.startswith("Not applied: D-1 was refused (grant_capability_unavailable); nothing was "
                                         "done. It still waits, as D-2.") for t in texts), texts)
        self.assertEqual(self.item("C-17.review")["state"], "proposed")
        # The fresh D-code is answered through the full owner path: applied, and Robert is told.
        self.notify.reply("yes", again["message_id"])
        self.sup.read_answers(wait=0)
        self.sup.runtime_tick()
        self.assertEqual(self.item("C-17.review")["state"], "queued")
        self.assertTrue(self.runtime_texts()[-1].startswith("Applied: D-2 approved: the step is admitted"))
        (waiting,) = [d for d in self.waiting(self.publish()) if d["class"] == "step_admission"] or [None]
        self.assertIsNone(waiting)

    def test_p2_4_a_fresh_d_code_whose_send_failed_goes_out_after_a_restart_the_same_code(self):
        """Codex part 2 P2-4 on the real store: Robert's yes is refused, and every send of that tick fails (the fresh
        D-code's among them). After a restart of the supervisor, the same D-2 goes out alone, once; no D-3 is made;
        and it is answered through the full owner path."""
        narrow = DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH,
                                 grants=GrantStore({**C.grant_rows(), SINK: CapabilityRule(1, False, True)}))

        def supervisor():
            return Supervisor(Store(self.connect), self.notify, GitHub(), chat_id=OWNER, user_id=OWNER,
                              runtime=RuntimeLink(narrow, owner="robert", view_bound_s=60))
        sup = supervisor()
        self.admit(card())
        self.sql("UPDATE guild.mc_action_grants SET consumed_operations = max_operations")
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])
        self.publish()
        sup.runtime_tick()
        (first,) = self.notify.lines()
        self.notify.reply("yes", first["message_id"])
        sup.read_answers(wait=0)
        self.notify.fail_next = 50                                   # Telegram fails for the whole tick
        sup.runtime_tick()
        self.notify.fail_next = 0
        self.assertEqual(len(self.notify.lines()), 1)
        rows = self.sql("SELECT id, state FROM guild.supervision_decisions ORDER BY id")
        self.assertEqual([r["state"] for r in rows], ["approved", "new"])       # D-2 kept, unsent
        again = supervisor()                                         # a restart
        again.runtime_tick()
        again.runtime_tick()
        solo = [m for m in self.notify.lines() if "on its own" in m["text"]]     # D-1 went out batched; D-2 alone
        self.assertEqual([m["text"].split("\n")[0] for m in solo], ["Decision D-2 waiting on you, on its own"])
        self.assertEqual([r["state"] for r in self.sql("SELECT state FROM guild.supervision_decisions ORDER BY id")],
                         ["approved", "sent"])                       # no third code
        self.notify.reply("yes", solo[0]["message_id"])
        again.read_answers(wait=0)
        self.sup.runtime_tick()
        self.assertEqual(self.item("C-17.review")["state"], "queued")

    def test_delta_the_fresh_d_code_is_written_before_the_answer_is_applied_and_only_once(self):
        """Codex's fix1 delta check, on the real store: Robert's yes is refused; the fresh D-code's write fails, then
        (after a restart) it is written and sent but the process stops before the answer is marked applied; after
        another restart the answer is applied. Never an answer applied without its fresh D-code; one fresh code,
        sent once."""
        narrow = DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH,
                                 grants=GrantStore({**C.grant_rows(), SINK: CapabilityRule(1, False, True)}))

        class Faulty(Store):
            fail_write = fail_mark = 0

            def runtime_decision(self, *args, **kwargs):
                if kwargs.get("reissue") and self.fail_write:
                    self.fail_write -= 1
                    raise RuntimeError("database_down")
                return super().runtime_decision(*args, **kwargs)

            def mark_applied(self, decision_id):
                if self.fail_mark:
                    self.fail_mark -= 1
                    raise RuntimeError("process_stopped")
                return super().mark_applied(decision_id)
        store = Faulty(self.connect)

        def supervisor():
            return Supervisor(store, self.notify, GitHub(), chat_id=OWNER, user_id=OWNER,
                              runtime=RuntimeLink(narrow, owner="robert", view_bound_s=60))

        def rows():
            return [(r["state"], r["delivered_at"] is not None)
                    for r in self.sql("SELECT state, delivered_at FROM guild.supervision_decisions ORDER BY id")]
        sup = supervisor()
        self.admit(card())
        self.sql("UPDATE guild.mc_action_grants SET consumed_operations = max_operations")
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])
        self.publish()
        sup.runtime_tick()
        (first,) = self.notify.lines()
        self.notify.reply("yes", first["message_id"])
        sup.read_answers(wait=0)
        store.fail_write = 1
        sup.runtime_tick()                                           # refused; the fresh D-code's write fails
        self.assertEqual(rows(), [("approved", False)])              # the answer is not consumed
        store.fail_mark = 1
        supervisor().runtime_tick()                                  # a restart: refused again; D-2 written and sent,
        self.assertEqual(rows(), [("approved", False), ("sent", False)])   # then a stop before the mark
        last = supervisor()                                          # another restart
        last.runtime_tick()
        last.runtime_tick()
        self.assertEqual(rows(), [("approved", True), ("sent", False)])    # applied; one fresh code only
        # Its number is its row id; each deliver's INSERT ... ON CONFLICT spends a sequence value, so it may skip one.
        fresh = "D-{}".format(self.sql("SELECT max(id) AS id FROM guild.supervision_decisions")[0]["id"])
        solo = [m["text"].split("\n")[0] for m in self.notify.lines() if "on its own" in m["text"]]
        self.assertEqual(solo, [f"Decision {fresh} waiting on you, on its own"])
        # D-1 was replaced before the stop: completed here, never taken to the authority again (fix2 re-check).
        self.assertTrue(any(t.startswith(f"Not applied: D-1 was refused and replaced by {fresh}; nothing was done.")
                            for t in self.runtime_texts()), self.runtime_texts())

    # ── Codex's fix2 re-check: once a fresh D-code exists, the answer it replaced never reaches the authority again ──
    def recheck_setup(self, **faults):
        """Robert's yes to a step admission that the narrow authority refuses (its capability missing), through a
        supervision store with the given faults (fail_mark: the next mark fails; stop_after_write: the next fresh
        D-code's write commits and then the process stops). Returns (store, the first message)."""
        narrow = DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH,
                                 grants=GrantStore({**C.grant_rows(), SINK: CapabilityRule(1, False, True)}))

        class Faulty(Store):
            fail_mark = stop_after_write = 0

            def runtime_decision(self, *args, **kwargs):
                out = super().runtime_decision(*args, **kwargs)
                if kwargs.get("reissue") and self.stop_after_write:
                    self.stop_after_write -= 1
                    raise SystemExit("process_stopped")          # the whole process stops: nothing else runs
                return out

            def mark_applied(self, decision_id):
                if self.fail_mark:
                    self.fail_mark -= 1
                    raise RuntimeError("process_stopped")
                return super().mark_applied(decision_id)
        store = Faulty(self.connect)
        self.narrow_sup = Supervisor(store, self.notify, GitHub(), chat_id=OWNER, user_id=OWNER,
                                     runtime=RuntimeLink(narrow, owner="robert", view_bound_s=60))
        self.admit(card())
        self.sql("UPDATE guild.mc_action_grants SET consumed_operations = max_operations")
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])
        self.publish()
        self.narrow_sup.runtime_tick()
        (first,) = self.notify.lines()
        self.notify.reply("yes", first["message_id"])
        self.narrow_sup.read_answers(wait=0)
        for k, v in faults.items():
            setattr(store, k, v)
        return store, first

    def fresh_code(self):
        return "D-{}".format(self.sql("SELECT max(id) AS id FROM guild.supervision_decisions")[0]["id"])

    def test_recheck_an_old_yes_never_overrides_a_newer_no(self):
        """Codex's reproduction, as a regression: the yes is refused, the fresh D-code is written and sent, and the
        process stops before the yes is marked applied; Robert says no to the fresh code; after a restart, with the
        capability back, the old yes must not admit the step. His newer no is the one taken."""
        self.recheck_setup(fail_mark=1)
        self.narrow_sup.runtime_tick()
        fresh = next(m for m in self.notify.lines() if "on its own" in m["text"])
        self.notify.reply("no", fresh["message_id"])
        self.narrow_sup.read_answers(wait=0)
        self.assertEqual([r["state"] for r in self.sql("SELECT state FROM guild.supervision_decisions ORDER BY id")],
                         ["approved", "declined"])
        self.sup.runtime_tick()                                      # a restart, the ordinary catalogue
        self.assertEqual(self.item("C-17.review")["state"], "cancelled")      # the no, never the old yes
        code = self.fresh_code()
        texts = self.runtime_texts()
        self.assertTrue(any(t.startswith(f"Not applied: D-1 was refused and replaced by {code}") for t in texts),
                        texts)
        self.assertTrue(any(t.startswith(f"Applied: {code} declined") for t in texts), texts)
        self.assertEqual(self.sql("""SELECT count(*) AS n FROM guild.supervision_decisions
            WHERE delivered_at IS NULL""")[0]["n"], 0)

    def test_recheck_a_stop_after_the_fresh_code_is_written_before_it_is_sent_asks_robert_again(self):
        """The fresh D-code's write commits and the process stops before anything is sent or marked. After a restart,
        with the capability back, the old yes is completed here and never taken to the authority again; the fresh code
        goes out once, and only Robert's answer to it admits the step."""
        self.recheck_setup(stop_after_write=1)
        with self.assertRaises(SystemExit):
            self.narrow_sup.runtime_tick()
        self.assertEqual([(r["state"], r["delivered_at"] is None) for r in
                          self.sql("SELECT state, delivered_at FROM guild.supervision_decisions ORDER BY id")],
                         [("approved", True), ("new", True)])
        self.assertEqual([m for m in self.notify.lines() if "on its own" in m["text"]], [])
        self.sup.runtime_tick()                                      # a restart, the ordinary catalogue
        self.sup.runtime_tick()
        self.assertEqual(self.item("C-17.review")["state"], "proposed")       # the old yes admitted nothing
        code = self.fresh_code()
        solo = [m for m in self.notify.lines() if "on its own" in m["text"]]
        self.assertEqual([m["text"].split("\n")[0] for m in solo], [f"Decision {code} waiting on you, on its own"])
        self.notify.reply("yes", solo[0]["message_id"])
        self.sup.read_answers(wait=0)
        self.sup.runtime_tick()
        self.assertEqual(self.item("C-17.review")["state"], "queued")

    def test_sf6_a_reassignment_answer_is_bound_to_the_generation_its_words_name(self):
        self.admit(card())
        _, env = self.claim()
        rid = env["request_id"]
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                 "WHERE request_id=%s", (rid,))
        self.store.sweep()
        self.store.propose_reassignment(rid, "mac-a", by="mc")
        (r,) = self.waiting(self.publish(), "reassignment")
        self.assertIn("generation 2", r["words"])
        # The owner comes back and resumes at generation 2, then lapses again, before Robert answers.
        ans = self.store.reconcile("mac-a", rid, 1, {"child": "stopped", "checkpoint": None}, [])
        self.assertEqual((ans["answer"], ans["generation"]), ("resume", 2))
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                 "WHERE request_id=%s", (rid,))
        self.store.sweep()
        out = self.store.resolve(r["decision_id"], "approved", by="robert", receipt=self.receipt_of(r, "yes"))
        self.assertEqual((out["state"], out["generation_now"]), ("stale", 2))
        self.assertEqual((self.item("C-17.build")["generation"], self.item("C-17.build")["state"]), (2, "lapsed"))
        (fresh,) = self.waiting(self.publish(), "reassignment")
        self.assertEqual(fresh["decision_id"], out["fresh_decision_id"])
        self.assertIn("generation 3", fresh["words"])
        done = self.store.resolve(fresh["decision_id"], "approved", by="robert", receipt=self.receipt_of(fresh, "yes"))
        self.assertEqual((done["state"], done["action"]), ("resolved", "reassign"))
        self.assertEqual((self.item("C-17.build")["generation"], self.item("C-17.build")["state"]), (3, "queued"))
        self.assertEqual(self.owner_work(self.publish(), "C-17")["J"], 1)          # the stale answer is not a J

    def test_sf5_a_cleared_notice_still_reaches_robert_and_a_dead_sweeper_is_told_once(self):
        blocked = [{"item_id": str(uuid4()), "generation": 1, "code": "authority_error:audit_event_not_written",
                    "since": self.db_now().isoformat()}]
        self.beat(blocked=blocked)
        self.publish()                                          # the sweep raises it
        self.beat()                                             # an operator restart clears it before anyone looked
        self.sup.runtime_tick()
        told = [t for t in self.runtime_texts() if t.startswith("MC: the runtime\n")]
        self.assertEqual(len(told), 1)
        self.assertIn("blocked its task", told[0])
        self.sup.runtime_tick()
        self.assertEqual(len([t for t in self.runtime_texts() if t.startswith("MC: the runtime\n")]), 1)
        # The sweeper dies: nothing raises its own notice, so the link reads it from the snapshot, once.
        self.sql("UPDATE guild.mc_delegation_runners SET seen_at = seen_at - interval '120 seconds' "
                 "WHERE runner_id LIKE 'sweeper/%%'")
        self.sup.runtime_tick()
        self.sup.runtime_tick()
        told = [t for t in self.runtime_texts() if t.startswith("MC: the runtime\n")]
        self.assertEqual(len(told), 2)
        self.assertIn("sweeper has not run since", told[1])

    def test_sf7_contacts_before_migration_003_never_break_the_mac_sync(self):
        import os
        from flask import Flask
        from minimoi_portal.guild_ui import supervision
        os.environ.setdefault("DATABASE_URL", "postgresql://unused")
        self.sql("ALTER TABLE guild.supervision_contacts RENAME TO supervision_contacts_hidden")
        try:
            sync = Store(self.connect)
            self.assertEqual(sync.worker({"phase": "pull"}, credential="worker"), {"decisions": [], "nudges": []})
            self.assertFalse(sync.contacts_ready())
            token = "t" * 48
            env = {"MINIMOI_SUPERVISION": "1", "MINIMOI_SUPERVISION_CONTACTS": "1",
                   "MINIMOI_SUPERVISION_WORKER_TOKEN_SHA256": hashlib.sha256(token.encode()).hexdigest()}
            app = Flask("t")
            self.assertTrue(supervision.attach(app, env, store_factory=lambda: sync))   # refused to name callers
            ok = app.test_client().post(supervision.PATH, json={"phase": "pull"},
                                        headers={"Authorization": "Bearer " + token})
            self.assertEqual((ok.status_code, ok.get_json()), (200, {"decisions": [], "nudges": []}))
        finally:
            self.sql("ALTER TABLE guild.supervision_contacts_hidden RENAME TO supervision_contacts")

    def test_n2_a_solo_runtime_decision_never_joins_a_batched_message(self):
        solo = self.sup_store.runtime_decision(str(uuid4()), "An unknown that must arrive alone: yes ends it.",
                                               notice="decision:x", position=1, solo=True, environment="dev")
        with self.assertRaises(Refused):
            self.sup_store.add_batch(5555, [solo["id"]])
        self.assertEqual(self.sup_store.by_batch(5555), [])


    # ── the closure check's follow-up (REVIEW_independent_S1-G.md, "Closure check") ──
    def test_c_sf1_a_rule_notice_lost_to_a_failed_send_goes_out_next_tick_and_never_twice(self):
        blocked = [{"item_id": str(uuid4()), "generation": 1, "code": "authority_error:audit_event_not_written",
                    "since": self.db_now().isoformat()}]
        self.beat(blocked=blocked)
        self.publish()                                          # the sweep raises it
        self.notify.fail_next = 1                               # Telegram fails at the first try
        self.sup.runtime_tick()

        def told():
            return [t for t in self.runtime_texts() if t.startswith("MC: the runtime\n")]
        self.assertEqual(told(), [])
        self.assertEqual(self.sql("SELECT key FROM guild.supervision_alerts"), [])   # the notice is not marked
        self.assertEqual([(r["key"].split(":")[0], r["sent_at"] is not None)          # only the failure is told
                          for r in self.sql("SELECT key, sent_at FROM guild.supervision_outbox")],
                         [("runtime-link-error", True)])          # (through the supervisor's outbox, P2-3)
        self.sup.runtime_tick()
        self.assertEqual(len(told()), 1)
        self.assertIn("blocked its task", told()[0])
        # A restart reads the outbox from the start, whatever its age, and never tells it twice (DUP-7).
        restarted = Supervisor(self.sup_store, self.notify, GitHub(), chat_id=OWNER, user_id=OWNER,
                               runtime=RuntimeLink(self.store, owner="robert", view_bound_s=60))
        restarted.runtime_tick()
        restarted.runtime_tick()
        self.assertEqual(len(told()), 1)

    def test_c_n1_an_answer_applied_just_before_a_restart_is_told_as_applied(self):
        self.admit(card())
        self.sql("UPDATE guild.mc_action_grants SET consumed_operations = max_operations")
        _, env = self.claim()
        self.result(env, artifacts=[art("patch", "b")])         # the review step waits on Robert: one decision
        self.publish()
        self.sup.runtime_tick()
        (sent,) = self.notify.lines()
        self.notify.reply("yes", sent["message_id"])
        self.sup.read_answers(wait=0)

        def killed(decision_id):
            raise RuntimeError("killed")
        self.sup_store.mark_applied = killed                    # the supervisor dies after the authority applied it
        self.sup.runtime_tick()
        del self.sup_store.mark_applied
        self.assertEqual(self.item("C-17.review")["state"], "queued")
        restarted = Supervisor(self.sup_store, self.notify, GitHub(), chat_id=OWNER, user_id=OWNER,
                               runtime=RuntimeLink(self.store, owner="robert", view_bound_s=60))
        restarted.runtime_tick()
        restarted.runtime_tick()
        texts = self.runtime_texts()
        told = [t for t in texts if t.startswith(("Applied:", "Not applied:", "Recorded:"))]
        self.assertEqual(told, ["Applied: D-1 approved: the step is admitted under its own approval and queued."],
                         texts)
        self.assertIsNotNone(self.sup_store.by_code("D-1")["announced_at"])

    def test_c_n2_a_no_to_a_stale_reassignment_closes_it_and_asks_nothing_again(self):
        self.admit(card())
        _, env = self.claim()
        rid = env["request_id"]
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                 "WHERE request_id=%s", (rid,))
        self.store.sweep()
        self.store.propose_reassignment(rid, "mac-a", by="mc")
        (r,) = self.waiting(self.publish(), "reassignment")
        ans = self.store.reconcile("mac-a", rid, 1, {"child": "stopped", "checkpoint": None}, [])
        self.assertEqual((ans["answer"], ans["generation"]), ("resume", 2))
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                 "WHERE request_id=%s", (rid,))
        self.store.sweep()
        out = self.store.resolve(r["decision_id"], "declined", by="robert", receipt=self.receipt_of(r, "no"))
        self.assertEqual((out["state"], out["fresh_decision_id"]), ("stale", None))
        self.assertEqual(self.waiting(self.publish(), "reassignment"), [])     # his "no" is not asked again
        self.assertEqual((self.item("C-17.build")["generation"], self.item("C-17.build")["state"]), (2, "lapsed"))
        self.assertEqual(self.store.decisions_status([r["decision_id"]])[r["decision_id"]]["answer"], "stale")
        self.store.propose_reassignment(rid, "mac-a", by="mc")                # the usual proposal still raises one
        (fresh,) = self.waiting(self.publish(), "reassignment")
        self.assertIn("generation 3", fresh["words"])

    def test_c_n3_a_stalls_review_request_is_withdrawn_and_told_once_when_its_item_ends(self):
        self.admit(card())
        _, env = self.claim()
        self.progress(env, "s1")
        self.backdate(env["request_id"], 200)                   # no progress for 200 s; build timeout 180 s
        self.assertTrue(self.store.renew(env["request_id"], 1, protocol=2, runner_id="mac-a"))
        (review,) = self.waiting(self.publish(), "review_request")
        did = review["decision_id"]
        self.sup.runtime_tick()                                 # its D-code goes out
        (d_code,) = self.sup_store.runtime_rows(did)
        self.assertEqual(d_code["state"], "sent")
        self.result(env, artifacts=[art("patch", "a")])        # the item completes
        self.publish()
        st = self.store.decisions_status([did])[did]
        self.assertEqual((st["open"], st["answer"], st["code"]), (False, "withdrawn", "item_ended"))
        self.sup.runtime_tick()
        self.sup.runtime_tick()
        code = d_code["code"]
        told = [t for t in self.runtime_texts() if t.startswith(f"{code} is no longer open")]
        self.assertEqual(told, [f"{code} is no longer open: its item has ended (completed). Nothing to answer."])
        self.assertEqual(self.sup_store.runtime_rows(did)[0]["state"], "expired")
        self.assertNotIn(did, [d["decision_id"] for d in self.waiting(self.publish())])
        before = len(self.events("decision.resolved"))
        self.publish()
        self.assertEqual(len(self.events("decision.resolved")), before)     # once

if __name__ == "__main__":
    unittest.main()

