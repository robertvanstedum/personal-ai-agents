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
* the staging bot tokens come only from the Keychain test accounts;
* up.sh's native-poller and port guards are deterministic, build.sh never
  resets a worktree that is not its own and restores after a failed build;
* staging-only paths never trigger a production deploy.

Script tests run under the bash on PATH and again under macOS /bin/bash 3.2
(skipped with a reason where /bin/bash is not 3.2, e.g. Linux CI).
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


def _macos_bash32():
    bash = Path("/bin/bash")
    if not bash.exists():
        return None
    version = subprocess.run([str(bash), "-c", 'echo "${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]}"'],
                             capture_output=True, text=True).stdout.strip()
    return str(bash) if version.startswith("3.") else None


@pytest.fixture(params=["path-bash", "macos-bash-3.2"])
def shell(request):
    """The interpreter the staging scripts run under in this test."""
    if request.param == "path-bash":
        found = shutil.which("bash")
        if not found:
            pytest.skip("no bash on PATH")
        return found
    bash32 = _macos_bash32()
    if not bash32:
        pytest.skip("macOS /bin/bash 3.2 is not present here (Linux CI has bash 5); the 3.2 run happens on the Mac")
    return bash32


def _write_exe(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          capture_output=True, text=True, check=check)


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
    cmd = ["docker", "compose", "-p", "render", "--project-directory", str(REPO)]
    for profile in profiles:
        cmd += ["--profile", profile]
    for f in files:
        cmd += ["-f", str(f)]
    cmd += ["config", "--no-env-resolution"]
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=REPO)
    if result.returncode != 0 and ("unknown flag" in result.stderr or "is not a docker command" in result.stderr):
        pytest.skip("docker compose v2 with --no-env-resolution not available")
    if result.returncode != 0 and "env file" in result.stderr and "not found" in result.stderr:
        # Some compose versions (e.g. the CI runner's) insist that env_file paths
        # such as /opt/minimoi/.env exist even with --no-env-resolution. The
        # frozen-mount-list and YAML-level tests cover the same guarantee there;
        # this render runs on the Mac (docker compose 5.1.4) and on EC2-like hosts.
        pytest.skip("this docker compose requires env_file paths to exist: " + result.stderr.strip()[:120])
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


def _origin_main_prod_compose(tmp_path):
    shown = subprocess.run(["git", "-C", str(REPO), "show", "origin/main:docker-compose.prod.yml"],
                           capture_output=True, text=True)
    if shown.returncode != 0:
        pytest.skip("origin/main is not available in this checkout")
    base = tmp_path / "main" / "docker-compose.prod.yml"
    base.parent.mkdir()
    base.write_text(shown.stdout)
    return base, shown.stdout


def test_docker_renders_prod_exactly_like_origin_main(tmp_path):
    """The PR against the real base, not against its own copy."""
    base, text = _origin_main_prod_compose(tmp_path)
    if "MINIMOI_ROOT" in text:
        pytest.skip("origin/main already has MINIMOI_ROOT (this change is merged); "
                    "test_prod_root_parameter_is_a_no_op_when_unset guards it from here on")
    main_render = _compose_config(base)
    assert _compose_config(PROD) == main_render
    assert _compose_config(PROD, env_extra={"MINIMOI_ROOT": ""}) == main_render


def test_prod_root_parameter_is_a_no_op_when_unset(tmp_path):
    """The same file with every ${MINIMOI_ROOT:-/opt/minimoi} spelled out renders identically."""
    literal = tmp_path / "literal" / "docker-compose.prod.yml"
    literal.parent.mkdir()
    literal.write_text(PROD.read_text().replace(ROOT_VAR, "/opt/minimoi"))
    assert _compose_config(PROD) == _compose_config(literal)


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


def test_lib_host_port_list_matches_the_compose_files():
    published = set()
    for path in (PROD, STAGING):
        for service in _load(path)["services"].values():
            for port in service.get("ports", []):
                ip, host, _container = port.split(":")
                assert ip == "127.0.0.1"
                published.add(host)
    listed = re.search(r'^STAGING_HOST_PORTS="([^"]+)"', (SCRIPTS / "lib.sh").read_text(), re.M).group(1).split()
    assert sorted(listed) == sorted(published)


