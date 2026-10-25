"""Bind a tool call to the owner's turn through the durable turn ledger (design §3, spec v1.2 §7.1).

The portal records a per-turn binding when it dispatches a claimed owner turn (``record``), then attaches
the OpenClaw run it opened for that turn as soon as the relay reports it (``attach_run``). The tool service
resolves a call (``resolve``) from the exact run the plugin's host-sealed headers name, plus the runtime's
own session key and agent, never from model arguments. It accepts the call only while that run's ledger
entry is still claimed at the same generation with an unexpired lease. The conversation's session is
stable across turns, so a session alone never identifies a turn: a delayed call from an earlier run finds
that run's ended turn and is refused, even while a later turn of the same conversation is live. When the
turn ends the binding is dead: there is nothing to close.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime

from core.minimoi_actions.context import ActionContext, ActionContractError

POLICY = "mc-tools-v1"
SESSION_USER = re.compile(r"guild-mc:[0-9a-f]{32}")
# OpenClaw: agent:<agentId>:openai-user:<user> (openai-http resolveSessionKey, buildAgentMainSessionKey).
RUN_ID = re.compile(r"chatcmpl_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
TOOL_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
INSTANCE_ID = re.compile(r"[0-9a-f]{32}")
AGENT_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
# A tool call can reach MiniMoi before the portal has read the run's opening event; it waits this long for
# THAT run to be attached, and never falls back to looking the turn up by session.
RUN_WAIT_S = 2.0
_RUN_POLL_S = 0.05
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
    policy_version: str = POLICY            # the tool service sets the policy in force (spec §8.1)

    @property
    def turn_id(self) -> str:
        return f"ledger:{self.ledger_id}:{self.generation}"

    def context(self, grant_ids=()) -> ActionContext:
        """The server-owned action context for this turn. It expires with the claim's lease."""
        return ActionContext(self.principal, "mc", self.environment, self.conversation_id, self.turn_id,
                             self.turn_id, self.generation, self.lease_until, self.policy_version, "mc", "recorded",
                             "owner",
                             tuple(grant_ids))


def _lock_current(cur, environment: str, mode: str):
    """(current runtime instance, current portal epoch) under row locks, ALWAYS in this order (runtime, then epoch, then
    any tool turn) by every path that admits, records or replaces, so none of them can deadlock or interleave: a
    replacement (FOR UPDATE) waits for in-flight dispatches and admissions (FOR SHARE) and vice versa (Codex R1)."""
    cur.execute(f"SELECT instance_id FROM guild.mc_tool_runtime WHERE environment=%s FOR {mode}", (environment,))
    runtime = cur.fetchone()
    cur.execute(f"SELECT epoch FROM guild.mc_tool_portal_epoch WHERE environment=%s FOR {mode}", (environment,))
    epoch = cur.fetchone()
    return (runtime[0] if runtime else None), (str(epoch[0]) if epoch else None)


