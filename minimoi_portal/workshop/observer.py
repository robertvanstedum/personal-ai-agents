"""The workshop's host observer (4a): model-free, read-only.

Observes, never configures: memory pressure, swap, disk headroom, load, and
the agent clients actually running on the host (Claude Code, Codex, OpenClaw
processes, started by anyone). It gives one admission verdict for a new run:

* ok       room for one launcher-managed run;
* tight    a run could start but would share the host (another agent client
           is running, or a resource is near its limit);
* blocked  a new run must wait, with the reason;
* unknown  a probe failed: never "ok", never "nothing running".

Spec 158 §7: one launcher-managed local run per host by default; stale or
unknown health prevents a launch with a visible reason. The launcher itself
is 4b; this only observes. Process arguments are read to classify, and never
recorded (they can hold paths or tokens): only kind, pid and age are kept.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from .record import iso, now

THRESHOLDS = {
    "memory_free_pct": {"tight": 20, "blocked": 10},       # below
    "swap_used_gb": {"tight": 3.0, "blocked": 6.0},         # above
    "disk_free_gb": {"tight": 20.0, "blocked": 10.0},       # below
    "agent_clients": {"tight": 1, "blocked": 3},            # at or above
}


def _run(cmd: list[str], timeout: float = 5.0) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True).stdout


@dataclass
class Observation:
    observed_at: str
    memory_free_pct: float | None = None
    swap_used_gb: float | None = None
    disk_free_gb: float | None = None
    load_1m: float | None = None
    clients: list[dict] = field(default_factory=list)
    clients_known: bool = False
    errors: list[str] = field(default_factory=list)


def memory_free_pct(run=_run) -> float:
    out = run(["memory_pressure", "-Q"])
    m = re.search(r"free percentage:\s*(\d+(?:\.\d+)?)%", out)
    if not m:
        raise ValueError("memory_pressure: no free percentage")
    return float(m.group(1))


def swap_used_gb(run=_run) -> float:
    out = run(["sysctl", "-n", "vm.swapusage"])
    m = re.search(r"used\s*=\s*([\d.]+)([MG])", out)
    if not m:
        raise ValueError("vm.swapusage: no used value")
    value = float(m.group(1))
    return round(value / 1024 if m.group(2) == "M" else value, 2)


def classify(comm: str, args: str) -> str | None:
    """The kind of agent client a process is, or None. Helpers of the desktop
    apps are not clients; the apps' own CLIs are."""
    name = os.path.basename(comm.strip())
    if name == "claude":
        return "claude-code"
    if name == "codex":
        return "codex"
    if name == "openclaw" or (name in ("node", "node.exe") and "openclaw" in args):
        return "openclaw"
    return None


def agent_clients(run=_run) -> list[dict]:
    out = run(["ps", "-axo", "pid=,etime=,comm=,args="])
    me = os.getpid()
    found = []
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, etime, rest = parts
        # comm (a path that may contain spaces) and args are both in `rest`;
        # the executable path is the first token that is a path to a file.
        comm = rest.split(" ", 1)[0] if not rest.startswith("/") else _comm_path(rest)
        kind = classify(comm, rest)
        if kind and pid.isdigit() and int(pid) != me:
            found.append({"kind": kind, "pid": int(pid), "elapsed": etime})
    return found


def _comm_path(rest: str) -> str:
    """The executable path at the start of `rest` (paths may contain spaces;
    a macOS executable path ends at /MacOS/<name> or at the first space after
    the last /)."""
    m = re.match(r"(/.*?/MacOS/[^ /]+|/\S+)", rest)
    return m.group(1) if m else rest.split(" ", 1)[0]


