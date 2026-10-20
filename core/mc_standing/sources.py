"""Read-only source adapters (standing spec §4.3). Each turns one sanitized snapshot into one
observation. Readers never follow symlinks, never read past ``max_read_bytes``, and never parse
arbitrary logs: an unsupported, stale, oversize, future-dated or malformed source is ``unknown``.

Source formats (fixtures until the host exporters land, integration request IR-1):
  job_status       core/jobs/status.py documents (backups and other registered jobs)
  health_snapshot  mc-health-snapshot/1   {sampled_at, environment, services:[{name, status, ...}]}
  host_snapshot    mc-host-snapshot/1     {sampled_at, filesystems:[{mount,total_bytes,avail_bytes}],
                                           mem_total_bytes, mem_available_bytes}
  release_status   minimoi.release-observation/1 (+ optional verification block)
  guest_summary    minimoi_portal.guest_requests.pending_summary() (injected callable)
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from core.jobs.judge import judge
from core.jobs.status import parse_time

from .observe import Observation, unknown, utc

CLOCK_TOLERANCE_S = 300
_JUDGE_STATE = {"green": "healthy", "yellow": "degraded", "red": "failed", "unknown": "unknown"}


class SourceUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def read_source(root: Path, watch) -> tuple[bytes, str]:
    """Bytes and sha256 of the watch's bound file under ``root``. No symlinks anywhere in the
    path, regular files only, size-bounded."""
    root = Path(root)
    target = root / watch.source_binding
    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        raise SourceUnavailable("source_root_missing") from None
    current = root
    for part in Path(watch.source_binding).parts:
        current = current / part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise SourceUnavailable("source_symlink_refused")
        except FileNotFoundError:
            raise SourceUnavailable("source_missing") from None
        except PermissionError:
            raise SourceUnavailable("source_permission_denied") from None
    if resolved_root not in target.resolve().parents:
        raise SourceUnavailable("source_outside_root")
    try:
        info = os.stat(target)
        if not stat.S_ISREG(info.st_mode):
            raise SourceUnavailable("source_not_regular")
        if info.st_size > watch.max_read_bytes:
            raise SourceUnavailable("source_oversize")
        with open(target, "rb") as fh:
            data = fh.read(watch.max_read_bytes + 1)
    except PermissionError:
        raise SourceUnavailable("source_permission_denied") from None
    except OSError:
        raise SourceUnavailable("source_unreadable") from None
    if len(data) > watch.max_read_bytes:
        raise SourceUnavailable("source_oversize")
    return data, hashlib.sha256(data).hexdigest()


def _json(data: bytes) -> dict:
    try:
        doc = json.loads(data)
    except ValueError:
        raise SourceUnavailable("source_malformed") from None
    if not isinstance(doc, dict):
        raise SourceUnavailable("source_malformed")
    return doc


def _fresh(sampled: datetime | None, watch, now: datetime) -> str | None:
    """A reason code when the sample cannot be trusted as current, else None."""
    if sampled is None:
        return "sample_time_missing"
    if sampled > now + timedelta(seconds=CLOCK_TOLERANCE_S):
        return "sample_time_future"
    if (now - sampled).total_seconds() > watch.stale_after_s:
        return "sample_stale"
    return None


def _obs(cycle, watch, state, reason, now, *, observed=None, evidence=(), coverage="complete", **m):
    return Observation(cycle, watch.id, watch.version, watch.environment, watch.host_scope, state,
                       reason, coverage, utc(now), utc(observed) if observed else None,
                       measurements=m, evidence=evidence)


def job_status(cycle, watch, now, root, **_):
    """A registered job (e.g. a backup tier) judged by the shared job-status rules."""
    data, digest = read_source(root, watch)
    doc = _json(data)
    rule = watch.expected_run_rule
    job = SimpleNamespace(
        overdue_after_s=rule["expected_every_s"] + rule["grace_s"],
        missed_after_s=rule["missed_after_s"], stuck_after_s=rule["stuck_after_s"], active_from=None)
    verdict = judge(job, doc, now)
    success = parse_time(doc.get("last_success_at"))
    age_h = round((now - success).total_seconds() / 3600, 1) if success else None
    state = _JUDGE_STATE[verdict.state]
    return _obs(cycle, watch, state, f"job_{verdict.code}", now, observed=success,
                evidence=((watch.source_binding, digest),),
                coverage="complete" if state != "unknown" else "partial",
                last_success_age_h=age_h, job_state=str(doc.get("state")) if doc.get("state") in
                ("running", "ok", "warn", "failed") else None)


def health_snapshot(cycle, watch, now, root, **_):
    data, digest = read_source(root, watch)
    doc = _json(data)
    if doc.get("schema") != "mc-health-snapshot/1" or not isinstance(doc.get("services"), list):
        raise SourceUnavailable("source_schema_unsupported")
    if doc.get("environment") not in (None, watch.environment, {"prod": "production"}.get(watch.environment)):
        raise SourceUnavailable("source_environment_mismatch")
    sampled = parse_time(doc.get("sampled_at"))
    stale = _fresh(sampled, watch, now)
    if stale:
        return unknown(cycle, watch, stale, now, coverage="partial")
    services = doc["services"]
    if not services or not all(isinstance(s, dict) and s.get("status") in ("ok", "fail", "unreachable")
                               for s in services):
        raise SourceUnavailable("source_malformed")
    bad = [s for s in services if s["status"] != "ok"]
    state = "failed" if bad else "healthy"
    return _obs(cycle, watch, state, "services_failing" if bad else "services_ok", now,
                observed=sampled, evidence=((watch.source_binding, digest),),
                services_total=len(services), services_failing=len(bad))


def host_snapshot(cycle, watch, now, root, **_):
    rules = watch.severity_rules
    if "disk_used_pct_degraded" not in rules or "mem_used_pct_degraded" not in rules:
        return unknown(cycle, watch, "threshold_missing", now)
    data, digest = read_source(root, watch)
    doc = _json(data)
    if doc.get("schema") != "mc-host-snapshot/1":
        raise SourceUnavailable("source_schema_unsupported")
    sampled = parse_time(doc.get("sampled_at"))
    stale = _fresh(sampled, watch, now)
    if stale:
        return unknown(cycle, watch, stale, now, coverage="partial")
    try:
        disk = max(100.0 * (1 - f["avail_bytes"] / f["total_bytes"]) for f in doc["filesystems"])
        mem = 100.0 * (1 - doc["mem_available_bytes"] / doc["mem_total_bytes"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        raise SourceUnavailable("source_malformed") from None
    critical = rules.get("critical_pct", 95)
    if disk >= critical or mem >= critical:
        state, reason = "failed", "headroom_critical"
    elif disk >= rules["disk_used_pct_degraded"] or mem >= rules["mem_used_pct_degraded"]:
        state, reason = "degraded", "headroom_low"
    else:
        state, reason = "healthy", "headroom_ok"
    return _obs(cycle, watch, state, reason, now, observed=sampled,
                evidence=((watch.source_binding, digest),),
                disk_used_pct=round(disk, 1), mem_used_pct=round(mem, 1))


def release_status(cycle, watch, now, root, **_):
    """Image identity alone is not verification: without a verification block the deploy is
    unknown/unverified. A deployment stuck in progress past stale_after_s is unknown, not fine."""
    data, digest = read_source(root, watch)
    doc = _json(data)
    if doc.get("format") != "minimoi.release-observation/1":
        raise SourceUnavailable("source_schema_unsupported")
    observed = parse_time(doc.get("observed_at"))
    if observed is None or observed > now + timedelta(seconds=CLOCK_TOLERANCE_S):
        raise SourceUnavailable("sample_time_invalid")
    status = doc.get("status")
    ev = ((watch.source_binding, digest),)
    tag = doc.get("requested_tag") if isinstance(doc.get("requested_tag"), str) else None
    if status == "selected_image_check_failed":
        return _obs(cycle, watch, "failed", "deploy_image_check_failed", now, observed=observed, evidence=ev)
    if status == "deployment_in_progress":
        if (now - observed).total_seconds() > watch.stale_after_s:
            return _obs(cycle, watch, "unknown", "deploy_in_progress_stale", now, observed=observed,
                        evidence=ev, coverage="partial")
        return _obs(cycle, watch, "unknown", "deploy_in_progress", now, observed=observed, evidence=ev,
                    coverage="partial")
    if status != "selected_images_observed":
        raise SourceUnavailable("source_malformed")
    verification = doc.get("verification")
    if not isinstance(verification, dict) or not isinstance(verification.get("services"), list) \
            or not verification["services"]:
        return _obs(cycle, watch, "unknown", "deploy_unverified", now, observed=observed, evidence=ev,
                    coverage="partial")
    failing = [s for s in verification["services"] if not (isinstance(s, dict) and s.get("ok") is True)]
    return _obs(cycle, watch, "failed" if failing else "healthy",
                "deploy_verification_failed" if failing else "deploy_verified", now, observed=observed,
                evidence=ev, services_checked=len(verification["services"]), services_failing=len(failing),
                **({"tag": tag} if tag and len(tag) <= 40 and tag.isalnum() else {}))


def guest_summary(cycle, watch, now, root=None, *, guest_summary_fn=None, **_):
    """Pending access requests: counts and oldest age only, no names or emails. Informational
    unless the oldest request passes ``severity_rules.pending_age_degraded_h``."""
    if guest_summary_fn is None:
        return unknown(cycle, watch, "source_not_configured", now)
    try:
        summary = guest_summary_fn()
    except Exception:
        return unknown(cycle, watch, "source_unreadable", now)
    stores = summary.get("stores", {}) if isinstance(summary, dict) else {}
    counts = {k: v.get("count") for k, v in stores.items() if isinstance(v, dict)}
    oldest = [parse_time(v.get("oldest")) for v in stores.values() if isinstance(v, dict)]
    oldest = min((o for o in oldest if o), default=None)
    total = sum(c for c in counts.values() if isinstance(c, int))
    age_h = round((now - oldest).total_seconds() / 3600, 1) if oldest else None
    measurements = dict(pending_total=total, oldest_age_h=age_h,
                        **{f"pending_{k}": v for k, v in counts.items() if k in ("guild", "pending")})
    if summary.get("coverage") != "complete" or any(c is None for c in counts.values()):
        return _obs(cycle, watch, "unknown", "guest_store_unavailable", now, coverage="partial", **measurements)
    limit = watch.severity_rules.get("pending_age_degraded_h")
    if total and limit is not None and age_h is not None and age_h > limit:
        return _obs(cycle, watch, "degraded", "guest_requests_waiting", now, **measurements)
    return _obs(cycle, watch, "healthy", "guest_requests_pending" if total else "guest_requests_none",
                now, **measurements)


ADAPTERS = {
    "job_status": job_status, "health_snapshot": health_snapshot, "host_snapshot": host_snapshot,
    "release_status": release_status, "guest_summary": guest_summary,
}


def observe(cycle_key: str, watch, now: datetime, root: Path, **deps) -> Observation:
    """One watch → one observation. Every failure becomes an explicit unknown with its reason."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    try:
        return ADAPTERS[watch.adapter_id](cycle_key, watch, now, root, **deps)
    except SourceUnavailable as exc:
        return unknown(cycle_key, watch, exc.reason, now)
