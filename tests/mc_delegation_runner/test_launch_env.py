"""The environment policy the delegation runner and the front-door connections give the agent processes they start
(core/mc_delegation_runner/launch_env.py). Fake values only; no process is started."""
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
# The door also passes each tool's home variable (TOOL_HOME_VAR in the same file).
DOOR_TOOL_HOME_VARS = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME")

CORPUS = SIX + ("GITHUB_TOKEN", "AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN", "DATABASE_URL", "SSH_AUTH_SOCK", "MINIMOI_MODEL_GATEWAY_KEY",
                "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR", "OPENAI_BASE_URL", "OPENAI_IDENTITY_TOKEN_FILE",
                "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "CODEX_CONNECTORS_TOKEN", "AZURE_CLIENT_SECRET", "AZURE_FEDERATED_TOKEN_FILE",
                "BRAVE_API_KEY", "OPENCLAW_GATEWAY_TOKEN", "LITELLM_MASTER_KEY", "POSTGRES_PASSWORD", "GOOGLE_APPLICATION_CREDENTIALS")

# What the deny patterns must catch, by class (the independent review's probe, 28 Oct, plus the original credentials).
DENY_CLASSES = {
    "credentials": SIX + ("GITHUB_TOKEN", "AWS_ACCESS_KEY_ID", "CLAUDE_CODE_OAUTH_TOKEN", "CODEX_ACCESS_TOKEN", "AZURE_CLIENT_SECRET",
                          "OPENAI_IDENTITY_TOKEN_FILE", "POSTGRES_PASSWORD", "GOOGLE_APPLICATION_CREDENTIALS", "SSH_AUTH_SOCK"),
    "provider_switches": ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "OPENAI_BASE_URL",
                          "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR"),
    "endpoints": ("CODEX_BASE_URL", "LITELLM_BASE_URL", "OPENROUTER_BASE_URL", "AZURE_OPENAI_ENDPOINT", "CLOUD_ML_REGION"),
    "credential_urls": ("DATABASE_URL", "DATABASE_URL_RO", "REDIS_URL", "SENTRY_DSN"),
    "proxies_and_trust": ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "ALL_PROXY", "NODE_EXTRA_CA_CERTS", "NODE_TLS_REJECT_UNAUTHORIZED",
                          "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"),
    "config_redirects": ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME", "HOME"),
    "code_injection": ("NODE_OPTIONS", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
                       "LD_PRELOAD", "BASH_ENV", "ENV", "ZDOTDIR"),
    "git": ("GIT_SSH", "GIT_SSH_COMMAND", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_PARAMETERS"),
}


def test_the_base_list_is_the_reviewed_one_and_sits_inside_the_door():
    assert L.BASE_ALLOW == ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL")
    assert set(L.BASE_ALLOW) <= set(DOOR_ENV_ALLOW)               # unattended is never wider than the supervised door
    for name in L.BASE_ALLOW:
        assert not DOOR_SECRET_NAME.search(name) and not DOOR_CLAUDE_ENV_REFUSED.search(name), name
    # HOME comes from the parent only: as an extra it would move the CLI's sign-in and settings, so it is denied there.
    assert [n for n in L.BASE_ALLOW if L.denied(n)] == ["HOME"]


def test_the_environment_is_built_from_the_list_never_inherited():
    parent = {**{k: FAKE for k in SIX}, "PATH": "/usr/bin:/bin", "HOME": "/Users/someone", "TMPDIR": "/var/folders/x/T/",
              "NOT_ON_THE_LIST": "harmless", "SSH_AUTH_SOCK": "/tmp/agent.sock", "MINIMOI_AGENT_REQUESTS_DIR": "/the/global/default",
              "LC_CTYPE": "C.UTF-8", "CLAUDE_CONFIG_DIR": "/elsewhere", "NODE_OPTIONS": "--require /tmp/x.js"}
    env = L.child_env(environ=parent)
    assert env == {"PATH": "/usr/bin:/bin", "HOME": "/Users/someone", "TMPDIR": "/var/folders/x/T/"}


