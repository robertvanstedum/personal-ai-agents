"""The agent census at the authority on a real PostgreSQL (host runtime spec v0.5 §6.6, R3.2-a, R3.2-b; CEN-2, CEN-3,
CEN-4, CEN-6, T18e, N3): census events ride the heartbeat and are applied once each into guild.mc_delegation_events
(no other table); they cause no authority transition and no refusal, checked against the audit; the agents view is
derived at read time from them and each host's contact; and retention drops census events only, never an authority
event (grant.step_bound among them). Opt-in: a disposable mc12_test_* database
(tests/minimoi_actions/postgres_fixture_support.py)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))
sys.path.insert(0, str(ROOT / "tests" / "mc_delegation_dispatcher"))

from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

import test_cards_postgres as CP  # noqa: E402
from core.mc_delegation_dispatcher.store import Authority, DispatcherStore  # noqa: E402
from core.mc_delegation_runner import census as C  # noqa: E402

AUTH = Authority.parse(CP.TEST_VALUES)
CAPS = CP.CAPS
RUNTIME = C.RUNTIME_SOURCE


def ev(kind, body, *, at=None, event_id=None):
    return C.check_event({"event_id": event_id or str(uuid4()), "kind": kind, "body": body,
                          "at": (at or datetime.now(timezone.utc)).isoformat()})


def root(aid, item=None):
    return C.start(aid, source=RUNTIME, verified=True, children="exposed", vendor="minimoi", model="fakework/1",
                   role="build", item_id=item, start_path="runtime", route="test",
                   transcript_ref=C.not_kept("fakework_keeps_no_session"))


def sub(aid, parent):
    return C.start(aid, parent_id=parent, source="fakework.spawn", verified=True, children="not_exposed",
                   vendor="minimoi", role="explore")


def ago(seconds):
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class CensusAtTheAuthority(PostgresFixture, unittest.TestCase):
    migrations = CP.MIGRATIONS

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations, guild.mc_action_grant_consumptions, guild.mc_action_grants,
                guild.mc_action_events, guild.mc_action_receipts CASCADE""")
        self.store = DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH,
                                     grants=CP.GRANTS)

    def sql(self, query, args=()):
        from psycopg2.extras import RealDictCursor
        with self.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, args)
            return cur.fetchall() if cur.description else None

    def beat(self, census=None, runner="mac-a", store=None):
        kw = {} if census is None else {"census": census}
        return (store or self.store).heartbeat(runner, "online", CAPS, protocol=2, facts=CP.facts(runner), **kw)

    def census_rows(self):
        return self.sql("""SELECT * FROM guild.mc_delegation_events WHERE kind LIKE 'census.%%' ORDER BY seq""")

    def authority_audit(self):
        return [(str(r["event_id"]), r["kind"], r["source"], r["item_id"], r["generation"], r["body"])
                for r in self.sql("""SELECT * FROM guild.mc_delegation_events WHERE kind NOT LIKE 'census.%%'
                                     ORDER BY seq""")]

    def agents(self):
        return {r["agent_id"]: r for r in self.store.agents()}

    # ── CEN-2: each event applied once, under the id the host wrote it with ──
    def test_cen2_each_census_event_is_applied_once_and_a_replay_answers_already_applied(self):
        self.beat()
        batch = [ev("census.start", root("runtime:t-g1")), ev("census.start", sub("runtime:t-g1/a", "runtime:t-g1")),
                 ev("census.end", C.end("runtime:t-g1/a", source="fakework.spawn", state="done", reason="exit_0"))]
        got = self.beat(batch)
        self.assertEqual(got["state"], "recorded")
        self.assertEqual([a["state"] for a in got["census"]["events"]], ["applied"] * 3)
        again = self.beat(batch)                                                   # a replay, byte for byte
        self.assertEqual([a["state"] for a in again["census"]["events"]], ["already_applied"] * 3)
        rows = self.census_rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual({(r["source"], r["item_id"], r["generation"], r["host_id"]) for r in rows},
                         {("host", None, None, "mac-a")})                           # no item's audit holds one
        self.assertEqual(sorted(str(r["event_id"]) for r in rows), sorted(e["event_id"] for e in batch))
        changed = {**batch[0], "body": {**batch[0]["body"], "role": "review"}}
        facts_id = str(self.sql("SELECT event_id FROM guild.mc_delegation_events WHERE kind='host.facts'")[0]["event_id"])
        taken = ev("census.start", root("runtime:x-g1"), event_id=facts_id)        # the authority's own event's id
        content = {**ev("census.start", root("y")), "body": {**root("y"), "prompt": "x"}}
        future = ev("census.start", root("z"), at=datetime.now(timezone.utc) + timedelta(minutes=10))
        got = self.beat([changed, taken, content, future])["census"]["events"]
        self.assertEqual([(a["state"], a.get("code")) for a in got],
                         [("rejected", "event_id_conflict"), ("rejected", "event_id_conflict"),
                          ("rejected", "census_body_invalid"), ("deferred", "event_time_invalid")])
        self.assertEqual(len(self.census_rows()), 3)
        self.assertEqual(self.sql("SELECT kind FROM guild.mc_delegation_events WHERE event_id=%s",
                                  (facts_id,))[0]["kind"], "host.facts")            # untouched
        before = self.sql("SELECT seen_at FROM guild.mc_delegation_runners WHERE runner_id='mac-a'")[0]["seen_at"]
        self.assertEqual(self.beat({"not": "a list"})["census"], {"state": "rejected", "code": "census_batch_invalid"})
        after = self.sql("SELECT seen_at FROM guild.mc_delegation_runners WHERE runner_id='mac-a'")[0]["seen_at"]
        self.assertGreater(after, before)                                           # the contact still counted

    # ── CEN-6: seeing grants nothing, checked against the audit ──
    def test_cen6_census_events_cause_no_authority_transition_and_no_refusal(self):
        admitted = CP.Cards.admit(self, CP.card())
        self.beat()
        claimed = self.store.claim("mac-a", CAPS, protocol=2)
        env = claimed["envelope"]
        rid = env["request_id"]
        items = self.sql("SELECT * FROM guild.mc_delegations ORDER BY request_id")
        audit, spend = self.authority_audit(), self.sql("SELECT * FROM guild.mc_delegation_reservations")
        batch = [ev("census.start", root("runtime:r-g1", item=rid))]
        batch += [ev("census.end", C.end("runtime:r-g1", source=RUNTIME, state="failed", reason="exit_70"))]
        batch += [ev("census.update", C.update(f"x{i}", source="claude.hook", state="unknown", reason="frozen"))
                  for i in range(40)]
        self.assertEqual({a["state"] for a in self.beat(batch)["census"]["events"]}, {"applied"})
        self.assertEqual(self.sql("SELECT * FROM guild.mc_delegations ORDER BY request_id"), items)
        self.assertEqual(self.authority_audit(), audit)                             # no transition, no refusal
        self.assertEqual(self.sql("SELECT * FROM guild.mc_delegation_reservations"), spend)
        self.assertTrue(self.store.renew(rid, env["generation"], protocol=2, runner_id="mac-a"))
        done = self.store.result(rid, env["generation"], event_id=str(uuid4()), status="completed", artifacts=[],
                                 detail={}, output_text="ok", protocol=2, runner_id="mac-a",
                                 at=self.db_now().isoformat(), route="fakework",
                                 usage={"amount": CP.USED, "unit": "test_units", "children_stopped": True})
        self.assertEqual(done["state"], "accepted")
        self.assertEqual(admitted["state"], "admitted")

    def db_now(self):
        return self.sql("SELECT clock_timestamp() AS now")[0]["now"]

    # ── CEN-3, CEN-4 and the capture gap, derived at read time ──
    def test_the_agents_view_is_derived_never_running_on_an_unreachable_host_or_frozen(self):
        self.beat([ev("census.start", root("r1"), at=ago(30)), ev("census.start", sub("r1/s", "r1"), at=ago(29)),
                   ev("census.update", C.update("r1/s", source="fakework.spawn", state="running"), at=ago(20))])
        a = self.agents()
        self.assertEqual([(a[x]["state"], a[x]["host_id"], a[x]["environment"]) for x in ("r1", "r1/s")],
                         [("running", "mac-a", "dev")] * 2)
        self.assertEqual(a["r1/s"]["item_id"], None)
        self.sql("UPDATE guild.mc_delegation_runners SET seen_at = clock_timestamp() - interval '100 seconds' "
                 "WHERE runner_id='mac-a'")                                         # past contact_timeout_s (45)
        a = self.agents()
        self.assertEqual({(r["state"], r["reason"]) for r in a.values()}, {("unknown", "host_unreachable")})
        self.beat([ev("census.update", C.update("r1", source=RUNTIME, state="idle", reason="frozen"), at=ago(5))])
        a = self.agents()
        self.assertEqual({(r["state"], r["reason"]) for r in a.values()}, {("idle", "frozen")})
        self.beat([ev("census.end", C.end("r1", source=RUNTIME, state="done", reason="stopped:cancelled"),
                      at=ago(4))])
        a = self.agents()
        self.assertEqual((a["r1"]["state"], a["r1/s"]["state"], a["r1/s"]["reason"], a["r1/s"]["ended_at"]),
                         ("done", "unknown", "end_unobserved", None))
        self.beat([ev("census.start", C.start("sess-1234567890", source="claude.hook", verified=True,
                                              children="not_exposed", vendor="anthropic", role="session",
                                              start_path="fallback", route="subscription"), at=ago(3)),
                   ev("census.gap", C.gap("wake", ago(1).isoformat(), ago(2).isoformat()), at=ago(1))])
        self.assertEqual(self.agents()["sess-1234567890"]["reason"], "capture_gap")
        self.assertFalse([r for r in self.store.agents() if r["state"] == "running"])

    # ── N3: retention drops census events only, never an authority event ──
    def test_retention_drops_only_census_events_and_never_an_authority_event(self):
        CP.Cards.admit(self, CP.card())
        self.beat()
        self.store.claim("mac-a", CAPS, protocol=2)
        self.assertTrue([k for _, k, *_ in self.authority_audit() if k == "grant.step_bound"])
        audit = self.authority_audit()
        old = 40 * 86400
        self.beat([ev("census.start", root("done"), at=ago(old + 60)),
                   ev("census.start", sub("done/s", "done"), at=ago(old + 50)),
                   ev("census.end", C.end("done", source=RUNTIME, state="done", reason="exit_0"), at=ago(old)),
                   ev("census.start", root("open"), at=ago(old)),                     # never ended: kept
                   ev("census.gap", C.gap("restart", ago(old).isoformat()), at=ago(old)),
                   ev("census.gap", C.gap("restart", ago(10).isoformat()), at=ago(10)),   # the latest gap: kept
                   ev("census.start", root("recent"), at=ago(100)),
                   ev("census.end", C.end("recent", source=RUNTIME, state="done", reason="exit_0"), at=ago(90))])
        got = self.store.census_retention()
        self.assertEqual(got, {"agents_dropped": 2, "events_dropped": 4})
        self.assertEqual(self.authority_audit(), audit)                             # every authority event stays
        left = {(r["kind"], r["body"].get("agent_id")) for r in self.census_rows()}
        self.assertEqual(left, {("census.start", "open"), ("census.gap", None), ("census.start", "recent"),
                                ("census.end", "recent")})
        self.beat([ev("census.start", root("done2"), at=ago(old + 5)),
                   ev("census.end", C.end("done2", source=RUNTIME, state="done", reason="exit_0"), at=ago(old))])
        fresh = DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH, grants=CP.GRANTS)
        self.assertEqual(fresh.sweep().get("census_dropped"), 2)                   # the sweeper runs it, hourly
        self.assertIsNone(fresh.sweep().get("census_dropped"))
        self.assertEqual(self.authority_audit()[:len(audit)], audit)

    # ── T18e at the authority: a swarm is recorded whole; the spend gate is unchanged ──
    def test_t18e_a_swarm_is_recorded_whole_with_no_limit_and_the_spend_gate_is_unchanged(self):
        CP.Cards.admit(self, CP.card())
        self.beat()
        self.assertIsNotNone(self.store.claim("mac-a", CAPS, protocol=2))
        spend = self.sql("SELECT * FROM guild.mc_delegation_reservations")
        events = [ev("census.start", root("swarm-root"))]
        for i in range(300):
            events += [ev("census.start", sub(f"swarm-root/s{i}", "swarm-root")),
                       ev("census.end", C.end(f"swarm-root/s{i}", source="fakework.spawn", state="done",
                                              reason="stop_observed"))]
        for k in range(0, len(events), C.BATCH_MAX_EVENTS):
            got = self.beat(events[k:k + C.BATCH_MAX_EVENTS])
            self.assertEqual({a["state"] for a in got["census"]["events"]}, {"applied"})
        a = self.agents()
        self.assertEqual(len([r for r in a.values() if r["parent_id"] == "swarm-root"]), 300)
        self.assertEqual(self.sql("SELECT * FROM guild.mc_delegation_reservations"), spend)
