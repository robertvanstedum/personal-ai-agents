#!/usr/bin/env python3
"""
minimoi_portal/dev_launcher.py — secret-free launchd entry point for the
host-native dev portal.

DATABASE_URL only exists in the project's gitignored .env, in Docker
Compose form (host "postgres"). Running the portal natively on the Mac
(outside Docker) needs that host rewritten to "localhost" -- everything
else about the value passes through unchanged. This script never prints,
logs, or otherwise exposes the DSN; it only sets it in the environment of
the child process it execs into.

launchd's ProgramArguments points here instead of directly at app.py, so
the local launchd plist itself never contains a database password.

No shell involved anywhere (no `source`, no /bin/sh) -- the DSN's password
may contain shell-sensitive characters (quotes, $, backticks, etc.), so
this only ever does argv-list exec via os.execve, never a shell string.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ENV_FILE = Path(
    os.environ.get("MINIMOI_ENV_FILE", "/Users/vanstedum/Projects/personal-ai-agents/.env")
)
APP_SCRIPT = Path(__file__).resolve().parent / "app.py"
EXPECTED_SCHEME = "postgresql"
EXPECTED_DB = "personal_agents"
DOCKER_HOST = "postgres"
NATIVE_HOST = "localhost"


def _fail(message: str) -> None:
    print(f"dev_launcher: {message}", file=sys.stderr)
    raise SystemExit(1)


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        _fail(f"env file not found: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _rewrite_host_to_native(dsn: str) -> str:
    """Validate shape and swap a Docker Compose hostname for localhost,
    without ever decomposing/re-encoding the password itself."""
    parsed = urlsplit(dsn)
    if parsed.scheme != EXPECTED_SCHEME:
        _fail(f"unexpected DATABASE_URL scheme {parsed.scheme!r} (expected {EXPECTED_SCHEME!r})")
    if "@" not in parsed.netloc:
        _fail("unexpected DATABASE_URL shape: no userinfo separator")

    # Split on the LAST '@' and the LAST ':' so a password containing
    # either character (never touched beyond this point) can't corrupt
    # the split -- only the trailing host[:port] segment is inspected.
    userinfo, _, hostport = parsed.netloc.rpartition("@")
    if ":" in hostport:
        host, _, port = hostport.rpartition(":")
    else:
        host, port = hostport, ""

    if host not in (DOCKER_HOST, NATIVE_HOST):
        _fail(f"unexpected DATABASE_URL host {host!r} (expected {DOCKER_HOST!r} or {NATIVE_HOST!r})")

    db_name = parsed.path.lstrip("/")
    if db_name != EXPECTED_DB:
        _fail(f"unexpected DATABASE_URL database {db_name!r} (expected {EXPECTED_DB!r})")

    new_hostport = f"{NATIVE_HOST}:{port}" if port else NATIVE_HOST
    new_netloc = f"{userinfo}@{new_hostport}"
    return urlunsplit((parsed.scheme, new_netloc, parsed.path, parsed.query, parsed.fragment))


def main() -> None:
    env_values = _read_env_file(ENV_FILE)
    dsn = env_values.get("DATABASE_URL")
    if not dsn:
        _fail(f"DATABASE_URL not set in {ENV_FILE}")

    native_dsn = _rewrite_host_to_native(dsn)

    if not APP_SCRIPT.is_file():
        _fail(f"portal app script not found: {APP_SCRIPT}")

    child_env = dict(os.environ)
    child_env["DATABASE_URL"] = native_dsn

    python = sys.executable
    os.execve(python, [python, str(APP_SCRIPT)], child_env)


if __name__ == "__main__":
    main()
