"""P-L1 ``mc-learning-consolidation``: the nightly second trigger for learning extraction
(standing spec v0.3.1 §8a.1 and §8a.3; harness v0.3.1 §§14.5–14.6). Default off.

Proposals only. The job reads a harness-published eligibility snapshot (committed, on-record
sources since its cursor), claims the shared extraction key (the closeout path uses the same
claim, so the same sources are never extracted twice), reserves budget before the one bounded
model call, and emits ``mc-learning-event/1`` events through the standing ingress. It never
adopts, grants, edits roles or policy, or writes outside its spool. The extraction itself, the
forbidden-lesson pre-check and the importer are harness-owned (Codex) and injected here.

Collaborators (contracts; fixtures until the harness ships them):
  eligibility.eligible(cursor, limit) -> {snapshot_id, sources:[{id, edition, digest, seq}],
        learning_refs:[...], policy_version, next_cursor, coverage}
  eligibility.read(snapshot_id, source_id) -> text, or raises Revoked
  claims.claim(key) -> "claimed" | "done" | "in_progress"; claims.complete(key, result_ref)
  extractor.extract(sources, learning_refs) -> [{kind, payload, target_record_id?}], extractor_version
  admission.reserve(estimated_input_tokens, max_output_tokens) -> "admitted" | "deferred" | "refused"
  ingress.post(event) / ingress.get(event_id)  (same client as the brief)

Bounds (enforced here): at most 10 sources and ~12,000 input tokens per night (estimated at four
characters per token, including referenced lessons and a fixed prompt allowance), at most 1,500
output tokens requested, one model call, no retries of a paid call. The watermark (cursor)
advances only over a contiguous prefix of sources whose events were acknowledged; a deferral or
budget refusal advances nothing.
"""
from __future__ import annotations

import hashlib
import json
import uuid

SCHEMA = "mc-learning-event/1"
KINDS = ("lesson_proposal", "evidence_link", "challenge", "retire_proposal")   # reflection_note: post-1.2
MAX_SOURCES = 10
MAX_INPUT_TOKENS = 12_000
MAX_OUTPUT_TOKENS = 1_500
PROMPT_ALLOWANCE_TOKENS = 1_500
STATE_NAME = "learning-consolidation"


class Revoked(Exception):
    """The snapshot no longer authorizes this source (410/revoked); no content is returned."""


def _tokens(text: str) -> int:
    return (len(text) + 3) // 4


def extraction_key(principal: str, environment: str, sources: list, versions: dict) -> str:
    basis = {"principal": principal, "environment": environment,
             "sources": [[s["id"], s["edition"], s["digest"]] for s in sources],
             "versions": dict(sorted(versions.items()))}
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode()).hexdigest()


def _event(*, key, environment, policy_hash, kind, occurred_at, source_refs, target, payload_ref, payload_digest,
           extractor_version, index) -> dict:
    return {"schema": SCHEMA,
            "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"mc-learning:{key}:{index}:{kind}")),
            "job_key": f"consolidation.{key[:32]}", "policy_hash": policy_hash, "environment": environment,
            "kind": kind, "occurred_at": occurred_at, "source_refs": source_refs,
            "target_record_id": target, "payload_ref": payload_ref, "payload_digest": payload_digest,
            "extractor_version": extractor_version}


