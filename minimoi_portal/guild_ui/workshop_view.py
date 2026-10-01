"""The focused Workshop screen's data (4a: read only, model free).

Everything comes from files and the queue: the workshop's state.json (synced
from the Mac by scripts/workshop/workshop.py), the Build Queue, and the usage
store. Nothing here calls a model, and nothing launches: the launcher is 4b.

Honest states: a missing, unreadable or stale host reading is "unknown", and
its runs are "unknown", never "nothing running".
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone

from ..workshop.observer import LABELS, SESSION_KINDS, limits_text
from ..workshop.record import CLOCK_AHEAD, STALE_AFTER, Workshop, load_state, now, parse
from .adapters import ACTIVE, by_recent
from .needs import needs_you

APPROVED = ("spec_ready", "in_build")
AHEAD_TEXT = "unknown (clock ahead)"
log = logging.getLogger(__name__)


def workshop_dir() -> str | None:
    return os.environ.get("MINIMOI_WORKSHOPS_DIR") or None


def workshop_id() -> str:
    return os.environ.get("MINIMOI_WORKSHOP_ID") or "mac"


def _when(value) -> datetime | None:
    """A record time, or None when it is missing or not an ISO string."""
    return parse(value) if isinstance(value, str) and value else None


def _ahead(d: datetime | None) -> bool:
    """Dated more than CLOCK_AHEAD in the future: the clocks disagree, so untrustworthy."""
    return d is not None and d - now() > CLOCK_AHEAD


def _age(value) -> str | None:
    d = _when(value)
    if d is None:
        return None
    if _ahead(d):
        return AHEAD_TEXT
    mins = max(int((now() - d).total_seconds() // 60), 0)
    return "just now" if mins < 1 else f"{mins} min ago" if mins < 90 else f"{mins // 60} h ago"


def _num(value) -> float | int | None:
    """A host figure or a cost: a finite, non-negative number, else None (unknown)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _str(value) -> str | None:
    return value if isinstance(value, str) else None


