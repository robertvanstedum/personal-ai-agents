"""MC's governed tools on dev, the AWS staging host (DIAG_MC_TOOLS_RUNTIME_NOT_RECORDED_2026-11-01).

Dev ran with MC chat on and its tools off: production's compose names MC's environments "prod", and dev had no
switch. These tests pin down that the staging overlay names every one of them dev, that production never includes
it, and that scripts/staging/aws_mc_tools.sh refuses to switch on without Robert's policy authority or dev's names,
and never prints the credential.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
PROD = REPO / "docker-compose.prod.yml"
OVERLAY = REPO / "docker-compose.staging-mc-tools.yml"
SCRIPT = REPO / "scripts" / "staging" / "aws_mc_tools.sh"
TOKEN = "t" * 48


def _env(path, service="portal"):
    env = yaml.safe_load(path.read_text())["services"][service]["environment"]
    return dict(item.split("=", 1) for item in env)


def test_overlay_names_every_mc_environment_prod_fixes_as_dev():
    prod = {k for k, v in _env(PROD).items() if k.endswith("_ENV") and v == "prod"}
    assert prod == {"MINIMOI_MC_TOOLS_ENV", "MINIMOI_BOARD_ENV", "MINIMOI_MC_LESSONS_ENV", "MINIMOI_MC_DELEGATION_ENV"}
    overlay = _env(OVERLAY)
    assert overlay == {name: "dev" for name in prod}     # every one, and nothing else (switches stay in env files)


def test_production_never_includes_the_overlay():
    for path in REPO.glob(".github/workflows/*.yml"):
        assert "staging-mc-tools" not in path.read_text(), path
    assert "staging-mc-tools" not in (REPO / "scripts" / "operations" / "deploy_mc_container_ec2.sh").read_text()


def test_overlay_and_script_never_trigger_a_production_deploy():
    from scripts.ci.classify_release import classify
    assert classify(["docker-compose.staging-mc-tools.yml", "scripts/staging/aws_mc_tools.sh"]) == ("documents", ())


def test_script_parses():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


@pytest.fixture
def host(tmp_path):
    """A fake staging root with stub docker and compose that log their calls."""
    if shutil.which("sha256sum") is None:
        pytest.skip("sha256sum not on PATH")
    root = tmp_path / "root"
    (root / "mc" / "portal").mkdir(parents=True)
    (root / "mc" / "mc.env").write_text(f"MC_RELAY_CALLER_TOKEN=x\nMC_TOOLS_TOKEN={TOKEN}\n")
    (root / "mc" / "portal" / "mc-policy.prod.json").write_text('{"revision": "mc-prod-test"}')
    (root / "mc-portal.env").write_text("MC_RELAY_TOKEN=keep\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "calls.log"
    # compose: `config --format json` renders the portal's env from $RENDER_ENV (dev or prod); `up` is logged.
    (bin_ / "compose").write_text(f"""#!/bin/bash
echo "compose $*" >> {log}
if [[ "$1" == config ]]; then
  v=${{RENDER_ENV:-dev}}
  printf '{{"services":{{"portal":{{"environment":{{"MINIMOI_MC_TOOLS_ENV":"%s","MINIMOI_BOARD_ENV":"%s","MINIMOI_MC_LESSONS_ENV":"dev","MINIMOI_MC_DELEGATION_ENV":"dev"}}}}}}}}' "$v" "$v"
fi
""")
    # docker: the listener answers 200; the runtime row is newer than MC's start; policy commands succeed.
    (bin_ / "docker").write_text(f"""#!/bin/bash
echo "docker $*" >> {log}
case "$*" in
  *healthz*) echo 200;;
  *StartedAt*) echo 2026-11-01T01:31:02.6Z;;
  *registered_at*) echo 2026-11-01T17:30:00;;
  *"policy digest"*) printf 'a%.0s' $(seq 1 64); echo;;
  *"policy check"*) exit 0;;
  *printenv*) echo "";;
esac
""")
    # aws: Parameter Store answers with $ISSUES_TOKEN (quoted, as the staging values are), or fails when unset.
    (bin_ / "aws").write_text(f"""#!/bin/bash
