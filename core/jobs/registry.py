"""The scheduled-jobs registry: ``config/scheduled_jobs.json`` (schema_version 1).

One entry per job, whatever host or scheduler runs it. The registry is the single list of
what *should* be running; a job's status file says what *did*. Readers (the watchdog, the
Guild Operate tile, a future exporter) compare the two.

Fields per job
    id, name, host           host is a lowercase token ("mac", "ec2", ...)
    schedule                 {"text": human words, "kind": "daily", "at": "HH:MM", "tz": "Area/City"}
                             or {"text": ..., "kind": "interval", "every_s": N}
    expected_every_s         how often a run should finish
    missed_after_s           no completed run for this long = missed (alert / red)
    stuck_after_s            "running" for this long = stuck (alert / red)
    grace_s                  optional, default 7200: slack added to expected_every_s before "overdue"
    active_from              optional ISO time: the job is expected to have run by active_from +
                             expected_every_s + grace_s; before that, a missing status file is not a fault
    status_file              a plain file name under the jobs status root, e.g. "memory-watch.json"; required when the
                             evidence is a status file (the default), optional otherwise
    owner, runbook           who answers for it; where its runbook lives

Optional fields (FF-19, #537: one master list of every background job on every host)
    healthy                  plain words: what healthy means for this job (on time, last result ok, data fresh)
    retired                  true: kept on the list for the record, never judged; ``retired_on`` and ``retired_why`` say
                             when and why
    status_root              which status folder on the host holds ``status_file``: "jobs" (default, MINIMOI_JOBS_ROOT)
                             or another token the host's watchdog maps to a folder (MINIMOI_JOBS_ROOT_<TOKEN>), e.g.
                             "mc-observations" for MC's GitHub feed, whose status lives inside the portal's volume
    evidence                 how the watchdog sees the job. {"kind": "status_file"} (default; the full contract),
                             {"kind": "mtime", "path": P, "fail_marker": M?}: the job is on time when P was written
                             recently; with fail_marker, a marker in the last lines of P means its last run failed;
                             {"kind": "none", "why": W}: the job writes nothing a watchdog can read yet (reported as
                             "not watched", never as healthy)

Loading never touches a status file and never needs the time-zone database (the portal image
may lack one); ``tests/jobs`` checks the shipped zone names against it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_GRACE_S = 7200
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "scheduled_jobs.json"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_HOST = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.json$")
_AT = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_TZ = re.compile(r"^(UTC|[A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2})$")
_ROOT = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_PATH = re.compile(r"^/[A-Za-z0-9._/@ -]{1,240}$")
_MARKER = re.compile(r"^[A-Za-z0-9 _:.,;=()+\"-]{1,40}$")
EVIDENCE_KINDS = ("status_file", "mtime", "none")


class RegistryError(ValueError):
    """The registry is unusable. The message names the field, never any file content beyond it."""


@dataclass(frozen=True)
class Job:
    id: str
    name: str
    host: str
    schedule: dict
    expected_every_s: int
    missed_after_s: int
    stuck_after_s: int
    status_file: str | None
    owner: str
    runbook: str
    grace_s: int = DEFAULT_GRACE_S
    active_from: datetime | None = None
    healthy: str = ""
    retired: bool = False
    status_root: str = "jobs"
    evidence: dict = field(default_factory=lambda: {"kind": "status_file"})

    @property
    def overdue_after_s(self) -> int:
        """Past this age since the last completed run the job is late (yellow), before it is missed."""
        return self.expected_every_s + self.grace_s


@dataclass(frozen=True)
class Registry:
    jobs: tuple[Job, ...] = field(default_factory=tuple)

    def get(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def for_host(self, host: str) -> tuple[Job, ...]:
        """The jobs this host runs now (retired jobs are left out)."""
        return tuple(j for j in self.jobs if j.host == host and not j.retired)

    def hosts(self) -> tuple[str, ...]:
        return tuple(sorted({j.host for j in self.jobs if not j.retired}))


def _int(raw: dict, key: str, where: str, *, minimum: int = 1) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RegistryError(f"{where}: {key} must be a whole number of seconds >= {minimum}")
    return value


def _text(raw: dict, key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{where}: {key} is required")
    return value.strip()


def parse_time(value: object) -> datetime | None:
    """An ISO time with an offset or Z; a time without one is read as UTC. Anything else: None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _schedule(raw: object, where: str) -> dict:
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}: schedule must be an object")
    text = _text(raw, "text", f"{where} schedule")
    kind = raw.get("kind")
    if kind == "daily":
        if not isinstance(raw.get("at"), str) or not _AT.match(raw["at"]):
            raise RegistryError(f"{where} schedule: at must be HH:MM")
        if not isinstance(raw.get("tz"), str) or not _TZ.match(raw["tz"]):
            raise RegistryError(f"{where} schedule: tz must be a zone name such as America/Chicago")
        return {"text": text, "kind": "daily", "at": raw["at"], "tz": raw["tz"]}
    if kind == "interval":
        return {"text": text, "kind": "interval", "every_s": _int(raw, "every_s", f"{where} schedule")}
    raise RegistryError(f"{where} schedule: kind must be daily or interval")


