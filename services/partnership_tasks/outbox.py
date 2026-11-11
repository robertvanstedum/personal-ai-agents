"""Outbox drainer: task service -> execution authority (spec v1.1 E02, E03, Q05; batch B2A).

One pass over one operation. The order is the contract:

1. ``attempted`` is journalled in the task service before anything is sent. It is accepted only while the entry is
   pending and the task's fence is unchanged, so a drainer that read a now-stale entry loses here and sends nothing.
2. The destination admits under the same operation id and checks the fence again inside its own transaction
   (amendment fencing and effect admission serialize at the execution authority, C06).
3. The destination's receipt for this exact bundle digest is journalled as ``applied``. A lost answer is journalled
   as ``unknown``; it is never retried blind. A later pass asks the destination's own lookup: applied -> applied;
   definitely absent -> pending (resent under the same id after revalidation); conflict -> conflict; no answer ->
   still unknown.

Command ids are derived from the operation and the entry's revision, so a crashed drainer's retry replays its own
record instead of writing a second one; a replayed ``attempted`` means another drainer process owns the send.
"""
from __future__ import annotations

from typing import Protocol

from core.canonical_json import digest


class HarnessRefused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DestinationUnreachable(Exception):
    """The send or its answer was lost; whether the destination committed is unknown. Any other exception is a bug
    and propagates: it is never recorded as uncertainty (S3)."""


class HarnessPort(Protocol):
    """What the task service needs from the execution authority. Fencing lives there (C06): ``set_fence`` raises the
    authority's own fence for a task, and ``admit`` refuses a bundle below it inside the authority's transaction. Every
    send and lookup carries the attempt number; once a lookup has answered "absent" for attempt n, the authority
    refuses attempt n forever, so "definitely absent" stays true (E03).

    Mapping to the real harness (not joined yet; needs its Postgres): ``admit`` -> DispatcherStore.admit_card with an
    operation-id -> card-id binding; ``lookup`` -> its card once-record. The harness has no task fence today: a
    fence row checked in admit_card's transaction is a required harness change (recorded for design disposition)."""

    def admit(self, operation_id: str, bundle_digest: str, *, task_id: str, fence_generation: int,
              attempt: int) -> dict: ...

    def set_fence(self, operation_id: str, *, task_id: str, fence_generation: int, attempt: int) -> dict: ...

    def lookup(self, operation_id: str, *, attempt: int) -> dict: ...


def _cmd(op: dict, observation: str, receipt=None, code=None, *, environment: str, runtime: dict) -> dict:
    payload = {"observation": observation, "destination_receipt": receipt, "code": code}
    return {"schema": "minimoi.partnership.command/1",
            "command_id": f"cmd-{op['operation_id']}-r{op['revision']}-{observation}",
            "kind": "outbox.record", "target": {"type": "outbox", "id": op["operation_id"]},
            "expected_revision": op["revision"], "payload": payload, "payload_digest": digest(payload),
            "environment": environment, "data_scope": op["data_scope"], "basis_ref": None,
            "causal_parent": {"store": "task_service", "id": op["operation_id"], "edition": str(op["revision"]),
                              "digest": None},
            "session_id": None, "runtime": runtime, "event_at": None}


RUNTIME = {"kind": "deterministic", "model": None, "provider": None, "build": "partnership_tasks.outbox/2",
           "config_version": None}


class NoticePort(Protocol):
    """The notice channel (B2D). Synthetic: record-only. Real: the existing notice/outbox machinery
    (action_waiting / supervision outbox), joined later. Same attempt and lookup rules as the harness."""

    def deliver(self, operation_id: str, payload_digest: str, *, task_id: str, kind: str, attempt: int) -> dict: ...

    def lookup(self, operation_id: str, *, attempt: int) -> dict: ...


def drain_once(svc, token: str, harness: HarnessPort, operation_id: str, notices: NoticePort | None = None, lead_wake: NoticePort | None = None) -> str:
    """Advance one operation by at most one send or one lookup. Returns the entry's state afterwards."""
    op = svc.get("outbox", operation_id, token)
    if op is None:
        return "not_found"
    destination = {"notices": notices, "harness": harness, "lead_wake": lead_wake}.get(op["destination"])
    if destination is None:
        return op["state"]                                  # no channel configured: it stays pending, never dropped

    def record(observation, receipt=None, code=None):
        return svc.submit(_cmd(op, observation, receipt, code, environment=svc.environment, runtime=RUNTIME), token)

    def now():
        return svc.get("outbox", operation_id, token)["state"]

    sends = sum(1 for a in op["attempts"] if a["observation"] == "attempted")
    if op["state"] == "pending":
        res = record("attempted")
        if res["outcome"] != "accepted" or res.get("replayed"):
            if res.get("code") == "fenced":
                op = svc.get("outbox", operation_id, token)
                record("fenced", code="task_changed_since_enqueue")       # S2-2: a stale entry is closed, not left
            return now()                                                  # lost the race or fenced: no send
        attempt = sends + 1
        op = svc.get("outbox", operation_id, token)
        try:
            if op["operation"] in ("notify", "wake_lead"):
                answer = destination.deliver(operation_id, op["bundle_digest"], task_id=op["task_id"],
                                             kind=op["packet"]["kind"] if op["operation"] == "wake_lead" else op["reason"] or "notice",
                                             attempt=attempt)
            elif op["operation"] == "set_fence":
                answer = harness.set_fence(operation_id, task_id=op["task_id"],
                                           fence_generation=op["fence_generation"], attempt=attempt)
            else:
                answer = harness.admit(operation_id, op["bundle_digest"], task_id=op["task_id"],
                                       fence_generation=op["fence_generation"], attempt=attempt)
        except HarnessRefused as exc:
            record("conflict" if exc.code == "operation_id_reused" else "refused", code=exc.code)
        except DestinationUnreachable:
            record("unknown", code="answer_lost")
        else:
            record("applied", receipt=answer["receipt"], code="answer")
        return now()

    if op["state"] in ("attempted", "unknown"):
        found = destination.lookup(operation_id, attempt=max(sends, 1))
        state = found.get("state")
        if state == "applied" and found["receipt"].get("digest") == op["bundle_digest"]:
            record("applied", receipt=found["receipt"], code="lookup")
        elif state in ("applied", "conflict"):
            record("conflict", code="destination_digest_differs")
        elif state == "absent":
            record("definitely_absent", code="lookup")
        elif op["state"] == "attempted":
            record("unknown", code="lookup_unavailable")
        return now()
    return op["state"]
