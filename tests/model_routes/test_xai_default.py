"""All xAI (#466): every role can read a Grok API route, each Grok tier falls over to Claude's at its level, and a switched
turn says xAI rather than the plan. The runtimes keep the plan configured but a host can name a Grok tier as their default."""
import json
import subprocess
from pathlib import Path

import pytest

from core.model_routes import load_catalogue, route_of, switch_words, switched_route, snapshot

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "scripts/operations/production_route_catalogue.json"
XAI = "minimoi-gateway/minimoi-xai-strong"
XAI_ALL = f"{XAI},minimoi-gateway/minimoi-xai-mid,minimoi-gateway/minimoi-xai-low"
KEY = "sk-synthetic-test-key-not-real"


@pytest.mark.parametrize("role,tier", [("mc-conversation", "strong"), ("mc-private", "strong"), ("mc-rooms", "strong"), ("cos-conversation", "strong")])
def test_every_role_starts_on_grok_with_claude_at_its_level(role, tier):
    route = snapshot(load_catalogue(TEMPLATE), role)
    assert route["model"] == "minimoi-xai-strong" and route["billing_mode"] == "metered_api" and route["tier"] == tier
    assert route["failover"]["model"] == f"minimoi-anthropic-{tier}"


@pytest.mark.parametrize("bad", ["api-openai-strong", "api-anthropic-strong-x", "plan-anthropic-strong"])
def test_a_grok_route_may_fail_over_only_to_claudes_api(tmp_path, bad):
    catalogue = json.loads(TEMPLATE.read_text())
    route_of(catalogue, "api-xai-strong")["failover"] = bad
    path = tmp_path / "c.json"
    path.write_text(json.dumps(catalogue))
    with pytest.raises(ValueError):
        load_catalogue(path)


def test_a_switched_grok_turn_names_xai_not_the_plan():
    route = snapshot(load_catalogue(TEMPLATE), "mc-conversation")
    words = switch_words(switched_route(route, "rate_limit"), {"provider": "minimoi-gateway", "model": "minimoi-anthropic-strong"})
    assert words.startswith("[xAI hit its rate or usage limit,") and words.endswith("The next turn tries xAI again.]")
    assert "plan" not in words
    assert "ran on the Claude API: minimoi-anthropic-strong" in words


def _node(script, env):
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, cwd=ROOT, env={"PATH": __import__("os").environ["PATH"], **env})


MC_ENV = {"MC_RUNTIME_MODEL_ACCESS": "plan", "MC_PLAN_MODEL": "openai/gpt-6-astra", "MC_PLAN_ROUTES": "openai/gpt-6-sol",
          "MC_PLAN_AUTH_PROFILE": "openai:owner", "MC_METERED_ROUTES": XAI_ALL, "MC_MODEL_GATEWAY_KEY": KEY}
MC_SCRIPT = "const p=require('./docker/mc-agent-container/model-access.cjs');console.log(JSON.stringify(p.overlay(process.env).agents.defaults.model))"


def test_mc_defaults_to_the_grok_tier_a_host_names():
    run = _node(MC_SCRIPT, {**MC_ENV, "MC_PRIMARY_ROUTE": XAI})
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"primary": XAI, "fallbacks": []}


def test_mc_keeps_its_plan_default_when_no_primary_is_named():
    run = _node(MC_SCRIPT, MC_ENV)
    assert json.loads(run.stdout)["primary"] == "openai/gpt-6-astra"


def test_mc_deep_alias_requires_explicit_host_allowlist():
    deep = XAI + "-deep"
    assert _node(MC_SCRIPT, {**MC_ENV, "MC_PRIMARY_ROUTE": deep}).returncode != 0
    run = _node(MC_SCRIPT, {**MC_ENV, "MC_METERED_ROUTES": XAI_ALL + "," + deep, "MC_PRIMARY_ROUTE": deep})
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"primary": deep, "fallbacks": []}


def test_cos_deep_alias_requires_capped_key_proof():
    deep = XAI + "-deep"
    script = "const p=require('./docker/cos-agent-a/plan-access.cjs');console.log(JSON.stringify(p.offeredRoutes(process.env)))"
    env = {"COS_METERED_ROUTES": deep, "COS_GATEWAY_KEY_FILE": "/nonexistent/synthetic-key"}
    assert json.loads(_node(script, env).stdout) == []
    assert json.loads(_node(script, {**env, "COS_GATEWAY_KEY_CAPPED": "1"}).stdout) == [deep]


def test_dev_gateway_aliases_pin_same_model_with_different_effort():
    import yaml
    doc = yaml.safe_load((ROOT / "services/model_gateway/litellm.staging.yaml").read_text())
    models = {m["model_name"]: m["litellm_params"] for m in doc["model_list"]}
    quick, deep = models["minimoi-xai-strong"], models["minimoi-xai-strong-deep"]
    assert quick["model"] == deep["model"] == "xai/grok-4.7"
    assert (quick["reasoning_effort"], deep["reasoning_effort"]) == ("low", "high")


@pytest.mark.parametrize("bad", ["minimoi-gateway/minimoi-openai-strong", "xai/grok-4.7", "minimoi-gateway/minimoi-xai-pro"])
def test_mc_refuses_a_primary_that_is_not_an_offered_grok_tier(bad):
    assert _node(MC_SCRIPT, {**MC_ENV, "MC_PRIMARY_ROUTE": bad}).returncode != 0