def test_staging_volumes_are_external_and_staging_named():
    data = _load(STAGING)
    assert set(data["volumes"]) == set(_load(PROD)["volumes"])
    for key, volume in data["volumes"].items():
        assert volume == {"external": True, "name": f"minimoi-staging-{key}"}
    assert data["networks"] == {
        "iotconnect-edge": {"name": "minimoi-staging-iotconnect-edge"},
        # Master Craftsman's internal network (MC spec v0.9 §3; tests/test_staging_mc_stage_a.py).
        "mc-net": {"name": "minimoi-staging-mc-net", "internal": True,
                   "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}},
        "mc-front": {"name": "minimoi-staging-mc-front", "internal": True,
                     "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"}},
        # Rooms' Records network (Guild 1.1 slice 4; docker-compose.records.yml): internal.
        "records-net": {"name": "minimoi-staging-records", "internal": True},
    }


def test_staging_override_holds_no_production_path_or_secret():
    text = STAGING.read_text()
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "/opt/minimoi" not in body
    assert "env_file" not in body
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_POLLING_BOT_TOKEN", "AWS_", "_PROD", "iotconnect:"):
        assert name not in body, name
    gateway = _load(STAGING)["services"]["model-gateway"]
    assert gateway["volumes"] == [
        "${MINIMOI_ROOT:?set MINIMOI_ROOT (scripts/staging/lib.sh)}/config/litellm.staging.yaml:/app/config.yaml:ro",
        # usage-record U1 (staging only): the recorder's code, read-only, and the usage store.
        "${MINIMOI_ROOT}/config/usage/usage_record.py:/app/usage_record.py:ro",
        "${MINIMOI_ROOT}/config/usage/litellm_recorder.py:/app/usage_recorder.py:ro",
        "${MINIMOI_ROOT}/data/usage:/app/usage-data"]


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
    # Production's names, plus exactly one staging-only route: Master
    # Craftsman's (MC stage C; tests/test_staging_mc_stage_c.py).
    assert [m["model_name"] for m in staging["model_list"]] == \
        [m["model_name"] for m in prod["model_list"]] + ["minimoi-mc-agent"]
    for block in ("router_settings", "general_settings"):
        assert staging[block] == prod[block], block
    # litellm_settings: production's, plus exactly the staging-only usage recorder (usage-record U1).
    staged = dict(staging["litellm_settings"])
    assert staged.pop("callbacks") == prod["litellm_settings"]["callbacks"] + ["usage_recorder.usage_recorder"]
    assert staged == {k: v for k, v in prod["litellm_settings"].items() if k != "callbacks"}


def test_staging_cos_agent_route_is_haiku_and_the_rest_follow_the_dev_gateway():
    staging, prod, dev = _routes(GATEWAY_STAGING), _routes(GATEWAY_PROD), _routes(GATEWAY_DEV)
    agent = staging["minimoi-cos-agent"]
    haiku = "anthropic/claude-haiku-4-5-20251001"
    assert agent["litellm_params"]["model"] == haiku
    # Gateway-only provider key names on staging (the pass-through gap; MC stage C).
    assert agent["litellm_params"]["api_key"] == "os.environ/GATEWAY_ANTHROPIC_API_KEY"
    assert agent["model_info"]["base_model"] == haiku
    assert set(agent["model_info"]) == set(prod["minimoi-cos-agent"]["model_info"])
    assert agent["model_info"]["fallback_position"] == 0
    renamed = {"os.environ/GATEWAY_ANTHROPIC_API_KEY": "os.environ/ANTHROPIC_API_KEY",
               "os.environ/GATEWAY_XAI_API_KEY": "os.environ/XAI_API_KEY"}
    for name in staging:
        if name == "minimoi-cos-agent" or name.startswith("minimoi-mc-"):
            continue
        route = __import__("copy").deepcopy(staging[name])
        route["litellm_params"]["api_key"] = renamed[route["litellm_params"]["api_key"]]
        assert route == dev[name], name


# ── seed safety ───────────────────────────────────────────────────────────────