def run_consolidation(*, spool, eligibility, claims, extractor, admission, ingress, principal: str,
                      environment: str, policy_hash: str, versions: dict, now_iso: str,
                      enabled: bool = False, kill=lambda: False, paused=lambda: False) -> dict:
    if not enabled:
        return {"state": "disabled"}
    if kill() or paused():
        return {"state": "paused"}
    state = spool.read("cycles", STATE_NAME) or {"cursor": None, "pending": None}

    # 1. Finish delivering any events from an earlier run before considering new sources.
    if state.get("pending"):
        return _deliver(spool, state, ingress, claims, kill, paused)

    snap = eligibility.eligible(state.get("cursor"), MAX_SOURCES)
    sources = list(snap.get("sources", []))[:MAX_SOURCES]
    if not sources:
        return {"state": "nothing_new", "coverage": snap.get("coverage")}

    # 2. Read within the snapshot's authority and the token bound; stop at the bound (the rest waits).
    taken, texts, budget = [], [], MAX_INPUT_TOKENS - PROMPT_ALLOWANCE_TOKENS
    budget -= sum(_tokens(json.dumps(r)) for r in snap.get("learning_refs", []))
    revoked = []
    for src in sources:
        if kill() or paused():
            return {"state": "paused"}
        try:
            text = eligibility.read(snap["snapshot_id"], src["id"])
        except Revoked:
            revoked.append({"id": src["id"], "disposition": "revoked"})   # auditable, no content copied
            taken.append(src)
            continue
        cost = _tokens(text)
        if cost > budget:
            break
        budget -= cost
        taken.append(src)
        texts.append({"id": src["id"], "edition": src["edition"], "text": text})
    if not taken:
        return {"state": "deferred", "reason": "first_source_exceeds_bound"}

    key = extraction_key(principal, environment, taken, versions)
    claim = claims.claim(key)
    if claim != "claimed":
        # The closeout path (or an earlier run) owns this exact source set; never a second extraction.
        if claim == "done":
            state["cursor"] = taken[-1].get("seq", state.get("cursor"))
            spool.put_state("cycles", STATE_NAME, state)
        return {"state": "skipped", "reason": f"extraction_{claim}"}

    if kill() or paused():
        return {"state": "paused"}
    if admission.reserve(MAX_INPUT_TOKENS - budget, MAX_OUTPUT_TOKENS) != "admitted":
        return {"state": "deferred", "reason": "budget_not_admitted"}      # nothing advances

    proposals, extractor_version = extractor.extract(texts, snap.get("learning_refs", []))
    source_refs = [{"id": s["id"], "digest": s["digest"]} for s in taken if s["id"] not in {r["id"] for r in revoked}]
    events = []
    for i, prop in enumerate(proposals or []):
        if prop.get("kind") not in KINDS:
            continue                               # never forward an unknown or disabled kind
        payload = json.dumps(prop.get("payload", {}), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode()).hexdigest()
        ref = f"learning.{key[:24]}.{i}"
        spool.put_immutable("delivery", ref, {"payload": payload, "sha256": digest})
        event = _event(key=key, environment=environment, policy_hash=policy_hash, kind=prop["kind"],
                       occurred_at=now_iso, source_refs=source_refs, target=prop.get("target_record_id"),
                       payload_ref=ref, payload_digest=digest, extractor_version=str(extractor_version), index=i)
        spool.put_immutable("outbox", event["event_id"], event)
        events.append(event["event_id"])
    state["pending"] = {"key": key, "events": events, "acked": [], "advance_to": taken[-1].get("seq"),
                        "revoked": revoked}
    spool.put_state("cycles", STATE_NAME, state)
    return _deliver(spool, state, ingress, claims, kill, paused)


def _deliver(spool, state, ingress, claims, kill, paused) -> dict:
    from .runner import IngressUncertain
    pending = state["pending"]
    for event_id in pending["events"]:
        if event_id in pending["acked"]:
            continue
        if kill() or paused():
            return {"state": "paused", "pending": len(pending["events"]) - len(pending["acked"])}
        event = spool.read("outbox", event_id)
        try:
            ack = ingress.get(event_id) or ingress.post(event)
        except IngressUncertain:
            return {"state": "delivery_unknown", "event_id": event_id}
        except Exception:
            return {"state": "delivery_pending", "event_id": event_id}
        if ack.get("state") in ("accepted", "already_applied", "deferred"):
            pending["acked"].append(event_id)
            spool.put_state("cycles", STATE_NAME, state)
        else:
            return {"state": "failed", "event_id": event_id, "code": ack.get("code")}
    # Every event acknowledged: complete the shared claim and advance the watermark.
    claims.complete(pending["key"], result_ref=",".join(pending["events"]))
    state["cursor"] = pending["advance_to"]
    state["pending"] = None
    spool.put_state("cycles", STATE_NAME, state)
    return {"state": "delivered", "events": len(pending["events"]), "revoked": len(pending.get("revoked", []))}
