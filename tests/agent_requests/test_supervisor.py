"""The supervisor side of the connection: a manager that restarts a dead provider (resuming the same saved session, bounded backoff), the operator CLI end to end
against test doubles, and the launchd plist. No real Claude or Codex, no network, no model."""
from __future__ import annotations

import json
import os
import plistlib
import signal
import stat
import subprocess
import sys
import threading
import time

import pytest

from agent_fakes import fake_controls, given_environments, pin_of, signed_in
from core import agent_requests as ar
from core.agent_requests.connection import (ConnectionManager, Delivery, Probe, Provider, RESTART_BACKOFF_S, run_forever)
from test_codex_provider import FAKE_SERVER, THREAD, wait_for
from test_connection import FAKE_CLAUDE, SID, Clock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLI = os.path.join(REPO, "scripts", "agent_connection.py")
CONV = "c-132bc60f078e"


class Dead(Provider):
    name = "dead"

    def __init__(self):
        self.up, self.starts, self.fail_starts = False, 0, 0

    def probe(self):
        return Probe("idle" if self.up else "offline", SID)

    def deliver(self, notice, correlation):
        return Delivery("accepted")

    def start(self):
        self.starts += 1
        if self.fail_starts > 0:
            self.fail_starts -= 1
            raise OSError("spawn failed")
        self.up = True


class OnceStop:
    def __init__(self, passes, clock=None, tick=1.0):
        self.n, self.passes, self.clock, self.tick, self.waits = 0, passes, clock, tick, []

    def is_set(self):
        return self.n >= self.passes

    def wait(self, delay):
        self.n += 1
        self.waits.append(delay)
        if self.clock:
            self.clock.advance(self.tick)


def status(root, agent="claude-code"):
    return json.load(open(os.path.join(root, "connection", agent, "status.json")))


@pytest.fixture
def root(tmp_path):
    return str(tmp_path / "ledger")


def test_a_dead_provider_is_restarted_with_a_bounded_backoff_and_the_failures_are_visible(root):
    clock, p = Clock(), Dead()
    p.fail_starts = 3
    m = ConnectionManager(root, "claude-code", p, session_id=SID, clock=clock)
    stop = OnceStop(120, clock)
    run_forever(m, stop, interval=1.0, restart=p.start)
    assert p.up and p.starts == 4                                                                          # three failures, then it came back
    attempt_gaps = [1, 2, 5]                                                                               # the documented backoff: 1, 2, 5 ... seconds between tries
    assert RESTART_BACKOFF_S[:3] == (1, 2, 5) and RESTART_BACKOFF_S[-1] == 300
    assert status(root)["restart_attempts"] == 0 and status(root)["last_restart_error"] == ""              # recovered: the counter is reset and says so
    m.close()


def test_a_provider_that_never_comes_back_is_retried_at_most_every_five_minutes_and_says_why(root):
    clock, p = Clock(), Dead()
    p.fail_starts = 10 ** 6
    m = ConnectionManager(root, "claude-code", p, session_id=SID, clock=clock)
    run_forever(m, OnceStop(3600, clock), interval=1.0, restart=p.start)
    assert 12 <= p.starts <= 20                                                                            # an hour: bounded, not a tight loop
    st = status(root)
    assert st["restart_attempts"] == p.starts and "spawn failed" in st["last_restart_error"] and st["state"] == "offline"
    m.close()


def test_no_restart_is_attempted_while_the_provider_is_up_or_when_no_restart_is_configured(root):
    clock, p = Clock(), Dead()
    p.up = True
    m = ConnectionManager(root, "claude-code", p, session_id=SID, clock=clock)
    run_forever(m, OnceStop(20, clock), interval=1.0, restart=p.start)
    assert p.starts == 0
    p.up = False
    run_forever(m, OnceStop(20, clock), interval=1.0)                                                      # observer only: it NEVER starts a session
    assert p.starts == 0 and status(root)["state"] == "offline"
    m.close()


# ── the operator CLI end to end ──────────────────────────────────────────────

