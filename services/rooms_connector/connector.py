"""Rooms R2 connector (ROOMS_R2.md §3.2): runs on this Mac under launchd.

The reviewed R1 worker loop with the Claude Code runner in place of the MC
relay. It reaches Records only through the records-door sidecar on
127.0.0.1:18881 with its own work-scoped credential, and posts Claude Code's
replies with Claude Code's own membership-scoped credential. One worker per
hosted teammate: Claude Code on the Mac, Codex in its container (R2 v0.4 §3.6).
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.rooms_connector.claude_runner import ClaudeRunner
from services.rooms_worker.clients import Records, Unavailable, read_secret
from services.rooms_worker.journal import TurnJournal
from services.rooms_worker.worker import Worker, log


def build(env=os.environ):
    teammate = env.get("ROOMS_TEAMMATE", "claude-code")
    if env.get("ROOMS_MANAGED_LEDGER"):
        from services.rooms_connector.managed_runner import ManagedRunner
        secrets = Path(env.get("ROOMS_CONNECTOR_SECRETS", os.path.expanduser("~/minimoi-staging/secrets/rooms-connector")))
        state = Path(env.get("ROOMS_CONNECTOR_STATE", os.path.expanduser("~/minimoi-staging/data/rooms-managed/" + teammate)))
        url = env.get("RECORDS_URL", "http://127.0.0.1:18881")
        runner = ManagedRunner(env["ROOMS_MANAGED_LEDGER"], teammate)
        work_token = "rooms-connector-codex.token" if teammate == "codex" else "rooms-connector-mac.token"
        runtime_name = "Managed " + teammate
    elif teammate == "codex":                     # R2 v0.4 §3.6: inside the rooms-codex container
        from services.rooms_connector.codex_runner import CodexRunner
        secrets = Path(env.get("ROOMS_CONNECTOR_SECRETS", "/run/secrets/rooms"))
        state = Path(env.get("ROOMS_CONNECTOR_STATE", "/state"))
        url = env.get("RECORDS_URL", "http://minimoi-records:18880")
        runner = CodexRunner(cli=env.get("CODEX_CLI", "/usr/local/bin/codex"),
                             home=env.get("CODEX_HOME", "/codex-home"), state_dir=state,
                             turns_root=env.get("ROOMS_TURNS", "/turns"))
        work_token, runtime_name = "rooms-connector-codex.token", "Codex CLI"
    else:
        secrets = Path(env.get("ROOMS_CONNECTOR_SECRETS", os.path.expanduser("~/minimoi-staging/secrets/rooms-connector")))
        state = Path(env.get("ROOMS_CONNECTOR_STATE", os.path.expanduser("~/minimoi-staging/data/rooms-connector")))
        url = env.get("RECORDS_URL", "http://127.0.0.1:18881")
        runner = ClaudeRunner(cli=env.get("CLAUDE_CLI", "/opt/homebrew/bin/claude"), state_dir=state)
        work_token, runtime_name = "rooms-connector-mac.token", "Claude Code CLI"
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        runner.fingerprint()
    except Exception:
        pass
    teammate_records = Records(url, lambda: read_secret(secrets / f"{teammate}.token"))
    if hasattr(runner, "bind_files"):
        runner.bind_files(teammate_records.get_bytes)          # the teammate fetches a room's shared files with ITS OWN credential, so membership is checked again by Records
    worker = Worker(Records(url, lambda: read_secret(secrets / work_token)),
                    teammate_records,
                    runner, TurnJournal(state / "journal"), teammate=teammate,
                    agent_id=teammate, runtime=f"{runtime_name} {runner.version or 'unknown'}")
    worker.on_committed = getattr(runner, "record_proof", None)
    return worker, state


def main():
    # Managed workers share a canonical lock regardless of their journal path.
    # Old standalone workers must be retired during the reviewed cutover.
    managed_lock = None
    if os.environ.get('ROOMS_MANAGED_LEDGER'):
        folder=Path(os.environ['ROOMS_MANAGED_LEDGER'])/'room-workers'
        folder.mkdir(mode=0o700,parents=True,exist_ok=True)
        teammate=os.environ.get('ROOMS_TEAMMATE','claude-code')
        if teammate not in ('claude-code','codex'): raise ValueError('Invalid teammate')
        managed_lock=open(folder/(teammate+'.lock'),'a')
        fcntl.flock(managed_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    worker, state = build()
    lock = open(state / "connector.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)            # one connector per Mac
    log(event="started", teammate=worker.teammate, runtime=worker.runtime)
    worker.start_housekeeping()
    while True:
        try:
            busy = worker.run_once()
        except Unavailable as error:
            log(event="records_unavailable", detail=str(error))
            busy = False
        except Exception as error:
            log(event="loop_error", kind=type(error).__name__)
            busy = False
        if not busy:
            time.sleep(1)


if __name__ == "__main__":
    main()
