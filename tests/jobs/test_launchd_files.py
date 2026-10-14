"""The two launchd plists and the install helper. The plists are only parsed; the helper runs against a stub
launchctl and a temporary agents folder, so nothing is ever loaded."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LAUNCHD = REPO / "infrastructure" / "launchd"
MAIN = "/Users/vanstedum/Projects/personal-ai-agents"
CASES = [
    ("com.vanstedum.minimoi-memory-watch", "scripts/memory/daily_watch.py", 4, "memory-watch.log"),
    ("com.vanstedum.minimoi-jobs-watchdog", "scripts/jobs/watchdog.py", 9, "jobs-watchdog.log"),
]


def load(label):
    return plistlib.loads((LAUNCHD / f"{label}.plist").read_bytes())


@pytest.mark.parametrize("label,script,hour,log", CASES)
def test_plist_shape(label, script, hour, log):
    plist = load(label)
    assert plist["Label"] == label
    assert plist["ProgramArguments"] == [f"{MAIN}/venv/bin/python3", f"{MAIN}/{script}"]
    assert (REPO / script).is_file() and os.access(REPO / script, os.X_OK)
    assert plist["StartCalendarInterval"] == {"Hour": hour, "Minute": 0}
    assert plist["WorkingDirectory"] == MAIN
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == f"/Users/vanstedum/minimoi-staging/logs/{log}"
    assert not plist.get("RunAtLoad") and not plist.get("KeepAlive")      # a daily job, not a daemon


@pytest.mark.parametrize("label,script,hour,log", CASES)
def test_plist_sets_pythonpath_and_the_jobs_root(label, script, hour, log):
    env = load(label)["EnvironmentVariables"]
    assert env["PYTHONPATH"] == MAIN                                       # a missing PYTHONPATH broke a CoS plist
    assert env["MINIMOI_JOBS_ROOT"] == "/Users/vanstedum/minimoi-staging/data/jobs"
    assert "/opt/homebrew/bin" in env["PATH"]


@pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")
@pytest.mark.parametrize("label", [c[0] for c in CASES])
def test_plutil_accepts_the_plists(label):
    assert subprocess.run(["plutil", "-lint", str(LAUNCHD / f"{label}.plist")], capture_output=True).returncode == 0


@pytest.fixture
def helper(tmp_path):
    stub = tmp_path / "launchctl"
    stub.write_text('#!/bin/sh\necho "$@" >> "$LAUNCHCTL_LOG"\n[ "$1" = "print" ] && exit 1\nexit 0\n')
    stub.chmod(0o755)
    agents, staging = tmp_path / "agents", tmp_path / "staging"
    env = {**os.environ, "LAUNCHCTL": str(stub), "LAUNCHCTL_LOG": str(tmp_path / "calls.log"),
           "MINIMOI_LAUNCH_AGENTS_DIR": str(agents), "MINIMOI_STAGING_ROOT": str(staging),
           "MINIMOI_REPO": "/tmp/trial-checkout"}

    def run(cmd, **extra):
        return subprocess.run(["/bin/sh", str(REPO / "scripts" / "jobs" / "launchd.sh"), cmd], env={**env, **extra},
                              capture_output=True, text=True)
    return run, agents, staging, tmp_path / "calls.log"


def test_install_copies_both_plists_points_them_at_the_chosen_checkout_and_bootstraps(helper):
    run, agents, staging, calls = helper
    done = run("install")
    assert done.returncode == 0, done.stderr
    for label, script, *_ in CASES:
        plist = plistlib.loads((agents / f"{label}.plist").read_bytes())
        assert plist["ProgramArguments"][1] == f"/tmp/trial-checkout/{script}"
        assert plist["EnvironmentVariables"]["PYTHONPATH"] == "/tmp/trial-checkout"
        assert plist["StandardOutPath"].startswith("/Users/vanstedum/minimoi-staging/logs/")
    assert (staging / "logs").is_dir() and oct((staging / "data" / "jobs").stat().st_mode & 0o777) == "0o700"
    log = calls.read_text()
    assert log.count("bootstrap") == 2 and "com.vanstedum.minimoi-memory-watch.plist" in log


def test_a_worktree_install_keeps_the_interpreter_in_the_main_checkouts_venv(helper):
    """A worktree has no venv: the plist must not point python (or moi's python) at one."""
    run, agents, *_ = helper
    run("install")
    for label, script, *_ in CASES:
        plist = plistlib.loads((agents / f"{label}.plist").read_bytes())
        assert plist["ProgramArguments"][0] == f"{MAIN}/venv/bin/python3"
    assert plistlib.loads((agents / "com.vanstedum.minimoi-memory-watch.plist").read_bytes())[
        "EnvironmentVariables"]["MOI_PYTHON"] == f"{MAIN}/venv/bin/python3"


def test_the_venv_can_be_chosen_explicitly(helper):
    run, agents, *_ = helper
    assert run("install", MINIMOI_VENV="/tmp/some-venv").returncode == 0
    plist = plistlib.loads((agents / "com.vanstedum.minimoi-memory-watch.plist").read_bytes())
    assert plist["ProgramArguments"][0] == "/tmp/some-venv/bin/python3"
    assert plist["EnvironmentVariables"]["MOI_PYTHON"] == "/tmp/some-venv/bin/python3"


def test_uninstall_unloads_and_removes_the_copies_but_keeps_status_and_logs(helper):
    run, agents, staging, calls = helper
    run("install")
    (staging / "data" / "jobs" / "memory-watch.json").write_text("{}")
    done = run("uninstall")
    assert done.returncode == 0 and list(agents.glob("*.plist")) == []
    assert (staging / "data" / "jobs" / "memory-watch.json").exists() and (staging / "logs").is_dir()
    assert calls.read_text().count("bootout") >= 2


def test_status_reports_not_loaded_and_missing_status_files(helper):
    run, *_ = helper
    done = run("status")
    assert done.returncode == 0
    assert "memory-watch: no status file yet" in done.stdout and "not loaded" in done.stdout


def test_unknown_command_is_a_usage_error(helper):
    run, *_ = helper
    assert run("frobnicate").returncode == 2
