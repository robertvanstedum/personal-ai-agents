"""start-with-mc.sh: the staging-only combined start (MC spec v0.7 §1.4).

Runs the real script against a fake OpenClaw (tests/cos/fakes) and the real
selfcheck.mjs with a fake gateway CLI, so the decisions are tested without
Docker:

* the readiness gate (Codex v0.7 finding 1): every config is first started
  with ``--bind loopback`` and checked; only a checked config is started on
  the LAN bind, and the ``serving`` marker is written only then;
* N11 split by agent: a CoS failure never starts OpenClaw (sticky marker, no
  loop); an MC-only failure starts CoS alone, after its own loopback check,
  sticky for this release and config;
* MC never runs on CoS's key, and MC's workspace is applied on every start.
Needs node and curl; skipped otherwise.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO / "docker" / "cos-agent-a"
FAKES = Path(__file__).resolve().parent / "fakes"

pytestmark = pytest.mark.skipif(not (shutil.which("node") and shutil.which("curl") and shutil.which("python3")),
                                reason="needs node, curl and python3")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class World:
    def __init__(self, tmp: Path, *, mc_key="mc-placeholder-not-a-key", cos_key="cos-master-key", release="rel1",
                 fake_tools=None, enabled_job=None, combined_patch=None):
        self.tmp = tmp
        self.image = tmp / "image"
        (self.image / "mc-agent").mkdir(parents=True)
        for name in ("openclaw.json", "openclaw.cos-mc.json", "selfcheck.mjs"):
            shutil.copy(AGENT_DIR / name, self.image / name)
        shutil.copytree(AGENT_DIR / "mc-agent" / "workspace", self.image / "mc-agent" / "workspace")
        if combined_patch:
            config = json.loads((self.image / "openclaw.cos-mc.json").read_text())
            combined_patch(config)
            (self.image / "openclaw.cos-mc.json").write_text(json.dumps(config, indent=2))
        self.state = tmp / "state"
        self.state.mkdir()
        self.run = tmp / "run"
        self.log = tmp / "gateway.log"
        self.log.touch()
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        if not shutil.which("sha256sum"):
            (bin_dir / "sha256sum").write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n')
            (bin_dir / "sha256sum").chmod(0o755)
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp),
            "OPENCLAW_STATE_DIR": str(self.state),
            "OPENCLAW_CONFIG_PATH": str(self.state / "openclaw.json"),
            "MINIMOI_MC_IMAGE_DIR": str(self.image),
            "MINIMOI_MC_RUN_DIR": str(self.run),
            "MINIMOI_MC_PORT": str(_free_port()),
            "MINIMOI_MC_READY_WAIT_S": "20",
            "MINIMOI_MC_OPENCLAW": f"python3 {FAKES / 'fake_openclaw.py'}",
            "MINIMOI_OPENCLAW_CLI": str(FAKES / "fake-openclaw-cli.mjs"),
            "MINIMOI_RELEASE_SHA": release,
            "MINIMOI_MODEL_GATEWAY_KEY": cos_key,
            "MC_MODEL_GATEWAY_KEY": mc_key,
            "FAKE_LOG": str(self.log),
            "FAKE_LAN_SECONDS": "5",
        }
        if fake_tools:
            self.env["FAKE_TOOLS_JSON"] = json.dumps(fake_tools)
        if enabled_job:
            self.env["FAKE_ENABLED_JOB"] = enabled_job

    def start(self) -> subprocess.Popen:
        return subprocess.Popen(["sh", str(AGENT_DIR / "start-with-mc.sh")], env=self.env, cwd=self.tmp,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)

    def run_to_end(self, timeout=60):
        proc = self.start()
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, err

    def run_until_state(self, want: str, timeout=60):
        proc = self.start()
        end = time.time() + timeout
        state = ""
        while time.time() < end:
            state = self.state_now()
            if state == want:
                break
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        still_running = proc.poll() is None
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            _out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            _out, err = proc.communicate()
        return state, still_running, err

    def state_now(self) -> str:
        path = self.run / "state"
        return path.read_text().strip() if path.exists() else ""

    def starts(self) -> list[str]:
        return self.log.read_text().splitlines()


COS_ONLY = "agents=cos-agent-a"
COMBINED = "agents=cos-agent-a,mc-agent"


def test_combined_start_checks_on_loopback_before_serving_on_the_lan(tmp_path):
    w = World(tmp_path)
    code, err = w.run_to_end()
    assert code == 0, err
    # One loopback CHECK start, then the checked config on the LAN bind.
    assert w.starts() == [f"start bind=loopback {COMBINED}", f"start bind=lan {COMBINED}"]
    assert (w.run / "checked").read_text().startswith("mode=combined")
    serving = (w.run / "serving").read_text()
    assert "mode=combined" in serving and "release=rel1" in serving
    assert (w.state / "openclaw.json").read_bytes() == (w.image / "openclaw.cos-mc.json").read_bytes()
    for result in ("selfcheck-static-combined.json", "selfcheck-runtime-combined.json"):
        data = json.loads((w.run / result).read_text())
        assert data["cos"]["ok"] and data["mc"]["ok"], data
    assert not (w.state / ".mc-selfcheck-failed").exists()
    assert not (w.state / ".cos-selfcheck-failed").exists()


def test_mc_workspace_is_applied_on_every_start_and_a_changed_copy_is_kept(tmp_path):
    w = World(tmp_path)
    ws = w.state / "workspace-mc"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("edited inside the runtime")
    code, err = w.run_to_end()
    assert code == 0, err
    for name in ("AGENTS.md", "IDENTITY.md", "SOUL.md"):
        assert (ws / name).read_bytes() == (w.image / "mc-agent" / "workspace" / name).read_bytes()
    # Nothing MC keeps may live only in OpenClaw: the replaced copy stays beside it.
    assert (ws / "AGENTS.md.replaced-by-image").read_text() == "edited inside the runtime"
    assert not (ws / "MEMORY.md").exists()


def test_mc_tool_leak_starts_cos_alone_after_its_own_loopback_check(tmp_path):
    w = World(tmp_path, fake_tools={"mc-agent": ["session_status", "read"]})
    code, err = w.run_to_end()
    assert code == 0, err
    assert w.starts() == [f"start bind=loopback {COMBINED}", f"start bind=loopback {COS_ONLY}",
                          f"start bind=lan {COS_ONLY}"]
    marker = (w.state / ".mc-selfcheck-failed").read_text()
    assert "release=rel1" in marker and "combined_sha256=" in marker and "mc-agent: effective tools" in marker
    assert "mode=cos-only" in (w.run / "serving").read_text()
    assert (w.state / "openclaw.json").read_bytes() == (w.image / "openclaw.json").read_bytes()
    assert "Master Craftsman is OFF" in err


def test_a_scheduled_job_is_an_mc_failure_in_the_combined_config(tmp_path):
    w = World(tmp_path, enabled_job="Memory Dreaming Promotion")
    code, err = w.run_to_end()
    assert code == 0, err
    assert "enabled jobs: Memory Dreaming Promotion" in (w.state / ".mc-selfcheck-failed").read_text()


@pytest.mark.parametrize("mc_key,cos_key,reason", [
    ("", "cos-master-key", "MC_MODEL_GATEWAY_KEY is empty"),
    ("same-key", "same-key", "MC_MODEL_GATEWAY_KEY equals the CoS gateway key"),
])
def test_mc_never_runs_without_its_own_key(tmp_path, mc_key, cos_key, reason):
    w = World(tmp_path, mc_key=mc_key, cos_key=cos_key)
    code, err = w.run_to_end()
    assert code == 0, err
    marker = (w.state / ".mc-selfcheck-failed").read_text()
    assert reason in marker
    assert cos_key not in marker.replace(reason, "")          # the key value is never written
    # The combined config was never started, not even on loopback.
    assert w.starts() == [f"start bind=loopback {COS_ONLY}", f"start bind=lan {COS_ONLY}"]


def test_sticky_mc_failure_for_this_release_skips_the_combined_config(tmp_path):
    w = World(tmp_path, fake_tools={"mc-agent": ["session_status", "exec"]})
    assert w.run_to_end()[0] == 0
    w.log.write_text("")
    w.env.pop("FAKE_TOOLS_JSON")          # even with MC fixed, the same release stays CoS-only
    code, err = w.run_to_end()
    assert code == 0, err
    assert w.starts() == [f"start bind=loopback {COS_ONLY}", f"start bind=lan {COS_ONLY}"]
    assert "stays OFF" in err


def test_a_new_release_retries_the_combined_config(tmp_path):
    w = World(tmp_path, fake_tools={"mc-agent": ["session_status", "exec"]})
    assert w.run_to_end()[0] == 0
    w.log.write_text("")
    w.env.pop("FAKE_TOOLS_JSON")
    w.env["MINIMOI_RELEASE_SHA"] = "rel2"
    code, err = w.run_to_end()
    assert code == 0, err
    assert w.starts() == [f"start bind=loopback {COMBINED}", f"start bind=lan {COMBINED}"]
    assert (w.state / ".mc-selfcheck-failed.previous").exists()
    assert not (w.state / ".mc-selfcheck-failed").exists()


def _widen_cos(config):
    config["agents"]["entries"]["cos-agent-a"]["tools"]["allow"].append("exec")


@pytest.mark.parametrize("world_kwargs", [
    {"combined_patch": _widen_cos},                                   # static: CoS's set in the file
    {"fake_tools": {"cos-agent-a": ["session_status", "web_search", "exec"]}},   # runtime: CoS's effective tools
])
def test_cos_failure_never_serves_and_stays_down_without_a_loop(tmp_path, world_kwargs):
    w = World(tmp_path, **world_kwargs)
    state, still_running, err = w.run_until_state("cos-selfcheck-failed")
    assert state == "cos-selfcheck-failed"
    assert still_running, "the container must stay up (no restart loop), not exit"
    assert not any("bind=lan" in line for line in w.starts()), "a failed CoS check must never reach the LAN bind"
    assert not (w.run / "serving").exists() and not (w.run / "checked").exists()
    assert "cos tools.allow" in (w.state / ".cos-selfcheck-failed").read_text() \
        or "cos-agent-a: effective tools" in (w.state / ".cos-selfcheck-failed").read_text()
    assert "CoS self-check FAILED" in err


def test_an_existing_cos_failure_marker_never_starts_openclaw(tmp_path):
    w = World(tmp_path)
    (w.state / ".cos-selfcheck-failed").write_text("failed_at=earlier\n")
    state, still_running, _err = w.run_until_state("cos-selfcheck-failed")
    assert state == "cos-selfcheck-failed" and still_running
    assert w.starts() == []


def test_run_markers_belong_to_this_start(tmp_path):
    w = World(tmp_path)
    w.run.mkdir()
    (w.run / "serving").write_text("mode=combined\nstale=1\n")
    (w.state / ".cos-selfcheck-failed").write_text("x")   # stays down: the stale serving marker must be gone
    state, _running, _err = w.run_until_state("cos-selfcheck-failed")
    assert state == "cos-selfcheck-failed"
    assert not (w.run / "serving").exists()


def test_a_check_that_cannot_complete_serves_nothing_writes_nothing_sticky_and_exits(tmp_path):
    """A slow host (the gateway call times out, twice) is not a verdict: the
    container exits so Docker restarts it and checks again. Never serves."""
    w = World(tmp_path)
    w.env["FAKE_TRANSPORT_ERROR"] = "tools.effective"
    code, err = w.run_to_end()
    assert code == 1
    assert w.starts() == [f"start bind=loopback {COMBINED}"]
    assert w.state_now() == "check-inconclusive-combined"
    assert not (w.state / ".cos-selfcheck-failed").exists() and not (w.state / ".mc-selfcheck-failed").exists()
    assert not (w.run / "serving").exists() and not (w.run / "checked").exists()
    assert "could not complete" in err
    result = json.loads((w.run / "selfcheck-runtime-combined.json").read_text())
    assert result["inconclusive"] and result["cos"]["ok"] and result["mc"]["ok"]


def test_a_combined_config_that_cannot_start_costs_mc_not_cos(tmp_path):
    w = World(tmp_path)
    w.env["FAKE_CRASH_AGENTS"] = "cos-agent-a,mc-agent"
    code, err = w.run_to_end()
    assert code == 0, err
    assert w.starts() == [f"start bind=loopback {COMBINED}", f"start bind=loopback {COS_ONLY}",
                          f"start bind=lan {COS_ONLY}"]
    assert "exited before it was ready" in (w.state / ".mc-selfcheck-failed").read_text()


def test_script_is_posix_sh_and_executable():
    script = AGENT_DIR / "start-with-mc.sh"
    assert os.access(script, os.X_OK)
    assert subprocess.run(["sh", "-n", str(script)]).returncode == 0
    text = script.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert "--bind loopback" in text
    assert "set +e" not in text        # errexit is never toggled (a toggle once turned a CoS failure into an exit)
