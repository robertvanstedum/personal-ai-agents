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
                                                 item_subject_digest)
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
               "bundle_root": str(self.tmp / "bundle"), "lock_dir": str(self.tmp / "locks"), "gap_detection": "on",
               "stop_timeout_s": 6, "effect_sink": {"path": str(self.tmp / "sink"), "lookup": True}, **over}
        self.host_path.write_text(json.dumps(doc))

    # ── helpers ──
    def store(self):
        return DispatcherStore(self.connect, principal="robert", environment="dev", authority=AUTH, grants=self.grants)

    def runtime(self, **over):
        if over:
            self.write_host(**over)
        h = host_mod.load(self.host_path)
        rt = cli.runtime_for(h, AUTH, StoreDispatcherV2(self.store()), digest=DIGEST)
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
        job = f"job-{self.n + 1}"
        gid = self.effect_grant(job, "e2b", "e2")
        brief = self.program("checkpoint s1", f"stale_effect_on_wake e2b grant={gid} wait_s=20 gap_s=1",
                             f"effect e2 grant={gid}", "result ok")
        self.assertEqual(self.item(brief), job)
        rt = self.runtime()
        rid = self.row(job)["request_id"]
        real = rt.dispatcher

        class SlowAnswer:                     # the authority answers the wake's question slowly: the child stays frozen
            def __getattr__(self, name):
                return getattr(real, name)

            def reconcile(self, *a, **k):
                time.sleep(1.5)
                return real.reconcile(*a, **k)
        rt.dispatcher = SlowAnswer()

        def sleep_once_the_lease_is_held():
            for _ in range(400):
                if self.sql("SELECT 1 FROM guild.mc_action_receipts WHERE lease_until IS NOT NULL"):
                    # The whole Mac slept past the receipt lease it held (at most 30 s; moved, not waited).
                    rt.on_wake({"gap_s": 600.0, "wall_minus_mono_s": 600.0, "hint": None})
                    self.sql("UPDATE guild.mc_action_receipts SET lease_until = clock_timestamp() - interval '1 s'")
                    return
                time.sleep(0.02)
        threading.Thread(target=sleep_once_the_lease_is_held, daemon=True).start()
        out = self.run_pass(rt)
        self.assertEqual(out["state"], "completed", out)
        sink = fakework.FileSink(self.tmp / "sink")
        self.assertEqual([x["answer"] for x in sink.lines()], ["committed"])            # e2 once, never e2b
        refused = self.sql("""SELECT body FROM guild.mc_delegation_events WHERE kind='effect.refused'
                              AND item_id=%s""", (rid,))
        self.assertTrue(any(r["body"]["op"] == "dispatch" for r in refused), refused)
        log = (self.tmp / "artifacts" / f"{rid}-g{out['generation']}.log").read_text()
        self.assertIn("stale e2b: refused", log)

    def test_t0m_a_do_not_resume_session_is_refused_before_any_spawn(self):
        rt = self.runtime()
        job = self.item(self.program("resume_session 01a17dde-fb26-7923-8105-c7478428d17d", "result ok"))
        out = self.run_pass(rt)
        self.assertEqual((out["state"], out["code"]), ("refused", "do_not_resume"))
        row = self.row(job)
        self.assertEqual((row["state"], row["result_detail"]["code"]), ("refused", "do_not_resume"))
        self.assertFalse((self.tmp / "artifacts" / f"{row['request_id']}-g1.log").exists())


if __name__ == "__main__":
    unittest.main()
