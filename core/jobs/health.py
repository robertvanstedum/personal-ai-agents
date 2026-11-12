"""Health of every job on a host, in one small report MC and Robert can read (FF-19, #537).

Robert, 12 Nov 2026: "If something is failing for a day or two, I expect MC to know. If I ask how are things, it needs to
tell me." The watchdog on each host calls ``host_report``; the daily check calls ``daily_items`` over every host's
report. All of it is deterministic: the judging is ``core.jobs.judge``; this module adds the evidence kinds that are not
a status file, a severity, and how long a job has been off.

    observe(job, roots, now)            -> (status document or None, verdict)
    host_report(registry, host, roots, now, previous=None) -> report dict  (health-<host>.json)
    severity(item)                      -> "fine" | "watch" | "needs action"
    daily_items(reports, now)           -> every non-green item across hosts, worst first
    words(items, ...)                   -> short plain text, problems first, with durations

Severity:
    needs action  red: failed, missed, stuck, never ran, no completed run on record
    watch         yellow late or warn; a clock skew; a host whose watchdog report is stale or missing
    fine          not due yet, a first run in progress

Never any content: a report holds job ids, names from the registry, fixed codes and times only.

Across hosts (FF-19 follow-up): every host's watchdog pushes its report to ``<dest>/health/<host>/health-<host>.json`` in
the Vault bucket (``push``), and production pulls the other hosts' reports into MC's folder hourly (``pull``). Each report
carries its own ``checked_at``, so a host that stops reporting reads "no new watchdog report, for N h", never silence.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from core.jobs import judge, status

REPORT_VERSION = 1
NEEDS_ACTION, WATCH, FINE = "needs action", "watch", "fine"
SEVERITY_ORDER = {NEEDS_ACTION: 0, WATCH: 1, FINE: 2}
RED_CODES = ("failed", "missed", "stuck", "never_ran", "no_success")
WATCH_CODES = ("late", "warn", "skew", "report_stale", "report_missing", "report_unreadable")
TAIL_BYTES = 4096
TAIL_LINES = 5
REPORT_STALE_S = 3 * 3600           # a host's watchdog report older than this is itself a problem (hourly watchdogs)
STALE_AFTER_S = {"mac": 36 * 3600}  # the Mac's watchdog runs daily at 09:00 Chicago, and only while the Mac is awake
SILENT_ACTION_S = 72 * 3600         # no report for this long: needs action (a host or its watchdog is down)
HOW = {"failed": "last run failed", "missed": "no completed run in time", "stuck": "stuck, still running",
       "never_ran": "never ran", "no_success": "no completed run on record", "late": "late",
       "warn": "last run finished with warnings", "skew": "status time is in the future (clock skew)",
       "not_due": "not due yet", "first_run": "first run in progress", "not_tracked": "not watched yet",
       "report_stale": "no new watchdog report", "report_missing": "no watchdog report has arrived",
       "report_unreadable": "watchdog report unreadable", "ok": "ok"}


def report_name(host: str) -> str:
    return f"health-{host}.json"


def root_for(job, roots: dict) -> Path | None:
    """The folder that holds ``job``'s status file on this host, or None when the host has not mapped its root."""
    value = roots.get(job.status_root)
    return Path(value) if value else None


def _tail_has(path: Path, marker: str) -> bool:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - TAIL_BYTES))
        tail = f.read().decode("utf-8", "replace")
    lines = [line for line in tail.splitlines() if line.strip()][-TAIL_LINES:]
    return any(marker in line for line in lines)


def observe(job, roots: dict, now: datetime):
    """``(doc, verdict)``. ``doc`` is the job's status document (or one built from a file's write time), or None."""
    kind = job.evidence.get("kind", "status_file")
    if kind == "none":
        return None, judge.Verdict("unknown", "not_tracked", "none")
    if kind == "mtime":
        path = Path(job.evidence["path"])
        try:
            written = datetime.fromtimestamp(path.stat().st_mtime, now.tzinfo)
            failed = bool(job.evidence.get("fail_marker")) and _tail_has(path, job.evidence["fail_marker"])
        except OSError:
            return None, judge.judge(job, None, now)
        moment = status.iso(written)
        doc = {"run_id": "mtime", "state": "failed" if failed else "ok", "started_at": moment, "finished_at": moment,
               "last_success_at": None if failed else moment}
        return doc, judge.judge(job, doc, now)
    root = root_for(job, roots)
    doc = status.read_status(root, job.status_file, job.id) if root is not None else None
    return doc, judge.judge(job, doc, now)


