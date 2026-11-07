"""mc_container_security_test.py check 8a: a native tool on the Claude route fails, and a Claude-less host has nothing to check.

The fakes stand in for the container. With MC_TEST_CLAUDE_BIN set to a real Claude CLI, the last tests run its own tool list
offline (a closed port, a throwaway config directory) against the settings MC writes.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("mc_container_security_test", ROOT / "scripts/ops/mc_container_security_test.py")
sec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sec)

CLAUDE = {"agents": {"defaults": {"model": {"primary": "openai/gpt-6-astra"}, "modelPolicy": {"allow": ["openai/gpt-6-astra", "anthropic/claude-sonnet-4-6"]}}}}
NO_CLAUDE = {"agents": {"defaults": {"model": {"primary": "openai/gpt-6-astra", "fallbacks": []},
                                     "modelPolicy": {"allow": ["openai/gpt-6-astra", "minimoi-gateway/minimoi-anthropic-mid"]}}}}


def init(*tools: str) -> str:
    return "\n".join(["some banner", json.dumps({"type": "system", "subtype": "init", "tools": list(tools)}),
                      json.dumps({"type": "result", "subtype": "success"})])


def container(monkeypatch, config: dict, listing: str) -> list[str]:
    calls: list[str] = []

    def fake(_agent, script, timeout=30):
        calls.append(script)
        return 0, json.dumps(config) if script.startswith("cat ") else listing
    monkeypatch.setattr(sec, "sh", fake)
    return calls


def test_the_dev_finding_fails(monkeypatch):
    container(monkeypatch, CLAUDE, init("TaskCreate", "TaskGet", "TaskList", "TaskUpdate"))
    ok, detail = sec.claude_native_tools()
    assert ok is False and "TaskList" in detail


def test_a_tool_no_list_has_heard_of_fails(monkeypatch):
    container(monkeypatch, CLAUDE, init("mcp__openclaw__minimoi_task_list", "FutureNativeTool"))
    ok, detail = sec.claude_native_tools()
    assert ok is False and "FutureNativeTool" in detail and "minimoi_task_list" not in detail


def test_another_mcp_server_fails(monkeypatch):
    container(monkeypatch, CLAUDE, init("mcp__other__x"))
    assert sec.claude_native_tools()[0] is False


def test_empty_list_and_governed_tools_pass(monkeypatch):
    container(monkeypatch, CLAUDE, init())
    assert sec.claude_native_tools()[0] is True
    container(monkeypatch, CLAUDE, init("mcp__openclaw__minimoi_task_list"))
    assert sec.claude_native_tools()[0] is True


def test_no_tool_list_proves_nothing(monkeypatch):
    container(monkeypatch, CLAUDE, "claude: command not found")
    assert sec.claude_native_tools()[0] is False


def test_unreadable_config_does_not_pass(monkeypatch):
    monkeypatch.setattr(sec, "sh", lambda *_a, **_k: (1, "cat: no such file"))
    with pytest.raises(ValueError):
        sec.claude_native_tools()


def test_a_host_without_claude_runs_no_cli(monkeypatch):
    calls = container(monkeypatch, NO_CLAUDE, init("TaskList"))
    assert sec.claude_native_tools()[0] is True
    assert len(calls) == 1


def test_it_is_registered_and_spends_no_model_call():
    assert "8a" in [cid for cid, _, _ in sec.CHECKS]
    assert "ANTHROPIC_BASE_URL=http://127.0.0.1:9" in sec.CLAUDE_TOOL_LIST
    # The arguments OpenClaw runs Claude with, so this reads the same settings and sees the same list.
    assert "--setting-sources user" in sec.CLAUDE_TOOL_LIST and "'mcp__openclaw__*'" in sec.CLAUDE_TOOL_LIST


def claude_settings() -> dict:
    env = {"PATH": os.environ["PATH"], "MC_RUNTIME_MODEL_ACCESS": "plan", "MC_PLAN_MODEL": "openai/gpt-6-astra",
           "MC_PLAN_AUTH_PROFILE": "openai:owner", "MC_PLAN_ROUTES": "anthropic/claude-sonnet-4-6"}
    js = "console.log(JSON.stringify(require(process.argv[1]).claudeSettings(process.env)))"
    out = subprocess.run(["node", "-e", js, str(ROOT / "docker/mc-agent-container/model-access.cjs")], env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def real_cli_tools(bin_path: str, tmp_path: Path, settings: dict) -> list[str] | None:
    shim, cfg = tmp_path / "bin", tmp_path / "cfg"
    shim.mkdir()
    cfg.mkdir()
    (shim / "claude").symlink_to(bin_path)
    (shim / "timeout").write_text('#!/bin/sh\nshift\nexec "$@"\n')  # macOS has none; subprocess.run below holds the limit
    (shim / "timeout").chmod(0o755)
    (cfg / "settings.json").write_text(json.dumps(settings))
    env = {"PATH": f"{shim}:{os.environ['PATH']}", "HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(cfg), "DISABLE_AUTOUPDATER": "1",
           "ENABLE_TOOL_SEARCH": "false", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    out = subprocess.run(["sh", "-c", sec.CLAUDE_TOOL_LIST], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    return sec.claude_init_tools(out.stdout)


REAL = os.environ.get("MC_TEST_CLAUDE_BIN")
needs_cli = pytest.mark.skipif(not REAL, reason="MC_TEST_CLAUDE_BIN not set")


@needs_cli
def test_real_cli_with_no_settings_shows_the_native_list(tmp_path):
    tools = real_cli_tools(REAL, tmp_path, {})
    assert tools and "Bash" in tools


@needs_cli
def test_real_cli_under_mc_settings_has_no_native_tools(tmp_path):
    assert real_cli_tools(REAL, tmp_path, claude_settings()) == []
