#!/usr/bin/python3 -I
"""Start Claude Code, Codex or Grok on the plan sign-in, never on an API key (operations, 28 Oct 2026).

  mm-claude [args...]   mm-codex [args...]   mm-grok [args...]
      start the tool with a clean environment; every argument is passed through unchanged.
  mm-<tool> --mm-check
      run the checks and print the result line; do not start the tool.
  mm-agent <claude|codex|grok> [args...]
      the same, naming the tool as the first argument.

This is the fallback door. Robert's entry point is Master Craftsman and Chief of Staff, and MiniMoi's coordination
layer starts models headless (host runtime spec). When a desktop session is still needed, this door makes sure that:

* the environment is rebuilt from an allowlist, so no provider key or token reaches the tool. An API key in the
  environment makes Grok ("The API key takes precedence over browser credentials") and Claude Code bill the API
  instead of the plan;
* the plan sign-in is present and no config file routes the tool to a key; otherwise it stops and names the login
  command (it never falls back to a key);
* it prints names only, never values;
* the session start is recorded for MiniMoi as one JSON line (the census format comes later, spec R6.6).

Supervised profile: Robert is present, so SSH_AUTH_SOCK passes for his normal git use. Unattended work items use the
stricter delegate policy in core/mc_delegation_runner/workspace.py.

Stdlib only, no repo imports, Python 3.9: installed by copy to ~/.local/bin (scripts/agents/install_mm_agent.sh).
Exit codes: 0 checked, 64 usage, 69 tool binary missing, 78 refused by a check.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TOOLS = ("claude", "codex", "grok")
ENV_ALLOW = ("HOME", "USER", "LOGNAME", "SHELL", "PATH", "PWD", "TMPDIR", "SSH_AUTH_SOCK",
             "TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "TERM_SESSION_ID", "COLORTERM", "TERMINFO",
             "TERMINFO_DIRS", "CLICOLOR", "NO_COLOR", "LANG", "__CF_USER_TEXT_ENCODING",
             "EDITOR", "VISUAL", "GIT_EDITOR", "DOCKER_HOST", "DOCKER_CONTEXT",
             # TLS trust only (paths and switches), so tools behind a local CA keep working.
             "NODE_USE_SYSTEM_CA", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE")
ENV_ALLOW_PREFIXES = ("LC_",)
TOOL_HOME_VAR = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "grok": "GROK_HOME"}
# Removed names that look like credentials are listed by name in the result line; the rest are only counted.
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)$|^(AWS_|GH_|GITHUB_)|^DATABASE_URL$")
# Claude Code applies a settings file's "env" block to itself, so a key there would undo the clean environment.
CLAUDE_KEY_SETTINGS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                       "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
CLAUDE_MANAGED_SETTINGS = Path("/Library/Application Support/ClaudeCode/managed-settings.json")
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"
GROK_KEY_CONFIG = re.compile(r"^\s*(api_key|env_key)\s*=", re.M)
CODEX_KEY_CONFIG = re.compile(r"^\s*(preferred_auth_method|forced_login_method)\s*=\s*[\"'](apikey|api)[\"']", re.M)
BINARIES = {
    "claude": ("/opt/homebrew/bin/claude", "~/.local/bin/claude", "~/.claude/local/claude"),
    "codex": ("~/.local/bin/codex", "/opt/homebrew/bin/codex"),
    "grok": ("~/.grok/bin/grok",),
}
LOGIN_HINT = {"claude": "start `claude` once and run /login with the plan account",
              "codex": "run `codex login` and choose Sign in with ChatGPT",
              "grok": "run `grok login` and sign in with the plan account"}
EX_USAGE, EX_UNAVAILABLE, EX_CONFIG = 64, 69, 78


class Refused(Exception):
    """A check failed; the message names the file or setting, never a value."""


def clean_env(environ, tool):
    """Return (env, removed): env keeps only allowlisted names; removed lists every name dropped, sorted."""
    keep = set(ENV_ALLOW) | {TOOL_HOME_VAR[tool]}
    env = {k: v for k, v in environ.items() if k in keep or k.startswith(ENV_ALLOW_PREFIXES)}
    return env, sorted(k for k in environ if k not in env)


def _tool_home(tool, home, environ):
    override = environ.get(TOOL_HOME_VAR[tool], "")
    return Path(override) if override else home / {"claude": ".claude", "codex": ".codex", "grok": ".grok"}[tool]


def _project_dirs(cwd, home):
    """cwd and its parents, stopping before home (home holds the user-level config, checked separately)."""
    out, here = [], cwd.resolve()
    home = home.resolve()
    while here != home and here != here.parent:
        out.append(here)
        here = here.parent
    return out


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise Refused(f"{path} could not be read as JSON")


def check_grok(home, cwd, environ, probe):
    grok_home = _tool_home("grok", home, environ)
    auth = _read_json(grok_home / "auth.json")
    modes = sorted({v.get("auth_mode") for v in (auth or {}).values() if isinstance(v, dict) and v.get("auth_mode")})
    if not modes:
        raise Refused(f"no plan sign-in in {grok_home / 'auth.json'}")
    for path in [grok_home / "config.toml"] + [d / ".grok" / "config.toml" for d in _project_dirs(cwd, home)]:
        if path.is_file():
            found = GROK_KEY_CONFIG.search(path.read_text(errors="replace"))
            if found:
                raise Refused(f"{path} sets `{found.group(1)}`, which bills a key instead of the plan")
    return "plan sign-in (" + ", ".join(modes) + ")"


def check_claude(home, cwd, environ, probe):
    claude_home = _tool_home("claude", home, environ)
    if not ((claude_home / ".credentials.json").is_file() or probe(CLAUDE_KEYCHAIN_SERVICE)):
        raise Refused("no Claude Code sign-in found (Keychain or .credentials.json)")
    files = [CLAUDE_MANAGED_SETTINGS, claude_home / "settings.json", claude_home / "settings.local.json"]
    for d in _project_dirs(cwd, home):
        files += [d / ".claude" / "settings.json", d / ".claude" / "settings.local.json"]
    for path in files:
        settings = _read_json(path) if path.is_file() else None
        if not isinstance(settings, dict):
            continue
        if "apiKeyHelper" in settings:
            raise Refused(f"{path} sets apiKeyHelper, which bills a key instead of the plan")
        block = settings.get("env") if isinstance(settings.get("env"), dict) else {}
        names = sorted(n for n in block if n in CLAUDE_KEY_SETTINGS or SECRET_NAME.search(n))
        if names:
            raise Refused(f"{path} puts {', '.join(names)} in Claude Code's environment")
    return "plan sign-in"


def check_codex(home, cwd, environ, probe):
    codex_home = _tool_home("codex", home, environ)
    auth = _read_json(codex_home / "auth.json")
    if not isinstance(auth, dict) or auth.get("auth_mode") != "chatgpt":
        raise Refused(f"{codex_home / 'auth.json'} is not a ChatGPT sign-in")
    if auth.get("OPENAI_API_KEY"):
        raise Refused(f"{codex_home / 'auth.json'} also stores an API key")
    for path in [codex_home / "config.toml"] + [d / ".codex" / "config.toml" for d in _project_dirs(cwd, home)]:
        if path.is_file():
            found = CODEX_KEY_CONFIG.search(path.read_text(errors="replace"))
            if found:
                raise Refused(f"{path} sets `{found.group(1)}` to an API key")
    return "plan sign-in (chatgpt)"


CHECKS = {"claude": check_claude, "codex": check_codex, "grok": check_grok}


def resolve_binary(tool, home, self_path):
    """The first installed candidate that is not this launcher. None when the tool is not installed."""
    me = os.path.realpath(self_path)
    for candidate in BINARIES[tool]:
        path = candidate.replace("~", str(home), 1) if candidate.startswith("~") else candidate
        if os.path.isfile(path) and os.access(path, os.X_OK) and os.path.realpath(path) != me:
            return path
    return None


def keychain_has(service):
    """True when a Keychain item exists for service. Reads attributes only; the secret itself is never requested."""
    try:
        return subprocess.run(["/usr/bin/security", "find-generic-password", "-s", service],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=10, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def record_session(home, tool, binary, cwd, removed):
    """Append one JSON line for MiniMoi. Best effort: a failed record warns and does not stop the session."""
    folder = home / ".minimoi" / "agents"
    row = {"schema": "minimoi.fallback_session.v0", "tool": tool, "binary": binary, "cwd": str(cwd),
           "host": socket.gethostname(), "pid": os.getpid(),
           "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "removed_credential_names": [n for n in removed if SECRET_NAME.search(n)]}
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(folder / "fallback_sessions.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"mm-{tool}: warning: session not recorded ({exc.__class__.__name__})", file=sys.stderr)


def summary(tool, label, removed):
    secret = [n for n in removed if SECRET_NAME.search(n)]
    others = len(removed) - len(secret)
    parts = [f"mm-{tool}: {label}", "removed: " + (", ".join(secret) if secret else "no credential names")]
    if others:
        parts.append(f"+{others} other names")
    return " · ".join(parts)


def main(argv, environ=None, home=None, cwd=None, execve=os.execve, probe=keychain_has, self_path=None):
    environ = dict(os.environ if environ is None else environ)
    home = Path(home or environ.get("HOME") or Path.home())
    cwd = Path(cwd or os.getcwd())
    name = os.path.basename(argv[0])
    args = list(argv[1:])
    tool = name[3:] if name.startswith("mm-") and name[3:] in TOOLS else None
    if tool is None and args and args[0] in TOOLS:
        tool = args.pop(0)
    if tool is None or "--mm-help" in args:
        print(__doc__.strip(), file=sys.stderr if tool is None else sys.stdout)
        return EX_USAGE if tool is None else 0
    check_only = "--mm-check" in args
    args = [a for a in args if a != "--mm-check"]
    env, removed = clean_env(environ, tool)
    try:
        label = CHECKS[tool](home, cwd, environ, probe)
    except Refused as exc:
        print(f"mm-{tool}: refused: {exc}. To sign in on the plan: {LOGIN_HINT[tool]}. No key is used.", file=sys.stderr)
        return EX_CONFIG
    binary = resolve_binary(tool, home, self_path or argv[0])
    if binary is None:
        print(f"mm-{tool}: {tool} is not installed in any of: {', '.join(BINARIES[tool])}", file=sys.stderr)
        return EX_UNAVAILABLE
    print(summary(tool, label, removed), file=sys.stderr)
    if check_only:
        return 0
    record_session(home, tool, binary, cwd, removed)
    execve(binary, [binary, *args], env)
    return 0                                                     # reached only when execve is replaced in tests


if __name__ == "__main__":
    sys.exit(main(sys.argv))
