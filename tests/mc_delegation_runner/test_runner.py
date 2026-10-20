"""Delegation runner: envelope rules, isolated workspace, lifecycle, against a fake dispatcher and a
fake agent CLI (a short Python script), with a real temporary git mirror."""
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.mc_delegation_runner import commands, envelope, runner, workspace

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


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


def env_doc(base_ref, **over):
    ref = base_ref
    doc = {"schema": envelope.SCHEMA, "request_id": str(uuid.uuid4()), "work_id": "work-1", "generation": 1,
           "actor": "codex", "principal_id": "robert", "environment": "dev", "scope": "build",
           "repository": "personal-ai-agents", "ref": ref, "brief": "Add a line to README.",
           "input_artifacts": [], "deadline": (NOW + timedelta(minutes=30)).isoformat(), "cost_cap_usd": 2.0}
    doc.update(over)
    return doc


class FakeDispatcher:
    def __init__(self, envelopes, cancel_after=None, renew_ok=True, fail_results=0):
        self.queue = list(envelopes)
        self.results, self.beats, self.progress_calls = [], [], []
        self.cancel_after, self.polls, self.renew_ok, self.fail_results = cancel_after, 0, renew_ok, fail_results

    def heartbeat(self, runner_id, state, capabilities):
        self.beats.append(state)

    def claim(self, runner_id, capabilities):
        return self.queue.pop(0) if self.queue else None

    def progress(self, request_id, generation, phase):
        self.progress_calls.append(phase)

    def cancel_requested(self, request_id, generation):
        self.polls += 1
        return self.cancel_after is not None and self.polls >= self.cancel_after

    def renew(self, request_id, generation):
        return self.renew_ok

    def result(self, request_id, generation, *, event_id, status, artifacts, detail):
        if self.fail_results:
            self.fail_results -= 1
            raise ConnectionError("dispatcher down")
        self.results.append({"request_id": request_id, "event_id": event_id, "status": status,
                             "artifacts": artifacts, "detail": detail})
        return {"state": "accepted"}


def cfg(tmp_path, mirror_path, **over):
    base = dict(runner_id="mac-1", actors=("codex", "claude-code"), scopes=("research", "build"),
                repositories={"personal-ai-agents": mirror_path}, workspace_root=tmp_path / "ws",
                artifact_root=tmp_path / "artifacts", max_cost_usd=5.0, lease_renew_s=0.0, tick_s=0.01,
                kill_grace_s=1.0)
    base.update(over)
    return runner.RunnerConfig(**base)


def fake_cli(monkeypatch, script):
    monkeypatch.setattr(commands, "argv", lambda actor, scope, brief, task_dir: [sys.executable, "-c", script])


# ── envelope ────────────────────────────────────────────────────────────────

def test_envelope_refuses_authority_fields_and_loose_refs(mirror):
    _, ref = mirror
    ok = dict(allowed_repositories={"personal-ai-agents": "x"}, max_cost_usd=5.0, now=NOW)
    envelope.parse(env_doc(ref), **ok)
    for bad, code in ((env_doc(ref, push=True), "envelope_fields_invalid"),
                      (env_doc(ref, ref="main"), "envelope_ref_must_be_exact_commit"),
                      (env_doc(ref, repository="other"), "envelope_repository_not_allowed"),
                      (env_doc(ref, scope="deploy"), "envelope_scope_invalid"),
                      (env_doc(ref, cost_cap_usd=50), "envelope_cost_cap_invalid"),
                      (env_doc(ref, deadline=(NOW - timedelta(minutes=1)).isoformat()), "envelope_deadline_invalid"),
                      (env_doc(ref, generation=True), "envelope_generation_invalid")):
        with pytest.raises(envelope.EnvelopeRefused) as exc:
            envelope.parse(bad, **ok)
        assert str(exc.value) == code


# ── workspace ───────────────────────────────────────────────────────────────

def test_workspace_has_no_remote_and_exact_ref(tmp_path, mirror):
    path, ref = mirror
    rid = str(uuid.uuid4())
    task = workspace.prepare(tmp_path / "ws", path, rid, ref)
    assert git(task, "remote") == ""
    assert git(task, "rev-parse", "HEAD") == ref
    with pytest.raises(subprocess.CalledProcessError):
        git(task, "push")
    with pytest.raises(workspace.WorkspaceError):
        workspace.prepare(tmp_path / "ws", path, rid, ref)      # never reused


