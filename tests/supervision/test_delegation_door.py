"""MC delegation from production to the Mac (#365): the runner door, Codex's plan limit, and one end-to-end research
job (relay -> prod store -> HTTPS door -> real runner -> stand-in codex -> result back) on a disposable PostgreSQL."""
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "minimoi_actions"))

from core.mc_delegation_dispatcher import door  # noqa: E402
from core.mc_delegation_runner import commands  # noqa: E402
from core.mc_delegation_runner.cost import CodexPlanLimit, ENFORCERS  # noqa: E402
from core.mc_delegation_runner.dispatcher_adapter import readable  # noqa: E402

RID = "6f1c2a9e-1b2c-4d3e-8f90-0a1b2c3d4e5f"


class FakeStore:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*a, **kw):
            self.calls.append((name, a, kw))
            return {"ok": name}
        return call


class Door(unittest.TestCase):
    def test_each_call_is_checked_and_passed_through(self):
        s = FakeStore()
        caps = {"cost_enforced_actors": ["claude-code", "codex"], "scopes": ["research"], "repositories": ["personal-ai-agents"]}
        door.handle(s, {"op": "heartbeat", "runner_id": "mac-prod-1", "state": "idle", "capabilities": caps})
        door.handle(s, {"op": "claim", "runner_id": "mac-prod-1", "capabilities": caps})
        door.handle(s, {"op": "progress", "request_id": RID, "generation": 1, "phase": "started"})
        door.handle(s, {"op": "renew", "request_id": RID, "generation": 1})
        door.handle(s, {"op": "result", "request_id": RID, "generation": 1, "event_id": "e-1", "status": "completed",
                        "artifacts": [], "detail": {}, "output_text": "answer"})
        self.assertEqual([c[0] for c in s.calls], ["heartbeat", "claim", "progress", "renew", "result"])
        self.assertEqual(s.calls[-1][2]["output_text"], "answer")

    def test_anything_else_is_refused(self):
        bad = [
            {"op": "enqueue", "job_id": "x"},                                         # the runner never creates work
            {"op": "claim", "runner_id": "mac", "capabilities": {"principal": "eve"}},  # no authority fields
            {"op": "claim", "runner_id": "mac; rm", "capabilities": {}},
            {"op": "renew", "request_id": "not-a-uuid", "generation": 1},
            {"op": "renew", "request_id": RID, "generation": True},
            {"op": "progress", "request_id": RID, "generation": 1, "phase": "x" * 41},
            {"op": "result", "request_id": RID, "generation": 1, "event_id": "e", "status": "completed",
             "artifacts": [], "detail": {}, "output_text": "x" * (64 * 1024 + 1)},
            {"op": "result", "request_id": RID, "generation": 1, "event_id": "e", "status": "completed",
             "artifacts": [], "detail": {}},                                            # every field, always
            [],
        ]
        for body in bad:
            with self.assertRaises(door.Refused, msg=str(body)[:80]):
                door.handle(FakeStore(), body)

    def test_http_dispatcher_only_talks_to_known_doors(self):
        with self.assertRaises(ValueError):
            door.HttpDispatcher("https://evil.example/internal/mc/v1/delegation-runner", "t" * 40, Path("/tmp"))
        with self.assertRaises(ValueError):
            door.HttpDispatcher(door.DOORS[0], "short", Path("/tmp"))


class CodexLimit(unittest.TestCase):
    def test_only_read_only_codex_exec(self):
        lim = CodexPlanLimit()
        argv = commands.argv("codex", "research", "brief", Path("/w"))
        self.assertEqual(lim.apply(argv, {"A": "1"}, 2.0), (argv, {"A": "1"}))
        self.assertIn("--ignore-user-config", argv)
        for bad in (commands.argv("codex", "build", "brief", Path("/w")),
                    argv + ["--dangerously-bypass-approvals-and-sandbox"],
                    argv + ["--sandbox", "workspace-write"],
                    ["claude", "-p", "x"]):
            with self.assertRaises(ValueError):
                lim.apply(bad, {}, 2.0)
        self.assertIn("codex", ENFORCERS)

    def test_codex_answer_is_its_last_message(self):
        events = "\n".join([
            "Reading additional input from stdin...",
            json.dumps({"type": "thread.started", "thread_id": "t"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "first"}}),
            "2026-10-26T22:21:19Z ERROR some stderr line",
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "PR #300: close."}}),
            json.dumps({"type": "turn.completed"})])
        self.assertEqual(readable(events.encode()), "PR #300: close.")
        failed = "\n".join([json.dumps({"type": "thread.started"}), json.dumps({"type": "turn.failed", "error": {}})])
        self.assertEqual(readable(failed.encode()), "Codex stopped with an error before finishing.")
        self.assertEqual(readable(b"plain text answer"), "plain text answer")

    def test_relay_keeps_codex_to_research_within_30_minutes(self):
        from core.mc_delegation_dispatcher.relay import DelegationRelay
        seen = []

        class S:
            def enqueue(self, **kw):
                seen.append(kw)
                return "accepted"
        r = DelegationRelay(S(), ref_for=lambda repo: "a" * 40)
        self.assertEqual(r.start({"agent": "codex", "job_id": "j1", "task": "t", "deadline_s": 7200})[0], "accepted")
        self.assertEqual(seen[-1]["deadline_s"], 1800)
        self.assertEqual(r.start({"agent": "codex", "job_id": "j2", "task": "t", "scope": "build"}),
                         ("refused", {"reason": "scope_invalid"}))
        r.start({"agent": "claude-code", "job_id": "j3", "task": "t", "scope": "build", "deadline_s": 3600})
        self.assertEqual(seen[-1]["deadline_s"], 3600)


