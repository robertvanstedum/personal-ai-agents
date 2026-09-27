from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = ROOT / "docker-compose.prod.yml"
PROD_GATEWAY_CONFIG = ROOT / "services/model_gateway/litellm.prod.yaml"
WORKFLOW = ROOT / ".github/workflows/deploy.yml"


def _service_block(service_name: str) -> str:
    text = PROD_COMPOSE.read_text()
    start = text.index(f"  {service_name}:\n")
    remainder = text[start + len(f"  {service_name}:\n"):]
    next_service = re.search(r"(?m)^  [^\s][^:]*:\s*$", remainder)
    return text[start:] if next_service is None else text[start:start + len(f"  {service_name}:\n") + next_service.start()]


def test_production_gateway_omits_development_only_ollama():
    config = yaml.safe_load(PROD_GATEWAY_CONFIG.read_text())
    deployments = {
        item["model_name"]: item["litellm_params"]["model"]
        for item in config["model_list"]
    }

    assert deployments == {
        "minimoi-cos-agent": "xai/grok-4.3",
        "minimoi-cos-agent-xai-fast": "xai/grok-4.3",
        "minimoi-cos-web-search": "xai/grok-4.3",
        "minimoi-cos-agent-anthropic": "anthropic/claude-sonnet-4-6",
    }
    assert config["router_settings"]["fallbacks"] == [{
        "minimoi-cos-agent": [
            "minimoi-cos-agent-xai-fast",
            "minimoi-cos-agent-anthropic",
        ],
    }]
    assert "ollama_chat/" not in PROD_GATEWAY_CONFIG.read_text()


def test_production_uses_immutable_application_images_and_isolated_state():
    gateway = _service_block("model-gateway")
    agent = _service_block("cos-agent-a")

    assert "minimoi/cos-scheduler:model-gateway-${MINIMOI_IMAGE_TAG:-latest}" in gateway
    assert "LITELLM_MASTER_KEY=${MINIMOI_MODEL_GATEWAY_KEY:?" in gateway
    assert "MINIMOI_RECEIPT_ENDPOINT=http://cos-scheduler:8769" in gateway
    assert "ports:" not in gateway
    assert "no-new-privileges:true" in gateway
    assert "minimoi/cos-scheduler:agent-a-${MINIMOI_IMAGE_TAG:-latest}" in agent
    assert "cos-agent-a-state:/home/node/.openclaw" in agent
    assert "COS_AGENT_A_GATEWAY_TOKEN:?" in agent
    assert "XAI_API_KEY" not in agent
    assert "ANTHROPIC_API_KEY" not in agent
    assert "OPENAI_API_KEY" not in agent
    assert "ports:" not in agent


def test_production_agent_runtime_has_memory_cap_and_never_self_updates():
    agent = _service_block("cos-agent-a")
    service = yaml.safe_load(PROD_COMPOSE.read_text())["services"]["cos-agent-a"]

    # OpenClaw 2026.9.x idles at about 0.9 GB (7.1: about 0.3 GB).
    assert service["mem_limit"] == "1200m"
    assert "OPENCLAW_NO_AUTO_UPDATE=1" in service["environment"]
    assert "OPENCLAW_NO_AUTO_UPDATE=1" in agent


def test_staging_inherits_the_production_agent_runtime_limits():
    staging = yaml.safe_load(
        (ROOT / "docker-compose.staging.yml").read_text()
    )["services"]["cos-agent-a"]

    # The staging override only swaps the image and adds the loopback port;
    # it must not loosen or replace the memory cap or the environment.
    assert "mem_limit" not in staging
    assert "deploy" not in staging
    assert "environment" not in staging


