"""MiniMoi "action waiting" notices (spec v0.2: planning-studio/operations/specs/SPEC_ACTION_WAITING_NOTICES_v0.2_2026-10-29.md).

Every action waiting on Robert from the phase-1 sources (guest requests in both stores, password resets, supervision
decisions, agent asks) is recorded once and told once on Telegram, with a link into MC. The service never approves,
grants, rejects or decides anything.

The portal calls only the functions below. With ``MINIMOI_ACTION_NOTICES=off`` (the default) ``install`` returns None
and touches nothing: every other function is then a no-op, and ``after_create`` calls today's direct send exactly as
before (spec T22).
"""
from __future__ import annotations

import logging

from .settings import Settings

_log = logging.getLogger("action_waiting")
_SERVICE = None
# What the minimal fault notice needs, copied at install so it never asks the service (D12, Robert 29 Oct).
_MINIMAL = {"base_url": "", "label": "", "chat_id": None}


def install(app, environ, *, base_url):
    """Build the service from the environment (parsed once, review N6) and open the agent door. No thread yet."""
    global _SERVICE
    settings = Settings.from_environ(environ, base_url=base_url)
    if settings.mode == "off":
        _SERVICE = None
        return None
    _MINIMAL.update(base_url=settings.base_url, label=settings.label, chat_id=settings.chat_id)
    from .service import Service
    _SERVICE = Service(settings)
    if settings.agent_tokens:
        from .door import attach
        attach(app, _SERVICE)
    _log.warning("action notices: %s (env %s); door %s", settings.mode, settings.env or "unset",
                 "open" if settings.agent_tokens else "closed")
    return _SERVICE


def current():
    """The running service, or None when off. (Not named ``service``: that is the submodule's name, and importing the
    submodule would replace a function of that name on the package.)"""
    return _SERVICE


def start():
    """Start the one sweep thread (app.main() only)."""
    if _SERVICE is not None:
        _SERVICE.start()


def wake():
    if _SERVICE is not None:
        _SERVICE.wake()


def record_guest(email, *, pending=None, guild=None, app=None):
    return _SERVICE.record_guest(email, pending=pending, guild=guild, app=app) if _SERVICE is not None else None


def record_reset(request, *, account_expired=None):
    return _SERVICE.record_reset(request, account_expired=account_expired) if _SERVICE is not None else None


def after_create(kind, code, legacy, *, facts=None, app=None, refs=()):
    """After a create path recorded (or failed to record) its item: today's direct send, its safe form, or nothing.

    * off: ``legacy()``, today's function with today's arguments, unchanged;
    * on, this request's item recorded open (``code``), and the service healthy: nothing (the service tells it, D5);
    * otherwise: the safe direct notice on the legacy bot (no name or email, D11);
    * any fault inside the service (in wake, health, suppression or the safe direct notice itself): the minimal
      no-name notice, through a sender that needs no store and no service state (D12, Robert 29 Oct: "No-name
      notice"). Today's name-and-email message is never sent while the service is record or on.
    Returns "legacy", "suppressed", "direct" or "minimal"."""
    svc = _SERVICE
    if svc is None:
        legacy()
        return "legacy"
    try:
        svc.wake()
        if code and svc.suppresses():
            return "suppressed"
        svc.direct_notice(kind, code, facts=facts, app=app, refs=refs)
        return "direct"
    except Exception as exc:          # never lose the notice, never send a name or an email (D12)
        _log.warning("action notices: after_create failed (%s); the minimal no-name notice goes", type(exc).__name__)
        minimal_notice()
        return "minimal"


def minimal_bot():
    """Today's bot for direct sends (TELEGRAM_BOT_TOKEN, else Keychain telegram/polling_bot_token). Replaced in tests."""
    from core.get_secret import get_secret
    from core.supervision.telegram import Bot
    return Bot(get_secret("TELEGRAM_BOT_TOKEN", "telegram", "polling_bot_token"))


def minimal_text() -> str:
    """Fixed words only: an action is waiting, and the MiniMoi link. No kind detail, name, email or words."""
    return (f"{_MINIMAL['label']}MiniMoi · action waiting (NOT RECORDED in MiniMoi)\n"
            f"Open MiniMoi: {_MINIMAL['base_url']}/guild-next/guild/build")


def minimal_notice():
    """The independent no-name sender: no store, no service, nothing that can carry personal data. Never raises."""
    try:
        minimal_bot().send(_MINIMAL["chat_id"] or 8379221702, minimal_text())
    except Exception as exc:
        from .sender import failure_code
        _log.warning("action notices: minimal notice failed (%s)", failure_code(exc))


def main(argv=None):
    """`python3 -m minimoi_portal.action_waiting status`: the operator's one line (counts and states, no words)."""
    import os
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] != ["status"]:
        print("usage: python3 -m minimoi_portal.action_waiting status", file=sys.stderr)
        return 2
    settings = Settings.from_environ(os.environ)
    if settings.mode == "off":
        print("mode=off")
        return 0
    from .service import Service
    print(Service(settings).status_line())
    return 0
