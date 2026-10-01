"""Rooms R2 connector (ROOMS_R2.md §3.2): runs on this Mac under launchd.

The reviewed R1 worker loop with the Claude Code runner in place of the MC
relay. It reaches Records only through the records-door sidecar on
127.0.0.1:18881 with its own work-scoped credential, and posts Claude Code's
replies with Claude Code's own membership-scoped credential. One worker per
hosted teammate; R2 hosts Claude Code only (Codex is parked, §3.6).
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
    secrets = Path(env.get("ROOMS_CONNECTOR_SECRETS", os.path.expanduser("~/minimoi-staging/secrets/rooms-connector")))
    state = Path(env.get("ROOMS_CONNECTOR_STATE", os.path.expanduser("~/minimoi-staging/data/rooms-connector")))
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    url = env.get("RECORDS_URL", "http://127.0.0.1:18881")
    runner = ClaudeRunner(cli=env.get("CLAUDE_CLI", "/opt/homebrew/bin/claude"), state_dir=state)
    try:
        runner.fingerprint()
    except Exception:
        pass
    worker = Worker(Records(url, read_secret(secrets / "rooms-connector-mac.token")),
                    Records(url, read_secret(secrets / "claude-code.token")),
                    runner, TurnJournal(state / "journal"), teammate="claude-code",
                    agent_id="claude-code", runtime=f"Claude Code CLI {runner.version or 'unknown'}")
    worker.on_committed = runner.record_proof
    return worker, state


def main():
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
