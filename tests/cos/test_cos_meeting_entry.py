"""F2 step 5: the cos-meeting agent entry, the cos-bound plugin config, and where the tool credential and binding
store may live. Config checks only; the native probe against a running container is part of the B4 Dev package."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / "docker/cos-agent-a/openclaw.json").read_text())
NATIVE = {"read", "ls", "write", "edit", "apply_patch", "exec", "process", "web_fetch", "browser", "memory_search",
          "memory_get"}


def meeting():
    return CONFIG["agents"]["entries"]["cos-meeting"]


def test_cos_meeting_has_only_bound_tools_and_bounded_search():
    tools = meeting()["tools"]
    assert tools["alsoAllow"] == ["web_search", "cos_actions"]
    assert NATIVE <= set(tools["deny"]) and {"group:fs", "group:runtime", "cos_board", "cos_github_issues",
                                             "gateway"} <= set(tools["deny"])
    assert tools["fs"] == {"workspaceOnly": True} and tools["elevated"] == {"enabled": False}
    assert meeting()["workspace"] != CONFIG["agents"]["entries"]["cos-agent-a"]["workspace"]
    assert meeting()["skills"] == [] and meeting()["heartbeat"] == {"every": "0m"}


def test_owner_entry_keeps_native_tools_per_robert_q1():
    owner = CONFIG["agents"]["entries"]["cos-agent-a"]["tools"]["alsoAllow"]
    assert {"write", "edit", "exec", "cos_actions"} <= set(owner)


def test_cos_bound_replaces_cos_actions_and_is_configured_for_both_entries():
    plugins = CONFIG["plugins"]
    assert "cos-actions" not in plugins["allow"] and "cos-actions" not in plugins["entries"]
    cfg = plugins["entries"]["cos-bound"]["config"]
    assert cfg["enabled"] is True and cfg["agents"] == ["cos-agent-a", "cos-meeting"]
    assert cfg["acceptCredentialReadableBy"] == ["cos-agent-a"]          # the recorded Q1 residual, owner entry only
    assert re.fullmatch(r"http://cos-scheduler:\d+/internal/cos/v1", cfg["endpoint"])
    schema = json.loads((ROOT / "docker/cos-agent-a/plugins/cos-bound/openclaw.plugin.json").read_text())
    assert set(cfg) <= set(schema["configSchema"]["properties"])


def _agent_container_mounts():
    """Container paths mounted into any CoS/MC agent service in the compose files."""
    mounts = set()
    for compose in ROOT.glob("docker-compose*.yml"):
        text = compose.read_text()
        for block in re.split(r"\n  (?=[a-z0-9-]+:\n)", text):
            if not re.match(r"(cos-agent|mc-agent|minimoi-mc-agent)", block.strip()):
                continue
            for line in block.splitlines():
                m = re.match(r"\s+- (\S+)$", line)
                if not m or ":" not in m.group(1):
                    continue
                parts = m.group(1).strip("'\"").split(":")
                target = parts[-2] if parts[-1] in ("ro", "rw") else parts[-1]
                if target.startswith("/minimoi") or target.startswith("/home/") or target.startswith("/run/"):
                    mounts.add(target.rstrip("/"))
    return mounts


def test_credential_is_outside_the_meeting_workspace_and_binding_store_outside_agent_mounts():
    token = CONFIG["plugins"]["entries"]["cos-bound"]["config"]["tokenFile"]
    assert not token.startswith(meeting()["workspace"] + "/")
    mounts = _agent_container_mounts()
    assert "/minimoi/data" in mounts                       # the reason the store may not live under data/
    store = "/minimoi-private/cos-bindings/bindings.sqlite3"   # the path B4 provisions for cos-scheduler only
    assert not any(store == m or store.startswith(m + "/") for m in mounts)
