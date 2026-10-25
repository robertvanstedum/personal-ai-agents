"""Guest access requests through the MC action path: observe, propose, reject, verify.

Journey (handoffs/JOURNEY_MAP_GUEST_REJECT_2026-10-21.md): MC finds the request (observation,
no grant), proposes the exact effect, Robert approves it once (a finite exact-approval grant is
issued by trusted owner-control code, never here), and execution rejects it and verifies the
result by read-back.

A guest reject is a LOCAL database change, so the whole execution is one transaction in the
caller's connection: current grant recheck → receipt claim and dispatch → conditional UPDATE →
read-back → evidence row → receipt finish. Either all of it commits or none of it does; there is
no partially applied state to reconcile. The caller commits; any exception means roll back.

Scope v1: website requests (``guild:<id>``) only. File registrations (``pending:<token>``) live in
the auth store, outside this transaction, and are refused here.
"""
from __future__ import annotations

import json
import re
from uuid import uuid4

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.grants import CapabilityRule, GrantExecution, GrantStore
from core.minimoi_actions.receipts import EffectIntent, ReceiptStore, _cursor
from core.minimoi_actions.requests import ToolRequest

CAPABILITY = "guest.reject_request"
VERSION = 1
# Operator catalogue entry: consequential, so an exact owner approval is always required; no spend.
CATALOGUE_RULE = CapabilityRule(VERSION, True, True)
_REF = re.compile(r"^guild:([1-9][0-9]{0,9})$")
_FIELDS = "id, name, email, domain, status, requested_at, actioned_at"


def reject_request(ref: str) -> ToolRequest:
    """The exact canonical request for one reject. Arguments carry the reference only."""
    if not isinstance(ref, str) or not _REF.match(ref):
        raise ActionContractError("guest_ref_invalid")
    return ToolRequest(CAPABILITY, VERSION, json.dumps({"ref": ref}, separators=(",", ":")).encode())


def target_version(row: dict) -> str:
    """What the owner approved must still be true at execution: same request, still open."""
    return f"guest-request:{row['id']}:requested:{row['requested_at'].isoformat()}"


