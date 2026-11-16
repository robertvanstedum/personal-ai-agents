"""Sweep browsers this runner left behind, and record the host before a suite.

The pid file is the only kill list. A recorded pid is stopped on the next start
when that run has exited, and only if its command still carries Playwright's
Chrome flag. A pid that was reused for another program is left alone. An
unrecorded process is stopped only when it is orphaned (parent 1) and carries
that same flag. A Chrome without the flag is never stopped.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

FLAG = "--disable-field-trial-config"
CANARY_S = 8
RECORD = Path(__file__).resolve().parent / "_working" / "browser-runner.json"
RESULT = Path(__file__).resolve().parent / "_working" / "browser-result.json"

_STATE: dict = {}


def list_processes() -> list[dict]:
    out = subprocess.check_output(["ps", "-ax", "-o", "pid=,ppid=,command="], text=True)
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "cmd": parts[2]})
    return rows


def _flagged(proc: dict) -> bool:
    return FLAG in proc.get("cmd", "") and "chrome" in proc.get("cmd", "").lower()


def pids_to_stop(recorded: dict, procs: list[dict]) -> list[int]:
    """Pids this runner may stop. A live run's Chrome, and any Chrome without the flag, stay."""
    by_pid = {p["pid"]: p for p in procs}
    parent = recorded.get("parent")
    parent_dead = parent not in by_pid
    stop: list[int] = []
    for pid in recorded.get("pids") or []:
        proc = by_pid.get(pid)
        # A dead run's Chrome is reparented to pid 1. A pid now owned by a live
        # process is not ours anymore.
        if proc and parent_dead and proc["ppid"] == 1 and _flagged(proc):
            stop.append(pid)
    for proc in procs:
        if proc["pid"] in stop:
            continue
        if proc["ppid"] == 1 and _flagged(proc):
            stop.append(proc["pid"])
    return stop


def stop_pids(pids: list[int]) -> list[int]:
    stopped = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            continue
        stopped.append(pid)
    return stopped


def _read_record() -> dict:
    try:
        return json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def remember(pids: list[int]) -> None:
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(
        json.dumps({"parent": os.getpid(), "pids": sorted(set(pids))}) + "\n",
        encoding="utf-8",
    )


def clear_record() -> None:
    remember([])


def flag_pids() -> set[int]:
    try:
        return {p["pid"] for p in list_processes() if _flagged(p)}
    except (OSError, subprocess.SubprocessError, ValueError):
        return set()


def remember_new(before: set[int]) -> None:
    remember(sorted(flag_pids() - set(before)))


def _write_result(status: str) -> None:
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(
        json.dumps({"status": status, "host": _STATE.get("host", {})}, indent=2) + "\n",
        encoding="utf-8",
    )


def prepare(launch, timeout_s: int = CANARY_S) -> dict:
    """Sweep, then open about:blank. A launch that does not finish is blocked, not a failed build."""
    try:
        procs = list_processes()
    except (OSError, subprocess.SubprocessError, ValueError):
        procs = []
    swept = stop_pids(pids_to_stop(_read_record(), procs))
    host = {"load": [round(n, 2) for n in os.getloadavg()], "swept": swept}
    started = time.monotonic()
    try:
        launch()
        ok = True
    except Exception:
        ok = False
    host["canary_seconds"] = round(time.monotonic() - started, 3)
    host["canary_limit_seconds"] = timeout_s
    _STATE["host"] = host
    _STATE["blocked"] = not ok
    if not ok:
        _write_result("blocked")
        return {
            "status": "blocked",
            "reason": "blocked: host cannot launch a browser promptly",
            "host": host,
        }
    _write_result("running")
    return {"status": "ready", "host": host}


def finish_session(exitstatus: int) -> None:
    if "host" not in _STATE:
        return
    if _STATE.get("blocked"):
        status = "blocked"
    elif exitstatus:
        status = "fail"
    else:
        status = "pass"
    _write_result(status)
