"""#553: one conversation stays on one warm xAI cache.

OpenClaw sends a stable prompt_cache_key per session (compat.supportsPromptCacheKey on the gateway provider); the gateway
turns it into xAI's x-grok-conv-id header on xAI deployments only, and every gateway config loads the hook."""
import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cache_affinity", REPO / "services/model_gateway/cache_affinity.py")
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)

KEY = "6c0e2f1a-0b7e-4b4e-9c55-3f1d0a9e8b21"


def _call(**extra):
    return {"model": "xai/grok-4.7", "messages": [{"role": "user", "content": "hi"}], "prompt_cache_key": KEY, **extra}


def test_an_xai_call_with_a_cache_key_gets_a_stable_conversation_id():
    out = ca.with_conversation_id(_call())
    conv = out["extra_headers"]["x-grok-conv-id"]
    assert conv == ca.conversation_id(KEY) and conv.startswith("minimoi-") and len(conv) == len("minimoi-") + 32
    assert KEY not in conv                                              # a digest: the session id never leaves the gateway
    assert ca.with_conversation_id(_call())["extra_headers"]["x-grok-conv-id"] == conv      # same key, same id
    assert ca.conversation_id(KEY + "x") != conv                        # another conversation, another id
    assert out["prompt_cache_key"] == KEY and out["messages"] == _call()["messages"]


def test_existing_extra_headers_are_kept_and_a_callers_own_id_wins():
    out = ca.with_conversation_id(_call(extra_headers={"x-other": "1"}))
    assert out["extra_headers"] == {"x-other": "1", "x-grok-conv-id": ca.conversation_id(KEY)}
    assert ca.with_conversation_id(_call(extra_headers={"X-Grok-Conv-Id": "mine"})) is None
    assert ca.with_conversation_id(_call(headers={"x-grok-conv-id": "mine"})) is None


@pytest.mark.parametrize("kwargs", [
    {"model": "openai/gpt-6-astra", "prompt_cache_key": KEY},          # OpenAI keeps its own prompt_cache_key
    {"model": "anthropic/claude-opus-5-5", "prompt_cache_key": KEY},   # the Claude fallback is untouched
    {"model": "grok-4.7", "custom_llm_provider": "openai", "prompt_cache_key": KEY},
    {"model": "xai/grok-4.7"},                                          # no key: nothing to send
    {"model": "xai/grok-4.7", "prompt_cache_key": ""},
    {"model": "xai/grok-4.7", "prompt_cache_key": 42},
    {"model": "xai/grok-4.7", "prompt_cache_key": "k" * 2000},
    None, "not a dict",
])
def test_nothing_changes_off_xai_or_without_a_usable_key(kwargs):
    assert ca.with_conversation_id(kwargs) is None


def test_the_provider_is_read_from_litellm_params_too():
    out = ca.with_conversation_id({"model": "grok-4.7", "litellm_params": {"custom_llm_provider": "xai"}, "prompt_cache_key": KEY})
    assert out["extra_headers"]["x-grok-conv-id"] == ca.conversation_id(KEY)


def test_each_xai_call_logs_a_count_with_or_without_the_header_and_never_the_key(caplog):
    hook = ca.CacheAffinity()
    ca.COUNTS.update({"with": 0, "without": 0})
    with caplog.at_level("INFO", logger="minimoi.cache_affinity"):
        asyncio.run(hook.async_pre_call_deployment_hook(_call(), None))
        asyncio.run(hook.async_pre_call_deployment_hook({"model": "xai/grok-4.7"}, None))
        asyncio.run(hook.async_pre_call_deployment_hook({"model": "openai/gpt-6-astra", "prompt_cache_key": KEY}, None))
    lines = [r.getMessage() for r in caplog.records if r.name == "minimoi.cache_affinity"]
    assert lines == ["xai call with x-grok-conv-id (with=1 without=0)", "xai call without x-grok-conv-id (with=1 without=1)"]
    assert not any(KEY in line or ca.conversation_id(KEY) in line for line in lines)


def test_the_hook_never_raises_into_litellm(monkeypatch):
    hook = ca.CacheAffinity()
    assert asyncio.run(hook.async_pre_call_deployment_hook(_call(), None))["extra_headers"]["x-grok-conv-id"]
    monkeypatch.setattr(ca, "with_conversation_id", lambda kw: 1 / 0)
    assert asyncio.run(hook.async_pre_call_deployment_hook(_call(), None)) is None


