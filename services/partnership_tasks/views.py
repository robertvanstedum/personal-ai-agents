"""Shared read views and meeting close (spec v1.1 C02, C04, C05, D03; batch B3A; acceptance F08). SYNTHETIC-first.

Views are reads of the task service, scoped by the caller's binding, never a second list:
- ``work_for(seat)``: what the seat leads, holds, is waiting on, and is a delegate or verifier for.
- ``primary_status(task)``: one primary status per subject (C04): blocked or uncertain first, then meaningful
  progress or readiness; the full fact ladder is returned alongside for inspection, never as competing badges.

Meeting close (F08): the convener saves the meeting's actionable items at once. Every item gets ids derived from
the close id and the item's position, so a close that was only partly saved is reconciled by running the same close
again: saved items replay their receipts, missing ones are saved, nothing is duplicated, and the close reports
``complete`` only when every item has a receipt. Records owns the meeting record; this keeps only a reference.
"""
from __future__ import annotations

from core.canonical_json import digest
from services.partnership_tasks.journal import JournalDamaged
from services.partnership_tasks.service import ACTIVE_TASK, DIRS, StoreUnavailable

RUNTIME = {"kind": "deterministic", "model": None, "provider": None, "build": "partnership_tasks.views/1",
           "config_version": None}


def _all(svc, kind: str, readable) -> list[dict]:
    def read():
        folder = svc.journal.root / DIRS[kind]
        out = []
        for p in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            rec = svc.journal.read(f"{DIRS[kind]}/{p.name}")
            if rec and rec["data_scope"] in readable:
                out.append(rec)
        return out
    try:
        return svc.journal.view(read)
    except (JournalDamaged, OSError) as exc:
        raise StoreUnavailable(type(exc).__name__) from exc


def _lead_and_blocked(svc, token, task) -> tuple[str | None, bool]:
    """The task's accountable lead and whether it is blocked, the same way the service decides (effective lead:
    the parent's current lead for a child; blocked: waiting, or the parent in a lead_unavailable episode)."""
    if task["parent_ref"] is None:
        return task["lead"], task["state"] == "waiting"
    parent = svc.get("task", task["parent_ref"], token)
    if parent is None:
        return None, task["state"] == "waiting"            # not visible: no lead claimed from the child's copy
    episode = bool(parent["waiting"] and parent["waiting"]["reason"] == "lead_unavailable")
    return parent["lead"], task["state"] == "waiting" or episode


def primary_status(svc, token: str, task: dict) -> dict | None:
    """C04: one primary status. Order: blocked (waiting, or held by its parent's lead episode) > uncertain (an
    effect whose outcome is unknown or in conflict) > ready for acceptance (an applicable non-author approval of the
    current set) > in progress > submitted (saved, not yet picked up) > ended."""
    binding = svc.bindings.resolve(token, svc._clock())
    if binding is None or task["data_scope"] not in binding.read_scopes:
        return None
    readable = binding.read_scopes
    outbox = [o for o in _all(svc, "outbox", readable) if o["task_id"] == task["task_id"]]
    uncertain = [o["operation_id"] for o in outbox if o["operation"] == "admit_bundle"
                 and o["state"] in ("unknown", "conflict")]
    current = task["artifact_sets"][-1]["digest"] if task["artifact_sets"] else None
    authors = {seat for st in task["artifact_sets"] if st["digest"] == current for seat in st["authored_by"]}
    approved = any(r["applicable_to_current"] and r["verdict"] == "approve" and r["reviewer"] not in authors
                   for r in task["reviews"])
    lead, blocked = _lead_and_blocked(svc, token, task)
    ladder = {"state": task["state"], "waiting": task["waiting"], "lead": lead, "uncertain_effects": uncertain,
              "current_set": current, "approved_for_current": approved, "fence_generation": task["fence_generation"],
              "pending_sends": [o["operation_id"] for o in outbox if o["state"] in ("pending", "attempted")]}
    if blocked and task["state"] == "waiting":
        primary = {"status": "blocked", "reason": task["waiting"]["reason"],
                   "who_can_unblock": task["waiting"]["who_can_unblock"]}
    elif blocked:
        primary = {"status": "blocked", "reason": "lead_unavailable", "who_can_unblock": "principal",
                   "note": "held by the parent task's lead episode"}
    elif uncertain:
        primary = {"status": "uncertain", "reason": "an effect's outcome is not known yet"}
    elif task["state"] == "working" and current and approved:
        primary = {"status": "ready_for_acceptance"}
    elif task["state"] == "working":
        primary = {"status": "in_progress"}
    elif task["state"] == "submitted":
        primary = {"status": "submitted", "note": "saved; not yet picked up"}       # saved != queued != working
    else:
        primary = {"status": task["state"]}
    return {"task_id": task["task_id"], "primary": primary, "ladder": ladder}


