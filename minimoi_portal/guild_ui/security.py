"""API answers for the real Shop floor: JSON 401/403, CSRF, and the record mode.

json_guard wraps the portal's own owner guard (spec §5.2) instead of copying
its checks: when the guard lets a call through, the view runs; when it
answers with a redirect, the API answers 401 (no session user) or 403 (a
user who is not the owner), with no Location header.

CSRF (spec §5.3, review S7). Every write must carry the per-session token in
X-CSRF-Token and be JSON. The token is the control; it does not depend on
Origin matching. As a second check, a browser's Sec-Fetch-Site must be
same-origin, and an Origin header, when present, must name this site's
host. The host is compared without the scheme, because behind the tunnel
the portal sees plain HTTP while the browser sends an https Origin, and the
portal has no ProxyFix; X-Forwarded-* headers are never trusted.

Record mode (review S8). Every write must say X-Record-Mode: on_record.
off_record answers 409 not_listening before anything is read or written; a
missing or other value answers 422.
"""
from __future__ import annotations

import functools
import hmac
import secrets
from urllib.parse import urlsplit

from flask import current_app, jsonify, request, session

CSRF_SESSION_KEY = "guild_floor_csrf"
REDIRECTS = (301, 302, 303, 307, 308)
OFF_RECORD_TEXT = "Off the record · nothing is kept. Save, notes and post-its are paused."


def json_error(error: str, message: str, status: int, **extra):
    response = jsonify({"error": error, "message": message, **extra})
    response.status_code = status
    return response


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def json_guard(owner_guard, current_user):
    def decorate(view):
        guarded = owner_guard(view)

        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            response = current_app.make_response(guarded(*args, **kwargs))
            if response.status_code in REDIRECTS:
                if current_user():
                    return json_error("not_allowed", "This needs the owner's sign-in.", 403)
                return json_error("not_signed_in", "Sign in first.", 401)
            return response
        return wrapped
    return decorate


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlsplit(url).netloc.lower() or None
    except ValueError:
        return None


def check_write(base_url: str | None, *, record_mode: bool = True):
    """None when the write may proceed; otherwise the JSON refusal. With
    ``record_mode=False`` (Master Craftsman's Stop, streaming spec v0.3 N1)
    the record-mode check is skipped, so Stop works while off the record; the
    JSON, same-origin and token checks still apply."""
    refuse = lambda why: json_error("csrf", f"This request could not be verified ({why}). Nothing was changed.", 403)  # noqa: E731
    if not request.is_json:
        return refuse("not JSON")
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None and site not in ("same-origin", "none"):
        return refuse("cross-site")
    origin = request.headers.get("Origin")
    if origin is not None:
        allowed = {request.host.lower()}
        base = _host_of(base_url)
        if base:
            allowed.add(base)
        if _host_of(origin) not in allowed:
            return refuse("other origin")
    expected = session.get(CSRF_SESSION_KEY)
    sent = request.headers.get("X-CSRF-Token", "")
    if not expected or not sent or not hmac.compare_digest(sent, expected):
        return refuse("token")
    if not record_mode:
        return None
    mode = request.headers.get("X-Record-Mode")
    if mode == "off_record":
        return json_error("not_listening", OFF_RECORD_TEXT, 409)
    if mode != "on_record":
        return json_error("invalid", "Every write must say whether it is on the record (X-Record-Mode).", 422)
    body = request.get_json(silent=True)
    if isinstance(body, dict) and "record_mode" in body and body["record_mode"] != mode:
        return json_error("invalid", "The record mode in the body and the header disagree.", 422)
    return None
