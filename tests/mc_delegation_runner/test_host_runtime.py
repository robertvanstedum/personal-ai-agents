"""The version 2 host runtime (spec v0.5 §2.2, R2.3-a, R3.2-c, R4.5-e/f, §5.5, R6.3, R6.4, R7.6-a): host.yaml, the
lock, the pinned interpreter, the bundle digest, pending records, wake detection, every §5.5 step in order, DUP-5,
damaged records failing closed, the offline hold, G4, T0m's do-not-resume refusal, T6c (detection off), X5, T4b and
T4c at the host, a stale effect after a wake, and spend across resumes.

The authority here is an in-memory stand-in with the store's idempotence rules (exact replay already_applied, any
other body under a used event id refused, receipts with leases on a clock). Children are real fakework/1 processes,
short-lived, in a temporary folder, always reaped. Version 1 is untouched: test_runner.py runs unchanged."""
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.mc_delegation_dispatcher.store import Authority
from core.mc_delegation_runner import cli, cost, envelope, fakework, host as host_mod, journal as journal_mod, runner

ROOT = Path(__file__).resolve().parents[2]
VALUES = json.loads((ROOT / "tests" / "mc_delegation_dispatcher" / "authority.test_values.json").read_text())
CAPS = ["repo:personal-ai-agents", "scope:research", "scope:build", "actor:fakework", "host:mac-local"]
DNR = host_mod.PRESEEDED_DO_NOT_RESUME


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def mirror(tmp_path):
    repo = tmp_path / "mirror"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@x.test")
    git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("hello\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "init")
    return repo, git(repo, "rev-parse", "HEAD")


# ── an in-memory authority with the store's rules ──────────────────────────────────────────────────────────────────
class Down(ConnectionError):
    pass


class FakeAuthority:
    def __init__(self, ref, lease_s=60):
        self.ref, self.lease_s = ref, lease_s
        self.log, self.items, self.queue, self.events, self.facts = [], {}, [], {}, []
        self.receipts, self.reconciles, self.answers, self.hooks, self.answers_log = {}, [], [], {}, []
        self.down, self.renew_ok, self.lock = False, True, threading.RLock()
        self.decisions = []

    def enter(self, name, *args):
        hook = self.hooks.get(name)
        if hook is not None:
            hook(*args)
        if self.down:
            raise Down(name)
        self.log.append((name,) + tuple(args[:3]))

    def add(self, brief, *, reserved=1.5, route="fakework", unit="test_units", deadline_s=1800):
        rid = str(uuid.uuid4())
        env = {"schema": envelope.SCHEMA, "request_id": rid, "work_id": f"job-{len(self.items) + 1}", "generation": 1,
               "actor": "fakework", "principal_id": "robert", "environment": "dev", "scope": "build",
               "repository": "personal-ai-agents", "ref": self.ref, "brief": brief, "input_artifacts": [],
               "deadline": iso(time.time() + deadline_s), "cost_cap_usd": 1.0}
        self.items[rid] = {"env": env, "gen": 0, "state": "queued", "reserved": reserved, "unit": unit, "route": route,
                           "checkpoint": None, "result": None, "lease_until": 0.0, "resumed": {}}
        self.queue.append(rid)
        return rid

    def heartbeat(self, runner_id, state, capabilities, facts):
        self.enter("heartbeat", runner_id, facts["announced"])
        self.facts.append(dict(facts))
        return {"state": "recorded"}

    def claim(self, runner_id, capabilities):
        self.enter("claim", runner_id)
        if not self.queue:
            return None
        rid = self.queue.pop(0)
        it = self.items[rid]
        it.update(gen=it["gen"] + 1, state="claimed", lease_until=time.time() + self.lease_s)
        return {"envelope": {**it["env"], "generation": it["gen"]}, "route": it["route"], "unit": it["unit"],
                "reserved": it["reserved"], "lease_until": iso(it["lease_until"]), "card_id": None, "step": None,
                "attempt": None, "resume_from": None}

    def _replay(self, eid, record):
        old = self.events.get(eid)
        if old is None:
            return None
        return "already_applied" if old == record else "event_id_conflict"

    def progress(self, request_id, generation, *, runner_id, event_id, at, phase, checkpoint):
        self.enter("progress", request_id, generation, (checkpoint or {}).get("name"))
        record = ("progress", request_id, generation, at, phase, json.dumps(checkpoint, sort_keys=True))
        seen = self._replay(event_id, record)
        if seen:
            return {"state": seen if seen == "already_applied" else "refused", "event_id": event_id,
                    **({"code": seen} if seen != "already_applied" else {})}
        it = self.items[request_id]
        if generation != it["gen"]:
            return {"state": "refused", "event_id": event_id, "code": "stale_generation"}
        self.events[event_id] = record
        if checkpoint:
            it["checkpoint"] = {"name": checkpoint["name"], "digest": checkpoint["digest"]}
        return {"state": "applied", "event_id": event_id}

    def cancel_requested(self, request_id, generation):
        self.enter("cancel_requested", request_id, generation)
        return False

    def renew(self, request_id, generation, *, runner_id):
        self.enter("renew", request_id, generation)
        it = self.items[request_id]
        ok = self.renew_ok and generation == it["gen"] and it["state"] in ("claimed", "running")
        if ok:
            it["lease_until"] = time.time() + self.lease_s
        return ok

    def result(self, request_id, generation, *, runner_id, event_id, at, status, artifacts, detail, output_text, route,
               usage):
        self.enter("result", request_id, generation, status)
        record = ("result", request_id, generation, at, json.dumps([status, artifacts, detail, output_text, route,
                                                                    usage], sort_keys=True))
        seen = self._replay(event_id, record)
        if seen:
            return {"state": seen if seen == "already_applied" else "refused", "event_id": event_id,
                    **({"code": seen} if seen != "already_applied" else {})}
        it = self.items[request_id]
        if generation != it["gen"]:
            return {"state": "refused", "event_id": event_id, "code": "stale_generation"}
        self.events[event_id] = record
        it.update(state=status, result={"status": status, "usage": usage, "route": route})
        return {"state": "accepted", "event_id": event_id}

    def reconcile(self, runner_id, request_id, generation, stage, open_operations):
        self.enter("reconcile", request_id, generation)
        it = self.items[request_id]
        self.reconciles.append({"generation": generation, "stage": stage, "open": list(open_operations)})
        if generation in it["resumed"]:                                # X5: the same decision, asked again
            return it["resumed"][generation]
        if it["state"] == "unknown" and it.get("decision_id"):          # waits on the owner: the same decision
            return {"answer": "decide", "code": "effect_outcome_conflict", "decision_id": it["decision_id"],
                    "operations": []}
        if self.answers:
            answer = self.answers.pop(0)
            answer = answer(it, generation, stage, open_operations) if callable(answer) else answer
        elif [o for o, r in self.receipts.items() if r.get("rid") == request_id
              and r["state"] in ("dispatched", "unknown")]:      # the authority's own open receipts (store order)
            answer = {"answer": "decide", "code": "effect_unknown", "decision_id": str(uuid.uuid4()),
                      "operations": sorted(o for o, r in self.receipts.items() if r.get("rid") == request_id
                                           and r["state"] in ("dispatched", "unknown"))}
            it["state"] = "unknown"
        elif stage["child"] == "frozen" and it["lease_until"] > time.time():
            answer = {"answer": "continue", "generation": generation, "lease_until": iso(it["lease_until"])}
        else:
            answer = self.resume(it, generation)
        return answer

    def resume(self, it, generation):
        it.update(gen=generation + 1, state="claimed", lease_until=time.time() + self.lease_s)
        answer = {"answer": "resume", "generation": it["gen"], "lease_until": iso(it["lease_until"]),
                  "checkpoint": it["checkpoint"], "reserved": it["reserved"], "unit": it["unit"]}
        it["resumed"][generation] = answer
        return answer

    def effect(self, runner_id, request_id, generation, effect):
        what = f"{effect['op']}:{effect['key']}"
        self.enter("effect", request_id, generation, what)
        answer = self._effect(request_id, generation, effect)
        self.answers_log.append((what, generation, answer.get("state"), answer.get("code")))
        after = self.hooks.get("effect_after")
        if after is not None:
            after(request_id, generation, what)          # may raise Down: applied here, its answer lost
        return answer

    def _effect(self, request_id, generation, effect):
        op = effect["op"]
        op_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{request_id}:{effect['key']}"))
        out = {"op": op, "operation_id": op_id}
        it = self.items[request_id]
        if op in ("prepare", "claim", "dispatch"):        # a new effect: the owner, now, under its lease (store order)
            if generation != it["gen"]:
                return {"state": "refused", **out, "code": "stale_generation"}
            if it["lease_until"] <= time.time():
                return {"state": "refused", **out, "code": "lease_lapsed"}
        r = self.receipts.setdefault(op_id, {"state": None, "gen": 0, "token": None, "until": 0.0, "mode": None,
                                             "rid": request_id})
        live = r["until"] > time.time()
        if op == "prepare":
            r["state"] = r["state"] or "prepared"
            return {"state": "ok", **out, "receipt_state": r["state"]}
        if op in ("claim", "reconcile"):
            if r["state"] in ("verified", "failed"):
                return {"state": "refused", **out, "code": "receipt_terminal"}
            if live:
                return {"state": "refused", **out, "code": "receipt_busy"}
            if op == "reconcile":
                if r["state"] not in ("dispatched", "unknown"):
                    return {"state": "refused", **out, "code": "receipt_not_dispatched"}
                r["state"] = "unknown"
            r.update(gen=r["gen"] + 1, token=secrets.token_urlsafe(32)[:43].ljust(43, "A"), until=time.time() + 30,
                     mode="execute" if op == "claim" else "reconcile")
            return {"state": "ok", **out, "receipt_state": r["state"],
                    "lease": {"receipt_generation": r["gen"], "token": r["token"], "mode": r["mode"],
                              "lease_until": iso(r["until"])}}
        lease = effect["lease"]
        if not live or lease["token"] != r["token"] or lease["receipt_generation"] != r["gen"]:
            if op == "finish" and r["state"] == effect["state"]:
                return {"state": "already_applied", **out}
            if op == "finish" and r["state"] in ("verified", "failed") and effect["state"] in ("verified", "failed"):
                return self._conflict(request_id, op_id, r, effect, out)
            return {"state": "refused", **out, "code": "receipt_lease_stale"}
        if op == "dispatch":
            if r["state"] != "prepared":
                return {"state": "refused", **out, "code": "receipt_dispatch_refused"}
            r["state"] = "dispatched"
            return {"state": "ok", **out, "receipt_state": "dispatched", "lease_until": iso(r["until"])}
        if r["state"] not in ("dispatched", "unknown"):
            if r["state"] == effect["state"]:
                return {"state": "already_applied", **out}
            return self._conflict(request_id, op_id, r, effect, out)
        r.update(state=effect["state"], until=0.0, token=None)
        return {"state": "ok", **out, "receipt_state": effect["state"]}

    def _conflict(self, request_id, op_id, r, effect, out):
        """The store's rule (N-D2): a finish contradicting its receipt's recorded outcome is one decision on the item."""
        it = self.items[request_id]
        if it["state"] != "unknown":
            it.update(state="unknown", decision_id=str(uuid.uuid4()))
            self.decisions.append({"item": request_id, "decision_id": it["decision_id"], "operation_id": op_id,
                                   "recorded": r["state"], "reported": effect["state"]})
        return {"state": "refused", **out, "code": "receipt_outcome_conflict", "decision_id": it["decision_id"]}

    def calls(self, name):
        return [c for c in self.log if c[0] == name]


# ── building a host ────────────────────────────────────────────────────────────────────────────────────────────────
HOSTS = {"contact_interval_s": 1, "contact_timeout_s": 3, "renew_s": 1, "offline_hold_s": 1}


def host_doc(tmp_path, mirror_path, hosts=None, **over):
    values = tmp_path / "authority.json"
    if hosts is not None or not values.exists():
        values.write_text(json.dumps({**VALUES, "hosts": hosts or HOSTS}))
    bundle = tmp_path / "bundle"
    (bundle / "core").mkdir(parents=True, exist_ok=True)
    (bundle / "core" / "__init__.py").write_text("")
    doc = {"schema": "minimoi.host.v1", "host_id": "mac-test-1", "environment": "dev", "principal": "robert",
           "roles": ["authority", "worker", "witness"], "expected_availability": "intermittent",
           "unreachable_grace_s": 900, "service_manager": "launchd",
           "authority": {"endpoint_id": "dev-authority", "dsn_file": str(tmp_path / "dev.dsn"), "values": str(values)},
           "capabilities": CAPS, "concurrency": 1, "offline_scope_default": "none", "do_not_resume": [],
           "artifact_root": str(tmp_path / "artifacts"), "workspace_root": str(tmp_path / "work"),
           "mirrors": {"personal-ai-agents": str(mirror_path)},
           "interpreter": {"path": sys.executable, "version": "%d.%d" % sys.version_info[:2]},
           "bundle_root": str(bundle), "gap_detection": "on",
           "stop_timeout_s": 6, "effect_sink": {"path": str(tmp_path / "sink"), "lookup": True}}
    doc.update(over)
    return doc


def make_host(tmp_path, mirror_path, hosts=None, **over):
    path = tmp_path / "host.yaml"
    path.write_text(json.dumps(host_doc(tmp_path, mirror_path, hosts, **over)))
    return host_mod.load(path)


RUNTIMES = []


@pytest.fixture(autouse=True)
def _reap(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))       # G6's fixed lock folder lives under it, never the Mac's
    yield
    while RUNTIMES:
        rt = RUNTIMES.pop()
        rt.stop_contact()
        if rt.child is not None:
            rt.child.end_group()


