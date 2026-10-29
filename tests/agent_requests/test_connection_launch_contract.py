"""The launch contract on MC's two front-door connections and the fixed-send probe (spec v0.5 R6.7-c to R6.7-e, R6.6-c's
root record seam): the same one policy file as the runtime, a pinned binary, the plan sign-in and no key setting
checked before every start, and a launch record per start. Proves the children's environments are exactly what P0 §5
observed on the Mac after PR #436. Fake values and fake CLIs only; no process of a real CLI is started, no Keychain
read, and the Mac's own configuration is never read (each test's HOME is its own folder)."""
from __future__ import annotations

import json
import os
import plistlib
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_fakes import pin_of, signed_in
from core.agent_requests.claude_stream import ClaudeStreamProvider
from core.agent_requests.codex_app_server import CodexAppServerProvider
from core.agent_requests.connection import ConnectionManager, run_forever
from core.mc_delegation_runner import launch, launch_env
from test_connection import SID, Clock, EnvSpy
from test_codex_provider import THREAD

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLI = os.path.join(REPO, "scripts", "agent_connection.py")
PLANTED = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "TELEGRAM_BOT_TOKEN",
           "SSH_AUTH_SOCK")
VALUE = "test-planted-value-6f1d2c"
# P0 §5 (28 Oct, after PR #436's reinstall), names observed in each live child:
P0_CLAUDE = {"DISABLE_AUTOUPDATER", "HOME", "LANG", "MINIMOI_AGENT_REQUESTS_DIR", "PATH", "SHELL", "TMPDIR", "USER"}
P0_CODEX = P0_CLAUDE - {"DISABLE_AUTOUPDATER"}


@pytest.fixture
def launchd(tmp_path, monkeypatch):
    """The manager's own environment as launchd gives it on the Mac (the plist's PATH, MINIMOI_AGENT_REQUESTS_DIR and,
    for Claude, DISABLE_AUTOUPDATER; launchd's own names; PEP 538's LC_CTYPE), plus the planted credentials."""
    for name in list(os.environ):
        monkeypatch.delenv(name)
    home = tmp_path / "home"
    signed_in(home)
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin", "HOME": str(home), "USER": "someone",
           "LOGNAME": "someone", "SHELL": "/bin/zsh", "TMPDIR": str(tmp_path / "t"), "XPC_FLAGS": "0x0",
           "XPC_SERVICE_NAME": "ai.minimoi.connection.x", "__CF_USER_TEXT_ENCODING": "0x1F5:0x0:0x0",
           "LC_CTYPE": "C.UTF-8", "MINIMOI_AGENT_REQUESTS_DIR": str(tmp_path / "the-global-default"),
           "DISABLE_AUTOUPDATER": "1", **{n: VALUE for n in PLANTED}}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return home


def _fake(tmp_path, name):
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


def _claude(tmp_path, root, seen, **kw):
    fake = _fake(tmp_path, "claude")
    kw.setdefault("binary_sha256", pin_of(fake))
    return ClaudeStreamProvider(claude_bin=fake, session_id=SID, cwd=str(tmp_path), allowed_tools=["Read"], ledger_root=root,
                                popen=lambda *a, **k: EnvSpy(seen, *a, **k), **kw)


def _codex(tmp_path, root, seen, **kw):
    fake = _fake(tmp_path, "codex")
    kw.setdefault("binary_sha256", pin_of(fake))
    (tmp_path / "work").mkdir(exist_ok=True)
    return CodexAppServerProvider(codex_bin=fake, cwd=str(tmp_path / "work"), ledger_root=root, thread_id=THREAD,
                                  request_timeout=0.2, popen=lambda *a, **k: EnvSpy(seen, *a, **k), **kw)


def _launches(root, agent):
    path = os.path.join(root, "connection", agent, "launch.jsonl")
    return [json.loads(x) for x in open(path).read().splitlines()] if os.path.exists(path) else []


