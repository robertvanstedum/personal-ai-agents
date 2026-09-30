"""The workshop's host observer (4a): model-free, read-only.

Observes, never configures: memory pressure, swap, disk headroom, load, and
the agent clients actually running on this Mac, outside Docker (Claude Code,
Codex and OpenClaw processes, started by anyone). The staging containers'
agents run inside the Colima VM and are invisible to the Mac's `ps`, so the
screen says "on this Mac (outside Docker)". It gives one admission verdict
for a new run:

* ok       room for one launcher-managed run;
* tight    a run could start but would share the host (an agent session is
           running, or a resource is near its limit);
* blocked  a new run must wait, with the reason;
* unknown  a probe failed: never "ok", never "nothing running".

Sessions, not processes: a client whose ancestor is the same kind is part of
that session (Claude's desktop `disclaimer` wrapper is not a client, so its
child is counted once). Background agent apps (the ChatGPT app's bundled
Codex, an OpenClaw gateway) are reported and labelled, but are not build
sessions and do not count toward the verdict.

`ps` is read with the executable path LAST (`pid,ppid,etime,comm`), so macOS
never truncates it; arguments are read only for `node` processes, to find the
script, and are never recorded: only kind, label, pid, parent and age are kept.

Spec 158 section 7: one launcher-managed local run per host by default; stale
or unknown health prevents a launch with a visible reason. The launcher
itself is 4b; this only observes.
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
    "agent_sessions": {"tight": 1, "blocked": 3},           # at or above
}

SESSION_KINDS = ("claude-code", "codex")                    # count toward the verdict
LABELS = {"claude-code": "Claude Code", "codex": "Codex CLI", "codex-desktop": "Codex desktop (ChatGPT app)",
          "openclaw": "OpenClaw (background)"}


def limits_text(t: dict = THRESHOLDS) -> str:
    """The four rules, once, so every verdict can be checked against them."""
    return (f"Limits: memory free tight under {t['memory_free_pct']['tight']}%, blocked under "
            f"{t['memory_free_pct']['blocked']}% · swap used tight over {t['swap_used_gb']['tight']:g} GB, blocked over "
            f"{t['swap_used_gb']['blocked']:g} GB · disk free tight under {t['disk_free_gb']['tight']:g} GB, blocked under "
            f"{t['disk_free_gb']['blocked']:g} GB · agent sessions tight at {t['agent_sessions']['tight']}, blocked at "
            f"{t['agent_sessions']['blocked']}")


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


def _script_kind(script: str) -> str | None:
    """The kind of a node script (argv[1]), from its path only."""
    base = os.path.basename(script)
    if base in ("claude", "codex", "openclaw"):
        return "claude-code" if base == "claude" else base
    if "/@anthropic-ai/claude-code/" in script:
        return "claude-code"
    if "/@openai/codex/" in script:
        return "codex"
    if "/openclaw/" in script:
        return "openclaw"
    return None


def classify(comm: str, script: str | None = None) -> str | None:
    """The kind of agent client an executable is, or None. comm is the full
    executable path (or the name it was started as); script is a node
    process's argv[1]. Desktop apps and their helpers are not clients; the
    ChatGPT app's bundled Codex is its own, background kind."""
    path = comm.strip()
    name = os.path.basename(path)
    if name == "claude" or "/claude/versions/" in path:
        return "claude-code"
    if name == "codex":
        return "codex-desktop" if ".app/Contents/" in path else "codex"
    if name == "openclaw":
        return "openclaw"
    if name in ("node", "node.exe") and script:
        return _script_kind(script)
    return None


def parse_ps(out: str) -> list[dict]:
    """Rows of `ps -axo pid=,ppid=,etime=,comm=` (comm last: the full path)."""
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit():
            rows.append({"pid": int(parts[0]), "ppid": int(parts[1]), "elapsed": parts[2], "comm": parts[3]})
    return rows


def _node_scripts(pids: list[int], run) -> dict[int, str]:
    """argv[1] of each node process, in memory only (never recorded)."""
    if not pids:
        return {}
    out = run(["ps", "-o", "pid=,args=", "-p", ",".join(str(p) for p in pids)])
    scripts = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) >= 3 and parts[0].isdigit():
            scripts[int(parts[0])] = parts[2]
    return scripts


