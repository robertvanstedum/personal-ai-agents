"""SYNTHETIC rig for the task service tests: invented seats, tokens, grants and commands. No real credential, store
or provider is touched; every service lives in a pytest temporary folder with environment ``synthetic``."""
from __future__ import annotations

import itertools

from core.canonical_json import digest
from services.partnership_tasks import contracts
from services.partnership_tasks.auth import Bindings, Grants, token_sha256
from services.partnership_tasks.service import TaskService

ALL = contracts.kinds()
AGENT_OPS = [k for k in ALL]
# token -> (seat, origin, operations, scopes, read_scopes, extra)
SEATS = {
    "tok-principal": ("principal", "owner_session", ALL, ["personal", "shared:build"], ["personal", "shared:build"]),
    "tok-cos": ("cos", "agent", AGENT_OPS, ["personal"], ["personal"]),
    "tok-mc": ("mc", "agent", AGENT_OPS, ["personal", "shared:build"], ["personal", "shared:build"]),
    "tok-worker": ("worker-a", "agent", ["task.transition", "source_event.record", "task.submit_artifacts"],
                   ["personal"], ["personal"]),
    "tok-scheduler": ("scheduler", "scheduler", ["task.lead_unavailable"], ["personal"], ["personal"]),
    "tok-platform": ("platform", "platform", ["source_event.record", "interpretation.record", "interpretation.dispose",
                                              "request.record"], ["personal"], ["personal"]),
    "tok-peer": ("codex", "peer", AGENT_OPS, ["shared:build"], ["shared:build"]),
    "tok-reader": ("auditor", "agent", AGENT_OPS, ["shared:build"], ["personal", "shared:build"]),
    "tok-drainer": ("outbox-drainer", "platform", ["outbox.record"], ["personal"], ["personal"]),
    # Codex finding 2: a platform seat granted outbox.record that is not the drainer (must be refused).
    "tok-platform-recorder": ("notice-relay", "platform", ["outbox.record"], ["personal"], ["personal"]),
    "tok-migration": ("migration", "scheduler", ["source_event.record", "interpretation.record",
                                                  "interpretation.dispose", "request.record"],
                      ["personal"], ["personal"]),
    "tok-builder": ("claude-code", "agent", AGENT_OPS, ["shared:build"], ["shared:build"]),
    # B4 v0.6 runner seats in the personal scope (delegated children of an owner task).
    "tok-claude-code": ("claude-code", "agent", AGENT_OPS, ["personal"], ["personal"]),
    "tok-codex": ("codex", "agent", AGENT_OPS, ["personal"], ["personal"]),
    "tok-expired": ("mc", "agent", AGENT_OPS, ["personal"], ["personal"]),
    "tok-revoked": ("mc", "agent", AGENT_OPS, ["personal"], ["personal"]),
}
NOW = "2026-11-04T16:00:00+00:00"


def bindings(environment: str = "synthetic") -> Bindings:
    recs = []
    for token, (seat, origin, ops, scopes, read) in SEATS.items():
        rec = {"binding_id": f"bind-{token[4:]}", "token_sha256": token_sha256(token), "seat": seat,
               "principal": "principal", "environment": environment, "origin": origin, "scopes": scopes,
               "read_scopes": read, "operations": sorted(set(ops))}
        if token == "tok-expired":
            rec["expires_at"] = "2026-11-01T00:00:00+00:00"
        if token == "tok-revoked":
            rec["revoked_at"] = "2026-11-02T00:00:00+00:00"
        recs.append(rec)
    return Bindings(recs)


GRANTS = Grants([
    {"grant_id": "grant-syn-lookup", "seats": ["cos", "mc"], "scopes": ["personal"], "expires_at": None},
    {"grant_id": "grant-syn-migration-cutover", "seats": ["migration"], "scopes": ["personal"], "expires_at": None},
    {"grant_id": "grant-syn-build", "seats": ["claude-code"], "scopes": ["shared:build"], "expires_at": None},
    {"grant_id": "grant-syn-expired", "seats": ["cos", "mc"], "scopes": ["personal"],
     "expires_at": "2026-11-01T00:00:00+00:00"},
])


