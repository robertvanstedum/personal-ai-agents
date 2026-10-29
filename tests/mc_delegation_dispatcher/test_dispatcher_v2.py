"""Protocol version 2 of the delegation dispatcher, without a database (host runtime spec v0.5 §3.2, R2.3-b, R6.7-l,
R8.3): the authority configuration, the route gate, the reservation rule, the store's protocol switch, and the door's
strict shapes. A version 1 body still reaches the store exactly as before. The PostgreSQL half is
test_dispatcher_v2_postgres.py."""
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.mc_delegation_dispatcher import door  # noqa: E402
from core.mc_delegation_dispatcher.store import (Authority, AuthorityRefused, DispatcherStore, check_checkpoint,  # noqa: E402
                                                 check_facts, check_usage, in_test_family, item_subject_digest,
                                                 parse_at, reservation_hold, route_refusal)

PROD = ROOT / "core" / "mc_delegation_dispatcher" / "authority.prod.json"
TEST = Path(__file__).resolve().parent / "authority.test_values.json"
RID = "6f1c2a9e-1b2c-4d3e-8f90-0a1b2c3d4e5f"
EID = "0b0e7a52-3c1d-4f2e-9a8b-7c6d5e4f3a2b"
DIGEST = "sha256:" + "a" * 64
FACTS = {"host_id": "mac-test-1", "bundle_digest": DIGEST, "roles": ["authority", "worker", "witness"],
         "availability": "intermittent", "grace": 900, "last_gap_s": 0, "announced": "online"}
CAPS = ["repo:personal-ai-agents", "scope:build", "actor:fakework", "host:mac-local"]
AT = "2026-10-28T20:00:00+00:00"


def TEST_VALUES_ROUTES():
    return json.loads(TEST.read_text())["routes"]


def doc(**over):
    base = json.loads(TEST.read_text())
    base.update(over)
    return base


