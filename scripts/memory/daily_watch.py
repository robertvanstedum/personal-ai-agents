#!/usr/bin/env python3
"""The daily memory capture: ``moi watch`` for every memory source Robert has approved, with a status file.

    venv/bin/python scripts/memory/daily_watch.py

Registered as job ``memory-watch`` in config/scheduled_jobs.json; launchd runs it at 04:00 Chicago
(infrastructure/launchd/com.vanstedum.minimoi-memory-watch.plist). Contract: docs/jobs_status_contract.md.

What it does
* Writes ``<jobs root>/memory-watch.json`` as ``running``, then runs ``moi watch <source>`` for each of
  SOURCES (claude-code, codex) whose approval is **approved right now**. A source that is not approved
  (or whose approval went stale) is recorded as ``not_approved`` / ``stale`` and is never captured, and
  that is a warning, not an error. The inbox is never run by this job.
* Finishes ``ok``; ``warn`` (a source not approved or stale, disk_low, unstable or failed files, or a coverage
  flag: ``possible_gap`` / ``unknown_kind``);
  or ``failed`` (an exception, a nonzero exit from a watch, an output it cannot read, or an approval check that
  cannot run: it then captures nothing, fail closed).
* After the captures it publishes the counts-only **memory capture report** and the Operate matrix (``moi report --publish
  --fidelity N``: ``memory-capture-report.json`` and ``memory-capture-matrix.json`` under the jobs root; contract
  ``docs/memory_capture_report_contract.md``). A report failure is a **warning** (``report_failed``), never a failed capture.
* On ``failed`` it sends ONE Telegram message (job id, state, fixed codes; no content) and records
  ``alert`` as ``sent`` or ``not_sent``; it exits nonzero either way.

Exit codes: 0 finished ok or warn; 1 finished failed; 2 the wrapper itself broke (the status file could not
be written; it then stays ``running`` and the watchdog reports it stuck).

The jobs root is MINIMOI_JOBS_ROOT (default ~/minimoi-staging/data/jobs). ``moi`` is scripts/moi, or MOI_BIN.
Nothing here reads conversation content: it sees only ``moi``'s one-line summary (status and counts).
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.jobs import alert, registry, schedule, status  # noqa: E402

JOB_ID = "memory-watch"
SOURCES = ("claude-code", "codex")              # the approved-memory sources; the inbox is deliberately absent
OPTIONAL_SOURCES = ("rooms",)                   # Rooms export bundles: run only when configured (then it follows the same approval gate)
DEFAULT_JOBS_ROOT = "~/minimoi-staging/data/jobs"
WATCH_TIMEOUT_S = 3600
OK, WARN, FAILED = "ok", "warn", "failed"
WARN_CODES = frozenset({"not_approved", "stale", "not_configured", "disk_low", "ok_unstable", "ok_failed_files",
                        "ok_possible_gap", "ok_unknown_kind", "ok_refused_files", "ok_unknown_participant", "unreadable",
                        "report_failed", "report_unparseable", "report_timeout"})
EXIT = {OK: 0, WARN: 0, FAILED: 1}
EXIT_BROKEN = 2


class ApprovalUnavailable(RuntimeError):
    """The approval state could not be read. The job captures nothing."""


# ── the two seams the tests replace ──────────────────────────────────────────

def shelf_approval(source: str) -> str:
    """``approved`` | ``not_approved`` | ``stale`` | ``no_dry_run`` | ``not_configured``, from the shelf's own approval records."""
    try:
        from core.memory_shelf import approvals, config
        from core.memory_shelf.shelf import Shelf
        cfg = config.load(os.environ.get("MOI_CONFIG") or None)
        if source not in cfg.sources:
            return "not_configured"
        shelf = Shelf(cfg.shelf_root, min_free_bytes=cfg.min_free_bytes, min_free_fraction=cfg.min_free_fraction)
        return approvals.source_status(shelf, source, cfg.sources[source].fingerprint())
    except Exception as exc:                    # noqa: BLE001 - missing shelf code, bad config, unreadable record
        raise ApprovalUnavailable(type(exc).__name__) from exc


REPORT_TIMEOUT_S = 1800
FIDELITY_SAMPLE = 10                            # records re-read from their sources each day for the report's quality section


