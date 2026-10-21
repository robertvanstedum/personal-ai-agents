"""Bind a tool call to the owner's turn through the durable turn ledger (design §3).

The portal records a per-turn binding when it dispatches a claimed owner turn (``record``). The tool
service resolves a call (``resolve``) from the runtime's own session key, never from model
arguments, and accepts it only while that ledger entry is still claimed at the same generation with
an unexpired lease. When the turn ends the binding is dead: there is nothing to close.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from core.minimoi_actions.context import ActionContext, ActionContractError

POLICY = "mc-tools-v1"
SESSION_USER = re.compile(r"guild-mc:[0-9a-f]{32}")
# OpenClaw: agent:<agentId>:openai-user:<user> (openai-http resolveSessionKey, buildAgentMainSessionKey).
_SESSION_KEY = re.compile(r"agent:(?P<agent>[a-z][a-z0-9-]{0,63}):openai-user:(?P<user>guild-mc:[0-9a-f]{32})")


class NotBound(ActionContractError):
    """No live owner turn owns this call. A fixed code, never request contents."""


@dataclass(frozen=True, slots=True, repr=False)
class TurnBinding:
    principal: str
    environment: str
    conversation_id: str
    note_request_id: str
    ledger_id: int
    generation: int
    lease_until: datetime

    @property
    def turn_id(self) -> str:
        return f"ledger:{self.ledger_id}:{self.generation}"

    def context(self, grant_ids=()) -> ActionContext:
        """The server-owned action context for this turn. It expires with the claim's lease."""
        return ActionContext(self.principal, "mc", self.environment, self.conversation_id, self.turn_id,
                             self.turn_id, self.generation, self.lease_until, POLICY, "mc", "recorded", "owner",
                             tuple(grant_ids))


def record(conn, *, ledger_id: int, claim_generation: int, principal: str, conversation_id: str,
           note_request_id: str, session_user: str) -> bool:
    """Portal dispatch only: the binding for a turn the ledger has claimed. True when the row matches a
    live claim (written now or already there); False when the claim is not live, so tools stay refused."""
    if (type(ledger_id) is not int or type(claim_generation) is not int or claim_generation < 1
            or not isinstance(session_user, str) or not SESSION_USER.fullmatch(session_user)):
        raise ActionContractError("tool_turn_invalid")
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM guild.mc_turn_ledger WHERE id=%s AND state='claimed' AND claim_generation=%s
            AND principal=%s AND conversation_id=%s AND note_request_id=%s AND lease_until > clock_timestamp()
            FOR SHARE""", (ledger_id, claim_generation, principal, conversation_id, note_request_id))
        if cur.fetchone() is None:
            return False
        cur.execute("""INSERT INTO guild.mc_tool_turns (ledger_id, claim_generation, principal, conversation_id,
            note_request_id, runtime_user) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (ledger_id, claim_generation, principal, conversation_id, note_request_id, session_user))
        cur.execute("""SELECT principal, conversation_id, note_request_id, runtime_user FROM guild.mc_tool_turns
            WHERE ledger_id=%s AND claim_generation=%s""", (ledger_id, claim_generation))
        return tuple(cur.fetchone()) == (principal, conversation_id, note_request_id, session_user)


def parse_session_key(session_key, agent_id, allowed_agents) -> str:
    """The portal's ``user`` value from a host-supplied session key, or NotBound."""
    if not isinstance(session_key, str) or not isinstance(agent_id, str) or len(session_key) > 200:
        raise NotBound("tool_session_invalid")
    m = _SESSION_KEY.fullmatch(session_key)
    if m is None or m.group("agent") != agent_id or agent_id not in allowed_agents:
        raise NotBound("tool_session_invalid")
    return m.group("user")


def resolve(conn, *, session_key, agent_id, allowed_agents, principal: str, environment: str) -> TurnBinding:
    """The live owner turn for this call, read under a share lock on its ledger entry, or NotBound."""
    session_user = parse_session_key(session_key, agent_id, allowed_agents)
    with conn.cursor() as cur:
        cur.execute("""SELECT t.ledger_id, t.claim_generation, t.conversation_id, t.note_request_id, l.lease_until
            FROM guild.mc_tool_turns t JOIN guild.mc_turn_ledger l ON l.id = t.ledger_id
            WHERE t.runtime_user=%s AND t.principal=%s AND l.principal=t.principal
              AND l.state='claimed' AND l.claim_generation=t.claim_generation
              AND l.conversation_id=t.conversation_id AND l.note_request_id=t.note_request_id
              AND l.lease_until > clock_timestamp()
            FOR SHARE OF l""", (session_user, principal))
        rows = cur.fetchall()
    if len(rows) != 1:
        raise NotBound("tool_turn_not_live")
    ledger_id, generation, conversation_id, note_id, lease_until = rows[0]
    return TurnBinding(principal, environment, conversation_id, note_id, int(ledger_id), int(generation),
                       lease_until)
