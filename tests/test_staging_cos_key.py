"""CoS's own capped gateway key on staging (cos.sh key; Robert, September 28
2026: "Seems like we should do this for CoS as well"). Runs against a fake
docker: no real key is made. The throwaway proof with a real gateway is
scripts/staging/mc_probe/stage_c.py ("cos.sh's key" checks)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from test_staging_environment import (  # noqa: F401  (shell is a fixture)
    PROD, SCRIPTS, STAGING, _compose_config, _git, _load, _staging_world, _write_exe, shell)

REPO = Path(__file__).resolve().parent.parent
OVERLAY = REPO / "docker-compose.staging-cos-key.yml"

FAKE_DOCKER_COS = r"""#!/bin/sh
printf '%s\n' "$*" >> "$FAKE/argv.log"
case "$*" in
  "inspect -f {{.State.Running}} "*) echo true; exit 0 ;;
  "inspect -f {{if .State.Health}}"*) echo healthy; exit 0 ;;
  "inspect "*) exit 1 ;;
  "exec -i -e COS_OLD_KEY -e COS_CAP minimoi-model-gateway python -")
    cat > "$FAKE/keygen.stdin"
    printf 'cap=%s old=%s\n' "$COS_CAP" "$COS_OLD_KEY" > "$FAKE/keygen.env"
    printf '%s\nc0de\n' "${FAKE_NEW_KEY:-sk-fake-cos-key-0123456789}"; exit 0 ;;
  compose*) exit 0 ;;
