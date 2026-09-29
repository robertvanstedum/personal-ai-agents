"""Master Craftsman stage C prep (MC spec v0.9 §2, §6; the #251 review's stage
C list): the gateway's key database, MC's route, the pass-through gap, and the
two commands Robert runs (`mc.sh gateway-keys`, `mc.sh key`). No real
credential, no model call: the commands run against a fake docker here, and
against a throwaway gateway and Postgres in scripts/staging/mc_probe/stage_c.py.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from test_staging_environment import (  # noqa: F401  (shell is a fixture)
    GATEWAY_PROD, GATEWAY_STAGING, PROD, SCRIPTS, STAGING, _compose_config, _git, _load, _staging_world, _write_exe, shell)

REPO = Path(__file__).resolve().parent.parent
KEYS_OVERLAY = REPO / "docker-compose.staging-keys.yml"
MC_KEYS = SCRIPTS / "mc_keys.sh"


# ── the gateway config: MC's route and gateway-only provider key names ───────

def _routes():
    return {m["model_name"]: m for m in _load(GATEWAY_STAGING)["model_list"]}


def test_mc_route_is_haiku_on_mcs_own_key_with_no_fallback():
    mc = _routes()["minimoi-mc-agent"]
    assert mc["litellm_params"]["model"] == "anthropic/claude-haiku-4-5-20251001"
    assert mc["litellm_params"]["api_key"] == "os.environ/MC_ANTHROPIC_API_KEY"
    assert mc["model_info"]["id"] == "mc-anthropic-haiku" and "fallback_position" not in mc["model_info"]
    assert "minimoi-mc-agent" not in str(_load(GATEWAY_STAGING)["router_settings"])
    assert "minimoi-mc-agent" not in GATEWAY_PROD.read_text()


def test_no_staging_route_uses_the_default_provider_key_names():
    """The pass-through routes sign upstream calls with ANTHROPIC_API_KEY /
    XAI_API_KEY; no deployment may depend on those names on staging."""
    for name, route in _routes().items():
        key = route["litellm_params"]["api_key"]
        assert key in ("os.environ/GATEWAY_ANTHROPIC_API_KEY", "os.environ/GATEWAY_XAI_API_KEY",
                       "os.environ/MC_ANTHROPIC_API_KEY"), (name, key)


def test_staging_gateway_env_empties_the_default_names_and_never_leaves_mcs_key_empty():
    rendered = yaml.safe_load(_compose_config(PROD, STAGING, env_extra={
        "MINIMOI_ROOT": "/Users/x/minimoi-staging", "MINIMOI_IMAGE_TAG": "abc1234"}))
    env = rendered["services"]["model-gateway"]["environment"]
    assert env["ANTHROPIC_API_KEY"] == "" and env["XAI_API_KEY"] == ""
    assert env["GATEWAY_ANTHROPIC_API_KEY"] and env["GATEWAY_XAI_API_KEY"]
    assert env["MC_ANTHROPIC_API_KEY"] == "mc-anthropic-placeholder-not-a-key"
    assert "DATABASE_URL" not in env                      # only with the keys overlay
    staging_text = STAGING.read_text()
    assert "${MC_ANTHROPIC_API_KEY:-mc-anthropic-placeholder-not-a-key}" in staging_text


def test_keys_overlay_adds_only_the_database_url_to_the_gateway():
    data = _load(KEYS_OVERLAY)
    assert set(data) == {"services"} and set(data["services"]) == {"model-gateway"}
    gw = data["services"]["model-gateway"]
    assert gw["environment"] == ["DATABASE_URL=${LITELLM_DATABASE_URL:?set by scripts/staging/mc.sh gateway-keys (gateway.env)}"]
    assert gw["healthcheck"] == {"start_period": "120s"}
    extra = {"MINIMOI_ROOT": "/Users/x/minimoi-staging", "MINIMOI_IMAGE_TAG": "abc1234",
             "LITELLM_DATABASE_URL": "postgresql://u:p@postgres:5432/litellm_keys"}
    with_keys = yaml.safe_load(_compose_config(PROD, STAGING, KEYS_OVERLAY, env_extra=extra))["services"]
    without = yaml.safe_load(_compose_config(PROD, STAGING, env_extra=extra))["services"]
    assert with_keys["model-gateway"]["environment"]["DATABASE_URL"].endswith("/litellm_keys")
    assert set(with_keys) == set(without)
    for name in without:                                   # no other service sees the key database
        if name != "model-gateway":
            assert with_keys[name] == without[name], name


def test_production_gateway_and_compose_are_unchanged():
    for path in ("docker-compose.prod.yml", "services/model_gateway/litellm.prod.yaml",
                 "services/model_gateway/litellm.yaml", "docker/Dockerfile.model-gateway",
                 "services/model_gateway/receipt_callback.py"):
        main = subprocess.run(["git", "-C", str(REPO), "show", f"origin/main:{path}"], capture_output=True, text=True)
        if main.returncode != 0:
            pytest.skip("origin/main is not available")
        assert (REPO / path).read_text() == main.stdout, path


# ── mc_keys.sh ────────────────────────────────────────────────────────────────

def _keys(fn, *args):
    return subprocess.run(["bash", "-c", f'source "{MC_KEYS}"; {fn} "$@"', "keys", *args], capture_output=True, text=True)


def test_keydb_sql_is_idempotent_least_privilege_and_refuses_a_bad_password():
    pw = "a" * 48
    sql = _keys("keydb_sql", pw).stdout
    assert "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'litellm_keys')" in sql
    assert "ALTER ROLE litellm_keys WITH LOGIN PASSWORD" in sql            # a re-run re-passwords
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert "CREATE DATABASE litellm_keys OWNER litellm_keys" in sql and "WHERE NOT EXISTS" in sql
    assert "REVOKE ALL ON DATABASE litellm_keys FROM PUBLIC" in sql
    assert "personal_agents" not in sql
    for bad in ("short", "x" * 40, "a'; DROP TABLE x; --"):
        assert _keys("keydb_sql", bad).returncode == 64
    assert _keys("keydb_url", pw, "postgres").stdout.strip() == f"postgresql://litellm_keys:{pw}@postgres:5432/litellm_keys"


def test_mc_key_scope_is_its_route_chat_only_monthly_and_rate_limited():
    py = _keys("mc_keygen_py").stdout
    assert '"models": ["minimoi-mc-agent"]' in py
    assert '"allowed_routes": ["/v1/chat/completions", "/chat/completions"]' in py
    assert "/key/info" not in py.split('"allowed_routes"')[1].split("\n")[0]   # finding: /key/info reads other keys
    assert '"budget_duration": "30d"' in py and '"rpm_limit": 10' in py and '"max_budget": cap' in py
    assert "LITELLM_MASTER_KEY" in py and "print(made[\"key\"])" in py


@pytest.mark.parametrize("cap,ok", [("15", True), ("7.50", True), ("0.01", True), ("500", True), ("0", False),
                                    ("501", False), ("-5", False), ("15.555", False), ("abc", False), ("", False)])
def test_valid_cap(cap, ok):
    assert (_keys("valid_cap", cap).returncode == 0) is ok


# ── mc.sh gateway-keys and mc.sh key, against a fake docker ──────────────────

FAKE_DOCKER_C = r"""#!/bin/sh
# Fake docker for mc.sh stage C: every call's argv goes to $FAKE/argv.log.
printf '%s\n' "$*" >> "$FAKE/argv.log"
case "$*" in
  "inspect -f {{.State.Running}} "*) echo true; exit 0 ;;
  "inspect -f {{if .State.Health}}"*) echo healthy; exit 0 ;;
  "inspect "*) exit 1 ;;
  "exec -i postgres-ai-agents psql"*) cat > "$FAKE/psql.stdin"; exit "${FAKE_PSQL_EXIT:-0}" ;;
  "exec postgres-ai-agents psql"*) echo 1; exit 0 ;;
  "exec -i -e MC_OLD_KEY -e MC_CAP minimoi-model-gateway python -")
    cat > "$FAKE/keygen.stdin"
    printf 'cap=%s old=%s\n' "$MC_CAP" "$MC_OLD_KEY" > "$FAKE/keygen.env"
    printf 'sk-fake-new-key-0123456789\nab12\n'; exit 0 ;;
  compose*) exit 0 ;;
  volume*|network*|image*) exit 0 ;;