@pytest.mark.parametrize("name", ["litellm.prod.yaml", "litellm.staging.yaml"])
def test_the_production_and_staging_gateways_load_the_hook(name):
    callbacks = yaml.safe_load((REPO / "services/model_gateway" / name).read_text())["litellm_settings"]["callbacks"]
    assert "cache_affinity.cache_affinity" in callbacks
    assert callbacks[0] == "receipt_callback.receipt_callback"


def test_the_gateway_image_carries_the_hook():
    """Production and staging run the image built from this Dockerfile. The Mac's local compose (litellm.yaml) is left
    as it is: changing docker-compose.yml would make the release classifier redeploy every service."""
    assert "COPY services/model_gateway/cache_affinity.py /app/cache_affinity.py\n" in (REPO / "docker/Dockerfile.model-gateway").read_text()
    assert "cache_affinity" not in (REPO / "services/model_gateway/litellm.yaml").read_text()


def _node(expr: str, env: dict) -> object:
    script = f"const m=require({json.dumps(str(REPO / 'docker/mc-agent-container/model-access.cjs'))}); console.log(JSON.stringify({expr}));"
    r = subprocess.run(["node", "-e", script], env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", **env},
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_mcs_gateway_provider_sends_openclaw_s_session_cache_key():
    """Each model OpenClaw reaches through the gateway opts in to prompt_cache_key (OpenClaw's documented compat flag for
    an OpenAI-compatible proxy; it is never detected automatically)."""
    env = {"MC_RUNTIME_MODEL_ACCESS": "plan", "MC_PLAN_MODEL": "anthropic/claude-opus-5", "MC_MODEL_GATEWAY_KEY": "sk-" + "a" * 20,
           "MC_METERED_ROUTES": "minimoi-gateway/minimoi-xai-strong,minimoi-gateway/minimoi-xai-low",
           "MC_PRIMARY_ROUTE": "minimoi-gateway/minimoi-xai-strong",
           "MC_FAILOVER_ROUTES": "minimoi-gateway/minimoi-openai-strong"}
    provider = _node("m.overlay(process.env).models.providers['minimoi-gateway']", env)
    assert [m["id"] for m in provider["models"]] == ["minimoi-xai-strong", "minimoi-xai-low", "minimoi-openai-strong"]
    assert all(m["compat"] == {"supportsPromptCacheKey": True} for m in provider["models"])
    dev = _node("m.overlay(process.env).models.providers['minimoi-gateway-mc']",
                {"MC_RUNTIME_MODEL_ACCESS": "gateway", "MINIMOI_ENV": "dev", "MC_MODEL_GATEWAY_KEY": "sk-" + "a" * 20})
    assert all(m["compat"] == {"supportsPromptCacheKey": True} for m in dev["models"])


def test_cos_gateway_models_send_the_cache_key_too():
    """CoS shares the gateway path: its pinned model and the Grok tiers a host offers it, in gateway and plan mode."""
    cfg_path = REPO / "docker/cos-agent-a/openclaw.json"
    cfg = json.loads(cfg_path.read_text())
    assert all(m["compat"] == {"supportsPromptCacheKey": True} for m in cfg["models"]["providers"]["minimoi-gateway"]["models"])
    script = (f"const m=require({json.dumps(str(REPO / 'docker/cos-agent-a/plan-access.cjs'))});"
              f"const c=require({json.dumps(str(cfg_path))});"
              "console.log(JSON.stringify([m.apply(c,process.env).models.providers['minimoi-gateway'].models,"
              "m.apply(c,{...process.env,COS_RUNTIME_MODEL_ACCESS:'plan'}).models.providers['minimoi-gateway'].models]));")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "COS_GATEWAY_KEY_CAPPED": "1",
           "COS_METERED_ROUTES": "minimoi-gateway/minimoi-xai-strong", "COS_PLAN_MODEL": "openai/gpt-6-astra",
           "COS_PLAN_AUTH_PROFILE": "openai:robert"}
    r = subprocess.run(["node", "-e", script], env=env, capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    gateway, plan = json.loads(r.stdout)
    assert [m["id"] for m in gateway] == ["minimoi-cos-agent", "minimoi-xai-strong"]
    assert [m["id"] for m in plan] == ["minimoi-xai-strong"]
    assert all(m["compat"] == {"supportsPromptCacheKey": True} for m in gateway + plan)
