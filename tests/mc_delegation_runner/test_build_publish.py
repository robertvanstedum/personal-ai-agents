"""A delegated build, finished by the runner (#693): its own branch, the runner's commit, the runner's own test run,
and the optional push and draft pull request. Fakes only: no real push to GitHub, no real GitHub call, no real token.
The agent CLI is a short Python script; git runs for real against temporary repositories."""
import base64
import json
import os
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_runner as R  # noqa: E402
from core.mc_delegation_runner import cli, commands, cost, publish, runner, workspace  # noqa: E402
from core.mc_delegation_runner.dispatcher_adapter import OUTPUT_MAX, output_of  # noqa: E402

mirror = R.mirror
NOW, git, env_doc, cfg, FakeDispatcher, fake_cli = R.NOW, R.git, R.env_doc, R.cfg, R.FakeDispatcher, R.fake_cli
TOKEN = "github_pat_SENTINEL0123456789abcdefSENTINEL"          # a sentinel, not a token
TOKEN_B64 = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
BRANCH = re.compile(r"^mc/[0-9a-f]{8}-g1$")
EDIT_AND_TEST = "import os; os.makedirs('tests', exist_ok=True); open('tests/test_new.py','w').write('def test_a(): pass\\n'); print('done')"
EDIT_README = "open('README.md','a').write('added\\n'); print('done')"
PASSING = (sys.executable, "-c", "print('collected'); print('2 passed in 0.01s')")
FAILING = (sys.executable, "-c", "print('1 failed, 1 passed in 0.02s'); raise SystemExit(1)")


def probe_cli(monkeypatch, tmp_path, edit=EDIT_README):
    """A fake agent that also writes down what its checkout looks like while it runs."""
    out = tmp_path / "probe.json"
    script = ("import subprocess, json\n"
              "g = lambda *a: subprocess.run(['git', *a], capture_output=True, text=True)\n"
              f"open({str(out)!r}, 'w').write(json.dumps({{'branch': g('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip(),"
              " 'remote': g('remote').stdout.strip(), 'push': g('push').returncode,"
              " 'push_default': g('config', 'push.default').stdout.strip(),"
              " 'env': sorted(__import__('os').environ)}))\n" + edit)
    fake_cli(monkeypatch, script)
    return out


class RecordingPublisher:
    """Stands in for publish.Publisher: records each call and what the branch looks like at that moment."""

    def __init__(self, answer=None, ref=None):
        self.calls, self.answer, self.ref = [], answer, ref

    def publish(self, task_dir, branch, *, title, body, base_branch="main", commit=None):
        call = {"branch": branch, "commit": commit, "title": title, "body": body, "base": base_branch,
                "head": git(task_dir, "rev-parse", "HEAD"), "on": git(task_dir, "rev-parse", "--abbrev-ref", "HEAD"),
                "log": git(task_dir, "log", "-1", "--format=%an|%ae|%cn|%ce|%B"),
                "remote": git(task_dir, "remote")}
        if self.ref:
            call["commits"] = git(task_dir, "rev-list", "--count", f"{self.ref}..HEAD")
        self.calls.append(call)
        return self.answer or {"state": "published", "pr_url": "https://github.com/o/r/pull/7", "pr_number": 7}


def run(tmp_path, path, doc, **over):
    d = over.pop("dispatcher", None) or FakeDispatcher([doc])
    out = runner.run_once(d, cfg(tmp_path, path, **over), now=lambda: NOW)
    return out, d


def artifact(tmp_path, result, kind):
    ref = next(a for a in result["artifacts"] if a["kind"] == kind)["ref"]
    return (tmp_path / "artifacts" / ref).read_bytes()


# ── branch and commit ───────────────────────────────────────────────────────────────────────────────────────────