echo "aws $*" >> {log}
[[ -n "$ISSUES_TOKEN" ]] || exit 255
printf '"%s"\\n' "$ISSUES_TOKEN"
""")
    for f in ("compose", "docker", "aws"):
        (bin_ / f).chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", MINIMOI_STAGING_ROOT=str(root),
               MINIMOI_STAGING_COMPOSE=str(bin_ / "compose"))
    return root, log, env


def run(env, *args, **extra):
    return subprocess.run(["bash", str(SCRIPT), *args], env=dict(env, **extra), capture_output=True, text=True)


def test_on_refuses_without_robert_policy_authority(host):
    root, log, env = host
    r = run(env, "on")
    assert r.returncode == 2 and "no policy authority" in r.stderr
    assert "MINIMOI_MC_TOOLS" not in (root / "mc-portal.env").read_text()
    assert "compose up" not in log.read_text() if log.exists() else True


def test_approve_policy_needs_words_and_records_them(host):
    root, _log, env = host
    assert run(env, "approve-policy").returncode == 2
    r = run(env, "approve-policy", "Yes, turn MC's tools on in dev")
    assert r.returncode == 0, r.stderr
    authority = (root / "mc" / "portal" / "mc-policy.prod.authority.json").read_text()
    assert '"ref": "Yes, turn MC\'s tools on in dev"' in authority and '"revision": "mc-prod-test"' in authority
    assert oct((root / "mc" / "portal" / "mc-policy.prod.authority.json").stat().st_mode & 0o777) == "0o600"


def test_on_refuses_when_compose_renders_prod_names(host):
    root, log, env = host
    run(env, "approve-policy", "yes")
    r = run(env, "on", RENDER_ENV="prod")
    assert r.returncode == 2 and "MINIMOI_MC_TOOLS_ENV,MINIMOI_BOARD_ENV" in r.stderr
    assert "MINIMOI_MC_TOOLS" not in (root / "mc-portal.env").read_text()
    assert "compose up" not in log.read_text()


def test_on_writes_only_the_hash_recreates_only_the_portal_and_waits_for_the_record(host):
    root, log, env = host
    run(env, "approve-policy", "yes")
    r = run(env, "on")
    assert r.returncode == 0, r.stderr
    assert TOKEN not in r.stdout + r.stderr
    written = (root / "mc-portal.env").read_text()
    assert TOKEN not in written
    assert f"MINIMOI_MC_TOOLS_TOKEN_SHA256={hashlib.sha256(TOKEN.encode()).hexdigest()}" in written
    assert "MINIMOI_MC_TOOLS=1" in written and "MC_RELAY_TOKEN=keep" in written
    assert oct((root / "mc-portal.env").stat().st_mode & 0o777) == "0o600"
    ups = [line for line in log.read_text().splitlines() if line.startswith("compose up")]
    assert ups == ["compose up -d --no-deps portal"]
    assert "runtime is recorded" in r.stdout


def test_off_removes_the_switches_and_keeps_other_keys(host):
    root, _log, env = host
    run(env, "approve-policy", "yes")
    run(env, "on")
    assert run(env, "off").returncode == 0
    written = (root / "mc-portal.env").read_text()
    assert "MINIMOI_MC_TOOLS" not in written and "MC_RELAY_TOKEN=keep" in written


GH = "github_pat_" + "A" * 40


def test_issues_on_refuses_while_the_tools_are_off(host):
    root, _log, env = host
    r = run(env, "issues", "on", ISSUES_TOKEN=GH)
    assert r.returncode == 2 and "tools are off" in r.stderr
    assert not (root / "mc" / "portal" / "github-issues-token").exists()


def test_issues_on_projects_dev_token_unprinted_and_off_removes_it(host):
    root, log, env = host
    run(env, "approve-policy", "yes")
    run(env, "on")
    r = run(env, "issues", "on", ISSUES_TOKEN=GH)
    assert r.returncode == 0, r.stderr
    assert GH not in r.stdout + r.stderr
    token = root / "mc" / "portal" / "github-issues-token"
    assert token.read_text() == GH and oct(token.stat().st_mode & 0o777) == "0o600"
    assert "--name /minimoi/staging/mc_github_issues_token" in log.read_text()   # dev's own, never production's
    written = (root / "mc-portal.env").read_text()
    assert "MINIMOI_MC_TOOLS_TICKETS=1" in written and "MINIMOI_MC_TOOLS_COMMENTS=1" in written
    assert run(env, "issues", "off").returncode == 0
    assert not token.exists() and "TICKETS" not in (root / "mc-portal.env").read_text()


def test_issues_on_refuses_a_classic_or_missing_token(host):
    root, _log, env = host
    run(env, "approve-policy", "yes")
    run(env, "on")
    for value in ("ghp_" + "B" * 36, ""):
        r = run(env, "issues", "on", ISSUES_TOKEN=value)
        assert r.returncode == 2, value
        assert not (root / "mc" / "portal" / "github-issues-token").exists()
        assert "TICKETS" not in (root / "mc-portal.env").read_text()
