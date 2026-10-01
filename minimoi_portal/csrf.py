"""The portal's write guard for browser writes, shared by the Guild API and the
CoS web chat (/app/cos).

A write must carry the per-session token in X-CSRF-Token and the expected
content type. The token is the control; it does not depend on Origin
matching. As a second check, a browser's Sec-Fetch-Site must be same-origin
(or none), and an Origin header, when present, must name this site's host.
The host is compared without the scheme, because behind the tunnel the portal
sees plain HTTP while the browser sends an https Origin, and the portal has
no ProxyFix; X-Forwarded-* headers are never trusted.
"""
from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlsplit

from flask import request, session

JSON = "json"
MULTIPART = "multipart"


def token(session_key: str) -> str:
    """This session's token for session_key, minted on first use."""
    value = session.get(session_key)
    if not value:
        value = secrets.token_urlsafe(32)
        session[session_key] = value
    return value


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlsplit(url).netloc.lower() or None
    except ValueError:
        return None


def _content_ok(kind: str) -> bool:
    if kind == JSON:
        return request.is_json
    if kind == MULTIPART:
        return (request.mimetype or "") == "multipart/form-data"
    raise ValueError(f"unknown content kind: {kind!r}")


def refusal(session_key: str, base_url: str | None, *, content: str = JSON) -> str | None:
    """None when the write may proceed; otherwise why it was refused
    ("not JSON", "not multipart", "cross-site", "other origin" or "token")."""
    if not _content_ok(content):
        return "not JSON" if content == JSON else "not multipart"
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None and site not in ("same-origin", "none"):
        return "cross-site"
    origin = request.headers.get("Origin")
    if origin is not None:
        allowed = {request.host.lower()}
        base = host_of(base_url)
        if base:
            allowed.add(base)
        if host_of(origin) not in allowed:
            return "other origin"
    if not _token_matches(session.get(session_key), request.headers.get("X-CSRF-Token", "")):
        return "token"
    return None


TOKEN_MAX = 256
HEADER = "X-CSRF-Token"


def _token_matches(expected, sent) -> bool:
    """A constant-time match; anything malformed (not a string, empty, too
    long, not ASCII) is simply no match, never an error."""
    if not isinstance(expected, str) or not isinstance(sent, str):
        return False
    if not expected or not sent or len(sent) > TOKEN_MAX or not sent.isascii() or not expected.isascii():
        return False
    return hmac.compare_digest(sent.encode("ascii"), expected.encode("ascii"))


__all__ = ["token", "refusal", "host_of", "JSON", "MULTIPART", "HEADER", "TOKEN_MAX"]