class AuthorityConfig(unittest.TestCase):
    def test_the_production_profile_is_todays_behaviour(self):
        prod = Authority.load(PROD)
        self.assertEqual((prod.environment, prod.protocols, prod.speaks_v2, prod.launch_closure, prod.lease_s),
                         ("prod", frozenset({1}), False, "pending", 120))
        self.assertEqual(set(prod.routes), {"claude-code.subscription", "codex.subscription"})
        claude = prod.routes["claude-code.subscription"]
        self.assertEqual((claude.unit, claude.per_item, claude.per_day, claude.supervised_only),
                         ("usd_notional", Decimal("2.0"), None, True))
        self.assertEqual(prod.routes["codex.subscription"].unit, "minutes")
        self.assertFalse(any(in_test_family(r) for r in prod.routes))
        self.assertFalse(any(r.unit == "test_units" for r in prod.routes.values()))
        # Version 1 only: a production store needs no grant reader and refuses version 2 before touching a database.
        store = DispatcherStore(_no_db, principal="robert", environment="prod", authority=prod)
        with self.assertRaisesRegex(AuthorityRefused, "protocol_not_enabled"):
            store.claim("mac-prod-1", CAPS, protocol=2)

    def test_the_test_values_are_section_9_2s(self):
        t = Authority.load(TEST)
        self.assertTrue(t.speaks_v2)
        self.assertEqual((t.environment, t.lease_s, t.launch_closure), ("dev", 60, "pending"))
        fw = t.routes["fakework"]
        self.assertEqual((fw.unit, fw.per_day, fw.per_item, fw.supervised_only),
                         ("test_units", Decimal("2.0"), Decimal("1.5"), False))
        self.assertTrue(t.routes["fakework.subscription"].supervised_only)

    def test_missing_values_admit_nothing_and_closure_defaults_to_pending(self):
        d = doc()
        del d["launch_closure"]
        d["routes"] = {"fakework": {"unit": "test_units", "actors": ["fakework"], "per_item": 1.0,
                                    "supervised_only": False},
                       "fakework.unsaid": {"unit": "test_units", "actors": ["fakework"], "per_day": 2.0, "per_item": 1.0},
                       "fakework.nobody": {"unit": "test_units", "per_day": 2.0, "per_item": 1.0,
                                           "supervised_only": False}}
        a = Authority.parse(d)
        self.assertEqual(a.launch_closure, "pending")
        self.assertEqual(route_refusal(a, "fakework", "fakework"), {"code": "route_cap_missing"})   # no daily cap
        self.assertEqual(route_refusal(a, "fakework.unsaid", "fakework"),                           # not stated
                         {"code": "route_supervised_only", "reason": "route_configured"})
        self.assertEqual(route_refusal(a, "fakework.nobody", "fakework"), {"code": "route_actor_mismatch"})  # no actors
        self.assertEqual(route_refusal(a, "nowhere", "fakework"), {"code": "route_unknown"})

    def test_anything_malformed_is_refused(self):
        bad = [
            doc(extra=1), doc(schema="minimoi.authority.v2"), doc(environment="staging"),
            doc(protocols=[2]), doc(protocols=[1, 3]), doc(protocols=[True]), doc(protocols=[1, 1]),
            doc(launch_closure="open"), doc(lease_s=None), doc(lease_s=5), doc(lease_s=60.0),
            doc(routes={"Fake Work": {"unit": "test_units"}}),
            doc(routes={"fakework": {"unit": "credits"}}),
            doc(routes={"fakework": {"unit": "test_units", "cap": 1}}),
            doc(routes={"fakework": {"unit": "test_units", "per_day": 1.0, "per_item": 1.5}}),
            doc(routes={"fakework": {"unit": "test_units", "per_day": -1}}),
            doc(routes={"fakework": {"unit": "test_units", "per_day": 1.00001}}),
            doc(routes={"fakework": {"unit": "test_units", "per_day": True}}),
            doc(routes={"fakework": {"unit": "test_units", "supervised_only": "no"}}),
            doc(routes={"fakework": {"unit": "test_units", "actors": "fakework"}}),
            doc(routes={"fakework": {"unit": "test_units", "actors": ["grok"]}}),
            doc(routes={"fakework": {"unit": "test_units", "actors": ["fakework", "fakework"]}}),
            # A test route escapes the closure gate only because its worker carries no key: no other worker on it.
            doc(routes={"fakework": {"unit": "test_units", "actors": ["claude-code"]}}),
            doc(routes={"fakework.api": {"unit": "usd", "actors": ["fakework", "codex"]}}),
            doc(environment="prod", protocols=[1], routes={"anthropic.api": {"unit": "usd", "actors": ["fakework"]}}),
            # Test values never in production configuration.
            doc(environment="prod", protocols=[1]),
            doc(environment="prod", routes={"anthropic.api": {"unit": "test_units"}}),
            [],
        ]
        for d in bad:
            with self.assertRaises(AuthorityRefused, msg=str(d)[:120]):
                Authority.parse(d)
        Authority.parse(doc(protocols=[1], lease_s=None))          # version 1 needs no lease of its own

    def test_a_version_2_store_must_read_grants_and_match_its_environment(self):
        t = Authority.load(TEST)
        with self.assertRaisesRegex(ValueError, "authority_grant_store_missing"):
            DispatcherStore(_no_db, principal="robert", environment="dev", authority=t)
        from core.minimoi_actions.grants import GrantStore
        with self.assertRaisesRegex(ValueError, "authority_environment_mismatch"):
            DispatcherStore(_no_db, principal="robert", environment="prod", authority=t, grants=GrantStore())
        DispatcherStore(_no_db, principal="robert", environment="dev", authority=t, grants=GrantStore())


def _no_db():
    raise AssertionError("no database connection expected")