def record(conn, *, ledger_id: int, claim_generation: int, principal: str, conversation_id: str,
           note_request_id: str, session_user: str, runtime_agent: str, environment: str,
           portal_epoch: str | None = None) -> bool:
    """Portal dispatch only: the binding for a turn the ledger has claimed, with the exact runtime agent the portal
    sends it to and the runtime instance current at this dispatch (never the one current when a delayed first call
    arrives; none announced yet: none bound, and every call is refused). True when the row matches a live claim
    (written now or already there); False when the claim is not live, so tools stay refused."""
    if environment not in ("dev", "prod"):
        raise ActionContractError("tool_turn_invalid")
    if (type(ledger_id) is not int or type(claim_generation) is not int or claim_generation < 1
            or not isinstance(session_user, str) or not SESSION_USER.fullmatch(session_user)
            or not isinstance(runtime_agent, str) or not AGENT_ID.fullmatch(runtime_agent)):
        raise ActionContractError("tool_turn_invalid")
    with conn.cursor() as cur:
        instance, epoch = _lock_current(cur, environment, "SHARE")
        cur.execute("""SELECT 1 FROM guild.mc_turn_ledger WHERE id=%s AND state='claimed' AND claim_generation=%s
            AND principal=%s AND conversation_id=%s AND note_request_id=%s AND lease_until > clock_timestamp()
            FOR SHARE""", (ledger_id, claim_generation, principal, conversation_id, note_request_id))
        if cur.fetchone() is None:
            return False
        # The epoch is this portal's; it must be the current one (else this process is already superseded: no tools).
        bound_epoch = portal_epoch if portal_epoch is not None and portal_epoch == epoch else None
        cur.execute("""INSERT INTO guild.mc_tool_turns (ledger_id, claim_generation, principal, conversation_id,
            note_request_id, runtime_user, runtime_agent, runtime_instance, portal_epoch)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (ledger_id, claim_generation, principal, conversation_id, note_request_id, session_user, runtime_agent,
             instance, bound_epoch))
        # A Stop that came before this binding (recorded against the claim) applies to it from the start.
        cur.execute("""UPDATE guild.mc_tool_turns t SET revoked_at = r.revoked_at, revoked_reason = r.reason
            FROM guild.mc_tool_turn_revocations r WHERE t.ledger_id=%s AND t.claim_generation=%s
            AND r.ledger_id = t.ledger_id AND r.claim_generation = t.claim_generation AND t.revoked_at IS NULL""",
            (ledger_id, claim_generation))
        cur.execute("""SELECT principal, conversation_id, note_request_id, runtime_user, runtime_agent
            FROM guild.mc_tool_turns WHERE ledger_id=%s AND claim_generation=%s""", (ledger_id, claim_generation))
        return tuple(cur.fetchone()) == (principal, conversation_id, note_request_id, session_user, runtime_agent)


def attach_run(conn, *, ledger_id: int, claim_generation: int, run_id: str, environment: str) -> bool:
    """Portal stream only: the OpenClaw run the portal opened for this claimed turn, from the relay's ``run``
    event (the runtime instance was fixed at dispatch by ``record``). Attached once and never moved: True when the
    binding now holds exactly this run, False otherwise (no binding, already another run, the run already bound
    elsewhere, the claim no longer live, or no runtime instance has announced itself). A stopped turn still gets its
    run attached: that admits nothing (resolve refuses it as stopped) and lets a late call be told so at once."""
    if type(ledger_id) is not int or type(claim_generation) is not int or not isinstance(run_id, str) \
            or not RUN_ID.fullmatch(run_id) or environment not in ("dev", "prod"):
        raise ActionContractError("tool_run_invalid")
    with conn.cursor() as cur:
        _lock_current(cur, environment, "SHARE")
        cur.execute("""UPDATE guild.mc_tool_turns t SET runtime_run_id=%s, run_bound_at=clock_timestamp()
            FROM guild.mc_turn_ledger l, guild.mc_tool_runtime r
            WHERE t.ledger_id=%s AND t.claim_generation=%s AND t.runtime_run_id IS NULL
              AND r.environment=%s AND t.runtime_instance = r.instance_id
              AND l.id=t.ledger_id AND l.state='claimed' AND l.claim_generation=t.claim_generation
              AND l.lease_until > clock_timestamp()
              AND NOT EXISTS (SELECT 1 FROM guild.mc_tool_turns o WHERE o.runtime_run_id=%s)""",
            (run_id, ledger_id, claim_generation, environment, run_id))
        if cur.rowcount == 1:
            return True
        cur.execute("SELECT runtime_run_id FROM guild.mc_tool_turns WHERE ledger_id=%s AND claim_generation=%s",
                    (ledger_id, claim_generation))
        row = cur.fetchone()
    return row is not None and row[0] == run_id


def register_instance(conn, *, environment: str, instance_id: str) -> bool:
    """The plugin's announcement of its gateway process (authenticated by the listener). A never-seen instance becomes
    current and the previous one is retired for good; a replacement revokes every turn bound to an earlier instance,
    or to none, at once (spec §7.4). A retired instance (a late or replayed announcement from a replaced process) is
    refused and changes nothing: no last-arrival-wins. True when it became current now, False when it already was."""
    if environment not in ("dev", "prod") or not isinstance(instance_id, str) or not INSTANCE_ID.fullmatch(instance_id):
        raise ActionContractError("tool_instance_invalid")
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f'["mc-runtime","{environment}"]',))
        # Same order as every admitting path: the runtime row first (here exclusively: in-flight dispatches finish first).
        cur.execute("SELECT instance_id FROM guild.mc_tool_runtime WHERE environment=%s FOR UPDATE", (environment,))
        row = cur.fetchone()
        if row is not None and row[0] == instance_id:
            return False
        cur.execute("SELECT retired_at FROM guild.mc_tool_runtime_history WHERE instance_id=%s", (instance_id,))
        seen = cur.fetchone()
        if seen is not None:
            raise ActionContractError("tool_instance_retired")
        if row is not None:
            cur.execute("""INSERT INTO guild.mc_tool_runtime_history (instance_id, environment, retired_at)
                VALUES (%s,%s,clock_timestamp()) ON CONFLICT (instance_id) DO UPDATE SET retired_at=clock_timestamp()""",
                (row[0], environment))
        cur.execute("""INSERT INTO guild.mc_tool_runtime_history (instance_id, environment) VALUES (%s,%s)""",
                    (instance_id, environment))
        cur.execute("""INSERT INTO guild.mc_tool_runtime (environment, instance_id) VALUES (%s,%s)
            ON CONFLICT (environment) DO UPDATE SET instance_id=excluded.instance_id, registered_at=clock_timestamp()""",
            (environment, instance_id))
        cur.execute("""UPDATE guild.mc_tool_turns SET revoked_at=clock_timestamp(), revoked_reason='runtime_replaced'
            WHERE revoked_at IS NULL AND runtime_instance IS DISTINCT FROM %s""", (instance_id,))
        return True


def activate_policy(conn, *, environment: str, policy_version: str) -> bool:
    """Make ``policy_version`` the one in force here (spec §7.4). When it differs from the version in force, every live
    turn's admission is revoked (reason 'policy_changed') and every pending offer made under another version is closed:
    nothing admitted or offered under the old policy can go on. True when it changed now, False when already active."""
    if environment not in ("dev", "prod") or not isinstance(policy_version, str) or len(policy_version) > 256:
        raise ActionContractError("tool_policy_invalid")
    with conn.cursor() as cur:
        cur.execute("SELECT policy_version FROM guild.mc_tool_policy WHERE environment=%s FOR UPDATE", (environment,))
        row = cur.fetchone()
        if row is not None and row[0] == policy_version:
            return False
        cur.execute("""INSERT INTO guild.mc_tool_policy (environment, policy_version) VALUES (%s,%s)
            ON CONFLICT (environment) DO UPDATE SET policy_version=excluded.policy_version,
            activated_at=clock_timestamp()""", (environment, policy_version))
        if row is None:
            return True                       # the first policy here: nothing was admitted under another one
        cur.execute("""UPDATE guild.mc_tool_turns SET revoked_at=clock_timestamp(), revoked_reason='policy_changed'
            WHERE revoked_at IS NULL""")
        cur.execute("""UPDATE guild.mc_tool_proposals SET state='stale', closed_reason='policy_changed'
            WHERE environment=%s AND state='pending' AND policy_version IS DISTINCT FROM %s""",
            (environment, policy_version))
        return True


def admit(conn, binding: "TurnBinding", *, tool: str, tool_call_id: str, policy_version: str, limit: int) -> None:
    """Record this admitted call for its turn and enforce the policy's per-turn quota for the tool (spec §8.3). The same
    host call id retried is the same admission, never counted twice. Raises NotBound("tool_quota_exceeded")."""
    with conn.cursor() as cur:
        # One admission at a time per turn and tool, so two concurrent calls cannot both pass the count.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f'["mc-admit",{binding.ledger_id},{binding.generation},"{tool}"]',))
        cur.execute("""INSERT INTO guild.mc_tool_admissions (ledger_id, claim_generation, tool_call_id, tool,
            policy_version) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (binding.ledger_id, binding.generation, tool_call_id, tool, policy_version))
        cur.execute("""SELECT count(*) FROM guild.mc_tool_admissions WHERE ledger_id=%s AND claim_generation=%s
            AND tool=%s AND admitted_at <= (SELECT admitted_at FROM guild.mc_tool_admissions WHERE ledger_id=%s
            AND claim_generation=%s AND tool_call_id=%s)""",
            (binding.ledger_id, binding.generation, tool, binding.ledger_id, binding.generation, tool_call_id))
        if cur.fetchone()[0] > limit:
            raise NotBound("tool_quota_exceeded")


