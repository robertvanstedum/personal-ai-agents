"""Docker staging for dev.minimoi.ai (issue #234).

Staging runs docker-compose.prod.yml plus docker-compose.staging.yml with
MINIMOI_ROOT pointing at ~/minimoi-staging. These tests pin down that:

* production renders exactly as before when MINIMOI_ROOT is unset;
* the staging override changes only what it should (local images, standby
  role, Guild flags on the portal only, bots behind a profile, loopback ports,
  external volumes, no production secrets);
* build.sh builds the same image map as .github/workflows/deploy.yml;
* the staging gateway keeps production's logical names and settings;
* seed.sh copies and never moves, and env.sh never writes a forbidden name;
* staging-only paths never trigger a production deploy.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.ci.classify_release import ALL_SERVICES, classify

REPO = Path(__file__).resolve().parent.parent
PROD = REPO / "docker-compose.prod.yml"
STAGING = REPO / "docker-compose.staging.yml"
SCRIPTS = REPO / "scripts" / "staging"
DEPLOY = REPO / ".github" / "workflows" / "deploy.yml"
GATEWAY_PROD = REPO / "services" / "model_gateway" / "litellm.prod.yaml"
GATEWAY_DEV = REPO / "services" / "model_gateway" / "litellm.yaml"
GATEWAY_STAGING = REPO / "services" / "model_gateway" / "litellm.staging.yaml"

ROOT_VAR = "${MINIMOI_ROOT:-/opt/minimoi}"
REQUIRED_VARS = {
    "MINIMOI_MODEL_GATEWAY_KEY": "x", "XAI_API_KEY": "x", "ANTHROPIC_API_KEY": "x",
    "MINIMOI_MODEL_GATEWAY_RECEIPT_KEY": "x", "COS_AGENT_A_GATEWAY_TOKEN": "x",
}

# Frozen from main (plus PR #237's queue folder): (service, host, container).
EXPECTED_PROD_MOUNTS = [
    ("cos-agent-a", "cos-agent-a-state", "/home/node/.openclaw"),
    ("cos-agent-a", "cos-agent-a-auth", "/home/node/.config/openclaw"),
    ("cos-bot", "/opt/minimoi/cos_memory.md", "/app/data/cos_memory.md"),
    ("cos-bot", "/opt/minimoi/data/model_gateway_receipts.jsonl", "/app/data/model_gateway_receipts.jsonl"),
    ("cos-bot", "/opt/minimoi/data/guild/cos_context.json", "/app/domains/guild/config/cos_context.json"),
    ("cos-scheduler", "/opt/minimoi/cos_memory.md", "/app/data/cos_memory.md"),
    ("cos-scheduler", "/opt/minimoi/data/model_gateway_receipts.jsonl", "/app/data/model_gateway_receipts.jsonl"),
    ("cos-scheduler", "/opt/minimoi/data/guild/cos_context.json", "/app/domains/guild/config/cos_context.json"),
    ("cos-scheduler", "/var/run/docker.sock", "/var/run/docker.sock"),
    ("curator", "/opt/minimoi/data/curator", "/app/data/curator"),
    ("curator", "/opt/minimoi/data/curator_archive", "/app/curator_archive"),
    ("curator", "/opt/minimoi/data/curator_history.json", "/app/curator_history.json"),
    ("curator", "/opt/minimoi/data/curator_costs.json", "/app/curator_costs.json"),
    ("curator", "/opt/minimoi/data/interests", "/app/interests"),
    ("curator", "/opt/minimoi/data/research-intelligence", "/app/_NewDomains/research-intelligence/data"),
    ("german", "/opt/minimoi/data/german", "/app/domains/german/data"),
    ("portal", "/opt/minimoi/data/guild", "/app/runtime/guild"),
    ("portal", "/opt/minimoi/docs/design", "/app/docs/design"),
    ("portal", "/opt/minimoi/docs/specs", "/app/docs/specs"),
    ("portal", "/opt/minimoi/auth/users.json", "/app/minimoi_portal/auth/users.json"),
    ("portal", "/opt/minimoi/auth/guests.json", "/app/minimoi_portal/auth/guests.json"),
    ("portal", "/opt/minimoi/agent_logs", "/app/agent_logs"),
    ("portuguese", "/opt/minimoi/data/portuguese", "/app/domains/portuguese/data"),
    ("postgres", "postgres-data", "/var/lib/postgresql"),
    ("system-bot", "/opt/minimoi/data/german", "/app/domains/german/data"),
]
EXPECTED_ENV_FILES = {
    name: "/opt/minimoi/.env"
    for name in ("postgres", "curator", "german", "portuguese", "portal", "system-bot", "cos-bot", "cos-scheduler")
}
# deploy.yml's service -> ECR repository map, and the tag prefix of each.
IMAGE_MAP = {
    "portal": ("portal", ""), "curator": ("curator", ""), "german": ("mein-deutsch", ""),
    "portuguese": ("portuguese", ""), "system-bot": ("system-bot", ""), "cos-bot": ("cos-bot", ""),
    "cos-scheduler": ("cos-scheduler", ""), "cos-agent-a": ("cos-scheduler", "agent-a-"),
    "model-gateway": ("cos-scheduler", "model-gateway-"),
}
PYTHON_SERVICES = ("curator", "german", "portuguese", "portal", "system-bot", "cos-bot", "cos-scheduler")


def _load(path):
    return yaml.safe_load(path.read_text())


def _default_root(value: str) -> str:
    return value.replace(ROOT_VAR, "/opt/minimoi")


def _split_volume(volume: str):
    host, container = _default_root(volume).split(":")[:2]
    return host, container


def _env(service) -> dict:
    env = service.get("environment") or []
    if isinstance(env, dict):
        return {k: str(v) for k, v in env.items()}
    return dict(item.split("=", 1) for item in env)


# ── production default unchanged ──────────────────────────────────────────────

def test_prod_mounts_and_env_files_are_unchanged_with_root_unset():
    services = _load(PROD)["services"]
    mounts = sorted(
        (name, *_split_volume(v))
        for name, service in services.items() for v in service.get("volumes", [])
    )
    assert mounts == sorted(EXPECTED_PROD_MOUNTS)
    env_files = {name: _default_root(s["env_file"]) for name, s in services.items() if "env_file" in s}
    assert env_files == EXPECTED_ENV_FILES


def test_every_prod_host_path_is_parameterized_one_way_only():
    text = PROD.read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # A host path is a volume entry or env_file; messages inside ${VAR:?...} may name /opt/minimoi.
        if stripped.startswith("- /opt/minimoi") or stripped.startswith("env_file: /opt/minimoi"):
            pytest.fail(f"bare production host path left: {stripped}")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    forms = set(re.findall(r"\$\{MINIMOI_ROOT[^}]*\}", code))
    assert forms == {ROOT_VAR}
    assert code.count(ROOT_VAR) == len(EXPECTED_ENV_FILES) + sum(
        1 for _s, host, _c in EXPECTED_PROD_MOUNTS if host.startswith("/opt/minimoi"))


def test_prod_compose_sets_neither_guild_flag():
    text = PROD.read_text()
    assert "MINIMOI_GUILD_NEXT" not in text and "MINIMOI_GUILD_PROTO" not in text


def _compose_config(*files, env_extra=None, profiles=()):
    if not shutil.which("docker"):
        pytest.skip("docker CLI not installed")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"), **REQUIRED_VARS}
    env.update(env_extra or {})
    cmd = ["docker", "compose", "-p", "render"]
    for profile in profiles:
        cmd += ["--profile", profile]
    for f in files:
        cmd += ["-f", str(f)]
    cmd += ["config", "--no-env-resolution"]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=REPO)
    if result.returncode != 0 and ("unknown flag" in result.stderr or "is not a docker command" in result.stderr):
        pytest.skip("docker compose v2 with --no-env-resolution not available")
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_docker_renders_prod_identically_with_root_unset_or_default():
    unset = _compose_config(PROD)
    explicit = _compose_config(PROD, env_extra={"MINIMOI_ROOT": "/opt/minimoi"})
    assert unset == explicit
    rendered = yaml.safe_load(unset)
    for name, service in rendered["services"].items():
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                assert mount["source"].startswith(("/opt/minimoi/", "/var/run/docker.sock")), (name, mount)
        for env_file in service.get("env_file", []):
            assert env_file["path"] == "/opt/minimoi/.env"
    assert "MINIMOI_ROOT" not in unset


def test_docker_renders_staging_under_the_staging_root_only():
    root = "/Users/someone/minimoi-staging"
    rendered = yaml.safe_load(_compose_config(
        PROD, STAGING, profiles=("bots",),
        env_extra={"MINIMOI_ROOT": root, "MINIMOI_IMAGE_TAG": "abc1234"}))
    services = rendered["services"]
    assert set(services) == set(ALL_SERVICES) | {"postgres"}
    for name, service in services.items():
        for mount in service.get("volumes", []):
            if mount["type"] == "bind":
                assert mount["source"].startswith((root + "/", "/var/run/docker.sock")), (name, mount)
            else:
                assert rendered["volumes"][mount["source"]]["external"] is True
        for env_file in service.get("env_file", []):
            assert env_file["path"] == f"{root}/.env"
        for port in service.get("ports", []):
            assert port["host_ip"] == "127.0.0.1", (name, port)
    assert services["portal"]["environment"]["MINIMOI_GUILD_NEXT"] == "1"
    assert services["portal"]["environment"]["MINIMOI_GUILD_PROTO"] == "1"
    assert rendered["networks"]["iotconnect-edge"]["name"] == "minimoi-staging-iotconnect-edge"


# ── staging override rules ────────────────────────────────────────────────────

def test_staging_override_uses_local_images_never_pulled():
    prod = _load(PROD)["services"]
    staging = _load(STAGING)["services"]
    assert set(staging) == set(prod)
    assert staging["postgres"] == {"pull_policy": "never"}
    for name, (repository, prefix) in IMAGE_MAP.items():
        service = staging[name]
        assert service["pull_policy"] == "never", name
        assert service["image"].startswith(f"minimoi-staging/{repository}:{prefix}${{MINIMOI_IMAGE_TAG:?"), name
        assert "dkr.ecr" not in service["image"]


def test_staging_role_is_standby_on_every_python_service():
    prod = _load(PROD)["services"]
    staging = _load(STAGING)["services"]
    prod_role_setters = {n for n, s in prod.items() if "MINIMOI_ROLE" in _env(s)}
    assert prod_role_setters <= set(PYTHON_SERVICES)
    for name in PYTHON_SERVICES:
        assert _env(staging[name]).get("MINIMOI_ROLE") == "standby", name


def test_guild_flags_are_on_the_staging_portal_only():
    staging = _load(STAGING)["services"]
    portal = _env(staging["portal"])
    assert portal["MINIMOI_GUILD_NEXT"] == "1" and portal["MINIMOI_GUILD_PROTO"] == "1"
    assert portal["BASE_URL"] == "https://dev.minimoi.ai"
    for name, service in staging.items():
        if name != "portal":
            assert not any(k.startswith("MINIMOI_GUILD_") for k in _env(service)), name


def test_bots_are_behind_a_profile_and_nothing_else_is():
    staging = _load(STAGING)["services"]
    profiled = {name: s["profiles"] for name, s in staging.items() if "profiles" in s}
    assert profiled == {"system-bot": ["bots"], "cos-bot": ["bots"]}


def test_staging_ports_are_loopback_and_keep_records_ports():
    staging = _load(STAGING)["services"]
    ports = {name: s.get("ports", []) for name, s in staging.items() if s.get("ports")}
    assert ports == {"model-gateway": ["127.0.0.1:14000:4000"], "cos-agent-a": ["127.0.0.1:18790:18789"]}


def test_staging_volumes_are_external_and_staging_named():
    data = _load(STAGING)
    assert set(data["volumes"]) == set(_load(PROD)["volumes"])
    for key, volume in data["volumes"].items():
        assert volume == {"external": True, "name": f"minimoi-staging-{key}"}
    assert data["networks"] == {"iotconnect-edge": {"name": "minimoi-staging-iotconnect-edge"}}


def test_staging_override_holds_no_production_path_or_secret():
    text = STAGING.read_text()
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "/opt/minimoi" not in body
    assert "env_file" not in body
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_POLLING_BOT_TOKEN", "AWS_", "_PROD", "iotconnect:"):
        assert name not in body, name
    gateway = _load(STAGING)["services"]["model-gateway"]
    assert gateway["volumes"] == [
        "${MINIMOI_ROOT:?set MINIMOI_ROOT (scripts/staging/lib.sh)}/config/litellm.staging.yaml:/app/config.yaml:ro"]


# ── build map parity with deploy.yml ──────────────────────────────────────────

CASE_LINE = re.compile(r"^\s*([a-z-]+)\) dockerfile=.*$")


def _case_lines(path):
    return [line.strip() for line in path.read_text().splitlines() if CASE_LINE.match(line)]


def test_build_sh_uses_the_same_image_map_as_deploy_yml():
    deploy = _case_lines(DEPLOY)
    build = _case_lines(SCRIPTS / "build.sh")
    assert len(deploy) == len(ALL_SERVICES)
    assert build == deploy
    services = re.search(r'^SERVICES="([^"]+)"', (SCRIPTS / "build.sh").read_text(), re.M).group(1).split()
    assert tuple(services) == ALL_SERVICES


def test_build_sh_builds_native_and_tags_staging_images():
    text = "\n".join(_code_lines(SCRIPTS / "build.sh"))
    assert "--platform" not in text
    assert 'image="minimoi-staging/$repository:$tag"' in text
    assert "--allow-emulated" in text
    assert "merge-base --is-ancestor" in text and "--reviewed-branch" in text


# ── gateway ───────────────────────────────────────────────────────────────────

def _routes(path):
    return {m["model_name"]: m for m in _load(path)["model_list"]}


def test_staging_gateway_keeps_production_names_and_settings():
    prod, staging = _load(GATEWAY_PROD), _load(GATEWAY_STAGING)
    assert [m["model_name"] for m in staging["model_list"]] == [m["model_name"] for m in prod["model_list"]]
    for block in ("router_settings", "litellm_settings", "general_settings"):
        assert staging[block] == prod[block], block


def test_staging_cos_agent_route_is_haiku_and_the_rest_follow_the_dev_gateway():
    staging, prod, dev = _routes(GATEWAY_STAGING), _routes(GATEWAY_PROD), _routes(GATEWAY_DEV)
    agent = staging["minimoi-cos-agent"]
    haiku = "anthropic/claude-haiku-4-5-20251001"
    assert agent["litellm_params"]["model"] == haiku
    assert agent["litellm_params"]["api_key"] == "os.environ/ANTHROPIC_API_KEY"
    assert agent["model_info"]["base_model"] == haiku
    assert set(agent["model_info"]) == set(prod["minimoi-cos-agent"]["model_info"])
    assert agent["model_info"]["fallback_position"] == 0
    for name in staging:
        if name != "minimoi-cos-agent":
            assert staging[name] == dev[name], name


# ── seed safety ───────────────────────────────────────────────────────────────

def _code_lines(path):
    return [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]


def test_seed_never_moves_or_deletes():
    code = "\n".join(_code_lines(SCRIPTS / "seed.sh"))
    assert not re.search(r"(^|[\s;&|(])mv\s", code)
    assert not re.search(r"\brm\s+-[a-zA-Z]*r", code)
    assert "--delete" not in code and "--remove-source-files" not in code
    assert ":ro" in code  # the postgres source volume is mounted read-only


def test_scripts_run_on_macos_bash_32():
    for script in sorted(SCRIPTS.glob("*.sh")):
        text = script.read_text()
        assert text.startswith("#!/bin/bash"), script.name
        for construct in ("mapfile", "readarray", "declare -A", ",,}", "^^}"):
            assert construct not in text, (script.name, construct)
        subprocess.run(["bash", "-n", str(script)], check=True)


def _tool(name):
    if not shutil.which(name):
        pytest.skip(f"{name} not installed")


def test_seed_copies_sources_without_changing_them(tmp_path):
    for tool in ("git", "rsync", "shasum", "tar"):
        _tool(tool)
    repo = tmp_path / "repo"
    files = {
        "data/curator/a.json": "{}", "interests/i.md": "x", "_NewDomains/research-intelligence/data/r.json": "r",
        "domains/german/data/config/c.json": "base", "domains/portuguese/data/personas.json": "p",
        "domains/guild/config/cos_context.json": "{}", "data/guild/build_queue.json": '[{"id": 1}]',
        "docs/specs/s.md": "s", "docs/design/d.md": "d", "minimoi_portal/auth/users.json": "{}",
        "minimoi_portal/auth/guests.json": "{}", "curator_history.json": "[]", "curator_costs.json": "{}",
        "data/cos_memory.md": "m", "agent_logs/log.txt": "l", "curator_archive/old.json": "{}",
    }
    for rel, content in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content)
    git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-qm", "seed"], check=True)
    subprocess.run(git + ["update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    german = tmp_path / "german-state"
    (german / "sessions").mkdir(parents=True)
    (german / "sessions" / "s1.json").write_text("live")
    before = {p: p.read_bytes() for p in list(repo.rglob("*")) + list(german.rglob("*"))
              if p.is_file() and ".git" not in p.parts}

    root = tmp_path / "staging-root"
    env = {**os.environ, "STAGING_ROOT": str(root), "SEED_SOURCE_REPO": str(repo),
           "SEED_GERMAN_STATE": str(german), "SEED_PORTUGUESE_STATE": str(tmp_path / "absent"),
           "SEED_COS_MEMORY": str(tmp_path / "absent.md"), "SEED_RECEIPTS": str(tmp_path / "absent.jsonl")}
    seed = SCRIPTS / "seed.sh"
    result = subprocess.run(["bash", str(seed)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    after = {p: p.read_bytes() for p in before}
    assert after == before  # nothing read was changed, moved or removed
    assert (root / "data/guild/build_queue.json").read_text() == '[{"id": 1}]'
    assert (root / "data/german/sessions/s1.json").read_text() == "live"
    assert (root / "data/german/config/c.json").read_text() == "base"
    for record in ("SEEDED_FROM.txt", "SHA256SUMS.sources", "SHA256SUMS.seed"):
        assert (root / record).stat().st_size > 0, record
    assert oct(root.stat().st_mode & 0o777) == "0o700"

    drift = subprocess.run(["bash", str(seed), "--drift"], env=env, capture_output=True, text=True)
    assert drift.returncode == 0, drift.stdout + drift.stderr
    (german / "sessions" / "s1.json").write_text("changed")
    drift = subprocess.run(["bash", str(seed), "--drift"], env=env, capture_output=True, text=True)
    assert drift.returncode == 1 and "changed: " in drift.stdout

    again = subprocess.run(["bash", str(seed)], env=env, capture_output=True, text=True)
    assert again.returncode != 0 and "not empty" in again.stderr


# ── secrets ───────────────────────────────────────────────────────────────────

def test_env_sh_copies_by_name_and_never_prints_or_writes_forbidden_names(tmp_path):
    if not Path("/usr/bin/python3").exists():
        pytest.skip("/usr/bin/python3 missing")
    secret = "s3cr3t-value-never-printed"
    source = tmp_path / "root.env"
    source.write_text("\n".join([
        f"export ANTHROPIC_API_KEY={secret}-a", f'XAI_API_KEY="{secret}-x"',
        f"MINIMOI_MODEL_GATEWAY_KEY={secret}-g", f"MINIMOI_MODEL_GATEWAY_RECEIPT_KEY={secret}-r",
        f"COS_AGENT_A_GATEWAY_TOKEN={secret}-t", f"DATABASE_URL=postgresql://u:{secret}@localhost:5432/personal_agents",
        f"POSTGRES_PASSWORD={secret}-p", "PORTAL_SECRET_KEY=" + "k" * 40,
        f"TELEGRAM_BOT_TOKEN={secret}-prod", f"TELEGRAM_POLLING_BOT_TOKEN={secret}-poll",
        f"AWS_SECRET_ACCESS_KEY={secret}-aws", "MINIMOI_ROLE=production", "COS_BACKEND=http://localhost:18769",
    ]) + "\n")
    root = tmp_path / "staging-root"
    env = {**os.environ, "STAGING_ROOT": str(root), "STAGING_NO_KEYCHAIN": "1",
           "STAGING_GERMAN_PLIST": str(tmp_path / "none.plist")}
    result = subprocess.run(["bash", str(SCRIPTS / "env.sh"), "--source-env", str(source)],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout + result.stderr
    target = root / ".env"
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    written = dict(line.split("=", 1) for line in target.read_text().splitlines() if line and not line.startswith("#"))
    for name in written:
        assert not re.match(r"^(TELEGRAM_BOT_TOKEN|TELEGRAM_POLLING_BOT_TOKEN|AWS_.*|.*_PROD|MINIMOI_GUILD_.*)$", name)
    assert written["MINIMOI_ROLE"] == "'standby'"
    assert written["COS_BACKEND"] == "'http://cos-scheduler:8769'"
    assert written["MINIMOI_ROOT"] == f"'{root}'"
    assert written["DATABASE_URL"] == f"'postgresql://u:{secret}@postgres:5432/personal_agents'"
    assert written["XAI_API_KEY"] == f"'{secret}-x'"

    again = subprocess.run(["bash", str(SCRIPTS / "env.sh"), "--source-env", str(source)],
                           env=env, capture_output=True, text=True)
    assert again.returncode != 0 and "--force" in again.stderr


def test_env_sh_never_reads_production_telegram_accounts():
    text = (SCRIPTS / "env.sh").read_text()
    assert '"cos_test_bot_token"' in text and '"system_test_bot_token"' in text
    for account in ('"cos_bot_token")', '"system_bot_token")', '"polling_bot_token")', '"bot_token")'):
        assert account not in text


def test_lib_refuses_to_remove_volumes(tmp_path):
    for flag in ("-v", "--volumes"):
        result = subprocess.run(
            ["bash", "-c", f'source "{SCRIPTS}/lib.sh"; staging_compose down {flag}'],
            env={**os.environ, "STAGING_ROOT": str(tmp_path)}, capture_output=True, text=True)
        assert result.returncode != 0 and "refusing" in result.stderr


# ── release classifier ────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "docker-compose.staging.yml",
    "scripts/staging/up.sh",
    "scripts/staging/README.md",
    "services/model_gateway/litellm.staging.yaml",
])
def test_staging_only_paths_never_deploy_production(path):
    assert classify([path]) == ("documents", ())


def test_production_compose_still_takes_the_full_deploy():
    assert classify(["docker-compose.prod.yml"]) == ("full", ALL_SERVICES)
    assert classify(["docker-compose.staging.yml", "docker-compose.prod.yml"]) == ("full", ALL_SERVICES)


def test_prod_gateway_config_still_restarts_the_gateway():
    release_class, services = classify(["services/model_gateway/litellm.prod.yaml"])
    assert release_class == "domain" and "model-gateway" in services
