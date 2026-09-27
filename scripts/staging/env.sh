#!/bin/bash
# env.sh — write $STAGING_ROOT/.env (mode 600) for the staging stack.
#
# Values come from the root checkout's existing .env, copied by NAME from an
# allowlist; a value that is missing there falls back to the macOS Keychain
# (a Keychain "Allow" prompt for `security` may appear). No value is ever
# printed: the script reports names and where each came from, nothing else,
# and records the same (name and source, never a value) in
# $STAGING_ROOT/env.sources for verify.sh.
#
# Telegram bot tokens are NEVER taken from the root .env: that file may hold
# a production token, and utils/telegram.py lets the environment variable win
# over the role, so a copied production token would make the staging bot poll
# the production bot. The allowlist has no TELEGRAM*TOKEN* name. The two
# staging bot tokens come only from the Keychain TEST accounts
# (telegram/cos_test_bot_token, telegram/system_test_bot_token), and only
# while $STAGING_ROOT/state/bots.on exists; with the bots off they are not
# written at all. Any TELEGRAM*TOKEN* name in the root .env is reported as
# ignored. Turning the bots on or off means rerunning env.sh --force.
#
# Never written: TELEGRAM_BOT_TOKEN, TELEGRAM_POLLING_BOT_TOKEN, any AWS_*,
# any name ending _PROD, and never a production Telegram token.
#
# Usage: env.sh [--force] [--source-env PATH]
#   --force         replace an existing staging .env (the old one is kept as
#                   $STAGING_ROOT/state/env.prev, mode 600)
#   --source-env    the .env to copy from (default: the root checkout's .env)

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

FORCE=0
SOURCE_ENV=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1 ;;
    --source-env) SOURCE_ENV="${2:?--source-env needs a path}"; shift ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

require_absolute_root
if [[ -z "$SOURCE_ENV" ]]; then
  # The main (root) checkout is the first entry of `git worktree list`.
  MAIN_CHECKOUT=$(git -C "$STAGING_SCRIPTS_DIR" worktree list --porcelain | sed -n '1s/^worktree //p')
  [[ -n "$MAIN_CHECKOUT" ]] || die "cannot find the root checkout; pass --source-env PATH"
  SOURCE_ENV="$MAIN_CHECKOUT/.env"
fi
[[ -f "$SOURCE_ENV" ]] || die "source env file not found: $SOURCE_ENV"

if [[ -e "$STAGING_ENV_FILE" && "$FORCE" != "1" ]]; then
  die "$STAGING_ENV_FILE exists; pass --force to replace it"
fi

umask 077
mkdir -p "$STAGING_ROOT/state"
chmod 700 "$STAGING_ROOT"

STAGING_ROOT="$STAGING_ROOT" SOURCE_ENV="$SOURCE_ENV" STAGING_ENV_FILE="$STAGING_ENV_FILE" \
  STAGING_ENV_SOURCES="$STAGING_ENV_SOURCES" STAGING_BOTS_FLAG="$STAGING_BOTS_FLAG" \
  STAGING_NO_KEYCHAIN="${STAGING_NO_KEYCHAIN:-0}" \
  GERMAN_PLIST="${STAGING_GERMAN_PLIST:-$HOME/Library/LaunchAgents/com.vanstedum.german-html-server.plist}" \
  /usr/bin/python3 - <<'PY'
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

root = os.environ["STAGING_ROOT"]
source = os.environ["SOURCE_ENV"]
target = os.environ["STAGING_ENV_FILE"]
sources_target = os.environ["STAGING_ENV_SOURCES"]
bots_on = os.path.exists(os.environ["STAGING_BOTS_FLAG"])
german_plist = os.environ["GERMAN_PLIST"]

