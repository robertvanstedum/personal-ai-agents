"""host.health: what the plugin measured on the runtime host, checked and put into words. Read-only.

The plugin measures (it runs on the host; the portal's container cannot see the host's disk). The
observation is trusted plugin output, never model text, and is still checked field by field.
"""
from __future__ import annotations

import re

from .catalogue import ArgumentsRefused

_MOUNT = re.compile(r"/[A-Za-z0-9._/ -]{0,120}")
_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_STATUS = re.compile(r"[A-Za-z0-9 ()_.:,-]{1,80}")
_ERROR = re.compile(r"[a-z][a-z0-9_]{0,39}")


def _num(value, high):
    if type(value) not in (int, float) or value != value or not 0 <= value <= high:
        raise ArgumentsRefused("host_observation_invalid")
    return float(value)


def check(observation) -> dict:
    """The observation in canonical form, or ArgumentsRefused."""
    if type(observation) is not dict or set(observation) != {"host", "disks", "memory", "containers"}:
        raise ArgumentsRefused("host_observation_invalid")
    if observation["host"] not in ("mac", "linux"):
        raise ArgumentsRefused("host_observation_invalid")
    disks = observation["disks"]
    if type(disks) is not list or not 1 <= len(disks) <= 4:
        raise ArgumentsRefused("host_observation_invalid")
    out_disks = []
    for d in disks:
        if type(d) is not dict or set(d) != {"mount", "total_gb", "free_gb"} or type(d["mount"]) is not str \
                or not _MOUNT.fullmatch(d["mount"]):
            raise ArgumentsRefused("host_observation_invalid")
        total, free = _num(d["total_gb"], 1e6), _num(d["free_gb"], 1e6)
        if free > total or total == 0:
            raise ArgumentsRefused("host_observation_invalid")
        out_disks.append({"mount": d["mount"], "total_gb": round(total, 1), "free_gb": round(free, 1),
                          "used_pct": round(100 * (total - free) / total, 1)})
    mem = observation["memory"]
    if type(mem) is not dict or set(mem) != {"total_gb", "free_gb"}:
        raise ArgumentsRefused("host_observation_invalid")
    mem_total, mem_free = _num(mem["total_gb"], 1e5), _num(mem["free_gb"], 1e5)
    if mem_free > mem_total:
        raise ArgumentsRefused("host_observation_invalid")
    containers = observation["containers"]
    if type(containers) is dict:
        if set(containers) != {"error"} or type(containers["error"]) is not str or not _ERROR.fullmatch(containers["error"]):
            raise ArgumentsRefused("host_observation_invalid")
        out_containers = {"error": containers["error"]}
    elif type(containers) is list and len(containers) <= 60:
        out_containers = []
        for c in containers:
            if (type(c) is not dict or set(c) != {"name", "status"} or type(c["name"]) is not str
                    or type(c["status"]) is not str or not _CONTAINER.fullmatch(c["name"])
                    or not _STATUS.fullmatch(c["status"])):
                raise ArgumentsRefused("host_observation_invalid")
            out_containers.append({"name": c["name"], "status": c["status"]})
    else:
        raise ArgumentsRefused("host_observation_invalid")
    return {"host": observation["host"], "disks": out_disks,
            "memory": {"total_gb": round(mem_total, 1), "free_gb": round(mem_free, 1)}, "containers": out_containers}


def words(o: dict) -> str:
    where = "this Mac" if o["host"] == "mac" else "the server"
    lines = [f"Measured just now on {where}:"]
    for d in o["disks"]:
        lines.append(f"- Disk {d['mount']}: {d['free_gb']} GB free of {d['total_gb']} GB ({d['used_pct']}% used)")
    mem = f"- Memory: {o['memory']['free_gb']} GB free of {o['memory']['total_gb']} GB"
    if o["host"] == "mac":
        mem += " (macOS fills spare memory with cache, so low free memory alone is normal)"
    lines.append(mem)
    c = o["containers"]
    if isinstance(c, dict):
        lines.append(f"- Containers: could not be read ({c['error']})")
    else:
        up = [x for x in c if x["status"].lower().startswith("up")]
        lines.append(f"- Containers: {len(up)} running, {len(c) - len(up)} stopped" + (":" if up else ""))
        lines.extend(f"  - {x['name']}: {x['status']}" for x in up)
    lines.append("Scope: this is a current filesystem and Docker-status snapshot, not an application regression "
                 "test or error history. Uptime does not prove error-free operation or identify a deployment. "
                 "Only report a container as healthy when its status explicitly says healthy. Stopped containers "
                 "are not necessarily disposable; reclaimable space and cleanup eligibility were not measured. "
                 "Keep each disk measurement tied to its mount; Docker cleanup may affect a different filesystem.")
    return "\n".join(lines)