def test_the_extras_are_a_reviewed_list_each_with_its_reason():
    assert set(L.EXTRA_ALLOW) == {"MINIMOI_AGENT_REQUESTS_DIR", "DISABLE_AUTOUPDATER", "LANG", "GIT_TERMINAL_PROMPT"}
    for name, reason in L.EXTRA_ALLOW.items():
        assert isinstance(reason, str) and len(reason) > 20, name
        L.check_extra(name)                                       # every reviewed extra passes the policy's own checks
    assert L.DENY_OVERRIDES <= set(L.EXTRA_ALLOW)
    for name in L.DENY_OVERRIDES:
        assert L.denied(name)                                     # an override exists only because a pattern would catch it


@pytest.mark.parametrize("name", CORPUS + ("NOT_ON_THE_LIST", "FOO", "MINIMOI_AGENT_REQUESTS_DIRS", "MINIMOI_AGENT_REQUESTS",
                                           "minimoi_agent_requests_dir", "lang", "DISABLE_UPDATES"))
def test_an_extra_off_the_reviewed_list_is_refused_by_name_without_its_value(name):
    with pytest.raises(L.EnvRefused) as caught:
        L.child_env({"DISABLE_AUTOUPDATER": "1", name: FAKE}, environ={})
    assert name in str(caught.value) and FAKE not in str(caught.value)


@pytest.mark.parametrize("cls", sorted(DENY_CLASSES))
def test_the_deny_patterns_are_a_second_check_for_each_class(cls, monkeypatch):
    for name in DENY_CLASSES[cls]:
        assert L.denied(name), name
    # Even a name wrongly added to the reviewed list is refused, unless it is a reviewed override.
    monkeypatch.setattr(L, "EXTRA_ALLOW", {**L.EXTRA_ALLOW, **{n: "added by mistake" for n in DENY_CLASSES[cls]}})
    for name in DENY_CLASSES[cls]:
        with pytest.raises(L.EnvRefused, match="credential_variable_refused"):
            L.child_env({name: FAKE}, environ={})


def test_every_name_the_fallback_door_treats_as_a_credential_is_refused_here_too():
    door = [n for n in CORPUS if DOOR_SECRET_NAME.search(n) or DOOR_CLAUDE_ENV_REFUSED.search(n)]
    assert len(door) >= 25                                        # the comparison is not vacuous
    assert [n for n in door if not L.denied(n)] == []


def test_where_the_unattended_policy_and_the_supervised_door_differ():
    """The door (Robert present) passes these from his own environment; the unattended policy never accepts them as
    extras: his git agent, a tool's alternate home folder, and TLS trust paths."""
    passed_by_door_denied_here = {n for n in DOOR_ENV_ALLOW + DOOR_TOOL_HOME_VARS if L.denied(n)}
    assert passed_by_door_denied_here == {"HOME", "SSH_AUTH_SOCK", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR",
                                          "REQUESTS_CA_BUNDLE", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME"}


def test_refusal_ignores_case():
    with pytest.raises(L.EnvRefused):
        L.child_env({"anthropic_api_key": FAKE}, environ={})
    assert L.denied("https_proxy") and L.denied("node_options")


def test_the_only_override_of_a_deny_pattern_is_the_ledger_folder():
    assert L.DENY_OVERRIDES == {"MINIMOI_AGENT_REQUESTS_DIR"}
    env = L.child_env({"MINIMOI_AGENT_REQUESTS_DIR": "/ledger", "DISABLE_AUTOUPDATER": "1"}, environ={"HOME": "/h"})
    assert env == {"HOME": "/h", "MINIMOI_AGENT_REQUESTS_DIR": "/ledger", "DISABLE_AUTOUPDATER": "1"}


@pytest.mark.parametrize("name, value", [("", "x"), ("A=B", "x"), ("1ABC", "x"), ("WITH SPACE", "x"), (None, "x"), ("LANG", 1), ("LANG", "a\0b")])
def test_malformed_names_and_values_are_refused(name, value):
    with pytest.raises(L.EnvRefused):
        L.child_env({name: value}, environ={})


def test_lang_may_replace_the_parents_locale_but_home_never_moves():
    assert L.child_env({"LANG": "en_US.UTF-8"}, environ={"LANG": "C"})["LANG"] == "en_US.UTF-8"
    with pytest.raises(L.EnvRefused, match="HOME"):
        L.child_env({"HOME": "/enrolled/home"}, environ={"HOME": "/Users/someone"})