def observe(*, run=_run, home: str | None = None) -> Observation:
    obs = Observation(observed_at=iso(now()))
    for name, probe in (("memory_free_pct", lambda: memory_free_pct(run)), ("swap_used_gb", lambda: swap_used_gb(run))):
        try:
            setattr(obs, name, probe())
        except Exception as exc:                        # unknown, never zero
            obs.errors.append(f"{name}: {type(exc).__name__}")
    try:
        obs.disk_free_gb = round(shutil.disk_usage(home or os.path.expanduser("~")).free / 1024 ** 3, 1)
    except OSError as exc:
        obs.errors.append(f"disk_free_gb: {type(exc).__name__}")
    try:
        obs.load_1m = round(os.getloadavg()[0], 2)
    except OSError:
        obs.errors.append("load_1m: unavailable")
    try:
        obs.clients = agent_clients(run)
        obs.clients_known = True
    except Exception as exc:
        obs.errors.append(f"agent_clients: {type(exc).__name__}")
    return obs


def verdict(obs: Observation, thresholds: dict = THRESHOLDS) -> dict:
    """{"verdict": ok|tight|blocked|unknown, "reasons": [...]} for one new run."""
    blocked, tight, unknown = [], [], []

    def below(value, key, label, unit):
        t = thresholds[key]
        if value is None:
            unknown.append(f"{label} unknown")
        elif value < t["blocked"]:
            blocked.append(f"{label} {value}{unit} (under {t['blocked']}{unit})")
        elif value < t["tight"]:
            tight.append(f"{label} {value}{unit} (under {t['tight']}{unit})")

    def above(value, key, label, unit):
        t = thresholds[key]
        if value is None:
            unknown.append(f"{label} unknown")
        elif value > t["blocked"]:
            blocked.append(f"{label} {value}{unit} (over {t['blocked']}{unit})")
        elif value > t["tight"]:
            tight.append(f"{label} {value}{unit} (over {t['tight']}{unit})")

    below(obs.memory_free_pct, "memory_free_pct", "memory free", "%")
    above(obs.swap_used_gb, "swap_used_gb", "swap used", " GB")
    below(obs.disk_free_gb, "disk_free_gb", "disk free", " GB")
    if not obs.clients_known:
        unknown.append("running agent clients unknown")
    else:
        n = len(obs.clients)
        t = thresholds["agent_clients"]
        kinds = ", ".join(sorted({c["kind"] for c in obs.clients}))
        if n >= t["blocked"]:
            blocked.append(f"{n} agent clients running ({kinds})")
        elif n >= t["tight"]:
            tight.append(f"{n} agent client{'s' if n != 1 else ''} running ({kinds}): a new run would share the host")
    if blocked:
        return {"verdict": "blocked", "reasons": blocked + unknown}
    if unknown:
        return {"verdict": "unknown", "reasons": unknown + tight}
    if tight:
        return {"verdict": "tight", "reasons": tight}
    return {"verdict": "ok", "reasons": ["room for one run"]}


def health_event(obs: Observation, workshop_id: str, *, hostname: str = "mac") -> dict:
    """The workshop event for one observation (the host's health)."""
    v = verdict(obs)
    counts: dict[str, int] = {}
    for c in obs.clients:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    health = {"observed_at": obs.observed_at, "verdict": v["verdict"], "reasons": v["reasons"][:6],
              "memory_free_pct": obs.memory_free_pct, "swap_used_gb": obs.swap_used_gb,
              "disk_free_gb": obs.disk_free_gb, "load_1m": obs.load_1m,
              "clients_known": obs.clients_known, "clients": counts,
              "runs": [{"kind": c["kind"], "pid": c["pid"], "elapsed": c["elapsed"]} for c in obs.clients[:12]],
              "errors": obs.errors[:6]}
    text = f"Host {v['verdict']}: {v['reasons'][0]}" if v["reasons"] else f"Host {v['verdict']}"
    return {"actor": "host", "kind": "health", "item": f"host:{hostname}", "text": text[:280], "health": health}


def changed(previous: dict | None, event: dict) -> bool:
    """Write a health event only when something that matters changed."""
    if not previous:
        return True
    a, b = previous, event["health"]
    keys = ("verdict", "clients_known", "clients")
    return any(a.get(k) != b.get(k) for k in keys) or tuple(a.get("reasons") or ()) != tuple(b.get("reasons") or ())


__all__ = ["observe", "verdict", "health_event", "changed", "classify", "Observation", "THRESHOLDS"]
