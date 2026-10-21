"""MC's tool path in the portal (docs/design/mc_tool_path_design_2026-10-21.md). Off unless the mount's
settings have MINIMOI_MC_TOOLS=1 with MINIMOI_MC_TOOLS_ENV=dev|prod, MINIMOI_MC_TOOLS_PRINCIPAL, the
executor credential's SHA-256 (MINIMOI_MC_TOOLS_TOKEN_SHA256) and DATABASE_URL, on the turn ledger.

Three pieces:
* ``binder``: at dispatch of a ledger-claimed owner turn, record the per-turn tool binding;
* ``attach``: start the internal tool listener on its own port (MINIMOI_MC_TOOLS_PORT, default 5011);
* ``proposals``: the owner's yes / no for MC's proposals (data-repair tickets, delegation, invites).

Guest rejects follow MINIMOI_MC_GUEST_ACTIONS=1. GitHub issue reads (public, no credential) are on with the tools
unless MINIMOI_MC_TOOLS_GITHUB=0. Issue comments (MINIMOI_MC_TOOLS_COMMENTS=1) and data-repair tickets
(MINIMOI_MC_TOOLS_TICKETS=1) need MINIMOI_MC_ISSUES_TOKEN_FILE: a private file holding a GitHub token that can only
write issues in MINIMOI_GITHUB_REPO. Without it MC is told they are off.
"""
from __future__ import annotations

import logging
import os

FLAG = "MINIMOI_MC_TOOLS"
_log = logging.getLogger("guild_ui.mc_tools")


def settings(environ) -> dict | None:
    environ = environ or {}
    if environ.get(FLAG) != "1" or environ.get("MINIMOI_MC_TOOLS_ENV") not in ("dev", "prod"):
        return None
    principal = (environ.get("MINIMOI_MC_TOOLS_PRINCIPAL") or "").strip()
    dsn = os.environ.get("DATABASE_URL")
    if not principal or not dsn or environ.get("MINIMOI_MC_TURN_LEDGER") != "1":
        return None
    agents = tuple(a for a in (environ.get("MINIMOI_MC_TOOLS_AGENTS") or "mc-chat").split(",") if a)
    return {"environment": environ["MINIMOI_MC_TOOLS_ENV"], "principal": principal, "dsn": dsn, "agents": agents,
            "token_sha256": (environ.get("MINIMOI_MC_TOOLS_TOKEN_SHA256") or "").strip().lower(),
            "port": int(environ.get("MINIMOI_MC_TOOLS_PORT") or 5011),
            "invites": environ.get("MINIMOI_MC_TOOLS_INVITES") == "1",
            "tickets": environ.get("MINIMOI_MC_TOOLS_TICKETS") == "1",
            "comments": environ.get("MINIMOI_MC_TOOLS_COMMENTS") == "1",
            "github_read": environ.get("MINIMOI_MC_TOOLS_GITHUB", "1") != "0",
            "guest_rejects": environ.get("MINIMOI_MC_GUEST_ACTIONS") == "1",
            # Operator statement, not a check: mc-chat's gateway runs alone under its own OS user (deploy handoff §4).
            "isolated_runtime": environ.get("MINIMOI_MC_TOOLS_RUNTIME_ISOLATED") == "1",
            "standard_changes": _standard_changes(environ.get("MINIMOI_MC_TOOLS_STANDARD_CHANGES") or ""),
            "issues_token_file": environ.get("MINIMOI_MC_ISSUES_TOKEN_FILE") or "",
            "repo": environ.get("MINIMOI_GITHUB_REPO") or "",
            "delegation": environ.get("MINIMOI_MC_DELEGATION") == "1"}


def _standard_changes(value):
    """'guest_reject=SC-3@371,access_invite=SC-4@372' -> {kind: {"id", "ticket"}}: standard changes Robert pre-approved
    (change-control standard §5). Malformed entries are ignored, so a typo means 'no standard change', never wider."""
    import re
    out = {}
    for part in value.split(","):
        m = re.fullmatch(r"\s*(guest_reject|access_invite)=([A-Za-z0-9_.-]{1,40})@([1-9][0-9]{0,4})\s*", part)
        if m:
            out[m.group(1)] = {"id": m.group(2), "ticket": int(m.group(3))}
    return out


def _connect(dsn):
    import psycopg2
    return lambda: psycopg2.connect(dsn, connect_timeout=4, options="-c statement_timeout=5000 -c lock_timeout=3000")