def service(root, clock=lambda: NOW, environment="synthetic"):
    return TaskService(root, environment, bindings(environment), grants=GRANTS, clock=clock)


_ids = itertools.count(1)


def cmd(kind, target_type, target_id, payload, *, expected=None, scope="personal", cid=None, causal=None,
        runtime=None, session="syn-sess", environment="synthetic"):
    return {"schema": "minimoi.partnership.command/1", "command_id": cid or f"cmd-syn-{next(_ids):05d}", "kind": kind,
            "target": {"type": target_type, "id": target_id}, "expected_revision": expected, "payload": payload,
            "payload_digest": digest(payload), "environment": environment, "data_scope": scope, "basis_ref": None,
            "causal_parent": causal, "session_id": session,
            "runtime": runtime or {"kind": "model", "model": "synthetic-model", "provider": "synthetic",
                                   "build": None, "config_version": "syn-1"},
            "event_at": None}


def ref(store, id_, digest_=None, edition=None):
    return {"store": store, "id": id_, "edition": edition, "digest": digest_}


GRANT_REF = ref("grants", "grant-syn-lookup")


class Flow:
    """Short helpers that send one command each and assert it was accepted."""

    def __init__(self, svc):
        self.svc = svc

    def ok(self, command, token):
        res = self.svc.submit(command, token)
        assert res["outcome"] == "accepted", res
        return res

    def rev(self, target_type, target_id, token="tok-principal"):
        return self.svc.get(target_type, target_id, token)["revision"]

    def source(self, sid, text, token="tok-principal", correction_of=None, scope="personal", position=1):
        return self.ok(cmd("source_event.record", "source_event", sid,
                           {"source": {"store": "records", "id": f"evt-{sid}", "position": position},
                            "content": text, "correction_of": correction_of}, scope=scope), token)

    def interpret(self, iid, sources, outcome, token="tok-cos", corrections=(), supersedes=None, continuing=True,
                  scope="personal", recipients=("principal",)):
        return self.ok(cmd("interpretation.record", "interpretation", iid,
                           {"source_event_ids": list(sources), "correction_event_ids": list(corrections),
                            "proposed": {"outcome": outcome, "scope": scope, "recipients": list(recipients),
                                         "continuing": continuing},
                            "context_refs": [ref("artifacts", "target-list", edition="syn-ed-4")],
                            "supersedes": supersedes}, scope=scope), token)

    def adopt(self, iid, token="tok-cos", authority=None):
        authority = authority or {"kind": "existing_grant", "ref": GRANT_REF}
        return self.ok(cmd("interpretation.dispose", "interpretation", iid,
                           {"disposition": "adopted", "authority": authority},
                           expected=self.rev("interpretation", iid)), token)

    def request(self, rid, sid, text, iid=None, responder="cos", token="tok-cos", scope="personal"):
        return self.ok(cmd("request.record", "request", rid,
                           {"source_event_id": sid, "intent_text": text, "interpretation_ref": iid,
                            "responder": responder}, scope=scope), token)

    def promote(self, rid, tid, lead="mc", holder="mc", token="tok-cos", cid=None):
        return self.ok(cmd("request.promote", "request", rid,
                           {"task_id": tid, "lead": lead, "holder": holder, "outcome": "A reviewed design note.",
                            "done_when": "The lead accepts a reviewed draft.",
                            "return_window": {"at": "2026-11-05T14:00:00+00:00", "timezone": "America/Chicago",
                                              "basis": "default_0800_next_day", "basis_ref": None},
                            "parent_ref": None}, expected=self.rev("request", rid), cid=cid), token)

    def transition(self, tid, to, token="tok-mc", evidence=(), reason=None, waiting=None, fence=None):
        task = self.svc.get("task", tid, "tok-principal")
        return self.svc.submit(cmd("task.transition", "task", tid,
                                   {"to": to, "reason": reason, "waiting": waiting, "evidence_refs": list(evidence),
                                    "fence_generation": task["fence_generation"] if fence is None else fence},
                                   expected=task["revision"]), token)

    def ready_for_acceptance(self, tid, *, author="tok-mc", reviewer="tok-cos", tag="a", verdict="approve",
                             review_id=None):
        """Submit a synthetic artifact set and one review of exactly it; returns the set digest."""
        from core.canonical_json import digest as dg
        t = self.svc.get("task", tid, "tok-principal")
        art = [ref("artifacts", f"draft-{tag}", "sha256:" + dg(tag)[7:], edition="1")]
        self.ok(cmd("task.submit_artifacts", "task", tid, {"artifacts": art, "fence_generation": t["fence_generation"]},
                    expected=t["revision"]), author)
        t = self.svc.get("task", tid, "tok-principal")
        current = t["artifact_sets"][-1]["digest"]
        self.ok(cmd("task.record_review", "task", tid,
                    {"review_id": review_id or f"RV-syn-{tag}-1", "reviewed_set_digest": current, "verdict": verdict,
                     "input_digest": current,
                     "review_ref": ref("artifacts", f"review-{tag}", "sha256:" + dg("r" + tag)[7:], edition="1"),
                     "findings_ref": None}, expected=t["revision"]), reviewer)
        return current

    def accept(self, tid, set_digest, token="tok-mc", ack=(), fence=None):
        t = self.svc.get("task", tid, "tok-principal")
        return self.svc.submit(cmd("task.accept", "task", tid,
                                   {"artifact_set_digest": set_digest, "acknowledged_disagreements": list(ack),
                                    "fence_generation": t["fence_generation"] if fence is None else fence},
                                   expected=t["revision"]), token)

    def promoted_task(self, n="0001", lead="mc"):
        """source -> interpretation -> adopted with a grant -> request -> promoted task (all synthetic)."""
        self.source(f"SE-syn-{n}", "Draft a design note on agent coordination for tomorrow.")
        self.interpret(f"IN-syn-{n}", [f"SE-syn-{n}"], "A reviewed design note on agent coordination.")
        self.adopt(f"IN-syn-{n}")
        self.request(f"RQ-syn-{n}", f"SE-syn-{n}", "Design note on agent coordination.", iid=f"IN-syn-{n}")
        self.promote(f"RQ-syn-{n}", f"TK-syn-{n}", lead=lead, holder=lead)
        return f"TK-syn-{n}"


