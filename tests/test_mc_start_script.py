"""docker/mc-agent/start-mc.sh and selfcheck.mjs: MC's own container start
(MC spec v0.8 §2.1, v0.9 §5 C8), run against a fake OpenClaw.

* loopback-first: the config is checked on --bind loopback before it is served;
* a VERDICT (wrong value, a gateway request error such as "Unknown agent id",
  a forbidden plugin, a CoS credential in MC's environment) writes a sticky
  marker and stays up without ever starting OpenClaw again: no restart loop;
* INCONCLUSIVE (a timeout, a gateway that exits while starting) exits 1 for the
  restart policy (on-failure:3), with nothing sticky;
* the marker survives restarts until cleared.
Nothing here knows about CoS: MC's start never involves CoS Agent A.
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

REPO = Path(__file__).resolve().parent.parent
MC_DIR = REPO / "docker" / "mc-agent"
FAKES = Path(__file__).resolve().parent / "mc_fakes"

pytestmark = pytest.mark.skipif(not (shutil.which("node") and shutil.which("curl") and shutil.which("python3")),
                                reason="needs node, curl and python3")


def _port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class World:
    def __init__(self, tmp: Path, **fake_env):
        self.image = tmp / "image"
        shutil.copytree(MC_DIR, self.image)
        self.state = tmp / "state"
        self.state.mkdir()
        self.run = tmp / "run"
        self.log = tmp / "gateway.log"
        self.log.touch()
        self.env = {
            "PATH": os.environ["PATH"], "HOME": str(tmp),
            "OPENCLAW_STATE_DIR": str(self.state), "OPENCLAW_CONFIG_PATH": str(self.state / "openclaw.json"),
            "MINIMOI_MC_IMAGE_DIR": str(self.image), "MINIMOI_MC_RUN_DIR": str(self.run),
            "MINIMOI_MC_PORT": str(_port()), "MINIMOI_MC_READY_WAIT_S": "20",
            "MINIMOI_MC_OPENCLAW": f"python3 {FAKES / 'fake_openclaw.py'}",
            "MINIMOI_OPENCLAW_CLI": str(FAKES / "fake-openclaw-cli.mjs"),
            "OPENCLAW_GATEWAY_TOKEN": "mc-openclaw-token-1111", "MC_MODEL_GATEWAY_KEY": "mc-placeholder-not-a-key",
            "MINIMOI_RELEASE_SHA": "rel1", "FAKE_LOG": str(self.log), "FAKE_LAN_SECONDS": "5",
            **fake_env,
        }

    def start(self):
        return subprocess.Popen(["sh", str(MC_DIR / "start-mc.sh")], env=self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)

    def run_to_end(self, timeout=60):
        proc = self.start()
        _out, err = proc.communicate(timeout=timeout)
        return proc.returncode, err

    def run_until(self, want, timeout=60):
        proc = self.start()
        end = time.time() + timeout
        while time.time() < end and proc.poll() is None and self.state_now() != want:
            time.sleep(0.2)
        running = proc.poll() is None
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            _o, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            _o, err = proc.communicate()
        return self.state_now(), running, err

    def state_now(self):
        path = self.run / "state"
        return path.read_text().strip() if path.exists() else ""

    def starts(self):
        return self.log.read_text().splitlines()


def test_checked_on_loopback_then_served(tmp_path):
    w = World(tmp_path)
    code, err = w.run_to_end()
    assert code == 0, err
    assert w.starts() == ["start bind=loopback", "start bind=lan"]
    assert (w.run / "serving").exists() and (w.run / "checked").exists()
    assert (w.state / "openclaw.json").read_bytes() == (MC_DIR / "openclaw.json").read_bytes()
    for name in ("AGENTS.md", "IDENTITY.md", "SOUL.md"):
        assert (w.state / "workspace-mc" / name).read_bytes() == (MC_DIR / "workspace" / name).read_bytes()
    for phase in ("static", "runtime"):
        assert json.loads((w.run / f"selfcheck-{phase}.json").read_text())["failures"] == []
    assert not (w.state / ".mc-selfcheck-failed").exists()


@pytest.mark.parametrize("fake,needle", [
    ({"FAKE_REQUEST_ERROR": "sessions.create"}, "INVALID_REQUEST"),        # the #248 F1 case: a verdict, not a loop
    ({"FAKE_REQUEST_ERROR": "tools.effective"}, "INVALID_REQUEST"),
    ({"FAKE_REQUEST_ERROR": "cron.status"}, "INVALID_REQUEST"),
    ({"FAKE_REQUEST_ERROR": "device.pair.list"}, "INVALID_REQUEST"),
    ({"FAKE_REQUEST_ERROR": "health"}, "INVALID_REQUEST"),
    ({"FAKE_TOOLS": "session_status,read"}, "effective tools"),
    ({"FAKE_ENABLED_JOB": "Memory Dreaming Promotion"}, "enabled jobs"),
    ({"FAKE_PLUGINS": "memory-core,github"}, "github"),
    ({"MINIMOI_MODEL_GATEWAY_KEY": "cos-key"}, "MINIMOI_MODEL_GATEWAY_KEY is in MC's environment"),
    ({"MC_MODEL_GATEWAY_KEY": "mc-openclaw-token-1111"}, "OPENCLAW_GATEWAY_TOKEN"),
    ({"MC_MODEL_GATEWAY_KEY": ""}, "empty"),
])
def test_a_verdict_is_sticky_never_served_and_never_loops(tmp_path, fake, needle):
    w = World(tmp_path, **fake)
    state, running, err = w.run_until("selfcheck-failed")
    assert state == "selfcheck-failed"
    assert running, "a verdict stays up (unhealthy) instead of exiting into a restart loop"
    assert "bind=lan" not in "".join(w.starts())
    marker = (w.state / ".mc-selfcheck-failed").read_text()
    assert needle in marker and "release=rel1" in marker
    for secret in ("cos-key", "mc-openclaw-token-1111"):
        assert secret not in marker + err
    # The next start never starts OpenClaw at all while the marker is there.
    w.log.write_text("")
    state, running, _ = w.run_until("selfcheck-failed")
    assert state == "selfcheck-failed" and running and w.starts() == []


@pytest.mark.parametrize("fake", [
    {"FAKE_TRANSPORT_ERROR": "tools.effective"},
    # A start that keeps failing (owner lease still held after a hard kill, or
    # the start-up lease deadline missed under CPU contention): bounded retries.
    {"FAKE_GATEWAY_EXIT": "loopback", "MINIMOI_MC_START_ATTEMPTS": "3", "MINIMOI_MC_RETRY_PAUSE_S": "0"},
])
def test_inconclusive_exits_for_the_restart_policy_and_is_not_sticky(tmp_path, fake):
    w = World(tmp_path, **fake)
    code, err = w.run_to_end()
    assert code == 1
    assert w.state_now() == "check-inconclusive"
    assert not (w.state / ".mc-selfcheck-failed").exists()
    assert not (w.run / "serving").exists()
    assert "could not complete" in err
    assert "bind=lan" not in "".join(w.starts())
    if fake.get("FAKE_GATEWAY_EXIT"):
        assert w.starts() == ["start bind=loopback"] * 3


def test_a_gateway_killed_while_starting_is_not_retried_in_the_start(tmp_path):
    """An OOM kill goes straight to the restart policy (on-failure:3), never an in-start loop."""
    w = World(tmp_path, FAKE_GATEWAY_KILLED="loopback", MINIMOI_MC_RETRY_PAUSE_S="0")
    code, err = w.run_to_end()
    assert code == 1
    assert w.starts() == ["start bind=loopback"]
    assert "killed while starting (status 137)" in err
    assert not (w.state / ".mc-selfcheck-failed").exists()


def test_a_start_that_fails_once_is_retried_within_the_same_start(tmp_path):
    w = World(tmp_path, FAKE_GATEWAY_EXIT_ONCE="1", MINIMOI_MC_RETRY_PAUSE_S="0")
    code, err = w.run_to_end()
    assert code == 0, err
    assert w.starts() == ["start bind=loopback", "start bind=loopback", "start bind=lan"]
    assert "retrying" in err


def test_start_delay_staggers_the_gateway_start(tmp_path):
    w = World(tmp_path, MC_START_DELAY_S="2")
    started = time.time()
    code, err = w.run_to_end()
    assert code == 0, err
    assert time.time() - started >= 2
    assert "start delay 2s" in err


def test_a_changed_workspace_copy_is_kept_beside_the_image_one(tmp_path):
    w = World(tmp_path)
    (w.state / "workspace-mc").mkdir()
    (w.state / "workspace-mc" / "AGENTS.md").write_text("edited in the runtime")
    assert w.run_to_end()[0] == 0
    assert (w.state / "workspace-mc" / "AGENTS.md.replaced-by-image").read_text() == "edited in the runtime"


def test_script_shape():
    script = MC_DIR / "start-mc.sh"
    assert os.access(script, os.X_OK)
    assert subprocess.run(["sh", "-n", str(script)]).returncode == 0
    text = script.read_text()
    assert "--bind loopback" in text and "set +e" not in text
    assert "cos-agent-a" not in text and "COS_" not in text


# ── selfcheck.mjs pieces ──────────────────────────────────────────────────────

def _node(script):
    result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_interpret_request_errors_are_verdicts_and_transport_errors_inconclusive():
    sc = json.dumps(str(MC_DIR / "selfcheck.mjs"))
    out = _node(f"""