def start_portal_epoch(conn, *, environment: str, epoch: str) -> None:
    """A portal process starting its tool listener: its epoch becomes current, and every tool turn recorded under any
    earlier epoch is revoked (reason 'portal_restarted'), so a Stop whose durable record failed in the previous
    process cannot be forgotten (Codex R2). Raises on failure: the caller then does not start the listener."""
    if environment not in ("dev", "prod"):
        raise ActionContractError("tool_epoch_invalid")
    with conn.cursor() as cur:
        cur.execute("SELECT instance_id FROM guild.mc_tool_runtime WHERE environment=%s FOR SHARE", (environment,))
        cur.execute("SELECT epoch FROM guild.mc_tool_portal_epoch WHERE environment=%s FOR UPDATE", (environment,))
        cur.execute("""INSERT INTO guild.mc_tool_portal_epoch (environment, epoch) VALUES (%s,%s)
            ON CONFLICT (environment) DO UPDATE SET epoch=excluded.epoch, started_at=clock_timestamp()""",
            (environment, epoch))
        cur.execute("""UPDATE guild.mc_tool_turns SET revoked_at=clock_timestamp(), revoked_reason='portal_restarted'
            WHERE revoked_at IS NULL AND portal_epoch IS DISTINCT FROM %s""", (epoch,))


