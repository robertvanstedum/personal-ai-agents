"""The focused Workshop screen's data (4a: read only, model free).

Everything comes from files and the queue: the workshop's state.json (synced
from the Mac by scripts/workshop/workshop.py), the Build Queue, and the usage
store. Nothing here calls a model, and nothing launches: the launcher is 4b.

Honest states: a missing, unreadable or stale host reading is "unknown", and
its runs are "unknown", never "nothing running".
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ..workshop.observer import LABELS, SESSION_KINDS, limits_text
from ..workshop.record import STALE_AFTER, Workshop, load_state, now, parse
from .adapters import ACTIVE, by_recent
from .needs import needs_you

APPROVED = ("spec_ready", "in_build")


def workshop_dir() -> str | None:
    return os.environ.get("MINIMOI_WORKSHOPS_DIR") or None


def workshop_id() -> str:
    return os.environ.get("MINIMOI_WORKSHOP_ID") or "mac"


def _age(value) -> str | None:
    d = parse(value) if value else None
    if d is None:
        return None
    mins = int((now() - d).total_seconds() // 60)
    return "just now" if mins < 1 else f"{mins} min ago" if mins < 90 else f"{mins // 60} h ago"


def admission(state: dict | None, status: str) -> dict:
    """ok | tight | blocked | unknown, with the reason and the reading's age."""
    host = (state or {}).get("host") or {}
    if status == "missing":
        return {"verdict": "unknown", "reasons": ["no workshop record synced yet (workshop.py observe, then sync)"],
                "observed_at": None, "age": None}
    if status == "unreadable":
        return {"verdict": "unknown", "reasons": ["the workshop record could not be read"], "observed_at": None, "age": None}
    if not host:
        return {"verdict": "unknown", "reasons": ["no host reading yet (workshop.py observe)"], "observed_at": None, "age": None}
    seen = host.get("observed_at") or host.get("at")
    if status == "stale":
        return {"verdict": "unknown",
                "reasons": [f"the last host reading is {_age(seen)} old (over {int(STALE_AFTER.total_seconds() // 60)} min): "
                            f"it said {host.get('verdict', 'unknown')}"],
                "observed_at": seen, "age": _age(seen)}
    return {"verdict": host.get("verdict") or "unknown", "reasons": host.get("reasons") or [],
            "observed_at": seen, "age": _age(seen)}


WHERE = "on this Mac (outside Docker)"


def observed_runs(state: dict | None, status: str) -> dict:
    """Agent sessions on the Mac, and background agent apps, labelled. The
    staging containers' agents run in the Colima VM, which the Mac's ps cannot
    see: the text says where it looked."""
    host = (state or {}).get("host") or {}
    if status != "ok" or not host or not host.get("clients_known"):
        return {"known": False, "runs": [], "background": [], "sessions": None,
                "text": f"Running agents {WHERE} unknown (no fresh host reading)"}
    rows = [{**r, "label": LABELS.get(r.get("kind"), r.get("kind")),
             "counted": r.get("counted", r.get("kind") in SESSION_KINDS)} for r in host.get("runs") or []]
    counts = host.get("clients") or {}
    sessions = host.get("sessions")
    if sessions is None:
        sessions = sum(n for k, n in counts.items() if k in SESSION_KINDS)
    runs = [r for r in rows if r["counted"]]
    background = [r for r in rows if not r["counted"]]
    text = (f"No agent session running {WHERE}" if not sessions
            else f"{sessions} agent session{'s' if sessions != 1 else ''} running {WHERE}")
    return {"known": True, "runs": runs, "background": background, "sessions": sessions, "text": text}


# ── The ops strip (Guild 1.1 slice 5, spec §7) ───────────────────────────────
# Each figure says where it came from and how fresh it is, or "not measured".
# No new metrics: these are the observer's own readings (synced from the Mac)
# and the usage store's gateway records, nothing more.
HOST_SOURCE = "host reading on the Mac (workshop.py observe, then sync)"
OPS = (
    # (id, label, host key, unit)
    ("memory", "Memory free", "memory_free_pct", "%"),
    ("swap", "Swap used", "swap_used_gb", " GB"),
    ("disk", "Disk free", "disk_free_gb", " GB"),
    ("load", "Load (1 min)", "load_1m", ""),
)


def _fresh(at) -> dict:
    """When a figure was taken: the ISO time (the page shows it in local time) and its age."""
    return {"at": at, "age": _age(at)} if at and parse(at) else {"at": None, "age": None}