class RouteGateAndReservation(unittest.TestCase):
    def test_route_gate_judges_the_route_and_actor_pair(self):
        t = Authority.load(TEST)
        self.assertIsNone(route_refusal(t, "fakework", "fakework"))
        self.assertIsNone(route_refusal(t, "fakework.api", "fakework"))        # the test worker on a test route
        self.assertEqual(route_refusal(t, "fakework.subscription", "fakework"),
                         {"code": "route_supervised_only", "reason": "route_configured"})
        self.assertEqual(route_refusal(t, "anthropic.api", "claude-code"),
                         {"code": "route_supervised_only", "reason": "closure_pending"})
        # A real worker under a test route's name gets no exemption: the route doesn't run it at all.
        for route, actor in (("fakework", "claude-code"), ("fakework", "codex"), ("anthropic.api", "fakework")):
            self.assertEqual(route_refusal(t, route, actor), {"code": "route_actor_mismatch"}, (route, actor))
        # The test worker on a route outside the test family meets the closure gate like any other.
        routes = {**TEST_VALUES_ROUTES(), "anthropic.api": {"unit": "usd", "actors": ["claude-code", "fakework"],
                                                            "per_day": 10.0, "per_item": 3.0, "supervised_only": False}}
        mixed = Authority.parse(doc(routes=routes))
        self.assertEqual(route_refusal(mixed, "anthropic.api", "fakework"),
                         {"code": "route_supervised_only", "reason": "closure_pending"})
        verified = Authority.parse(doc(launch_closure="verified"))
        self.assertIsNone(route_refusal(verified, "anthropic.api", "claude-code"))
        self.assertEqual(route_refusal(verified, "fakework.subscription", "fakework")["code"], "route_supervised_only")
        self.assertEqual(route_refusal(verified, "fakework", "claude-code"), {"code": "route_actor_mismatch"})

    def test_the_test_family_is_named_not_configured(self):
        for r in ("fakework", "fakejudge", "fakework.api", "fakejudge.x"):
            self.assertTrue(in_test_family(r), r)
        for r in ("fakeworks", "fake", "anthropic.api", "xfakework", "api.fakework"):
            self.assertFalse(in_test_family(r), r)

    def test_two_one_and_a_half_unit_items_against_two(self):
        cap, a = Decimal("2.0"), Decimal("1.5")
        self.assertIsNone(reservation_hold("test_units", "test_units", Decimal(0), Decimal(0), a, cap))
        self.assertEqual(reservation_hold("test_units", "test_units", a, Decimal(0), a, cap), "reservation_cap")
        self.assertIsNone(reservation_hold("test_units", "test_units", a, Decimal(0), Decimal("0.5"), cap))   # exact
        self.assertEqual(reservation_hold("test_units", "test_units", Decimal(0), a, Decimal("0.6"), cap),
                         "reservation_cap")                                   # observed usage counts too
        # A row in another unit is never added to: the claim waits instead.
        self.assertEqual(reservation_hold("test_units", "usd", Decimal(0), Decimal(0), Decimal("0.1"), cap),
                         "route_unit_changed")


class ValueChecks(unittest.TestCase):
    def test_facts_checkpoint_usage_and_time(self):
        self.assertEqual(check_facts(FACTS)["host_id"], "mac-test-1")
        for k, v in (("roles", []), ("roles", ["boss"]), ("availability", "sometimes"), ("grace", 0),
                     ("grace", 1.5), ("last_gap_s", -1), ("announced", "asleep"), ("bundle_digest", "a" * 64)):
            with self.assertRaises(AuthorityRefused, msg=k):
                check_facts({**FACTS, k: v})
        with self.assertRaises(AuthorityRefused):
            check_facts({**FACTS, "census": []})                     # S1-F adds census; not before
        self.assertIsNone(check_checkpoint(None))
        cp = {"name": "s1", "digest": DIGEST, "paths": ["core/x.py", "tests/y_test.py"]}
        self.assertEqual(check_checkpoint(cp), cp)
        for paths in (["/etc/passwd"], ["../x"], ["a/../../b"], ["a b"], ["x"] * 201):
            with self.assertRaises(AuthorityRefused, msg=str(paths)[:40]):
                check_checkpoint({**cp, "paths": paths})
        self.assertEqual(check_usage({"amount": 1.25, "unit": "test_units", "children_stopped": True})["amount"],
                         Decimal("1.25"))
        self.assertIsNone(check_usage({"amount": None, "unit": "usd", "children_stopped": False})["amount"])
        for u in ({"amount": -1, "unit": "usd", "children_stopped": True},
                  {"amount": 1, "unit": "credits", "children_stopped": True},
                  {"amount": 1, "unit": "usd"}, {"amount": 1, "unit": "usd", "children_stopped": 1}):
            with self.assertRaises(AuthorityRefused):
                check_usage(u)
        self.assertEqual(parse_at("2026-10-28T20:00:00Z").utcoffset().total_seconds(), 0)
        # The same answers on every Python this runs on (3.9's fromisoformat refuses two-digit fractions).
        self.assertEqual(parse_at("2026-10-28T20:00:00.12+00:00").microsecond, 120000)
        self.assertEqual(parse_at("2026-10-28T20:00:00.123456-05:30").utcoffset().total_seconds(), -19800)
        self.assertEqual(parse_at("2026-10-28T20:00:00.5Z"), parse_at("2026-10-28T20:00:00.500000+00:00"))
        for t in ("2026-10-28T20:00:00", "yesterday", 1730145600, "x" * 41, "2026-10-28 20:00:00Z",
                  "2026-10-28T20:00:00.1234567Z", "2026-02-30T20:00:00Z", "2026-10-28T20:00Z", "2026-10-28T20:00:00+24:00"):
            with self.assertRaises(AuthorityRefused):
                parse_at(t)


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*a, **kw):
            self.calls.append((name, a, kw))
            return {"ok": name}
        return call