def moi_report(jobs_root) -> tuple[int, str]:
    """Run ``moi report --publish --fidelity N``: the counts-only capture report and the Operate matrix, written atomically to the
    jobs root. Read-only on the shelf apart from the report files and the sample result. Raises subprocess.TimeoutExpired."""
    moi = os.environ.get("MOI_BIN") or str(REPO / "scripts" / "moi")
    done = subprocess.run([moi, "report", "--publish", "--fidelity", str(FIDELITY_SAMPLE), "--jobs-root", str(jobs_root)],
                          capture_output=True, text=True, timeout=REPORT_TIMEOUT_S, stdin=subprocess.DEVNULL, cwd=str(REPO), check=False)
    return done.returncode, done.stdout


def code_for_report(returncode: int, stdout: str) -> str:
    """``ok`` or a fixed code; only the summary line's first word is read."""
    if returncode != 0:
        return f"exit_{returncode}"
    return "ok" if any(line.startswith("report\tpublished=") for line in stdout.splitlines()) else "unparseable"


def moi_watch(source: str) -> tuple[int, str]:
    """Run ``moi watch <source>``; returns (exit code, stdout). Raises subprocess.TimeoutExpired."""
    moi = os.environ.get("MOI_BIN") or str(REPO / "scripts" / "moi")
    done = subprocess.run([moi, "watch", source], capture_output=True, text=True, timeout=WATCH_TIMEOUT_S,
                          stdin=subprocess.DEVNULL, cwd=str(REPO), check=False)
    return done.returncode, done.stdout


# ── reading ``moi watch`` ────────────────────────────────────────────────────

def code_for_watch(returncode: int, stdout: str) -> str:
    """A fixed code for one watch. Only the status word and two counts are read; nothing else is kept."""
    if returncode != 0:
        return f"exit_{returncode}"
    line = next((ln for ln in reversed(stdout.splitlines()) if ln.startswith("watch\t")), None)
    parts = line.split("\t") if line else []
    if len(parts) != 4:
        return "unparseable"
    word = parts[2]
    if word == "ok":
        try:
            counts = json.loads(parts[3])
        except ValueError:
            return "unparseable"
        if not isinstance(counts, dict):
            return "unparseable"
        failed, unstable = counts.get("failed", 0), counts.get("unstable", 0)
        if isinstance(failed, int) and failed > 0:
            return "ok_failed_files"
        if isinstance(unstable, int) and unstable > 0:
            return "ok_unstable"
        # The coverage check (amendment §10): files whose capture took fewer messages than the file shows, or a
        # format the reader has not seen. Counts are files flagged now, so the warning stays until it is cleared.
        # Rooms: a meeting whose participants could not be identified, or a bundle refused as corrupt, is a warning
        # (an ordinary D2 exclusion of a meeting with a guest is expected and is not).
        who, refused = counts.get("unknown_participant", 0), counts.get("refused", 0)
        if isinstance(who, int) and who > 0:
            return "ok_unknown_participant"
        if isinstance(refused, int) and refused > 0:
            return "ok_refused_files"
        gap, unknown = counts.get("possible_gap", 0), counts.get("unknown_kind", 0)
        if isinstance(gap, int) and gap > 0:
            return "ok_possible_gap"
        if isinstance(unknown, int) and unknown > 0:
            return "ok_unknown_kind"
        return "ok"
    if word == "disk_low":
        return "disk_low"
    if word in ("not_approved", "dry_run_only"):
        return "not_approved"                   # the approval went away between our check and the watch
    return "unexpected"


def state_for(results: dict[str, str]) -> str:
    codes = set(results.values())
    if any(c not in WARN_CODES and c != "ok" for c in codes):
        return FAILED
    return WARN if codes & WARN_CODES else OK


def summarize(state: str, results: dict[str, str]) -> str:
    if state == FAILED:
        bad = [f"{p}={c}" for p, c in results.items() if c not in WARN_CODES and c != "ok"]
        return ("failed: " + ", ".join(bad))[: status.SUMMARY_MAX]
    sources = {k: v for k, v in results.items() if k != "report"}
    good = sum(1 for c in sources.values() if c == "ok")
    return f"{good} of {len(sources)} sources ok" + ("" if state == OK else ", see results")


