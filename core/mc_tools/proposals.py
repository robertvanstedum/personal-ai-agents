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
from uuid import UUID

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.receipts import EffectIntent, _cursor

from . import effects, offers

_log = logging.getLogger("mc_tools")
KINDS = {"lesson": "lessons.adopt", "guest_reject": "guest.reject_request", "issue_comment": "issue.comment",
         "data_repair": "issue.create_data_repair", "delegate_research": "delegate.start_research",
         "access_invite": "access.invite_user"}
_YES = re.compile(r"^(?:yes|yes,?\s+please|ja|ja,?\s+bitte|yep|yeah|ok(?:ay)?|sure|go\s+ahead|do\s+it|please\s+do|confirm(?:ed)?|"
                  r"approve(?:d)?)(?:[,.!]?\s+(?:please|thanks|thank\s+you|do\s+it|go\s+ahead))?\s*[.!]?$", re.I)
_NO = re.compile(r"^(?:no|nope|no,?\s+thanks|cancel|don['’]t|do\s+not|never\s*mind)\s*[.!]?$", re.I)


_NAMED = re.compile(r"^(?P<word>yes|ja|ok(?:ay)?|confirm|approve|no|nein|cancel)[,:]?\s+(?:proposal\s+)?p-?(?P<code>[a-z0-9]{4})\s*[.!]?$",
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
        kind = "cancel" if m.group("word").lower() in ("no", "nein", "cancel") else "confirm"
        return kind, "P-" + m.group("code").upper()
    return ("confirm", None) if _YES.match(t) else ("cancel", None) if _NO.match(t) else None


def issue_text(payload, operation_id):
    """A DRAFT change record (RFC) under the change-control standard §4, not a work order (review P2-6): refs, change
    and environment only; names and emails stay in the database. It authorizes nothing by itself."""
    refs, change, env = payload["refs"], payload["change"], payload["environment"]
    ids = ", ".join(r.split(":")[1] for r in refs)
    title = f"RFC (draft, {env}): {change} guest request(s) {', '.join(refs)}"[:200]
    todo = "_to be written by the implementer before review_"
    body = (f"**Status: draft change record. Not approved. Apply only after independent review and Robert's approval "
            f"(change-control standard §4–§5).** Requested by Robert in the Guild conversation, where MiniMoi's screens "
            f"could not make the change.\n\n"
            f"1. **What:** {change} `guild.guest_requests` id(s) {ids} (refs {', '.join(refs)}), environment {env}.\n"
            f"2. **Why:** MiniMoi's screens cannot do it; link the incident or problem ticket (for guest approval, #69).\n"
            f"3. **Risk and impact:** {todo}\n4. **Steps:** a reviewed application operation, never ad hoc SQL: {todo}\n"
            f"5. **Pre-checks:** each record still in the state it had when requested: {todo}\n"
            f"6. **Verification:** read-back of each record: {todo}\n7. **Rollback:** {todo}\n"
            f"8. **Test evidence:** the dev run: {todo}\n9. **Review:** an agent other than the author: {todo}\n"
            f"10. **Approval:** Robert, with date and time: {todo}\n11. **Outcome:** {todo}\n\n"
            f"Request receipt: operation {operation_id} in `guild.mc_action_receipts`. Names and emails are left out "
            "on purpose because this repository is public: read them from the database.")
    return title, body
INVITE_REASON = "Invited by Robert through Master Craftsman"


def comment_text(payload, operation_id):
    """The public comment: the owner-approved summary, then refs and provenance. Never names or emails."""
    refs = payload["refs"]
    tail = (f"Records: {', '.join(refs)} (`guild.guest_requests`). " if refs else "") + \
        f"Environment: {payload['environment']}. Operation {operation_id}."
    return (f"{payload['summary']}\n\n---\n{tail}\nPosted by MiniMoi's Master Craftsman after Robert approved this "
            "exact text in the Guild conversation.")


def _db_now(cur):
    cur.execute("SELECT clock_timestamp() AS now")
    return cur.fetchone()["now"]


def _reply(text, state, **extra):
    return {"reply": text, "operation": {"type": "mc_tool_proposal", "state": state, **extra}}


class ToolProposals:
    def __init__(self, connect, *, principal: str, environment: str, start_research=None, invite=None,
                 file_issue=None, comment_issue=None, guest_rejects: bool = False, lessons=None, read_shown=None,
                 display_tz: str = offers.DISPLAY_TZ, clock=None, policy=None):
        """``file_issue(title, body) -> (state, number | None)`` and ``comment_issue(number, body) -> (state, id)``;
        state is created | refused | unknown. ``guest_rejects`` switches on the guest reject (a local effect).
        ``start_research(proposal) -> (state, job_id | None)``; state is started | refused | unknown.
        ``invite(proposal, ref) -> (state, detail)``; state is done | conflict | unknown, detail content-free."""
        self._connect, self.principal, self.environment = connect, principal, environment
        self.start_research, self.invite, self.file_issue = start_research, invite, file_issue
        self.comment_issue, self.guest_rejects, self.lessons = comment_issue, guest_rejects, lessons
        # read_shown(conversation_id, note_key) -> note | None: finishes a publication a crash interrupted (§9.1).
        self.read_shown, self.display_tz = read_shown, display_tz
        # clock(cur) -> the approval's "now": the database clock, injectable so the offer-expiry boundary can be tested
        # at exact equality (A02+). Grants and dispatch always check the database clock themselves.
        self._clock = clock or _db_now
        # policy: the same PolicySource as the tool service. An approval needs the policy the offer was made under to
        # still be the one in force (spec §8.1: replacement prevents old-policy effects).
        self.policy = policy

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
            offers.lock_conversation(cur, self.principal, self.environment, conversation)
            cur.execute("""UPDATE guild.mc_tool_proposals SET state='cancelled', closed_reason='owner_cancelled'
                WHERE principal_id=%s AND environment=%s
                AND conversation_id=%s AND state='pending' AND (%s::text IS NULL OR code=%s) RETURNING kind""",
                (self.principal, self.environment, conversation, code, code))
            row = cur.fetchone()
        if row is None:
            return _reply("Nothing was waiting, so there is nothing to cancel.", "nothing_pending")
        return _reply("Cancelled. Nothing was changed.", "cancelled", kind=row["kind"])

    def _context(self, conversation, turn_key, deadline, grant_id, policy_version):
        """The approval's action context: its lease ends at the persisted execution deadline (spec §9.2)."""
        return ActionContext(self.principal, "mc", self.environment, conversation, turn_key, turn_key, 1,
                             deadline, policy_version, "mc", "recorded", "owner", (grant_id,))

    def _policy_now(self):
        """The policy version in force, or None when there is no usable policy (then nothing is approved)."""
        if self.policy is None:
            return None
        compiled, _problem = self.policy.current()
        return compiled.version if compiled is not None else None

    def _next_turn(self, cur, p, turn_key) -> bool:
        """Whether this confirming note is the owner's very next turn in the conversation after the proposal's, and
        began after the card was on the page (a yes cannot approve what it never saw)."""
        cur.execute("""SELECT note_request_id, created_at FROM guild.mc_turn_ledger WHERE principal=%s
            AND conversation_id=%s AND id > %s ORDER BY id LIMIT 1""",
            (self.principal, p["conversation_id"], p["proposing_ledger_id"]))
        row = cur.fetchone()
        return (row is not None and row["note_request_id"] == turn_key and p["published_at"] is not None
                and row["created_at"] > p["published_at"])

    def _confirm(self, conversation, turn_key, code=None):
        with self._tx() as conn, _cursor(conn) as cur:
            offers.lock_conversation(cur, self.principal, self.environment, conversation)
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
                # Only an offer whose exact card is on the page can be approved, by yes or by code (spec §9.1, A04).
                if p is not None and offers.reconcile(cur, self.read_shown, p) != "published":
                    return _reply(f"Proposal {p['code']} can't be approved: MiniMoi can't confirm its card is on the page "
                                  "exactly as it was made. Nothing was done. Ask me to propose it again if you still "
                                  "want it.", "not_shown", proposal=p["code"])
                if p is not None and p["display_state"] != "published":
                    cur.execute("SELECT * FROM guild.mc_tool_proposals WHERE proposal_id=%s", (p["proposal_id"],))
                    p = cur.fetchone()                 # reconciled just now: re-read its published time
                # A plain yes approves only what the platform showed in the turn just before, and never a
                # proposal made after reading text anyone can write; otherwise Robert names it (review P1-1).
                if p is not None and code is None and (p["untrusted_input"] or not self._next_turn(cur, p, turn_key)):
                    return _reply(f"To approve proposal {p['code']}, reply “yes {p['code']}” (it is shown above). "
                                  "Nothing was done.", "needs_code", proposal=p["code"])
            if p is None:
                return _reply("Nothing is waiting for your confirmation in this conversation.", "nothing_pending")
            now = self._clock(cur)
            if p["state"] == "pending" and p["expires_at"] <= now:
                cur.execute("""UPDATE guild.mc_tool_proposals SET state='stale', closed_reason='offer_expired'
                    WHERE proposal_id=%s""", (p["proposal_id"],))
                return _reply(f"Proposal {p['code']} expired at {offers.expiry_words(p['expires_at'], self.display_tz)}, "
                              "so nothing was done. If you still want it, ask me to propose it again: I will check "
                              "everything afresh and it gets a new code.", "expired", proposal=p["code"])
            if p["state"] == "pending":
                version = self._policy_now()
                if version is None:
                    return _reply("MiniMoi's tool policy is not available, so nothing can be approved right now. "
                                  "Nothing was done.", "refused", code="policy_unavailable")
                if p["policy_version"] != version:
                    cur.execute("""UPDATE guild.mc_tool_proposals SET state='stale', closed_reason='policy_changed'
                        WHERE proposal_id=%s""", (p["proposal_id"],))
                    return _reply(f"Proposal {p['code']} was made under an earlier version of MiniMoi's tool policy, so "
                                  "it can't be approved now. Nothing was done. Ask me to propose it again if you still "
                                  "want it.", "stale", code="policy_changed", proposal=p["code"])
            else:
                version = None
            handlers = {"lesson": self.lessons, "guest_reject": self.guest_rejects or None,
                        "guest_reject_batch": self.guest_rejects or None, "issue_comment": self.comment_issue,
                        "data_repair": self.file_issue, "delegate_research": self.start_research,
                        "access_invite": self.invite}
            if handlers[p["kind"]] is None:
                cur.execute("UPDATE guild.mc_tool_proposals SET state='stale' WHERE proposal_id=%s", (p["proposal_id"],))
                return _reply("That is not switched on here, so nothing was done.", "refused", code="kind_off")
            if p["kind"] == "guest_reject_batch":
                if p["state"] == "confirmed":                # the same yes retried: report, never dispatch again
                    return self._batch_report(conn, p)
                return self._guest_reject_batch(conn, cur, p, conversation, turn_key, now, version)
            target = None
            if p["kind"] == "guest_reject":
                from core.mc_domain_actions import guest
                req, target = guest.reject_request(p["payload"]["ref"]), p["payload"]["target_version"]
            else:
                req = effects.request(KINDS[p["kind"]], p["payload"])
            grant_id = str(UUID(hashlib.sha256(("mc-tool-grant\0" + str(p["proposal_id"])).encode()).hexdigest()[:32]))
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
            deadline = now + offers.EXECUTION_GRACE        # fixed at this approval, persisted below (spec §9.2)
            context = self._context(conversation, turn_key, deadline, grant_id, version)
            effects.issue_exact(conn, grant_id=grant_id, principal=self.principal, environment=self.environment,
                                conversation_id=conversation,
                                owner_turn_ids=tuple(dict.fromkeys((p["owner_turn_id"], turn_key))), req=req,
                                proposal_id=str(p["proposal_id"]), operation_id=str(p["operation_id"]),
                                execution_deadline=deadline, policy_version=version,
                                expected_target_version=target)
            cur.execute("""UPDATE guild.mc_tool_proposals SET state='confirmed', confirm_turn_id=%s, approved_at=%s,
                execution_deadline=%s WHERE proposal_id=%s""", (turn_key, now, deadline, p["proposal_id"]))
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
            actions.prepare(conn, context, intent, current_policy_version=context.policy_version)
        except ActionContractError as exc:
            cur.execute("ROLLBACK TO SAVEPOINT tool_guest_prepare")
            if str(exc) not in ("guest_request_changed_since_approval", "guest_request_not_found"):
                raise
            cur.execute("UPDATE guild.mc_tool_proposals SET state='stale' WHERE proposal_id=%s", (p["proposal_id"],))
            return _reply("That request changed since I proposed it (for example it was already handled), so "
                          "nothing was done.", "stale")
        conn.commit()
        with self._tx() as conn2:
            result = actions.execute(conn2, context, intent, f"portal-{turn_key[:40]}", current_policy_version=context.policy_version)
        logged = self._log_standard_change(p, result["receipt"], intent.operation_id) if result["receipt"]["state"] == "verified" else ""
        return _reply(actions.reply(result) + logged, result["receipt"]["state"], outcome=result["outcome"], ref=result["ref"],
                      receipt_id=str(result["receipt"]["receipt_id"]))

    # ── guest rejection, one offer for one or several requests (spec §9.3, A02/A02+) ────────────────────────────
    def _batch_items(self, p):
        """(item, grant id, intent) in the order Robert saw them; every id fixed by the proposal."""
        from core.mc_domain_actions import guest
        out = []
        for n, item in enumerate(p["payload"]["items"]):
            grant_id = str(UUID(hashlib.sha256(f"mc-tool-grant\0{p['proposal_id']}\0{n}".encode()).hexdigest()[:32]))
            out.append((item, grant_id, EffectIntent(item["operation_id"], guest.reject_request(item["ref"]),
                                                     grant_id=grant_id, proposal_id=str(p["proposal_id"]),
                                                     expected_target_version=item["target_version"])))
        return out

    def _guest_reject_batch(self, conn, cur, p, conversation, turn_key, now, version):
        """Approve the batch: one exact grant per item, all ending at the execution deadline; each item prepared only
        if it is still exactly what Robert saw. Then dispatch in order, one local transaction per item, checking the
        deadline before each; stop at the first outcome that cannot be confirmed. Never all-or-nothing, never retried."""
        from core.mc_domain_actions import guest
        actions = guest.GuestActions(effects.GRANTS, owner_principal=self.principal)
        deadline = now + offers.EXECUTION_GRACE
        owner_turns = tuple(dict.fromkeys((p["owner_turn_id"], turn_key)))
        items = self._batch_items(p)
        for item, grant_id, intent in items:
            effects.issue_exact(conn, grant_id=grant_id, principal=self.principal, environment=self.environment,
                                conversation_id=conversation, owner_turn_ids=owner_turns, req=intent.request,
                                proposal_id=str(p["proposal_id"]), operation_id=item["operation_id"],
                                execution_deadline=deadline, policy_version=version,
                                expected_target_version=item["target_version"])
        cur.execute("""UPDATE guild.mc_tool_proposals SET state='confirmed', confirm_turn_id=%s, approved_at=%s,
            execution_deadline=%s WHERE proposal_id=%s""", (turn_key, now, deadline, p["proposal_id"]))
        outcome, ready = {}, []
        for item, grant_id, intent in items:
            context = self._context(conversation, turn_key, deadline, grant_id, version)
            cur.execute("SAVEPOINT tool_batch_item")
            try:
                actions.prepare(conn, context, intent, current_policy_version=context.policy_version)
                cur.execute("RELEASE SAVEPOINT tool_batch_item")
                ready.append((item, context, intent))
            except ActionContractError as exc:
                cur.execute("ROLLBACK TO SAVEPOINT tool_batch_item")
                if str(exc) not in ("guest_request_changed_since_approval", "guest_request_not_found"):
                    raise
                outcome[item["ref"]] = {"state": "changed"}
        conn.commit()                    # the approval and every prepared receipt, before anything is dispatched
        stop = None
        for item, context, intent in ready:
            if stop is not None:
                outcome[item["ref"]] = {"state": stop}
                continue
            with self._tx() as c, _cursor(c) as cur2:
                if self._clock(cur2) >= deadline:
                    stop = "window_ended"            # at or after the execution deadline: no new dispatch (§9.2)
                    outcome[item["ref"]] = {"state": stop}
                    continue
            try:
                with self._tx() as c:
                    r = actions.execute(c, context, intent, f"portal-{turn_key[:40]}", current_policy_version=context.policy_version)
            except ActionContractError as exc:      # refused before anything committed (C07 re-checks the deadline)
                if str(exc) in ("grant_expired", "guest_action_context_expired"):
                    stop = "window_ended"
                    outcome[item["ref"]] = {"state": "window_ended"}
                else:
                    outcome[item["ref"]] = {"state": "refused"}
                    stop = "not_attempted"
                continue
            except Exception:
                _log.exception("mc tool guest batch: item outcome unknown")
                outcome[item["ref"]] = {"state": "unknown"}
                stop = "not_attempted"             # nothing more until this one is reconciled
                continue
            state = r["receipt"]["state"]
            outcome[item["ref"]] = {"state": "rejected" if state == "verified" and r["outcome"] == "rejected"
                                    else "already" if state == "verified" else "changed", "name": r.get("name")}
        results = [(item["ref"], outcome[item["ref"]]) for item, _, _ in items]
        logged = ""
        done = [ref for ref, o in results if o["state"] == "rejected"]
        if done and p["payload"].get("standard_change"):
            logged = self._log_standard_change({**p, "payload": {**p["payload"], "ref": ", ".join(done)}},
                                               {"receipt_id": f"{len(done)} receipt(s)"}, str(p["operation_id"]))
        return self._batch_reply(results, deadline, logged)

    def _batch_reply(self, results, deadline, logged="", repeated=False):
        """What happened to each item, in English, never 'all done' unless all were."""
        def who(ref, o):
            return f"{o['name']} ({ref})" if o.get("name") else ref
        groups = {}
        for ref, o in results:
            groups.setdefault(o["state"], []).append(who(ref, o))
        total, parts = len(results), []
        if groups.get("rejected"):
            parts.append(f"{len(groups['rejected'])} of {total} rejected, verified by read-back: "
                         + ", ".join(groups["rejected"]) + ".")
        if groups.get("already"):
            parts.append(f"{len(groups['already'])} already rejected, nothing changed: " + ", ".join(groups["already"]) + ".")
        if groups.get("changed"):
            parts.append(f"{len(groups['changed'])} changed since you approved and {'was' if len(groups['changed']) == 1 else 'were'} "
                         "not rejected: " + ", ".join(groups["changed"]) + ".")
        if groups.get("window_ended"):
            late = groups["window_ended"]
            parts.append(f"{len(late)} not done: the five-minute window to carry out your approval ended at "
                         f"{offers.expiry_words(deadline, self.display_tz)}: " + ", ".join(late)
                         + f". Ask me to propose {'it' if len(late) == 1 else 'them'} again if you still want that.")
        if groups.get("not_done"):
            parts.append(f"{len(groups['not_done'])} not done, nothing changed: " + ", ".join(groups["not_done"]) + ".")
        if groups.get("refused"):
            parts.append("Refused safely, nothing changed: " + ", ".join(groups["refused"]) + ".")
        if groups.get("unknown"):
            parts.append("Could not confirm the outcome for " + ", ".join(groups["unknown"])
                         + ". Do not ask again yet; check it first.")
        if groups.get("not_attempted"):
            parts.append("Not attempted after that: " + ", ".join(groups["not_attempted"]) + ".")
        states = {o["state"] for _, o in results}
        state = ("unknown" if "unknown" in states else "verified" if states <= {"rejected", "already"}
                 else "partial" if states & {"rejected", "already"} else "failed")
        text = ("That was already handled; nothing new was done. " if repeated else "") + " ".join(parts) + logged
        return _reply(text, state, items=[{"ref": ref, "state": o["state"]} for ref, o in results], repeated=repeated)

    def _batch_report(self, conn, p):
        """A retried yes: each item's outcome from its own receipt and evidence. Dispatches nothing."""
        results = []
        with _cursor(conn) as cur:
            for item, _, _ in self._batch_items(p):
                try:
                    receipt = effects.RECEIPTS.read(conn, self.principal, self.environment, item["operation_id"])
                except ActionContractError:
                    receipt = None
                cur.execute("""SELECT outcome FROM guild.mc_guest_action_evidence WHERE operation_id=%s""",
                            (item["operation_id"],))
                ev = cur.fetchone()
                state = (receipt or {}).get("state")
                results.append((item["ref"], {"state": "changed" if state in (None, "failed")
                                              else "not_done" if state == "prepared"
                                              else ("rejected" if ev and ev["outcome"] == "rejected" else "already")
                                              if state == "verified" else "unknown"}))
        return self._batch_reply(results, p["execution_deadline"], repeated=True)

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

    def _log_standard_change(self, p, receipt, operation_id):
        """Prod: log this use of a pre-approved standard change on its ticket, refs and receipt only (standard §5)."""
        sc = p["payload"].get("standard_change")
        if not sc:
            return ""
        ref = p["payload"].get("ref") or "a new guest request"
        text = (f"Standard change {sc['id']} used ({self.environment}): {p['kind']} {ref}. Receipt "
                f"{receipt['receipt_id']}, operation {operation_id}, verified by read-back. Approved by Robert in the "
                "Guild conversation.")
        try:
            state, _ = self.comment_issue(sc["ticket"], text) if self.comment_issue else ("refused", None)
        except Exception:
            state = "unknown"
        if state == "created":
            return f" Logged on ticket #{sc['ticket']}."
        return f" I could not log this use on ticket #{sc['ticket']}; please log it there by hand."

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
        """The owner's own invite as a guest request (domain = the app), returned as its ref. An open request with the
        same email is reused; any other state (rejected, granted) refuses, leaving the guest's record as it is."""
        pay = p["payload"]
        with self._tx() as conn, _cursor(conn) as cur:
            cur.execute("SELECT id, status FROM guild.guest_requests WHERE lower(email)=%s FOR UPDATE", (pay["email"],))
            row = cur.fetchone()
            if row is None:
                cur.execute("""INSERT INTO guild.guest_requests (name, email, reason, domain, status)
                    VALUES (%s,%s,%s,%s,'requested') RETURNING id""", (pay["name"], pay["email"], INVITE_REASON, pay["app"]))
                return f"guild:{cur.fetchone()['id']}", None
            # Never rewrite a guest's own earlier request, even a rejected one (review P3-4): that is the guest's
            # record. Robert decides on it separately.
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
                      f"(user {detail.get('username')}), verified by read-back. {tail}"
                      + self._log_standard_change(p, receipt, effect.operation_id), "verified",
                      ref=ref, receipt_id=str(receipt["receipt_id"]))
