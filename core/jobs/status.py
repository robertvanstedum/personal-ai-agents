"""The job status contract (schema_version 1): one small JSON file per job. See docs/jobs_status_contract.md.

    <jobs_root>/<job_id>.json = {schema_version, job_id, host, run_id, state, started_at, finished_at,
                                 exit_code, summary, results, next_due_by, last_success_at, alert,
                                 [progress], [last_refusal_at, last_refusal_reason]}

* Optional (WS3 C1.2, 28 Nov 2026): ``progress`` = {work_due: int >= 0 (1 means "some, count unknown"),
  last_progress_at: ISO time or null, phase: active | idle_no_work | complete | outside_window}, carried across runs
  so a failed or refused run does not erase when the job last moved.
* Refusals (a run refused at its lock) are a separate observation, ``<status file stem>.refusal.json`` beside the
  status file, written by ``record_refusal`` and read by ``read_refusal``. The status file itself is never rewritten
  for a refusal, so a refusal can never roll back a holder's run, completion, last success or progress, and a holder
  finishing can never drop a refusal (Codex Wave 1 review finding 2). ``last_refusal_at`` / ``last_refusal_reason``
  remain valid optional fields of the status document for writers that already know them.

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
PHASES = ("active", "idle_no_work", "complete", "outside_window")
FILE_MODE, DIR_MODE = 0o600, 0o700
SUMMARY_MAX = 120
RESULTS_MAX = 20                            # parts in ``results``; a writer with more must summarise
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
          last_success_at: datetime | None = None, alert: str | None = None, progress: dict | None = None,
          last_refusal_at: datetime | None = None, last_refusal_reason: str | None = None) -> dict:
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
    if len(results) > RESULTS_MAX:
        raise StatusError("results")
    if state == "running" and (finished_at is not None or exit_code is not None):
        raise StatusError("running run has no finish")
    if state != "running" and finished_at is None:
        raise StatusError("finished run needs finished_at")
    doc = {"schema_version": SCHEMA_VERSION, "job_id": job_id, "host": host, "run_id": run_id, "state": state,
           "started_at": iso(started_at), "finished_at": iso(finished_at), "exit_code": exit_code,
           "summary": summary, "results": results, "next_due_by": iso(next_due_by),
           "last_success_at": iso(last_success_at), "alert": alert}
    if progress is not None:
        doc["progress"] = _progress(progress)
    if (last_refusal_at is None) != (last_refusal_reason is None):
        raise StatusError("refusal")
    if last_refusal_at is not None:
        if not isinstance(last_refusal_reason, str) or not _CODE.match(last_refusal_reason):
            raise StatusError("refusal")
        doc["last_refusal_at"], doc["last_refusal_reason"] = iso(last_refusal_at), last_refusal_reason
    return doc


def _progress(raw: object) -> dict:
    """The validated ``progress`` block. Times are ISO strings in the document."""
    if not isinstance(raw, dict) or set(raw) - {"work_due", "last_progress_at", "phase"}:
        raise StatusError("progress")
    due, phase, last = raw.get("work_due"), raw.get("phase"), raw.get("last_progress_at")
    if isinstance(due, bool) or not isinstance(due, int) or due < 0:
        raise StatusError("progress")
    if phase not in PHASES:
        raise StatusError("progress")
    if isinstance(last, datetime):
        last = iso(last)
    if last is not None and parse_time(last) is None:
        raise StatusError("progress")
    return {"work_due": due, "last_progress_at": last, "phase": phase}


def validate(doc: object) -> bool:
    """True when ``doc`` is a status document this contract would have written."""
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        return False
    try:
        build(job_id=doc.get("job_id"), host=doc.get("host"), run_id=doc.get("run_id"), state=doc.get("state"),
              started_at=parse_time(doc.get("started_at")) or _bad(),
              finished_at=parse_time(doc.get("finished_at")) if doc.get("finished_at") is not None else None,
              exit_code=doc.get("exit_code"), summary=doc.get("summary", ""), results=doc.get("results"),
              next_due_by=None, last_success_at=None, alert=doc.get("alert"), progress=doc.get("progress"),
              last_refusal_at=parse_time(doc.get("last_refusal_at")) if doc.get("last_refusal_at") is not None else None,
              last_refusal_reason=doc.get("last_refusal_reason"))
        if doc.get("last_refusal_at") is not None and parse_time(doc["last_refusal_at"]) is None:
            return False
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


def write_json_atomic(jobs_root: str | Path, name: str, obj: object) -> Path:
    """Temp file in the same folder, fsync, rename; the file 0600, the folder 0700 (created if missing)."""
    root = ensure_root(jobs_root)
    path = root / name
    tmp = root / f".{name}.tmp-{os.getpid()}"
    data = (json.dumps(obj, sort_keys=True, indent=1) + "\n").encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(tmp, FILE_MODE)
    os.replace(tmp, path)
    return path


def write_status(jobs_root: str | Path, status_file: str, doc: dict) -> Path:
    """Atomic write of a status document. Refuses a document that breaks the contract."""
    if not validate(doc):
        raise StatusError("document")
    return write_json_atomic(jobs_root, status_file, doc)


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
                summary="run in progress", last_success_at=last, **_carried(previous))
    write_status(jobs_root, job.status_file, doc)
    return doc


def _carried(previous: dict | None) -> dict:
    """The optional block a new document carries from the previous one: progress."""
    if previous and isinstance(previous.get("progress"), dict):
        return {"progress": previous["progress"]}
    return {}


def refusal_name(status_file: str) -> str:
    stem = status_file[:-5] if status_file.endswith(".json") else status_file
    return f"{stem}.refusal.json"


def record_refusal(jobs_root, job, reason: str, now: datetime) -> dict:
    """Record that a run of ``job`` was refused (its lock was held), as its own small atomic file. One writer kind, no
    read-modify-write of the status file, so it cannot race the holder that owns the status file."""
    if not isinstance(reason, str) or not _CODE.match(reason):
        raise StatusError("refusal")
    doc = {"schema_version": SCHEMA_VERSION, "job_id": job.id, "last_refusal_at": iso(now), "last_refusal_reason": reason}
    write_json_atomic(jobs_root, refusal_name(job.status_file), doc)
    return doc


def read_refusal(jobs_root, job) -> dict | None:
    """The job's last refusal ({last_refusal_at, last_refusal_reason}), or None when there is none or it is unreadable."""
    try:
        doc = json.loads((Path(jobs_root) / refusal_name(job.status_file)).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION or doc.get("job_id") != job.id
            or parse_time(doc.get("last_refusal_at")) is None or not isinstance(doc.get("last_refusal_reason"), str)
            or not _CODE.match(doc["last_refusal_reason"])):
        return None
    return {"last_refusal_at": doc["last_refusal_at"], "last_refusal_reason": doc["last_refusal_reason"]}


def finish_run(jobs_root, job, running: dict, *, state: str, now: datetime, exit_code: int, summary: str,
               results: dict, next_due_by: datetime | None, alert: str | None = None, success: bool | None = None,
               progress: dict | None = None) -> dict:
    """Write the final file for the run ``running`` started. ``success=False`` keeps the previous ``last_success_at`` even for a
    completed run: a job whose success means more than finishing (``vault-sync``: the backlog reached 0) says so."""
    if state not in ("ok", "warn", "failed"):
        raise StatusError("state")
    last = parse_time(running.get("last_success_at"))
    if state in COMPLETED and success is not False:
        last = now
    doc = build(job_id=job.id, host=job.host, run_id=running["run_id"], state=state,
                started_at=parse_time(running["started_at"]), finished_at=now, exit_code=exit_code, summary=summary,
                results=results, next_due_by=next_due_by, last_success_at=last, alert=alert,
                **dict(_carried(running), **({"progress": progress} if progress is not None else {})))
    write_status(jobs_root, job.status_file, doc)
    return doc