def _off_since(code: str, doc: dict | None, job, prior: dict | None, now: datetime) -> str | None:
    """When the job stopped being green. Failing jobs: since their last success (so "failing 2 days" means 2 days
    without a good run). Other conditions: carried forward from the previous report, else now."""
    if code in ("ok", "not_tracked"):
        return None
    if prior and prior.get("off_since") and prior.get("code") != "ok":
        carried = prior["off_since"]
    else:
        carried = None
    if code in RED_CODES + ("late",):
        success = status.parse_time((doc or {}).get("last_success_at"))
        if success is not None:
            return status.iso(success)
        if job.active_from is not None and job.active_from <= now and not carried:
            return status.iso(job.active_from)
    return carried or status.iso(now)


def host_report(reg, host: str, roots: dict, now: datetime, previous: dict | None = None,
                exclude: tuple = ()) -> dict:
    """Judge every active job of ``host``. ``previous`` is the last report (for carried-forward durations)."""
    prior_items = {i.get("id"): i for i in (previous or {}).get("items", []) if isinstance(i, dict)}
    items = []
    for job in reg.for_host(host):
        if job.id in exclude:
            continue
        doc, verdict = observe(job, roots, now)
        since = _off_since(verdict.code, doc, job, prior_items.get(job.id), now)
        items.append({"id": job.id, "name": job.name, "host": host, "light": verdict.state, "code": verdict.code,
                      "evidence": job.evidence.get("kind", "status_file"),
                      "last_success_at": (doc or {}).get("last_success_at"), "off_since": since,
                      "summary": (doc or {}).get("summary", "") if job.evidence.get("kind") == "status_file" else ""})
    return {"schema_version": REPORT_VERSION, "host": host, "checked_at": status.iso(now),
            "worst": judge.worst(i["light"] for i in items if i["code"] != "not_tracked"),
            "counts": _counts(items), "items": items}


def _counts(items) -> dict:
    out = {"green": 0, "yellow": 0, "red": 0, "unknown": 0, "not_tracked": 0}
    for i in items:
        out["not_tracked" if i["code"] == "not_tracked" else i["light"]] += 1
    return out


def severity(item: dict) -> str:
    code = item.get("code")
    if code in RED_CODES or item.get("light") == "red":
        return NEEDS_ACTION
    if code == "report_stale" and (item.get("off_for_s") or 0) > SILENT_ACTION_S:
        return NEEDS_ACTION
    if code in WATCH_CODES or item.get("light") == "yellow":
        return WATCH
    return FINE


def off_for_s(item: dict, now: datetime) -> int | None:
    since = status.parse_time(item.get("off_since"))
    return None if since is None else max(0, int((now - since).total_seconds()))


def duration(seconds: int | None) -> str:
    """"2 days", "40 h", "40 min"; None -> "" (unknown). Hours up to two days, so a day and a half reads "36 h"."""
    if seconds is None:
        return ""
    if seconds >= 2 * 86400:
        return f"{seconds // 86400} days"
    if seconds >= 3600:
        return f"{seconds // 3600} h"
    return f"{max(1, seconds // 60)} min"


def read_report(folder, host: str) -> dict | None:
    """A host's report, or None when missing, unreadable or not a report of this host."""
    import json
    try:
        doc = json.loads((Path(folder) / report_name(host)).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("schema_version") != REPORT_VERSION or doc.get("host") != host \
            or not isinstance(doc.get("items"), list) or status.parse_time(doc.get("checked_at")) is None:
        return None
    return doc


def daily_items(reports: dict, now: datetime, expected_hosts=()) -> list[dict]:
    """Every non-green item across ``reports`` ({host: report or None}), each with severity and how long it has been
    off, worst first. A host whose report is missing or stale is itself an item: silence is never health."""
    out = []
    for host in sorted(set(reports) | set(expected_hosts)):
        report = reports.get(host)
        if report is None:
            out.append({"id": f"watchdog-{host}", "name": f"Watchdog report from {host}", "host": host,
                        "light": "unknown", "code": "report_missing", "off_since": None})
            continue
        age = (now - status.parse_time(report["checked_at"])).total_seconds()
        if age > STALE_AFTER_S.get(host, REPORT_STALE_S):
            out.append({"id": f"watchdog-{host}", "name": f"Watchdog report from {host}", "host": host,
                        "light": "unknown", "code": "report_stale", "off_since": report["checked_at"]})
        for item in report["items"]:
            if item.get("code") in ("ok", "not_tracked"):
                continue
            out.append(dict(item))
    for item in out:
        item["off_for_s"] = off_for_s(item, now)
        item["off_for"] = duration(item["off_for_s"])
        item["severity"] = severity(item)
    out.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], -(i["off_for_s"] or 0), i["host"], i["id"]))
    return out


