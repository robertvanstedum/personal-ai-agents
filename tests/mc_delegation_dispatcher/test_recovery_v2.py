"""Recovery at the authority, without a database (host runtime spec v0.5 §3.2 reconcile and effect, §4.5, §5): the
sweeper's and reassignment's configuration, the shapes of the two new calls at the store and the door, the grace
arithmetic (R5.2-a, X2a), the lapse causes, and relay.status's reap moving to the sweeper (R3.3) while production keeps
today's. The PostgreSQL half is test_recovery_v2_postgres.py."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.mc_delegation_dispatcher import door  # noqa: E402
from core.mc_delegation_dispatcher.relay import DelegationRelay  # noqa: E402
from core.mc_delegation_dispatcher.store import (LEASE_SECONDS, Authority, AuthorityRefused, DispatcherStore,  # noqa: E402
                                                 check_effect, check_operations, check_stage, effect_operation_id,
                                                 grace_deadline, lapse_cause)

PROD = ROOT / "core" / "mc_delegation_dispatcher" / "authority.prod.json"
TEST = Path(__file__).resolve().parent / "authority.test_values.json"
RID = "6f1c2a9e-1b2c-4d3e-8f90-0a1b2c3d4e5f"
OP = "0b0e7a52-3c1d-4f2e-9a8b-7c6d5e4f3a2b"
GID = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
DIGEST = "sha256:" + "a" * 64
TOKEN = "A" * 43
STAGE = {"child": "stopped", "checkpoint": {"name": "s1", "digest": DIGEST}}
REQUEST = {"operation": "fakework.sink_post", "operation_version": 1, "arguments": {"op": "e1"}}


def doc(**over):
    base = json.loads(TEST.read_text())
    base.update(over)
    return base


def eff(op, **over):
    d = {"op": op, "key": "e1", "grant_id": GID, "request": dict(REQUEST)}
    if op in ("dispatch", "finish"):
        d["lease"] = {"receipt_generation": 1, "token": TOKEN, "mode": "execute"}
    if op == "finish":
        d.update(state="verified", evidence={"kind": "readback", "outcome": "committed"})
    d.update(over)
    return d


def _no_db():
    raise AssertionError("no database connection expected")


class Configuration(unittest.TestCase):
    def test_the_sweeper_and_reassignment_are_configured_and_production_has_neither(self):
        t, prod = Authority.load(TEST), Authority.load(PROD)
        self.assertEqual((t.sweeper.sweep_s, t.sweeper.gap_threshold_s, t.reassign_across_hosts, t.clock_skew_s),
                         (15, 30, False, 1))
        self.assertEqual((prod.sweeper, prod.reassign_across_hosts, prod.clock_skew_s), (None, False, 0))
        self.assertEqual(Authority.parse({k: v for k, v in doc().items() if k != "clock_skew_s"}).clock_skew_s, 0)
        self.assertTrue(Authority.parse(doc(reassign_across_hosts=True)).reassign_across_hosts)
        # Version 1 only: a sweeper value changes nothing, and relay.status keeps today's reap.
        self.assertEqual(Authority.parse(doc(protocols=[1], lease_s=None)).sweeper.sweep_s, 15)
        self.assertFalse(DispatcherStore(_no_db, principal="robert", environment="prod", authority=prod).sweeps)
        self.assertFalse(DispatcherStore(_no_db, principal="robert", environment="prod").sweeps)
        self.assertFalse(DispatcherStore(_no_db, principal="robert", environment="dev",
                                         authority=Authority.parse(doc(protocols=[1], lease_s=None))).sweeps)
        from core.minimoi_actions.grants import GrantStore
        self.assertTrue(DispatcherStore(_no_db, principal="robert", environment="dev", authority=t,
                                        grants=GrantStore()).sweeps)
        from dataclasses import replace
        with self.assertRaisesRegex(ValueError, "authority_sweeper_missing"):     # missing admits nothing (R2.3-b)
            DispatcherStore(_no_db, principal="robert", environment="dev", authority=replace(t, sweeper=None),
                            grants=GrantStore())

    def test_malformed_values_are_refused(self):
        for d in (doc(sweeper={"sweep_s": 15}), doc(sweeper={"sweep_s": 30, "gap_threshold_s": 30}),
                  doc(sweeper={"sweep_s": 0, "gap_threshold_s": 30}), doc(sweeper={"sweep_s": 15.0, "gap_threshold_s": 30}),
                  doc(sweeper={"sweep_s": 15, "gap_threshold_s": 7200}), doc(sweeper=[15, 30]),
                  doc(sweeper={"sweep_s": 15, "gap_threshold_s": 30, "x": 1}), doc(reassign_across_hosts="yes"),
                  doc(reassign_across_hosts=1), doc(clock_skew_s=-1), doc(clock_skew_s=61), doc(clock_skew_s=1.0),
                  # Version 2 without a sweeper would never lapse or end anything at grace: missing admits nothing.
                  {k: v for k, v in doc().items() if k != "sweeper"}, doc(sweeper=None)):
            with self.assertRaises(AuthorityRefused, msg=str(d.get("sweeper")) + str(d.get("clock_skew_s"))):
                Authority.parse(d)


class Shapes(unittest.TestCase):
    def test_stage_and_open_operations(self):
        self.assertEqual(check_stage(STAGE), STAGE)
        self.assertEqual(check_stage({"child": "frozen", "checkpoint": None}), {"child": "frozen", "checkpoint": None})
        for bad in ({"child": "running", "checkpoint": None}, {"child": "stopped"}, {"child": "stopped", "checkpoint": None, "x": 1},
                    {"child": "stopped", "checkpoint": {"name": "s1", "digest": "sha256:zz"}},
                    {"child": "stopped", "checkpoint": {"name": "s1", "digest": DIGEST, "paths": []}}, "stopped", None):
            with self.assertRaises(AuthorityRefused, msg=repr(bad)):
                check_stage(bad)
        self.assertEqual(check_operations([OP]), [OP])
        for bad in ([OP, OP], ["e1"], [OP] * 33, OP, None):
            with self.assertRaises(AuthorityRefused):
                check_operations(bad)

    def test_each_effect_step_has_exactly_its_fields(self):
        for op in ("prepare", "claim", "dispatch", "finish", "reconcile"):
            self.assertEqual(check_effect(eff(op))["op"], op)
        self.assertIsNone(check_effect(eff("finish", state="unknown", evidence=None))["evidence"])
        self.assertEqual(check_effect(eff("finish", lease={"receipt_generation": 2, "token": TOKEN, "mode": "reconcile"}))
                         ["lease"]["mode"], "reconcile")
        bad = [
            eff("prepare", lease={"receipt_generation": 1, "token": TOKEN, "mode": "execute"}),   # not on prepare
            {k: v for k, v in eff("dispatch").items() if k != "lease"},
            eff("dispatch", lease={"receipt_generation": 1, "token": TOKEN, "mode": "reconcile"}),  # dispatch executes
            eff("dispatch", lease={"receipt_generation": 1, "token": "short", "mode": "execute"}),
            eff("finish", state="verified", evidence=None),                     # verified or failed needs evidence
            eff("finish", state="done"),
            eff("finish", evidence={"kind": "readback", "outcome": "Committed!"}),
            eff("claim", key="e 1"), eff("claim", grant_id="not-a-uuid"), eff("cancel"),
            eff("prepare", request={**REQUEST, "operation": "delegate.start_build"}),   # an effect never admits work
            eff("prepare", request={**REQUEST, "operation_version": 0}),
            eff("prepare", request={**REQUEST, "operation_version": True}),
            eff("prepare", request={**REQUEST, "arguments": ["e1"]}),
            eff("prepare", request={**REQUEST, "arguments": {"blob": "x" * 9000}}),
            eff("prepare", request={**REQUEST, "extra": 1}),
            eff("prepare", extra=1),
        ]
        for b in bad:
            with self.assertRaises(AuthorityRefused, msg=str(b)[:160]):
                check_effect(b)

    def test_the_operation_id_is_the_authoritys_bound_to_the_item_and_stable_across_generations(self):
        a = effect_operation_id(RID, "e1")
        self.assertEqual(a, effect_operation_id(RID, "e1"))
        self.assertNotEqual(a, effect_operation_id(RID, "e2"))
        self.assertNotEqual(a, effect_operation_id("7f1c2a9e-1b2c-4d3e-8f90-0a1b2c3d4e5f", "e1"))


T0 = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)


def at(s):
    return T0 + timedelta(seconds=s)


class Grace(unittest.TestCase):
    def test_grace_runs_from_the_lapse_and_outages_do_not_count(self):
        self.assertEqual(grace_deadline(at(0), 900, []), (at(900), False))
        # An outage that starts before the deadline moves it on by its whole length (R5.2-a).
        self.assertEqual(grace_deadline(at(0), 900, [(at(100), at(1300))]), (at(2100), False))
        # A lease that ran out inside an outage gets its grace from the restart (X2a), so the cause is authority_down.
        self.assertEqual(grace_deadline(at(0), 900, [(at(-60), at(1200))]), (at(2100), True))
        self.assertEqual(grace_deadline(at(0), 900, [(at(-60), at(0))]), (at(900), True))      # ended at the lapse
        # Two outages: the second counts only because the first moved the deadline past its start.
        self.assertEqual(grace_deadline(at(0), 900, [(at(950), at(1050)), (at(100), at(200))]), (at(1100), False))
        self.assertEqual(grace_deadline(at(0), 900, [(at(100), at(200)), (at(1000), at(1100))]), (at(1000), False))
        self.assertEqual(grace_deadline(at(0), 900, [(at(1000), at(1100))]), (at(900), False))  # after the deadline
        self.assertEqual(grace_deadline(at(0), 0, []), (at(0), False))                         # no grace stated

    def test_the_lapse_cause(self):
        self.assertEqual(lapse_cause(True, at(10), at(0)), "authority_down")
        self.assertEqual(lapse_cause(False, None, at(0)), "owner_contact_lost")
        self.assertEqual(lapse_cause(False, at(-30), at(0)), "owner_contact_lost")    # silent before the lease ran out
        self.assertEqual(lapse_cause(False, at(0), at(0)), "owner_contact_lost")
        self.assertEqual(lapse_cause(False, at(30), at(0)), "lease_not_renewed")      # in contact, but no renewal


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*a, **kw):
            self.calls.append((name, a, kw))
            return {"ok": name}
        return call


def v2(op, **args):
    bodies = {"reconcile": {"runner_id": "mac-test-1", "request_id": RID, "generation": 1, "stage": STAGE,
                            "open_operations": [OP]},
              "effect": {"runner_id": "mac-test-1", "request_id": RID, "generation": 1, "effect": eff("dispatch")}}
    return {"op": op, "protocol": 2, **bodies[op], **args}


class Door(unittest.TestCase):
    def test_reconcile_and_effect_are_checked_and_passed_through(self):
        s = Recorder()
        door.handle(s, v2("reconcile"))
        door.handle(s, v2("effect"))
        self.assertEqual(s.calls[0], ("reconcile", ("mac-test-1", RID, 1, STAGE, [OP]), {"protocol": 2}))
        self.assertEqual(s.calls[1][0:2], ("effect", ("mac-test-1", RID, 1, check_effect(eff("dispatch")))))
        self.assertEqual(s.calls[1][2], {"protocol": 2})

    def test_anything_else_is_refused(self):
        bad = [
            {k: v for k, v in v2("reconcile").items() if k != "open_operations"},
            v2("reconcile", stage={"child": "running", "checkpoint": None}),
            v2("reconcile", open_operations=["e1"]),
            v2("reconcile", generation=-1),
            v2("reconcile", runner_id="mac; rm"),
            v2("reconcile", principal="eve"),
            v2("effect", effect=eff("dispatch", lease=None)),
            v2("effect", effect=eff("prepare", request={**REQUEST, "operation": "delegate.card"})),
            v2("effect", operation_id=OP),                               # the authority assigns the operation id
            {**v2("reconcile"), "protocol": 1},
            {k: v for k, v in v2("reconcile").items() if k != "protocol"},   # version 1 has no reconcile
        ]
        for body in bad:
            with self.assertRaises(door.Refused, msg=str(body)[:120]):
                door.handle(Recorder(), body)


class StoreSwitch(unittest.TestCase):
    def test_the_new_calls_and_acts_are_version_2_only(self):
        store = DispatcherStore(_no_db, principal="robert", environment="prod", authority=Authority.load(PROD))
        for call in (lambda: store.reconcile("m", RID, 1, STAGE, []),
                     lambda: store.effect("m", RID, 1, eff("prepare")),
                     lambda: store.reassign(RID, "m", by="robert", note="n"),
                     lambda: store.end(RID, by="robert", note="n"),
                     lambda: store.revoke(GID, by="robert", note="n"), lambda: store.open_operations(RID),
                     lambda: store.sweep(), lambda: store.sweeper_status(), lambda: store.authority_beat()):
            with self.assertRaises(AuthorityRefused):
                call()
        with self.assertRaisesRegex(AuthorityRefused, "protocol_invalid"):
            store.reconcile("m", RID, 1, STAGE, [], protocol=1)

    def test_owner_acts_say_who_and_why(self):
        from core.minimoi_actions.grants import GrantStore
        store = DispatcherStore(_no_db, principal="robert", environment="dev", authority=Authority.load(TEST),
                                grants=GrantStore())
        for kw in ({"by": "robert", "note": " "}, {"by": "robert", "note": None}, {"by": "rob ert", "note": "n"}):
            with self.assertRaises(AuthorityRefused):
                store.end(RID, **kw)


class NoAuthorityAndTheLoop(unittest.TestCase):
    def store(self, connect):
        from core.minimoi_actions.grants import GrantStore
        return DispatcherStore(connect, principal="robert", environment="dev", authority=Authority.load(TEST),
                               grants=GrantStore())

    def test_no_authority_no_effect(self):
        """R4.5-e: every effect step is a transaction at the authority, so an unreachable one admits no step at all."""
        def unreachable():
            raise OSError("authority unreachable")
        store = self.store(unreachable)
        for call in (lambda: store.effect("m", RID, 1, eff("prepare")), lambda: store.effect("m", RID, 1, eff("dispatch")),
                     lambda: store.reconcile("m", RID, 1, STAGE, [])):
            with self.assertRaises(OSError):
                call()

    def test_the_sweeper_loop_goes_on_after_a_failed_pass_and_stops_when_told(self):
        import threading

        class Stop(threading.Event):
            def wait(self, timeout=None):            # no real waiting in a test
                self.waited = timeout
                return self.is_set()
        store, stop, passes, logged = self.store(_no_db), Stop(), [], []

        def sweep():
            passes.append(1)
            if len(passes) == 1:
                raise RuntimeError("postgres: password=secret")          # only its type is ever logged
            stop.set()
        store.sweep = sweep

        class Log:
            def warning(self, msg, *args):
                logged.append(msg % args)
        store.run_sweeper(stop, log=Log())
        self.assertEqual((len(passes), stop.waited, logged), (2, 15, ["sweeper pass failed (RuntimeError)"]))


class FakeStore:
    def __init__(self, sweeps, lease_past_s):
        self.sweeps, self.reaped = sweeps, []
        now = datetime.now(timezone.utc)
        self.row = {"state": "running", "lease_until": now - timedelta(seconds=lease_past_s), "now": now,
                    "deadline": now + timedelta(hours=1), "finished_at": None, "started_at": now - timedelta(minutes=5),
                    "created_at": now - timedelta(minutes=6), "heartbeat_at": now - timedelta(minutes=4),
                    "actor": "codex", "result_detail": None, "output_text": None}

    def by_job(self, job_id):
        return dict(self.row)

    def reap_lapsed(self, job_id):
        self.reaped.append(job_id)
        self.row.update(state="interrupted", result_detail={"code": "runner_lost"}, finished_at=self.row["now"])
        return True


class RelayReap(unittest.TestCase):
    def test_relay_reaps_only_where_no_sweeper_runs_and_never_shows_a_lapse_as_running(self):
        today = FakeStore(sweeps=False, lease_past_s=LEASE_SECONDS + 5)
        outcome, st = DelegationRelay(today, ref_for=lambda r: "a" * 40).status("j-1")
        self.assertEqual((outcome, st["state"], st["error_class"], today.reaped), ("ok", "failed", "interrupted", ["j-1"]))
        within = FakeStore(sweeps=False, lease_past_s=LEASE_SECONDS - 5)
        self.assertEqual(DelegationRelay(within, ref_for=lambda r: "a" * 40).status("j-1")[1]["state"], "running")
        self.assertEqual(within.reaped, [])                       # production: today's reap, today's display
        # Where a sweeper runs (it may be dead), the read writes nothing and derives the lapse: never running (E11).
        for past in (1, LEASE_SECONDS + 5, 86400):
            swept = FakeStore(sweeps=True, lease_past_s=past)
            outcome, st = DelegationRelay(swept, ref_for=lambda r: "a" * 40).status("j-1")
            self.assertEqual((st["state"], st["error_class"], swept.reaped), ("unknown", None, []), past)
        live = FakeStore(sweeps=True, lease_past_s=-30)
        self.assertEqual(DelegationRelay(live, ref_for=lambda r: "a" * 40).status("j-1")[1]["state"], "running")

    def test_version_2_states_are_never_shown_as_running(self):
        for state, shown in (("lapsed", "unknown"), ("paused", "unknown"), ("unknown", "unknown"),
                             ("proposed", "unknown"), ("expired", "failed")):
            s = FakeStore(sweeps=True, lease_past_s=0)
            s.row["state"] = state
            self.assertEqual(DelegationRelay(s, ref_for=lambda r: "a" * 40).status("j-1")[1]["state"], shown, state)


if __name__ == "__main__":
    unittest.main()