esac
exit 0
"""


def _world(tmp_path, *, keys_on=True, cos_env=None):
    env, fake, release = _staging_world(tmp_path)
    _write_exe(Path(env["PATH"].split(":")[0]) / "docker", FAKE_DOCKER_COS)
    root = Path(env["STAGING_ROOT"])
    for name in ("docker-compose.staging-keys.yml", "docker-compose.staging-cos-key.yml"):
        (release / name).write_text("services: {}\n")
    _git(release, "add", "-A")
    _git(release, "commit", "-qm", "c")
    sha = _git(release, "rev-parse", "HEAD").stdout.strip()
    (root / "RELEASE").write_text(f"sha={sha}\ntag=abc1234\n")
    with (root / ".env").open("a") as f:
        f.write("MINIMOI_MODEL_GATEWAY_KEY='cos-master-key'\nCOS_AGENT_A_GATEWAY_TOKEN='cos-token'\n")
    (root / "mc.env").write_text("MC_MODEL_GATEWAY_KEY=sk-mc-key-999\n")
    (root / "mc.env").chmod(0o600)
    if keys_on:
        (root / "state" / "gateway.keys").write_text("on\n")
        (root / "gateway.env").write_text("LITELLM_DATABASE_URL=postgresql://litellm_keys:x@postgres:5432/litellm_keys\n")
        (root / "gateway.env").chmod(0o600)
    if cos_env is not None:
        (root / "cos.env").write_text(cos_env)
        (root / "cos.env").chmod(0o600)
    return env, fake, root


def _cos(shell, env, *args, stdin="", extra=None):
    return subprocess.run([shell, str(SCRIPTS / "cos.sh"), *args], env={**env, **(extra or {})}, input=stdin,
                          capture_output=True, text=True)


def _compose_lines(fake):
    return [l for l in (fake / "argv.log").read_text().splitlines() if l.startswith("compose")]


# ── the key's scope: CoS's routes, chat and responses, monthly ───────────────

def test_cos_key_scope_is_its_four_routes_chat_and_responses_monthly():
    out = subprocess.run(["bash", "-c", f'source "{SCRIPTS}/mc_keys.sh"; cos_keygen_py'], capture_output=True, text=True).stdout
    assert '"models": ["minimoi-cos-agent", "minimoi-cos-agent-xai-fast", "minimoi-cos-web-search", "minimoi-cos-agent-anthropic"]' in out
    assert '"allowed_routes": ["/v1/chat/completions", "/chat/completions", "/v1/responses", "/responses"]' in out
    assert '"budget_duration": "30d"' in out and '"rpm_limit": 60' in out and '"key_alias": "cos-agent-"' in out
    assert '"role": "guild.cos"' in out and 'os.environ["COS_CAP"]' in out and 'os.environ.get("COS_OLD_KEY", "")' in out
    assert "minimoi-mc-agent" not in out and "/key/info" not in out
    mc = subprocess.run(["bash", "-c", f'source "{SCRIPTS}/mc_keys.sh"; mc_keygen_py'], capture_output=True, text=True).stdout
    assert '"models": ["minimoi-mc-agent"]' in mc and 'os.environ["MC_CAP"]' in mc and '"key_alias": "mc-agent-"' in mc


def test_the_overlay_swaps_only_agent_as_gateway_key():
    data = _load(OVERLAY)
    assert data == {"services": {"cos-agent-a": {"environment": [
        "MINIMOI_MODEL_GATEWAY_KEY=${COS_MODEL_GATEWAY_KEY:?set by scripts/staging/cos.sh key (cos.env)}"]}}}
    extra = {"MINIMOI_ROOT": "/Users/x/minimoi-staging", "MINIMOI_IMAGE_TAG": "abc1234", "COS_MODEL_GATEWAY_KEY": "sk-cos-own"}
    with_key = yaml.safe_load(_compose_config(PROD, STAGING, OVERLAY, env_extra=extra))["services"]
    without = yaml.safe_load(_compose_config(PROD, STAGING, env_extra=extra))["services"]
    assert with_key["cos-agent-a"]["environment"]["MINIMOI_MODEL_GATEWAY_KEY"] == "sk-cos-own"
    assert with_key["model-gateway"]["environment"]["LITELLM_MASTER_KEY"] == without["model-gateway"]["environment"]["LITELLM_MASTER_KEY"]
    for name in without:
        if name != "cos-agent-a":
            assert with_key[name] == without[name], name
    a, b = dict(with_key["cos-agent-a"]), dict(without["cos-agent-a"])
    a["environment"] = {k: v for k, v in a["environment"].items() if k != "MINIMOI_MODEL_GATEWAY_KEY"}
    b["environment"] = {k: v for k, v in b["environment"].items() if k != "MINIMOI_MODEL_GATEWAY_KEY"}
    assert a == b


# ── cos.sh key ────────────────────────────────────────────────────────────────

def test_key_asks_for_the_cap_writes_cos_env_unprinted_and_recreates_only_agent_a(tmp_path, shell):
    env, fake, root = _world(tmp_path)
    result = _cos(shell, env, "key", stdin="25\n")
    assert result.returncode == 0, result.stderr
    assert (root / "cos.env").read_text() == "COS_MODEL_GATEWAY_KEY=sk-fake-cos-key-0123456789\n"
    assert oct((root / "cos.env").stat().st_mode & 0o777) == "0o600"
    assert (root / "state" / "cos.key").read_text().strip() == "on"
    out = result.stdout + result.stderr
    assert "sk-fake-cos-key" not in out and "cos-master-key" not in out
    assert "monthly cap $25 (30d)" in out and "…c0de" in out and "rpm 60" in out
    assert (fake / "keygen.env").read_text().strip() == "cap=25 old="
    up = _compose_lines(fake)
    assert len(up) == 1 and up[0].endswith("up -d --no-build --no-deps cos-agent-a")
    assert f"--env-file {root}/cos.env" in up[0] and "docker-compose.staging-cos-key.yml" in up[0]
    assert "docker-compose.staging-keys.yml" in up[0]


def test_key_defaults_to_30_and_refuses_a_bad_cap(tmp_path, shell):
    env, fake, root = _world(tmp_path)
    assert _cos(shell, env, "key", stdin="\n").returncode == 0
    assert (fake / "keygen.env").read_text().startswith("cap=30 ")
    env2, fake2, root2 = _world(tmp_path / "b")
    bad = _cos(shell, env2, "key", stdin="plenty\n")
    assert bad.returncode != 0 and "is not a dollar amount" in bad.stderr
    assert not (fake2 / "keygen.env").exists() and not (root2 / "state" / "cos.key").exists()


def test_key_asks_before_rotating_and_hands_the_old_key_over_by_env(tmp_path, shell):
    env, fake, root = _world(tmp_path, cos_env="COS_MODEL_GATEWAY_KEY=sk-old-cos-key-1\n")
    kept = _cos(shell, env, "key", stdin="n\n")
    assert kept.returncode == 0 and "kept the existing key" in kept.stdout and not (fake / "keygen.env").exists()
    rotated = _cos(shell, env, "key", stdin="y\n40\n")
    assert rotated.returncode == 0, rotated.stderr
    assert (fake / "keygen.env").read_text().strip() == "cap=40 old=sk-old-cos-key-1"
    assert "sk-old-cos-key-1" not in (fake / "argv.log").read_text()
    assert (root / "cos.env").read_text().count("COS_MODEL_GATEWAY_KEY=") == 1


def test_key_needs_the_key_database(tmp_path, shell):
    env, fake, root = _world(tmp_path, keys_on=False)
    result = _cos(shell, env, "key", stdin="30\n")
    assert result.returncode != 0 and "run mc.sh gateway-keys first" in result.stderr


@pytest.mark.parametrize("clash,problem", [("master", "equals the master key"), ("mc", "equals MC's key")])
def test_key_refuses_a_key_equal_to_the_master_or_mcs(tmp_path, shell, clash, problem):
    env, fake, root = _world(tmp_path)
    if clash == "master":
        with (root / ".env").open("a") as f:
            f.write("MINIMOI_MODEL_GATEWAY_KEY='sk-clash-0123456789'\n")
    else:
        (root / "mc.env").write_text("MC_MODEL_GATEWAY_KEY=sk-clash-0123456789\n")
        (root / "mc.env").chmod(0o600)
    result = _cos(shell, env, "key", stdin="30\n", extra={"FAKE_NEW_KEY": "sk-clash-0123456789"})
    assert result.returncode != 0 and problem in result.stderr
    assert not (root / "state" / "cos.key").exists() and not _compose_lines(fake)
    assert "sk-clash" not in result.stdout + result.stderr


def test_off_goes_back_to_the_master_key_and_recreates_only_agent_a(tmp_path, shell):
    env, fake, root = _world(tmp_path, cos_env="COS_MODEL_GATEWAY_KEY=sk-cos-1\n")
    (root / "state" / "cos.key").write_text("on\n")
    result = _cos(shell, env, "off")
    assert result.returncode == 0 and (root / "state" / "cos.key").read_text().strip() == "off"
    up = _compose_lines(fake)
    assert len(up) == 1 and "staging-cos-key" not in up[0] and "cos.env" not in up[0]
    assert "sk-cos-1" in (root / "cos.env").read_text()                    # kept
    status = _cos(shell, env, "status")
    assert "the gateway's master key" in status.stdout and "sk-" not in status.stdout


def test_lib_refuses_cos_key_on_without_the_key_database_or_a_private_cos_env(tmp_path, shell):
    from test_staging_environment import _up
    env, fake, root = _world(tmp_path, cos_env="COS_MODEL_GATEWAY_KEY=sk-cos-1\n")
    (root / "state" / "cos.key").write_text("on\n")
    (root / "cos.env").chmod(0o644)
    bad = _up(shell, env)
    assert bad.returncode != 0 and "cos.env is missing or not mode 600" in bad.stderr
    env2, fake2, root2 = _world(tmp_path / "b", keys_on=False, cos_env="COS_MODEL_GATEWAY_KEY=sk-cos-1\n")
    (root2 / "state" / "cos.key").write_text("on\n")
    bad2 = _up(shell, env2)
    assert bad2.returncode != 0 and "no key database" in bad2.stderr


def test_verify_checks_cos_key_from_inside_agent_a_and_its_record_with_the_master_key():
    text = (SCRIPTS / "verify.sh").read_text()
    assert "== 10. CoS's own gateway key (cos.sh key)" in text
    assert "process.env.MINIMOI_MODEL_GATEWAY_KEY" in text and "'/key/list'" in text and "(r.status===401||r.status===403)" in text
    assert "COSK=$(sed -n 's/^COS_MODEL_GATEWAY_KEY=//p'" in text and "docker exec -e COSK minimoi-model-gateway" in text


def test_classifier_treats_the_cos_key_overlay_and_script_as_staging_only():
    import sys
    sys.path.insert(0, str(REPO / "scripts" / "ci"))
    from classify_release import classify
    assert classify(["docker-compose.staging-cos-key.yml", "scripts/staging/cos.sh", "scripts/staging/mc_keys.sh"]) == ("documents", ())