def test_the_claude_connection_gives_exactly_p0s_names_and_records_its_launch(tmp_path, launchd):
    root, seen = str(tmp_path / "ledger"), {}
    p = _claude(tmp_path, root, seen)
    m = ConnectionManager(root, "claude-code", p, session_id=SID)
    p.start()
    assert set(seen["env"]) == P0_CLAUDE and VALUE not in seen["env"].values()      # unchanged by the move onto the file
    (rec,) = _launches(root, "claude-code")
    assert rec["start_path"] == "connection" and rec["route"] == "claude-code.subscription" and rec["tool"] == "claude-code"
    assert rec["profile"] == "unattended" and rec["route_kind"] == "subscription" and rec["key"] is None
    assert rec["binary"] == {"path": p.claude_bin, "sha256": "sha256:" + pin_of(p.claude_bin), "pinned_by": "argument"}
    assert rec["policy"] == {"version": launch_env.POLICY_VERSION, "sha256": launch_env.POLICY_DIGEST}
    assert set(rec["env_names"]) == P0_CLAUDE and set(PLANTED) <= set(rec["removed"]) and rec["session_ref"] == SID
    assert rec["checks"] == {"tool": "claude-code", "signin": "present", "user_config": "checked", "project_config": "checked"}
    raw = open(os.path.join(root, "connection", "claude-code", "launch.jsonl")).read()
    assert VALUE not in raw and str(launchd) not in json.dumps(rec["removed"])      # names only
    p.close()
    m.close()


def test_the_codex_connection_gives_exactly_p0s_names_and_records_its_launch_even_before_its_manager(tmp_path, launchd):
    root, seen = str(tmp_path / "ledger"), {}
    p = _codex(tmp_path, root, seen)
    with pytest.raises(TimeoutError):                         # the stand-in never answers initialize
        p.start()                                             # Codex's first --create starts before its manager exists
    assert set(seen["env"]) == P0_CODEX and VALUE not in seen["env"].values()
    assert _launches(root, "codex") == []                     # kept until a manager is attached ...
    m = ConnectionManager(root, "codex", p, session_id=THREAD)
    (rec,) = _launches(root, "codex")                         # ... then written
    assert rec["route"] == "codex.subscription" and set(rec["env_names"]) == P0_CODEX and rec["key"] is None
    assert rec["checks"]["signin"] == "present" and rec["checks"]["user_config"] == "checked"
    m.close()


@pytest.mark.parametrize("make", [_claude, _codex])
@pytest.mark.parametrize("pinned, reason", [(None, "no_pin"), ("0" * 64, "digest_mismatch")])
def test_an_unpinned_or_changed_binary_is_refused_before_any_spawn(tmp_path, launchd, make, pinned, reason):
    root, seen = str(tmp_path / "ledger"), {}
    p = make(tmp_path, root, seen, binary_sha256=pinned)
    with pytest.raises(launch.LaunchRefused) as caught:
        p.start()
    assert caught.value.code == "binary_unpinned" and caught.value.detail["reason"] == reason and seen == {}


def test_a_missing_sign_in_or_a_key_setting_refuses_the_start_by_name_never_falling_back_to_a_key(tmp_path, launchd):
    root, seen = str(tmp_path / "ledger"), {}
    (launchd / ".claude.json").unlink()
    with pytest.raises(launch.LaunchRefused) as caught:
        _claude(tmp_path, root, seen).start()
    assert str(caught.value) == "signin_missing: login=claude /login, tool=claude-code" and seen == {}
    signed_in(launchd, "claude-code")
    (launchd / ".claude").mkdir()
    (launchd / ".claude" / "settings.json").write_text(json.dumps({"apiKeyHelper": "/bin/echo " + VALUE}))
    with pytest.raises(launch.LaunchRefused) as caught:
        _claude(tmp_path, root, seen).start()
    assert caught.value.code == "key_config_present" and VALUE not in str(caught.value) and seen == {}
    (launchd / ".codex" / "auth.json").write_text(json.dumps({"auth_mode": "apikey"}))
    with pytest.raises(launch.LaunchRefused, match="signin_missing: login=codex login"):
        _codex(tmp_path, root, seen).start()
    assert seen == {}


