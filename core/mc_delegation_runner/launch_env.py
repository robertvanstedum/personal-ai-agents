"""The environment an agent process gets from the delegation runner and the front-door connections: one policy,
defined once.

This is the environment step of the launch contract (host runtime spec, R6.7-d). It is used by the delegation
runner's workspace (``workspace.child_env``) and by the two front-door connections (``core/agent_requests/
claude_stream.py`` and ``codex_app_server.py``). Other launchers in the repo keep their own fixed, minimal lists
(for example ``claude_fixed_send.py`` and ``codex_read_observer.py``); none of them copies the whole environment.

* The child's environment is built from ``BASE_ALLOW``, never inherited: a name in the parent that is not on the
  list does not reach the child. A provider key or token in the parent therefore cannot make Claude Code or Codex
  bill the API instead of the plan sign-in.
* Anything else a child needs is passed by its exact name, as an ``extra``, and only if the name is on
  ``EXTRA_ALLOW``: a reviewed list, each name with its reason. Any other extra is refused.
* As a second check, an extra whose name matches ``DENY_PATTERNS`` (credentials, provider and endpoint switches,
  configuration redirects, code injection) is refused even when it is on ``EXTRA_ALLOW``, unless it is also on
  ``DENY_OVERRIDES``. Nothing else overrides a deny pattern.
* Refusals name the variable, never its value.

It lives in this package because the runner's installed bundle copies only ``core/mc_delegation_runner`` and
``core/mc_delegation_dispatcher`` (``scripts/mc_host/install_delegation_runner.sh``). Stdlib only.
"""
from __future__ import annotations

import os
import re
from typing import Mapping

# Copied from the parent when present. Why each is here (read in the pinned CLIs' own code, 28 Oct 2026:
# Claude Code 2.1.308 and the Codex CLI in ChatGPT.app):
#   PATH          Claude Code reads its plan sign-in by running `security find-generic-password` by name; both CLIs'
#                 shell tools find programs with it.
#   HOME          Claude Code's ~/.claude and the login Keychain; Codex's CODEX_HOME defaults to ~/.codex (auth.json).
#   USER          the Keychain account Claude Code reads the plan sign-in under (USER, else the OS user name).
#   TMPDIR        the runtime's temporary folder (Claude Code's runtime: TMPDIR, else /tmp); Codex adds it to its
#                 sandbox's writable roots.
#   SHELL         the shell Claude Code's Bash tool and Codex's command runner start.
#   LANG, LC_ALL  the locale of the commands they run. The connections also pass LANG as an extra (EXTRA_ALLOW).
#   TERM          terminal handling only, harmless without a terminal; kept so the runner's list is unchanged.
BASE_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL")

# The only names a caller may add, each with its reason. Spec R6.7-d: "A profile may add names, each listed in the
# policy with its reason."
EXTRA_ALLOW = {
    "MINIMOI_AGENT_REQUESTS_DIR": "a connection's own ledger folder, a path and not a credential: the ledger helper "
                                  "(scripts/agent_requests.py) falls back to it when a command has no --root, so the "
                                  "session never reaches the global default ledger",
    "DISABLE_AUTOUPDATER": "Claude Code: the pinned CLI never replaces itself (the CLI reads it; the installed "
                           "connection plist set it to 1)",
    "LANG": "a UTF-8 locale (en_US.UTF-8) for the agents and the commands they run. Under launchd the manager has no "
            "LANG; Python's locale coercion (PEP 538) gave it LC_CTYPE=C.UTF-8, which the whole-environment copy used "
            "to pass on. The fixed sender and the Codex read observer set the same value.",
    "GIT_TERMINAL_PROMPT": "the delegation runner's git never waits on a credential prompt (value 0)",
}

# Names that must never reach a child as an extra, even when listed above. Matched without regard to case.
DENY_PATTERNS = (
    # the runner's original list (workspace.py before 28 Oct), DATABASE_URL widened to its variants
    r"^GH_", r"^GITHUB_", r"^AWS_", r"^MINIMOI_", r"_API_KEY$", r"_TOKEN$", r"^DATABASE_URL", r"^SSH_AUTH_SOCK$",
    # secret-shaped names, as the fallback door reports them (SECRET_NAME in scripts/agents/mm_agent.py, operations branch)
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)$",
    # what routes Claude Code to a key or another provider (CLAUDE_ENV_REFUSED in the same file)
    r"API_KEY", r"AUTH_TOKEN", r"OAUTH", r"FILE_DESCRIPTOR", r"^ANTHROPIC_", r"^CLAUDE_CODE_USE_",
    # Codex's and Grok's provider names, and token files (the Codex CLI reads OPENAI_* credential names)
    r"^OPENAI_", r"^XAI_", r"TOKEN_FILE$",
    # endpoints and URLs that may carry a credential: another provider, gateway or region
    r"_BASE_URL$", r"_ENDPOINT$", r"^CLOUD_ML_", r"_DSN$", r"^REDIS_URL",
    # proxies and TLS trust: traffic rerouted or inspected
    r"PROXY$", r"^NODE_EXTRA_CA_CERTS$", r"^NODE_TLS_", r"^SSL_CERT_", r"^REQUESTS_CA_BUNDLE$", r"^CURL_CA_BUNDLE$",
    # configuration redirects: a different folder can hold an apiKeyHelper, a settings env key or an API-key auth.json
    r"^CLAUDE_CONFIG_DIR$", r"^CODEX_HOME$", r"^GROK_HOME$", r"^HOME$",
    # code injection into the CLI's runtime or its shells
    r"^NODE_OPTIONS$", r"^PYTHON", r"^DYLD_", r"^LD_", r"^BASH_ENV$", r"^ENV$", r"^ZDOTDIR$",
    # git transport and configuration
    r"^GIT_SSH", r"^GIT_CONFIG",
)
_DENY = tuple(re.compile(p, re.IGNORECASE) for p in DENY_PATTERNS)

# Names on EXTRA_ALLOW that a deny pattern catches but are not secrets, each reviewed. Nothing else overrides one.
DENY_OVERRIDES = frozenset({"MINIMOI_AGENT_REQUESTS_DIR"})

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


class EnvRefused(ValueError):
    """An extra the policy will not pass. The message names the variable, never its value."""


def denied(name: str) -> bool:
    """True when ``name`` matches a deny pattern (before ``DENY_OVERRIDES`` is considered)."""
    return any(p.search(name) for p in _DENY)


def check_extra(name) -> None:
    """Raise ``EnvRefused`` unless ``name`` may be passed to a child by explicit name."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise EnvRefused("environment_name_invalid")
    if name not in EXTRA_ALLOW:
        raise EnvRefused(f"extra_not_reviewed: {name}")
    if denied(name) and name not in DENY_OVERRIDES:
        raise EnvRefused(f"credential_variable_refused: {name}")


def child_env(extra: Mapping[str, str] | None = None, *, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for one child: ``BASE_ALLOW`` names copied from ``environ`` (default: this process's
    environment) when present, then each ``extra`` by its exact name. Every extra is checked before anything is
    returned; a refused one raises ``EnvRefused`` and no environment is built."""
    source = os.environ if environ is None else environ
    env = {k: source[k] for k in BASE_ALLOW if k in source}
    for name, value in (extra or {}).items():
        check_extra(name)
        if not isinstance(value, str) or "\0" in value:
            raise EnvRefused(f"environment_value_invalid: {name}")
        env[name] = value
    return env
