"""The job status contract (schema_version 1): one small JSON file per job. See docs/jobs_status_contract.md.

    <jobs_root>/<job_id>.json = {schema_version, job_id, host, run_id, state, started_at, finished_at,
                                 exit_code, summary, results, next_due_by, last_success_at, alert}

* Written at the start of a run (state "running") and again at its end ("ok" | "warn" | "failed").
  Both writes are atomic (temp file + rename); files are 0600 and the folder 0700.
* **Never any content.** ``summary`` is a short fixed phrase, ``results`` maps a part name to a fixed code,
  and both are checked against a narrow character set that cannot hold a path, a title or a sentence of
  conversation. A writer that tries to put something else in gets ``StatusError``; nothing is written.
* ``last_success_at`` is carried forward from the previous file: it is the finish time of the last run that
  completed (ok or warn), so a run in progress or a failed one does not erase when the job last worked.
* Readers (the watchdog, the Guild tile, an exporter) tolerate a missing, unreadable or corrupt file and
  treat it as *unknown*, never as healthy: ``read_status`` returns ``None`` for all of them.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
STATES = ("running", "ok", "warn", "failed")
COMPLETED = ("ok", "warn")                 # a run that finished; counts for "last success"
ALERT_VALUES = ("sent", "not_sent", "not_needed")
FILE_MODE, DIR_MODE = 0o600, 0o700
SUMMARY_MAX = 120
_SUMMARY = re.compile(r"^[A-Za-z0-9 _:.,;=()+-]{0,%d}$" % SUMMARY_MAX)
_PART = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_CODE = re.compile(r"^[a-z0-9][a-z0-9_:.-]{0,39}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_RUN = re.compile(r"^[A-Za-z0-9-]{1,40}$")


class StatusError(ValueError):
    """A status document that breaks the contract. Raised before anything is written."""


def iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def build(*, job_id: str, host: str, run_id: str, state: str, started_at: datetime,
          finished_at: datetime | None = None, exit_code: int | None = None, summary: str = "",
          results: dict | None = None, next_due_by: datetime | None = None,
          last_success_at: datetime | None = None, alert: str | None = None) -> dict:
    """A validated status document."""
    if results is not None and not isinstance(results, dict):
        raise StatusError("results")
    results = dict(results or {})
    if not _ID.match(job_id or ""):
        raise StatusError("job_id")
    if not _ID.match(host or ""):
        raise StatusError("host")
    if not _RUN.match(run_id or ""):
        raise StatusError("run_id")
    if state not in STATES:
        raise StatusError("state")
    if not isinstance(summary, str) or not _SUMMARY.match(summary):
        raise StatusError("summary")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        raise StatusError("exit_code")
    if alert is not None and alert not in ALERT_VALUES:
        raise StatusError("alert")
    for part, code in results.items():
        if not isinstance(part, str) or not _PART.match(part) or not isinstance(code, str) or not _CODE.match(code):
            raise StatusError("results")
    if len(results) > 20:
        raise StatusError("results")
    if state == "running" and (finished_at is not None or exit_code is not None):
        raise StatusError("running run has no finish")
    if state != "running" and finished_at is None:
        raise StatusError("finished run needs finished_at")
    return {"schema_version": SCHEMA_VERSION, "job_id": job_id, "host": host, "run_id": run_id, "state": state,
            "started_at": iso(started_at), "finished_at": iso(finished_at), "exit_code": exit_code,
            "summary": summary, "results": results, "next_due_by": iso(next_due_by),
            "last_success_at": iso(last_success_at), "alert": alert}


def validate(doc: object) -> bool:
    """True when ``doc`` is a status document this contract would have written."""
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        return False
    try:
        build(job_id=doc.get("job_id"), host=doc.get("host"), run_id=doc.get("run_id"), state=doc.get("state"),
              started_at=parse_time(doc.get("started_at")) or _bad(),
              finished_at=parse_time(doc.get("finished_at")) if doc.get("finished_at") is not None else None,
              exit_code=doc.get("exit_code"), summary=doc.get("summary", ""), results=doc.get("results"),
              next_due_by=None, last_success_at=None, alert=doc.get("alert"))
    except (ValueError, TypeError, AttributeError):
        return False
    for key in ("finished_at", "next_due_by", "last_success_at"):
        if doc.get(key) is not None and parse_time(doc[key]) is None:
            return False
    return True


def _bad():
    raise StatusError("started_at")


def ensure_root(jobs_root: str | Path) -> Path:
    root = Path(jobs_root)
    missing, probe = [], root
    while not probe.exists() and probe != probe.parent:
        missing.append(probe)
        probe = probe.parent
    for folder in reversed(missing):
        try:
            os.mkdir(folder, DIR_MODE)
        except FileExistsError:
            pass
        os.chmod(folder, DIR_MODE)
    return root


def write_status(jobs_root: str | Path, status_file: str, doc: dict) -> Path:
    """Atomic write: temp file in the same folder, fsync, rename. Refuses a document that breaks the contract."""
    if not validate(doc):
        raise StatusError("document")
    root = ensure_root(jobs_root)
    path = root / status_file
    tmp = root / f".{status_file}.tmp-{os.getpid()}"
    data = (json.dumps(doc, sort_keys=True, indent=1) + "\n").encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, FILE_MODE)
    os.replace(tmp, path)
    return path


def read_status(jobs_root: str | Path, status_file: str, job_id: str | None = None) -> dict | None:
    """The status document, or ``None`` when the file is missing, unreadable, corrupt or breaks the contract
    (or belongs to another job). ``None`` means *unknown*; callers must never treat it as healthy."""
    try:
        doc = json.loads((Path(jobs_root) / status_file).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not validate(doc) or (job_id is not None and doc.get("job_id") != job_id):
        return None
    return doc


def start_run(jobs_root, job, run_id: str, now: datetime) -> dict:
    """Write the ``running`` file, carrying the previous ``last_success_at`` forward."""
    previous = read_status(jobs_root, job.status_file, job.id)
    last = parse_time(previous.get("last_success_at")) if previous else None
    doc = build(job_id=job.id, host=job.host, run_id=run_id, state="running", started_at=now,
                summary="run in progress", last_success_at=last)
    write_status(jobs_root, job.status_file, doc)
    return doc


def finish_run(jobs_root, job, running: dict, *, state: str, now: datetime, exit_code: int, summary: str,
               results: dict, next_due_by: datetime | None, alert: str | None = None) -> dict:
    """Write the final file for the run ``running`` started."""
    if state not in ("ok", "warn", "failed"):
        raise StatusError("state")
    last = parse_time(running.get("last_success_at"))
    if state in COMPLETED:
        last = now
    doc = build(job_id=job.id, host=job.host, run_id=running["run_id"], state=state,
                started_at=parse_time(running["started_at"]), finished_at=now, exit_code=exit_code, summary=summary,
                results=results, next_due_by=next_due_by, last_success_at=last, alert=alert)
    write_status(jobs_root, job.status_file, doc)
    return doc
