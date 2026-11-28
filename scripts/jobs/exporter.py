#!/usr/bin/env python3
"""The trap exporter (WS3 C1.1): job health and progress as Prometheus text, for a standard monitoring agent to read.

    python3 scripts/jobs/exporter.py            # one snapshot, then exit (run every minute by cron)

It reads the registry, every host's ``health-<host>.json`` in ``MINIMOI_JOBS_HEALTH_DIR`` and this host's status files,
and writes one snapshot atomically (temp file + rename) to ``MINIMOI_METRICS_FILE`` (default
``/var/lib/minimoi-metrics/minimoi.prom``). That is the node_exporter textfile convention, read by Grafana Alloy's
``prometheus.exporter.unix`` textfile collector (option A) or node_exporter (option B). Option C reads the same text
through its own Prometheus scrape, which needs an endpoint; that adapter is added only if C is chosen.

**Lifecycle (Codex build acceptance item 6):** a bounded one-shot run per minute, no listener and no daemon. Every
snapshot carries ``minimoi_exporter_snapshot_timestamp_seconds``; a rule on its age catches an exporter that stopped,
so a stale file can never pass for a healthy one.

**Privacy (plan v0.2 §6.4, Codex finding 4):** every label value must appear in ``config/metrics_public_aliases.json``
(hosts, job ids, codes, phases, refusal reasons, threshold kinds). Anything else is dropped and counted in the
``minimoi_exporter_dropped`` gauge (this snapshot's count, not a persisted counter).

**Invalid data (Codex exporter review):** a report entry that is not an object, has no string id, or repeats an
identity (host, job) already published is skipped and counted in ``minimoi_exporter_invalid``; one bad entry never
stops the snapshot, and no metric identity is ever emitted twice. No summaries, paths, run ids, record ids or free text are ever published; values
are numbers only. Standard library only; the hosts run python3 -I.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from core.jobs import health, registry, status  # noqa: E402

ALIASES_PATH = REPO / "config" / "metrics_public_aliases.json"
DEFAULT_OUT = "/var/lib/minimoi-metrics/minimoi.prom"
LIGHT = {"green": 0, "yellow": 1, "red": 2, "unknown": 3}

HELP = {
    "minimoi_exporter_snapshot_timestamp_seconds": "When this snapshot was written (alert on its age).",
    "minimoi_exporter_dropped": "Samples left out of this snapshot: a label value not in the public alias table.",
    "minimoi_exporter_invalid": "Report entries left out of this snapshot: malformed, or a duplicate identity.",
    "minimoi_health_report_timestamp_seconds": "checked_at of each host's watchdog report.",
    "minimoi_job_light": "The judge's light: 0 green, 1 yellow, 2 red, 3 unknown.",
    "minimoi_job_condition": "1 for the job's current code.",
    "minimoi_job_last_success_timestamp_seconds": "The job's last completed run.",
    "minimoi_job_off_since_timestamp_seconds": "When the job stopped being green.",
    "minimoi_job_work_due": "Work waiting (1 means some, count unknown).",
    "minimoi_job_last_progress_timestamp_seconds": "When the job last made progress.",
    "minimoi_job_phase": "1 for the job's reported phase.",
    "minimoi_job_last_refusal_timestamp_seconds": "The last run refused at its lock.",
    "minimoi_job_threshold_seconds": "The registry's thresholds, so rules and the registry cannot drift.",
}


class Aliases:
    def __init__(self, doc: dict):
        self.allowed = {k: frozenset(doc.get(k) or ()) for k in
                        ("hosts", "job_ids", "codes", "phases", "refusal_reasons", "threshold_kinds")}

    @classmethod
    def load(cls, path=ALIASES_PATH) -> "Aliases":
        return cls(json.loads(Path(path).read_text("utf-8")))

    def ok(self, kind: str, value) -> bool:
        return isinstance(value, str) and value in self.allowed[kind]


class Snapshot:
    def __init__(self, aliases: Aliases):
        self.aliases, self.rows, self.dropped, self.invalid, self.seen = aliases, [], 0, 0, set()

    def add(self, name: str, value, **labels) -> None:
        kinds = {"host": "hosts", "job_id": "job_ids", "code": "codes", "phase": "phases", "reason": "refusal_reasons",
                 "kind": "threshold_kinds"}
        for key, val in labels.items():
            if key not in kinds or not self.aliases.ok(kinds[key], val):
                self.dropped += 1
                return
        if value is None:
            return                                          # nothing observed: no sample (absence is itself alertable)
        identity = (name, tuple(sorted(labels.items())))
        if identity in self.seen:
            self.invalid += 1                               # a duplicate identity is never emitted twice
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.dropped += 1
            return
        self.seen.add(identity)
        self.rows.append((name, identity[1], float(value)))

    def text(self) -> str:
        out, seen = [], set()
        for name, labels, value in sorted(self.rows):
            if name not in seen:
                seen.add(name)
                out.append(f"# HELP {name} {HELP[name]}")
                out.append(f"# TYPE {name} gauge")
            lab = ",".join(f'{k}="{v}"' for k, v in labels)
            out.append(f"{name}{{{lab}}} {_num(value)}" if lab else f"{name} {_num(value)}")
        return "\n".join(out) + "\n"


def _num(v: float) -> str:
    return str(int(v)) if v == int(v) else repr(v)


def _ts(value) -> float | None:
    moment = status.parse_time(value)
    return moment.timestamp() if moment else None


def build(reg, health_dir, roots: dict, host: str, aliases: Aliases, now: datetime) -> str:
    snap = Snapshot(aliases)
    for h in sorted(set(reg.hosts()) - {"github"}):
        report = health.read_report(health_dir, h)
        if report is None:
            continue
        snap.add("minimoi_health_report_timestamp_seconds", _ts(report["checked_at"]), host=h)
        published = set()
        for item in report["items"]:
            if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                    or not isinstance(item.get("light"), str) or item["light"] not in LIGHT
                    or not isinstance(item.get("code"), str)
                    or any(item.get(k) is not None and not isinstance(item.get(k), str)
                           for k in ("last_success_at", "off_since"))):
                snap.invalid += 1                           # field types checked before any use: one bad entry is skipped
                continue
            jid = item["id"]
            if jid in published:
                snap.invalid += 1                           # the first entry for a job wins; later ones are skipped
                continue
            published.add(jid)
            snap.add("minimoi_job_light", LIGHT[item["light"]], job_id=jid, host=h)
            snap.add("minimoi_job_condition", 1, job_id=jid, host=h, code=item.get("code"))
            for metric, key in (("minimoi_job_last_success_timestamp_seconds", "last_success_at"),
                                ("minimoi_job_off_since_timestamp_seconds", "off_since")):
                ts = _ts(item.get(key))
                if ts is not None:
                    snap.add(metric, ts, job_id=jid, host=h)
    for job in reg.for_host(host):
        if job.evidence.get("kind", "status_file") != "status_file" or not job.status_file:
            continue
        root = roots.get(job.status_root) or roots.get("jobs")
        doc = status.read_status(root, job.status_file, job.id) if root else None
        for kind in ("expected_every_s", "missed_after_s", "stuck_after_s", "overdue_after_s", "idle_allowed_s"):
            value = getattr(job, kind)
            if value:
                snap.add("minimoi_job_threshold_seconds", value, job_id=job.id, kind=kind)
        # The refusal sidecar is its own observation: read it whether or not the status file is readable.
        refusal = (status.read_refusal(root, job) if root else None) or (doc or {})
        ts = _ts(refusal.get("last_refusal_at"))
        if ts is not None:
            snap.add("minimoi_job_last_refusal_timestamp_seconds", ts, job_id=job.id, host=host,
                     reason=refusal.get("last_refusal_reason"))
        if doc is None:
            continue
        progress = doc.get("progress")
        if isinstance(progress, dict):
            snap.add("minimoi_job_work_due", progress.get("work_due"), job_id=job.id, host=host)
            ts = _ts(progress.get("last_progress_at"))
            if ts is not None:
                snap.add("minimoi_job_last_progress_timestamp_seconds", ts, job_id=job.id, host=host)
            snap.add("minimoi_job_phase", 1, job_id=job.id, host=host, phase=progress.get("phase"))
    snap.add("minimoi_exporter_snapshot_timestamp_seconds", now.timestamp())
    snap.rows.append(("minimoi_exporter_dropped", (), float(snap.dropped)))
    snap.rows.append(("minimoi_exporter_invalid", (), float(snap.invalid)))
    return snap.text()


def publish(text: str, out_path: str | Path) -> Path:
    """Atomic: write a temp file beside the target, then rename, so a reader never sees half a snapshot."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(text, "utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return path


def run(*, environ=None, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc), err=sys.stderr) -> int:
    env = os.environ if environ is None else environ
    try:
        reg = registry.load(env.get("MINIMOI_JOBS_REGISTRY") or None)
        aliases = Aliases.load(env.get("MINIMOI_METRICS_ALIASES") or ALIASES_PATH)
    except Exception as exc:                                    # noqa: BLE001
        print(f"exporter: cannot load the registry or aliases ({type(exc).__name__})", file=err)
        return 2
    roots = {"jobs": env.get("MINIMOI_JOBS_ROOT", "")}
    for key, value in env.items():
        if key.startswith("MINIMOI_JOBS_ROOT_") and value:
            roots[key[len("MINIMOI_JOBS_ROOT_"):].lower().replace("_", "-")] = value
    text = build(reg, env.get("MINIMOI_JOBS_HEALTH_DIR", ""), roots, env.get("MINIMOI_JOBS_HOST", "ec2"), aliases, now())
    try:
        publish(text, env.get("MINIMOI_METRICS_FILE") or DEFAULT_OUT)
    except OSError as exc:
        print(f"exporter: cannot write the snapshot ({type(exc).__name__})", file=err)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
