"""The Mac's side of P1-a's integrated test (tests/harness_acceptance/p1a.py): two helpers Robert runs on the Mac, from
a checkout, with ``/usr/bin/python3`` (stdlib only, Python 3.9). Never run in this build.

    python3 tests/harness_acceptance/p1a_mac.py kill-at-commit --op <operation id> --item <request id>
        I-5: waits for the production worker's sink to commit that operation for that item, then SIGKILLs the worker
        at once, so the effect is committed and its receipt never finished. Only a process that holds the worker's
        one-instance lock now and runs as the production worker (run_runner.py run --host-config <its host.yaml>),
        checked again right before the signal; any doubt signals nothing.
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
HOST_CONFIG = BASE / "host.yaml"


def committed(effects: Path, op: str, item: str) -> bool:
    try:
        lines = effects.read_text().splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if doc.get("operation_id") == op and doc.get("item_id") == item and doc.get("answer") == "committed":
            return True
    return False


def holder(lock: Path):
    """The worker's pid, only while it holds its one-instance lock (host.HostLock: an exclusive flock, its pid written
    in the file). A lock nobody holds names a process that is gone, or another's by now: None (Codex's P1-a review S4)."""
    import fcntl
    try:
        fd = os.open(lock, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            pass                                       # held by its worker: read who
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None                                # free: nothing holds it, so nothing is killed
        try:
            pid = json.loads(os.read(fd, 4096) or b"{}").get("pid")
        except ValueError:
            return None
    finally:
        os.close(fd)
    return pid if isinstance(pid, int) and pid > 1 else None


def identity(pid, *, host_config=HOST_CONFIG, run=None):
    """The process's start time and command, only when it is the production worker on this host config
    (run_runner.py run --host-config <host_config>); else None."""
    import subprocess
    run = run or subprocess.run
    try:
        out = run(["ps", "-o", "lstart=,command=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    line = (out.stdout or "").strip()
    if out.returncode != 0 or not line:
        return None
    if "run_runner.py" not in line or " run " not in f" {line} " or f"--host-config {host_config}" not in line:
        return None
    return line


def kill_at_commit(op, item, *, effects=EFFECTS, lock=LOCK, host_config=HOST_CONFIG, timeout_s=1800, every_s=0.05,
                   kill=os.kill, clock=time.monotonic, sleep=time.sleep, held=None, ident=None) -> dict:
    """SIGKILL the production worker the moment the sink commits ``op`` for ``item`` (this run's admitted item only),
    and only a process that holds the worker's lock now and is that worker, checked again right before the signal.
    Any doubt signals nothing."""
    held = held or holder
    ident = ident or (lambda pid: identity(pid, host_config=host_config))
    deadline = clock() + timeout_s
    while clock() < deadline:
        if committed(effects, op, item):
            pid = held(lock)
            if pid is None:
                return {"state": "no_worker_pid"}
            who = ident(pid)
            if who is None:
                return {"state": "not_the_worker", "pid": pid}
            if held(lock) != pid or ident(pid) != who:          # rechecked immediately before the signal
                return {"state": "worker_changed", "pid": pid}
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
    k.add_argument("--item", required=True, help="the admitted item's request id: only its own effect counts")
    k.add_argument("--effects", type=Path, default=EFFECTS)
    k.add_argument("--lock", type=Path, default=LOCK)
    k.add_argument("--timeout-s", type=float, default=1800)
    pk = sub.add_parser("pack")
    pk.add_argument("--out", required=True, type=Path)
    args = p.parse_args(argv)
    if args.cmd == "kill-at-commit":
        out = kill_at_commit(args.op, args.item, effects=args.effects, lock=args.lock, timeout_s=args.timeout_s)
    else:
        out = pack(args.out)
    print(json.dumps(out, sort_keys=True))
    return 0 if out["state"] in ("killed", "packed") else 1


if __name__ == "__main__":
    sys.exit(main())
