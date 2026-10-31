"""Structural contract tests for the shared LiteLLM gateway module."""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "services" / "model_gateway" / "litellm.yaml"
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
GATEWAY_CONFIGS = sorted((REPO_ROOT / "services" / "model_gateway").glob("litellm*.yaml"))

# #458: three providers × three tiers, the only provider models any gateway config may name.
TIERS = {
    ("anthropic", "strong"): "anthropic/claude-opus-5-5",
    ("anthropic", "mid"): "anthropic/claude-sonnet-5-5",
    ("anthropic", "low"): "anthropic/claude-haiku-5-5",
    ("openai", "strong"): "openai/gpt-6-astra",
    ("openai", "mid"): "openai/gpt-6.1-sol",
    ("openai", "low"): "openai/gpt-6-luna",
    ("xai", "strong"): "xai/grok-4.7",
    ("xai", "mid"): "xai/grok-4.6",
    ("xai", "low"): "xai/grok-4.3",
}
EFFORT = {"strong": "high", "mid": "medium", "low": "low"}


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

    assert {name: model for name, model in deployments.items()
            if not name.startswith(("minimoi-cos", "minimoi-voice"))} == {
        f"minimoi-{provider}-{tier}": model for (provider, tier), model in TIERS.items()
    }
    # Voice's own routes (Spec 165 §5.4; confirm by voice v3): reached only with the capped voice keys, never a fallback.
    assert {name: model for name, model in deployments.items() if name.startswith("minimoi-voice")} == {
        "minimoi-voice-summary": "xai/grok-4.3",
        "minimoi-voice-intent": "xai/grok-4.3",
    }
    assert {name: model for name, model in deployments.items() if name.startswith("minimoi-cos")} == {
        "minimoi-cos-agent": "xai/grok-4.3",
        "minimoi-cos-agent-xai-fast": "xai/grok-4.3",
        "minimoi-cos-web-search": "xai/grok-4.3",
        "minimoi-cos-agent-anthropic": "anthropic/claude-sonnet-5-5",
        "minimoi-cos-agent-local": "ollama_chat/llama3.2:3b",
    }
    assert config["router_settings"]["fallbacks"] == [{
        "minimoi-cos-agent": [
            "minimoi-cos-agent-xai-fast",
            "minimoi-cos-agent-anthropic",
        ],
    }]
    assert config["router_settings"]["num_retries"] == 0


def test_config_references_environment_names_without_secret_values():
    text = CONFIG_PATH.read_text()

    assert "os.environ/XAI_API_KEY" in text
    assert "os.environ/ANTHROPIC_API_KEY" in text
    assert "os.environ/LITELLM_MASTER_KEY" in text
    assert "os.environ/OPENAI_API_KEY" in text
    assert "sk-" not in text


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
    assert "OPENAI_API_KEY=${OPENAI_API_KEY:?" in service

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


def test_explicit_local_chat_route_does_not_claim_unverified_tool_or_thinking_support():
    local = next(
        item
        for item in _config()["model_list"]
        if item["model_name"] == "minimoi-cos-agent-local"
    )

    assert local["model_info"]["mode"] == "chat"
    assert "supports_function_calling" not in local["model_info"]
    assert "think" not in local["litellm_params"]


def test_local_regression_is_explicit_and_never_a_fallback():
    for filename in ('litellm.yaml', 'litellm.staging.yaml'):
        config = yaml.safe_load((CONFIG_PATH.parent / filename).read_text())
        route = next(r for r in config['model_list'] if r['model_name'] == 'minimoi-cos-agent-local')
        assert route['litellm_params'] == {
            'model': 'ollama_chat/llama3.2:3b', 'api_base': 'os.environ/OLLAMA_API_BASE', 'timeout': 180}
        assert 'minimoi-cos-agent-local' not in str(config['router_settings']['fallbacks'])
        assert 'fallback_position' not in route['model_info']
    prod = yaml.safe_load((CONFIG_PATH.parent / 'litellm.prod.yaml').read_text())
    assert all('ollama' not in r['litellm_params']['model'] for r in prod['model_list'])
    staging = yaml.safe_load((REPO_ROOT / 'docker-compose.staging.yml').read_text())
    assert 'OLLAMA_API_BASE=${OLLAMA_API_BASE:-http://host.docker.internal:11434}' in staging['services']['model-gateway']['environment']


def test_no_route_switches_a_shared_chat_backend_to_responses_mode():
    """LiteLLM 1.93 keys a deployment's mode by its backend model (e.g. xai/grok-4.3) and never downgrades it.

    One route marked `mode: responses` therefore sends every chat route on the same backend through the Responses
    bridge, which drops the system message for xAI (measured on staging, 30 Oct 2026: persona, policy and handbook never
    reached Grok). Every gateway config is checked, production's included.
    """
    for path in sorted((REPO_ROOT / "services" / "model_gateway").glob("litellm*.yaml")):
        model_list = yaml.safe_load(path.read_text())["model_list"]
        chat_backends = {
            item["litellm_params"]["model"]
            for item in model_list
            if (item.get("model_info") or {}).get("mode", "chat") == "chat"
        }
        for item in model_list:
            if (item.get("model_info") or {}).get("mode") == "responses":
                assert item["litellm_params"]["model"] not in chat_backends, (path.name, item["model_name"])


def test_every_config_defines_the_nine_tiers_with_explicit_effort():
    """Opus 5.5 defaults to effort medium, so a strong tier without an explicit effort is quietly weaker (#458)."""
    for path in GATEWAY_CONFIGS:
        routes = {item["model_name"]: item for item in yaml.safe_load(path.read_text())["model_list"]}
        for (provider, tier), model in TIERS.items():
            route = routes[f"minimoi-{provider}-{tier}"]
            params = route["litellm_params"]
            assert params["model"] == model, (path.name, provider, tier)
            assert params["reasoning_effort"] == EFFORT[tier], (path.name, provider, tier)
            assert params["api_key"].endswith(f"{provider.upper()}_API_KEY"), (path.name, provider, tier)
            assert route["model_info"]["id"] == f"tier-{provider}-{tier}"


def test_every_route_and_fallback_serves_a_tier_model():
    """A role route takes its model from a tier (`<<: *<provider>-<tier>`), so nothing names a model outside the set and
    no fallback reaches a removed model. The explicit Ollama regression route is the one exception, only in non-production configs."""
    allowed = set(TIERS.values())
    for path in GATEWAY_CONFIGS:
        config = yaml.safe_load(path.read_text())
        routes = {item["model_name"]: item["litellm_params"]["model"] for item in config["model_list"]}
        for name, model in routes.items():
            if name == "minimoi-cos-agent-local" and path.name in ("litellm.yaml", "litellm.staging.yaml"):
                continue
            assert model in allowed, (path.name, name, model)
        for chain in config["router_settings"]["fallbacks"]:
            for primary, fallbacks in chain.items():
                for name in [primary, *fallbacks]:
                    assert name in routes, (path.name, name)


def test_role_routes_switch_by_their_merge_line():
    """The toggle is one line per role: each role's litellm_params is a merge of one tier anchor."""
    for path in GATEWAY_CONFIGS:
        text = path.read_text()
        for provider, tier in TIERS:
            assert f"litellm_params: &{provider}-{tier}\n" in text, (path.name, provider, tier)
        for line in text.splitlines():
            if line.strip().startswith("litellm_params: {"):
                assert "<<: *" in line, (path.name, line)