def approve_admission(svc, op, token="tok-principal", event=None):
    """Robert's approval of a pending admission, as the portal records it (B4 execution design v0.5 M2-M3): an owner
    action event naming the operation and the card digest he saw, then outbox.approve citing it. Returns the
    outbox.approve result. Refusals (a stale or non-pending entry) are returned, not raised."""
    import json as _json
    ob = svc.get("outbox", op, "tok-principal")
    if ob is None or ob["operation"] != "admit_bundle" or ob["state"] != "pending":
        return None
    sid = event or f"SE-syn-ok-{op[3:]}-{len(ob['attempts'])}"
    content = _json.dumps({"action": "admit", "operation_id": op, "card_digest": ob["bundle_digest"]},
                          sort_keys=True, separators=(",", ":"))
    svc.submit(cmd("source_event.record", "source_event", sid,
                   {"source": {"store": "portal-owner-action", "id": sid, "position": 1}, "content": content,
                    "correction_of": None}, scope=ob["data_scope"]), token)
    return svc.submit(cmd("outbox.approve", "outbox", op, {"card_digest": ob["bundle_digest"], "approval_event_id": sid},
                          expected=ob["revision"], scope=ob["data_scope"]), token)


def drain(svc, token, harness, operation_id, notices=None, approve=True):
    """drain_once after Robert's approval, for tests that are about draining, not approving."""
    from services.partnership_tasks.outbox import drain_once
    if approve:
        approve_admission(svc, operation_id)
    return drain_once(svc, token, harness, operation_id, notices) if notices is not None else \
        drain_once(svc, token, harness, operation_id)