# name: (required, keychain service, keychain account)
COPIED = {
    "ANTHROPIC_API_KEY": (True, "anthropic", "api_key"),
    "XAI_API_KEY": (True, "xai", "api_key"),
    "MINIMOI_MODEL_GATEWAY_KEY": (True, None, None),
    "MINIMOI_MODEL_GATEWAY_RECEIPT_KEY": (True, None, None),
    # Must stay the DEV value: the Records CoS worker's token file matches it.
    "COS_AGENT_A_GATEWAY_TOKEN": (True, None, None),
    "DATABASE_URL": (True, "minimoi-dev-db", "database_url"),
    "POSTGRES_PASSWORD": (True, None, None),
    "PORTAL_SECRET_KEY": (True, None, None),
    "OPENAI_API_KEY": (False, "openai", "api_key"),
    "CURATOR_XAI_MODEL": (False, None, None),
    "TELEGRAM_CHAT_ID": (False, "telegram", "chat_id"),
    # Containers cannot read the Keychain, so these are copied in by name.
    "DEEPL_API_KEY": (False, "deepl", "api_key"),
}
# The staging bots' tokens: Keychain TEST accounts ONLY, never the root .env,
# and only while bots.on exists. Keep in step with lib.sh telegram_test_source.
TELEGRAM_TEST_TOKENS = {
    "TELEGRAM_COS_BOT_TOKEN": ("telegram", "cos_test_bot_token"),
    "TELEGRAM_SYSTEM_BOT_TOKEN": ("telegram", "system_test_bot_token"),
}
TELEGRAM_TOKEN_NAME = re.compile(r"^TELEGRAM.*TOKEN")
assert not any(TELEGRAM_TOKEN_NAME.match(name) for name in COPIED), "no Telegram token may be copied from .env"
PRODUCTION_KEYCHAIN_ACCOUNTS = {"bot_token", "polling_bot_token", "cos_bot_token", "system_bot_token"}
EXPLICIT = {
    "MINIMOI_ROOT": root,
    "MINIMOI_ROLE": "standby",
    "BASE_URL": "https://dev.minimoi.ai",
    "SESSION_COOKIE_SECURE": "true",
    "COS_BACKEND": "http://cos-scheduler:8769",
    "COS_BACKEND_TYPE": "openclaw",
    "VOICE_REALTIME_UI_ENABLED": "1",
    "CURATOR_BACKEND": "http://curator:8766",
    "GERMAN_BACKEND": "http://german:8767",
    "PORTUGUESE_BACKEND": "http://portuguese:8770",
}
FORBIDDEN = re.compile(r"^(TELEGRAM_BOT_TOKEN|TELEGRAM_POLLING_BOT_TOKEN|AWS_.*|.*_PROD|MINIMOI_GUILD_.*)$")


def parse_env(path):
    values = {}
    for raw in open(path, encoding="utf-8").read().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def keychain(service, account):
    if not service or account in PRODUCTION_KEYCHAIN_ACCOUNTS:
        return None
    if os.environ.get("STAGING_NO_KEYCHAIN") == "1":
        return None
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=60,
        )
    except Exception:
        return None
    value = result.stdout.rstrip("\n")
    return value if result.returncode == 0 and value else None


def plist_value(key):
    if not os.path.exists(german_plist):
        return None
    result = subprocess.run(
        ["/usr/libexec/PlistBuddy", "-c", f"Print :EnvironmentVariables:{key}", german_plist],
        capture_output=True, text=True,
    )
    value = result.stdout.rstrip("\n")
    return value if result.returncode == 0 and value else None