def ops_strip(state: dict | None, status: str, budget: dict) -> list[dict]:
    """The small ops strip: memory, swap, disk and load from the host reading,
    and this month's gateway spend. state is measured, stale, unknown or
    not_measured; value is None unless measured or stale."""
    host = (state or {}).get("host") or {}
    seen = host.get("observed_at") or host.get("at")
    rows = []
    for key, label, field, unit in OPS:
        row = {"id": key, "label": label, "source": HOST_SOURCE, "fresh": _fresh(seen), "value": None}
        if status in ("missing", "unreadable") or not host:
            row.update(state="not_measured", text=f"{label} · not measured", source="no host reading synced",
                       fresh=_fresh(None))
        elif host.get(field) is None:
            row.update(state="unknown", text=f"{label} · unknown (the probe failed)")
        else:
            value = f"{host[field]}{unit}"
            row.update(value=value, state="stale" if status == "stale" else "measured",
                       text=f"{label} {value}" + (" · stale" if status == "stale" else ""))
        rows.append(row)
    if budget.get("known"):
        spent = sum((budget.get("by_actor") or {}).values())
        rows.append({"id": "spend", "label": "Model spend", "value": f"${spent:.2f}", "state": "measured",
                     "text": f"Model spend ${spent:.2f} in {budget.get('month')}",
                     "source": "usage store, gateway records", "fresh": _fresh(budget.get("as_of"))})
    else:
        rows.append({"id": "spend", "label": "Model spend", "value": None, "state": "not_measured",
                     "text": "Model spend · not measured", "source": "no usage store here", "fresh": _fresh(None)})
    rows.append({"id": "production", "label": "Production host", "value": None, "state": "not_measured",
                 "text": "Production host · not measured", "source": "no source yet", "fresh": _fresh(None)})
    return rows


# ── Job states from the workshop record (spec §7) ─────────────────────────────
JOB_STATE = {"started": "running", "progress": "running", "needs_you": "needs you", "decision": "running",
             "blocked": "blocked", "review": "in review", "done": "completed", "next": "queued", "health": "running"}
EVIDENCE_LINES = 5


def jobs(root: str | None, wid: str, state: dict | None, status: str) -> dict:
    """One light card per work item in the workshop record (newest first):
    its state from the latest event, who reported it and when, and the
    item's last few events as evidence. Read only; unknown when the record
    can't be read, marked stale when the host reading is stale."""
    if status in ("missing", "unreadable") or not state:
        return {"known": False, "stale": False, "cards": [],
                "text": "Job states unknown (no workshop record could be read)."}
    events = []
    try:
        if root:
            events = Workshop(root, wid).events()
    except (OSError, ValueError):
        events = []
    by_item: dict[str, list[dict]] = {}
    for ev in sorted(events, key=lambda e: e.get("at") or ""):
        if not str(ev.get("item", "")).startswith("host:"):
            by_item.setdefault(ev["item"], []).append(ev)
    items = {k: v for k, v in ((state.get("items") or {}).items()) if not k.startswith("host:")}
    cards = []
    for key, last in sorted(items.items(), key=lambda kv: kv[1].get("at") or "", reverse=True):
        evs = by_item.get(key) or [last]
        kind, ref = key.split(":", 1) if ":" in key else (key, "")
        cards.append({
            "item": key, "kind": kind, "ref": ref, "queue_id": int(ref) if kind == "queue" and ref.isdigit() else None,
            "state": JOB_STATE.get(last.get("kind"), last.get("kind") or "unknown"),
            "title": last.get("text") or key, "actor": last.get("actor"), "stage": last.get("stage"),
            "next_actor": last.get("next_actor"), "last_contact": _fresh(last.get("at")),
            "evidence": [{"at": e.get("at"), "actor": e.get("actor"), "kind": e.get("kind"), "text": e.get("text")}
                         for e in evs[-EVIDENCE_LINES:]],
        })
    return {"known": True, "stale": status == "stale", "cards": cards,
            "text": None if cards else "No jobs in the workshop record yet."}


def usage_this_month(folder: str | None) -> dict:
    """Gateway spend this month by actor, from the usage store; None when unknown."""
    if not folder or not os.path.isdir(folder):
        return {"known": False}
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    path = os.path.join(folder, f"usage-{month}.jsonl")
    spent: dict[str, float] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("emitter") == "gateway" and isinstance(r.get("cost_usd"), (int, float)):
                    spent[r.get("actor") or "unknown"] = spent.get(r.get("actor") or "unknown", 0.0) + r["cost_usd"]
    except FileNotFoundError:
        return {"known": True, "month": month, "by_actor": {}}
    except OSError:
        return {"known": False}
    try:
        as_of = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat(timespec="seconds")
    except OSError:
        as_of = None
    return {"known": True, "month": month, "by_actor": {k: round(v, 4) for k, v in sorted(spent.items())},
            "as_of": as_of}


