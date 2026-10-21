"""MC's tool path in the portal (docs/design/mc_tool_path_design_2026-10-21.md). Off unless the mount's
settings have MINIMOI_MC_TOOLS=1 with MINIMOI_MC_TOOLS_ENV=dev|prod, MINIMOI_MC_TOOLS_PRINCIPAL, the
executor credential's SHA-256 (MINIMOI_MC_TOOLS_TOKEN_SHA256) and DATABASE_URL, on the turn ledger.

Three pieces:
* ``binder``: at dispatch of a ledger-claimed owner turn, record the per-turn tool binding;
* ``attach``: start the internal tool listener on its own port (MINIMOI_MC_TOOLS_PORT, default 5011);
* ``proposals``: the owner's Confirm / Cancel for MC's delegation and invite proposals.
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
            "delegation": environ.get("MINIMOI_MC_DELEGATION") == "1"}


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


def build_service(cfg, *, lessons_root=None, lessons_env=None, invites=None):
    from core.mc_tools.service import ToolService
    lessons = None
    if lessons_root and os.path.isabs(lessons_root) and lessons_env in ("dev", "prod"):
        from core.mc_lessons.ledger import LessonLedger
        lessons = LessonLedger(lessons_root, principal=cfg["principal"], environment=lessons_env)
    return ToolService(_connect(cfg["dsn"]), principal=cfg["principal"], environment=cfg["environment"],
                       allowed_agents=cfg["agents"], lessons=lessons, delegation=cfg["delegation"],
                       invites=cfg["invites"] if invites is None else invites, guests_lookup=_guests_lookup)


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
                            invites=invites)
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


def proposals(environ, *, start_research, delivery):
    """The ToolProposals handler for this mount, or None when tools are off."""
    cfg = settings(environ)
    if cfg is None:
        return None
    from core.mc_tools.proposals import ToolProposals
    invite = invite_runner(*delivery) if cfg["invites"] and delivery else None
    return ToolProposals(_connect(cfg["dsn"]), principal=cfg["principal"], environment=cfg["environment"],
                         start_research=start_research if cfg["delegation"] else None, invite=invite)
