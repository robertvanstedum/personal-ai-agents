"""The release scripts carry MiniMoi 1.2's layers: real scripts, stub docker, compose and aws. No host, no daemon, no network.

Opt-in is the rule under test: with no layer file a release runs today's commands exactly, and with one every path keeps
the settings, starts the companions on this release's images, and refuses before it changes anything if setup is unfinished.
"""
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCOPED = ROOT / "scripts/operations/deploy_scoped_release.sh"
MANUAL = ROOT / "scripts/deploy.sh"
MC_SCRIPT = ROOT / "scripts/operations/deploy_mc_container_ec2.sh"
COS_PROD = ROOT / "scripts/operations/cos_prod.sh"
REGISTRY = "332704997792.dkr.ecr.us-east-1.amazonaws.com/minimoi"
TAG = "a1b2c3d"
COMMIT = TAG + "e" * 33
LAYER_12 = "docker-compose.minimoi-1-2-production.yml"
LAYER_MC = "docker-compose.mc-1-2-production.yml"
LAYER_GUILDHALL = "docker-compose.guildhall-production-main.yml"
LAYER_RUNTIME = "docker-compose.runtime.prod.yml"
LAYER_RECORDS = "docker-compose.records-production-portal.yml"

COMPOSE_STUB = r'''#!/bin/bash
printf 'compose|%s|%s|%s\n' "${MINIMOI_PORTAL_IMAGE:-}" "${MINIMOI_COS_SCHEDULER_IMAGE:-}" "$*" >> "$STUB_LOG"
[ -z "${MINIMOI_PARTNERSHIP_CARD_REF:-}" ] || echo "card|$MINIMOI_PARTNERSHIP_CARD_REF" >> "$STUB_LOG"
if [ "${STUB_CONFIG_FAILS:-0}" = 1 ]; then
  case " $* " in *" config "*) echo 'required variable MINIMOI_1_2_SECRETS is missing' >&2; exit 1 ;; esac
fi
exit 0
'''

DOCKER_STUB = r'''#!/bin/bash
echo "docker $*" >> "$STUB_LOG"
case "$1" in
  container) exit 1 ;;
  exec) [ "$2 $3 $4" = "minimoi-portal printenv PARTNERSHIP_CARD_REF" ] && echo "${STUB_PORTAL_CARD_REF:-}"; exit 0 ;;
  login) cat >/dev/null; exit 0 ;;
  inspect)
    shift; fmt=""; target=""
    for arg in "$@"; do case "$arg" in --format=*) fmt="${arg#--format=}" ;; *) target="$arg" ;; esac; done
    case "$fmt" in
      *Config.Image*)
        case "$target" in
          minimoi-cos-board) [ -n "${STUB_STALE_BOARD:-}" ] && echo "$STUB_REGISTRY/portal:old" || echo "$STUB_REGISTRY/portal:$STUB_TAG" ;;
          minimoi-partnership-*) echo "$STUB_REGISTRY/cos-scheduler:$STUB_TAG" ;;
          minimoi-german) echo "$STUB_REGISTRY/mein-deutsch:$STUB_TAG" ;;
          minimoi-cos-agent-a) echo "$STUB_REGISTRY/cos-scheduler:agent-a-$STUB_TAG" ;;
          *) echo "$STUB_REGISTRY/${target#minimoi-}:$STUB_TAG" ;;
        esac ;;
      *State.Running*) [ "$target" = "${STUB_NOT_RUNNING:-}" ] && echo false || echo true ;;
      *State.Restarting*) echo false ;;
      *State.Health.Status*) echo healthy ;;
      *image.version*) echo 2026.9.6 ;;
      "") exit 1 ;;
      *) echo "" ;;
    esac ;;
  image) exit 0 ;;
esac
exit 0
'''


