"""Synthetic report payloads for each state the Operate matrix must handle (normal, watch, failed, stale, missing, malformed).
``python scripts/memory/make_report_samples.py`` writes them to ``docs/memory_capture_report_samples/``; a test regenerates
them and fails if the committed files drift from the producer."""
from __future__ import annotations

import json
import tempfile
from datetime import timedelta
from pathlib import Path

from core.memory_shelf import approvals, record, report, watchers

from .report_world import FIXED_RUN, NOW, OWNER, build_world


def _fidelity(shelf, *, failed=0):
    report.record_quality(shelf, "fidelity", {"at": "2026-10-04T11:30:00Z", "method": "random_sample", "seed": 20261004, "sample_size": 10,
                                              "checked": 3, "ok": 3 - failed, "failed": failed, "skipped": {},
                                              "scope": "storage_and_edition_same_parser"})


def _job(jobs: Path, state="ok", finished="2026-10-04T11:59:00Z"):
    from datetime import datetime, timezone
    from core.jobs import registry, status
    job = registry.parse({"schema_version": 1, "jobs": [{"id": "memory-watch", "name": "m", "host": "mac",
        "schedule": {"text": "daily 04:00 Chicago", "kind": "daily", "at": "04:00", "tz": "America/Chicago"}, "expected_every_s": 86400,
        "missed_after_s": 180000, "stuck_after_s": 7200, "status_file": "memory-watch.json", "owner": "robert", "runbook": "x"}]}).jobs[0]
    running = status.start_run(jobs, job, "r1", datetime(2026, 10, 4, 11, 58, tzinfo=timezone.utc))
    status.finish_run(jobs, job, running, state=state, now=datetime.fromisoformat(finished.replace("Z", "+00:00")), exit_code=0 if state != "failed" else 1,
                      summary="done", results={"claude-code": "ok", "codex": "ok" if state != "failed" else "exit_1"}, next_due_by=None,
                      alert="not_needed")


def _scenario(name: str, base: Path):
    tmp = base / name
    tmp.mkdir()
    shelf, cfg, sources = build_world(tmp, approve=name != "missing", run=name != "missing")
    jobs = tmp / "jobs"
    now = NOW
    if name == "missing":
        for src in sources.values():
            watchers.dry_run(shelf, src, NOW)
            approvals.approve_source(shelf, src.name, src.fingerprint(), OWNER)       # approved, never run: no state
        return report.build(shelf, cfg, now, jobs_root=jobs, run_id=FIXED_RUN)
    _fidelity(shelf, failed=1 if name == "watch" else 0)
    _job(jobs, state="failed" if name == "failed" else "ok", finished="2026-10-04T11:59:00Z")
    if name == "watch":
        main = shelf.main_path(shelf.index()["codex:cx-1"])
        meta, body = record.load(record.read(main))
        meta["normalized"]["normalizer"] = 2                                          # a record still on the older parser
        record.write(main, meta, body)
    if name == "failed":
        path = Path(shelf.status_dir) / "watch-codex.json"
        doc = json.loads(path.read_text())
        doc.update(last_run_at="2026-10-04T11:58:00Z", counts={"failed": 2})
        path.write_text(json.dumps(doc))
    if name == "stale":
        now = NOW + timedelta(seconds=report.MISSED_AFTER_S + 3600)
    if name == "malformed":
        (Path(shelf.status_dir) / "watch-codex.json").write_text("{not json")
        (Path(shelf.status_dir) / "quality" / "fidelity.json").write_text("[1, 2")
    return report.build(shelf, cfg, now, jobs_root=jobs, run_id=FIXED_RUN)


SCENARIOS = ("normal", "watch", "failed", "stale", "missing", "malformed")


def make_samples() -> dict[str, dict]:
    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        return {name: _scenario(name, base) for name in SCENARIOS}


def render(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, indent=1) + "\n"
