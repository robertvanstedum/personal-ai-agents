"""Staging-only sign-in for the automatic UAT (SPEC_AUTOMATIC_UAT v0.1 §2).

The UAT drives dev.minimoi.ai the way Robert does, so it needs his owner session there, and nothing else is allowed
to mint one. This route is the one door. It opens only where all of these hold:

* ``MINIMOI_ENV`` is exactly ``staging`` **and** ``MINIMOI_ROLE`` is exactly ``standby`` (production sets
  ``MINIMOI_ROLE=production``), **and** ``BASE_URL`` is ``https://dev.minimoi.ai``;
* ``MINIMOI_UAT_SIGNIN_SHA256`` holds the sha256 (hex) of a 64-hex random token kept in SSM, never the token itself;
* the caller POSTs that token in the ``X-MiniMoi-UAT-Token`` header (never the URL, so it never reaches a log).

Anywhere else, production included, every method answers 404, as if the route did not exist. A deploy test keeps the
hash out of every production compose file. The session it creates carries ``uat: True`` and ``uat_issued_at``;
:func:`expired` lets the portal drop a UAT session after :data:`MAX_AGE_S`, so a copied cookie cannot be replayed for
the portal's usual 30 days. The marker is also written to the portal log on every sign-in. Chats created under such a
session are marked server-side (:func:`is_uat`) and kept out of Vault search. Carrying the marker into receipts and
audit records is a follow-up (not done here).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from urllib.parse import urlsplit

HEADER = "X-MiniMoi-UAT-Token"
ENV_HASH = "MINIMOI_UAT_SIGNIN_SHA256"
ENV_NAME = "MINIMOI_ENV"
ENV_ROLE = "MINIMOI_ROLE"
STAGING_HOST = "dev.minimoi.ai"
MAX_AGE_S = 2 * 3600
_HEX64 = re.compile(r"[0-9a-f]{64}")
_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def _is_staging(env) -> bool:
    if (env.get(ENV_NAME) or "").strip() != "staging" or (env.get(ENV_ROLE) or "").strip() != "standby":
        return False
    base = urlsplit((env.get("BASE_URL") or "").strip())
    return base.scheme == "https" and (base.hostname or "").lower() == STAGING_HOST


def expected_hash(environ=None) -> str | None:
    """The configured token hash, or None when the door is closed (not staging, unset or malformed)."""
    env = os.environ if environ is None else environ
    if not _is_staging(env):
        return None
    value = (env.get(ENV_HASH) or "").strip().lower()
    return value if _HEX64.fullmatch(value) else None


def token_ok(sent: str | None, environ=None) -> bool:
    want = expected_hash(environ)
    if want is None or not isinstance(sent, str) or not _HEX64.fullmatch(sent):
        return False
    got = hashlib.sha256(sent.encode("ascii")).hexdigest()
    return hmac.compare_digest(got, want)


def owner_session_user(now: float | None = None) -> dict:
    """Robert's owner account on staging, marked as a UAT session. Guildhall, CoS and MC know the owner as principal
    ``robert`` (rooms_identity.OWNER_PRINCIPAL), so it must be his account rather than a second owner."""
    return {"username": "robert", "tier": "owner", "display_name": "Robert", "uat": True,
            "uat_issued_at": int(time.time() if now is None else now)}


def expired(user, now: float | None = None) -> bool:
    """True for a UAT session past :data:`MAX_AGE_S` (or with no issue time). False for every non-UAT session."""
    if not isinstance(user, dict) or not user.get("uat"):
        return False
    issued = user.get("uat_issued_at")
    if not isinstance(issued, int):
        return True
    return (time.time() if now is None else now) - issued > MAX_AGE_S


def is_uat(user, environ=None, now: float | None = None) -> bool:
    """True only for a live session this door issued, on a host where the door is open. Chats created under it are
    marked ``uat`` (MC: the conversation record; CoS: a signed ``uat`` claim, then its conversation metadata) and never
    reach Vault search. Always False outside staging, whatever the session says."""
    return (isinstance(user, dict) and user.get("uat") is True and expected_hash(environ) is not None
            and not expired(user, now))


def scrub_sentry_event(event):
    """Drop the UAT token header from a Sentry event or transaction, whatever path it came from."""
    try:
        headers = (event.get("request") or {}).get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() == HEADER.lower():
                    headers[key] = "[removed]"
        elif isinstance(headers, list):
            event["request"]["headers"] = [h for h in headers
                                           if not (isinstance(h, (list, tuple)) and h and str(h[0]).lower() == HEADER.lower())]
    except Exception:
        pass
    return event


def install(app, session) -> None:
    """Register ``/uat/sign-in``. Every method is 404 unless :func:`expected_hash` says the door is configured and a
    POST carries the right token (no automatic OPTIONS/HEAD answers that would reveal the route)."""
    from flask import abort, request

    def uat_sign_in():
        if request.method != "POST" or expected_hash() is None or not token_ok(request.headers.get(HEADER)):
            abort(404)
        session.clear()
        session["user"] = owner_session_user()
        app.logger.warning("uat sign-in: owner session issued on staging (uat=true, max age %ss)", MAX_AGE_S)
        return {"ok": True, "uat": True}, 200

    @app.before_request
    def _drop_expired_uat_session():
        # Every reader of session["user"] sees this first: a UAT session lives MAX_AGE_S, not the usual 30 days.
        if expired(session.get("user")):
            session.pop("user", None)

    uat_sign_in.provide_automatic_options = False
    app.add_url_rule("/uat/sign-in", "uat_sign_in", uat_sign_in, methods=_ALL_METHODS)
