"""The tool service: one call = strict arguments, a live owner-turn binding, then one adapter (design §3–5).

The listener (services/mc_tools/listener.py) authenticates the plugin and passes the host-supplied
session key and agent; nothing in ``arguments`` names an identity, a turn or an authority. Each call
resolves its binding inside the transaction that does its work, so the owner's ledger entry stays
claimed (share-locked) until that work commits.

Results are ``{status, code, message, data}``: a plain sentence MC voices, and bounded data. A
refusal says nothing was changed; an uncertain outcome says so and is never retried here.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from core.minimoi_actions.context import ActionContractError
from core.minimoi_actions.grants import GrantStore
from core.minimoi_actions.receipts import _cursor

from . import binding as binding_mod, catalogue, effects, host
from .binding import NotBound

_log = logging.getLogger("mc_tools")
STATUSES = ("done", "needs_confirmation", "needs_clarification", "refused", "unknown")
PROPOSAL_MINUTES = 10
INVITE_APP = "portuguese"
INVITE_APP_NAME = "Meu Português"
AGENT_NAMES = {"claude-code": "Claude Code", "codex": "Codex"}
REFUSED_WORDS = {
    "tool_turn_not_live": "This tool only works inside Robert's own live turn, and that turn is no longer running. Nothing was done.",
    "tool_session_invalid": "This conversation is not one MiniMoi can act in. Nothing was done.",
    "tool_unknown": "There is no such MiniMoi tool. Nothing was done.",
    "tool_arguments_invalid": "Those tool arguments do not fit the tool. Nothing was done.",
    "tool_argument_invalid": "One of those tool arguments is not allowed. Nothing was done.",
    "host_observation_invalid": "The host measurement could not be checked, so I am not reporting it.",
    "tool_unavailable": "That tool is not switched on here. Nothing was done.",
}


def result(status, code, message, data=None) -> dict:
    assert status in STATUSES
    return {"status": status, "code": code, "message": message, "data": data or {}}


def refused(code) -> dict:
    return result("refused", code, REFUSED_WORDS.get(code, "MiniMoi refused that safely. Nothing was done."))


class ToolService:
    def __init__(self, connect, *, principal: str, environment: str, allowed_agents=("mc-chat",),
                 lessons=None, delegation: bool = False, invites: bool = False, worker: str = "mc-tools",
                 guests_lookup=None):
        """``connect()`` returns a new psycopg2 connection to the guild database (autocommit off).
        ``lessons`` is a LessonLedger (or None: lessons off). ``guests_lookup(email)`` returns an existing
        guest login with that email (or None); invites are refused while it is missing."""
        if environment not in ("dev", "prod") or not principal:
            raise ValueError("tool_service_config_invalid")
        self._connect, self.principal, self.environment = connect, principal, environment
        self.allowed_agents = frozenset(allowed_agents)
        self.lessons, self.delegation, self.invites = lessons, bool(delegation), bool(invites)
        self.worker, self.guests_lookup = worker[:80], guests_lookup

    @contextmanager
    def _tx(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _bind(self, conn, session_key, agent_id):
        return binding_mod.resolve(conn, session_key=session_key, agent_id=agent_id, allowed_agents=self.allowed_agents,
                                   principal=self.principal, environment=self.environment)

    def call(self, name, arguments, *, session_key, agent_id, observation=None) -> dict:
        try:
            args = catalogue.validate(name, arguments)
            if (observation is not None) != (name == "minimoi_host_health"):
                return refused("tool_arguments_invalid")
            handler = getattr(self, "_" + name.removeprefix("minimoi_"))
            return handler(args, session_key, agent_id, observation)
        except NotBound as exc:
            return refused(str(exc))
        except ActionContractError as exc:
            code = str(exc)
            _log.info("mc tool %s refused: %s", name, code)
            return refused(code if code in REFUSED_WORDS else "tool_refused")

    # ── read-only ───────────────────────────────────────────────────────────
    def _host_health(self, args, session_key, agent_id, observation):
        checked = host.check(observation)
        with self._tx() as conn:
            self._bind(conn, session_key, agent_id)
        return result("done", "measured", host.words(checked), checked)

    def _guest_actions(self):
        from core.mc_domain_actions import guest
        return guest.GuestActions(GrantStore({guest.CAPABILITY: guest.CATALOGUE_RULE}), owner_principal=self.principal)

    def _guest_requests(self, args, session_key, agent_id, observation):
        with self._tx() as conn:
            b = self._bind(conn, session_key, agent_id)
            rows = self._guest_actions().open_requests(conn, b.context(), limit=50)
        items = [{"ref": r["ref"], "name": r["name"], "email": r["email"], "app": r["domain"],
                  "requested": r["requested_at"][:10]} for r in rows]
        if not items:
            return result("done", "none_open", "There are no open guest requests.", {"requests": []})
        lines = [f"- {i['name'] or '(no name)'} <{i['email']}> for {i['app']}, requested {i['requested']} (ref {i['ref']})"
                 for i in items]
        return result("done", "listed", f"{len(items)} open guest request(s):\n" + "\n".join(lines), {"requests": items})

    # ── proposals (turn grant, C06 receipt) ─────────────────────────────────
    def _guest_request_reject(self, args, session_key, agent_id, observation):
        from core.mc_domain_actions import guest_commands
        with self._tx() as conn:
            b = self._bind(conn, session_key, agent_id)
            match = next((r for r in self._guest_actions().open_requests(conn, b.context(), limit=200)
                          if r["ref"] == args["ref"]), None)
            if match is None:
                return result("refused", "no_open_request", f"There is no open guest request {args['ref']}. "
                              "Nothing was changed; list the open requests again.")
            effect = effects.begin_turn_effect(conn, b, effects.request("guest.propose_reject", args), worker=self.worker)
            proposal_id = guest_commands.propose(conn, self.principal, self.environment, b.conversation_id,
                                                 b.note_request_id, match)
            receipt = effect.finish(conn, "guest_reject_proposal", {"proposal_id": proposal_id, "ref": match["ref"]},
                                    verified=True)
        return result("needs_confirmation", "proposed", guest_commands.proposal_words(match),
                      {"ref": match["ref"], "receipt_id": str(receipt["receipt_id"])})

    def _lesson_propose(self, args, session_key, agent_id, observation):
        from core.mc_lessons import commands as lessons
        if self.lessons is None:
            return refused("tool_unavailable")
        text, reason = lessons.clean(args["text"])
        if reason:
            return result("refused", reason, lessons.REFUSALS[reason])
        req = effects.request("lessons.propose", {"text": text})
        with self._tx() as conn:                       # the dispatched receipt commits before the file ledger is written
            b = self._bind(conn, session_key, agent_id)
            effect = effects.begin_turn_effect(conn, b, req, worker=self.worker)
        try:
            answer = lessons.LessonCommands(self.lessons).handle(lessons.Command("remember", text=text),
                                                                 conversation=b.conversation_id,
                                                                 turn_key="tool-" + effect.operation_id)
        except Exception:
            _log.exception("mc tool lesson proposal: outcome unknown")
            with self._tx() as conn:
                effect.unknown(conn)
            return result("unknown", "lesson_unconfirmed", "I could not confirm whether that lesson offer was recorded. "
                          "Ask Robert to say 'What lessons do you have?' before trying again.")
        state = answer["operation"]["state"]
        with self._tx() as conn:
            effect.finish(conn, "lesson_proposal", {"state": state}, verified=state == "proposed")
        status = "needs_confirmation" if state == "proposed" else "refused" if state == "refused" else "done"
        return result(status, state, answer["reply"])

    def _pending_tool_proposal(self, conn, b, kind, payload, req, words):
        """Record a delegation or invite proposal under the turn grant; the owner's Confirm acts on it."""
        import json
        from uuid import uuid4
        effect = effects.begin_turn_effect(conn, b, req, worker=self.worker)
        proposal_id, operation_id = str(uuid4()), str(uuid4())
        with _cursor(conn) as cur:
            cur.execute("""UPDATE guild.mc_tool_proposals SET state='cancelled' WHERE principal_id=%s AND environment=%s
                AND conversation_id=%s AND state='pending'""", (self.principal, self.environment, b.conversation_id))
            cur.execute("""INSERT INTO guild.mc_tool_proposals (proposal_id, principal_id, environment, conversation_id,
                owner_turn_id, kind, payload, operation_id, expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s, clock_timestamp() + %s * interval '1 minute')""",
                (proposal_id, self.principal, self.environment, b.conversation_id, b.note_request_id, kind,
                 json.dumps(payload, sort_keys=True), operation_id, PROPOSAL_MINUTES))
        receipt = effect.finish(conn, kind + "_proposal", {"proposal_id": proposal_id}, verified=True)
        return result("needs_confirmation", "proposed", words, {"receipt_id": str(receipt["receipt_id"])})

    def _delegate_research(self, args, session_key, agent_id, observation):
        if not self.delegation:
            return refused("tool_unavailable")
        who = AGENT_NAMES[args["agent"]]
        words = (f"Ask {who} to research your message (“{args['title']}”), read-only in a fresh copy of the "
                 f"repository, cost cap 2 USD? Your own message is the brief. Reply Confirm within "
                 f"{PROPOSAL_MINUTES} minutes, or Cancel.")
        with self._tx() as conn:
            b = self._bind(conn, session_key, agent_id)
            return self._pending_tool_proposal(conn, b, "delegate_research", args,
                                               effects.request("delegate.propose_research", args), words)

    def _access_invite(self, args, session_key, agent_id, observation):
        if not self.invites or self.guests_lookup is None:
            return refused("tool_unavailable")
        email = args["email"].lower()
        existing = self.guests_lookup(email)
        if existing is not None:
            return result("needs_clarification", "login_exists",
                          f"{email} already has a MiniMoi guest login ({existing}). Nothing was changed. "
                          "Ask Robert whether he wants it extended instead.")
        payload = {"name": args["name"], "email": email, "app": INVITE_APP, "days": args["days"]}
        words = (f"Give {args['name']} <{email}> a guest login to {INVITE_APP_NAME} for {args['days']} days? "
                 "They get an email with a temporary password to change at first sign-in. "
                 f"Reply Confirm within {PROPOSAL_MINUTES} minutes, or Cancel.")
        with self._tx() as conn:
            b = self._bind(conn, session_key, agent_id)
            return self._pending_tool_proposal(conn, b, "access_invite", payload,
                                               effects.request("access.propose_invite", payload), words)
