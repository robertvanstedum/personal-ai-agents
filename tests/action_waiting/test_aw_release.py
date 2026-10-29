"""C7, release plumbing (spec v0.2 §3.10): T19. The branch is a portal-only release; the CLI is release-only; the
compose binds can never become empty directories; the ops command never writes the bot token into .env."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

from scripts.ci.classify_release import classify, compose_changed_services

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.prod.yml"
OPS = ROOT / "scripts" / "operations" / "deploy_mc_container_ec2.sh"

BRANCH_PATHS = [
    "minimoi_portal/action_waiting/__init__.py", "minimoi_portal/action_waiting/__main__.py",
    "minimoi_portal/action_waiting/door.py", "minimoi_portal/action_waiting/sender.py",
    "minimoi_portal/action_waiting/service.py", "minimoi_portal/action_waiting/settings.py",
    "minimoi_portal/action_waiting/sources.py", "minimoi_portal/action_waiting/store.py",
    "minimoi_portal/action_waiting/words.py", "minimoi_portal/app.py", "minimoi_portal/guest_requests.py",
    "minimoi_portal/guild_ui/action_waiting.py", "minimoi_portal/guild_ui/api.py", "minimoi_portal/guild_ui/pages.py",
    "scripts/agents/waiting.py", "scripts/ci/classify_release.py", "scripts/operations/deploy_mc_container_ec2.sh",
    "tests/action_waiting/test_aw_release.py", "tests/test_release_classifier.py"]


def _main_compose():
    done = subprocess.run(["git", "show", "origin/main:docker-compose.prod.yml"], cwd=ROOT, capture_output=True,
                          text=True)
    return done.stdout if done.returncode == 0 else None


def test_the_branch_is_a_portal_only_release():
    before, after = _main_compose(), COMPOSE.read_text()
    assert before is not None
    assert compose_changed_services(before, after) == ("portal",)
    assert classify(BRANCH_PATHS + ["docker-compose.prod.yml"],
                    lambda: compose_changed_services(before, after)) == ("domain", ("portal",))


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
