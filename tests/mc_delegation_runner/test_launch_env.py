"""The one environment policy for every agent process MiniMoi starts (core/mc_delegation_runner/launch_env.py).
Fake values only; no process is started."""
from __future__ import annotations

import re

import pytest

from core.mc_delegation_runner import launch_env as L

SIX = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "GH_TOKEN", "AWS_SECRET_ACCESS_KEY", "TELEGRAM_BOT_TOKEN")
FAKE = "sk-test-not-real"

# The fallback door's own patterns, copied verbatim from scripts/agents/mm_agent.py at 859d286e (claude/ops-agent-start-commands, 28 Oct, not merged):
# every name the door treats as a credential or a billing switch must be refused here too.
DOOR_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)$|^(AWS_|GH_|GITHUB_)|^DATABASE_URL$")
DOOR_CLAUDE_ENV_REFUSED = re.compile(r"^ANTHROPIC_|^CLAUDE_CODE_USE_|API_KEY|AUTH_TOKEN|OAUTH|FILE_DESCRIPTOR")
# The door's supervised allowlist (Robert present), from the same file. The unattended base list must sit inside it.
DOOR_ENV_ALLOW = ("HOME", "USER", "LOGNAME", "SHELL", "PATH", "PWD", "TMPDIR", "SSH_AUTH_SOCK",
                  "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "TERM_SESSION_ID", "COLORTERM", "TERMINFO",
                  "TERMINFO_DIRS", "CLICOLOR", "NO_COLOR", "LANG", "__CF_USER_TEXT_ENCODING",
                  "LC_ALL", "LC_CTYPE", "LC_COLLATE", "LC_MESSAGES", "LC_MONETARY", "LC_NUMERIC", "LC_TIME",
                  "EDITOR", "VISUAL", "GIT_EDITOR", "DOCKER_HOST", "DOCKER_CONTEXT",
                  "NODE_USE_SYSTEM_CA", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE")
CORPUS = SIX + ("GITHUB_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN", "DATABASE_URL", "SSH_AUTH_SOCK", "MINIMOI_MODEL_GATEWAY_KEY",
                "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR", "OPENAI_BASE_URL", "OPENAI_IDENTITY_TOKEN_FILE",
                "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_CONNECTORS_TOKEN", "AZURE_CLIENT_SECRET", "AZURE_FEDERATED_TOKEN_FILE",
                "BRAVE_API_KEY", "OPENCLAW_GATEWAY_TOKEN", "LITELLM_MASTER_KEY", "POSTGRES_PASSWORD", "GOOGLE_APPLICATION_CREDENTIALS")
# Names the policy itself passes (base list and the extras its callers use): none may look like a credential.
PASSED = L.BASE_ALLOW + ("DISABLE_AUTOUPDATER", "GIT_TERMINAL_PROMPT")


def test_the_base_list_is_the_reviewed_one_and_nothing_on_it_looks_like_a_credential():
    assert L.BASE_ALLOW == ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL")
    assert set(L.BASE_ALLOW) <= set(DOOR_ENV_ALLOW)               # unattended is never wider than the supervised door
    for name in PASSED:
        assert not L.denied(name), name
        assert not DOOR_SECRET_NAME.search(name) and not DOOR_CLAUDE_ENV_REFUSED.search(name), name


def test_the_environment_is_built_from_the_list_never_inherited():
    parent = {**{k: FAKE for k in SIX}, "PATH": "/usr/bin:/bin", "HOME": "/Users/someone", "TMPDIR": "/var/folders/x/T/",
              "NOT_ON_THE_LIST": "harmless", "SSH_AUTH_SOCK": "/tmp/agent.sock", "MINIMOI_AGENT_REQUESTS_DIR": "/the/global/default"}
    env = L.child_env(environ=parent)
    assert env == {"PATH": "/usr/bin:/bin", "HOME": "/Users/someone", "TMPDIR": "/var/folders/x/T/"}


@pytest.mark.parametrize("name", CORPUS)
def test_a_credential_or_billing_switch_passed_as_an_explicit_extra_is_refused_by_name_without_its_value(name):
    with pytest.raises(L.EnvRefused) as caught:
        L.child_env({"DISABLE_AUTOUPDATER": "1", name: FAKE}, environ={})
    assert name in str(caught.value) and FAKE not in str(caught.value)


def test_every_name_the_fallback_door_treats_as_a_credential_is_refused_here_too():
    door = [n for n in CORPUS if DOOR_SECRET_NAME.search(n) or DOOR_CLAUDE_ENV_REFUSED.search(n)]
    assert len(door) >= 25                                       # the comparison is not vacuous
    assert [n for n in door if not L.denied(n)] == []


def test_refusal_ignores_case():
    with pytest.raises(L.EnvRefused):
        L.child_env({"anthropic_api_key": FAKE}, environ={})


def test_the_only_override_of_a_deny_pattern_is_the_reviewed_exact_name():
    assert set(L.REVIEWED_NAMES) == {"MINIMOI_AGENT_REQUESTS_DIR"}
    for name in L.REVIEWED_NAMES:
        assert L.denied(name)                                    # on the list only because a pattern would catch it
    env = L.child_env({"MINIMOI_AGENT_REQUESTS_DIR": "/ledger", "DISABLE_AUTOUPDATER": "1"}, environ={"HOME": "/h"})
    assert env == {"HOME": "/h", "MINIMOI_AGENT_REQUESTS_DIR": "/ledger", "DISABLE_AUTOUPDATER": "1"}
    for near in ("MINIMOI_AGENT_REQUESTS_DIRS", "MINIMOI_AGENT_REQUESTS", "minimoi_agent_requests_dir_x"):
        with pytest.raises(L.EnvRefused):
            L.child_env({near: "/ledger"}, environ={})


@pytest.mark.parametrize("name, value", [("", "x"), ("A=B", "x"), ("1ABC", "x"), ("WITH SPACE", "x"), (None, "x"), ("OK_NAME", 1), ("OK_NAME", "a\0b")])
def test_malformed_names_and_values_are_refused(name, value):
    with pytest.raises(L.EnvRefused):
        L.child_env({name: value}, environ={})


def test_an_extra_may_replace_a_base_value_by_its_exact_name():
    assert L.child_env({"HOME": "/enrolled/home"}, environ={"HOME": "/Users/someone"})["HOME"] == "/enrolled/home"
