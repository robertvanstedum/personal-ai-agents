"""Depth admission must not turn quoted context into routing authority."""
import copy
import json
from pathlib import Path

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
    ("Quick answer: how late?", "quick"), ("Please look into this: delivery failures", None),
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
    result = for_owner_turn(catalogue, f"{agent}-conversation", conv, "Think hard about this")
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


@pytest.mark.parametrize("invalid", ["unavailable", "same_model", "bad_shape", "non_runtime", "non_selectable",
                                     "phrase_only_not_bool"])
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
    elif invalid == "phrase_only_not_bool":
        catalogue["roles"]["mc-conversation"]["depth_routes"]["phrase_only"] = "yes"
    else:
        deep["runtime_override"] = False
    path = tmp_path / "routes.json"
    path.write_text(json.dumps(catalogue))
    with pytest.raises(ValueError):
        load_catalogue(path)


def test_missing_canonical_owner_text_refuses(catalogue):
    with pytest.raises(RouteRefused):
        for_owner_turn(catalogue, "mc-conversation", {}, None)


# ── #586: production's pair for MC, gated by phrase ──────────────────────────────────────────────
TEMPLATE = Path(__file__).resolve().parents[2] / "scripts/operations/production_route_catalogue.json"


def test_production_names_mcs_quick_deep_pair_by_phrase_only():
    cat = load_catalogue(TEMPLATE)
    assert cat["roles"]["mc-conversation"]["depth_routes"] == {
        "quick": "api-xai-strong", "deep": "api-xai-strong-deep", "light": "api-xai-strong-light", "phrase_only": True}
    assert "depth_routes" not in cat["roles"]["cos-conversation"]
    deep = next(r for r in cat["routes"] if r["route_id"] == "api-xai-strong-deep")
    assert deep["model"] == "minimoi-xai-strong-deep" and deep["offered"] == {"mc": "available"}
    assert "failover" not in deep                       # a deep turn that cannot run stops; it never moves


@pytest.mark.parametrize("text,route,explicit", [
    ("Think hard about the retry design", "api-xai-strong-deep", True),
    ("Go deep on the cache", "api-xai-strong-deep", True),
    ("This is important: compare the options", "api-xai-strong-deep", True),
    ("Look into this: the failed backup", "api-xai-strong", False),     # scope first, on the quick route
    ("What are the risks?", "api-xai-strong", False),
    ("Quick answer: is it up?", "api-xai-strong", True),
])
def test_production_pair_picks_deep_only_on_the_owners_words(text, route, explicit):
    result = for_owner_turn(load_catalogue(TEMPLATE), "mc-conversation", {}, text)
    assert result["route_id"] == route
    assert result["depth"]["explicit"] is explicit
    assert result["depth"]["allow_continuation"] is False   # phrase only: the model never escalates by itself
    assert "continuation_route" not in result["depth"]


def test_ordinary_turn_keeps_the_quick_routes_failover():
    result = for_owner_turn(load_catalogue(TEMPLATE), "mc-conversation", {}, "What are the risks?")
    assert result["failover"]["route_id"] == "api-anthropic-strong"


def test_phrase_only_absent_keeps_model_chosen_continuation(catalogue):
    assert for_owner_turn(catalogue, "mc-conversation", {}, "What are the risks?")["depth"]["allow_continuation"] is True


def test_revert_leaves_nothing_naming_the_deep_alias(tmp_path):
    """The revert: delete depth_routes and set the deep route's offered back to {} (or delete the route)."""
    from core.model_routes import relay_routes, view
    cat = json.loads(TEMPLATE.read_text())
    del cat["roles"]["mc-conversation"]["depth_routes"]
    next(r for r in cat["routes"] if r["route_id"] == "api-xai-strong-deep")["offered"] = {}
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(cat))
    reverted = load_catalogue(path)
    assert "strong-deep" not in json.dumps(relay_routes(reverted))
    for agent in ("mc", "cos"):
        assert "api-xai-strong-deep" not in {r["route_id"] for r in view(reverted, agent, {})["routes"]}
    turn = for_owner_turn(reverted, "mc-conversation", {}, "Think hard about this")
    assert turn["route_id"] == "api-xai-strong" and "depth" not in turn