def _shape(state):
    """The record's sections in the shapes the page reads. A section of the
    wrong shape becomes "absent", and items of the wrong shape are flagged,
    so a malformed but valid JSON record reads as unknown, never a crash."""
    if not isinstance(state, dict):
        return state
    s = dict(state)
    if not isinstance(s.get("host"), dict):
        s["host"] = None
    if s.get("items") is not None and not isinstance(s.get("items"), dict):
        s["items"], s["_items_unreadable"] = None, True
    for key in ("needs_you", "next"):
        s[key] = [x for x in s[key] if isinstance(x, dict)] if isinstance(s.get(key), list) else []
    if not isinstance(s.get("last_event"), dict):
        s["last_event"] = None
    return s


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
    d = _when(seen)
    if _ahead(d):
        mins = int((d - now()).total_seconds() // 60)
        return {"verdict": "unknown",
                "reasons": [f"the reading is dated {mins} min ahead of this server (clock skew), so it is not trusted"],
                "observed_at": seen, "age": AHEAD_TEXT}
    if status == "stale":
        return {"verdict": "unknown",
                "reasons": [f"the last host reading is {_age(seen)} old (over {int(STALE_AFTER.total_seconds() // 60)} min): "
                            f"it said {host.get('verdict', 'unknown')}"],
                "observed_at": seen, "age": _age(seen)}
    reasons = host.get("reasons")
    reasons = [r for r in reasons if isinstance(r, str)] if isinstance(reasons, list) else []
    return {"verdict": _str(host.get("verdict")) or "unknown", "reasons": reasons,
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
    raw = host.get("runs") if isinstance(host.get("runs"), list) else []
    rows = [{**r, "label": LABELS.get(_str(r.get("kind")), _str(r.get("kind"))),
             "counted": r.get("counted", _str(r.get("kind")) in SESSION_KINDS)} for r in raw if isinstance(r, dict)]
    counts = host.get("clients") if isinstance(host.get("clients"), dict) else {}
    sessions = host.get("sessions")
    if not isinstance(sessions, int) or isinstance(sessions, bool) or sessions < 0:
        sessions = sum(n for k, n in counts.items() if k in SESSION_KINDS and _num(n) is not None)
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
    return {"at": at, "age": _age(at)} if _when(at) else {"at": None, "age": None}


def _host_unknown(field, host) -> str:
    if field not in host:
        return "not in the reading"
    return "the probe failed" if host[field] is None else "unreadable value"


def _spend_fresh(at) -> dict:
    """The usage file's time is the last gateway record's, which may be days
    old: labelled "last record", with its date when it is not today."""
    fresh = _fresh(at)
    d = _when(at)
    if d is not None:
        fresh["label"] = "last record"
        if d.date() != now().date():
            fresh["date"] = d.strftime("%d %b").lstrip("0")
    return fresh


def ops_strip(state: dict | None, status: str, budget: dict) -> list[dict]:
    """The small ops strip: memory, swap, disk and load from the host reading,
    and this month's gateway spend. state is measured, stale, unknown or
    not_measured; value is None unless measured or stale."""
    host = (state or {}).get("host")
    host = host if isinstance(host, dict) else {}
    seen = host.get("observed_at") or host.get("at")
    ahead = _ahead(_when(seen))
    rows = []
    for key, label, field, unit in OPS:
        row = {"id": key, "label": label, "source": HOST_SOURCE, "fresh": _fresh(seen), "value": None}
        if status in ("missing", "unreadable") or not host:
            row.update(state="not_measured", text=f"{label} · not measured", source="no host reading synced",
                       fresh=_fresh(None))
        elif ahead:
            row.update(state="unknown", text=f"{label} · {AHEAD_TEXT}")
        elif _num(host.get(field)) is None:
            row.update(state="unknown", text=f"{label} · unknown ({_host_unknown(field, host)})")
        else:
            value = f"{host[field]}{unit}"
            row.update(value=value, state="stale" if status == "stale" else "measured",
                       text=f"{label} {value}" + (" · stale" if status == "stale" else ""))
        rows.append(row)
    month = budget.get("month")
    if budget.get("known") and budget.get("bad"):
        n = budget["bad"]
        rows.append({"id": "spend", "label": "Model spend", "value": None, "state": "unknown",
                     "text": f"Model spend · unknown (unreadable value in {n} gateway record{'s' if n != 1 else ''})",
                     "source": "usage store, gateway records", "fresh": _spend_fresh(budget.get("as_of"))})
    elif budget.get("known") and budget.get("none_yet"):
        rows.append({"id": "spend", "label": "Model spend", "value": None, "state": "not_measured",
                     "text": "Model spend · not measured yet this month",
                     "source": f"usage store: no gateway records yet in {month}", "fresh": _fresh(None)})
    elif budget.get("known"):
        spent = sum((budget.get("by_actor") or {}).values())
        rows.append({"id": "spend", "label": "Model spend", "value": f"${spent:.2f}", "state": "measured",
                     "text": f"Model spend ${spent:.2f} in {month}",
                     "source": "usage store, gateway records", "fresh": _spend_fresh(budget.get("as_of"))})
    else:
        rows.append({"id": "spend", "label": "Model spend", "value": None, "state": "not_measured",
                     "text": "Model spend · not measured", "source": "no usage store here", "fresh": _fresh(None)})
    rows.append({"id": "production", "label": "Production host", "value": None, "state": "not_measured",
                 "text": "Production host · not measured", "source": "no source yet", "fresh": _fresh(None)})
    return rows


# ── Job states from the workshop record (spec §7) ─────────────────────────────
JOB_STATE = {"started": "running", "progress": "running", "needs_you": "needs you", "decision": "running",
             "blocked": "blocked", "review": "in review", "done": "completed", "next": "queued", "health": "running"}
# Needs you first, then what is stuck or moving, completed last.
JOB_ORDER = {"needs you": 0, "blocked": 1, "in review": 2, "running": 3, "queued": 4, "unknown": 5, "completed": 7}
EVIDENCE_LINES = 5
JOB_CAP = 12
LAST_SEEN_STALE = timedelta(hours=48)       # a job not heard from in this long is not "running" any more
JOBS_UNREADABLE = "Job states unknown (the workshop record could not be read)."
JOBS_STALE = "The host reading is stale, so these job states may be out of date."
JOBS_AHEAD = "The host reading is dated ahead of this server's clock, so these job states may be out of date."


def _event_ok(ev) -> bool:
    return isinstance(ev, dict) and isinstance(ev.get("item"), str) and isinstance(ev.get("at"), str)


def _evidence(e: dict) -> dict:
    return {"at": _str(e.get("at")), "actor": _str(e.get("actor")), "kind": _str(e.get("kind")),
            "text": _str(e.get("text"))}


def _card(key: str, last, evs: list[dict]) -> dict:
    kind, ref = key.split(":", 1) if ":" in key else (key, "")
    card = {"item": key, "kind": kind, "ref": ref,
            "queue_id": int(ref) if kind == "queue" and ref.isascii() and ref.isdigit() else None,
            "evidence": [_evidence(e) for e in evs[-EVIDENCE_LINES:]]}
    if not isinstance(last, dict):              # the record's entry for this item is the wrong shape
        return {**card, "state": "unknown", "pill": "unknown", "flag": "unreadable",
                "title": key, "actor": None, "stage": None, "next_actor": None, "last_contact": _fresh(None),
                "note": "This item's entry in the workshop record could not be read."}
    reported = JOB_STATE.get(_str(last.get("kind")) or "", _str(last.get("kind")) or "unknown")
    at = _when(last.get("at"))
    card.update(state=reported, pill=reported, flag=None, title=_str(last.get("text")) or key,
                actor=_str(last.get("actor")), stage=_str(last.get("stage")), next_actor=_str(last.get("next_actor")),
                last_contact=_fresh(last.get("at")), note=None)
    if at is None:
        card.update(pill="unknown", flag="unreadable", note="This item's last contact time could not be read.")
    elif _ahead(at):
        card.update(pill=AHEAD_TEXT, flag="ahead")
    elif reported == "needs you" and now() - at > LAST_SEEN_STALE:
        # Still waiting on Robert: age makes it more urgent, not stale (#289 re-check R1).
        days = int((now() - at).total_seconds() // 86400)
        card.update(pill=f"needs you · {days}d")
    elif reported != "completed" and now() - at > LAST_SEEN_STALE:
        days = int((now() - at).total_seconds() // 86400)
        card.update(pill=f"last seen {days}d ago (stale)", flag="stale")
    return card


def _order(card: dict) -> int:
    if card["state"] == "needs you":
        return 0
    return 6 if card["flag"] else JOB_ORDER.get(card["state"], 5)


def jobs(root: str | None, wid: str, state: dict | None, status: str) -> dict:
    """One light card per work item in the workshop record: its state from the
    latest event, who reported it and when, and the item's last few events as
    evidence. Needs you first, completed last, at most JOB_CAP cards. Read
    only; unknown when the record can't be read, marked stale when the host
    reading is stale, and a card not heard from in 48 hours says so."""
    if status in ("missing", "unreadable") or not isinstance(state, dict):
        return {"known": False, "stale": False, "cards": [], "more": 0, "stale_text": None,
                "text": "Job states unknown (no workshop record could be read)."}
    items = state.get("items") or {}
    if state.get("_items_unreadable") or not isinstance(items, dict):
        return {"known": False, "stale": False, "cards": [], "more": 0, "stale_text": None, "text": JOBS_UNREADABLE}
    events = []
    try:
        if root:
            events = Workshop(root, wid).events()
    except (OSError, ValueError):
        events = []
    by_item: dict[str, list[dict]] = {}
    for ev in sorted((e for e in events if _event_ok(e)), key=lambda e: e["at"]):   # a malformed line is skipped
        if not ev["item"].startswith("host:"):
            by_item.setdefault(ev["item"], []).append(ev)
    rows = [(k, v) for k, v in items.items() if isinstance(k, str) and not k.startswith("host:")]
    rows.sort(key=lambda kv: (_str(kv[1].get("at")) or "") if isinstance(kv[1], dict) else "", reverse=True)
    cards = []
    for k, v in rows:
        evs = by_item.get(k) or ([v] if isinstance(v, dict) and isinstance(v.get("at"), str) else [])
        cards.append(_card(k, v, evs))
    cards.sort(key=_order)                      # stable: newest first within each group
    host = state.get("host") if isinstance(state.get("host"), dict) else {}
    ahead = _ahead(_when(host.get("observed_at") or host.get("at")))
    stale = status == "stale"
    return {"known": True, "stale": stale, "cards": cards[:JOB_CAP], "more": max(len(cards) - JOB_CAP, 0),
            "stale_text": (JOBS_AHEAD if ahead else JOBS_STALE) if stale else None,
            "text": None if cards else "No jobs in the workshop record yet."}


def usage_this_month(folder: str | None) -> dict:
    """Gateway spend this month by actor, from the usage store; None when unknown."""
    if not folder or not os.path.isdir(folder):
        return {"known": False}
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    path = os.path.join(folder, f"usage-{month}.jsonl")
    spent: dict[str, float] = {}
    bad = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("emitter") != "gateway" or r.get("cost_usd") is None:
                    continue
                cost = _num(r.get("cost_usd"))
                if cost is None:                    # NaN, negative, text: the total is then unknown
                    bad += 1
                    continue
                actor = _str(r.get("actor")) or "unknown"
                spent[actor] = spent.get(actor, 0.0) + cost
    except FileNotFoundError:
        return {"known": True, "month": month, "by_actor": {}, "none_yet": True}
    except OSError:
        return {"known": False}
    try:
        as_of = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat(timespec="seconds")
    except OSError:
        as_of = None
    out = {"known": True, "month": month, "by_actor": {k: round(v, 4) for k, v in sorted(spent.items())},
           "as_of": as_of}
    if bad:
        out["bad"] = bad
    return out


RECOVERY = {
    "blocked": ["Let a running agent client finish, or close one you are not using, then observe again.",
                "Free disk on the Mac (for example docker builder prune -f, then colima ssh -- sudo fstrim -av)."],
    "tight": ["A new run can start but would share the Mac: prefer one at a time."],
    "unknown": ["On the Mac: scripts/workshop/workshop.py observe, then sync. A reading older than 15 minutes counts as unknown."],
    "ok": [],
}


def view(services, item_id: int | None, *, usage_dir: str | None = None) -> dict:
    state, status = load_state(workshop_dir(), workshop_id())
    try:
        return _view(services, item_id, _shape(state), status, usage_dir)
    except (AttributeError, KeyError, TypeError, ValueError):
        # A record that is valid JSON but the wrong shape reads as unknown, never a 500.
        log.warning("workshop record %s could not be read; shown as unknown", workshop_id(), exc_info=True)
        return _view(services, item_id, None, "unreadable", usage_dir)


def _view(services, item_id, state, status, usage_dir) -> dict:
    adm = admission(state, status)
    queue_res = services.queue.list_items()
    checks = services.queue.checks()
    items = queue_res.data or []
    item = next((i for i in items if i.get("id") == item_id), None) if item_id else None
    key = f"queue:{item_id}" if item_id else None
    item_event = ((state or {}).get("items") or {}).get(key) if key else None
    item_event = item_event if isinstance(item_event, dict) else None
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
    if status != "ok" or not isinstance(h, dict) or not h:
        return None
    val = lambda k, unit: f"{h[k]}{unit}" if _num(h.get(k)) is not None else "unknown"  # noqa: E731
    return (f"Headroom {WHERE}: memory free {val('memory_free_pct', '%')} · swap used {val('swap_used_gb', ' GB')} · "
            f"disk free {val('disk_free_gb', ' GB')}")


def api_view(v: dict) -> dict:
    """What the browser's refresh reads (no queue rows, no raw state)."""
    return {"workshop": v["workshop"], "record_status": v["record_status"], "admission": v["admission"],
            "runs": v["runs"], "last_event": v["last_event"], "next_actor": v["next_actor"],
            "needs": {"status": v["needs"]["status"], "workshop_status": v["needs"]["workshop_status"],
                      "count": len(v["needs"]["items"]) + len(v["needs"]["workshop"])},
            "headroom": v["headroom"], "recovery": v["recovery"], "budget": v["budget"], "ops": v["ops"],
            "jobs": {**v["jobs"], "count": len(v["jobs"]["cards"])}}


__all__ = ["view", "api_view", "admission", "observed_runs", "usage_this_month", "ops_strip", "jobs",
           "workshop_dir", "workshop_id"]
