"""Guildhall on production's own host: its own profile and admission. Dev's behaviour stays as it was."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from domains.cos.guildhall import admit_environment
from minimoi_portal.guild_ui import mc_rooms_bridge
from services.rooms_worker.clients import Records
from services.rooms_worker.transport_profile import PRODUCTION_INTERNAL, admit_runtime, validate_endpoint

ROOT = Path(__file__).resolve().parents[2]
INTERNAL = "http://minimoi-records:18880"
HOST_COS = {"ROOMS_RECORDS_PROFILE": PRODUCTION_INTERNAL, "ROOMS_RECORDS_PRODUCTION": "1", "RECORDS_URL": INTERNAL,
            "ROOMS_SECRETS_DIR": "/run/secrets/guildhall-cos", "ROOMS_JOURNAL_DIR": "/var/lib/guildhall-cos"}


def test_host_workers_reach_records_only_by_the_internal_endpoint():
    assert validate_endpoint(INTERNAL, PRODUCTION_INTERNAL) == INTERNAL
    for url in ("https://minimoi.ai/internal/records", "http://127.0.0.1:18881", "http://evil:18880"):
        with pytest.raises(ValueError):
            validate_endpoint(url, PRODUCTION_INTERNAL)


def test_the_public_production_profile_still_refuses_the_internal_endpoint():
    with pytest.raises(ValueError):
        validate_endpoint(INTERNAL, "production")


def test_cos_worker_is_admitted_without_a_relay_token():
    assert admit_runtime(HOST_COS, kind="cos_worker") == (INTERNAL, PRODUCTION_INTERNAL)


def test_mc_worker_still_needs_its_relay_token():
    with pytest.raises(ValueError, match="MC_RELAY_TOKEN_FILE"):
        admit_runtime(HOST_COS, kind="worker")
    env = {**HOST_COS, "MC_RELAY_TOKEN_FILE": "/run/secrets/guildhall-mc/mc-relay.token"}
    assert admit_runtime(env, kind="worker") == (INTERNAL, PRODUCTION_INTERNAL)


def test_a_connector_can_never_select_the_host_profile():
    env = {**HOST_COS, "ROOMS_CONNECTOR_SECRETS": "/a", "ROOMS_CONNECTOR_STATE": "/b"}
    with pytest.raises(ValueError, match="records_transport_profile_refused"):
        admit_runtime(env, kind="connector")


def test_host_profile_needs_explicit_admission():
    env = {k: v for k, v in HOST_COS.items() if k != "ROOMS_RECORDS_PRODUCTION"}
    with pytest.raises(ValueError, match="records_production_admission_required"):
        admit_runtime(env, kind="cos_worker")


@pytest.mark.parametrize("key,bad", [("ROOMS_SECRETS_DIR", "/run/secrets/rooms"), ("ROOMS_JOURNAL_DIR", "/journal"),
                                     ("ROOMS_JOURNAL_DIR", "relative"), ("ROOMS_SECRETS_DIR", "")])
def test_host_profile_refuses_dev_shaped_or_missing_locators(key, bad):
    with pytest.raises(ValueError):
        admit_runtime({**HOST_COS, key: bad}, kind="cos_worker")


def test_host_profile_refuses_secrets_and_journal_in_one_folder():
    with pytest.raises(ValueError, match="overlap"):
        admit_runtime({**HOST_COS, "ROOMS_JOURNAL_DIR": HOST_COS["ROOMS_SECRETS_DIR"]}, kind="cos_worker")


def test_dev_worker_defaults_are_unchanged():
    assert admit_runtime({"RECORDS_URL": INTERNAL}, kind="worker") == (INTERNAL, "dev")
    assert admit_runtime({"RECORDS_URL": INTERNAL}, kind="cos_worker") == (INTERNAL, "dev")
    assert Records(INTERNAL, lambda: "t").base == INTERNAL


def test_records_client_takes_the_host_profile():
    assert Records(INTERNAL, lambda: "t", profile=PRODUCTION_INTERNAL).base == INTERNAL


def test_cos_scheduler_dev_admission_is_unchanged():
    admit_environment({"COS_STORE_ENVIRONMENT": "dev"}, verifier=None)


@pytest.mark.parametrize("env", [{}, {"COS_STORE_ENVIRONMENT": "staging"},
                                 {"COS_STORE_ENVIRONMENT": "prod"},
                                 {"COS_STORE_ENVIRONMENT": "prod", "MINIMOI_ROLE": "production"},
                                 {"COS_STORE_ENVIRONMENT": "prod", "MINIMOI_GUILDHALL_COS_PRODUCTION": "1"}])
def test_cos_scheduler_refuses_without_explicit_admission(env):
    with pytest.raises(RuntimeError):
        admit_environment(env, verifier=object())


def test_cos_scheduler_on_production_requires_the_records_verifier():
    env = {"COS_STORE_ENVIRONMENT": "prod", "MINIMOI_ROLE": "production", "MINIMOI_GUILDHALL_COS_PRODUCTION": "1"}
    with pytest.raises(RuntimeError, match="verifier"):
        admit_environment(env, verifier=None)
    admit_environment(env, verifier=object())


def _services(kind="openclaw", model="openclaw/mc-chat", ledger=object()):
    return SimpleNamespace(mc=SimpleNamespace(kind=kind, chat_model=model), turn_ledger=ledger)


@pytest.mark.parametrize("environment,environ,code", [
    ("prod", {"MINIMOI_MC_ROOMS": "1"}, "mc_rooms_production_admission_required"),
    ("prod", {"MINIMOI_MC_ROOMS": "1", "MINIMOI_MC_ROOMS_PRODUCTION": "1"}, "mc_rooms_production_admission_required"),
    ("prod", {"MINIMOI_MC_ROOMS": "1", "MINIMOI_ROLE": "production"}, "mc_rooms_production_admission_required"),
    ("other", {"MINIMOI_MC_ROOMS": "1"}, "mc_rooms_dev_contract_required"),
])
def test_mc_rooms_bridge_refuses_production_without_admission(environment, environ, code):
    with pytest.raises(ValueError, match=code):
        mc_rooms_bridge.attach(None, _services(), environ,
                               {"environment": environment, "principal": "robert"}, craft_prefix="/x")


def test_mc_rooms_bridge_on_production_keeps_every_other_contract():
    environ = {"MINIMOI_MC_ROOMS": "1", "MINIMOI_MC_ROOMS_PRODUCTION": "1", "MINIMOI_ROLE": "production"}
    with pytest.raises(ValueError, match="mc_rooms_dev_contract_required"):
        mc_rooms_bridge.attach(None, _services(kind="other"), environ,
                               {"environment": "prod", "principal": "robert"}, craft_prefix="/x")
    with pytest.raises(ValueError, match="mc_rooms_dev_contract_required"):
        mc_rooms_bridge.attach(None, _services(), environ,
                               {"environment": "prod", "principal": "someone"}, craft_prefix="/x")


def test_mc_rooms_stay_off_without_the_switch():
    assert mc_rooms_bridge.attach(None, _services(), {}, {"environment": "prod", "principal": "robert"},
                                  craft_prefix="/x") is None


def _compose(name):
    return yaml.safe_load((ROOT / name).read_text())


def test_worker_project_is_opt_in_and_never_calls_itself_dev():
    text = (ROOT / "docker-compose.guildhall-production.yml").read_text()
    assert "minimoi-staging" not in text and "ROOMS_RECORDS_PROFILE: dev" not in text
    value = _compose("docker-compose.guildhall-production.yml")
    assert value["name"] == "minimoi-guildhall-production"
    assert set(value["services"]) == {"guildhall-cos", "guildhall-mc"}
    for service in value["services"].values():
        assert service["profiles"] == ["guildhall-production"]
        assert service["read_only"] is True and service["cap_drop"] == ["ALL"]
        assert service["environment"]["ROOMS_RECORDS_PROFILE"] == PRODUCTION_INTERNAL
        assert service["environment"]["ROOMS_RECORDS_PRODUCTION"] == "1"
        assert all(v["bind"]["create_host_path"] is False for v in service["volumes"])
    assert all(n["external"] is True for n in value["networks"].values())


def test_each_compose_worker_passes_its_own_admission():
    services = _compose("docker-compose.guildhall-production.yml")["services"]
    cos, mc = services["guildhall-cos"]["environment"], services["guildhall-mc"]["environment"]
    assert admit_runtime(cos, kind="cos_worker") == (INTERNAL, PRODUCTION_INTERNAL)
    assert admit_runtime(mc, kind="worker") == (INTERNAL, PRODUCTION_INTERNAL)
    assert cos["ROOMS_SECRETS_DIR"] != mc["ROOMS_SECRETS_DIR"] and cos["ROOMS_JOURNAL_DIR"] != mc["ROOMS_JOURNAL_DIR"]


def test_main_layer_adds_only_the_scheduler_and_portal_and_never_says_dev():
    value = _compose("docker-compose.guildhall-production-main.yml")
    assert set(value["services"]) == {"cos-scheduler", "portal"}
    scheduler = value["services"]["cos-scheduler"]
    assert scheduler["environment"]["MINIMOI_GUILDHALL_COS_PRODUCTION"] == "1"
    assert "COS_STORE_ENVIRONMENT" not in scheduler["environment"]
    assert "records-production" in scheduler["networks"]
    assert value["services"]["portal"]["environment"]["MINIMOI_MC_ROOMS_PRODUCTION"] == "1"


def test_records_network_may_hold_the_guildhall_members_and_no_stranger():
    text = (ROOT / "scripts/rooms/deployment.py").read_text()
    assert ("<={'minimoi-records','minimoi-portal','minimoi-cos-scheduler','minimoi-guildhall-cos',"
            "'minimoi-guildhall-mc'}") in text


def test_mc_worker_portal_port_is_the_production_tools_listener():
    """The rooms bridge is attached to MC's tools listener (mc_tools_wiring), not the portal's PORT=5001 app."""
    from services.rooms_worker.portal_mc import PortalMC
    assert PortalMC("/x").base == "http://portal:5011"
    assert "MINIMOI_MC_TOOLS_PORT=5011" in (ROOT / "docker-compose.prod.yml").read_text()
    wiring = (ROOT / "minimoi_portal/guild_ui/mc_tools_wiring.py").read_text()
    assert "attach_rooms(listener, services" in wiring


def test_production_has_the_discussion_route_each_guildhall_teammate_asks_for():
    routes = {m["model_name"] for m in _compose("services/model_gateway/litellm.prod.yaml")["model_list"]}
    assert {"minimoi-mc-agent", "minimoi-cos-agent"} <= routes


def test_the_task_service_needs_explicit_admission_on_production(monkeypatch, tmp_path):
    from services.partnership_tasks import http_api
    monkeypatch.setenv("PARTNERSHIP_ROOT", str(tmp_path))
    monkeypatch.setenv("PARTNERSHIP_ENVIRONMENT", "prod")
    monkeypatch.delenv("PARTNERSHIP_PRODUCTION", raising=False)
    monkeypatch.delenv("MINIMOI_ROLE", raising=False)
    with pytest.raises(SystemExit, match="explicit admission"):
        http_api.main([])
    monkeypatch.setenv("MINIMOI_ROLE", "production")
    monkeypatch.setenv("PARTNERSHIP_PRODUCTION", "1")
    with pytest.raises(SystemExit, match="no task store"):        # admitted; it then still refuses to create a store
        http_api.main([])
    monkeypatch.setenv("PARTNERSHIP_ENVIRONMENT", "other")
    with pytest.raises(SystemExit, match="runs only on"):
        http_api.main([])


def test_each_new_production_service_has_the_settings_its_code_requires():
    services = _compose("docker-compose.minimoi-1-2-production.yml")["services"]
    board = services["cos-board"]["environment"]
    assert {"BOARD_API_PROFILES_FILE", "BOARD_API_CURSOR_KEY_FILE", "DATABASE_URL", "BOARD_API_OWNER",
            "MINIMOI_BOARD_ENV"} <= set(board)
    tasks = services["partnership-tasks"]
    assert tasks["environment"]["PARTNERSHIP_PRODUCTION"] == "1" and tasks["environment"]["MINIMOI_ROLE"] == "production"
    assert "http_api" in " ".join(tasks["command"])


def test_production_turns_on_the_cos_write_guard_and_stores_where_backups_look():
    services = _compose("docker-compose.minimoi-1-2-production.yml")["services"]
    assert services["portal"]["environment"]["MINIMOI_COS_WRITE_GUARD"] == "on"
    assert any(v["source"] == "/opt/minimoi/cos/store" for v in services["cos-scheduler"]["volumes"])
    assert "/opt/minimoi/cos/store/" in (ROOT / "scripts/backup_local.sh").read_text()