def make_rt(tmp_path, mirror_path, fake, hosts=None, **over):
    h = make_host(tmp_path, mirror_path, hosts, **over)
    auth = Authority.load(h.authority.values)
    rt = cli.runtime_for(h, auth, fake, digest="sha256:" + "0" * 64)
    rt.cfg.tick_s, rt.cfg.kill_grace_s = 0.05, 1.0
    RUNTIMES.append(rt)
    return rt


def program(*steps):
    return "\n".join(("fakework/1",) + steps)


def run_pass(rt, timeout=40):
    """One pass on a thread, with a bound: never a hung test."""
    box = {}
    t = threading.Thread(target=lambda: box.update(out=rt.pass_once()), daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "the pass did not finish"
    return box["out"]


def notes(rt, task):
    return [e for e in rt.journal(task).entries()]


def kinds(rt, task):
    out = []
    for e in rt.journal(task).entries():
        out.append(e["kind"] if e["type"] != "mark" else "mark")
    return out


# ── host.yaml, the lock, the interpreter, the bundle digest ─────────────────────────────────────────────────────────
def test_host_yaml_is_strict_json_and_the_two_billed_sessions_are_always_refused(tmp_path, mirror):
    h = make_host(tmp_path, mirror[0], do_not_resume=["ABCDEF12-0000"])
    assert h.runner_id == "mac-test-1:runner" and h.actors() == ("fakework",) and h.scopes() == ("research", "build")
    assert set(DNR) <= set(h.do_not_resume) and "abcdef12-0000" in h.do_not_resume
    assert make_host(tmp_path, mirror[0], do_not_resume=[]).do_not_resume == DNR       # never fewer than the two
    bad = [({"extra": 1}, "host_config_invalid"), ({"schema": "minimoi.host.v2"}, "host_schema_invalid"),
           ({"concurrency": 2}, "host_concurrency_unsupported"), ({"host_id": "a/b"}, "host_id_invalid"),
           ({"offline_scope_default": "all"}, "host_offline_scope_invalid"),
           ({"interpreter": {"path": "/usr/bin/python3", "version": "3.9"}}, "interpreter_too_old"),
           ({"environment": "prod"}, "host_test_instrument_in_production"),
           ({"authority": {"endpoint_id": "x", "values": "/v", "url": "https://dev.minimoi.ai/internal/mc/v1/"
                           "delegation-runner", "dsn_file": "/d"}}, "host_authority_transport_invalid"),
           ({"authority": {"endpoint_id": "x", "values": "/v", "url": "https://minimoi.ai/internal/mc/v1/"
                           "delegation-runner", "credential_ref": "keychain:minimoi_runtime/dev"}},
            "host_authority_environment_mismatch"),
           ({"gap_detection": "maybe"}, "host_gap_detection_invalid")]
    for over, code in bad:
        with pytest.raises(host_mod.HostRefused) as exc:
            host_mod.parse(host_doc(tmp_path, mirror[0], **over))
        assert str(exc.value) == code, over
    doc = host_doc(tmp_path, mirror[0])
    del doc["artifact_root"]
    with pytest.raises(host_mod.HostRefused, match="host_config_value_missing"):     # a missing value admits nothing
        host_mod.parse(doc)
    path = tmp_path / "dup.yaml"
    path.write_text('{"schema": "minimoi.host.v1", "schema": "minimoi.host.v1"}')
    with pytest.raises(host_mod.HostRefused, match="host_config_duplicate_key"):
        host_mod.load(path)
    # A worker that reaches the authority over HTTPS builds no authority store: 3.9 is enough there.
    door = {"endpoint_id": "dev-authority", "values": "/v", "url": "https://dev.minimoi.ai/internal/mc/v1/delegation-"
            "runner", "credential_ref": "keychain:minimoi_runtime/dev"}
    assert host_mod.parse(host_doc(tmp_path, mirror[0], roles=["worker", "witness"], authority=door,
                                   interpreter={"path": "/usr/bin/python3", "version": "3.9"})).interpreter.version \
        == (3, 9)


def test_one_instance_per_host_and_environment_and_the_pinned_interpreter(tmp_path, mirror):
    h = make_host(tmp_path, mirror[0])
    first, second = host_mod.HostLock(h), host_mod.HostLock(h)
    first.acquire("now")
    try:
        with pytest.raises(host_mod.HostRefused, match="host_lock_held"):
            second.acquire("now")
        assert host_mod.HostLock(h).holder() == {"state": "held", "pid": os.getpid(), "since": "now"}
    finally:
        first.release()
    assert host_mod.HostLock(h).holder() == {"state": "free"}
    second.acquire("again")                 # released by its owner (or the kernel at its death): free again
    second.release()
    assert host_mod.interpreter_problems(h) == []
    assert host_mod.interpreter_problems(h, executable="/usr/bin/python3", version=(3, 9)) == [
        "interpreter_unpinned", "interpreter_version_mismatch", "interpreter_too_old"]


def test_the_bundle_digest_is_p0s_tree_digest(tmp_path):
    root = tmp_path / "b"
    (root / "core" / "__pycache__").mkdir(parents=True)
    (root / "core" / "a.py").write_text("a\n")
    (root / "run.py").write_text("r\n")
    (root / "core" / "__pycache__" / "a.cpython-314.pyc").write_bytes(b"x")
    (root / "core" / "b.pyc").write_bytes(b"x")
    (root / ".DS_Store").write_bytes(b"x")
    os.symlink(root / "run.py", root / "link.py")
    import hashlib
    lines = sorted(f"{p}\t{hashlib.sha256((root / p).read_bytes()).hexdigest()}\n" for p in ("core/a.py", "run.py"))
    assert host_mod.tree_digest(root) == ("sha256:" + hashlib.sha256("".join(lines).encode()).hexdigest(), 2)


# ── pending records and the gap detector ────────────────────────────────────────────────────────────────────────────
def test_pending_records_are_write_ahead_marked_once_and_damage_fails_closed(tmp_path):
    j = journal_mod.Journal(tmp_path / "t.pending.jsonl")
    assert j.entries() == []                                    # only a missing file means "no records"
    a = j.call("progress", str(uuid.uuid4()), "rid", 1, "2026-10-29T00:00:00+00:00", {"phase": "x", "n": 0.125})
    j.note("freeze", "2026-10-29T00:00:01+00:00", child="frozen")
    assert [c["event_id"] for c in j.pending()] == [a["event_id"]]
    j.mark(a["event_id"], "applied", {"state": "applied"})
    assert j.pending() == [] and j.calls()[0]["args"] == {"phase": "x", "n": 0.125}
    raw = (tmp_path / "t.pending.jsonl").read_bytes()
    (tmp_path / "t.pending.jsonl").write_bytes(raw.replace(b'"x"', b'"y"', 1))
    with pytest.raises(journal_mod.JournalDamaged, match="pending_line_invalid:1"):
        j.entries()
    (tmp_path / "t.pending.jsonl").write_bytes(raw[:-5])
    with pytest.raises(journal_mod.JournalDamaged, match="pending_file_torn"):
        j.entries()
    assert journal_mod.classify("progress", {"state": "refused", "code": "lease_lapsed"}) == "final"
    assert journal_mod.classify("result", {"state": "refused", "code": "event_time_invalid"}) == "pending"
    assert journal_mod.classify("result", {"state": "accepted", "code": "retry"}) == "applied"


def test_a_tick_overrun_is_a_wake_and_detection_off_reports_nothing():
    clock = {"wall": 1000.0, "mono": 50.0}
    det = runner.GapDetector(30, True, wall=lambda: clock["wall"], mono=lambda: clock["mono"], hint=lambda: "pmset")
    assert det.tick() is None
    clock["wall"] += 29
    clock["mono"] += 29
    assert det.tick() is None                                      # jitter under the threshold is no wake
    clock["wall"] += 600                                           # the loop did not run; monotonic time did not move
    gap = det.tick()
    assert gap == {"gap_s": 600.0, "wall_minus_mono_s": 600.0, "hint": "pmset"}
    off = runner.GapDetector(30, False, wall=lambda: clock["wall"], mono=lambda: clock["mono"])
    off.tick()
    clock["wall"] += 10_000
    assert off.tick() is None


# ── a pass end to end ────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_fakework_item_completes_with_its_usage_and_replay_again_applies_nothing_dup5(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1", "report_usage 0.25", "findings ok", "result ok"))
    out = run_pass(rt)
    assert out["state"] == "completed" and out["answer"]["state"] == "accepted" and out.get("workspace_removed")
    it = fake.items[rid]
    assert it["result"] == {"status": "completed", "route": "fakework",
                            "usage": {"amount": 0.25, "unit": "test_units", "children_stopped": True}}
    task = f"{rid}-g1"
    assert {a["kind"] for a in out["artifacts"]} == {"output", "patch", "findings"}
    assert (tmp_path / "artifacts" / f"{task}.closed.json").exists() and rt.owned() == []
    calls = rt.journal(task).calls()
    assert [c["kind"] for c in calls] == ["progress", "progress", "result"] and all(c["mark"] == "applied"
                                                                                    for c in calls)
    cp = calls[1]["args"]["checkpoint"]
    assert cp["name"] == "s1" and cp["paths"] == ["fakework/s1.txt"]
    assert (tmp_path / "artifacts" / f"{rid}.cp.{cp['digest'][7:]}.patch").exists()
    # DUP-5: every record resent, byte for byte: nothing applies twice.
    again = rt.replay(task, again=True)
    assert again == {"sent": 3, "applied": 0, "already_applied": 3, "final": 0, "pending": 0}
    # Contact facts carry the bundle digest; the host-local view is labelled and dated.
    rt.contact()
    assert fake.facts[-1]["bundle_digest"] == "sha256:" + "0" * 64 and fake.facts[-1]["announced"] == "online"
    rt.write_state(force=True)
    state = json.loads((tmp_path / "artifacts" / "host-state.json").read_text())
    assert state["source"] == "host-local" and state["schema"] == "minimoi.runtime.state.v1"


def test_the_5_5_steps_run_in_order_freeze_replay_reconcile_then_thaw(tmp_path, mirror):
    """A reconnect: the checkpoint's progress failed (authority down), the child froze at that first failed call
    (offline scope none), the record waited, and on the first successful call the host froze, replayed, reconciled
    (continue: the lease never lapsed) and thawed. No claim in between (step 6)."""
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1", "checkpoint s2", "result ok"))
    state = {"failed": False}

    def drop(request_id, generation, name):
        if name == "s1" and not state["failed"]:
            state["failed"] = fake.down = True
            threading.Timer(0.4, lambda: setattr(fake, "down", False)).start()
    fake.hooks["progress"] = drop
    rt.start_contact()                               # the contact timer notices the authority is back
    out = run_pass(rt)
    assert out["state"] == "completed", out
    task = f"{rid}-g1"
    entries = rt.journal(task).entries()
    order = [e["kind"] if e["type"] != "mark" else f"mark:{e['mark']}" for e in entries]
    s1 = next(e for e in entries if e["type"] == "call" and (e["args"].get("checkpoint") or {}).get("name") == "s1")
    first_freeze = order.index("freeze")
    s1_mark = next(i for i, e in enumerate(entries) if e["type"] == "mark" and e["event_id"] == s1["event_id"])
    reconcile, thaw = order.index("reconcile"), order.index("thaw")
    assert first_freeze < s1_mark < reconcile < thaw, order          # freeze, replay, reconcile, workspace
    assert entries[first_freeze]["fields"]["reason"] == "authority_unreachable"
    assert entries[reconcile]["fields"]["answer"]["answer"] == "continue"
    assert entries[reconcile]["fields"]["stage"] == {"child": "frozen",
                                                     "checkpoint": {"name": "s1", "digest": s1["args"]["checkpoint"]
                                                                    ["digest"]}}
    names = [c[0] for c in fake.log]
    assert names.count("claim") == 1 and names.index("claim") < names.index("reconcile")


def test_a_wake_resumes_at_the_checkpoint_rebuilt_and_a_resumed_child_gets_only_the_remainder(tmp_path, mirror,
                                                                                              monkeypatch):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1", "report_usage 0.5", "checkpoint s2", "result ok"), reserved=1.5)
    caps = []
    real = cost.UNIT_ENFORCERS["fakework"]

    class Recording:
        def apply(self, argv, env, cap, unit):
            caps.append((cap, unit))
            return real.apply(argv, env, cap, unit)
    monkeypatch.setitem(cost.UNIT_ENFORCERS, "fakework", Recording())

    once = []

    def sleep_at_s2(request_id, generation, name):
        if name == "s2" and not once:
            once.append(1)
            # Written after s1 and before the wake is acted on: the rebuilt workspace must not keep it.
            (tmp_path / "work" / f"{rid}-g1" / "stray.txt").write_text("after the checkpoint\n")
            fake.down = True                         # s2 never reaches the authority: the lease lapses meanwhile
            fake.items[rid]["lease_until"] = 0.0
            rt.on_wake({"gap_s": 600.0, "wall_minus_mono_s": 600.0, "hint": None})
            threading.Timer(0.3, lambda: setattr(fake, "down", False)).start()
    fake.hooks["progress"] = sleep_at_s2
    rt.start_contact()
    out = run_pass(rt)
    assert out["state"] == "completed" and out["generation"] == 2, out
    g1, g2 = f"{rid}-g1", f"{rid}-g2"
    rec = next(e for e in rt.journal(g1).entries() if e["type"] == "note" and e["kind"] == "reconcile")
    assert rec["fields"]["answer"]["answer"] == "resume" and rec["fields"]["stage"]["child"] == "frozen"
    # s2's record waited while the authority was away and was replayed before the question: the newest acknowledged,
    # rebuildable checkpoint is s2, and the authority resumes from it.
    assert rec["fields"]["stage"]["checkpoint"]["name"] == "s2"
    assert json.loads((tmp_path / "artifacts" / f"{g1}.closed.json").read_text())["reason"] == "resumed"
    claim2 = json.loads((tmp_path / "artifacts" / f"{g2}.claim.json").read_text())
    assert claim2["resume_from"]["name"] == "s2" and claim2["resumed_from_generation"] == 1
    # Spend across resumes: 1.5 reserved for the item; generation 1 used 0.5 that no result settled.
    assert caps == [(1.5, "test_units"), (1.0, "test_units")]
    assert fake.items[rid]["result"]["usage"]["amount"] == 0.5
    log = (tmp_path / "artifacts" / f"{g2}.log").read_text()
    assert "restarting after checkpoint s2" in log and "checkpoint s1" not in log
    patch = (tmp_path / "artifacts" / f"{g2}.patch").read_text()
    assert "fakework/s1.txt" in patch and "fakework/s2.txt" in patch and "stray.txt" not in patch
    assert (tmp_path / "work" / g1).exists()                              # the old workspace is kept


def test_t6c_with_detection_off_a_refused_renew_triggers_reconcile(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake, gap_detection="off")
    rid = fake.add(program("checkpoint s1 hold_s=3", "result ok"))

    def lapse(request_id, generation, name):
        if name == "s1" and generation == 1:
            fake.renew_ok = False                     # the laptop slept and nobody noticed: the authority did
            fake.items[rid]["lease_until"] = 0.0
    fake.hooks["progress"] = lapse
    out = run_pass(rt)
    assert out["state"] == "completed" and out["generation"] == 2, out
    entries = rt.journal(f"{rid}-g1").entries()
    freeze = next(e for e in entries if e["type"] == "note" and e["kind"] == "freeze")
    assert freeze["fields"]["reason"] == "renew_refused"
    assert [e["kind"] for e in entries if e["type"] == "note"][-2:] == ["freeze", "reconcile"]
    assert not any(e["kind"] == "wake" for e in entries if e["type"] == "note")


def test_x5_the_same_question_after_an_authority_restart_gets_the_same_resume(tmp_path, mirror):
    """The authority decides resume, then restarts before the host hears it: the host asks again at the same
    generation and gets the same answer; the generation rose once, and one next generation is launched."""
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake, gap_detection="off")
    rid = fake.add(program("checkpoint s1 hold_s=2", "result ok"))
    cut = {"done": False}

    def lapse(request_id, generation, name):
        if name == "s1" and generation == 1:
            fake.renew_ok = False
            fake.items[rid]["lease_until"] = 0.0

    def lost_answer(request_id, generation):
        if not cut["done"]:
            cut["done"] = True
            fake.resume(fake.items[rid], generation)          # decided and recorded at the authority...
            fake.down = True                                  # ...and the answer never arrived
            threading.Timer(0.3, lambda: setattr(fake, "down", False)).start()
    fake.hooks["progress"], fake.hooks["reconcile"] = lapse, lost_answer
    rt.start_contact()
    out = run_pass(rt)
    assert out["state"] == "completed" and out["generation"] == 2, out
    assert fake.items[rid]["gen"] == 2
    assert sorted(p.name for p in (tmp_path / "artifacts").glob(f"{rid}-g*.claim.json")) == [
        f"{rid}-g1.claim.json", f"{rid}-g2.claim.json"]
    assert [r["generation"] for r in fake.reconciles] == [1]          # asked once after the lost answer, same gen