def _wrapper(tmp_path, name, script_text):
    script = tmp_path / f"{name}.py"
    script.write_text(script_text)
    w = tmp_path / name
    w.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n")
    w.chmod(w.stat().st_mode | stat.S_IXUSR)
    return str(w)


def _spawn(args, env):
    return subprocess.Popen([sys.executable, CLI, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def _env(tmp_path, **extra):
    """The operator CLI's environment. The fakes' FAKE_* controls go to their controls file: the connection never hands its own environment to the child."""
    controls = {"FAKE_SID": SID, "FAKE_THREAD": THREAD, "FAKE_DELAY": "0.1"}
    controls.update({k: v for k, v in extra.items() if k.startswith("FAKE_")})
    fake_controls(tmp_path, **controls)
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path / "home")}
    env.update({k: v for k, v in extra.items() if not k.startswith("FAKE_")})
    signed_in(tmp_path / "home")                                           # the plan sign-ins the launch contract checks before every start
    return env


def _ask(root, seed, to, action="say hello"):
    return ar.create_request(root, request_id=ar.request_id_for(seed), frm="mc", to=to, action=action, conversation_id=CONV)[0]["request_id"]


def test_the_claude_cli_runs_a_persistent_session_restarts_it_when_it_dies_and_resumes_the_same_session(tmp_path):
    root = str(tmp_path / "ledger")
    fake = _wrapper(tmp_path, "claude", FAKE_CLAUDE)
    work = tmp_path / "work"
    work.mkdir()
    args = ["--root", root, "--agent", "claude-code", "--provider", "claude-stream", "--session-id", SID, "--claude-bin", fake, "--claude-bin-sha256", pin_of(fake),
            "--cwd", str(work), "--allowed-tools", "Read,Grep", "--interval", "0.2"]
    a = _ask(root, "a", "claude-code")
    proc = _spawn(args + ["run", "--start"], _env(tmp_path))
    try:
        assert wait_for(lambda: ar.load(root, a)["stage"] == "returned", 25)
        assert ar.armed_age(root, "claude-code") is not None and ar.connection_owner(root, "claude-code")["session_id"] == SID
        assert json.load(open(os.path.join(root, "connection", "claude-code", "identity.json")))["created"] is True
        child = [p for p in subprocess.run(["pgrep", "-f", os.path.join(str(tmp_path), "claude.py")], capture_output=True, text=True).stdout.split()]
        for pid in child:
            os.kill(int(pid), signal.SIGKILL)                                                              # the session's process dies
        assert wait_for(lambda: status(root)["state"] in ("offline", "idle"), 10)
        b = _ask(root, "b", "claude-code", "say hello again")
        assert wait_for(lambda: ar.load(root, b)["stage"] == "returned", 30)                               # the manager restarted it and it answered
        assert len([e for e in ar.load(root, b)["events"] if e["kind"] == "result"]) == 1
    finally:
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err
    assert ar.connection_owner(root, "claude-code") is None                                                # a clean stop released the lease


def test_the_codex_cli_creates_once_resumes_after_a_restart_and_refuses_a_second_create(tmp_path):
    root = str(tmp_path / "ledger")
    log = tmp_path / "wire.jsonl"
    fake = _wrapper(tmp_path, "codex", FAKE_SERVER)
    work = tmp_path / "work"
    work.mkdir()
    base = ["--root", root, "--agent", "codex", "--provider", "codex-app-server", "--codex-bin", fake, "--codex-bin-sha256", pin_of(fake), "--cwd", str(work),
            "--interval", "0.2"]
    env = _env(tmp_path, FAKE_LOG=str(log))
    assert subprocess.run([sys.executable, CLI, *base], capture_output=True, text=True, env=env).returncode == 2        # no thread and no --create: refused
    a = _ask(root, "a", "codex")
    proc = _spawn(base + ["--create", "run", "--start"], env)
    try:
        assert wait_for(lambda: ar.load(root, a)["stage"] == "returned", 25)
        assert json.load(open(os.path.join(root, "connection", "codex", "identity.json")))["session_id"] == THREAD
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=20)
    methods = [json.loads(x).get("method") for x in log.read_text().splitlines()]
    assert methods.count("thread/start") == 1
    again = subprocess.run([sys.executable, CLI, *base, "--create"], capture_output=True, text=True, env=env)       # a second first-session is refused
    assert again.returncode == 2 and "already recorded" in again.stderr
    b = _ask(root, "b", "codex", "again")
    proc2 = _spawn(base + ["run", "--start"], env)                                                          # a later run RESUMES the recorded thread
    try:
        assert wait_for(lambda: ar.load(root, b)["stage"] == "returned", 25)
    finally:
        proc2.send_signal(signal.SIGTERM)
        proc2.communicate(timeout=20)
    methods = [json.loads(x).get("method") for x in log.read_text().splitlines()]
    assert methods.count("thread/start") == 1 and methods.count("thread/resume") >= 1


