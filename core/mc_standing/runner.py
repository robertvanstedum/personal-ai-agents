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
* Every (re)send checks kill, pause and the cycle's own date/cutoff; past the cutoff an unsent
  brief is ``held`` for operator disposition. Read-back may still confirm an earlier send.
* Delivery uses the platform ingress contract (harness v0.3.1 §14.1). The site ingress is
  idempotent by event id, so an unconfirmed send is reconciled by reading back and, only if the
  event is verifiably absent, re-posted with the same id. Nothing else is ever sent.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
import uuid
from datetime import date, datetime, time
from time import monotonic
from zoneinfo import ZoneInfo

from . import findings
from .observe import Observation, unknown
from .render import render_brief
from .sources import observe

ZONE = ZoneInfo("America/Chicago")
CATCH_UP_UNTIL = time(9, 0)
EVENT_SCHEMA = "mc-standing-event/1"
MAX_SOURCE_REFS = 64
CYCLE_BUDGET_S = 120          # collection + assessment; later sources become cycle_deadline unknowns


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


def _send_window_open(record: dict, local) -> bool:
    """A brief may only be (re)sent on its own local date, before the catch-up cutoff."""
    return local.date().isoformat() == record["local_date"] and local.time() < CATCH_UP_UNTIL


def _cycle_observations(spool, key) -> list[Observation]:
    prefix = f"{key}."
    return [_observation_from(spool.read("observations", n)) for n in spool.names("observations")
            if n.startswith(prefix)]


def _run_locked(spool, watchlist, policy_id, environment, now, local, key, source_root, ingress,
                destination, kill, paused, details_link, learning_counts, deps):
    record = spool.read("cycles", key) or {"cycle_key": key, "policy_id": policy_id,
                                           "local_date": local.date().isoformat(),
                                           "watchlist_sha256": watchlist.sha256, "state": "scheduled",
                                           "delayed": False}
    if record["state"] in ("delivered", "missed", "failed", "held"):
        return {"cycle_key": key, "state": record["state"], "reason": "already_final"}
    if record["state"] in ("delivery_pending", "delivery_unknown"):
        return _deliver(spool, record, ingress, kill, paused, local)
    if kill():
        return _save(spool, key, record, state="failed", reason="killed_before_start")
    if paused():
        return {"cycle_key": key, "state": "paused"}
    if record["state"] == "scheduled":
        if local.time() >= CATCH_UP_UNTIL:
            return _save(spool, key, record, state="missed", reason="started_after_catch_up_window")
        _save(spool, key, record, state="collecting", started_at=now.isoformat(),
              delayed=local.time() > time(6, 0))
    if watchlist.sha256 and watchlist.sha256 != record.get("watchlist_sha256"):
        # The revision actually used is recorded; collection continues under the current reviewed list.
        _save(spool, key, record, watchlist_changed_to=watchlist.sha256)

    if record["state"] == "collecting":
        started = monotonic()
        for seq, watch in enumerate(watchlist.watches):
            name = f"{key}.{watch.id}"
            stored = spool.read("observations", name)
            if stored is None:
                if kill():
                    return _save(spool, key, record, state="failed", reason="killed_during_collection")
                if monotonic() - started > CYCLE_BUDGET_S:
                    obs = unknown(key, watch, "cycle_deadline", now)
                else:
                    obs = observe(key, watch, now, source_root, environment=environment, **deps)
                spool.put_immutable("observations", name, obs.to_dict())
            else:
                obs = _observation_from(stored)
            findings.record(spool, obs, seq=seq)
        _save(spool, key, record, state="assessed")

    # Every later stage resumes from what is already durable; nothing written before is rebuilt
    # with new timestamps, so a restart between any two steps converges on the same artifacts.
    observations = _cycle_observations(spool, key)
    if not record.get("rendered_at"):
        _save(spool, key, record, rendered_at=now.isoformat())
    payload_name = f"{key}.brief"
    payload = spool.read("delivery", payload_name)
    if payload is None:
        today = date.fromisoformat(record["local_date"])
        overdue = [w.label for w in watchlist.watches if w.review_by < today]
        text = render_brief(observations, watchlist.watches, local_date=today, environment=environment,
                            details_link=details_link, learning_counts=learning_counts,
                            review_needed=overdue)
        if record.get("delayed"):
            text = text.replace(".\n", " (delayed).\n", 1)
        payload = {"cycle_key": key, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        spool.put_immutable("delivery", payload_name, payload)
    if spool.read("outbox", f"{key}.site") is None:
        refs = [{"id": o.observation_id, "digest": hashlib.sha256(repr(o.to_dict()).encode()).hexdigest()}
                for o in observations][:MAX_SOURCE_REFS]
        event = {"schema": EVENT_SCHEMA, "event_id": _event_id(key, destination), "cycle_key": key,
                 "policy_hash": record.get("watchlist_sha256") or hashlib.sha256(policy_id.encode()).hexdigest(),
                 "environment": environment, "kind": "brief", "occurred_at": record["rendered_at"],
                 "source_refs": refs, "finding_key": None, "payload_ref": payload_name,
                 "payload_digest": payload["sha256"], "destination_binding": destination}
        spool.put_immutable("outbox", f"{key}.site", event)
    _save(spool, key, record, state="delivery_pending")
    return _deliver(spool, record, ingress, kill, paused, local)


def _deliver(spool, record, ingress, kill, paused, local):
    """Read-back may always confirm an earlier send; a (re)send needs: not killed, not paused,
    the cycle's own local date and before the catch-up cutoff. Past the cutoff an unsent brief is
    held for operator disposition, never sent late."""
    key = record["cycle_key"]
    event = spool.read("outbox", f"{key}.site")
    if event is None:
        return _save(spool, key, record, state="failed", reason="outbox_missing")
    if record["state"] == "delivery_unknown":
        try:
            ack = ingress.get(event["event_id"])
        except Exception:
            return {"cycle_key": key, "state": "delivery_unknown", "reason": "readback_unavailable"}
        if ack and ack.get("state") in ("accepted", "already_applied"):
            return _save(spool, key, record, state="delivered", ack=ack)
        if ack:   # a response we can't treat as delivered; keep the hold, never resend blindly
            return _save(spool, key, record, state="delivery_unknown", ack=ack)
        # verifiably absent: the same idempotent event may be posted again, if sending is allowed
    if kill():
        return {"cycle_key": key, "state": record["state"], "reason": "killed_before_delivery"}
    if paused():
        return {"cycle_key": key, "state": record["state"], "reason": "paused_before_delivery"}
    if not _send_window_open(record, local):
        return _save(spool, key, record, state="held", reason="send_window_closed")
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