def _evidence(raw: object, where: str) -> dict:
    if raw is None:
        return {"kind": "status_file"}
    if not isinstance(raw, dict) or raw.get("kind") not in EVIDENCE_KINDS:
        raise RegistryError(f"{where}: evidence kind must be one of {', '.join(EVIDENCE_KINDS)}")
    kind = raw["kind"]
    if kind == "status_file":
        return {"kind": kind}
    if kind == "none":
        return {"kind": kind, "why": _text(raw, "why", f"{where} evidence")}
    path = raw.get("path")
    if not isinstance(path, str) or not _PATH.match(path) or ".." in path:
        raise RegistryError(f"{where} evidence: path must be an absolute path")
    out = {"kind": kind, "path": path}
    if "fail_marker" in raw:
        if not isinstance(raw["fail_marker"], str) or not _MARKER.match(raw["fail_marker"]):
            raise RegistryError(f"{where} evidence: fail_marker must be a short plain word")
        out["fail_marker"] = raw["fail_marker"]
    return out


def parse(doc: object) -> Registry:
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        raise RegistryError("not a scheduled-jobs registry (schema_version 1)")
    rows = doc.get("jobs")
    if not isinstance(rows, list):
        raise RegistryError("jobs must be a list")
    jobs, seen_ids, seen_files = [], set(), set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise RegistryError(f"job {index}: must be an object")
        job_id = raw.get("id")
        if not isinstance(job_id, str) or not _ID.match(job_id):
            raise RegistryError(f"job {index}: id must be lowercase letters, digits and dashes")
        where = f"job {job_id}"
        if job_id in seen_ids:
            raise RegistryError(f"{where}: duplicate id")
        seen_ids.add(job_id)
        host = raw.get("host")
        if not isinstance(host, str) or not _HOST.match(host):
            raise RegistryError(f"{where}: host must be a lowercase token such as mac or ec2")
        evidence = _evidence(raw.get("evidence"), where)
        status_file = raw.get("status_file")
        if status_file is not None or evidence["kind"] == "status_file":
            if not isinstance(status_file, str) or not _FILE.match(status_file) or ".." in status_file:
                raise RegistryError(f"{where}: status_file must be a plain file name ending in .json")
            if (host, status_file) in seen_files:
                raise RegistryError(f"{where}: status_file is used by another job")
            seen_files.add((host, status_file))
        status_root = raw.get("status_root", "jobs")
        if not isinstance(status_root, str) or not _ROOT.match(status_root):
            raise RegistryError(f"{where}: status_root must be a lowercase token")
        retired = raw.get("retired", False)
        if not isinstance(retired, bool):
            raise RegistryError(f"{where}: retired must be true or false")
        healthy = raw.get("healthy", "")
        if not isinstance(healthy, str):
            raise RegistryError(f"{where}: healthy must be text")
        expected = _int(raw, "expected_every_s", where)
        missed = _int(raw, "missed_after_s", where)
        if missed < expected:
            raise RegistryError(f"{where}: missed_after_s must not be shorter than expected_every_s")
        active_raw = raw.get("active_from")
        active = parse_time(active_raw) if active_raw is not None else None
        if active_raw is not None and active is None:
            raise RegistryError(f"{where}: active_from must be an ISO time")
        jobs.append(Job(
            id=job_id, name=_text(raw, "name", where), host=host, schedule=_schedule(raw.get("schedule"), where),
            expected_every_s=expected, missed_after_s=missed, stuck_after_s=_int(raw, "stuck_after_s", where),
            status_file=status_file, owner=_text(raw, "owner", where), runbook=_text(raw, "runbook", where),
            grace_s=_int(raw, "grace_s", where, minimum=0) if "grace_s" in raw else DEFAULT_GRACE_S,
            active_from=active, healthy=healthy.strip(), retired=retired, status_root=status_root,
            evidence=evidence))
    return Registry(tuple(jobs))


def load(path: str | Path | None = None) -> Registry:
    target = Path(path) if path else DEFAULT_PATH
    try:
        doc = json.loads(target.read_text("utf-8"))
    except OSError as exc:
        raise RegistryError("the registry file cannot be read") from exc
    except ValueError as exc:
        raise RegistryError("the registry file is not valid JSON") from exc
    return parse(doc)
