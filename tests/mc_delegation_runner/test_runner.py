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
        return {"state": "accepted", "event_id": event_id}


class Enforcer:
    """Test double for a budget-binding adapter: records the cap it was asked to bind."""

    def __init__(self):
        self.caps = []

    def apply(self, argv, env, cap_usd):
        self.caps.append(cap_usd)
        return argv, dict(env, FAKE_COST_CAP_USD=str(cap_usd))


def cfg(tmp_path, mirror_path, **over):
    base = dict(runner_id="mac-1", actors=("codex", "claude-code"), scopes=("research", "build"),
                repositories={"personal-ai-agents": mirror_path}, workspace_root=tmp_path / "ws",
                artifact_root=tmp_path / "artifacts", max_cost_usd=5.0, lease_renew_s=0.0, tick_s=0.01,
                kill_grace_s=1.0, cost_enforcers={"codex": Enforcer(), "claude-code": Enforcer()})
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
    rid = str(uuid.uuid4()) + "-g1"
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


def test_result_survives_dispatcher_outage_and_is_resent_later(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "print('ok')")
    d = FakeDispatcher([env_doc(ref)], fail_results=99)
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "completed" and out["delivered"] is False and d.results == []
    d.fail_results = 0
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)       # next poll: resend first
    assert out == {"state": "idle"} and len(d.results) == 1 and d.results[0]["status"] == "completed"


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



# ── Codex review D-F1 / D-F2 / D-F3 ─────────────────────────────────────────
import os
import signal


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.parametrize("method", ["progress", "cancel_requested", "renew"])
def test_child_is_stopped_when_dispatcher_calls_fail(tmp_path, mirror, monkeypatch, method):
    path, ref = mirror
    fake_cli(monkeypatch, "import os,time; open('pid','w').write(str(os.getpid())); time.sleep(30)")
    d = FakeDispatcher([env_doc(ref)])
    def boom(*a, **k):
        import time as _t
        _t.sleep(0.2)
        raise ConnectionError("dispatcher unreachable")
    monkeypatch.setattr(d, method, boom)
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "control_lost" and out["delivered"] is False and d.results == []
    pid_file = next((tmp_path / "ws").glob("*/pid"), None)
    if pid_file:
        assert not _alive(int(pid_file.read_text()))


def test_reclaim_after_crash_reports_recorded_result_without_rerun(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "open('README.md','a').write('x\\n')")
    doc = env_doc(ref)
    d = FakeDispatcher([doc], fail_results=99)
    first = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert first["state"] == "completed" and not first["delivered"]
    fake_cli(monkeypatch, "raise SystemExit(1)")                          # would fail if rerun
    d2 = FakeDispatcher([dict(doc)])
    d2.fail_results = 0
    # simulate the result never reaching even the resend: remove the acknowledged flag path order
    out = runner.run_once(d2, cfg(tmp_path, path), now=lambda: NOW)
    assert d2.results and d2.results[0]["status"] == "completed"


def test_new_generation_gets_its_own_workspace_and_artifacts(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "print('ok')")
    doc = env_doc(ref)
    runner.run_once(FakeDispatcher([doc]), cfg(tmp_path, path), now=lambda: NOW)
    runner.run_once(FakeDispatcher([dict(doc, generation=2)]), cfg(tmp_path, path), now=lambda: NOW)
    names = sorted(p.name for p in (tmp_path / "ws").iterdir())
    assert names[0].endswith("-g1") and names[1].endswith("-g2")
    assert len(list((tmp_path / "artifacts").glob("*.result.json"))) == 2


def test_output_bound_is_enforced_while_running(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "import sys,time\nwhile True:\n    sys.stdout.write('x'*10000); sys.stdout.flush(); time.sleep(0.01)")
    out = runner.run_once(FakeDispatcher([env_doc(ref)]), cfg(tmp_path, path, max_output_bytes=50_000),
                          now=lambda: NOW)
    assert out["state"] == "output_limit"
    output = next(a for a in out["artifacts"] if a["kind"] == "output")
    assert output["bytes"] <= 50_000


def test_unenforceable_cost_cap_is_refused(tmp_path, mirror):
    path, ref = mirror
    d = FakeDispatcher([env_doc(ref)])
    out = runner.run_once(d, cfg(tmp_path, path, cost_enforcers={}), now=lambda: NOW)
    assert out["state"] == "refused" and d.results[0]["detail"]["code"] == "cost_limit_unenforceable"


# ── Codex second repair review of 3817b1af ──────────────────────────────────
import json


def test_immediate_large_burst_is_bounded_and_reported(tmp_path, mirror, monkeypatch):
    """A child that writes far past the bound and exits before any poll: log stays bounded and
    the task is output_limit, not completed."""
    path, ref = mirror
    fake_cli(monkeypatch, "import sys; sys.stdout.write('x' * 3_000_000)")
    out = runner.run_once(FakeDispatcher([env_doc(ref)]), cfg(tmp_path, path, max_output_bytes=50_000,
                                                               tick_s=5.0), now=lambda: NOW)
    assert out["state"] == "output_limit"
    log = next((tmp_path / "artifacts").glob("*.log"))
    assert log.stat().st_size <= 50_000


