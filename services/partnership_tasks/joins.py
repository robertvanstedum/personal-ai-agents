"""Evidence joins for reconstruction (spec v1.1 Q03, Q04, Q06, Q07, Q08; batch B2E). Read-only and scoped.

Three questions a non-author reviewer must answer from records, never from an agent's memory:

- ``occurrence(...)`` (F14, "missed disk check"): was the occurrence expected by the obligation store, who owed it
  across its window (every duty interval that touches the window, overlaps flagged), what *trustworthy* check
  evidence exists (an authorised seat, the expected policy edition, a measurement and a threshold result), whether a
  breach was escalated and the escalation notice delivered, and whether the monitor itself was observed working.
  A missing or untrustworthy receipt is never "fulfilled"; an unobserved monitor is a coverage gap, never health; a
  missing receipt is missing evidence, not proof that no check ran.
- ``charges(...)`` (F13, "unexpected charge"): for a task (and its children), each attempt's grant and reservation
  link, reported usage, estimate with its rate version, and provider-billed lines, kept apart and totalled per
  currency. Lines are matched only by provider request id, each line once; conflicting duplicates are named, not
  silently dropped; account-level adjustments and lines for other requests are summarised as counts and per-currency
  amounts with no content. Nothing is presented as an exact per-agent charge.
- ``restore_check(...)`` (F16): on a scratch copy of a restored store (the restore itself is never written to), does
  the journal verify, is it anchored by at least one independently exported head, which heads are lost, do its
  harness links resolve in the harness, and which harness effects does the restored journal no longer know about.

SYNTHETIC in B2E: the obligation store is the real CoS store in a temporary folder; monitor receipts, attempts and
billing are fixtures; the harness is the synthetic harness. Real monitors, the harness attempt/reservation store and
provider billing join in B3/B4; no real charge attribution is claimed before then.
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
from decimal import Decimal
from pathlib import Path

from services.partnership_tasks.journal import Journal, JournalDamaged

OBLIGATION_SCOPE = "personal"        # the CoS v1 store holds personal data only


def _t(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _binding(svc, token):
    return svc.bindings.resolve(token, svc._clock())


def _readable(svc, token):
    binding = _binding(svc, token)
    return None if binding is None else binding.read_scopes


def expected_from_store(cos_store, occurrence_id: str) -> dict | None:
    """S2-4: the window and policy edition come from the obligation definition itself (its revision is the policy
    edition; its reminder window in its zone on the occurrence's local date is the window), never from the monitor."""
    definition_id, local = occurrence_id.split("@", 1)
    d = cos_store.get("cos.obligation", definition_id)
    if d is None:
        return None
    tz = ZoneInfo(d["recurrence"]["tz"])
    day = date.fromisoformat(local)
    if d["recurrence"]["kind"] == "weekly" and day.weekday() != d["recurrence"]["weekday"]:
        return None
    w = d["reminder_window"]
    start = datetime.combine(day, time.fromisoformat(w["from"]), tz)
    end = datetime.combine(day, time.fromisoformat(w["to"]), tz)
    return {"occurrence_id": occurrence_id, "window_start": start.isoformat(), "window_end": end.isoformat(),
            "policy_edition": f"{definition_id}@r{d['revision']}"}


# ---- F14: obligation occurrence ------------------------------------------------------------------------------------

def occurrence(svc, token: str, occurrence_id: str, *, cos_store, monitor: dict, duty_task_id: str | None,
               authorized_monitor_seats: set, now: str) -> dict | None:
    """None when the caller may not read personal obligations (same answer as "no such occurrence")."""
    readable = _readable(svc, token)
    if readable is None or OBLIGATION_SCOPE not in readable:
        return None
    gaps, coverage = [], []
    occ = cos_store.occurrence(occurrence_id)
    in_store = occ.events > 0
    coverage.append({"store": "obligations", "status": "verified" if in_store else "absent"})
    if not in_store:
        gaps.append("The obligation store has no record of this occurrence; its expectation is not established.")
    expected = expected_from_store(cos_store, occurrence_id)
    claimed = next((e for e in monitor.get("expected", []) if e["occurrence_id"] == occurrence_id), None)
    if expected is not None and claimed is not None and (
            _t(claimed["window_start"]) != _t(expected["window_start"])
            or _t(claimed["window_end"]) != _t(expected["window_end"])
            or claimed["policy_edition"] != expected["policy_edition"]):
        gaps.append("The monitor's own expectation differs from the obligation definition; the definition is used.")
    if expected is None:
        gaps.append("The obligation definition does not establish this occurrence's window or policy edition.")
        return {"occurrence_id": occurrence_id, "status": "unknown", "coverage": coverage, "gaps": gaps,
                "synthetic": True}
    start, end = _t(expected["window_start"]), _t(expected["window_end"])

    owners = []
    duty_task = svc.get("task", duty_task_id, token) if duty_task_id else None
    if duty_task is not None:
        for a in duty_task["assignments"]:
            a_from = _t(a["effective_from_at"])
            a_to = None if a["effective_to_at"] is None else _t(a["effective_to_at"])
            if a["role"] == "duty" and a_from <= end and (a_to is None or a_to > start):
                owners.append({"seat": a["seat"], "from": a["effective_from_at"], "to": a["effective_to_at"]})
    if not owners:
        gaps.append("No duty assignment overlaps the window; who owed it then is unknown.")
    elif len(owners) > 1:
        gaps.append("Duty changed hands or overlapped during the window: " + ", ".join(o["seat"] for o in owners))
    owner_seats = {o["seat"] for o in owners}

    beats = sorted((_t(h["at"]), h["ok"]) for h in monitor.get("heartbeats", []))
    observed = any(ok and start <= at <= end for at, ok in beats)
    last_ok = max((at for at, ok in beats if ok), default=None)
    coverage.append({"store": "monitor", "status": "observed" if observed else "not_observed"})

    trusted, untrusted = [], []
    for r in (r for r in monitor.get("receipts", []) if r["occurrence_id"] == occurrence_id and r["kind"] == "check"):
        why = []
        if r.get("seat") not in authorized_monitor_seats | owner_seats:
            why.append("seat not authorised for this duty")
        if r.get("policy_edition") != expected["policy_edition"]:
            why.append("other policy edition")
        if not r.get("measured"):
            why.append("no measurement")
        if r.get("threshold_result") not in ("ok", "breach"):
            why.append("no threshold evaluation")
        (untrusted.append(dict(r, untrusted_because=why)) if why else trusted.append(r))
    before = [r for r in trusted if _t(r["at"]) < start]
    on_time = [r for r in trusted if start <= _t(r["at"]) <= end]
    late = [r for r in trusted if _t(r["at"]) > end]
    if before:
        gaps.append("Check receipts dated before the window do not count for it.")
    if untrusted:
        gaps.append(f"{len(untrusted)} check receipt(s) are not trustworthy and do not count.")
    if on_time:
        status = "fulfilled"
    elif late:
        status = "late"
    elif _t(now) <= end:
        status = "open"
    elif untrusted:
        status = "unverified"
    elif observed:
        status = "missed"
        gaps.append("No trustworthy check receipt in the window although the monitor was observed working; this is "
                    "missing evidence of the check, not proof that no check ran.")
    else:
        status = "unknown"
        gaps.append("The monitor was not observed working during the window: a coverage gap, not health.")

    escalations = []
    for b in (r for r in on_time + late if r["threshold_result"] == "breach"):
        esc = next((e for e in monitor.get("receipts", []) if e["occurrence_id"] == occurrence_id
                    and e["kind"] == "escalation" and _t(e["at"]) >= _t(b["at"])), None)
        if esc is None:
            gaps.append(f"Breach measured at {b['at']} was not escalated.")
            continue
        notice = svc.get("outbox", esc["notice_operation_id"], token) if esc.get("notice_operation_id") else None
        state = None if notice is None else notice["state"]
        if state not in ("acknowledged", "read"):
            gaps.append(f"Escalation at {esc['at']} has no delivered notice (state: {state}).")
        escalations.append(dict(esc, notice_state=state))
    if occ.state in ("satisfied", "satisfied_late") and status not in ("fulfilled", "late"):
        gaps.append(f"The obligation store says {occ.state} but no trustworthy check receipt supports it.")
    if occ.state in ("open", "none") and status in ("fulfilled", "late"):
        gaps.append("A trustworthy check exists but the obligation store still shows the occurrence open.")
    return {"occurrence_id": occurrence_id, "status": status, "policy_edition": expected["policy_edition"],
            "window": {"start": expected["window_start"], "end": expected["window_end"]},
            "threshold": None if claimed is None else claimed.get("threshold"),
            "threshold_source": "monitor expectation (the obligation definition carries no threshold in CoS v1)",
            "owners_during_window": owners,
            "obligation_store": {"state": occ.state, "evidence": list(occ.evidence)},
            "checks": on_time + late, "untrusted_receipts": untrusted, "escalations": escalations,
            "monitor": {"observed_in_window": observed,
                        "last_successful_check": None if last_ok is None else last_ok.isoformat()},
            "coverage": coverage, "gaps": gaps, "synthetic": True}


# ---- F13: charges ---------------------------------------------------------------------------------------------------

def charges(svc, token: str, task_id: str, *, attempts: list[dict], billing: dict) -> dict | None:
    """``attempts``: the harness's attempt records (fixture in B2E), each with attempt_id, operation_id, task_id,
    executor_seat, provider, route, status, retry_of, grant_id, reservation_id, reserved, reported_usage,
    estimate {amount, currency, rate_version}, provider_request_id. ``billing``: {"grants": {id: {cap, currency}},
    "invoice_lines": [{line_id, amount, currency, provider_request_id|null}]}. None when the task is not readable."""
    task = svc.get("task", task_id, token)
    if task is None:
        return None
    readable = _readable(svc, token)
    principal_view = _binding(svc, token).is_owner
    family = {task_id}
    folder = svc.journal.root / "tasks"
    for p in folder.glob("TK-*.json") if folder.is_dir() else []:
        t = svc.get("task", p.stem, token)
        if t and t["parent_ref"] == task_id:
            family.add(t["task_id"])
    gaps = []
    mine = [a for a in attempts if a["task_id"] in family]
    seen_req = {}
    for a in mine:
        rid = a.get("provider_request_id")
        if rid in seen_req:
            gaps.append(f"Attempts {seen_req[rid]} and {a['attempt_id']} share request id {rid}; its lines are "
                        "not allocated to either.")
        elif rid:
            seen_req[rid] = a["attempt_id"]
    clash = {rid for rid in seen_req if sum(1 for a in attempts if a.get("provider_request_id") == rid) > 1}
    for rid in sorted(clash):
        if sum(1 for a in mine if a.get("provider_request_id") == rid) == 1:
            # S3: shared with an attempt this caller may not see; the line is not attributed, the other is not named.
            gaps.append(f"Request id {rid} is also used outside this task; its lines are not allocated.")
    by_request = {a["provider_request_id"]: a for a in mine
                  if a.get("provider_request_id") and a["provider_request_id"] not in clash}
    lines, conflicts = {}, set()
    for line in billing.get("invoice_lines", []):
        prior = lines.get(line["line_id"])
        if prior is not None and prior != line:
            conflicts.add(line["line_id"])
        lines.setdefault(line["line_id"], line)
    for lid in sorted(conflicts):
        gaps.append(f"Invoice line {lid} appears with different contents; it is excluded until reconciled.")
    allocated, account_level, other_requests = {}, [], []
    per_request_lines = {}
    for line in lines.values():
        if line["line_id"] in conflicts:
            continue
        rid = line.get("provider_request_id")
        if rid and rid in by_request:
            allocated.setdefault(by_request[rid]["attempt_id"], []).append(line)
            per_request_lines.setdefault(rid, []).append(line["line_id"])
        elif rid is None:
            account_level.append(line)
        else:
            other_requests.append(line)
    for rid, ids in per_request_lines.items():
        if len(ids) > 1:
            gaps.append(f"Request {rid} has several invoice lines ({', '.join(ids)}); allocated to the same attempt.")

    def by_currency(items, amount=lambda x: x["amount"], currency=lambda x: x["currency"]):
        out = {}
        for x in items:
            out[currency(x)] = out.get(currency(x), Decimal("0")) + Decimal(str(amount(x)))
        return {k: str(v) for k, v in sorted(out.items())}

    rows = []
    for a in mine:
        mine_lines = allocated.get(a["attempt_id"], [])
        for field, what in (("grant_id", "grant"), ("reservation_id", "reservation")):
            if not a.get(field):
                gaps.append(f"{a['attempt_id']}: no {what} link (Q04).")
        if a.get("grant_id") and a["grant_id"] not in billing.get("grants", {}):
            gaps.append(f"{a['attempt_id']}: grant {a['grant_id']} is not on record.")
        if a.get("provider_request_id") and not mine_lines and a["provider_request_id"] not in clash:
            gaps.append(f"{a['attempt_id']}: no provider-billed line yet (billing may lag).")
        if not a.get("provider_request_id"):
            gaps.append(f"{a['attempt_id']}: no provider request id; its billing cannot be matched.")
        rows.append({k: a.get(k) for k in ("attempt_id", "operation_id", "task_id", "status", "retry_of",
                                          "executor_seat", "provider", "route", "grant_id", "reservation_id",
                                          "reserved", "reported_usage", "estimate")}
                    | {"billed": {"by_currency": by_currency(mine_lines), "lines": [l["line_id"] for l in mine_lines]}})
    estimates = [a["estimate"] for a in mine if a.get("estimate")]
    caps = {gid: g for gid, g in billing.get("grants", {}).items() if gid in {a.get("grant_id") for a in mine}}
    return {"task_id": task_id, "tasks_included": sorted(family), "authorized_caps": caps, "attempts": rows,
            "totals": {"estimated_by_currency": by_currency(estimates),
                       "billed_allocated_by_currency": by_currency([l for ls in allocated.values() for l in ls])},
            # S2-A: account-level and other requests' amounts concern work this caller may not see; only the
            # principal's view carries them. Others learn only that unallocated amounts are not attributed here.
            "unallocated": ({"account_level": {"count": len(account_level), "by_currency": by_currency(account_level)},
                             "other_requests": {"count": len(other_requests),
                                                "by_currency": by_currency(other_requests)}}
                            if principal_view else {"shown": False,
                                                    "detail": "Unallocated account amounts are visible to the "
                                                              "principal only."}),
            "attribution": "Billed amounts are attributed only through provider request ids, each invoice line "
                           "once; unallocated amounts are not assigned to any seat or task; estimates are not charges.",
            "readable_scopes": sorted(readable), "gaps": gaps, "synthetic": True}


# ---- F16: restore and link integrity --------------------------------------------------------------------------------

def restore_check(restored_root, *, doc_dirs, exported_heads: list[dict], harness) -> dict:
    """Never writes to ``restored_root``: the check runs on a scratch copy. ``harness`` answers ``peek(op)``
    (applied/absent, no side effects) and ``admissions()``; None means the harness is unavailable."""
    scratch = Path(tempfile.mkdtemp(prefix="restore-check-"))
    try:
        shutil.copytree(restored_root, scratch / "store")
        j = Journal(scratch / "store", result_schema="minimoi.partnership.result/1",
                    clock=lambda: "1970-01-01T00:00:00+00:00", doc_dirs=tuple(doc_dirs))
        gaps = []
        try:
            counts = j.recover()
        except (JournalDamaged, OSError) as exc:
            return {"journal": "damaged", "detail": str(exc), "anchored": False, "heads": [], "links": [],
                    "unknown_effects": [],
                    "gaps": ["The restored journal or a document does not verify; nothing is claimed from it."]}
        if counts.get("quarantined"):
            gaps.append("The restored journal ended in a torn line; that tail is lost.")
        if counts.get("recovered"):
            gaps.append(f"{counts['recovered']} intent(s) were unfinished in the restore and would be finished on "
                        "first use.")
        heads = [{"seq": h["seq"], "reproduced": j.verify_head(h)} for h in exported_heads]
        anchored = any(h["reproduced"] for h in heads)
        if not exported_heads:
            gaps.append("No independently exported head was supplied: the chain is unanchored and a rewritten "
                        "history would verify too (Q06).")
        for h in heads:
            if not h["reproduced"]:
                gaps.append(f"Exported head at seq {h['seq']} is not reproduced: history after the backup is lost "
                            "or differs.")
        outbox = j.view(lambda: [j.read(f"outbox/{p.name}") for p in sorted((scratch / "store" / "outbox").glob("*.json"))]
                        if (scratch / "store" / "outbox").is_dir() else [])
        links, known = [], set()
        for ob in outbox:
            if ob["destination"] != "harness":
                continue
            known.add(ob["operation_id"])
            if ob["state"] in ("acknowledged", "attempted", "unknown", "conflict", "pending"):
                found = None if harness is None else harness.peek(ob["operation_id"])
                links.append({"operation_id": ob["operation_id"], "journal_state": ob["state"],
                              "harness": found})
                if found is None:
                    gaps.append(f"Harness unavailable: {ob['operation_id']} is unverified.")
                elif ob["state"] == "pending" and found == "applied":
                    gaps.append(f"{ob['operation_id']} is pending in the journal but already applied in the harness; "
                                "the next send will be deduplicated.")
                elif ob["state"] == "acknowledged" and found != "applied":
                    gaps.append(f"{ob['operation_id']} is acknowledged in the journal but absent in the harness.")
        unknown_effects = [] if harness is None else [op for op in harness.admissions() if op not in known]
        for op in unknown_effects:
            gaps.append(f"The harness holds effect {op} that the restored journal does not know; it must be "
                        "reconciled (the harness dedups a re-send under the same id), never re-created under a new one.")
        return {"journal": "verified" if anchored else "verified_unanchored", "anchored": anchored, "heads": heads, "links": links,
                "unknown_effects": unknown_effects, "gaps": gaps}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ---- Q07: the scoped query with joined stores ----------------------------------------------------------------------

def reconstruct_joined(svc, token: str, start_type: str, start_id: str, *, harness=None, attempts=None,
                       billing=None, at: str | None = None) -> dict | None:
    """The scoped reconstruction (reconstruct.py) with B2E's synthetic joins: each harness execution reference is
    looked up without side effects (effect applied/absent, with the exact payload digest the journal recorded), and
    costs come from ``charges``. Coverage says which stores were joined; anything not joined stays not_joined."""
    from services.partnership_tasks.reconstruct import Reconstructor
    out = Reconstructor(svc).reconstruct(start_type, start_id, token, at=at)
    if out is None or out.get("integrity") != "verified":
        return out
    task = out["chain"]["task"]
    coverage = {c["store"]: c["status"] for c in out["coverage"]}
    effects = []
    if harness is not None and task is not None:
        for r in task["execution_refs"]:
            # Only admissions this task's outbox sent are effects; other harness refs are actor-reported evidence.
            if r["store"] == "harness" and svc.get("outbox", r["id"], token) is not None:
                effects.append({"operation_id": r["id"], "payload_digest": r["digest"], "harness": harness.peek(r["id"])})
        coverage["harness"] = "synthetic_joined"
    out["effects"] = effects
    if attempts is not None and billing is not None and task is not None:
        out["costs"] = dict(charges(svc, token, task["task_id"], attempts=attempts, billing=billing),
                            status="synthetic_joined")
        coverage["billing_fixture"] = "synthetic_joined"
    out["coverage"] = [{"store": k, "status": v} for k, v in coverage.items()]
    return out


def from_charge(svc, token: str, provider_request_id: str, *, attempts, billing, harness=None) -> dict | None:
    """Q07: start from a charge reference (a provider request id on an invoice line): find the attempt that made it,
    then its task, then the joined reconstruction. None when the attempt's task is not readable to the caller."""
    found = [a for a in attempts if a.get("provider_request_id") == provider_request_id]
    visible = [a for a in found if svc.get("task", a["task_id"], token) is not None]
    if not visible:
        return None                                       # nothing readable: same answer as "no such charge"
    if len(found) > 1:
        # S3: ambiguous, not missing. Which tasks share it is not disclosed.
        return {"schema": "minimoi.partnership.charge_lookup/1", "answer": "ambiguous",
                "detail": "This provider request id belongs to more than one attempt; it cannot be attributed."}
    return reconstruct_joined(svc, token, "task", found[0]["task_id"], harness=harness, attempts=attempts,
                              billing=billing)