def work_for(svc, token: str, seat: str | None = None) -> dict | None:
    """A seat's own work; the principal may ask about any seat. Scoped to the caller's read scopes."""
    binding = svc.bindings.resolve(token, svc._clock())
    if binding is None:
        return None
    seat = seat or binding.seat
    if seat != binding.seat and not binding.is_owner:
        return None
    readable = binding.read_scopes
    tasks = {t["task_id"]: t for t in _all(svc, "task", readable)}
    active = [t for t in tasks.values() if t["state"] in ACTIVE_TASK]
    roles = {"leads": [], "holds": [], "waiting_on": [], "delegate_or_verifier": []}
    for t in active:
        lead, _ = _lead_and_blocked(svc, token, t)
        if lead == seat:
            roles["leads"].append(t["task_id"])
        if t["holder"] == seat:
            roles["holds"].append(t["task_id"])
        if t["waiting"] and t["waiting"]["who_can_unblock"] == seat:
            roles["waiting_on"].append(t["task_id"])
        if any(a["seat"] == seat and a["role"] in ("delegate", "verifier") and a["to_revision"] is None
               for a in t["assignments"]):
            roles["delegate_or_verifier"].append(t["task_id"])
    statuses = {tid: primary_status(svc, token, tasks[tid])
                for tid in sorted({x for v in roles.values() for x in v})}
    return {"seat": seat, **{k: sorted(v) for k, v in roles.items()}, "status": statuses}


# ---- F08: meeting close -------------------------------------------------------------------------------------------

def _cmd(kind, ttype, tid, payload, cid, scope, environment, causal):
    return {"schema": "minimoi.partnership.command/1", "command_id": cid, "kind": kind,
            "target": {"type": ttype, "id": tid}, "expected_revision": None, "payload": payload,
            "payload_digest": digest(payload), "environment": environment, "data_scope": scope, "basis_ref": None,
            "causal_parent": causal, "session_id": None, "runtime": RUNTIME, "event_at": None}


def close_meeting(svc, token: str, close: dict) -> dict:
    """``close``: {close_id, meeting_ref: {store, id, edition}, scope, items: [{text, responder, position}]}.
    Each actionable item becomes a source event (the words as recorded) and a request for its responder.

    Item ids derive from (environment, scope, meeting, close id, item position), so another meeting or scope can never
    collide. Each item ends with one disposition:
      saved    its request exists with exactly these words and responder (now or from an earlier close, by any
               convener: a takeover reconciles);
      pending  the store was unavailable; closing again reconciles it;
      refused  it cannot be saved as given (a different item already holds that place, or the caller lacks the
               right): visible, with its code, never reported as pending (C07)."""
    meeting = close["meeting_ref"]
    key = digest({"environment": svc.environment, "scope": close["scope"], "meeting": [meeting["store"], meeting["id"]],
                  "close_id": close["close_id"]})[7:23]
    results = []
    for n, item in enumerate(close["items"], start=1):
        sid, rid = f"SE-mc{key}-{n}", f"RQ-mc{key}-{n}"
        receipts, disposition, code = [], None, None
        try:
            existing_rq = svc.get("request", rid, token)
            existing_se = svc.get("source_event", sid, token)
        except StoreUnavailable:
            results.append({"item": n, "source_event_id": sid, "request_id": rid, "disposition": "pending",
                            "code": "store_unavailable", "receipts": []})
            continue
        if existing_rq is not None:
            same = existing_rq["intent"]["text"] == item["text"] and existing_rq["responder"] == item["responder"]
            disposition, code = ("saved", None) if same else ("refused", "a_different_item_is_saved_here")
        else:
            if existing_se is not None and existing_se["content"] != item["text"]:
                disposition, code = "refused", "a_different_item_is_saved_here"
            else:
                if existing_se is None:
                    se = _cmd("source_event.record", "source_event", sid,
                              {"source": {"store": meeting["store"], "id": meeting["id"], "position": item["position"]},
                               "content": item["text"], "correction_of": None},
                              f"close-{key}-{n}-se", close["scope"], svc.environment,
                              {"store": meeting["store"], "id": meeting["id"], "edition": meeting.get("edition"),
                               "digest": None})
                    r1 = svc.submit(se, token)
                    receipts.append(r1)
                    if r1["outcome"] != "accepted":
                        disposition = "pending" if r1["outcome"] == "failed" else "refused"
                        code = r1.get("code")
                if disposition is None:
                    rq = _cmd("request.record", "request", rid,
                              {"source_event_id": sid, "intent_text": item["text"], "interpretation_ref": None,
                               "responder": item["responder"]},
                              f"close-{key}-{n}-rq", close["scope"], svc.environment,
                              {"store": "task_service", "id": sid, "edition": None, "digest": None})
                    r2 = svc.submit(rq, token)
                    receipts.append(r2)
                    disposition = {"accepted": "saved", "failed": "pending"}.get(r2["outcome"], "refused")
                    code = r2.get("code")
        results.append({"item": n, "source_event_id": sid, "request_id": rid, "disposition": disposition,
                        "code": code, "receipts": receipts})
    counts = {d: sum(1 for r in results if r["disposition"] == d) for d in ("saved", "pending", "refused")}
    state = ("complete" if counts["saved"] == len(results) else
             "partial" if counts["saved"] else "not_saved")
    say = [f"Saved {counts['saved']} of {len(results)}."]
    if counts["pending"]:
        say.append(f"{counts['pending']} pending (the store was unavailable); closing again saves them.")
    for r in results:
        if r["disposition"] == "refused":
            say.append(f"Item {r['item']} refused: {r['code']}.")
    return {"schema": "minimoi.partnership.meeting_close/1", "close_id": close["close_id"], "meeting_ref": meeting,
            "state": state, "counts": counts, "items": results, "say": " ".join(say)}