def binder(services, ledger_turn, *, environ, principal, conversation_id, note_id, session_conversation_id):
    """A callable that records this turn's tool binding right before it is sent; a no-op when tools are
    off, the ledger is off or the backend is not OpenClaw. A failure leaves tools refused for this turn
    and never stops the turn itself."""
    cfg = settings(environ)
    backend = getattr(services, "mc", None)
    if (cfg is None or ledger_turn is None or principal != cfg["principal"]
            or getattr(backend, "kind", "") != "openclaw" or not hasattr(backend, "session_user")):
        return lambda: None
    claim, connect = ledger_turn.claim, _connect(cfg["dsn"])

    def bind():
        from core.mc_tools.binding import record
        try:
            conn = connect()
            try:
                with conn:
                    live = record(conn, ledger_id=claim.ledger_id, claim_generation=claim.generation,
                                  principal=principal, conversation_id=conversation_id, note_request_id=note_id,
                                  session_user=backend.session_user(session_conversation_id))
            finally:
                conn.close()
            if not live:
                _log.warning("mc tools: ledger %s not live at dispatch; tools refused for this turn", claim.ledger_id)
        except Exception:
            _log.exception("mc tools: binding for ledger %s not recorded; tools refused for this turn", claim.ledger_id)
    return bind


def _guests_lookup(email):
    from minimoi_portal import auth
    for g in auth.list_guests():               # any login with this email, expired too: a grant would refuse it
        if (g.get("email") or "").lower() == email:
            return g.get("username") + (", expired" if g.get("expired") else "")
    return None


def _private_token(path):
    """The token from an owner-only regular file (no symlink), or None. Never logged."""
    import stat
    try:
        if not path or not os.path.isabs(path):
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077 or st.st_size > 512:
                return None
            token = os.read(fd, 512).decode("ascii").strip()
        finally:
            os.close(fd)
        return token if 20 <= len(token) <= 255 and token.isprintable() and " " not in token else None
    except (OSError, UnicodeError):
        return None


def _repo(cfg):
    from .mc_context import repo as default_repo
    return default_repo({"MINIMOI_GITHUB_REPO": cfg["repo"]} if cfg["repo"] else {})


def _github_post(cfg, path, payload, key, http_post):
    """POST one issue write. A 201 carrying ``key`` (an int) is ``created``; an answer GitHub gives before writing
    anything (401/403/404/410/422) is ``refused``; anything else, including a timeout, is ``unknown``, never retried."""
    import requests
    from .mc_context import API
    token = _private_token(cfg["issues_token_file"])
    if token is None:
        return "refused", None
    try:
        r = (http_post or requests.post)(f"{API}/repos/{_repo(cfg)}{path}", json=payload, timeout=(5, 15),
                                         allow_redirects=False, headers={"Authorization": f"Bearer {token}",
                                                                         "Accept": "application/vnd.github+json",
                                                                         "X-GitHub-Api-Version": "2022-11-28"})
    except Exception:
        return "unknown", None
    if r.status_code in (401, 403, 404, 410, 422):
        return "refused", None
    try:
        value = r.json().get(key) if r.status_code == 201 else None
    except ValueError:
        value = None
    return ("created", value) if type(value) is int else ("unknown", None)


def issue_filer(cfg, *, http_post=None):
    """``file_issue(title, body) -> (state, number)`` for data-repair tickets, or None while tickets are off."""
    if not cfg["tickets"] or _private_token(cfg["issues_token_file"]) is None:
        return None
    return lambda title, body: _github_post(cfg, "/issues", {"title": title, "body": body}, "number", http_post)


def issue_commenter(cfg, *, http_post=None):
    """``comment_issue(number, body) -> (state, comment id)``, or None while comments are off."""
    if not cfg["comments"] or _private_token(cfg["issues_token_file"]) is None:
        return None

    def comment_issue(number, body):
        if type(number) is not int or not 1 <= number <= 99999:
            return "refused", None
        return _github_post(cfg, f"/issues/{number}/comments", {"body": body}, "id", http_post)
    return comment_issue


def github_reader(cfg, *, http_get=None):
    if not cfg["github_read"]:
        return None
    import requests
    from core.mc_tools.github import GitHubIssues
    return GitHubIssues(http_get or requests.get, repo=_repo(cfg))


def _guest_people():
    """Display names and emails of every guest login, expired too: names that must never appear in public text."""
    from minimoi_portal import auth
    return [f"{g.get('display_name') or ''} {g.get('email') or ''}" for g in auth.list_guests()]


def proposal_shower(services, principal):
    """``show(conversation_id, note_key, text) -> bool``: the platform keeps a proposal, word for word, as its own note
    in that conversation, so Robert approves what the platform showed, not MC's retelling (review P1-1)."""
    def show(conversation_id, note_key, text):
        from .conversations import conversations_of
        from .stores import GUILD_PLATFORM
        try:
            conv = conversations_of(services).get(conversation_id, principal)
            if not isinstance(conv, dict) or conv.get("principal") != principal or conv.get("archived"):
                return False
            base, nf = services.floor, conv.get("notes_floor")
            floor = base if not nf or nf == base.floor else base.for_floor(nf)
            return floor.add_note(note_key, text, GUILD_PLATFORM, area="Guild", page="mc").outcome == "kept"
        except Exception:
            _log.exception("mc tools: proposal could not be shown")
            return False
    return show


