"""The production route catalogue template is a file to review, not a catalogue that is installed.

MC and CoS default to Grok 4.7 (api-xai-strong) on the capped API key, each Grok tier falling over to Claude's at its
level (#466). The OpenAI plan routes stay available to choose under Options, each with its own API failover. No failover
ever lands on Grok.
"""
import json
from pathlib import Path

from core.model_routes import CATALOGUE_PATH, load_catalogue, route_of, view

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = ROOT / "scripts/operations/production_route_catalogue.json"
OPENAI_FAILOVER = {
    "plan-openai-strong": "api-openai-strong",
    "plan-openai-mid": "api-openai-mid",
    "plan-openai-low": "api-openai-low",
}
BANNED = ("auth.json", "grok-cli", "sk-", "consumer", "subscription_adapter")


def _template():
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _shipped():
    return json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))


def test_the_template_loads_and_is_not_the_installed_catalogue():
    loaded = load_catalogue(TEMPLATE_PATH)
    assert loaded["revision"] == "2026-11-10.production-template"
    assert TEMPLATE_PATH.resolve() != CATALOGUE_PATH.resolve()
    assert loaded["revision"] != _shipped()["revision"]


def test_grok_is_offered_to_mc_and_cos_as_paid_api():
    catalogue = load_catalogue(TEMPLATE_PATH)
    grok = route_of(catalogue, "api-xai-low")
    assert grok["offered"] == {"mc": "available", "cos": "available"}
    assert grok["provider"] == "minimoi-gateway"
    assert grok["model"] == "minimoi-xai-low"
    assert grok["billing_mode"] == "metered_api"
    assert grok["runtime"] == "openclaw"
    assert grok.get("auth_profile_ref") is None
    assert grok["failover"] == "api-anthropic-low"
    assert "pay per use" in grok["label"]
    for agent in ("mc", "cos"):
        shown = {row["route_id"]: row for row in view(catalogue, agent, {})["routes"]}
        assert shown["api-xai-low"]["available"] is True
        assert shown["api-xai-low"]["billing_label"] == "API"
        assert shown["api-xai-low"]["billing_mode"] == "metered_api"


GROK_FAILOVER = {"api-xai-strong": "api-anthropic-strong", "api-xai-mid": "api-anthropic-mid", "api-xai-low": "api-anthropic-low"}


def test_failover_is_the_openai_plan_routes_and_grok_onto_claude():
    catalogue = _template()
    declared = {route["route_id"]: route["failover"] for route in catalogue["routes"] if "failover" in route}
    assert declared == {**OPENAI_FAILOVER, **GROK_FAILOVER}
    by_id = {route["route_id"]: route for route in catalogue["routes"]}
    for plan, target in OPENAI_FAILOVER.items():
        assert by_id[plan]["provider"] == "openai"
        assert by_id[plan]["billing_mode"] == "subscription"
        assert by_id[target]["billing_mode"] == "metered_api"
        assert by_id[target]["provider"] == "minimoi-gateway"
        assert "failover" not in by_id[target]
    for grok, target in GROK_FAILOVER.items():
        assert by_id[grok]["billing_mode"] == by_id[target]["billing_mode"] == "metered_api"
        assert by_id[grok]["tier"] == by_id[target]["tier"]
        assert "failover" not in by_id[target]


def test_no_claude_route_fails_over_and_no_failover_names_grok():
    for route in _template()["routes"]:
        target = str(route.get("failover") or "")
        assert "xai" not in target.lower() and "grok" not in target.lower()
        claude = (
            route["provider"] == "anthropic"
            or route["route_id"].startswith("plan-anthropic")
            or route["route_id"].startswith("api-anthropic")
            or "claude" in route["route_id"]
        )
        if claude:
            assert "failover" not in route


def test_every_role_defaults_to_grok_strong():
    template, shipped = _template(), _shipped()
    assert template["tiers"] == shipped["tiers"]
    assert template["tiers"]["strong"]["letter"] == "A"
    assert route_of(template, "api-xai-strong")["tier"] == "strong"
    assert {role["route"] for role in template["roles"].values()} == {"api-xai-strong"}
    catalogue = load_catalogue(TEMPLATE_PATH)
    for agent in ("mc", "cos"):
        assert view(catalogue, agent, {})["selected_route_id"] == "api-xai-strong"


def test_the_template_changes_only_revision_claude_failover_cos_plan_and_the_grok_routes():
    template, shipped = _template(), _shipped()
    assert [route["route_id"] for route in template["routes"]] == [route["route_id"] for route in shipped["routes"]]
    for new, old in zip(template["routes"], shipped["routes"]):
        if new["route_id"] in ("plan-anthropic-strong", "plan-anthropic-mid"):
            assert "failover" in old and "failover" not in new
            without = {key: value for key, value in old.items() if key != "failover"}
            assert new == without
        elif new["route_id"] in ("plan-openai-strong", "plan-openai-mid", "plan-openai-low"):
            assert new["offered"]["cos"] == "available" and new["offered"]["mc"] == old["offered"]["mc"]
            assert {k: v for k, v in new.items() if k != "offered"} == {k: v for k, v in old.items() if k != "offered"}
        elif new["route_id"] in GROK_FAILOVER:
            assert old["offered"] == {}
            assert new["offered"] == {"mc": "available", "cos": "available"}
            assert new["failover"] == GROK_FAILOVER[new["route_id"]] and "pay per use" in new["label"]   # the picker adds its billing word, API
            rest_new = {key: value for key, value in new.items() if key not in {"offered", "failover"}}
            rest_old = {key: value for key, value in old.items() if key not in {"offered", "failover"}}
            assert rest_new == rest_old
        else:
            assert new == old


def test_there_is_no_grok_subscription_adapter_cli_route_or_copied_credential():
    text = TEMPLATE_PATH.read_text(encoding="utf-8").lower()
    for banned in BANNED:
        assert banned not in text
    for route in _template()["routes"]:
        assert route.get("runtime") not in {"grok", "grok-cli"}
        blob = json.dumps(route).lower()
        assert "auth.json" not in blob
        if "xai" in route["route_id"] or "grok" in route.get("label", "").lower():
            assert route["billing_mode"] == "metered_api"
            assert route["runtime"] == "openclaw"