import {{ interpret }} from {sc};
const kind = (fn) => {{ try {{ fn(); return "ok"; }} catch (e) {{ return e.name; }} }};
console.log(JSON.stringify({{
  request: kind(() => interpret("m", 1, '{{"ok":false,"error":{{"type":"gateway_request_error","code":"INVALID_REQUEST"}}}}')),
  transport: kind(() => interpret("m", 1, '{{"ok":false,"error":{{"type":"gateway_transport_error","kind":"timeout"}}}}')),
  timeout: kind(() => interpret("m", null, "", "ETIMEDOUT")),
  nojson: kind(() => interpret("m", 1, "boom")),
  exit1json: kind(() => interpret("m", 1, '{{"x":1}}')),
  ok: kind(() => interpret("m", 0, '{{"groups":[]}}')),
}}));
""")
    assert out == {"request": "RequestRefused", "transport": "Inconclusive", "timeout": "Inconclusive",
                   "nojson": "Inconclusive", "exit1json": "RequestRefused", "ok": "ok"}


def test_static_check_passes_the_committed_config_and_flags_each_drift():
    sc = json.dumps(str(MC_DIR / "selfcheck.mjs"))
    cfg = json.dumps(str(MC_DIR / "openclaw.json"))
    out = _node(f"""
import {{ staticCheck }} from {sc};
import {{ readFileSync }} from "node:fs";
const base = () => JSON.parse(readFileSync({cfg}, "utf8"));
const env = {{ MC_MODEL_GATEWAY_KEY: "k", OPENCLAW_GATEWAY_TOKEN: "t" }};
const drift = (fn) => {{ const c = base(); fn(c); return staticCheck(c, env).length; }};
console.log(JSON.stringify({{
  clean: staticCheck(base(), env).length,
  toolSearch: drift((c) => {{ c.tools.toolSearch = true; }}),
  controlUi: drift((c) => {{ c.gateway.controlUi.enabled = true; }}),
  plugins: drift((c) => {{ c.plugins.allow = ["memory-core", "github"]; }}),
  secondAgent: drift((c) => {{ c.agents.entries["cos-agent-a"] = {{}}; }}),
  webSearch: drift((c) => {{ c.agents.entries["mc-agent"].tools.allow.push("web_search"); }}),
  modelPolicy: drift((c) => {{ delete c.agents.entries["mc-agent"].modelPolicy; }}),
}}));
""")
    assert out["clean"] == 0
    assert all(v >= 1 for k, v in out.items() if k != "clean"), out