def build_service(cfg, *, lessons_root=None, lessons_env=None, invites=None, show=None):
    from core.mc_tools.service import ToolService
    lessons = None
    if lessons_root and os.path.isabs(lessons_root) and lessons_env in ("dev", "prod"):
        from core.mc_lessons.ledger import LessonLedger
        lessons = LessonLedger(lessons_root, principal=cfg["principal"], environment=lessons_env)
    return ToolService(_connect(cfg["dsn"]), principal=cfg["principal"], environment=cfg["environment"],
                       allowed_agents=cfg["agents"], lessons=lessons, delegation=cfg["delegation"],
                       invites=cfg["invites"] if invites is None else invites,
                       tickets=issue_filer(cfg) is not None, comments=issue_commenter(cfg) is not None,
                       guest_rejects=cfg["guest_rejects"], github=github_reader(cfg), show=show,
                       isolated_runtime=cfg["isolated_runtime"], people=_guest_people,
                       standard_changes=cfg["standard_changes"],
                       guests_lookup=_guests_lookup)


def attach(app, blueprint_name, services):
    """Start the tool listener for this mount (once per process). Nothing happens while tools are off."""
    environ = app.extensions[blueprint_name].get("feature_environ") or {}
    cfg = settings(environ)
    if cfg is None or app.extensions.get("mc_tools_listener"):
        return None
    import re
    if not re.fullmatch(r"[0-9a-f]{64}", cfg["token_sha256"]):
        _log.warning("mc tools: MINIMOI_MC_TOOLS_TOKEN_SHA256 missing or invalid; the tool listener stays off")
        return None
    from services.mc_tools.listener import create_app, serve
    lessons_env = environ.get("MINIMOI_MC_LESSONS_ENV") if environ.get("MINIMOI_MC_LESSONS") == "1" else None
    # Invites need the portal's own delivery (app.py); without it MC is told invites are off, never half-done.
    invites = cfg["invites"] and app.extensions.get("mc_invite_delivery") is not None
    service = build_service(cfg, lessons_root=environ.get("MINIMOI_MC_LESSONS_DIR"), lessons_env=lessons_env,
                            invites=invites, show=proposal_shower(services, cfg["principal"]))
    thread = serve(create_app(service, token_sha256=cfg["token_sha256"]), host="0.0.0.0", port=cfg["port"])
    app.extensions["mc_tools_listener"] = thread
    _log.info("mc tools: listener on port %s (agents %s)", cfg["port"], ",".join(cfg["agents"]))
    return thread


# ── the owner's Confirm / Cancel ──────────────────────────────────────────────

def invite_runner(send_email, notify, *, db=None):
    """``invite(proposal, ref)`` for ToolProposals: grant through guest_requests.grant (the hardened state
    machine), and hand back a ``deliver()`` that emails the one-time password to the invitee."""
    def invite(p, ref):
        from minimoi_portal import guest_requests as gr
        pay = p["payload"]
        out = gr.grant(ref, duration_days=pay["days"], db=db)
        if out.status == "unknown":
            return "unknown", {}, None
        if out.status != "done":
            return "conflict", {"error": (out.verified_state or {}).get("error") or out.status}, None
        state = out.verified_state
        secret, generation = out.delivery_secret, state["credential_generation"]
        guest = {"email": pay["email"], "display_name": pay["name"], "username": state["username"]}

        def deliver():
            sent = bool(send_email(guest, temp_password=secret)) if send_email else False
            if sent:
                gr.confirm_delivery(ref, generation)
            if notify:
                notify(guest, email_sent=sent, temp_password=secret)     # as the Operate grant path does
            return sent
        return "done", {"username": state["username"]}, deliver
    return invite


def proposals(environ, *, start_research, delivery, lessons=None):
    """The ToolProposals handler for this mount, or None when tools are off."""
    cfg = settings(environ)
    if cfg is None:
        return None
    from core.mc_tools.proposals import ToolProposals
    invite = invite_runner(*delivery) if cfg["invites"] and delivery else None
    return ToolProposals(_connect(cfg["dsn"]), principal=cfg["principal"], environment=cfg["environment"],
                         start_research=start_research if cfg["delegation"] else None, invite=invite,
                         file_issue=issue_filer(cfg), comment_issue=issue_commenter(cfg),
                         guest_rejects=cfg["guest_rejects"], lessons=lessons)
