"""API answers for the real Shop floor: JSON 401/403, CSRF, and the record mode.

json_guard wraps the portal's own owner guard (spec §5.2) instead of copying
its checks: when the guard lets a call through, the view runs; when it
answers with a redirect, the API answers 401 (no session user) or 403 (a
user who is not the owner), with no Location header.

CSRF (spec §5.3, review S7). Every write must carry the per-session token in
X-CSRF-Token and be JSON, with a same-origin Sec-Fetch-Site and a matching
Origin when present: the portal's shared write guard (minimoi_portal/csrf.py),
which the CoS web chat uses too.

Record mode (review S8). Every write must say X-Record-Mode: on_record.
off_record answers 409 not_listening before anything is read or written; a
missing or other value answers 422.
"""
from __future__ import annotations

import functools

from flask import current_app, jsonify, request

from .. import csrf as _csrf

CSRF_SESSION_KEY = "guild_floor_csrf"
REDIRECTS = (301, 302, 303, 307, 308)
OFF_RECORD_TEXT = "Off the record · nothing is kept. Save, notes and post-its are paused."


def json_error(error: str, message: str, status: int, **extra):
    response = jsonify({"error": error, "message": message, **extra})
    response.status_code = status
    return response


def csrf_token() -> str:
    return _csrf.token(CSRF_SESSION_KEY)


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


def check_write(base_url: str | None):
    """None when the write may proceed; otherwise the JSON refusal."""
    why = _csrf.refusal(CSRF_SESSION_KEY, base_url, content=_csrf.JSON)
    if why:
        return json_error("csrf", f"This request could not be verified ({why}). Nothing was changed.", 403)
    mode = request.headers.get("X-Record-Mode")
    if mode == "off_record":
        return json_error("not_listening", OFF_RECORD_TEXT, 409)
    if mode != "on_record":
        return json_error("invalid", "Every write must say whether it is on the record (X-Record-Mode).", 422)
    body = request.get_json(silent=True)
    if isinstance(body, dict) and "record_mode" in body and body["record_mode"] != mode:
        return json_error("invalid", "The record mode in the body and the header disagree.", 422)
    return None


def check_stop(base_url: str | None):
    """The write guard for Master Craftsman's Stop (streaming spec v0.3 N1):
    the shared guard's JSON, same-origin and token checks, but no record-mode
    check, so Stop works while off the record."""
    why = _csrf.refusal(CSRF_SESSION_KEY, base_url, content=_csrf.JSON)
    if why:
        return json_error("csrf", f"This request could not be verified ({why}). Nothing was changed.", 403)
    return None