# ── #593: short turns take the light route ───────────────────────────────────────────────────────
from core.model_routes import served_receipt
from core.model_routes.depth import short_turn

LIGHT_TURNS = [
    "Hi", "hey MC", "Good morning!", "Morning", "thanks!", "Thank you so much", "cheers", "How are you?",
    "Hi, how are you doing today?", "What's up?",
    "What is a race condition?", "Why is the sky blue?", "How does TCP slow start work?",
    "Is Python garbage collected?", "What does idempotent mean?",
]
QUICK_TURNS = [
    "", "   ", "yes", "ok", "go ahead", "do it", "no", "why?", "Why is that?",                 # approvals, follow-ups
    "Is prod up?", "What did we decide about Grok?", "Can you check the logs?",               # lookup and recall
    "What's the latest on the backup?", "How much did we spend today?", "Is the deploy done?",
    "Hi, can you check prod?", "hey, look at issue 593", "What is #593 about?",                # greeting plus work
    "What is in docs/runbooks?", "What does https://example.com say?", "What does foo.py do?",
    "Think hard: what is a race condition?", "Go deep", "Quick answer: what is TCP?",          # explicit depth stays explicit
    "What is the difference between optimistic and pessimistic locking in large databases?",  # over the word cap
    "> How are you?", "`What is TCP?`", "What is TCP?\nAnd UDP?",                                # quoted, code, multi-line
    "Explain the plan", "Write a haiku about rain", "Tell me a joke",                          # actions, not questions
    "What is TCP? And UDP?",
    "What are the risks?", "Which is better?", "What are the options?", "What is the time?",   # about the thread
    "What should we do next?", "How about the other one?",
]


@pytest.mark.parametrize("text", LIGHT_TURNS)
def test_short_turn_accepts_a_social_line_or_a_short_general_question(text):
    assert short_turn(text) is True


@pytest.mark.parametrize("text", QUICK_TURNS)
def test_short_turn_refuses_anything_that_may_need_a_look_a_tool_or_the_thread(text):
    assert short_turn(text) is False


@pytest.mark.parametrize("text", [None, 12, b"hi"])
def test_short_turn_refuses_a_non_text(text):
    assert short_turn(text) is False


def test_production_pair_sends_a_short_turn_with_no_files_to_the_light_route():
    result = for_owner_turn(load_catalogue(TEMPLATE), "mc-conversation", {}, "Good morning!", attached=False)
    assert result["route_id"] == "api-xai-strong-light" and result["model"] == "minimoi-xai-strong-light"
    assert result["depth"] == {"kind": "light", "explicit": False, "allow_continuation": False}
    assert result["failover"]["route_id"] == "api-anthropic-strong"      # never worse off than an ordinary turn


@pytest.mark.parametrize("attached", [True, None])
def test_a_short_turn_with_files_or_unknown_files_stays_quick(attached):
    result = for_owner_turn(load_catalogue(TEMPLATE), "mc-conversation", {}, "Good morning!", attached=attached)
    assert result["route_id"] == "api-xai-strong" and result["depth"]["kind"] == "quick"


@pytest.mark.parametrize("text,route", [
    ("What are the risks?", "api-xai-strong"),                  # ordinary question keeps the normal route
    ("Can you check the logs?", "api-xai-strong"),
    ("Think hard about the retry design", "api-xai-strong-deep"),
    ("Quick answer: is it up?", "api-xai-strong"),
])
def test_an_ordinary_or_explicit_turn_never_takes_the_light_route(text, route):
    assert for_owner_turn(load_catalogue(TEMPLATE), "mc-conversation", {}, text, attached=False)["route_id"] == route