def untracked(reports: dict) -> list[dict]:
    return [i for r in reports.values() if r for i in r["items"] if i.get("code") == "not_tracked"]


def line(item: dict) -> str:
    how = HOW.get(item.get("code"), item.get("code", ""))
    if item.get("code") in RED_CODES and item.get("off_for"):
        how = f"{how}, not working for {item['off_for']}"
    elif item.get("off_for"):
        how = f"{how}, for {item['off_for']}"
    return f"[{item['severity']}] {item['name']} ({item['host']}): {how}"


def words(items: list[dict], *, hosts=(), not_tracked: int = 0, limit: int = 12) -> str:
    """Problems first, short, with durations. All green: one line."""
    if not items:
        text = f"All watched jobs are green on {', '.join(hosts) or 'the hosts reporting'}."
    else:
        counts = {s: sum(1 for i in items if i["severity"] == s) for s in (NEEDS_ACTION, WATCH, FINE)}
        head = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
        rows = [line(i) for i in items[:limit]]
        if len(items) > limit:
            rows.append(f"... and {len(items) - limit} more")
        text = f"{head}.\n" + "\n".join(rows)
    if not_tracked:
        text += f"\n{not_tracked} listed jobs write nothing a watchdog can read yet (not watched)."
    return text


def remote_key(host: str) -> str:
    return f"health/{host}/{report_name(host)}"


def _aws(run, args, timeout=60):
    import subprocess
    try:
        return run(["aws", *args, "--only-show-errors"], capture_output=True, text=True, timeout=timeout).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def push(path, dest: str, host: str, *, run=None, dest_factory=None) -> str:
    """``"ok"`` or ``"failed"``: this host's report to ``<dest>/health/<host>/``. On the Mac the pinned Vault principal
    writes it (core.memory_backup.backup.Dest: identity proven first, SSE); on EC2 and Dev, the instance role through the
    aws CLI. Never raises; nothing about the failure but the word."""
    import subprocess
    if not dest.startswith(("s3://", "file://")):
        return "failed"
    try:
        if dest_factory is None:
            try:
                from core.memory_backup import backup as B                   # the Mac's runtime pin carries it
                dest_factory = B.Dest
            except ImportError:
                dest_factory = None
        if dest_factory is not None:
            dest_factory(dest).put(Path(path), remote_key(host), replace=True)
            return "ok"
        if dest.startswith("file://"):
            return "failed"
        ok = _aws(run or subprocess.run, ["s3", "cp", str(path), f"{dest.rstrip('/')}/{remote_key(host)}", "--sse", "AES256"])
        return "ok" if ok else "failed"
    except Exception:                                   # noqa: BLE001
        return "failed"


def pull(dest: str, host: str, folder, now: datetime, *, run=None, fetch=None) -> str:
    """Fetch ``host``'s report from ``<dest>/health/<host>/`` into ``folder``: ``"ok"``, ``"missing"`` (nothing there
    yet, or not readable) or ``"invalid"`` (not a report of that host, or dated in the future). The previous copy stays
    when a fetch fails, so its own ``checked_at`` shows how old it is."""
    import json
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / report_name(host)
        if fetch is not None:
            got = fetch(remote_key(host), local)
        else:
            got = _aws(run or subprocess.run, ["s3", "cp", f"{dest.rstrip('/')}/{remote_key(host)}", str(local)])
        if not got or not local.is_file():
            return "missing"
        doc = read_report(tmp, host)
        if doc is None or status.parse_time(doc["checked_at"]) > now + _skew():
            return "invalid"
        publish(folder, report_name(host), doc)
        return "ok"


def _skew():
    from datetime import timedelta
    return timedelta(seconds=judge.SKEW_S)


def publish(folder, name: str, obj: object) -> Path:
    """Write ``obj`` where another reader (the portal, as its own user) can read it: atomic, the file 0644, owned like
    the folder it lands in. Only reports go here: job ids, registry names, fixed codes and times, never content."""
    import json
    target = Path(folder)
    target.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = target / f".{name}.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(obj, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        st = target.stat()
        os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, target / name)
    return target / name
