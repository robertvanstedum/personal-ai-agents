"""The headless state contract (host runtime spec v0.5 §7.6, R7.6-a/b/c): one versioned snapshot,
``minimoi.runtime.state.v1``, built from the authority's own records, and the rules that read it (§7.7, R7.4-c/d/e).

Everything here is a pure function of the records ``DispatcherStore.state_records`` reads (and of the clock it read
them at): no database, no model, no network, no write. It runs on Python 3.9 like the rest of this package.

* **The snapshot** (``build``): R7.6-b's field names. Hosts are derived at read time (R5.1-a) and skip the authority's
  reserved rows (``sweeper/…``, ``authority/…``, ``reader/…``: every id with a ``/``); each host lists the tasks it has
  blocked (``authority_error:<code>``, with its age and the next step, an operator restart). Items carry both clocks,
  what they are shown as (R5.1-b), the §7.7 flags, ``who_may_act`` and ``next_authorized_step``; spend per item is
  ``spent`` (earlier attempts) + ``observed`` (the last), as one total. ``unknowns`` names every fact the authority
  does not know (R7.6-c): an unknown is ``null`` and named, never rendered as zero, false or done.
* **The rule flags** (§7.7): ``held``/``lapsed`` (``shown_as``), ``stalled`` (owner reachable, lease current, progress
  age past the item's class progress timeout), ``overdue`` (past its deadline, not ended), ``unknown`` (state unknown,
  or a blocking entry in ``unknowns``), ``drift`` (``out_of_scope_paths``, ``over_estimate``) and
  ``next_authorized_step``. A flag never starts a call (R7.5-d): a stall yields at most one ``review_request``
  proposal per stall (so at most one per item per window), which only Robert can turn into a judgment call.
* **Notices** (``notices``): one per key, each carrying the next authorized step (R7.4-d, R5.1-e, OWN-4). Silence
  where nothing is needed (R7.4-e): a portable owner's lapse within grace, a retry within the attempt allowance and a
  host's expected sleep raise nothing. The check-in's three delegation items (unclaimed, silent, ended) are rules of
  this same set, with today's keys and words (``legacy_items``, §3.3: one rule set, not two).
* **Decisions** (R7.4-c, OWN-6): every queued decision in full words, with what "yes" and "no" do and the digest of
  those words. Decisions waiting at the same sweep are grouped into one notice of at most five; the three solo
  classes (an ``unknown`` resolution, a reassignment, a cross-vendor review admission) each arrive alone.
* **Owner work** (§7.2, OWN-1…OWN-3): A, R, C, O and J per card, from the authority's records only.
* **The read path** (R7.6-a): ``read_request`` checks a read credential and answers the snapshot; the HTTPS server
  that serves it is S1-H's. ``write_state_file`` writes ``state.json`` atomically; ``read_state_file`` reads it back
  as a view with its age (R4.6).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = "minimoi.runtime.state.v1"
BATCH_MAX = 5                                   # R7.4-c: decisions per notice
SOLO = ("unknown_resolution", "reassignment", "cross_vendor_admission")     # R7.4-c: never batched
ROUTINE = ("step_admission", "step_redo")
TERMINAL = ("completed", "failed", "refused", "timed_out", "cancelled", "output_limit", "interrupted", "expired")
OWNED = ("claimed", "running")
LIVE = ("proposed", "queued", "claimed", "running", "lapsed", "paused", "unknown")
LEGACY_ENDED = ("failed", "refused", "timed_out", "output_limit", "interrupted")    # watch.py's ended rule, today
UNCLAIMED_AFTER = timedelta(minutes=5)           # watch.py, today: a queued delegation nobody picked up
SILENT_AFTER = timedelta(minutes=5)              # watch.py, today: a running delegation whose runner stopped renewing
OVER_ESTIMATE_FACTOR = 2                         # §7.7 drift: more than twice the card's estimate
OVER_RESERVATION = 0.8                           # §7.7 drift: 80% of the reservation used
BLOCKING = ("outcome", "observed_usage", "owner_reachability", "missing_capability:")
NOTICE_KIND = {"stalled": "stalled", "lapsed": "stalled", "overdue": "stalled", "drift": "stalled",
               "unknown": "waiting_on_robert", "blocked": "blocked", "ready": "waiting_on_robert",
               "card_ended": "error", "ended": "error", "sweeper_stale": "error", "escalated": "waiting_on_robert",
               "decision": "waiting_on_robert", "decisions": "waiting_on_robert"}
READ_ROLES = ("observer", "owner")              # who may read the snapshot; host and watch credentials may not
NAMES = {"claude-code": "Claude Code", "codex": "Codex", "grok": "Grok", "mc": "MC", "robert": "Robert",
         "fakework": "fakework"}
_TOKEN = re.compile(r"^[\x21-\x7e]{32,256}$")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_PROPOSALS = uuid.UUID("6b1d3a52-8c4e-4e0f-9a51-1f6c2d9e7a10")    # namespace of review_request ids (deterministic)


def name(actor):
    """How a worker is named to Robert (core.supervision.store.name, kept here so this package stands alone)."""
    return NAMES.get(actor, actor)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iso(value):
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value


def _at(value):
    """A datetime from a stored value (a datetime, or the ISO text the store writes into JSON), or None."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo is not None else out.replace(tzinfo=timezone.utc)


def _age(now, then):
    then = _at(then)
    return None if then is None else round((now - then).total_seconds(), 3)


def reachability(seen_at, announced, now, timeout_s) -> str:
    """R5.1-a at read time (census.host_reachability's rule): reachable when the last contact is at most
    ``timeout_s`` old; stopping or stopped when that contact said so; otherwise unreachable; unknown without a
    contact or a timeout."""
    seen_at = _at(seen_at)
    if seen_at is None or timeout_s is None or now is None:
        return "unknown"
    if announced in ("stopping", "stopped"):
        return announced
    return "reachable" if (now - seen_at).total_seconds() <= timeout_s else "unreachable"


def reserved_row(runner_id) -> bool:
    """The authority's own rows among the contacts (its liveness, the sweeper, readers): never a host."""
    return "/" in str(runner_id)


def _short(rid) -> str:
    return str(rid)[:8]


def _label(item) -> str:
    """An item as Robert reads it: its card and step, else its job id; never its brief."""
    if item.get("card_id") and item.get("step"):
        return f"card {item['card_id']} step {item['step']}"
    return f"item {item.get('job_id') or _short(item['request_id'])}"


