#!/usr/bin/env python3
"""Production: bring every other host's job-health report into MC's folder (FF-19 follow-up, #537).

    python3 scripts/jobs/pull_health.py          (host_watchdog.sh ec2 runs it before each hourly watch)

For each host in config/scheduled_jobs.json other than this one (and GitHub), fetch
``$MINIMOI_JOBS_HEALTH_PULL/health/<host>/health-<host>.json`` (the Vault bucket, read with the instance role) and, if it is
a valid report of that host and not dated in the future, place it in ``$MINIMOI_JOBS_HEALTH_DIR`` for MC and the daily
check. A failed fetch leaves the previous copy, whose own ``checked_at`` then shows how long the host has been silent.

Prints one line: ``pull mac=ok dev=missing``. Exit 0 always: a missing report is a finding for the daily check, not a
failure of this step.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.jobs import health, registry  # noqa: E402

NOT_A_HOST = ("github",)


def run(*, dest: str, health_dir, this_host: str, registry_path=None, now=None, run_cmd=None, fetch=None) -> dict:
    reg = registry.load(registry_path)
    now = now or datetime.now(timezone.utc)
    out = {}
    for host in reg.hosts():
        if host == this_host or host in NOT_A_HOST:
            continue
        out[host] = health.pull(dest, host, health_dir, now, run=run_cmd, fetch=fetch)
    return out


def main() -> int:
    dest = os.environ.get("MINIMOI_JOBS_HEALTH_PULL")
    folder = os.environ.get("MINIMOI_JOBS_HEALTH_DIR")
    if not dest or not folder:
        print("pull skipped (MINIMOI_JOBS_HEALTH_PULL or MINIMOI_JOBS_HEALTH_DIR not set)")
        return 0
    try:
        result = run(dest=dest, health_dir=folder, this_host=os.environ.get("MINIMOI_JOBS_HOST") or "ec2",
                     registry_path=os.environ.get("MINIMOI_JOBS_REGISTRY") or None)
    except registry.RegistryError:
        print("pull skipped (registry unreadable)")
        return 0
    print("pull " + " ".join(f"{h}={r}" for h, r in sorted(result.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
