#!/usr/bin/env python3
"""Operator entry point for the connection manager. Nothing in the repository installs, schedules or starts it.

  agent_connection.py --root R --agent claude-code --provider claude-stream --session-id <uuid> --claude-bin /abs/claude --cwd /abs/dir --allowed-tools "Read,Bash(...)" --print-argv
  agent_connection.py --root R --agent codex       --provider codex-app-server --codex-bin /abs/codex --cwd /abs/dir [--thread-id ID | --create] --print-argv
        print the exact command the provider would run, and exit. Start nothing.
  ... run                 watch the ledger and probe the session. NEVER starts a session.
  ... run --start         also start the explicitly identified persistent session, and RESTART it if it dies (resuming the same saved session, with bounded backoff).
  ... print-launchd       print a launchd plist for ``run --start`` (for review; nothing is installed)

Every start goes through the launch contract (host runtime spec v0.5 R6.7-d; core/mc_delegation_runner/launch.py and launch_policy.json): the binary must
match its pin (``--claude-bin-sha256`` / ``--codex-bin-sha256``: the SHA-256 of the exact file named by --claude-bin / --codex-bin), the plan sign-in must be
present, and no key setting may sit in the user or project configuration. A failed check refuses the start before any spawn and names the check (never a
value). The first start and every restart are treated alike: the refusal is written to status.json (``last_restart_error``, ``restart_attempts``) and the start
is retried on the restart backoff (1, 2, 5 ... 300 s); the manager keeps running, so launchd never restarts it in a loop. ``print-launchd`` runs the same full
check now (pin, sign-in, key settings) and refuses, naming why, to print a plist whose every start would be refused. A check that passes at print time can
still fail later (an updated binary, a sign-out): that is what status.json is for. Updating the CLI is a configuration change: a new path and its digest, then
a reinstall.

The ledger root, helper and interpreter are absolute and bound into every command the session is given; there is no default ledger. A first session is created only with an explicit
--session-id (Claude) or --create (Codex); every later start RESUMES the recorded one. Zero model calls while the queue is empty. SIGTERM/Ctrl-C stop the manager and close the
session's stdin; the saved session (its history) stays."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import xml.sax.saxutils as xml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.agent_requests.claude_stream import ClaudeStreamProvider, LaunchRefused, helper_selector  # noqa: E402
from core.agent_requests.codex_app_server import CodexAppServerProvider  # noqa: E402
from core.agent_requests.connection import (DEFAULT_HELPER, RESTART_BACKOFF_S, ConnectionManager, IdentityMismatch, LeaseHeld, StateUnreadable,  # noqa: E402
                                            read_identity, run_forever)

LAUNCH_LABEL = {"claude-code": "ai.minimoi.connection.claude-code", "codex": "ai.minimoi.connection.codex"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", required=True, help="the ledger folder (absolute); a managed session never falls back to a default ledger")
    p.add_argument("--agent", required=True, choices=["claude-code", "codex"])
    p.add_argument("--provider", required=True, choices=["claude-stream", "codex-app-server"])
    p.add_argument("--cwd", required=True)
    p.add_argument("--session-id", help="Claude: the UUID of the persistent session (created the first time, resumed after)")
    p.add_argument("--claude-bin")
    p.add_argument("--claude-bin-sha256", help="the pinned SHA-256 of --claude-bin (launch contract); a start without it, or with another digest, is refused")
    p.add_argument("--allowed-tools", default="Read", help="comma-separated tool names; the ledger helper is added automatically as one exact Bash selector, and no other Bash permission is accepted")
    p.add_argument("--permission-mode", default="default")
    p.add_argument("--max-budget-usd", type=float, default=5.0, help="the Claude process cap when no limits folder is given")
    p.add_argument("--limits-dir", help="the limits registry folder (absolute): the session cap and the daily ceiling are read from it (#296); a changed cap applies at the next restart")
    p.add_argument("--codex-bin")
    p.add_argument("--codex-bin-sha256", help="the pinned SHA-256 of --codex-bin (launch contract); a start without it, or with another digest, is refused")
    p.add_argument("--thread-id", help="Codex: the stored thread id (omit on a later run to use the recorded one)")
    p.add_argument("--create", action="store_true", help="Codex: create the first thread (refused when one is already recorded)")
    p.add_argument("--approval-policy", default="on-request")
    p.add_argument("--sandbox", default="workspace-write")
    p.add_argument("--writable-root", action="append", default=[], help="Codex: an extra exact folder the session may write (the ledger is always included)")
    p.add_argument("--request-timeout", type=float, default=30.0)
    p.add_argument("--adopt-identity", action="store_true")
    p.add_argument("--census-dir", help="the host runtime's census folder (absolute; spec v0.5 §6.6): each start writes the session's root row there")
    p.add_argument("--print-argv", action="store_true")
    p.add_argument("--interval", type=float, default=1.0)
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--start", action="store_true")
    sub.add_parser("print-launchd")
    v = sub.add_parser("resolve-usage-hold", help="lift a standing unknown-usage hold, on the record (say what was reconciled)")
    v.add_argument("--note", required=True)
    return p


def build_provider(a, identity: dict):
    root = os.path.abspath(a.root)
    created = bool(identity.get("created"))
    if a.provider == "claude-stream":
        if a.agent != "claude-code" or not a.session_id or not a.claude_bin:
            raise ValueError("claude-stream needs --agent claude-code, --session-id and --claude-bin")
        selector = helper_selector(sys.executable, DEFAULT_HELPER, root)
        limits = getattr(a, "limits_obj", None)
        cap = (lambda: limits.value("claude.session_cap_usd")) if limits is not None else a.max_budget_usd
        return ClaudeStreamProvider(claude_bin=a.claude_bin, session_id=a.session_id, cwd=a.cwd, allowed_tools=[t for t in a.allowed_tools.split(",") if t] + [selector],
                                    helper_bash=selector,
                                    permission_mode=a.permission_mode, max_budget_usd=cap, resume=created, display_name="claude-code (persistent)", ledger_root=root,
                                    binary_sha256=a.claude_bin_sha256)
    if a.agent != "codex" or not a.codex_bin:
        raise ValueError("codex-app-server needs --agent codex and --codex-bin")
    thread = a.thread_id or identity.get("session_id")
    if a.create and identity.get("session_id"):
        raise ValueError("a Codex thread is already recorded; creating a second first-session is refused (omit --create to resume it)")
    if not thread and not a.create:
        raise ValueError("no Codex thread is recorded: give --thread-id, or --create the first one explicitly")
    return CodexAppServerProvider(codex_bin=a.codex_bin, cwd=a.cwd, ledger_root=root, thread_id=thread, approval_policy=a.approval_policy, sandbox=a.sandbox,
                                  writable_roots=a.writable_root, request_timeout=a.request_timeout, binary_sha256=a.codex_bin_sha256)


def check_start(a, provider) -> None:
    """The plist's ``run --start`` must be able to start: the whole pre-start check of the launch contract, now (the pin, the plan sign-in and the key settings,
    from the same environment the start builds). A refusal names the check, never a value."""
    binary, flag = (a.claude_bin, "--claude-bin-sha256") if a.provider == "claude-stream" else (a.codex_bin, "--codex-bin-sha256")
    try:
        provider.launch_record(provider.child_env())
    except ValueError as exc:                                  # launch.LaunchRefused, or the policy refusing an environment name
        hint = f"; give {flag} with the SHA-256 of {binary}" if getattr(exc, "code", None) == "binary_unpinned" else ""
        raise ValueError(f"{exc}{hint}; a plist whose every start would be refused is not printed") from None


def launchd_plist(a, script: str, identity: dict) -> str:
    if a.provider == "codex-app-server" and not identity.get("session_id"):
        raise ValueError("no Codex thread is recorded yet: create it once with `run --start --create`, then print the resume-only plist")
    args = [sys.executable, script] + [x for x in sys.argv[1:] if x not in ("print-launchd", "--print-argv", "--create")] + ["run", "--start"]   # resume-only: a restart must never create a first session
    log = os.path.join(os.path.abspath(a.root), "connection", a.agent, "manager.log")
    items = "\n".join(f"    <string>{xml.escape(x)}</string>" for x in args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LAUNCH_LABEL[a.agent]}</string>
  <key>ProgramArguments</key><array>
{items}
  </array>
  <key>WorkingDirectory</key><string>{xml.escape(os.path.abspath(a.cwd))}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{xml.escape(log)}</string>
  <key>StandardErrorPath</key><string>{xml.escape(log)}</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin</string>
    <key>MINIMOI_AGENT_REQUESTS_DIR</key><string>{xml.escape(os.path.abspath(a.root))}</string>
  </dict>
</dict></plist>
"""


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    root = os.path.abspath(a.root)
    a.limits_obj = None
    if a.cmd == "resolve-usage-hold":
        from core.agent_requests.connection import resolve_usage_hold
        try:
            lifted = resolve_usage_hold(root, a.agent, a.note)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print("hold lifted and recorded" if lifted else "no hold was standing")
        return 0
    if a.limits_dir:
        if not os.path.isabs(a.limits_dir):
            print("refused: --limits-dir must be an absolute path", file=sys.stderr)
            return 2
        from core.limits import Limits
        a.limits_obj = Limits(a.limits_dir)
    try:
        identity = read_identity(root, a.agent)
        provider = build_provider(a, identity)
    except (ValueError, StateUnreadable) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if a.create and not (a.cmd == "run" and a.start):
        print("refused: --create only applies to `run --start`; nothing is created otherwise", file=sys.stderr)
        return 2
    if a.print_argv or a.cmd is None:
        print(json.dumps(provider.argv()))
        return 0
    if a.cmd == "print-launchd":
        try:
            text = launchd_plist(a, os.path.abspath(__file__), identity)
            check_start(a, provider)
            print(text)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        return 0
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    manager = None
    try:
        if a.provider == "codex-app-server" and not provider.thread_id:           # the first thread: explicit --create only
            provider.start(create=True)
            session_id = provider.thread_id
        else:
            session_id = a.session_id if a.provider == "claude-stream" else provider.thread_id
        manager = ConnectionManager(root, a.agent, provider, session_id=session_id, adopt_identity=a.adopt_identity, limits=getattr(a, "limits_obj", None),
                                    census_dir=a.census_dir)

        def start_or_resume():
            """(Re)start the child. A Claude session exists on disk only after its FIRST TURN, so until then a restart creates it again with the same id; after that every start
            RESUMES it (a resume of a session that never had a turn fails with "No conversation found", which the supervisor reports instead of hiding)."""
            if a.provider == "claude-stream":
                provider.resume = bool(manager.identity().get("created"))
            provider.start()

        def after_pass(out):
            d = out.get("delivered")
            if d:
                print(json.dumps(d), flush=True)
                if d.get("status") == "accepted" and not manager.identity().get("created"):
                    manager.mark_created()                                       # a real turn was accepted: the session now exists and is resumed from here on
        first_failures = 0
        if a.cmd == "run" and a.start:
            try:
                start_or_resume()
            except LaunchRefused as exc:
                # A refused first start (no or a wrong pin, a changed binary, the sign-in missing, a key setting) is treated as a refused restart: named in
                # status.json (never a value) and retried on the restart backoff by the loop below. Exiting instead would make launchd restart the manager
                # every ThrottleInterval, with the reason only in its log (S1-E review SF1).
                first_failures = 1
                manager.note_restart(first_failures, f"{type(exc).__name__}: {exc}", RESTART_BACKOFF_S[0])
                print(f"refused: {exc}; retrying on the restart backoff", file=sys.stderr, flush=True)
        run_forever(manager, stop, interval=a.interval, restart=start_or_resume if (a.cmd == "run" and a.start) else None, on_pass=after_pass,
                    initial_failures=first_failures)
        return 0
    except (LeaseHeld, IdentityMismatch, StateUnreadable, LaunchRefused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    finally:
        provider.close()
        if manager is not None:
            manager.close()


if __name__ == "__main__":
    sys.exit(main())