V1_CAPS = {"cost_enforced_actors": ["claude-code", "codex"], "scopes": ["research"], "repositories": ["personal-ai-agents"]}


def v2(op, **args):
    bodies = {
        "heartbeat": {"runner_id": "mac-test-1", "state": "online", "capabilities": CAPS, "facts": FACTS},
        "claim": {"runner_id": "mac-test-1", "capabilities": CAPS},
        "progress": {"runner_id": "mac-test-1", "request_id": RID, "generation": 1, "event_id": EID, "at": AT,
                     "phase": "executing", "checkpoint": {"name": "s1", "digest": DIGEST, "paths": ["README.md"]}},
        "cancel_requested": {"request_id": RID, "generation": 1},
        "renew": {"runner_id": "mac-test-1", "request_id": RID, "generation": 1},
        "result": {"runner_id": "mac-test-1", "request_id": RID, "generation": 1, "event_id": EID, "at": AT,
                   "status": "completed", "artifacts": [], "detail": {}, "output_text": "done", "route": "fakework",
                   "usage": {"amount": 1.0, "unit": "test_units", "children_stopped": True}},
    }
    return {"op": op, "protocol": 2, **bodies[op], **args}


class DoorVersion1Unchanged(unittest.TestCase):
    def test_each_version_1_call_reaches_the_store_exactly_as_before(self):
        s = Recorder()
        door.handle(s, {"op": "heartbeat", "runner_id": "mac-prod-1", "state": "idle", "capabilities": V1_CAPS})
        door.handle(s, {"op": "claim", "runner_id": "mac-prod-1", "capabilities": V1_CAPS})
        door.handle(s, {"op": "progress", "request_id": RID, "generation": 1, "phase": "started"})
        door.handle(s, {"op": "cancel_requested", "request_id": RID, "generation": 1})
        door.handle(s, {"op": "renew", "request_id": RID, "generation": 1})
        door.handle(s, {"op": "result", "request_id": RID, "generation": 1, "event_id": "e-1", "status": "completed",
                        "artifacts": [], "detail": {}, "output_text": "answer"})
        self.assertEqual(s.calls, [
            ("heartbeat", ("mac-prod-1", "idle", V1_CAPS), {}),
            ("claim", ("mac-prod-1", V1_CAPS), {}),
            ("progress", (RID, 1, "started"), {}),
            ("cancel_requested", (RID, 1), {}),
            ("renew", (RID, 1), {}),
            ("result", (RID, 1), {"event_id": "e-1", "status": "completed", "artifacts": [], "detail": {},
                                  "output_text": "answer"})])

    def test_a_protocol_field_is_only_ever_version_2(self):
        base = {"op": "renew", "request_id": RID, "generation": 1}
        for p in (1, "2", True, 3, None, 2.0):
            with self.assertRaises(door.Refused, msg=repr(p)):
                door.handle(Recorder(), {**base, "protocol": p})


