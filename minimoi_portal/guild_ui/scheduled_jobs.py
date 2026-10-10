"""Read-only Operate view of the shared scheduled-jobs contract (#292)."""
from datetime import datetime, timezone
import os
from pathlib import Path
from core.jobs import registry, status, judge
from .lights import make
from .adapters.contract import SourceResult


def overview(root=None, *, registry_path=None, host="mac", now=None):
    now = now or datetime.now(timezone.utc)
    root = root if root is not None else os.environ.get("MINIMOI_JOBS_DIR")
    observed = now.isoformat()
    def light(state, reason):
        return make(state, reason, SourceResult("live", "ok" if state != "unknown" else "unknown", observed_at=observed))
    if not root:
        return {"light": make("unknown", "jobs status not connected", None), "jobs": []}
    try:
        folder = Path(root)
        # Force a directory read: permissions/missing mount are not 'never ran'.
        list(folder.iterdir())
        jobs = [j for j in registry.load(registry_path).jobs if j.host == host]
    except (OSError, registry.RegistryError):
        return {"light": light("unknown", "jobs status unavailable"), "jobs": []}
    rows = []
    for job in jobs:
        doc = None
        try:
            path = folder / job.status_file
            if path.is_symlink():
                raise OSError("unsafe status")
            try:
                path.stat()
            except FileNotFoundError:
                verdict = judge.judge(job, None, now)
            else:
                doc = status.read_status(folder, job.status_file, job.id)
                if doc is None or doc.get("host") != host:
                    raise OSError("invalid status")
                verdict = judge.judge(job, doc, now)
            state, reason = verdict.state, verdict.code.replace("_", " ")
        except (OSError, ValueError, TypeError):
            doc = None
            state, reason = "unknown", "status unreadable"
        rows.append({"name": job.name, "host": job.host, "light": light(state, reason),
                     "finished": (doc or {}).get("finished_at"),
                     "next_due": (doc or {}).get("next_due_by"),
                     "results": (doc or {}).get("results") or {}})
    state = judge.worst(row["light"]["state"] for row in rows)
    reason = f"{len(rows)} registered jobs" if rows else "no jobs registered for this host"
    return {"light": light(state, reason), "jobs": rows}