def revoke(conn, *, ledger_id: int, claim_generation: int) -> bool:
    """The owner's Stop: no new tool call is admitted for this turn from now on (spec §7.4). Runs before the runtime is
    asked to abort and does not end the ledger claim, so the turn still holds its place until the runtime is done.
    The row lock waits for any admission already in progress, so once this commits nothing new gets through.
    True when the turn had a binding (now revoked), False when it has none yet (the Stop is recorded against the claim
    and applies to a binding written later)."""
    if type(ledger_id) is not int or type(claim_generation) is not int:
        raise ActionContractError("tool_turn_invalid")
    with conn.cursor() as cur:
        # Against the claim first, so a binding written after this Stop starts revoked (record() applies it).
        cur.execute("""INSERT INTO guild.mc_tool_turn_revocations (ledger_id, claim_generation, reason)
            VALUES (%s,%s,'owner_stop') ON CONFLICT DO NOTHING""", (ledger_id, claim_generation))
        cur.execute("""UPDATE guild.mc_tool_turns SET revoked_at = coalesce(revoked_at, clock_timestamp()),
                revoked_reason = coalesce(revoked_reason, 'owner_stop')
            WHERE ledger_id=%s AND claim_generation=%s""", (ledger_id, claim_generation))
        return cur.rowcount == 1


def parse_session_key(session_key, agent_id, allowed_agents) -> str:
    """The portal's ``user`` value from a host-supplied session key, or NotBound."""
    if not isinstance(session_key, str) or not isinstance(agent_id, str) or len(session_key) > 200:
        raise NotBound("tool_session_invalid")
    m = _SESSION_KEY.fullmatch(session_key)
    if m is None or m.group("agent") != agent_id or agent_id not in allowed_agents:
        raise NotBound("tool_session_invalid")
    return m.group("user")