def test_a_refused_start_is_retried_with_the_backoff_and_status_names_the_check_never_a_value(tmp_path, launchd):
    from test_supervisor import OnceStop
    root, seen = str(tmp_path / "ledger"), {}
    p = _claude(tmp_path, root, seen, binary_sha256="0" * 64)
    clock = Clock()
    m = ConnectionManager(root, "claude-code", p, session_id=SID, clock=clock)
    run_forever(m, OnceStop(40, clock), interval=1.0, restart=p.start)
    status = json.load(open(os.path.join(root, "connection", "claude-code", "status.json")))
    assert status["restart_attempts"] >= 2 and "binary_unpinned" in status["last_restart_error"]
    assert seen == {} and _launches(root, "claude-code") == [] and VALUE not in json.dumps(status)
    m.close()


def test_a_launch_record_that_cannot_be_written_refuses_the_start(tmp_path, launchd):
    root, seen = str(tmp_path / "ledger"), {}
    p = _claude(tmp_path, root, seen)

    def full(_rec):
        raise OSError("no space left")
    p.attach_launch_sink(full)
    with pytest.raises(OSError):
        p.start()
    assert seen == {}


def _cli_env(tmp_path):
    """The operator CLI's own environment: PATH, and a HOME of the test's own with the plan sign-ins (never the Mac's)."""
    home = tmp_path / "home"
    signed_in(home)
    return {"PATH": os.environ["PATH"], "HOME": str(home)}


