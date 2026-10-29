"""The Mac's side of P1-a's integrated test (tests/harness_acceptance/p1a.py): two helpers Robert runs on the Mac, from
a checkout, with ``/usr/bin/python3`` (stdlib only, Python 3.9). Never run in this build.

    python3 tests/harness_acceptance/p1a_mac.py kill-at-commit --op <operation id>
        I-5: waits for the production worker's sink to commit that operation, then SIGKILLs the worker at once (the
        pid the worker's own one-instance lock names), so the effect is committed and its receipt never finished.
        launchd restarts the worker (KeepAlive); with the sink's lookup off, its reconcile goes to ``unknown``.
    python3 tests/harness_acceptance/p1a_mac.py pack --out <folder>
        the end: copies the worker's own evidence (the sink's effects.jsonl, its pending logs, its census and its
        witness memory) into <folder>, for Robert to place in the run's evidence as mac/. Names, ids and digests only:
        none of these files holds a value (LCH-6's rule for the runtime's files).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

BASE = Path("~/minimoi-runtime/prod").expanduser()
LOCK = Path("~/minimoi-runtime/locks/mac-prod-1.prod.lock").expanduser()       # host.HostLock's path for this host
EFFECTS = BASE / "acceptance" / "effects.jsonl"


def committed(effects: Path, op: str) -> bool:
    try:
        lines = effects.read_text().splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if doc.get("operation_id") == op and doc.get("answer") == "committed":
            return True
    return False


def holder(lock: Path):
    """The worker's pid, as its one-instance lock records it (host.HostLock writes {"pid", "since"})."""
    try:
        pid = json.loads(lock.read_text() or "{}").get("pid")
    except (OSError, ValueError):
        return None
    return pid if isinstance(pid, int) and pid > 1 else None


def kill_at_commit(op, *, effects=EFFECTS, lock=LOCK, timeout_s=1800, every_s=0.05, kill=os.kill,
                   clock=time.monotonic, sleep=time.sleep) -> dict:
    deadline = clock() + timeout_s
    while clock() < deadline:
        if committed(effects, op):
            pid = holder(lock)
            if pid is None:
                return {"state": "no_worker_pid"}
            kill(pid, signal.SIGKILL)
            return {"state": "killed", "pid": pid, "operation_id": op}
        sleep(every_s)
    return {"state": "timed_out"}


def pack(out: Path, *, base=BASE) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    copied = []
    for rel in ("acceptance/effects.jsonl", "artifacts/witness/told.jsonl", "artifacts/witness/failures.jsonl",
                "artifacts/host-state.json"):
        src = base / rel
        if src.is_file():
            shutil.copy2(src, out / src.name)
            copied.append(rel)
    for rel in ("artifacts/pending", "artifacts/census", "artifacts/launch"):
        src = base / rel
        if src.is_dir():
            shutil.copytree(src, out / src.name)
            copied.append(rel)
    return {"state": "packed", "copied": copied}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="p1a_mac.py")
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("kill-at-commit")
    k.add_argument("--op", required=True)
    k.add_argument("--effects", type=Path, default=EFFECTS)
    k.add_argument("--lock", type=Path, default=LOCK)
    k.add_argument("--timeout-s", type=float, default=1800)
    pk = sub.add_parser("pack")
    pk.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)
    if args.cmd == "kill-at-commit":
        out = kill_at_commit(args.op, effects=args.effects, lock=args.lock, timeout_s=args.timeout_s)
    else:
        out = pack(args.out)
    print(json.dumps(out, sort_keys=True))
    return 0 if out["state"] in ("killed", "packed") else 1


if __name__ == "__main__":
    sys.exit(main())
