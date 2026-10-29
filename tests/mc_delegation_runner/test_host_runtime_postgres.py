"""The version 2 host runtime against the real authority (spec v0.5 R2.4-a: the runner reaches the store by DSN): a
fakework item end to end with its reservation settled and DUP-5; T4a (a graceful restart: paused, then resumed at
generation 2); T4b (the runtime SIGKILLed as a real process at a committed effect: the orphan is verified and stopped,
the effect read back, the item resumed after it, the effect committed once); T6c (detection off: the authority's
refused renew triggers reconcile); a stale effect after a wake refused by the expired receipt lease (T6a/T6m's host
half); and T0m's do-not-resume refusal recorded by the authority.

Opt-in: a disposable mc12_test_* database (tests/minimoi_actions/postgres_fixture_support.py). Time is moved by
setting lease columns relative to the database clock. Children are real fakework/1 processes, always reaped."""
import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))
sys.path.insert(0, str(ROOT / "tests" / "mc_delegation_runner"))

from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_delegation_dispatcher.store import (EFFECT_POLICY, Authority, DispatcherStore,  # noqa: E402
                                                 effect_operation_id, item_subject_digest, parse_at)
from core.mc_delegation_runner import cli, fakework, host as host_mod  # noqa: E402
from core.mc_delegation_runner.dispatcher_adapter import StoreDispatcherV2  # noqa: E402
from core.minimoi_actions.grants import GrantDefinition  # noqa: E402
from core.minimoi_actions.requests import ToolRequest  # noqa: E402

MIGRATIONS = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",
              "core/mc_delegation_dispatcher/sql/002_host_runtime_v2.sql",
              "core/minimoi_actions/sql/004_action_receipts.sql",
              "core/minimoi_actions/sql/005_action_grants.sql")
VALUES = {**json.loads((ROOT / "tests" / "mc_delegation_dispatcher" / "authority.test_values.json").read_text()),
          "hosts": {"contact_interval_s": 1, "contact_timeout_s": 3, "renew_s": 2, "offline_hold_s": 2}}
