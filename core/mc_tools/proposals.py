"""The owner's Confirm / Cancel for MC's delegation and invite proposals (design §4).

The platform answers these from the owner's own kept note before any model is asked, exactly like
the guest-request and lesson commands. Confirm turns the pending proposal in THIS conversation into a
one-time exact-approval grant for the request MC proposed, then acts and verifies:

  delegate_research  the job starts from the owner's own note of the proposing turn (never model text)
  access_invite      a Meu Português guest request is recorded and granted through guest_requests.grant;
                     the temporary password goes only to the invitee's approval email (and, if that
                     fails, the owner's Telegram notice, as on the Operate grant path), never to a reply

Each effect lies outside one database transaction, so its receipt is dispatched and committed first,
and any uncertainty ends ``unknown`` with no retry.
"""
from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.receipts import EffectIntent, _cursor

from . import effects
from .binding import POLICY

_log = logging.getLogger("mc_tools")
KINDS = {"delegate_research": "delegate.start_research", "access_invite": "access.invite_user"}
INVITE_REASON = "Invited by Robert through Master Craftsman"


def _reply(text, state, **extra):
    return {"reply": text, "operation": {"type": "mc_tool_proposal", "state": state, **extra}}


class ToolProposals:
    def __init__(self, connect, *, principal: str, environment: str, start_research=None, invite=None):
        """``start_research(proposal) -> (state, job_id | None)``; state is started | refused | unknown.
        ``invite(proposal, ref) -> (state, detail)``; state is done | conflict | unknown, detail content-free."""
        self._connect, self.principal, self.environment = connect, principal, environment
        self.start_research, self.invite = start_research, invite

    @contextmanager
    def _tx(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def has_pending(self, conversation: str) -> bool:
        """'Confirm' and 'Cancel' are ordinary words: ours only while a proposal waits (or was just confirmed)."""
        with self._tx() as conn, _cursor(conn) as cur:
            cur.execute("""SELECT 1 FROM guild.mc_tool_proposals WHERE principal_id=%s AND environment=%s
                AND conversation_id=%s AND ((state='pending' AND expires_at > clock_timestamp()) OR (state='confirmed'
                AND created_at > clock_timestamp() - interval '30 minutes')) LIMIT 1""",
                (self.principal, self.environment, conversation))
            return cur.fetchone() is not None

    def handle(self, kind: str, *, conversation: str, turn_key: str) -> dict:
        try:
            return self._confirm(conversation, turn_key) if kind == "confirm" else self._cancel(conversation)
        except ActionContractError as exc:
            return _reply(f"I could not do that safely ({exc}). Nothing was changed.", "refused", code=str(exc))

    def _cancel(self, conversation):
        with self._tx() as conn, _cursor(conn) as cur:
            cur.execute("""UPDATE guild.mc_tool_proposals SET state='cancelled' WHERE principal_id=%s AND environment=%s
                AND conversation_id=%s AND state='pending' RETURNING kind""", (self.principal, self.environment, conversation))
            row = cur.fetchone()
        if row is None:
            return _reply("Nothing was waiting, so there is nothing to cancel.", "nothing_pending")
        return _reply("Cancelled. Nothing was changed.", "cancelled", kind=row["kind"])

    def _context(self, conversation, turn_key, now, grant_id):
        return ActionContext(self.principal, "mc", self.environment, conversation, turn_key, turn_key, 1,
                             now + timedelta(minutes=5), POLICY, "mc", "recorded", "owner", (grant_id,))

    def _confirm(self, conversation, turn_key):
        with self._tx() as conn, _cursor(conn) as cur:
            cur.execute("""SELECT * FROM guild.mc_tool_proposals WHERE principal_id=%s AND environment=%s
                AND conversation_id=%s AND (state='pending' OR (state='confirmed' AND confirm_turn_id=%s))
                ORDER BY created_at DESC LIMIT 1 FOR UPDATE""", (self.principal, self.environment, conversation, turn_key))
            p = cur.fetchone()
            if p is None:
                return _reply("Nothing is waiting for your confirmation in this conversation.", "nothing_pending")
            cur.execute("SELECT clock_timestamp() AS now")
            now = cur.fetchone()["now"]
            if p["state"] == "pending" and p["expires_at"] <= now:
                cur.execute("UPDATE guild.mc_tool_proposals SET state='stale' WHERE proposal_id=%s", (p["proposal_id"],))
                return _reply("That proposal expired, so nothing was done. Ask again if you still want it.", "expired")
            if (self.start_research if p["kind"] == "delegate_research" else self.invite) is None:
                cur.execute("UPDATE guild.mc_tool_proposals SET state='stale' WHERE proposal_id=%s", (p["proposal_id"],))
                return _reply("That is not switched on here, so nothing was done.", "refused", code="kind_off")
            req = effects.request(KINDS[p["kind"]], p["payload"])
            grant_id = str(UUID(hashlib.sha256(("mc-tool-grant\0" + str(p["proposal_id"])).encode()).hexdigest()[:32]))
            context = self._context(conversation, turn_key, now, grant_id)
            intent = EffectIntent(str(p["operation_id"]), req, grant_id=grant_id, proposal_id=str(p["proposal_id"]))
            if p["state"] == "confirmed":                    # the same Confirm retried: report, never act twice
                try:
                    receipt = effects.RECEIPTS.read(conn, self.principal, self.environment, intent.operation_id)
                except ActionContractError:
                    receipt = None
                state = receipt["state"] if receipt else "unknown"
                return _reply("That was already handled; nothing new was done (its outcome is "
                              f"{'verified' if state == 'verified' else state}).", state, repeated=True)
            effects.issue_exact(conn, grant_id=grant_id, principal=self.principal, environment=self.environment,
                                conversation_id=conversation,
                                owner_turn_ids=tuple(dict.fromkeys((p["owner_turn_id"], turn_key))), req=req,
                                proposal_id=str(p["proposal_id"]), operation_id=str(p["operation_id"]),
                                approval_expires_at=p["expires_at"])
            cur.execute("UPDATE guild.mc_tool_proposals SET state='confirmed', confirm_turn_id=%s WHERE proposal_id=%s",
                        (turn_key, p["proposal_id"]))
            effects.prepare_exact(conn, context, intent)
            effect = effects.dispatch_exact(conn, context, intent, worker=f"portal-{turn_key[:40]}")
            proposal = dict(p)
        # The approval, the prepared receipt and its dispatch are committed; now act outside this transaction.
        if proposal["kind"] == "delegate_research":
            return self._run_research(effect, proposal)
        return self._run_invite(effect, proposal)

    def _finish(self, effect, kind, outcome, verified):
        with self._tx() as conn:
            return effect.finish(conn, kind, outcome, verified=verified)

    def _unknown(self, effect):
        try:
            with self._tx() as conn:
                effect.unknown(conn)
        except Exception:
            _log.exception("mc tool receipt %s: could not record unknown", effect.operation_id)

    def _run_research(self, effect, p):
        who = {"claude-code": "Claude Code", "codex": "Codex"}[p["payload"]["agent"]]
        try:
            state, job_id = self.start_research(p) if self.start_research else ("refused", None)
        except Exception:
            _log.exception("mc tool delegation: start outcome unknown")
            state, job_id = "unknown", None
        if state == "unknown":
            self._unknown(effect)
            return _reply(f"I asked to start {who}'s research but cannot confirm it started. Do not ask again yet; "
                          "check this conversation's jobs first.", "unknown")
        receipt = self._finish(effect, "delegate_research", {"state": state, "job_id": job_id}, state == "started")
        if state != "started":
            return _reply(f"{who}'s research could not be started, so nothing is running.", "failed",
                          receipt_id=str(receipt["receipt_id"]))
        return _reply(f"Started: {who} is researching your message, read-only, in the background on the Mac. "
                      "Its answer comes back here when it's done; say stop to cancel.", "verified",
                      receipt_id=str(receipt["receipt_id"]))

    def _guest_request(self, p):
        """The owner's own invite as a guest request (domain = the app), returned as its ref.
        A rejected request with the same email is reopened; an open one is reused; any other state refuses."""
        pay = p["payload"]
        with self._tx() as conn, _cursor(conn) as cur:
            cur.execute("SELECT id, status FROM guild.guest_requests WHERE lower(email)=%s FOR UPDATE", (pay["email"],))
            row = cur.fetchone()
            if row is None:
                cur.execute("""INSERT INTO guild.guest_requests (name, email, reason, domain, status)
                    VALUES (%s,%s,%s,%s,'requested') RETURNING id""", (pay["name"], pay["email"], INVITE_REASON, pay["app"]))
                return f"guild:{cur.fetchone()['id']}", None
            if row["status"] == "rejected":
                cur.execute("""UPDATE guild.guest_requests SET name=%s, reason=%s, domain=%s, status='requested',
                    requested_at=clock_timestamp(), actioned_at=NULL WHERE id=%s""",
                    (pay["name"], INVITE_REASON, pay["app"], row["id"]))
                return f"guild:{row['id']}", None
            if row["status"] == "requested":
                return f"guild:{row['id']}", None
            return None, row["status"]

    def _run_invite(self, effect, p):
        pay = p["payload"]
        try:
            ref, blocked = self._guest_request(p)
        except Exception:
            _log.exception("mc tool invite: guest request could not be recorded")
            self._unknown(effect)
            return _reply("I could not confirm whether the invitation was recorded. Nothing more was done; "
                          "check Operate before asking again.", "unknown")
        if ref is None:
            receipt = self._finish(effect, "access_invite", {"state": "conflict", "request_status": blocked}, False)
            return _reply(f"{pay['email']} already has a guest request that is {blocked}, so nothing was created.",
                          "failed", receipt_id=str(receipt["receipt_id"]))
        try:
            state, detail, deliver = self.invite(p, ref) if self.invite else ("conflict", {"error": "invites_off"}, None)
        except Exception:
            _log.exception("mc tool invite: grant outcome unknown")
            state, detail, deliver = "unknown", {}, None
        if state == "unknown":
            self._unknown(effect)
            return _reply(f"I started {pay['name']}'s login but cannot confirm it finished. Do not ask again yet; "
                          "check Operate first.", "unknown", ref=ref)
        outcome = {"state": state, "ref": ref, **{k: v for k, v in detail.items() if k in ("username", "error")}}
        receipt = self._finish(effect, "access_invite", outcome, state == "done")
        if state != "done":
            return _reply(f"No login was created for {pay['email']} ({detail.get('error', state)}).", "failed",
                          ref=ref, receipt_id=str(receipt["receipt_id"]))
        # The account is verified and recorded; delivering its one-time password is a separate notification.
        try:
            sent = deliver() if deliver else False
        except Exception:
            _log.exception("mc tool invite: delivery failed")
            sent = False
        tail = (f"An email with a temporary password went to {pay['email']}; they change it at first sign-in."
                if sent else "The approval email did NOT send. The Operate grant path's Telegram notice to you "
                "carries the temporary password when email fails; if it did not arrive either, reset the password "
                "in Operate.")
        return _reply(f"Done: {pay['name']} has a {pay['days']}-day guest login to Meu Português "
                      f"(user {detail.get('username')}), verified by read-back. {tail}", "verified",
                      ref=ref, receipt_id=str(receipt["receipt_id"]))