def test_a_conversation_set_to_deep_or_another_model_never_goes_light():
    cat = load_catalogue(TEMPLATE)
    deep_conv = {"routes": {"mc": {"route_id": "api-xai-strong-deep", "revision": 1}}}
    assert for_owner_turn(cat, "mc-conversation", deep_conv, "Hi", attached=False)["route_id"] == "api-xai-strong-deep"
    other = {"routes": {"mc": {"route_id": "api-xai-low", "revision": 1}}}
    assert for_owner_turn(cat, "mc-conversation", other, "Hi", attached=False)["route_id"] == "api-xai-low"


def test_the_light_turns_route_shows_in_last_turn():
    route = for_owner_turn(load_catalogue(TEMPLATE), "mc-conversation", {}, "Hi", attached=False)
    receipt = served_receipt(route, {"provider": "minimoi-gateway", "model": "minimoi-xai-strong-light"})
    assert receipt["route_id"] == "api-xai-strong-light" and receipt["depth"] == "light" and receipt["served_matches"] is True
    quick = for_owner_turn(load_catalogue(TEMPLATE), "mc-conversation", {}, "What are the risks?", attached=False)
    assert served_receipt(quick, None)["depth"] == "quick"


def test_cos_gets_the_light_route_when_its_catalogue_names_one(catalogue):
    """MC and CoS are twins: the same policy serves cos-conversation once its host pairs a light route."""
    light = next(r for r in catalogue["routes"] if r["route_id"] == "api-xai-strong-light")
    light["offered"] = {"mc": "available", "cos": "available"}
    catalogue["roles"]["cos-conversation"]["depth_routes"]["light"] = "api-xai-strong-light"
    assert for_owner_turn(catalogue, "cos-conversation", {}, "Hi", attached=False)["route_id"] == "api-xai-strong-light"
    assert for_owner_turn(catalogue, "cos-conversation", {}, "Hi", attached=True)["route_id"] == "api-xai-strong"


@pytest.mark.parametrize("bad", [
    lambda p: p.update(light=7),                                # not a route id
    lambda p: p.update(light="api-xai-strong"),                 # the same runtime model as quick
    lambda p: p.update(light="unknown"),
])
def test_an_invalid_light_route_refuses_the_catalogue(bad):
    cat = json.loads(TEMPLATE.read_text())
    bad(cat["roles"]["mc-conversation"]["depth_routes"])
    from core.model_routes.depth import validate_policy
    with pytest.raises((ValueError, RouteRefused)):
        validate_policy(cat, "mc-conversation")


def test_revert_leaves_nothing_naming_the_light_alias(tmp_path):
    """The revert: drop "light" from depth_routes and set the light route's offered back to {}."""
    from core.model_routes import relay_routes, view
    cat = json.loads(TEMPLATE.read_text())
    del cat["roles"]["mc-conversation"]["depth_routes"]["light"]
    next(r for r in cat["routes"] if r["route_id"] == "api-xai-strong-light")["offered"] = {}
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(cat))
    reverted = load_catalogue(path)
    assert "strong-light" not in json.dumps(relay_routes(reverted))
    assert "api-xai-strong-light" not in {r["route_id"] for r in view(reverted, "mc", {})["routes"]}
    turn = for_owner_turn(reverted, "mc-conversation", {}, "Hi", attached=False)
    assert turn["route_id"] == "api-xai-strong" and turn["depth"]["kind"] == "quick"
    assert for_owner_turn(reverted, "mc-conversation", {}, "Go deep")["route_id"] == "api-xai-strong-deep"


@pytest.mark.parametrize("files,context,attached", [
    (None, None, False), (None, {}, False), (None, {"area": "board", "page": "/guild"}, False),
    (object(), None, True), (None, {"item_ref": "board:12"}, True),
])
def test_portal_admission_knows_whether_a_turn_carries_anything_beyond_words(files, context, attached):
    from minimoi_portal.guild_ui.api import _turn_attached
    assert _turn_attached(files, context) is attached
