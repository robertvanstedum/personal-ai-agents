"""The route catalogue: roles read routes, tier/provider/billing stay apart, only proved routes are offered, nothing falls back."""
import json

import pytest

from core.model_routes import (RouteRefused, check_selectable, conversation_role, load_catalogue, relay_routes,
                               role_snapshot_for, route_of, served_receipt, snapshot, view)

CAT = load_catalogue()


def test_tiers_are_the_gateway_tiers_shown_as_abc():
    assert {k: v["letter"] for k, v in CAT["tiers"].items()} == {"strong": "A", "mid": "B", "low": "C"}


def test_every_mc_task_reads_one_route_and_mc_is_on_the_openai_plan_today():
    for name, role in CAT["roles"].items():
        if role["agent"] == "mc":
            route = route_of(CAT, role["route"])
            assert (route["provider"], route["billing_mode"]) == ("openai", "subscription"), name


def test_no_metered_route_is_offered_to_mc_at_all():
    assert all(r["billing_mode"] == "subscription" for r in CAT["routes"] if "mc" in r["offered"])
    assert all(ref.split("/")[0] in ("openai", "anthropic") for ref in relay_routes(CAT).values())


def test_a_role_must_read_an_available_route(tmp_path):
    broken = json.loads(json.dumps(CAT))
    broken["roles"]["mc-morning"]["route"] = "plan-anthropic-strong"     # not proved yet
    path = tmp_path / "c.json"
    path.write_text(json.dumps(broken))
    with pytest.raises(ValueError):
        load_catalogue(path)


def test_claude_is_listed_with_its_reason_but_cannot_be_chosen_until_proved():
    shown = {r["route_id"]: r for r in view(CAT, "mc", {})["routes"]}
    assert not shown["plan-anthropic-strong"]["available"] and shown["plan-anthropic-strong"]["reason"]
    with pytest.raises(RouteRefused) as refused:
        check_selectable(CAT, "mc", "plan-anthropic-strong")
    assert refused.value.code == "route_unavailable"


@pytest.mark.parametrize("bad", [None, 3, "", "openai/gpt-6-astra", "http://evil/", "gateway-cos-role", "../x"])
def test_only_a_catalogue_route_of_this_agent_can_be_chosen(bad):
    with pytest.raises(RouteRefused):
        check_selectable(CAT, "mc", bad)


def test_a_route_withdrawn_later_refuses_the_turn_and_never_substitutes(tmp_path):
    conv = {"routes": {"mc": {"route_id": "plan-openai-strong", "revision": 1}}}
    withdrawn = json.loads(json.dumps(CAT))
    withdrawn["routes"][0]["offered"]["mc"] = "Sign-in expired"
    withdrawn["roles"] = {"mc-conversation": {"agent": "mc", "task": "x", "route": "plan-openai-strong", "selectable": True}}
    with pytest.raises(RouteRefused) as refused:
        snapshot(withdrawn, "mc-conversation", conv)
    assert "Sign-in expired" in refused.value.message and "Nothing was sent" in refused.value.message


def test_one_agents_choice_never_reads_anothers():
    conv = {"routes": {"mc": {"route_id": "plan-openai-strong", "revision": 4}}}
    assert snapshot(CAT, conversation_role("cos"), conv)["route_id"] == "gateway-cos-role"
    assert view(CAT, "cos", conv)["revision"] == 0


def test_cos_is_honest_its_plan_routes_are_listed_unavailable_and_it_never_overrides_its_runtime():
    assert snapshot(CAT, "cos-conversation", {})["runtime_override"] is False
    assert [r["route_id"] for r in view(CAT, "cos", {})["routes"] if r["available"]] == ["gateway-cos-role"]


def test_a_non_selectable_task_ignores_a_conversation_choice():
    conv = {"routes": {"mc": {"route_id": "plan-anthropic-strong"}}}
    assert snapshot(CAT, "mc-morning", conv)["route_id"] == CAT["roles"]["mc-morning"]["route"]
    assert role_snapshot_for("cos", "mc-morning") is None and role_snapshot_for("stub", "mc-rooms") is None


def test_selected_and_served_stay_separate():
    route = snapshot(CAT, "mc-conversation", {})
    assert served_receipt(route, None)["served"] is None and served_receipt(route, None)["served_matches"] is None
    assert served_receipt(route, {"provider": "openai", "model": "gpt-6-astra"})["served_matches"] is True
    assert served_receipt(route, {"provider": "anthropic", "model": "claude-opus-5"})["served_matches"] is False