AUTH = Authority.parse(VALUES)
CAPS = ["repo:personal-ai-agents", "scope:research", "scope:build", "actor:fakework", "host:mac-local"]
DIGEST = "sha256:" + "d" * 64


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class HostRuntimeOnPostgres(PostgresFixture, unittest.TestCase):
    migrations = MIGRATIONS

    def setUp(self):
        import tempfile
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""TRUNCATE guild.mc_delegations, guild.mc_delegation_runners, guild.mc_delegation_events,
                guild.mc_delegation_reservations, guild.mc_action_grant_consumptions, guild.mc_action_grants,
                guild.mc_action_events, guild.mc_action_receipts CASCADE""")
        self.tmp = Path(tempfile.mkdtemp(prefix="hostrt-"))
        repo = self.tmp / "mirror"
        repo.mkdir()
        for args in (["init", "-q"], ["config", "user.email", "t@x.test"], ["config", "user.name", "t"]):
            git(repo, *args)
        (repo / "README.md").write_text("hello\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "init")
        self.mirror, self.ref = repo, git(repo, "rev-parse", "HEAD")
        (self.tmp / "authority.json").write_text(json.dumps(VALUES))
        dsn = self.tmp / "dev.dsn"
        dsn.write_text(DSN)
        os.chmod(dsn, 0o600)
        (self.tmp / "bundle" / "core").mkdir(parents=True)
        (self.tmp / "bundle" / "core" / "__init__.py").write_text("")
        self.host_path = self.tmp / "host.yaml"
        self.write_host()
        self.host = host_mod.load(self.host_path)
        self.grants = cli._grant_store(self.host)
        self.runtimes, self.n = [], 0

    def tearDown(self):
        import shutil
        for rt in self.runtimes:
            rt.stop_contact()
            if rt.child is not None:
                rt.child.end_group()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_host(self, **over):
        doc = {"schema": "minimoi.host.v1", "host_id": "mac-test-1", "environment": "dev", "principal": "robert",
               "roles": ["authority", "worker", "witness"], "expected_availability": "intermittent",
               "unreachable_grace_s": 900, "service_manager": "launchd",
               "authority": {"endpoint_id": "dev-authority", "dsn_file": str(self.tmp / "dev.dsn"),
                             "values": str(self.tmp / "authority.json")},
               "capabilities": CAPS, "concurrency": 1, "offline_scope_default": "none",
               "artifact_root": str(self.tmp / "artifacts"), "workspace_root": str(self.tmp / "work"),
               "mirrors": {"personal-ai-agents": str(self.mirror)},
               "interpreter": {"path": sys.executable, "version": "%d.%d" % sys.version_info[:2]},
               "bundle_root": str(self.tmp / "bundle"), "gap_detection": "on",
               "stop_timeout_s": 6, "effect_sink": {"path": str(self.tmp / "sink"), "lookup": True}, **over}
        self.host_path.write_text(json.dumps(doc))

    # ── helpers ──
    def store(self):
        return DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH, grants=self.grants)

    def runtime(self, hosts=None, **over):
        if over:
            self.write_host(**over)
        h = host_mod.load(self.host_path)
        auth = AUTH if hosts is None else Authority.parse({**VALUES, "hosts": {**VALUES["hosts"], **hosts}})
        rt = cli.runtime_for(h, auth, StoreDispatcherV2(self.store()), digest=DIGEST)
        rt.cfg.tick_s, rt.cfg.kill_grace_s = 0.05, 1.0
        self.runtimes.append(rt)
        rt.contact()
        return rt

    def sql(self, query, args=()):
        from psycopg2.extras import RealDictCursor
        with self.connect() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, args)
            return cur.fetchall() if cur.description else None

    def db_now(self):
        return self.sql("SELECT clock_timestamp() AS now")[0]["now"]

    def item(self, brief, amount=0.5):
        """The owner's exact approval of one item, admitted (R4.3-b's owner path, as the test identity)."""
        self.n += 1
        job = f"job-{self.n}"
        subject = item_subject_digest(actor="fakework", scope="build", repository="personal-ai-agents", ref=self.ref,
                                      brief=brief, route="fakework", amount=amount)
        gid, now = str(uuid.uuid4()), self.db_now()
        with self.connect() as conn:
            self.grants.issue(conn, GrantDefinition(
                gid, "robert", "dev", "conversation", ("owner-turn",), ("mc",), "policy", "delegate.start_build", 1,
                (subject,), 1, now + timedelta(seconds=3600), proposal_id=str(uuid.uuid4()),
                approved_operation_id=str(uuid.uuid4()), approval_expires_at=now + timedelta(seconds=600)))
        self.assertEqual(self.store().enqueue(job_id=job, actor="fakework", scope="build",
                                              repository="personal-ai-agents", ref=self.ref, brief=brief,
                                              cost_cap_usd=1.0, deadline_s=1800, route="fakework", amount=amount,
                                              grant_id=gid), "accepted")
        return job

    def effect_grant(self, job, *keys):
        gid = str(uuid.uuid4())
        digests = tuple(ToolRequest(fakework.OPERATION, 1, json.dumps({"op": k}).encode()).arguments_sha256
                        for k in keys)
        with self.connect() as conn:
            self.grants.issue(conn, GrantDefinition(gid, "robert", "dev", job, ("owner-turn",), ("fakework",),
                                                    EFFECT_POLICY, fakework.OPERATION, 1, digests, len(keys),
                                                    self.db_now() + timedelta(seconds=3600), work_id=job))
        return gid

    def row(self, job):
        return self.store().by_job(job)

    def kinds(self, request_id):
        return [r["kind"] for r in self.sql("SELECT kind FROM guild.mc_delegation_events WHERE item_id=%s ORDER BY seq",
                                            (str(request_id),))]

    def run_pass(self, rt, timeout=60):
        box = {}
        t = threading.Thread(target=lambda: box.update(out=rt.pass_once()), daemon=True)
        t.start()
        t.join(timeout)
        self.assertFalse(t.is_alive(), "the pass did not finish")
        return box["out"]

    @staticmethod
    def program(*steps):
        return "\n".join(("fakework/1",) + steps)

    # ── the scenarios ──
    def test_a_fakework_item_completes_and_settles_and_replay_again_applies_nothing_dup5(self):
        rt = self.runtime()
        job = self.item(self.program("checkpoint s1", "report_usage 0.25", "result ok"))
        out = self.run_pass(rt)
        self.assertEqual((out["state"], out["answer"]["state"]), ("completed", "accepted"), out)
        row = self.row(job)
        self.assertEqual((row["state"], row["generation"], row["checkpoint"]["name"]), ("completed", 1, "s1"))
        self.assertEqual((row["reservation"]["state"], row["reservation"]["observed"]), ("settled", 0.25))
        day = self.sql("SELECT reserved, observed FROM guild.mc_delegation_reservations WHERE route='fakework'")[0]
        self.assertEqual((float(day["reserved"]), float(day["observed"])), (0.0, 0.25))
        facts = self.sql("SELECT * FROM guild.mc_delegation_runners WHERE runner_id='mac-test-1:runner'")[0]
        self.assertEqual((facts["bundle_digest"], facts["protocol"], facts["announced"]), (DIGEST, 2, "online"))
        task = f"{row['request_id']}-g1"
        again = rt.replay(task, again=True)
        self.assertEqual(again, {"sent": 3, "applied": 0, "already_applied": 3, "final": 0, "pending": 0})
        self.assertEqual(self.kinds(row["request_id"]).count("item.progress"), 2)

    def test_t4a_a_graceful_restart_pauses_then_resumes_at_generation_2(self):
        rt = self.runtime()
        job = self.item(self.program("checkpoint s1 hold_s=20", "checkpoint s2", "result ok"))
        rid = self.row(job)["request_id"]

        def stop_when_s1_is_on_record():
            for _ in range(400):
                row = self.row(job)
                if row["checkpoint"] and row["checkpoint"]["name"] == "s1":
                    rt.request_stop()
                    return
                time.sleep(0.05)
        threading.Thread(target=stop_when_s1_is_on_record, daemon=True).start()
        out = self.run_pass(rt)
        self.assertEqual(out["state"], "stopped", out)
        row = self.row(job)
        self.assertEqual((row["state"], row["generation"], row["lapse_cause"]), ("paused", 1, "owner_stopped"))
        rt2 = self.runtime()                                     # the restart: a new process, nothing in memory
        out = self.run_pass(rt2)
        self.assertEqual((out["state"], out["generation"]), ("completed", 2), out)
        row = self.row(job)
        self.assertEqual((row["state"], row["generation"]), ("completed", 2))
        kinds = self.kinds(rid)
        self.assertLess(kinds.index("item.paused"), kinds.index("item.resumed"))
        for task in (f"{rid}-g1", f"{rid}-g2"):                   # DUP-5 across both generations' records
            self.assertEqual(rt2.replay(task, again=True)["applied"], 0)

    def test_t4b_the_runtime_killed_at_a_committed_effect_reads_it_back_and_resumes_after_it(self):
        """A real runtime process (the CLI, by DSN) is SIGKILLed the moment its effect e1 is dispatched; its child
        commits e1 at the sink as an orphan. The restart verifies and stops the orphan, reads e1 back, and resumes
        after it: e1 committed once (DUP-1)."""
        job = f"job-{self.n + 1}"                                # the effect grant names the item's work
        gid = self.effect_grant(job, "e1")
        brief = self.program("checkpoint s1", f"effect e1 grant={gid}", "checkpoint s2", "result ok")
        self.assertEqual(self.item(brief), job)
        rid = self.row(job)["request_id"]
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        env["HOME"] = str(self.tmp / "home")              # G6's fixed lock folder, here and never the Mac's own
        proc = subprocess.Popen([sys.executable, "-m", "core.mc_delegation_runner.cli", "run", "--host-config",
                                 str(self.host_path), "--once"], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        ack = self.tmp / "artifacts" / f"{rid}-g1.chan" / "ack-000002.json"     # e1's dispatch answered
        try:
            for _ in range(600):
                if ack.exists() or proc.poll() is not None:
                    break
                time.sleep(0.02)
            self.assertTrue(ack.exists(), proc.stdout.read().decode() if proc.poll() is not None else "no dispatch")
            os.kill(proc.pid, signal.SIGKILL)
        finally:
            proc.wait()
        sink = fakework.FileSink(self.tmp / "sink")
        for _ in range(200):
            if sink.lines():
                break
            time.sleep(0.05)
        self.assertEqual([x["answer"] for x in sink.lines()], ["committed"])
        stage = json.loads((self.tmp / "artifacts" / f"{rid}-g1.stage.json").read_text())
        self.assertEqual(stage["stage"], "started")
        self.assertTrue(self._alive(stage["pgid"]))               # the orphan waits for an answer nobody will send
        self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                 "WHERE job_id=%s", (job,))                         # held → lapsed while it was down
        self.sql("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 second'")
        rt = self.runtime()
        out = self.run_pass(rt)
        self.assertEqual((out["state"], out["generation"]), ("completed", 2), out)
        self.assertFalse(self._alive(stage["pgid"]))                # verified, then stopped
        self.assertEqual([x["answer"] for x in sink.lines()], ["committed"])            # DUP-1
        receipt = self.sql("SELECT state FROM guild.mc_action_receipts")[0]
        self.assertEqual(receipt["state"], "verified")
        log = (self.tmp / "artifacts" / f"{rid}-g2.log").read_text()
        self.assertIn("effect e1: already verified, not sent again", log)
        notes = [e for e in rt.journal(f"{rid}-g1").entries() if e["type"] == "note"]
        kinds = [n["kind"] for n in notes]
        self.assertLess(kinds.index("freeze"), kinds.index("reconcile"))
        self.assertEqual(notes[kinds.index("reconcile")]["fields"]["answer"]["answer"], "resume")

    @staticmethod
    def _alive(pgid):
        try:
            os.killpg(pgid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def test_t6c_with_detection_off_the_authoritys_refused_renew_resumes_it(self):
        rt = self.runtime(gap_detection="off")
        job = self.item(self.program("checkpoint s1 hold_s=6", "result ok"))

        def lapse_when_s1_is_on_record():
            for _ in range(400):
                row = self.row(job)
                if row["checkpoint"]:
                    self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                             "WHERE job_id=%s", (job,))                 # slept through its lease, unnoticed
                    return
                time.sleep(0.05)
        threading.Thread(target=lapse_when_s1_is_on_record, daemon=True).start()
        out = self.run_pass(rt)
        self.assertEqual((out["state"], out["generation"]), ("completed", 2), out)
        rid = self.row(job)["request_id"]
        notes = [e for e in rt.journal(f"{rid}-g1").entries() if e["type"] == "note"]
        self.assertEqual([n["fields"].get("reason") for n in notes if n["kind"] == "freeze"][0], "renew_refused")
        self.assertIn("item.lapsed", self.kinds(rid))

    def test_a_stale_effect_after_a_wake_is_refused_and_the_next_effect_is_committed_once(self):
        """A wake within the item's lease (reconcile answers continue): the dispatch on the receipt lease the worker
        held before the wake is refused, and e2 is committed once. The injection is asserted, and a failure names its
        branch before the audit is read (review S4)."""
        job = f"job-{self.n + 1}"
        gid = self.effect_grant(job, "e2b", "e2")
        brief = self.program("checkpoint s1", f"stale_effect_on_wake e2b grant={gid} wait_s=20 gap_s=1",
                             f"effect e2 grant={gid}", "result ok")
        self.assertEqual(self.item(brief), job)
        rt = self.runtime()
        rid = str(self.row(job)["request_id"])
        e2b = effect_operation_id(rid, "e2b")
        real = rt.dispatcher

        class SlowAnswer:                     # the authority answers the wake's question slowly: the child stays frozen
            def __getattr__(self, name):
                return getattr(real, name)

            def reconcile(self, *a, **k):
                time.sleep(1.5)
                return real.reconcile(*a, **k)
        rt.dispatcher = SlowAnswer()
        box, t0 = {}, time.monotonic()

        def sleep_once_the_lease_is_held():
            try:
                for _ in range(3000):                                  # up to about a minute, then fail loudly
                    if self.sql("SELECT 1 FROM guild.mc_action_receipts WHERE operation_id=%s AND lease_until IS NOT "
                                "NULL", (e2b,)):
                        box["lease_seen_s"] = round(time.monotonic() - t0, 3)
                        rt.on_wake({"gap_s": 600.0, "wall_minus_mono_s": 600.0, "hint": None})
                        self.sql("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 s' "
                                 "WHERE operation_id=%s", (e2b,))
                        box["fired"] = True
                        return
                    time.sleep(0.02)
            except BaseException as exc:
                box["error"] = repr(exc)
        watcher = threading.Thread(target=sleep_once_the_lease_is_held, daemon=True)
        watcher.start()
        out = self.run_pass(rt)
        watcher.join(5)
        print(f"S4-evidence: watcher {box}, pass {round(time.monotonic() - t0, 3)} s, out {out.get('state')} "
              f"generation {out.get('generation')}")
        self.assertNotIn("error", box, box)
        self.assertTrue(box.get("fired"), f"the wake was never injected (branch A): {box}")
        self.assertEqual((out["state"], out["generation"]), ("completed", 1), out)       # branch B would read 2
        log = (self.tmp / "artifacts" / f"{rid}-g1.log").read_text()
        self.assertIn("stale e2b: refused receipt_lease_stale", log, log)
        sink = fakework.FileSink(self.tmp / "sink")
        self.assertEqual([x["answer"] for x in sink.lines()], ["committed"])            # e2 once, never e2b
        refused = self.sql("""SELECT body FROM guild.mc_delegation_events WHERE kind='effect.refused'
                              AND item_id=%s""", (rid,))
        self.assertTrue(any(r["body"]["op"] == "dispatch" for r in refused), refused)

    def test_t6m_a_sleep_past_the_item_lease_refuses_the_stale_effect_then_resumes_and_e2_once(self):
        """T6a/T6m's long sleep on the real store: the woken worker sends e2b on its pre-sleep lease before the runtime
        freezes it, and the authority refuses it (the item's lease lapsed first: lease_lapsed). Then §5.5 resumes it
        at generation 2, which claims its own lease afresh (S3), and e2 is committed once."""
        import signal
        job = f"job-{self.n + 1}"
        gid = self.effect_grant(job, "e2b", "e2")
        brief = self.program("checkpoint s1", f"stale_effect_on_wake e2b grant={gid} wait_s=3 gap_s=1",
                             f"effect e2 grant={gid}", "result ok")
        self.assertEqual(self.item(brief), job)
        rt = self.runtime(hosts={"renew_s": 50})                        # no renewal inside the window
        rid = str(self.row(job)["request_id"])
        e2b = effect_operation_id(rid, "e2b")
        chan = self.tmp / "artifacts" / f"{rid}-g1.chan"
        box = {}

        def sleeper():
            try:
                for name in ("ack-000002.json",):
                    for _ in range(1000):
                        if (chan / name).exists():
                            break
                        time.sleep(0.02)
                    else:
                        raise AssertionError(f"never answered: {name}")
                time.sleep(0.3)
                child = rt.child
                os.killpg(child.pgid, signal.SIGSTOP)               # the Mac sleeps (here, the child alone pauses)
                self.sql("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '1 second' "
                         "WHERE job_id=%s", (job,))
                self.sql("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 s' "
                         "WHERE operation_id=%s", (e2b,))
                time.sleep(1.5)
                os.killpg(child.pgid, signal.SIGCONT)               # it wakes first and sends e2b on its old lease
                for _ in range(1000):
                    if (chan / "ack-000003.json").exists():
                        break
                    time.sleep(0.02)
                rt.on_wake({"gap_s": 600.0, "wall_minus_mono_s": 600.0, "hint": None})
                box["fired"] = True
            except BaseException as exc:
                box["error"] = repr(exc)
        watcher = threading.Thread(target=sleeper, daemon=True)
        watcher.start()
        out = self.run_pass(rt)
        watcher.join(5)
        self.assertNotIn("error", box, box)
        self.assertTrue(box.get("fired"), box)
        self.assertEqual((out["state"], out["generation"]), ("completed", 2), out)
        refused = self.sql("""SELECT generation, body FROM guild.mc_delegation_events WHERE kind='effect.refused'
                              AND item_id=%s""", (rid,))
        self.assertIn((1, "dispatch", "lease_lapsed"), [(r["generation"], r["body"]["op"], r["body"]["code"])
                                                        for r in refused])
        claimed = self.sql("""SELECT generation FROM guild.mc_delegation_events WHERE kind='effect.claimed'
                              AND item_id=%s AND body->>'operation_id'=%s ORDER BY seq""", (rid, e2b))
        self.assertEqual([r["generation"] for r in claimed], [1, 2])         # generation 2 claimed its own lease
        sink = fakework.FileSink(self.tmp / "sink")
        self.assertEqual([x["answer"] for x in sink.lines()], ["committed"])
        self.assertEqual(sink.lines()[0]["operation_id"], effect_operation_id(rid, "e2"))
        kinds = self.kinds(rid)
        self.assertLess(kinds.index("item.lapsed"), kinds.index("item.resumed"))

    def test_s2_a_finish_that_outlived_its_receipt_lease_is_recorded_by_readback_and_never_decided(self):
        """The finish (the sink's own "committed", on file) cannot reach the authority for longer than its receipt
        lease. The host keeps it pending and records it under a reconcile lease; the item is never decided."""
        job = f"job-{self.n + 1}"
        gid = self.effect_grant(job, "e1")
        brief = self.program("checkpoint s1", f"effect e1 grant={gid}", "checkpoint s2", "result ok")
        self.assertEqual(self.item(brief), job)
        rt = self.runtime()
        rid = str(self.row(job)["request_id"])
        e1 = effect_operation_id(rid, "e1")
        real, box = rt.dispatcher, {}
        test = self

        class Outage:                         # the authority is unreachable from the finish until past its lease
            def __getattr__(self, name):
                if box.get("down"):
                    def down(*a, **k):
                        raise ConnectionError("authority unreachable")
                    return down
                return getattr(real, name)

            def effect(self, *a, **k):
                if a[3]["op"] == "finish" and "done" not in box:
                    box.update(done=True, down=True)
                    test.sql("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 s' "
                             "WHERE operation_id=%s", (e1,))
                    threading.Timer(0.5, lambda: box.update(down=False)).start()
                    raise ConnectionError("authority unreachable")
                return self.__getattr__("effect")(*a, **k)
        rt.dispatcher = Outage()
        rt.start_contact()
        out = self.run_pass(rt)
        self.assertEqual((out["state"], out["generation"]), ("completed", 1), out)
        self.assertEqual(self.sql("SELECT state FROM guild.mc_action_receipts WHERE operation_id=%s", (e1,))[0]["state"],
                         "verified")
        kinds = self.kinds(rid)
        self.assertNotIn("decision.queued", kinds)
        self.assertNotIn("item.unknown", kinds)
        self.assertIn("effect.reconciling", kinds)                       # recorded by the readback route
        sink = fakework.FileSink(self.tmp / "sink")
        self.assertEqual([x["answer"] for x in sink.lines()], ["committed"])

    def test_n_d2_a_finish_contradicting_its_receipt_is_one_decision_never_a_refused_finish(self):
        """The receipt holds failed (read back absent); a late finish reports committed. The authority records it as
        effect.conflict and queues exactly one decision on the item, which goes unknown naming both outcomes; no
        effect.refused row describes the finish as a lease problem, and a replay finds the same decision."""
        job = f"job-{self.n + 1}"
        gid = self.effect_grant(job, "e1")
        self.assertEqual(self.item(self.program("result ok")), job)
        rt = self.runtime()
        store, runner_id = self.store(), rt.runner_id
        env = store.claim(runner_id, CAPS, protocol=2)["envelope"]
        rid, g, req = env["request_id"], env["generation"], fakework.effect_request("e1")

        def eff(op, **extra):
            return store.effect(runner_id, rid, g, {"op": op, "key": "e1", "grant_id": gid, "request": req, **extra})
        self.assertEqual(eff("prepare")["state"], "ok")
        claimed = eff("claim")
        lease = {k: claimed["lease"][k] for k in ("receipt_generation", "token", "mode")}
        self.assertEqual(eff("dispatch", lease=lease)["state"], "ok")
        self.assertEqual(eff("finish", lease=lease, state="failed",
                             evidence={"kind": "readback", "outcome": "absent"})["state"], "ok")
        rec = {"kind": "effect_finish", "request_id": rid, "generation": g, "event_id": str(uuid.uuid4()),
               "args": {"runner_id": runner_id, "operation_id": claimed["operation_id"],
                        "receipt_generation": lease["receipt_generation"],
                        "effect": {"op": "finish", "key": "e1", "grant_id": gid, "request": req, "state": "verified",
                                   "evidence": {"kind": "sink", "outcome": "committed"}}}}
        first, again = rt._send_finish(rec), rt._send_finish(rec)
        self.assertEqual((first["state"], first["code"]), ("refused", "receipt_outcome_conflict"), first)
        self.assertEqual(again["decision_id"], first["decision_id"])
        row = self.row(job)
        self.assertEqual((row["state"], row["result_detail"]["reason"]), ("unknown", "effect_outcome_conflict"))
        self.assertEqual({k: row["result_detail"]["reported"][k] for k in ("recorded", "reported")},
                         {"recorded": "failed", "reported": "verified"})
        events = self.sql("SELECT kind, body FROM guild.mc_delegation_events WHERE item_id=%s ORDER BY seq", (rid,))
        self.assertEqual([e["body"].get("reason") for e in events if e["kind"] == "decision.queued"],
                         ["effect_outcome_conflict"])                                   # exactly one decision
        self.assertEqual(len([e for e in events if e["kind"] == "effect.conflict"]), 1)
        self.assertFalse([e for e in events if e["kind"] == "effect.refused" and e["body"]["op"] == "finish"])
        self.assertEqual(self.sql("SELECT state FROM guild.mc_action_receipts WHERE operation_id=%s",
                                  (claimed["operation_id"],))[0]["state"], "failed")      # the receipt is unchanged

    # ── Codex part 1, F2 ──
    def test_f2_a_lost_claim_reply_is_found_and_reconciled_and_a_resend_returns_the_same_claim(self):
        """Codex's probe: the real claim commits, a connection error replaces its reply, and no local claim exists. A
        resend of the same request returns the same claim, never a second item. The lease then runs out, a fresh
        runtime starts, and the item is found (withheld, never idle) and reconciled."""
        rt = self.runtime()
        job = self.item(self.program("checkpoint s1", "result ok"))
        self.item(self.program("result ok"))                     # a second queued item a second claim could take
        real = rt.dispatcher.claim

        def lost_reply(*args, **kwargs):
            got = real(*args, **kwargs)
            assert got["state"] == "claimed"
            raise ConnectionError("simulated reply loss after authority commit")
        rt.dispatcher.claim = lost_reply
        self.assertEqual(rt.pass_once()["state"], "offline")
        self.assertEqual(rt.owned(), [])
        claim_id = rt.journal("claims").pending()[0]["event_id"]
        row = self.row(job)
        store = self.store()
        for _ in range(2):
            again = store.claim(rt.runner_id, CAPS, protocol=2, claim_id=claim_id, held=[])
            self.assertEqual((again["state"], again["replayed"], again["envelope"]["request_id"],
                              again["envelope"]["generation"], parse_at(again["lease_until"])),
                             ("claimed", True, str(row["request_id"]), 1, row["lease_until"]))
        self.assertEqual(self.sql("SELECT count(*) AS n FROM guild.mc_delegations WHERE state='claimed'")[0]["n"], 1)
        self.sql("UPDATE guild.mc_delegations SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s",
                 (job,))
        restarted = self.runtime()
        answers = [restarted.pass_once() for _ in range(3)]
        self.assertFalse(all(a["state"] == "idle" for a in answers), answers)
        self.assertEqual(answers[0]["state"], "claim_superseded")
        self.assertEqual((answers[1]["state"], answers[1]["generation"], answers[1]["request_id"]),
                         ("completed", 2, str(row["request_id"])), answers)        # reconciled, then resumed
        row = self.row(job)
        self.assertEqual((row["state"], row["generation"]), ("completed", 2))
        kinds = self.kinds(row["request_id"])
        self.assertIn("host.claim_withheld", [r["kind"] for r in self.sql(
            "SELECT kind FROM guild.mc_delegation_events WHERE kind='host.claim_withheld'")])
        self.assertLess(kinds.index("item.lapsed"), kinds.index("item.resumed"))
        self.assertEqual(kinds.count("item.claim_bound"), 1)
        notes = [e for e in restarted.journal(f"{row['request_id']}-g1").entries() if e["type"] == "note"]
        rec = next(n for n in notes if n["kind"] == "reconcile")
        self.assertEqual(rec["fields"]["stage"], {"child": "stopped", "checkpoint": None})   # nothing of it ran here

    def test_f2_a_crash_between_the_authoritys_commit_and_the_local_write_is_recovered(self):
        rt = self.runtime()
        job = self.item(self.program("checkpoint s1", "result ok"))

        class Crash(BaseException):
            pass

        def die(*a, **k):
            raise Crash()                                 # the process dies after the commit, before the local write
        rt._record_claim = die
        with self.assertRaises(Crash):
            rt.pass_once()
        self.assertEqual(rt.owned(), [])
        self.assertEqual(self.row(job)["state"], "claimed")
        restarted = self.runtime()                            # same request id again: the same claim, adopted
        out = self.run_pass(restarted)
        self.assertEqual((out["state"], out["generation"]), ("completed", 2), out)
        rid = self.row(job)["request_id"]
        adopted = json.loads((self.tmp / "artifacts" / f"{rid}-g1.claim.json").read_text())
        self.assertEqual(adopted["adopted"], "claim_reply_lost")
        self.assertEqual(self.kinds(rid).count("item.claim_bound"), 1)
        self.assertEqual(restarted.journal("claims").pending(), [])

    def test_t0m_a_do_not_resume_session_is_refused_before_any_spawn(self):
        rt = self.runtime()
        job = self.item(self.program("resume_session 01a17dde-fb26-7923-8105-c7478428d17d", "result ok"))
        out = self.run_pass(rt)
        self.assertEqual((out["state"], out["code"]), ("refused", "do_not_resume"))
        row = self.row(job)
        self.assertEqual((row["state"], row["result_detail"]["code"]), ("refused", "do_not_resume"))
        self.assertFalse((self.tmp / "artifacts" / f"{row['request_id']}-g1.log").exists())

    def test_cen1_the_census_reaches_the_authority_once_each_and_is_derived_there(self):
        """S1-F: a fakework root spawns three transient subagents and one long one; the host's census rides its
        heartbeats into the events table, each event once (CEN-2), and the authority's agents view shows the tree:
        the root done, the three done with their ends, the long one end_unobserved (CEN-1, CEN-4). No item changed
        beyond its own result (CEN-6)."""
        from core.mc_delegation_runner import runner
        rt = self.runtime()
        job = self.item(self.program("spawn a role=explore hold_s=0", "spawn b role=explore hold_s=0",
                                     "spawn c role=explore hold_s=0", "spawn long1 role=worker hold_s=600 beat_s=1 long",
                                     "checkpoint s1 hold_s=2", "result ok"))
        out = self.run_pass(rt)
        self.assertEqual(out["state"], "completed", out)
        for _ in range(10):
            rt.contact()
            if not rt.census.unsent():
                break
        self.assertEqual(rt.census.unsent(), [])
        rid = self.row(job)["request_id"]
        root = runner.census_agent_id(f"{rid}-g1")
        agents = {r["agent_id"]: r for r in self.store().agents()}
        kids = {a: r for a, r in agents.items() if r["parent_id"] == root}
        self.assertEqual((agents[root]["state"], agents[root]["reason"], agents[root]["host_id"]),
                         ("done", "exit_0", "mac-test-1"))
        self.assertEqual(sorted((a.rsplit("/", 1)[1], r["state"]) for a, r in kids.items()),
                         [("a", "done"), ("b", "done"), ("c", "done"), ("long1", "unknown")])
        self.assertEqual((kids[root + "/long1"]["reason"], kids[root + "/long1"]["ended_at"]), ("end_unobserved", None))
        self.assertTrue(all(r["item_id"] == rid and r["start_path"] == "runtime" for r in kids.values()))
        on_file = [json.loads(x)["event_id"] for p in rt.census.root.glob("*.jsonl") for x in p.read_text().splitlines()
                   if '"type":"event"' in x]
        stored = [str(r["event_id"]) for r in self.sql("SELECT event_id FROM guild.mc_delegation_events "
                                                        "WHERE kind LIKE 'census.%%'")]
        self.assertEqual(sorted(on_file), sorted(stored))                          # each event once
        self.assertEqual(self.sql("SELECT count(*) AS n FROM guild.mc_delegation_events WHERE kind LIKE 'census.%%' "
                                  "AND item_id IS NOT NULL")[0]["n"], 0)
        self.assertEqual(self.row(job)["state"], "completed")


if __name__ == "__main__":
    unittest.main()
