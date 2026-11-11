"""Depth admission must not turn quoted context into routing authority."""
import copy
import json

import pytest

from core.model_routes import RouteRefused, load_catalogue, snapshot
from core.model_routes.depth import explicit_depth, for_owner_turn


@pytest.fixture
def catalogue():
    cat = copy.deepcopy(load_catalogue())
    for rid in ("api-xai-strong", "api-anthropic-strong"):
        route = next(r for r in cat["routes"] if r["route_id"] == rid)
        route["offered"] = {"mc": "available", "cos": "available"}
    for agent in ("mc", "cos"):
        cat["roles"][f"{agent}-conversation"].update(
            route="api-xai-strong", depth_routes={"quick": "api-xai-strong", "deep": "api-anthropic-strong"})
    return cat


@pytest.mark.parametrize("text,kind", [
    ("Quick answer: how late?", "quick"), ("Please look into this: delivery failures", "deep"),
    ("Think harder about this", "deep"), ("Think hard. Compare the options", "deep"),
    ("Please think hard about this", "deep"), ("thinking hard", None),
    ("This is important: compare the options", "deep"),
    ("go deep", "deep"), ('Translate "look into this"', None),
    ('> Think harder about this', None), ('```\nlook into this\n```', None),
    ('"this is important"', None), ("What are the risks?", None),
])
def test_only_explicit_owner_instruction_prefix(text, kind):
    assert explicit_depth(text) == kind


@pytest.mark.parametrize("agent", ["mc", "cos"])
def test_explicit_depth_pins_route_without_mutating_selection(catalogue, agent):
    conv = {"routes": {agent: {"route_id": "api-xai-strong", "revision": 7}}}
    original = copy.deepcopy(conv)
    result = for_owner_turn(catalogue, f"{agent}-conversation", conv, "Look into this")
    assert result["route_id"] == "api-anthropic-strong"
    assert result["selection_revision"] == 7
    assert result["depth"] == {"kind": "deep", "explicit": True, "allow_continuation": False}
    assert conv == original


def test_quick_override_cannot_auto_escalate(catalogue):
    conv = {"routes": {"mc": {"route_id": "api-anthropic-strong"}}}
    result = for_owner_turn(catalogue, "mc-conversation", conv, "Quick answer please")
    assert result["route_id"] == "api-xai-strong"
    assert result["depth"]["allow_continuation"] is False
    assert "continuation_route" not in result["depth"]


def test_implicit_quick_pins_deeper_candidate_before_catalogue_changes(catalogue):
    result = for_owner_turn(catalogue, "mc-conversation", {}, "What are the risks?")
    deeper = result["depth"]["continuation_route"]
    catalogue["roles"]["mc-conversation"]["depth_routes"]["deep"] = "unknown"
    assert result["depth"]["allow_continuation"] is True
    assert deeper["route_id"] == "api-anthropic-strong"
    assert "depth" not in deeper


def test_explicit_unrelated_conversation_model_is_preserved(catalogue):
    conv = {"routes": {"mc": {"route_id": "plan-openai-mid"}}}
    assert for_owner_turn(catalogue, "mc-conversation", conv, "Go deep") == snapshot(catalogue, "mc-conversation", conv)


def test_absent_policy_is_unchanged():
    cat = load_catalogue()
    for agent in ("mc", "cos"):
        role = f"{agent}-conversation"
        assert for_owner_turn(cat, role, {}, "Go deep") == snapshot(cat, role, {})


@pytest.mark.parametrize("invalid", ["unavailable", "same_model", "bad_shape", "non_runtime", "non_selectable"])
def test_invalid_host_policy_refuses_on_load(catalogue, tmp_path, invalid):
    deep = next(r for r in catalogue["routes"] if r["route_id"] == "api-anthropic-strong")
    if invalid == "unavailable":
        deep["offered"]["cos"] = "unproved"
    elif invalid == "same_model":
        deep["model"] = "minimoi-xai-strong"
    elif invalid == "bad_shape":
        catalogue["roles"]["mc-conversation"]["depth_routes"]["extra"] = "x"
    elif invalid == "non_selectable":
        catalogue["roles"]["mc-conversation"]["selectable"] = False
    else:
        deep["runtime_override"] = False
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(catalogue))
    with pytest.raises(ValueError):
        load_catalogue(path)


def test_missing_canonical_owner_text_refuses(catalogue):
    with pytest.raises(RouteRefused):
        for_owner_turn(catalogue, "mc-conversation", {}, None)
