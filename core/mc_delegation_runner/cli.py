"""Run the delegation runner on the Mac (journey 3).

    python -m core.mc_delegation_runner.cli --dsn-file ~/minimoi-runtime/runner/dev.dsn \\
        --principal robert --environment dev --mirror personal-ai-agents=~/minimoi-runtime/runner/mirrors/personal-ai-agents.git \\
        --workspace-root ~/minimoi-runtime/runner/work --artifact-root ~/minimoi-runtime/runner/artifacts [--once]

* The DSN comes from a private file (0600), never the command line or environment of the child.
* Before each claim the mirror is fetched, so the dispatcher's exact commit is present.
* Actors: only those with a real budget adapter (Claude Code today). Scopes: research.
* Each pass is one ``run_once``; a failed pass is logged by type and the loop continues.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import runner
from .cost import ENFORCERS
from .dispatcher_adapter import StoreDispatcher


def _dsn(path: Path) -> str:
    info = os.stat(path)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit("dsn file must not be readable by group or others")
    return path.read_text().strip()


def _fetch(mirror: Path):
    subprocess.run(["git", "--git-dir", str(mirror), "fetch", "--quiet", "origin", "+refs/heads/*:refs/heads/*"],
                   check=False, timeout=120, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                                  "HOME": os.environ.get("HOME", ""), "GIT_TERMINAL_PROMPT": "0"})


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mc-delegation-runner")
    p.add_argument("--dsn-file", required=True, type=Path)
    p.add_argument("--principal", required=True)
    p.add_argument("--environment", required=True, choices=("dev", "prod"))
    p.add_argument("--mirror", action="append", required=True, help="name=path to a bare mirror")
    p.add_argument("--workspace-root", required=True, type=Path)
    p.add_argument("--artifact-root", required=True, type=Path)
    p.add_argument("--runner-id", default="mac-runner-1")
    p.add_argument("--max-cost-usd", type=float, default=5.0)
    p.add_argument("--interval", type=float, default=20.0)
    p.add_argument("--once", action="store_true")
    args = p.parse_args(argv)
    mirrors = {}
    for item in args.mirror:
        name, _, path = item.partition("=")
        mirrors[name] = Path(path).expanduser()
    import psycopg2
    from core.mc_delegation_dispatcher.store import DispatcherStore
    dsn = _dsn(args.dsn_file.expanduser())
    store = DispatcherStore(lambda: psycopg2.connect(dsn, connect_timeout=5), principal=args.principal,
                            environment=args.environment)
    cfg = runner.RunnerConfig(runner_id=args.runner_id, actors=tuple(ENFORCERS), scopes=("research",),
                              repositories={k: str(v) for k, v in mirrors.items()},
                              workspace_root=args.workspace_root.expanduser(),
                              artifact_root=args.artifact_root.expanduser(), max_cost_usd=args.max_cost_usd,
                              cost_enforcers=dict(ENFORCERS))
    dispatcher = StoreDispatcher(store, cfg.artifact_root)
    while True:
        for mirror in mirrors.values():
            _fetch(mirror)
        try:
            out = runner.run_once(dispatcher, cfg)
            print(json.dumps({k: out.get(k) for k in ("state", "request_id", "delivered", "code") if k in out}),
                  flush=True)
        except Exception as exc:
            print(json.dumps({"state": "pass_failed", "error": type(exc).__name__}), flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
