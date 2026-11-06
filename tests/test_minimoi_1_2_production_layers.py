"""Production layers for plan failover and the offered Grok API route. No host, no secret values."""
import json
import re

import yaml

from scripts.operations import minimoi_1_2_prod_setup as setup


def _root():
    from pathlib import Path
    return Path(__file__).resolve().parents[1]


def _compose(name):
    return yaml.safe_load((_root() / name).read_text())


def _text(name):
    return (_root() / name).read_text()


LAYERS = (
    "docker-compose.minimoi-1-2-production.yml",
    "docker-compose.mc-1-2-production.yml",
)


def test_cos_keeps_route_a_and_offers_grok_only_as_api():
    env = _compose(LAYERS[0])["services"]["cos-agent-a"]["environment"]
    assert env["COS_PLAN_MODEL"] == "openai/gpt-6-astra"
    assert env["COS_PLAN_ROUTES"] == "openai/gpt-6-sol,openai/gpt-6-luna"
    assert env["COS_RUNTIME_MODEL_ACCESS"] == "plan"
    assert env["COS_METERED_ROUTES"] == "minimoi-gateway/minimoi-xai-low"
    assert "xai" not in env["COS_FAILOVER_ROUTES"]
    assert env["COS_FAILOVER_ROUTES"].split(",") == [
        "minimoi-gateway/minimoi-openai-strong",
        "minimoi-gateway/minimoi-openai-mid",
        "minimoi-gateway/minimoi-openai-low",
    ]
    # CoS's capped key stays where docker-compose.prod.yml mounts it, the path the code reads by default.
    assert "COS_GATEWAY_KEY_FILE" not in env
    assert "/run/cos-key" in _text("docker-compose.prod.yml")
    assert "fallbacks" not in _text(LAYERS[0])


def test_mc_plan_settings_are_required_and_have_no_compose_default():
    env = _compose(LAYERS[1])["services"]["mc-agent"]["environment"]
    required = {
        "MC_RUNTIME_MODEL_ACCESS": "MINIMOI_MC_RUNTIME_MODEL_ACCESS",
        "MC_PLAN_MODEL": "MINIMOI_MC_PLAN_MODEL",
        "MC_PLAN_ROUTES": "MINIMOI_MC_PLAN_ROUTES",
        "MC_PLAN_AUTH_PROFILE": "MINIMOI_MC_PLAN_AUTH_PROFILE",
        "MC_MODEL_GATEWAY_KEY": "MINIMOI_MC_MODEL_GATEWAY_KEY",
    }
    for key, variable in required.items():
        assert env[key] == f"${{{variable}:?{env[key].split(':?', 1)[1]}"
        assert ":-" not in env[key]
    assert "openai/gpt-6-astra" in env["MC_PLAN_MODEL"]
    assert env["MC_METERED_ROUTES"] == "minimoi-gateway/minimoi-xai-low"
    assert "xai" not in env["MC_FAILOVER_ROUTES"]
    assert "minimoi-gateway/minimoi-openai-strong" in env["MC_FAILOVER_ROUTES"]
    assert "anthropic" not in env["MC_FAILOVER_ROUTES"]


def test_relay_knows_the_plan_targets_and_the_grok_api_route():
    env = _compose(LAYERS[1])["services"]["mc-relay"]["environment"]
    routes = json.loads(env["MC_RELAY_ROUTES"])
    assert routes["plan-openai-strong"] == "openai/gpt-6-astra"
    assert routes["api-openai-strong"] == "minimoi-gateway/minimoi-openai-strong"
    assert routes["api-xai-low"] == "minimoi-gateway/minimoi-xai-low"
    assert all("xai" not in ref for route_id, ref in routes.items() if route_id.startswith("plan-"))
    assert env["MC_RELAY_TOOL_STARTS"] == "/mc-state/mc-tool-starts"
    assert env["MC_RELAY_CHAT_DB"] == "/mc-state/agents/mc-chat/agent/openclaw-agent.sqlite"


