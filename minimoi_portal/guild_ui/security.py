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
missing or other value answers 422. The one exception is a Private question
(check_private): it must say off_record, and it is the only thing that does.
"""
from __future__ import annotations

import functools
import re

from flask import current_app, jsonify, request

from .. import csrf as _csrf

CSRF_SESSION_KEY = "guild_floor_csrf"
REDIRECTS = (301, 302, 303, 307, 308)
OFF_RECORD_TEXT = ("Private · your messages go to Master Craftsman and he answers. MiniMoi keeps no notes, history, files or memory "
                   "of them, and this page forgets the conversation when you leave it. The agent runtime on this Mac keeps its own "
                   "session record, and Master Craftsman can read the project's files as in any conversation. Save, notes and "
                   "post-its are paused.")


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


def check_private(base_url: str | None):
    """The guard for a Private question to Master Craftsman: the same owner, same-origin, token and JSON checks as any
    write, but it must say X-Record-Mode: off_record. On the record it is refused (422): a kept note is asked through the
    normal turn, never through this path, so this path can never be used to ask about kept work without keeping it."""
    why = _csrf.refusal(CSRF_SESSION_KEY, base_url, content=_csrf.JSON)
    if why:
        return json_error("csrf", f"This request could not be verified ({why}). Nothing was sent.", 403)
    if request.headers.get("X-Record-Mode") != "off_record":
        return json_error("invalid", "A Private question must say it is off the record (X-Record-Mode). Nothing was sent.", 422)
    return None


def check_stop(base_url: str | None):
    """The write guard for Master Craftsman's Stop (streaming spec v0.3 N1):
    the shared guard's JSON, same-origin and token checks, but no record-mode
    check, so Stop works while off the record."""
    why = _csrf.refusal(CSRF_SESSION_KEY, base_url, content=_csrf.JSON)
    if why:
        return json_error("csrf", f"This request could not be verified ({why}). Nothing was changed.", 403)
    return None


UPLOAD_KEY_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")


def check_upload(base_url: str | None):
    """(refusal, idempotency_key) for a media upload (Guild 1.1 slice 3, spec
    §2 and §5.2): the same owner, same-origin and CSRF checks as every write,
    but for multipart/form-data; X-Record-Mode: on_record (off the record is
    409, nothing is read); and the idempotency key in the Idempotency-Key
    header, since the body is the file. The size caps are the caller's."""
    why = _csrf.refusal(CSRF_SESSION_KEY, base_url, content=_csrf.MULTIPART)
    if why:
        return json_error("csrf", f"This request could not be verified ({why}). Nothing was changed.", 403), None
    mode = request.headers.get("X-Record-Mode")
    if mode == "off_record":
        return json_error("not_listening", OFF_RECORD_TEXT, 409), None
    if mode != "on_record":
        return json_error("invalid", "Every write must say whether it is on the record (X-Record-Mode).", 422), None
    key = request.headers.get("Idempotency-Key", "")
    if not UPLOAD_KEY_RE.fullmatch(key):
        return json_error("invalid", "An upload needs an Idempotency-Key header (8 to 64 letters, digits, - or _).",
                          422), None
    return None, key
