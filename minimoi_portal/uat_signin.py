"""Staging-only sign-in for the automatic UAT (SPEC_AUTOMATIC_UAT v0.1 §2).

The UAT drives dev.minimoi.ai the way Robert does, so it needs his owner session there, and nothing else is allowed
to mint one. This route is the one door. It exists only where all three hold:

* ``MINIMOI_ENV`` is exactly ``staging``;
* ``MINIMOI_UAT_SIGNIN_SHA256`` holds the sha256 (hex) of a random token kept in SSM, never the token itself;
* the caller sends that token in the ``X-MiniMoi-UAT-Token`` header (never the URL, so it never reaches a log).

Anywhere else, production included, the route answers 404 as if it did not exist. A deploy test keeps the hash out
of the production compose file. The session it creates carries ``uat: True`` so every reply, receipt and log line
from a UAT run can be told apart from Robert's own.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re

HEADER = "X-MiniMoi-UAT-Token"
ENV_HASH = "MINIMOI_UAT_SIGNIN_SHA256"
ENV_NAME = "MINIMOI_ENV"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_MIN_TOKEN = 32


def expected_hash(environ=None) -> str | None:
    """The configured token hash, or None when the door is closed (not staging, unset or malformed)."""
    env = os.environ if environ is None else environ
    if (env.get(ENV_NAME) or "").strip() != "staging":
        return None
    value = (env.get(ENV_HASH) or "").strip().lower()
    return value if _HEX64.fullmatch(value) else None


def token_ok(sent: str | None, environ=None) -> bool:
    want = expected_hash(environ)
    if want is None or not isinstance(sent, str) or len(sent) < _MIN_TOKEN:
        return False
    got = hashlib.sha256(sent.encode("utf-8")).hexdigest()
    return hmac.compare_digest(got, want)


def owner_session_user() -> dict:
    """Robert's owner account on staging, marked as a UAT session. Guildhall, CoS and MC know the owner as principal
    ``robert`` (rooms_identity.OWNER_PRINCIPAL), so it must be his account rather than a second owner."""
    return {"username": "robert", "tier": "owner", "display_name": "Robert", "uat": True}


def install(app, session) -> None:
    """Register ``POST /uat/sign-in``. Closed unless :func:`expected_hash` says the door is configured."""
    from flask import abort, request

    @app.route("/uat/sign-in", methods=["POST"])
    def uat_sign_in():
        if expected_hash() is None:
            abort(404)
        if not token_ok(request.headers.get(HEADER)):
            abort(404)
        session.clear()
        session["user"] = owner_session_user()
        app.logger.warning("uat sign-in: owner session issued on staging (uat=true)")
        return {"ok": True, "uat": True}, 200