def capture(approval: Callable[[str], str], watch: Callable[[str], tuple[int, str]]) -> dict[str, str]:
    results: dict[str, str] = {}
    try:
        gates = {source: approval(source) for source in SOURCES}
        for source in OPTIONAL_SOURCES:
            gate = approval(source)
            if gate != "not_configured":                # an optional source nobody configured is not a problem
                gates[source] = gate
    except ApprovalUnavailable:
        return {"approvals": "approval_unavailable"}
    for source, gate in gates.items():
        if gate != "approved":                  # never captured; not an error
            results[source] = gate if gate in ("stale", "not_configured") else "not_approved"
            continue
        try:
            results[source] = code_for_watch(*watch(source))
        except subprocess.TimeoutExpired:
            results[source] = "timeout"
        except Exception as exc:                # noqa: BLE001 - a fixed code, never the message
            results[source] = f"error_{type(exc).__name__.lower()}"[:40]
    return results


def _job(registry_path) -> tuple[registry.Job, dict[str, str]]:
    try:
        job = registry.load(registry_path).get(JOB_ID)
        if job:
            return job, {}
    except registry.RegistryError:
        pass
    fallback = registry.parse({"schema_version": 1, "jobs": [{
        "id": JOB_ID, "name": "Memory capture (daily watch)", "host": "mac",
        "schedule": {"text": "daily 04:00 Chicago", "kind": "daily", "at": "04:00", "tz": "America/Chicago"},
        "expected_every_s": 86400, "missed_after_s": 180000, "stuck_after_s": 7200,
        "status_file": f"{JOB_ID}.json", "owner": "robert", "runbook": "OPERATIONS.md"}]}).jobs[0]
    return fallback, {"registry": "unreadable"}


def publish_report(jobs_root, report: Callable[[object], tuple[int, str]]) -> dict[str, str]:
    """The report step: a failure here is a warning (the capture itself may be fine) and never an exception."""
    try:
        code = code_for_report(*report(jobs_root))
    except subprocess.TimeoutExpired:
        return {"report": "report_timeout"}
    except Exception:                           # noqa: BLE001 - a fixed code, never the message
        return {"report": "report_failed"}
    return {"report": "ok" if code == "ok" else ("report_unparseable" if code == "unparseable" else "report_failed")}


def run(*, jobs_root, registry_path=None, approval=shelf_approval, watch=moi_watch, report=moi_report, sender=alert.send,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc), out=sys.stdout, err=sys.stderr) -> int:
    job, extra = _job(registry_path)
    started = now()
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    try:
        running = status.start_run(jobs_root, job, run_id, started)
    except Exception as exc:                    # noqa: BLE001
        print(f"{JOB_ID}: cannot write the status file ({type(exc).__name__})", file=err)
        sender(f"minimoi job {JOB_ID} FAILED on {job.host}: status file not writable, run {run_id}")
        return EXIT_BROKEN
    try:
        results = capture(approval, watch)
        results.update(publish_report(jobs_root, report))
    except Exception as exc:                    # noqa: BLE001
        results = {"wrapper": f"exception_{type(exc).__name__.lower()}"[:40]}
    results.update(extra)
    state = state_for(results)
    if "registry" in results and state == OK:
        state = WARN
    finished = now()
    try:
        next_due = schedule.next_fire(job.schedule, finished)
    except Exception:                           # noqa: BLE001 - no zone database: the file just has no next_due_by
        next_due = None
    summary = summarize(state, results)
    alert_result = "not_needed"
    if state == FAILED:
        body = ", ".join(f"{p}={c}" for p, c in results.items())
        try:
            alert_result = sender(f"minimoi job {job.id} FAILED on {job.host}, run {run_id}: {body}")
        except Exception:                       # noqa: BLE001
            alert_result = alert.NOT_SENT
        if alert_result not in status.ALERT_VALUES:
            alert_result = alert.NOT_SENT
    code = EXIT[state]
    try:
        status.finish_run(jobs_root, job, running, state=state, now=finished, exit_code=code, summary=summary,
                          results=results, next_due_by=next_due, alert=alert_result)
    except Exception as exc:                    # noqa: BLE001
        print(f"{JOB_ID}: cannot write the final status ({type(exc).__name__})", file=err)
        return EXIT_BROKEN
    print(f"{JOB_ID} {state} {json.dumps(results, sort_keys=True)} alert={alert_result}", file=out)
    return code


def main() -> int:
    root = Path(os.environ.get("MINIMOI_JOBS_ROOT") or DEFAULT_JOBS_ROOT).expanduser()
    return run(jobs_root=root, registry_path=os.environ.get("MINIMOI_JOBS_REGISTRY") or None)


if __name__ == "__main__":
    sys.exit(main())