def test_layers_create_nothing_and_carry_no_dev_values():
    for name in LAYERS:
        text = _text(name)
        assert "minimoi-staging" not in text
        value = _compose(name)
        for service in value["services"].values():
            for key, item in (service.get("environment") or {}).items():
                assert key != "COS_BACKGROUND_JOBS"
                assert item != "dev" and "minimoi-staging" not in str(item)
        for service in value["services"].values():
            for volume in service.get("volumes") or []:
                if isinstance(volume, dict):
                    assert volume["bind"]["create_host_path"] is False
        for service in value["services"].values():
            image = service.get("image")
            if image:
                assert ":?" in str(image) and ":-" not in str(image)
        if name.endswith("mc-1-2-production.yml"):
            assert text.count("immutable reviewed MC image digest required") == 2


def test_the_relay_routes_are_the_catalogues_plus_the_offered_grok_route():
    """The layer's pinned list can't drift from the route catalogue. Claude's targets stay: the catalogue lists them, and
    they are unoffered there. Only MC_FAILOVER_ROUTES leaves them out."""
    from core.model_routes import load_catalogue, relay_routes
    pinned = json.loads(_compose(LAYERS[1])["services"]["mc-relay"]["environment"]["MC_RELAY_ROUTES"])
    catalogue = load_catalogue()
    grok = next(r for r in catalogue["routes"] if r["route_id"] == "api-xai-low")
    assert pinned == {**relay_routes(catalogue), "api-xai-low": f"{grok['provider']}/{grok['model']}"}
    # The host's catalogue offers Grok to MC (the runbook's one change for it): then the two are the same list.
    grok["offered"] = {**grok["offered"], "mc": "available"}
    assert pinned == relay_routes(catalogue)


def test_grok_is_nobodys_failover_in_the_catalogue_the_relay_reads():
    from core.model_routes import load_catalogue
    assert not [r["route_id"] for r in load_catalogue()["routes"] if r.get("failover") == "api-xai-low"]


def test_action_notices_are_on_by_a_mode_the_code_knows_and_stay_switchable():
    from minimoi_portal.action_waiting.settings import MODES, Settings
    value = _compose(LAYERS[0])["services"]["portal"]["environment"]["MINIMOI_ACTION_NOTICES"]
    assert value == "${MINIMOI_ACTION_NOTICES:-on}"     # `notices off` writes .env; a fixed value would override it
    assert "on" in MODES and Settings.from_environ({"MINIMOI_ACTION_NOTICES": "on"}).mode == "on"
    assert Settings.from_environ({"MINIMOI_ACTION_NOTICES": "1"}).mode == "off"    # the old value: off, silently


def test_voice_starts_are_admitted_in_production_by_what_the_layer_sets():
    from domains.cos import direct_voice
    from minimoi_portal import guild_mounts
    services = _compose(LAYERS[0])["services"]
    portal = {**services["portal"]["environment"], "MINIMOI_GUILD_VOICE_PRODUCTION": "1", "MINIMOI_MC_VOICE": "1",
              "MINIMOI_GUILD_CHAT": "mc", "MC_RUNTIME_URL": guild_mounts.PRODUCTION_MC_RELAY_URL, "MC_RUNTIME_TOKEN": "x"}
    environ = guild_mounts.production_environ(portal)
    assert environ[guild_mounts.DIRECT_VOICE_ADMITTED] == "1" and environ["MINIMOI_MC_VOICE_DIRECT"] == "1"
    del portal[guild_mounts.DIRECT_VOICE_PRODUCTION_FLAG]
    assert guild_mounts.production_environ(portal)[guild_mounts.DIRECT_VOICE_ADMITTED] == "0"
    cos = dict(services["cos-scheduler"]["environment"], MINIMOI_ROLE="production")
    assert direct_voice.may_start(cos)
    del cos[direct_voice.PRODUCTION_SWITCH]
    assert not direct_voice.may_start(cos)


