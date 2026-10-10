"""The CoS turn log on staging (Spec 160 path (a), first part): Confer voice
transcripts. docker-compose.staging-cos-turns.yml gives cos-scheduler
COS_TURNS_DIR and the data/cos-turns mount. It is opt-in (#281 review):
lib.sh includes it only while state/cos.turns says "on", and `cos.sh turns
on|off` switches it without a rebuild. build.sh makes the folder (0700).
Fake docker only."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from test_staging_cos_key import FAKE_DOCKER_COS, _compose_lines, _cos
from test_staging_environment import (  # noqa: F401  (shell is a fixture)
    PROD, SCRIPTS, STAGING, _compose_config, _git, _load, _staging_world, _write_exe, shell)

REPO = Path(__file__).resolve().parent.parent
OVERLAY = REPO / "docker-compose.staging-cos-turns.yml"


def test_the_overlay_adds_only_cos_schedulers_turn_log():
    assert _load(OVERLAY) == {"services": {"cos-scheduler": {
        "environment": ["COS_TURNS_DIR=/app/data/cos-turns"],
        "volumes": ["${MINIMOI_ROOT:?}/data/cos-turns:/app/data/cos-turns"],
    }}}
    extra = {"MINIMOI_ROOT": "/Users/x/minimoi-staging", "MINIMOI_IMAGE_TAG": "abc1234"}
    with_log = yaml.safe_load(_compose_config(PROD, STAGING, OVERLAY, env_extra=extra))["services"]
    without = yaml.safe_load(_compose_config(PROD, STAGING, env_extra=extra))["services"]
    cos = with_log["cos-scheduler"]
    assert cos["environment"]["COS_TURNS_DIR"] == "/app/data/cos-turns"
    mounts = {(v["source"], v["target"]) for v in cos["volumes"]}
    assert ("/Users/x/minimoi-staging/data/cos-turns", "/app/data/cos-turns") in mounts
    for name in without:
        if name != "cos-scheduler":
            assert with_log[name] == without[name], name
    assert "COS_TURNS_DIR" not in without["cos-scheduler"]["environment"]


def test_production_does_not_write_the_turn_log():
    assert "COS_TURNS_DIR" not in PROD.read_text()


def _world(tmp_path, *, with_overlay, turns=None):
    env, fake, release = _staging_world(tmp_path)
    _write_exe(Path(env["PATH"].split(":")[0]) / "docker", FAKE_DOCKER_COS)
    root = Path(env["STAGING_ROOT"])
    names = ["docker-compose.staging-keys.yml", "docker-compose.staging-cos-key.yml"]
    if with_overlay:
        names.append("docker-compose.staging-cos-turns.yml")
    for name in names:
        (release / name).write_text("services: {}\n")
    _git(release, "add", "-A")
    _git(release, "commit", "-qm", "c")
    sha = _git(release, "rev-parse", "HEAD").stdout.strip()
    (root / "RELEASE").write_text(f"sha={sha}\ntag=abc1234\n")
    with (root / ".env").open("a") as f:
        f.write("MINIMOI_MODEL_GATEWAY_KEY='cos-master-key'\nCOS_AGENT_A_GATEWAY_TOKEN='cos-token'\n")
    (root / "state").mkdir(exist_ok=True)
    (root / "data" / "cos-turns").mkdir(parents=True, exist_ok=True)
    if turns is not None:
        (root / "state" / "cos.turns").write_text(f"{turns}\n")
    return env, fake, root


@pytest.mark.parametrize("turns", [None, "off"])
def test_the_turn_log_is_off_unless_switched_on(tmp_path, shell, turns):
    env, fake, root = _world(tmp_path, with_overlay=True, turns=turns)
    result = _cos(shell, env, "off")                     # any staging_compose call
    assert result.returncode == 0, result.stderr
    up = _compose_lines(fake)
    assert len(up) == 1 and "docker-compose.staging-cos-turns.yml" not in up[0]


def test_on_includes_the_overlay(tmp_path, shell):
    env, fake, root = _world(tmp_path, with_overlay=True, turns="on")
    assert _cos(shell, env, "off").returncode == 0
    assert "docker-compose.staging-cos-turns.yml" in _compose_lines(fake)[0]


def test_on_without_the_overlay_in_the_release_is_refused(tmp_path, shell):
    env, fake, root = _world(tmp_path, with_overlay=False, turns="on")
    result = _cos(shell, env, "off")
    assert result.returncode != 0 and "state/cos.turns is on but the pinned release has no" in result.stderr


def test_cos_sh_turns_switches_without_a_rebuild(tmp_path, shell):
    env, fake, root = _world(tmp_path, with_overlay=True)
    on = _cos(shell, env, "turns", "on")
    assert on.returncode == 0, on.stderr
    assert (root / "state" / "cos.turns").read_text().strip() == "on"
    up = _compose_lines(fake)
    assert len(up) == 1 and up[0].endswith("up -d --no-build --no-deps cos-scheduler")
    assert "docker-compose.staging-cos-turns.yml" in up[0]
    assert "CoS turn log: on" in _cos(shell, env, "status").stdout
    off = _cos(shell, env, "turns", "off")
    assert off.returncode == 0, off.stderr
    assert (root / "state" / "cos.turns").read_text().strip() == "off"
    last = _compose_lines(fake)[-1]
    assert last.endswith("up -d --no-build --no-deps cos-scheduler") and "staging-cos-turns" not in last
    assert "CoS turn log: off" in _cos(shell, env, "status").stdout
    assert "build" not in " ".join(l for l in (fake / "argv.log").read_text().splitlines() if "compose" in l
                                   and "--no-build" not in l)


def test_cos_sh_turns_on_needs_the_overlay_and_the_folder(tmp_path, shell):
    env, fake, root = _world(tmp_path, with_overlay=False)
    assert "has no docker-compose.staging-cos-turns.yml" in _cos(shell, env, "turns", "on").stderr
    env2, fake2, root2 = _world(tmp_path / "b", with_overlay=True)
    (root2 / "data" / "cos-turns").rmdir()
    result = _cos(shell, env2, "turns", "on")
    assert result.returncode != 0 and "run build.sh" in result.stderr
    assert not (root2 / "state" / "cos.turns").exists()
    assert _cos(shell, env2, "turns", "maybe").returncode != 0


def test_build_makes_the_folder_0700_and_the_release_classifies_it_as_staging_only():
    build = (SCRIPTS / "build.sh").read_text()
    assert 'mkdir -p "$STAGING_ROOT/data/cos-turns"\nchmod 700 "$STAGING_ROOT/data/cos-turns"' in build
    from scripts.ci.classify_release import classify
    assert classify(["docker-compose.staging-cos-turns.yml", "scripts/staging/lib.sh"]) == ("documents", ())