def test_print_launchd_runs_the_whole_pre_start_check_and_refuses_a_plist_whose_every_start_would_be_refused(tmp_path):
    root = str(tmp_path / "ledger")
    fake = _fake(tmp_path, "claude")
    env = _cli_env(tmp_path)
    base = [sys.executable, CLI, "--root", root, "--agent", "claude-code", "--provider", "claude-stream", "--session-id", SID,
            "--claude-bin", fake, "--cwd", str(tmp_path), "--allowed-tools", "Read"]
    for extra in ([], ["--claude-bin-sha256", "0" * 64]):
        out = subprocess.run(base + extra + ["print-launchd"], capture_output=True, text=True, timeout=30, env=env)
        assert out.returncode == 2 and "binary_unpinned" in out.stderr and "--claude-bin-sha256" in out.stderr
        assert out.stdout == ""                                                        # no plist is printed
    pinned = base + ["--claude-bin-sha256", pin_of(fake), "print-launchd"]
    out = subprocess.run(pinned, capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 0, out.stderr
    args = plistlib.loads(out.stdout.encode())["ProgramArguments"]
    assert args[args.index("--claude-bin-sha256") + 1] == pin_of(fake) and args[-2:] == ["run", "--start"]
    # Not only the pin: a key setting, or a missing sign-in, refuses the plist too, by name.
    home = tmp_path / "home"
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"apiKeyHelper": "/bin/echo " + VALUE}))
    out = subprocess.run(pinned, capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 2 and out.stdout == "" and "key_config_present" in out.stderr and "apiKeyHelper" in out.stderr
    assert VALUE not in out.stderr
    (home / ".claude" / "settings.json").unlink()
    (home / ".claude.json").unlink()
    out = subprocess.run(pinned, capture_output=True, text=True, timeout=30, env=env)
    assert out.returncode == 2 and out.stdout == "" and "signin_missing" in out.stderr and "claude /login" in out.stderr


@pytest.mark.parametrize("case", ["no_pin", "wrong_pin", "signed_out"])
def test_a_refused_first_start_through_main_is_named_in_status_and_retried_on_the_backoff_never_a_crash_loop(tmp_path, case):
    """SF1: the operator CLI as launchd runs it (``run --start``). Its first start is refused; the manager keeps running,
    status.json names the refusal (never a value), the retries follow the restart backoff, and nothing is spawned."""
    root = str(tmp_path / "ledger")
    marker = tmp_path / "spawned"
    fake = _fake(tmp_path, "claude")
    Path(fake).write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    env = {**_cli_env(tmp_path), **{n: VALUE for n in PLANTED}}
    pin = {"no_pin": [], "wrong_pin": ["--claude-bin-sha256", "0" * 64],
           "signed_out": ["--claude-bin-sha256", pin_of(fake)]}[case]
    if case == "signed_out":
        (tmp_path / "home" / ".claude.json").unlink()
    args = [sys.executable, CLI, "--root", root, "--agent", "claude-code", "--provider", "claude-stream", "--session-id", SID,
            "--claude-bin", fake, *pin, "--cwd", str(tmp_path), "--allowed-tools", "Read", "--interval", "0.2", "run", "--start"]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    status_path = Path(root) / "connection" / "claude-code" / "status.json"
    try:
        deadline = time.time() + 15
        while time.time() < deadline and not (status_path.exists() and "LaunchRefused" in status_path.read_text()):
            time.sleep(0.1)
        time.sleep(3.5)                                                                 # long enough for a tight loop to show
        assert proc.poll() is None                                                      # still running: no exit, no launchd loop
        status = json.loads(status_path.read_text())
    finally:
        proc.send_signal(signal.SIGTERM)
        out, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err
    want = {"no_pin": "binary_unpinned: reason=no_pin", "wrong_pin": "binary_unpinned: reason=digest_mismatch",
            "signed_out": "signin_missing: login=claude /login, tool=claude-code"}[case]
    assert status["last_restart_error"] == f"LaunchRefused: {want}" and status["state"] == "offline"
    assert 2 <= status["restart_attempts"] <= 4                                         # 1, then the backoff's 1 s, 2 s, 5 s ...
    assert status["next_restart_in_s"] >= 1
    assert not marker.exists()                                                          # nothing was spawned, ever
    assert VALUE not in status_path.read_text() and VALUE not in err and VALUE not in out


def test_the_fixed_send_probe_builds_its_environment_from_the_policy_and_publishes_a_launch_record(tmp_path, monkeypatch):
    from test_claude_fixed_send import fixture
    from core.agent_requests import claude_fixed_send as C
    for name in PLANTED:
        monkeypatch.setenv(name, VALUE)
    box, rid, calls, children, _ = fixture(tmp_path, monkeypatch)
    box.dispatch(rid)
    env = calls[0][1]["env"]
    assert set(env) == {"HOME", "PATH", "LANG"} and env["HOME"] == str(tmp_path)        # unchanged: a fixed HOME, PATH, LANG
    rec = json.loads((box.root / "requests" / rid / "launch.json").read_text())
    assert rec["start_path"] == "probe" and rec["route"] == "claude-code.subscription" and rec["tool"] == "claude-code"
    assert rec["binary"]["pinned_by"] == "code" and rec["binary"]["sha256"] == "sha256:" + C.G.BINARY_SHA256
    assert set(rec["env_names"]) == {"HOME", "PATH", "LANG"} and set(PLANTED) <= set(rec["removed"])
    assert rec["policy"]["sha256"] == launch_env.POLICY_DIGEST
    assert VALUE not in (box.root / "requests" / rid / "launch.json").read_text()


def test_the_fixed_send_probes_preflight_checks_the_sign_in_and_key_settings_where_it_runs(tmp_path):
    from core.agent_requests import claude_fixed_send as C
    from core.agent_requests.direct_contact import Refused
    target = type("T", (), {"cwd": str(tmp_path / "project")})()
    enrolled = {"home": str(tmp_path / "home")}
    with pytest.raises(Refused, match="signin_missing"):
        C.contract_checks(enrolled, target)
    signed_in(tmp_path / "home", "claude-code")
    assert C.contract_checks(enrolled, target)["signin"] == "present"
    (tmp_path / "project" / ".claude").mkdir(parents=True)
    (tmp_path / "project" / ".claude" / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "x"}}))
    with pytest.raises(Refused, match="key_config_present"):
        C.contract_checks(enrolled, target)
    # Its pinned binary (2.1.304) is gone from the Mac (P0 F6): preflight still refuses first, failing closed.
    assert C.G.BINARY.endswith("/2.1.304/6bbba6adc37f/claude.app/Contents/MacOS/claude")
