#!/usr/bin/env python3
"""The memory index job: embed-on-file plus the freshness light (v0.5 C3, amendment M3).

One run = ``indexer.update`` (index new and changed shelf records, drop deleted ones) followed by ``health.status``.
The result is published through the jobs status contract (``docs/jobs_status_contract.md``) as
``<jobs root>/memory-index.json``, so the watchdog and the Guild Operate tile read it like any other job: counts and
fixed codes only.

State: ``ok`` = updated and the light is ok; ``warn`` = something to look at but nothing broken (records pending
because the embedder was down, the disk is low, another writer held the index, or a problem hold); ``failed`` =
orphans, a model-pin mismatch, an index that needs ``moi rebuild``, or the run broke. A failure never touches the
shelf and never blocks capture: the index is a cache.

Not scheduled by this file. Activation (Robert's go) = a registry entry in ``config/scheduled_jobs.json`` plus a
launchd plist, or chaining this run after the daily memory watch; see the M3 handoff.

Exit codes: 0 ok/warn, 1 failed, 2 the wrapper itself broke (status file not writable).
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.jobs import registry, status  # noqa: E402
from core.memory_index import health, indexer  # noqa: E402
from core.memory_index.embedder import OllamaEmbedder, DEFAULT_URL  # noqa: E402

JOB_ID = "memory-index"
DEFAULT_JOBS_ROOT = "~/minimoi-staging/data/jobs"
WARN_UPDATE = frozenset({indexer.EMBEDDER_UNAVAILABLE, indexer.DISK_LOW, indexer.BUSY, indexer.OUTSIDE_WINDOW})
FAIL_UPDATE = frozenset({indexer.PIN_MISMATCH, indexer.INCOMPATIBLE})


def _job(registry_path) -> registry.Job:
    try:
        job = registry.load(registry_path).get(JOB_ID)
        if job:
            return job
    except registry.RegistryError:
        pass
    return registry.parse({"schema_version": 1, "jobs": [{
        "id": JOB_ID, "name": "Memory index (embed-on-file)", "host": "mac",
        "schedule": {"text": "daily 04:30 Chicago", "kind": "daily", "at": "04:30", "tz": "America/Chicago"},
        "expected_every_s": 86400, "missed_after_s": 180000, "stuck_after_s": 10800,
        "status_file": f"{JOB_ID}.json", "owner": "robert", "runbook": "OPERATIONS.md"}]}).jobs[0]


def judge(update: dict, light: dict) -> tuple[str, dict]:
    """(state, results) from the update result and the status light. Fixed codes only."""
    code = str(update.get("code", "unknown"))
    lamp = str(light.get("light", "unknown"))
    results = {"update": code, "light": lamp}
    if light.get("orphans"):
        results["orphans"] = "present"
    if light.get("model_pin") and light["model_pin"] != "ok":
        results["model_pin"] = str(light["model_pin"])
    pending = sum((light.get("pending") or {}).values())
    if pending:
        results["pending"] = "some"
    if code in FAIL_UPDATE or lamp == "failed" or code not in WARN_UPDATE | {indexer.OK}:
        return "failed", results
    if code in WARN_UPDATE or lamp in ("warn", "unknown"):
        return "warn", results
    return "ok", results


def summarize(state: str, update: dict, light: dict) -> str:
    held = sum((light.get("held") or {}).values())
    text = (f"{state}: {int(light.get('indexed_current') or 0)} records current, {held} held, "
            f"{int(light.get('chunks') or 0)} chunks, update {update.get('code', 'unknown')}")
    return text[:status.SUMMARY_MAX]


def run(*, jobs_root, shelf_root: Path, index_path: Path | None = None, embedder=None, registry_path=None,
        now=lambda: datetime.now(timezone.utc), out=sys.stdout) -> int:
    job = _job(registry_path)
    started = now()
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    try:
        running = status.start_run(jobs_root, job, run_id, started)
    except Exception:                                   # noqa: BLE001
        print(f"{JOB_ID}: cannot write the status file", file=out)
        return 2
    emb = embedder or OllamaEmbedder(os.environ.get("MOI_OLLAMA_URL") or DEFAULT_URL)
    try:
        update = indexer.update(shelf_root, emb, index_path=index_path)
    except Exception as exc:                            # noqa: BLE001 - fixed code, never the message
        update = {"code": f"exception_{type(exc).__name__.lower()}"[:40]}
    try:
        light = health.status(shelf_root, index_path=index_path, embedder=emb)
    except Exception as exc:                            # noqa: BLE001
        light = {"light": "unknown", "reason": f"exception_{type(exc).__name__.lower()}"[:40]}
    state, results = judge(update, light)
    finished = now()
    try:
        from core.jobs import schedule
        next_due = schedule.next_fire(job.schedule, finished)
    except Exception:                                   # noqa: BLE001
        next_due = None
    try:
        status.finish_run(jobs_root, job, running, state=state, now=finished, exit_code=1 if state == "failed" else 0,
                          summary=summarize(state, update, light), results=results, next_due_by=next_due,
                          alert="not_needed")
    except Exception:                                   # noqa: BLE001
        print(f"{JOB_ID}: cannot write the final status", file=out)
        return 2
    print(f"{JOB_ID} {state} {json.dumps(results, sort_keys=True)}", file=out)
    return 1 if state == "failed" else 0


def main() -> int:
    from core.memory_shelf import config
    root = Path(os.environ.get("MINIMOI_JOBS_ROOT") or DEFAULT_JOBS_ROOT).expanduser()
    return run(jobs_root=root, shelf_root=config.load().shelf_root,
               registry_path=os.environ.get("MINIMOI_JOBS_REGISTRY") or None)


if __name__ == "__main__":
    sys.exit(main())
