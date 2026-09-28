"""Master Craftsman stage A on staging (MC spec v0.8, v0.9): MC's own Compose
project, the permanent gateway attachment to mc-net, focus.sh, mc.sh, and
production and CoS left as they are.

Static boundary checks C1, C2 and C6 run here on the rendered Compose; the
running-container checks (C1-C8) are scripts/staging/mc_probe/stage_a.py.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from test_staging_environment import (  # noqa: F401  (shell is a fixture)
    FAKE_DOCKER, PROD, SCRIPTS, STAGING, _compose_config, _load, _staging_world, _up, _write_exe, shell)

REPO = Path(__file__).resolve().parent.parent
MC_FILE = REPO / "docker-compose.mc.yml"
COS_NAMES = {"MINIMOI_MODEL_GATEWAY_KEY", "COS_AGENT_A_GATEWAY_TOKEN", "ANTHROPIC_API_KEY", "XAI_API_KEY",
             "OPENAI_API_KEY", "DATABASE_URL", "LITELLM_MASTER_KEY", "MINIMOI_MODEL_GATEWAY_RECEIPT_KEY"}


def _main(path):
    shown = subprocess.run(["git", "-C", str(REPO), "show", f"origin/main:{path}"], capture_output=True, text=True)
    return shown.stdout if shown.returncode == 0 else None


def _mc_render(env=None):
    return yaml.safe_load(_compose_config(MC_FILE, env_extra={"MINIMOI_IMAGE_TAG": "abc1234",
                                                                "MC_OPENCLAW_GATEWAY_TOKEN": "t", **(env or {})}))


# ── MC's own project: C1, C2 on the render ────────────────────────────────────

def test_mc_project_is_one_isolated_service():
    rendered = _mc_render()
    assert set(rendered["services"]) == {"mc-agent"}
    mc = rendered["services"]["mc-agent"]
    assert mc["image"] == "minimoi-staging/mc-agent:abc1234" and mc["pull_policy"] == "never"
    assert mc["container_name"] == "minimoi-mc-agent"
    assert list(mc["networks"]) == ["mc-net"]
    assert rendered["networks"]["mc-net"] == {"name": "minimoi-staging-mc-net", "external": True}
    assert "ports" not in mc and "env_file" not in mc and "depends_on" not in mc
    assert "privileged" not in mc and "cap_add" not in mc
    assert mc["cap_drop"] == ["ALL"] and mc["security_opt"] == ["no-new-privileges:true"]


def test_c1_only_its_own_volumes():
    rendered = _mc_render()
    mounts = rendered["services"]["mc-agent"]["volumes"]
    assert [(m["type"], m["source"], m["target"]) for m in mounts] == [
        ("volume", "mc-agent-state", "/home/node/.openclaw"), ("volume", "mc-agent-auth", "/home/node/.config/openclaw")]
    assert rendered["volumes"] == {
        "mc-agent-state": {"name": "minimoi-staging-mc-agent-state", "external": True},
        "mc-agent-auth": {"name": "minimoi-staging-mc-agent-auth", "external": True}}
    cos = _load(PROD)["services"]["cos-agent-a"]["volumes"]
    assert not {v.split(":")[0] for v in cos} & {"mc-agent-state", "mc-agent-auth"}


def test_c2_no_cos_credential_and_a_refused_placeholder_key():
    env = _mc_render()["services"]["mc-agent"]["environment"]
    assert not set(env) & COS_NAMES
    assert env["MC_MODEL_GATEWAY_KEY"] == "mc-placeholder-not-a-key"
    assert env["OPENCLAW_GATEWAY_TOKEN"] == "t"
    assert env["MALLOC_ARENA_MAX"] == "2" and "NODE_OPTIONS" not in env
    text = MC_FILE.read_text()
    assert "${MC_OPENCLAW_GATEWAY_TOKEN:?" in text   # MC's own token is required, never defaulted


def test_restart_oom_memory_and_slow_first_start():
    mc = _mc_render()["services"]["mc-agent"]
    assert mc["restart"] == "on-failure:3"
    assert mc["oom_score_adj"] == 1000
    assert int(mc["mem_limit"]) == 1200 * 1024 * 1024
    assert mc["stop_grace_period"] == "30s"
    check = mc["healthcheck"]
    assert "/tmp/minimoi-mc/serving" in check["test"][3]
    assert check["start_period"] in ("10m0s", "600s")


def test_mc_image_uses_cos_s_pinned_openclaw_and_none_of_cos_s_files():
    mc_df = (REPO / "docker" / "Dockerfile.mc-agent").read_text()
    cos_df = (REPO / "docker" / "Dockerfile.cos-agent-a").read_text()
    pin = next(l for l in cos_df.splitlines() if l.startswith("FROM "))
    assert [l for l in mc_df.splitlines() if l.startswith("FROM ")] == [pin]
    assert "docker/cos-agent-a" not in mc_df
    assert 'ENTRYPOINT ["tini", "-g", "--", "/opt/minimoi/mc-agent/start-mc.sh"]' in mc_df


# ── the main stack: CoS untouched, gateway permanently on mc-net (C6) ────────

def test_staging_gateway_is_permanently_on_the_internal_mc_net():
    staging = _load(STAGING)
    assert staging["services"]["model-gateway"]["networks"] == ["default", "mc-net"]
    assert staging["networks"]["mc-net"] == {
        "name": "minimoi-staging-mc-net", "internal": True,
        # No host address: without it MC could reach the VM through the bridge IP (#250 review F1).
        "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}}
    for name, service in staging["services"].items():
        if name != "model-gateway":
            assert "mc-net" not in (service.get("networks") or []), name


def _staging_render(files):
    return yaml.safe_load(_compose_config(*files, env_extra={"MINIMOI_ROOT": "/Users/x/minimoi-staging",
                                                             "MINIMOI_IMAGE_TAG": "abc1234"}))


def test_c6_cos_render_is_identical_to_main_and_only_the_gateway_gains_mc_net(tmp_path):
    main_staging = _main("docker-compose.staging.yml")
    main_prod = _main("docker-compose.prod.yml")
    if main_staging is None or main_prod is None:
        pytest.skip("origin/main is not available")
    (tmp_path / "docker-compose.prod.yml").write_text(main_prod)
    (tmp_path / "docker-compose.staging.yml").write_text(main_staging)
    before = _staging_render([tmp_path / "docker-compose.prod.yml", tmp_path / "docker-compose.staging.yml"])
    after = _staging_render([PROD, STAGING])
    for name in before["services"]:
        if name == "model-gateway":
            continue
        assert after["services"][name] == before["services"][name], name
    gw_before, gw_after = before["services"]["model-gateway"], after["services"]["model-gateway"]
    assert set(gw_after["networks"]) == {"default", "mc-net"}
    gw_after = {k: v for k, v in gw_after.items() if k != "networks"}
    gw_before = {k: v for k, v in gw_before.items() if k != "networks"}
    assert gw_after == gw_before


def test_production_compose_and_cos_files_are_byte_identical_to_main():
    for path in ("docker-compose.prod.yml", "docker-compose.yml", "docker/Dockerfile.cos-agent-a",
                 "docker/cos-agent-a/openclaw.json", "docker/cos-agent-a/apply-config.sh",
                 "services/model_gateway/litellm.prod.yaml", "services/model_gateway/litellm.staging.yaml",
                 ".github/workflows/deploy.yml", "scripts/operations/deploy_scoped_release.sh",
                 "scripts/staging/build.sh"):
        main = _main(path)
        if main is None:
            pytest.skip("origin/main is not available")
        assert (REPO / path).read_text() == main, path


# ── focus.sh ──────────────────────────────────────────────────────────────────

def _focus(shell, env, *args):
    return subprocess.run([shell, str(SCRIPTS / "focus.sh"), *args], env=env, capture_output=True, text=True)


def test_focus_mc_stops_the_rest_and_persists_the_set(tmp_path, shell):
    env, fake, _ = _staging_world(tmp_path, bots_on=True, tokens=True)
    root = Path(env["STAGING_ROOT"])
    result = _focus(shell, env, "mc")
    assert result.returncode == 0, result.stderr
    assert (root / "state" / "focus").read_text().strip() == "mc"
    stopped = (root / "state" / "focus.stopped").read_text().split()
    assert stopped == ["cos-agent-a", "curator", "german", "portuguese", "cos-scheduler", "system-bot", "cos-bot"]
    log = (fake / "compose.log").read_text()
    assert "--profile bots stop cos-agent-a curator german portuguese cos-scheduler system-bot cos-bot" in log
    assert " rm" not in log and "down -v" not in log and "--volumes" not in log


def test_focus_mc_plus_cos_keeps_cos_agent_a_and_its_scheduler(tmp_path, shell):
    env, fake, _ = _staging_world(tmp_path)
    assert _focus(shell, env, "mc+cos").returncode == 0
    stopped = (Path(env["STAGING_ROOT"]) / "state" / "focus.stopped").read_text().split()
    assert stopped == ["curator", "german", "portuguese", "system-bot", "cos-bot"]


def test_up_respects_the_focus_set_and_never_starts_portal_dependencies(tmp_path, shell):
    env, fake, _ = _staging_world(tmp_path)
    assert _focus(shell, env, "mc").returncode == 0
    (fake / "compose.log").write_text("")
    result = _up(shell, env)
    assert result.returncode == 0, result.stderr
    log = (fake / "compose.log").read_text()
    assert "up -d --no-build --no-deps postgres model-gateway portal" in log
    assert "up -d --no-build --remove-orphans" not in log
    refused = _up(shell, env, "curator")
    assert refused.returncode != 0 and "kept stopped by focus 'mc'" in refused.stderr


def test_focus_all_clears_the_set_and_brings_everything_back(tmp_path, shell):
    env, fake, _ = _staging_world(tmp_path)
    root = Path(env["STAGING_ROOT"])
    assert _focus(shell, env, "mc").returncode == 0
    (fake / "compose.log").write_text("")
    result = _focus(shell, env, "all")
    assert result.returncode == 0, result.stderr
    assert not (root / "state" / "focus").exists() and not (root / "state" / "focus.stopped").exists()
    assert "up -d --no-build --remove-orphans" in (fake / "compose.log").read_text()


def test_focus_refuses_unknown_sets_and_never_touches_mc(tmp_path, shell):
    env, fake, _ = _staging_world(tmp_path)
    assert _focus(shell, env, "everything").returncode != 0
    text = (SCRIPTS / "focus.sh").read_text()
    assert "minimoi-staging-mc" not in text and "docker-compose.mc.yml" not in text


def test_verify_reports_focus_stopped_services_as_off_not_failures():
    text = (SCRIPTS / "verify.sh").read_text()
    assert 'pass "$name off (focus: $FOCUS)"' in text
    assert "docker exec minimoi-portal python" in text          # the health probe no longer needs cos-scheduler
    assert 'pass "$port off (focus: $FOCUS)"' in text
    assert "runs although focus" in text


# ── mc.sh ─────────────────────────────────────────────────────────────────────

FAKE_DOCKER_MC = FAKE_DOCKER.replace(
    'compose) echo "$*" >> "$FAKE/compose.log"; exit 0 ;;',
    'compose) echo "$*" >> "$FAKE/compose.log"; exit 0 ;;\n  build) echo "$*" >> "$FAKE/build.log"; exit 0 ;;')


def _mc_world(tmp_path, *, enabled=True, mc_env=None, dot_env_extra=""):
    env, fake, release = _staging_world(tmp_path)
    _write_exe(Path(env["PATH"].split(":")[0]) / "docker", FAKE_DOCKER_MC)
    root = Path(env["STAGING_ROOT"])
    (release / "docker-compose.mc.yml").write_text("services: {}\n")
    subprocess.run(["git", "-C", str(release), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(release), "commit", "-qm", "mc"], check=True)
    sha = subprocess.run(["git", "-C", str(release), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (root / "RELEASE").write_text(f"sha={sha}\ntag=abc1234\n")
    with (root / ".env").open("a") as f:
        f.write("MINIMOI_MODEL_GATEWAY_KEY='cos-master-key'\nCOS_AGENT_A_GATEWAY_TOKEN='cos-token'\n" + dot_env_extra)
    if enabled:
        (root / "state" / "mc.enabled").touch()
    if mc_env is not None:
        (root / "mc.env").write_text(mc_env)
        (root / "mc.env").chmod(0o600)
    return env, fake, root


def _mc(shell, env, *args):
    return subprocess.run([shell, str(SCRIPTS / "mc.sh"), *args], env={**env, "MC_WAIT_S": "5"},
                          capture_output=True, text=True)


def test_mc_up_uses_its_own_project_and_only_mc_env(tmp_path, shell):
    env, fake, root = _mc_world(tmp_path, mc_env="MC_OPENCLAW_GATEWAY_TOKEN=mc-token\n")
    result = _mc(shell, env, "up")
    assert result.returncode == 0, result.stderr
    line = (fake / "compose.log").read_text().strip()
    assert line.startswith("compose -p minimoi-staging-mc ")
    assert f"--env-file {root}/mc.env" in line and f"--env-file {root}/.env" not in line
    assert line.endswith("docker-compose.mc.yml up -d --no-build")
    for secret in ("mc-token", "cos-master-key", "cos-token"):
        assert secret not in result.stdout + result.stderr


def test_mc_up_needs_enablement_and_its_own_token(tmp_path, shell):
    env, fake, _ = _mc_world(tmp_path, enabled=False, mc_env="MC_OPENCLAW_GATEWAY_TOKEN=mc-token\n")
    result = _mc(shell, env, "up")
    assert result.returncode != 0 and "not enabled" in result.stderr
    env, fake, _ = _mc_world(tmp_path / "b", mc_env="")
    result = _mc(shell, env, "up")
    assert result.returncode != 0 and "MC_OPENCLAW_GATEWAY_TOKEN is missing" in result.stderr
    assert not (fake / "compose.log").exists()


@pytest.mark.parametrize("mc_env,needle", [
    ("MC_OPENCLAW_GATEWAY_TOKEN=cos-token\n", "MC_OPENCLAW_GATEWAY_TOKEN equals one of CoS's"),
    ("MC_OPENCLAW_GATEWAY_TOKEN=mc-token\nMC_MODEL_GATEWAY_KEY=cos-master-key\n", "MC_MODEL_GATEWAY_KEY equals one of CoS's"),
])
def test_mc_up_refuses_cos_credentials_without_printing_them(tmp_path, shell, mc_env, needle):
    env, fake, _ = _mc_world(tmp_path, mc_env=mc_env)
    result = _mc(shell, env, "up")
    assert result.returncode != 0 and needle in result.stderr
    assert "cos-master-key" not in result.stderr and "cos-token" not in result.stderr.replace("CoS's", "")
    assert not (fake / "compose.log").exists()


def test_mc_up_refuses_an_mc_secret_in_the_shared_env(tmp_path, shell):
    env, fake, _ = _mc_world(tmp_path, mc_env="MC_OPENCLAW_GATEWAY_TOKEN=mc-token\n",
                             dot_env_extra="MC_MODEL_GATEWAY_KEY='x'\n")
    result = _mc(shell, env, "up")
    assert result.returncode != 0 and "an MC secret name is in .env" in result.stderr


def test_mc_token_writes_mc_env_600_and_prints_nothing(tmp_path, shell):
    env, fake, root = _mc_world(tmp_path)
    result = _mc(shell, env, "token")
    assert result.returncode == 0, result.stderr
    content = (root / "mc.env").read_text()
    token = content.split("=", 1)[1].strip()
    assert len(token) == 64 and token not in result.stdout + result.stderr
    assert oct((root / "mc.env").stat().st_mode & 0o777) == "0o600"
    assert _mc(shell, env, "token").returncode == 0
    assert (root / "mc.env").read_text() == content          # never replaced


def test_mc_build_bakes_the_release_and_never_removes_volumes(tmp_path, shell):
    env, fake, root = _mc_world(tmp_path)
    result = _mc(shell, env, "build")
    assert result.returncode == 0, result.stderr
    build = (fake / "build.log").read_text()
    assert "-t minimoi-staging/mc-agent:abc1234" in build and "MINIMOI_RELEASE_SHA=" in build
    assert "docker/Dockerfile.mc-agent" in build
    text = (SCRIPTS / "mc.sh").read_text()
    assert "never removed by a script" in text
    assert "minimoi-staging\" " not in text   # never addresses the main project