# ── the decision words (R7.4-c): what Robert answers, and what each answer does ─────────────────────────────────
def decision_actions(d, item) -> tuple:
    """(what yes does, what no does) for one queued decision, fixed by its class and its record."""
    cls, body = d["class"], d["body"]
    if cls in ("step_admission", "cross_vendor_admission", "step_redo"):
        return "admit", "decline"
    if cls == "reassignment":
        return "reassign", "withdraw"
    if body.get("item_state") is not None:
        return "acknowledge", "leave"                       # a decision about an item that has already ended
    if body.get("reason") == "result_reported":
        return "accept", "end"
    return "end", "leave"


def decision_words(d, item) -> str:
    """The full words of one decision as Robert reads them, built only from the decision's record and the item's
    fixed fields (job, card, step, worker, route, amount), so the same decision always reads the same and its digest
    binds the answer (each receipt binds its own). Never the brief, never a model's words."""
    body, cls = d["body"], d["class"]
    who = _label(item)
    worker = f"{name(item.get('actor'))} on route {item.get('route')}"
    res = item.get("reservation") or {}
    amount = f"{res.get('requested')} {res.get('unit') or _unit_of(item)}".strip()
    ops = body.get("operations") or []
    nops = f"{len(ops)} effect{'s' if len(ops) != 1 else ''}"
    if cls == "step_admission":
        return (f"Admit {who} ({worker}, {amount}): the card's own approval no longer admits it "
                f"({body.get('reason')}). Yes admits it under its own exact approval; no ends the card.")
    if cls == "cross_vendor_admission":
        return (f"Admit a cross-vendor review on {who} ({worker}, {amount}). A card's approval never covers another "
                f"model family. Yes admits it under its own exact approval; no ends the card.")
    if cls == "step_redo":
        return (f"Redo {who} as a new item? An effect of it failed for good ({nops}), so the step ended failed "
                f"(effect_failed) and nothing retries it. Yes admits one new item for this step ({worker}, {amount}), "
                f"with new operation ids; no ends the card.")
    if cls == "reassignment":
        return (f"Reassign {who} ({worker}) to runner {body.get('runner_id')}, generation "
                f"{(d.get('generation') or 0) + 1}? MC proposed it; nothing moves it but you. Yes reassigns it; no "
                f"leaves it where it is.")
    reason = body.get("reason")
    if body.get("item_state") is not None:
        why = {"process_unverified": "a process of it could not be verified",
               "effect_outcome_conflict": "an effect's reported outcome contradicts the one recorded"}.get(
            reason, reason)
        return (f"{who[0].upper()}{who[1:]} has ended ({body['item_state']}), but {why}. Nothing is retried and "
                f"nothing changes. Yes acknowledges it; no leaves it listed.")
    if reason == "result_reported":
        rep = (body.get("reported") or {}).get("status")
        tally = body.get("effects") or {}
        return (f"{who[0].upper()}{who[1:]} reported its result ({rep}) while an effect of it was open; every "
                f"effect of it is now decided ({', '.join(f'{k} {v}' for k, v in sorted(tally.items())) or 'none'}). "
                f"Yes accepts the reported result ({rep}), and its card goes on; no ends it "
                f"(interrupted: owner_decision).")
    why = {"effect_unknown": f"the outcome of {nops} cannot be established",
           "process_unverified": "a process of it could not be verified",
           "effect_outcome_conflict": "an effect's reported outcome contradicts the one recorded"}.get(reason, reason)
    then = ""
    if body.get("ending"):
        then = f" Otherwise it would have ended {body['ending'].get('state')} ({body['ending'].get('code')})."
    return (f"{who[0].upper()}{who[1:]} ({worker}) is unknown: {why}. Nothing retries it, and its workspace is "
            f"kept.{then} Yes ends it (interrupted: owner_decision); no leaves it waiting, and you can still end or "
            f"reassign it.")


def _unit_of(item):
    return (item.get("reservation") or {}).get("unit") or ""


def batch_decisions(waiting, raised) -> list:
    """The decision notices (R7.4-c, OWN-6). ``waiting``: the queued decisions, oldest first. ``raised``: the
    decision notices already recorded, each {key, decisions}. A decision is in at most one notice: those already in a
    recorded notice stay there; the rest are grouped now, each solo class alone, the routine ones in queued order,
    at most BATCH_MAX to a notice. The grouping is a function of the records, so a live read and the next sweep agree."""
    noticed = {d for n in raised for d in n["decisions"]}
    out = [n for n in raised if any(d["decision_id"] in n["decisions"] for d in waiting)]
    fresh = [d for d in waiting if d["decision_id"] not in noticed]
    routine = []
    for d in fresh:
        if d["solo"]:
            out.append({"key": f"decision:{d['decision_id']}", "decisions": [d["decision_id"]], "solo": True})
        else:
            routine.append(d["decision_id"])
    for i in range(0, len(routine), BATCH_MAX):
        ids = routine[i:i + BATCH_MAX]
        out.append({"key": "decisions:" + sha256(",".join(ids))[:24], "decisions": ids, "solo": False})
    return out


