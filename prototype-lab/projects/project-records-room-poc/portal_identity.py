"""Rooms identity from the mini-moi sign-in (docs: _working/guild-beta/ROOMS_MINIMOI_IDENTITY_DESIGN_2026-10-11.md, draft 3).

The portal has already signed the person in. For each request it forwards, the portal's bridge adds ``X-Portal-Identity``: a short assertion
that names ONE Records principal for ONE exact request (method, path, sorted query, body digest), signed with a secret only the portal and
Records hold. Records checks it here and nowhere else trusts a name. A bad, stale, replayed or altered assertion is a 401; it never falls back
to a cookie or a key.

Assertion: ``v2.<base64url(claims JSON)>.<base64url(HMAC-SHA256(secret, "v2." + base64url(claims)))>``.
Claims: aud (records), env, kid, sub (principal), iat, exp (at most MAX_LIFETIME seconds after iat), nonce, method, target, body (SHA-256 hex).
The key file holds one line ``<kid>:<secret>``; it is never printed or logged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl, urlencode

AUDIENCE = "records"
MAX_LIFETIME = 30        # seconds an assertion may be valid
FUTURE_SKEW = 5          # seconds an issue time may be ahead of Records' clock
HEADER = "X-Portal-Identity"
CONTEXT_PREFIX = "portal:"

SCHEMA = """
CREATE TABLE IF NOT EXISTS portal_nonces(nonce TEXT PRIMARY KEY, expires REAL NOT NULL);
CREATE INDEX IF NOT EXISTS portal_nonces_expires ON portal_nonces(expires);
"""


class Refused(Exception):
    """The assertion is not acceptable. ``status`` is 401 for a bad assertion, 503 when this Records has no key."""
    def __init__(self, message, status=401):
        super().__init__(message)
        self.message, self.status = message, status


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _bytes(secret):
    return secret.encode("utf-8") if isinstance(secret, str) else secret


def canonical_target(path: str, query: str | bytes) -> str:
    """The path as the receiver sees it (decoded) plus the query, parsed and sorted so both sides agree whatever the original order."""
    if isinstance(query, bytes):
        query = query.decode("utf-8", "replace")
    pairs = sorted(parse_qsl(query, keep_blank_values=True))
    return path + ("?" + urlencode(pairs) if pairs else "")


def load_key(path):
    """(kid, secret bytes) from the key file, or None when there is no usable key (then Records ignores nothing: a header is refused)."""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            line = fh.readline().strip()
    except OSError:
        return None
    kid, sep, secret = line.partition(":")
    if not sep or not kid or len(secret) < 32:
        return None
    return kid, secret.encode("utf-8")


def sign(key, *, principal, method, target, body: bytes, env, now=None, nonce=None, lifetime=MAX_LIFETIME) -> str:
    """Build an assertion. Used by the portal bridge and by tests."""
    kid, secret = key[0], _bytes(key[1])
    now = int(time.time()) if now is None else int(now)
    claims = {"aud": AUDIENCE, "env": env, "kid": kid, "sub": principal, "iat": now, "exp": now + lifetime,
              "nonce": nonce or b64(os.urandom(18)),
              "method": method.upper(), "target": target, "body": hashlib.sha256(body or b"").hexdigest()}
    payload = "v2." + b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return payload + "." + b64(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())


def verify(header, key, *, env, method, target, body: bytes, connect, now=None) -> str:
    """The principal id the assertion names, or Refused. Spends the nonce: a second delivery of the same assertion is refused, also across a
    restart and when two copies arrive at once (the database decides who was first)."""
    if key is None:
        raise Refused("Rooms sign-in through mini-moi is not set up on this Records", 503)
    if not isinstance(header, str) or header.count(".") != 2 or len(header) > 4096:
        raise Refused("Portal identity is malformed")
    version, claims_b64, mac_b64 = header.split(".")
    if version != "v2":
        raise Refused("Portal identity version is not supported")
    kid, secret = key[0], _bytes(key[1])
    expected = hmac.new(secret, f"v2.{claims_b64}".encode("ascii"), hashlib.sha256).digest()
    try:
        given = unb64(mac_b64)
        claims = json.loads(unb64(claims_b64).decode("utf-8"))
    except Exception:
        raise Refused("Portal identity is malformed") from None
    if not hmac.compare_digest(expected, given) or not isinstance(claims, dict):
        raise Refused("Portal identity is not valid")
    now = time.time() if now is None else now
    try:
        iat, exp = float(claims["iat"]), float(claims["exp"])
        if (claims["aud"] != AUDIENCE or claims["env"] != env or claims["kid"] != kid or claims["method"] != method.upper()
                or claims["target"] != target or claims["body"] != hashlib.sha256(body or b"").hexdigest()):
            raise Refused("Portal identity is not for this request")
        sub, nonce = claims["sub"], claims["nonce"]
    except (KeyError, TypeError, ValueError):
        raise Refused("Portal identity is malformed") from None
    if not isinstance(sub, str) or not sub or len(sub) > 80 or not isinstance(nonce, str) or not 16 <= len(nonce) <= 80:
        raise Refused("Portal identity is malformed")
    if exp - iat > MAX_LIFETIME or exp <= iat:
        raise Refused("Portal identity lifetime is not acceptable")
    if iat > now + FUTURE_SKEW:
        raise Refused("Portal identity is dated in the future")
    if exp < now:
        raise Refused("Portal identity has expired")
    with connect() as db:
        db.execute("DELETE FROM portal_nonces WHERE expires < ?", (now,))
        try:
            db.execute("INSERT INTO portal_nonces VALUES(?,?)", (nonce, exp + FUTURE_SKEW))
        except Exception:
            raise Refused("Portal identity was already used") from None
    return sub


def actor_for(db, principal):
    """The Records actor for a portal-signed principal: it must exist and be a human. Never an agent, a service or a collaborator credential."""
    row = db.execute("SELECT id,label,kind FROM principals WHERE id=?", (principal,)).fetchone()
    if not row or row["kind"] != "human":
        raise Refused("This account has no Rooms identity", 403)
    return {"id": row["id"], "label": row["label"], "kind": "human", "credential_id": CONTEXT_PREFIX + row["id"],
            "installation_id": None, "grants": None, "legacy": False, "scope": "portal", "operations": None,
            "expires": None, "revoked": None}
