"""The tool service: one call = strict arguments, a live owner-turn binding, then one adapter (design §3–5).

The listener (services/mc_tools/listener.py) authenticates the plugin and passes the host-supplied
session key and agent; nothing in ``arguments`` names an identity, a turn or an authority. Each call
resolves its binding inside the transaction that does its work, so the owner's ledger entry stays
claimed (share-locked) until that work commits.

Results are ``{status, code, message, data}``: a plain sentence MC voices, and bounded data. A
refusal says nothing was changed; an uncertain outcome says so and is never retried here.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from contextlib import contextmanager
from uuid import uuid4

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
# Words that appear in placeholder names ("Test Guest") and in any workaround text; never treated as a person's name.
_GENERIC_WORDS = {"test", "guest", "guests", "user", "users", "admin", "request", "requests", "info", "contact", "hello",
                  "mail", "the", "and", "for", "example", "demo", "new", "minimoi", "robert"}
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # no 0/O or 1/I/L: Robert types it back
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
                 lessons=None, delegation: bool = False, invites: bool = False, tickets: bool = False,
                 comments: bool = False, guest_rejects: bool = False, github=None, show=None,
                 isolated_runtime: bool = False, worker: str = "mc-tools", guests_lookup=None):
        """``connect()`` returns a new psycopg2 connection to the guild database (autocommit off).
        ``lessons`` is a LessonLedger (or None: lessons off). ``guests_lookup(email)`` returns an existing
        guest login with that email (or None); invites are refused while it is missing."""
        if environment not in ("dev", "prod") or not principal:
            raise ValueError("tool_service_config_invalid")
        self._connect, self.principal, self.environment = connect, principal, environment
        self.allowed_agents = frozenset(allowed_agents)
        self.lessons, self.delegation, self.invites = lessons, bool(delegation), bool(invites)
        self.tickets, self.comments, self.guest_rejects = bool(tickets), bool(comments), bool(guest_rejects)
        self.github = github                          # core.mc_tools.github.GitHubIssues, or None: GitHub reads off
        # show(conversation_id, note_key, text) -> bool: the platform posts a proposal verbatim in the conversation.
        self.show = show
        # The operator states that the MC runtime runs alone under its own OS user, so only the plugin can hold the
        # executor credential. Until then any process of the gateway's user could forge calls, so every proposal
        # needs Robert to name its code: a forged one can never ride on his next plain "ok" (review P1-2).
        self.isolated_runtime = bool(isolated_runtime)
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
        # Refs, app and date only: no guest's name or email reaches the model or any caller of this listener
        # (review P1-2). The platform shows who it is to Robert when MC proposes acting on one.
        items = [{"ref": r["ref"], "app": r["domain"], "requested": r["requested_at"][:10]} for r in rows]
        if not items:
            return result("done", "none_open", "There are no open guest requests.", {"requests": []})
        lines = [f"- {i['ref']}: {i['app']}, requested {i['requested']}" for i in items]
        return result("done", "listed", f"{len(items)} open guest request(s) (names stay private; MiniMoi shows Robert "
                      "who it is when you propose acting on one):\n" + "\n".join(lines), {"requests": items})

    # ── proposals (turn grant, C06 receipt) ─────────────────────────────────
    def _guest_request_reject(self, args, session_key, agent_id, observation):
        if not self.guest_rejects:
            return refused("tool_unavailable")
        with self._tx() as conn:
            b = self._bind(conn, session_key, agent_id)
            m = next((r for r in self._guest_actions().open_requests(conn, b.context(), limit=200)
                      if r["ref"] == args["ref"]), None)
            if m is None:
                return result("refused", "no_open_request", f"There is no open guest request {args['ref']}. "
                              "Nothing was changed; list the open requests again.")
            words = (f"Reject the guest request from {m['name'] or m['email']} <{m['email']}> for {m['domain']}, "
                     f"requested {m['requested_at'][:10]} ({m['ref']})?")
            return self._pending_tool_proposal(conn, b, "guest_reject",
                                               {"ref": m["ref"], "target_version": m["target_version"]},
                                               effects.request("guest.propose_reject", {"ref": m["ref"]}), words)

    def _github_issues(self, args, session_key, agent_id, observation):
        if self.github is None:
            return refused("tool_unavailable")
        if len(args) != 1:
            return refused("tool_arguments_invalid")
        with self._tx() as conn:
            b = self._bind(conn, session_key, agent_id)
            with _cursor(conn) as cur:                # anyone can write on GitHub: this turn's proposals need a code
                cur.execute("""UPDATE guild.mc_tool_turns SET untrusted_read=true WHERE ledger_id=%s
                    AND claim_generation=%s""", (b.ledger_id, b.generation))
        state, message, data = (self.github.issue(args["number"]) if "number" in args
                                else self.github.search(args["query"]))
        return result("done" if state == "ok" else "refused", "read" if state == "ok" else state, message, data)

    def _public_text_refusal(self, conn, text):
        """The reason this text must not go to the public repository, or None: no email address, and no name of
        anyone who asked for guest access (records are named by ref only)."""
        if "@" in text:
            return "email_in_public_text"
        with _cursor(conn) as cur:
            cur.execute("SELECT name, email FROM guild.guest_requests")
            rows = cur.fetchall()
        lowered = text.lower()
        for row in rows:
            for word in re.findall(r"[^\W\d_]{3,}", f"{row['name'] or ''} {(row['email'] or '').split('@')[0]}"):
                if word.lower() in _GENERIC_WORDS:
                    continue
                if re.search(r"(?<!\w)" + re.escape(word.lower()) + r"(?!\w)", lowered):
                    return "name_in_public_text"
        return None

    def _issue_comment(self, args, session_key, agent_id, observation):
        if not self.comments:
            return refused("tool_unavailable")
        from .proposals import comment_text
        payload = {"number": args["number"], "summary": args["summary"],
                   "refs": sorted(args["refs"], key=lambda r: int(r.split(":")[1])), "environment": self.environment}
        with self._tx() as conn:
            b = self._bind(conn, session_key, agent_id)
            reason = self._public_text_refusal(conn, args["summary"])
            if reason:
                return result("refused", reason, "That text names a person or an email address, and the repository is "
                              "public. Nothing was proposed; say it with refs (guild:N) only.")
            preview = comment_text(payload, "<operation id>")
            words = f"Add this comment to issue #{args['number']}? It is public.\n\n{preview}"
            return self._pending_tool_proposal(conn, b, "issue_comment", payload,
                                               effects.request("issue.propose_comment", payload), words)

    def _data_repair_ticket(self, args, session_key, agent_id, observation):
        """Propose a public data-repair ticket naming guest requests by ref only (no names or emails)."""
        if not self.tickets:
            return refused("tool_unavailable")
        refs = sorted(args["refs"], key=lambda r: int(r.split(":")[1]))
        ids = [int(r.split(":")[1]) for r in refs]
        payload = {"refs": refs, "change": args["change"], "environment": self.environment}
        listed = " and ".join(refs) if len(refs) < 3 else ", ".join(refs[:-1]) + " and " + refs[-1]
        words = (f"Open a data-repair ticket asking Claude Code or Codex to {args['change']} guest request(s) {listed}? "
                 "It names them by ref only, because the ticket is public.")
        with self._tx() as conn:
            b = self._bind(conn, session_key, agent_id)
            with _cursor(conn) as cur:
                cur.execute("SELECT id FROM guild.guest_requests WHERE id = ANY(%s)", (ids,))
                found = {row["id"] for row in cur.fetchall()}
            missing = [r for r, i in zip(refs, ids) if i not in found]
            if missing:
                return result("refused", "unknown_ref", f"There is no guest request {', '.join(missing)}. Nothing was "
                              "proposed; list the guest requests again.")
            return self._pending_tool_proposal(conn, b, "data_repair", payload,
                                               effects.request("issue.propose_data_repair", payload), words)

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
        """Record one proposal under the turn grant and have the PLATFORM show it to Robert verbatim, with its code
        (review P1-1). The model is only told that it was shown; it never relays the text that Robert approves.
        One proposal per owner turn; a new turn's proposal supersedes any older one still waiting."""
        if self.show is None:
            return refused("tool_unavailable")
        with _cursor(conn) as cur:
            cur.execute("""SELECT 1 FROM guild.mc_tool_proposals WHERE principal_id=%s AND environment=%s
                AND proposing_ledger_id=%s AND state <> 'cancelled' LIMIT 1""",
                (self.principal, self.environment, b.ledger_id))
            if cur.fetchone() is not None:
                return result("refused", "one_proposal_per_turn", "I can offer only one change per message. Nothing "
                              "more was proposed; tell Robert about the next one after he answers this one.")
            cur.execute("""SELECT untrusted_read FROM guild.mc_tool_turns WHERE ledger_id=%s AND claim_generation=%s""",
                        (b.ledger_id, b.generation))
            untrusted = bool(cur.fetchone()["untrusted_read"]) or not self.isolated_runtime
        effect = effects.begin_turn_effect(conn, b, req, worker=self.worker)
        proposal_id, operation_id = str(uuid4()), str(uuid4())
        with _cursor(conn) as cur:
            cur.execute("""UPDATE guild.mc_tool_proposals SET state='cancelled' WHERE principal_id=%s AND environment=%s
                AND conversation_id=%s AND state='pending'""", (self.principal, self.environment, b.conversation_id))
            for _ in range(5):
                code = "P-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
                cur.execute("SAVEPOINT tool_code")
                try:
                    cur.execute("""INSERT INTO guild.mc_tool_proposals (proposal_id, principal_id, environment,
                        conversation_id, owner_turn_id, proposing_ledger_id, kind, payload, code, shown_text,
                        untrusted_input, operation_id, expires_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s, clock_timestamp() + %s * interval '1 minute')""",
                        (proposal_id, self.principal, self.environment, b.conversation_id, b.note_request_id,
                         b.ledger_id, kind, json.dumps(payload, sort_keys=True), code, words, untrusted, operation_id,
                         PROPOSAL_MINUTES))
                    cur.execute("RELEASE SAVEPOINT tool_code")
                    break
                except Exception:
                    cur.execute("ROLLBACK TO SAVEPOINT tool_code")
            else:
                raise ActionContractError("proposal_code_unavailable")
        receipt = effect.finish(conn, kind + "_proposal", {"proposal_id": proposal_id, "code": code}, verified=True)
        conn.commit()
        how = (f"Reply “yes {code}” to approve" if untrusted else f"Reply “yes” as your next message (or “yes {code}”)")
        card = (f"Proposal {code} from Master Craftsman, waiting for you:\n\n{words}\n\n{how}, or “no”. Nothing "
                f"happens until you do; it lapses after {PROPOSAL_MINUTES} minutes.")
        if not self.show(b.conversation_id, f"mc-tool-proposal-{proposal_id}", card):
            with _cursor(conn) as cur:
                cur.execute("UPDATE guild.mc_tool_proposals SET state='cancelled' WHERE proposal_id=%s", (proposal_id,))
            conn.commit()
            return result("refused", "proposal_not_shown", "MiniMoi could not show Robert this proposal, so it was "
                          "withdrawn and nothing will happen. Tell him plainly.")
        return result("needs_confirmation", "proposed",
                      f"MiniMoi has shown Robert proposal {code} word for word in the conversation:\n\n{words}\n\n"
                      "In one or two sentences, say what it is and that nothing happens until he approves it there"
                      + (f" by replying “yes {code}”" if untrusted else "") + ". Do not restate it as done.",
                      {"code": code, "receipt_id": str(receipt["receipt_id"])})

    def _delegate_research(self, args, session_key, agent_id, observation):
        if not self.delegation:
            return refused("tool_unavailable")
        who = AGENT_NAMES[args["agent"]]
        words = (f"Ask {who} to research your message (“{args['title']}”), read-only in a fresh copy of the "
                 "repository, cost cap 2 USD? Your own message is the brief.")
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
                 "They get an email with a temporary password to change at first sign-in.")
        with self._tx() as conn:
            b = self._bind(conn, session_key, agent_id)
            return self._pending_tool_proposal(conn, b, "access_invite", payload,
                                               effects.request("access.propose_invite", payload), words)