# ── the check-in's delegation items, today's rules (watch.py at 5d6c7295), kept as one rule set (§3.3) ──────────
def legacy_items(jobs) -> tuple:
    """(items, running) for the check-in's delegation rows (store.delegations()): unclaimed after 5 minutes, silent
    after 5 minutes without renewal, ended unhappily. Today's keys, kinds and words, in today's order; the check-in
    with no runtime configured calls exactly this, so its output is unchanged byte for byte."""
    items, running = [], []
    for job in jobs:
        who = name(job["actor"])
        what = f"{who} {job['scope']} “{job['brief'][:60]}”"
        if job["state"] in ("queued", "claimed", "running"):
            minutes = int((job["now"] - (job["started_at"] or job["created_at"])).total_seconds() // 60)
            running.append({"job": job["job_id"], "agent": job["actor"], "state": job["state"], "minutes": minutes,
                            "brief": job["brief"][:80]})
            if job["state"] == "queued" and job["now"] - job["created_at"] >= UNCLAIMED_AFTER:
                items.append({"key": f"delegation-unclaimed:{job['request_id']}", "kind": "stalled",
                              "text": f"{what} has waited {minutes} min and no runner on the Mac picked it up."})
            elif job["state"] == "running" and job["heartbeat_at"] and job["now"] - job["heartbeat_at"] >= SILENT_AFTER:
                items.append({"key": f"delegation-silent:{job['request_id']}", "kind": "stalled",
                              "text": f"{what} has not reported for "
                                      f"{int((job['now'] - job['heartbeat_at']).total_seconds() // 60)} min."})
        elif job["state"] in LEGACY_ENDED:
            items.append({"key": f"delegation-ended:{job['request_id']}", "kind": "error",
                          "text": f"{what} ended {job['state'].replace('_', ' ')}"
                                  + (f" ({job['code']})" if job.get("code") else "") + "."})
    return items, running


# ── the snapshot ─────────────────────────────────────────────────────────────────────────────────────────────
def build(rec) -> dict:
    """``minimoi.runtime.state.v1`` from the authority's records (DispatcherStore.state_records). Pure."""
    now, values = rec["now"], rec["values"]
    unknowns = []
    events = rec["events"]
    hosts = _hosts(rec, now, values, unknowns)
    by_runner = {h["runner_id"]: h for h in hosts}
    caps = set()
    for h in hosts:
        if "worker" in (h["roles"] or []):
            caps.update(h["capabilities"])
    cards = _cards(events)
    resolved = {e["body"].get("decision_id"): e for e in events if e["kind"] == "decision.resolved"}
    answered = {}
    for e in events:
        if e["kind"] == "decision.answered":
            answered[e["body"].get("decision_id")] = e
    item_rows = {str(r["request_id"]): r for r in rec["items"]}
    waiting = []
    for e in events:
        if e["kind"] != "decision.queued" or str(e["event_id"]) in resolved:
            continue
        item = item_rows.get(str(e["item_id"]))
        if item is None:
            unknowns.append(f"decision_item:{e['event_id']}")
            continue
        d = {"decision_id": str(e["event_id"]), "class": e["body"].get("class"), "body": e["body"],
             "item_id": str(e["item_id"]), "generation": e["generation"], "queued_at": iso(e["at"]),
             "solo": e["body"].get("class") in SOLO or bool(e["body"].get("solo"))}
        words = decision_words(d, item)
        yes, no = decision_actions(d, item)
        last = answered.get(d["decision_id"])
        waiting.append({"decision_id": d["decision_id"], "code": rec["codes"].get(d["decision_id"]),
                        "class": d["class"], "solo": d["solo"], "reason": e["body"].get("reason"),
                        "item_id": d["item_id"], "generation": d["generation"], "job_id": item.get("job_id"),
                        "card_id": item.get("card_id"), "step": item.get("step"), "queued_at": d["queued_at"],
                        "words": words, "text_sha256": sha256(words), "yes": yes, "no": no,
                        "answered": None if last is None else {"answer": last["body"].get("answer"),
                                                               "at": iso(last["at"])}})
    decision_of = {}
    for d in waiting:
        decision_of.setdefault(d["item_id"], d)
    items = [_item(r, rec, now, values, by_runner, caps, cards, decision_of) for r in rec["items"]]
    raised = _raised(events)
    notices = _notices(items, rec, now, values, hosts, cards, waiting, raised, unknowns)
    proposals = _proposals(items, events, now)
    sweeper = rec["liveness"].get("sweeper")
    sweeper_age = None if sweeper is None else _age(now, sweeper["seen_at"])
    if sweeper_age is None:
        unknowns.append("sweeper_age: the sweeper has not run")
    auth = rec.get("authority") or {}
    for k in ("endpoint_id", "host_id"):
        if not auth.get(k):
            unknowns.append(f"authority_{k}")
    if values.get("contact_timeout_s") is None:
        unknowns.append("contact_timeout_s: no host values in the authority's file")
    doc = {"schema": SCHEMA, "environment": rec["environment"], "as_of": iso(now), "source": "authority",
           "view_age_s": 0,
           "authority": {"endpoint_id": auth.get("endpoint_id"), "host_id": auth.get("host_id"),
                         "sweeper_age_s": sweeper_age,
                         "suspended": [{"from": iso(s["from"]), "to": iso(s["to"]), "outage_s": s.get("outage_s")}
                                       for s in rec["suspended"]]},
           "hosts": [{k: v for k, v in h.items() if k not in ("capabilities", "runner_id")} for h in hosts],
           "items": items, "agents": rec["agents"], "proposals": proposals, "decisions_waiting": waiting,
           "notices": notices, "owner_work": owner_work(rec, cards, raised),
           "judgment": _judgment(rec, values, events), "unknowns": sorted(set(unknowns))}
    if rec.get("agents_unknown"):
        doc["unknowns"].append("agents: " + rec["agents_unknown"])
    return doc


def _hosts(rec, now, values, unknowns) -> list:
    """One row per host (its latest-seen runner), the authority's reserved rows skipped (R5.1-a)."""
    latest = {}
    for r in rec["runners"]:
        if reserved_row(r["runner_id"]) or r.get("protocol") != 2 or not r.get("host_id") \
                or r.get("environment") != rec["environment"]:
            continue
        old = latest.get(r["host_id"])
        if old is None or _at(r["seen_at"]) > _at(old["seen_at"]):
            latest[r["host_id"]] = r
    out = []
    for host_id in sorted(latest):
        r = latest[host_id]
        reach = reachability(r["seen_at"], r.get("announced"), now, values.get("contact_timeout_s"))
        blocked = []
        for b in (rec["blocked"].get(r["runner_id"]) or []):
            blocked.append({"item_id": b.get("item_id"), "generation": b.get("generation"), "code": b.get("code"),
                            "since": b.get("since"), "age_s": _age(now, b.get("since")),
                            "next_step": "an operator restarts the runtime, which replays the task's pending records"})
        out.append({"host_id": host_id, "runner_id": r["runner_id"], "roles": list(r.get("roles") or []),
                    "availability": r.get("expected_availability"), "grace_s": r.get("unreachable_grace_s"),
                    "bundle_digest": r.get("bundle_digest"), "reachability": reach,
                    "contact_age_s": _age(now, r["seen_at"]), "announced": r.get("announced"),
                    "last_gap_s": r.get("last_gap_s"), "blocked": blocked,
                    "capabilities": list(r.get("capabilities") or [])})
        if reach == "unknown":
            unknowns.append(f"host_reachability:{host_id}")
    return out


def _cards(events) -> dict:
    """card_id -> {record (card.admitted body), admitted_at, ready_at, ended (body), ended_at}."""
    out = {}
    for e in events:
        b = e["body"]
        if e["kind"] == "card.admitted":
            out.setdefault(b["card_id"], {}).update(record=b, admitted_at=_at(e["at"]))
        elif e["kind"] == "card.ready":
            out.setdefault(b.get("card_id"), {}).update(ready_at=_at(e["at"]), ready=b)
        elif e["kind"] == "card.ended":
            out.setdefault(b.get("card_id"), {}).update(ended=b, ended_at=_at(e["at"]))
    return out


def _class(item):
    """An item's task class for its progress timeout (§5.3): a card step's kind (a fix round is build work), else
    its scope."""
    kind = ((item.get("facts") or {}).get("card") or {}).get("kind")
    if kind is not None:
        return "build" if kind == "fix" else kind
    return item.get("scope")


def _item(r, rec, now, values, by_runner, caps, cards, decision_of) -> dict:
    v2 = r.get("route") is not None
    rid, state = str(r["request_id"]), r["state"]
    facts = r.get("facts") or {}
    card = (facts.get("card") or {}) if v2 else {}
    unknowns = []
    owner = by_runner.get(r.get("runner_id"))
    reach = None
    if r.get("runner_id") is not None and state not in TERMINAL:
        reach = owner["reachability"] if owner is not None else "unknown"
    lease_until = _at(r.get("lease_until"))
    lease_left = None if lease_until is None else round((lease_until - now).total_seconds(), 3)
    current = lease_until is not None and lease_until > now
    ending = facts.get("ending")
    if not v2:
        shown = state
    elif ending and state in OWNED + ("lapsed", "paused"):
        shown = "ending"
    elif state in OWNED:
        shown = ("running" if reach == "reachable" else "held") if current else "lapsed"
    else:
        shown = state
    progress_ref = None
    if v2 and state in OWNED + ("lapsed", "paused"):
        marks = [x for x in (_at(r.get("progress_at")), _at(facts.get("leased_at"))) if x is not None]
        progress_ref = max(marks) if marks else None
        if progress_ref is None:
            unknowns.append("progress_age")
    progress_age = None if progress_ref is None else round((now - progress_ref).total_seconds(), 3)
    cp = r.get("checkpoint")
    res = r.get("reservation") or {}
    observed = res.get("observed")
    spent = res.get("spent")
    usage_unknown = res.get("state") == "usage_unknown"
    if usage_unknown:
        unknowns.append("observed_usage")
        total = None
    elif observed is None and spent is None:
        total = None
    else:
        total = round(float(observed or 0) + float(spent or 0), 4)
    if state == "unknown":
        unknowns.append("outcome")
    if v2 and owner is None and r.get("runner_id") is not None and state not in TERMINAL:
        unknowns.append("owner_reachability")
    elif v2 and reach == "unknown" and state in OWNED:
        unknowns.append("owner_reachability")
    if not v2 and state in OWNED:
        unknowns.append("protocol_1: no host facts, progress or checkpoint are kept for this item")
    needs = sorted({*(r.get("required_capabilities") or []), f"actor:{r.get('actor')}", f"scope:{r.get('scope')}",
                    f"repo:{r.get('repository')}"})
    if v2 and state == "queued" and r.get("runner_id") is None:
        for c in needs:
            if c not in caps:
                unknowns.append(f"missing_capability:{c}")
    # the grant and the card's allowances
    gid = r.get("grant_id")
    grant = None
    if gid is not None:
        g = rec["grants"].get(str(gid))
        record = (cards.get(r.get("card_id")) or {}).get("record") or {}
        attempts = (record.get("card") or {}).get("attempts")
        grant = {"grant_id": str(gid), "code": rec["codes"].get(_admitted_by(rec, rid)),
                 "steps_left": None if g is None else g["max_operations"] - g["consumed_operations"],
                 "attempts_left": None if not card or attempts is None else attempts - (card.get("attempt") or 1),
                 "expires_at": None if g is None else iso(g["expires_at"]),
                 "revoked": None if g is None else g["revoked_at"] is not None}
        if g is None:
            unknowns.append("grant_record")
    # flags (§7.7)
    timeout = (values.get("progress_timeout_s") or {}).get(_class(r))
    if shown == "running" and v2:
        if timeout is None:
            unknowns.append(f"progress_timeout_s:{_class(r)}")
            stalled = None
        elif progress_age is None:
            stalled = None
        else:
            stalled = progress_age > timeout
    elif v2:
        stalled = False
    else:
        stalled = None
    deadline = _at(r.get("deadline"))
    overdue = state not in TERMINAL and deadline is not None and deadline <= now
    drift = _drift(r, rec, now, cards, card, cp, total, unknowns) if v2 and state not in TERMINAL else []
    blocking = [u for u in unknowns if u.startswith(BLOCKING)]
    flags = {"stalled": stalled, "overdue": overdue, "unknown": state == "unknown" or bool(blocking), "drift": drift}
    decision = decision_of.get(rid)
    item = {"item_id": rid, "protocol": 2 if v2 else 1, "job_id": r.get("job_id"), "card_id": r.get("card_id"),
            "step": r.get("step"), "actor": r.get("actor"), "route": r.get("route"), "stored_state": state,
            "shown_as": shown, "code": (r.get("result_detail") or {}).get("code"),
            "owner_host_id": None if owner is None else owner["host_id"], "owner_runner_id": r.get("runner_id"),
            "owner_reachability": reach, "generation": r.get("generation"),
            "lease_until": iso(lease_until), "lease_left_s": lease_left, "grace_deadline": iso(r.get("grace_deadline")),
            "lapse_cause": r.get("lapse_cause"), "deadline": iso(deadline),
            "last_renewal_at": iso(r.get("heartbeat_at")), "last_progress_at": iso(r.get("progress_at")),
            "last_progress": r.get("progress_phase") if cp is None else f"checkpoint {cp.get('name')}",
            "progress_age_s": progress_age, "progress_from_at": iso(progress_ref),
            "checkpoint": None if cp is None else {"name": cp.get("name"), "digest": cp.get("digest")},
            "grant": grant,
            "reservation": None if not v2 else {
                "route": r.get("route"), "unit": res.get("unit"), "requested": res.get("requested"),
                "reserved": res.get("reserved"), "observed": total, "usage_unknown": usage_unknown,
                "held": res.get("held")},
            "facts": {"picked_up_at": facts.get("picked_up_at"), "reported_at": facts.get("reported_at"),
                      "verified_at": None, "released_at": facts.get("released_at")},
            "flags": flags, "unknowns": sorted(set(unknowns)),
            "decision": None if decision is None else {"decision_id": decision["decision_id"],
                                                       "code": decision["code"], "class": decision["class"]},
            "awaiting_runner": r.get("runner_id") if state == "queued" else None,
            "created_by": r.get("created_by"), "needs": needs}
    item["who_may_act"], item["next_authorized_step"] = _who(item, r, decision, ending)
    return item


def _admitted_by(rec, rid):
    """The decision whose yes admitted this item (its D-code is the grant's code), if one did."""
    for e in rec["events"]:
        if e["kind"] == "decision.resolved" and str(e["item_id"]) == rid and e["body"].get("action") == "admit":
            return e["body"].get("decision_id")
    return None


def _in(path, allowed) -> bool:
    return any(path == a or path.startswith(a.rstrip("/") + "/") for a in allowed)


def _drift(r, rec, now, cards, card, cp, total, unknowns) -> list:
    """§7.7's drift: the latest checkpoint's paths outside the card's paths_allowed; more than twice the card's
    estimate elapsed, or 80% of the reservation used, while the card is not ready. A bound the card does not state is
    named in unknowns, never read as "no drift"."""
    if not card:
        return []
    c = cards.get(r.get("card_id")) or {}
    if not c.get("record"):
        unknowns.append("card_record: its admission is not among the records read")
        return []
    approved = c["record"].get("card") or {}
    out = []
    allowed = approved.get("paths_allowed") or []
    if not allowed:
        unknowns.append("paths_allowed: the card states none")
    elif cp is not None:
        outside = sorted(p for p in (cp.get("paths") or []) if not _in(p, allowed))
        if outside:
            out.append({"kind": "out_of_scope_paths", "paths": outside[:20], "checkpoint": cp.get("name")})
    estimate = approved.get("estimate_s")
    admitted = c.get("admitted_at")
    if estimate is None:
        unknowns.append("estimate_s: the card states none")
    elif admitted is not None and c.get("ready_at") is None:
        elapsed = (now - admitted).total_seconds()
        if elapsed > OVER_ESTIMATE_FACTOR * estimate:
            out.append({"kind": "over_estimate", "elapsed_s": round(elapsed, 3), "estimate_s": estimate})
    res = r.get("reservation") or {}
    requested = res.get("requested")
    used = res.get("spent") or 0
    if requested and used and float(used) >= OVER_RESERVATION * float(requested) and \
            not any(d["kind"] == "over_estimate" for d in out):
        out.append({"kind": "over_estimate", "reservation_used": round(float(used) / float(requested), 4)})
    return out


def _who(item, r, decision, ending) -> tuple:
    """(who_may_act, next_authorized_step) by R5.1-c and §7.7, read from the item's state and grant."""
    shown, owner = item["shown_as"], item["owner_host_id"] or item["owner_runner_id"]
    code = None if decision is None else (decision["code"] or f"decision {decision['decision_id'][:8]}")
    authority = (item["grant"] or {}).get("code") or (None if item["grant"] is None else
                                                       f"grant {item['grant']['grant_id'][:8]}")
    if shown == "queued":
        actor = item["awaiting_runner"] or ("a host with " + ", ".join(item["needs"]))
        return (f"{actor} claims it; Robert may stop or revoke it",
                {"actor": actor, "step": "claim", "authority": authority})
    if shown == "running":
        return (f"the owner {owner} renews and continues; Robert may stop or revoke it",
                {"actor": owner, "step": "continue", "authority": authority})
    if shown == "held":
        return (f"nobody until {item['lease_until']}; then the owner reconciles, or Robert may end or reassign it",
                {"actor": owner, "step": f"renew on return (nobody else may act until {item['lease_until']})",
                 "authority": authority})
    if shown == "lapsed":
        return (f"the owner {owner} reconciles; Robert may end or reassign it (grace until {item['grace_deadline']})",
                {"actor": owner, "step": "reconcile; Robert may end or reassign", "authority": authority})
    if shown == "paused":
        return (f"the owner {owner} reconciles on its return; Robert may end or reassign it "
                f"(grace until {item['grace_deadline']})",
                {"actor": owner, "step": "reconcile on return", "authority": authority})
    if shown == "ending":
        return (f"the owner {owner} records its open effect; then it ends {ending.get('state')} ({ending.get('code')})",
                {"actor": owner, "step": "finish or read back the open effect", "authority": authority})
    if shown == "unknown":
        return (f"Robert decides {code or 'it'}; nothing is retried",
                {"actor": "robert", "step": f"decide {code}" if code else "end or reassign", "authority": code})
    if shown == "proposed":
        return (f"Robert admits {code or 'it'}",
                {"actor": "robert", "step": f"admit {code}" if code else "admit", "authority": code})
    if shown == "interrupted":
        return ("Robert may re-admit it as a new item from its checkpoint, or leave it",
                {"actor": "robert", "step": "re-admit or leave it", "authority": None})
    return (f"nobody: it has ended ({shown})", None)


def _raised(events) -> dict:
    """key -> the notice.raised record (body, at)."""
    out = {}
    for e in events:
        if e["kind"] == "notice.raised":
            out[e["body"]["key"]] = {**e["body"], "raised_at": iso(e["at"])}
    return out


def _notice(key, kind, text, next_step, raised, **refs) -> dict:
    r = raised.get(key)
    return {"key": key, "kind": NOTICE_KIND[kind], "rule": kind, "text": text, "next_authorized_step": next_step,
            "raised_at": None if r is None else r["raised_at"], **refs}


def _notices(items, rec, now, values, hosts, cards, waiting, raised, unknowns) -> list:
    """Every notice the rules raise now (R7.4-d, R5.1-e), each with its key (one notice per key, DUP-7) and the next
    authorized step. Silent where nothing is needed (R7.4-e)."""
    out = []
    host = {h["host_id"]: h for h in hosts}
    legacy_rows = [_legacy_row(r, now) for r in rec["items"] if r.get("route") is None]
    for i in legacy_items(legacy_rows)[0]:
        out.append({**i, "rule": i["key"].split(":", 1)[0], "next_authorized_step": None,
                    "raised_at": (raised.get(i["key"]) or {}).get("raised_at")})
    for it in items:
        if it["protocol"] != 2:
            continue
        rid, g, label, step = it["item_id"], it["generation"], _label(it), it["next_authorized_step"]
        refs = {"item_id": rid, "card_id": it["card_id"], "step": it["step"]}
        if it["flags"]["stalled"]:
            ref = _progress_ref(it, now)
            proposal = review_request_id(rid, g, ref)
            out.append(_notice(
                f"runtime-stalled:{rid}:g{g}:{ref}", "stalled",
                f"{label[0].upper()}{label[1:]} ({name(it['actor'])} on {it['owner_host_id']}) has made no progress "
                f"for {int(it['progress_age_s'])} s; its owner is in contact and its lease is current. Nothing is "
                f"stopped. Next: MC may review it if you say so (review request {proposal[:8]}).",
                {"actor": "robert", "step": f"approve review request {proposal[:8]}, or wait",
                 "authority": None}, raised, proposal=proposal, **refs))
        if it["shown_as"] == "lapsed" and (host.get(it["owner_host_id"]) or {}).get("availability") == "always_on":
            out.append(_notice(
                f"runtime-lapsed:{rid}:g{g}", "lapsed",
                f"{label[0].upper()}{label[1:]}: lease lapsed ({it['lapse_cause'] or 'cause not recorded'}); owner "
                f"{it['owner_host_id']} unchanged; grace until {it['grace_deadline']}. Next: the owner reconciles; "
                f"you may end or reassign it.", step, raised, **refs))
        if it["flags"]["overdue"]:
            out.append(_notice(
                f"runtime-overdue:{rid}:g{g}", "overdue",
                f"{label[0].upper()}{label[1:]} is past its deadline ({it['deadline']}) and has not ended "
                f"({it['shown_as']}). Nothing ends it on its own. Next: {_step_words(step)}.", step, raised, **refs))
        for d in it["flags"]["drift"]:
            if d["kind"] == "out_of_scope_paths":
                why = f"its checkpoint {d['checkpoint']} touched paths outside the card's: {', '.join(d['paths'][:5])}"
            elif "elapsed_s" in d:
                why = f"{int(d['elapsed_s'])} s have passed against an estimate of {d['estimate_s']} s"
            else:
                why = f"{int(d['reservation_used'] * 100)}% of its reservation is used"
            out.append(_notice(
                f"runtime-drift:{rid}:{d['kind']}", "drift",
                f"{label[0].upper()}{label[1:]} drifts: {why}. The work is not stopped. Next: you may let it run, "
                f"stop it or revoke the card.", {"actor": "robert", "step": "let it run, stop it, or revoke the card",
                                                 "authority": None}, raised, **refs))
        blocking = [u for u in it["unknowns"] if u.startswith(BLOCKING) and u != "outcome"]
        if it["stored_state"] != "unknown" and blocking:
            for u in blocking:
                nxt = step
                if u == "observed_usage":       # the route stays closed until the usage is recorded (R8.3-c)
                    nxt = {"actor": "robert", "step": f"record what route {it['route']} used (resolve its usage hold)",
                           "authority": None}
                out.append(_notice(
                    f"runtime-unknown:{rid}:{u}", "unknown",
                    f"{label[0].upper()}{label[1:]}: the authority does not know {u.replace('_', ' ')}. "
                    f"Next: {_step_words(nxt)}.", nxt, raised, **refs))
        if it["stored_state"] in ("failed", "refused", "timed_out", "output_limit", "interrupted", "expired") \
                and not it["card_id"]:
            out.append(_notice(
                f"delegation-ended:{rid}", "ended",
                f"{label[0].upper()}{label[1:]} ended {it['stored_state'].replace('_', ' ')}"
                + (f" ({it['code']})" if it["code"] else "") + ". Next: you may re-admit it as a new item, or leave "
                "it.", step, raised, **refs))
    for card_id, c in sorted(cards.items(), key=lambda kv: kv[0] or ""):
        if c.get("ready") is not None and _recent(now, c.get("ready_at")):
            out.append(_notice(
                f"runtime-ready:{card_id}", "ready",
                f"Card {card_id} is ready for your merge: its last step ({c['ready'].get('step')}) completed and "
                f"every step was written by the authority.", {"actor": "robert", "step": "merge", "authority": None},
                raised, card_id=card_id))
        ended = c.get("ended")
        if ended is not None and _recent(now, c.get("ended_at")) and ended.get("state") not in ("revoked", "cancelled"):
            out.append(_notice(
                f"runtime-card-ended:{card_id}", "card_ended",
                f"Card {card_id} ended at step {ended.get('step')}: {ended.get('state')} ({ended.get('code')}). "
                f"Nothing follows it. Next: you may admit a new card.", {"actor": "robert", "step": "admit a new card",
                                                                         "authority": None}, raised, card_id=card_id))
    for h in hosts:
        for b in h["blocked"]:
            out.append(_notice(
                f"runtime-blocked:{h['host_id']}:{b['item_id']}:g{b['generation']}:{b['code']}", "blocked",
                f"Host {h['host_id']} blocked its task for item {_short(b['item_id'])} (generation {b['generation']}): "
                f"{b['code']}, since {b['since']}. It claims nothing until then. Next: an operator restarts the "
                f"runtime, which replays the task's pending records.",
                {"actor": "operator", "step": "restart the runtime", "authority": None}, raised,
                item_id=b["item_id"], host_id=h["host_id"]))
    sweeper = rec["liveness"].get("sweeper")
    threshold = values.get("gap_threshold_s")
    if threshold is not None and (sweeper is None or _age(now, sweeper["seen_at"]) > threshold):
        since = "never" if sweeper is None else iso(_at(sweeper["seen_at"]))
        out.append(_notice(
            f"runtime-sweeper-stale:{since}", "sweeper_stale",
            f"The authority's sweeper has not run since {since}. Nothing is shown as running past its lease (every "
            f"answer reads the clock), but lapses and grace ends are not written. Next: an operator restarts the "
            f"authority's sweeper.", {"actor": "operator", "step": "restart the sweeper", "authority": None}, raised))
    for e in rec["events"]:
        if e["kind"] == "judgment.escalated":
            rid = str(e["item_id"])
            out.append(_notice(
                f"runtime-escalated:{rid}", "escalated",
                f"MC reviewed item {_short(rid)} {e['body'].get('reviews')} times with no change and stops reviewing "
                f"it; the evidence is in its reviews ({', '.join(x[:8] for x in e['body'].get('reservations', []))})."
                f" Next: you decide what happens to it.", {"actor": "robert", "step": "decide", "authority": None},
                raised, item_id=rid))
    by_id = {d["decision_id"]: d for d in waiting}
    raised_decisions = [{"key": k, "decisions": v.get("decisions") or []} for k, v in raised.items()
                        if k.startswith(("decision:", "decisions:"))]
    for n in batch_decisions(waiting, raised_decisions):
        ds = [by_id[i] for i in n["decisions"] if i in by_id]
        if not ds:
            continue
        kind = "decisions" if n["key"].startswith("decisions:") else "decision"
        text = "\n\n".join(f"{d['code'] or 'decision ' + d['decision_id'][:8]}: {d['words']}" for d in ds)
        out.append(_notice(n["key"], kind, text, {"actor": "robert", "step": "answer", "authority": None}, raised,
                           decisions=n["decisions"], items=[d["item_id"] for d in ds],
                           solo=n.get("solo", kind == "decision"),
                           words=[{"decision_id": d["decision_id"], "words": d["words"],
                                   "text_sha256": d["text_sha256"]} for d in ds]))
    return out


def _recent(now, at, hours=24) -> bool:
    at = _at(at)
    return at is not None and now - at <= timedelta(hours=hours)


def _step_words(step) -> str:
    if not step:
        return "nothing is needed"
    return f"{step['actor']}: {step['step']}"


def _progress_ref(item, now=None) -> str:
    """The progress mark a stall is measured from (the stall episode): the later of its last progress and its
    lease's start, as stored, so every read of the same stall names it the same way (keys and proposal ids)."""
    return item.get("progress_from_at") or "unknown"


def review_request_id(item_id, generation, ref) -> str:
    """One review_request per stall episode of an item: the same stall read twice is the same proposal (T12)."""
    return str(uuid.uuid5(_PROPOSALS, f"{item_id}:{generation}:{ref}"))


def _proposals(items, events, now) -> list:
    """The review_request proposals (R7.5-d): one per stall episode of an item, which only Robert can turn into a
    judgment call. Recorded at the sweep (proposal.review_request); a live read shows a due one as ``due``."""
    out, seen = [], set()
    for e in events:
        if e["kind"] == "proposal.review_request":
            seen.add(e["body"]["proposal_id"])
            out.append({"proposal_id": e["body"]["proposal_id"], "kind": "review_request",
                        "item_id": e["body"]["item_id"], "generation": e["body"].get("generation"),
                        "state": "queued", "at": iso(e["at"]), "since": e["body"].get("since")})
    for it in items:
        if it["protocol"] == 2 and it["flags"]["stalled"]:
            pid = review_request_id(it["item_id"], it["generation"], _progress_ref(it, now))
            if pid not in seen:
                out.append({"proposal_id": pid, "kind": "review_request", "item_id": it["item_id"],
                            "generation": it["generation"], "state": "due", "at": None,
                            "since": _progress_ref(it, now)})
    return out


def _legacy_row(r, now) -> dict:
    return {"request_id": r["request_id"], "job_id": r.get("job_id"), "actor": r.get("actor"), "scope": r.get("scope"),
            "state": r["state"], "created_at": _at(r.get("created_at")), "started_at": _at(r.get("started_at")),
            "heartbeat_at": _at(r.get("heartbeat_at")), "brief": r.get("brief") or "",
            "code": (r.get("result_detail") or {}).get("code"), "now": now}


# ── owner work (§7.2, OWN-1…OWN-3) ────────────────────────────────────────────────────────────────────────────
OWNER_ACTS = ("item.admitted", "decision.resolved", "item.reassigned", "item.interrupted", "item.cancelled",
              "grant.revoked", "owner.act", "item.accepted")


def owner_work(rec, cards, raised) -> list:
    """A, R, C, O and J per card (§7.2), from the authority's records only:
    A  the owner's admissions: card.admitted and item.admitted for the card (source owner; S1-C's rule);
    R  the card's step items after its first that the authority did not compile from its records: not created_by
       authority, not a card step, or inputs that are not the recorded outputs its inputs_from names (OWN-2);
    C  owner calls about the card that no notice naming its item, step or decision preceded, and owner reads of the
       state while the card was open with no notice raised since the owner's previous read (the card's admission is
       Robert's go, not a check);
    O  merge, push and deploy recorded by the owner path (owner.act);
    J  decisions on uncertainty: an unknown resolved, or a reassignment, one per occurrence."""
    events = rec["events"]
    items = [r for r in rec["card_items"]]
    notice_times = []
    for e in events:
        if e["kind"] == "notice.raised":
            notice_times.append((_at(e["at"]), e["body"]))
    item_card = {str(r["request_id"]): r.get("card_id") for r in items}
    decision_card = {}
    decision_class = {}
    for e in events:
        if e["kind"] == "decision.queued":
            decision_card[str(e["event_id"])] = item_card.get(str(e["item_id"]))
            decision_class[str(e["event_id"])] = e["body"].get("class")
    out = []
    reads = sorted(_at(e["at"]) for e in events if e["kind"] == "owner.read")
    for card_id in sorted(c for c in cards if c and cards[c].get("record")):
        c = cards[card_id]
        mine = [r for r in items if r.get("card_id") == card_id]
        mine.sort(key=lambda r: (((r.get("facts") or {}).get("card") or {}).get("index", 0), _at(r.get("created_at"))))
        a = sum(1 for e in events if e["source"] == "owner" and e["kind"] in ("card.admitted", "item.admitted")
                and e["body"].get("card_id") == card_id)
        r_count = sum(1 for r in mine[1:] if _relayed(r))
        o = sum(1 for e in events if e["kind"] == "owner.act" and e["body"].get("card_id") == card_id)
        j, c_count = 0, 0
        resolved_items = set()
        for e in events:
            if e["source"] != "owner":
                continue
            card_of = e["body"].get("card_id") or item_card.get(str(e["item_id"])) or \
                decision_card.get(e["body"].get("decision_id"))
            if card_of != card_id:
                continue
            if e["kind"] == "decision.resolved" and decision_class.get(e["body"].get("decision_id")) in \
                    ("unknown_resolution", "reassignment") and e["body"].get("answer") not in ("withdrawn",):
                j += 1
                resolved_items.add((str(e["item_id"]), e["generation"]))
            if e["kind"] in OWNER_ACTS and not _prompted(e, notice_times):
                c_count += 1
        for e in events:
            if e["kind"] == "item.reassigned" and e["source"] == "owner" and \
                    item_card.get(str(e["item_id"])) == card_id and \
                    (str(e["item_id"]), e["body"].get("from_generation")) not in resolved_items:
                j += 1
        start, end = c.get("admitted_at"), c.get("ready_at") or c.get("ended_at")
        last = None
        for t in reads:
            if t is None or start is None or t < start or (end is not None and t > end):
                last = t
                continue
            if not any(last is None or n_at > last for n_at, _ in notice_times if n_at is not None and n_at <= t):
                c_count += 1
            last = t
        out.append({"card_id": card_id, "A": a, "R": r_count, "C": c_count, "O": o, "J": j})
    return out


def _relayed(row) -> bool:
    """A step item that a person, not the authority, wrote or fed (OWN-2, S1-C N3 and N11): created_by is a caller
    field, so the item must also be a card step whose input_artifacts are exactly the recorded outputs its
    inputs_from names."""
    card = (row.get("facts") or {}).get("card") or {}
    sources = card.get("inputs_from")
    if row.get("created_by") != "authority" or not card or sources is None:
        return True
    return list(row.get("input_artifacts") or []) != [{"ref": s.get("ref"), "sha256": s.get("sha256")}
                                                      for s in sources]


def _prompted(e, notice_times) -> bool:
    """Whether a notice naming this owner call's item, step or decision was raised before it (OWN-3)."""
    at = _at(e["at"])
    item = str(e["item_id"]) if e.get("item_id") else None
    decision = e["body"].get("decision_id")
    card = e["body"].get("card_id")
    for n_at, body in notice_times:
        if n_at is None or at is None or n_at > at:
            continue
        if decision and decision in (body.get("decisions") or []):
            return True
        if item and (item == body.get("item_id") or item in (body.get("items") or [])):
            return True
        if e["kind"] == "owner.act" and card and card == body.get("card_id") and body.get("rule") == "ready":
            return True
    return False


# ── the judgment reservation, as shown (R7.5-c) ────────────────────────────────────────────────────────────────
def _judgment(rec, values, events) -> dict:
    j = values.get("judgment") or {}
    day = rec.get("judgment_day") or {}
    reserved = {e["body"].get("reservation_id") for e in events if e["kind"] == "judgment.reserved"}
    done = {e["body"].get("reservation_id") for e in events if e["kind"] == "judgment.recorded"}
    pending = sum(1 for e in events if e["kind"] == "judgment.coalesced"
                  and e["body"].get("reservation_id") in reserved - done)
    return {"calls_today": day.get("calls"), "cap_calls": j.get("calls_per_day"),
            "usd_today": day.get("used"), "cap_usd": j.get("per_day"), "unit": j.get("unit"),
            "route": j.get("route"), "coalesced_pending": pending,
            "escalated": sorted(str(e["item_id"]) for e in events if e["kind"] == "judgment.escalated")}


# ── state.json and the read path (R7.6-a) ─────────────────────────────────────────────────────────────────────
def write_state_file(state_dir, doc) -> Path:
    """``<state_dir>/state.json``, written atomically (a whole new file renamed over the old, both fsynced)."""
    folder = Path(state_dir)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / "state.json"
    tmp = folder / f".state.json.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    data = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)
    dfd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return target


def read_state_file(path, now=None) -> dict:
    """The published snapshot as a view, with its age (R4.6): ``view_age_s`` from its ``as_of``; a file that cannot
    be read, or carries no time, has an unknown age (null and named), never zero."""
    now = now or datetime.now(timezone.utc)
    try:
        with open(path, "rb") as fh:
            raw = fh.read(32 * 1024 * 1024 + 1)
        doc = json.loads(raw.decode("utf-8"))
        if type(doc) is not dict or doc.get("schema") != SCHEMA:
            raise ValueError("schema")
    except (OSError, ValueError, UnicodeDecodeError):
        return {"schema": SCHEMA, "source": "authority", "as_of": None, "view_age_s": None,
                "unknowns": ["state_file_unreadable"], "items": [], "hosts": [], "notices": [],
                "decisions_waiting": []}
    as_of = _at(doc.get("as_of"))
    doc["view_age_s"] = None if as_of is None else round((now - as_of).total_seconds(), 3)
    if as_of is None:
        doc.setdefault("unknowns", []).append("view_age")
    return doc


def view_stale(doc, bound_s) -> bool:
    """R4.6-b: a rule reading this view refuses to fire when its age is unknown or past its bound (or no bound is
    configured: a missing value admits nothing)."""
    age = doc.get("view_age_s") if isinstance(doc, dict) else None
    return bound_s is None or age is None or age > bound_s or age < -60


def credential_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_readers(readers) -> dict:
    """The read credentials, by name: {name: {"sha256": hex, "role": observer|owner}}; only digests are configured."""
    if type(readers) is not dict or not readers:
        raise ValueError("state_readers_invalid")
    for k, v in readers.items():
        if not (type(k) is str and re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", k) and type(v) is dict
                and set(v) == {"sha256", "role"} and type(v["sha256"]) is str and _HEX.match(v["sha256"])
                and v["role"] in READ_ROLES):
            raise ValueError("state_readers_invalid")
    return readers


def read_request(headers, *, readers, provide, note_read=None) -> tuple:
    """One HTTP GET of the snapshot with a read credential (R7.6-a). -> (status, body). The bearer token is compared
    in constant time against each configured digest; only an observer or owner credential reads (a host's, or the
    staging watch's, is not one: 401). ``note_read(name, role)`` records the reader's contact (and an owner's read,
    which C counts). ``provide()`` gives the snapshot (state.json as a view, or built live). Never echoes a token."""
    check_readers(readers)
    header = ""
    for k, v in (headers or {}).items():
        if str(k).lower() == "authorization":
            header = str(v)
    presented = header[7:] if header.startswith("Bearer ") else ""
    found = None
    if _TOKEN.match(presented):
        digest = credential_digest(presented)
        for who, r in sorted(readers.items()):
            if hmac.compare_digest(digest, r["sha256"]):
                found = (who, r["role"])
    if found is None:
        return 401, {"error": "unauthorized"}
    if note_read is not None:
        note_read(*found)
    try:
        doc = provide()
    except Exception:
        return 503, {"error": "unavailable"}
    return 200, doc