PARENT_SECRETS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "TELEGRAM_BOT_TOKEN")


@pytest.mark.parametrize("agent", ["claude-code", "codex"])
def test_keys_and_tokens_in_the_connections_own_environment_never_reach_the_agent_it_starts(tmp_path, agent):
    """End to end through the operator CLI, as launchd runs it: the six credentials sit in the manager's environment (fake values), the session still
    answers, and the agent process it started was given none of them, nor the one unlisted sentinel, and was given the connection's own ledger and LANG.
    (The full-set check is in the unit tests: the fake's sh wrapper adds PWD, SHLVL and _ here.)"""
    root = str(tmp_path / "ledger")
    work = tmp_path / "work"
    work.mkdir()
    given = tmp_path / "given-env.jsonl"
    if agent == "claude-code":
        fake = _wrapper(tmp_path, "claude", FAKE_CLAUDE)
        args = ["--root", root, "--agent", "claude-code", "--provider", "claude-stream", "--session-id", SID, "--claude-bin", fake, "--claude-bin-sha256", pin_of(fake),
                "--cwd", str(work), "--allowed-tools", "Read", "--interval", "0.2", "run", "--start"]
    else:
        fake = _wrapper(tmp_path, "codex", FAKE_SERVER)
        args = ["--root", root, "--agent", "codex", "--provider", "codex-app-server", "--codex-bin", fake, "--codex-bin-sha256", pin_of(fake), "--cwd", str(work),
                "--interval", "0.2", "--create", "run", "--start"]
    env = _env(tmp_path, FAKE_ENV_OUT=str(given), MINIMOI_AGENT_REQUESTS_DIR=str(tmp_path / "the-global-default"), NOT_ON_THE_LIST="harmless",
               **{name: "sk-test-not-real" for name in PARENT_SECRETS})
    a = _ask(root, "a", agent)
    proc = _spawn(args, env)
    try:
        assert wait_for(lambda: ar.load(root, a)["stage"] == "returned", 25)                               # the agent still works with the smaller environment
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=20)
    starts = given_environments(given)
    assert starts                                                                                          # the fake recorded what it was given
    for start in starts:
        assert not set(PARENT_SECRETS) & set(start["names"]) and "NOT_ON_THE_LIST" not in start["names"]
        assert start["ledger"] == root                                                                     # the connection's own ledger, never the global default
        assert ("DISABLE_AUTOUPDATER" in start["names"]) == (agent == "claude-code")
        assert "LANG" in start["names"]