def _code_lines(path):
    return [line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")]


def test_seed_never_moves_or_deletes():
    code = "\n".join(_code_lines(SCRIPTS / "seed.sh"))
    assert not re.search(r"(^|[\s;&|(])mv\s", code)
    assert not re.search(r"\brm\s+-[a-zA-Z]*r", code)
    assert "--delete" not in code and "--remove-source-files" not in code
    assert ":ro" in code  # the postgres source volume is mounted read-only


def test_scripts_parse_under_each_shell(shell):
    for script in sorted(SCRIPTS.glob("*.sh")):
        text = script.read_text()
        assert text.startswith("#!/bin/bash"), script.name
        for construct in ("mapfile", "readarray", "declare -A", ",,}", "^^}"):
            assert construct not in text, (script.name, construct)
        subprocess.run([shell, "-n", str(script)], check=True)


def test_no_script_pipes_into_an_early_exiting_reader():
    """`cmd | grep -q` / `| head` return 141 under pipefail when the reader exits first (review B1)."""
    for script in sorted(SCRIPTS.glob("*.sh")):
        for number, line in enumerate(_code_lines(script), 1):
            assert not re.search(r"\|\s*grep\s+-[A-Za-z]*[qm]", line), (script.name, number, line)
            assert not re.search(r"\|\s*head\b", line), (script.name, number, line)


def _tool(name):
    if not shutil.which(name):
        pytest.skip(f"{name} not installed")


def test_seed_copies_sources_without_changing_them(tmp_path, shell):
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
    result = subprocess.run([shell, str(seed)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    after = {p: p.read_bytes() for p in before}
    assert after == before  # nothing read was changed, moved or removed
    assert (root / "data/guild/build_queue.json").read_text() == '[{"id": 1}]'
    assert (root / "data/german/sessions/s1.json").read_text() == "live"
    assert (root / "data/german/config/c.json").read_text() == "base"
    for record in ("SEEDED_FROM.txt", "SHA256SUMS.sources", "SHA256SUMS.seed"):
        assert (root / record).stat().st_size > 0, record
    assert oct(root.stat().st_mode & 0o777) == "0o700"

    drift = subprocess.run([shell, str(seed), "--drift"], env=env, capture_output=True, text=True)
    assert drift.returncode == 0, drift.stdout + drift.stderr
    (german / "sessions" / "s1.json").write_text("changed")
    drift = subprocess.run([shell, str(seed), "--drift"], env=env, capture_output=True, text=True)
    assert drift.returncode == 1 and "changed: " in drift.stdout

    again = subprocess.run([shell, str(seed)], env=env, capture_output=True, text=True)
    assert again.returncode != 0 and "not empty" in again.stderr


# ── secrets ───────────────────────────────────────────────────────────────────

def test_env_sh_copies_by_name_and_never_prints_or_writes_forbidden_names(tmp_path, shell):
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
    result = subprocess.run([shell, str(SCRIPTS / "env.sh"), "--source-env", str(source)],
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

    again = subprocess.run([shell, str(SCRIPTS / "env.sh"), "--source-env", str(source)],
                           env=env, capture_output=True, text=True)
    assert again.returncode != 0 and "--force" in again.stderr


def test_env_sh_never_reads_production_telegram_accounts():
    text = (SCRIPTS / "env.sh").read_text()
    assert '"cos_test_bot_token"' in text and '"system_test_bot_token"' in text
    for account in ('"cos_bot_token")', '"system_bot_token")', '"polling_bot_token")', '"bot_token")'):
        assert account not in text


def test_lib_refuses_to_remove_volumes(tmp_path, shell):
    for flag in ("-v", "--volumes"):
        result = subprocess.run(
            [shell, "-c", f'source "{SCRIPTS}/lib.sh"; staging_compose down {flag}'],
            env={**os.environ, "STAGING_ROOT": str(tmp_path)}, capture_output=True, text=True)
        assert result.returncode != 0 and "refusing" in result.stderr


# ── Telegram bot tokens: Keychain test accounts only (review B2) ─────────────

ROOT_ENV_BASE = [
    "ANTHROPIC_API_KEY=a", "XAI_API_KEY=x", "MINIMOI_MODEL_GATEWAY_KEY=g", "MINIMOI_MODEL_GATEWAY_RECEIPT_KEY=r",
    "COS_AGENT_A_GATEWAY_TOKEN=t", "DATABASE_URL=postgresql://u:p@localhost:5432/personal_agents",
    "POSTGRES_PASSWORD=p", "PORTAL_SECRET_KEY=" + "k" * 40,
]
ROOT_TOKEN = "root-env-production-token-never-copied"
KEYCHAIN_COS = "keychain-cos-test-token"
KEYCHAIN_SYSTEM = "keychain-system-test-token"


def _fake_security(bin_dir: Path):
    """A `security` that knows only the two Telegram TEST accounts."""
    _write_exe(bin_dir / "security", f"""#!/bin/sh
service=""; account=""
while [ $# -gt 0 ]; do
  case "$1" in -s) service="$2"; shift ;; -a) account="$2"; shift ;; esac
  shift
done
case "$service/$account" in
  telegram/cos_test_bot_token) echo "{KEYCHAIN_COS}" ;;
  telegram/system_test_bot_token) echo "{KEYCHAIN_SYSTEM}" ;;
  *) exit 44 ;;
esac
""")


def _run_env_sh(shell, tmp_path, *, bots_on, keychain):
    if not Path("/usr/bin/python3").exists():
        pytest.skip("/usr/bin/python3 missing")
    source = tmp_path / "root.env"
    source.write_text("\n".join(ROOT_ENV_BASE + [
        f"TELEGRAM_COS_BOT_TOKEN={ROOT_TOKEN}-cos", f"TELEGRAM_SYSTEM_BOT_TOKEN={ROOT_TOKEN}-system",
        f"TELEGRAM_BOT_TOKEN={ROOT_TOKEN}-prod", f"TELEGRAM_POLLING_BOT_TOKEN={ROOT_TOKEN}-poll",
        f"TELEGRAM_EXTRA_TOKEN_X={ROOT_TOKEN}-extra",
    ]) + "\n")
    root = tmp_path / "staging-root"
    if bots_on:
        (root / "state").mkdir(parents=True)
        (root / "state" / "bots.on").touch()
    bin_dir = tmp_path / "bin"
    env = {**os.environ, "STAGING_ROOT": str(root), "STAGING_GERMAN_PLIST": str(tmp_path / "none.plist")}
    if keychain:
        _fake_security(bin_dir)
        env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        env["STAGING_NO_KEYCHAIN"] = "0"
    else:
        env["STAGING_NO_KEYCHAIN"] = "1"
    result = subprocess.run([shell, str(SCRIPTS / "env.sh"), "--source-env", str(source)],
                            env=env, capture_output=True, text=True)
    return result, root


def _written(root):
    text = (root / ".env").read_text()
    return dict(line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#")), text


def _sources(root):
    path = root / "env.sources"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    return dict(line.split(" ", 1) for line in path.read_text().splitlines() if line and not line.startswith("#"))


def test_env_sh_copy_list_has_no_telegram_token():
    text = (SCRIPTS / "env.sh").read_text()
    copied = re.search(r"^COPIED = \{(.*?)^\}", text, re.M | re.S).group(1)
    assert not re.search(r'"TELEGRAM[A-Z_]*TOKEN', copied)


def test_env_sh_ignores_root_env_bot_tokens_when_bots_are_off(tmp_path, shell):
    result, root = _run_env_sh(shell, tmp_path, bots_on=False, keychain=True)
    assert result.returncode == 0, result.stderr
    written, text = _written(root)
    assert ROOT_TOKEN not in text
    assert not any(name.startswith("TELEGRAM") and "TOKEN" in name for name in written)
    assert ROOT_TOKEN not in result.stdout + result.stderr
    assert "TELEGRAM_COS_BOT_TOKEN: in the root .env, IGNORED" in result.stdout
    assert "TELEGRAM_COS_BOT_TOKEN: not written (bots off" in result.stdout
    sources = _sources(root)
    assert set(sources) == set(written)
    assert not any("TOKEN" in name and name.startswith("TELEGRAM") for name in sources)
    assert sources["XAI_API_KEY"] == "root-env" and sources["MINIMOI_ROLE"] == "env.sh"


def test_env_sh_takes_bot_tokens_from_the_keychain_test_accounts_only(tmp_path, shell):
    result, root = _run_env_sh(shell, tmp_path, bots_on=True, keychain=True)
    assert result.returncode == 0, result.stderr
    written, text = _written(root)
    assert ROOT_TOKEN not in text
    assert written["TELEGRAM_COS_BOT_TOKEN"] == f"'{KEYCHAIN_COS}'"
    assert written["TELEGRAM_SYSTEM_BOT_TOKEN"] == f"'{KEYCHAIN_SYSTEM}'"
    assert "TELEGRAM_BOT_TOKEN" not in written and "TELEGRAM_EXTRA_TOKEN_X" not in written
    assert KEYCHAIN_COS not in result.stdout + result.stderr
    sources = _sources(root)
    assert sources["TELEGRAM_COS_BOT_TOKEN"] == "keychain:telegram/cos_test_bot_token"
    assert sources["TELEGRAM_SYSTEM_BOT_TOKEN"] == "keychain:telegram/system_test_bot_token"
    assert KEYCHAIN_COS not in (root / "env.sources").read_text()


def test_env_sh_never_falls_back_to_root_env_bot_tokens(tmp_path, shell):
    result, root = _run_env_sh(shell, tmp_path, bots_on=True, keychain=False)
    assert result.returncode != 0
    assert "TELEGRAM_COS_BOT_TOKEN (Keychain telegram/cos_test_bot_token" in result.stderr
    assert not (root / ".env").exists() and not (root / "env.sources").exists()


def _lib(shell, root, script, extra_env=None):
    env = {**os.environ, "STAGING_ROOT": str(root), **(extra_env or {})}
    return subprocess.run([shell, "-c", f'source "{SCRIPTS}/lib.sh"; {script}'],
                          env=env, capture_output=True, text=True)


def test_bot_token_problems_checks_names_against_bots_on_and_sources(tmp_path, shell):
    root = tmp_path / "root"
    (root / "state").mkdir(parents=True)
    env_file, sources = root / ".env", root / "env.sources"
    env_file.write_text("XAI_API_KEY='x'\n")
    sources.write_text("XAI_API_KEY root-env\n")
    assert _lib(shell, root, "bot_token_problems").stdout == ""
    env_file.write_text("XAI_API_KEY='x'\nTELEGRAM_COS_BOT_TOKEN='t'\n")
    assert "while the bots are off" in _lib(shell, root, "bot_token_problems").stdout
    (root / "state" / "bots.on").touch()
    out = _lib(shell, root, "bot_token_problems").stdout
    assert "TELEGRAM_SYSTEM_BOT_TOKEN is not in" in out
    assert "TELEGRAM_COS_BOT_TOKEN source is 'unrecorded'" in out
    env_file.write_text("TELEGRAM_COS_BOT_TOKEN='t'\nTELEGRAM_SYSTEM_BOT_TOKEN='s'\n")
    sources.write_text("TELEGRAM_COS_BOT_TOKEN root-env\nTELEGRAM_SYSTEM_BOT_TOKEN keychain:telegram/system_test_bot_token\n")
    out = _lib(shell, root, "bot_token_problems").stdout
    assert out.strip() == ("TELEGRAM_COS_BOT_TOKEN source is 'root-env' in " + str(sources)
                           + ", expected keychain:telegram/cos_test_bot_token (rerun env.sh --force)")
    sources.write_text("TELEGRAM_COS_BOT_TOKEN keychain:telegram/cos_test_bot_token\n"
                       "TELEGRAM_SYSTEM_BOT_TOKEN keychain:telegram/system_test_bot_token\n")
    assert _lib(shell, root, "bot_token_problems").stdout == ""


# ── up.sh guards with fake docker / launchctl / lsof ─────────────────────────

FAKE_DOCKER = """#!/bin/sh
# Fake docker: volumes exist, no container of the staging names exists,
# `ps --filter publish=P` prints $FAKE/ps.P, compose calls are logged.
case "$1" in
  volume) exit 0 ;;
  inspect) exit 1 ;;
  ps)
    for a in "$@"; do case "$a" in publish=*) p="${a#publish=}" ;; esac; done
    [ -n "$p" ] && [ -f "$FAKE/ps.$p" ] && cat "$FAKE/ps.$p"
    exit 0 ;;
  compose) echo "$*" >> "$FAKE/compose.log"; exit 0 ;;
esac
exit 0
"""
FAKE_LSOF = """#!/bin/sh
for a in "$@"; do case "$a" in -iTCP:*) p="${a#-iTCP:}" ;; esac; done
[ -f "$FAKE/lsof.$p" ] || exit 1
echo "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME"
cat "$FAKE/lsof.$p"
"""
# Many lines, the native poller, then far more than a pipe buffer: with
# `launchctl list | grep -q` under pipefail this reproduced the 141 false pass.
FAKE_LAUNCHCTL_POLLER = """#!/bin/sh
awk 'BEGIN { for (i = 0; i < 2000; i++) print "-\\t0\\tcom.apple.before." i;
             print "123\\t0\\tcom.vanstedum.cos-bot";
             for (i = 0; i < 50000; i++) print "-\\t0\\tcom.apple.after." i }'
"""
FAKE_LAUNCHCTL_CLEAN = """#!/bin/sh
awk 'BEGIN { for (i = 0; i < 50000; i++) print "-\\t0\\tcom.apple.other." i }'
"""


def _release_repo(path: Path):
    path.mkdir(parents=True)
    (path / "docker-compose.prod.yml").write_text("services: {}\n")
    (path / "docker-compose.staging.yml").write_text("services: {}\n")
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "release")
    return _git(path, "rev-parse", "HEAD").stdout.strip()


def _staging_world(tmp_path, *, launchctl=FAKE_LAUNCHCTL_CLEAN, bots_on=False, tokens=False):
    for tool in ("git", "awk", "lsof"):
        if tool != "lsof":
            _tool(tool)
    root, release, fake = tmp_path / "root", tmp_path / "release", tmp_path / "fake"
    sha = _release_repo(release)
    for rel in ("data/curator_history.json", "data/curator_costs.json", "auth/users.json", "auth/guests.json",
                "cos_memory.md", "data/model_gateway_receipts.jsonl", "data/guild/cos_context.json",
                "data/guild/build_queue.json", "config/litellm.staging.yaml",
                "config/usage/usage_record.py", "config/usage/litellm_recorder.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    for rel in ("data/curator", "data/curator_archive", "data/interests", "data/research-intelligence",
                "data/german", "data/portuguese", "data/guild", "data/usage", "data/usage/portal", "data/workshops", "docs/design", "docs/specs", "agent_logs",
                "state"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    for writer in ("cos-bot", "cos-scheduler"):                      # each root writer's own usage folder
        (root / "data" / "usage" / writer).mkdir(parents=True, exist_ok=True)
    names = ["XAI_API_KEY='x'"]
    sources = ["XAI_API_KEY root-env"]
    if tokens:
        names += ["TELEGRAM_COS_BOT_TOKEN='c'", "TELEGRAM_SYSTEM_BOT_TOKEN='s'"]
        sources += ["TELEGRAM_COS_BOT_TOKEN keychain:telegram/cos_test_bot_token",
                    "TELEGRAM_SYSTEM_BOT_TOKEN keychain:telegram/system_test_bot_token"]
    (root / ".env").write_text("\n".join(names) + "\n")
    (root / ".env").chmod(0o600)
    (root / "env.sources").write_text("\n".join(sources) + "\n")
    (root / "release.env").write_text("MINIMOI_IMAGE_TAG=abc1234\n")
    (root / "RELEASE").write_text(f"sha={sha}\ntag=abc1234\n")
    if bots_on:
        (root / "state" / "bots.on").touch()
    fake.mkdir()
    bin_dir = tmp_path / "bin"
    _write_exe(bin_dir / "docker", FAKE_DOCKER)
    _write_exe(bin_dir / "lsof", FAKE_LSOF)
    _write_exe(bin_dir / "launchctl", launchctl)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE": str(fake),
           "STAGING_ROOT": str(root), "STAGING_RELEASE_DIR": str(release)}
    return env, fake, release


def _up(shell, env, *args):
    return subprocess.run([shell, str(SCRIPTS / "up.sh"), *args], env=env, capture_output=True, text=True)


def test_up_refuses_bots_while_a_native_poller_is_loaded_every_time(tmp_path, shell):
    env, fake, _release = _staging_world(tmp_path, launchctl=FAKE_LAUNCHCTL_POLLER, bots_on=True, tokens=True)
    for attempt in range(25):
        result = _up(shell, env)
        assert result.returncode != 0, (attempt, result.stdout, result.stderr)
        assert "native test-bot poller is still loaded" in result.stderr, (attempt, result.stderr)
    assert not (fake / "compose.log").exists()


def test_up_refuses_bots_when_launchctl_fails(tmp_path, shell):
    env, fake, _release = _staging_world(tmp_path, launchctl="#!/bin/sh\nexit 3\n", bots_on=True, tokens=True)
    result = _up(shell, env)
    assert result.returncode != 0 and "'launchctl list' failed" in result.stderr
    assert not (fake / "compose.log").exists()


def test_up_refuses_bots_without_keychain_tokens(tmp_path, shell):
    env, fake, _release = _staging_world(tmp_path, bots_on=True, tokens=False)
    result = _up(shell, env)
    assert result.returncode != 0 and "TELEGRAM_COS_BOT_TOKEN is not in" in result.stderr
    assert not (fake / "compose.log").exists()


def test_up_starts_bots_when_every_guard_passes(tmp_path, shell):
    env, fake, _release = _staging_world(tmp_path, bots_on=True, tokens=True)
    result = _up(shell, env)
    assert result.returncode == 0, result.stderr
    assert "--profile bots up -d --no-build --remove-orphans" in (fake / "compose.log").read_text()


def test_up_refuses_a_native_port_holder_and_names_it(tmp_path, shell):
    env, fake, _release = _staging_world(tmp_path)
    (fake / "lsof.5001").write_text("Python 4242 robert 5u IPv4 0t0 TCP 127.0.0.1:5001 (LISTEN)\n")
    (fake / "lsof.8769").write_text("limactl 99 robert 5u IPv4 0t0 TCP 127.0.0.1:8769 (LISTEN)\n")
    (fake / "ps.8769").write_text("minimoi-cos-dev personal-ai-agents\n")
    result = _up(shell, env)
    assert result.returncode != 0
    assert "5001: native process Python(4242)" in result.stderr
    assert "8769: container minimoi-cos-dev (project personal-ai-agents)" in result.stderr
    assert not (fake / "compose.log").exists()

    # --allow-holder covers only the listed port.
    result = _up(shell, env, "--allow-holder", "5001")
    assert result.returncode != 0 and "8769: container minimoi-cos-dev" in result.stderr
    assert "5001:" not in result.stderr

    (fake / "ps.8769").write_text("minimoi-cos-scheduler minimoi-staging\n")
    result = _up(shell, env, "--allow-holder", "5001,8767,8770")
    assert result.returncode == 0, result.stderr
    assert "port 5001 is held by native process Python(4242) (allowed" in result.stdout
    assert "up -d --no-build --remove-orphans" in (fake / "compose.log").read_text()


def test_up_refuses_a_stale_forward_and_an_unknown_allow_port(tmp_path, shell):
    env, fake, _release = _staging_world(tmp_path)
    (fake / "lsof.14000").write_text("limactl 99 robert 5u IPv4 0t0 TCP 127.0.0.1:14000 (LISTEN)\n")
    result = _up(shell, env)
    assert result.returncode != 0 and "14000: forwarder limactl(99) with no running container" in result.stderr
    result = _up(shell, env, "--allow-holder", "9999")
    assert result.returncode != 0 and "not a staging host port" in result.stderr


def test_up_and_down_refuse_when_the_release_worktree_left_the_pinned_sha(tmp_path, shell):
    env, fake, release = _staging_world(tmp_path)
    (release / "extra.txt").write_text("x")
    _git(release, "add", "-A")
    _git(release, "commit", "-qm", "moved")
    for script in ("up.sh", "down.sh"):
        result = subprocess.run([shell, str(SCRIPTS / script)], env=env, capture_output=True, text=True)
        assert result.returncode != 0 and "but RELEASE pins" in result.stderr, script
    assert not (fake / "compose.log").exists()


# ── build.sh: never reset a foreign worktree; restore after a failed build ───

FAKE_BUILD_DOCKER = """#!/bin/sh
case "$1" in
  version) echo arm64 ;;
  build) [ -f "$FAKE/fail-build" ] && exit 1; exit 0 ;;
  image) shift 2
    case "$*" in
      *"{{.Id}} {{.Architecture}}"*) echo "sha256:img arm64" ;;
      *"{{.Id}}"*) echo "sha256:pg" ;;
    esac
    exit 0 ;;
esac
exit 0
"""


def _source_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "docker-compose.prod.yml").write_text("services: {}\n")
    (repo / "docker-compose.staging.yml").write_text("services: {}\n")
    (repo / "services/model_gateway").mkdir(parents=True)
    (repo / "services/model_gateway/litellm.staging.yaml").write_text("model_list: []\n")
    (repo / "services/usage").mkdir(parents=True)
    for name in ("usage_record.py", "litellm_recorder.py"):
        (repo / "services/usage" / name).write_text("# usage\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "one")
    first = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "next.txt").write_text("two")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "two")
    second = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo, first, second


def _build(shell, tmp_path, repo, release_dir, ref, fake=None):
    fake = fake or tmp_path / "fake"
    fake.mkdir(exist_ok=True)
    bin_dir = tmp_path / "bin"
    _write_exe(bin_dir / "docker", FAKE_BUILD_DOCKER)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE": str(fake),
           "STAGING_ROOT": str(tmp_path / "root"), "STAGING_RELEASE_DIR": str(release_dir),
           "STAGING_REPO": str(repo), "TMPDIR": str(tmp_path)}
    return subprocess.run([shell, str(SCRIPTS / "build.sh"), ref, "--no-fetch"],
                          env=env, capture_output=True, text=True)


def test_build_refuses_a_release_dir_inside_a_checkout(tmp_path, shell):
    _tool("git")
    repo, first, second = _source_repo(tmp_path)
    (repo / "sub").mkdir()
    for target in (repo / "sub", repo / "not-yet" / "release", repo):
        result = _build(shell, tmp_path, repo, target, first)
        assert result.returncode != 0, target
        assert "refusing" in result.stderr, (target, result.stderr)
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == second
    assert _git(repo, "symbolic-ref", "-q", "HEAD", check=False).returncode == 0  # still on its branch


def test_build_refuses_a_worktree_it_did_not_create(tmp_path, shell):
    _tool("git")
    repo, first, second = _source_repo(tmp_path)
    other = tmp_path / "someone-elses-worktree"
    _git(repo, "worktree", "add", "-q", "-b", "feature", str(other), second)
    result = _build(shell, tmp_path, repo, other, first)
    assert result.returncode != 0 and "not the staging release worktree" in result.stderr
    assert _git(other, "rev-parse", "HEAD").stdout.strip() == second


def test_build_restores_the_release_worktree_after_a_failed_build(tmp_path, shell):
    _tool("git")
    repo, first, second = _source_repo(tmp_path)
    release = tmp_path / "wt" / "staging-release"
    fake = tmp_path / "fake"
    result = _build(shell, tmp_path, repo, release, first, fake)
    assert result.returncode == 0, result.stderr
    root = tmp_path / "root"
    assert f"sha={first}" in (root / "RELEASE").read_text()
    assert _git(release, "rev-parse", "HEAD").stdout.strip() == first

    (fake / "fail-build").touch()
    result = _build(shell, tmp_path, repo, release, second, fake)
    assert result.returncode != 0
    assert "restored to the pinned release" in result.stderr
    assert _git(release, "rev-parse", "HEAD").stdout.strip() == first
    assert f"sha={first}" in (root / "RELEASE").read_text()
    failed = (root / "state" / "build.failed").read_text()
    assert f"attempted_sha={second}" in failed and f"restored_to={first}" in failed

    env = {**os.environ, "STAGING_ROOT": str(root), "STAGING_RELEASE_DIR": str(release)}
    ok = subprocess.run([shell, "-c", f'source "{SCRIPTS}/lib.sh"; require_release_files; release_mismatch'],
                        env=env, capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout == ""

    (fake / "fail-build").unlink()
    result = _build(shell, tmp_path, repo, release, second, fake)
    assert result.returncode == 0, result.stderr
    assert not (root / "state" / "build.failed").exists()
    assert _git(release, "rev-parse", "HEAD").stdout.strip() == second


# ── jobs.sh ──────────────────────────────────────────────────────────────────

def test_jobs_curator_refuses_instead_of_a_silent_no_op(tmp_path, shell):
    fake = tmp_path / "fake"
    fake.mkdir()
    bin_dir = tmp_path / "bin"
    _write_exe(bin_dir / "docker", '#!/bin/sh\necho "$*" >> "$FAKE/docker.log"\n')
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE": str(fake),
           "STAGING_ROOT": str(tmp_path / "root")}
    result = subprocess.run([shell, str(SCRIPTS / "jobs.sh"), "curator"], env=env, capture_output=True, text=True)
    assert result.returncode == 3
    assert "refused" in result.stderr and "MINIMOI_ROLE=standby" in result.stderr
    assert not (fake / "docker.log").exists()


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