class GuestActions:
    def __init__(self, grants: GrantStore, *, owner_principal: str):
        if type(grants) is not GrantStore or not isinstance(owner_principal, str) or not owner_principal:
            raise ActionContractError("guest_action_config_invalid")
        self.grants, self.owner, self.receipts = grants, owner_principal, ReceiptStore()

    # ── context and template ────────────────────────────────────────────────
    def _owner(self, context):
        if type(context) is not ActionContext or context.principal_id != self.owner \
                or context.privacy_mode not in ("recorded", "on_record"):
            raise ActionContractError("guest_action_context_refused")

    def _now(self, cur, context):
        cur.execute("SELECT clock_timestamp() AS now")
        now = cur.fetchone()["now"]
        if context.has_expired(now):
            raise ActionContractError("guest_action_context_expired")
        return now

    def _template(self, context, intent) -> int:
        self._owner(context)
        if (type(intent) is not EffectIntent or intent.request.operation != CAPABILITY
                or intent.request.operation_version != VERSION or intent.proposal_id is None
                or intent.expected_target_version is None or intent.grant_id is None):
            raise ActionContractError("guest_exact_owner_action_required")
        args = intent.request.arguments
        if set(args) != {"ref"}:
            raise ActionContractError("guest_arguments_invalid")
        match = _REF.match(args["ref"]) if isinstance(args["ref"], str) else None
        if match is None:
            raise ActionContractError("guest_ref_invalid")
        return int(match.group(1))

    # ── observation (no grant, no effect) ───────────────────────────────────
    def open_requests(self, conn, context, *, limit: int = 50) -> list[dict]:
        """Open website requests, oldest first, for MC to find the right one. Read-only."""
        self._owner(context)
        with _cursor(conn) as cur:
            self._now(cur, context)
            cur.execute(f"SELECT {_FIELDS} FROM guild.guest_requests WHERE status='requested' "
                        "ORDER BY requested_at, id LIMIT %s", (min(max(limit, 1), 200),))
            rows = cur.fetchall()
        return [{"ref": f"guild:{r['id']}", "name": r["name"], "email": r["email"], "domain": r["domain"],
                 "requested_at": r["requested_at"].isoformat(), "target_version": target_version(r)}
                for r in rows]

    def find(self, conn, context, text: str) -> list[dict]:
        """Open requests whose name or email contains ``text`` (case-insensitive). Several matches
        mean MC must ask which one; none means it must say so. Never guesses."""
        if not isinstance(text, str) or not 2 <= len(text.strip()) <= 120:
            raise ActionContractError("guest_find_text_invalid")
        needle = text.strip().lower()
        return [r for r in self.open_requests(conn, context, limit=200)
                if needle in (r["name"] or "").lower() or needle in r["email"].lower()]

    # ── effect ──────────────────────────────────────────────────────────────
    def prepare(self, conn, context, intent, *, current_policy_version) -> dict:
        """After owner approval: consume the grant and prepare the receipt, only if the request is
        still exactly what was approved."""
        request_id = self._template(context, intent)
        with _cursor(conn) as cur:
            self._now(cur, context)
            cur.execute(f"SELECT {_FIELDS} FROM guild.guest_requests WHERE id=%s", (request_id,))
            row = cur.fetchone()
        if row is None:
            raise ActionContractError("guest_request_not_found")
        if row["status"] != "requested" or target_version(row) != intent.expected_target_version:
            raise ActionContractError("guest_request_changed_since_approval")
        return self.grants.prepare_effect(conn, context, intent, current_policy_version=current_policy_version)

    def execute(self, conn, context, intent, worker_id: str, *, current_policy_version) -> dict:
        """One transaction: recheck grant, claim, dispatch, reject, read back, evidence, finish."""
        request_id = self._template(context, intent)
        authority = GrantExecution(self.grants, context, intent, current_policy_version)
        lease = self.receipts.claim(conn, context.principal_id, context.environment, intent.operation_id,
                                    worker_id, authority=authority)
        self.receipts.dispatch(conn, lease, authority=authority)
        with _cursor(conn) as cur:
            self._now(cur, context)
            cur.execute(f"SELECT {_FIELDS} FROM guild.guest_requests WHERE id=%s FOR UPDATE", (request_id,))
            before = cur.fetchone()
            if before is None:
                outcome = "missing"
            elif before["status"] == "requested" and target_version(before) == intent.expected_target_version:
                from minimoi_portal.guest_requests import reject_guild_in      # the application operation
                reject_guild_in(cur, request_id)
                outcome = "rejected"
            elif before["status"] == "rejected":
                outcome = "already_rejected"
            else:
                outcome = "conflict"            # granted, pending, or a different request at that id
            cur.execute(f"SELECT {_FIELDS} FROM guild.guest_requests WHERE id=%s", (request_id,))
            after = cur.fetchone()
            verified = after is not None and after["status"] == "rejected" and outcome in ("rejected", "already_rejected")
            evidence_id = str(uuid4())
            cur.execute("""INSERT INTO guild.mc_guest_action_evidence
                (evidence_id, principal_id, environment, operation_id, request_id, outcome, status_after, actioned_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (evidence_id, context.principal_id, context.environment, intent.operation_id, request_id,
                 outcome, after["status"] if after else None, after["actioned_at"] if after else None))
        self.receipts.finish(conn, lease, "verified" if verified else "failed", evidence_refs=(evidence_id,),
                             authority=authority)
        receipt = self.receipts.read(conn, context.principal_id, context.environment, intent.operation_id)
        return {"receipt": receipt, "outcome": outcome, "ref": f"guild:{request_id}",
                "status_after": after["status"] if after else None,
                "name": (after or before or {}).get("name"), "email": (after or before or {}).get("email")}

    def reply(self, result: dict) -> str:
        """What MC tells Robert, from the verified evidence, never from the model's own claim."""
        who = result.get("name") or result.get("email") or result["ref"]
        state = result["receipt"]["state"]
        if state == "verified" and result["outcome"] == "rejected":
            return f"Done: the guest request from {who} ({result['ref']}) is rejected, verified by read-back."
        if state == "verified" and result["outcome"] == "already_rejected":
            return f"The guest request from {who} ({result['ref']}) was already rejected; nothing changed."
        if result["outcome"] == "missing":
            return f"No guest request {result['ref']} exists any more; nothing was changed."
        return (f"I did not reject the request from {who} ({result['ref']}): its status is now "
                f"{result['status_after']}, so it no longer matches what you approved. Nothing was changed.")