def test_child_env_drops_credentials(monkeypatch):
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "MINIMOI_MODEL_GATEWAY_KEY",
              "ANTHROPIC_API_KEY", "DATABASE_URL", "SSH_AUTH_SOCK"):
        monkeypatch.setenv(k, "secret")
    env = workspace.child_env()
    assert not any(v == "secret" for v in env.values())
    with pytest.raises(workspace.WorkspaceError):
        workspace.child_env({"GH_TOKEN": "x"})


def test_remove_refuses_paths_outside_root(tmp_path):
    with pytest.raises(workspace.WorkspaceError):
        workspace.remove(tmp_path / "elsewhere", tmp_path / "ws")


# ── lifecycle ───────────────────────────────────────────────────────────────

def test_build_task_completes_with_patch_evidence(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "open('README.md','a').write('added\\n'); print('done')")
    d = FakeDispatcher([env_doc(ref)])
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "completed" and out["delivered"]
    res = d.results[0]
    assert res["status"] == "completed"
    kinds = {a["kind"]: a for a in res["artifacts"]}
    patch = (tmp_path / "artifacts" / kinds["patch"]["ref"]).read_bytes()
    assert b"+added" in patch and kinds["patch"]["review_status"] == "unreviewed"
    assert kinds["patch"]["author"] == "codex"
    assert d.beats[0] == "online" and "busy" in d.beats and d.beats[-1] == "online"
    # the mirror itself is untouched
    assert (path / "README.md").read_text() == "hello\n"


def test_research_scope_returns_output_only(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "print('the answer')")
    d = FakeDispatcher([env_doc(ref, scope="research")])
    runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert [a["kind"] for a in d.results[0]["artifacts"]] == ["output"]


def test_cancel_kills_and_reports_cancelled(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "import time; time.sleep(30)")
    d = FakeDispatcher([env_doc(ref)], cancel_after=2)
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "cancelled" and d.results[0]["status"] == "cancelled"


def test_deadline_kills_and_reports_timed_out(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "import time; time.sleep(30)")
    clock = iter([NOW] + [NOW + timedelta(hours=1)] * 1000)
    d = FakeDispatcher([env_doc(ref)])
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: next(clock))
    assert out["state"] == "timed_out"


def test_lost_lease_kills_and_posts_nothing(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "import time; time.sleep(30)")
    d = FakeDispatcher([env_doc(ref)], renew_ok=False)
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "lease_lost" and d.results == []


def test_refused_envelope_is_reported(tmp_path, mirror):
    path, ref = mirror
    d = FakeDispatcher([env_doc(ref, ref="main")])
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out == {"state": "refused", "code": "envelope_ref_must_be_exact_commit"}
    assert d.results[0]["status"] == "refused"


def test_runner_without_capability_refuses(tmp_path, mirror):
    path, ref = mirror
    d = FakeDispatcher([env_doc(ref, actor="claude-code")])
    out = runner.run_once(d, cfg(tmp_path, path, actors=("codex",)), now=lambda: NOW)
    assert out["code"] == "runner_not_capable"


def test_result_is_retried_with_stable_event_id(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "print('ok')")
    d = FakeDispatcher([env_doc(ref)], fail_results=2)
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["delivered"] and len(d.results) == 1


def test_idle_when_nothing_to_claim(tmp_path, mirror):
    path, _ = mirror
    assert runner.run_once(FakeDispatcher([]), cfg(tmp_path, path), now=lambda: NOW) == {"state": "idle"}


def test_command_vectors_are_sandboxed_and_shell_free(tmp_path):
    c = commands.argv("codex", "build", "x", tmp_path)
    assert c[:4] == ["codex", "exec", "--sandbox", "workspace-write"]
    assert commands.argv("codex", "research", "x", tmp_path)[3] == "read-only"
    cl = commands.argv("claude-code", "build", "x", tmp_path)
    assert "Bash(git push:*)" in cl and "--permission-mode" in cl
    with pytest.raises(ValueError):
        commands.argv("other", "build", "x", tmp_path)
