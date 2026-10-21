"""Owner commands for guest requests in the MC conversation (journey link 4: proposal and approval).

Same authority model as Board outcomes and owner notes: the owner's authenticated turn carries
authority, the platform resolves and acts, and no model output can trigger an effect.

    "Reject the guest request from Bruno"   → find; one match: propose; several: ask which; none: say so
    "Confirm"                               → the pending proposal in THIS conversation becomes a
                                              one-time exact-approval grant, then reject + verify
    "Cancel"                                → the pending proposal is dropped; nothing changes

A proposal lasts 10 minutes and binds the exact request and its target version; if the request
changes before confirmation, nothing happens and the owner is told. Every reply comes from the
platform's own read-back, never from a model claim.
"""
from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from core.minimoi_actions.context import ActionContext, ActionContractError
from core.minimoi_actions.grants import GrantDefinition, GrantStore
from core.minimoi_actions.receipts import EffectIntent, _cursor

from . import guest

FLAG = "MINIMOI_MC_GUEST_ACTIONS"
POLICY = "guest-actions-v1"
PROPOSAL_MINUTES = 10
_GUARDS = ('"', "“", "”", "`", ">")
_REJECT = re.compile(r"^(?:please\s+)?(?:reject|decline|deny)\s+(?:the\s+)?(?:guest\s+)?(?:access\s+)?request\s+"
                     r"(?:from|by|for|of)\s+(?P<who>[^!?\n]{2,80}?)\s*[.!]?$", re.I)
