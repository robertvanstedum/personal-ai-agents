#!/usr/bin/python3 -I
"""Start Claude Code, Codex or Grok on the plan sign-in, never on an API key (operations, 28 Oct 2026).

  mm-claude [args...]   mm-codex [args...]   mm-grok [args...]
      start the tool with a clean environment; arguments are passed through unchanged (after the checks below).
  mm-<tool> --mm-check
      run the checks and print the result line; do not start the tool.
  mm-agent <claude|codex|grok> [args...]
      the same, naming the tool as the first argument.

This is the fallback door. Robert's entry point is Master Craftsman and Chief of Staff, and MiniMoi's coordination
layer starts models headless (host runtime spec). When a desktop session is still needed, this door makes sure that:

* the environment is rebuilt from an allowlist, so no provider key or token reaches the tool. An API key in the
  environment makes Grok and Claude Code bill the API instead of the plan;
* the plan sign-in is present, and no config layer, env file or argument routes the tool to a key. Otherwise it
  stops and names the login command; it never falls back to a key;
* it prints names only, never values;
* the session start is recorded for MiniMoi as one JSON line (the census format comes later, spec R6.6).

Config layers checked:
* Grok: the user, managed and requirements files, both under GROK_HOME and under /etc/grok, plus the project file
  and the MDM domain ai.x.grok.
* Codex: CODEX_HOME's config, managed_config and requirements files, the same under /etc/codex, the project file,
  CODEX_HOME/.env (Codex loads it into its own environment) and the MDM domain com.openai.codex.
* Claude Code: the managed settings file, the MDM domain com.anthropic.claudecode, the user, local and remote
  settings, the project settings, and any --settings argument.

Not checkable here: Grok's remote campaign patches.

Which copy starts: for Claude Code, MiniMoi's pinned copy under ~/minimoi-runtime/tools (the one the agent connection
runs), newest version first, before Homebrew's.

Supervised profile: Robert is present, so SSH_AUTH_SOCK passes for his normal git use. Unattended work items use the
stricter delegate policy in core/mc_delegation_runner/workspace.py.

Stdlib only, no repo imports, Python 3.9: installed by copy to ~/.local/bin (scripts/agents/install_mm_agent.sh).
Exit codes: 0 checked, 64 usage, 69 tool missing or failed to start, 78 refused by a check.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import plistlib
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
             "LC_ALL", "LC_CTYPE", "LC_COLLATE", "LC_MESSAGES", "LC_MONETARY", "LC_NUMERIC", "LC_TIME",
             "EDITOR", "VISUAL", "GIT_EDITOR", "DOCKER_HOST", "DOCKER_CONTEXT",
             # TLS trust only (paths and switches), so tools behind a local CA keep working.
             "NODE_USE_SYSTEM_CA", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE")
TOOL_HOME_VAR = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME", "grok": "GROK_HOME"}
TOOL_HOME_DIR = {"claude": ".claude", "codex": ".codex", "grok": ".grok"}
# Removed names that look like credentials are listed by name in the result line; the rest are only counted.
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?)$|^(AWS_|GH_|GITHUB_)|^DATABASE_URL$")
# Claude Code applies a settings "env" block to itself, so these names there would undo the clean environment
# (an API key, an auth token or file descriptor, a provider switch, or a base URL or socket that reroutes traffic).
CLAUDE_ENV_REFUSED = re.compile(r"^ANTHROPIC_|^CLAUDE_CODE_USE_|API_KEY|AUTH_TOKEN|OAUTH|FILE_DESCRIPTOR")
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"
GROK_KEY_CONFIG = re.compile(r"[\"']?\b(api_key|env_key|auth_provider|auth_provider_command)\b[\"']?\s*=")
CODEX_KEY_CONFIG = re.compile(
    r"[\"']?\b(env_key|experimental_bearer_token)\b[\"']?\s*="
    r"|[\"']?\b(forced_login_method)\b[\"']?\s*=\s*[\"']api[\"']"
    r"|[\"']?\b(preferred_auth_method)\b[\"']?\s*=\s*[\"']apikey[\"']"
    r"|[\"']?\b(model_provider)\b[\"']?\s*=\s*[\"'](?!openai[\"'])")
CODEX_KEY_OVERRIDES = ("env_key", "experimental_bearer_token", "forced_login_method", "preferred_auth_method")
# System locations, kept as names so tests can point them at a temporary folder.
ETC = Path("/etc")
MANAGED_PREFS = Path("/Library/Managed Preferences")
CLAUDE_MANAGED_SETTINGS = Path("/Library/Application Support/ClaudeCode/managed-settings.json")
MDM_DOMAIN = {"claude": "com.anthropic.claudecode", "codex": "com.openai.codex", "grok": "ai.x.grok"}
# MiniMoi's pinned Claude Code (the copy the agent connection runs) comes first, newest version first. Homebrew's
# copy is not kept current: on 28 Oct it was 2.1.76, against 2.1.308 in the runtime folder.
BINARIES = {
    "claude": ("~/minimoi-runtime/tools/claude-code-*/claude.app/Contents/MacOS/claude",
               "/opt/homebrew/bin/claude", "~/.local/bin/claude", "~/.claude/local/claude"),
    "codex": ("~/.local/bin/codex", "/opt/homebrew/bin/codex"),
    "grok": ("~/.grok/bin/grok",),
}
LOGIN_HINT = {"claude": "start `claude` once and run /login with the plan account",
              "codex": "run `codex login` and choose Sign in with ChatGPT",
              "grok": "run `grok login` and sign in with the plan account"}
EX_USAGE, EX_UNAVAILABLE, EX_CONFIG = 64, 69, 78


class Refused(Exception):
    """A check failed; the message names the file, setting or argument, never a value."""


def clean_env(environ, tool):
    """Return (env, removed): env keeps only allowlisted names; removed lists every name dropped, sorted."""
    keep = set(ENV_ALLOW) | {TOOL_HOME_VAR[tool]}
    env = {k: v for k, v in environ.items() if k in keep}
    return env, sorted(k for k in environ if k not in env)


def _tool_home(tool, home, environ):
    override = environ.get(TOOL_HOME_VAR[tool], "")
    return Path(override) if override else home / TOOL_HOME_DIR[tool]


def _project_dirs(cwd, home):
    """cwd and its parents, stopping before home (home holds the user-level config, checked separately)."""
    out, here = [], cwd.resolve()
    home = home.resolve()
    while here != home and here != here.parent:
        out.append(here)
        here = here.parent
    return out


def _mdm_plists(tool, environ):
    domain = MDM_DOMAIN[tool] + ".plist"
    user = environ.get("USER", "")
    return [MANAGED_PREFS / domain] + ([MANAGED_PREFS / user / domain] if user else [])


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise Refused(f"{path} could not be read as JSON")


def _scan_toml(paths, pattern, what):
    """Refuse on the first layer whose text matches pattern. Matches anywhere in the text, so dotted keys, quoted keys
    and inline tables are caught; a commented-out line is refused too (fail closed)."""
    for path in paths:
        if path.is_file():
            found = pattern.search(path.read_text(errors="replace"))
            if found:
                name = found.group(0).split("=", 1)[0].strip(" \t\"'")
                raise Refused(f"{path} sets `{name}`, {what}")


def _refuse_mdm(tool, environ):
    for path in _mdm_plists(tool, environ):
        if path.exists():
            raise Refused(f"managed preferences {path} are present; review them before using the door")


def check_grok(home, cwd, environ, args, probe):
    grok_home = _tool_home("grok", home, environ)
    auth = _read_json(grok_home / "auth.json")
    modes = sorted({v.get("auth_mode") for v in (auth or {}).values() if isinstance(v, dict) and v.get("auth_mode")})
    if not modes:
        raise Refused(f"no plan sign-in in {grok_home / 'auth.json'}")
    layers = [ETC / "grok" / "managed_config.toml", grok_home / "managed_config.toml", grok_home / "config.toml",
              grok_home / "requirements.toml", ETC / "grok" / "requirements.toml"]
    layers += [d / ".grok" / "config.toml" for d in _project_dirs(cwd, home)]
    _scan_toml(layers, GROK_KEY_CONFIG, "which bills a key or an outside token instead of the plan")
    _refuse_mdm("grok", environ)
    return "plan sign-in (" + ", ".join(modes) + ")"


def _claude_settings_problem(settings):
    if not isinstance(settings, dict):
        return None
    if "apiKeyHelper" in settings:
        return "sets apiKeyHelper, which bills a key instead of the plan"
    block = settings.get("env") if isinstance(settings.get("env"), dict) else {}
    names = sorted(n for n in block if CLAUDE_ENV_REFUSED.search(n) or SECRET_NAME.search(n))
    return f"puts {', '.join(names)} in Claude Code's environment" if names else None


def _claude_settings_args(args, cwd):
    """(label, settings) for every --settings argument, which takes a file path or a JSON string."""
    out = []
    for i, arg in enumerate(args):
        value = None
        if arg == "--settings":
            if i + 1 >= len(args):
                raise Refused("--settings has no value")
            value = args[i + 1]
        elif arg.startswith("--settings="):
            value = arg.split("=", 1)[1]
        if value is None:
            continue
        if value.lstrip().startswith("{"):
            try:
                out.append(("the --settings argument", json.loads(value)))
            except ValueError:
                raise Refused("the --settings argument is not valid JSON")
        else:
            path = Path(os.path.expanduser(value))
            path = path if path.is_absolute() else cwd / path
            settings = _read_json(path)
            if settings is None:
                raise Refused(f"the --settings file {path} does not exist")
            out.append((f"the --settings file {path}", settings))
    return out


def check_claude(home, cwd, environ, args, probe):
    claude_home = _tool_home("claude", home, environ)
    service = CLAUDE_KEYCHAIN_SERVICE
    if environ.get("CLAUDE_CONFIG_DIR"):                 # Claude Code keys the Keychain item by its config folder
        service += "-" + hashlib.sha256(environ["CLAUDE_CONFIG_DIR"].encode()).hexdigest()[:8]
    if not ((claude_home / ".credentials.json").is_file() or probe(service)):
        raise Refused("no Claude Code sign-in found (Keychain or .credentials.json)")
    sources = []
    for path in _mdm_plists("claude", environ):
        if path.exists():
            try:
                with path.open("rb") as fh:
                    sources.append((str(path), plistlib.load(fh)))
            except (OSError, plistlib.InvalidFileException, ValueError):
                raise Refused(f"managed preferences {path} could not be read")
    files = [CLAUDE_MANAGED_SETTINGS, claude_home / "settings.json", claude_home / "settings.local.json",
             claude_home / "remote-settings.json"]
    for d in _project_dirs(cwd, home):
        files += [d / ".claude" / "settings.json", d / ".claude" / "settings.local.json"]
    sources += [(str(path), _read_json(path)) for path in files if path.is_file()]
    sources += _claude_settings_args(args, cwd)
    for label, settings in sources:
        problem = _claude_settings_problem(settings)
        if problem:
            raise Refused(f"{label} {problem}")
    return "plan sign-in"


def _env_file_names(path):
    names = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line.removeprefix("export ").split("=", 1)[0].strip())
    return names


def _codex_override_problem(override):
    key, _, value = override.partition("=")
    key, value = key.strip(), value.strip().strip("\"'")
    leaf = key.rsplit(".", 1)[-1]
    if leaf in CODEX_KEY_OVERRIDES or key.startswith("model_providers"):
        return f"the -c override `{key}` routes Codex to a key"
    if leaf == "model_provider" and value != "openai":
        return f"the -c override `{key}` selects a provider other than the plan"
    return None


def check_codex(home, cwd, environ, args, probe):
    codex_home = _tool_home("codex", home, environ)
    auth = _read_json(codex_home / "auth.json")
    if not isinstance(auth, dict) or auth.get("auth_mode") != "chatgpt":
        raise Refused(f"{codex_home / 'auth.json'} is not a ChatGPT sign-in")
    if auth.get("OPENAI_API_KEY"):
        raise Refused(f"{codex_home / 'auth.json'} also stores an API key")
    env_file = codex_home / ".env"
    if env_file.is_file():
        names = _env_file_names(env_file)
        if names:
            raise Refused(f"{env_file} sets {', '.join(names)}, which Codex loads into its own environment")
    layers = [ETC / "codex" / "managed_config.toml", codex_home / "managed_config.toml", codex_home / "config.toml",
              ETC / "codex" / "config.toml", codex_home / "requirements.toml", ETC / "codex" / "requirements.toml"]
    layers += [d / ".codex" / "config.toml" for d in _project_dirs(cwd, home)]
    _scan_toml(layers, CODEX_KEY_CONFIG, "which routes Codex to a key instead of the ChatGPT plan")
    _refuse_mdm("codex", environ)
    for i, arg in enumerate(args):
        if arg == "--remote-auth-token-env" or arg.startswith("--remote-auth-token-env="):
            raise Refused("--remote-auth-token-env hands Codex a token from the environment")
        override = None
        if arg in ("-c", "--config") and i + 1 < len(args):
            override = args[i + 1]
        elif arg.startswith("--config="):
            override = arg.split("=", 1)[1]
        elif arg.startswith("-c") and not arg.startswith("--") and len(arg) > 2:
            override = arg[2:]
        problem = _codex_override_problem(override) if override else None
        if problem:
            raise Refused(problem)
    return "plan sign-in (chatgpt)"


CHECKS = {"claude": check_claude, "codex": check_codex, "grok": check_grok}


def _version(text):
    return tuple(int(n) for n in re.findall(r"\d+", text))


def _expand(candidate, home):
    """One candidate as paths. A '*' matches the installed versions, newest first (2.1.308 before 2.1.40)."""
    path = candidate.replace("~", str(home), 1) if candidate.startswith("~") else candidate
    if "*" not in path:
        return [path]
    head, tail = path.split("*", 1)
    found = glob.glob(glob.escape(head) + "*" + glob.escape(tail))
    return sorted(found, key=lambda p: _version(p[len(head):len(p) - len(tail)]), reverse=True)


def resolve_binary(tool, home, self_path):
    """The first installed candidate that is not this launcher. None when the tool is not installed."""
    me = os.path.realpath(self_path)
    for candidate in BINARIES[tool]:
        for path in _expand(candidate, home):
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
    root, folder = home / ".minimoi", home / ".minimoi" / "agents"
    row = {"schema": "minimoi.fallback_session.v0", "tool": tool, "binary": binary, "cwd": str(cwd),
           "host": socket.gethostname(), "pid": os.getpid(),
           "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "removed_credential_names": [n for n in removed if SECRET_NAME.search(n)]}
    try:
        for d in (root, folder):                         # only folders created here get 0700; existing ones are kept
            d.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(folder / "fallback_sessions.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
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
        label = CHECKS[tool](home, cwd, environ, args, probe)
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
    try:
        execve(binary, [binary, *args], env)
    except OSError as exc:
        print(f"mm-{tool}: {binary} could not be started ({exc.__class__.__name__})", file=sys.stderr)
        return EX_UNAVAILABLE
    return 0                                                     # reached only when execve is replaced in tests


if __name__ == "__main__":
    sys.exit(main(sys.argv))