def test_the_layer_leaves_both_voice_providers_available():
    from core.realtime_voice.capabilities import AGENT_CONVERSATION_MODE, providers_for_mode
    for name in LAYERS:
        for service in _compose(name)["services"].values():
            for key in service.get("environment") or {}:
                assert "VOICE" not in key or "PROVIDER" not in key, key     # nothing narrows or chooses the provider
    assert {c.provider for c in providers_for_mode(AGENT_CONVERSATION_MODE, "en-US")} >= {"openai", "xai"}


def test_the_board_database_password_is_not_a_compose_variable():
    text = _text(LAYERS[0])
    assert "MINIMOI_COS_BOARD_DATABASE_URL" not in text
    board = _compose(LAYERS[0])["services"]["cos-board"]
    assert "DATABASE_URL" not in board["environment"]
    mount = next(v for v in board["volumes"] if v["target"] == "/run/board-secrets")
    assert board["environment"]["DATABASE_URL_FILE"] == f"{mount['target']}/db.url" and mount["read_only"] is True
    # The partnership worker already reads its URL from a file (services/partnership_tasks/worker.py).
    assert "DATABASE_URL" not in _compose(LAYERS[0])["services"]["partnership-worker"]["environment"]
    assert f"{setup.SECRETS}/cos-board" in setup.FOLDERS and f"{setup.SECRETS}/partnership/db.url" in setup.BY_OPERATOR


def test_secret_folders_are_on_the_setup_list():
    folders = []
    for name in LAYERS:
        folders.extend(re.findall(r"MINIMOI_1_2_SECRETS:\?[^}]*\}/([a-z0-9-]+)", _text(name)))
    assert folders
    assert all(f"{setup.SECRETS}/{name}" in setup.FOLDERS for name in folders)


def test_voice_keys_come_by_file_from_a_folder_each_service_already_mounts_read_only():
    from scripts.operations import minimoi_1_2_prod_bind as bind
    services = _compose(LAYERS[0])["services"]
    for service, variable, name, folder in (
            ("portal", "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC_FILE", "voice-summary-mc.key", "portal-partnership"),
            ("cos-scheduler", "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS_FILE", "voice-summary-cos.key", "cos-partnership")):
        spec = services[service]
        assert spec["environment"][variable] == f"/run/secrets/partnership/{name}"
        mount = next(v for v in spec["volumes"] if v["target"] == "/run/secrets/partnership")
        assert mount["source"].endswith(f"/{folder}") and mount["read_only"] is True
        assert mount["bind"]["create_host_path"] is False
        assert f"{setup.SECRETS}/{folder}/{name}" in bind.GATEWAY_KEYS      # the operator writes that file, capped
        assert f"{setup.SECRETS}/{folder}/{name}" in setup.BY_OPERATOR
    assert "MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC:" not in _text(LAYERS[0])      # no value through compose


def test_settings_production_removes_anyway_are_not_in_the_layer_and_mc_still_turns_on():
    from minimoi_portal import guild_mounts
    portal = _compose(LAYERS[0])["services"]["portal"]["environment"]
    assert not set(portal) & set(guild_mounts.PRODUCTION_MC_ADMITTED)
    environ = guild_mounts.production_environ({**portal, "MINIMOI_GUILD_CHAT": "mc", "MC_RUNTIME_URL": guild_mounts.PRODUCTION_MC_RELAY_URL,
                                               "MC_RUNTIME_TOKEN": "x"})
    assert environ["MINIMOI_GUILD_MC"] == "openclaw" and environ["MINIMOI_GUILD_MC_TURNS"] == "1" and environ["MINIMOI_GUILD_MC_STREAM"] == "1"


def test_the_owner_subject_has_no_default_and_its_one_form_is_owner_robert():
    from scripts.operations import minimoi_1_2_prod_bind as bind
    tasks = _compose(LAYERS[0])["services"]["partnership-tasks"]["environment"]
    assert tasks["PARTNERSHIP_OWNER_SUBJECT"].startswith("${MINIMOI_PARTNERSHIP_OWNER_SUBJECT:?")
    assert "owner:robert" in _text(LAYERS[0]) and f"owner:{bind.OWNER}" == "owner:robert"