def resolve(conn, *, session_key, agent_id, allowed_agents, principal: str, environment: str, run_id=None,
            tool_call_id=None, instance_id=None, wait_s: float = RUN_WAIT_S, clock=time.monotonic,
            sleep=time.sleep) -> TurnBinding:
    """The live owner turn of exactly this run, with ledger SHARE then turn NO KEY UPDATE, or NotBound.

    The run and call ids come from the plugin's host-sealed headers. A run not yet attached is waited for up
    to ``wait_s`` (the portal reads the run's opening event on its own path); a run attached to a turn that
    is no longer live, or to another session or principal, is refused at once. No run, no call: refused."""
    session_user = parse_session_key(session_key, agent_id, allowed_agents)
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id) \
            or not isinstance(tool_call_id, str) or not TOOL_CALL_ID.fullmatch(tool_call_id) \
            or not isinstance(instance_id, str) or not INSTANCE_ID.fullmatch(instance_id):
        raise NotBound("tool_run_missing")
    deadline = clock() + max(0.0, wait_s)
    with conn.cursor() as cur:
        while True:
            current_instance, current_epoch = _lock_current(cur, environment, "SHARE")
            # All handlers may write turn taint after binding. Taking SHARE on
            # the turn then upgrading lets concurrent native reads deadlock.
            # Lock ledger first explicitly (as record() does), then acquire the
            # turn's write-compatible lock before quota/admission/taint work.
            # External reads occur after the admission transaction commits.
            cur.execute("""SELECT id, claim_generation FROM guild.mc_turn_ledger WHERE id =
                (SELECT ledger_id FROM guild.mc_tool_turns WHERE runtime_run_id=%s)
                FOR SHARE""", (run_id,))
            locked_ledger = cur.fetchone()
            if locked_ledger is None:
                if clock() >= deadline:
                    row = None
                    break
                conn.commit()
                sleep(_RUN_POLL_S)
                continue
            cur.execute("""SELECT ledger_id FROM guild.mc_tool_turns WHERE runtime_run_id=%s
                AND ledger_id=%s AND claim_generation=%s FOR NO KEY UPDATE""", (run_id, *locked_ledger))
            if cur.fetchone() is None:
                raise NotBound("tool_run_not_bound")
            # A lock wait can outlive the lease. Use a fresh statement/snapshot
            # after all locks, so clock and revocation checks are not pre-wait.
            cur.execute("""SELECT t.ledger_id, t.claim_generation, t.conversation_id, t.note_request_id,
                    t.runtime_user, t.principal, l.state, l.claim_generation AS live_generation, l.principal AS
                    ledger_principal, l.conversation_id AS ledger_conversation, l.note_request_id AS ledger_note,
                    l.lease_until, (l.lease_until > clock_timestamp()) AS lease_live, t.revoked_at,
                    t.runtime_agent, t.runtime_instance, t.revoked_reason, t.portal_epoch,
                    EXISTS (SELECT 1 FROM guild.mc_tool_turn_revocations r WHERE r.ledger_id = t.ledger_id
                            AND r.claim_generation = t.claim_generation) AS stopped
                FROM guild.mc_tool_turns t JOIN guild.mc_turn_ledger l ON l.id = t.ledger_id
                WHERE t.runtime_run_id=%s AND t.ledger_id=%s AND t.claim_generation=%s
                """, (run_id, *locked_ledger))
            row = cur.fetchone()
            if row is not None or clock() >= deadline:
                break
            conn.commit()                       # see the portal's attach as soon as it commits; nothing held
            sleep(_RUN_POLL_S)
    if row is None:
        raise NotBound("tool_run_not_bound")
    (ledger_id, generation, conversation_id, note_id, runtime_user, bound_principal, state, live_generation,
     ledger_principal, ledger_conversation, ledger_note, lease_until, lease_live, revoked_at, runtime_agent,
     runtime_instance, revoked_reason, bound_epoch, stopped) = row
    if (runtime_user != session_user or bound_principal != principal or ledger_principal != principal
            or runtime_agent != agent_id
            or ledger_conversation != conversation_id or ledger_note != note_id):
        raise NotBound("tool_session_invalid")
    if state != "claimed" or live_generation != generation or not lease_live:
        raise NotBound("tool_turn_not_live")
    if runtime_instance != instance_id or runtime_instance != current_instance:
        raise NotBound("tool_runtime_replaced")          # not this turn's process, or no longer the current one (§7.1)
    if bound_epoch is None or str(bound_epoch) != current_epoch:
        raise NotBound("tool_portal_restarted")          # admitted under a portal process that has since restarted
    from .fence import fenced
    if stopped or fenced(int(ledger_id), int(generation)):
        raise NotBound("tool_turn_stopped")                    # the owner's Stop, durable or fenced in this process
    if revoked_at is not None:
        raise NotBound({"runtime_replaced": "tool_runtime_replaced", "policy_changed": "tool_policy_changed",
                        "portal_restarted": "tool_portal_restarted"}
                       .get(revoked_reason, "tool_turn_stopped"))
    return TurnBinding(principal, environment, conversation_id, note_id, int(ledger_id), int(generation),
                       lease_until)