from postgres_fixture_support import DSN, PostgresFixture  # noqa: E402


class _TestClientOpener:
    """urllib-style opener that hands HttpDispatcher's requests to a Flask test client."""

    def __init__(self, client):
        self.client = client

    def __call__(self, req, timeout=None):
        resp = self.client.post(req.full_url.replace("https://minimoi.ai", ""), data=req.data,
                                headers=dict(req.header_items()))
        if resp.status_code != 200:
            raise RuntimeError(f"door answered {resp.status_code}")
        body = io.BytesIO(resp.data)
        body.__enter__ = lambda *a: body
        return _Ctx(body)


class _Ctx:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self.body

    def __exit__(self, *a):
        return False


@unittest.skipUnless(DSN, "requires disposable MC_BINDING_TEST_DSN")
class EndToEnd(PostgresFixture, unittest.TestCase):
    migrations = ("core/mc_delegation_dispatcher/sql/001_delegations.sql",)

    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path, monkeypatch):
        self.tmp, self.mp = tmp_path, monkeypatch

    def test_codex_research_from_prod_through_the_door(self):
        from flask import Flask
        from core.mc_delegation_dispatcher.relay import DelegationRelay
        from core.mc_delegation_dispatcher.store import DispatcherStore
        from core.mc_delegation_runner import runner
        from minimoi_portal.guild_ui import supervision
        with self.guard.cursor() as cur:
            cur.execute("TRUNCATE guild.mc_delegations, guild.mc_delegation_runners")
        self.guard.commit()
        # A real mirror at an exact commit.
        repo = self.tmp / "mirror"
        repo.mkdir()
        for args in (["init", "-q"], ["config", "user.email", "t@x.test"], ["config", "user.name", "t"]):
            subprocess.run(["git", *args], cwd=repo, check=True)
        (repo / "README.md").write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
        ref = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
        # A stand-in codex on PATH that answers in codex's --json event format (and writes noise on stderr).
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "codex"
        fake.write_text("#!" + sys.executable + "\nimport json, sys\nassert sys.argv[1:4] == ['exec', '--sandbox', 'read-only']\n"
                        "sys.stderr.write('Reading additional input from stdin...\\n')\n"
                        "print(json.dumps({'type': 'thread.started'}))\n"
                        "print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', "
                        "'text': 'PR #300: close (superseded by #340).'}}))\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.mp.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
        # Production side: the relay queues the job in prod's store; the portal opens the door.
        store = DispatcherStore(self.connect, principal="robert", environment="prod")
        relay = DelegationRelay(store, ref_for=lambda r: ref)
        self.assertEqual(relay.start({"agent": "codex", "job_id": "job-triage-1", "task": "Triage open PRs."})[0], "accepted")
        token = "r" * 48
        self.mp.setenv("DATABASE_URL", "postgresql://unused")
        app = Flask("door")
        env = {"MINIMOI_MC_DELEGATION": "1", "MINIMOI_MC_DELEGATION_PRINCIPAL": "robert", "MINIMOI_MC_DELEGATION_ENV": "prod",
               "MINIMOI_MC_DELEGATION_RUNNER_TOKEN_SHA256": hashlib.sha256(token.encode()).hexdigest()}
        self.assertTrue(supervision.attach_runner_door(app, env, store_factory=lambda: store))
        # The Mac side: the real runner over HttpDispatcher, Codex's real plan limit.
        dispatcher = door.HttpDispatcher(door.DOORS[0], token, self.tmp / "artifacts",
                                         opener=_TestClientOpener(app.test_client()))
        cfg = runner.RunnerConfig(runner_id="mac-prod-1", actors=tuple(ENFORCERS), scopes=("research",),
                                  repositories={"personal-ai-agents": str(repo)}, workspace_root=self.tmp / "ws",
                                  artifact_root=self.tmp / "artifacts", max_cost_usd=2.0, cost_enforcers=dict(ENFORCERS),
                                  tick_s=0.05, lease_renew_s=0.5)
        out = runner.run_once(dispatcher, cfg)
        self.assertEqual(out.get("state"), "completed", out)
        row = store.by_job("job-triage-1")
        self.assertEqual((row["state"], row["actor"], row["output_text"]),
                         ("completed", "codex", "PR #300: close (superseded by #340)."))
        self.assertLessEqual((row["deadline"] - row["created_at"]).total_seconds(), 1800)
        bad = door.HttpDispatcher(door.DOORS[0], "x" * 48, self.tmp, opener=_TestClientOpener(app.test_client()))
        with self.assertRaises(RuntimeError):
            bad.claim("mac-prod-1", {"cost_enforced_actors": ["codex"], "scopes": ["research"],
                                     "repositories": ["personal-ai-agents"]})


if __name__ == "__main__":
    unittest.main()
