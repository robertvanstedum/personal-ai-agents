"""P-L1 ``mc-learning-consolidation``: the nightly second trigger for learning extraction
(standing spec v0.3.1 §8a.1 and §8a.3; harness v0.3.1 §§14.5–14.6). Default off.

Proposals only. The job reads a harness-published eligibility snapshot (committed, on-record
sources since its cursor), claims the shared extraction key (the closeout path uses the same
claim, so the same sources are never extracted twice), reserves budget before the one bounded
model call, and emits ``mc-learning-event/1`` events through the standing ingress. It never
adopts, grants, edits roles or policy, or writes outside its spool. The extraction itself, the
forbidden-lesson pre-check and the importer are harness-owned (Codex) and injected here.

Durable phases (``cycles/learning-consolidation`` → ``current``), each written before its effect:

  claimed ──reserve──▶ reserved ──▶ calling ──result written──▶ extracted ──all acked──▶ (done)
     │                    │            │
     └─ deferred/paused ──┴─ release   └─ crash or error with unknown spend ──▶ held

* A budget deferral, refusal, pause or kill before the call **releases** the claim and the
  reservation: nothing is stranded and the sources stay eligible.
* A restart in ``claimed``/``reserved`` (no paid call was made) releases and starts over.
* A restart in ``calling`` with no result record means unknown spend/output: the claim and the
  reservation are **held** for an operator disposition; never a second paid call.
* The extraction result is written immutably **before** any event is prepared; events are derived
  deterministically from it, so a restart resends the same event IDs without calling the model.
* The cursor follows commit sequence and advances only over the contiguous prefix of sources the
  run considered, once every event is acknowledged. Overflow stays queued. Revoked or missing
  sources get a durable reason-code disposition, without content.

Collaborator contracts (proposed to Codex; fixtures until the harness ships them):

  eligibility.eligible(cursor, limit) -> {snapshot_id, sources:[{id, edition, digest, seq}],
        learning_refs:[...], policy_version, next_cursor, coverage}
  eligibility.read(snapshot_id, source_id) -> text, or raises Revoked
  claims.claim(key, owner=, source_set=) -> {status: claimed|in_progress|done|held, claim_id,
        phase, result_ref}        (the same owner re-claiming its own key gets its claim back)
  claims.advance(claim_id, phase, reservation_id=, result_ref=)
  claims.release(claim_id, reason) · claims.hold(claim_id, reason) · claims.complete(claim_id, result_ref)
  admission.reserve(key, input_tokens, output_tokens) -> {status: admitted|deferred|refused,
        reservation_id}           (idempotent by key)
  admission.release(reservation_id) · admission.settle(reservation_id, usage)
  extractor.extract(texts, learning_refs) -> {proposals:[{kind, payload, target_record_id?}],
        extractor_version, usage}; raises NotDispatched only when no request left the host
  ingress.post(event) / ingress.get(event_id)  (same client as the brief)

Bounds (enforced here): at most 10 sources and ~12,000 input tokens per night (estimated at four
characters per token, including referenced lessons and a fixed prompt allowance), at most 1,500
output tokens requested, one model call, no retries of a paid call, payloads ≤ 64 KiB.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from . import ack as ack_mod

SCHEMA = "mc-learning-event/1"
KINDS = ("lesson_proposal", "evidence_link", "challenge", "retire_proposal")   # reflection_note: post-1.2
MAX_SOURCES = 10
MAX_INPUT_TOKENS = 12_000
MAX_OUTPUT_TOKENS = 1_500
PROMPT_ALLOWANCE_TOKENS = 1_500
MAX_PAYLOAD_BYTES = 64 * 1024
STATE_NAME = "learning-consolidation"
OWNER = "mc-learning-consolidation"


class Revoked(Exception):
    """The snapshot no longer authorizes this source (410/revoked); no content is returned."""


class NotDispatched(Exception):
    """The extractor is certain no request left the host (nothing could have been spent)."""


def _tokens(text: str) -> int:
    return (len(text) + 3) // 4


def extraction_key(principal: str, environment: str, sources: list, versions: dict) -> str:
    basis = {"principal": principal, "environment": environment,
             "sources": [[s["id"], s["edition"], s["digest"]] for s in sources],
             "versions": dict(sorted(versions.items()))}
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()


def _result_name(key: str) -> str:
    return f"learning-result.{key[:40]}"


def _event(*, key, environment, policy_hash, kind, occurred_at, source_refs, target, payload_ref, payload_digest,
           extractor_version, index) -> dict:
    return {"schema": SCHEMA,
            "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"mc-learning:{key}:{index}:{kind}")),
            "job_key": f"consolidation.{key[:32]}", "policy_hash": policy_hash, "environment": environment,
            "kind": kind, "occurred_at": occurred_at, "source_refs": source_refs,
            "target_record_id": target, "payload_ref": payload_ref, "payload_digest": payload_digest,
            "extractor_version": extractor_version}


def _state(spool) -> dict:
    return spool.read("cycles", STATE_NAME) or {"cursor": None, "current": None}


def _save(spool, state):
    spool.put_state("cycles", STATE_NAME, state)


def _drop_current(spool, state, claims, admission, reason: str):
    """Give back a claim (and any reservation) that never reached a paid call."""
    cur = state.get("current") or {}
    if cur.get("reservation_id"):
        admission.release(cur["reservation_id"])
    if cur.get("claim_id"):
        claims.release(cur["claim_id"], reason)
    state["current"] = None
    _save(spool, state)


def _hold(spool, state, claims, reason: str) -> dict:
    cur = state["current"]
    claims.hold(cur["claim_id"], reason)
    cur.update(phase="held", held_reason=reason)
    _save(spool, state)
    return {"state": "held", "reason": reason, "key": cur["key"]}


def run_consolidation(*, spool, eligibility, claims, extractor, admission, ingress, principal: str,
                      environment: str, policy_hash: str, versions: dict, now_iso: str,
                      enabled: bool = False, kill=lambda: False, paused=lambda: False) -> dict:
    if not enabled:
        return {"state": "disabled"}
    state = _state(spool)
    cur = state.get("current")
    if state.get("unsettled") and not cur:
        _settle_queue(spool, state, admission)

    # 1. Resume whatever an earlier run left, by its durable phase.
    if cur:
        phase = cur["phase"]
        if phase == "held":
            return {"state": "held", "reason": cur.get("held_reason"), "key": cur["key"]}
        if phase in ("claimed", "reserved"):
            _drop_current(spool, state, claims, admission, "restart_before_call")
        elif phase == "calling":
            if spool.read("delivery", _result_name(cur["key"])) is None:
                return _hold(spool, state, claims, "model_call_outcome_unknown")
            return _prepare_and_deliver(spool, state, claims, admission, ingress, environment, policy_hash,
                                        now_iso, kill, paused)
        elif phase == "extracted":
            return _deliver(spool, state, ingress, claims, admission, kill, paused)

    if kill() or paused():
        return {"state": "paused"}

    # 2. Select: a contiguous, commit-ordered prefix within the source and token bounds.
    try:
        snap = eligibility.eligible(state.get("cursor"), MAX_SOURCES)
    except Exception:
        return {"state": "deferred", "reason": "eligibility_unavailable"}   # before any claim: nothing held
    sources = list(snap.get("sources", []))[:MAX_SOURCES]
    if not sources:
        return {"state": "nothing_new", "coverage": snap.get("coverage")}
    last = state.get("cursor")
    for s in sources:
        if not isinstance(s.get("seq"), int) or isinstance(s.get("seq"), bool) or (last is not None and s["seq"] <= last):
            return {"state": "invalid_snapshot", "reason": "sources_not_in_commit_order"}
        last = s["seq"]

    taken, texts, revoked = [], [], []
    budget = MAX_INPUT_TOKENS - PROMPT_ALLOWANCE_TOKENS
    budget -= sum(_tokens(json.dumps(r)) for r in snap.get("learning_refs", []))
    for src in sources:
        if kill() or paused():
            return {"state": "paused"}
        try:
            text = eligibility.read(snap["snapshot_id"], src["id"])
        except Revoked:
            revoked.append({"id": src["id"], "edition": src["edition"], "reason_code": "revoked"})
            taken.append(src)
            continue
        except Exception:
            return {"state": "deferred", "reason": "source_read_unavailable"}  # before any claim
        if text is None:
            revoked.append({"id": src["id"], "edition": src["edition"], "reason_code": "missing"})
            taken.append(src)
            continue
        cost = _tokens(text)
        if cost > budget:
            break                                  # overflow stays queued for the next night
        budget -= cost
        taken.append(src)
        texts.append({"id": src["id"], "edition": src["edition"], "text": text})
    if not taken:
        return {"state": "deferred", "reason": "first_source_exceeds_bound"}
    for d in revoked:                              # durable, auditable, no content
        spool.put_immutable("findings", f"learning-disposition.{d['id']}.{d['edition']}", d)
    if not texts:                                  # nothing readable: dispositions only, no call
        state["cursor"] = taken[-1]["seq"]
        _save(spool, state)
        return {"state": "nothing_readable", "revoked": len(revoked)}

    # 3. Claim the shared key; record the claim before anything else can happen.
    key = extraction_key(principal, environment, taken, versions)
    claim = claims.claim(key, owner=OWNER,
                         source_set=[[s["id"], s["edition"], s["digest"]] for s in taken])
    status = claim.get("status")
    if status != "claimed":
        # The closeout path (or an earlier run) owns this exact source set; never a second extraction.
        if status == "done":
            state["cursor"] = taken[-1]["seq"]
            _save(spool, state)
        return {"state": "skipped", "reason": f"extraction_{status}"}
    state["current"] = {"key": key, "claim_id": claim["claim_id"], "phase": "claimed",
                        "snapshot_id": snap["snapshot_id"], "advance_to": taken[-1]["seq"],
                        "source_refs": [{"id": s["id"], "digest": s["digest"]} for s in taken
                                        if s["id"] not in {r["id"] for r in revoked}],
                        "revoked": revoked, "reservation_id": None}
    _save(spool, state)

    # 4. Reserve budget before the one paid call; anything but admission gives everything back.
    if kill() or paused():
        _drop_current(spool, state, claims, admission, "paused")
        return {"state": "paused"}
    reservation = admission.reserve(key, MAX_INPUT_TOKENS - budget, MAX_OUTPUT_TOKENS)
    if reservation.get("status") != "admitted":
        if reservation.get("reservation_id"):
            state["current"]["reservation_id"] = reservation["reservation_id"]
        _drop_current(spool, state, claims, admission, "budget_not_admitted")
        return {"state": "deferred", "reason": "budget_not_admitted"}       # nothing advances
    state["current"].update(phase="reserved", reservation_id=reservation["reservation_id"])
    _save(spool, state)
    claims.advance(claim["claim_id"], "reserved", reservation_id=reservation["reservation_id"])

    # 5. The one model call. ``calling`` is durable first: a crash from here on is unknown spend.
    if kill() or paused():
        _drop_current(spool, state, claims, admission, "paused")
        return {"state": "paused"}
    # Freeze the delivery envelope before the call: a replay rebuilds byte-identical events.
    state["current"].update(phase="calling", occurred_at=now_iso, policy_hash=policy_hash, environment=environment)
    _save(spool, state)
    claims.advance(claim["claim_id"], "calling")
    try:
        result = extractor.extract(texts, snap.get("learning_refs", []))
    except NotDispatched:
        _drop_current(spool, state, claims, admission, "extractor_not_dispatched")
        return {"state": "deferred", "reason": "extractor_not_dispatched"}
    except Exception:
        return _hold(spool, state, claims, "model_call_outcome_unknown")
    proposals = []
    for prop in (result or {}).get("proposals") or []:
        if not isinstance(prop, dict) or prop.get("kind") not in KINDS or not isinstance(prop.get("payload"), dict):
            continue                               # never forward an unknown or disabled kind
        payload = json.dumps(prop["payload"], sort_keys=True, separators=(",", ":"))
        if len(payload.encode()) > MAX_PAYLOAD_BYTES:
            continue
        target = prop.get("target_record_id")
        proposals.append({"kind": prop["kind"], "payload": payload,
                          "target_record_id": target if isinstance(target, str) else None})
    spool.put_immutable("delivery", _result_name(key), {
        "key": key, "proposals": proposals, "extractor_version": str((result or {}).get("extractor_version")),
        "usage": (result or {}).get("usage"), "reservation_id": reservation["reservation_id"]})
    return _prepare_and_deliver(spool, state, claims, admission, ingress, environment, policy_hash, now_iso,
                                kill, paused)


def _prepare_and_deliver(spool, state, claims, admission, ingress, environment, policy_hash, now_iso,
                         kill, paused) -> dict:
    """From the immutable result: payloads and outbox events (deterministic), then delivery."""
    cur = state["current"]
    result = spool.read("delivery", _result_name(cur["key"]))
    # The frozen envelope wins over this run's arguments (L-F3); only a pre-freeze record falls back.
    occurred_at = cur.get("occurred_at") or now_iso
    policy_hash = cur.get("policy_hash") or policy_hash
    environment = cur.get("environment") or environment
    events = []
    for i, prop in enumerate(result["proposals"]):
        digest = hashlib.sha256(prop["payload"].encode()).hexdigest()
        ref = f"learning.{cur['key'][:24]}.{i}"
        spool.put_immutable("delivery", ref, {"payload": prop["payload"], "sha256": digest})
        event = _event(key=cur["key"], environment=environment, policy_hash=policy_hash, kind=prop["kind"],
                       occurred_at=occurred_at, source_refs=cur["source_refs"], target=prop["target_record_id"],
                       payload_ref=ref, payload_digest=digest, extractor_version=result["extractor_version"],
                       index=i)
        spool.put_immutable("outbox", event["event_id"], event)
        events.append(event["event_id"])
    cur.update(phase="extracted", result_ref=_result_name(cur["key"]), events=events,
               acked=cur.get("acked", []))
    _save(spool, state)
    claims.advance(cur["claim_id"], "extracted", result_ref=cur["result_ref"])
    return _deliver(spool, state, ingress, claims, admission, kill, paused)


def _settle_queue(spool, state, admission):
    """Budget reconciliation outlives delivery (L-F2): every unsettled reservation stays queued,
    durably, until the gateway confirms settlement."""
    remaining = []
    for item in state.get("unsettled") or []:
        result = spool.read("delivery", item["result_ref"]) or {}
        try:
            admission.settle(item["reservation_id"], result.get("usage"))
        except Exception:
            remaining.append(item)
    if remaining != (state.get("unsettled") or []):
        state["unsettled"] = remaining
        _save(spool, state)
    return remaining


def _deliver(spool, state, ingress, claims, admission, kill, paused) -> dict:
    from .runner import IngressUncertain
    cur = state["current"]
    if not cur.get("queued_for_settlement"):
        state.setdefault("unsettled", []).append({"reservation_id": cur["reservation_id"],
                                                  "result_ref": cur["result_ref"]})
        cur["queued_for_settlement"] = True
        _save(spool, state)
    _settle_queue(spool, state, admission)
    for event_id in cur["events"]:
        if event_id in cur["acked"]:
            continue
        if kill() or paused():
            return {"state": "paused", "pending": len(cur["events"]) - len(cur["acked"])}
        event = spool.read("outbox", event_id)
        try:
            ack = ingress.get(event_id) or ingress.post(event)
        except IngressUncertain:
            return {"state": "delivery_unknown", "event_id": event_id}
        except Exception:
            return {"state": "delivery_pending", "event_id": event_id}
        if ack_mod.committed(ack, event_id):
            cur["acked"].append(event_id)
            _save(spool, state)
            continue
        valid = ack_mod.check(ack, event_id)
        if valid is not None and valid["state"] == "deferred":
            return {"state": "delivery_pending", "event_id": event_id}      # stays pending (L-F1)
        if isinstance(ack, dict) and ack.get("state") == "refused":
            return {"state": "failed", "event_id": event_id, "code": ack.get("code")}
        return {"state": "delivery_unknown", "event_id": event_id, "code": "ack_invalid"}
    # Every event acknowledged: complete the shared claim and advance the watermark.
    claims.complete(cur["claim_id"], result_ref=cur["result_ref"])
    state["cursor"] = cur["advance_to"]
    state["current"] = None
    _save(spool, state)
    unsettled = len(state.get("unsettled") or [])
    return {"state": "delivered", "events": len(cur["events"]), "revoked": len(cur.get("revoked", [])),
            "settlement": "pending" if unsettled else "settled"}


def clear_held(*, spool, claims, operator: str, disposition: str) -> dict:
    """Operator disposition for a held extraction (unknown spend/output). ``release`` makes the
    sources eligible again (a later run may pay again: the operator's decision, recorded);
    the reservation is left to the gateway's own reconciliation."""
    if disposition not in ("release",) or not operator:
        raise ValueError("disposition_invalid")
    state = _state(spool)
    cur = state.get("current")
    if not cur or cur.get("phase") != "held":
        return {"state": "nothing_held"}
    spool.put_immutable("findings", f"learning-held-disposition.{cur['key'][:40]}",
                        {"key": cur["key"], "claim_id": cur["claim_id"], "reason": cur.get("held_reason"),
                         "operator": operator, "disposition": disposition})
    claims.release(cur["claim_id"], f"operator_{disposition}")
    state["current"] = None
    _save(spool, state)
    return {"state": "released", "key": cur["key"]}