def test_descendants_are_stopped_after_the_parent_exits(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "import subprocess,sys\n"
                          "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
                          " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                          "open('child.pid','w').write(str(p.pid))")
    out = runner.run_once(FakeDispatcher([env_doc(ref)]), cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "completed"
    pid = int(next((tmp_path / "ws").glob("*/child.pid")).read_text())
    assert not _alive(pid)


@pytest.mark.parametrize("ack", [None, {}, {"state": "refused"}, {"state": "accepted"},
                                 {"state": "accepted", "event_id": "someone-else"}])
def test_result_needs_a_matching_acknowledgement(tmp_path, mirror, monkeypatch, ack):
    path, ref = mirror
    fake_cli(monkeypatch, "print('ok')")
    d = FakeDispatcher([env_doc(ref)])
    real = d.result
    monkeypatch.setattr(d, "result", lambda *a, **k: (real(*a, **k), ack)[1])
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["delivered"] is False
    record = json.loads(next((tmp_path / "artifacts").glob("*.result.json")).read_text())
    assert record["acknowledged"] is False                 # stays pending, resent on the next run
    monkeypatch.setattr(d, "result", real)
    assert runner.resend_pending(d, cfg(tmp_path, path)) == 1


def _crash_at(monkeypatch, target):
    class Crash(BaseException):
        pass

    real = runner._write_json

    def write(path, doc):
        real(path, doc)
        if path.name.endswith(".stage.json") and doc["stage"] == target:
            raise Crash()
    monkeypatch.setattr(runner, "_write_json", write)
    return Crash


def test_crash_after_child_exited_keeps_its_real_outcome(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "open('README.md','a').write('kept\\n'); print('done')")
    doc = env_doc(ref)
    Crash = _crash_at(monkeypatch, "exited")
    with pytest.raises(Crash):
        runner.run_once(FakeDispatcher([doc]), cfg(tmp_path, path), now=lambda: NOW)
    monkeypatch.undo()
    fake_cli(monkeypatch, "raise SystemExit(3)")            # would fail if it were rerun
    d = FakeDispatcher([dict(doc)])
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "completed" and out["resumed"] and d.results[0]["status"] == "completed"
    patch = next(a for a in d.results[0]["artifacts"] if a["kind"] == "patch")
    assert b"+kept" in (tmp_path / "artifacts" / patch["ref"]).read_bytes()


def test_crash_after_artifacts_reports_them_without_recollecting(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "print('done')")
    doc = env_doc(ref)
    Crash = _crash_at(monkeypatch, "artifacts")
    with pytest.raises(Crash):
        runner.run_once(FakeDispatcher([doc]), cfg(tmp_path, path), now=lambda: NOW)
    monkeypatch.undo()
    d = FakeDispatcher([dict(doc)])
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "completed" and d.results[0]["artifacts"][0]["kind"] == "output"


def test_crash_while_child_running_is_interrupted_and_orphans_are_stopped(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "import os,time; open('pid','w').write(str(os.getpid())); time.sleep(60)")
    doc = env_doc(ref)

    class Crash(BaseException):
        pass

    d = FakeDispatcher([doc])

    def crash(*a, **k):
        import time as _t
        _t.sleep(0.3)                                       # let the child record its pid
        raise Crash()
    monkeypatch.setattr(d, "progress", crash)
    monkeypatch.setattr(runner, "_kill_group", lambda *a, **k: None)   # the crashed runner never cleaned up
    with pytest.raises(Crash):
        runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    monkeypatch.undo()
    pid = int(next((tmp_path / "ws").glob("*/pid")).read_text())
    assert _alive(pid)
    d2 = FakeDispatcher([dict(doc)])
    out = runner.run_once(d2, cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "interrupted" and d2.results[0]["detail"]["code"] == "child_outcome_unknown"
    assert not _alive(pid)


def test_workspace_prepared_but_never_started_is_rebuilt(tmp_path, mirror, monkeypatch):
    """A durable 'preparing' stage without 'started' means the child never passed the barrier."""
    path, ref = mirror
    fake_cli(monkeypatch, "print('ok')")
    doc = env_doc(ref)
    task = f"{doc['request_id']}-g1"
    workspace.prepare(tmp_path / "ws", path, task, ref)
    (tmp_path / "artifacts").mkdir(mode=0o700)
    runner._write_json(tmp_path / "artifacts" / f"{task}.stage.json", {"stage": "preparing"})
    out = runner.run_once(FakeDispatcher([doc]), cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "completed"


def test_workspace_without_any_stage_is_kept_and_reported_never_rerun(tmp_path, mirror, monkeypatch):
    """Codex R-F3: missing metadata is not proof that nothing ran."""
    path, ref = mirror
    fake_cli(monkeypatch, "open('ran','w').write('1')")
    doc = env_doc(ref)
    task_dir = workspace.prepare(tmp_path / "ws", path, f"{doc['request_id']}-g1", ref)
    (task_dir / "someone-elses-work.txt").write_text("keep me")
    d = FakeDispatcher([doc])
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "interrupted" and d.results[0]["detail"]["code"] == "workspace_without_stage"
    assert (task_dir / "someone-elses-work.txt").exists() and not (task_dir / "ran").exists()


def test_cost_cap_is_bound_by_the_adapter_and_a_flag_alone_is_not_enough(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "import os; print(os.environ['FAKE_COST_CAP_USD'])")
    enforcer = Enforcer()
    c = cfg(tmp_path, path, cost_enforcers={"codex": enforcer})
    out = runner.run_once(FakeDispatcher([env_doc(ref, cost_cap_usd=1.5)]), c, now=lambda: NOW)
    assert out["state"] == "completed" and enforcer.caps == [1.5]
    d = FakeDispatcher([env_doc(ref)])
    out = runner.run_once(d, cfg(tmp_path / "b", path, cost_enforcers={"codex": True}), now=lambda: NOW)
    assert d.results[0]["detail"]["code"] == "cost_limit_unenforceable"


# ── Codex R-F1..R-F4 ────────────────────────────────────────────────────────

def test_child_never_starts_before_its_ownership_is_durable(tmp_path, mirror, monkeypatch):
    """R-F1: a crash between spawn and the 'started' stage leaves no agent running."""
    path, ref = mirror
    fake_cli(monkeypatch, "open('agent_ran','w').write('1')")

    class Crash(BaseException):
        pass
    real = runner._write_json

    def crash_on_started(p, doc):
        if doc.get("stage") == "started" and doc.get("pgid"):   # the write that follows the spawn
            raise Crash()
        real(p, doc)
    monkeypatch.setattr(runner, "_write_json", crash_on_started)
    monkeypatch.setattr(runner, "_kill_group", lambda *a, **k: None)   # a real crash runs no cleanup
    with pytest.raises(Crash):
        runner.run_once(FakeDispatcher([env_doc(ref)]), cfg(tmp_path, path), now=lambda: NOW)
    import time as _t
    _t.sleep(1.0)
    assert not list((tmp_path / "ws").glob("*/agent_ran"))


def test_recovery_never_signals_a_group_whose_identity_does_not_match(tmp_path, mirror, monkeypatch):
    """R-F2: a saved pgid that now belongs to someone else is held, not killed."""
    path, ref = mirror
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        doc = env_doc(ref)
        task = f"{doc['request_id']}-g1"
        (tmp_path / "artifacts").mkdir(mode=0o700)
        runner._write_json(tmp_path / "artifacts" / f"{task}.stage.json",
                           {"stage": "started", "pgid": other.pid, "identity": "999 Thu Jan  1 00:00:00 1970"})
        d = FakeDispatcher([doc])
        out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
        assert out["state"] == "interrupted" and d.results[0]["detail"]["code"] == "process_identity_unverified"
        assert other.poll() is None                                     # untouched
    finally:
        other.kill()
        other.wait()


def test_recovery_stops_a_verified_orphan(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        doc = env_doc(ref)
        task = f"{doc['request_id']}-g1"
        (tmp_path / "artifacts").mkdir(mode=0o700)
        runner._write_json(tmp_path / "artifacts" / f"{task}.stage.json",
                           {"stage": "started", "pgid": orphan.pid, "identity": runner._identity(orphan.pid)})
        d = FakeDispatcher([doc])
        out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
        assert out["state"] == "interrupted" and d.results[0]["detail"]["code"] == "child_outcome_unknown"
        orphan.wait(timeout=10)
    finally:
        if orphan.poll() is None:
            orphan.kill()
            orphan.wait()


def test_capture_failure_is_a_failure_even_with_exit_zero(tmp_path, mirror, monkeypatch):
    """R-F4: a broken output pipe returned 'completed' with empty evidence."""
    path, ref = mirror
    fake_cli(monkeypatch, "print('ok')")

    class BadPipe:
        def __init__(self, pipe):
            self.pipe = pipe

        def fileno(self):
            return 10_000_000                                            # EBADF on read

        def close(self):
            self.pipe.close()
    real_init = runner._BoundedCapture.__init__

    def broken_init(self, pipe, log_path, limit):
        real_init(self, BadPipe(pipe), log_path, limit)
    monkeypatch.setattr(runner._BoundedCapture, "__init__", broken_init)
    d = FakeDispatcher([env_doc(ref)])
    out = runner.run_once(d, cfg(tmp_path, path), now=lambda: NOW)
    assert out["state"] == "failed" and d.results[0]["detail"]["code"].startswith("capture_read_")


def test_stage_writes_fsync_the_directory(tmp_path, monkeypatch):
    synced = []
    real = os.fsync

    def spy(fd):
        synced.append(os.fstat(fd).st_mode)
        return real(fd)
    monkeypatch.setattr(os, "fsync", spy)
    runner._write_atomic(tmp_path / "x.json", b"{}")
    import stat as _s
    assert any(_s.S_ISDIR(m) for m in synced)
