#!/bin/bash
# env.sh — write $STAGING_ROOT/.env (mode 600) for the staging stack.
#
# Values come from the root checkout's existing .env, copied by NAME from an
# allowlist; a value that is missing there falls back to the macOS Keychain
# (a Keychain "Allow" prompt for `security` may appear). No value is ever
# printed: the script reports names and where each came from, nothing else.
#
# Never written: TELEGRAM_BOT_TOKEN, TELEGRAM_POLLING_BOT_TOKEN, any AWS_*,
# any name ending _PROD, and never a production Telegram token. The staging
# bots read the TEST bot tokens and stay off unless the "bots" profile is on.
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
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
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
    # TEST bot tokens only (the production accounts are never read).
    "TELEGRAM_COS_BOT_TOKEN": (False, "telegram", "cos_test_bot_token"),
    "TELEGRAM_SYSTEM_BOT_TOKEN": (False, "telegram", "system_test_bot_token"),
}
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
report = []
missing = []
for name, (required, service, account) in COPIED.items():
    value = src.get(name) or None
    origin = "root .env"
    if value is None:
        value = keychain(service, account)
        origin = f"Keychain {service}/{account}" if value else None
    if value is None:
        if required:
            missing.append(name)
        report.append(f"  {name}: absent (optional)" if not required else f"  {name}: MISSING")
        continue
    out[name] = value
    report.append(f"  {name}: {origin}")

for key in ("WHEREBY_ROOM_URL", "WHEREBY_HOST_URL"):
    value = src.get(key) or plist_value(key)
    if value:
        out[key] = value
        report.append(f"  {key}: {'root .env' if src.get(key) else 'german-html-server plist'}")
    else:
        report.append(f"  {key}: absent (optional)")

if missing:
    print("env.sh: required values are missing from the root .env and the Keychain:", file=sys.stderr)
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
    report.append(f"  {name}: set by env.sh")

bad = sorted(name for name in out if FORBIDDEN.match(name))
if bad:
    raise SystemExit(f"env.sh: refusing to write forbidden names: {', '.join(bad)}")

lines = ["# Staging .env, written by scripts/staging/env.sh. Mode 600. Never commit or print.",
         "# Copied by name from the root checkout's .env (or the Keychain); see env.sh."]
lines += [f"{name}={quote(value)}" for name, value in sorted(out.items())]
tmp = target + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as stream:
    stream.write("\n".join(lines) + "\n")
os.chmod(tmp, 0o600)
if os.path.exists(target):
    prev = os.path.join(root, "state", "env.prev")
    os.replace(target, prev)
    os.chmod(prev, 0o600)
os.replace(tmp, target)
print(f"env.sh: wrote {target} (mode 600) with {len(out)} names:")
print("\n".join(report))
PY
