"""Delegation journey on a real PostgreSQL: jobs relay → dispatcher → Mac runner → result → relay.

The runner, workspace and envelope are the real (cleared) code; only the agent CLI is a stand-in
script, so no model is called and nothing is spent.
"""
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "minimoi_actions"))
from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402

from core.mc_delegation_dispatcher.relay import DelegationRelay, RoutingRelay  # noqa: E402
from core.mc_delegation_dispatcher.store import DispatcherStore, request_id_for  # noqa: E402
from core.mc_delegation_runner import commands, envelope, runner  # noqa: E402
from core.mc_delegation_runner.cost import ClaudeBudget  # noqa: E402
from core.mc_delegation_runner.dispatcher_adapter import StoreDispatcher  # noqa: E402


class PassThrough:
    """Test stand-in for a budget adapter around the stand-in CLI (the real one is ClaudeBudget)."""

    def apply(self, argv, env, cap):
        return argv, env


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class DelegationTests(PostgresFixture, unittest.TestCase):
    migrations = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",)

    def setUp(self):
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE guild.mc_delegations, guild.mc_delegation_runners")
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        repo = root / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "t@x.test")
        git(repo, "config", "user.name", "t")
        (repo / "README.md").write_text("hello\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "init")
        self.ref = git(repo, "rev-parse", "HEAD")
        self.store = DispatcherStore(self.connect, principal="robert", environment="dev")
        self.relay = DelegationRelay(self.store, ref_for=lambda repo_name: self.ref, repository="personal-ai-agents")
        self.cfg = runner.RunnerConfig(runner_id="mac-1", actors=("claude-code",), scopes=("research",),
                                       repositories={"personal-ai-agents": str(repo)}, workspace_root=root / "ws",
                                       artifact_root=root / "art", max_cost_usd=5.0, lease_renew_s=0.0, tick_s=0.01,
                                       kill_grace_s=1.0, cost_enforcers={"claude-code": PassThrough()})
        self._argv = commands.argv

    def tearDown(self):
        commands.argv = self._argv
        self.tmp.cleanup()

    def fake_cli(self, script):
        commands.argv = lambda actor, scope, brief, task_dir: [sys.executable, "-c", script]

    def start(self, job_id="j-0123456789abcdef", agent="claude-code", task="Ask Claude Code to research: what is in README?"):
        return self.relay.start({"job_id": job_id, "agent": agent, "task": task, "deadline_s": 600})

    def test_job_is_researched_and_the_answer_comes_back(self):
        self.fake_cli("print('The README says hello.')")
        self.assertEqual(self.start(), ("accepted", {}))
        self.assertEqual(self.start(), ("duplicate", {}))                  # one note makes one job
        self.assertEqual(self.relay.status("j-0123456789abcdef")[1]["state"], "running")
        out = runner.run_once(StoreDispatcher(self.store, self.cfg.artifact_root), self.cfg)
        self.assertEqual(out["state"], "completed")
        self.assertTrue(out["delivered"])
        outcome, st = self.relay.status("j-0123456789abcdef")
        self.assertEqual((outcome, st["state"]), ("ok", "completed"))
        self.assertIn("The README says hello.", st["result_text"])

    def test_a_dead_runner_ends_the_job_interrupted_instead_of_running_forever(self):
        self.start()
        caps = {"cost_enforced_actors": ["claude-code"], "scopes": ["research"], "repositories": ["personal-ai-agents"]}
        env = self.store.claim("mac-1", caps)
        self.store.progress(env["request_id"], env["generation"], "executing")
        self.assertEqual(self.relay.status("j-0123456789abcdef")[1]["state"], "running")     # within its lease
        with self.connect() as conn, conn.cursor() as cur:                                     # the runner died
            cur.execute("UPDATE guild.mc_delegations SET lease_until = clock_timestamp() - interval '300 seconds'")
        outcome, st = self.relay.status("j-0123456789abcdef")
        self.assertEqual((st["state"], st["error_class"]), ("failed", "interrupted"))
        self.assertIn("did not complete", st["result_text"])

    def test_a_refusal_never_overwrites_a_job_stopped_before_pickup(self):
        self.start()
        self.assertEqual(self.relay.stop("j-0123456789abcdef"), "confirmed")
        row = self.store.by_job("j-0123456789abcdef")
        ack = self.store.result(str(row["request_id"]), 7, event_id=str(uuid4()), status="refused", artifacts=[],
                                detail={"code": "late"})
        self.assertEqual(ack["code"], "stale_generation")
        self.assertEqual(self.store.by_job("j-0123456789abcdef")["state"], "cancelled")

    def test_a_finished_job_leaves_no_workspace_behind(self):
        self.fake_cli("print('done')")
        self.start()
        out = runner.run_once(StoreDispatcher(self.store, self.cfg.artifact_root), self.cfg)
        self.assertEqual((out["state"], out.get("workspace_removed")), ("completed", True))
        self.assertEqual(list(Path(self.cfg.workspace_root).iterdir()), [])

    def test_the_claimed_envelope_passes_the_runners_own_validation(self):
        self.start()
        doc = self.store.claim("mac-1", self.cfg.caps())
        from datetime import datetime, timezone
        env = envelope.parse(doc, allowed_repositories=self.cfg.repositories, max_cost_usd=5.0,
                             now=datetime.now(timezone.utc))
        self.assertEqual((env.actor, env.scope, env.ref, env.generation), ("claude-code", "research", self.ref, 1))

    def test_nothing_is_claimed_for_an_actor_without_a_budget_adapter(self):
        self.start(agent="codex")
        caps = dict(self.cfg.caps(), cost_enforced_actors=["claude-code"])
        self.assertIsNone(self.store.claim("mac-1", caps))

    def test_stop_before_pickup_is_confirmed_and_nothing_runs(self):
        self.fake_cli("open('ran','w').write('1')")
        self.start()
        self.assertEqual(self.relay.stop("j-0123456789abcdef"), "confirmed")
        self.assertEqual(runner.run_once(StoreDispatcher(self.store, self.cfg.artifact_root), self.cfg),
                         {"state": "idle"})
        self.assertEqual(self.relay.status("j-0123456789abcdef")[1]["state"], "stopped")

    def test_stop_while_running_cancels_and_is_reported_as_stopped(self):
        self.fake_cli("import time; time.sleep(30)")
        self.start()
        store = self.store

        class StopDuringRun(StoreDispatcher):
            def progress(self, *a, **k):
                super().progress(*a, **k)
                store.stop("j-0123456789abcdef")
        out = runner.run_once(StopDuringRun(self.store, self.cfg.artifact_root), self.cfg)
        self.assertEqual(out["state"], "cancelled")
        self.assertEqual(self.relay.status("j-0123456789abcdef")[1]["state"], "stopped")

    def test_a_stale_generation_can_never_complete_the_job(self):
        self.start()
        first = self.store.claim("mac-1", self.cfg.caps())
        rid = request_id_for("j-0123456789abcdef")
        with self.connect() as conn, conn.cursor() as cur:            # the lease lapses; another claim takes over
            cur.execute("UPDATE guild.mc_delegations SET state='queued', lease_until=NULL WHERE request_id=%s", (rid,))
        second = self.store.claim("mac-2", self.cfg.caps())
        self.assertEqual(second["generation"], first["generation"] + 1)
        stale = self.store.result(rid, first["generation"], event_id=str(uuid4()), status="completed", artifacts=[],
                                  detail={}, output_text="old")
        self.assertEqual(stale["state"], "refused")
        self.assertFalse(self.store.renew(rid, first["generation"]))
        self.assertTrue(self.store.cancel_requested(rid, first["generation"]))    # a lost claim means stop

    def test_results_are_idempotent_by_event(self):
        self.start()
        doc = self.store.claim("mac-1", self.cfg.caps())
        rid, gen, ev = doc["request_id"], doc["generation"], str(uuid4())
        a = self.store.result(rid, gen, event_id=ev, status="completed", artifacts=[], detail={}, output_text="x")
        b = self.store.result(rid, gen, event_id=ev, status="completed", artifacts=[], detail={}, output_text="x")
        c = self.store.result(rid, gen, event_id=str(uuid4()), status="failed", artifacts=[], detail={})
        self.assertEqual([a["state"], b["state"], c["state"]], ["accepted", "already_applied", "refused"])

    def test_routing_keeps_ordinary_mc_jobs_on_mcs_runtime(self):
        class McRelay:
            connected, started = True, []

            def start(self, spec):
                self.started.append(spec["job_id"])
                return "accepted", {}

            def status(self, job_id):
                return "ok", {"state": "running"}

            def stop(self, job_id):
                return "requested"
        mc = McRelay()
        routing = RoutingRelay(mc, self.relay)
        routing.start({"job_id": "j-aaaaaaaaaaaaaaaa", "agent": "mc-agent", "task": "x", "deadline_s": 60})
        routing.start({"job_id": "j-bbbbbbbbbbbbbbbb", "agent": "claude-code", "task": "y", "deadline_s": 60})
        self.assertEqual(mc.started, ["j-aaaaaaaaaaaaaaaa"])
        self.assertIsNotNone(self.store.by_job("j-bbbbbbbbbbbbbbbb"))
        self.assertEqual(routing.status("j-aaaaaaaaaaaaaaaa")[1]["state"], "running")


class OutputAndConfigTests(unittest.TestCase):
    def test_the_owner_reads_claude_codes_answer_not_its_json_envelope(self):
        from core.mc_delegation_runner.dispatcher_adapter import readable
        env = b'{"type":"result","subtype":"success","is_error":false,"result":"References.py reads them.","total_cost_usd":0.12}'
        self.assertEqual(readable(env), "References.py reads them.")
        self.assertTrue(readable(env.replace(b"false", b"true")).startswith("Claude Code reported an error"))
        self.assertEqual(readable(b"plain answer\n"), "plain answer")
        self.assertEqual(readable(b'{"other": 1}'), '{"other": 1}')
        self.assertEqual(readable(env.replace(b'"References.py reads them."', b'""')),
                         "Claude Code finished but returned no answer text.")

    def test_research_is_read_only_and_never_waits_for_a_plan_approval(self):
        argv = commands.argv("claude-code", "research", "brief", Path("/tmp/x"))
        self.assertNotIn("plan", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Grep,Glob")

    def test_the_dsn_can_come_from_the_environment_once_and_is_then_removed(self):
        import os
        from core.mc_delegation_runner import cli
        os.environ["MC_TEST_DSN"] = "postgresql://x"
        self.assertEqual(cli._dsn_from_env("MC_TEST_DSN"), "postgresql://x")
        self.assertNotIn("MC_TEST_DSN", os.environ)
        with self.assertRaises(SystemExit):
            cli._dsn_from_env("MC_TEST_DSN")


class BudgetTests(unittest.TestCase):
    def test_claude_budget_is_bound_on_the_real_cli_only(self):
        argv = commands.argv("claude-code", "research", "brief", Path("/tmp/x"))
        bound, _ = ClaudeBudget().apply(argv, {}, 2.0)
        self.assertEqual(bound[-2:], ["--max-budget-usd", "2.00"])
        with self.assertRaises(ValueError):
            ClaudeBudget().apply(commands.argv("codex", "research", "b", Path("/tmp/x")), {}, 2.0)
        with self.assertRaises(ValueError):
            ClaudeBudget().apply(argv, {}, float("nan"))


if __name__ == "__main__":
    unittest.main()