def test_the_launchd_plist_is_valid_keeps_the_manager_alive_with_throttling_and_names_every_argument(tmp_path):
    root = str(tmp_path / "ledger")
    fake = _wrapper(tmp_path, "claude", FAKE_CLAUDE)
    real_path = os.path.expanduser("~/Library/LaunchAgents/ai.minimoi.connection.claude-code.plist")
    before = os.path.exists(real_path)
    before_mtime = os.stat(real_path).st_mtime_ns if before else 0
    out = subprocess.run([sys.executable, CLI, "--root", root, "--agent", "claude-code", "--provider", "claude-stream", "--session-id", SID, "--claude-bin", fake,
                          "--claude-bin-sha256", pin_of(fake), "--cwd", str(tmp_path), "--allowed-tools", "Read", "print-launchd"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    plist = plistlib.loads(out.stdout.encode())
    assert plist["Label"] == "ai.minimoi.connection.claude-code" and plist["KeepAlive"] is True and plist["RunAtLoad"] is True and plist["ThrottleInterval"] == 10
    args = plist["ProgramArguments"]
    assert args[0] == sys.executable and args[1] == CLI and args[-2:] == ["run", "--start"] and "--print-argv" not in args and "print-launchd" not in args
    assert plist["EnvironmentVariables"]["MINIMOI_AGENT_REQUESTS_DIR"] == root
    assert not any(k in plist for k in ("UserName", "GroupName")) and plist["StandardOutPath"].startswith(root)
    real = os.path.expanduser("~/Library/LaunchAgents/ai.minimoi.connection.claude-code.plist")
    assert os.path.exists(real) == before and (not before or os.stat(real).st_mtime_ns == before_mtime)      # printing never installs or touches the real LaunchAgents file


class Flapping(Provider):
    """Spawns fine and then dies on the very next pass: a crash loop that a naive supervisor would retry every second."""
    name = "flapping"

    def __init__(self):
        self.up, self.starts = False, 0

    def probe(self):
        was = self.up
        self.up = False if was else self.up
        return Probe("idle" if was else "offline", SID)

    def deliver(self, notice, correlation):
        return Delivery("accepted")

    def start(self):
        self.starts += 1
        self.up = True


def test_a_crash_loop_backs_off_because_the_failure_count_is_cleared_only_after_stable_uptime(root):
    clock, p = Clock(), Flapping()
    m = ConnectionManager(root, "claude-code", p, session_id=SID, clock=clock)
    run_forever(m, OnceStop(3600, clock), interval=1.0, restart=p.start)
    assert p.starts <= 20                                                                                  # an hour of instant deaths: bounded by the backoff, not one per second
    st = status(root)
    assert st["restart_attempts"] >= 8 and "exited right after it was started" in st["last_restart_error"]
    m.close()


def test_a_provider_that_stays_up_for_the_stable_window_resets_the_count(root):
    clock, p = Clock(), Dead()
    p.fail_starts = 2
    m = ConnectionManager(root, "claude-code", p, session_id=SID, clock=clock)
    seen = []
    run_forever(m, OnceStop(80, clock), interval=1.0, restart=p.start, on_pass=lambda out: seen.append(status(root).get("restart_attempts")))
    assert p.up and max(x for x in seen if x is not None) == 2                                              # two failed starts were counted
    assert any(x == 2 for x in seen[:15]) and seen[-1] == 0                                                 # still counted while it was only just back; cleared after 30 s of stable uptime
    assert status(root)["last_restart_error"] == ""
    m.close()


# ── a blocked manager never advertises a listener ────────────────────────────

def test_a_damaged_history_removes_the_listening_marker_and_it_stays_off_until_repaired(root):
    p = Dead()
    p.up = True
    m = ConnectionManager(root, "claude-code", p, session_id=SID)
    assert m.step()["error"] == "" and ar.armed_age(root, "claude-code") is not None                        # healthy: listening
    attempts = os.path.join(root, "connection", "claude-code", "attempts.jsonl")
    open(attempts, "w").write("this is damaged\nand so is this\n")
    for _ in range(3):
        out = m.step()
        assert out["error"].startswith("blocked:") and ar.armed_age(root, "claude-code") is None           # repeated blocked passes never re-arm
    assert status(root)["error"].startswith("blocked:")
    os.remove(attempts)
    assert m.step()["error"] == "" and ar.armed_age(root, "claude-code") is not None                        # repaired: listening again
    m.close()


def test_an_offline_or_unknown_or_mismatched_provider_clears_the_marker(root):
    p = Dead()
    p.up = True
    m = ConnectionManager(root, "claude-code", p, session_id=SID)
    m.step()
    assert ar.armed_age(root, "claude-code") is not None
    p.up = False
    m.step()
    assert ar.armed_age(root, "claude-code") is None
    m.close()


def test_the_codex_plist_is_resume_only_it_never_keeps_create_and_needs_a_recorded_thread(tmp_path):
    root = str(tmp_path / "ledger")
    fake = _wrapper(tmp_path, "codex", FAKE_SERVER)
    base = ["--root", root, "--agent", "codex", "--provider", "codex-app-server", "--codex-bin", fake, "--codex-bin-sha256", pin_of(fake), "--cwd", str(tmp_path)]
    no = subprocess.run([sys.executable, CLI, *base, "--thread-id", THREAD, "print-launchd"], capture_output=True, text=True, timeout=30)
    assert no.returncode == 2 and "create it once" in no.stderr                                            # no thread recorded in the ledger yet: no resume-only plist
    seed = ConnectionManager(root, "codex", Dead(), session_id=THREAD)
    seed.mark_created()
    seed.close()
    out = subprocess.run([sys.executable, CLI, *base, "print-launchd"], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    args = plistlib.loads(out.stdout.encode())["ProgramArguments"]
    assert "--create" not in args and args[-2:] == ["run", "--start"]


def test_create_without_run_start_creates_nothing_and_is_refused(tmp_path):
    root = str(tmp_path / "ledger")
    log = tmp_path / "wire.jsonl"
    fake = _wrapper(tmp_path, "codex", FAKE_SERVER)
    base = ["--root", root, "--agent", "codex", "--provider", "codex-app-server", "--codex-bin", fake, "--cwd", str(tmp_path)]
    env = _env(tmp_path, FAKE_LOG=str(log))
    for tail in ([], ["run"], ["--print-argv"], ["print-launchd"]):
        out = subprocess.run([sys.executable, CLI, *base, "--create", *tail], capture_output=True, text=True, env=env, timeout=30)
        assert out.returncode == 2 and "--create only applies to `run --start`" in out.stderr or "refused" in out.stderr, (tail, out.stderr)
    assert not log.exists() and not os.path.exists(os.path.join(root, "connection"))                       # no process was started, no thread created, no state written


def test_a_claude_session_gets_one_exact_bash_selector_for_this_ledger_and_nothing_broader(tmp_path):
    from core.agent_requests.claude_stream import ClaudeStreamProvider, helper_selector
    root = str(tmp_path / "ledger")
    fake = _wrapper(tmp_path, "claude", FAKE_CLAUDE)
    exact = helper_selector(sys.executable, os.path.join(REPO, "scripts", "agent_requests.py"), root)
    assert len(exact) > 120 and exact.startswith("Bash(") and exact.endswith(f"--root {root}:*)")          # longer than the old limit: now possible
    ok = ClaudeStreamProvider(claude_bin=fake, session_id=SID, cwd=str(tmp_path), allowed_tools=["Read", exact], ledger_root=root, helper_bash=exact)
    assert ok.argv()[ok.argv().index("--allowedTools") + 1] == f"Read,{exact}"
    base = dict(claude_bin=fake, session_id=SID, cwd=str(tmp_path), ledger_root=root, helper_bash=exact)
    for bad in (["Bash"], ["Bash(*)"], ["Bash(python3:*)"], [f"Bash({sys.executable}:*)"], [exact.replace(root, "/somewhere/else")], ["Read", "Bash(rm -rf /:*)"]):
        with pytest.raises(ValueError):
            ClaudeStreamProvider(**{**base, "allowed_tools": bad})
    out = subprocess.run([sys.executable, CLI, "--root", root, "--agent", "claude-code", "--provider", "claude-stream", "--session-id", SID, "--claude-bin", fake,
                          "--cwd", str(tmp_path), "--allowed-tools", "Read,Grep", "--print-argv"], capture_output=True, text=True, timeout=30)
    argv = json.loads(out.stdout)
    tools = argv[argv.index("--allowedTools") + 1].split(",")
    assert tools[:2] == ["Read", "Grep"] and tools[2] == exact and len(tools) == 3                          # the CLI adds exactly that one selector
    bad_cli = subprocess.run([sys.executable, CLI, "--root", root, "--agent", "claude-code", "--provider", "claude-stream", "--session-id", SID, "--claude-bin", fake,
                              "--cwd", str(tmp_path), "--allowed-tools", "Read,Bash(python3:*)", "--print-argv"], capture_output=True, text=True, timeout=30)
    assert bad_cli.returncode == 2 and "Bash permission" in bad_cli.stderr


def test_the_plist_builder_itself_strips_create_even_if_it_were_ever_given_one(tmp_path, monkeypatch):
    import argparse
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_connection_cli", CLI)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    ns = argparse.Namespace(agent="codex", provider="codex-app-server", root=str(tmp_path / "ledger"), cwd=str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["agent_connection.py", "--root", ns.root, "--agent", "codex", "--create", "--print-argv", "print-launchd"])
    args = plistlib.loads(cli.launchd_plist(ns, CLI, {"session_id": THREAD, "created": True}).encode())["ProgramArguments"]
    assert "--create" not in args and "--print-argv" not in args and "print-launchd" not in args and args[-2:] == ["run", "--start"]


def test_a_claude_session_is_marked_created_only_after_its_first_accepted_turn_so_an_early_restart_creates_it_again(tmp_path):
    root = str(tmp_path / "ledger")
    fake = _wrapper(tmp_path, "claude", FAKE_CLAUDE)
    work = tmp_path / "work"
    work.mkdir()
    args = ["--root", root, "--agent", "claude-code", "--provider", "claude-stream", "--session-id", SID, "--claude-bin", fake, "--claude-bin-sha256", pin_of(fake),
            "--cwd", str(work), "--allowed-tools", "Read", "--interval", "0.2"]
    proc = _spawn(args + ["run", "--start"], _env(tmp_path))
    try:
        assert wait_for(lambda: os.path.exists(os.path.join(root, "connection", "claude-code", "identity.json")), 15)
        assert wait_for(lambda: subprocess.run(["pgrep", "-f", os.path.join(str(tmp_path), "claude.py")], capture_output=True, text=True).stdout.split(), 10)
        ident = json.load(open(os.path.join(root, "connection", "claude-code", "identity.json")))
        assert "created" not in ident                                                                      # started, but no turn yet: NOT created
        cmd = subprocess.run(["ps", "-ww", "-o", "command=", "-p", subprocess.run(["pgrep", "-f", os.path.join(str(tmp_path), "claude.py")], capture_output=True, text=True).stdout.split()[0]],
                             capture_output=True, text=True).stdout
        assert "--session-id" in cmd and "--resume" not in cmd                                             # the first start creates
        for pid in subprocess.run(["pgrep", "-f", os.path.join(str(tmp_path), "claude.py")], capture_output=True, text=True).stdout.split():
            os.kill(int(pid), signal.SIGKILL)                                                              # it dies before any turn
        assert wait_for(lambda: subprocess.run(["pgrep", "-f", os.path.join(str(tmp_path), "claude.py")], capture_output=True, text=True).stdout.split(), 15)
        cmd2 = subprocess.run(["ps", "-ww", "-o", "command=", "-p", subprocess.run(["pgrep", "-f", os.path.join(str(tmp_path), "claude.py")], capture_output=True, text=True).stdout.split()[0]],
                              capture_output=True, text=True).stdout
        assert "--session-id" in cmd2 and "--resume" not in cmd2                                           # still no turn: it creates again, never a doomed resume
        a = _ask(root, "a", "claude-code")
        assert wait_for(lambda: ar.load(root, a)["stage"] == "returned", 25)
        assert wait_for(lambda: json.load(open(os.path.join(root, "connection", "claude-code", "identity.json"))).get("created") is True, 10)   # NOW it exists
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=20)