def container_database_url(url):
    """Point a host-side dev URL at the in-network postgres service."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1", "host.docker.internal", "postgres", "postgres-ai-agents"}:
        raise SystemExit("env.sh: DATABASE_URL does not point at the local dev database; refusing (value withheld)")
    userinfo = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
    return urlunsplit((parts.scheme, f"{userinfo}postgres:5432", parts.path, parts.query, parts.fragment)), host


def quote(value):
    if "\n" in value or "\r" in value:
        raise SystemExit("env.sh: a value contains a newline; refusing")
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value and "\\" not in value and "$" not in value:
        return f'"{value}"'
    raise SystemExit("env.sh: a value contains both quote kinds; refusing")


src = parse_env(source)
out = {}
sources = {}   # name -> source label (never a value), written to env.sources
report = []
missing = []
for name, (required, service, account) in COPIED.items():
    value = src.get(name) or None
    origin = "root-env"
    if value is None:
        value = keychain(service, account)
        origin = f"keychain:{service}/{account}" if value else None
    if value is None:
        if required:
            missing.append(name)
        report.append(f"  {name}: absent (optional)" if not required else f"  {name}: MISSING")
        continue
    out[name] = value
    sources[name] = origin
    report.append(f"  {name}: {origin}")

for name, (service, account) in TELEGRAM_TEST_TOKENS.items():
    if not bots_on:
        report.append(f"  {name}: not written (bots off; touch state/bots.on and rerun env.sh --force)")
        continue
    value = keychain(service, account)
    if value is None:
        missing.append(f"{name} (Keychain {service}/{account}; scripts/store_test_tokens.sh)")
        report.append(f"  {name}: MISSING")
        continue
    out[name] = value
    sources[name] = f"keychain:{service}/{account}"
    report.append(f"  {name}: {sources[name]}")
for name in sorted(n for n in src if TELEGRAM_TOKEN_NAME.match(n)):
    report.append(f"  {name}: in the root .env, IGNORED (staging bot tokens come only from the Keychain test accounts)")

for key in ("WHEREBY_ROOM_URL", "WHEREBY_HOST_URL"):
    value = src.get(key) or plist_value(key)
    if value:
        out[key] = value
        sources[key] = "root-env" if src.get(key) else "plist:german-html-server"
        report.append(f"  {key}: {sources[key]}")
    else:
        report.append(f"  {key}: absent (optional)")

if missing:
    print("env.sh: required values are missing:", file=sys.stderr)
    for name in missing:
        print(f"  {name}", file=sys.stderr)
    print("Add them to the root .env (or the Keychain entry named in env.sh), then rerun. Nothing was written.",
          file=sys.stderr)
    raise SystemExit(1)

if len(out["PORTAL_SECRET_KEY"]) < 32:
    raise SystemExit("env.sh: PORTAL_SECRET_KEY is shorter than 32 characters (the portal refuses it); nothing written")

out["DATABASE_URL"], original_host = container_database_url(out["DATABASE_URL"])
report.append(f"  DATABASE_URL host: {original_host} -> postgres:5432")

for name, value in EXPLICIT.items():
    out[name] = value
    sources[name] = "env.sh"
    report.append(f"  {name}: set by env.sh")

bad = sorted(name for name in out if FORBIDDEN.match(name))
if bad:
    raise SystemExit(f"env.sh: refusing to write forbidden names: {', '.join(bad)}")

stray = sorted(n for n in out if TELEGRAM_TOKEN_NAME.match(n) and sources.get(n) != "keychain:%s/%s" % TELEGRAM_TEST_TOKENS.get(n, ("?", "?")))
if stray:
    raise SystemExit(f"env.sh: a Telegram token would not come from its Keychain test account: {', '.join(stray)}")

lines = ["# Staging .env, written by scripts/staging/env.sh. Mode 600. Never commit or print.",
         "# Copied by name from the root checkout's .env (or the Keychain); see env.sh."]
lines += [f"{name}={quote(value)}" for name, value in sorted(out.items())]
source_lines = ["# Where scripts/staging/env.sh got each name in .env: NAME SOURCE. No values.",
                f"# bots_on={'1' if bots_on else '0'}"]
source_lines += [f"{name} {sources[name]}" for name in sorted(out)]


def write_private(path, text, prev_name):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(tmp, 0o600)
    if os.path.exists(path):
        prev = os.path.join(root, "state", prev_name)
        os.replace(path, prev)
        os.chmod(prev, 0o600)
    os.replace(tmp, path)


write_private(target, "\n".join(lines) + "\n", "env.prev")
write_private(sources_target, "\n".join(source_lines) + "\n", "env.sources.prev")
print(f"env.sh: wrote {target} (mode 600) with {len(out)} names, sources in {sources_target}:")
print("\n".join(report))
PY
