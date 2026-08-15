"""Fail-closed structural checks for the isolated COS Agent A runtime."""

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
CONFIG_PATH = REPO_ROOT / "docker" / "cos-agent-a" / "openclaw.json"
DOCKERFILE_PATH = REPO_ROOT / "docker" / "Dockerfile.cos-agent-a"


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def _compose_service_block() -> str:
    compose = COMPOSE_PATH.read_text()
    start = compose.index("  cos-agent-a:\n")
    end = compose.index("\nvolumes:\n", start)
    return compose[start:end]


def _cos_service_block() -> str:
    compose = COMPOSE_PATH.read_text()
    start = compose.index("  cos:\n")
    end = compose.index("\n  cos-agent-a:\n", start)
    return compose[start:end]


def test_runtime_image_is_pinned_and_seeds_only_openclaw_config():
    dockerfile = DOCKERFILE_PATH.read_text()
    assert "FROM ghcr.io/openclaw/openclaw:2026.7.1" in dockerfile
    assert ":latest" not in dockerfile
    assert "COPY --chown=node:node docker/cos-agent-a/openclaw.json" in dockerfile


def test_gateway_is_internal_token_authenticated_and_not_model_hardcoded():
    config = _config()
    gateway = config["gateway"]
    agent = config["agents"]["list"][0]

    assert gateway["mode"] == "local"
    assert gateway["bind"] == "lan"
    assert gateway["auth"] == {
        "mode": "token",
        "token": "${OPENCLAW_GATEWAY_TOKEN}",
    }
    assert gateway["controlUi"] == {
        "enabled": True,
        "allowedOrigins": [
            "http://127.0.0.1:18790",
            "http://localhost:18790",
        ],
    }
    assert gateway["http"]["endpoints"]["chatCompletions"] == {
        "enabled": True,
        "maxBodyBytes": 262144,
    }
    assert agent["id"] == "cos-agent-a"
    assert agent["model"] == {
        "primary": "minimoi-gateway/minimoi-cos-agent",
        "fallbacks": [],
    }
    gateway_provider = config["models"]["providers"]["minimoi-gateway"]
    assert gateway_provider["baseUrl"] == "http://model-gateway:4000/v1"
    assert gateway_provider["apiKey"] == "${MINIMOI_MODEL_GATEWAY_KEY}"
    assert gateway_provider["api"] == "openai-completions"
    assert gateway_provider["models"][0]["id"] == "minimoi-cos-agent"


def test_runtime_starts_with_no_channels_skills_or_dangerous_tools():
    config = _config()
    agent = config["agents"]["list"][0]
    tools = agent["tools"]

    assert "channels" not in config
    assert agent["skills"] == []
    assert agent["heartbeat"]["every"] == "0m"
    assert tools["allow"] == ["session_status"]
    # Deny wins in OpenClaw; group:sessions would accidentally block the one
    # explicitly allowed status tool along with the dangerous session tools.
    assert "group:sessions" not in tools["deny"]
    assert tools["elevated"]["enabled"] is False
    for denied in (
        "group:fs",
        "group:runtime",
        "group:web",
        "group:ui",
        "group:messaging",
        "cron",
        "gateway",
        "nodes",
        "sessions_spawn",
        "subagents",
    ):
        assert denied in tools["deny"]


def test_compose_service_has_isolated_state_and_local_only_ui_access():
    service = _compose_service_block()

    assert "container_name: minimoi-cos-agent-a" in service
    assert "OPENCLAW_GATEWAY_TOKEN=${COS_AGENT_A_GATEWAY_TOKEN:?" in service
    assert "MINIMOI_MODEL_GATEWAY_KEY=${MINIMOI_MODEL_GATEWAY_KEY:?" in service
    assert "XAI_API_KEY" not in service
    assert "ANTHROPIC_API_KEY" not in service
    assert "OPENAI_API_KEY" not in service
    assert "OLLAMA_API" not in service
    assert "cos-agent-a-state:/home/node/.openclaw" in service
    assert "cos-agent-a-auth:/home/node/.config/openclaw" in service
    assert '"127.0.0.1:18790:18789"' in service
    assert "0.0.0.0:18790" not in service
    assert "env_file:" not in service
    assert "/var/run/docker.sock" not in service
    assert "~/.openclaw" not in service
    assert "./domains" not in service
    assert "./data" not in service
    assert "./docs" not in service
    assert "depends_on:\n      model-gateway:\n        condition: service_healthy" in service


def test_cos_service_selects_runtime_without_breaking_grok_rollback():
    service = _cos_service_block()

    assert "COS_BACKEND_TYPE=${COS_BACKEND_TYPE:-grok}" in service
    assert "COS_AGENT_RUNTIME_URL=${COS_AGENT_RUNTIME_URL:-http://cos-agent-a:18789/v1}" in service
    assert "COS_AGENT_RUNTIME_TOKEN=${COS_AGENT_A_GATEWAY_TOKEN:-}" in service
    assert "COS_AGENT_RUNTIME_AGENT_ID=${COS_AGENT_RUNTIME_AGENT_ID:-cos-agent-a}" in service
    assert "COS_AGENT_RUNTIME_ROUTING_CONFIG" not in service
    assert "cos_agent_runtime.json" not in service
    assert "COS_AGENT_RUNTIME_TOKEN=${COS_AGENT_A_GATEWAY_TOKEN:?" not in service