def test_a_build_runs_on_its_own_branch_and_the_runner_commits_it(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    probe = probe_cli(monkeypatch, tmp_path)
    doc = env_doc(ref, brief="Add a line to README for issue #693.")
    pub = RecordingPublisher(ref=ref)
    out, d = run(tmp_path, path, doc, publisher=pub)
    seen = json.loads(probe.read_text())
    branch = f"mc/{doc['request_id'].replace('-', '')[:8]}-g1"
    # while the agent ran: on the branch, with no remote, pushes refused
    assert seen["branch"] == branch and BRANCH.match(branch)
    assert seen["remote"] == "" and seen["push"] != 0 and seen["push_default"] == "nothing"
    assert out["state"] == "completed"
    detail = d.results[0]["detail"]
    assert detail["branch"] == branch and detail["changed"] is True and re.fullmatch(r"[0-9a-f]{40}", detail["commit"])
    assert detail["commit"] != ref and detail["base_ref"] == ref and detail["exit_code"] == 0
    (call,) = pub.calls
    assert call["branch"] == branch and call["commit"] == detail["commit"] == call["head"] and call["on"] == branch
    assert call["commits"] == "1" and call["remote"] == ""             # one commit; still no remote afterwards
    name, email, cname, cemail, message = call["log"].split("|", 4)
    assert (name, email, cname, cemail) == ("MiniMoi delegated build", "noreply@minimoi.ai") * 2
    assert message.splitlines()[0] == f"Delegated build {branch[3:]} (#693)"
    assert "Co-Authored-By: Claude <noreply@anthropic.com>" in message
    assert b"+added" in artifact(tmp_path, d.results[0], "patch")        # the patch artifact is kept
    assert (path / "README.md").read_text() == "hello\n" and git(path, "branch", "--list", "mc/*") == ""


def test_a_commit_message_names_no_issue_when_the_brief_names_none(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_README)
    pub = RecordingPublisher()
    run(tmp_path, path, env_doc(ref, brief="Tidy the README."), publisher=pub)
    assert re.fullmatch(r"Delegated build [0-9a-f]{8}-g1", pub.calls[0]["log"].split("|", 4)[4].splitlines()[0])


def test_research_is_unchanged_detached_no_commit_no_tests_no_publish(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    probe = probe_cli(monkeypatch, tmp_path, edit="print('the answer')")
    pub = RecordingPublisher()
    out, d = run(tmp_path, path, env_doc(ref, scope="research"), publisher=pub)
    assert json.loads(probe.read_text())["branch"] == "HEAD"              # detached, as before
    assert out["state"] == "completed" and pub.calls == []
    assert [a["kind"] for a in d.results[0]["artifacts"]] == ["output"]
    assert d.results[0]["detail"] == {"exit_code": 0, "base_ref": ref}
    assert output_of(tmp_path / "artifacts", d.results[0]["artifacts"])[0] == "the answer"


def test_a_build_that_changed_nothing_makes_no_commit(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, "print('nothing to do')")
    pub = RecordingPublisher()
    out, d = run(tmp_path, path, env_doc(ref), publisher=pub, test_command=PASSING)
    detail = d.results[0]["detail"]
    assert out["state"] == "completed" and pub.calls == []
    assert detail["changed"] is False and detail["commit"] is None
    assert detail["tests"]["note"] == "no_changes" and detail["publish"] == {"state": "skipped", "code": "no_changes"}


def test_workspace_branch_names_are_strict(tmp_path, mirror):
    path, ref = mirror
    rid = "0f3a9c2e-1111-4222-8333-444455556666"
    assert workspace.branch_name(rid, 3) == "mc/0f3a9c2e-g3"
    for bad in ("main", "mc/x", "mc/0f3a9c2e-g0", "mc/0f3a9c2e-g1/..", "-mc/0f3a9c2e-g1", "mc/0F3A9C2E-g1"):
        with pytest.raises(workspace.WorkspaceError):
            workspace.prepare(tmp_path / "ws", path, rid + "-g1", ref, branch=bad)
    with pytest.raises(workspace.WorkspaceError):
        workspace.branch_name("not-a-uuid-at-all", 1)


# ── the runner's own test run ───────────────────────────────────────────────────────────────────────────────────

def test_the_runners_test_run_is_recorded_when_it_passes(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    monkeypatch.setenv("GH_TOKEN", "must-not-reach-the-tests")
    fake_cli(monkeypatch, EDIT_AND_TEST)
    show = (sys.executable, "-c", "import os, sys; print('GH' if 'GH_TOKEN' in os.environ else 'clean', sys.argv[1:]); print('1 passed in 0.01s')")
    out, d = run(tmp_path, path, env_doc(ref), test_command=show)
    tests = d.results[0]["detail"]["tests"]
    assert out["state"] == "completed"
    assert tests["command"] == [*show, "tests/test_new.py"] and tests["exit_code"] == 0 and tests["timed_out"] is False
    assert "clean ['tests/test_new.py']" in tests["summary"] and tests["summary"].endswith("1 passed in 0.01s")
    assert isinstance(tests["duration_s"], float) and "note" not in tests
    recorded = json.loads(artifact(tmp_path, d.results[0], "tests"))
    assert recorded["exit_code"] == 0 and recorded["command"] == tests["command"]
    kinds = {a["kind"]: a for a in d.results[0]["artifacts"]}
    assert list(kinds) == ["output", "patch", "tests", "summary"] and kinds["tests"]["author"] == "runner"


def test_a_failing_test_run_is_reported_and_the_job_stays_completed(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_AND_TEST)
    pub = RecordingPublisher()
    out, d = run(tmp_path, path, env_doc(ref), test_command=FAILING, publisher=pub)
    assert out["state"] == "completed" and d.results[0]["status"] == "completed"
    assert d.results[0]["detail"]["tests"]["exit_code"] == 1
    assert "1 failed, 1 passed" in d.results[0]["detail"]["tests"]["summary"]
    assert len(pub.calls) == 1 and "Exit code: 1" in pub.calls[0]["body"]      # the draft says so itself


def test_a_test_run_past_its_timeout_is_stopped_and_recorded(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_AND_TEST)
    slow = (sys.executable, "-c", "import time; print('started', flush=True); time.sleep(60)")
    out, d = run(tmp_path, path, env_doc(ref), test_command=slow, test_timeout_s=0.5)
    tests = d.results[0]["detail"]["tests"]
    assert out["state"] == "completed" and tests["timed_out"] is True and tests["exit_code"] is None
    assert "started" in tests["summary"] and tests["duration_s"] < 30


def test_no_test_paths_found_is_said_plainly(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_README)
    out, d = run(tmp_path, path, env_doc(ref), test_command=FAILING)       # would fail if it ran
    tests = d.results[0]["detail"]["tests"]
    assert out["state"] == "completed"
    assert tests == {"command": None, "exit_code": None, "summary": "", "duration_s": 0.0, "timed_out": False,
                     "note": "no_tests_selected"}
    assert "Tests: not run (no_tests_selected)" in artifact(tmp_path, d.results[0], "summary").decode()


def test_a_test_command_that_cannot_start_is_recorded_not_raised(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_AND_TEST)
    out, d = run(tmp_path, path, env_doc(ref), test_command=(str(tmp_path / "no-such-python"), "-m", "pytest"))
    assert out["state"] == "completed" and d.results[0]["detail"]["tests"]["note"] == "test_command_unavailable"


def test_the_test_run_never_outlives_the_jobs_deadline(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_AND_TEST)
    clock = iter([NOW] * 3)
    late = NOW + timedelta(hours=2)
    out = runner.run_once(FakeDispatcher([env_doc(ref)]), cfg(tmp_path, path, test_command=FAILING, tick_s=5.0),
                          now=lambda: next(clock, late), sleep=lambda s: None)
    d_record = json.loads(next((tmp_path / "artifacts").glob("*.result.json")).read_text())
    if out["state"] == "completed":                                       # the agent finished before the deadline
        assert d_record["detail"]["tests"]["note"] == "deadline_reached"


def test_select_tests_takes_the_matching_directories_and_changed_test_files(tmp_path):
    for d in ("tests/mc_delegation_runner", "tests/guild", "tests/other"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "tests/other/test_x.py").write_text("")
    (tmp_path / "tests/mc_delegation_runner/test_y.py").write_text("")
    (tmp_path / "tests/other/helper.py").write_text("")
    changed = ["core/mc_delegation_runner/runner.py", "minimoi_portal/guild/x/y.py", "tests/other/test_x.py",
               "tests/mc_delegation_runner/test_y.py", "tests/other/helper.py", "tests/other/test_gone.py",
               "-p/evil.py", "../tests/other/test_x.py", "docs/readme.md", "README.md"]
    assert runner.select_tests(tmp_path, changed) == ["tests/mc_delegation_runner", "tests/guild", "tests/other/test_x.py"]
    assert runner.select_tests(tmp_path, ["README.md"]) == []
    assert runner.DEFAULT_TEST_COMMAND == (sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider")
    assert runner.RunnerConfig.__dataclass_fields__["test_timeout_s"].default == 600.0


def test_stop_during_the_test_run_ends_the_job_cancelled_and_publishes_nothing(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_AND_TEST)
    marker = tmp_path / "tests-started"
    slow = (sys.executable, "-c", f"import time; open({str(marker)!r}, 'w').write('1'); time.sleep(60)")
    d = FakeDispatcher([env_doc(ref)])
    d.cancel_requested = lambda request_id, generation: marker.exists()
    pub = RecordingPublisher()
    out, _ = run(tmp_path, path, None, dispatcher=d, test_command=slow, publisher=pub)
    assert out["state"] == "cancelled" and pub.calls == []
    detail = d.results[0]["detail"]
    assert detail["tests"]["note"] == "cancelled" and detail["publish"] == {"state": "skipped", "code": "not_completed"}


def test_a_lost_lease_during_the_test_run_posts_nothing_and_publishes_nothing(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_AND_TEST)
    marker = tmp_path / "tests-started"
    slow = (sys.executable, "-c", f"import time; open({str(marker)!r}, 'w').write('1'); time.sleep(60)")
    d = FakeDispatcher([env_doc(ref)])
    d.renew = lambda request_id, generation: not marker.exists()
    pub = RecordingPublisher()
    out, _ = run(tmp_path, path, None, dispatcher=d, test_command=slow, publisher=pub)
    assert out["state"] == "lease_lost" and d.results == [] and pub.calls == []


# ── publishing: only a completed build with a commit ────────────────────────────────────────────────────────────

def test_the_publisher_is_called_only_for_a_completed_build_with_a_commit(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    cases = (("exit1", "open('README.md','a').write('x\\n'); raise SystemExit(1)", {}, "failed"),
             ("sleep", "open('README.md','a').write('x\\n'); import time; time.sleep(30)", {"cancel_after": 2}, "cancelled"),
             ("nochange", "print('nothing')", {}, "completed"))
    for name, script, dargs, state in cases:
        fake_cli(monkeypatch, script)
        pub = RecordingPublisher()
        d = FakeDispatcher([env_doc(ref)], **dargs)
        out, _ = run(tmp_path / name, path, None, dispatcher=d, publisher=pub)
        assert out["state"] == state and pub.calls == [], name
        if state != "completed":                          # the runner's own steps never ran: no branch claims
            assert set(d.results[0]["detail"]) == {"exit_code", "base_ref"}, name
            assert {a["kind"] for a in d.results[0]["artifacts"]} == {"output", "patch"}, name
    fake_cli(monkeypatch, EDIT_README)
    pub = RecordingPublisher()
    out, d = run(tmp_path / "ok", path, env_doc(ref), publisher=pub)
    assert out["state"] == "completed" and len(pub.calls) == 1
    assert d.results[0]["detail"]["pr_url"] == "https://github.com/o/r/pull/7"
    assert d.results[0]["detail"]["publish"] == {"state": "published", "pr_url": "https://github.com/o/r/pull/7",
                                                 "pr_number": 7}


def test_without_a_publisher_nothing_is_published_and_the_result_says_so(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_README)
    out, d = run(tmp_path, path, env_doc(ref))
    detail = d.results[0]["detail"]
    assert out["state"] == "completed" and "pr_url" not in detail
    assert detail["publish"] == {"state": "skipped", "code": "publisher_not_configured"}
    assert set(detail) == {"exit_code", "base_ref", "branch", "commit", "changed", "tests", "publish"}


def test_stop_pressed_before_publishing_publishes_nothing(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_README)
    d = FakeDispatcher([env_doc(ref)])
    stage = lambda: next(iter((tmp_path / "artifacts").glob("*.stage.json")), None)      # noqa: E731
    d.cancel_requested = lambda request_id, generation: bool(stage()) and json.loads(stage().read_text())["stage"] == "tested"
    pub = RecordingPublisher()
    out, _ = run(tmp_path, path, None, dispatcher=d, publisher=pub)
    assert out["state"] == "cancelled" and pub.calls == []
    assert d.results[0]["detail"]["publish"] == {"state": "skipped", "code": "cancelled"}


def test_a_publish_failure_leaves_the_job_completed_and_is_recorded(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_README)
    for name, answer, expected in (
            ("push", {"state": "push_failed", "code": "auth_failed"}, {"state": "push_failed", "code": "auth_failed"}),
            ("pr", {"state": "pr_failed", "code": "http_403", "extra": TOKEN}, {"state": "pr_failed", "code": "http_403"}),
            ("odd", {"state": "published", "pr_url": "https://evil.example/pull/1", "pr_number": 1},
             {"state": "pr_failed", "code": "unknown"}),
            ("text", {"state": "refused", "code": "free text with " + TOKEN}, {"state": "refused", "code": "unknown"})):
        out, d = run(tmp_path / name, path, env_doc(ref), publisher=RecordingPublisher(answer=answer))
        assert out["state"] == "completed" and d.results[0]["status"] == "completed", name
        assert d.results[0]["detail"]["publish"] == expected and "pr_url" not in d.results[0]["detail"], name
        assert f"Pull request: not published: {expected['code']}" in artifact(tmp_path / name, d.results[0], "summary").decode()

    class Broken:
        def publish(self, *a, **k):
            raise RuntimeError("boom " + TOKEN)
    out, d = run(tmp_path / "raise", path, env_doc(ref), publisher=Broken())
    assert out["state"] == "completed"
    assert d.results[0]["detail"]["publish"] == {"state": "pr_failed", "code": "error_RuntimeError"}


# ── publish.Publisher itself ────────────────────────────────────────────────────────────────────────────────────

class Git:
    """``run`` for a Publisher: real git for the local steps, and a recorded, never-run push."""

    def __init__(self, push=(0, "", "")):
        self.calls, self.push = [], push

    def __call__(self, argv, *, cwd, env, timeout):
        self.calls.append({"argv": list(argv), "env": dict(env), "cwd": cwd})
        if "push" in argv:
            return self.push
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr


class GitHub:
    """``http`` for a Publisher: one repository's pull requests, in memory."""

    def __init__(self, post_status=201, get_status=200):
        self.calls, self.pulls, self.post_status, self.get_status = [], [], post_status, get_status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if method == "GET":
            return self.get_status, list(self.pulls) if self.get_status == 200 else None
        doc = json.loads(body)
        if self.post_status != 201:
            return self.post_status, {"message": "refused " + TOKEN}
        self.pulls.append({"number": 41 + len(self.pulls), "head": {"ref": doc["head"]}, "draft": doc["draft"]})
        return 201, self.pulls[-1]

    def posts(self):
        return [c for c in self.calls if c["method"] == "POST"]


def a_built_workspace(tmp_path, mirror):
    path, ref = mirror
    rid = "0f3a9c2e-1111-4222-8333-444455556666"
    task = workspace.prepare(tmp_path / "ws", path, rid + "-g1", ref, branch="mc/0f3a9c2e-g1")
    (task / "README.md").write_text("hello\nmore\n")
    done = workspace.commit_all(task, ref, "Delegated build 0f3a9c2e-g1\n")
    return task, done["commit"]


def publisher(tmp_path, run=None, http=None, **over):
    return publish.Publisher("robertvanstedum/personal-ai-agents", lambda: TOKEN, run=run or Git(), http=http or GitHub(),
                             base_env=workspace.child_env, workdir=tmp_path, **over)


def test_publish_pushes_exactly_the_one_branch_and_opens_a_draft(tmp_path, mirror):
    task, commit = a_built_workspace(tmp_path, mirror)
    g, hub = Git(), GitHub()
    out = publisher(tmp_path, g, hub).publish(task, "mc/0f3a9c2e-g1", title="Delegated build", body="text", commit=commit)
    assert out == {"state": "published", "pr_url": "https://github.com/robertvanstedum/personal-ai-agents/pull/41",
                   "pr_number": 41}
    (push,) = [c for c in g.calls if "push" in c["argv"]]
    assert push["argv"][-2:] == ["https://github.com/robertvanstedum/personal-ai-agents.git",
                                 "refs/heads/mc/0f3a9c2e-g1:refs/heads/mc/0f3a9c2e-g1"]
    assert not any(a.startswith("+") or a in ("--force", "-f", "--force-with-lease", "--tags", "--mirror", "--all",
                                              "--delete") for a in push["argv"])
    # the push runs from the runner's own bare repository, never inside the agent's checkout
    assert push["cwd"] is None and str(task) not in " ".join(push["argv"])
    assert all(c["cwd"] is None for c in g.calls)
    # the token: in that one call's environment only, never on a command line
    for c in g.calls:
        assert TOKEN not in " ".join(c["argv"]) and TOKEN_B64 not in " ".join(c["argv"])
        held = TOKEN_B64 in json.dumps(c["env"]) or TOKEN in json.dumps(c["env"])
        assert held == ("push" in c["argv"])
        assert c["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null" and c["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert push["env"]["GIT_CONFIG_KEY_0"] == "credential.helper" and push["env"]["GIT_CONFIG_VALUE_0"] == ""
    assert push["env"]["GIT_CONFIG_KEY_1"] == "http.https://github.com/.extraHeader"
    assert push["env"]["GIT_CONFIG_VALUE_1"] == f"Authorization: Basic {TOKEN_B64}"
    get, post = hub.calls
    assert get["method"] == "GET" and post["method"] == "POST"
    assert post["url"] == "https://api.github.com/repos/robertvanstedum/personal-ai-agents/pulls"
    assert json.loads(post["body"]) == {"title": "Delegated build", "head": "mc/0f3a9c2e-g1", "base": "main",
                                        "body": "text", "draft": True}
    assert TOKEN not in get["url"] and TOKEN not in post["url"] and TOKEN.encode() not in post["body"]
    assert TOKEN not in json.dumps(out)
    assert git(task, "remote") == "" and "extraheader" not in git(task, "config", "--list").lower()
    assert list(tmp_path.glob("mc-publish-*")) == []                      # the staging repository is gone


def test_publish_refuses_every_branch_but_the_jobs_own(tmp_path, mirror):
    task, commit = a_built_workspace(tmp_path, mirror)
    g, hub = Git(), GitHub()
    pub = publisher(tmp_path, g, hub)
    for bad in ("main", "release/x", "mc/main", "mc/0f3a9c2e-g0", "mc/0f3a9c2e-g1 ", "mc/0f3a9c2e-g1\n",
                "refs/heads/mc/0f3a9c2e-g1", "+mc/0f3a9c2e-g1", "mc/0f3a9c2e-g1:refs/heads/main", "mc/0F3A9C2E-g1",
                "mc/0f3a9c2e-g1/../../main", "", None, 7):
        assert pub.publish(task, bad, title="t", body="b", commit=commit) == {"state": "refused",
                                                                              "code": "branch_not_allowed"}, bad
    assert pub.publish(task, "mc/0f3a9c2e-g1", title="t", body="b", base_branch="mc/0f3a9c2e-g1")["state"] == "refused"
    assert pub.publish(task, "mc/0f3a9c2e-g1", title="t", body="b", commit="HEAD")["state"] == "refused"
    assert g.calls == [] and hub.calls == []                              # refused before anything ran
    for slug in ("personal-ai-agents", "a/b/c", "../x/y", "o/r.git", "o/r x", ""):
        with pytest.raises(ValueError):
            publish.Publisher(slug, lambda: TOKEN)


def test_publish_refuses_without_a_token_and_never_pushes_another_commit(tmp_path, mirror):
    task, commit = a_built_workspace(tmp_path, mirror)
    g, hub = Git(), GitHub()
    for token in (lambda: None, lambda: "", lambda: "short", lambda: "has space " * 5, lambda: 1 / 0):
        pub = publish.Publisher("o/r", token, run=g, http=hub, base_env=workspace.child_env, workdir=tmp_path)
        assert pub.publish(task, "mc/0f3a9c2e-g1", title="t", body="b") == {"state": "refused",
                                                                              "code": "no_publish_credential"}
    assert g.calls == [] and hub.calls == []
    out = publisher(tmp_path, g, hub).publish(task, "mc/0f3a9c2e-g1", title="t", body="b", commit="a" * 40)
    assert out == {"state": "push_failed", "code": "commit_mismatch"}
    assert not any("push" in c["argv"] for c in g.calls) and hub.calls == []


def test_a_failed_push_or_pull_request_returns_a_fixed_code_and_never_the_token(tmp_path, mirror):
    task, commit = a_built_workspace(tmp_path, mirror)
    noisy = f"remote: Invalid username or token {TOKEN}\nfatal: Authentication failed for 'https://x:{TOKEN_B64}@github.com/'"
    cases = (((128, "", noisy), "auth_failed"), ((1, "", " ! [rejected] mc/x -> mc/x (non-fast-forward)"), "rejected_not_fast_forward"),
             ((124, "", "timeout"), "timeout"), ((128, "", "fatal: something else " + TOKEN), "git_exit_128"))
    for push, code in cases:
        hub = GitHub()
        out = publisher(tmp_path, Git(push=push), hub).publish(task, "mc/0f3a9c2e-g1", title="t", body="b", commit=commit)
        assert out == {"state": "push_failed", "code": code} and hub.calls == []
    for status, code in ((403, "http_403"), (422, "http_422"), (500, "http_500")):
        out = publisher(tmp_path, Git(), GitHub(post_status=status)).publish(task, "mc/0f3a9c2e-g1", title="t", body="b")
        assert out == {"state": "pr_failed", "code": code}
    out = publisher(tmp_path, Git(), GitHub(get_status=401)).publish(task, "mc/0f3a9c2e-g1", title="t", body="b")
    assert out == {"state": "pr_failed", "code": "lookup_http_401"}

    def down(*a, **k):
        raise OSError("connection refused " + TOKEN)
    out = publisher(tmp_path, Git(), down).publish(task, "mc/0f3a9c2e-g1", title="t", body="b")
    assert out == {"state": "pr_failed", "code": "error_OSError"}
    assert publish.scrub(noisy, TOKEN).count("[token]") == 2 and TOKEN not in publish.scrub(noisy, TOKEN)


def test_publishing_twice_opens_one_pull_request(tmp_path, mirror):
    task, commit = a_built_workspace(tmp_path, mirror)
    hub = GitHub()
    first = publisher(tmp_path, Git(), hub).publish(task, "mc/0f3a9c2e-g1", title="t", body="b", commit=commit)
    second = publisher(tmp_path, Git(), hub).publish(task, "mc/0f3a9c2e-g1", title="t", body="b", commit=commit)
    assert first == second and first["pr_number"] == 41 and len(hub.posts()) == 1


def test_a_real_push_to_a_local_bare_repository_carries_only_the_branch(tmp_path, mirror):
    """The real git commands end to end, against a bare repository in the temporary directory (never GitHub)."""
    task, commit = a_built_workspace(tmp_path, mirror)
    (task / ".git" / "hooks").mkdir(exist_ok=True)
    hook = task / ".git" / "hooks" / "pre-push"                          # what an agent could have left behind
    hook.write_text(f"#!/bin/sh\nenv > {tmp_path / 'leak'}\nexit 1\n")
    hook.chmod(0o755)
    git(task, "config", "url.https://evil.example/.insteadOf", "https://github.com/")
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    out = publish.Publisher("o/r", lambda: TOKEN, http=GitHub(), base_env=workspace.child_env, workdir=tmp_path,
                            remote_url=str(remote)).publish(task, "mc/0f3a9c2e-g1", title="t", body="b", commit=commit)
    assert out["state"] == "published"
    assert git(remote, "for-each-ref", "--format=%(refname) %(objectname)") == f"refs/heads/mc/0f3a9c2e-g1 {commit}"
    assert not (tmp_path / "leak").exists()                               # the checkout's hook never ran
    assert TOKEN not in (remote / "config").read_text() and TOKEN not in (task / ".git" / "config").read_text()


# ── the token end to end, and resume ────────────────────────────────────────────────────────────────────────────

def test_the_token_never_reaches_argv_the_result_the_artifacts_or_the_output(tmp_path, mirror, monkeypatch, capfd):
    path, ref = mirror
    probe = probe_cli(monkeypatch, tmp_path, edit=EDIT_AND_TEST)
    g, hub = Git(), GitHub()
    out, d = run(tmp_path, path, env_doc(ref), publisher=publisher(tmp_path, g, hub), test_command=PASSING)
    assert out["state"] == "completed"
    assert d.results[0]["detail"]["pr_url"] == "https://github.com/robertvanstedum/personal-ai-agents/pull/41"
    assert len(hub.posts()) == 1 and len([c for c in g.calls if "push" in c["argv"]]) == 1
    everything = [json.dumps(out), json.dumps(d.results), probe.read_text()]
    everything += [p.read_text(errors="replace") for p in (tmp_path / "artifacts").iterdir() if p.is_file()]
    everything += [" ".join(c["argv"]) for c in g.calls] + list(capfd.readouterr())
    text, _ = output_of(tmp_path / "artifacts", d.results[0]["artifacts"])
    for blob in everything + [text]:
        assert TOKEN not in blob and TOKEN_B64 not in blob
    assert not any("GIT_CONFIG" in name or "TOKEN" in name for name in json.loads(probe.read_text())["env"])
    body = json.loads(hub.posts()[0]["body"])
    assert body["draft"] is True and body["base"] == "main" and BRANCH.match(body["head"])
    assert runner.PR_NOTICE in body["body"] and f"`{ref}`" in body["body"] and "> Add a line to README." in body["body"]
    assert "2 passed in 0.01s" in body["body"] and "Exit code: 0" in body["body"]


def _crash_in(monkeypatch, where):
    class Crash(BaseException):
        pass
    real = runner._write_json

    def write(path, doc):
        if path.name.endswith(".stage.json") and doc.get("stage") == where[0] and where[1] == "before":
            raise Crash()
        real(path, doc)
        if path.name.endswith(".stage.json") and doc.get("stage") == where[0] and where[1] == "after":
            raise Crash()
    monkeypatch.setattr(runner, "_write_json", write)
    return Crash


@pytest.mark.parametrize("where", [("committed", "after"), ("tested", "after"), ("published", "before"),
                                   ("published", "after"), ("artifacts", "before")])
def test_a_resumed_build_commits_once_and_opens_one_pull_request(tmp_path, mirror, monkeypatch, where):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_AND_TEST)
    doc = env_doc(ref)
    g, hub = Git(), GitHub()
    count = tmp_path / "test-runs"
    counting = (sys.executable, "-c", f"open({str(count)!r}, 'a').write('x'); print('1 passed in 0.01s')")
    Crash = _crash_in(monkeypatch, where)
    with pytest.raises(Crash):
        runner.run_once(FakeDispatcher([doc]), cfg(tmp_path, path, publisher=publisher(tmp_path, g, hub),
                                                   test_command=counting), now=lambda: NOW)
    monkeypatch.undo()
    fake_cli(monkeypatch, "raise SystemExit(3)")                          # the agent would fail if it were rerun
    d = FakeDispatcher([dict(doc)])
    out = runner.run_once(d, cfg(tmp_path, path, publisher=publisher(tmp_path, g, hub), test_command=counting),
                          now=lambda: NOW)
    assert out["state"] == "completed" and out["resumed"]
    detail = d.results[0]["detail"]
    assert len(hub.posts()) == 1 and len(hub.pulls) == 1                  # never a second pull request
    assert detail["pr_url"].endswith("/pull/41") and detail["changed"] is True
    assert detail["tests"]["exit_code"] == 0 and count.read_text() == "x"       # the tests ran once
    task_dir = next((tmp_path / "ws").iterdir())                          # kept on a resume
    assert git(task_dir, "rev-list", "--count", f"{ref}..HEAD") == "1" and git(task_dir, "rev-parse", "HEAD") == detail["commit"]
    pushes = [c for c in g.calls if "push" in c["argv"]]
    assert 1 <= len(pushes) <= 2 and all(p["argv"][-1].startswith("refs/heads/mc/") for p in pushes)
    assert b"test_new.py" in artifact(tmp_path, d.results[0], "patch")


# ── what the owner reads ────────────────────────────────────────────────────────────────────────────────────────

def test_the_answer_ends_with_the_fixed_footer(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_AND_TEST.replace("print('done')", "print('I changed one file.')"))
    out, d = run(tmp_path, path, env_doc(ref), publisher=RecordingPublisher(), test_command=PASSING)
    detail = d.results[0]["detail"]
    text, _ = output_of(tmp_path / "artifacts", d.results[0]["artifacts"])
    assert text == "\n".join(["I changed one file.", "", "--- Delegated build ---",
                              f"Branch: {detail['branch']}", f"Commit: {detail['commit']}", "Changed: yes",
                              f"Tests: {' '.join(PASSING)} tests/test_new.py | exit 0 | 2 passed in 0.01s",
                              "Pull request: https://github.com/o/r/pull/7"])


def test_the_footer_survives_an_answer_at_the_output_bound(tmp_path):
    (tmp_path / "t.output").write_bytes(("é" * OUTPUT_MAX).encode())
    footer = runner.build_footer({"branch": "mc/0f3a9c2e-g1", "commit": None, "changed": False,
                                  "tests": runner._no_tests("no_changes"),
                                  "publish": {"state": "skipped", "code": "no_changes"}})
    (tmp_path / "t.summary").write_text(footer)
    text, cost_seen = output_of(tmp_path, [{"kind": "output", "ref": "t.output"}, {"kind": "summary", "ref": "t.summary"}])
    assert text.endswith(footer) and len(text.encode()) <= OUTPUT_MAX and len(text) <= 64 * 1024 and cost_seen is None
    assert footer.splitlines() == ["--- Delegated build ---", "Branch: mc/0f3a9c2e-g1", "Commit: none", "Changed: no",
                                   "Tests: not run (no_changes)", "Pull request: not published: no_changes"]
    assert output_of(tmp_path, [{"kind": "summary", "ref": "t.summary"}])[0] == footer
    assert output_of(tmp_path, [{"kind": "summary", "ref": "../t.summary"}]) == (None, None)


def test_the_detail_always_fits_the_doors_bound(tmp_path, mirror):
    _, ref = mirror
    env = runner.env_mod.parse(env_doc(ref), allowed_repositories={"personal-ai-agents": "x"}, max_cost_usd=5.0, now=NOW)
    tests = {"command": [sys.executable, "-m", "pytest"] + ["tests/" + "p" * 400] * 20, "exit_code": 1,
             "summary": "\n".join("é\"\\ line %d " % i + "ü" * 90 for i in range(40))[-4000:], "duration_s": 3.2,
             "timed_out": False}
    build = {"branch": "mc/0f3a9c2e-g1", "commit": "a" * 40, "changed": True, "tests": tests,
             "publish": {"state": "published", "pr_url": "https://github.com/o/r/pull/7", "pr_number": 7}}
    detail = runner._detail(env, 0, build)
    assert len(json.dumps({**detail, "cost_usd": 4.9999})) <= 8192          # door.py: _small_json(detail, 8192)
    assert detail["tests"]["exit_code"] == 1 and detail["pr_url"].endswith("/pull/7")
    assert len(tests["summary"]) > len(detail["tests"]["summary"])          # cut from its start; the record keeps it all
    assert tests["summary"].endswith(detail["tests"]["summary"])


# ── command line, cap and installer ─────────────────────────────────────────────────────────────────────────────

def test_claude_code_may_run_a_fixed_list_in_a_build_and_still_cannot_push(tmp_path):
    c = commands.argv("claude-code", "build", "Do the thing.", tmp_path)
    assert c[:2] == ["claude", "-p"] and c[c.index("--permission-mode") + 1] == "acceptEdits"
    allowed = c[c.index("--allowedTools") + 1:c.index("--disallowedTools")]
    denied = c[c.index("--disallowedTools") + 1:]
    assert allowed == ["Bash(python -m pytest:*)", "Bash(python3 -m pytest:*)", "Bash(pytest:*)", "Bash(git status:*)",
                       "Bash(git diff:*)", "Bash(git log:*)", "Bash(git add:*)", "Bash(node --check:*)"]
    assert denied == ["Bash(git push:*)", "Bash(gh:*)", "WebFetch", "WebSearch", "Bash(curl:*)", "Bash(wget:*)"]
    assert not any(a in ("Bash", "Bash(*)", "Bash(git:*)", "Bash(git commit:*)") for a in allowed)
    assert "bypassPermissions" not in c and "--dangerously-skip-permissions" not in c
    prompt = c[2]
    assert prompt.startswith(commands.PREAMBLE) and prompt.endswith("Do the thing.")
    assert "Do not commit" in prompt and "pytest" in prompt and "real results" in prompt
    # the budget flag still binds after the lists (cost.ClaudeBudget)
    bound, _ = cost.ClaudeBudget().apply(c, {}, 5.0)
    assert bound[-2:] == ["--max-budget-usd", "5.00"]
    # research is exactly what it was
    assert commands.argv("claude-code", "research", "x", tmp_path) == [
        "claude", "-p", commands.PREAMBLE + "x", "--output-format", "json", "--permission-mode", "dontAsk",
        "--tools", "Read,Grep,Glob", "--allowedTools", "Read", "Grep", "Glob"]


def test_the_relay_caps_a_build_at_5_usd_and_research_at_2():
    from core.mc_delegation_dispatcher.relay import DelegationRelay
    seen = []

    class Store:
        def enqueue(self, **kw):
            seen.append(kw)
            return "accepted"
    relay = DelegationRelay(Store(), ref_for=lambda repo: "a" * 40)
    relay.start({"agent": "claude-code", "job_id": "j1", "task": "t", "scope": "build", "deadline_s": 1800})
    relay.start({"agent": "claude-code", "job_id": "j2", "task": "t", "scope": "research", "deadline_s": 1800})
    relay.start({"agent": "claude-code", "job_id": "j3", "task": "t"})
    relay.start({"agent": "codex", "job_id": "j4", "task": "t"})
    assert [(s["scope"], s["cost_cap_usd"], s["deadline_s"]) for s in seen] == [
        ("build", 5.0, 3600), ("research", 2.0, 1800), ("research", 2.0, 1800), ("research", 2.0, 1800)]
    assert relay.start({"agent": "codex", "job_id": "j5", "task": "t", "scope": "build"})[0] == "refused"


def test_the_publish_credential_is_a_choice_and_gh_is_the_default(capsys, monkeypatch):
    """Fake sources only: neither `gh auth token` nor the Keychain is ever asked here."""
    asked = []

    def source(name, value):
        def get(environment):
            asked.append((name, environment))
            return value
        return get
    have = {"gh": source("gh", TOKEN), "keychain": source("keychain", None)}
    slug = "robertvanstedum/personal-ai-agents"
    pub = cli._publisher(slug, "prod", sources=have)                      # default: gh
    assert isinstance(pub, publish.Publisher) and capsys.readouterr().out == "" and asked == [("gh", "prod")]
    assert pub.remote_url == "https://github.com/robertvanstedum/personal-ai-agents.git"
    assert pub._token() == TOKEN and asked == [("gh", "prod")] * 2        # asked again at each publish, not kept
    none = cli._publisher(slug, "prod", "keychain", sources=have)         # no token: one plain line, still a publisher
    line = capsys.readouterr().out
    assert isinstance(none, publish.Publisher) and none._token() is None
    assert "NOT published" in line and "minimoi_delegation_publish" in line and line.count("\n") == 1
    assert TOKEN not in line
    with pytest.raises(SystemExit):
        cli._publisher("not a slug", "prod", sources=have)
    assert sorted(cli.PUBLISH_CREDENTIALS) == ["gh", "keychain"]
    # what each real source would run, without running it; with the runner's own environment (no env= override)
    ran = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **k: ran.append((argv, k)) or type("R", (), {"stdout": TOKEN + "\n"})())
    assert cli._gh_token("prod") == TOKEN and cli._keychain_publish_token("prod") == TOKEN
    assert [a for a, _ in ran] == [["gh", "auth", "token"],
                                   ["security", "find-generic-password", "-s", "minimoi_delegation_publish", "-a", "prod", "-w"]]
    assert all("env" not in k for _, k in ran)
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert cli._gh_token("prod") is None


def test_a_build_with_no_publish_credential_still_completes_and_says_so(tmp_path, mirror, monkeypatch):
    path, ref = mirror
    fake_cli(monkeypatch, EDIT_README)
    g, hub = Git(), GitHub()
    pub = publish.Publisher("o/r", lambda: None, run=g, http=hub, base_env=workspace.child_env, workdir=tmp_path)
    out, d = run(tmp_path, path, env_doc(ref), publisher=pub)
    assert out["state"] == "completed" and d.results[0]["status"] == "completed"
    assert d.results[0]["detail"]["publish"] == {"state": "refused", "code": "no_publish_credential"}
    assert g.calls == [] and hub.calls == []
    assert artifact(tmp_path, d.results[0], "summary").decode().endswith("Pull request: not published: no_publish_credential")


def test_the_installer_turns_builds_on_and_never_reads_a_publish_credential():
    text = (Path(__file__).resolve().parents[2] / "scripts" / "mc_host" / "install_delegation_runner.sh").read_text()
    assert "<string>--scopes</string><string>research,build</string>" in text
    assert "<string>--max-cost-usd</string><string>5</string>" in text
    assert "<string>--publish-repo</string><string>$PUBLISH_REPO</string>" in text
    assert "PUBLISH_REPO=robertvanstedum/personal-ai-agents" in text
    assert "<string>--publish-credential</string><string>$PUBLISH_CREDENTIAL</string>" in text
    assert 'PUBLISH_CREDENTIAL="${MINIMOI_PUBLISH_CREDENTIAL:-gh}"' in text
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not any("gh auth" in line and "echo" not in line for line in code)       # the installer never asks gh
    touching = [line for line in code if "minimoi_delegation_publish" in line]
    assert touching and all("find-generic-password" in line and "-w" not in line.split() for line in touching)


def test_the_pull_request_title_is_the_approved_designs_title_not_the_briefs_header():
    """First live run, 27 Nov 2026: the title was the brief's fixed header line."""
    from types import SimpleNamespace
    from core.mc_delegation_runner import runner as R
    brief = ("Build task from Robert, started by his own yes in MiniMoi. Build the approved design below.\n\n"
             "--- Robert's message ---\nAdd the sentence.\n\n"
             "--- Approved design: Test sentence in supervision runbook (revision 1, sha256 " + "a" * 64 + ") ---\n# Design\n")
    env = SimpleNamespace(request_id="30ff5b4d-b96b-517f-85eb-5b0b3f9b250d", generation=1, brief=brief, ref="b" * 40)
    build = {"branch": "mc/30ff5b4d-g1", "commit": "c" * 40, "changed": True,
             "tests": {"command": None, "exit_code": None, "summary": "", "duration_s": 0.0, "timed_out": False,
                       "note": "no_tests_selected"}}
    title, body = R.pr_text(env, build)
    assert title == "Test sentence in supervision runbook (delegated build 30ff5b4d-g1)"
    assert "Draft: not reviewed, not merged." in body
    env.brief = "Plain first line\nmore"
    assert R.pr_text(env, build)[0] == "Plain first line (delegated build 30ff5b4d-g1)"
