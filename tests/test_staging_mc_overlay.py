"""Master Craftsman stage 1a on staging, and production left exactly as it is.

* Production: the rendered docker-compose.prod.yml, the CoS-only
  openclaw.json, apply-config.sh, the image's ENTRYPOINT/CMD and the
  production healthcheck are byte-identical to origin/main (spec §1.4, 9q).
* Staging: docker-compose.staging-mc.yml changes only cos-agent-a's start,
  health, memory and MC key; lib.sh adds it only with state/mc.agent = on;
  state/mc.mode drives the portal's MINIMOI_GUILD_MC; MC keys stay out of the
  shared .env; the gateway's MC route never has an empty provider key.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.ci.classify_release import classify
from test_staging_environment import (  # noqa: F401  (shell is a fixture)
    FAKE_DOCKER, GATEWAY_PROD, GATEWAY_STAGING, PROD, SCRIPTS, STAGING, _compose_config, _load, _staging_world, _up,
    _write_exe, shell)

REPO = Path(__file__).resolve().parent.parent
MC_OVERLAY = REPO / "docker-compose.staging-mc.yml"
AGENT_DIR = REPO / "docker" / "cos-agent-a"


def _main(path: str) -> str | None:
    shown = subprocess.run(["git", "-C", str(REPO), "show", f"origin/main:{path}"], capture_output=True, text=True)
    return shown.stdout if shown.returncode == 0 else None


# ── production is unchanged ───────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "docker/cos-agent-a/openclaw.json",
    "docker/cos-agent-a/apply-config.sh",
    "docker/cos-agent-a/AGENTS.md", "docker/cos-agent-a/IDENTITY.md", "docker/cos-agent-a/SOUL.md",
    "docker/cos-agent-a/MEMORY.md",
    "docker-compose.prod.yml",
    "services/model_gateway/litellm.prod.yaml",
    "services/model_gateway/litellm.yaml",
])
def test_production_files_are_byte_identical_to_main(path):
    main = _main(path)
    if main is None:
        pytest.skip("origin/main is not available in this checkout")
    assert (REPO / path).read_text() == main, path


def test_production_render_is_identical_to_main(tmp_path):
    main = _main("docker-compose.prod.yml")
    if main is None:
        pytest.skip("origin/main is not available in this checkout")
    base = tmp_path / "docker-compose.prod.yml"
    base.write_text(main)
    assert _compose_config(PROD) == _compose_config(base)


def test_production_start_path_entrypoint_and_healthcheck_are_as_main():
    """Check 9q: the image gains files but not a new start path."""
    main = _main("docker/Dockerfile.cos-agent-a")
    if main is None:
        pytest.skip("origin/main is not available in this checkout")
    ours = (REPO / "docker" / "Dockerfile.cos-agent-a").read_text()
    for line in main.splitlines():
        if line.startswith(("FROM ", "ENTRYPOINT ", "CMD ", "ENV ")):
            assert line in ours.splitlines(), line
    assert [l for l in ours.splitlines() if l.startswith(("ENTRYPOINT ", "CMD "))] == \
        [l for l in main.splitlines() if l.startswith(("ENTRYPOINT ", "CMD "))]
    agent = _load(PROD)["services"]["cos-agent-a"]
    assert "entrypoint" not in agent and "command" not in agent
    assert agent["healthcheck"]["test"][3].startswith("fetch('http://127.0.0.1:18789/healthz')")
    assert "minimoi-mc" not in agent["healthcheck"]["test"][3]
    for text in (PROD.read_text(), (REPO / "docker-compose.yml").read_text()):
        assert "start-with-mc" not in text and "MC_MODEL_GATEWAY_KEY" not in text and "MINIMOI_GUILD_MC" not in text


def test_the_mc_overlay_is_staging_only_for_the_release_classifier():
    assert classify(["docker-compose.staging-mc.yml"]) == ("documents", ())
    assert classify(["docker/cos-agent-a/start-with-mc.sh"]) == ("domain", ("cos-agent-a",))


# ── the staging MC overlay ────────────────────────────────────────────────────

def test_overlay_changes_only_cos_agent_a_start_health_memory_and_mc_key():
    data = _load(MC_OVERLAY)
    assert set(data) == {"services"}
    assert set(data["services"]) == {"cos-agent-a"}
    agent = data["services"]["cos-agent-a"]
    assert agent["entrypoint"] == ["tini", "-g", "--", "/opt/minimoi/cos-agent-a/start-with-mc.sh"]
    env = dict(item.split("=", 1) for item in agent["environment"])
    assert env == {"MINIMOI_MC_AGENT": "on",
                   "MC_MODEL_GATEWAY_KEY": "${MC_MODEL_GATEWAY_KEY:-mc-placeholder-not-a-key}"}
    # N8 = no: CoS keeps its own (master) key; the overlay never touches it.
    assert "MINIMOI_MODEL_GATEWAY_KEY" not in env
    assert agent["mem_limit"] == "1536m"
    assert agent["stop_grace_period"] == "30s"
    check = agent["healthcheck"]
    assert "/tmp/minimoi-mc/serving" in check["test"][3] and "/healthz" in check["test"][3]
    assert int(check["start_period"].rstrip("s")) >= 90
    for forbidden in ("ports", "env_file", "volumes", "image", "privileged", "cap_add"):
        assert forbidden not in agent, forbidden
    text = MC_OVERLAY.read_text()
    for name in ("ANTHROPIC_API_KEY=", "XAI_API_KEY", "OPENAI_API_KEY", "MINIMOI_ROOT"):
        assert name not in text, name


def test_staging_render_with_the_mc_overlay():
    root = "/Users/someone/minimoi-staging"
    rendered = yaml.safe_load(_compose_config(
        PROD, STAGING, MC_OVERLAY, env_extra={"MINIMOI_ROOT": root, "MINIMOI_IMAGE_TAG": "abc1234",
                                              "MINIMOI_GUILD_MC": "openclaw"}))
    agent = rendered["services"]["cos-agent-a"]
    assert agent["entrypoint"] == ["tini", "-g", "--", "/opt/minimoi/cos-agent-a/start-with-mc.sh"]
    assert agent["environment"]["MC_MODEL_GATEWAY_KEY"] == "mc-placeholder-not-a-key"   # 1a: no MC key
    assert "MINIMOI_MODEL_GATEWAY_KEY" in agent["environment"]
    assert agent["environment"]["MALLOC_ARENA_MAX"] == "2"
    assert "NODE_OPTIONS" not in agent["environment"]
    assert agent["mem_limit"] in ("1536m", 1536 * 1024 * 1024, str(1536 * 1024 * 1024))
    assert [(p["host_ip"], p["published"]) for p in agent["ports"]] == [("127.0.0.1", "18790")]
    assert "openclaw-agents" in agent["networks"]["default"]["aliases"]
    assert rendered["services"]["portal"]["environment"]["MINIMOI_GUILD_MC"] == "openclaw"
    gateway = rendered["services"]["model-gateway"]["environment"]
    assert gateway["MC_ANTHROPIC_API_KEY"] == "mc-anthropic-placeholder-not-a-key"


def test_staging_without_the_overlay_keeps_the_cos_only_start():
    staging = _load(STAGING)["services"]
    assert "entrypoint" not in staging["cos-agent-a"]
    assert staging["portal"]["environment"][-1] == "MINIMOI_GUILD_MC=${MINIMOI_GUILD_MC:-off}"


def test_gateway_mc_route_uses_mc_provider_key_never_empty_and_no_fallback():
    staging = _load(GATEWAY_STAGING)
    routes = {m["model_name"]: m for m in staging["model_list"]}
    mc = routes["minimoi-mc-agent"]
    assert mc["litellm_params"]["model"] == "anthropic/claude-haiku-4-5-20251001"
    assert mc["litellm_params"]["api_key"] == "os.environ/MC_ANTHROPIC_API_KEY"
    assert mc["model_info"]["id"].startswith("mc-")
    assert "fallback_position" not in mc["model_info"]
    fallbacks = staging["router_settings"]["fallbacks"]
    assert all("minimoi-mc-agent" not in str(entry) for entry in fallbacks)
    assert "minimoi-mc-agent" not in {m["model_name"] for m in _load(GATEWAY_PROD)["model_list"]}
    gateway_env = _load(STAGING)["services"]["model-gateway"]["environment"]
    assert gateway_env == ["MC_ANTHROPIC_API_KEY=${MC_ANTHROPIC_API_KEY:-mc-anthropic-placeholder-not-a-key}"]


# ── lib.sh / up.sh switches ───────────────────────────────────────────────────

FAKE_DOCKER_ENV = FAKE_DOCKER.replace(
    'compose) echo "$*" >> "$FAKE/compose.log"; exit 0 ;;',
    'compose) echo "MINIMOI_GUILD_MC=$MINIMOI_GUILD_MC $*" >> "$FAKE/compose.log"; exit 0 ;;')


def _mc_world(tmp_path, *, agent=None, mode=None, overlay=True, mc_env_mode=None):
    env, fake, release = _staging_world(tmp_path)
    _write_exe(Path(env["PATH"].split(":")[0]) / "docker", FAKE_DOCKER_ENV)
    root = Path(env["STAGING_ROOT"])
    if overlay:
        (release / "docker-compose.staging-mc.yml").write_text("services: {}\n")
        subprocess.run(["git", "-C", str(release), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(release), "commit", "-qm", "mc"], check=True)
        sha = subprocess.run(["git", "-C", str(release), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        (root / "RELEASE").write_text(f"sha={sha}\ntag=abc1234\n")
    if agent is not None:
        (root / "state" / "mc.agent").write_text(agent + "\n")
    if mode is not None:
        (root / "state" / "mc.mode").write_text(mode + "\n")
    if mc_env_mode is not None:
        (root / "mc.env").write_text("MC_MODEL_GATEWAY_KEY=x\n")
        (root / "mc.env").chmod(mc_env_mode)
    return env, fake, release


def _compose_lines(fake):
    return (fake / "compose.log").read_text().splitlines()


def test_mc_agent_off_by_default_adds_no_overlay_and_mode_off(tmp_path, shell):
    env, fake, _ = _mc_world(tmp_path)
    result = _up(shell, env)
    assert result.returncode == 0, result.stderr
    line = _compose_lines(fake)[0]
    assert line.startswith("MINIMOI_GUILD_MC=off ")
    assert "docker-compose.staging-mc.yml" not in line
    assert "CoS-only start (#244)" in result.stdout


def test_mc_agent_on_adds_the_overlay_and_mode_reaches_the_portal(tmp_path, shell):
    env, fake, release = _mc_world(tmp_path, agent="on", mode="openclaw")
    result = _up(shell, env)
    assert result.returncode == 0, result.stderr
    line = _compose_lines(fake)[0]
    assert line.startswith("MINIMOI_GUILD_MC=openclaw ")
    assert f"-f {release}/docker-compose.staging.yml -f {release}/docker-compose.staging-mc.yml" in line


def test_mc_agent_on_refuses_a_release_without_the_overlay(tmp_path, shell):
    env, fake, _ = _mc_world(tmp_path, agent="on", overlay=False)
    result = _up(shell, env)
    assert result.returncode != 0 and "docker-compose.staging-mc.yml" in result.stderr
    assert not (fake / "compose.log").exists()


def test_openclaw_mode_needs_the_agent_on(tmp_path, shell):
    env, fake, _ = _mc_world(tmp_path, agent="off", mode="openclaw")
    result = _up(shell, env)
    assert result.returncode != 0 and "mc.agent is not on" in result.stderr
    assert not (fake / "compose.log").exists()


@pytest.mark.parametrize("mode", ["stub", "grok", "off"])
def test_stub_grok_and_off_need_no_agent(tmp_path, shell, mode):
    env, fake, _ = _mc_world(tmp_path, mode=mode)
    result = _up(shell, env)
    assert result.returncode == 0, result.stderr
    assert _compose_lines(fake)[0].startswith(f"MINIMOI_GUILD_MC={mode} ")


def test_an_unknown_mode_is_refused(tmp_path, shell):
    env, fake, _ = _mc_world(tmp_path, mode="claude")
    result = _up(shell, env)
    assert result.returncode != 0 and "use one of: off stub openclaw grok" in result.stderr


def test_mc_env_is_interpolation_only_and_must_be_600(tmp_path, shell):
    env, fake, _ = _mc_world(tmp_path, agent="on", mc_env_mode=0o600)
    assert _up(shell, env).returncode == 0
    line = _compose_lines(fake)[0]
    root = env["STAGING_ROOT"]
    assert f"--env-file {root}/mc.env" in line
    env2, fake2, _ = _mc_world(tmp_path / "second", agent="on", mc_env_mode=0o644)
    result = _up(shell, env2)
    assert result.returncode != 0 and "mc.env must be mode 600" in result.stderr


def test_no_service_loads_mc_env_whole():
    for path in (PROD, STAGING, MC_OVERLAY):
        assert "mc.env" not in "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#"))


def test_build_sh_bakes_the_release_into_the_agent_image_only():
    text = (SCRIPTS / "build.sh").read_text()
    assert '[[ "$service" != cos-agent-a ]] || build_args=(--build-arg "MINIMOI_RELEASE_SHA=$FULL_SHA")' in text
    assert "ARG MINIMOI_RELEASE_SHA=unknown" in (REPO / "docker" / "Dockerfile.cos-agent-a").read_text()


def test_verify_sh_has_the_mc_checks_and_shows_the_cos_failure_marker():
    text = (SCRIPTS / "verify.sh").read_text()
    for check in ("9a ", "9b ", "9f ", "9j ", "9k ", "9m ", "9o ", "9p "):
        assert check in text, check
    assert ".cos-selfcheck-failed" in text and "CoS SELF-CHECK FAILED" in text
    assert "serving-cos-only" in text
