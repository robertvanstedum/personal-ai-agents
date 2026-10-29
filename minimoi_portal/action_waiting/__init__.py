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


def install(app, environ, *, base_url):
    """Build the service from the environment (parsed once, review N6) and open the agent door. No thread yet."""
    global _SERVICE
    settings = Settings.from_environ(environ, base_url=base_url)
    if settings.mode == "off":
        _SERVICE = None
        return None
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
    * an unexpected fault in the service: today's send, so Robert still knows (D12).
    Returns "legacy", "suppressed" or "direct"."""
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
    except Exception as exc:          # a fault here must never cost Robert the notice: today's send goes instead
        _log.warning("action notices: after_create failed (%s); today's direct send goes", type(exc).__name__)
        legacy()
        return "legacy"


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