def test_a_damaged_pending_file_fails_closed_and_nothing_new_is_claimed(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1 hold_s=5", "result ok"))
    fake.hooks["progress"] = lambda r, g, n: n == "s1" and threading.Timer(0.1, rt.request_stop).start()
    assert run_pass(rt)["state"] == "stopped"
    path = tmp_path / "artifacts" / f"{rid}-g1.pending.jsonl"
    raw = path.read_bytes()
    path.write_bytes(raw[:40] + (b"X" if raw[40:41] != b"X" else b"Y") + raw[41:])
    fake.add(program("result ok"))
    rt2 = make_rt(tmp_path, mirror[0], fake)
    before = len(fake.calls("claim"))
    out = run_pass(rt2)
    assert out["state"] == "blocked" and out["damaged"] == [f"{rid}-g1"]
    assert len(fake.calls("claim")) == before and not fake.calls("reconcile")
    assert run_pass(rt2)["state"] == "blocked"                         # and it stays so until an operator acts
    rt2.write_state(force=True)
    item = json.loads((tmp_path / "artifacts" / "host-state.json").read_text())["items"][0]
    assert item["journal"].startswith("pending_line_")


def test_offline_the_child_freezes_stops_at_its_checkpoint_after_the_hold_and_nothing_reaches_a_target(tmp_path,
                                                                                                    mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    grant = str(uuid.uuid4())
    rid = fake.add(program("checkpoint s1", f"effect e1 grant={grant}", "result ok"))

    once = []

    def drop(request_id, generation, name):
        if name == "s1" and not once:
            once.append(1)
            fake.down = True
    fake.hooks["progress"] = drop
    t0 = time.monotonic()
    out = run_pass(rt)
    assert out == {"state": "offline", "request_id": rid, "stopped": "offline_hold"}
    assert time.monotonic() - t0 >= 1.0                                # offline_hold_s, then stopped
    task = f"{rid}-g1"
    ns = [e for e in rt.journal(task).entries() if e["type"] == "note"]
    assert [n["kind"] for n in ns][:2] == ["checkpoint", "freeze"] and ns[1]["fields"]["reason"] == \
        "authority_unreachable"
    stop = next(n for n in ns if n["kind"] == "stop")
    assert stop["fields"]["reason"] == "offline_hold" and stop["fields"]["child"] == "stopped"
    assert run_pass(rt) == {"state": "offline"}                        # nothing new admitted while disconnected
    assert fakework.FileSink(tmp_path / "sink").lines() == [] and not fake.calls("effect")
    # Restored: the pending checkpoint is replayed, reconcile resumes it, and the effect happens once.
    fake.down = False
    rt.contact()
    out = run_pass(rt)
    assert out["state"] == "completed" and out["generation"] == 2, out
    lines = fakework.FileSink(tmp_path / "sink").lines()
    assert [x["answer"] for x in lines] == ["committed"]


def test_workspace_only_keeps_editing_offline_and_its_result_waits(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake, offline_scope_default="workspace_only")
    rid = fake.add(program("checkpoint s1", "checkpoint s2", "result ok"))
    once = []
    fake.hooks["progress"] = lambda r, g, n: n == "s1" and not once and (once.append(1) or setattr(fake, "down", True))
    out = run_pass(rt)
    assert out["state"] == "completed" and not out["delivered"]       # done offline; its result waits, pending
    task = f"{rid}-g1"
    assert [c["kind"] for c in rt.journal(task).pending()] == ["progress", "progress", "result"]
    assert run_pass(rt) == {"state": "offline"}
    fake.down = False
    rt.contact()
    out = run_pass(rt)                                                 # replay first: the result closes the item
    assert out["state"] == "idle" and fake.items[rid]["result"]["status"] == "completed"
    assert rt.journal(task).pending() == [] and rt.owned() == []


def test_g4_a_graceful_stop_announces_stops_at_a_checkpoint_and_keeps_the_records(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1 hold_s=20", "result ok"))
    fake.hooks["progress"] = lambda r, g, n: n == "s1" and threading.Timer(0.2, rt.request_stop).start()
    t0 = time.monotonic()
    out = run_pass(rt)
    assert out["state"] == "stopped" and out["child"] == "stopped"
    assert time.monotonic() - t0 < 6 + 2                                # within stop_timeout_s
    assert [f["announced"] for f in fake.facts][-2:] == ["stopping", "stopped"]
    task = f"{rid}-g1"
    stop = next(e for e in rt.journal(task).entries() if e["type"] == "note" and e["kind"] == "stop")
    assert stop["fields"]["reason"] == "graceful" and stop["fields"]["checkpoint"]["name"] == "s1"
    assert "stopped at the runtime's request" in (tmp_path / "artifacts" / f"{task}.log").read_text()
    assert rt.owned() == [task] and rt.journal(task).pending() == []   # the item is owned, paused, not ended


def test_t0m_a_do_not_resume_session_is_refused_before_any_spawn(tmp_path, mirror, monkeypatch):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    spawned = []
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(
        AssertionError("spawned")))
    for sid in DNR:
        rid = fake.add(program(f"resume_session {sid.upper()}", "result ok"))
        out = run_pass(rt)
        assert (out["state"], out["code"]) == ("refused", "do_not_resume")
        assert fake.items[rid]["result"]["status"] == "refused"
        assert not (tmp_path / "artifacts" / f"{rid}-g1.log").exists()
        assert not (tmp_path / "work" / f"{rid}-g1").exists()
    assert spawned == []
    # Whatever the adapter: the check reads what the launch would run, not fakework's grammar.
    assert rt._do_not_resume("claude", "--resume", DNR[0]) == DNR[0]
    assert rt._do_not_resume("codex", "exec", "resume", DNR[1].upper()) == DNR[1]


class Crash(BaseException):
    pass


@pytest.mark.parametrize("when", ["before_its_finish_is_written", "with_its_finish_written_not_sent"])
def test_t4b_a_crash_at_a_committed_effect_reads_it_back_and_resumes_after_it(tmp_path, mirror, monkeypatch, when):
    """The runtime dies the moment e1 is committed at the sink: either before it learned the sink's answer (the
    restart reads the target back) or with the finish on file and unsent (the restart records it under a reconcile
    lease, since the execute lease's token died with the process)."""
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake, gap_detection="off")
    grant = str(uuid.uuid4())
    rid = fake.add(program("checkpoint s1", f"effect e1 grant={grant}", "checkpoint s2", "result ok"))

    def crash(*a, **k):
        raise Crash()
    if when == "before_its_finish_is_written":
        monkeypatch.setattr(rt, "_effect_end", crash)
    else:
        fake.hooks["effect"] = lambda r, g, what: what == "finish:e1" and crash()
    with pytest.raises(Crash):
        run_pass_raw(rt)
    sink = fakework.FileSink(tmp_path / "sink")
    assert [x["answer"] for x in sink.lines()] == ["committed"]
    fake.hooks.pop("effect", None)
    fake.items[rid]["lease_until"] = 0.0                                 # held → lapsed while it was down
    rt2 = make_rt(tmp_path, mirror[0], fake, gap_detection="off")
    assert run_pass(rt2) == {"state": "recovering"}                      # the old receipt lease still runs
    for r in fake.receipts.values():
        r["until"] = 0.0
    out = run_pass(rt2)
    assert out["state"] == "completed" and out["generation"] == 2, out
    assert [x["answer"] for x in sink.lines()] == ["committed"]          # DUP-1: e1 once
    assert {r["state"] for r in fake.receipts.values()} == {"verified"}
    assert "effect e1: already verified, not sent again" in (tmp_path / "artifacts" / f"{rid}-g2.log").read_text()
    rec = next(e for e in rt2.journal(f"{rid}-g1").entries() if e["type"] == "note" and e["kind"] == "reconcile")
    assert rec["fields"]["open_operations"] == [] and rec["fields"]["stage"]["child"] == "stopped"


def run_pass_raw(rt):
    return rt.pass_once()


def test_a_child_that_exited_before_its_result_was_recorded_is_reported_never_rerun(tmp_path, mirror, monkeypatch):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1", "result ok"))

    def crash(*a, **k):
        raise Crash()                         # the runtime dies after the exit is on file, before the result is
    monkeypatch.setattr(rt, "_result", crash)
    with pytest.raises(Crash):
        run_pass_raw(rt)
    rt2 = make_rt(tmp_path, mirror[0], fake)
    assert run_pass(rt2) == {"state": "idle"}
    assert fake.items[rid]["result"]["status"] == "completed" and fake.items[rid]["gen"] == 1
    assert not fake.reconciles                                         # its known exit closed it on replay
    assert len(list((tmp_path / "artifacts").glob(f"{rid}-g*.log"))) == 1   # never rerun


def test_t4c_without_readback_an_open_effect_goes_to_the_owner_and_the_workspace_is_kept(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake, gap_detection="off",
                 effect_sink={"path": str(tmp_path / "sink"), "lookup": False})
    grant = str(uuid.uuid4())
    rid = fake.add(program("checkpoint s1", f"effect e1 grant={grant}", "result ok"))

    def crash(*a, **k):
        raise Crash()                         # committed at the sink; the host never learned its answer
    rt._effect_end = crash
    with pytest.raises(Crash):
        run_pass_raw(rt)
    fake.items[rid]["lease_until"] = 0.0
    for r in fake.receipts.values():
        r["until"] = 0.0
    rt2 = make_rt(tmp_path, mirror[0], fake, gap_detection="off",
                  effect_sink={"path": str(tmp_path / "sink"), "lookup": False})
    out = run_pass(rt2)
    assert out["state"] == "idle"
    closed = json.loads((tmp_path / "artifacts" / f"{rid}-g1.closed.json").read_text())
    assert closed["reason"] == "decide" and closed["answer"]["code"] == "effect_unknown"
    assert fake.reconciles[-1]["open"] and fake.items[rid]["gen"] == 1   # DUP-6: no new generation
    assert (tmp_path / "work" / f"{rid}-g1").exists()


@pytest.mark.parametrize("when", ["while_it_waits", "before_it_reads_the_hold"])
def test_a_stale_effect_after_a_wake_is_refused_and_the_next_effect_happens_once(tmp_path, mirror, when):
    """T6a/T6m's host half. The sleep lands while the worker holds its receipt lease: either while it waits, or
    before it has even read the answer that granted the lease (the PostgreSQL run's timing: a freeze before the ack)."""
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    grant = str(uuid.uuid4())
    rid = fake.add(program("checkpoint s1", f"stale_effect_on_wake e2b grant={grant} wait_s=15 gap_s=1",
                           f"effect e2 grant={grant}", "result ok"))
    wake = {"gap_s": 600.0, "wall_minus_mono_s": 600.0, "hint": None}

    def held(request_id, generation, what):
        if what == "claim:e2b" and when == "while_it_waits":
            threading.Timer(0.4, lambda: rt.on_wake(wake)).start()
        elif what == "claim:e2b":
            rt.on_wake(wake)                      # frozen now; the hold's answer is written after this call

    def slow_reconcile(request_id, generation):
        time.sleep(1.5)                                   # the child stays frozen longer than its gap_s
        for r in fake.receipts.values():
            r["until"] = min(r["until"], time.time() - 1)  # the receipt lease it held before sleeping has expired
    fake.hooks["effect"], fake.hooks["reconcile"] = held, slow_reconcile
    out = run_pass(rt)
    assert out["state"] == "completed" and out["generation"] == 1, out
    log = (tmp_path / "artifacts" / f"{rid}-g1.log").read_text()
    assert "stale e2b: refused receipt_lease_stale" in log
    lines = fakework.FileSink(tmp_path / "sink").lines()
    assert [(x["answer"]) for x in lines] == ["committed"]                  # e2 once; e2b never reached the sink
    order = [e["kind"] for e in rt.journal(f"{rid}-g1").entries() if e["type"] == "note"]
    assert order.index("freeze") < order.index("reconcile") < order.index("thaw")


def test_production_configuration_is_version_1(tmp_path, monkeypatch):
    """No --host-config: today's runner, its flags and its adapters; fakework is never offered or parsed there."""
    seen = []
    monkeypatch.setattr(cli, "_run_v1", lambda argv=None: seen.append(argv) or 0)
    legacy = ["--door", "https://minimoi.ai/internal/mc/v1/delegation-runner", "--principal", "robert"]
    assert cli.main(legacy) == 0 and cli.main(["run", *legacy]) == 0 and seen == [legacy, legacy]
    assert set(cost.ENFORCERS) == {"claude-code", "codex"} and "fakework" not in envelope.ACTORS
    doc = {"schema": envelope.SCHEMA, "request_id": str(uuid.uuid4()), "work_id": "w", "generation": 1,
           "actor": "fakework", "principal_id": "robert", "environment": "dev", "scope": "build", "repository": "r",
           "ref": "a" * 40, "brief": "b", "input_artifacts": [],
           "deadline": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "cost_cap_usd": 1.0}
    with pytest.raises(envelope.EnvelopeRefused, match="envelope_actor_invalid"):
        envelope.parse(doc, allowed_repositories={"r": "x"}, max_cost_usd=5.0, now=datetime.now(timezone.utc))
    assert envelope.parse(doc, allowed_repositories={"r": "x"}, max_cost_usd=5.0, now=datetime.now(timezone.utc),
                          actors=envelope.V2_ACTORS).actor == "fakework"
    assert runner.RunnerConfig(runner_id="r", actors=(), scopes=(), repositories={}, workspace_root=tmp_path,
                               artifact_root=tmp_path, max_cost_usd=1.0).runtime is None


def test_doctor_state_version_and_journal_commands(tmp_path, mirror, capsys, monkeypatch):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1", "result ok"))
    run_pass(rt)
    monkeypatch.setenv("PLANTED_TEST_TOKEN", "planted-value-never-printed")
    cfgpath = str(tmp_path / "host.yaml")
    assert cli.main(["doctor", "--host-config", cfgpath, "--env-names", "--do-not-resume"]) == 0
    raw = capsys.readouterr().out
    doc = json.loads(raw)
    assert "planted-value-never-printed" not in raw and "PLANTED_TEST_TOKEN" in doc["env_names"][
        "runtime_credential_shaped"]
    assert doc["env_names"]["child_credential_shaped"] == [] and doc["do_not_resume"] == list(DNR)
    assert doc["interpreter"]["problems"] == [] and doc["bundle"]["digest"].startswith("sha256:")
    assert doc["values"]["contact_interval_s"] == 1 and doc["lock"] == {"state": "free"} and doc["ok"]
    assert cli.main(["state", "--host-config", cfgpath, "--json"]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["source"] == "host-local" and state["view_age_s"] is not None and state["view_age_s"] >= 0
    assert cli.main(["version", "--host-config", cfgpath]) == 0
    assert json.loads(capsys.readouterr().out)["protocols"] == [1, 2]
    assert cli.main(["journal", "--host-config", cfgpath, "show"]) == 0
    shown = json.loads(capsys.readouterr().out.splitlines()[0])
    assert shown["task"] == f"{rid}-g1" and {r["mark"] for r in shown["records"]} == {"applied"}
    # doctor fails closed on an interpreter that is not the pinned one.
    bad = tmp_path / "bad.yaml"
    bad.write_text(json.dumps(host_doc(tmp_path, mirror[0], interpreter={"path": "/usr/bin/python3",
                                                                         "version": "3.13"})))
    assert cli.main(["doctor", "--host-config", str(bad)]) == 1
    assert "interpreter_unpinned" in json.loads(capsys.readouterr().out)["problems"]
    # the run command refuses the same before acting, and while another instance holds the lock.
    with pytest.raises(SystemExit, match="interpreter_unpinned"):
        cli.main(["run", "--host-config", str(bad), "--once"])
    lock = host_mod.HostLock(host_mod.load(cfgpath))
    lock.acquire("now")
    try:
        with pytest.raises(SystemExit, match="host_lock_held"):
            cli.main(["journal", "--host-config", cfgpath, "replay", "--again"])
    finally:
        lock.release()


def test_the_fakework_program_is_strict_and_the_census_steps_are_refused():
    ok = fakework.parse_program(program("checkpoint s1 hold_s=2 paths=a.txt,b/c.txt", "report_usage unknown",
                                        "findings", "resume_session 01a17dde-fb26-7923-8105-c7478428d17d",
                                        "crash: self", "hang_s 1", "result ok"))
    assert [k for k, _ in ok] == ["checkpoint", "report_usage", "findings", "resume_session", "crash", "hang_s",
                                  "result"]
    for bad, code in (("spawn {id: a}", "fakework_step_unsupported:spawn"), ("checkpoint ../x", "fakework_checkpoint_"
                      "invalid"), ("effect e1", "fakework_effect_invalid"), ("result ok\nresult ok",
                                                                             "fakework_result_not_last"),
                      ("rm -rf /", "fakework_step_unknown")):
        with pytest.raises(fakework.ProgramRefused, match=code.split(":")[0]):
            fakework.parse_program(program(*bad.split("\n")))
    with pytest.raises(fakework.ProgramRefused):
        fakework.parse_program("echo hello")
    sink = fakework.FileSink(Path(os.environ.get("TMPDIR", "/tmp")) / f"sink-{uuid.uuid4()}")
    try:
        future, past = iso(time.time() + 30), iso(time.time() - 1)
        assert sink.post("op1", "i", 1, future, "d1") == "committed"
        assert sink.post("op1", "i", 1, future, "d1") == "duplicate"
        assert sink.post("op1", "i", 1, future, "d2") == "operation_reused"
        assert sink.post("op2", "i", 1, past, "d1") == "lease_expired"
        assert (sink.get("op1"), sink.get("op2")) == ("committed", "absent")
        assert fakework.FileSink(sink.path, lookup=False).get("op1") == "unsupported"
    finally:
        shutil.rmtree(sink.path, ignore_errors=True)


# ── fix round (REVIEW_independent_S1-D.md) ─────────────────────────────────────────────────────────────────────────
def wait_for(path, timeout=20):
    end = time.monotonic() + timeout
    while not Path(path).exists():
        assert time.monotonic() < end, f"never appeared: {Path(path).name}"
        time.sleep(0.02)


def test_b1_a_graceful_stop_while_frozen_pauses_the_item_and_never_records_a_failure(tmp_path, mirror):
    """SIGTERM (a logout, a restart) while the child is frozen at the authority's outage: the runtime's own kill is
    never the child's exit. The stage says stopped, nothing is reported, and the next start resumes it (G4, E14)."""
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1", "checkpoint s2", "result ok"))
    once = []

    def drop(request_id, generation, name):
        if name == "s2" and not once:
            once.append(1)
            fake.down = True                                   # frozen at this first failed call (scope none)
            threading.Timer(0.3, rt.request_stop).start()      # then SIGTERM arrives
    fake.hooks["progress"] = drop
    out = run_pass(rt)
    assert (out["state"], out["child"]) == ("stopped", "stopped")
    task = f"{rid}-g1"
    assert json.loads((tmp_path / "artifacts" / f"{task}.stage.json").read_text()) == {"stage": "stopped",
                                                                                       "reason": "graceful"}
    ns = [e for e in rt.journal(task).entries() if e["type"] == "note"]
    assert [n["fields"].get("reason") for n in ns if n["kind"] in ("freeze", "stop")][:2] == [
        "authority_unreachable", "graceful"]
    assert not [c for c in rt.journal(task).calls() if c["kind"] == "result"]
    fake.down = False
    rt2 = make_rt(tmp_path, mirror[0], fake)
    out = run_pass(rt2)
    assert (out["state"], out["generation"]) == ("completed", 2), out
    assert fake.reconciles[0]["stage"]["child"] == "stopped"
    assert [c for c in fake.calls("result")] == [("result", rid, 2, "completed")]   # never failed at generation 1


def test_s1_t8_a_lost_dispatch_answer_read_back_absent_fails_the_step_and_never_reads_as_done(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    grant = str(uuid.uuid4())
    rid = fake.add(program("checkpoint s1", f"effect e1 grant={grant}", "result ok"))
    once = []

    def lose(request_id, generation, what):
        if what == "dispatch:e1" and not once:
            once.append(1)
            for r in fake.receipts.values():
                r["until"] = 0.0                               # the outage outlasts the receipt lease
            fake.down = True                                   # committed at the authority; its answer is lost
            threading.Timer(0.3, lambda: setattr(fake, "down", False)).start()
            raise Down("dispatch")
    fake.hooks["effect_after"] = lose
    rt.start_contact()
    out = run_pass(rt)
    assert out["state"] == "failed" and fake.items[rid]["result"]["status"] == "failed", out
    assert fakework.FileSink(tmp_path / "sink").lines() == []                 # e1 never happened
    assert {r["state"] for r in fake.receipts.values()} == {"failed"}           # read back absent: definite
    log = (tmp_path / "artifacts" / f"{rid}-g1.log").read_text()
    assert "effect e1 refused: effect_failed" in log and "already" not in log
    assert ("effect", rid, 1, "reconcile:e1") in fake.log                       # readback before the question


def test_s2_a_finish_that_outlived_its_receipt_lease_is_recorded_by_readback_and_never_decided(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    grant = str(uuid.uuid4())
    rid = fake.add(program("checkpoint s1", f"effect e1 grant={grant}", "checkpoint s2", "result ok"))
    once = []

    def outage(request_id, generation, what):
        if what == "finish:e1" and not once:
            once.append(1)
            for r in fake.receipts.values():
                r["until"] = 0.0                               # the finish can't arrive for longer than its lease
            fake.down = True
            threading.Timer(0.3, lambda: setattr(fake, "down", False)).start()
    fake.hooks["effect"] = outage
    rt.start_contact()
    out = run_pass(rt)
    assert (out["state"], out["generation"]) == ("completed", 1), out
    assert {r["state"] for r in fake.receipts.values()} == {"verified"}
    assert [x["answer"] for x in fakework.FileSink(tmp_path / "sink").lines()] == ["committed"]
    finish = [c for c in rt.journal(f"{rid}-g1").calls() if c["kind"] == "effect_finish"]
    assert len(finish) == 1 and finish[0]["mark"] == "applied" and finish[0]["answer"]["state"] == "ok"
    assert ("finish:e1", 1, "refused", "receipt_lease_stale") in fake.answers_log   # its own lease, then readback
    assert ("reconcile:e1", 1, "ok", None) in fake.answers_log
    assert fake.reconciles and all(r["open"] == [] for r in fake.reconciles)
    assert fake.items[rid]["state"] != "unknown"


@pytest.mark.parametrize("sleep", ["outlasting_the_item_lease", "within_the_item_lease"])
def test_s3_t6a_the_woken_child_sends_its_stale_effect_first_and_the_authority_refuses_it(tmp_path, mirror, sleep):
    """T6a/T6m: the child, noticing its own wake gap, sends e2b with the receipt lease it held before sleeping, before
    the runtime freezes it. The authority refuses it; then the runtime's own wake runs §5.5. A sleep past the item's
    lease resumes at generation 2, which claims its own lease afresh (never generation 1's); a shorter one continues.
    Either way e2 is committed once and e2b never reaches the sink."""
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake, hosts={**HOSTS, "renew_s": 50})       # no renewal inside the window
    grant = str(uuid.uuid4())
    rid = fake.add(program("checkpoint s1", f"stale_effect_on_wake e2b grant={grant} wait_s=3 gap_s=1",
                           "checkpoint s2 hold_s=2", f"effect e2 grant={grant}", "result ok"))
    chan = tmp_path / "artifacts" / f"{rid}-g1.chan"
    box = {}

    def sleeper():
        try:
            wait_for(chan / "ack-000002.json")                 # e2b's receipt lease is held
            time.sleep(0.3)
            child = rt.child
            os.killpg(child.pgid, signal.SIGSTOP)              # the Mac sleeps (here, the child alone pauses)
            for r in fake.receipts.values():
                r["until"] = 0.0                               # its receipt lease ran out meanwhile
            if sleep == "outlasting_the_item_lease":
                fake.items[rid]["lease_until"] = 0.0
            time.sleep(1.5)
            os.killpg(child.pgid, signal.SIGCONT)              # it wakes first and sends e2b on its old lease
            wait_for(chan / "ack-000003.json")
            if sleep == "within_the_item_lease":
                wait_for(chan / "ack-000004.json")             # s2 recorded; it holds there
            rt.on_wake({"gap_s": 600.0, "wall_minus_mono_s": 600.0, "hint": None})   # the runtime notices
            box["fired"] = True
        except BaseException as exc:                           # never silent: the test fails on it
            box["error"] = exc
    import signal
    t = threading.Thread(target=sleeper, daemon=True)
    t.start()
    out = run_pass(rt)
    t.join(5)
    assert "error" not in box and box.get("fired"), box
    code = "lease_lapsed" if sleep == "outlasting_the_item_lease" else "receipt_lease_stale"
    assert ("dispatch:e2b", 1, "refused", code) in fake.answers_log
    closed = [e["fields"] for e in rt.journal(f"{rid}-g1").entries() if e["type"] == "note"
              and e["kind"] == "effect_closed"]
    assert [c["code"] for c in closed] == [code]                  # the host's own record of the refusal
    if sleep == "within_the_item_lease":                          # it lives on to say so; on resume it is stopped
        log1 = (tmp_path / "artifacts" / f"{rid}-g1.log").read_text()
        assert f"stale e2b: refused {code}" in log1, log1
    lines = fakework.FileSink(tmp_path / "sink").lines()
    assert [x["answer"] for x in lines] == ["committed"] and lines[0]["digest"] == fakework.effect_digest("e2")
    order = [e["kind"] for e in rt.journal(f"{rid}-g1").entries() if e["type"] == "note"]
    assert order.index("freeze") < order.index("reconcile")
    if sleep == "outlasting_the_item_lease":
        assert (out["state"], out["generation"]) == ("completed", 2), out
        assert ("claim:e2b", 2, "ok", None) in fake.answers_log           # its own lease, claimed afresh (S3a)
        assert not [a for a in fake.answers_log if a[0] == "dispatch:e2b" and a[1] == 2]
        assert "stale e2b: no wake observed" in (tmp_path / "artifacts" / f"{rid}-g2.log").read_text()
    else:
        assert (out["state"], out["generation"]) == ("completed", 1), out
        assert any(r["generation"] == 1 for r in fake.reconciles)
        rec = [e for e in rt.journal(f"{rid}-g1").entries() if e["type"] == "note" and e["kind"] == "reconcile"]
        assert rec[-1]["fields"]["answer"]["answer"] == "continue"


def test_s5_x3_a_restart_while_the_authority_is_unreachable_freezes_the_left_child_first(tmp_path, mirror,
                                                                                         monkeypatch):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1", "checkpoint s2", "result ok"))
    left = {}

    def die(request_id, generation, name):
        if name == "s1":
            left["child"] = rt.child
            raise Crash()                                      # the runtime process dies; nothing is cleaned up
    fake.hooks["progress"] = die
    with monkeypatch.context() as m:
        m.setattr(runner._Child, "kill", lambda self, reason: "stopped")
        with pytest.raises(Crash):
            run_pass_raw(rt)
    orphan = left["child"]
    try:
        assert orphan.alive()                                   # it waits for an answer nobody will send
        fake.hooks.pop("progress")
        fake.down = True                                        # the network is still down at the restart
        rt2 = make_rt(tmp_path, mirror[0], fake)
        before = len(fake.log)
        out = run_pass(rt2)
        assert out == {"state": "offline"}
        assert orphan.exited()                                  # frozen, verified, stopped: before any answer
        task = f"{rid}-g1"
        ns = [e for e in rt2.journal(task).entries() if e["type"] == "note" and e["kind"] == "freeze"]
        assert (ns[-1]["fields"]["reason"], ns[-1]["fields"]["child"]) == ("restart", "stopped")
        assert len(fake.log) == before and not fake.calls("claim")[1:]       # no claim, no effect while unreachable
        assert [c["kind"] for c in rt2.journal(task).pending()] == ["progress"]   # its record survives
        fake.down = False
        rt2.contact()
        out = run_pass(rt2)
        assert (out["state"], out["generation"]) == ("completed", 2), out
    finally:
        if orphan.proc.returncode is None:
            orphan.proc.wait()


def test_s6_workspace_only_offline_still_enforces_the_deadline(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake, offline_scope_default="workspace_only")
    rid = fake.add(program("checkpoint s1", "hang_s 60", "result ok"), deadline_s=4)
    once = []
    fake.hooks["progress"] = lambda r, g, n: n == "s1" and not once and (once.append(1) or setattr(fake, "down", True))
    t0 = time.monotonic()
    out = run_pass(rt)
    assert out["state"] == "timed_out" and not out["delivered"], out
    assert time.monotonic() - t0 < 15
    result = [c for c in rt.journal(f"{rid}-g1").pending() if c["kind"] == "result"]
    assert result and result[0]["args"]["status"] == "timed_out"


def test_n1_a_failing_ps_never_leaves_the_runtimes_own_child_unreaped(tmp_path, mirror, monkeypatch):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1", "result ok"))
    real, seen = runner._identity, []

    def flaky(pid):
        seen.append(pid)
        return real(pid) if len(seen) == 1 else None            # ps answers at spawn, then fails
    monkeypatch.setattr(runner, "_identity", flaky)
    fake.hooks["progress"] = lambda r, g, n: n == "s1" and rt.on_wake({"gap_s": 600.0, "wall_minus_mono_s": 600.0,
                                                                       "hint": None})
    out = run_pass(rt)
    assert out["state"] == "completed" and fake.items[rid]["result"]["status"] == "completed", out
    kinds = [e["kind"] for e in rt.journal(f"{rid}-g1").entries() if e["type"] == "note"]
    assert kinds.index("freeze") < kinds.index("reconcile") < kinds.index("thaw")
    assert len(seen) == 1                                        # the runtime's own child needed ps only once


def test_n2_a_deterministic_authority_error_is_final_and_never_offline(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1", "checkpoint s2", "result ok"))
    once = []

    def audit(request_id, generation, name):
        if name == "s1" and not once:
            once.append(1)
            raise RuntimeError("audit_event_not_written")
    fake.hooks["progress"] = audit
    out = run_pass(rt)
    assert out["state"] == "completed" and not rt.offline(), out
    s1 = next(c for c in rt.journal(f"{rid}-g1").calls() if (c["args"].get("checkpoint") or {}).get("name") == "s1")
    assert (s1["mark"], s1["answer"]["code"]) == ("final", "authority_error:audit_event_not_written")
    reasons = [e["fields"]["reason"] for e in rt.journal(f"{rid}-g1").entries()
               if e["type"] == "note" and e["kind"] == "freeze"]
    assert "authority_unreachable" not in reasons and fake.reconciles


def test_n9_a_receipt_holding_another_outcome_is_a_conflict_never_already_applied(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("result ok"))
    fake.items[rid]["gen"] = 1
    grant = str(uuid.uuid4())
    op = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{rid}:e1"))
    fake.receipts[op] = {"state": "failed", "gen": 1, "token": None, "until": 0.0, "mode": None, "rid": rid}

    def rec(state, outcome):
        return {"kind": "effect_finish", "request_id": rid, "generation": 1, "event_id": str(uuid.uuid4()),
                "args": {"runner_id": rt.runner_id, "operation_id": op, "receipt_generation": 1,
                         "effect": {"op": "finish", "key": "e1", "grant_id": grant,
                                    "request": fakework.effect_request("e1"), "state": state,
                                    "evidence": {"kind": "sink", "outcome": outcome}}}}
    assert rt._send_finish(rec("failed", "lease_expired"))["state"] == "already_applied"
    first = rt._send_finish(rec("verified", "committed"))
    again = rt._send_finish(rec("verified", "committed"))
    assert first["code"] == again["code"] == "receipt_outcome_conflict" and first["decision_id"] == again["decision_id"]
    assert len(fake.decisions) == 1 and fake.items[rid]["state"] == "unknown"           # exactly one decision (N-D2)


def test_n3_n4_n8_one_owner_identity_one_door_list_one_lock_folder(tmp_path, mirror):
    from core.mc_delegation_dispatcher import door
    with pytest.raises(host_mod.HostRefused, match="host_id_invalid"):
        host_mod.parse(host_doc(tmp_path, mirror[0], host_id="mac:1"))
    with pytest.raises(host_mod.HostRefused, match="host_config_invalid"):
        host_mod.parse(host_doc(tmp_path, mirror[0], lock_dir=str(tmp_path / "elsewhere")))    # never per file
    assert host_mod._DOORS is door.DOORS
    h = make_host(tmp_path, mirror[0])
    assert h.lock_path == tmp_path / "home" / "minimoi-runtime" / "locks" / "mac-test-1.dev.lock"
    assert h.runner_id.rsplit(":", 1) == ["mac-test-1", "runner"] and ":" not in h.host_id


# ── second fix round (delta re-review) ─────────────────────────────────────────────────────────────────────────────
def test_ns1_an_authority_error_on_a_known_result_blocks_the_task_and_never_reruns_the_work(tmp_path, mirror):
    fake = FakeAuthority(mirror[1])
    rt = make_rt(tmp_path, mirror[0], fake)
    rid = fake.add(program("checkpoint s1", "result ok"))
    once = []

    def store_fault(request_id, generation, status):
        if not once:
            once.append(1)
            raise RuntimeError("audit_event_not_written")       # the store raises once on the result
    fake.hooks["result"] = store_fault
    out = run_pass(rt)
    assert out["state"] == "completed" and not out["delivered"], out
    task = f"{rid}-g1"
    assert rt.blocked == {task: "authority_error:audit_event_not_written"}
    result = [c for c in rt.journal(task).calls() if c["kind"] == "result"]
    assert len(result) == 1 and result[0]["mark"] is None                    # kept pending, never marked final
    assert run_pass(rt)["state"] == "blocked" and not fake.reconciles          # no reconcile, no claim, no rerun
    rt2 = make_rt(tmp_path, mirror[0], fake)                                   # a restart replays it
    assert run_pass(rt2) == {"state": "idle"}
    assert fake.items[rid]["result"]["status"] == "completed" and fake.items[rid]["gen"] == 1
    assert not fake.reconciles and len(list((tmp_path / "artifacts").glob(f"{rid}-g*.log"))) == 1


def test_n_d1_a_group_is_verified_right_before_every_signal_and_a_child_reaped_elsewhere_is_never_signalled(
        tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(runner.os, "killpg", lambda pgid, sig: sent.append((pgid, sig)))
    rt = type("RT", (), {"cfg": type("Cfg", (), {"kill_grace_s": 2.0})(), "at": lambda self: "now"})()

    class Notes:
        def note(self, *a, **k):
            pass

    def child_of(proc):
        c = runner._Child.__new__(runner._Child)
        c.rt, c.proc, c.pgid, c.identity, c.frozen, c.signalled, c.lost = rt, proc, proc.pid, "recorded", False, \
            False, False
        c.lock, c.capture, c.journal, c.request_id, c.generation, c.task = threading.RLock(), None, Notes(), "r", 1, "t"
        return c
    # Reaped by someone else: its pgid could be anyone's now. Never signalled, recorded as unverified.
    gone = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    os.waitpid(gone.pid, 0)
    c = child_of(gone)
    assert (c.end_group(), c.freeze("wake"), c.lost, sent) == ("unverified", "unverified", True, [])
    # Its own child: verified before SIGTERM and again before SIGKILL, then reaped.
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"], start_new_session=True)
    c = child_of(live)
    assert c.end_group() == "stopped" and live.returncode == 0
    assert [sig for _, sig in sent] == [runner.signal.SIGTERM, runner.signal.SIGKILL]
