"""Run deploy_scoped_release.sh against stub docker/compose/aws binaries.

Proves the Agent A snapshot step never leaves Agent A stopped, never fails a
deploy on housekeeping, keeps the pre-upgrade snapshot, and is skipped when
cos-agent-a is not in the release. No real Docker, AWS or network is used.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/operations/deploy_scoped_release.sh"
REGISTRY = "332704997792.dkr.ecr.us-east-1.amazonaws.com/minimoi"
TAG = "a1b2c3d"
STAMP = "20260928T120000Z"
OLD_IMAGE = f"{REGISTRY}/cos-scheduler:agent-a-oldsha"
NEW_IMAGE = f"{REGISTRY}/cos-scheduler:agent-a-{TAG}"

DOCKER_STUB = r'''#!/bin/bash
echo "docker $*" >> "$STUB_LOG"
# The real Records admission pins the local production Docker socket.
if [ "$1" = --host ]; then
  [ "$2" = unix:///var/run/docker.sock ] || exit 1
  shift 2
fi
case "$1" in
  container)
    # Real docker: inspecting a missing container fails. The Vault container exists only when a test says so.
    [ "$2" = inspect ] && [ "$3" = minimoi-vault-ask ] && [ "${STUB_VAULT_EXISTS:-0}" != 1 ] && exit 1
    exit 0 ;;
  ps)
    [ "$2" = -a ] && [ "$3" = --format ] && [ "$4" = '{{.Names}}' ] || exit 1
    printf 'minimoi-portal\n' ;;
  login) cat >/dev/null; exit 0 ;;
  inspect)
    shift; fmt=""; target=""; targets=()
    while [ $# -gt 0 ]; do
      case "$1" in
        --format=*) fmt="${1#--format=}" ;;
        --format) shift; fmt="$1" ;;
        *) target="$1"; targets+=("$1") ;;
      esac
      shift
    done
    case "$fmt" in
      "")
        if [ "$target" = minimoi-portal ]; then
          # Existing portal is deliberately unwired: absent Records binding
          # must be admitted by the REAL reader, never a no-op substitute.
          printf '[{"Name":"/minimoi-portal","Config":{"Env":[]},"Mounts":[],"NetworkSettings":{"Networks":{}}}]\n'
        else [ "$STUB_AGENT_EXISTS" = 1 ] && exit 0; exit 1; fi ;;
      *"json .Name"*)
        for target in "${targets[@]}"; do
          if [ "$target" = minimoi-cos-agent-a ]; then
            if [ -e "$STUB_STATE/recreated" ]; then image="$STUB_NEW_IMAGE"
            elif [ "$STUB_AGENT_EXISTS" = 1 ]; then image="$STUB_OLD_IMAGE"
            else continue; fi
          elif [ "$target" = minimoi-model-gateway ]; then image="$STUB_REGISTRY/cos-scheduler:model-gateway-$STUB_TAG"
          elif [ "$target" = minimoi-german ]; then image="$STUB_REGISTRY/mein-deutsch:$STUB_TAG"
          else image="$STUB_REGISTRY/${target#minimoi-}:$STUB_TAG"; fi
          printf '{"name":"/%s","image":"%s","image_id":"sha256:%064d","running":true,"health":"healthy"}\n' "$target" "$image" 1
        done ;;
      *Config.Image*)
        if [ "$target" = minimoi-cos-agent-a ]; then
          if [ -e "$STUB_STATE/recreated" ]; then echo "$STUB_NEW_IMAGE"; else echo "$STUB_OLD_IMAGE"; fi
        else
          echo "$STUB_REGISTRY/${target#minimoi-}:$STUB_TAG"
        fi ;;
      *image.version*) echo "$STUB_OLD_VERSION" ;;
      *State.Running*) echo true ;;
      *State.Health.Status*) echo healthy ;;
      *) echo "" ;;
    esac ;;
  image) [ "$2" = inspect ] && echo "$STUB_NEW_VERSION"; exit 0 ;;
  stop) touch "$STUB_STATE/stopped" ;;
  start) touch "$STUB_STATE/restarted" ;;
  cp) [ "$STUB_FAIL_AT" = docker_cp ] && exit 1; printf 'fake-tar-%s' "$2" ;;
esac
exit 0
'''

COMPOSE_STUB = r'''#!/bin/bash
echo "docker-compose $*" >> "$STUB_LOG"
case " $* " in
  *" up "*) [ "$STUB_FAIL_AT" = up ] && exit 1; touch "$STUB_STATE/recreated" ;;
esac
exit 0
'''


def _failing_wrapper(real: str, condition: str) -> str:
    return f'''#!/bin/bash
if {condition}; then echo "stub: forced failure" >&2; exit 1; fi
exec {real} "$@"
'''


def _write_exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def harness(tmp_path):
    opt = tmp_path / "opt"
    (opt / "scripts").mkdir(parents=True)
    # Exercise the real activation reader against an isolated absent host config.
    reader = (ROOT / "scripts/operations/production_activation.py").read_text()
    (opt / "scripts/production_activation.py").write_text(reader.replace(
        "/etc/minimoi/production-activation.json",
        str(tmp_path / "etc/minimoi/production-activation.json"), 1))
    # Exercise the REAL default-off Records reader. Isolate its host binding,
    # project fixed /opt paths into this sandbox, and model root identity with
    # this fixture's UID exactly as for the real status producer below.
    records = (ROOT / "scripts/rooms/deployment.py").read_text()
    assert records.count("def read(path=PATH, *, trusted_uid=0):") == 1
    assert records.count("need(os.geteuid()==0)") == 1
    records = records.replace("/etc/minimoi/records-production.json",
        str(tmp_path / "etc/minimoi/records-production.json"), 1)
    records = records.replace("/opt/minimoi", str(opt))
    records = records.replace("def read(path=PATH, *, trusted_uid=0):",
        "def read(path=PATH, *, trusted_uid=os.getuid()):")
    records = records.replace("need(os.geteuid()==0)", "need(os.geteuid()==os.getuid())")
    (opt / "scripts/rooms").mkdir()
    (opt / "scripts/rooms/deployment.py").write_text(records)
    # Use the REAL status producer with isolated paths and fixture ownership.
    # This unprivileged sandbox represents root-owned production files using
    # its own UID; only the three exact root-identity guards are projected.
    status = (ROOT / "scripts/operations/release_status.py").read_text()
    assert status.count("s.st_uid==0") == 2
    assert status.count("need(os.geteuid()==0)") == 1
    status = status.replace("/opt/minimoi/release-status", str(opt / "release-status"))
    status = status.replace("s.st_uid==0", "s.st_uid==os.getuid()")
    status = status.replace("need(os.geteuid()==0)", "need(os.geteuid()==os.getuid())")
    # Model trusted /opt ancestry only inside this pytest-owned sandbox.
    # Linux /tmp is deliberately world-writable and is not a production parent.
    parent_loop = "for p in (root.parent,*root.parent.parents):"
    assert status.count(parent_loop) == 1
    status = status.replace(parent_loop, parent_loop +
        f"\n        if p == Path({str(tmp_path.parent)!r}):break")
    (opt / "scripts/release_status.py").write_text(status)
    for name in ("install_lesen_refresh_cron.sh", "setup_ec2_cron.sh"):
        _write_exe(opt / "scripts" / name, "#!/bin/bash\nexit 0\n")
    script = tmp_path / "deploy_scoped_release.sh"
    script.write_text(SCRIPT.read_text().replace("/opt/minimoi", str(opt)))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exe(bin_dir / "docker", DOCKER_STUB)
    _write_exe(bin_dir / "docker-compose", COMPOSE_STUB)
    for name in ("aws", "curl", "runuser", "sleep"):
        _write_exe(bin_dir / name, "#!/bin/bash\nexit 0\n")
    _write_exe(bin_dir / "date", f"#!/bin/bash\necho {STAMP}\n")
    real = {name: shutil.which(name) for name in ("sha256sum", "shasum", "mv", "du", "rm")}
    sha = real["sha256sum"] or f"{real['shasum']} -a 256"
    _write_exe(bin_dir / "sha256sum", _failing_wrapper(sha, '[ "$STUB_FAIL_AT" = sha256sum ]'))
    _write_exe(bin_dir / "mv", _failing_wrapper(
        real["mv"], '[ "$STUB_FAIL_AT" = mv ] && [[ "$1" == *state.tar.gz.partial ]]'))
    _write_exe(bin_dir / "du", _failing_wrapper(real["du"], '[ "$STUB_FAIL_AT" = du ]'))
    _write_exe(bin_dir / "rm", _failing_wrapper(
        real["rm"], '[ "$STUB_FAIL_AT" = prune ] && [ "$1" = -rf ]'))

    state = tmp_path / "state"
    state.mkdir()
    snapshots = opt / "backups" / "cos-agent-a"

    def run(*services, fail_at="", old_version="2026.9.6", new_version="2026.9.6",
            agent_exists=True):
        env = {
            "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "STUB_LOG": str(tmp_path / "calls.log"),
            "STUB_STATE": str(state),
            "STUB_FAIL_AT": fail_at,
            "STUB_AGENT_EXISTS": "1" if agent_exists else "0",
            "STUB_OLD_IMAGE": OLD_IMAGE,
            "STUB_NEW_IMAGE": NEW_IMAGE,
            "STUB_OLD_VERSION": old_version,
            "STUB_NEW_VERSION": new_version,
            "STUB_REGISTRY": REGISTRY,
            "STUB_TAG": TAG,
        }
        result = subprocess.run(
            ["bash", str(script), TAG, *services],
            env=env, capture_output=True, text=True, check=False,
        )
        log = (tmp_path / "calls.log").read_text() if (tmp_path / "calls.log").exists() else ""
        if result.returncode == 0:
            observed = json.loads((opt / "release-status/status.json").read_text())
            assert observed["status"] == "selected_images_observed", result.stderr + result.stdout
            assert observed["requested_tag"] == TAG
            assert observed["selected"] == list(services)
        return result, log

    run.state = state
    run.snapshots = snapshots
    return run


def _compose_up_ran(log: str) -> bool:
    return any(
        line.startswith("docker-compose ") and " up " in f" {line} "
        for line in log.splitlines()
    )


def _make_set(snapshots: Path, stamp: str, line: str, keep: bool = False) -> Path:
    path = snapshots / stamp
    path.mkdir(parents=True)
    (path / "SNAPSHOT").write_text(f"taken_at={stamp}\nopenclaw_line={line}\n")
    if keep:
        (path / "KEEP").write_text("pre-upgrade\n")
    return path


def test_successful_release_snapshots_then_recreates(harness):
    result, log = harness("cos-agent-a")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (harness.state / "stopped").exists()
    assert not (harness.state / "restarted").exists()
    assert log.index("docker stop") < log.index("docker cp") < log.index(" up -d")
    snap = harness.snapshots / STAMP
    assert (snap / "cos-agent-a-state.tar.gz").exists()
    assert (snap / "cos-agent-a-auth.tar.gz").exists()
    record = (snap / "SNAPSHOT").read_text()
    assert f"previous_image={OLD_IMAGE}" in record
    assert "openclaw_line=2026.9" in record
    assert "cos-agent-a-state.tar.gz" in record
    assert not (snap / "KEEP").exists()
    assert not list(snap.glob("*.partial"))


@pytest.mark.parametrize("fail_at", ["docker_cp", "mv", "sha256sum", "snapshot_write"])
def test_any_failure_after_stop_restarts_old_agent_and_skips_up(harness, fail_at):
    if fail_at == "snapshot_write":
        # A full disk on the small SNAPSHOT write: the write target is unusable.
        (harness.snapshots / STAMP / "SNAPSHOT.partial").mkdir(parents=True)

    result, log = harness("portal", "cos-agent-a", fail_at=fail_at)

    assert result.returncode != 0
    assert (harness.state / "stopped").exists()
    assert (harness.state / "restarted").exists()
    assert not _compose_up_ran(log)
    assert log.index("docker stop") < log.index("docker start")
    assert "starting the previous container again" in result.stdout
    # The incomplete set is removed so it cannot be mistaken for a snapshot.
    assert not (harness.snapshots / STAMP).exists()


def test_failed_recreate_also_restarts_old_agent(harness):
    result, log = harness("cos-agent-a", fail_at="up")

    assert result.returncode != 0
    assert (harness.state / "restarted").exists()
    # The snapshot itself completed and is kept.
    assert (harness.snapshots / STAMP / "SNAPSHOT").exists()


@pytest.mark.parametrize("fail_at", ["prune", "du"])
def test_housekeeping_failures_never_fail_the_deploy(harness, fail_at):
    for day in range(1, 8):
        _make_set(harness.snapshots, f"2026090{day}T000000Z", "2026.9")

    result, log = harness("cos-agent-a", fail_at=fail_at)

    assert result.returncode == 0, result.stderr + result.stdout
    assert _compose_up_ran(log)
    assert not (harness.state / "restarted").exists()
    if fail_at == "prune":
        assert "snapshot pruning failed" in result.stdout


def test_step_is_skipped_when_agent_a_is_not_released(harness):
    result, log = harness("portal", "cos-scheduler")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "docker stop" not in log
    assert "docker cp" not in log
    assert not harness.snapshots.exists()
    assert _compose_up_ran(log)


def test_upgrade_snapshot_is_marked_keep(harness):
    result, _ = harness("cos-agent-a", old_version="2026.7.1", new_version="2026.9.6")

    assert result.returncode == 0, result.stderr + result.stdout
    snap = harness.snapshots / STAMP
    assert "2026.7 -> 2026.9" in (snap / "KEEP").read_text()
    record = (snap / "SNAPSHOT").read_text()
    assert "openclaw_line=2026.7" in record
    assert "new_openclaw_line=2026.9" in record


def test_unknown_versions_are_kept_rather_than_rotated(harness):
    result, _ = harness("cos-agent-a", old_version="", new_version="2026.9.6")

    assert result.returncode == 0, result.stderr + result.stdout
    assert (harness.snapshots / STAMP / "KEEP").exists()


def test_pruning_keeps_other_openclaw_lines_keep_sets_and_newest_five(harness):
    pre96_keep = _make_set(harness.snapshots, "20260801T000000Z", "2026.7", keep=True)
    pre96_plain = _make_set(harness.snapshots, "20260802T000000Z", "2026.7")
    unknown = harness.snapshots / "20260803T000000Z"
    unknown.mkdir(parents=True)  # no SNAPSHOT record: never pruned
    same_line = [
        _make_set(harness.snapshots, f"202609{day:02d}T000000Z", "2026.9")
        for day in range(1, 8)
    ]

    result, _ = harness("cos-agent-a")

    assert result.returncode == 0, result.stderr + result.stdout
    assert pre96_keep.exists() and pre96_plain.exists() and unknown.exists()
    # 7 older 2026.9 sets + today's = 8; the newest 5 stay.
    kept = sorted(
        path.name for path in harness.snapshots.iterdir()
        if (path / "SNAPSHOT").exists()
        and "openclaw_line=2026.9" in (path / "SNAPSHOT").read_text()
    )
    assert kept == [p.name for p in same_line[-4:]] + [STAMP]
