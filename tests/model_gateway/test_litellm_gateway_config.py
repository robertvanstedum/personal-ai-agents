"""Structural contract tests for the shared LiteLLM gateway module."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "services" / "model_gateway" / "litellm.yaml"
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"


def _config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


def _service_block() -> str:
    compose = COMPOSE_PATH.read_text()
    start = compose.index("  model-gateway:\n")
    end = compose.index("\n  # CoS scheduler", start)
    return compose[start:end]


def _agent_service_block() -> str:
    compose = COMPOSE_PATH.read_text()
    start = compose.index("  cos-agent-a:\n")
    end = compose.index("\nvolumes:\n", start)
    return compose[start:end]


def test_gateway_has_one_logical_primary_and_ordered_fallbacks():
    config = _config()
    deployments = {
        item["model_name"]: item["litellm_params"]["model"]
        for item in config["model_list"]
    }

    assert deployments == {
        "minimoi-cos-agent": "xai/grok-4.3",
        "minimoi-cos-agent-xai-fast": "xai/grok-4.3",
        "minimoi-cos-web-search": "xai/grok-4.3",
        "minimoi-cos-agent-anthropic": "anthropic/claude-sonnet-4-6",
        "minimoi-cos-agent-local": "ollama_chat/qwen3:4b",
    }
    assert config["router_settings"]["fallbacks"] == [{
        "minimoi-cos-agent": [
            "minimoi-cos-agent-xai-fast",
            "minimoi-cos-agent-anthropic",
            "minimoi-cos-agent-local",
        ],
    }]
    assert config["router_settings"]["num_retries"] == 0


def test_config_references_environment_names_without_secret_values():
    text = CONFIG_PATH.read_text()

    assert "os.environ/XAI_API_KEY" in text
    assert "os.environ/ANTHROPIC_API_KEY" in text
    assert "os.environ/LITELLM_MASTER_KEY" in text
    assert "sk-" not in text
    assert "OPENAI_API_KEY" not in text


def test_gateway_image_is_immutable_private_and_least_privilege():
    service = _service_block()

    assert "ghcr.io/berriai/litellm-non_root@sha256:" in service
    assert ":latest" not in service
    assert '"127.0.0.1:14000:4000"' in service
    assert "0.0.0.0:14000" not in service
    assert "no-new-privileges:true" in service
    assert "cap_drop:\n      - ALL" in service
    assert "./services/model_gateway/litellm.yaml:/app/config.yaml:ro" in service
    assert "./services/model_gateway/receipt_callback.py:/app/receipt_callback.py:ro" in service
    assert "env_file:" not in service


def test_only_gateway_receives_provider_credentials():
    compose = COMPOSE_PATH.read_text()
    service = _service_block()

    assert "LITELLM_MASTER_KEY=${MINIMOI_MODEL_GATEWAY_KEY:?" in service
    assert "XAI_API_KEY=${XAI_API_KEY:?" in service
    assert "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:?" in service
    assert "OPENAI_API_KEY=${OPENAI_API_KEY:-}" in service

    assert compose.count("LITELLM_MASTER_KEY=${MINIMOI_MODEL_GATEWAY_KEY:?") == 1
    agent_service = _agent_service_block()
    assert "MINIMOI_MODEL_GATEWAY_KEY=${MINIMOI_MODEL_GATEWAY_KEY:?" in agent_service
    assert "XAI_API_KEY" not in agent_service
    assert "ANTHROPIC_API_KEY" not in agent_service
    assert "OPENAI_API_KEY" not in agent_service
    assert "OLLAMA_API" not in agent_service


def test_gateway_emits_receipts_to_cos_over_an_independent_secret():
    config = _config()
    service = _service_block()

    assert config["litellm_settings"]["callbacks"] == [
        "receipt_callback.receipt_callback"
    ]
    assert "MINIMOI_RECEIPT_ENDPOINT=http://cos:18769/internal/model-gateway/receipt" in service
    assert "MINIMOI_RECEIPT_KEY=${MINIMOI_MODEL_GATEWAY_RECEIPT_KEY:?" in service


def test_bounded_search_uses_gateway_credential_boundary():
    config = _config()
    search_route = next(
        item
        for item in config["model_list"]
        if item["model_name"] == "minimoi-cos-web-search"
    )
    agent_config = (REPO_ROOT / "docker/cos-agent-a/openclaw.json").read_text()
    plugin = (
        REPO_ROOT
        / "docker/cos-agent-a/plugins/cos-bounded-search/index.ts"
    ).read_text()

    assert search_route["litellm_params"]["model"] == "xai/grok-4.3"
    assert search_route["model_info"]["supports_web_search"] is True
    assert '"provider": "minimoi"' in agent_config
    assert "minimoi-cos-web-search" in plugin
    assert "process.env.MINIMOI_MODEL_GATEWAY_KEY" in plugin
    assert "XAI_API_KEY" not in plugin


def test_local_fallback_declares_tool_capability_and_realistic_token_budget():
    local = next(
        item
        for item in _config()["model_list"]
        if item["model_name"] == "minimoi-cos-agent-local"
    )

    assert local["model_info"]["supports_function_calling"] is True
    assert local["model_info"]["max_output_tokens"] >= 500
    assert local["litellm_params"]["think"] is True
