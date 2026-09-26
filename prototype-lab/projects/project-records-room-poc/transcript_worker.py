"""Opt-in local publication worker. No model calls, network or global install.

The running worker coalesces changes between scans into observed snapshots.
It catches up on startup; no sleeping-laptop freshness SLA is claimed.
"""
from datetime import datetime, timezone
import json
import math
import threading
import time

from transcript_publish import publish, recover, cleanup_scratch, verify, _locked, _write, _sync_dir


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00","Z")


def _status(store, report):
    # Shares publisher lock; status is a current projection, not evidence of
    # delivery or an independent outage monitor on an offline machine.
    import os
    from uuid import uuid4
    with _locked(store) as root:
        temporary=root/(".worker-status-"+str(uuid4()))
        _write(temporary,(json.dumps(report,indent=2,sort_keys=True)+"\n").encode())
        os.replace(temporary,root/"worker-status.json")
        _sync_dir(root)


def cycle(store, actor):
    store.owner(actor)
    report=dict(started_at=_now(),finished_at=None,state="running",published=[],failures=[],
                recovered=[],cleanup=None,network_used=False,model_calls=0)
    # Publish attempts use a durable idempotent journal. Failure of one session
    # must not prevent independent sessions becoming scannable.
    try: report["recovered"]=[str(p) for p in recover(store,actor)]
    except Exception:
        report["failures"].append(dict(stage="recovery",code="recovery_failed"))
    with store.connect() as db:
        sessions=[r[0] for r in db.execute("SELECT id FROM rooms ORDER BY id")]
    for session in sessions:
        try:
            path=publish(store,actor,session)
            manifest=verify(path)
            report["published"].append(dict(session_id=session,bundle=str(path),
                snapshot_at=manifest["snapshot_at"],source_revision=manifest["source_revision"],
                publication_status=manifest["publication_status"]))
        except Exception:
            # Never expose arbitrary filesystem/provider/record text in errors.
            report["failures"].append(dict(stage="publish",session_id=session,code="publication_failed"))
    try: report["cleanup"]=cleanup_scratch(store,actor)
    except Exception:
        report["failures"].append(dict(stage="cleanup",code="cleanup_failed"))
    report["state"]="degraded" if report["failures"] else "healthy"
    report["finished_at"]=_now()
    _status(store,report)
    return report


def run(store, actor, *, interval=5.0, duration=3600.0, stop_event=None,
        monotonic=time.monotonic, tick=cycle, on_cycle=None):
    """Bounded opt-in loop; return on stop or deadline, no unbounded background job."""
    store.owner(actor)
    if (not math.isfinite(interval) or not math.isfinite(duration)
            or not 0.1<=interval<=3600 or not 0<duration<=86400):
        raise ValueError("Worker requires finite interval0.1–3600s and duration>0–86400s")
    event=stop_event or threading.Event()
    deadline=monotonic()+duration
    count=0
    result=None
    while not event.is_set() and monotonic()<deadline:
        result=tick(store,actor)
        count+=1
        if on_cycle: on_cycle(result)
        remaining=deadline-monotonic()
        if remaining>0: event.wait(min(interval,remaining))
    # A running cycle is allowed to finish transactionally; duration is not a
    # hard kill deadline. No subsequent cycle starts after the deadline.
    _status(store,dict(state="stopped",finished_at=_now(),cycles=count,
                       last_cycle=result,
                       reason="stop_requested" if event.is_set() else "duration_elapsed"))
    return count