RECOVERY = {
    "blocked": ["Let a running agent client finish, or close one you are not using, then observe again.",
                "Free disk on the Mac (for example docker builder prune -f, then colima ssh -- sudo fstrim -av)."],
    "tight": ["A new run can start but would share the Mac: prefer one at a time."],
    "unknown": ["On the Mac: scripts/workshop/workshop.py observe, then sync. A reading older than 15 minutes counts as unknown."],
    "ok": [],
}


def view(services, item_id: int | None, *, usage_dir: str | None = None) -> dict:
    state, status = load_state(workshop_dir(), workshop_id())
    adm = admission(state, status)
    queue_res = services.queue.list_items()
    checks = services.queue.checks()
    items = queue_res.data or []
    item = next((i for i in items if i.get("id") == item_id), None) if item_id else None
    key = f"queue:{item_id}" if item_id else None
    item_event = ((state or {}).get("items") or {}).get(key) if key else None
    needs = needs_you(queue_res, checks, cap=100)
    item_needs = [n for n in needs.get("items", []) if item_id and n.get("item_id") == item_id]
    ws_needs = [n for n in (state or {}).get("needs_you", []) if not key or n.get("item") == key]
    # The workshop's own needs are only as good as its record (never "nothing" when unknown).
    ws_needs_status = {"ok": "ok", "stale": "stale"}.get(status, "unknown")
    ws_needs_text = {"ok": None,
                     "stale": "Workshop needs may be out of date (no fresh workshop record).",
                     "unknown": "Workshop needs unknown (no workshop record could be read)."}[ws_needs_status]
    queued = sorted([i for i in items if i.get("status_known") and i.get("status") == "spec_ready" and i.get("id") != item_id],
                    key=by_recent, reverse=True)[:5]
    budget = usage_this_month(usage_dir or os.environ.get("MINIMOI_USAGE_DIR"))
    return {
        "ops": ops_strip(state, status, budget),
        "jobs": jobs(workshop_dir(), workshop_id(), state, status),
        "workshop": workshop_id(), "record_status": status, "state": state,
        "admission": adm, "runs": observed_runs(state, status),
        "last_event": item_event or (state or {}).get("last_event"),
        "last_event_scope": "this item" if item_event else "the workshop",
        "next_actor": (item_event or {}).get("next_actor"),
        "item": item, "item_res": queue_res, "approved": bool(item and item.get("status") in APPROVED),
        "needs": {"status": needs.get("status"), "text": needs.get("text"), "items": item_needs, "workshop": ws_needs,
                  "workshop_status": ws_needs_status, "workshop_text": ws_needs_text},
        "headroom": headroom(state, status), "limits": limits_text(),
        "queued": queued, "queued_next": (state or {}).get("next", [])[:5],
        "budget": budget,
        "recovery": RECOVERY.get(adm["verdict"], RECOVERY["unknown"]),
        "active_statuses": ACTIVE,
    }


def headroom(state: dict | None, status: str) -> str | None:
    """The host's numbers from a fresh reading, or None (then it says unknown)."""
    h = (state or {}).get("host") or {}
    if status != "ok" or not h:
        return None
    val = lambda k, unit: f"{h[k]}{unit}" if h.get(k) is not None else "unknown"  # noqa: E731
    return (f"Headroom {WHERE}: memory free {val('memory_free_pct', '%')} · swap used {val('swap_used_gb', ' GB')} · "
            f"disk free {val('disk_free_gb', ' GB')}")


def api_view(v: dict) -> dict:
    """What the browser's refresh reads (no queue rows, no raw state)."""
    return {"workshop": v["workshop"], "record_status": v["record_status"], "admission": v["admission"],
            "runs": v["runs"], "last_event": v["last_event"], "next_actor": v["next_actor"],
            "needs": {"status": v["needs"]["status"], "workshop_status": v["needs"]["workshop_status"],
                      "count": len(v["needs"]["items"]) + len(v["needs"]["workshop"])},
            "headroom": v["headroom"], "recovery": v["recovery"], "budget": v["budget"], "ops": v["ops"],
            "jobs": {"known": v["jobs"]["known"], "stale": v["jobs"]["stale"], "count": len(v["jobs"]["cards"])}}


__all__ = ["view", "api_view", "admission", "observed_runs", "usage_this_month", "ops_strip", "jobs",
           "workshop_dir", "workshop_id"]
