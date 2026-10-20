"""One morning cycle (standing spec §§3, 5.2, 7): collect → assess → render → deliver.

* One run at a time per environment: an fcntl lock on the spool root (single host only; a second
  host would need a shared lease before it is enabled).
* Stable cycle key ``<env>.<local date>.<policy id>.morning`` (America/Chicago calendar). A
  delivered cycle is never sent again; a policy revision does not create a second brief that day.
* Crash-safe resume from the durable stage: stored observations are reused, never re-collected
  with a new timestamp, and delivery reuses the same event id.
* Late start: one catch-up until 09:00 local, labelled delayed; after that the day is recorded as
  missed and nothing is sent (no yesterday's brief as today's, no backlog flood).
* Kill flag checked before every source read and before delivery; owner pause skips the cycle.
* Delivery uses the platform ingress contract (harness v0.3.1 §14.1). The site ingress is
  idempotent by event id, so an unconfirmed send is reconciled by reading back and, only if the
  event is verifiably absent, re-posted with the same id. Nothing else is ever sent.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import uuid
from datetime import datetime, time
from zoneinfo import ZoneInfo

from . import findings
from .observe import Observation
from .render import render_brief
from .sources import observe

ZONE = ZoneInfo("America/Chicago")
CATCH_UP_UNTIL = time(9, 0)
EVENT_SCHEMA = "mc-standing-event/1"
MAX_SOURCE_REFS = 64


class IngressUncertain(Exception):
    """The send may or may not have been committed (timeout after send, connection reset)."""


def cycle_key(environment: str, local_date, policy_id: str) -> str:
    return f"{environment}.{local_date.isoformat()}.{policy_id}.morning"


def _event_id(key: str, destination: str) -> str:
    return str(uuid.UUID(hashlib.sha256(f"{key}|site|{destination}".encode()).hexdigest()[:32]))


def _observation_from(doc: dict) -> Observation:
    fields = {k: doc[k] for k in ("cycle_key", "watch_id", "watch_version", "environment", "host",
                                  "state", "reason", "coverage", "collected_at", "observed_at",
                                  "measurements", "collector_version")}
    return Observation(evidence=tuple(tuple(e) for e in doc.get("evidence", [])), **fields)


def run_cycle(*, spool, watchlist, policy_id: str, environment: str, now: datetime, source_root,
              ingress, destination_binding: str, kill=lambda: False, paused=lambda: False,
              details_link: str | None = None, learning_counts=None, deps: dict | None = None) -> dict:
    deps = deps or {}
    local = now.astimezone(ZONE)
    key = cycle_key(environment, local.date(), policy_id)
    lock_fd = os.open(spool.root / ".cycle.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"cycle_key": key, "state": "skipped", "reason": "overlap"}
        return _run_locked(spool, watchlist, policy_id, environment, now, local, key, source_root,
                           ingress, destination_binding, kill, paused, details_link, learning_counts, deps)
    finally:
        os.close(lock_fd)


def _save(spool, key, record, **changes):
    record.update(changes)
    spool.put_state("cycles", key, record)
    return record


def _run_locked(spool, watchlist, policy_id, environment, now, local, key, source_root, ingress,
                destination, kill, paused, details_link, learning_counts, deps):
    record = spool.read("cycles", key) or {"cycle_key": key, "policy_id": policy_id,
                                           "watchlist_sha256": watchlist.sha256, "state": "scheduled",
                                           "delayed": False}
    if record["state"] in ("delivered", "missed", "failed"):
        return {"cycle_key": key, "state": record["state"], "reason": "already_final"}
    if record["state"] in ("delivery_pending", "delivery_unknown"):
        return _deliver(spool, record, ingress, destination, kill)
    if kill():
        return _save(spool, key, record, state="failed", reason="killed_before_start")
    if paused():
        return {"cycle_key": key, "state": "paused"}
    if record["state"] == "scheduled":
        if local.time() >= CATCH_UP_UNTIL:
            return _save(spool, key, record, state="missed", reason="started_after_catch_up_window")
        _save(spool, key, record, state="collecting", started_at=now.isoformat(),
              delayed=local.time() > time(6, 0))

    observations = []
    for seq, watch in enumerate(watchlist.watches):
        name = f"{key}.{watch.id}"
        stored = spool.read("observations", name)
        if stored is not None:
            obs = _observation_from(stored)
        else:
            if kill():
                return _save(spool, key, record, state="failed", reason="killed_during_collection")
            obs = observe(key, watch, now, source_root, **deps)
            spool.put_immutable("observations", name, obs.to_dict())
        findings.record(spool, obs, seq=seq)
        observations.append(obs)
    _save(spool, key, record, state="assessed")

    text = render_brief(observations, watchlist.watches, local_date=local.date(), environment=environment,
                        details_link=details_link, learning_counts=learning_counts)
    if record.get("delayed"):
        text = text.replace(".\n", " (delayed).\n", 1)
    digest = hashlib.sha256(text.encode()).hexdigest()
    spool.put_immutable("delivery", f"{key}.brief", {"cycle_key": key, "text": text, "sha256": digest})
    refs = [{"id": o.observation_id, "digest": hashlib.sha256(repr(o.to_dict()).encode()).hexdigest()}
            for o in observations][:MAX_SOURCE_REFS]
    event = {"schema": EVENT_SCHEMA, "event_id": _event_id(key, destination), "cycle_key": key,
             "policy_hash": watchlist.sha256 or hashlib.sha256(policy_id.encode()).hexdigest(),
             "environment": environment, "kind": "brief", "occurred_at": now.isoformat(),
             "source_refs": refs, "finding_key": None, "payload_ref": f"{key}.brief",
             "payload_digest": digest, "destination_binding": destination}
    spool.put_immutable("outbox", f"{key}.site", event)
    _save(spool, key, record, state="delivery_pending", rendered_at=now.isoformat())
    return _deliver(spool, record, ingress, destination, kill)


def _deliver(spool, record, ingress, destination, kill):
    key = record["cycle_key"]
    event = spool.read("outbox", f"{key}.site")
    if event is None:
        return _save(spool, key, record, state="failed", reason="outbox_missing")
    if kill():
        return {"cycle_key": key, "state": record["state"], "reason": "killed_before_delivery"}
    if record["state"] == "delivery_unknown":
        try:
            ack = ingress.get(event["event_id"])
        except Exception:
            return {"cycle_key": key, "state": "delivery_unknown", "reason": "readback_unavailable"}
        if ack and ack.get("state") in ("accepted", "already_applied"):
            return _save(spool, key, record, state="delivered", ack=ack)
        if ack:   # a response we can't treat as delivered; keep the hold, never resend blindly
            return _save(spool, key, record, state="delivery_unknown", ack=ack)
        # verifiably absent: the same idempotent event may be posted again
    try:
        ack = ingress.post(event)
    except IngressUncertain:
        return _save(spool, key, record, state="delivery_unknown", reason="send_unconfirmed")
    except Exception:
        return _save(spool, key, record, state="delivery_pending", reason="send_failed_before_commit")
    state = ack.get("state")
    if state in ("accepted", "already_applied"):
        return _save(spool, key, record, state="delivered", ack=ack)
    if state == "deferred":
        return _save(spool, key, record, state="delivery_pending", ack=ack)
    return _save(spool, key, record, state="failed", reason="ingress_refused", ack=ack)
