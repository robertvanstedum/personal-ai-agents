"""The owner's yes / no for MC's proposals: data-repair tickets, delegated research, invites (design §4).

The platform answers these from the owner's own kept note before any model is asked, exactly like
the guest-request and lesson commands. Model output alone never acts: only the owner's yes turns the
pending proposal in THIS conversation into a one-time exact-approval grant for the request MC
proposed, and then the platform acts and verifies:

  lesson             the exact lesson text adopted into the owner's lesson ledger, verified by read-back
  guest_reject       one open guest request rejected through the guest action path, verified by read-back
  issue_comment      a comment on an existing GitHub issue, exactly the text the owner saw, refs only
  data_repair        a public GitHub issue naming the records by ref only (no names or emails)
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
import re
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.receipts import EffectIntent, _cursor

from . import effects
from .binding import POLICY

_log = logging.getLogger("mc_tools")
KINDS = {"lesson": "lessons.adopt", "guest_reject": "guest.reject_request", "issue_comment": "issue.comment",
         "data_repair": "issue.create_data_repair", "delegate_research": "delegate.start_research",
         "access_invite": "access.invite_user"}
_YES = re.compile(r"^(?:yes|yes,?\s+please|yep|yeah|ok(?:ay)?|sure|go\s+ahead|do\s+it|please\s+do|confirm(?:ed)?|"
                  r"approve(?:d)?)(?:[,.!]?\s+(?:please|thanks|thank\s+you|do\s+it|go\s+ahead))?\s*[.!]?$", re.I)
_NO = re.compile(r"^(?:no|nope|no,?\s+thanks|cancel|don['’]t|do\s+not|never\s*mind)\s*[.!]?$", re.I)


_NAMED = re.compile(r"^(?P<word>yes|ok(?:ay)?|confirm|approve|no|cancel)[,:]?\s+(?:proposal\s+)?p-?(?P<code>[a-z0-9]{4})\s*[.!]?$",
                    re.I)


def parse(text):
    """(kind, code) for the owner's own short reply, else None: kind 'confirm' or 'cancel'; code 'P-XXXX' when he
    named the proposal ("yes P-7K3M"), else None. Only the whole short message counts, and the caller claims it only
    while a proposal waits in this conversation."""
    if not isinstance(text, str) or len(text) > 60:
        return None
    t = text.strip()
    m = _NAMED.match(t)
    if m:
        kind = "cancel" if m.group("word").lower() in ("no", "cancel") else "confirm"
        return kind, "P-" + m.group("code").upper()
    return ("confirm", None) if _YES.match(t) else ("cancel", None) if _NO.match(t) else None


def issue_text(payload, operation_id):
    """The public ticket: refs, change and environment only. Names and emails stay in the database."""
    refs, change, env = payload["refs"], payload["change"], payload["environment"]
    ids = ", ".join(r.split(":")[1] for r in refs)
    title = f"Data repair ({env}): {change} guest request(s) {', '.join(refs)}"[:200]
    body = (f"Robert asked Master Craftsman for this in the Guild conversation and confirmed it; MiniMoi's screens "
            f"could not make the change.\n\n- Environment: {env}\n- Records: `guild.guest_requests` id(s) {ids} "
            f"(refs {', '.join(refs)})\n- Requested change: {change}\n- Operation: {operation_id} "
            f"(receipt in `guild.mc_action_receipts`)\n\nNames and emails are left out on purpose because this "
            "repository is public: read them from the database.")
    return title, body
INVITE_REASON = "Invited by Robert through Master Craftsman"


def comment_text(payload, operation_id):
    """The public comment: the owner-approved summary, then refs and provenance. Never names or emails."""
    refs = payload["refs"]
    tail = (f"Records: {', '.join(refs)} (`guild.guest_requests`). " if refs else "") + \
        f"Environment: {payload['environment']}. Operation {operation_id}."
    return (f"{payload['summary']}\n\n---\n{tail}\nPosted by MiniMoi's Master Craftsman after Robert approved this "
            "exact text in the Guild conversation.")


def _reply(text, state, **extra):
    return {"reply": text, "operation": {"type": "mc_tool_proposal", "state": state, **extra}}


class ToolProposals:
    def __init__(self, connect, *, principal: str, environment: str, start_research=None, invite=None,
                 file_issue=None, comment_issue=None, guest_rejects: bool = False, lessons=None):
        """``file_issue(title, body) -> (state, number | None)`` and ``comment_issue(number, body) -> (state, id)``;
        state is created | refused | unknown. ``guest_rejects`` switches on the guest reject (a local effect).
        ``start_research(proposal) -> (state, job_id | None)``; state is started | refused | unknown.
        ``invite(proposal, ref) -> (state, detail)``; state is done | conflict | unknown, detail content-free."""
        self._connect, self.principal, self.environment = connect, principal, environment
        self.start_research, self.invite, self.file_issue = start_research, invite, file_issue
        self.comment_issue, self.guest_rejects, self.lessons = comment_issue, guest_rejects, lessons

    @contextmanager
    def _tx(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def has_pending(self, conversation: str, turn_key: str) -> bool:
        """'yes', 'ok' and 'no' are Robert's commonest words: ours only while a proposal is live, or when this very
        note already confirmed one (a retry). Never a window after a finished one (review P2-2)."""
        with self._tx() as conn, _cursor(conn) as cur:
            cur.execute("""SELECT 1 FROM guild.mc_tool_proposals WHERE principal_id=%s AND environment=%s
                AND conversation_id=%s AND ((state='pending' AND expires_at > clock_timestamp())
                OR (state='confirmed' AND confirm_turn_id=%s)) LIMIT 1""",
                (self.principal, self.environment, conversation, turn_key))
            return cur.fetchone() is not None

    def handle(self, kind: str, *, conversation: str, turn_key: str, code: str | None = None) -> dict:
        try:
            return (self._confirm(conversation, turn_key, code) if kind == "confirm"
                    else self._cancel(conversation, code))
        except ActionContractError as exc:
            return _reply(f"I could not do that safely ({exc}). Nothing was changed.", "refused", code=str(exc))

    def _cancel(self, conversation, code=None):
        with self._tx() as conn, _cursor(conn) as cur:
            cur.execute("""UPDATE guild.mc_tool_proposals SET state='cancelled' WHERE principal_id=%s AND environment=%s
                AND conversation_id=%s AND state='pending' AND (%s::text IS NULL OR code=%s) RETURNING kind""",
                (self.principal, self.environment, conversation, code, code))
            row = cur.fetchone()
        if row is None:
            return _reply("Nothing was waiting, so there is nothing to cancel.", "nothing_pending")
        return _reply("Cancelled. Nothing was changed.", "cancelled", kind=row["kind"])

    def _context(self, conversation, turn_key, now, grant_id):
        return ActionContext(self.principal, "mc", self.environment, conversation, turn_key, turn_key, 1,
                             now + timedelta(minutes=5), POLICY, "mc", "recorded", "owner", (grant_id,))

    def _next_turn(self, cur, p, turn_key) -> bool:
        """Whether this confirming note is the owner's very next turn in the conversation after the proposal's."""
        cur.execute("""SELECT note_request_id FROM guild.mc_turn_ledger WHERE principal=%s AND conversation_id=%s
            AND id > %s ORDER BY id LIMIT 1""", (self.principal, p["conversation_id"], p["proposing_ledger_id"]))
        row = cur.fetchone()
        return row is not None and row["note_request_id"] == turn_key

    def _confirm(self, conversation, turn_key, code=None):
        with self._tx() as conn, _cursor(conn) as cur:
            # A retried yes resolves to what THAT yes confirmed, never to a newer proposal (review P2-1).
            cur.execute("""SELECT * FROM guild.mc_tool_proposals WHERE principal_id=%s AND environment=%s
                AND conversation_id=%s AND state='confirmed' AND confirm_turn_id=%s FOR UPDATE""",
                (self.principal, self.environment, conversation, turn_key))
            p = cur.fetchone()
            if p is None:
                cur.execute("""SELECT * FROM guild.mc_tool_proposals WHERE principal_id=%s AND environment=%s
                    AND conversation_id=%s AND state='pending' AND (%s::text IS NULL OR code=%s)
                    ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                    (self.principal, self.environment, conversation, code, code))
                p = cur.fetchone()
                # A plain yes approves only what the platform showed in the turn just before, and never a
                # proposal made after reading text anyone can write; otherwise Robert names it (review P1-1).
                if p is not None and code is None and (p["untrusted_input"] or not self._next_turn(cur, p, turn_key)):
                    return _reply(f"To approve proposal {p['code']}, reply “yes {p['code']}” (it is shown above). "
                                  "Nothing was done.", "needs_code", proposal=p["code"])
            if p is None:
                return _reply("Nothing is waiting for your confirmation in this conversation.", "nothing_pending")
            cur.execute("SELECT clock_timestamp() AS now")
            now = cur.fetchone()["now"]
            if p["state"] == "pending" and p["expires_at"] <= now:
                cur.execute("UPDATE guild.mc_tool_proposals SET state='stale' WHERE proposal_id=%s", (p["proposal_id"],))
                return _reply("That proposal expired, so nothing was done. Ask again if you still want it.", "expired")
            handlers = {"lesson": self.lessons, "guest_reject": self.guest_rejects or None, "issue_comment": self.comment_issue,
                        "data_repair": self.file_issue, "delegate_research": self.start_research,
                        "access_invite": self.invite}
            if handlers[p["kind"]] is None:
                cur.execute("UPDATE guild.mc_tool_proposals SET state='stale' WHERE proposal_id=%s", (p["proposal_id"],))
                return _reply("That is not switched on here, so nothing was done.", "refused", code="kind_off")
            target = None
            if p["kind"] == "guest_reject":
                from core.mc_domain_actions import guest
                req, target = guest.reject_request(p["payload"]["ref"]), p["payload"]["target_version"]
            else:
                req = effects.request(KINDS[p["kind"]], p["payload"])
            grant_id = str(UUID(hashlib.sha256(("mc-tool-grant\0" + str(p["proposal_id"])).encode()).hexdigest()[:32]))
            context = self._context(conversation, turn_key, now, grant_id)
            intent = EffectIntent(str(p["operation_id"]), req, grant_id=grant_id, proposal_id=str(p["proposal_id"]),
                                  expected_target_version=target)
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
                                approval_expires_at=p["expires_at"], expected_target_version=target)
            cur.execute("UPDATE guild.mc_tool_proposals SET state='confirmed', confirm_turn_id=%s WHERE proposal_id=%s",
                        (turn_key, p["proposal_id"]))
            if p["kind"] == "guest_reject":
                return self._guest_reject(conn, cur, p, context, intent, turn_key)
            effects.prepare_exact(conn, context, intent)
            effect = effects.dispatch_exact(conn, context, intent, worker=f"portal-{turn_key[:40]}")
            proposal = dict(p)
        # The approval, the prepared receipt and its dispatch are committed; now act outside this transaction.
        if proposal["kind"] == "data_repair":
            return self._run_ticket(effect, proposal)
        if proposal["kind"] == "issue_comment":
            return self._run_comment(effect, proposal)
        if proposal["kind"] == "lesson":
            return self._run_lesson(effect, proposal)
        if proposal["kind"] == "delegate_research":
            return self._run_research(effect, proposal)
        return self._run_invite(effect, proposal)

    def _guest_reject(self, conn, cur, p, context, intent, turn_key):
        """A local effect, as in the guest command path: the approval and prepared receipt commit first (only if the
        request is still exactly what MC proposed), then one transaction rejects, reads back and finishes."""
        from core.mc_domain_actions import guest
        actions = guest.GuestActions(effects.GRANTS, owner_principal=self.principal)
        cur.execute("SAVEPOINT tool_guest_prepare")
        try:
            actions.prepare(conn, context, intent, current_policy_version=POLICY)
        except ActionContractError as exc:
            cur.execute("ROLLBACK TO SAVEPOINT tool_guest_prepare")
            if str(exc) not in ("guest_request_changed_since_approval", "guest_request_not_found"):
                raise
            cur.execute("UPDATE guild.mc_tool_proposals SET state='stale' WHERE proposal_id=%s", (p["proposal_id"],))
            return _reply("That request changed since I proposed it (for example it was already handled), so "
                          "nothing was done.", "stale")
        conn.commit()
        with self._tx() as conn2:
            result = actions.execute(conn2, context, intent, f"portal-{turn_key[:40]}", current_policy_version=POLICY)
        return _reply(actions.reply(result), result["receipt"]["state"], outcome=result["outcome"], ref=result["ref"],
                      receipt_id=str(result["receipt"]["receipt_id"]))

    def _run_lesson(self, effect, p):
        from core.mc_lessons import commands as lessons
        handler, key = lessons.LessonCommands(self.lessons), "tool-" + effect.operation_id
        try:
            offered = handler.handle(lessons.Command("remember", text=p["payload"]["text"]),
                                     conversation=p["conversation_id"], turn_key=key)
            state = offered["operation"]["state"]
            kept = handler.handle(lessons.Command("confirm"), conversation=p["conversation_id"],
                                  turn_key=key + "-yes") if state == "proposed" else offered
        except Exception:
            _log.exception("mc tool lesson: outcome unknown")
            self._unknown(effect)
            return _reply("I could not confirm whether the lesson was kept. Ask me to list your lessons before trying "
                          "again.", "unknown")
        verified = kept["operation"]["state"] == "verified"
        receipt = self._finish(effect, "lesson", {"state": kept["operation"]["state"],
                                                  "lesson": kept["operation"].get("lesson")}, verified)
        return _reply(kept["reply"], "verified" if verified else kept["operation"]["state"],
                      receipt_id=str(receipt["receipt_id"]))

    def _run_comment(self, effect, p):
        number = p["payload"]["number"]
        try:
            state, comment_id = self.comment_issue(number, comment_text(p["payload"], effect.operation_id))
        except Exception:
            _log.exception("mc tool issue comment: outcome unknown")
            state, comment_id = "unknown", None
        if state == "unknown":
            self._unknown(effect)
            return _reply(f"I sent the comment to issue #{number} but cannot confirm it was posted. Do not ask again "
                          "yet; check the issue first.", "unknown")
        receipt = self._finish(effect, "issue_comment", {"state": state, "issue": number, "comment": comment_id},
                               state == "created")
        if state != "created":
            return _reply(f"The comment on issue #{number} could not be posted, so nothing was added.", "failed",
                          receipt_id=str(receipt["receipt_id"]))
        return _reply(f"Comment added to issue #{number}.", "verified", issue=number,
                      receipt_id=str(receipt["receipt_id"]))

    def _finish(self, effect, kind, outcome, verified):
        with self._tx() as conn:
            return effect.finish(conn, kind, outcome, verified=verified)

    def _unknown(self, effect):
        try:
            with self._tx() as conn:
                effect.unknown(conn)
        except Exception:
            _log.exception("mc tool receipt %s: could not record unknown", effect.operation_id)

    def _run_ticket(self, effect, p):
        title, body = issue_text(p["payload"], effect.operation_id)
        try:
            state, number = self.file_issue(title, body)
        except Exception:
            _log.exception("mc tool data repair: filing outcome unknown")
            state, number = "unknown", None
        if state == "unknown":
            self._unknown(effect)
            return _reply("I sent the data-repair ticket but cannot confirm it was created. Do not ask again yet; "
                          "check the repository's issues first.", "unknown")
        receipt = self._finish(effect, "data_repair", {"state": state, "issue": number}, state == "created")
        if state != "created":
            return _reply("The data-repair ticket could not be created, so nothing was filed.", "failed",
                          receipt_id=str(receipt["receipt_id"]))
        return _reply(f"Ticket {number} created: a data-repair request for {', '.join(p['payload']['refs'])} "
                      f"({p['payload']['change']}).", "verified", issue=number, receipt_id=str(receipt["receipt_id"]))

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
