"""Lane V (#659, #661, #658): Voice and Model linked, the owner's setting per partner, the Last-answer line."""
import json
import threading
from pathlib import Path

import pytest

from core.model_routes import load_catalogue
from core.model_routes.depth import for_owner_turn
from core.model_routes.last_answer import label_for, line
from core.model_routes.menu import VoiceUnavailable, choose, menu_view
from core.model_routes.picker import effective_voice, family, owner_conversation, route_for, voice_options
from core.partner_settings import PartnerSettings, SettingsConflict

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = ROOT / "scripts/operations/production_route_catalogue.json"
REPO = ROOT / "core/model_routes/catalogue.json"


@pytest.fixture
def prod():
    return load_catalogue(PRODUCTION)


@pytest.fixture
def repo():
    return load_catalogue(REPO)


# ── picker ────────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("agent", ["mc", "cos"])
def test_production_offers_one_strong_model_per_voice(prod, agent):
    assert route_for(prod, agent, "openai")["route_id"] == "plan-openai-strong"
    assert route_for(prod, agent, "xai")["route_id"] == "api-xai-strong"
    assert [o["provider"] for o in voice_options(prod, agent)] == ["openai", "xai"]
    assert all(o["available"] for o in voice_options(prod, agent))


def test_no_claude_in_the_voice_list(prod):
    assert {o["provider"] for o in voice_options(prod, "mc")} == {"openai", "xai"}
    assert route_for(prod, "mc", "anthropic") is None


def test_plan_first_default(prod):
    assert effective_voice(prod, "mc", None) == "openai"
    assert effective_voice(prod, "mc", "xai") == "xai"


def test_unavailable_voice_is_disabled_with_reason(repo):
    # The repo's own catalogue offers OpenAI's plan to MC only, and Grok's API to nobody.
    cos = {o["provider"]: o for o in voice_options(repo, "cos")}
    assert cos["openai"]["available"] is False and "CoS" in cos["openai"]["reason"]
    assert effective_voice(repo, "cos", "openai") is None
    assert effective_voice(repo, "mc", "xai") == "openai"      # a saved voice not offered here falls back, plan first


def test_host_mapping_override(prod):
    cat = {**prod, "voice_routes": {"cos": {"openai": "plan-openai-mid"}}}
    assert route_for(cat, "cos", "openai")["route_id"] == "plan-openai-mid"
    assert route_for(cat, "mc", "openai")["route_id"] == "plan-openai-strong"


def test_family():
    assert family({"provider": "openai", "model": "gpt-6-astra"}) == "openai"
    assert family({"provider": "minimoi-gateway", "model": "minimoi-xai-strong-light"}) == "xai"
    assert family({"provider": "gateway", "model": "minimoi-cos-agent", "route_id": "gateway-cos-role"}) is None


def test_owner_conversation_replaces_selection_and_keeps_evidence(prod):
    conv = {"id": "c1", "routes": {"mc": {"route_id": "api-xai-low", "revision": 3, "last_turn": {"turn_id": "t"}}}}
    out = owner_conversation(conv, "mc", "openai", prod)
    assert out["routes"]["mc"] == {"route_id": "plan-openai-strong", "revision": 3, "last_turn": {"turn_id": "t"}}
    assert conv["routes"]["mc"]["route_id"] == "api-xai-low"                 # the stored conversation is not touched
    route = for_owner_turn(prod, "mc-conversation", out, "hello there")
    assert route["route_id"] == "plan-openai-strong" and route["failover"]["route_id"] == "api-openai-strong"


def test_new_conversation_inherits_owner_voice(prod):
    route = for_owner_turn(prod, "cos-conversation", owner_conversation(None, "cos", "xai", prod), "what's next?")
    assert route["provider"] == "minimoi-gateway" and family(route) == "xai"


# ── settings store ────────────────────────────────────────────────────────────────────────────────────────────────

def test_settings_per_owner_per_partner(tmp_path):
    mc, cos = PartnerSettings(tmp_path, "mc"), PartnerSettings(tmp_path, "cos")
    assert mc.get("robert") == {"voice": None, "revision": 0, "updated_at": None}
    mc.set_voice("robert", "xai", 0)
    assert mc.get("robert")["voice"] == "xai" and mc.get("robert")["revision"] == 1
    assert cos.get("robert")["voice"] is None and mc.get("guest")["voice"] is None
    assert not any("robert" in str(p) for p in tmp_path.rglob("*"))      # owner names are hashed


