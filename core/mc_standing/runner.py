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
import resource
import sys
import uuid
from datetime import date, datetime, time, timezone
from time import monotonic
from zoneinfo import ZoneInfo

from . import ack as ack_mod, findings, learning_view, telegram_copy
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
              details_link: str | None = None, learning_source=None, learning_principal: str | None = None,
              telegram=None, envelope: dict | None = None, telemetry=None, deps: dict | None = None,
              clock=None) -> dict:
    """``now`` fixes the cycle's date and the observation timestamps; ``clock`` (default: the
    fixed ``now``) is read again immediately before every send and between stages, so a cycle that
    starts at 08:59 cannot deliver after 09:00. Production passes ``clock=lambda: datetime.now(UTC)``.

    ``learning_source`` (P-G7, optional) returns the harness's learning count snapshot; it is read
    once when the brief is first rendered, and any fault there only makes the learning line say
    "unavailable".

    ``telegram`` (optional, explicitly enabled): ``{"sender", "destination_id"}``. The counts-only
    copy goes out at most once per cycle, only after the site brief is verified delivered; the
    result then names each channel (``channels: {site, telegram}``).

    Every invocation records its resource use on the cycle record (``resources``: wall and CPU
    milliseconds, peak RSS; the last ten invocations) as non-interference evidence (P-07). With an
    ``envelope`` (``max_wall_ms``, ``max_cpu_ms``, ``max_rss_kb``) any excess is named in the result
    (``envelope_exceeded``); it never changes what was delivered."""
    deps = deps or {}
    clock = clock or (lambda: now)
    local = now.astimezone(ZONE)
    key = cycle_key(environment, local.date(), policy_id)
    lock_fd = os.open(spool.root / ".cycle.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"cycle_key": key, "state": "skipped", "reason": "overlap"}
        # Explicit in-flight status: while this is "running", only this worker acknowledges the
        # owner's control document (at its checkpoints); the idle ack path stays out.
        spool.put_state("cycles", "worker-status", {"state": "running", "cycle_key": key,
                                                    "since": clock().isoformat()})
        started, usage0 = monotonic(), resource.getrusage(resource.RUSAGE_SELF)
        try:
            result = _run_locked(spool, watchlist, policy_id, environment, now, local, key, source_root,
                                 ingress, destination_binding, kill, paused, details_link,
                                 (learning_source, learning_principal),
                                 deps, clock)
            if telegram is not None:
                result = _telegram_copy(spool, key, environment, details_link, telegram, kill, paused,
                                        clock, result)
            if telemetry is not None:
                _emit_telemetry(telemetry, spool, key, clock)
            return _record_resources(spool, key, result, started, usage0, envelope, clock)
        finally:
            try:   # informational; the lock (not this file) guards acknowledgement, so never mask the result
                spool.put_state("cycles", "worker-status", {"state": "idle", "cycle_key": key,
                                                            "since": clock().isoformat()})
            except OSError:
                pass
    finally:
        os.close(lock_fd)


# An observation is "source unreadable" when its adapter could not read a fresh, valid source:
# every such reason code starts with source_ or sample_ (core/mc_standing/sources.py).
_UNREADABLE_PREFIXES = ("source_", "sample_")


def _emit_telemetry(telemetry, spool, key, clock):
    """Facts only (spec 161 §5.2). Any failure is swallowed: monitoring never alters the brief."""
    try:
        now_s = clock().timestamp()
        for obs in _cycle_observations(spool, key):
            readable = 0 if obs.state == "unknown" and obs.reason.startswith(_UNREADABLE_PREFIXES) else 1
            try:
                telemetry.gauge("minimoi_check_source_readable", readable, check_id=obs.watch_id)
                telemetry.gauge("minimoi_check_last_run_timestamp_seconds", now_s, check_id=obs.watch_id)
                m = obs.measurements or {}
                if obs.watch_id == "guests" and obs.state != "unknown":
                    if isinstance(m.get("pending_total"), int):
                        telemetry.gauge("minimoi_guest_requests_pending", m["pending_total"])
                    if isinstance(m.get("oldest_age_h"), (int, float)):
                        telemetry.gauge("minimoi_guest_requests_oldest_age_seconds", float(m["oldest_age_h"]) * 3600)
            except Exception:
                continue
    except Exception:
        pass


def _rss_kb(usage) -> int:
    # ru_maxrss is bytes on macOS and kilobytes on Linux.
    return int(usage.ru_maxrss / 1024) if sys.platform == "darwin" else int(usage.ru_maxrss)


def _record_resources(spool, key, result, started, usage0, envelope, clock):
    usage = resource.getrusage(resource.RUSAGE_SELF)
    measured = {"at": clock().isoformat(), "state": result.get("state"),
                "wall_ms": int((monotonic() - started) * 1000),
                "cpu_ms": int(((usage.ru_utime - usage0.ru_utime) + (usage.ru_stime - usage0.ru_stime)) * 1000),
                "max_rss_kb": _rss_kb(usage)}
    try:   # evidence only: never let measurement change the outcome
        record = spool.read("cycles", key)
        if record is not None:
            record["resources"] = (record.get("resources") or [])[-9:] + [measured]
            spool.put_state("cycles", key, record)
    except (OSError, ValueError):
        pass
    result = dict(result, resources=measured)
    if envelope:
        limits = {"max_wall_ms": "wall_ms", "max_cpu_ms": "cpu_ms", "max_rss_kb": "max_rss_kb"}
        over = sorted(k for k, m in limits.items() if k in envelope and measured[m] > envelope[k])
        if over:
            result["envelope_exceeded"] = over
    return result


def _telegram_copy(spool, key, environment, details_link, telegram, kill, paused, clock, result):
    record = spool.read("cycles", key) or {}
    site = record.get("state")
    if site != "delivered":
        return dict(result, channels={"site": site, "telegram": "waiting_for_site"})
    payload = spool.read("delivery", f"{key}.brief") or {}
    text = telegram_copy.compose(_cycle_observations(spool, key), environment=environment,
                                 local_date=record["local_date"], learning=payload.get("learning"),
                                 details_link=details_link)
    try:
        out = telegram_copy.deliver(spool=spool, cycle_key=key, text=text, sender=telegram["sender"],
                                    destination_id=telegram["destination_id"], kill=kill, paused=paused,
                                    send_allowed=lambda: _send_window_open(record, clock().astimezone(ZONE)))
    except Exception:
        out = {"telegram": "error", "reason": "copy_failed_locally"}       # never affects the site brief
    return dict(result, channels={"site": "delivered", "telegram": out["telegram"]},
                telegram_reason=out.get("reason"))


def _save(spool, key, record, **changes):
    record.update(changes)
    spool.put_state("cycles", key, record)
    return record


def _send_window_open(record: dict, local) -> bool:
    """A brief may only be (re)sent on its own local date, before the catch-up cutoff."""
    return local.date().isoformat() == record["local_date"] and local.time() < CATCH_UP_UNTIL


def _learning_view(learning, environment, clock):
    source, principal = learning
    if source is None:
        return None
    if not principal:
        return learning_view.LearningView("unavailable", "principal_not_configured")
    return learning_view.read_view(source, principal=principal, environment=environment,
                                   now=clock().astimezone(timezone.utc))


def _cycle_observations(spool, key) -> list[Observation]:
    prefix = f"{key}."
    return [_observation_from(spool.read("observations", n)) for n in spool.names("observations")
            if n.startswith(prefix)]


def _run_locked(spool, watchlist, policy_id, environment, now, local, key, source_root, ingress,
                destination, kill, paused, details_link, learning, deps, clock):
    record = spool.read("cycles", key) or {"cycle_key": key, "policy_id": policy_id,
                                           "local_date": local.date().isoformat(),
                                           "watchlist_sha256": watchlist.sha256, "state": "scheduled",
                                           "delayed": False}
    if record["state"] in ("delivered", "missed", "failed", "held"):
        return {"cycle_key": key, "state": record["state"], "reason": "already_final"}
    if record["state"] in ("delivery_pending", "delivery_unknown"):
        return _deliver(spool, record, ingress, kill, paused, clock)
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
        # Never mix policies in one brief: a cycle started under one approved watchlist is not
        # finished under another. Hold it for explicit disposition; tomorrow uses the new list.
        return _save(spool, key, record, state="held", reason="policy_changed_mid_cycle",
                     watchlist_changed_to=watchlist.sha256)

    if record["state"] == "collecting":
        started = monotonic()
        for seq, watch in enumerate(watchlist.watches):
            name = f"{key}.{watch.id}"
            stored = spool.read("observations", name)
            if stored is None:
                if kill():
                    return _save(spool, key, record, state="failed", reason="killed_during_collection")
                if paused():
                    return {"cycle_key": key, "state": "paused", "reason": "paused_during_collection"}
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
    if paused():
        return {"cycle_key": key, "state": "paused", "reason": "paused_before_render"}
    observations = _cycle_observations(spool, key)
    if not record.get("rendered_at"):
        _save(spool, key, record, rendered_at=now.isoformat())
    payload_name = f"{key}.brief"
    payload = spool.read("delivery", payload_name)
    if payload is None:
        today = date.fromisoformat(record["local_date"])
        overdue = [w.label for w in watchlist.watches if w.review_by < today]
        view = _learning_view(learning, environment, clock)
        text = render_brief(observations, watchlist.watches, local_date=today, environment=environment,
                            details_link=details_link, learning=view, review_needed=overdue)
        if record.get("delayed"):
            text = text.replace(".\n", " (delayed).\n", 1)
        payload = {"cycle_key": key, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}
        if view is not None:
            payload["learning"] = view.record()       # snapshot id, as_of, coverage, counts
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
    return _deliver(spool, record, ingress, kill, paused, clock)


def _deliver(spool, record, ingress, kill, paused, clock):
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
        if ack_mod.committed(ack, event["event_id"]):
            return _save(spool, key, record, state="delivered", ack=ack)
        if ack is not None:   # a response we can't treat as delivered; keep the hold, never resend blindly
            return _save(spool, key, record, state="delivery_unknown", reason="readback_not_committed")
        # verifiably absent: the same idempotent event may be posted again, if sending is allowed
    if kill():
        return {"cycle_key": key, "state": record["state"], "reason": "killed_before_delivery"}
    if paused():
        return {"cycle_key": key, "state": record["state"], "reason": "paused_before_delivery"}
    if not _send_window_open(record, clock().astimezone(ZONE)):      # the time of sending, not of starting
        return _save(spool, key, record, state="held", reason="send_window_closed")
    try:
        ack = ingress.post(event)
    except IngressUncertain:
        return _save(spool, key, record, state="delivery_unknown", reason="send_unconfirmed")
    except Exception:
        return _save(spool, key, record, state="delivery_pending", reason="send_failed_before_commit")
    if ack_mod.committed(ack, event["event_id"]):
        return _save(spool, key, record, state="delivered", ack=ack)
    valid = ack_mod.check(ack, event["event_id"])
    if valid is not None and valid["state"] == "deferred":
        return _save(spool, key, record, state="delivery_pending", ack=ack)
    if isinstance(ack, dict) and ack.get("state") == "refused":
        return _save(spool, key, record, state="failed", reason="ingress_refused", ack=ack)
    # Not an acknowledgement of this event: it may still have committed, so reconcile, never resend blindly.
    return _save(spool, key, record, state="delivery_unknown", reason="ack_invalid")
