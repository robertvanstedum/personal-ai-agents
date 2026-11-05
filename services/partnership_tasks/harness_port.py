"""The execution authority, as the outbox drainer sees it (B4 execution design v0.4 §4, v0.5 M1-M3, v0.6).

``HarnessDispatcher`` implements ``outbox.HarnessPort`` over the delegation dispatcher's store
(``core.mc_delegation_dispatcher.store.DispatcherStore``):

- ``admit``: re-reads the outbox entry from the task service (its card and Robert's approval, spent by the
  ``attempted`` record just made), checks the card compiles to exactly the entry's digest, and calls
  ``admit_card_for_task`` with that approval. The authority re-checks the approval inside its own transaction (digest,
  15 minutes by its clock, single use, this operation). Ids are uuid5 of the operation (v0.5 M1), so a resend under
  the same operation proposes the same grant and only one attempt can commit.
- ``lookup``: the authority's own answer for an admission (applied with its receipt, or absent; absent kills every
  attempt up to the one asked about, so it stays true).
- ``set_fence``: raises the authority's fence for the task (a later admission below it is refused there).

Digests: the harness writes bare hex, the task service ``sha256:<hex>``; converted here, at the boundary.

Refusal map (v0.6 Should 3): every ``AuthorityRefused`` is a ``HarnessRefused`` with its code, which the drainer
records as ``refused`` (``operation_id_reused`` as ``conflict``). A lost connection is ``DestinationUnreachable``
(recorded ``unknown``, settled later by lookup). Anything else is a bug and propagates.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from services.partnership_tasks.outbox import DestinationUnreachable, HarnessRefused

NS_GRANT = uuid.UUID("6b3f1c2e-5f1a-4d4e-9a51-0c2f6a7e9b11")
NS_PROPOSAL = uuid.UUID("0e9d7a54-2b6f-4c1d-8f3e-6a1b2c3d4e5f")
APPROVAL_WINDOW = timedelta(minutes=15)
PREFIX = "sha256:"


def _bare(d: str) -> str:
    if not isinstance(d, str) or not d.startswith(PREFIX):
        raise HarnessRefused("digest_invalid")
    return d[len(PREFIX):]


def _receipt(op_receipt: dict) -> dict:
    out = dict(op_receipt)
    if out.get("digest") and not str(out["digest"]).startswith(PREFIX):
        out["digest"] = PREFIX + out["digest"]
    return out


class HarnessDispatcher:
    def __init__(self, store, read_outbox, *, owner: str = "robert", catalogue=None, clock=None,
                 unreachable=(ConnectionError, TimeoutError)):
        """``read_outbox(operation_id) -> dict`` returns the task service's outbox entry (drainer seat).
        ``unreachable`` are the exception types that mean the authority could not be reached (psycopg2's
        OperationalError and InterfaceError on Dev)."""
        from core.mc_delegation_dispatcher import cards as card_mod
        from core.mc_delegation_dispatcher.store import AuthorityRefused
        self.store, self.read_outbox, self.owner = store, read_outbox, owner
        self.catalogue = catalogue or card_mod.Catalogue.load()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._refused, self._card_refused, self._unreachable = AuthorityRefused, card_mod.CardRefused, tuple(unreachable)

    def _call(self, fn):
        try:
            return fn()
        except self._refused as exc:
            raise HarnessRefused(str(exc).split(":")[0].strip() or "refused") from None
        except self._unreachable:
            raise DestinationUnreachable() from None

    def admit(self, operation_id, bundle_digest, *, task_id, fence_generation, attempt):
        ob = self.read_outbox(operation_id)
        if ob is None or ob.get("operation") != "admit_bundle" or ob.get("bundle_digest") != bundle_digest:
            raise HarnessRefused("entry_not_found")
        card, approval = ob.get("packet"), ob.get("approval")
        if not isinstance(card, dict):
            raise HarnessRefused("card_missing")
        try:
            compiled = self.catalogue.compile(card)
        except self._card_refused as exc:
            raise HarnessRefused("card_" + str(exc)[:40]) from None
        if PREFIX + compiled.digest != bundle_digest:
            raise HarnessRefused("card_digest_mismatch")
        if not approval or approval.get("card_digest") != bundle_digest:
            raise HarnessRefused("approval_missing")
        approved = datetime.fromisoformat(approval["approved_at"].replace("Z", "+00:00"))
        expires = min(approved + APPROVAL_WINDOW, self.clock() + APPROVAL_WINDOW)
        if expires <= self.clock():
            raise HarnessRefused("approval_expired")
        ids = dict(grant_id=str(uuid.uuid5(NS_GRANT, operation_id)),
                   proposal_id=str(uuid.uuid5(NS_PROPOSAL, operation_id)))
        answer = self._call(lambda: self.store.admit_card_for_task(
            operation_id, card, task_id=task_id, fence=fence_generation, attempt=attempt,
            grant_id=ids["grant_id"], owner_turn_ids=(approval["event_id"],), proposal_id=ids["proposal_id"],
            approved_operation_id=ids["proposal_id"], approval_expires_at=expires, by=self.owner,
            note=f"partnership task {task_id}; approval {approval['event_id']}",
            approval={"event_id": approval["event_id"], "operation_id": operation_id,
                      "card_digest": compiled.digest, "approved_at": approval["approved_at"]}))
        return {"receipt": _receipt(answer["receipt"]), "state": answer.get("state")}

    def lookup(self, operation_id, *, attempt):
        found = self._call(lambda: self.store.lookup_task_operation(operation_id, attempt=attempt))
        if found.get("state") == "applied":
            return {"state": "applied", "receipt": _receipt(found["receipt"])}
        return {"state": found.get("state", "absent")}

    def set_fence(self, operation_id, *, task_id, fence_generation, attempt):
        """The fence entry's own digest is what the authority records, so its receipt names this exact entry (the
        drainer's "applied" check requires receipt digest == the entry's bundle_digest)."""
        ob = self.read_outbox(operation_id)
        if ob is None or ob.get("operation") != "set_fence" or ob.get("task_id") != task_id:
            raise HarnessRefused("entry_not_found")
        answer = self._call(lambda: self.store.set_task_fence(operation_id, task_id=task_id, fence=fence_generation,
                                                              digest=ob["bundle_digest"]))
        self.stop_task_cards(task_id)
        return {"receipt": _receipt(answer["receipt"])}

    def stop_task_cards(self, task_id):
        """v0.5 S3: the fence stops new admissions; running work for the task stops too. Every card admitted for this
        task has its grant (uuid5 of its operation) revoked, which cancels its open items and ends the card, so no
        later step is written. Already revoked or ended: nothing to do. Refusals are logged, never raised: the fence
        itself is the guarantee for new work, and a card left running is reported by the next pass."""
        import logging
        rows = self._call(lambda: self.store.task_cards(task_id))
        for row in rows:
            gid = str(uuid.uuid5(NS_GRANT, row["operation_id"]))
            try:
                self.store.revoke(gid, by=self.owner, note=f"partnership task {task_id} fenced (cancel or correction)")
            except self._refused as exc:
                logging.getLogger("partnership.harness").info("revoke %s for %s: %s", gid, task_id, exc)