# The owner's own words, inside a longer message: "Guten Morgen. … Just reject the guest requests we have."
_NATURAL = re.compile(r"^(?:(?:just|please|so|and|then|ok(?:ay)?|now|can\s+you|could\s+you|would\s+you|kindly)[,\s]+)*"
                      r"(?:reject|decline|deny)\s+(?:the\s+|all\s+(?:the\s+|of\s+the\s+)?|our\s+|any\s+|that\s+|this\s+)?"
                      r"(?:open\s+|pending\s+|new\s+)?guest\s+(?:access\s+)?requests?"
                      r"(?:\s+(?:from|by|for|of)\s+(?P<who>[^!?\n]{2,80}?))?"
                      r"(?:\s+(?:we\s+have|that\s+are\s+(?:open|pending)|(?:for|with)\s+me|(?:right\s+)?now|please))*\s*$", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_CONFIRM = re.compile(r"^(?:yes[,.!]?\s+)?(?:confirm(?:ed)?|approve(?:d)?)(?:\s+(?:it|that|the\s+reject(?:ion)?))?\s*[.!]?$", re.I)
_CANCEL = re.compile(r"^(?:no[,.!]?\s+)?(?:cancel|don['’]t|do\s+not)(?:\s+(?:it|that|the\s+reject(?:ion)?))?\s*[.!]?$", re.I)


@dataclass(frozen=True)
class Command:
    kind: str           # reject | confirm | cancel
    who: str | None = None


def parse(text) -> Command | None:
    if not isinstance(text, str):
        return None
    t = text.strip()
    if not t or len(t) > 600 or any(c in t for c in _GUARDS):
        return None
    if len(t) <= 200 and "\n" not in t:
        exact = _parse_exact(t)
        if exact is not None:
            return exact
    for sentence in _SENTENCE.split(t):                 # an instruction inside a longer message
        n = _NATURAL.match(sentence.strip().rstrip(".!?").strip())
        if n:
            who = (n.group("who") or "").strip().rstrip(".").strip()
            return Command("reject", who if len(who) >= 2 else None)
    return None


def _parse_exact(t):
    m = _REJECT.match(t)
    if m:
        who = m.group("who").strip().rstrip(".").strip()
        return Command("reject", who) if len(who) >= 2 else None
    if _CONFIRM.match(t):
        return Command("confirm")
    if _CANCEL.match(t):
        return Command("cancel")
    return None


def _context(principal, environment, conversation, turn_key, now, grant_ids=()):
    return ActionContext(principal, "mc", environment, conversation, turn_key, turn_key, 1,
                         now + timedelta(minutes=5), POLICY, "mc", "recorded", "owner", tuple(grant_ids))


def _date(value) -> str:
    return value[:10] if isinstance(value, str) else str(value)[:10]


def propose(conn, principal, environment, conversation, turn_key, match) -> str:
    """Record the exact reject of one open request (``match`` from GuestActions.open_requests) as this
    conversation's pending proposal, in the caller's transaction. Only the owner's Confirm acts on it."""
    proposal_id, operation_id = str(uuid4()), str(uuid4())
    with _cursor(conn) as cur:
        # One pending proposal per conversation: a new one replaces the old.
        cur.execute("""UPDATE guild.mc_guest_proposals SET state='cancelled'
            WHERE principal_id=%s AND environment=%s AND conversation_id=%s AND state='pending'""",
            (principal, environment, conversation))
        cur.execute("""INSERT INTO guild.mc_guest_proposals (proposal_id, principal_id, environment,
            conversation_id, owner_turn_id, ref, target_version, operation_id, expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s, clock_timestamp() + %s * interval '1 minute')""",
            (proposal_id, principal, environment, conversation, turn_key, match["ref"],
             match["target_version"], operation_id, PROPOSAL_MINUTES))
    return proposal_id


def proposal_words(m) -> str:
    return (f"Reject the guest request from {m['name'] or m['email']} <{m['email']}>, "
            f"requested {_date(m['requested_at'])} for {m['domain']}? "
            f"Reply Confirm within {PROPOSAL_MINUTES} minutes, or Cancel.")


class GuestCommands:
    def __init__(self, connect, *, principal: str, environment: str):
        """``connect()`` returns a new psycopg2 connection to the guild database."""
        self._connect, self.principal, self.environment = connect, principal, environment
        self.grants = GrantStore({guest.CAPABILITY: guest.CATALOGUE_RULE})
        self.actions = guest.GuestActions(self.grants, owner_principal=principal)

    @contextmanager
    def connect(self):
        """One transaction per block: commit on success, roll back on error, always close."""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def has_pending(self, conversation: str) -> bool:
        """'Confirm'/'Cancel' are ordinary words: only claim them while a guest proposal is
        pending (or was just confirmed) in this conversation."""
        with self.connect() as conn, _cursor(conn) as cur:
            cur.execute("""SELECT 1 FROM guild.mc_guest_proposals WHERE principal_id=%s AND environment=%s
                AND conversation_id=%s AND (state='pending' OR (state='confirmed'
                AND created_at > clock_timestamp() - interval '30 minutes')) LIMIT 1""",
                (self.principal, self.environment, conversation))
            return cur.fetchone() is not None

    def handle(self, command: Command, *, conversation: str, turn_key: str, now=None) -> dict:
        now = now or datetime.now(timezone.utc)
        try:
            if command.kind == "reject":
                return self._reject(command.who, conversation, turn_key, now)
            if command.kind == "confirm":
                return self._confirm(conversation, turn_key, now)
            return self._cancel(conversation, now)
        except ActionContractError as exc:
            return {"reply": f"I could not do that safely ({exc}). Nothing was changed.",
                    "operation": {"type": "guest_action", "state": "refused", "code": str(exc)}}

    # ── propose ────────────────────────────────────────────────────────────
    def _reject(self, who, conversation, turn_key, now):
        context = _context(self.principal, self.environment, conversation, turn_key, now)
        with self.connect() as conn:
            if who is None:                                  # "reject the guest requests": the open ones
                matches = self.actions.open_requests(conn, context)
                if not matches:
                    return {"reply": "There are no open guest requests right now. Nothing was changed.",
                            "operation": {"type": "guest_action", "state": "no_match"}}
                if len(matches) > 1:
                    rows = "\n".join(f"- {m['name'] or '(no name)'} <{m['email']}>, requested {_date(m['requested_at'])}"
                                     for m in matches[:10])
                    more = f"\n…and {len(matches) - 10} more." if len(matches) > 10 else ""
                    return {"reply": f"There are {len(matches)} open guest requests:\n{rows}{more}\n"
                                     "I reject one at a time, each confirmed by you. Which first? "
                                     "Say for example: Reject the guest request from <name>.",
                            "operation": {"type": "guest_action", "state": "clarify", "matches": len(matches)}}
            else:
                matches = self.actions.find(conn, context, who)
            if not matches:
                open_count = len(self.actions.open_requests(conn, context))
                return {"reply": f"No open guest request matches “{who}”. There are {open_count} open request(s).",
                        "operation": {"type": "guest_action", "state": "no_match"}}
            if len(matches) > 1:
                rows = "\n".join(f"- {m['name'] or '(no name)'} <{m['email']}>, requested {_date(m['requested_at'])}"
                                 for m in matches[:5])
                more = f"\n…and {len(matches) - 5} more." if len(matches) > 5 else ""
                return {"reply": f"Several open requests match “{who}”:\n{rows}{more}\n"
                                 "Which one? Say for example: Reject the guest request from <email>.",
                        "operation": {"type": "guest_action", "state": "clarify", "matches": len(matches)}}
            m = matches[0]
            propose(conn, self.principal, self.environment, conversation, turn_key, m)
        return {"reply": proposal_words(m), "operation": {"type": "guest_action", "state": "proposed", "ref": m["ref"]}}

    # ── approve and act ────────────────────────────────────────────────────
    def _confirm(self, conversation, turn_key, now):
        with self.connect() as conn, _cursor(conn) as cur:
            cur.execute("""SELECT * FROM guild.mc_guest_proposals
                WHERE principal_id=%s AND environment=%s AND conversation_id=%s
                  AND (state='pending' OR (state='confirmed' AND confirm_turn_id=%s))
                ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
                (self.principal, self.environment, conversation, turn_key))
            p = cur.fetchone()
            if p is None:
                return {"reply": "Nothing is waiting for your confirmation in this conversation.",
                        "operation": {"type": "guest_action", "state": "nothing_pending"}}
            cur.execute("SELECT clock_timestamp() AS now")
            db_now = cur.fetchone()["now"]
            if p["state"] == "pending" and p["expires_at"] <= db_now:
                cur.execute("UPDATE guild.mc_guest_proposals SET state='stale' WHERE proposal_id=%s", (p["proposal_id"],))
                return {"reply": "That proposal expired, so nothing was done. Ask again if you still want it.",
                        "operation": {"type": "guest_action", "state": "expired"}}
            request = guest.reject_request(p["ref"])
            grant_id = str(UUID(hashlib.sha256(("guest-grant\0" + str(p["proposal_id"])).encode()).hexdigest()[:32]))
            grant = GrantDefinition(grant_id, self.principal, self.environment, conversation,
                                    tuple(dict.fromkeys((p["owner_turn_id"], turn_key))), ("mc",), POLICY,
                                    guest.CAPABILITY, guest.VERSION, (request.arguments_sha256,), 1,
                                    p["expires_at"] + timedelta(minutes=5), proposal_id=str(p["proposal_id"]),
                                    approved_operation_id=str(p["operation_id"]),
                                    approval_expires_at=p["expires_at"], expected_target_version=p["target_version"])
            if p["state"] == "pending":
                self.grants.issue(conn, grant)
                cur.execute("""UPDATE guild.mc_guest_proposals SET state='confirmed', confirm_turn_id=%s
                    WHERE proposal_id=%s""", (turn_key, p["proposal_id"]))
            context = _context(self.principal, self.environment, conversation, turn_key, db_now, (grant_id,))
            intent = EffectIntent(str(p["operation_id"]), request, grant_id=grant_id,
                                  proposal_id=str(p["proposal_id"]), expected_target_version=p["target_version"])
            if p["state"] == "confirmed":
                # The same confirmation retried: never prepare again; report what the receipt says.
                try:
                    receipt = self.actions.receipts.read(conn, self.principal, self.environment, intent.operation_id)
                except ActionContractError:
                    receipt = None
                if receipt is not None and receipt["state"] in ("verified", "failed"):
                    return {"reply": "That was already done earlier; nothing changed now.",
                            "operation": {"type": "guest_action", "state": receipt["state"], "repeated": True}}
            try:
                cur.execute("SAVEPOINT guest_prepare")
                self.actions.prepare(conn, context, intent, current_policy_version=POLICY)
                cur.execute("RELEASE SAVEPOINT guest_prepare")
            except ActionContractError as exc:
                cur.execute("ROLLBACK TO SAVEPOINT guest_prepare")
                if str(exc) == "guest_request_changed_since_approval":
                    cur.execute("UPDATE guild.mc_guest_proposals SET state='stale' WHERE proposal_id=%s",
                                (p["proposal_id"],))
                    return {"reply": "That request changed since you asked (for example it was already granted), "
                                     "so nothing was done.", "operation": {"type": "guest_action", "state": "stale"}}
                if str(exc) != "receipt_operation_reused":
                    raise
        # Commit the approval and prepared receipt first; then act in its own transaction.
        with self.connect() as conn:
            try:
                result = self.actions.execute(conn, context, intent, f"portal-{turn_key[:40]}",
                                              current_policy_version=POLICY)
            except ActionContractError as exc:
                if str(exc) != "receipt_terminal":
                    raise
                conn.rollback()
                receipt = self.actions.receipts.read(conn, self.principal, self.environment, intent.operation_id)
                return {"reply": "That was already done earlier; nothing changed now.",
                        "operation": {"type": "guest_action", "state": receipt["state"], "repeated": True}}
        return {"reply": self.actions.reply(result),
                "operation": {"type": "guest_action", "state": result["receipt"]["state"],
                              "outcome": result["outcome"], "ref": result["ref"],
                              "receipt_id": str(result["receipt"]["receipt_id"])}}

    def _cancel(self, conversation, now):
        with self.connect() as conn, _cursor(conn) as cur:
            cur.execute("""UPDATE guild.mc_guest_proposals SET state='cancelled'
                WHERE principal_id=%s AND environment=%s AND conversation_id=%s AND state='pending'
                RETURNING ref""", (self.principal, self.environment, conversation))
            row = cur.fetchone()
        if row is None:
            return {"reply": "Nothing was waiting, so there is nothing to cancel.",
                    "operation": {"type": "guest_action", "state": "nothing_pending"}}
        return {"reply": "Cancelled. Nothing was changed.", "operation": {"type": "guest_action", "state": "cancelled"}}