def test_production_cos_consumers_select_agent_runtime_and_share_receipts():
    scheduler = _service_block("cos-scheduler")
    bot = _service_block("cos-bot")
    portal = _service_block("portal")

    for service in (scheduler, bot):
        assert "COS_BACKEND_TYPE=${COS_BACKEND_TYPE:-openclaw}" in service
        assert "COS_AGENT_RUNTIME_URL=http://cos-agent-a:18789/v1" in service
        assert "COS_AGENT_RUNTIME_TOKEN=${COS_AGENT_A_GATEWAY_TOKEN:?" in service
        assert "model_gateway_receipts.jsonl" in service
    assert "COS_BACKEND=http://cos-scheduler:8769" in portal


def test_production_images_include_gateway_module_and_agent_identity():
    for name in ("Dockerfile.cos-scheduler", "Dockerfile.cos-bot"):
        assert "COPY services/model_gateway/ services/model_gateway/" in (
            ROOT / "docker" / name
        ).read_text()

    agent_dockerfile = (ROOT / "docker/Dockerfile.cos-agent-a").read_text()
    for filename in ("AGENTS.md", "IDENTITY.md", "SOUL.md", "MEMORY.md"):
        assert f"docker/cos-agent-a/{filename}" in agent_dockerfile


def test_ci_builds_and_verifies_the_two_new_production_services():
    workflow = WORKFLOW.read_text()
    deploy_script = (
        ROOT / "scripts/operations/deploy_scoped_release.sh"
    ).read_text()

    assert 'cos-agent-a) dockerfile="docker/Dockerfile.cos-agent-a"' in workflow
    assert 'model-gateway) dockerfile="docker/Dockerfile.model-gateway"' in workflow
    assert 'tag="agent-a-$SHA"' in workflow
    assert 'tag="model-gateway-$SHA"' in workflow
    assert "cos-agent-a) echo" in deploy_script
    assert "model-gateway) echo" in deploy_script
    assert "State.Health.Status" in deploy_script


def test_ssm_sync_uses_named_parameters_without_printing_values():
    script = (
        ROOT / "scripts/credentials/sync_cos_runtime_secrets_from_ssm.sh"
    ).read_text()

    for parameter in (
        "/minimoi/production/cos_agent_a_gateway_token",
        "/minimoi/production/model_gateway_key",
        "/minimoi/production/model_gateway_receipt_key",
        "/minimoi/production/xai_api_key",
        "/minimoi/production/anthropic_api_key",
        "/minimoi/production/openai_api_key",
    ):
        assert parameter in script
    assert "--with-decryption" in script
    assert 'echo "$secret_value"' not in script


def test_deploy_snapshots_agent_a_volumes_before_recreating_it():
    script = (
        ROOT / "scripts/operations/deploy_scoped_release.sh"
    ).read_text()
    pull = script.index('"${COMPOSE[@]}" pull "${SERVICES[@]}"')
    snapshot_call = script.index("\n  snapshot_agent_a_volumes\n")
    up = script.index('"${COMPOSE[@]}" up -d --no-deps')

    # Pull first (a failed pull changes nothing), then stop + snapshot, then
    # recreate. Only when cos-agent-a is in the release.
    assert pull < snapshot_call < up
    guard = script[pull:snapshot_call]
    assert '*" cos-agent-a "*' in guard

    body = script[script.index("snapshot_agent_a_volumes() {"):pull]
    assert 'AGENT_A_SNAPSHOT_DIR="/opt/minimoi/backups/cos-agent-a"' in script
    assert 'docker stop --time 30 "$AGENT_A_CONTAINER"' in body
    assert body.index("docker stop") < body.index("docker cp")
    for path in ("/home/node/.openclaw", "/home/node/.config/openclaw"):
        assert path in body
    assert "umask 077" in body
    assert "previous_image=" in body
    assert "sha256sum" in body
    # A failed snapshot restarts the old container and aborts the deploy.
    failure = body[body.index("snapshot FAILED"):]
    assert failure.index('docker start "$AGENT_A_CONTAINER"') < failure.index("return 1")
    # backup_local.sh prunes only dated folders (20*-*-*) under backups/, and
    # the S3/Dropbox syncs read only those, so this folder is left alone.
    assert "-name '20*-*-*'" in (ROOT / "scripts/backup_local.sh").read_text()
