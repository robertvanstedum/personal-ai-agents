"""C7, release plumbing (spec v0.2 §3.10): T19. The branch is a portal-only release; the CLI is release-only; the
compose binds can never become empty directories; the ops command never writes the bot token into .env."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.ci.classify_release import classify, compose_changed_services

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.prod.yml"
OPS = ROOT / "scripts" / "operations" / "deploy_mc_container_ec2.sh"

FIXTURE = json.loads((Path(__file__).resolve().parent / "release_fixture.json").read_text())


def test_the_notices_change_is_a_portal_only_release():
    """Deterministic (no git): the committed fixture's paths, with the compose change owned by the portal block."""
    paths = FIXTURE["notices_paths"]
    assert "docker-compose.prod.yml" in paths and "scripts/agents/waiting.py" in paths
    expected = (FIXTURE["expected"]["release_class"], tuple(FIXTURE["expected"]["services"]))
    assert classify(paths, lambda: tuple(FIXTURE["compose_services_changed"])) == expected == ("domain", ("portal",))


def test_the_release_pipeline_preflight_makes_its_own_release_full():
    """The auth-file preflight lives in deploy_scoped_release.sh, which bootstraps with a full release by design
    (tests/test_release_classifier.py). Shipped as its own PR first, it leaves the notices release portal-only."""
    assert FIXTURE["pipeline_paths"] == ["scripts/operations/deploy_scoped_release.sh"]
    assert classify(FIXTURE["pipeline_paths"])[0] == FIXTURE["expected_with_pipeline"] == "full"


def _git(*args):
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def test_the_fixture_still_covers_the_branch_and_its_compose_change():
    """Where git has a real base (a branch checkout with origin/main), the fixture must cover the diff and the compose
    change must be the portal's alone. On CI's pull-request checkout there is no origin/main, and on the push to main
    the base is HEAD itself; both have nothing to compare, and the deterministic tests above still run."""
    base = _git("merge-base", "origin/main", "HEAD")
    if not base or base == _git("rev-parse", "HEAD"):
        pytest.skip("no separate base to diff against in this checkout (the fixture tests above cover the class)")
    changed = set((_git("diff", "--name-only", f"{base}...HEAD") or "").split())
    assert changed, "git diff gave nothing"
    uncovered = changed - set(FIXTURE["notices_paths"]) - set(FIXTURE["pipeline_paths"])
    assert not uncovered, f"update release_fixture.json for: {sorted(uncovered)}"
    before = _git("show", f"{base}:docker-compose.prod.yml")
    if "docker-compose.prod.yml" in changed:
        assert compose_changed_services(before, COMPOSE.read_text()) == tuple(FIXTURE["compose_services_changed"])


def test_the_cli_and_the_ops_command_are_release_only():
    assert classify(["scripts/agents/waiting.py"]) == ("documents", ())
    assert classify(["scripts/operations/deploy_mc_container_ec2.sh"]) == ("documents", ())
    assert classify(["scripts/agents/waiting_extra.py"])[0] == "full"          # only the reviewed file


def _portal():
    return yaml.safe_load(COMPOSE.read_text())["services"]["portal"]


def test_compose_portal_block_defaults_off_and_holds_no_secret():
    env = dict(e.split("=", 1) for e in _portal()["environment"])
    assert env["MINIMOI_ACTION_NOTICES"] == "${MINIMOI_ACTION_NOTICES:-off}"
    assert env["MINIMOI_ACTION_NOTICES_ENV"] == "prod"
    assert env["MINIMOI_ACTION_NOTICES_DIR"] == "/app/runtime/guild/notices"
    assert env["MINIMOI_ACTION_NOTICES_BOT_TOKEN_FILE"] == "/run/secrets/notices/bot-token"
    assert env["MINIMOI_ACTION_NOTICES_CHAT_ID"] == "8379221702"                # review N4: not .env.example's value
    assert env["MINIMOI_ACTION_NOTICES_OWNER"] == "robert"
    assert env["MINIMOI_ACTION_NOTICES_AGENT_TOKENS"] == "${MINIMOI_ACTION_NOTICES_AGENT_TOKENS:-}"
    assert not any(re.search(r"\d{5,15}:[A-Za-z0-9_-]{30,}", v) for v in env.values())     # no bot token anywhere


def test_auth_files_are_bound_as_files_that_can_never_become_directories():
    volumes = _portal()["volumes"]
    for name in ("pending.json", "reset_requests.json"):
        [bind] = [v for v in volumes if isinstance(v, dict) and v.get("target") == f"/app/minimoi_portal/auth/{name}"]
        assert bind == {"type": "bind", "source": f"${{MINIMOI_ROOT:-/opt/minimoi}}/auth/{name}",
                        "target": f"/app/minimoi_portal/auth/{name}", "bind": {"create_host_path": False}}
    short = [v for v in volumes if isinstance(v, str)]
    assert "${MINIMOI_ROOT:-/opt/minimoi}/notices:/run/secrets/notices:ro" in short
    # The store lives in the guild folder's existing bind (and so in its daily backup), never a bind of its own.
    assert "${MINIMOI_ROOT:-/opt/minimoi}/data/guild:/app/runtime/guild" in short
    assert not any("data/notices" in v for v in short)


def test_ops_command_shape():
    text = OPS.read_text()
    assert subprocess.run(["bash", "-n", str(OPS)]).returncode == 0
    assert "notices) notices" in text and "notices prepare|on [<hashes>]|off|status" in text
    body = text[text.index("notices() {"):text.index("\ncollect() {")]
    # Only the mode and the agents' token hashes go into the shared .env (review N5); the token stays a 0600 file.
    for line in re.findall(r"set_env_keys [^\n]*", body):
        assert "bot-token" not in line and "$tmp" not in line
        assert set(re.findall(r"MINIMOI_[A-Z_]+", line)) <= {"MINIMOI_ACTION_NOTICES", "MINIMOI_ACTION_NOTICES_AGENT_TOKENS"}
    assert 'chmod 0600 "$tmp"' in body and "--with-decryption" in body
    prepare = text[text.index("notices_prepare() {"):text.index("\nnotices() {")]
    assert "create_host_path" in prepare and "docker cp" in prepare and '{"pending": []}' in prepare