def test_settings_revision_conflict(tmp_path):
    s = PartnerSettings(tmp_path, "mc")
    s.set_voice("o", "openai", 0)
    with pytest.raises(SettingsConflict) as exc:
        s.set_voice("o", "xai", 0)
    assert exc.value.revision == 1


def test_settings_refuse_unknown(tmp_path):
    with pytest.raises(ValueError):
        PartnerSettings(tmp_path, "mc").set_voice("o", "anthropic", 0)
    with pytest.raises(ValueError):
        PartnerSettings(tmp_path, "german")


def test_settings_lock_serialises_writers(tmp_path):
    s, wins, losses = PartnerSettings(tmp_path, "cos"), [], []

    def write(voice):
        try:
            s.set_voice("o", voice, 0)
            wins.append(voice)
        except SettingsConflict:
            losses.append(voice)
    threads = [threading.Thread(target=write, args=(v,)) for v in ("openai", "xai") * 4]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(wins) == 1 and len(losses) == 7 and s.get("o")["revision"] == 1


# ── menu view ─────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("agent", ["mc", "cos"])
def test_menu_view_same_shape_for_both_twins(prod, agent):
    view = menu_view(prod, agent, {"voice": "xai", "revision": 2})
    assert view["voice"]["selected"] == "xai" and view["revision"] == 2
    assert view["model"]["text"] == "Grok 4.7 · API"
    assert menu_view(prod, agent, {"voice": None, "revision": 0})["model"]["text"] == "GPT-6 Astra · Plan"
    shown = json.dumps({k: v for k, v in view.items() if k != "model"}) + view["model"]["text"]
    assert "api-xai" not in shown and "plan-openai" not in shown and "gateway" not in shown


def test_choose_refuses_unavailable_voice(repo, tmp_path):
    with pytest.raises(VoiceUnavailable):
        choose(PartnerSettings(tmp_path, "cos"), "o", repo, "cos", "openai", 0)
    with pytest.raises(VoiceUnavailable):
        choose(PartnerSettings(tmp_path, "cos"), "o", repo, "cos", "anthropic", 0)


def test_menu_without_routes():
    view = menu_view(None, "cos", {"voice": "xai", "revision": 1})
    assert view["voice"]["selected"] == "xai" and view["model"] is None and view["last_answer"] == ""


# ── last answer ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_nothing_when_the_chosen_model_answered(prod):
    assert line(prod, None) == ""
    assert line(prod, {"route_id": "plan-openai-strong", "provider": "openai", "model": "gpt-6-astra",
                       "served": {"provider": "openai", "model": "gpt-6-astra"}, "served_matches": True}) == ""


def test_depth_pin_is_not_a_fallback(prod):
    turn = {"route_id": "api-xai-strong-light", "provider": "minimoi-gateway", "model": "minimoi-xai-strong-light",
            "served": {"provider": "minimoi-gateway", "model": "minimoi-xai-strong"}, "served_matches": False}
    assert line(prod, turn) == ""


def test_plan_switch(prod):
    turn = {"route_id": "api-openai-strong", "provider": "minimoi-gateway", "model": "minimoi-openai-strong",
            "switched_from": "plan-openai-strong", "switch_signal": "rate_limit",
            "served": {"provider": "minimoi-gateway", "model": "minimoi-openai-strong"}}
    assert line(prod, turn) == "Last answer: GPT-6 Astra (API) (fallback)"


def test_grok_failover_to_claude_is_shown(prod):
    turn = {"route_id": "api-anthropic-strong", "provider": "minimoi-gateway", "model": "minimoi-anthropic-strong",
            "switched_from": "api-xai-strong", "served": None}
    assert line(prod, turn) == "Last answer: Claude Opus 5.5 (API) (fallback)"


def test_gateway_receipt_fallback(prod):
    turn = {"route_id": "gateway-cos-role", "provider": "gateway", "model": "minimoi-cos-agent",
            "served": {"provider": "anthropic", "model": "anthropic/claude-sonnet-4-6", "fallback_position": 2}}
    assert line(prod, turn) == "Last answer: Claude Sonnet 4.6 (fallback)"


def test_runtime_record_of_another_family(prod):
    turn = {"route_id": "plan-openai-strong", "provider": "openai", "model": "gpt-6-astra",
            "served": {"provider": "claude-cli", "model": "claude-opus-5"}, "served_matches": False}
    assert line(prod, turn) == "Last answer: Claude Opus 5 (fallback)"


def test_labels(prod):
    assert label_for(prod, "minimoi-anthropic-strong") == "Claude Opus 5.5 (API)"
    assert label_for(prod, "xai/grok-9-unknown") == "Grok"
    assert label_for(prod, "something") == "another model"