def _exe(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def host(tmp_path):
    """A host with the fixed paths moved into tmp_path: stubs for docker, compose, aws and the host's own python readers."""
    opt = tmp_path / "opt"
    (opt / "scripts/rooms").mkdir(parents=True)
    (opt / "docker-compose.prod.yml").write_text("services: {}\n")
    (opt / "scripts/production_activation.py").write_text("")
    (opt / "scripts/rooms/deployment.py").write_text("import os\nprint(os.environ.get('STUB_RECORDS', ''), end='')\n")
    (opt / "scripts/release_status.py").write_text(
        "import os, sys\nopen(os.environ['STUB_LOG'], 'a').write('release_status ' + sys.argv[1] + '\\n')\n")
    for name in ("install_lesen_refresh_cron.sh", "setup_ec2_cron.sh"):
        _exe(opt / "scripts" / name, "#!/bin/bash\nexit 0\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "docker", DOCKER_STUB)
    _exe(bin_dir / "docker-compose", COMPOSE_STUB)
    for name in ("curl", "runuser", "sleep"):
        _exe(bin_dir / name, "#!/bin/bash\nexit 0\n")
    _exe(bin_dir / "aws", '#!/bin/bash\necho "aws $*" >> "$STUB_LOG"\nexit 0\n')
    log = tmp_path / "calls.log"

    class Host:
        path = opt

        def place(self, *names):
            for name in names:
                (opt / name).write_text("services: {}\n")

        def run(self, source, *args, **env):
            script = tmp_path / source.name
            script.write_text(source.read_text().replace("/opt/minimoi", str(opt)))
            log.write_text("")
            base = {"PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}", "STUB_LOG": str(log),
                    "STUB_REGISTRY": REGISTRY, "STUB_TAG": TAG, "HOME": str(tmp_path), "MINIMOI_RELEASE_COMMIT": COMMIT}
            result = subprocess.run(["bash", str(script), *args], env={**base, **env}, capture_output=True, text=True,
                                    timeout=60)
            return result, log.read_text().splitlines()

    return Host()


def _compose_calls(lines):
    """Each compose call as (portal image, scheduler image, args)."""
    return [tuple(line.split("|", 3)[1:]) for line in lines if line.startswith("compose|")]


def _files(args, opt):
    words = args.split()
    return [words[i + 1].replace(f"{opt}/", "") for i, word in enumerate(words) if word == "-f"]


def _named_up(calls):
    """The services named by each `up`, by call."""
    return [args.split(" up ", 1)[1].split() for _, _, args in calls if " up " in args]


# ── G1: the layer's order, absent means unchanged ──────────────────────────────────────────────────────────────────────

def test_a_host_with_no_layer_runs_exactly_todays_commands(host):
    result, lines = host.run(SCOPED, TAG, "portal", "cos-scheduler")
    assert result.returncode == 0, result.stdout + result.stderr
    prod = f"--project-name minimoi -f {host.path}/docker-compose.prod.yml"
    assert _compose_calls(lines) == [("", "", f"{prod} pull portal cos-scheduler"),
                                     ("", "", f"{prod} up -d --no-deps --remove-orphans portal cos-scheduler")]


def test_the_manual_script_with_no_layer_runs_exactly_todays_commands(host):
    result, lines = host.run(MANUAL, TAG)
    assert result.returncode == 0, result.stdout + result.stderr
    prod = f"--project-name minimoi -f {host.path}/docker-compose.prod.yml"
    assert _compose_calls(lines) == [("", "", f"{prod} pull"), ("", "", f"{prod} up -d --remove-orphans"),
                                     ("", "", f"{prod} ps")]


@pytest.mark.parametrize("source,args", [(SCOPED, (TAG, "portal")), (MANUAL, (TAG,))])
def test_every_compose_call_has_the_layers_in_the_reviewed_order(host, source, args):
    host.place(LAYER_12, LAYER_RUNTIME, LAYER_GUILDHALL)
    result, lines = host.run(source, *args, STUB_RECORDS="MINIMOI_RECORDS_NETWORK=records-net\n")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _compose_calls(lines)
    assert calls
    expected = ["docker-compose.prod.yml", LAYER_RECORDS, LAYER_RUNTIME, LAYER_12, LAYER_GUILDHALL]
    for _, _, call in calls:
        assert _files(call, host.path) == expected, call
        assert "--profile minimoi-1-2" in call, call


@pytest.mark.parametrize("source,args", [(SCOPED, (TAG, "portal")), (MANUAL, (TAG,))])
def test_a_host_with_only_the_guildhall_or_runtime_layer_gets_no_1_2_settings(host, source, args):
    host.place(LAYER_RUNTIME, LAYER_GUILDHALL)
    result, lines = host.run(source, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    for portal, scheduler, call in _compose_calls(lines):
        assert _files(call, host.path) == ["docker-compose.prod.yml", LAYER_RUNTIME, LAYER_GUILDHALL]
        assert "minimoi-1-2" not in call and " config " not in f" {call} " and not portal and not scheduler


# ── G2: images from this release, companions never orphans ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("source,args", [(SCOPED, (TAG, "portal")), (MANUAL, (TAG,))])
def test_the_companions_images_come_from_this_release_not_the_host(host, source, args):
    host.place(LAYER_12)
    result, lines = host.run(source, *args, MINIMOI_PORTAL_IMAGE="stale:portal", MINIMOI_COS_SCHEDULER_IMAGE="stale:cos")
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _compose_calls(lines)
    assert {(p, s) for p, s, _ in calls} == {(f"{REGISTRY}/portal:{TAG}", f"{REGISTRY}/cos-scheduler:{TAG}")}


@pytest.mark.parametrize("services,companions", [
    (["portal", "cos-scheduler"], ["cos-board", "partnership-tasks", "partnership-worker"]),
    (["portal"], ["cos-board"]),                                  # switch, issues, notices, runner: the portal alone
    (["cos-scheduler"], ["partnership-tasks", "partnership-worker"]),   # vault
    (["cos-agent-a"], []),                                        # key
    (["german"], []),
])
def test_companions_follow_the_service_whose_image_they_run(host, services, companions):
    host.place(LAYER_12)
    result, lines = host.run(SCOPED, TAG, *services)
    assert result.returncode == 0, result.stdout + result.stderr
    ups = _named_up(_compose_calls(lines))
    assert ups[0] == ["-d", "--no-deps", "--remove-orphans", *services]   # the main up still removes real orphans
    assert ups[1:] == ([["-d", "--no-deps", *companions]] if companions else [])
    for name in companions:
        assert f"docker inspect --format={{{{.Config.Image}}}} minimoi-{name}" in lines
    if companions:
        assert lines.index(f"docker inspect --format={{{{.State.Running}}}} minimoi-{companions[-1]}") \
            < lines.index("docker image prune -af")


def test_the_profile_is_active_on_the_removing_up_so_no_companion_is_an_orphan(host):
    host.place(LAYER_12)
    _, lines = host.run(SCOPED, TAG, "german")
    (up,) = [args for _, _, args in _compose_calls(lines) if "--remove-orphans" in args]
    assert "--profile minimoi-1-2" in up


@pytest.mark.parametrize("env", [{"STUB_STALE_BOARD": "1"}, {"STUB_NOT_RUNNING": "minimoi-partnership-worker"}])
def test_a_drifted_or_stopped_companion_stops_the_release_before_the_prune(host, env):
    host.place(LAYER_12)
    result, lines = host.run(SCOPED, TAG, "portal", "cos-scheduler", **env)
    assert result.returncode == 1
    assert "docker image prune -af" not in lines and "release_status finish" not in lines


@pytest.mark.parametrize("source,args", [(SCOPED, (TAG, "portal", "cos-scheduler")), (MANUAL, (TAG,))])
def test_unfinished_setup_refuses_before_anything_changes(host, source, args):
    host.place(LAYER_12)
    result, lines = host.run(source, *args, STUB_CONFIG_FAILS="1")
    assert result.returncode == 1 and "Nothing was changed" in result.stderr
    assert len(_compose_calls(lines)) == 1 and " config -q" in _compose_calls(lines)[0][2]
    assert not [line for line in lines if line.startswith(("aws ", "release_status", "docker stop", "docker login"))]


# ── G1: the paths that restart one service ─────────────────────────────────────────────────────────────────────────────

def test_cos_prod_key_and_vault_only_restart_through_the_scoped_script():
    source = COS_PROD.read_text()
    assert "docker-compose" not in source and "docker compose" not in source
    assert '"$ROOT/scripts/deploy_scoped_release.sh" "$tag" cos-agent-a' in source
    assert '"$ROOT/scripts/deploy_scoped_release.sh" "$tag" cos-scheduler' in source


def test_the_mc_scripts_portal_switches_only_restart_through_the_scoped_script():
    source = MC_SCRIPT.read_text()
    bodies = {name: source[source.index(f"\n{name}() {{"):] for name in ("switch", "issues", "runner", "notices", "supervision")}
    for name, body in bodies.items():
        body = body[:body.index("\n}\n")]
        assert '"$ROOT/scripts/deploy_scoped_release.sh" "$tag" portal' in body, name
        assert "--remove-orphans" not in body, name         # a supervisor-only up can't remove what its list omits


# ── G3: MC's layer and image ───────────────────────────────────────────────────────────────────────────────────────────

def _mc_functions():
    source = MC_SCRIPT.read_text()
    start = source.index("mc_compose() {")
    return source[start:source.index("\nenv_value()", start)]


def _mc(tmp_path, call, env_file=""):
    bin_dir = tmp_path / "mc-bin"
    bin_dir.mkdir(exist_ok=True)
    _exe(bin_dir / "docker-compose", '#!/bin/bash\necho "${MC_IMAGE_TAG:-}|${MINIMOI_MC_IMAGE:-}|$*"\n')
    script = (f'set -euo pipefail\nROOT="{tmp_path}"\nMC_ENV="$ROOT/mc.env"\nCOMPOSE_FILE="$ROOT/docker-compose.mc.prod.yml"\n'
              f'MC_LAYER="$ROOT/{LAYER_MC}"\nREGISTRY=332704997792.dkr.ecr.us-east-1.amazonaws.com\n' + _mc_functions() + "\n" + call)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10,
                          env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}", **({"MINIMOI_MC_IMAGE": env_file} if env_file else {})})


def test_mc_compose_with_no_layer_runs_todays_command(tmp_path):
    out = _mc(tmp_path, 'mc_compose mc-agent-v1.0.0 pull')
    assert out.returncode == 0
    assert out.stdout.strip() == (f"mc-agent-v1.0.0||-p minimoi-mc --env-file {tmp_path}/mc.env "
                                  f"-f {tmp_path}/docker-compose.mc.prod.yml pull")


def test_mc_compose_adds_the_layer_and_the_image_of_the_tag_it_pulls(tmp_path):
    (tmp_path / LAYER_MC).write_text("services: {}\n")
    out = _mc(tmp_path, 'mc_compose mc-agent-v1.0.0 up -d --remove-orphans', env_file="stale:image")
    assert out.returncode == 0
    assert out.stdout.strip() == (
        f"mc-agent-v1.0.0|{REGISTRY.rsplit('/', 1)[0]}/minimoi/cos-scheduler:mc-agent-v1.0.0|-p minimoi-mc --env-file "
        f"{tmp_path}/mc.env -f {tmp_path}/docker-compose.mc.prod.yml -f {tmp_path}/{LAYER_MC} up -d --remove-orphans")


def test_mc_release_refreshes_a_placed_layer_and_never_creates_one(tmp_path):
    bundle = tmp_path / "mc-release"
    bundle.mkdir()
    (bundle / LAYER_MC).write_text("new\n")
    assert _mc(tmp_path, "refresh_mc_layer").returncode == 0
    assert not (tmp_path / LAYER_MC).exists()                          # absent stays absent: placing it is the opt-in
    (tmp_path / LAYER_MC).write_text("old\n")
    assert _mc(tmp_path, "refresh_mc_layer").returncode == 0
    assert (tmp_path / LAYER_MC).read_text() == "new\n"
    (bundle / LAYER_MC).unlink()                                       # an older tag's bundle has none: the layer stays
    assert _mc(tmp_path, "refresh_mc_layer").returncode == 0
    assert (tmp_path / LAYER_MC).read_text() == "new\n"


def test_mc_image_carries_the_layer_and_the_refresh_runs_before_the_pull():
    assert f"COPY --chown=root:root {LAYER_MC} /opt/minimoi/mc-release/" in \
        (ROOT / "docker/mc-agent-container/Dockerfile").read_text()
    source = MC_SCRIPT.read_text()
    assert source.index("\n  refresh_mc_layer\n") < source.index('mc_compose "$tag" pull')


def test_the_layers_classes_match_where_they_travel():
    from scripts.ci.classify_release import ALL_SERVICES, classify
    assert classify([LAYER_MC]) == ("documents", ())                   # MC's tag release, not this pipeline
    assert classify([LAYER_12]) == ("full", ALL_SERVICES)
    assert classify([LAYER_GUILDHALL]) == ("full", ALL_SERVICES)


# ── CI: refresh a layer already placed; never place one ────────────────────────────────────────────────────────────────

def _layer_step():
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    for job in workflow["jobs"].values():
        names = [step.get("name") for step in job.get("steps", [])]
        if "Refresh the production layers already switched on" in names:
            return names, job["steps"][names.index("Refresh the production layers already switched on")]["run"]
    raise AssertionError("no layer step")


def test_ci_refreshes_after_the_new_script_arrives_and_before_the_deploy():
    names, run = _layer_step()
    index = names.index("Refresh the production layers already switched on")
    assert names.index("Push docker-compose.prod.yml to EC2") < index < names.index("Deploy on EC2")
    assert LAYER_12 in run and LAYER_GUILDHALL in run
    assert LAYER_MC not in run                                          # MC's travels in MC's image
    assert ".env" not in run and "/etc/minimoi" not in run


def test_ci_never_creates_a_layer_on_a_host_that_has_not_opted_in(tmp_path):
    _, run = _layer_step()
    loop = re.search(r"for f in .*?done", run).group(0).replace("\\$", "$").replace("/opt/minimoi", str(tmp_path))
    (tmp_path / "release-layers").mkdir()
    for name in (LAYER_12, LAYER_GUILDHALL):
        (tmp_path / "release-layers" / name).write_text("release\n")
    assert subprocess.run(["bash", "-c", "set -e\n" + loop], capture_output=True, timeout=5).returncode == 0
    assert not (tmp_path / LAYER_12).exists() and not (tmp_path / LAYER_GUILDHALL).exists()
    (tmp_path / LAYER_12).write_text("older\n")
    assert subprocess.run(["bash", "-c", "set -e\n" + loop], capture_output=True, timeout=5).returncode == 0
    assert (tmp_path / LAYER_12).read_text() == "release\n"
    assert not (tmp_path / LAYER_GUILDHALL).exists()                    # Guildhall held back while 1.2 is on


# ── G10: the Board's database URL by file ──────────────────────────────────────────────────────────────────────────────

def test_the_board_reads_its_url_from_a_private_file_or_the_old_variable(tmp_path):
    from core.domain_api.contract import DomainError
    from minimoi_portal.guild_ui.board_headless_runner import database_url
    assert database_url({"DATABASE_URL": "postgresql://dev"}) == "postgresql://dev"
    path = tmp_path / "db.url"
    path.write_text("postgresql://file\n")
    path.chmod(0o600)
    assert database_url({"DATABASE_URL_FILE": str(path), "DATABASE_URL": "postgresql://ignored"}) == "postgresql://file"
    path.chmod(0o644)
    with pytest.raises(DomainError, match="private_file_required"):
        database_url({"DATABASE_URL_FILE": str(path)})
    path.write_text("\n")
    path.chmod(0o600)
    with pytest.raises(DomainError, match="database_url_file_empty"):
        database_url({"DATABASE_URL_FILE": str(path)})


# ── the card commit follows the release ───────────────────────────────────────────────────────────────────────────────────

STALE = "b" * 40


def _cards(lines):
    return {line.split("|", 1)[1] for line in lines if line.startswith("card|")}


@pytest.mark.parametrize("source,args", [(SCOPED, (TAG, "portal")), (MANUAL, (TAG,))])
def test_a_release_exports_its_own_commit_over_a_stale_env_file(host, source, args):
    host.place(LAYER_12)
    (host.path / ".env").write_text(f"MINIMOI_PARTNERSHIP_CARD_REF={STALE}\n")
    result, lines = host.run(source, *args, MINIMOI_PARTNERSHIP_CARD_REF=STALE, STUB_PORTAL_CARD_REF=STALE)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _cards(lines) == {COMMIT}


@pytest.mark.parametrize("source,args", [(SCOPED, (TAG, "portal")), (MANUAL, (TAG,))])
def test_a_release_at_the_running_tag_keeps_the_running_portals_commit(host, source, args):
    host.place(LAYER_12)
    (host.path / ".env").write_text(f"MINIMOI_PARTNERSHIP_CARD_REF={STALE}\n")
    result, lines = host.run(source, *args, MINIMOI_RELEASE_COMMIT="", STUB_PORTAL_CARD_REF=COMMIT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _cards(lines) == {COMMIT}


@pytest.mark.parametrize("source,args", [(SCOPED, (TAG, "portal")), (MANUAL, (TAG,))])
@pytest.mark.parametrize("env", [{"MINIMOI_RELEASE_COMMIT": ""}, {"MINIMOI_RELEASE_COMMIT": STALE}, {"MINIMOI_RELEASE_COMMIT": TAG}])
def test_a_portal_release_with_no_commit_for_its_tag_changes_nothing(host, source, args, env):
    host.place(LAYER_12)
    (host.path / ".env").write_text(f"MINIMOI_PARTNERSHIP_CARD_REF={STALE}\n")
    result, lines = host.run(source, *args, STUB_PORTAL_CARD_REF=STALE, **env)
    assert result.returncode == 1 and "Nothing was changed" in result.stderr
    assert _compose_calls(lines) == [] and not [line for line in lines if line.startswith("release_status")]


def test_a_release_that_leaves_the_portal_alone_needs_only_a_value_that_renders(host):
    host.place(LAYER_12)
    result, lines = host.run(SCOPED, "0ld0000", "cos-agent-a", MINIMOI_RELEASE_COMMIT="", STUB_PORTAL_CARD_REF=COMMIT,
                             STUB_TAG="0ld0000")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _cards(lines) == {COMMIT}


def test_no_layer_means_no_card_commit_and_no_look_at_the_portal(host):
    result, lines = host.run(SCOPED, TAG, "portal")
    assert result.returncode == 0, result.stdout + result.stderr
    assert _cards(lines) == set() and not [line for line in lines if "printenv" in line]


def test_ci_gives_the_full_commit_and_the_portal_refuses_a_bare_tag():
    from core.mc_tools.tasks_adapter import TasksAdapter
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "MINIMOI_RELEASE_COMMIT=${{ github.sha }} /opt/minimoi/scripts/deploy_scoped_release.sh" in workflow
    assert 'echo "sha=${GITHUB_SHA::7}"' in workflow                    # why the tag alone can't be the commit
    TasksAdapter(None, environment="prod", card_ref=COMMIT)
    with pytest.raises(ValueError, match="40-hex"):
        TasksAdapter(None, environment="prod", card_ref=TAG)


# ── a real offline render: no daemon, no pull ──────────────────────────────────────────────────────────────────────────

PROD_ENV = {"MINIMOI_MODEL_GATEWAY_KEY": "synthetic", "XAI_API_KEY": "synthetic", "ANTHROPIC_API_KEY": "synthetic",
            "OPENAI_API_KEY": "synthetic", "MINIMOI_IMAGE_TAG": TAG, "MINIMOI_MODEL_GATEWAY_RECEIPT_KEY": "synthetic",
            "COS_AGENT_A_GATEWAY_TOKEN": "synthetic"}
SETUP_ENV = {
    "MINIMOI_1_2_SECRETS": "/opt/minimoi/secrets/1-2", "MINIMOI_1_2_STATE": "/opt/minimoi/state/1-2",
    "MINIMOI_MODEL_ROUTES_DIR": "/opt/minimoi/state/1-2/model-routes", "MINIMOI_COS_PLAN_AUTH_PROFILE": "openai:synthetic",
    "MINIMOI_COS_TOOL_LISTENER_TOKEN_SHA256": "0" * 64, "MINIMOI_PARTNERSHIP_CARD_REF": "a" * 40,
    "MINIMOI_PARTNERSHIP_OWNER_SUBJECT": "owner:robert", "MINIMOI_PORTAL_IMAGE": f"{REGISTRY}/portal:{TAG}",
    "MINIMOI_COS_SCHEDULER_IMAGE": f"{REGISTRY}/cos-scheduler:{TAG}",
}


def _render(tmp_path, *files, profile=None, **extra):
    executable = shutil.which("docker-compose")
    if not executable:
        pytest.skip("standalone Compose CLI needed for the offline render")
    (tmp_path / ".env").write_text("BASE_URL=https://minimoi.ai\n")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "COMPOSE_DISABLE_ENV_FILE": "1",
           "DOCKER_HOST": "unix:///nonexistent/no-daemon.sock", "MINIMOI_ROOT": str(tmp_path), **PROD_ENV, **extra}
    argv = [executable, "--project-name", "minimoi", "--project-directory", str(ROOT)]
    for name in files:
        argv += ["-f", str(ROOT / name)]
    if profile:
        argv += ["--profile", profile]
    return subprocess.run([*argv, "config", "--format", "json"], env=env, capture_output=True, text=True, timeout=30)


def test_the_1_2_layer_renders_with_the_setup_values_and_keeps_notices_switchable(tmp_path):
    out = _render(tmp_path, "docker-compose.prod.yml", LAYER_12, profile="minimoi-1-2", **SETUP_ENV)
    assert out.returncode == 0, out.stderr
    services = json.loads(out.stdout)["services"]
    assert services["portal"]["environment"]["MINIMOI_ACTION_NOTICES"] == "on"
    assert services["partnership-tasks"]["environment"]["PARTNERSHIP_OWNER_SUBJECT"] == "owner:robert"
    assert services["portal"]["environment"]["MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_MC_FILE"] == "/run/secrets/partnership/voice-summary-mc.key"
    # docker-compose.prod.yml's empty scheduler line stays, and the file variable sits beside it.
    assert services["cos-scheduler"]["environment"]["MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS_FILE"] == "/run/secrets/partnership/voice-summary-cos.key"
    assert services["cos-scheduler"]["environment"]["MINIMOI_GATEWAY_KEY_VOICE_SUMMARY_COS"] == ""
    assert services["cos-board"]["image"] == f"{REGISTRY}/portal:{TAG}"
    assert services["partnership-worker"]["image"] == f"{REGISTRY}/cos-scheduler:{TAG}"
    assert "DATABASE_URL" not in services["cos-board"]["environment"]
    assert "MINIMOI_COS_BOARD_DATABASE_URL" not in out.stdout
    off = _render(tmp_path, "docker-compose.prod.yml", LAYER_12, profile="minimoi-1-2", MINIMOI_ACTION_NOTICES="off", **SETUP_ENV)
    assert json.loads(off.stdout)["services"]["portal"]["environment"]["MINIMOI_ACTION_NOTICES"] == "off"


def test_a_layer_without_its_setup_refuses_which_is_why_ci_must_not_place_it(tmp_path):
    out = _render(tmp_path, "docker-compose.prod.yml", LAYER_12, profile="minimoi-1-2")
    assert out.returncode != 0 and "MINIMOI_" in out.stderr
    assert _render(tmp_path, "docker-compose.prod.yml").returncode == 0  # the same host without the layer is unaffected


def test_the_whole_stack_in_order_keeps_every_layers_settings(tmp_path):
    out = _render(tmp_path, "docker-compose.prod.yml", LAYER_RECORDS, LAYER_12, LAYER_GUILDHALL, profile="minimoi-1-2",
                  MINIMOI_RECORDS_PORTAL_KEY="/opt/minimoi/records/key", MINIMOI_RECORDS_NETWORK="minimoi-records-production-1",
                  MINIMOI_COS_GUILDHALL_SECRETS="/opt/minimoi/secrets/1-2/cos-guildhall",
                  MINIMOI_COS_GUILDHALL_STATE="/opt/minimoi/state/1-2/cos-guildhall",
                  MINIMOI_MC_ROOMS_WORK_SECRETS="/opt/minimoi/secrets/1-2/mc-rooms-work",
                  MINIMOI_MC_ROOMS_TOKEN_SHA256="0" * 64, **SETUP_ENV)
    assert out.returncode == 0, out.stderr
    services = json.loads(out.stdout)["services"]
    assert {"default", "iotconnect-edge", "mc-portal", "partnership", "records-production"} <= set(services["portal"]["networks"])
    assert {"default", "vault-internal", "partnership", "records-production"} <= set(services["cos-scheduler"]["networks"])
    assert services["portal"]["environment"]["MINIMOI_GUILD_DIRECT_VOICE_PRODUCTION"] == "1"
    assert services["portal"]["environment"]["MINIMOI_MC_ROOMS_PRODUCTION"] == "1"
    assert services["cos-scheduler"]["environment"]["MINIMOI_COS_DIRECT_VOICE_PRODUCTION"] == "1"
    assert services["cos-scheduler"]["environment"]["MINIMOI_GUILDHALL_COS_PRODUCTION"] == "1"
    assert {"cos-board", "partnership-tasks", "partnership-worker"} <= set(services)


def test_mc_layer_takes_the_release_image_over_a_stale_env_file(tmp_path):
    executable = shutil.which("docker-compose")
    if not executable:
        pytest.skip("standalone Compose CLI needed for the offline render")
    env_file = tmp_path / "mc.env"
    env_file.write_text("MINIMOI_MC_IMAGE=stale:image\nMC_CONTAINER_GATEWAY_TOKEN=x\nMC_RELAY_CALLER_TOKEN=x\n"
                        "MINIMOI_MC_RUNTIME_MODEL_ACCESS=plan\nMINIMOI_MC_PLAN_MODEL=openai/gpt-6-astra\n"
                        "MINIMOI_MC_PLAN_ROUTES=openai/gpt-6-sol\nMINIMOI_MC_PLAN_AUTH_PROFILE=openai:x\n"
                        "MINIMOI_MC_MODEL_GATEWAY_KEY=x\n")
    out = subprocess.run([executable, "-p", "minimoi-mc", "--env-file", str(env_file), "-f", str(ROOT / "docker-compose.mc.prod.yml"),
                          "-f", str(ROOT / LAYER_MC), "config", "--format", "json"],
                         env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "MC_IMAGE_TAG": "mc-agent-v1.0.0",
                              "MINIMOI_MC_IMAGE": "release:image", "DOCKER_HOST": "unix:///nonexistent/no-daemon.sock"},
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    services = json.loads(out.stdout)["services"]
    assert services["mc-agent"]["image"] == "release:image" and services["mc-relay"]["image"] == "release:image"