def agent_clients(run=_run) -> list[dict]:
    """Agent sessions and background agent apps on this Mac (outside Docker)."""
    rows = parse_ps(run(["ps", "-axo", "pid=,ppid=,etime=,comm="]))
    me = os.getpid()
    nodes = [r["pid"] for r in rows if os.path.basename(r["comm"]) in ("node", "node.exe")]
    scripts = _node_scripts(nodes, run)
    kinds = {r["pid"]: classify(r["comm"], scripts.get(r["pid"])) for r in rows}
    parent = {r["pid"]: r["ppid"] for r in rows}

    def inside_same_kind(pid: int, kind: str) -> bool:
        seen, p = set(), parent.get(pid)
        while p and p not in seen and p != 1:
            if kinds.get(p) == kind:
                return True
            seen.add(p)
            p = parent.get(p)
        return False

    found = []
    for r in rows:
        kind = kinds[r["pid"]]
        if not kind or r["pid"] == me or inside_same_kind(r["pid"], kind):
            continue
        found.append({"kind": kind, "label": LABELS[kind], "pid": r["pid"], "elapsed": r["elapsed"],
                      "counted": kind in SESSION_KINDS})
    return found


def observe(*, run=_run, home: str | None = None, disk_usage=shutil.disk_usage) -> Observation:
    obs = Observation(observed_at=iso(now()))
    for name, probe in (("memory_free_pct", lambda: memory_free_pct(run)), ("swap_used_gb", lambda: swap_used_gb(run))):
        try:
            setattr(obs, name, probe())
        except Exception as exc:                        # unknown, never zero
            obs.errors.append(f"{name}: {type(exc).__name__}")
    try:
        obs.disk_free_gb = round(disk_usage(home or os.path.expanduser("~")).free / 1024 ** 3, 1)
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
        unknown.append("running agent sessions unknown")
    else:
        sessions = [c for c in obs.clients if c.get("counted")]
        n = len(sessions)
        t = thresholds["agent_sessions"]
        kinds = ", ".join(sorted({c["kind"] for c in sessions}))
        plural = "s" if n != 1 else ""
        if n >= t["blocked"]:
            blocked.append(f"{n} agent session{plural} running ({kinds}; blocked at {t['blocked']})")
        elif n >= t["tight"]:
            tight.append(f"{n} agent session{plural} running ({kinds}; tight at {t['tight']}): a new run would share the host")
    if blocked:
        return {"verdict": "blocked", "reasons": blocked + unknown}
    if unknown:
        return {"verdict": "unknown", "reasons": unknown + tight}
    if tight:
        return {"verdict": "tight", "reasons": tight}
    m, s, d = (thresholds[k] for k in ("memory_free_pct", "swap_used_gb", "disk_free_gb"))
    return {"verdict": "ok", "reasons": [f"room for one run (memory free {m['tight']}% or more, swap {s['tight']:g} GB or "
                                         f"less, disk free {d['tight']:g} GB or more, no agent session running)"]}


def health_event(obs: Observation, workshop_id: str, *, hostname: str = "mac") -> dict:
    """The workshop event for one observation (the host's health)."""
    v = verdict(obs)
    counts: dict[str, int] = {}
    for c in obs.clients:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    health = {"observed_at": obs.observed_at, "verdict": v["verdict"], "reasons": v["reasons"][:6],
              "scope": "this Mac (outside Docker)",
              "memory_free_pct": obs.memory_free_pct, "swap_used_gb": obs.swap_used_gb,
              "disk_free_gb": obs.disk_free_gb, "load_1m": obs.load_1m,
              "clients_known": obs.clients_known, "clients": counts,
              "sessions": sum(1 for c in obs.clients if c.get("counted")),
              "runs": [{"kind": c["kind"], "pid": c["pid"], "elapsed": c["elapsed"], "counted": bool(c.get("counted"))}
                       for c in obs.clients[:12]],          # the list is capped; the counts are complete
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


__all__ = ["observe", "verdict", "health_event", "changed", "classify", "parse_ps", "agent_clients",
           "limits_text", "Observation", "THRESHOLDS", "LABELS", "SESSION_KINDS"]
