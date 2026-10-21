"""Status-file bridge (spec 161 §8): existing job status files and host snapshots become
metrics, so no existing job changes in O1.

Reads (read-only, bounded, no symlinks):
  * the scheduled-jobs registry (``config/scheduled_jobs.json``) and each job's status file
    through ``core.jobs.status.read_status`` (missing or invalid is unknown, never healthy);
  * ``mc-health-snapshot/1`` (services up/down);
  * ``mc-host-snapshot/1`` (filesystem and memory used ratios);
  * ``minimoi.release-observation/1`` (last deploy outcome and time).
Emits only facts. Per-job windows (expected interval, missed-after, stuck-after) come from the
registry, which is job configuration, so one generic alert rule covers every job.

    python -m core.observability.bridge --jobs-root /app/data/jobs [--interval 60] [--once]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from core.jobs import registry as job_registry, status as job_status

from . import configure
from .vocabulary import TelemetryContractError

MAX_SNAPSHOT_BYTES = 64 * 1024
DEPLOY_OUTCOME = {"selected_images_observed": None, "selected_image_check_failed": 1,
                  "deployment_in_progress": 2}


class SourceUnreadable(Exception):
    pass


def _read_json(path: Path) -> dict:
    try:
        # O_NONBLOCK: a FIFO or device at a snapshot path is refused below instead of hanging.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise SourceUnreadable() from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SNAPSHOT_BYTES:
            raise SourceUnreadable()
        raw = os.read(fd, MAX_SNAPSHOT_BYTES + 1)
    finally:
        os.close(fd)
    try:
        doc = json.loads(raw)
    except ValueError:
        raise SourceUnreadable() from None
    if not isinstance(doc, dict):
        raise SourceUnreadable()
    return doc


def _ts(value) -> float | None:
    moment = job_status.parse_time(value)
    return moment.timestamp() if moment else None


def _ratio(total, avail) -> float:
    if isinstance(total, bool) or isinstance(avail, bool) or not isinstance(total, (int, float)) \
            or not isinstance(avail, (int, float)) or not math.isfinite(total) or not math.isfinite(avail) \
            or total <= 0 or avail < 0 or avail > total:
        raise SourceUnreadable()
    return 1.0 - avail / total


def _safe(tel, name, value, **labels):
    """One value whose label comes from a source file: refuse it alone, never the whole pass."""
    try:
        tel.gauge(name, value, **labels)
    except TelemetryContractError:
        tel._drop("metrics")


def collect_jobs(tel, jobs_root: Path, registry_path: Path | None) -> int:
    try:
        reg = job_registry.load(registry_path)
    except job_registry.RegistryError:
        tel.gauge("minimoi_bridge_source_readable", 0, source="job_registry")
        return 0
    tel.gauge("minimoi_bridge_source_readable", 1, source="job_registry")
    for job in reg.jobs:
        tel.gauge("minimoi_job_expected_interval_seconds", job.expected_every_s, job_id=job.id)
        tel.gauge("minimoi_job_missed_after_seconds", job.missed_after_s, job_id=job.id)
        tel.gauge("minimoi_job_stuck_after_seconds", job.stuck_after_s, job_id=job.id)
        try:
            tel.vocab.check_label("job_id", job.id)
        except TelemetryContractError:
            tel._drop("metrics")
            continue
        doc = job_status.read_status(jobs_root, job.status_file, job.id)
        if doc is None:
            tel.gauge("minimoi_job_status_readable", 0, job_id=job.id)
            continue
        tel.gauge("minimoi_job_status_readable", 1, job_id=job.id)
        tel.gauge("minimoi_job_running", 1 if doc["state"] == "running" else 0, job_id=job.id)
        success = _ts(doc.get("last_success_at"))
        if success is not None:
            tel.gauge("minimoi_job_last_success_timestamp_seconds", success, job_id=job.id)
        finished = _ts(doc.get("finished_at")) or _ts(doc.get("started_at"))
        if finished is not None:
            tel.gauge("minimoi_job_last_finish_timestamp_seconds", finished, job_id=job.id)
        if isinstance(doc.get("exit_code"), int) and not isinstance(doc.get("exit_code"), bool):
            tel.gauge("minimoi_job_last_exit_code", doc["exit_code"], job_id=job.id)
    return len(reg.jobs)


def collect_health(tel, path: Path):
    try:
        doc = _read_json(path)
        services = doc.get("services")
        if doc.get("schema") != "mc-health-snapshot/1" or not isinstance(services, list) or not services:
            raise SourceUnreadable()
        rows = []
        for s in services:
            if not isinstance(s, dict) or s.get("status") not in ("ok", "fail", "unreachable") \
                    or not isinstance(s.get("name"), str):
                raise SourceUnreadable()
            rows.append((s["name"], 1 if s["status"] == "ok" else 0))
    except SourceUnreadable:
        tel.gauge("minimoi_bridge_source_readable", 0, source="health_snapshot")
        return
    tel.gauge("minimoi_bridge_source_readable", 1, source="health_snapshot")
    for name, up in rows:
        _safe(tel, "minimoi_service_up", up, service=name)  # an unregistered name is refused and counted


def collect_host(tel, path: Path):
    try:
        doc = _read_json(path)
        if doc.get("schema") != "mc-host-snapshot/1":
            raise SourceUnreadable()
        filesystems = doc.get("filesystems")
        if not isinstance(filesystems, list) or not filesystems or len(filesystems) > 32:
            raise SourceUnreadable()
        disks = []
        for f in filesystems:
            if not isinstance(f, dict) or not isinstance(f.get("mount"), str):
                raise SourceUnreadable()
            disks.append((f["mount"], _ratio(f.get("total_bytes"), f.get("avail_bytes"))))
        memory = _ratio(doc.get("mem_total_bytes"), doc.get("mem_available_bytes"))
    except SourceUnreadable:
        tel.gauge("minimoi_bridge_source_readable", 0, source="host_snapshot")
        return
    tel.gauge("minimoi_bridge_source_readable", 1, source="host_snapshot")
    for mount, ratio in disks:
        _safe(tel, "minimoi_host_filesystem_used_ratio", round(ratio, 4), mountpoint=mount)
    tel.gauge("minimoi_host_memory_used_ratio", round(memory, 4))


def collect_release(tel, path: Path):
    try:
        doc = _read_json(path)
        if doc.get("format") != "minimoi.release-observation/1" or doc.get("status") not in DEPLOY_OUTCOME:
            raise SourceUnreadable()
        observed = _ts(doc.get("observed_at"))
        if observed is None:
            raise SourceUnreadable()
        outcome = DEPLOY_OUTCOME[doc["status"]]
        if outcome is None:   # images observed: ok only with a fully passing verification block
            verification = doc.get("verification")
            services = verification.get("services") if isinstance(verification, dict) else None
            if not isinstance(services, list) or not services:
                outcome = 2   # unverified: treated as not finished, never as ok
            else:
                outcome = 0 if all(isinstance(s, dict) and s.get("ok") is True for s in services) else 1
    except SourceUnreadable:
        tel.gauge("minimoi_bridge_source_readable", 0, source="release_status")
        return
    tel.gauge("minimoi_bridge_source_readable", 1, source="release_status")
    tel.gauge("minimoi_deploy_last_outcome", outcome)
    tel.gauge("minimoi_deploy_last_timestamp_seconds", observed)


def collect_once(tel, args) -> None:
    root = Path(args.jobs_root)
    collect_jobs(tel, root, Path(args.registry) if args.registry else None)
    collect_health(tel, root / args.health)
    collect_host(tel, root / args.host)
    if args.release:
        collect_release(tel, Path(args.release))
    tel.heartbeat()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="observability-bridge")
    p.add_argument("--jobs-root", required=True)
    p.add_argument("--registry", help="default: config/scheduled_jobs.json")
    p.add_argument("--health", default="health/services.json")
    p.add_argument("--host", default="health/host.json")
    p.add_argument("--release", help="path to the release-observation status.json")
    p.add_argument("--interval", type=float, default=60)
    p.add_argument("--once", action="store_true")
    args = p.parse_args(argv)
    tel = configure(service_name="minimoi-observability-bridge")
    while True:
        started = time.monotonic()
        try:
            collect_once(tel, args)
        except Exception:     # one bad pass never stops the bridge; the heartbeat shows liveness
            tel.count("minimoi_telemetry_dropped_total", 1, signal="metrics")
        if args.once:
            tel.flush(5.0)
            return 0
        time.sleep(max(1.0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
