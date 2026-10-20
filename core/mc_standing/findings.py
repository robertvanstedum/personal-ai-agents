"""Immutable finding events (``mc-finding/1``, standing spec §5.1).

A finding is a stream of events under a stable key (environment/host/watch/reason family), so the
same problem seen every morning is one incident, not a new task each day. Events:
  opened         first failing/degraded observation for the key
  observed       still failing on a later cycle
  recovered      a later observation for the watch is healthy (fresh evidence, never silence)
  coverage_lost  the watch could not be checked while the finding was open
Absence of evidence never closes a finding. A recovery does not claim any repair was performed.
"""
from __future__ import annotations

import hashlib
import json

from .spool import Spool

SCHEMA = "mc-finding/1"
OPEN_STATES = ("failed", "degraded")


def family(reason: str) -> str:
    """Reason family for the stable key: e.g. job_missed and job_late are one 'job' problem."""
    return reason.split("_", 1)[0] if reason else "unknown"


def finding_key(obs) -> str:
    return f"{obs.environment}.{obs.host}.{obs.watch_id}.{family(obs.reason)}"


def _event_id(key: str, cycle_key: str, kind: str) -> str:
    return hashlib.sha256(json.dumps([key, cycle_key, kind]).encode()).hexdigest()[:32]


def _open_keys_for_watch(spool: Spool, watch_id: str) -> dict:
    """Latest event per finding key for this watch (only keys whose stream is still open)."""
    latest = {}
    for name in spool.names("findings"):
        ev = spool.read("findings", name)
        if not ev or ev.get("watch_id") != watch_id:
            continue
        key = ev["finding_key"]
        if key not in latest or (ev["occurred_at"], ev["seq"]) > (latest[key]["occurred_at"], latest[key]["seq"]):
            latest[key] = ev
    return {k: v for k, v in latest.items() if v["event"] != "recovered"}


def record(spool: Spool, obs, seq: int = 0) -> list[dict]:
    """Derive this cycle's finding events from one observation and store them (idempotent per
    cycle: rerunning the same cycle writes nothing new)."""
    already = [ev for ev in (spool.read("findings", n) for n in spool.names("findings"))
               if ev and ev.get("watch_id") == obs.watch_id and ev.get("cycle_key") == obs.cycle_key]
    if already:
        return already
    open_streams = _open_keys_for_watch(spool, obs.watch_id)
    events = []

    def emit(key, kind, severity):
        ev = {"schema": SCHEMA, "event_id": _event_id(key, obs.cycle_key, kind), "finding_key": key,
              "watch_id": obs.watch_id, "cycle_key": obs.cycle_key, "event": kind, "severity": severity,
              "reason": obs.reason, "observation_id": obs.observation_id, "occurred_at": obs.collected_at,
              "seq": seq, "proposed_next_step_code": obs.reason}
        spool.put_immutable("findings", ev["event_id"], ev)
        events.append(ev)

    if obs.state in OPEN_STATES:
        key = finding_key(obs)
        emit(key, "observed" if key in open_streams else "opened", obs.state)
    elif obs.state == "healthy" and obs.coverage == "complete":
        for key in open_streams:
            emit(key, "recovered", "healthy")
    elif obs.state == "unknown":
        for key, last in open_streams.items():
            if last["event"] != "coverage_lost" or last["cycle_key"] != obs.cycle_key:
                emit(key, "coverage_lost", last["severity"])
    return events