class DoorVersion2(unittest.TestCase):
    def test_each_version_2_call_is_checked_and_passed_through(self):
        s = Recorder()
        for op in ("heartbeat", "claim", "progress", "cancel_requested", "renew", "result"):
            door.handle(s, v2(op))
        self.assertEqual([c[0] for c in s.calls],
                         ["heartbeat", "claim", "progress", "cancel_requested", "renew", "result"])
        self.assertTrue(all(c[2].get("protocol") == 2 for c in s.calls))
        self.assertEqual(s.calls[0][2]["facts"], FACTS)
        self.assertEqual(s.calls[2][2]["checkpoint"]["name"], "s1")
        self.assertEqual(s.calls[4], ("renew", (RID, 1), {"protocol": 2, "runner_id": "mac-test-1"}))
        self.assertEqual((s.calls[5][2]["route"], s.calls[5][2]["usage"]["amount"], s.calls[5][2]["at"]),
                         ("fakework", 1.0, AT))
        door.handle(s, v2("progress", checkpoint=None))                         # a checkpoint is optional, the key is not

    def test_anything_else_is_refused(self):
        bad = [
            v2("heartbeat", census=[]),                                          # not before S1-F
            {k: v for k, v in v2("heartbeat").items() if k != "facts"},
            v2("heartbeat", facts={**FACTS, "grace": "8h"}),
            v2("heartbeat", capabilities=V1_CAPS),                               # version 1's shape
            v2("claim", capabilities=["Repo personal"]),
            v2("claim", runner_id="mac; rm"),
            v2("claim", principal="eve"),                                        # no authority fields
            {**v2("claim"), "op": "enqueue"},                                    # the runner never creates work
            v2("progress", event_id="e-1"),                                      # version 2 event ids are UUIDs
            v2("progress", at="2026-10-28 20:00"),
            v2("progress", checkpoint={"name": "s1", "digest": DIGEST, "paths": ["../../etc"]}),
            v2("progress", phase="x" * 41),
            v2("renew", generation=True),
            {k: v for k, v in v2("renew").items() if k != "runner_id"},
            v2("cancel_requested", runner_id="mac-test-1"),                      # unchanged: no new field
            v2("result", route="Fakework"),
            v2("result", usage={"amount": 1.0, "unit": "test_units"}),
            v2("result", usage={"amount": -0.5, "unit": "test_units", "children_stopped": True}),
            {k: v for k, v in v2("result").items() if k != "output_text"},      # every field, always
            v2("result", output_text="x" * (64 * 1024 + 1)),
        ]
        for body in bad:
            with self.assertRaises(door.Refused, msg=str(body)[:100]):
                door.handle(Recorder(), body)


class SubjectDigest(unittest.TestCase):
    def test_the_digest_names_the_work_not_the_item(self):
        base = dict(actor="fakework", scope="build", repository="personal-ai-agents", ref="a" * 40, brief="b",
                    route="fakework", amount=1.5, required_capabilities=["host:mac-local", "cap:x"],
                    input_artifacts=[{"ref": "r", "sha256": "e" * 64}])
        d = item_subject_digest(**base)
        self.assertRegex(d, r"^[0-9a-f]{64}$")
        self.assertEqual(d, item_subject_digest(**{**base, "amount": 1.50,
                                                   "required_capabilities": ["cap:x", "host:mac-local"]}))
        for k, v in (("brief", "c"), ("ref", "b" * 40), ("route", "fakework.api"), ("amount", 1.4), ("step", "fix"),
                     ("input_artifacts", []), ("actor", "codex")):
            self.assertNotEqual(d, item_subject_digest(**{**base, k: v}), k)


class StoreProtocolSwitch(unittest.TestCase):
    def test_a_version_2_authority_needs_python_3_10(self):
        from types import SimpleNamespace
        from unittest import mock
        from core.mc_delegation_dispatcher import store as store_mod
        from core.minimoi_actions.grants import GrantStore
        with mock.patch.object(store_mod, "sys", SimpleNamespace(version_info=(3, 9, 6, "final", 0))):
            with self.assertRaisesRegex(RuntimeError, "authority_requires_python_3_10"):
                DispatcherStore(_no_db, principal="robert", environment="dev", authority=Authority.load(TEST),
                                grants=GrantStore())
            DispatcherStore(_no_db, principal="robert", environment="prod", authority=Authority.load(PROD))  # v1 only
            DispatcherStore(_no_db, principal="robert", environment="prod")


    def test_version_1_calls_carry_no_version_2_field(self):
        store = DispatcherStore(_no_db, principal="robert", environment="dev")
        with self.assertRaisesRegex(AuthorityRefused, "protocol_fields_invalid"):
            store.renew(RID, 1, runner_id="mac-test-1")
        with self.assertRaisesRegex(AuthorityRefused, "protocol_fields_invalid"):
            store.enqueue(job_id="j", actor="codex", scope="research", repository="r", ref="a" * 40, brief="b",
                          cost_cap_usd=1, deadline_s=60, grant_id=RID)
        for call in (lambda: store.heartbeat("m", "online", CAPS, protocol=2, facts=FACTS),
                     lambda: store.progress(RID, 1, "x", protocol=2, runner_id="m", event_id=EID, at=AT),
                     lambda: store.result(RID, 1, event_id=EID, status="completed", artifacts=[], detail={},
                                          protocol=2, runner_id="m", at=AT, route="fakework",
                                          usage={"amount": 0, "unit": "test_units", "children_stopped": True}),
                     lambda: store.enqueue(job_id="j", actor="fakework", scope="build", repository="r", ref="a" * 40,
                                           brief="b", cost_cap_usd=1, deadline_s=60, route="fakework", amount=1)):
            with self.assertRaisesRegex(AuthorityRefused, "protocol_not_enabled"):
                call()
        with self.assertRaisesRegex(AuthorityRefused, "protocol_invalid"):
            store.renew(RID, 1, protocol=True)