esac
exit 0
"""


def _world(tmp_path, *, keys_on=False, mc_env="MC_OPENCLAW_GATEWAY_TOKEN=mc-token-1111\nMC_RELAY_TOKEN=relay-token-2222\n"):
    env, fake, release = _staging_world(tmp_path)
    _write_exe(Path(env["PATH"].split(":")[0]) / "docker", FAKE_DOCKER_C)
    root = Path(env["STAGING_ROOT"])
    for name in ("docker-compose.mc.yml", "docker-compose.staging-keys.yml"):
        (release / name).write_text("services: {}\n")
    _git(release, "add", "-A")
    _git(release, "commit", "-qm", "c")
    sha = _git(release, "rev-parse", "HEAD").stdout.strip()
    (root / "RELEASE").write_text(f"sha={sha}\ntag=abc1234\n")
    with (root / ".env").open("a") as f:
        f.write("MINIMOI_MODEL_GATEWAY_KEY='cos-master-key'\nCOS_AGENT_A_GATEWAY_TOKEN='cos-token'\nANTHROPIC_API_KEY='cos-anthropic-key'\n")
    (root / "state" / "mc.enabled").touch()
    (root / "mc.env").write_text(mc_env)
    (root / "mc.env").chmod(0o600)
    if keys_on:
        (root / "state" / "gateway.keys").write_text("on\n")
        (root / "gateway.env").write_text("LITELLM_DATABASE_URL=postgresql://litellm_keys:x@postgres:5432/litellm_keys\n")
        (root / "gateway.env").chmod(0o600)
    return env, fake, root


def _mc(shell, env, *args, stdin=""):
    return subprocess.run([shell, str(SCRIPTS / "mc.sh"), *args], env=env, input=stdin, capture_output=True, text=True)


def test_gateway_keys_makes_the_database_via_stdin_and_recreates_only_the_gateway(tmp_path, shell):
    env, fake, root = _world(tmp_path)
    result = _mc(shell, env, "gateway-keys", stdin="yes\nmc-own-anthropic-key-xyz\n")
    assert result.returncode == 0, result.stderr
    gw_env = (root / "gateway.env").read_text()
    url = gw_env.split("=", 1)[1].strip()
    pw = url.split(":")[2].split("@")[0]
    assert len(pw) == 48 and oct((root / "gateway.env").stat().st_mode & 0o777) == "0o600"
    sql = (fake / "psql.stdin").read_text()
    assert pw in sql and "CREATE DATABASE litellm_keys" in sql         # the password goes through stdin...
    argv = (fake / "argv.log").read_text()
    assert pw not in argv                                              # ...never through argv
    out = result.stdout + result.stderr
    assert pw not in out and "mc-own-anthropic-key-xyz" not in out     # nothing secret printed
    assert "MC_ANTHROPIC_API_KEY=mc-own-anthropic-key-xyz" in (root / "mc.env").read_text()
    assert (root / "state" / "gateway.keys").read_text().strip() == "on"
    up = [l for l in argv.splitlines() if l.startswith("compose") and " up " in l]
    assert len(up) == 1 and up[0].endswith("up -d --no-build --no-deps model-gateway")
    assert f"--env-file {root}/gateway.env" in up[0] and "docker-compose.staging-keys.yml" in up[0]
    again = _mc(shell, env, "gateway-keys")
    assert again.returncode == 0 and "unchanged" in again.stdout
    assert (root / "gateway.env").read_text() == gw_env                # never re-passworded by a re-run


def test_gateway_keys_refuses_coss_anthropic_key_for_mc(tmp_path, shell):
    env, fake, root = _world(tmp_path)
    result = _mc(shell, env, "gateway-keys", stdin="yes\ncos-anthropic-key\n")
    assert result.returncode != 0 and "that is CoS's Anthropic key" in result.stderr
    assert "MC_ANTHROPIC_API_KEY" not in (root / "mc.env").read_text()
    assert "cos-anthropic-key" not in result.stdout
    assert not (fake / "psql.stdin").exists() and (root / "gateway.env").read_text() == ""   # nothing changed
    assert not (root / "state" / "gateway.keys").exists()


@pytest.mark.parametrize("answer", ["", "y", "no", "YES please"])
def test_gateway_keys_needs_a_typed_yes_for_the_console_limit_before_anything_changes(tmp_path, shell, answer):
    """#252 review, condition A: the console limit on MC's own key is the hard
    ceiling (a key database outage can undercount the gateway's cap)."""
    env, fake, root = _world(tmp_path)
    result = _mc(shell, env, "gateway-keys", stdin=answer + "\nmc-own-anthropic-key-xyz\n")
    assert result.returncode != 0 and "set the console spend limit on MC's key first (nothing changed)" in result.stderr
    assert "console spend limit" in result.stderr and "hard ceiling" in result.stderr and "undercount" in result.stderr
    assert "Paste Master Craftsman's OWN Anthropic API key" not in result.stderr        # asked before the key
    assert not (fake / "psql.stdin").exists() and not (root / "state" / "gateway.keys").exists()
    assert "MC_ANTHROPIC_API_KEY" not in (root / "mc.env").read_text()
    argv = (fake / "argv.log").read_text()
    assert not [l for l in argv.splitlines() if l.startswith("compose")]


def test_key_sets_umask_before_writing_the_key_and_mc_never_carries_provider_keys():
    mc = (SCRIPTS / "mc.sh").read_text()
    key_branch = mc.split("\n  key)\n", 1)[1].split("\n    ;;\n", 1)[0]
    assert key_branch.index("umask 077") < key_branch.index('tmp="$STAGING_MC_ENV.tmp.$$"')
    selfcheck = (REPO / "docker/mc-agent/selfcheck.mjs").read_text()
    verify = (SCRIPTS / "verify.sh").read_text()
    for name in ("MC_ANTHROPIC_API_KEY", "GATEWAY_ANTHROPIC_API_KEY", "GATEWAY_XAI_API_KEY"):
        assert f'"{name}"' in selfcheck and f"|{name}|" in verify, name
    assert "MC_ANTHROPIC_API_KEY" not in (REPO / "docker-compose.mc.yml").read_text()


def test_key_asks_for_the_cap_writes_the_key_unprinted_and_recreates_only_mc(tmp_path, shell):
    env, fake, root = _world(tmp_path, keys_on=True)
    result = _mc(shell, env, "key", stdin="20\n")
    assert result.returncode == 0, result.stderr
    assert "MC_MODEL_GATEWAY_KEY=sk-fake-new-key-0123456789" in (root / "mc.env").read_text()
    assert oct((root / "mc.env").stat().st_mode & 0o777) == "0o600"
    out = result.stdout + result.stderr
    assert "sk-fake-new-key" not in out
    assert "monthly cap $20 (30d)" in out and "…ab12" in out and '["minimoi-mc-agent"]' in out
    assert (fake / "keygen.env").read_text().strip() == "cap=20 old="
    argv = (fake / "argv.log").read_text()
    up = [l for l in argv.splitlines() if l.startswith("compose") and " up " in l]
    assert len(up) == 1 and up[0].startswith("compose -p minimoi-staging-mc ") and "model-gateway" not in up[0]


def test_key_defaults_to_15_and_refuses_a_bad_cap(tmp_path, shell):
    env, fake, root = _world(tmp_path, keys_on=True)
    assert _mc(shell, env, "key", stdin="\n").returncode == 0
    assert (fake / "keygen.env").read_text().startswith("cap=15 ")
    env2, fake2, _ = _world(tmp_path / "b", keys_on=True)
    bad = _mc(shell, env2, "key", stdin="lots\n")
    assert bad.returncode != 0 and "is not a dollar amount" in bad.stderr
    assert not (fake2 / "keygen.env").exists()


def test_key_asks_before_rotating_and_passes_the_old_key_by_env_not_argv(tmp_path, shell):
    env, fake, root = _world(tmp_path, keys_on=True,
                             mc_env="MC_OPENCLAW_GATEWAY_TOKEN=mc-token-1111\nMC_RELAY_TOKEN=relay-token-2222\nMC_MODEL_GATEWAY_KEY=sk-old-key-999\n")
    kept = _mc(shell, env, "key", stdin="n\n")
    assert kept.returncode == 0 and "kept the existing key" in kept.stdout
    assert "sk-old-key-999" in (root / "mc.env").read_text() and not (fake / "keygen.env").exists()
    rotated = _mc(shell, env, "key", stdin="y\n12\n")
    assert rotated.returncode == 0, rotated.stderr
    assert (fake / "keygen.env").read_text().strip() == "cap=12 old=sk-old-key-999"     # handed over by env
    assert "sk-old-key-999" not in (fake / "argv.log").read_text()                       # never in argv
    text = (root / "mc.env").read_text()
    assert "sk-old-key-999" not in text and text.count("MC_MODEL_GATEWAY_KEY=") == 1


def test_key_needs_the_key_database_first(tmp_path, shell):
    env, fake, _ = _world(tmp_path, keys_on=False)
    result = _mc(shell, env, "key", stdin="15\n")
    assert result.returncode != 0 and "run mc.sh gateway-keys first" in result.stderr


def test_lib_adds_the_keys_overlay_and_gateway_env_only_when_on(tmp_path, shell):
    from test_staging_environment import _up
    env, fake, root = _world(tmp_path, keys_on=True)
    assert _up(shell, env).returncode == 0
    line = [l for l in (fake / "argv.log").read_text().splitlines() if l.startswith("compose")][0]
    assert "docker-compose.staging-keys.yml" in line and f"--env-file {root}/gateway.env" in line
    (root / "gateway.env").chmod(0o644)
    bad = _up(shell, env)
    assert bad.returncode != 0 and "gateway.env is missing or not mode 600" in bad.stderr
    env2, fake2, _ = _world(tmp_path / "b", keys_on=False)
    assert _up(shell, env2).returncode == 0
    line = [l for l in (fake2 / "argv.log").read_text().splitlines() if l.startswith("compose")][0]
    assert "staging-keys" not in line and "gateway.env" not in line


def test_verify_runs_the_stage_c_checks_once_a_key_exists():
    text = (SCRIPTS / "verify.sh").read_text()
    assert "(r.status===401||r.status===403)" in text                     # only 401/403 are refusals
    for path in ("/anthropic/v1/messages", "/openai/v1/chat/completions", "/key/generate", "/v1/responses"):
        assert path in text
    assert "/key/info?key=" in text and "LITELLM_MASTER_KEY" in text   # positive control with the master key
    assert "no pass-through spend" in text


def test_classifier_treats_the_keys_overlay_as_staging_only():
    out = subprocess.run(["python3", str(REPO / "scripts/ci/classify_release.py")], input="docker-compose.staging-keys.yml\nscripts/staging/mc_keys.sh\nservices/model_gateway/litellm.staging.yaml\n",
                         capture_output=True, text=True, cwd=REPO)
    assert out.returncode == 0, out.stderr
    import json
    result = json.loads(out.stdout)
    assert result["release_class"] == "documents" and result["services"] == "", result   # nothing deploys