if __name__ == "__main__":
    unittest.main()


PY39 = "/usr/bin/python3"


def _python_3_9():
    import subprocess
    try:
        out = subprocess.run([PY39, "-c", "import sys; print(sys.version_info[:2] == (3, 9))"], capture_output=True,
                             text=True, timeout=20)
    except OSError:
        return False
    return out.stdout.strip() == "True"


@unittest.skipUnless(Path(PY39).exists() and _python_3_9(), "needs the Mac's Python 3.9 at /usr/bin/python3")
class ProductionBundleOnPython39(unittest.TestCase):
    """The production runner's bundle is what install_delegation_runner.sh archives (core/__init__.py and the two
    delegation packages) run by /usr/bin/python3 -I. Version 1 must keep compiling, importing and working there."""

    def test_the_version_1_bundle_compiles_imports_and_refuses_version_2_by_name(self):
        import shutil
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            (bundle / "core").mkdir(parents=True)
            shutil.copy(ROOT / "core" / "__init__.py", bundle / "core" / "__init__.py")
            for pkg in ("mc_delegation_runner", "mc_delegation_dispatcher"):
                shutil.copytree(ROOT / "core" / pkg, bundle / "core" / pkg,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            probe = (
                "import sys, py_compile, pathlib\n"
                "root = pathlib.Path(sys.argv[1]); sys.path.insert(0, str(root))\n"
                "for f in sorted(root.rglob('*.py')):\n"
                "    py_compile.compile(str(f), cfile=str(f) + '.check', doraise=True)\n"
                "from core.mc_delegation_runner import cli, runner, envelope, cost, commands, workspace, launch_env\n"
                "from core.mc_delegation_runner import dispatcher_adapter\n"
                "from core.mc_delegation_dispatcher import door, store, relay\n"
                "s = store.DispatcherStore(lambda: None, principal='robert', environment='prod')\n"
                "door.HttpDispatcher(door.DOORS[0], 't' * 40, root)\n"
                "a = store.Authority.load(root / 'core/mc_delegation_dispatcher/authority.prod.json')\n"
                "assert not a.speaks_v2\n"
                "store.DispatcherStore(lambda: None, principal='robert', environment='prod', authority=a)\n"
                "assert store.parse_at('2026-10-28T20:00:00.12+00:00').microsecond == 120000\n"
                "v2 = store.Authority.parse({'schema': store.AUTHORITY_SCHEMA, 'environment': 'dev',\n"
                "                            'protocols': [1, 2], 'lease_s': 60,\n"
                "                            'sweeper': {'sweep_s': 15, 'gap_threshold_s': 30}})\n"
                "try:\n"
                "    store.DispatcherStore(lambda: None, principal='robert', environment='dev', authority=v2)\n"
                "except RuntimeError as exc:\n"
                "    print(exc)\n"
                "print(sys.version_info[:2], 'ok')\n")
            out = subprocess.run([PY39, "-B", "-I", "-c", probe, str(bundle)], capture_output=True, text=True,
                                 timeout=60, cwd=tmp)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertEqual(out.stdout.split("\n")[:2], ["authority_requires_python_3_10", "(3, 9) ok"])
